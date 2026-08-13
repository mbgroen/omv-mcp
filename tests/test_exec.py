"""Tests for the execution layer: how commands reach the NAS and how errors return."""

from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from helpers import fake_completed, fixture, load_module


class TestSshPrefix(unittest.TestCase):
    def test_local_mode_does_not_use_ssh(self):
        module = load_module()  # no OMV_SSH_HOST
        with mock.patch("subprocess.run", return_value=fake_completed("hi")) as run:
            module._exec("echo hi")

        argv = run.call_args[0][0]
        self.assertEqual(argv, ["/bin/sh", "-c", "echo hi"])

    def test_ssh_mode_builds_the_connection(self):
        module = load_module(OMV_SSH_HOST="nas.local", OMV_SSH_USER="root", OMV_SSH_PORT="2222")
        with mock.patch("subprocess.run", return_value=fake_completed("")) as run:
            module._exec("uptime")

        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "ssh")
        self.assertIn("root@nas.local", argv)
        self.assertIn("2222", argv)
        self.assertEqual(argv[-1], "uptime")

    def test_batchmode_is_always_on(self):
        """An MCP server cannot prompt interactively for a password."""
        module = load_module(OMV_SSH_HOST="nas.local")
        with mock.patch("subprocess.run", return_value=fake_completed("")) as run:
            module._exec("uptime")

        self.assertIn("BatchMode=yes", run.call_args[0][0])

    def test_key_path_is_expanded(self):
        module = load_module(OMV_SSH_HOST="nas.local", OMV_SSH_KEY="~/.ssh/id_ed25519")
        with mock.patch("subprocess.run", return_value=fake_completed("")) as run:
            module._exec("uptime")

        argv = run.call_args[0][0]
        key = argv[argv.index("-i") + 1]
        self.assertFalse(key.startswith("~"))
        self.assertTrue(key.endswith("id_ed25519"))

    def test_sudo_is_prefixed_to_the_command(self):
        module = load_module(OMV_SSH_HOST="nas.local", OMV_SUDO="1")
        with mock.patch("subprocess.run", return_value=fake_completed("")) as run:
            module._exec("omv-rpc -u admin System getInformation")

        self.assertTrue(run.call_args[0][0][-1].startswith("sudo "))


class TestErrorHandling(unittest.TestCase):
    def test_non_zero_exit_raises(self):
        module = load_module()
        with mock.patch("subprocess.run",
                        return_value=fake_completed(stderr="bash: omv-rpc: not found", returncode=127)):
            with self.assertRaises(module.OmvError) as ctx:
                module._exec("omv-rpc")

        self.assertIn("127", str(ctx.exception))
        self.assertIn("not found", str(ctx.exception))

    def test_rpc_error_is_reduced_to_its_message(self):
        """
        A real omv-rpc failure is 363 bytes of JSON wrapping a PHP stack trace.
        Only `message` is useful to a caller.
        """
        module = load_module()
        with mock.patch("subprocess.run",
                        return_value=fake_completed(stderr=fixture("rpc_error.json"), returncode=1)):
            with self.assertRaises(module.OmvError) as ctx:
                module._exec("omv-rpc -u admin kernel doesNotExist")

        message = str(ctx.exception)
        self.assertEqual(
            message,
            "The method 'doesNotExist' does not exist for the RPC service 'kernel'.",
        )
        self.assertNotIn("Stack trace", message)
        self.assertNotIn("trace", message)

    def test_plain_stderr_stays_readable(self):
        """Non-JSON stderr must not be swallowed."""
        module = load_module()
        with mock.patch("subprocess.run",
                        return_value=fake_completed(stderr="Permission denied (publickey).", returncode=255)):
            with self.assertRaises(module.OmvError) as ctx:
                module._exec("uptime")

        self.assertIn("Permission denied", str(ctx.exception))

    def test_timeout_gives_an_actionable_hint(self):
        module = load_module()
        with mock.patch("subprocess.run",
                        side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=60)):
            with self.assertRaises(module.OmvError) as ctx:
                module._exec("sleep 999", timeout=60)

        message = str(ctx.exception)
        self.assertIn("60s", message)
        self.assertIn("OMV_TIMEOUT", message)

    def test_missing_ssh_binary(self):
        module = load_module(OMV_SSH_HOST="nas.local")
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("ssh")):
            with self.assertRaises(module.OmvError) as ctx:
                module._exec("uptime")

        self.assertIn("Could not start the command", str(ctx.exception))

    def test_timeout_argument_overrides_the_default(self):
        module = load_module(OMV_TIMEOUT="60")
        with mock.patch("subprocess.run", return_value=fake_completed("")) as run:
            module._exec("uptime", timeout=5)

        self.assertEqual(run.call_args.kwargs["timeout"], 5)


class TestRpcErrorMessage(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_extracts_message_from_a_real_error_response(self):
        message = self.module._rpc_error_message(fixture("rpc_error.json"))
        self.assertIn("does not exist for the RPC service", message)

    def test_not_json(self):
        self.assertIsNone(self.module._rpc_error_message("ssh: connect failed"))

    def test_json_without_error_field(self):
        self.assertIsNone(self.module._rpc_error_message('{"arch":"amd64"}'))

    def test_json_with_null_error(self):
        self.assertIsNone(self.module._rpc_error_message('{"response":1,"error":null}'))

    def test_empty_input(self):
        self.assertIsNone(self.module._rpc_error_message(""))


class TestParse(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_a_real_success_response_is_unwrapped_json(self):
        """
        omv-rpc already unwraps on success: you get the result itself, not
        {"response": ..., "error": null}. There is no envelope to strip.
        """
        result = self.module._parse(fixture("rpc_success.json"))

        self.assertIsInstance(result, dict)
        self.assertNotIn("response", result)
        self.assertIn("version", result)
        self.assertEqual(result["hostname"], "openmediavault")

    def test_empty_output_becomes_none(self):
        self.assertIsNone(self.module._parse("   \n"))

    def test_plain_text_stays_text(self):
        self.assertEqual(self.module._parse("not json"), "not json")


if __name__ == "__main__":
    unittest.main()
