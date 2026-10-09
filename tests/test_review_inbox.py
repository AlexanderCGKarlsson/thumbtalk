"""Review is one paste with no game receipt; only explicit accept sends chat."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_review import transfer, packet
from thumbtalk_platform import DeliveryStopped, KeyboardDelivery

KEYS = SimpleNamespace(
    Key=SimpleNamespace(
        ctrl_l="CTRL", shift_l="SHIFT", f6="F6", f8="F8", f9="F9", f10="F10"
    )
)
OPEN = ("29:1", "42:1", "66:1", "66:0", "42:0", "29:0")
PASTE = ("29:1", "47:1", "47:0", "29:0")


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.events, self.logs = [], []
        self.allowed = True
        self.guard = lambda: self.allowed
        self.keyboard, self.clip = Mock(), Mock(closed=False)
        self.clip.acquire_review.side_effect = (
            lambda text: self.events.append(("copy", text)) or "lease"
        )
        self.clip.wait_for_paste.side_effect = lambda _: self.events.append(
            "300ms grace"
        )

        def keys(codes, expected, guard):
            if not guard():
                raise DeliveryStopped("interrupted")
            self.events.append(codes)

        self.keyboard.keys.side_effect = keys
        self.key_patch = patch.dict(sys.modules, {"pynput.keyboard": KEYS})
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)

    def transfer(self, text="/p hello æøå 😀"):
        transfer(
            text,
            self.keyboard,
            self.clip,
            self.guard,
            self.logs.append,
            sleep=lambda seconds: self.events.append(seconds),
        )

    def test_no_copy_back_or_enter_and_no_game_reply_required(self):
        self.clip.read.side_effect = AssertionError("Must not request game receipt")
        with patch("thumbtalk_review.secrets.token_hex", return_value="aabbccdd"):
            self.transfer()
        self.assertEqual(
            self.events,
            [
                ("copy", packet("/p hello æøå 😀", "aabbccdd")),
                OPEN,
                0.25,
                PASTE,
                "300ms grace",
            ],
        )
        self.clip.restore.assert_called_once_with("lease")
        self.clip.read.assert_not_called()
        self.assertNotIn("hello", " ".join(self.logs))

    def test_clipboard_failure_does_not_take_input_focus(self):
        self.clip.acquire_review.side_effect = RuntimeError("clipboard failed")
        with self.assertRaises(RuntimeError):
            self.transfer()
        self.keyboard.keys.assert_not_called()

    def test_focus_loss_before_paste_stops_without_sending_keys_to_other_app(self):
        def lose_focus(seconds):
            self.allowed = False

        with self.assertRaises(DeliveryStopped):
            transfer(
                "/s private",
                self.keyboard,
                self.clip,
                self.guard,
                self.logs.append,
                sleep=lose_focus,
            )
        self.assertEqual([c.args[0] for c in self.keyboard.keys.call_args_list], [OPEN])
        self.clip.restore.assert_called_once_with("lease")

    def test_paste_failure_cancels_receiver_without_retry_or_enter(self):
        original = self.keyboard.keys.side_effect

        def fail(codes, expected, guard):
            original(codes, expected, guard)
            if codes == PASTE:
                raise RuntimeError("paste failed")

        self.keyboard.keys.side_effect = fail
        with self.assertRaisesRegex(RuntimeError, "paste failed"):
            self.transfer()
        self.assertEqual(self.events.count(PASTE), 1)
        self.assertTrue(
            any("64:1" in row for row in self.events if isinstance(row, tuple))
        )
        self.assertFalse(
            any("28:1" in row for row in self.events if isinstance(row, tuple))
        )
        self.clip.restore.assert_called_once_with("lease")

    @patch("thumbtalk_platform.platform.system", return_value="Linux")
    def test_review_waits_for_explicit_accept_and_discard_never_sends(self, _):
        delivery = KeyboardDelivery(Mock(), clipboard=self.clip, sleep=lambda _: None)
        delivery.linux_input = self.keyboard
        delivery._command, delivery._review_signal = Mock(), Mock()
        with patch("thumbtalk_review.time.sleep", lambda _: None):
            # Bind a no-sleep wrapper because the production default is captured.
            original = transfer
            with patch(
                "thumbtalk_review.transfer",
                side_effect=lambda *args: original(*args, sleep=lambda _: None),
            ):
                delivery.draft("/g protect healer", self.guard)
                delivery._command.assert_not_called()
                self.assertEqual(delivery.pending_text, "/g protect healer")
                delivery.send(self.guard)
                delivery._command.assert_called_once_with(
                    "/g protect healer", self.guard
                )
                self.assertIsNone(delivery.pending_text)
                with self.assertRaises(ValueError):
                    delivery.send(self.guard)
                delivery._command.reset_mock()
                delivery.draft("/s discard this", self.guard)
                delivery.cancel(self.guard)
                delivery._command.assert_not_called()
                self.assertIsNone(delivery.pending_text)
                delivery._review_signal.assert_called_with("F10", self.guard)

    def test_packet_bounds_unicode_and_checksum(self):
        self.assertEqual(
            packet("/s hello", "aabbccdd"),
            "TTREVIEW2:aabbccdd:2f732068656c6c6f:0b8c02d7;",
        )
        self.assertLess(len(packet("/s " + "ø" * 126, "aabbccdd")), 600)
        for text in ("hello", "/s bad\n", "/s " + "a" * 253):
            with self.assertRaises(ValueError):
                packet(text, "aabbccdd")
        with self.assertRaises(ValueError):
            packet("/s hello", "malformed")
