import io
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_platform import Foreground, Target, process_executable


class ForegroundTests(unittest.TestCase):
    def foreground(self, classes=("steam_app_123", "steam_app_123")):
        window = Mock(id=99)
        window.get_wm_class.return_value = classes
        window.query_tree.return_value.parent = Mock(id=1)
        fg = Foreground.__new__(Foreground)
        fg.system = "Linux"
        fg.lock = threading.RLock()
        fg.last_focus_detail = None
        fg.display = Mock()
        fg.display.screen.return_value.root.id = 1
        fg.display.get_input_focus.return_value.focus = window
        return fg, window

    def test_proton_wow_process_is_recognized_and_other_apps_are_rejected(self):
        fg, window = self.foreground()
        with (
            patch.object(fg, "_window_pid", return_value=42),
            patch("thumbtalk_platform.process_executable") as executable,
        ):
            for name in ("wow.exe", "wowclassic.exe", "wowclassicb.exe", "wowb.exe"):
                executable.return_value = name
                self.assertEqual(fg.current(), Target("Linux", 99, 42))
            for name in ("battle.net.exe", "steam", "firefox", "notwow.exe", ""):
                executable.return_value = name
                self.assertIsNone(fg.current())

    def test_focused_child_resolves_wow_parent_and_root_focus_is_rejected(self):
        fg, window = self.foreground(("wow.exe", "wow.exe"))
        child = Mock(id=100)
        child.get_wm_class.return_value = ()
        child.query_tree.return_value.parent = window
        fg.display.get_input_focus.return_value.focus = child
        with patch.object(fg, "_window_pid", return_value=0):
            self.assertEqual(fg.current(), Target("Linux", 99))
            fg.display.get_input_focus.return_value.focus = 1
            self.assertIsNone(fg.current())
            fg.display.get_input_focus.return_value.focus = Mock(id=1)
            self.assertIsNone(fg.current())

    def test_xres_pid_takes_priority_over_namespaced_window_pid(self):
        fg, window = self.foreground()
        fg.display.has_extension.return_value = True
        fg.display.res_query_client_ids.return_value.ids = [
            SimpleNamespace(spec=SimpleNamespace(mask=2), value=[456])
        ]
        self.assertEqual(fg._window_pid(window), 456)
        window.get_full_property.assert_not_called()

    def test_older_xserver_falls_back_to_window_pid(self):
        fg, window = self.foreground()
        fg.display.res_query_client_ids.side_effect = RuntimeError("XRes 1.0")
        window.get_full_property.return_value = SimpleNamespace(format=32, value=[42])
        with patch.dict(
            sys.modules, {"Xlib": SimpleNamespace(Xatom=SimpleNamespace(CARDINAL=6))}
        ):
            self.assertEqual(fg._window_pid(window), 42)
            window.get_full_property.return_value = None
            self.assertEqual(fg._window_pid(window), 0)

    def test_process_identity_uses_executable_not_window_title_or_later_arguments(self):
        for arguments, expected in (
            (b"Z:\\games\\World of Warcraft\\_retail_\\Wow.exe\0", "wow.exe"),
            (b"/games/WowClassicB.exe\0-option\0", "wowclassicb.exe"),
            (b"/usr/bin/wine64\0C:\\games\\Wow.exe\0", "wow.exe"),
            (b"/usr/bin/firefox\0/games/Wow.exe\0", "firefox"),
            (b"/usr/bin/wine\0notwow.exe\0Wow.exe\0", "notwow.exe"),
            (b"", ""),
        ):
            with (
                self.subTest(arguments=arguments),
                patch(
                    "thumbtalk_platform.Path.open", return_value=io.BytesIO(arguments)
                ),
            ):
                self.assertEqual(process_executable(42), expected)
        with patch("thumbtalk_platform.Path.open", side_effect=PermissionError):
            self.assertEqual(process_executable(42), "")
