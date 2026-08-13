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
