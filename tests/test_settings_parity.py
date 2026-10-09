"""Exercise the actual Setup controls and saved settings on both platform paths."""

from contextlib import contextmanager, ExitStack
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
import sounddevice  # Load the host audio library before mocking the UI platform.
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QPushButton
from thumbtalk_chat import ACTIONS, CHANNELS, ChatSettings, save_menu_preferences
from thumbtalk_models import CATALOG
from thumbtalk_overlay import STATUS_MODES, STATUS_POSITIONS
from thumbtalk_speech import PRESETS, SpeechSettings, read_config, write_config
from thumbtalk_ui import Setup


class SettingsParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @contextmanager
    def setup_for(self, system):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            path = Path(directory) / "settings.json"
            speech = SpeechSettings(language="no")
            chat = replace(
                ChatSettings(),
                channel="whisper",
                recipient="Friend-Realm",
                favorites=("Friend-Realm", "Another-Realm"),
                languages=("en", "no", "sv"),
                mode="toggle",
                review_b_cancel=True,
                overlay_mode="off",
                overlay_position="bottom-left",
                max_seconds=45,
                input_device="Test microphone",
                wow_dir="/games/World of Warcraft/_retail_",
            )
            chat.bindings.update(record="mouse:x1", menu="mouse:x2")
            write_config(
                path, dict(version=1, speech=asdict(speech), chat=asdict(chat))
            )
            stack.enter_context(
                patch("thumbtalk_ui.platform.system", return_value=system)
            )
            stack.enter_context(
                patch("thumbtalk_ui.pipewire_available", return_value=False)
            )
            stack.enter_context(patch("thumbtalk_ui.managed_device", return_value=None))
            stack.enter_context(
                patch("thumbtalk_ui.model_downloaded", return_value=True)
            )
            stack.enter_context(
                patch("thumbtalk_startup.startup_enabled", return_value=False)
            )
            stack.enter_context(
                patch.object(
                    sounddevice,
                    "query_devices",
                    return_value=[dict(name="Test microphone", max_input_channels=1)],
                )
            )
            window = Setup(path, speech, chat)
            window.timer.stop()
            try:
                yield window
            finally:
                window.close()

    @staticmethod
    def options(combo):
        return [combo.itemData(i) for i in range(combo.count())]

    def test_shared_controls_save_and_reload_all_chat_preferences(self):
        saved = []
        for system in ("Linux", "Windows"):
            with self.subTest(system=system), self.setup_for(system) as window:
                self.assertEqual(self.options(window.channel), list(CHANNELS))
                self.assertEqual(set(window.change_buttons), set(ACTIONS))
                self.assertEqual(self.options(window.mode), ["hold", "toggle"])
                self.assertEqual(set(window.model_checks), set(CATALOG))
                self.assertTrue(window.update_button.isEnabled())
                self.assertTrue(window.display_button.isEnabled())
                self.assertTrue(window.auto_send.isEnabled())
                self.assertTrue(window.review_b_cancel.isEnabled())
                self.assertEqual(
                    hasattr(window, "windows_startup"), system == "Windows"
                )
                self.assertEqual(
                    hasattr(window, "copy_launch_button"), system == "Linux"
                )
                window.channel.setCurrentIndex(window.channel.findData("guild"))
                window.auto_send.setChecked(True)
                window.preset.setCurrentIndex(window.preset.findData("balanced"))
                for check in window.model_checks.values():
                    check.setChecked(True)
                expected = json.loads(
                    json.dumps(
                        asdict(replace(window.chat, channel="guild", auto_send=True))
                    )
                )
                self.assertTrue(window.save())
                document = read_config(window.path)
                self.assertEqual(document["chat"], expected)
                self.assertEqual(set(document["model_downloads"]), set(CATALOG))
                self.assertEqual(document["speech"]["model"], "base")
                self.assertEqual(document["speech"]["language"], "no")
                saved.append(document)
                # In-game choices use the same saved preferences on both OSes.
                speech, chat = save_menu_preferences(
                    window.path,
                    dict(preset="economy", **PRESETS["economy"], language="sv"),
                    dict(auto_send=False, channel="party"),
                )
                self.assertEqual(speech.model, "tiny")
                self.assertFalse(chat.auto_send)
                self.assertEqual(chat.channel, "party")
                self.assertEqual(chat.bindings, window.bindings)
                self.assertEqual(chat.overlay_mode, "off")
                self.assertTrue(chat.review_b_cancel)
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0], saved[1])

    def test_every_model_and_its_language_rules_are_available_on_both_platforms(self):
        for system in ("Linux", "Windows"):
            with self.subTest(system=system), self.setup_for(system) as window:
                self.assertEqual(set(self.options(window.preset)), set(CATALOG))
                for preset in CATALOG:
                    window.preset.setCurrentIndex(window.preset.findData(preset))
                    speech, chat = window.values()
                    self.assertEqual(speech.model, PRESETS[preset]["model"])
                    automatic = preset == "parakeet"
                    self.assertEqual(window.language.isEnabled(), not automatic)
                    self.assertEqual(window.languages.isEnabled(), not automatic)
                    self.assertEqual(speech.language, None if automatic else "no")
                    self.assertEqual(chat.languages, ("en", "no", "sv"))

    def test_overlay_modes_and_positions_can_be_saved_on_both_platforms(self):
        case = self

        class SaveDisplay(QDialog):
            def exec(self):
                mode = self.findChild(QComboBox, "statusMode")
                position = self.findChild(QComboBox, "statusPosition")
                case.assertEqual(case.options(mode), list(STATUS_MODES))
                case.assertEqual(case.options(position), list(STATUS_POSITIONS))
                mode.setCurrentIndex(mode.findData("always"))
                position.setCurrentIndex(position.findData("top-right"))
                self.findChild(QPushButton, "saveDisplay").click()
                return self.result()

        for system in ("Linux", "Windows"):
            with self.subTest(system=system), self.setup_for(system) as window:
                with patch("thumbtalk_ui.QDialog", SaveDisplay):
                    window.display_settings()
                chat = read_config(window.path)["chat"]
                self.assertEqual(chat["overlay_mode"], "always")
                self.assertEqual(chat["overlay_position"], "top-right")
                self.assertEqual(chat["bindings"], window.bindings)
