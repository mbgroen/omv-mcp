"""
Helpers for the test suite.

All configuration in omv_mcp is read from os.environ at module level, so
testing a different configuration means re-importing the module. That is what
load_module() is for.

There is nothing to install and nothing to stub: the server depends only on
the standard library.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_module(**env: str):
    """
    Re-import omv_mcp with the given OMV_* environment variables.

    Variables that are not passed in are guaranteed to be absent, so a test
    never accidentally depends on the environment of whoever runs it.
    """
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
