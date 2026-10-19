"""Exercise the actual Setup controls and saved settings on both platform paths."""

from contextlib import contextmanager, ExitStack
from dataclasses import asdict, replace
import json
from queue import Queue
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

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

    def test_ctrl_space_picker_replaces_old_shortcut_and_saves_on_both_platforms(self):
        from thumbtalk_chat import load_chat
        from thumbtalk_picker import BindingPicker

        def choose(dialog):
            self.assertEqual(dialog.value, "key:f8")
            dialog.tabs.setCurrentWidget(dialog.combination_page)
            self.assertFalse(dialog.use.isEnabled())
            for combo, key in zip(dialog.combination_keys, ("ctrl", "space")):
                combo.setCurrentIndex(combo.findData(key))
            self.assertEqual(dialog.value, "key:ctrl+space")
            self.assertEqual(dialog.summary.text(), "Keyboard Ctrl + Space")
            self.assertFalse(dialog.buttons["key", "f8"].isChecked())
            self.assertTrue(dialog.use.isEnabled())
            dialog.use.click()
            return dialog.result()

        for system in ("Linux", "Windows"):
            with self.subTest(system=system), self.setup_for(system) as window:
                window.assign_binding("record", "key:f8")
                with patch.object(BindingPicker, "exec", choose):
                    window.choose_binding("record")
                self.assertEqual(window.bindings["record"], "key:ctrl+space")
                self.assertTrue(window.save())
                self.assertEqual(
                    load_chat(window.path).bindings["record"], "key:ctrl+space"
                )
                matcher = window.button_monitor
                self.assertEqual(matcher.feed("key", 0, "ctrl", True), [])
                self.assertEqual(
                    matcher.feed("key", 0, "space", True), [("record", True)]
                )
                self.assertEqual(
                    matcher.feed("key", 0, "ctrl", False), [("record", False)]
                )
                self.assertEqual(matcher.feed("key", 0, "space", False), [])

    def test_picker_clear_custom_combination_conflict_and_cancel(self):
        from thumbtalk_chat import DEFAULT_BINDINGS
        from thumbtalk_picker import BindingPicker

        bindings = dict(DEFAULT_BINDINGS)
        dialog = BindingPicker("record", bindings)
        try:
            dialog.tabs.setCurrentWidget(dialog.combination_page)
            dialog.clear_selection.click()
            self.assertIsNone(dialog.value)
            self.assertFalse(dialog.use.isEnabled())
            first, second = dialog.combination_keys
            first.setCurrentIndex(first.findData("ctrl"))
            self.assertFalse(
                dialog.use.isEnabled()
            )  # A modifier alone is not a shortcut.
            second.setCurrentIndex(second.findData("ctrl"))
            self.assertFalse(
                dialog.use.isEnabled()
            )  # Repeated keys do not form a chord.
            second.setCurrentIndex(second.findData("space"))
            self.assertEqual(dialog.value, "key:ctrl+space")
            dialog.add_combination.click()
            self.assertFalse(dialog.use.isEnabled())
            third = dialog.combination_keys[2]
            third.setCurrentIndex(third.findData("shift"))
            self.assertEqual(dialog.value, "key:ctrl+shift+space")
            self.assertTrue(dialog.use.isEnabled())
            # Switching views and reopening preserves an existing long shortcut.
            dialog.tabs.setCurrentIndex(0)
            self.assertEqual(dialog.value, "key:ctrl+shift+space")
            dialog.select_binding(dialog.value)
            self.assertIs(dialog.tabs.currentWidget(), dialog.combination_page)
            self.assertEqual(
                [c.currentData() for c in dialog.combination_keys],
                ["ctrl", "shift", "space"],
            )
            dialog.reject()
            self.assertEqual(bindings, DEFAULT_BINDINGS)
        finally:
            dialog.close()
        dialog = BindingPicker("record", dict(bindings, confirm="key:ctrl+space"))
        try:
            dialog.tabs.setCurrentWidget(dialog.combination_page)
            for combo, key in zip(dialog.combination_keys, ("ctrl", "space")):
                combo.setCurrentIndex(combo.findData(key))
            self.assertFalse(dialog.use.isEnabled())
            self.assertIn("overlap", dialog.summary.text())
        finally:
            dialog.close()

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

    def test_cpu_budget_is_saved_and_kept_when_selecting_models_on_both_platforms(self):
        for system in ("Linux", "Windows"):
            with self.subTest(system=system), self.setup_for(system) as window:
                window.cpu_threads.setCurrentIndex(window.cpu_threads.findData(4))
                for preset in CATALOG:
                    window.preset.setCurrentIndex(window.preset.findData(preset))
                    self.assertTrue(window.save())
                    self.assertEqual(window.speech.cpu_threads, 4)
                    self.assertEqual(
                        read_config(window.path)["speech"]["cpu_threads"], 4
                    )

    def test_cpu_comparison_uses_recorded_language_and_requires_explicit_selection(
        self,
    ):
        import numpy as np

        with self.setup_for("Windows") as window:
            audio = np.zeros(16000, dtype="float32")
            window.runtime = Mock(
                busy=False, last_audio=audio, speech=window.speech, record_language="sv"
            )
            window.runtime.session.state = "preview"
            comparison = Mock(events=Queue())
            with patch(
                "thumbtalk_compare.Comparison", return_value=comparison
            ) as factory:
                window.start_comparison(compare_cpu=True)
            self.assertIs(factory.call_args.args[0], audio)
            self.assertEqual(factory.call_args.args[1], "sv")
            self.assertTrue(factory.call_args.kwargs["compare_cpu"])
            self.assertEqual(factory.call_args.kwargs["presets"], ["accuracy"])
            self.assertIsNone(window.runtime)
            window.choose_compared_threads(4)
            self.assertEqual(window.cpu_threads.currentData(), 2)
            comparison.events.put(
                dict(
                    kind="result",
                    preset="accuracy",
                    cpu_threads=4,
                    compare_cpu=True,
                    text="test",
                    warm_seconds=1,
                    first_seconds=2,
                    load_seconds=3,
                    cpu_percent=200,
                    peak_mib=500,
                    consistent=True,
                    background_priority=False,
                )
            )
            comparison.events.put(dict(kind="done", cancelled=False))
            window.poll_comparison()
            self.assertIsNone(window.comparison)
            self.assertEqual(window.cpu_threads.currentData(), 2)
            button = next(
                b
                for b in window.compare_panel.findChildren(QPushButton)
                if b.text() == "Use 4 CPU threads"
            )
            button.click()
            self.assertEqual(window.speech.cpu_threads, 4)
            self.assertEqual(window.speech.model, "small")
            self.assertEqual(window.speech.language, "no")

    def test_windows_finish_saves_starts_game_companion_and_minimizes(self):
        with self.setup_for("Windows") as window:
            window.pages.setCurrentIndex(4)
            window.update_navigation()
            self.assertEqual(window.next_button.text(), "Save && minimize")
            window.show()
            window.channel.setCurrentIndex(window.channel.findData("guild"))
            # Finish must replace a running voice test with the game runtime.
            practice = Mock()
            window.runtime = practice
            with (
                patch("thumbtalk_ui.Runtime") as runtime,
                patch.object(window, "ensure_hub"),
                patch("thumbtalk_startup.set_startup") as register,
            ):
                window.next_button.click()
                self.assertTrue(window.isMinimized())
                practice.close.assert_called_once()
                self.assertIs(window.runtime, runtime.return_value)
                runtime.return_value.close.assert_not_called()
                self.assertFalse(runtime.call_args.kwargs["practice"])
                self.assertEqual(runtime.call_args.args[1].channel, "guild")
                self.assertEqual(read_config(window.path)["chat"]["channel"], "guild")
                self.assertIsNotNone(window.runtime_lock)
                register.assert_not_called()  # Windows sign-in remains opt-in.
                window.close()
                runtime.return_value.close.assert_called_once()
                self.assertIsNone(window.runtime_lock)

    def test_windows_finish_stays_visible_if_model_or_runtime_is_unavailable(self):
        for failure in ("model", "runtime"):
            with self.subTest(failure=failure), self.setup_for("Windows") as window:
                window.pages.setCurrentIndex(4)
                window.show()
                with (
                    patch(
                        "thumbtalk_ui.model_downloaded", return_value=failure != "model"
                    ),
                    patch(
                        "thumbtalk_ui.Runtime", side_effect=RuntimeError("Cannot start")
                    ),
                    patch.object(window, "ensure_hub"),
                    patch.object(window, "error") as error,
                ):
                    window.next_button.click()
                    self.assertTrue(window.isVisible())
                    self.assertFalse(window.isMinimized())
                    self.assertIsNone(window.runtime)
                    self.assertIsNone(window.runtime_lock)
                    error.assert_called_once()

    def test_windows_finish_does_not_hide_or_stop_runtime_when_save_fails(self):
        with self.setup_for("Windows") as window:
            window.pages.setCurrentIndex(4)
            window.show()
            active = Mock()
            window.runtime = active
            with patch.object(window, "save", return_value=False):
                window.next_button.click()
                self.assertTrue(window.isVisible())
                self.assertFalse(window.isMinimized())
                active.close.assert_not_called()

    def test_linux_finish_closes_setup_for_steam_launch_wrapper(self):
        with self.setup_for("Linux") as window:
            window.pages.setCurrentIndex(4)
            window.update_navigation()
            self.assertEqual(window.next_button.text(), "Save && finish")
            window.show()
            with patch("thumbtalk_ui.Runtime") as runtime:
                window.next_button.click()
                self.assertFalse(window.isVisible())
                runtime.assert_not_called()

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
