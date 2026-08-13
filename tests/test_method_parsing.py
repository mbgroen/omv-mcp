"""
Tests for reading a service's methods out of its source file.

The structure_*.txt fixtures are real output of a line-numbered grep over class
declarations and registerMethod() calls in the corresponding .inc files on an
OpenMediaVault 8.5.6-1 machine.
"""

from __future__ import annotations

import unittest

from helpers import fixture, load_module

omv = load_module()

# Line numbers of the `return "...";` inside getName(), as in the real sources.
NOTIFICATION_DECL = 33
EMAIL_NOTIFICATION_DECL = 271
KERNEL_DECL = 25


class TestParseMethods(unittest.TestCase):
    def test_single_quoted_methods_are_found(self):
        """
        kernel.inc registers every method with single quotes. A parser that
        only handles double quotes returns an empty list here, which reads as
        "this service has no methods".
        """
        methods = omv.parse_methods(fixture("structure_kernel.txt"), KERNEL_DECL)

        self.assertEqual(len(methods), 12)
        self.assertIn("getArch", methods)
        self.assertIn("getKernelList", methods)
        self.assertIn("writeIso", methods)

    def test_double_quoted_methods_are_found(self):
        methods = omv.parse_methods(fixture("structure_system.txt"), decl_line=10_000)
        self.assertTrue(methods)

    def test_methods_are_sorted_and_unique(self):
        methods = omv.parse_methods(fixture("structure_kernel.txt"), KERNEL_DECL)
        self.assertEqual(methods, sorted(methods))
        self.assertEqual(len(methods), len(set(methods)))

    def test_first_class_gets_only_its_own_methods(self):
        """
        notification.inc holds Notification (line 26) and EmailNotification
        (line 264). Without scoping, Notification would also be credited with
        sendTestEmail, a method it does not have.
        """
        methods = omv.parse_methods(fixture("structure_notification.txt"), NOTIFICATION_DECL)

        self.assertEqual(methods, ["get", "getList", "isEnabled", "set", "setList"])
        self.assertNotIn("sendTestEmail", methods)

    def test_second_class_gets_only_its_own_methods(self):
        methods = omv.parse_methods(
            fixture("structure_notification.txt"), EMAIL_NOTIFICATION_DECL
        )

        self.assertEqual(methods, ["get", "sendTestEmail", "set"])
        self.assertNotIn("getList", methods)
        self.assertNotIn("isEnabled", methods)

    def test_without_class_declaration_all_methods_are_returned(self):
        """Fallback for a dump that contains no class line."""
        raw = "10:        $this->registerMethod('a');\n11:        $this->registerMethod(\"b\");\n"
        self.assertEqual(omv.parse_methods(raw, decl_line=1), ["a", "b"])

    def test_declaration_before_first_class_falls_back_to_first_block(self):
        raw = (
            "20:class Foo extends \\OMV\\Rpc\\ServiceAbstract\n"
            "25:        $this->registerMethod('one');\n"
        )
        self.assertEqual(omv.parse_methods(raw, decl_line=5), ["one"])

    def test_ignores_lines_without_a_line_number(self):
        raw = "no line number here\n10:class Foo extends X\n12:  $this->registerMethod('ok');\n"
        self.assertEqual(omv.parse_methods(raw, decl_line=11), ["ok"])

    def test_empty_input(self):
        self.assertEqual(omv.parse_methods("", decl_line=1), [])


class TestListMethodsTool(unittest.TestCase):
    def _module_with(self, index, structure):
        module = load_module()
        module._service_index = lambda: index
        module._exec = lambda *a, **k: structure
        return module

    def test_returns_service_source_and_methods(self):
        module = self._module_with(
            {"Notification": ("/usr/share/openmediavault/engined/rpc/notification.inc", 33)},
            fixture("structure_notification.txt"),
        )

        result = module.omv_list_methods("Notification")

        self.assertEqual(result["service"], "Notification")
        self.assertTrue(result["source"].endswith("notification.inc"))
        self.assertEqual(result["methods"], ["get", "getList", "isEnabled", "set", "setList"])

    def test_unknown_service_suggests_alternatives(self):
        module = self._module_with({"System": ("/x/system.inc", 30)}, "")

        with self.assertRaises(module.OmvError) as ctx:
            module.omv_list_methods("DoesNotExist")
        message = str(ctx.exception)
        self.assertIn("Unknown service", message)
        self.assertIn("System", message)

    def test_rejects_invalid_service_name(self):
        module = self._module_with({}, "")

        with self.assertRaises(module.OmvError) as ctx:
            module.omv_list_methods("Sys; rm -rf /")
        self.assertIn("Invalid service name", str(ctx.exception))

    def test_source_path_is_shell_quoted(self):
        """The path comes from the index, but it still passes through a shell."""
        module = load_module()
        module._service_index = lambda: {"Foo": ("/path with space/foo.inc", 10)}
        commands = []

        def capture(cmd, **k):
            commands.append(cmd)
            return ""

        module._exec = capture
        module.omv_list_methods("Foo")

        self.assertIn("'/path with space/foo.inc'", commands[0])


if __name__ == "__main__":
    unittest.main()
