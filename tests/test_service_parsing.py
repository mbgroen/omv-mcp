"""
Tests for reading the service list out of OMV's RPC sources.

The fixture tests/fixtures/getname_grep.txt is real output of
`grep -rn -A4 'function getName' /usr/share/openmediavault/engined/rpc/`
on an OpenMediaVault 8.5.6-1 machine, so the expectations below reflect what
an actual NAS contains rather than an invented sample.
"""

from __future__ import annotations

import unittest

from helpers import fixture, load_module

omv = load_module()

# The six services that quote their name with single quotes. A parser that
# only understands double quotes drops exactly these.
SINGLE_QUOTED = {"AptTool", "Backup", "kernel", "Kvm", "omvextras", "Scripts"}


class TestParseServiceIndex(unittest.TestCase):
    def setUp(self):
        self.index = omv.parse_service_index(fixture("getname_grep.txt"))

    def test_finds_every_service_on_the_nas(self):
        self.assertEqual(len(self.index), 52)

    def test_finds_known_double_quoted_services(self):
        for name in ("System", "FileSystemMgmt", "ShareMgmt", "UserMgmt", "SMB", "Exec"):
            self.assertIn(name, self.index)

    def test_finds_single_quoted_services(self):
        """OMV's PHP sources mix ' and ", so the parser must accept both."""
        missing = SINGLE_QUOTED - set(self.index)
        self.assertEqual(missing, set(), f"missed single-quoted services: {missing}")

    def test_service_names_keep_their_casing(self):
        """`kernel` and `omvextras` really are lowercase; omv-rpc is case sensitive."""
        self.assertIn("kernel", self.index)
        self.assertNotIn("Kernel", self.index)

    def test_records_source_file_and_line_number(self):
        source, line = self.index["SMB"]
        self.assertEqual(source, "/usr/share/openmediavault/engined/rpc/smb.inc")
        self.assertEqual(line, 33)

    def test_two_services_in_one_file_get_distinct_line_numbers(self):
        """
        notification.inc holds two RPC classes. Without separate line numbers
        their methods cannot be told apart later on.
        """
        notif_file, notif_line = self.index["Notification"]
        email_file, email_line = self.index["EmailNotification"]

        self.assertEqual(notif_file, email_file)
        self.assertNotEqual(notif_line, email_line)
        self.assertLess(notif_line, email_line)

    def test_ignores_grep_group_separators(self):
        """`grep -A` prints '--' between groups; that is not a file path."""
        self.assertNotIn("--", self.index)

    def test_empty_input_yields_empty_index(self):
        self.assertEqual(omv.parse_service_index(""), {})

    def test_ignores_returns_that_are_not_literal_names(self):
        """`return $this->name;` is not a literal service name."""
        raw = (
            "/x/y.inc:10:    public function getName()\n"
            "/x/y.inc-11-    {\n"
            "/x/y.inc-12-        return $this->name;\n"
        )
        self.assertEqual(omv.parse_service_index(raw), {})

    def test_accepts_both_grep_separators(self):
        """Matching lines use ':' as separator, context lines use '-'."""
        with_colon = "/x/y.inc:12:        return 'Foo';\n"
        with_dash = "/x/y.inc-12-        return 'Foo';\n"
        self.assertEqual(omv.parse_service_index(with_colon), {"Foo": ("/x/y.inc", 12)})
        self.assertEqual(omv.parse_service_index(with_dash), {"Foo": ("/x/y.inc", 12)})


class TestListServicesTool(unittest.TestCase):
    """omv_list_services on top of the index."""

    def test_returns_sorted_names(self):
        module = load_module()
        module._exec = lambda *a, **k: fixture("getname_grep.txt")

        names = module.omv_list_services()

        self.assertEqual(names, sorted(names))
        self.assertEqual(len(names), 52)
        self.assertTrue(SINGLE_QUOTED.issubset(set(names)))

    def test_empty_directory_gives_an_actionable_error(self):
        module = load_module()
        module._exec = lambda *a, **k: ""

        with self.assertRaises(module.OmvError) as ctx:
            module.omv_list_services()
        self.assertIn("No RPC services found", str(ctx.exception))

    def test_index_is_cached(self):
        """Re-running the grep over SSH on every tool call would be needlessly slow."""
        module = load_module()
        calls = []

        def counting(*a, **k):
            calls.append(a)
            return fixture("getname_grep.txt")

        module._exec = counting
        module.omv_list_services()
        module.omv_list_services()

        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
