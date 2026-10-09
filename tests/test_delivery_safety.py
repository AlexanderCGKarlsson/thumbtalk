"""Movement and Unicode delivery regressions; no real keyboard input."""

import ctypes
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_activity import InputActivity
from thumbtalk_platform import KeyboardDelivery, DeliveryStopped, ChatTiming
from thumbtalk_windows_text import (
    Input,
    WindowsTextWriter,
    WindowsControlKeys,
    INPUT_MARKER,
)


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.activity = InputActivity(lambda: self.now)
        self.now = 1

    def test_hold_movement_release_and_settle_before_typing(self):
        self.assertTrue(self.activity.quiet())
        self.activity.axis("pad", 1, 0, 0.8)
        self.now = 5
        self.assertFalse(self.activity.quiet())
        self.activity.axis("pad", 1, 0, 0.1)
        self.assertFalse(self.activity.quiet())
        self.now += 0.21
        self.assertTrue(self.activity.quiet())
        self.activity.axis("pad", 1, 0, 0.05)
        self.assertTrue(self.activity.quiet(), "Neutral jitter must not delay forever")

    def test_new_input_permanently_invalidates_inflight_delivery(self):
        version = self.activity.version
        for kind, button in (("key", "w"), ("mouse", "right"), ("pad", "PAD1")):
            self.activity.button(kind, 1, button, True)
            self.assertFalse(self.activity.quiet(version))
            self.activity.button(kind, 1, button, False)
        self.now += 1
        self.assertTrue(self.activity.quiet())
        self.assertFalse(self.activity.quiet(version))

    def test_gamepad_and_raw_buttons_block_text_without_blocking_axes(self):
        self.activity.axis("pad", 1, "left", 0.9)
        self.assertTrue(self.activity.text_ready())
        for kind, name in (
            ("pad", "PADLSHOULDER"),
            ("pad", "PADRTRIGGER"),
            ("raw", "rear"),
        ):
            version = self.activity.button_version
            self.activity.button(kind, 1, name, True)
            self.assertFalse(self.activity.text_ready())
            self.activity.button(kind, 1, name, False)
            self.assertFalse(self.activity.text_ready())
            self.now += 0.21
            self.assertTrue(self.activity.text_ready())
            self.assertFalse(
                self.activity.text_ready(version),
                "A new button must invalidate in-flight input",
            )

    def test_disconnect_does_not_leave_stick_or_buttons_held(self):
        self.activity.axis("pad", 2, 3, 0.9)
        self.activity.button("pad", 2, "PAD1", True)
        self.activity.disconnect(2)
        self.now += 1
        self.assertTrue(self.activity.quiet())


class UnicodeTests(unittest.TestCase):
    def test_ascii_accents_and_surrogates_never_emit_letter_virtual_keys(self):
        batches = []

        def send(count, events, size):
            self.assertEqual(size, ctypes.sizeof(Input))
            batches.append(
                [
                    (e.type, e.data.ki.wVk, e.data.ki.wScan, e.data.ki.dwFlags)
                    for e in events
                ]
            )
            return count

        writer = WindowsTextWriter(send)
        text = "wasd /s æøå 😀"
        for char in text:
            writer.character(char)
        actual = []
        for batch in batches:
            for index, (kind, vk, unit, flags) in enumerate(batch):
                self.assertEqual((kind, vk, flags), (1, 0, 4 if index % 2 == 0 else 6))
                if index % 2 == 0:
                    actual.append(unit.to_bytes(2, "little"))
        self.assertEqual(b"".join(actual).decode("utf-16-le"), text)
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(Input), 40)

    def test_entire_unicode_draft_uses_one_batch_without_shortcuts(self):
        batches = []

        def send(count, events, size):
            batches.append(
                [(e.data.ki.wVk, e.data.ki.wScan, e.data.ki.dwFlags) for e in events]
            )
            return count

        text = "/s wasd bags æøå 😀"
        WindowsTextWriter(send).text(text)
        self.assertEqual(len(batches), 1)
        self.assertTrue(all(vk == 0 for vk, _, _ in batches[0]))
        self.assertEqual(
            [flags for _, _, flags in batches[0]],
            [4, 6] * (len(text.encode("utf-16-le")) // 2),
        )
        decoded = b"".join(
            unit.to_bytes(2, "little") for _, unit, flags in batches[0] if flags == 4
        ).decode("utf-16-le")
        self.assertEqual(decoded, text)

    def test_control_and_text_events_identify_the_same_process(self):
        events = []

        def send(count, data, size):
            events.extend(
                (
                    data[i].data.ki.wVk,
                    data[i].data.ki.dwFlags,
                    data[i].data.ki.dwExtraInfo,
                )
                for i in range(count)
            )
            return count

        writer = WindowsTextWriter(send)
        controls = WindowsControlKeys(writer)
        key = SimpleNamespace(value=SimpleNamespace(vk=0x0D))
        controls.press(key)
        controls.release(key)
        writer.text("æ")
        self.assertEqual(
            events,
            [
                (0x0D, 0, INPUT_MARKER),
                (0x0D, 2, INPUT_MARKER),
                (0, 4, INPUT_MARKER),
                (0, 6, INPUT_MARKER),
            ],
        )
        with self.assertRaises(ValueError):
            controls.press("w")
        self.assertEqual(len(events), 4)

    def test_partial_batch_releases_without_resending_text(self):
        calls = []

        def send(count, events, size):
            calls.append([e.data.ki.dwFlags for e in events])
            return 1

        with self.assertRaisesRegex(RuntimeError, "No keyboard fallback"):
            WindowsTextWriter(send).character("w")
        self.assertEqual(calls, [[4, 6], [6]])

    def test_rejected_batch_does_not_retry(self):
        send = Mock(return_value=0)
        with self.assertRaises(RuntimeError):
            WindowsTextWriter(send).character("b")
        self.assertEqual(send.call_count, 1)

    def test_windows_delivery_uses_text_writer_and_stops_on_guard_failure(self):
        keyboard, writer = Mock(), Mock()
        delivery = KeyboardDelivery(keyboard=keyboard, sleep=lambda _: None)
        guard = Mock(side_effect=[True, False])
        with (
            patch("thumbtalk_platform.platform.system", return_value="Windows"),
            patch("thumbtalk_windows_text.WindowsTextWriter", return_value=writer),
        ):
            with self.assertRaises(DeliveryStopped):
                delivery._type("bags", guard)
        writer.character.assert_called_once_with("b")
        keyboard.press.assert_not_called()


class SubmissionTests(unittest.TestCase):
    def key_module(self):
        return SimpleNamespace(
            Key=SimpleNamespace(
                **{
                    n: n.upper()
                    for n in ("enter", "esc", "cmd", "ctrl_l", "shift_l", "f9", "f10")
                }
            )
        )

    def test_legacy_style_preference_never_changes_game_on_any_platform(self):
        for system in ("Linux", "Windows", "Darwin"):
            with self.subTest(system=system), patch(
                "thumbtalk_platform.platform.system", return_value=system
            ):
                keyboard, sleep = Mock(), Mock()
                delivery = KeyboardDelivery(keyboard, sleep=sleep)
                delivery._command = Mock()
                for target in ("wow-a", "wow-a", "wow-b", None):
                    for enabled in (True, False):
                        delivery.prepare_chat(target, enabled, lambda: False)
                delivery._command.assert_not_called()
                keyboard.press.assert_not_called()
                sleep.assert_not_called()

    def test_legacy_transfer_survives_frame_phase_offsets(self):
        # State-sampled controls plus frame-dispatched text: exercise phase
        # boundaries, not only that calls occur in the expected order. This
        # deliberately does not model protected UI or concurrent physical keys.
        for system in ("Windows", "Darwin"):
            for fps in (20, 30, 60, 144):
                for phase in range(20):

                    class Game:
                        now = 0.0
                        next_frame = (phase + 0.5) / (20 * fps)
                        focused = False
                        text = ""
                        clipboard = ""
                        classic = False

                        def __init__(self):
                            self.held, self.previous = set(), set()
                            self.pending, self.messages = [], []
                            self.paste_at = None

                        def press(self, key):
                            self.held.add(key)

                        def release(self, key):
                            self.held.discard(key)

                        def slash(self, sleep, hold):
                            self.press("/")
                            try:
                                sleep(hold)
                            finally:
                                self.release("/")

                        def acquire(self, text):
                            self.clipboard = text
                            return "lease"

                        def advance(self, seconds):
                            end = self.now + seconds
                            while self.next_frame <= end + 1e-10:
                                down = self.held - self.previous
                                if "/" in down:
                                    self.focused, self.text = True, "/"
                                if self.pending:
                                    assert self.focused, (
                                        "Text arrived before chat opened"
                                    )
                                    self.text += "".join(self.pending)
                                    self.pending.clear()
                                if (
                                    self.paste_at is not None
                                    and self.next_frame >= self.paste_at
                                ):
                                    self.text += self.clipboard
                                    self.paste_at = None
                                if "v" in down and ({"CTRL_L", "CMD"} & self.held):
                                    assert self.focused, (
                                        "Paste arrived before chat opened"
                                    )
                                    self.paste_at = self.next_frame + 0.12
                                if "ENTER" in down and self.focused:
                                    if self.text == "/console chatStyle classic":
                                        self.classic = True
                                    else:
                                        self.messages.append(self.text)
                                    self.text, self.focused = "", not self.classic
                                self.previous = set(self.held)
                                self.next_frame += 1 / fps
                            self.now = end

                    game = Game()
                    clipboard = Mock(acquire=game.acquire)
                    with patch(
                        "thumbtalk_platform.platform.system", return_value=system
                    ):
                        delivery = KeyboardDelivery(
                            game, sleep=game.advance, clipboard=clipboard
                        )
                    delivery.text_writer = SimpleNamespace(text=game.pending.append)
                    with (
                        patch(
                            "thumbtalk_platform.platform.system", return_value=system
                        ),
                        patch.dict(sys.modules, {"pynput.keyboard": self.key_module()}),
                    ):
                        delivery.prepare_chat("wow", True, lambda: True)
                        self.assertFalse(game.classic)
                        self.assertFalse(game.focused)
                        for message in ("/s Move left! æøå 😀", "/g Next pull"):
                            started = game.now
                            delivery._command(message, lambda: True)
                            self.assertLess(
                                game.now - started,
                                0.30 if system == "Windows" else 0.50,
                            )
                            self.assertEqual(
                                game.focused, True, (system, fps, phase)
                            )
                            self.assertEqual(game.text, "")
                    self.assertEqual(
                        game.messages, ["/s Move left! æøå 😀", "/g Next pull"]
                    )
                    self.assertEqual(game.held, set())

    def test_native_commands_use_slash_and_one_text_batch_then_enter(self):
        for system in ("Darwin", "Windows"):
            keyboard, clipboard, writer = Mock(), Mock(), Mock()
            clipboard.acquire.return_value = "lease"
            delivery = KeyboardDelivery(
                keyboard, sleep=lambda _: None, clipboard=clipboard
            )
            delivery.text_writer = writer if system == "Windows" else None
            with (
                patch("thumbtalk_platform.platform.system", return_value=system),
                patch.dict(sys.modules, {"pynput.keyboard": self.key_module()}),
            ):
                delivery._command("/s hello æøå", lambda: True)
            if system == "Windows":
                keyboard.slash.assert_called_once()
                writer.text.assert_called_once_with("s hello æøå")
                clipboard.acquire.assert_not_called()
                self.assertEqual(
                    [c.args[0] for c in keyboard.press.call_args_list], ["ENTER"]
                )
            else:
                clipboard.acquire.assert_called_once_with("s hello æøå")
                clipboard.restore.assert_called_once_with("lease")
                modifier = "CMD" if system == "Darwin" else "CTRL_L"
                self.assertEqual(
                    [c.args[0] for c in keyboard.press.call_args_list],
                    ["/", modifier, "v", "ENTER"],
                )

    def test_send_opens_chat_once_and_never_injects_cleanup_slash_or_escape(self):
        for system in ("Windows", "Darwin"):
            keyboard, clipboard, writer = Mock(), Mock(), Mock()
            delivery = KeyboardDelivery(
                keyboard, sleep=lambda _: None, clipboard=clipboard
            )
            delivery.text_writer = writer
            with (
                patch("thumbtalk_platform.platform.system", return_value=system),
                patch.dict(sys.modules, {"pynput.keyboard": self.key_module()}),
            ):
                delivery.send(lambda: True, "/s hello")
            pressed = [c.args[0] for c in keyboard.press.call_args_list]
            self.assertEqual(pressed.count("ENTER"), 1)
            self.assertNotIn("ESC", pressed)
            if system == "Windows":
                keyboard.slash.assert_called_once()
            else:
                self.assertEqual(pressed.count("/"), 1)

    def test_repeated_review_and_send_do_not_reopen_or_fill_empty_editor(self):
        # Some native chat modes close on Enter; gamepad IM may keep focus.
        # We cannot assert focus release in that mode without game feedback.
        for retain_focus in (False, True):

            class ChatWindow:
                focused = False
                text = ""

                def __init__(self):
                    self.submitted = []
                    self.keys = []

                def slash(self, **kwargs):
                    self.focused = True
                    self.text += "/"

                def press(self, key):
                    self.keys.append(key)
                    if key == "ENTER" and self.focused:
                        self.submitted.append(self.text)
                        self.text, self.focused = "", retain_focus

                def release(self, key):
                    pass

                def insert(self, text):
                    assert self.focused, "Text arrived outside chat"
                    self.text += text

            window = ChatWindow()
            delivery = KeyboardDelivery(window, sleep=lambda _: None)
            delivery.text_writer = SimpleNamespace(text=window.insert)
            with (
                patch("thumbtalk_platform.platform.system", return_value="Windows"),
                patch.dict(sys.modules, {"pynput.keyboard": self.key_module()}),
            ):
                messages = ["/s first", "/s second", "/g guild", "/s back to say"]
                for payload in messages:
                    delivery.draft(payload, lambda: True)
                    self.assertEqual(window.focused, retain_focus)
                    self.assertEqual(window.text, "")
                    delivery.send(lambda: True)
                    self.assertEqual(window.focused, retain_focus)
                    self.assertEqual(window.text, "", "Cleanup left a slash in chat")
                delivery.draft("/s discard this", lambda: True)
                delivery.cancel(lambda: True)
            self.assertEqual(
                [s for s in window.submitted if not s.startswith("/ttr ")], messages
            )
            self.assertNotIn("ESC", window.keys)
            self.assertEqual(window.text, "")

    def test_autocomplete_can_consume_enter_without_delivery_ack(self):
        logs = []
        keyboard = Mock()
        delivery = KeyboardDelivery(keyboard, sleep=lambda _: None, log=logs.append)
        # This editor consumes Enter; returning from native input is not receipt.
        delivery.text_writer = Mock()
        with (
            patch("thumbtalk_platform.platform.system", return_value="Windows"),
            patch.dict(sys.modules, {"pynput.keyboard": self.key_module()}),
        ):
            delivery.send(lambda: True, "/w friend hello")
        self.assertEqual(
            [c.args[0] for c in keyboard.press.call_args_list].count("ENTER"), 1
        )
        self.assertTrue(
            any("delivery and editor focus unconfirmed" in line for line in logs)
        )

    def test_paste_and_cleanup_are_held_across_frames_and_release_modifiers(self):
        for system in ("Darwin", "Windows"):
            events = []
            keyboard = SimpleNamespace(
                press=lambda k: events.append(("press", k)),
                release=lambda k: events.append(("release", k)),
            )
            delivery = KeyboardDelivery(
                keyboard,
                sleep=lambda seconds: events.append(("sleep", seconds)),
                timing=ChatTiming(key_hold=0.12),
            )
            with (
                patch("thumbtalk_platform.platform.system", return_value=system),
                patch.dict(sys.modules, {"pynput.keyboard": self.key_module()}),
            ):
                delivery._paste(lambda: True)
                delivery._review_signal("F9", lambda: True)
            for key in ("v", "F9"):
                i = events.index(("press", key))
                self.assertEqual(
                    events[i + 1 : i + 3], [("sleep", 0.12), ("release", key)]
                )
            self.assertEqual(events.count(("press", "ENTER")), 0)

    def test_failed_paste_releases_keys_and_never_submits_or_retries(self):
        events, logs = [], []
        keyboard = Mock()
        keyboard.press.side_effect = lambda k: events.append(("press", k))
        keyboard.release.side_effect = lambda k: events.append(("release", k))

        def wait(seconds):
            if events[-1:] == [("press", "v")]:
                raise RuntimeError("test paste failure")

        clipboard = Mock()
        delivery = KeyboardDelivery(
            keyboard, sleep=wait, clipboard=clipboard, log=logs.append
        )
        with (
            patch("thumbtalk_platform.platform.system", return_value="Darwin"),
            patch.dict(sys.modules, {"pynput.keyboard": self.key_module()}),
        ):
            with self.assertRaises(RuntimeError):
                delivery.send(lambda: True, "/s private message")
        self.assertEqual(events[-2:], [("release", "v"), ("release", "CMD")])
        self.assertNotIn(("press", "ENTER"), events)
        self.assertIsNone(delivery.pending_text)
        clipboard.restore.assert_called_once()
        self.assertIn("stopped during insert", logs[-1])
        self.assertNotIn("private message", " ".join(logs))

    def test_pacing_overrides_and_logs_do_not_change_message_or_submit_twice(self):
        for system in ("Darwin", "Windows"):
            keyboard, writer, clipboard = Mock(), Mock(), Mock()
            waits, logs = [], []
            timing = ChatTiming(open_delay=0.31, insert_delay=0.42, settle_delay=0.23)
            delivery = KeyboardDelivery(
                keyboard,
                sleep=waits.append,
                clipboard=clipboard,
                timing=timing,
                log=logs.append,
            )
            delivery.text_writer = writer
            with (
                patch("thumbtalk_platform.platform.system", return_value=system),
                patch.dict(sys.modules, {"pynput.keyboard": self.key_module()}),
            ):
                delivery._command("/g secret æøå", lambda: True)
            self.assertIn(0.31, waits)
            self.assertIn(0.42, waits)
            self.assertIn(0.23, waits)
            self.assertEqual(
                [c.args[0] for c in keyboard.press.call_args_list].count("ENTER"), 1
            )
            self.assertNotIn("secret", " ".join(logs))
            self.assertIn("delivery and editor focus unconfirmed", logs[-1])

    def test_invalid_pacing_is_rejected_before_any_input(self):
        for value in ("nan", "inf", "-1", "0", "0.21", "2", "oops"):
            with patch.dict("os.environ", {"THUMBTALK_CHAT_KEY_HOLD": value}):
                with self.assertRaises(ValueError):
                    KeyboardDelivery(Mock())
        with patch.dict("os.environ", {"THUMBTALK_CHAT_OPEN_DELAY": "0.4"}):
            self.assertEqual(ChatTiming.from_environment().open_delay, 0.4)

    @patch("thumbtalk_platform.platform.system", return_value="Linux")
    def test_linux_review_uses_inbox_never_slash_command(self, _):
        delivery = KeyboardDelivery(Mock(), clipboard=Mock(closed=False))
        delivery.linux_input = Mock()
        delivery._command = Mock()

        def guard():
            return True

        with patch("thumbtalk_review.transfer") as transfer:
            delivery.draft("/s expected a private review", guard)
        transfer.assert_called_once_with(
            "/s expected a private review",
            delivery.linux_input,
            delivery.clipboard,
            guard,
            delivery.log,
        )
        self.assertEqual(delivery.pending_text, "/s expected a private review")
        delivery._command.assert_not_called()

    @patch("thumbtalk_platform.platform.system", return_value="Darwin")
    def test_review_is_local_chunked_losslessly_and_never_sent(self, _):
        delivery = KeyboardDelivery(Mock())
        delivery._command = Mock()
        text = "/s " + "ø hello " * 25
        delivery.draft(text, lambda: True)
        commands = [c.args[0] for c in delivery._command.call_args_list]
        chunks = [c.split(" ", 4) for c in commands]
        self.assertTrue(all(c[0] == "/ttr" for c in chunks))
        self.assertTrue(all(len(c.encode()) <= 255 for c in commands))
        self.assertEqual(bytes.fromhex("".join(c[4] for c in chunks)).decode(), text)
        self.assertEqual([int(c[2]) for c in chunks], list(range(1, len(chunks) + 1)))
        self.assertEqual(delivery.pending_text, text)
        self.assertNotIn(text, commands)

    def test_confirmation_sends_frozen_payload_once(self):
        delivery = KeyboardDelivery(Mock())
        delivery._command, delivery._review_signal = Mock(), Mock()
        delivery.pending_text = "/s move left"
        with patch.dict(sys.modules, {"pynput.keyboard": self.key_module()}):
            delivery.send(lambda: True)
        self.assertEqual(
            [c.args[0] for c in delivery._command.call_args_list],
            ["/s move left"],
        )
        self.assertEqual(delivery._review_signal.call_args.args[0], "F9")
        self.assertIsNone(delivery.pending_text)
        with patch.dict(sys.modules, {"pynput.keyboard": self.key_module()}):
            with self.assertRaises(ValueError):
                delivery.send(lambda: True)

    def test_immediate_sends_without_review_command(self):
        delivery = KeyboardDelivery(Mock())
        delivery._command, delivery._review_signal = Mock(), Mock()
        with patch.dict(sys.modules, {"pynput.keyboard": self.key_module()}):
            delivery.send(lambda: True, "/p hello")
        self.assertEqual(delivery._command.call_args_list[0].args[0], "/p hello")
        self.assertFalse(
            any(c.args[0].startswith("/ttr") for c in delivery._command.call_args_list)
        )

    def test_cancel_clears_only_local_review_without_chat_input(self):
        delivery = KeyboardDelivery(Mock())
        delivery._command, delivery._review_signal = Mock(), Mock()
        delivery.pending_text = "/s hi"
        with patch.dict(sys.modules, {"pynput.keyboard": self.key_module()}):
            delivery.cancel(lambda: True)
        delivery._command.assert_not_called()
        self.assertEqual(delivery._review_signal.call_args.args[0], "F10")
        self.assertIsNone(delivery.pending_text)

    def test_interrupted_send_is_not_retried(self):
        delivery = KeyboardDelivery(Mock())
        delivery._command = Mock(side_effect=DeliveryStopped("focus changed"))
        delivery.pending_text = "/s hi"
        with patch.dict(sys.modules, {"pynput.keyboard": self.key_module()}):
            with self.assertRaises(DeliveryStopped):
                delivery.send(lambda: True)
            with self.assertRaises(ValueError):
                delivery.send(lambda: True)
        self.assertEqual(delivery._command.call_count, 1)

    def test_failed_clipboard_and_guard_do_not_enter_chat(self):
        for unavailable in (True, False):
            keyboard, clipboard = Mock(), Mock()
            if unavailable:
                clipboard.acquire.side_effect = RuntimeError("clipboard unavailable")
            delivery = KeyboardDelivery(
                keyboard, sleep=lambda _: None, clipboard=clipboard
            )
            with (
                patch("thumbtalk_platform.platform.system", return_value="Darwin"),
                patch.dict(sys.modules, {"pynput.keyboard": self.key_module()}),
            ):
                with self.assertRaises((RuntimeError, DeliveryStopped)):
                    delivery._command("/s hi", lambda: False)
            keyboard.press.assert_not_called()
            if not unavailable:
                clipboard.restore.assert_called_once()

    def test_invalid_payload_never_enters_chat(self):
        delivery = KeyboardDelivery(Mock())
        delivery._command = Mock()
        for text in ("hello", "/s " + "ø" * 127, "/s hello\n/s bad"):
            with self.assertRaises(ValueError):
                delivery.draft(text, lambda: True)
        delivery._command.assert_not_called()

    def test_native_slash_maps_norwegian_layout_and_releases_on_failure(self):
        writer, u = Mock(), Mock()
        writer.send_input.return_value = 1
        u.VkKeyScanExW.return_value = 0x0137  # Shift+7 -> / on Norwegian keyboard
        events = []

        def send(count, data, size):
            events.append((data[0].data.ki.wVk, data[0].data.ki.dwFlags))
            return 1

        writer.send_input.side_effect = send
        WindowsControlKeys(writer).slash(u)
        self.assertEqual(events, [(0xA0, 0), (0x37, 0), (0x37, 2), (0xA0, 2)])
        u.VkKeyScanExW.return_value = -1
        with self.assertRaises(ValueError):
            WindowsControlKeys(writer).slash(u)
        self.assertEqual(len(events), 4)
