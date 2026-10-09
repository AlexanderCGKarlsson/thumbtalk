import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper"))
from thumbtalk_speech import (  # noqa: E402
    SpeechSettings,
    ModelCache,
    prepare_model,
    WhisperTranscriber,
    config_path,
    normalize_language,
    read_config,
    resolve_settings,
    save_settings,
)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "settings.json"

    def test_defaults_are_multilingual_cpu_with_bounded_threads(self):
        settings = resolve_settings(self.path)
        self.assertEqual(
            (
                settings.model,
                settings.language,
                settings.device,
                settings.compute_type,
                settings.cpu_threads,
            ),
            ("small", None, "cpu", "int8", 2),
        )

    def test_language_names_and_os_locales(self):
        for value, expected in (
            ("Norwegian", "no"),
            ("nb-NO", "no"),
            ("Bokmål", "no"),
            ("nn_NO", "nn"),
            ("en-US", "en"),
            ("sv", "sv"),
            ("DE", "de"),
            ("auto", None),
        ):
            with self.subTest(value=value):
                self.assertEqual(normalize_language(value), expected)

    def test_invalid_languages_rejected(self):
        for value in ("", "madeup", 123, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_language(value)

    def test_preferences_round_trip_and_one_run_override(self):
        saved = resolve_settings(self.path, language="Norwegian", preset="accuracy")
        save_settings(self.path, saved)
        self.assertEqual(resolve_settings(self.path), saved)
        overridden = resolve_settings(self.path, language="en")
        self.assertEqual(overridden.language, "en")
        self.assertEqual(resolve_settings(self.path).language, "no")
        self.assertIsNone(resolve_settings(self.path, language="auto").language)

    def test_explicit_preset_resets_model_threads_but_keeps_language(self):
        save_settings(
            self.path, resolve_settings(self.path, language="no", preset="accuracy")
        )
        result = resolve_settings(self.path, preset="economy")
        self.assertEqual(
            (result.model, result.cpu_threads, result.language), ("tiny", 2, "no")
        )
        result = resolve_settings(
            self.path, preset="economy", model="base", cpu_threads=3
        )
        self.assertEqual((result.model, result.cpu_threads), ("base", 3))

    def test_english_only_models_require_explicit_english(self):
        for language in ("no", "auto"):
            with self.subTest(language=language), self.assertRaises(ValueError):
                resolve_settings(self.path, model="tiny.en", language=language)
        self.assertEqual(
            resolve_settings(self.path, model="tiny.en", language="en").language, "en"
        )

    def test_bad_saved_settings_rejected(self):
        for speech in (
            {"cpu_threads": 0},
            {"cpu_threads": True},
            {"cpu_threads": "2"},
            {"preset": []},
            {"device": "vulkan"},
            {"model": ""},
            {"language": "invalid"},
            {"langauge": "no"},
        ):
            self.path.write_text(json.dumps({"version": 1, "speech": speech}))
            with self.subTest(speech=speech), self.assertRaises(ValueError):
                resolve_settings(self.path)

    def test_partial_saved_preset(self):
        self.path.write_text(
            json.dumps({"version": 1, "speech": {"preset": "economy"}})
        )
        self.assertEqual(resolve_settings(self.path).model, "tiny")

    def test_invalid_document_and_schema(self):
        for contents in (
            "broken json",
            "[]",
            '{"version":2}',
            '{"version":true}',
            '{"version":1,"speech":[]}',
        ):
            self.path.write_text(contents)
            with self.subTest(contents=contents), self.assertRaises(ValueError):
                read_config(self.path)

    def test_save_preserves_other_preferences(self):
        self.path.write_text(json.dumps({"version": 1, "bindings": {"record": "F8"}}))
        save_settings(self.path, SpeechSettings())
        self.assertEqual(read_config(self.path)["bindings"], {"record": "F8"})
        self.assertFalse(list(self.path.parent.glob(".thumbtalk-*")))

    def test_platform_config_directories(self):
        with (
            patch("thumbtalk_speech.platform.system", return_value="Windows"),
            patch.dict("os.environ", {"APPDATA": self.temp.name}),
        ):
            self.assertEqual(
                config_path(), Path(self.temp.name) / "thumbtalk/settings.json"
            )
        with (
            patch("thumbtalk_speech.platform.system", return_value="Linux"),
            patch.dict("os.environ", {"XDG_CONFIG_HOME": self.temp.name}),
        ):
            self.assertEqual(
                config_path(), Path(self.temp.name) / "thumbtalk/settings.json"
            )

    def test_cli_settings_do_not_need_device_libraries(self):
        command = [sys.executable, "-S", str(ROOT / "helper/thumbtalk_cli.py")]
        for args in (
            ["--help"],
            ["--list-languages"],
            [
                "--config",
                str(self.path),
                "--language",
                "nb-NO",
                "--save-speech-settings",
            ],
        ):
            result = subprocess.run(command + args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(resolve_settings(self.path).language, "no")

    def test_invalid_cli_options_fail_before_hardware_loading(self):
        command = [
            sys.executable,
            "-S",
            str(ROOT / "helper/thumbtalk_cli.py"),
            "--config",
            str(self.path),
        ]
        for args in (
            ["--language", "invalid"],
            ["--cpu-threads", "0"],
            ["--transcribe-file", str(self.path.parent / "missing.wav")],
        ):
            result = subprocess.run(command + args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("ModuleNotFoundError", result.stderr)


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.model = Mock(supported_languages=["en", "no", "sv", "de"])
        self.factory = Mock(return_value=self.model)
        self.patch = patch.dict(
            sys.modules, {"faster_whisper": SimpleNamespace(WhisperModel=self.factory)}
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)
        preparation = patch(
            "thumbtalk_speech.prepare_model",
            side_effect=lambda model, log, **kwargs: model,
        )
        preparation.start()
        self.addCleanup(preparation.stop)

    def test_preserves_unicode_and_passes_language_and_cpu_limit(self):
        self.model.transcribe.return_value = (
            iter(
                [SimpleNamespace(text="  Blåbær og "), SimpleNamespace(text="smør.  ")]
            ),
            SimpleNamespace(language="no"),
        )
        transcriber = WhisperTranscriber(
            SpeechSettings(language="no", cpu_threads=2), log=lambda _: None
        )
        self.assertEqual(transcriber.transcribe("sample.wav"), "Blåbær og smør.")
        self.factory.assert_called_once_with(
            "small", device="cpu", compute_type="int8", cpu_threads=2, num_workers=1
        )
        kwargs = self.model.transcribe.call_args.kwargs
        self.assertEqual(kwargs["language"], "no")
        self.assertEqual(kwargs["task"], "transcribe")
        self.assertTrue(kwargs["vad_filter"])

    def test_auto_detection_and_silence(self):
        self.model.transcribe.return_value = (iter([]), SimpleNamespace(language="en"))
        transcriber = WhisperTranscriber(SpeechSettings(), log=lambda _: None)
        self.assertEqual(transcriber.transcribe("silence.wav"), "")
        self.assertIsNone(self.model.transcribe.call_args.kwargs["language"])

    def test_short_recordings_skip_inference(self):
        transcriber = WhisperTranscriber(SpeechSettings())
        self.assertEqual(transcriber.transcribe(SimpleNamespace(size=100)), "")
        self.model.transcribe.assert_not_called()

    def test_local_english_model_cannot_silently_ignore_norwegian(self):
        self.model.supported_languages = ["en"]
        for language in ("no", None):
            with self.subTest(language=language), self.assertRaises(ValueError):
                WhisperTranscriber(
                    SpeechSettings(model="/models/custom", language=language)
                )

    def test_same_model_reused_with_independent_language_settings(self):
        cache = ModelCache()
        english = cache(SpeechSettings(language="en"), lambda _: None)
        swedish = cache(SpeechSettings(language="sv"), lambda _: None)
        self.factory.assert_called_once()
        self.assertIs(english.model, swedish.model)
        self.assertIsNot(english, swedish)
        self.assertEqual(english.settings.language, "en")
        self.assertEqual(swedish.settings.language, "sv")

    def test_changed_model_replaces_single_cache(self):
        cache = ModelCache()
        cache(SpeechSettings(), lambda _: None)
        cache(SpeechSettings(model="base"), lambda _: None)
        cache(SpeechSettings(), lambda _: None)
        self.assertEqual(self.factory.call_count, 3)

    def test_cached_model_still_validates_language(self):
        self.model.supported_languages = ["en"]
        cache = ModelCache()
        cache(SpeechSettings(language="en"), lambda _: None)
        with self.assertRaises(ValueError):
            cache(SpeechSettings(language="sv"), lambda _: None)
        self.factory.assert_called_once()


class ModelPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.download = Mock(return_value=str(self.path))
        mock = patch.dict(
            sys.modules,
            {"faster_whisper.utils": SimpleNamespace(download_model=self.download)},
        )
        mock.start()
        self.addCleanup(mock.stop)
        self.log = Mock()

    def test_cached_model_needs_no_network(self):
        (self.path / "model.bin").write_bytes(b"test")
        self.assertEqual(prepare_model("small", self.log), str(self.path))
        self.download.assert_called_once_with("small", local_files_only=True)
        self.assertFalse(
            any("Downloading" in call.args[0] for call in self.log.call_args_list)
        )

    def test_missing_and_incomplete_cache_download_with_status(self):
        for missing in (True, False):
            self.download.reset_mock()
            self.download.side_effect = [
                FileNotFoundError() if missing else str(self.path),
                str(self.path),
            ]
            self.assertEqual(prepare_model("small", self.log), str(self.path))
            self.assertEqual(self.download.call_count, 2)
            self.assertTrue(
                any(
                    "Downloading small" in call.args[0]
                    for call in self.log.call_args_list
                )
            )

    def test_local_path_needs_no_download_lookup(self):
        self.assertEqual(prepare_model(str(self.path), self.log), str(self.path))
        self.download.assert_not_called()

    def test_download_failure_is_not_swallowed(self):
        self.download.side_effect = [FileNotFoundError(), RuntimeError("offline")]
        with self.assertRaisesRegex(RuntimeError, "offline"):
            prepare_model("small", self.log)


if __name__ == "__main__":
    unittest.main()
