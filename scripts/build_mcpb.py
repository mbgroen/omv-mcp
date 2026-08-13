#!/usr/bin/env python3
"""
Build the .mcpb extension bundle.

An .mcpb file is a zip archive with manifest.json at its root, so this needs
nothing but the standard library -- no Node.js and no `mcpb` CLI. Run from the
repository root:

    python3 scripts/build_mcpb.py

The result lands in dist/omv-mcp-<version>.mcpb, ready to double-click.
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

# Files copied into the bundle's server/ directory. Keeping the sources at the
# repository root means the manual install and the extension run identical
# code; the bundle is only a different wrapper around it.
SERVER_MODULES = ("omv_mcp.py", "mcp_stdio.py")
BUNDLE_EXTRAS = ("icon.png", "README.md", "LICENSE")

LAUNCHER = '''#!/usr/bin/env python3
"""
Entry point for the packaged OpenMediaVault extension.

Claude Desktop starts this file with the host's Python. The parent directory
is added to sys.path explicitly so `import omv_mcp` resolves no matter how the
process is launched.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from omv_mcp import main  # noqa: E402

if __name__ == "__main__":
    main()
'''


def read_manifest() -> dict:
    manifest = json.loads((ROOT / "manifest.json").read_text())

    required = ("manifest_version", "name", "version", "description", "author", "server")
    missing = [field for field in required if field not in manifest]
    if missing:
        raise SystemExit(f"manifest.json is missing required fields: {', '.join(missing)}")

    return manifest


def module_version() -> str:
    """Read __version__ out of omv_mcp.py without importing it."""
    source = (ROOT / "omv_mcp.py").read_text()
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', source, re.MULTILINE)
    if not match:
        raise SystemExit("Could not find __version__ in omv_mcp.py")
    return match.group(1)


def check_consistency(manifest: dict) -> None:
    """
    Fail the build when the manifest and the code disagree.

    A bundle that reports one version while running another is the kind of
    thing you only notice when debugging something else entirely.
    """
    if manifest["version"] != module_version():
        raise SystemExit(
            f"Version mismatch: manifest.json says {manifest['version']}, "
            f"omv_mcp.py says {module_version()}"
        )

    declared = {tool["name"] for tool in manifest.get("tools", [])}
    source = (ROOT / "omv_mcp.py").read_text()
    actual = set(re.findall(r"^def (omv_\w+)", source, re.MULTILINE))
    actual |= set(re.findall(r"^    def (omv_\w+)", source, re.MULTILINE))

    if declared != actual:
        raise SystemExit(
            "manifest.json tool list is out of step with omv_mcp.py.\n"
            f"  only in manifest: {sorted(declared - actual) or 'none'}\n"
            f"  only in code:     {sorted(actual - declared) or 'none'}"
        )

    entry = manifest["server"]["entry_point"]
    if entry != "server/main.py":
        raise SystemExit(f"Unexpected entry_point {entry!r}; the builder writes server/main.py")


def build(manifest: dict) -> Path:
    DIST.mkdir(exist_ok=True)
    target = DIST / f"{manifest['name']}-{manifest['version']}.mcpb"

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("manifest.json", (ROOT / "manifest.json").read_text())
        bundle.writestr("server/main.py", LAUNCHER)

        for name in SERVER_MODULES:
            bundle.write(ROOT / name, f"server/{name}")

        for name in BUNDLE_EXTRAS:
            path = ROOT / name
            if path.exists():
                bundle.write(path, name)
            else:
                print(f"  note: {name} not found, leaving it out", file=sys.stderr)

    return target


def main() -> None:
    manifest = read_manifest()
    check_consistency(manifest)
    target = build(manifest)

    size_kb = target.stat().st_size / 1024
    print(f"built {target.relative_to(ROOT)} ({size_kb:.0f} KB)")
    print("install it by double-clicking the file, or via")
    print("  Claude Desktop -> Settings -> Extensions -> Advanced settings -> Install Extension")


if __name__ == "__main__":
    main()
