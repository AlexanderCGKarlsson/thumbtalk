"""Windows foreground guard behavior, without typing into real applications."""
import ctypes
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_platform import Foreground, Target


class WindowsForegroundTests(unittest.TestCase):
    def make_foreground(self, executable):
        user, kernel = Mock(), Mock()
        user.GetForegroundWindow.return_value = 2**40 + 1
        kernel.OpenProcess.return_value = 2**48 + 1

        def identify(window, pointer):
            pointer._obj.value = 73
            return 1

        def query(handle, flags, buffer, length):
            buffer.value = executable
            return True

        user.GetWindowThreadProcessId.side_effect = identify
        kernel.QueryFullProcessImageNameW.side_effect = query
        with (
            patch("thumbtalk_platform.platform.system", return_value="Windows"),
            patch.object(ctypes, "WinDLL", side_effect=[user, kernel], create=True),
        ):
            return Foreground(), user, kernel

    def test_wow_executable_and_large_handles_are_preserved(self):
        foreground, user, kernel = self.make_foreground(
            r"C:\Games\World of Warcraft\_retail_\Wow.exe"
        )
        self.assertEqual(foreground.current(), Target("Windows", 2**40 + 1, 73))
        kernel.CloseHandle.assert_called_once_with(2**48 + 1)
        self.assertIsNotNone(user.GetForegroundWindow.restype)
        self.assertIsNotNone(kernel.OpenProcess.restype)

    def test_other_process_and_failed_queries_are_rejected(self):
        foreground, _, kernel = self.make_foreground(r"C:\Battle.net\Battle.net.exe")
        self.assertIsNone(foreground.current())
        kernel.CloseHandle.assert_called_once()
        kernel.CloseHandle.reset_mock()
        kernel.QueryFullProcessImageNameW.side_effect = None
        kernel.QueryFullProcessImageNameW.return_value = False
        self.assertIsNone(foreground.current())
        kernel.CloseHandle.assert_called_once()
        kernel.CloseHandle.reset_mock()
        kernel.OpenProcess.return_value = 0
        self.assertIsNone(foreground.current())
        kernel.CloseHandle.assert_not_called()

    def test_no_foreground_window_does_not_open_a_process(self):
        foreground, user, kernel = self.make_foreground(r"C:\Games\WowClassic.exe")
        user.GetForegroundWindow.return_value = 0
        self.assertIsNone(foreground.current())
        kernel.OpenProcess.assert_not_called()
