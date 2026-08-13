# omv-mcp

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant manage an
**OpenMediaVault** NAS — check disks and filesystems, inspect shares and users, read
S.M.A.R.T. health, restart services, apply configuration changes, run background jobs and
tail their output.

It connects over plain SSH and drives OMV's own `omv-rpc` CLI, which is the exact same
RPC layer the web interface uses. Nothing gets installed on the NAS and no extra port is
opened.

```
MCP client  --stdio-->  omv_mcp.py  --ssh-->  NAS  -->  omv-rpc  -->  OMV RPC layer
 (your PC)              (your PC)                        (the same layer as the web UI)
```

**No dependencies.** The server is pure standard library — including its MCP protocol
layer — so there is no virtualenv to create, nothing to `pip install`, and it runs on any
Python 3.9 or newer.

## Why only six tools

OMV exposes roughly 50 RPC services with several hundred methods between them, and every
plugin adds more. Rather than wrapping each one in a hand-written tool, omv-mcp exposes a
small generic set: *discover the services*, *discover a service's methods*, *call a
method*. The assistant explores the API the same way a developer would.

The practical benefit: any plugin you install later — Docker/Compose, ZFS, K8s, whatever —
works immediately, with no update to this server.

## Requirements

| | |
|---|---|
| **NAS** | OpenMediaVault 7 or 8, reachable over SSH |
| **Your machine** | Python 3.9 or newer. That includes the Python already on macOS and most Linux systems; on Windows, install it from [python.org](https://www.python.org/downloads/) or the Microsoft Store. |
| **Access** | An SSH key that can log in without a password, and a user who may run `omv-rpc` |

Developed and verified against **OpenMediaVault 8.5.6-1 (Synchrony)** on Debian 13.
OMV 7 uses the same RPC layer and is expected to work; reports welcome.

### Platform support

| Your machine | Extension (`.mcpb`) | Manual setup | Notes |
|---|---|---|---|
| **macOS** | Yes | Yes | Python 3.9 ships with the system; `ssh` is present |
| **Windows** | Yes | Yes | Python is **not** included — install it from [python.org](https://www.python.org/downloads/) or the Microsoft Store first. `ssh` ships with Windows 10 1809 and later. |
| **Linux** | — | Yes | Claude Desktop is released for macOS and Windows only, so there is no extension to install. Everything else works: use Claude Code or any other MCP client. |

The server itself is verified on macOS (Python 3.9) and Linux (Python 3.13). Windows is
covered by CI but has not been run against a real NAS — reports welcome.

---

## Install as a Claude Desktop extension (macOS and Windows)

The extension gives you a settings panel — NAS address, SSH user, key, read-only mode —
so nothing has to be edited in any file. On Linux, skip to
[Install manually](#install-manually).

1. Download `omv-mcp-<version>.mcpb` from the
   [latest release](https://github.com/mbgroen/omv-mcp/releases/latest).
2. Double-click it, or drag it onto the Claude Desktop window. (Also available under
   *Settings → Extensions → Advanced settings → Install Extension…*)
3. Fill in at least the **NAS hostname or IP address**, then enable the extension.

<!-- Settings offered by the extension -->

| Setting | Default | What it does |
|---|---|---|
| NAS hostname or IP address | — | Required. An IP, or a host from your `~/.ssh/config`. |
| SSH username | `root` | The account used to log in |
| SSH port | `22` | Change only for a non-standard port |
| SSH private key | *(empty)* | Optional; empty uses your ssh-agent and `~/.ssh/config` |
| OpenMediaVault user | `admin` | The OMV login the RPC runs as — not the SSH user |
| Run commands with sudo | off | Turn on when the SSH user is not root |
| **Read-only mode** | **on** | Refuses anything that is not a read operation |
| Allow arbitrary shell commands | off | Adds the unrestricted `omv_shell` tool |
| Command timeout | `60`s | Per-command limit |

Read-only mode is **on by default**. Turn it off once you are comfortable letting Claude
change things.

You still need working SSH key access to the NAS — see [SSH setup](#ssh-setup) below.

### Building the bundle yourself

```bash
git clone https://github.com/mbgroen/omv-mcp.git
cd omv-mcp
python3 scripts/build_mcpb.py
```

The `.mcpb` lands in `dist/`. It is an ordinary zip archive with a `manifest.json` at the
root, so the build script needs nothing but the standard library — no Node.js, no `mcpb`
CLI.

---

## Install manually

Useful for Claude Code, for other MCP clients, or if you would rather not use an
extension.

```bash
git clone https://github.com/mbgroen/omv-mcp.git
```

There is nothing to install. Point your client at `omv_mcp.py` with your system Python.

**Claude Code** — works the same on macOS, Linux and Windows:

```bash
claude mcp add openmediavault -e OMV_SSH_HOST=192.168.1.100 -e OMV_READONLY=1 -- python3 "$PWD/omv_mcp.py"
```

Add `-s user` to make it available in every project rather than only the current one.
Check it with `claude mcp list`, which reports `✔ Connected` once the handshake succeeds.

**Any other MCP client** — Cursor, Zed, Cline, VS Code, the OpenAI Agents SDK and others
take the same three ingredients: the command `python3`, the argument
`/absolute/path/to/omv_mcp.py`, and the environment variables below. This is a standard
stdio MCP server with no client-specific behaviour.

**Claude Desktop**, in `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS), `%APPDATA%\Claude\claude_desktop_config.json` (Windows) or
`~/.config/Claude/claude_desktop_config.json` (Linux, unofficial builds):

```json
{
  "mcpServers": {
    "openmediavault": {
      "command": "python3",
      "args": ["/absolute/path/to/omv-mcp/omv_mcp.py"],
      "env": {
        "OMV_SSH_HOST": "192.168.1.100",
        "OMV_SSH_USER": "root",
        "OMV_RPC_USER": "admin",
        "OMV_READONLY": "1"
      }
    }
  }
}
```

Use absolute paths, not `~`. Restart the client completely afterwards — closing the
window is not enough for Claude Desktop.

### Environment variables

The extension sets these for you; this table is for manual setups.

| Variable | Default | Meaning |
|---|---|---|
| `OMV_SSH_HOST` | *(empty)* | Hostname or IP of the NAS. Empty means run commands locally — use that if you run this server *on* the NAS. |
| `OMV_SSH_USER` | `root` | SSH user |
| `OMV_SSH_PORT` | `22` | SSH port |
| `OMV_SSH_KEY` | *(empty)* | Path to a private key. Leave empty to use your ssh-agent and `~/.ssh/config`. |
| `OMV_RPC_USER` | `admin` | The OMV user the RPC call runs as |
| `OMV_SUDO` | `0` | `1` prefixes every command with `sudo`, for non-root SSH users |
| `OMV_READONLY` | `0` | `1` refuses anything that does not look like a read method, and disables `omv_shell` |
| `OMV_ALLOW_SHELL` | `1` | `0` removes the `omv_shell` tool entirely |
| `OMV_TIMEOUT` | `60` | Per-command timeout in seconds |

Booleans accept `1`/`true`/`yes`/`on` and their opposites.

### Running on the NAS itself

Leave `OMV_SSH_HOST` empty and commands run locally instead of over SSH, so the server can
live on the NAS alongside OMV. This is only useful when the MCP client also runs there —
Claude Code over an SSH session, for example — because stdio means the client starts the
server as a child process.

```bash
OMV_SSH_HOST= python3 /path/to/omv_mcp.py
```

`omv_connection_info` reports `"mode": "local"` when it is working this way. OMV 8 ships
Python 3.13, so nothing extra is needed on the NAS.

---

## SSH setup

The server runs `ssh` with `BatchMode=yes`, so password logins will not work. This is
deliberate: an MCP server has no way to prompt you for a password. Use a key.

```bash
ssh-keygen -t ed25519 -C "omv-mcp"        # skip if you already have a key
ssh-copy-id root@192.168.1.100            # use your own NAS address
ssh root@192.168.1.100 'omv-rpc -u admin System getInformation'
```

If that last command prints JSON, the server will work.

**Root login refused?** OMV disables SSH root login by default. Either enable it under
*Services → SSH → Permit root login*, or use your own account and turn on sudo — that
account needs to be in the `sudo` group.

Because `~/.ssh/config` is honoured, you can keep the details there instead:

```
Host nas
    HostName 192.168.1.100
    User root
    IdentityFile ~/.ssh/id_ed25519
```

and then use `nas` as the hostname.

## Verify

Ask your assistant *"Is my OMV connection working?"*. It should call `omv_connection_info`
and report your OMV version.

---

## Tools

| Tool | Purpose |
|---|---|
| `omv_connection_info` | Show the active configuration and test connectivity |
| `omv_list_services` | List all RPC services, including those added by plugins |
| `omv_list_methods` | List the methods of one service |
| `omv_call` | Call an RPC method — the main tool |
| `omv_wait_for_task` | Collect the output of a background job |
| `omv_shell` | Run an arbitrary shell command on the NAS |

### omv_call

The workhorse. Takes a service, a method, and optionally a parameter dict:

```python
omv_call("System", "getInformation")
omv_call("FileSystemMgmt", "enumerateMountedFilesystems", {"includeRoot": True})
omv_call("ShareMgmt", "enumerateSharedFolders")
omv_call("Smart", "getListBg", {"start": 0, "limit": -1})
```

A few services to know about:

| Service | What it covers |
|---|---|
| `System` | System information, time settings, reboot and shutdown |
| `FileSystemMgmt`, `DiskMgmt`, `FsTab` | Disks, filesystems, mount points |
| `ShareMgmt` | Shared folders and their permissions |
| `UserMgmt` | Users and groups |
| `Smart` | S.M.A.R.T. health and scheduled tests |
| `Services` | Status of the service daemons |
| `SMB`, `NFS`, `FTP`, `Rsync` | The individual file-sharing services |
| `Config` | Pending configuration changes and applying them |
| `Exec` | Background job control |

Rather than memorising these, let the assistant call `omv_list_services` and
`omv_list_methods` — that always reflects your actual installation.

### Background jobs

Heavier methods return `{"filename": "..."}` and keep running on the NAS. Pass that
filename to `omv_wait_for_task`, which polls until the job finishes and returns the
accumulated output:

```python
result = omv_call("Apt", "upgrade")           # -> {"filename": "/tmp/bgstatus..."}
omv_wait_for_task(result["filename"], max_seconds=600)
```

### omv_shell

An escape hatch for everything outside the RPC layer — `journalctl`, `docker ps`,
`smartctl`, package state. Prefer `omv_call` for anything OMV manages itself, so OMV's
configuration database stays in sync with the system. Disabled by default in the
extension.

---

## Security

**This server can do anything you can do in the OMV web interface, and — if you enable the
shell tool — run arbitrary commands as root.** That is the point of it, but be clear-eyed
about what it means: the only thing standing between a mistaken suggestion and a wiped
filesystem is the tool confirmation dialog in your MCP client. Read what it says before
approving.

Sensible precautions:

- **Keep read-only mode on** until you have a feel for what the assistant does with it.
- **Leave the shell tool off** unless you actually need it.
- **Use a dedicated SSH key** for this server rather than your everyday key.
- **Consider a non-root user** with sudo and a narrowed sudoers rule.
- **Keep it on your LAN.** There is no authentication in this server itself; its security
  boundary is your SSH configuration.

Read-only mode uses a prefix heuristic — a method is allowed if its name starts with
`get`, `enumerate`, `list`, `is`, `has`, `read`, `query`, `find`, `exists`, `count` or
`check`. It is deliberately conservative and will occasionally block a harmless method.
It is a guard rail, not a security boundary: it cannot stop a read method that happens to
have side effects.

Service and method names are validated against `^[A-Za-z0-9_]+$` and every value
interpolated into a shell command is passed through `shlex.quote`, so parameters cannot
break out into the shell.

---

## How it works

### Discovery

An RPC service's name is not its file name — it is whatever the PHP `getName()` method
returns. So `omv_list_services` greps the sources in
`/usr/share/openmediavault/engined/rpc/` and reads the names out. The result is cached for
the lifetime of the process; restart the server after installing a plugin.

Three details about OMV's sources that this parser handles, and which are easy to get
wrong if you write your own:

1. **Quoting is inconsistent.** OMV's PHP mixes `'` and `"` freely. On OMV 8.5, six of the
   52 service names and about a third of all `registerMethod()` calls use single quotes.
   Matching only double quotes silently loses them.
2. **Some service names are lowercase** — `kernel` and `omvextras`, for instance — and
   `omv-rpc` is case sensitive. Use names exactly as `omv_list_services` returns them.
3. **One file can hold several RPC classes.** `notification.inc` defines both
   `Notification` and `EmailNotification`, so methods are scoped to the class block they
   appear in rather than to the whole file.

Errors are cleaned up too: a failed `omv-rpc` call writes a JSON blob to stderr containing
a full PHP stack trace, and only the `message` field is surfaced.

### The MCP layer

`mcp_stdio.py` implements the protocol directly: newline-delimited JSON-RPC 2.0 over
stdio, `initialize` with version negotiation, `tools/list` with input schemas derived from
each function's signature and docstring, and `tools/call`.

That is a deliberate choice rather than an exercise. The official MCP Python SDK depends
on pydantic, which ships compiled binaries — and the MCPB documentation is explicit that
you [cannot portably bundle compiled dependencies](https://github.com/modelcontextprotocol/mcpb#python-servers).
Implementing the handful of methods a tools-only server needs keeps the extension a single
26 KB file that works on macOS, Windows and Linux alike, with no runtime to install and no
Python version floor beyond 3.9.

---

## Tests

No dependencies, nothing to install:

```bash
python3 -m unittest discover -s tests -t tests -v
```

Or with pytest, if you prefer its output:

```bash
pip install pytest && pytest
```

The fixtures under `tests/fixtures/` are real output captured from an OpenMediaVault
8.5.6-1 system (grep dumps of the RPC sources, a successful RPC response and an error
response), with host-identifying values replaced. The tests therefore assert against what
a NAS actually returns rather than against an idealised sample.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Extension will not install: "Python >=…" unmet | The manifest's version range must be node-semver (`>=3.9 <4`), not pip syntax (`>=3.9,<4.0`). Fixed in 1.1.1; upgrade if you see this. |
| Extension will not start | No `python3` on PATH (`python` on Windows). Check with `python3 --version`. |
| Server does not appear in the client | JSON syntax error, or a relative path in a manual config |
| `Permission denied (publickey)` | SSH key not installed, or root login refused by the NAS |
| `No RPC services found` | The SSH user cannot read `/usr/share/openmediavault/engined/rpc` |
| `command not found: omv-rpc` | `/usr/sbin` is not in the SSH user's PATH — turn on sudo |
| `Command exceeded 60s` | Raise the timeout, or use `omv_wait_for_task` for long jobs |
| A method is refused as "not a read method" | Read-only mode is on |
| A newly installed plugin is invisible | Restart the MCP server; the service list is cached |

Claude Desktop writes per-server logs to
`~/Library/Logs/Claude/mcp-server-openmediavault.log` on macOS.

## Contributing

Issues and pull requests are welcome — particularly reports from OMV 7, from plugins whose
RPC sources are laid out unusually, and from setups where discovery finds fewer services
than the web UI offers.

Please run the test suite before opening a PR. If you are fixing a parsing issue, add a
fixture captured from the real system alongside it; that is how the existing tests are
built.

## License

MIT — see [LICENSE](LICENSE).

This project is not affiliated with or endorsed by the OpenMediaVault project.
