"""Behavior checks without a game, microphone, or downloaded model."""

import queue
import sys
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_chat import (
    BindingMatcher,
    ChatSettings,
    Destination,
    Session,
    validate_bindings,
)
from thumbtalk_input import InputHub
from thumbtalk_platform import Target, KeyboardDelivery
from thumbtalk_runtime import Runtime
from thumbtalk_speech import SpeechSettings


class ChatTests(unittest.TestCase):
    def test_classic_chat_preference_round_trips_and_rejects_non_boolean(self):
        import tempfile
        from thumbtalk_chat import load_chat, save_chat

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            self.assertFalse(load_chat(path).classic_chat)
            save_chat(path, replace(ChatSettings(), classic_chat=True))
            self.assertTrue(load_chat(path).classic_chat)  # Old files remain readable.
            save_chat(path, replace(ChatSettings(), classic_chat=False))
            self.assertFalse(load_chat(path).classic_chat)
        for value in (None, 0, 1, "false"):
            with self.assertRaises(ValueError):
                replace(ChatSettings(), classic_chat=value).validate()

    def test_review_b_cancel_defaults_off_and_round_trips(self):
        import tempfile
        from thumbtalk_chat import load_chat, save_chat

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            self.assertFalse(load_chat(path).review_b_cancel)
            path.write_text('{"version": 1, "chat": {"mode": "hold"}}')
            self.assertFalse(load_chat(path).review_b_cancel)
            save_chat(path, replace(ChatSettings(), review_b_cancel=True))
            self.assertTrue(load_chat(path).review_b_cancel)
        for value in (None, 0, 1, "false"):
            with self.assertRaises(ValueError):
                replace(ChatSettings(), review_b_cancel=value).validate()

    def test_channel_commands_and_unicode(self):
        for channel, command in (
            ("say", "/s"),
            ("party", "/p"),
            ("raid", "/raid"),
            ("guild", "/g"),
            ("instance", "/i"),
            ("reply", "/r"),
            ("general", "/tts general"),
            ("trade", "/tts trade"),
            ("lfg", "/tts lfg"),
        ):
            self.assertEqual(
                Destination(channel).message("Blåbær  og\nsmør"),
                command + " Blåbær og smør",
            )
        self.assertEqual(
            Destination("whisper", "Name-Realm").message("hi"), "/w Name-Realm hi"
        )

    def test_invalid_messages_never_become_commands(self):
        for text in ("", "   ", "/logout", "ø" * 128, "hi\x00there"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                Destination("party").message(text)

    def test_language_alias_duplicates_and_malformed_values_rejected(self):
        for languages in (("no", "nb-NO"), ([],), ("en", "en")):
            with self.subTest(languages=languages), self.assertRaises(ValueError):
                replace(ChatSettings(), languages=languages).validate()

    def test_missing_whisper_recipient_is_rejected(self):
        with self.assertRaises(ValueError):
            Destination("whisper").message("hi")

    def test_chords_debounce_and_devices_do_not_combine(self):
        matcher = BindingMatcher(
            {"record": "pad:PAD1+PAD2", "confirm": "key:f9", "cancel": "key:f10"}
        )
        self.assertEqual(matcher.feed("pad", 1, "PAD1", True), [])
        self.assertEqual(matcher.feed("pad", 2, "PAD2", True), [])
        self.assertEqual(matcher.feed("pad", 1, "PAD2", True), [("record", True)])
        self.assertEqual(matcher.feed("pad", 1, "PAD2", True), [])
        self.assertEqual(matcher.disconnect("pad", 1), [("record", False)])

    def test_conflicting_bindings_rejected(self):
        with self.assertRaises(ValueError):
            validate_bindings({"record": "key:f8", "cancel": "key:f8"})

    def test_stale_transcription_and_changed_focus_cannot_send(self):
        state = Session()
        token = state.begin(Destination("guild"), Target("test", 1))
        state.stop()
        state.cancel()
        self.assertFalse(state.complete(token, "old message"))
        token = state.begin(Destination("party"), Target("test", 1))
        state.stop()
        state.complete(token, "hello")
        with self.assertRaises(ValueError):
            state.prepare(Target("test", 2))
        self.assertEqual(state.prepare(Target("test", 1)), "/p hello")

    @patch("thumbtalk_input.sys.platform", "win32")
    def test_windows_injected_keys_are_ignored_but_steam_remaps_work(self):
        hub = InputHub.__new__(InputHub)
        from thumbtalk_activity import InputActivity

        hub.activity = InputActivity()
        hub.injected_until, hub.physical_modifiers = {}, set()
        hub.listener = None
        hub.events = queue.SimpleQueue()
        key = SimpleNamespace(char="a")
        hub.expect_injected("a")
        hub._key(key, True, injected=True)
        self.assertTrue(hub.events.empty())
        hub._key(SimpleNamespace(name="f8"), True, injected=True)
        self.assertEqual(hub.events.get(timeout=0.5), ("key", 0, "f8", True))
        hub._key(key, True, injected=False)
        self.assertEqual(hub.events.get(timeout=0.5), ("key", 0, "a", True))

    def test_linux_chord_echo_does_not_become_a_physical_modifier(self):
        hub = InputHub.__new__(InputHub)
        from thumbtalk_activity import InputActivity

        hub.activity = InputActivity()
        hub.injected_until, hub.physical_modifiers = {}, set()
        hub.listener = None
        hub.events = queue.SimpleQueue()
        with patch("thumbtalk_input.sys.platform", "linux"):
            for name in ("ctrl", "shift"):
                hub.expect_injected(SimpleNamespace(name=name))
                hub._key(SimpleNamespace(name=name + "_l"), True, injected=False)
                hub._key(SimpleNamespace(name=name + "_l"), False, injected=False)
            self.assertFalse(hub.physical_modifiers)
            self.assertTrue(hub.events.empty())
            hub._key(SimpleNamespace(name="ctrl_r"), True, injected=False)
            self.assertIn("ctrl_r", hub.physical_modifiers)
            hub.injected_until.clear()
            hub._key(SimpleNamespace(name="ctrl_l"), True, injected=False)
            self.assertIn("ctrl_l", hub.physical_modifiers)

    def test_linux_virtual_keyboard_echo_does_not_retrigger_talk_binding(self):
        hub = InputHub.__new__(InputHub)
        hub.injected_until, hub.physical_modifiers = {}, set()
        hub.listener = None
        hub.events = queue.SimpleQueue()
        with patch("thumbtalk_input.sys.platform", "linux"):
            for key in (
                SimpleNamespace(name="enter"),
                SimpleNamespace(char="v"),
                SimpleNamespace(name="f9"),
            ):
                hub.expect_injected(key)
                hub._key(key, True, injected=False)
                hub._key(key, False, injected=False)
            self.assertTrue(hub.events.empty())
            hub._key(SimpleNamespace(char="x"), True, injected=False)
            self.assertEqual(hub.events.get(timeout=0.5), ("key", 0, "x", True))
            hub.injected_until.clear()
            hub._key(SimpleNamespace(char="v"), True, injected=False)
            self.assertEqual(hub.events.get(timeout=0.5), ("key", 0, "v", True))

    def test_delivery_releases_key_if_press_raises(self):
        keyboard = Mock()
        keyboard.press.side_effect = RuntimeError("injection failed")
        notify = Mock()
        delivery = KeyboardDelivery(keyboard=keyboard, on_key=notify)
        with self.assertRaises(RuntimeError):
            delivery._tap("a", lambda: True)
        keyboard.release.assert_called_once_with("a")
        notify.assert_called_once_with("a")


class RuntimeTests(unittest.TestCase):
    def make_runtime(self, mode="hold", transcriber=None, auto_send=False):
        hub = SimpleNamespace(
            error="", physical_modifiers=set(), poll=lambda: [], close=Mock()
        )
        foreground = Mock()
        foreground.current.return_value = Target("test", 1)
        delivery = Mock()
        recorder = Mock()
        recorder.stop.return_value = "audio"
        transcriber = transcriber or SimpleNamespace(
            settings=SpeechSettings(),
            model=SimpleNamespace(supported_languages=["en", "no"]),
            transcribe=lambda audio: "hello",
        )
        runtime = Runtime(
            SpeechSettings(language="en"),
            replace(ChatSettings(), mode=mode, auto_send=auto_send),
            notify=lambda text: None,
            hub=hub,
            recorder=recorder,
            foreground=foreground,
            delivery=delivery,
            transcriber_factory=lambda *args: transcriber,
            model_available=lambda _: False,
        )
        self.addCleanup(runtime.close)
        self.until(runtime, lambda: runtime.ready)
        return runtime, delivery, foreground, recorder

    def test_paused_review_preserves_choice_and_never_records_or_sends(self):
        runtime, delivery, _, recorder = self.make_runtime()
        delivery.local_review_available = False
        runtime.notify = Mock()
        runtime.action("record", True)
        runtime.action("record", False)
        runtime.poll()
        self.assertEqual(runtime.session.state, "idle")
        self.assertFalse(runtime.chat.auto_send)
        self.assertIn("Review is temporarily unavailable", runtime.notify.call_args.args[0])
        recorder.start.assert_not_called()
        delivery.prepare_chat.assert_not_called()
        delivery.draft.assert_not_called()
        delivery.send.assert_not_called()
        runtime.cancel()
        delivery.cancel.assert_not_called()

    def test_paused_review_also_stops_queued_draft_before_style_or_input(self):
        runtime, delivery, _, _ = self.make_runtime()
        delivery.local_review_available = False
        token = runtime.session.begin(runtime.destination, runtime.current_target())
        runtime.session.stop()
        runtime.session.complete(token, "hello")
        with self.assertRaisesRegex(ValueError, "Review is temporarily unavailable"):
            runtime._deliver("draft")
        self.assertEqual(runtime.session.state, "idle")
        self.assertFalse(runtime.draft_ready)
        delivery.prepare_chat.assert_not_called()
        delivery.draft.assert_not_called()
        delivery.send.assert_not_called()

    def test_review_pause_does_not_change_explicit_immediate_send(self):
        runtime, delivery, _, _ = self.make_runtime(auto_send=True)
        delivery.local_review_available = False
        runtime.action("record", True)
        runtime.action("record", False)
        self.until(runtime, lambda: runtime.session.state == "idle")
        delivery.draft.assert_not_called()
        delivery.send.assert_called_once()
        self.assertEqual(delivery.send.call_args.args[1], "/s hello")

    def test_radial_cannot_enable_paused_review_but_can_enable_immediate(self):
        runtime, delivery, _, _ = self.make_runtime()
        delivery.local_review_available = False
        runtime.save_preferences = Mock()
        runtime.menu_nav.selection = Mock(return_value=("sending", "review"))
        with self.assertRaisesRegex(ValueError, "Review is temporarily unavailable"):
            runtime.apply_menu_selection()
        runtime.save_preferences.assert_not_called()
        runtime.menu_nav.selection.return_value = ("sending", "immediate")
        runtime.apply_menu_selection()
        self.assertTrue(runtime.chat.auto_send)
        runtime.save_preferences.assert_called_once_with({}, {"auto_send": True})

    def test_linux_review_and_accept_never_prepare_style(self):
        runtime, delivery, foreground, _ = self.make_runtime()
        foreground.current.return_value = Target("Linux", 1)
        self.draft(runtime)
        delivery.prepare_chat.assert_not_called()
        delivery.draft.assert_called_once()
        delivery.send.assert_not_called()
        runtime.action("record", False)
        self.until(runtime, lambda: runtime.session.state == "idle")
        delivery.prepare_chat.assert_not_called()
        delivery.send.assert_called_once()

    def until(self, runtime, condition):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            runtime.poll()
            if condition():
                return
            time.sleep(0.001)
        self.fail("Runtime did not reach expected state: " + runtime.session.state)

    def draft(self, runtime):
        runtime.action("record", True)
        runtime.action("record", False)
        self.until(runtime, lambda: runtime.draft_ready)

    def open_language_menu(self, runtime):
        runtime.action("menu", True)
        self.assertEqual(runtime.menu_view()["title"], "ThumbTalk")
        runtime.action("next_language", True)
        runtime.action("record", True)
        runtime.action("record", False)
        self.assertEqual(runtime.menu_view()["title"], "Language")

    def test_immediate_skips_local_review_and_passes_exact_payload(self):
        runtime, delivery, _, _ = self.make_runtime(auto_send=True)
        runtime.action("record", True)
        runtime.action("record", False)
        self.until(
            runtime, lambda: delivery.send.called and runtime.session.state == "idle"
        )
        delivery.draft.assert_not_called()
        self.assertEqual(delivery.send.call_args.args[1], "/s hello")

    def test_quick_menu_switches_language_only_on_release_for_next_recording(self):
        transcriber = SimpleNamespace(
            settings=SpeechSettings(),
            model=SimpleNamespace(supported_languages=["en", "sv"]),
            transcribe=lambda audio: "hej",
        )
        runtime, _, _, recorder = self.make_runtime(transcriber=transcriber)
        runtime.chat = replace(runtime.chat, languages=("en", "sv"))
        recorder.prepare.assert_called_once()
        self.open_language_menu(runtime)
        runtime.action("next_language", True)
        runtime.action("next_language", True)
        self.assertEqual(runtime.language, "en")
        self.assertEqual(runtime.menu_view()["selected"], "sv")
        self.assertEqual(
            runtime.menu_view()["items"], [("en", "English"), ("sv", "Swedish")]
        )
        runtime.action("menu", False)
        self.assertEqual(runtime.language, "sv")
        self.draft(runtime)
        self.assertEqual(transcriber.settings.language, "sv")

    def test_language_wheel_never_adds_nonfavorite_starting_language(self):
        runtime, _, _, _ = self.make_runtime()
        runtime.chat = replace(runtime.chat, languages=("en",))
        runtime.language = None
        self.open_language_menu(runtime)
        for _ in range(3):
            runtime.action("next_language", True)
        self.assertEqual(runtime.menu_view()["items"], [("en", "English")])
        runtime.action("menu", False)
        self.assertEqual(runtime.language, "en")

    def test_parakeet_menu_does_not_pretend_to_force_language(self):
        runtime, _, _, _ = self.make_runtime()
        runtime.transcriber.model.automatic_language = True
        runtime.language = None
        self.open_language_menu(runtime)
        runtime.action("next_language", True)
        self.assertIn("Automatic · Parakeet", runtime.menu_text())
        runtime.action("menu", False)
        self.assertIsNone(runtime.language)

    def test_radial_whisper_uses_latest_reply_without_a_typed_recipient(self):
        runtime, delivery, _, _ = self.make_runtime()
        runtime.action("menu", True)
        runtime.action("next_channel", True)
        runtime.action("next_channel", True)
        runtime.action("record", True)
        runtime.action("record", False)
        self.assertIn(("reply", "Whisper"), runtime.menu_view()["items"])
        runtime.action("previous_channel", True)
        runtime.action("menu", False)
        self.assertEqual(runtime.destination.channel, "reply")
        self.draft(runtime)
        self.assertEqual(
            runtime.session.destination.message(runtime.session.text), "/r hello"
        )

    def test_channel_pages_debounce_and_require_new_selection(self):
        runtime, delivery, _, _ = self.make_runtime()
        runtime.action("menu", True)
        runtime.menu_nav.hover = "channel"
        runtime.menu_nav.enter()
        runtime.menu_nav.hover = "guild"
        runtime.hub.poll = Mock(return_value=[("pad", 1, "PADRSHOULDER", True)] * 2)
        runtime.poll()
        self.assertEqual(runtime.menu_view()["page"], 1)
        self.assertIn(("trade", "Trade"), runtime.menu_view()["items"])
        self.assertIsNone(runtime.menu_nav.selection())
        self.assertFalse(runtime.menu_nav.armed)
        runtime.action("menu", False)
        self.assertEqual(runtime.destination.channel, "say")
        delivery.send.assert_not_called()
        runtime.hub.poll = Mock(return_value=[("pad", 1, "PADRSHOULDER", False)])
        runtime.poll()
        runtime.action("menu", True)
        runtime.menu_nav.hover = "channel"
        runtime.menu_nav.enter()
        runtime.hub.poll = Mock(return_value=[("pad", 1, "PADRSHOULDER", True)])
        runtime.poll()
        runtime.action("next_channel", True)
        runtime.action("menu", False)
        self.assertEqual(runtime.destination.channel, "general")
        delivery.send.assert_not_called()

    def test_channel_paging_respects_custom_shoulder_binding(self):
        runtime, _, _, _ = self.make_runtime()
        runtime.matcher = BindingMatcher(
            {**runtime.chat.bindings, "record": "pad:PADRSHOULDER"}
        )
        runtime.action("menu", True)
        runtime.menu_nav.hover = "channel"
        runtime.menu_nav.enter()
        runtime.hub.poll = Mock(return_value=[("pad", 1, "PADRSHOULDER", True)])
        runtime.poll()
        self.assertEqual(runtime.menu_view()["page"], 0)

    def test_back_then_release_does_not_apply_hovered_choice(self):
        runtime, _, _, _ = self.make_runtime()
        runtime.chat = replace(runtime.chat, languages=("en", "no"))
        self.open_language_menu(runtime)
        runtime.action("previous_language", True)
        runtime.menu_back()
        runtime.action("menu", False)
        self.assertEqual(runtime.language, "en")

    def test_right_stick_events_enter_category_and_apply_only_on_menu_release(self):
        transcriber = SimpleNamespace(
            settings=SpeechSettings(),
            model=SimpleNamespace(supported_languages=["en", "sv"]),
            transcribe=lambda audio: "hej",
        )
        runtime, _, _, _ = self.make_runtime(transcriber=transcriber)
        runtime.chat = replace(runtime.chat, languages=("en", "sv"))
        runtime.action("menu", True)

        def point(x, y, now):
            runtime.hub.poll = Mock(return_value=[("stick", 7, "right", (x, y))])
            with patch("thumbtalk_runtime.time.monotonic", return_value=now):
                runtime.poll()

        point(0, -1, 1)
        point(0, -1, 1.4)
        self.assertEqual(runtime.menu_nav.section, "language")
        point(0, 1, 2)
        self.assertIsNone(runtime.menu_nav.selection())
        point(0, 0, 3)
        point(0, 1, 4)
        self.assertEqual(runtime.language, "en")
        runtime.action("menu", False)
        self.assertEqual(runtime.language, "sv")

    def test_radial_camera_lease_releases_on_back_and_runtime_shutdown(self):
        runtime, delivery, _, _ = self.make_runtime()
        runtime.action("menu", True)
        self.assertTrue(delivery.menu_signal.call_args.args[0])
        runtime.action("cancel", True)
        self.assertFalse(runtime.menu_open)
        self.assertFalse(delivery.menu_signal.call_args.args[0])
        runtime.action("menu", True)
        runtime.close()
        self.assertFalse(delivery.menu_signal.call_args.args[0])

    def test_b_goes_back_then_closes_without_applying_or_recording(self):
        runtime, delivery, _, recorder = self.make_runtime()
        runtime.chat = replace(runtime.chat, languages=("en", "no"))
        self.open_language_menu(runtime)
        runtime.action("previous_language", True)
        for expected in (True, False):
            runtime.hub.poll = Mock(
                return_value=[("pad", 7, "PAD2", True), ("pad", 7, "PAD2", False)]
            )
            runtime.poll()
            self.assertEqual(runtime.menu_open, expected)
            self.assertEqual(runtime.menu_nav.section, "root")
        runtime.action("menu", False)
        self.assertEqual(runtime.language, "en")
        self.assertEqual(runtime.session.state, "idle")
        recorder.start.assert_not_called()
        delivery.draft.assert_not_called()

    def test_b_keeps_its_custom_binding_outside_the_menu(self):
        runtime, _, _, recorder = self.make_runtime()
        runtime.matcher = BindingMatcher(
            {**runtime.chat.bindings, "record": "pad:PAD2"}
        )
        runtime.hub.poll = Mock(return_value=[("pad", 7, "PAD2", True)])
        runtime.poll()
        self.assertEqual(runtime.session.state, "recording")
        recorder.start.assert_called_once()

    def test_stick_disconnect_closes_menu_without_applying_hover(self):
        runtime, _, _, _ = self.make_runtime()
        self.open_language_menu(runtime)
        runtime.menu_nav.source = 7
        runtime.hub.poll = Mock(return_value=[("disconnect", 7, "pad", False)])
        runtime.poll()
        self.assertFalse(runtime.menu_open)
        self.assertEqual(runtime.language, "en")

    def test_loading_progress_does_not_release_worker_slot(self):
        runtime, _, _, _ = self.make_runtime()
        runtime.ready, runtime.busy = False, True
        runtime.events.put(("progress", 0, "Downloading small", None))
        runtime.poll()
        self.assertTrue(runtime.busy)
        self.assertFalse(runtime.ready)
        self.assertEqual(runtime.load_status, "Downloading small")

    def test_practice_retains_exact_audio_until_next_recording_or_close(self):
        runtime, _, _, recorder = self.make_runtime()
        runtime.practice = True
        audio = [0.1, 0.2, -0.3]
        recorder.stop.return_value = audio
        self.draft(runtime)
        self.assertIs(runtime.last_audio, audio)
        runtime.cancel()
        runtime.action("record", True)
        self.assertIsNone(runtime.last_audio)
        runtime.action("record", False)
        self.until(runtime, lambda: runtime.draft_ready)
        runtime.close()
        self.assertIsNone(runtime.last_audio)

    def test_gameplay_does_not_retain_audio_for_playback(self):
        runtime, _, _, _ = self.make_runtime()
        self.draft(runtime)
        self.assertIsNone(runtime.last_audio)

    def test_hold_release_prepares_but_does_not_send(self):
        runtime, delivery, _, _ = self.make_runtime()
        self.draft(runtime)
        delivery.draft.assert_called_once()
        delivery.send.assert_not_called()
        runtime.action("record", True)
        runtime.action("record", False)
        self.until(runtime, lambda: runtime.session.state == "idle")
        delivery.send.assert_called_once()
        self.assertEqual(delivery.draft.call_args.args[0], "/s hello")

    def test_send_and_discard_requests_do_not_claim_game_acknowledgement(self):
        for action in ("send", "cancel"):
            runtime, _, _, _ = self.make_runtime()
            notices = []
            runtime.notify = notices.append
            self.draft(runtime)
            self.assertIn("Review requested. Check WoW chat.", notices)
            self.assertNotIn("Review in WoW chat.", notices)
            if action == "send":
                runtime.action("confirm", False)
            else:
                runtime.cancel()
            self.until(runtime, lambda: runtime.session.state == "idle")
            self.assertEqual(
                notices[-1],
                "Send requested. Check WoW chat."
                if action == "send"
                else "Discard requested. Check WoW chat.",
            )
            self.assertNotIn("Message sent.", notices)
            self.assertNotIn("Message discarded.", notices)

    def test_review_escape_waits_for_release_before_cleanup_chord(self):
        runtime, delivery, _, _ = self.make_runtime()
        self.draft(runtime)
        runtime.hub.poll = Mock(return_value=[("key", 0, "esc", True)])
        runtime.poll()
        self.assertEqual(runtime.session.state, "clearing")
        delivery.cancel.assert_not_called()
        runtime.hub.poll = Mock(return_value=[("key", 0, "esc", False)])
        self.until(runtime, lambda: runtime.session.state == "idle")
        delivery.cancel.assert_called_once()

    def test_cancel_discards_prepared_draft(self):
        runtime, delivery, _, _ = self.make_runtime()
        self.draft(runtime)
        runtime.action("menu", True)
        runtime.action("menu", False)
        self.until(runtime, lambda: runtime.session.state == "idle")
        delivery.cancel.assert_called_once()
        delivery.send.assert_not_called()

    def test_gameplay_b_does_not_discard_review_by_default(self):
        runtime, delivery, _, recorder = self.make_runtime()
        self.draft(runtime)
        recorder.start.reset_mock()
        runtime.hub.poll = Mock(
            return_value=[("pad", 7, "PAD2", True), ("pad", 7, "PAD2", False)]
        )
        runtime.poll()
        self.assertEqual(runtime.session.state, "preview")
        self.assertTrue(runtime.draft_ready)
        delivery.cancel.assert_not_called()
        delivery.send.assert_not_called()
        recorder.start.assert_not_called()
        self.assertFalse(runtime.shortcut_held)

    def test_explicit_b_cancel_binding_works_with_optional_shortcut_off(self):
        runtime, delivery, _, _ = self.make_runtime()
        runtime.matcher = BindingMatcher(
            {**runtime.chat.bindings, "cancel": "pad:PAD2"}
        )
        self.draft(runtime)
        runtime.hub.poll = Mock(
            return_value=[("pad", 7, "PAD2", True), ("pad", 7, "PAD2", False)]
        )
        runtime.poll()
        runtime.hub.poll = Mock(return_value=[])
        self.until(runtime, lambda: runtime.session.state == "idle")
        delivery.cancel.assert_called_once()
        delivery.send.assert_not_called()

    def test_review_b_and_escape_discard_without_sending_or_recording(self):
        for kind, button in (("pad", "PAD2"), ("key", "esc")):
            with self.subTest(button=button):
                runtime, delivery, _, recorder = self.make_runtime()
                runtime.chat = replace(runtime.chat, review_b_cancel=True)
                self.draft(runtime)
                recorder.start.reset_mock()
                events = [
                    (kind, 7, button, True),
                    (kind, 7, button, True),
                    (kind, 7, button, False),
                ]
                runtime.hub.poll = Mock(side_effect=[events], return_value=[])
                runtime.poll()
                runtime.hub.poll = Mock(return_value=[])
                self.until(runtime, lambda: runtime.session.state == "idle")
                delivery.cancel.assert_called_once()
                delivery.send.assert_not_called()
                recorder.start.assert_not_called()
                self.assertFalse(runtime.shortcut_held)

    def test_review_b_cancels_while_menu_is_held(self):
        runtime, delivery, _, _ = self.make_runtime()
        runtime.chat = replace(runtime.chat, review_b_cancel=True)
        self.draft(runtime)
        runtime.action("menu", True)
        runtime.hub.poll = Mock(
            return_value=[("pad", 7, "PAD2", True), ("pad", 7, "PAD2", False)]
        )
        runtime.poll()
        runtime.hub.poll = Mock(return_value=[])
        runtime.action("menu", False)
        self.until(runtime, lambda: runtime.session.state == "idle")
        self.assertFalse(runtime.menu_open)
        delivery.cancel.assert_called_once()
        delivery.send.assert_not_called()

    def test_review_b_focus_change_discards_without_typing_elsewhere(self):
        runtime, delivery, foreground, _ = self.make_runtime()
        runtime.chat = replace(runtime.chat, review_b_cancel=True)
        self.draft(runtime)
        foreground.current.return_value = Target("test", 2)
        runtime.hub.poll = Mock(
            return_value=[("pad", 7, "PAD2", True), ("pad", 7, "PAD2", False)]
        )
        runtime.poll()
        self.assertEqual(runtime.session.state, "idle")
        delivery.cancel.assert_not_called()
        delivery.send.assert_not_called()

    def test_review_explicit_b_confirm_still_sends(self):
        runtime, delivery, _, _ = self.make_runtime()
        runtime.matcher = BindingMatcher(
            {**runtime.chat.bindings, "confirm": "pad:PAD2"}
        )
        self.draft(runtime)
        runtime.hub.poll = Mock(
            return_value=[("pad", 7, "PAD2", True), ("pad", 7, "PAD2", False)]
        )
        runtime.poll()
        runtime.hub.poll = Mock(return_value=[])
        self.until(runtime, lambda: runtime.session.state == "idle")
        delivery.send.assert_called_once()
        delivery.cancel.assert_not_called()

    def test_focus_change_blocks_confirmation(self):
        runtime, delivery, foreground, _ = self.make_runtime()
        self.draft(runtime)
        foreground.current.return_value = Target("test", 2)
        runtime.action("confirm", False)
        runtime.poll()
        delivery.send.assert_not_called()
        self.assertEqual(runtime.session.state, "preview")

    def test_modifiers_delay_staging_without_losing_the_transcript(self):
        runtime, delivery, _, _ = self.make_runtime()
        runtime.hub.physical_modifiers.add("ctrl")
        runtime.action("record", True)
        runtime.action("record", False)
        self.until(runtime, lambda: runtime.session.state == "preview")
        delivery.draft.assert_not_called()
        self.assertFalse(runtime.draft_ready)
        runtime.hub.physical_modifiers.clear()
        self.until(runtime, lambda: runtime.draft_ready)
        delivery.draft.assert_called_once()
        delivery.send.assert_not_called()

    def test_no_style_preparation_for_review_or_discard_with_old_preference(self):
        runtime, delivery, _, _ = self.make_runtime()
        self.draft(runtime)
        delivery.prepare_chat.assert_not_called()
        delivery.draft.assert_called_once()
        delivery.reset_mock()
        runtime.cancel()
        self.until(runtime, lambda: runtime.session.state == "idle")
        delivery.prepare_chat.assert_not_called()

    def test_immediate_send_and_practice_never_prepare_style(self):
        runtime, delivery, _, _ = self.make_runtime(auto_send=True)
        runtime.action("record", True)
        runtime.action("record", False)
        self.until(runtime, lambda: delivery.send.called and not runtime.busy)
        delivery.prepare_chat.assert_not_called()
        delivery.send.assert_called_once()
        practice, _, _, _ = self.make_runtime()
        practice.practice, practice.delivery = True, None
        self.draft(practice)
        practice.action("record", True)
        practice.action("record", False)
        self.until(practice, lambda: practice.session.state == "idle")

    def test_old_true_style_setting_is_ignored_for_immediate_and_review(self):
        for auto_send in (True, False):
            with self.subTest(auto_send=auto_send):
                runtime, delivery, _, _ = self.make_runtime(auto_send=auto_send)
                runtime.chat = replace(runtime.chat, classic_chat=True)
                delivery.prepare_chat.side_effect = AssertionError("Unsafe style setup invoked")
                runtime.action("record", True)
                runtime.action("record", False)
                if auto_send:
                    self.until(runtime, lambda: runtime.session.state == "idle")
                    delivery.send.assert_called_once()
                    delivery.draft.assert_not_called()
                else:
                    self.until(runtime, lambda: runtime.draft_ready)
                    delivery.draft.assert_called_once()
                    runtime.action("record", False)
                    self.until(runtime, lambda: runtime.session.state == "idle")
                    delivery.send.assert_called_once()
                delivery.prepare_chat.assert_not_called()

    def test_held_button_delays_staging_and_new_button_stops_delivery(self):
        from thumbtalk_activity import InputActivity

        runtime, delivery, _, _ = self.make_runtime()
        now = [0.0]
        runtime.hub.activity = activity = InputActivity(lambda: now[0])
        activity.button("key", 0, "w", True)
        runtime.action("record", True)
        runtime.action("record", False)
        self.until(runtime, lambda: runtime.session.state == "preview")
        delivery.draft.assert_not_called()
        self.assertFalse(runtime.draft_ready)
        self.assertEqual(delivery.mock_calls, [])
        # A held button leaves text pending without emitting any key/chord.
        runtime.deferred = ("draft", time.monotonic() - 10)
        runtime.poll()
        self.assertIsNotNone(runtime.deferred)
        activity.button("key", 0, "w", False)
        runtime.poll()
        delivery.draft.assert_not_called()
        now[0] += 0.21
        self.until(runtime, lambda: runtime.draft_ready)
        guard = delivery.draft.call_args.args[1]
        self.assertTrue(guard())
        activity.button("key", 0, "w", True)
        activity.button("key", 0, "w", False)
        now[0] += 1
        self.assertFalse(guard(), "Delivery cannot resume after gameplay interrupts it")
        delivery.send.assert_not_called()

    def test_analog_movement_allows_review_send_and_discard(self):
        from thumbtalk_activity import InputActivity

        for finish in ("send", "discard"):
            runtime, delivery, _, _ = self.make_runtime()
            now = [1.0]
            runtime.hub.activity = activity = InputActivity(lambda: now[0])
            now[0] += 1
            activity.axis("pad", 1, 0, 0.9)
            self.draft(runtime)
            guard = delivery.draft.call_args.args[1]
            activity.axis("pad", 1, 0, 0)
            activity.axis("pad", 1, 1, -0.9)
            activity.axis("pad", 1, 2, 0.8)
            self.assertTrue(guard())
            if finish == "send":
                runtime.action("confirm", False)
            else:
                runtime.cancel()
            self.until(runtime, lambda: runtime.session.state == "idle")
            operation = delivery.send if finish == "send" else delivery.cancel
            operation.assert_called_once()
            self.assertTrue(operation.call_args.args[0]())

    def test_pad_button_mashing_defers_text_and_interrupts_new_input(self):
        from thumbtalk_activity import InputActivity

        for kind, button in (
            ("pad", "PADLSHOULDER"),
            ("pad", "PADRTRIGGER"),
            ("raw", "rear"),
        ):
            runtime, delivery, _, _ = self.make_runtime()
            now = [1.0]
            runtime.hub.activity = activity = InputActivity(lambda: now[0])
            activity.axis("pad", 1, 0, 0.9)
            activity.button(kind, 1, button, True)
            runtime.action("record", True)
            runtime.action("record", False)
            self.until(runtime, lambda: runtime.session.state == "preview")
            for _ in range(5):
                activity.button(kind, 1, button, False)
                now[0] += 0.05
                activity.button(kind, 1, button, True)
                runtime.poll()
            delivery.draft.assert_not_called()
            activity.button(kind, 1, button, False)
            now[0] += 0.21
            self.until(runtime, lambda: runtime.draft_ready)
            guard = delivery.draft.call_args.args[1]
            self.assertTrue(guard())
            activity.button(kind, 1, button, True)
            activity.button(kind, 1, button, False)
            now[0] += 0.21
            self.assertFalse(
                guard(), "Do not resume a transaction after a new gamepad action"
            )
            delivery.send.assert_not_called()

    def test_interrupted_draft_can_only_be_discarded_not_sent_or_retyped(self):
        runtime, delivery, _, _ = self.make_runtime()
        delivery.draft.side_effect = RuntimeError("Movement interrupted typing")
        runtime.action("record", True)
        runtime.action("record", False)
        self.until(runtime, lambda: runtime.session.state == "interrupted")
        runtime.action("record", True)
        runtime.action("record", False)
        runtime.action("confirm", False)
        runtime.poll()
        delivery.draft.assert_called_once()
        delivery.send.assert_not_called()
        runtime.action("menu", True)
        runtime.action("menu", False)
        self.until(runtime, lambda: runtime.session.state == "idle")
        delivery.cancel.assert_called_once()

    def test_cancel_while_waiting_for_button_release_discards_pending_text(self):
        from thumbtalk_activity import InputActivity

        runtime, delivery, _, _ = self.make_runtime()
        runtime.hub.activity = InputActivity()
        runtime.hub.activity.button("key", 0, "w", True)
        runtime.action("record", True)
        runtime.action("record", False)
        self.until(runtime, lambda: runtime.session.state == "preview")
        runtime.cancel()
        runtime.poll()
        self.assertEqual(runtime.session.state, "idle")
        self.assertIsNone(runtime.deferred)
        delivery.draft.assert_not_called()

    def test_cancel_during_transcription_discards_late_result(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        transcriber = SimpleNamespace(
            settings=None, transcribe=lambda audio: (gate.wait(2), "old text")[1]
        )
        runtime, delivery, _, _ = self.make_runtime(transcriber=transcriber)
        runtime.action("record", True)
        runtime.action("record", False)
        runtime.cancel()
        gate.set()
        self.until(runtime, lambda: not runtime.busy)
        delivery.draft.assert_not_called()
        delivery.send.assert_not_called()
        self.assertEqual(runtime.session.state, "idle")

    def test_toggle_stop_release_does_not_send_a_fast_transcription(self):
        runtime, delivery, _, _ = self.make_runtime(mode="toggle")
        runtime.action("record", True)
        runtime.action("record", False)
        runtime.action("record", True)
        self.until(runtime, lambda: runtime.draft_ready)
        runtime.action("record", False)
        runtime.poll()
        delivery.send.assert_not_called()
        runtime.action("record", True)
        runtime.action("record", False)
        self.until(runtime, lambda: runtime.session.state == "idle")
        delivery.send.assert_called_once()

    def test_recording_limit_release_does_not_confirm(self):
        runtime, delivery, _, _ = self.make_runtime()
        runtime.action("record", True)
        runtime.session.started -= 60
        self.until(runtime, lambda: runtime.draft_ready)
        runtime.action("record", False)
        runtime.poll()
        delivery.send.assert_not_called()

    def test_destination_and_language_are_frozen(self):
        runtime, delivery, _, _ = self.make_runtime()
        runtime.action("record", True)
        runtime.destination = Destination("guild")
        runtime.language = "no"
        runtime.action("record", False)
        self.until(runtime, lambda: runtime.draft_ready)
        self.assertEqual(
            runtime.session.destination.message(runtime.session.text), "/s hello"
        )
        self.assertEqual(runtime.transcriber.settings.language, "en")

    def test_auto_send_is_explicit_opt_in(self):
        runtime, delivery, _, _ = self.make_runtime(auto_send=True)
        runtime.action("record", True)
        runtime.action("record", False)
        self.until(runtime, lambda: runtime.session.state == "idle")
        delivery.send.assert_called_once()

    def test_close_is_idempotent(self):
        runtime, _, foreground, _ = self.make_runtime()
        runtime.close()
        runtime.close()
        foreground.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
