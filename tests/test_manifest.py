"""
Tests for manifest.json, the Claude Desktop extension descriptor.

The manifest is not exercised by any other test: it is read by Claude Desktop,
not by this code, so a mistake in it shows up as an extension that silently
will not install. These checks stand in for that feedback loop.

The version-range check exists because of a real failure. The MCPB manifest
specification documents a Python requirement as ">=3.8,<4.0", which is pip
syntax. Claude Desktop evaluates it with node-semver, where a comma is not a
separator and ranges are ANDed with a space. semver.satisfies() swallows the
resulting parse error and returns false, so the requirement could never be
satisfied by any interpreter and the extension refused to install.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from helpers import ROOT, load_module

MANIFEST = json.loads((ROOT / "manifest.json").read_text())

# node-semver's own grammar, from src/internal/re.js. A range is split on
# whitespace and every token must be a comparator or an X-range.
_NUM = r"0|[1-9]\d*"
_NONNUM = r"\d*[a-zA-Z-][a-zA-Z0-9-]*"
_MAIN = rf"({_NUM})\.({_NUM})\.({_NUM})"
_PREID = rf"(?:{_NUM}|{_NONNUM})"
_PRE = rf"(?:-({_PREID}(?:\.{_PREID})*))"
_BUILDID = r"[0-9A-Za-z-]+"
_BUILD = rf"(?:\+({_BUILDID}(?:\.{_BUILDID})*))"
_FULLPLAIN = rf"v?{_MAIN}{_PRE}?{_BUILD}?"
_XID = rf"{_NUM}|x|X|\*"
_XPLAIN = rf"[v=\s]*({_XID})(?:\.({_XID})(?:\.({_XID})(?:{_PRE})?{_BUILD}?)?)?"
_GTLT = r"((?:<|>)?=?)"

COMPARATOR_RE = re.compile(rf"^{_GTLT}\s*({_FULLPLAIN})$|^$")
XRANGE_RE = re.compile(rf"^{_GTLT}\s*{_XPLAIN}$")


def is_valid_semver_range(value: str) -> bool:
    """Would node-semver's Range constructor accept this string?"""
    for alternative in value.split("||"):
        for token in alternative.strip().split():
            if not (XRANGE_RE.match(token) or COMPARATOR_RE.match(token)):
                return False
    return True


class TestSemverRangeHelper(unittest.TestCase):
    """The guard is only worth having if it actually rejects the bad syntax."""

    def test_accepts_node_semver_syntax(self):
        for value in (">=3.9 <4", ">=3.9", ">=3.9.0 <4.0.0", ">=0.10.0", "1.x || >=2.5.0"):
            self.assertTrue(is_valid_semver_range(value), value)

    def test_rejects_pip_style_comma_ranges(self):
        for value in (">=3.9,<4.0", ">=3.8,<4.0", ">=1.0,<2"):
            self.assertFalse(is_valid_semver_range(value), value)


class TestManifestStructure(unittest.TestCase):
    def test_required_fields_are_present(self):
        for field in ("manifest_version", "name", "version", "description",
                      "author", "server"):
            self.assertIn(field, MANIFEST)

    def test_author_has_a_name(self):
        self.assertTrue(MANIFEST["author"].get("name"))

    def test_entry_point_matches_what_the_builder_writes(self):
        self.assertEqual(MANIFEST["server"]["entry_point"], "server/main.py")

    def test_declared_icon_exists(self):
        self.assertTrue((ROOT / MANIFEST["icon"]).is_file())

    def test_windows_gets_its_own_python_command(self):
        """`python3` is usually absent on Windows; `python` is the one on PATH."""
        overrides = MANIFEST["server"]["mcp_config"]["platform_overrides"]
        self.assertEqual(overrides["win32"]["command"], "python")


class TestCompatibilityRanges(unittest.TestCase):
    def test_every_range_is_valid_node_semver(self):
        compatibility = MANIFEST["compatibility"]
        ranges = dict(compatibility.get("runtimes", {}))
        if "claude_desktop" in compatibility:
            ranges["claude_desktop"] = compatibility["claude_desktop"]

        for label, value in ranges.items():
            with self.subTest(label):
                self.assertTrue(
                    is_valid_semver_range(value),
                    f"{label} = {value!r} is not a node-semver range. "
                    "Use a space to combine bounds, not a comma.",
                )

    def test_the_declared_python_floor_matches_reality(self):
        """The suite itself runs on 3.9, so the floor must not be higher."""
        self.assertEqual(MANIFEST["compatibility"]["runtimes"]["python"], ">=3.9 <4")


class TestVersionConsistency(unittest.TestCase):
    def test_manifest_matches_the_module(self):
        module = load_module()
        self.assertEqual(MANIFEST["version"], module.__version__)

    def test_manifest_matches_pyproject(self):
        pyproject = (ROOT / "pyproject.toml").read_text()
        match = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(MANIFEST["version"], match.group(1))


class TestToolDeclarations(unittest.TestCase):
    def test_declared_tools_match_the_registered_ones(self):
        module = load_module(OMV_ALLOW_SHELL="1")
        declared = {tool["name"] for tool in MANIFEST["tools"]}
        self.assertEqual(declared, set(module.mcp.tools))

    def test_every_declared_tool_has_a_description(self):
        for tool in MANIFEST["tools"]:
            self.assertTrue(tool.get("description"), tool["name"])


class TestUserConfigWiring(unittest.TestCase):
    def setUp(self):
        self.env = MANIFEST["server"]["mcp_config"]["env"]
        self.user_config = MANIFEST["user_config"]

    def test_every_referenced_setting_exists(self):
        """A typo in a ${user_config.x} reference would pass through as a literal."""
        for variable, template in self.env.items():
            for key in re.findall(r"\$\{user_config\.(\w+)\}", template):
                self.assertIn(key, self.user_config, f"{variable} references unknown {key}")

    def test_every_setting_is_wired_to_an_environment_variable(self):
        referenced = set()
        for template in self.env.values():
            referenced.update(re.findall(r"\$\{user_config\.(\w+)\}", template))
        self.assertEqual(set(self.user_config), referenced)

    def test_environment_variables_are_ones_the_server_reads(self):
        source = (ROOT / "omv_mcp.py").read_text()
        known = set(re.findall(r'"(OMV_[A-Z_]+)"', source))
        for variable in self.env:
            self.assertIn(variable, known, f"{variable} is not read by omv_mcp.py")

    def test_the_nas_address_is_the_only_required_setting(self):
        required = {k for k, v in self.user_config.items() if v.get("required")}
        self.assertEqual(required, {"ssh_host"})

    def test_safe_defaults(self):
        """Read-only on and shell off is the right way round for a first install."""
        self.assertIs(self.user_config["readonly"]["default"], True)
        self.assertIs(self.user_config["allow_shell"]["default"], False)

    def test_every_setting_has_a_title_and_description(self):
        for key, spec in self.user_config.items():
            self.assertTrue(spec.get("title"), key)
            self.assertTrue(spec.get("description"), key)


if __name__ == "__main__":
    unittest.main()
