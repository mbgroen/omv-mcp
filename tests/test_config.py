"""
Tests for reading configuration out of the environment.

These matter more than they look: the same settings arrive as "1" from a
hand-written JSON config and as "true" from the Claude Desktop extension
settings UI, and a boolean that silently reads as False would quietly switch
read-only mode off.
"""

from __future__ import annotations

import unittest

from helpers import load_module


class TestEnvFlag(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def _flag(self, value, default=False):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"OMV_TEST_FLAG": value}, clear=False):
            return self.module.env_flag("OMV_TEST_FLAG", default)

    def test_truthy_spellings(self):
        for value in ("1", "true", "True", "TRUE", "yes", "on", " true "):
            self.assertTrue(self._flag(value), f"{value!r} should be true")

    def test_falsey_spellings(self):
        for value in ("0", "false", "False", "no", "off", ""):
            self.assertFalse(self._flag(value, default=True), f"{value!r} should be false")

    def test_missing_variable_uses_the_default(self):
        self.assertTrue(self.module.env_flag("OMV_DEFINITELY_UNSET", True))
        self.assertFalse(self.module.env_flag("OMV_DEFINITELY_UNSET", False))

    def test_unrecognised_value_keeps_the_default(self):
        """Falling back to off would be the unsafe direction for OMV_READONLY."""
        self.assertTrue(self._flag("maybe", default=True))
        self.assertFalse(self._flag("maybe", default=False))


class TestEnvInt(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def _int(self, value, default=60):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"OMV_TEST_INT": value}, clear=False):
            return self.module.env_int("OMV_TEST_INT", default)

    def test_plain_integer(self):
        self.assertEqual(self._int("120"), 120)

    def test_float_form_from_a_number_input(self):
        self.assertEqual(self._int("60.0"), 60)

    def test_empty_and_missing_use_the_default(self):
        self.assertEqual(self._int(""), 60)
        self.assertEqual(self.module.env_int("OMV_DEFINITELY_UNSET", 42), 42)

    def test_garbage_uses_the_default(self):
        self.assertEqual(self._int("soon"), 60)


class TestEnvStr(unittest.TestCase):
    """
    An optional setting the user never filled in can reach the process as the
    literal "${user_config.ssh_key}". Treating that as a path produces
    `ssh -i '${user_config.ssh_key}'` and an error mentioning nothing the user
    recognises, so it has to count as unset.
    """

    def setUp(self):
        self.module = load_module()

    def _str(self, value, default=""):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"OMV_TEST_STR": value}, clear=False):
            return self.module.env_str("OMV_TEST_STR", default)

    def test_ordinary_value_passes_through(self):
        self.assertEqual(self._str("  nas.local  "), "nas.local")

    def test_unresolved_placeholder_counts_as_unset(self):
        self.assertEqual(self._str("${user_config.ssh_key}"), "")
        self.assertEqual(self._str("${user_config.ssh_user}", "root"), "root")

    def test_a_path_that_merely_contains_a_brace_is_kept(self):
        self.assertEqual(self._str("/keys/${odd}/id_ed25519"), "/keys/${odd}/id_ed25519")

    def test_empty_uses_the_default(self):
        self.assertEqual(self._str("", "root"), "root")

    def test_placeholder_does_not_become_an_ssh_identity(self):
        module = load_module(OMV_SSH_HOST="nas.local", OMV_SSH_KEY="${user_config.ssh_key}")
        self.assertNotIn("-i", module._ssh_prefix())

    def test_placeholder_in_a_number_field_falls_back(self):
        module = load_module(OMV_SSH_HOST="nas.local", OMV_SSH_PORT="${user_config.ssh_port}")
        self.assertEqual(module.SSH_PORT, "22")


class TestHostKeyPolicy(unittest.TestCase):
    def test_unknown_hosts_are_accepted_on_first_contact(self):
        """
        BatchMode means ssh cannot ask "are you sure?", so the strict default
        makes the first connection from a fresh machine fail outright.
        """
        module = load_module(OMV_SSH_HOST="nas.local")
        self.assertIn("StrictHostKeyChecking=accept-new", module._ssh_prefix())

    def test_checking_is_not_disabled_outright(self):
        """accept-new still refuses a key that changes later; `no` would not."""
        module = load_module(OMV_SSH_HOST="nas.local")
        prefix = " ".join(module._ssh_prefix())
        self.assertNotIn("StrictHostKeyChecking=no", prefix)


class TestSshHints(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_permission_denied_points_at_the_key(self):
        hint = self.module._ssh_hint("Permission denied (publickey).")
        self.assertIn("authorized_keys", hint)
        self.assertIn(".pub", hint)

    def test_unknown_host_key_explains_the_one_off_terminal_step(self):
        hint = self.module._ssh_hint("Host key verification failed.")
        self.assertIn("known_hosts", hint)

    def test_changed_host_key_is_not_waved_away(self):
        hint = self.module._ssh_hint("REMOTE HOST IDENTIFICATION HAS CHANGED!")
        self.assertIn("ssh-keygen -R", hint)

    def test_dns_refused_and_unreachable_each_get_their_own_hint(self):
        cases = {
            "ssh: Could not resolve hostname nas": "IP address",
            "connect to host nas port 22: Connection refused": "Services / SSH",
            "connect to host nas port 22: No route to host": "powered on",
        }
        for detail, expected in cases.items():
            with self.subTest(detail):
                self.assertIn(expected, self.module._ssh_hint(detail))

    def test_unrecognised_errors_get_no_invented_advice(self):
        self.assertEqual(self.module._ssh_hint("something else entirely"), "")

    def test_the_hint_is_appended_to_the_raw_error(self):
        from unittest import mock
        from helpers import fake_completed

        module = load_module(OMV_SSH_HOST="nas.local")
        with mock.patch("subprocess.run",
                        return_value=fake_completed(stderr="Permission denied (publickey).",
                                                    returncode=255)):
            with self.assertRaises(module.OmvError) as ctx:
                module._exec("uptime")

        message = str(ctx.exception)
        self.assertIn("Permission denied (publickey).", message)
        self.assertIn("What this usually means", message)


class TestConfigDefaults(unittest.TestCase):
    def test_defaults_without_any_environment(self):
        module = load_module()

        self.assertEqual(module.SSH_HOST, "")
        self.assertEqual(module.SSH_USER, "root")
        self.assertEqual(module.SSH_PORT, "22")
        self.assertEqual(module.RPC_USER, "admin")
        self.assertFalse(module.USE_SUDO)
        self.assertFalse(module.READONLY)
        self.assertTrue(module.ALLOW_SHELL)
        self.assertEqual(module.TIMEOUT, 60)

    def test_extension_style_values_are_understood(self):
        """This is exactly what the Claude Desktop settings UI passes through."""
        module = load_module(
            OMV_SSH_HOST="nas.local",
            OMV_SSH_PORT="22",
            OMV_SUDO="false",
            OMV_READONLY="true",
            OMV_ALLOW_SHELL="false",
            OMV_TIMEOUT="90",
        )

        self.assertEqual(module.SSH_HOST, "nas.local")
        self.assertEqual(module.SSH_PORT, "22")
        self.assertFalse(module.USE_SUDO)
        self.assertTrue(module.READONLY)
        self.assertFalse(module.ALLOW_SHELL)
        self.assertEqual(module.TIMEOUT, 90)
        self.assertNotIn("omv_shell", module.mcp.tools)

    def test_blank_values_fall_back_to_defaults(self):
        """An untouched optional field in the settings UI arrives as an empty string."""
        module = load_module(OMV_SSH_USER="", OMV_RPC_USER="", OMV_SSH_PORT="")

        self.assertEqual(module.SSH_USER, "root")
        self.assertEqual(module.RPC_USER, "admin")
        self.assertEqual(module.SSH_PORT, "22")

    def test_port_is_normalised_to_a_bare_integer(self):
        """A number input may hand over "2222.0", which ssh -p would reject."""
        module = load_module(OMV_SSH_HOST="nas.local", OMV_SSH_PORT="2222.0")

        self.assertEqual(module.SSH_PORT, "2222")
        self.assertIn("2222", module._ssh_prefix())


if __name__ == "__main__":
    unittest.main()
