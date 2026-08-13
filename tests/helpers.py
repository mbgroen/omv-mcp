"""
Helpers for importing omv_mcp without the real MCP SDK.

The SDK requires Python 3.10+ and is irrelevant to the parsing and execution
logic under test, so we push a minimal stub into sys.modules that only mimics
`@mcp.tool()`. That decorator returns the function unchanged, which lets the
tests call the real tool functions directly.

All configuration in omv_mcp is read from os.environ at module level, so
testing a different configuration means re-importing the module. That is what
load_module() is for.
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


class StubServer:
    """Pretends to be an MCP server; records which tools were registered."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.tools: dict[str, object] = {}

    def tool(self, *args, **kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator

    def run(self):  # pragma: no cover - never reached in tests
        raise RuntimeError("StubServer.run() should not be called from tests")


def _install_mcp_stub() -> None:
    """
    Register a fake `mcp.server.fastmcp` package.

    `mcp.server.mcpserver` is deliberately left missing so omv_mcp takes its
    ImportError fallback to the 1.x name -- the same path a machine with SDK
    1.x installed would take.
    """
    if "mcp.server.fastmcp" in sys.modules:
        return

    mcp_pkg = types.ModuleType("mcp")
    mcp_pkg.__path__ = []  # mark as a package
    server_pkg = types.ModuleType("mcp.server")
    server_pkg.__path__ = []
    fastmcp_mod = types.ModuleType("mcp.server.fastmcp")
    fastmcp_mod.FastMCP = StubServer

    server_pkg.fastmcp = fastmcp_mod
    mcp_pkg.server = server_pkg

    sys.modules["mcp"] = mcp_pkg
    sys.modules["mcp.server"] = server_pkg
    sys.modules["mcp.server.fastmcp"] = fastmcp_mod


def load_module(**env: str):
    """
    Re-import omv_mcp with the given OMV_* environment variables.

    Variables that are not passed in are guaranteed to be absent, so a test
    never accidentally depends on the environment of whoever runs it.
    """
    _install_mcp_stub()

    clean = {k: v for k, v in os.environ.items() if not k.startswith("OMV_")}
    clean.update(env)

    with mock.patch.dict(os.environ, clean, clear=True):
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        sys.modules.pop("omv_mcp", None)
        return importlib.import_module("omv_mcp")


def fixture(name: str) -> str:
    """Read a captured piece of NAS output from tests/fixtures/."""
    return (FIXTURES / name).read_text()


def fake_completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Build a subprocess.CompletedProcess the way subprocess.run returns one."""
    import subprocess
    return subprocess.CompletedProcess(
        args=["dummy"], returncode=returncode, stdout=stdout, stderr=stderr
    )
