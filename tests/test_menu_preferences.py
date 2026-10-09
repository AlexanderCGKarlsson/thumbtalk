"""Runtime menu choices: load off-thread, preserve preferences, never send on selection."""

import gc
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import weakref

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_chat import ChatSettings, load_chat, save_menu_preferences
from thumbtalk_runtime import Runtime
from thumbtalk_speech import (
    SpeechSettings,
    resolve_settings,
    prepare_model,
    prepare_parakeet,
)


class FakeTranscriber:
    def __init__(self, settings):
        self.settings = settings
        self.model = SimpleNamespace(
            supported_languages=["en", "no"],
            automatic_language=settings.model == "parakeet-v3",
        )

    def transcribe(self, audio):
        return "hello"


class MenuPreferencesTests(unittest.TestCase):
    def wait(self, runtime, condition):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            runtime.poll()
            if condition():
                return
            time.sleep(0.005)
        self.fail("Runtime did not finish: " + runtime.load_status)

    def runtime(self, factory=None, save=None):
        factory = factory or (lambda settings, log, **kwargs: FakeTranscriber(settings))
        hub = SimpleNamespace(
            error="", physical_modifiers=set(), poll=lambda: [], close=Mock()
        )
        recorder = Mock()
        rt = Runtime(
            SpeechSettings(language="no"),
            ChatSettings(),
            practice=True,
            hub=hub,
            recorder=recorder,
            notify=Mock(),
            transcriber_factory=factory,
            model_available=lambda p: p in ("accuracy", "balanced", "parakeet"),
            save_preferences=save or Mock(),
        )
        self.addCleanup(rt.close)
        self.wait(rt, lambda: rt.ready)
        return rt

    def choose(self, rt, section, choice):
        rt.action("menu", True)
        rt.menu_nav.hover = section
        self.assertTrue(rt.menu_nav.enter())
        rt.menu_nav.hover = choice
        rt.action("menu", False)

    def test_only_downloaded_models_and_current_model_appear(self):
        rt = self.runtime()
        rt.action("menu", True)
        self.assertEqual(
            set(dict(rt.menu_view()["items"])),
            {"language", "channel", "model", "sending"},
        )
        rt.menu_nav.hover = "model"
        rt.menu_nav.enter()
        self.assertEqual(
            set(dict(rt.menu_view()["items"])), {"accuracy", "balanced", "parakeet"}
        )
        self.assertEqual(rt.menu_view()["selected"], "accuracy")
        with self.assertRaisesRegex(ValueError, "Download"):
            rt.change_model("economy")
        self.assertTrue(rt.ready)

    def test_model_load_is_async_saves_after_success_and_releases_old_weights(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        self.addCleanup(release.set)
        old = None

        def factory(settings, log, **kwargs):
            calls.append((settings, kwargs))
            if settings.model == "base":
                gc.collect()
                self.assertIsNone(old(), "Old transcriber still holds model weights")
                started.set()
                release.wait(3)
            return FakeTranscriber(settings)

        save = Mock()
        rt = self.runtime(factory, save)
        old = weakref.ref(rt.transcriber)
        self.choose(rt, "model", "balanced")
        self.assertTrue(started.wait(1))
        self.assertFalse(rt.ready)
        self.assertFalse(rt.menu_open)
        save.assert_not_called()
        rt.action("record", True)
        rt.recorder.start.assert_not_called()
        release.set()
        self.wait(rt, lambda: rt.ready)
        self.assertEqual(rt.speech.model, "base")
        self.assertEqual(rt.language, "no")
        self.assertTrue(calls[-1][1]["local_files_only"])
        self.assertEqual(save.call_args.args[0]["model"], "base")
        rt.recorder.prepare.assert_called_once()  # No microphone restart needed.

    def test_failed_model_restores_previous_without_saving_bad_choice(self):
        def factory(settings, log, **kwargs):
            if settings.model == "base":
                raise RuntimeError("unusable weights")
            return FakeTranscriber(settings)

        save = Mock()
        rt = self.runtime(factory, save)
        self.choose(rt, "model", "balanced")
        self.wait(rt, lambda: rt.ready)
        self.assertEqual(rt.speech.model, "small")
        save.assert_not_called()
        self.assertIn("Previous model restored", rt.notify.call_args.args[0])

    def test_failed_model_and_rollback_leave_speech_unavailable(self):
        count = 0

        def factory(settings, log, **kwargs):
            nonlocal count
            count += 1
            if count > 1:
                raise RuntimeError("models unavailable")
            return FakeTranscriber(settings)

        save = Mock()
        rt = self.runtime(factory, save)
        self.choose(rt, "model", "balanced")
        self.wait(rt, lambda: not rt.busy)
        self.assertFalse(rt.ready)
        self.assertIn("Reopen ThumbTalk", rt.load_status)
        save.assert_not_called()

    def test_parakeet_uses_automatic_language_and_switching_back_is_valid(self):
        rt = self.runtime()
        self.choose(rt, "model", "parakeet")
        self.wait(rt, lambda: rt.ready)
        self.assertIsNone(rt.language)
        self.assertEqual(rt.speech.device, "cpu")
        rt.speech.validate()
        self.choose(rt, "model", "accuracy")
        self.wait(rt, lambda: rt.ready)
        rt.speech.validate()
        self.assertEqual(rt.speech.model, "small")

    def test_sending_mode_applies_on_release_without_triggering_a_send(self):
        save = Mock()
        rt = self.runtime(save=save)
        rt.action("menu", True)
        rt.menu_nav.hover = "sending"
        rt.menu_nav.enter()
        rt.menu_nav.hover = "immediate"
        self.assertFalse(rt.chat.auto_send)
        rt.action("menu", False)
        self.assertTrue(rt.chat.auto_send)
        self.assertEqual(rt.session.state, "idle")
        self.assertIsNone(rt.deferred)
        save.assert_called_once_with({}, {"auto_send": True})
        self.choose(rt, "sending", "review")
        self.assertFalse(rt.chat.auto_send)

    def test_back_cancels_setting_and_in_flight_messages_cannot_change_it(self):
        save = Mock()
        rt = self.runtime(save=save)
        rt.action("menu", True)
        rt.menu_nav.hover = "sending"
        rt.menu_nav.enter()
        rt.menu_nav.hover = "immediate"
        rt.menu_back()
        rt.action("menu", False)
        self.assertFalse(rt.chat.auto_send)
        save.assert_not_called()
        for state in ("recording", "transcribing", "drafting", "preview", "sending"):
            rt.session.state = state
            with self.assertRaises(ValueError):
                rt.change_model("balanced")
        rt.session.state = "idle"

    def test_cannot_save_sending_choice_does_not_change_runtime(self):
        rt = self.runtime(save=Mock(side_effect=OSError("read only")))
        with self.assertRaises(OSError):
            self.choose(rt, "sending", "immediate")
        self.assertFalse(rt.chat.auto_send)
        self.assertFalse(rt.menu_open)

    def test_loaded_model_save_failure_is_reported_without_false_rollback(self):
        rt = self.runtime(save=Mock(side_effect=OSError("read only")))
        self.choose(rt, "model", "balanced")
        self.wait(rt, lambda: rt.ready)
        self.assertEqual(rt.speech.model, "base")
        self.assertIn("could not save", rt.notify.call_args.args[0])

    def test_preferences_merge_preserves_bindings_display_and_install_info(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "speech": {"language": "no"},
                        "chat": {
                            "overlay_mode": "off",
                            "input_device": "mic",
                            "languages": ["en", "no"],
                        },
                        "addon_installs": ["/games/wow"],
                        "model_downloads": ["accuracy", "balanced"],
                    }
                )
            )
            save_menu_preferences(
                path, {"preset": "balanced", "model": "base"}, {"auto_send": True}
            )
            saved = json.loads(path.read_text())
            self.assertEqual(saved["addon_installs"], ["/games/wow"])
            self.assertEqual(saved["model_downloads"], ["accuracy", "balanced"])
            self.assertEqual(load_chat(path).overlay_mode, "off")
            self.assertEqual(load_chat(path).input_device, "mic")
            self.assertTrue(load_chat(path).auto_send)
            self.assertEqual(resolve_settings(path).language, "no")
            self.assertEqual(resolve_settings(path).model, "base")
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                save_menu_preferences(path, {}, {"auto_send": "yes"})
            self.assertEqual(path.read_bytes(), before)

    def test_missing_cached_files_never_start_download_during_switch(self):
        with patch(
            "faster_whisper.utils.download_model", side_effect=FileNotFoundError
        ) as download:
            with self.assertRaises(ValueError):
                prepare_model("small", lambda _: None, local_files_only=True)
            download.assert_called_once_with("small", local_files_only=True)
        with patch(
            "huggingface_hub.hf_hub_download", side_effect=FileNotFoundError
        ) as download:
            with self.assertRaises(ValueError):
                prepare_parakeet(lambda _: None, local_files_only=True)
            self.assertEqual(download.call_count, 1)
            self.assertTrue(download.call_args.kwargs["local_files_only"])
