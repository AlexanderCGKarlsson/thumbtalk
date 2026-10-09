"""Windows startup registration without changing the test machine's registry."""

import contextlib
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
import thumbtalk_startup as startup


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.registry = Mock(
            HKEY_CURRENT_USER="current-user", REG_SZ=1, KEY_SET_VALUE=2
        )
        self.values = {"OtherApp": ("untouched", 1)}

        def opened(*args):
            return contextlib.nullcontext("run-key")

        def query(key, name):
            if name not in self.values:
                raise FileNotFoundError(name)
            return self.values[name]

        def remove(key, name):
            if name not in self.values:
                raise FileNotFoundError(name)
            del self.values[name]

        self.registry.OpenKey.side_effect = opened
        self.registry.CreateKeyEx.side_effect = opened
        self.registry.QueryValueEx.side_effect = query
        self.registry.DeleteValue.side_effect = remove
        self.registry.SetValueEx.side_effect = (
            lambda key, name, unused, kind, value: self.values.update(
                {name: (value, kind)}
            )
        )
        self.enterContext(patch.dict(sys.modules, winreg=self.registry))
        self.enterContext(patch.object(sys, "platform", "win32"))
        self.enterContext(patch.object(sys, "frozen", True, create=True))
        self.enterContext(
            patch.object(sys, "executable", r"C:\User Apps\ThumbTalk\thumbtalk.exe")
        )

    def test_opt_in_disable_and_repeat_preserve_other_startup_entries(self):
        self.assertFalse(startup.startup_enabled())
        self.registry.SetValueEx.assert_not_called()
        startup.set_startup(True, Path("saved settings.json"))
        self.assertTrue(startup.startup_enabled())
        command = self.values["ThumbTalk"][0]
        self.assertTrue(
            command.startswith(
                '"C:\\User Apps\\ThumbTalk\\thumbtalk.exe" --startup --config '
            )
        )
        self.assertIn('saved settings.json"', command)
        self.registry.CreateKeyEx.assert_called_with(
            "current-user", startup.RUN_KEY, 0, 2
        )
        startup.set_startup(False, None)
        startup.set_startup(False, None)
        self.assertFalse(startup.startup_enabled())
        self.assertEqual(self.values, {"OtherApp": ("untouched", 1)})

    def test_permissions_failure_is_reported(self):
        self.registry.CreateKeyEx.side_effect = PermissionError("denied")
        with self.assertRaises(PermissionError):
            startup.set_startup(True, Path("settings.json"))
        self.assertNotIn("ThumbTalk", self.values)

    def test_invalid_command_does_not_change_registry(self):
        with patch.object(sys, "executable", "x" * 270):
            with self.assertRaisesRegex(RuntimeError, "too long"):
                startup.set_startup(True, Path("settings.json"))
        with patch.object(sys, "frozen", False):
            with self.assertRaisesRegex(RuntimeError, "Install"):
                startup.set_startup(True, Path("settings.json"))
        self.registry.CreateKeyEx.assert_not_called()

    def test_other_platforms_never_touch_registry(self):
        for platform in ("darwin", "linux"):
            with patch.object(sys, "platform", platform):
                self.assertFalse(startup.startup_enabled())
                with self.assertRaisesRegex(RuntimeError, "only available"):
                    startup.set_startup(True, Path("settings.json"))
        self.registry.OpenKey.assert_not_called()
        self.registry.CreateKeyEx.assert_not_called()
