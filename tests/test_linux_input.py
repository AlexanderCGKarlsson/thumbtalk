"""Decktation protocol, interruption boundaries and private daemon lifetime."""

from pathlib import Path
from types import SimpleNamespace
import os
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_linux_input import LinuxKeyboard, send_command
from thumbtalk_platform import KeyboardDelivery, DeliveryStopped

KEYS = SimpleNamespace(
    Key=SimpleNamespace(
        enter="ENTER",
        ctrl_l="CTRL",
        shift_l="SHIFT",
        f9="F9",
        f10="F10",
        f11="F11",
        f12="F12",
    )
)
ENTER = ("28:1", "28:0")
PASTE = ("29:1", "47:1", "47:0", "29:0")


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.events, self.logs = [], []
        self.guard = Mock(return_value=True)
        self.keyboard = Mock()
        self.keyboard.start.side_effect = lambda: self.events.append("ready")

        def keys(codes, expected, guard):
            if not guard():
                raise DeliveryStopped("interrupted")
            self.events.append(tuple(codes))

        self.keyboard.keys.side_effect = keys
        self.clipboard = Mock(closed=False)
        self.clipboard.acquire.side_effect = (
            lambda text: self.events.append(("copy", text)) or "lease"
        )
        self.clipboard.arm.side_effect = lambda token: self.events.append(
            "check clipboard"
        )
        self.clipboard.wait_for_paste.side_effect = lambda token: self.events.append(
            "300ms grace"
        )
        self.clipboard.restore.side_effect = lambda token: self.events.append("restore")
        self.key_patch = patch.dict(sys.modules, {"pynput.keyboard": KEYS})
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)

    def send(self, text="/s hello æøå 😀"):
        send_command(text, self.keyboard, self.clipboard, self.guard, self.logs.append)

    def test_matches_upstream_enter_paste_restore_enter_and_includes_slash(self):
        self.send()
        self.assertEqual(
            self.events,
            [
                "ready",
                ENTER,
                ("copy", "/s hello æøå 😀"),
                "check clipboard",
                PASTE,
                "300ms grace",
                "check clipboard",
                "restore",
                ENTER,
            ],
        )
        self.assertNotIn("hello", " ".join(self.logs))
        self.assertIn("delivery and editor focus unconfirmed", self.logs[-1])

    def test_missing_access_fails_before_any_game_input(self):
        self.keyboard.start.side_effect = RuntimeError("access missing")
        with self.assertRaisesRegex(RuntimeError, "access missing"):
            self.send()
        self.keyboard.keys.assert_not_called()
        self.clipboard.acquire.assert_not_called()

    def test_focus_loss_before_open_never_touches_clipboard(self):
        self.guard.return_value = False
        with self.assertRaises(DeliveryStopped):
            self.send()
        self.assertEqual(self.events, ["ready"])
        self.clipboard.acquire.assert_not_called()

    def test_clipboard_failure_after_open_never_pastes_or_submits(self):
        self.clipboard.acquire.side_effect = RuntimeError("copy failed")
        with self.assertRaises(RuntimeError):
            self.send()
        self.assertEqual(self.events, ["ready", ENTER])
        self.assertIn("stopped during insert", self.logs[-1])

    def test_new_input_before_paste_or_send_aborts_without_retry(self):
        for checks in ([True, False], [True, True, False]):
            with self.subTest(checks=checks):
                self.events.clear()
                self.guard.side_effect = checks
                with self.assertRaises(DeliveryStopped):
                    self.send()
                self.assertEqual(self.events.count(ENTER), 1)
                self.assertEqual(self.events.count("restore"), 1)

    def test_replaced_clipboard_blocks_submit_and_restores_once(self):
        self.clipboard.arm.side_effect = [None, RuntimeError("copy replaced")]
        with self.assertRaisesRegex(RuntimeError, "copy replaced"):
            self.send()
        self.assertEqual(self.events.count(ENTER), 1)
        self.clipboard.restore.assert_called_once_with("lease")

    def test_wait_failure_never_submits_and_cleanup_retains_original_error(self):
        self.clipboard.wait_for_paste.side_effect = RuntimeError("paste interrupted")
        self.clipboard.restore.side_effect = RuntimeError("cleanup also failed")
        with self.assertRaisesRegex(RuntimeError, "paste interrupted"):
            self.send()
        self.assertEqual(self.events.count(ENTER), 1)
        self.clipboard.close.assert_called_once()

    @patch("thumbtalk_platform.platform.system", return_value="Linux")
    def test_immediate_uses_ydotool_and_review_uses_separate_inbox(self, _):
        legacy = Mock()
        delivery = KeyboardDelivery(legacy, clipboard=self.clipboard)
        delivery.linux_input = self.keyboard
        delivery.prepare_chat("wow", False, self.guard)
        delivery.send(self.guard, "/g hello")
        self.assertEqual(
            [c.args[0] for c in self.keyboard.keys.call_args_list],
            [ENTER, PASTE, ENTER],
        )
        self.keyboard.reset_mock()
        with patch("thumbtalk_review.transfer") as transfer:
            delivery.draft("/s hello", self.guard)
            transfer.assert_called_once()
        self.assertEqual(delivery.pending_text, "/s hello")
        self.keyboard.keys.assert_not_called()
        legacy.press.assert_not_called()
        delivery.close()
        self.keyboard.close.assert_called_once()

    @patch("thumbtalk_platform.platform.system", return_value="Linux")
    def test_saved_classic_true_still_sends_only_message_without_setup_or_escape(self, _):
        delivery = KeyboardDelivery(
            Mock(), clipboard=self.clipboard, sleep=lambda _: None
        )
        delivery.linux_input = self.keyboard
        delivery.prepare_chat("wow", True, self.guard)
        delivery.prepare_chat("wow", True, self.guard)
        delivery.send(self.guard, "/s hello")
        self.assertEqual(
            [c.args[0] for c in self.clipboard.acquire.call_args_list],
            ["/s hello"],
        )
        self.assertIsNone(delivery.pending_text)
        self.assertEqual(
            [c.args[0] for c in self.keyboard.keys.call_args_list],
            [ENTER, PASTE, ENTER],
        )
        delivery.close()

    @patch("thumbtalk_platform.platform.system", return_value="Linux")
    def test_no_transcript_retry_after_send_failure(self, system):
        delivery = KeyboardDelivery(Mock(), clipboard=self.clipboard)
        delivery.linux_input = self.keyboard
        delivery.pending_text = "/s pending"
        self.clipboard.acquire.side_effect = RuntimeError("copy failed")
        with self.assertRaises(RuntimeError):
            delivery.send(self.guard)
        with self.assertRaises(ValueError):
            delivery.send(self.guard)
        self.assertEqual(self.events.count(ENTER), 1)


class KeyboardTests(unittest.TestCase):
    def test_uses_raw_codes_and_private_socket_without_typing_text(self):
        keyboard = LinuxKeyboard(on_key=Mock())
        keyboard.start = Mock()
        keyboard.client, keyboard.env = (
            "/input/ydotool",
            {"YDOTOOL_SOCKET": "/private.sock"},
        )
        with patch(
            "thumbtalk_linux_input.subprocess.run",
            return_value=SimpleNamespace(returncode=0),
        ) as run:
            keyboard.keys(PASTE, ("CTRL", "v"), lambda: True)
        self.assertEqual(run.call_args.args[0], ["/input/ydotool", "key", *PASTE])
        self.assertEqual(run.call_args.kwargs["env"]["YDOTOOL_SOCKET"], "/private.sock")
        self.assertEqual(
            [c.args[0] for c in keyboard.on_key.call_args_list], ["CTRL", "v"]
        )

    def test_failed_or_timed_out_chord_destroys_owned_device_releasing_keys(self):
        for error in (subprocess.TimeoutExpired("ydotool", 3), OSError("broken")):
            keyboard = LinuxKeyboard()
            keyboard.start, keyboard.close = Mock(), Mock()
            keyboard.client, keyboard.env = "ydotool", {}
            with patch("thumbtalk_linux_input.subprocess.run", side_effect=error):
                with self.assertRaises(type(error)):
                    keyboard.keys(PASTE, ("CTRL", "v"), lambda: True)
            keyboard.close.assert_called_once()

    def test_no_input_or_automatic_permission_escalation_without_uinput_access(self):
        with (
            patch(
                "thumbtalk_linux_input.tool_paths", return_value=("ydotool", "ydotoold")
            ),
            patch("thumbtalk_linux_input.os.access", return_value=False),
            patch("thumbtalk_linux_input.subprocess.Popen") as spawn,
        ):
            with self.assertRaisesRegex(RuntimeError, "access is unavailable"):
                LinuxKeyboard().start()
        spawn.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "POSIX private socket permissions")
    def test_daemon_is_private_keyboard_only_and_only_our_child_is_stopped(self):
        child = Mock()
        child.poll.return_value = None

        def spawn(args, **kwargs):
            Path(args[args.index("--socket-path") + 1]).touch()
            return child

        keyboard = LinuxKeyboard()
        with (
            patch(
                "thumbtalk_linux_input.tool_paths", return_value=("ydotool", "ydotoold")
            ),
            patch("thumbtalk_linux_input.os.access", return_value=True),
            patch("thumbtalk_linux_input.subprocess.Popen", side_effect=spawn) as popen,
            patch("thumbtalk_linux_input.time.sleep"),
        ):
            keyboard.start()
            socket = Path(keyboard.socket)
            self.assertEqual(socket.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(
                popen.call_args.args[0][-3:], ["--socket-perm", "0600", "--mouse-off"]
            )
            keyboard.start()
            popen.assert_called_once()
            keyboard.close()
        child.terminate.assert_called_once()
        child.wait.assert_called_once_with(timeout=2)
        self.assertFalse(socket.parent.exists())
        with self.assertRaises(RuntimeError):
            keyboard.start()
