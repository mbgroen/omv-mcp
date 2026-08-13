#!/usr/bin/env python3
"""
omv-mcp — an MCP server for OpenMediaVault.

Design
------
The OpenMediaVault web interface is a thin client over an RPC layer: every
click sends a call to a service + method pair, such as
`FileSystemMgmt.enumerateMountedFilesystems`. The `omv-rpc` CLI tool that
ships with OMV talks to that exact same layer.

So instead of hand-writing dozens of narrow tools, this server exposes a few
generic ones: discover which services exist, discover a service's methods,
call a method, and (optionally) run a shell command. Anything the web
interface can do is reachable, and plugins you install later show up
automatically without a code change.

Transport
---------
By default the server runs on your own machine and reaches the NAS over the
system `ssh` binary. That reuses your existing SSH config, keys and agent --
no session cookies, no certificate handling, and no extra port open on the
NAS. Leave OMV_SSH_HOST empty to run the server directly on the NAS instead.

The MCP side speaks stdio through mcp_stdio.py, a standard-library-only
implementation. This server therefore has no third-party dependencies and
runs on any Python 3.9 or newer.

Configuration (environment variables)
-------------------------------------
OMV_SSH_HOST     Hostname/IP of the NAS. Empty = run commands locally.
OMV_SSH_USER     SSH user (default: root).
OMV_SSH_PORT     SSH port (default: 22).
OMV_SSH_KEY      Path to a private key (optional; otherwise ssh-agent/config).
OMV_RPC_USER     OMV user the RPC runs as (default: admin).
OMV_SUDO         "1" = prefix commands with `sudo` (needed for non-root SSH).
OMV_READONLY     "1" = refuse anything that does not look like a read method.
OMV_ALLOW_SHELL  "0" = do not register the omv_shell tool (default: on).
OMV_TIMEOUT      Per-command timeout in seconds (default: 60).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from functools import lru_cache
from typing import Any

from mcp_stdio import Server

__version__ = "1.1.1"

mcp = Server(
    "openmediavault",
    __version__,
    instructions=(
        "Manages an OpenMediaVault NAS through its own RPC layer. Start with "
        "omv_connection_info to confirm connectivity, then omv_list_services "
        "and omv_list_methods to discover what this particular installation "
        "offers before calling omv_call. Service names are case sensitive."
    ),
)

# --- Configuration ----------------------------------------------------------

TRUTHY = {"1", "true", "yes", "on"}
FALSEY = {"0", "false", "no", "off", ""}


def env_flag(name: str, default: bool) -> bool:
    """
    Read a boolean setting.

    Accepts more than "1" on purpose: when this server runs as a Claude Desktop
    extension the values come from checkboxes in the settings UI and arrive as
    "true"/"false", while a hand-written config is more likely to say "1".
    Anything unrecognised falls back to the default rather than silently
    counting as off, which for OMV_READONLY would be the dangerous direction.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in TRUTHY:
        return True
    if value in FALSEY:
        return False
    return default


def env_int(name: str, default: int) -> int:
    """
    Read a numeric setting, tolerating the "60.0" a number input may produce.

    An unparseable value falls back to the default; refusing to start over a
    malformed timeout would be worse than using a sane one.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


SSH_HOST = os.environ.get("OMV_SSH_HOST", "").strip()
SSH_USER = os.environ.get("OMV_SSH_USER", "").strip() or "root"
SSH_PORT = str(env_int("OMV_SSH_PORT", 22))
SSH_KEY = os.environ.get("OMV_SSH_KEY", "").strip()
RPC_USER = os.environ.get("OMV_RPC_USER", "").strip() or "admin"
USE_SUDO = env_flag("OMV_SUDO", False)
READONLY = env_flag("OMV_READONLY", False)
ALLOW_SHELL = env_flag("OMV_ALLOW_SHELL", True)
TIMEOUT = env_int("OMV_TIMEOUT", 60)

# Where the RPC sources live; plugins drop their own .inc files here too.
RPC_DIR = "/usr/share/openmediavault/engined/rpc"

IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")

# Lines from `grep -rn -A4` look like "/path/file.inc:31:text" for a match and
# "/path/file.inc-32-text" for a context line. Both separators occur.
GREP_LINE_RE = re.compile(r"^(?P<file>/.+?\.inc)[-:](?P<line>\d+)[-:](?P<text>.*)$")

# OMV's PHP sources mix single and double quotes freely -- on OMV 8.5, 6 of the
# 52 service names and roughly a third of all registerMethod() calls use single
# quotes. Matching only double quotes silently loses them, so accept both.
RETURN_NAME_RE = re.compile(r"""return\s+(?P<q>["'])(?P<name>[A-Za-z0-9_]+)(?P=q)\s*;""")
REGISTER_METHOD_RE = re.compile(
    r"""registerMethod\(\s*(?P<q>["'])(?P<name>[A-Za-z0-9_]+)(?P=q)"""
)
CLASS_DECL_RE = re.compile(r"^\s*(?:abstract\s+|final\s+)*class\s+[A-Za-z0-9_\\]+")

# Heuristic for "this only reads". Deliberately conservative: in read-only mode
# we would rather block a harmless method than let a write through.
READ_PREFIXES = (
    "get", "enumerate", "list", "is", "has", "read",
    "query", "find", "exists", "count", "check",
)


class OmvError(RuntimeError):
    """A command against the NAS failed."""


# --- Execution layer --------------------------------------------------------

def _ssh_prefix() -> list[str]:
    """Build the ssh command from the configuration."""
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",           # never prompt interactively for a password
        "-o", "ConnectTimeout=10",
        "-p", SSH_PORT,
    ]
    if SSH_KEY:
        cmd += ["-i", os.path.expanduser(SSH_KEY)]
    cmd.append(f"{SSH_USER}@{SSH_HOST}")
    return cmd


def _rpc_error_message(payload: str) -> str | None:
    """
    Pull the human-readable message out of an omv-rpc error response.

    A failed call exits 1 and writes to *stderr* (stdout stays empty)
    something like:

        {"response":null,"error":{"code":0,"message":"...","trace":"...\\n#0 ..."}}

    The trace is a few hundred characters of PHP stack that drowns out the
    actual message, so return only `message`.
    """
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    error = data.get("error")
    if not isinstance(error, dict):
        return None
    message = error.get("message")
    return message if isinstance(message, str) and message else None


def _exec(command: str, timeout: int | None = None) -> str:
    """
    Run a single shell command on the NAS (over SSH) or locally.

    Returns stdout on success; raises OmvError on a non-zero exit status so
    failures surface instead of quietly returning nothing.
    """
    if USE_SUDO:
        command = f"sudo {command}"

    argv = _ssh_prefix() + [command] if SSH_HOST else ["/bin/sh", "-c", command]

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout or TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise OmvError(
            f"Command exceeded {timeout or TIMEOUT}s and was aborted. "
            f"Raise OMV_TIMEOUT, or use omv_wait_for_task for long-running jobs."
        ) from exc
    except FileNotFoundError as exc:
        raise OmvError(f"Could not start the command: {exc}") from exc

    if proc.returncode != 0:
        for stream in (proc.stderr, proc.stdout):
            rpc_message = _rpc_error_message(stream)
            if rpc_message:
                raise OmvError(rpc_message)
        detail = (proc.stderr or proc.stdout or "").strip()
        raise OmvError(f"Exit status {proc.returncode}: {detail[:2000]}")

    return proc.stdout


def _parse(raw: str) -> Any:
    """
    OMV returns JSON; fall back to plain text when it does not parse.

    On a successful call omv-rpc already unwraps the result: you get
    `{"arch":"amd64"}`, not `{"response":{...},"error":null}`. There is no
    envelope to strip here.
    """
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


# --- Service and method discovery -------------------------------------------

def parse_service_index(raw: str) -> dict[str, tuple[str, int]]:
    """
    Turn the output of `grep -rn -A4 'function getName'` into an index.

    Returns service name -> (source file, line number of the return statement).
    The line number matters because one file can hold more than one RPC class
    (notification.inc defines both Notification and EmailNotification); without
    it their methods cannot be told apart later.
    """
    services: dict[str, tuple[str, int]] = {}
    for line in raw.splitlines():
        grep_match = GREP_LINE_RE.match(line)
        if not grep_match:
            continue
        name_match = RETURN_NAME_RE.search(grep_match.group("text"))
        if name_match:
            services[name_match.group("name")] = (
                grep_match.group("file"),
                int(grep_match.group("line")),
            )
    return services


def parse_methods(raw: str, decl_line: int) -> list[str]:
    """
    Extract the methods of a single RPC class from a file structure dump.

    `raw` is the output of a line-numbered grep matching both class
    declarations and registerMethod() calls. `decl_line` is the line of the
    getName() return for the service we want. We work out which class block
    that line falls in and return only the registerMethods inside that same
    block -- otherwise Notification would also inherit EmailNotification's
    methods.

    If the dump contains no class declaration at all we fall back to every
    method found, which is the correct behaviour for a single-class file.
    """
    class_lines: list[int] = []
    methods: list[tuple[int, str]] = []

    for line in raw.splitlines():
        head, sep, text = line.partition(":")
        if not sep or not head.isdigit():
            continue
        lineno = int(head)
        if CLASS_DECL_RE.match(text):
            class_lines.append(lineno)
            continue
        method_match = REGISTER_METHOD_RE.search(text)
        if method_match:
            methods.append((lineno, method_match.group("name")))

    if not class_lines:
        return sorted({name for _, name in methods})

    starts_before = [n for n in class_lines if n <= decl_line]
    class_start = max(starts_before) if starts_before else min(class_lines)
    later = [n for n in class_lines if n > class_start]
    class_end = min(later) if later else None

    return sorted({
        name for lineno, name in methods
        if lineno > class_start and (class_end is None or lineno < class_end)
    })


@lru_cache(maxsize=1)
def _service_index() -> dict[str, tuple[str, int]]:
    """
    Map RPC service names to their source file and declaration line.

    The service name is not the file name -- it is whatever the PHP method
    getName() returns -- so we read it out of the sources. The result is
    cached: it only changes when you install a plugin, in which case restart
    the MCP server.
    """
    raw = _exec(f"grep -rn -A4 'function getName' {shlex.quote(RPC_DIR)}/ || true")
    services = parse_service_index(raw)

    if not services:
        raise OmvError(
            f"No RPC services found in {RPC_DIR}. "
            "Is this actually an OpenMediaVault system, and can this user read "
            "that directory?"
        )
    return services


def _validate_ident(value: str, label: str) -> str:
    if not IDENT_RE.match(value or ""):
        raise OmvError(
            f"Invalid {label}: {value!r}. Only letters, digits and _ are allowed."
        )
    return value


# --- Tools ------------------------------------------------------------------

@mcp.tool()
def omv_list_services() -> list[str]:
    """
    List every available OpenMediaVault RPC service.

    This is the starting point: every action in the web interface belongs to
    one of these services (for example System, FileSystemMgmt, ShareMgmt,
    UserMgmt, Smart, Services). Installed plugins add their own services here.

    Note that some service names are lowercase (for example "kernel" and
    "omvextras") and omv-rpc is case sensitive, so use the name exactly as
    returned.
    """
    return sorted(_service_index().keys())


@mcp.tool()
def omv_list_methods(service: str) -> dict[str, Any]:
    """
    List every method of an RPC service.

    Call this before omv_call so you use the exact method name instead of
    guessing it.

    Args:
        service: A service name as returned by omv_list_services.
    """
    _validate_ident(service, "service name")

    services = _service_index()
    if service not in services:
        raise OmvError(
            f"Unknown service {service!r}. "
            f"Available: {', '.join(sorted(services)[:40])}..."
        )

    source, decl_line = services[service]
    raw = _exec(
        "grep -nE "
        "'^[[:space:]]*(abstract[[:space:]]+|final[[:space:]]+)?class[[:space:]]"
        "|registerMethod\\(' "
        f"{shlex.quote(source)}"
    )

    return {
        "service": service,
        "source": source,
        "methods": parse_methods(raw, decl_line),
    }


@mcp.tool()
def omv_call(service: str, method: str, params: dict | None = None,
             timeout: int | None = None) -> Any:
    """
    Call an OpenMediaVault RPC method. This is the main tool.

    Anything the web interface can do is available here. Some heavier methods
    run in the background and return {"filename": "..."} -- pass that value to
    omv_wait_for_task to collect the output.

    Args:
        service: For example "FileSystemMgmt", "System", "ShareMgmt".
        method:  For example "enumerateMountedFilesystems", "getInformation".
        params:  Parameters as a dict. Omit for methods that take none.
        timeout: Timeout in seconds for this specific call.

    Examples:
        omv_call("System", "getInformation")
        omv_call("FileSystemMgmt", "enumerateMountedFilesystems", {"includeRoot": True})
        omv_call("ShareMgmt", "enumerateSharedFolders")
    """
    _validate_ident(service, "service name")
    _validate_ident(method, "method name")

    if READONLY and not method.lower().startswith(READ_PREFIXES):
        raise OmvError(
            f"Refused: {service}.{method} does not look like a read method and "
            "OMV_READONLY is set. Set OMV_READONLY=0 to allow it."
        )

    cmd = f"omv-rpc -u {shlex.quote(RPC_USER)} {shlex.quote(service)} {shlex.quote(method)}"
    if params:
        cmd += f" {shlex.quote(json.dumps(params))}"

    return _parse(_exec(cmd, timeout=timeout))


@mcp.tool()
def omv_wait_for_task(filename: str, max_seconds: int = 120) -> dict[str, Any]:
    """
    Collect the output of a background task started by omv_call.

    When omv_call returns something like {"filename": "..."}, the job is
    running asynchronously on the NAS. This tool polls until it finishes.

    Args:
        filename: The filename value from the omv_call result.
        max_seconds: How long to wait before giving up.
    """
    if not re.match(r"^[A-Za-z0-9._\-/]+$", filename or ""):
        raise OmvError(f"Invalid filename: {filename!r}")

    output, pos = "", 0
    waited = 0

    while waited < max_seconds:
        result = omv_call("Exec", "getOutput", {"filename": filename, "pos": pos})
        if not isinstance(result, dict):
            return {"finished": True, "output": str(result)}

        output += result.get("output", "")
        pos = result.get("pos", pos)

        if not result.get("running", False):
            return {"finished": True, "output": output}

        time.sleep(2)
        waited += 2

    return {"finished": False, "output": output,
            "note": f"Still running after {max_seconds}s."}


if ALLOW_SHELL:
    @mcp.tool()
    def omv_shell(command: str, timeout: int | None = None) -> dict[str, Any]:
        """
        Run an arbitrary shell command on the NAS.

        Intended for everything outside the RPC layer: reading logs
        (journalctl), package status, docker commands, disk details and so on.

        Prefer omv_call for anything OMV manages itself, so that OMV's own
        configuration database stays in sync with the system.

        Args:
            command: The shell command.
            timeout: Timeout in seconds.
        """
        if READONLY:
            raise OmvError("Refused: OMV_READONLY is set.")
        if not command.strip():
            raise OmvError("Empty command.")
        return {"output": _exec(command, timeout=timeout)}


@mcp.tool()
def omv_connection_info() -> dict[str, Any]:
    """
    Show how this server connects and whether that works.

    Useful as a first smoke test and when troubleshooting connectivity.
    """
    info: dict[str, Any] = {
        "mode": f"ssh to {SSH_USER}@{SSH_HOST}:{SSH_PORT}" if SSH_HOST else "local",
        "rpc_user": RPC_USER,
        "sudo": USE_SUDO,
        "readonly": READONLY,
        "shell_enabled": ALLOW_SHELL,
    }
    try:
        # OMV 8 no longer ships /etc/openmediavault/version, so ask dpkg first.
        info["omv_version"] = _exec(
            "dpkg-query -W -f='${Version}' openmediavault 2>/dev/null "
            "|| cat /etc/openmediavault/version"
        ).strip()
        info["reachable"] = True
    except OmvError as exc:
        info["reachable"] = False
        info["error"] = str(exc)
    return info


def main() -> None:
    """Entry point for the `omv-mcp` console script."""
    mcp.run()


if __name__ == "__main__":
    main()
