"""Tests for omv_call, omv_wait_for_task, omv_shell and omv_connection_info."""

from __future__ import annotations

import json
import shlex
import unittest

from helpers import fixture, load_module


class TestOmvCall(unittest.TestCase):
    def _module(self, output="{}", **env):
        module = load_module(**env)
        self.commands = []

        def capture(cmd, timeout=None):
            self.commands.append((cmd, timeout))
            return output

        module._exec = capture
        return module

    def test_builds_command_without_parameters(self):
        module = self._module()
        module.omv_call("System", "getInformation")

        cmd, _ = self.commands[0]
        self.assertEqual(cmd, "omv-rpc -u admin System getInformation")

    def test_parameters_are_passed_as_json(self):
        module = self._module()
        module.omv_call("FileSystemMgmt", "enumerateMountedFilesystems", {"includeRoot": True})

        cmd, _ = self.commands[0]
        argv = shlex.split(cmd)
        self.assertEqual(argv[:5],
                         ["omv-rpc", "-u", "admin", "FileSystemMgmt",
                          "enumerateMountedFilesystems"])
        self.assertEqual(json.loads(argv[5]), {"includeRoot": True})

    def test_empty_parameters_are_omitted(self):
        module = self._module()
        module.omv_call("System", "getInformation", {})

        cmd, _ = self.commands[0]
        self.assertEqual(cmd, "omv-rpc -u admin System getInformation")

    def test_rpc_user_is_quoted(self):
        module = self._module(OMV_RPC_USER="admin")
        module.omv_call("System", "getInformation")
        self.assertIn("-u admin ", self.commands[0][0])

    def test_json_with_shell_metacharacters_is_quoted_safely(self):
        module = self._module()
        module.omv_call("Foo", "bar", {"name": "a'b; rm -rf /"})

        cmd, _ = self.commands[0]
        argv = shlex.split(cmd)
        # The whole payload arrives as a single argument: no stray ';' escapes
        # into the shell.
        self.assertEqual(len(argv), 6)
        self.assertEqual(json.loads(argv[5]), {"name": "a'b; rm -rf /"})

    def test_result_is_parsed(self):
        module = self._module(output=fixture("rpc_success.json"))
        result = module.omv_call("System", "getInformation")

        self.assertEqual(result["hostname"], "openmediavault")
        self.assertEqual(result["version"], "8.5.6-1 (Synchrony)")

    def test_timeout_is_forwarded(self):
        module = self._module()
        module.omv_call("System", "getInformation", timeout=5)
        self.assertEqual(self.commands[0][1], 5)

    def test_rejects_injection_in_service_name(self):
        module = self._module()
        for bad in ("System; id", "Sys tem", "../etc", "", "Sys$(id)"):
            with self.assertRaises(module.OmvError):
                module.omv_call(bad, "getInformation")
        self.assertEqual(self.commands, [])

    def test_rejects_injection_in_method_name(self):
        module = self._module()
        with self.assertRaises(module.OmvError):
            module.omv_call("System", "getInformation; id")
        self.assertEqual(self.commands, [])

    def test_lowercase_service_is_valid(self):
        """`kernel` and `omvextras` really exist; validation must not reject them."""
        module = self._module()
        module.omv_call("kernel", "getArch")
        self.assertIn("omv-rpc -u admin kernel getArch", self.commands[0][0])


class TestReadonly(unittest.TestCase):
    def _module(self):
        module = load_module(OMV_READONLY="1")
        self.commands = []
        module._exec = lambda cmd, timeout=None: self.commands.append(cmd) or "{}"
        return module

    def test_read_methods_are_allowed(self):
        module = self._module()
        for method in ("getInformation", "enumerateDevices", "listShares",
                       "isRunning", "hasQuota", "readLog", "queryStatus",
                       "findFile", "existsUser", "countItems", "checkPerms"):
            module.omv_call("System", method)
        self.assertEqual(len(self.commands), 11)

    def test_write_methods_are_blocked(self):
        module = self._module()
        for method in ("setSettings", "delete", "create", "applyChanges", "doReboot"):
            with self.assertRaises(module.OmvError) as ctx:
                module.omv_call("System", method)
            self.assertIn("OMV_READONLY", str(ctx.exception))
        self.assertEqual(self.commands, [])

    def test_casing_does_not_matter(self):
        module = self._module()
        module.omv_call("System", "GetInformation")
        self.assertEqual(len(self.commands), 1)

    def test_shell_is_blocked_in_readonly(self):
        module = self._module()
        with self.assertRaises(module.OmvError) as ctx:
            module.omv_shell("uptime")
        self.assertIn("OMV_READONLY", str(ctx.exception))


class TestOmvShell(unittest.TestCase):
    def test_disabled_does_not_register_the_tool(self):
        module = load_module(OMV_ALLOW_SHELL="0")
        self.assertFalse(hasattr(module, "omv_shell"))
        self.assertNotIn("omv_shell", module.mcp.tools)

    def test_enabled_registers_the_tool(self):
        module = load_module(OMV_ALLOW_SHELL="1")
        self.assertIn("omv_shell", module.mcp.tools)

    def test_shell_is_on_by_default(self):
        module = load_module()
        self.assertIn("omv_shell", module.mcp.tools)

    def test_returns_output(self):
        module = load_module()
        module._exec = lambda cmd, timeout=None: "01:50:28 up 4 days\n"
        self.assertEqual(module.omv_shell("uptime"), {"output": "01:50:28 up 4 days\n"})

    def test_empty_command_is_rejected(self):
        module = load_module()
        module._exec = lambda *a, **k: self.fail("should not have executed")
        with self.assertRaises(module.OmvError):
            module.omv_shell("   ")


class TestWaitForTask(unittest.TestCase):
    def _module(self, responses):
        module = load_module()
        self.calls = []

        def fake_call(service, method, params=None, timeout=None):
            self.calls.append(params)
            return responses[len(self.calls) - 1]

        module.omv_call = fake_call
        module.time.sleep = lambda s: None
        return module

    def test_task_that_is_already_done(self):
        module = self._module([{"output": "done\n", "pos": 5, "running": False}])
        result = module.omv_wait_for_task("/tmp/bgstatusXYZ")

        self.assertTrue(result["finished"])
        self.assertEqual(result["output"], "done\n")

    def test_output_is_concatenated_across_polls(self):
        module = self._module([
            {"output": "one\n", "pos": 4, "running": True},
            {"output": "two\n", "pos": 8, "running": True},
            {"output": "three\n", "pos": 14, "running": False},
        ])
        result = module.omv_wait_for_task("/tmp/bgstatusXYZ")

        self.assertEqual(result["output"], "one\ntwo\nthree\n")
        self.assertTrue(result["finished"])

    def test_pos_advances_so_nothing_is_read_twice(self):
        """The pos/output/running keys come from Exec::getOutput in OMV 8."""
        module = self._module([
            {"output": "one\n", "pos": 4, "running": True},
            {"output": "two\n", "pos": 8, "running": False},
        ])
        module.omv_wait_for_task("/tmp/bgstatusXYZ")

        self.assertEqual(self.calls[0]["pos"], 0)
        self.assertEqual(self.calls[1]["pos"], 4)

    def test_gives_up_after_max_seconds(self):
        module = self._module([{"output": "busy\n", "pos": 5, "running": True}] * 10)
        result = module.omv_wait_for_task("/tmp/bgstatusXYZ", max_seconds=4)

        self.assertFalse(result["finished"])
        self.assertIn("Still running", result["note"])

    def test_non_dict_result_ends_the_wait(self):
        module = self._module(["unexpected"])
        result = module.omv_wait_for_task("/tmp/bgstatusXYZ")

        self.assertTrue(result["finished"])
        self.assertEqual(result["output"], "unexpected")

    def test_rejects_suspicious_filename(self):
        module = load_module()
        module.omv_call = lambda *a, **k: self.fail("should not have been called")
        for bad in ("", "/tmp/x; rm -rf /", "$(id)", "a b"):
            with self.assertRaises(module.OmvError):
                module.omv_wait_for_task(bad)


class TestConnectionInfo(unittest.TestCase):
    def test_reports_configuration_and_version(self):
        module = load_module(OMV_SSH_HOST="nas.local", OMV_SSH_USER="root", OMV_RPC_USER="admin")
        module._exec = lambda *a, **k: "8.5.6-1"

        info = module.omv_connection_info()

        self.assertTrue(info["reachable"])
        self.assertEqual(info["omv_version"], "8.5.6-1")
        self.assertEqual(info["mode"], "ssh to root@nas.local:22")
        self.assertEqual(info["rpc_user"], "admin")

    def test_asks_dpkg_because_etc_version_is_gone_in_omv8(self):
        module = load_module()
        commands = []
        module._exec = lambda cmd, **k: commands.append(cmd) or "8.5.6-1"

        module.omv_connection_info()

        self.assertIn("dpkg-query", commands[0])
        self.assertLess(commands[0].index("dpkg-query"), commands[0].index("/etc/openmediavault"))

    def test_local_mode(self):
        module = load_module()
        module._exec = lambda *a, **k: "8.5.6-1"
        self.assertEqual(module.omv_connection_info()["mode"], "local")

    def test_unreachable_nas_does_not_raise(self):
        module = load_module(OMV_SSH_HOST="nas.local")

        def broken(*a, **k):
            raise module.OmvError("Permission denied (publickey).")

        module._exec = broken
        info = module.omv_connection_info()

        self.assertFalse(info["reachable"])
        self.assertIn("Permission denied", info["error"])
        self.assertNotIn("omv_version", info)


class TestToolRegistration(unittest.TestCase):
    def test_all_tools_are_registered(self):
        module = load_module()
        self.assertEqual(
            sorted(module.mcp.tools),
            ["omv_call", "omv_connection_info", "omv_list_methods",
             "omv_list_services", "omv_shell", "omv_wait_for_task"],
        )


if __name__ == "__main__":
    unittest.main()
