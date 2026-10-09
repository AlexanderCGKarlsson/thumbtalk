"""Release checks without microphone capture, model downloads, or game input."""

import importlib
import os
import tempfile
import time
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch


def check_virtual_controller(window):
    """Drive SDL's virtual gamepad API, including real hotplug and button events."""
    import ctypes
    import pygame.base
    from thumbtalk_chat import BindingMatcher
    from thumbtalk_ui import pretty_binding

    hub = window.ensure_hub()
    if os.name == "nt":
        # Use pygame's loaded SDL instance. Resolving the name again in a frozen
        # bundle can load its second SDL copy and create a separate event queue.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        module = kernel32.GetModuleHandleW("SDL2.dll")
        assert module, "pygame did not load SDL2.dll"
        sdl = ctypes.CDLL("SDL2.dll", handle=module)
    else:
        sdl = ctypes.CDLL(pygame.base.__file__)
    sdl.SDL_JoystickAttachVirtual.argtypes = [ctypes.c_int] * 4
    sdl.SDL_JoystickAttachVirtual.restype = ctypes.c_int
    sdl.SDL_JoystickOpen.argtypes = [ctypes.c_int]
    sdl.SDL_JoystickOpen.restype = ctypes.c_void_p
    sdl.SDL_JoystickSetVirtualButton.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_uint8,
    ]
    sdl.SDL_JoystickClose.argtypes = [ctypes.c_void_p]
    sdl.SDL_JoystickDetachVirtual.argtypes = [ctypes.c_int]
    index = sdl.SDL_JoystickAttachVirtual(1, 6, 15, 1)
    assert index >= 0, "SDL virtual controller did not attach"
    handle = sdl.SDL_JoystickOpen(index)
    assert handle
    previous = window.bindings["record"]
    try:
        hub.poll()
        assert "Controller detected" in hub.controller_status()
        for buttons, expected in [
            ([1], "pad:PAD2"),
            ([9], "pad:PADLSHOULDER"),
            ([9, 10], "pad:PADLSHOULDER+PADRSHOULDER"),
        ]:
            window.learn("record")
            assert window.learning is not None
            for button in buttons:
                assert sdl.SDL_JoystickSetVirtualButton(handle, button, 1) == 0
                window.tick()
            assert window.learning is not None, "Binding must be captured on release"
            for button in reversed(buttons):
                assert sdl.SDL_JoystickSetVirtualButton(handle, button, 0) == 0
                window.tick()
            assert window.learning is None
            assert window.bindings["record"] == expected, window.bindings["record"]
    finally:
        sdl.SDL_JoystickClose(handle)
        sdl.SDL_JoystickDetachVirtual(index)
        hub.poll()
        window.bindings["record"] = previous
        window.button_labels["record"].setText(pretty_binding(previous))
        window.button_monitor = BindingMatcher(window.bindings)
    print("Native SDL controller hotplug, LB learning, and LB+RB chord passed.")


def check_binding_picker(window):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from thumbtalk_picker import BindingPicker
    from thumbtalk_chat import BindingMatcher

    previous = dict(window.bindings)
    failures = []

    def select():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, BindingPicker)
            dialog.tabs.setCurrentIndex(2)
            dialog.buttons["mouse", "middle"].click()
            assert dialog.value == "mouse:middle"
            assert "Middle Mouse Click" in dialog.summary.text()
            screenshots = os.environ.get("THUMBTALK_SELF_TEST_SCREENSHOTS")
            if screenshots:
                dialog.grab().save(str(Path(screenshots) / "mouse-picker.png"))
            dialog.use.click()
        except Exception as error:
            failures.append(error)
            dialog.reject()

    QTimer.singleShot(50, select)
    window.change_buttons["record"].click()
    assert not failures, failures
    assert window.bindings["record"] == "mouse:middle"
    assert "Middle Mouse Click" in window.button_labels["record"].text()
    picker = BindingPicker("record", window.bindings, window)
    picker.buttons["key", "ctrl"].click()
    picker.buttons["key", "f8"].click()
    assert picker.value == "key:ctrl+f8"
    picker.reject()
    assert window.bindings["record"] == "mouse:middle", "Cancel changed binding"
    window.bindings = previous
    window.button_monitor = BindingMatcher(previous)
    window.button_labels["record"].setText("Keyboard F8")
    print("Visual mouse picker, keyboard combination and cancel passed.")


def check_font_rendering(app):
    """Confirm usable fonts, including the Swedish characters used in testing."""
    import sys
    from PySide6.QtGui import QFontDatabase, QFontInfo, QRawFont

    assert QFontDatabase.families(), "No installed fonts found"
    font = app.font()
    assert QFontInfo(font).family(), "No default font resolved"
    raw = QRawFont.fromFont(font)
    assert raw.isValid(), "Default font did not load"
    missing = [c for c in "ThumbTalk Åäö" if not raw.supportsCharacter(ord(c))]
    assert not missing, f"UI glyphs missing from {raw.familyName()}: {missing!r}"
    if sys.platform == "linux" and getattr(sys, "frozen", False):
        expected = Path(sys._MEIPASS) / "fontconfig/fonts.conf"
        assert Path(os.environ["FONTCONFIG_FILE"]) == expected
        assert expected.is_file()
    print("Installed fonts, default UI font and Swedish glyphs passed.")


def run():
    if os.environ.get("THUMBTALK_TEST_PROTON_WINDOW"):
        proton_test_window()
        return
    if os.environ.get("THUMBTALK_TEST_ROOT_DISPLAY"):
        check_separate_displays()
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    for module in (
        "numpy",
        "av",
        "ctranslate2",
        "faster_whisper",
        "sherpa_onnx",
        "thumbtalk_compare",
        "sounddevice",
        "pygame",
        "pynput.keyboard._base",
    ):
        # pynput needs a display when using the real backend; CI uses its dummy backend.
        if module.startswith("pynput"):
            os.environ.setdefault("PYNPUT_BACKEND", "dummy")
        importlib.import_module(module)
    import numpy as np
    from thumbtalk_audio import play_recording, resample_mono

    # Exercise real bundled PyAV conversion and output-rate negotiation.
    tone = (0.2 * np.sin(2 * np.pi * 440 * np.arange(48000) / 48000)).astype("float32")
    speech_audio = resample_mono(np.column_stack([tone, tone])[:, 1], 48000, 16000)
    assert len(speech_audio) == 16000 and np.max(np.abs(speech_audio)) < 0.21
    with (
        patch(
            "sounddevice.query_devices",
            return_value={"max_output_channels": 2, "default_samplerate": 48000},
        ),
        patch("sounddevice.check_output_settings"),
        patch("sounddevice.play") as playback,
    ):
        assert abs(play_recording(speech_audio) - 1) < 0.001
        assert playback.call_args.args[0].shape == (48000, 2)
        assert playback.call_args.args[1] == 48000
    from PySide6.QtWidgets import QApplication
    from thumbtalk_ui import Setup, apply_theme
    from thumbtalk_input import InputHub
    from PySide6.QtGui import QPalette, QColor, QKeyEvent
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtWidgets import QMessageBox, QFileDialog
    from thumbtalk_chat import ChatSettings, load_chat
    from thumbtalk_speech import SpeechSettings, resolve_settings
    from thumbtalk_setup import resources

    assert (resources() / "addon/ThumbTalk/ThumbTalk.toc").is_file(), "Addon missing"
    _app = QApplication.instance() or QApplication([])
    check_font_rendering(_app)
    if os.environ.get("PYNPUT_BACKEND") == "win32":
        check_windows_text(_app)
    elif (
        os.environ.get("PYNPUT_BACKEND") == "xorg"
        and os.environ.get("QT_QPA_PLATFORM") == "xcb"
    ):
        if os.environ.get("THUMBTALK_TEST_UINPUT") == "1":
            check_linux_chat(_app)
        else:
            print(
                "Native ydotool check skipped: requires real Xorg and THUMBTALK_TEST_UINPUT=1 (Xvfb does not read uinput).",
                flush=True,
            )
    dark = QPalette()
    dark.setColor(QPalette.Window, QColor("#000000"))
    dark.setColor(QPalette.Base, QColor("#000000"))
    _app.setPalette(dark)
    apply_theme(_app)
    assert not _app.windowIcon().isNull(), "ThumbTalk app icon missing"
    for dialog in (QMessageBox(), QFileDialog()):
        assert dialog.palette().color(QPalette.Window).lightness() > 220
        assert dialog.palette().color(QPalette.WindowText).lightness() < 80
        dialog.close()
    with (
        tempfile.TemporaryDirectory() as directory,
        patch("thumbtalk_ui.model_downloaded", return_value=True),
        patch("thumbtalk_ui.pipewire_available", return_value=False),
        patch(
            "thumbtalk_ui.InputHub",
            side_effect=lambda keyboard=True: InputHub(
                keyboard=keyboard and os.environ.get("PYNPUT_BACKEND") != "dummy"
            ),
        ),
        patch(
            "sounddevice.query_devices",
            return_value=[{"name": "Test microphone", "max_input_channels": 1}],
        ),
    ):
        window = Setup(
            Path(directory) / "settings.json", SpeechSettings(), ChatSettings()
        )
        assert window.pages.count() == 5
        # Verify bundled update modules and CA certificates without a network call.
        import io
        import json
        from thumbtalk_updates import check_for_updates
        from thumbtalk_updates_ui import UpdateDialog

        response = dict(
            tag_name="v0.8.1",
            draft=False,
            prerelease=False,
            published_at="2026-10-09T12:00:00Z",
        )
        result = check_for_updates(
            "0.8.0",
            opener=lambda *args, **kwargs: io.BytesIO(json.dumps([response]).encode()),
        )
        assert result.state == "available", result.message
        updates = UpdateDialog(window, checker=lambda: result)
        updates.start_check()
        limit = time.monotonic() + 5
        while updates.checking and time.monotonic() < limit:
            _app.processEvents()
            updates.poll()
            time.sleep(0.01)
        assert not updates.checking and updates.release_button.isEnabled()
        assert updates.release_button.text() == "View update on GitHub"
        updates.close()
        print(
            "Bundled release checker, CA certificates and update dialog passed.",
            flush=True,
        )
        assert not window.review_b_cancel.isChecked()
        assert not window.values()[1].review_b_cancel
        window.review_b_cancel.setChecked(True)
        assert window.values()[1].review_b_cancel
        window.review_b_cancel.setChecked(False)
        assert not hasattr(window, "classic_chat"), "Unsafe chat-style control returned"
        assert not window.values()[1].classic_chat
        active = window.preset.currentData()
        window.model_checks["accuracy"].setChecked(True)
        window.model_checks["parakeet"].setChecked(True)
        assert window.preset.currentData() == active, (
            "Ticking a download switched the active model"
        )
        with patch(
            "thumbtalk_ui.model_downloaded", side_effect=lambda p: p == "accuracy"
        ):
            window.refresh_models()
            assert (
                not window.preset.model()
                .item(window.preset.findData("parakeet"))
                .isEnabled()
            )
        window.refresh_models()
        # Favorites are chosen by the user; no preset language pair or model change.
        previous_preset = window.preset.currentData()
        for index in range(window.languages.count()):
            item = window.languages.item(index)
            item.setCheckState(
                Qt.Checked if item.data(Qt.UserRole) in ("en", "de") else Qt.Unchecked
            )
        assert window.preset.currentData() == previous_preset
        assert set(window.values()[1].languages) == {"en", "de"}
        assert "German" in window.favorites_summary.text()
        window.overlay.message(
            "",
            True,
            menu={
                "title": "Language",
                "items": [("en", "English"), ("sv", "Swedish")],
                "selected": "sv",
                "detail": "Swedish",
            },
        )
        _app.processEvents()
        assert window.overlay.wheel.isVisible()
        assert window.overlay.wheel.view["selected"] == "sv"
        screenshots = os.environ.get("THUMBTALK_SELF_TEST_SCREENSHOTS")
        if screenshots:
            Path(screenshots).mkdir(parents=True, exist_ok=True)
            window.overlay.grab().save(str(Path(screenshots) / "language-wheel.png"))
        from PySide6.QtCore import QPoint

        for page in (0, 1):
            window.overlay.message(
                "",
                True,
                menu={
                    "title": "Channels",
                    "page": page,
                    "pages": 2,
                    "items": [
                        ("say", "Say"),
                        ("guild", "Guild"),
                        ("party", "Party"),
                        ("raid", "Raid"),
                        ("instance", "Instance"),
                        ("reply", "Reply"),
                    ]
                    if page == 0
                    else [("general", "General"), ("trade", "Trade"), ("lfg", "LFG")],
                    "selected": "guild" if page == 0 else "trade",
                    "detail": "Guild" if page == 0 else "Trade",
                },
            )
            _app.processEvents()
            assert window.overlay.wheel_header.isVisible()
            assert (
                f"Page {page + 1} of 2" in window.overlay.wheel_header.accessibleName()
            )
            wheel = window.overlay.wheel
            center = wheel.mapToGlobal(QPoint(wheel.width() // 2, wheel.height() // 2))
            screen = _app.primaryScreen().geometry()
            assert (center - screen.center()).manhattanLength() <= 2
            assert screen.contains(window.overlay.frameGeometry())
            if screenshots:
                window.overlay.grab().save(
                    str(Path(screenshots) / f"channels-{page + 1}.png")
                )
        print("Radial page header and centered wheel passed.", flush=True)
        window.overlay.message("Ready")
        assert not window.overlay.wheel.isVisible()
        check_status_display(window.overlay)
        check_display_settings(window)
        assert window.microphone.currentData() == "Test microphone"
        assert window.microphone.findText("System default") == -1
        fake_microphone = SimpleNamespace(
            peak=0.3, level=0.3, start=lambda: None, stop=lambda **kwargs: None
        )
        with patch("thumbtalk_ui.Recorder", return_value=fake_microphone):
            window.check_microphone()
            window.tick()
            assert window.mic_level.value() > 0
            window.stop_mic_check(report=True)
            assert "receiving sound" in window.mic_feedback.text()
        window.progress_labels[3].click()
        assert window.pages.currentIndex() == 3
        window.progress_labels[0].click()
        assert window.pages.currentIndex() == 0
        window.next_button.click()
        assert window.pages.currentIndex() == 1
        window.show()
        window.resize(800, 720)
        _app.processEvents()
        assert window.pages.widget(1).verticalScrollBar().maximum() == 0, (
            "Voice test should fit without scrolling"
        )
        assert not window.recording_details.isVisible()
        window.audio_options.click()
        _app.processEvents()
        assert window.audio_dialog.isVisible() and window.recording_details.isVisible()
        window.audio_dialog.accept()
        if screenshots:
            window.grab().save(str(Path(screenshots) / "try-it.png"))
        check_binding_picker(window)
        check_virtual_controller(window)
        # The real X11 backend must initialize; imports alone missed a Linux bug.
        if os.environ.get("PYNPUT_BACKEND") == "xorg":
            hub = window.ensure_hub()
            assert not hub.error, hub.error
            hub.poll()
            window.learn("record")
            from pynput.keyboard import Key, Controller

            hub.listener.wait()
            keyboard = Controller()
            keyboard.press(Key.f8)
            keyboard.release(Key.f8)
            deadline = time.monotonic() + 5
            while window.learning is not None and time.monotonic() < deadline:
                window.tick()
                _app.processEvents()
                time.sleep(0.01)
            assert window.learning is None, "Real X11 F8 event was not learned"
            assert window.bindings["record"] == "key:f8"
            from pynput.mouse import Controller as MouseController, Button
            from thumbtalk_chat import BindingMatcher, DEFAULT_BINDINGS

            assert not hub.mouse_error, hub.mouse_error
            hub.mouse_listener.wait()
            mouse = MouseController()
            for native, signal in [
                (Button.middle, "middle"),
                (Button.button8, "x1"),
                (Button.button9, "x2"),
            ]:
                window.learn("record")
                matcher = BindingMatcher(
                    dict(DEFAULT_BINDINGS, record="mouse:" + signal)
                )
                changes = []
                mouse.press(native)
                mouse.release(native)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and len(changes) < 2:
                    for event in hub.poll():
                        changes.extend(matcher.feed(*event))
                        value = window.learning[1].feed(*event)
                        if value:
                            assert value == "mouse:" + signal
                    time.sleep(0.01)
                assert changes == [("record", True), ("record", False)], changes
                window.cancel_learning()
            print(
                "Native X11 middle and side mouse buttons: hold, release and learning passed."
            )

        # Exercise SDL's normalized right-stick events in the actual packaged backend.
        stick_hub = window.ensure_hub()
        pg = stick_hub.pygame
        pg.event.post(
            pg.event.Event(
                pg.CONTROLLERAXISMOTION, instance_id=98765, axis=2, value=32767
            )
        )
        pg.event.post(
            pg.event.Event(
                pg.CONTROLLERAXISMOTION, instance_id=98765, axis=3, value=-32768
            )
        )
        stick_events = [event for event in stick_hub.poll() if event[0] == "stick"]
        assert stick_events[-1] == ("stick", 98765, "right", (1.0, -1.0))
        stick_hub.sticks.pop(98765, None)
        if window.hub:
            window.hub.close()
            window.hub = None
        from thumbtalk_runtime import Runtime

        recorder = SimpleNamespace(start=lambda: None, stop=lambda: [0.0] * 16000)
        transcriber = SimpleNamespace(
            settings=SpeechSettings(),
            model=SimpleNamespace(supported_languages=["en", "no"]),
            transcribe=lambda audio: "Hello from the voice test",
        )

        def practice_runtime(*args, **kwargs):
            kwargs.pop("transcriber_factory", None)
            return Runtime(
                *args,
                **kwargs,
                recorder=recorder,
                transcriber_factory=lambda *args: transcriber,
            )

        def until(condition):
            deadline = time.monotonic() + 10
            while not condition() and time.monotonic() < deadline:
                window.tick()
                _app.processEvents()
                time.sleep(0.01)
            assert condition(), "Voice-test state did not complete"

        with patch("thumbtalk_ui.Runtime", side_effect=practice_runtime):
            window.practice_button.click()
        assert window.runtime is not None
        until(lambda: window.runtime.ready)
        for _ in range(2):
            # A touch press/release alone must not start or stop recording.
            # Windows may convert a held touch into a context-menu gesture.
            before = window.runtime.session.state
            window.test_talk.pressed.emit()
            window.test_talk.released.emit()
            assert window.runtime.session.state == before
            window.test_talk.click()
            assert window.runtime.session.state == "recording"
            assert window.test_talk.text() == "Stop recording"
            window.test_talk.released.emit()
            assert window.runtime.session.state == "recording"
            window.test_talk.click()
            until(lambda: window.runtime.draft_ready)
            assert window.preview.toPlainText() == "Hello from the voice test"
            assert window.play_recording.isEnabled()
            with (
                patch("thumbtalk_ui.play_recording", return_value=1.0) as play,
                patch("sounddevice.stop"),
            ):
                window.play_recording.click()
                assert play.call_count == 1
                assert len(play.call_args.args[0]) == 16000
                assert play.call_args.args[1] is None
                assert window.play_recording.text() == "Stop playback"
                window.play_recording.click()
                assert not window.playback_until
            assert "1.0s recorded" in window.recording_details.text()
        print(
            "Tap-to-record setup: start, stop, repeat and playback passed.", flush=True
        )
        from queue import Queue

        events = Queue()
        fake_comparison = SimpleNamespace(
            events=events, start=lambda: None, cancel=lambda: None
        )
        with patch("thumbtalk_compare.Comparison", return_value=fake_comparison):
            window.compare_button.click()
        assert window.runtime is None
        assert not window.next_button.isEnabled()
        events.put(
            dict(
                kind="result",
                preset="parakeet",
                text="Ska vi äta på McDonalds?",
                warm_seconds=0.5,
                first_seconds=0.8,
                load_seconds=2,
                cpu_percent=150,
                peak_mib=650,
                consistent=True,
                background_priority=True,
            )
        )
        events.put(dict(kind="done", cancelled=False))
        window.poll_comparison()
        assert window.comparison is None
        assert window.next_button.isEnabled()
        window.choose_compared_model("parakeet")
        assert window.speech.model == "parakeet-v3"
        assert window.speech.language is None
        assert not window.language.isEnabled()
        window.choose_compared_model("balanced")
        assert window.language.isEnabled()
        window.move_step(1)
        assert window.runtime is None
        assert window.pages.currentIndex() == 2
        # Focused-window fallback: setup can learn function keys on Wayland too.
        window.learn("record")
        for kind in (QEvent.KeyPress, QEvent.KeyRelease):
            QApplication.sendEvent(window, QKeyEvent(kind, Qt.Key_F8, Qt.NoModifier))
        window.tick()
        assert window.learning is None
        assert window.bindings["record"] == "key:f8"
        for kind in (QEvent.KeyPress, QEvent.KeyRelease):
            QApplication.sendEvent(window, QKeyEvent(kind, Qt.Key_F7, Qt.NoModifier))
        window.tick()
        assert "Menu button detected" in window.status.text()
        assert window.save()
        screenshots = os.environ.get("THUMBTALK_SELF_TEST_SCREENSHOTS")
        if screenshots:
            target = Path(screenshots)
            target.mkdir(parents=True, exist_ok=True)
            window.show()
            _app.processEvents()
            window.grab().save(str(target / "buttons.png"))
            message = QMessageBox(window)
            message.setWindowTitle("ThumbTalk")
            message.setText("Talk button detected. Your mapping works.")
            message.setStandardButtons(QMessageBox.Ok)
            message.show()
            _app.processEvents()
            message.grab().save(str(target / "message.png"))
            message.close()
            picker = QFileDialog(window)
            picker.setOption(QFileDialog.DontUseNativeDialog)
            picker.setFileMode(QFileDialog.Directory)
            picker.setDirectory(directory)
            picker.resize(700, 480)
            picker.show()
            _app.processEvents()
            picker.grab().save(str(target / "folder-picker.png"))
            picker.close()
        # Discovery runs off the GUI thread; install requires an explicit tap.
        game = Path(directory) / "Games/World of Warcraft/_classic_beta_"
        (game / "Interface").mkdir(parents=True)
        other = Path(directory) / "SD Card/World of Warcraft/_retail_"
        (other / "Interface").mkdir(parents=True)
        with patch("thumbtalk_ui.discover_wow", return_value=[game, other]):
            window.move_step(1)
            window.move_step(1)
            assert window.pages.currentIndex() == 4
            until(lambda: not window.wow_searching)
        assert window.wow_locations.count() == 2
        assert "WoW Retail" in window.wow_locations.itemText(1)
        assert not (game / "Interface/AddOns/ThumbTalk").exists()
        window.wow_locations.setCurrentIndex(1)
        with patch("thumbtalk_ui.QMessageBox.information") as confirmation:
            window.install_addon_button.click()
            assert confirmation.call_args.args[1] == "Addon installed"
            assert "WoW Retail" in confirmation.call_args.args[2]
        assert window.install_addon_button.text() == "Installed ✓"
        if hasattr(window, "copy_launch_button"):
            with (
                patch(
                    "thumbtalk_ui.steam_launch_option",
                    return_value="thumbtalk %command%",
                ),
                patch("thumbtalk_ui.QMessageBox.information") as confirmation,
            ):
                window.copy_launch_button.click()
                assert QApplication.clipboard().text() == "thumbtalk %command%"
                assert window.copy_launch_button.text() == "Copied!"
                assert confirmation.call_args.args[1] == "Copied!"
        assert (other / "Interface/AddOns/ThumbTalk/ThumbTalk.toc").is_file()
        assert window.chat.wow_dir == str(other.resolve())
        assert "ThumbTalk is installed" in window.wow_feedback.text()
        _app.processEvents()
        # Long Windows install paths and display scaling can require scrolling.
        # Every action must remain reachable, including the Windows startup option.
        page = window.pages.widget(4)
        actions = [window.install_addon_button, window.search_wow_button]
        for name in ("copy_launch_button", "windows_startup"):
            if hasattr(window, name):
                actions.append(getattr(window, name))
        for action in actions:
            page.ensureWidgetVisible(action)
            _app.processEvents()
            assert (
                page.viewport()
                .rect()
                .contains(action.mapTo(page.viewport(), action.rect().center()))
            ), "WoW setup action is not reachable: " + action.text()
        assert page.horizontalScrollBar().maximum() == 0
        page.verticalScrollBar().setValue(0)
        if screenshots:
            _app.processEvents()
            window.grab().save(str(target / "wow.png"))
        window.wow_locations.setCurrentIndex(0)
        assert window.install_addon_button.text() == "Install addon"
        with patch("thumbtalk_ui.discover_wow", return_value=[]):
            window.wow_locations.clear()
            window.search_wow_button.click()
            until(lambda: not window.wow_searching)
        assert "wasn’t found" in window.wow_feedback.text()
        assert not window.install_addon_button.isEnabled()
        # JSON restores language favorites as a list. Reopen the actual saved
        # setup, including its microphone and bindings, just as an update does.
        saved_path = Path(directory) / "settings.json"
        expected_speech, expected_chat = window.values()
        assert window.save()
        window.close()
        saved_bytes = saved_path.read_bytes()
        restored_chat = load_chat(saved_path)
        assert isinstance(restored_chat.languages, list)
        reopened = Setup(saved_path, resolve_settings(saved_path), restored_chat)
        _app.processEvents()
        actual_speech, actual_chat = reopened.values()
        assert actual_speech == expected_speech
        assert actual_chat.languages == expected_chat.languages
        assert actual_chat.bindings == expected_chat.bindings
        assert actual_chat.input_device == expected_chat.input_device
        assert reopened.pages.count() == 5
        assert saved_path.read_bytes() == saved_bytes, (
            "Opening setup changed saved settings"
        )
        if hasattr(reopened, "windows_startup"):
            with (
                patch("thumbtalk_startup.set_startup") as register,
                patch.object(reopened, "confirmation"),
                patch.object(reopened, "error") as error,
            ):
                reopened.windows_startup.setChecked(False)
                reopened.windows_startup.click()
                register.assert_called_with(True, saved_path)
                assert reopened.windows_startup.isChecked()
                with patch.object(
                    reopened,
                    "save",
                    side_effect=AssertionError(
                        "Disabling startup must not require setup"
                    ),
                ):
                    reopened.windows_startup.click()
                register.assert_called_with(False, saved_path)
                register.side_effect = PermissionError("denied")
                reopened.windows_startup.click()
                assert not reopened.windows_startup.isChecked()
                error.assert_called_once()
            print(
                "Windows startup checkbox: opt-in, disable and failed-write rollback passed.",
                flush=True,
            )
        # Exercise the real minimized startup path without opening a real microphone.
        reopened.timer.stop()
        before_startup = saved_path.read_bytes()
        with (
            patch("thumbtalk_ui.Runtime") as runtime,
            patch.object(reopened, "ensure_hub"),
            patch.object(
                reopened,
                "save",
                side_effect=AssertionError("Startup must not rewrite preferences"),
            ),
        ):
            assert reopened.start_at_login()
            assert reopened.isMinimized()
            assert runtime.call_args.args[0] == expected_speech
            assert runtime.call_args.args[1].input_device == expected_chat.input_device
            assert saved_path.read_bytes() == before_startup
            duplicate = Setup(saved_path, resolve_settings(saved_path), restored_chat)
            assert not duplicate.start_at_login()
            assert runtime.call_count == 1, (
                "Duplicate startup created another companion"
            )
            reopened.close()
        print(
            "Minimized startup, saved preferences and duplicate prevention passed.",
            flush=True,
        )
    if os.environ.get("THUMBTALK_PIPEWIRE_TEST") == "1":
        from thumbtalk_runtime import Recorder
        from thumbtalk_pipewire import SYSTEM_AUDIO, Playback

        capture = Recorder(SYSTEM_AUDIO)
        try:
            capture.prepare()
            capture.start()
            time.sleep(1)
            captured = capture.stop()
            assert len(captured) > 8000 and np.isfinite(captured).all()
            assert float(np.max(np.abs(captured))) < 0.001, (
                "Virtual silent input is corrupted"
            )
            playback = Playback(tone[:24000])
            playback.thread.join(timeout=15)
            try:
                assert playback.process.poll() == 0, playback.error
            finally:
                playback.stop()
        finally:
            capture.close()
        print("PipeWire native virtual-microphone capture and speaker playback passed.")
    print(
        "ThumbTalk bundle check passed: speech libraries, guided setup, voice-test flow, settings, addon."
    )


def check_separate_displays():
    """Use two real X servers to check Qt overlay routing and game key reception."""
    from Xlib import X, Xatom
    from Xlib.display import Display
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from thumbtalk_ui import run_app
    from thumbtalk_input import InputHub
    from thumbtalk_platform import Foreground, Target, KeyboardDelivery
    from thumbtalk_camera import MenuCameraLock
    from thumbtalk_speech import SpeechSettings
    from thumbtalk_chat import ChatSettings
    from pynput.keyboard import Controller, Key

    import faulthandler

    faulthandler.dump_traceback_later(90)
    game_name = os.environ["DISPLAY"]
    root_name = os.environ["THUMBTALK_TEST_ROOT_DISPLAY"]
    game = Display(game_name)
    root = Display(root_name)
    for connection, identity in ((game, 1), (root, 0)):
        connection.screen().root.change_property(
            connection.intern_atom("GAMESCOPE_XWAYLAND_SERVER_ID"),
            Xatom.CARDINAL,
            32,
            [identity],
        )
        connection.sync()
    target = game.screen().root.create_window(
        0, 0, 500, 300, 0, game.screen().root_depth, X.InputOutput, X.CopyFromParent
    )
    # The helper window uses the same generic class as WoW. Only its process
    # identity distinguishes it, just like Battle.net and WoW under Proton.
    target.set_wm_class("steam_app_123", "steam_app_123")
    target.map()
    game.sync()
    import subprocess
    import sys

    child = subprocess.Popen(
        [r"Z:\games\World of Warcraft\_classic_beta_\WowClassicB.exe", "--self-test"],
        executable=sys.executable,
        env={**os.environ, "THUMBTALK_TEST_PROTON_WINDOW": "1"},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        line = child.stdout.readline()
        assert line.startswith("PROTON_WINDOW="), line
        wow_id = int(line.split("=", 1)[1])
        foreground = Foreground()
        expected = Target("Linux", wow_id, child.pid)
        assert foreground.current() == expected, "Proton WoW process was rejected"
    except Exception:
        child.communicate("close\n", timeout=20)
        raise
    received, errors = [], []
    verifying_runtime = False

    class ProbeRuntime:
        def __init__(self, speech, chat, notify, **kwargs):
            self.closed = False
            self.menu_open = False
            self.session = SimpleNamespace(state="idle")
            self.hub = InputHub()
            self.notify = notify
            self.hub.listener.wait()
            notify("Ready for display test")
            QTimer.singleShot(400, send_key)
            QTimer.singleShot(1600, verify)

        def poll(self):
            if verifying_runtime:
                return
            for event in self.hub.poll():
                if event == ("key", 0, "f8", True):
                    received.append(event)
                    self.notify("Talk key received on game display")

        def close(self):
            self.closed = True
            self.hub.close()
            assert self.hub.poll() == []

    def send_key():
        keyboard = Controller()
        keyboard.press(Key.f8)
        keyboard.release(Key.f8)

    def verify():
        nonlocal verifying_runtime
        try:
            assert received, "Game display did not receive F8"
            assert os.environ["DISPLAY"] == game_name
            assert game.get_input_focus().focus.id == wow_id, "Overlay stole game focus"
            assert foreground.current() == expected, (
                "Overlay changed WoW identification"
            )
            target.set_input_focus(X.RevertToParent, X.CurrentTime)
            game.sync()
            assert foreground.current() is None, "Non-WoW app was accepted"
            game.create_resource_object("window", wow_id).set_input_focus(
                X.RevertToParent, X.CurrentTime
            )
            game.sync()
            assert foreground.current() == expected, (
                "Returning to WoW was not recognised"
            )
            app = QApplication.instance()
            assert app.property("thumbtalkOverlayDisplay") == root_name
            overlay = next(
                w
                for w in app.topLevelWidgets()
                if w.windowTitle() == "ThumbTalk status"
            )
            assert overlay.screen_canvas, "Gamescope did not get a full-size surface"
            overlay.message(
                "Menu",
                True,
                menu={
                    "title": "Language",
                    "items": [("en", "English"), ("sv", "Swedish")],
                    "selected": "sv",
                    "detail": "Swedish",
                },
            )
            app.processEvents()
            from PySide6.QtCore import QPoint

            center = overlay.wheel.mapTo(
                overlay, QPoint(overlay.wheel.width() // 2, overlay.wheel.height() // 2)
            )
            assert abs(center.x() - overlay.width() / 2) <= 1
            assert abs(center.y() - overlay.height() / 2) <= 1
            assert overlay.size() == app.primaryScreen().geometry().size()
            assert overlay.grab().toImage().pixelColor(1, 1).alpha() == 0, (
                "Overlay corner is opaque"
            )
            check_status_display(overlay)
            native = root.create_resource_object("window", int(overlay.winId()))
            opacity = native.get_full_property(
                root.intern_atom("_NET_WM_WINDOW_OPACITY"), Xatom.CARDINAL
            )
            assert opacity is not None and int(opacity.value[0]) == 0, (
                "Dismissed overlay opacity was not cleared"
            )
            print(
                "Status hiding, positions, radial independence and native opacity clearing passed.",
                flush=True,
            )
            check_clipboard_display(app, game, root)
            if os.environ.get("THUMBTALK_TEST_UINPUT") == "1":
                print("Checking native camera keys", flush=True)
                camera_delivery = KeyboardDelivery()
                try:
                    lease = MenuCameraLock(
                        foreground.current, camera_delivery.menu_signal
                    )
                    lease.update(True)
                    lease.close()
                finally:
                    camera_delivery.close()
                print("Camera keys sent", flush=True)
            else:
                print(
                    "Native camera key check needs real uinput/Xorg; skipped on Xvfb.",
                    flush=True,
                )
            assert foreground.current() == expected
            native = root.create_resource_object("window", int(overlay.winId()))
            prop = native.get_full_property(
                root.intern_atom("GAMESCOPE_EXTERNAL_OVERLAY"), Xatom.CARDINAL
            )
            assert prop is not None and int(prop.value[0]) == 1, (
                "Overlay was not registered on the root display"
            )
            verifying_runtime = True
            check_live_runtime_input(overlay)
        except Exception as error:
            errors.append(str(error))
        finally:
            QApplication.instance().quit()

    try:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("thumbtalk_ui.Runtime", ProbeRuntime),
        ):
            run_app(
                Path(directory) / "settings.json",
                SpeechSettings(),
                ChatSettings(),
                headless=True,
            )
        assert not errors, errors
        assert received
        print(
            "Proton process identification, XRes host PID, focus rejection and return passed."
        )
        print(
            "Separate X11 displays: game F8 input, overlay root registration, focus, and shutdown passed."
        )
    finally:
        print("Closing display test", flush=True)
        foreground.close()
        output, _ = child.communicate("close\n", timeout=20)
        assert "KEYSYM=65480" in output and "KEYSYM=65481" in output, output
        assert child.returncode == 0
        print(
            "Centred transparent Gamescope canvas and native F11/F12 camera signals passed."
        )
        print("Closing test X11 connections", flush=True)
        target.destroy()
        game.close()
        root.close()
        faulthandler.cancel_dump_traceback_later()


def check_live_runtime_input(overlay):
    """Check actual runtime dispatch after idle hides the Gamescope overlay."""
    from PySide6.QtWidgets import QApplication
    from pynput.keyboard import Controller, Key
    from pynput.mouse import Controller as Mouse, Button
    from thumbtalk_chat import ChatSettings
    from thumbtalk_runtime import Runtime
    from thumbtalk_speech import SpeechSettings
    import numpy as np

    app = QApplication.instance()
    holder = []
    starts = []
    messages = []

    def notify(text):
        messages.append(text)
        runtime = holder[0] if holder else None
        menu = runtime.menu_view() if runtime and runtime.menu_open else None
        persistent = (
            bool(runtime and runtime.session.state != "idle") or menu is not None
        )
        overlay.message(text, persistent, menu=menu)

    recorder = SimpleNamespace(
        prepare=lambda: None,
        start=lambda: starts.append(True),
        stop=lambda **kwargs: np.zeros(16000, dtype="float32"),
        close=lambda: None,
    )
    transcriber = SimpleNamespace(
        model=SimpleNamespace(supported_languages=["en"], automatic_language=False),
        transcribe=lambda audio: "",
    )
    bindings = dict(ChatSettings().bindings)
    bindings.update(record="key:f8", menu="mouse:middle")
    runtime = Runtime(
        SpeechSettings(language="en"),
        ChatSettings(bindings=bindings),
        notify,
        recorder=recorder,
        transcriber_factory=lambda *args: transcriber,
    )
    holder.append(runtime)
    keyboard, mouse = Controller(), Mouse()

    def until(predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            runtime.poll()
            app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError("Runtime input timeout: " + repr(messages))

    try:
        runtime.hub.listener.wait()
        runtime.hub.mouse_listener.wait()
        until(lambda: runtime.ready)
        assert not overlay.isVisible(), "Idle status should be hidden"
        mouse.press(Button.middle)
        until(lambda: runtime.menu_open)
        assert overlay.isVisible() and overlay.wheel.isVisible()
        mouse.release(Button.middle)
        until(lambda: not runtime.menu_open)
        assert not overlay.isVisible()
        keyboard.press(Key.f8)
        until(lambda: runtime.session.state == "recording")
        assert starts and overlay.isVisible()
        assert any(text.startswith("Recording") for text in messages)
        keyboard.release(Key.f8)
        until(lambda: runtime.session.state == "idle" and not runtime.busy)
        assert any(text.startswith("Transcribing") for text in messages)
        mouse.press(Button.middle)
        until(lambda: runtime.menu_open)
        assert overlay.isVisible() and overlay.wheel.isVisible()
        mouse.release(Button.middle)
        until(lambda: not runtime.menu_open)
        print(
            "Live runtime: hidden idle, middle-click radial and F8 recording dispatch passed.",
            flush=True,
        )
    finally:
        keyboard.release(Key.f8)
        mouse.release(Button.middle)
        runtime.close()


def proton_test_window():
    """A separate native X11 client with Proton's class and a Wine-style argv[0]."""
    import sys
    from Xlib import X, Xatom
    from Xlib.display import Display

    display = Display()
    window = display.screen().root.create_window(
        0, 0, 500, 300, 0, display.screen().root_depth, X.InputOutput, X.CopyFromParent
    )
    window.change_attributes(event_mask=X.KeyPressMask | X.KeyReleaseMask)
    window.set_wm_class("steam_app_123", "steam_app_123")
    # Deliberately wrong namespace PID: the guard must prefer XRes's host PID.
    window.change_property(
        display.intern_atom("_NET_WM_PID"), Xatom.CARDINAL, 32, [999999]
    )
    window.map()
    window.set_input_focus(X.RevertToParent, X.CurrentTime)
    display.sync()
    print(f"PROTON_WINDOW={window.id}", flush=True)
    try:
        sys.stdin.readline()
        display.sync()
        while display.pending_events():
            event = display.next_event()
            if event.type == X.KeyPress:
                print(
                    f"KEYSYM={display.keycode_to_keysym(event.detail, 0)}", flush=True
                )
    finally:
        window.destroy()
        display.close()


def check_status_display(overlay):
    from PySide6.QtWidgets import QApplication
    from thumbtalk_overlay import STATUS_POSITIONS, status_point

    app = QApplication.instance()
    overlay.configure("automatic", "top-center")
    overlay.message("Ready · Say")
    assert not overlay.isVisible()
    for text in ("Recording", "Transcribing", "Review your draft"):
        overlay.message(text, True)
        assert overlay.isVisible() and not overlay.timer.isActive()
    overlay.message("Ready · Say")
    assert not overlay.isVisible()
    overlay.message("Microphone disconnected")
    assert overlay.isVisible() and overlay.timer.isActive()
    overlay.timer.timeout.emit()
    assert not overlay.isVisible()
    overlay.configure("always", "top-center")
    overlay.message("Ready · Say")
    assert overlay.isVisible() and not overlay.timer.isActive()
    for position in STATUS_POSITIONS:
        overlay.configure("automatic", position)
        overlay.message("Recording", True)
        app.processEvents()
        area = app.primaryScreen().geometry()
        x, y = status_point(
            position,
            area.width(),
            area.height(),
            overlay.panel.width(),
            overlay.panel.height(),
        )
        point = (
            overlay.panel.pos()
            if overlay.screen_canvas
            else overlay.pos() - area.topLeft()
        )
        assert abs(point.x() - x) <= 1 and abs(point.y() - y) <= 1, (
            position,
            point,
            x,
            y,
        )
    overlay.configure("off", "bottom-left")
    overlay.message("Recording", True)
    assert not overlay.isVisible()
    overlay.message(
        "Menu",
        True,
        menu={
            "title": "Language",
            "items": [("en", "English")],
            "selected": "en",
            "detail": "English",
        },
    )
    assert overlay.isVisible() and overlay.wheel.isVisible()
    overlay.message("Ready")
    assert not overlay.isVisible()
    overlay.configure("automatic", "top-center")
    overlay.dismiss()


def check_display_settings(window):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QPushButton, QComboBox
    from thumbtalk_chat import load_chat

    errors = []

    def save_dialog():
        dialog = QApplication.activeModalWidget()
        try:
            mode = dialog.findChild(QComboBox, "statusMode")
            position = dialog.findChild(QComboBox, "statusPosition")
            mode.setCurrentIndex(mode.findData("off"))
            position.setCurrentIndex(position.findData("bottom-left"))
            dialog.findChild(QPushButton, "saveDisplay").click()
        except Exception as error:
            errors.append(str(error))
            dialog.reject()

    QTimer.singleShot(0, save_dialog)
    window.display_button.click()
    assert not errors, errors
    saved = load_chat(window.path)
    assert saved.overlay_mode == "off" and saved.overlay_position == "bottom-left"
    assert window.values()[1].overlay_mode == "off"
    print("Status display dialog saves visibility and position.")


def windows_test_editor():
    """Native test target for WM_CHAR ordering, independent of Qt's key mapper.

    The fixture consumes the reserved F-key signals, like the addon does;
    Enter and every Unicode character still pass through the native edit control.
    This validates Windows delivery, not WoW's secure chat handlers.
    """
    import ctypes
    from ctypes import wintypes as w

    u = ctypes.WinDLL("user32", use_last_error=True)
    u.CreateWindowExW.argtypes = [
        w.DWORD,
        w.LPCWSTR,
        w.LPCWSTR,
        w.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        w.HWND,
        w.HMENU,
        w.HINSTANCE,
        w.LPVOID,
    ]
    u.CreateWindowExW.restype = w.HWND
    for name in [
        "SetForegroundWindow",
        "SetFocus",
        "GetWindowTextLengthW",
        "DestroyWindow",
    ]:
        getattr(u, name).argtypes = [w.HWND]
    u.GetFocus.restype = w.HWND
    u.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    u.SetWindowTextW.argtypes = [w.HWND, w.LPCWSTR]
    u.PeekMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
    u.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
    u.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
    u.DispatchMessageW.restype = ctypes.c_ssize_t
    u.SetWindowLongPtrW.argtypes = [w.HWND, ctypes.c_int, ctypes.c_void_p]
    u.SetWindowLongPtrW.restype = ctypes.c_void_p
    u.CallWindowProcW.argtypes = [ctypes.c_void_p, w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    u.CallWindowProcW.restype = ctypes.c_ssize_t
    PROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

    class NativeEditor:
        def __init__(self):
            self.h = u.CreateWindowExW(
                0,
                "EDIT",
                "",
                0x10CF0000 | 0x04 | 0x1000,
                100,
                100,
                600,
                300,
                None,
                None,
                None,
                None,
            )
            assert self.h, ctypes.get_last_error()

            def proc(hwnd, msg, wp, lp):
                if msg in (0x100, 0x101, 0x104, 0x105) and 0x74 <= wp <= 0x7B:
                    return 0
                return u.CallWindowProcW(self.original, hwnd, msg, wp, lp)

            self.proc = PROC(proc)
            self.original = u.SetWindowLongPtrW(self.h, -4, self.proc)
            assert self.original, ctypes.get_last_error()

        def setWindowTitle(self, t):
            pass

        def resize(self, *args):
            pass

        def show(self):
            pass

        def activateWindow(self):
            u.SetForegroundWindow(self.h)

        def setFocus(self):
            u.SetFocus(self.h)

        def hasFocus(self):
            return u.GetFocus() == self.h

        def clear(self):
            u.SetWindowTextW(self.h, "")

        def toPlainText(self):
            b = ctypes.create_unicode_buffer(u.GetWindowTextLengthW(self.h) + 1)
            u.GetWindowTextW(self.h, b, len(b))
            return b.value.replace("\r\n", "\n")

        def close(self):
            u.SetWindowLongPtrW(self.h, -4, self.original)
            u.DestroyWindow(self.h)

        def pump(self):
            msg = w.MSG()
            while u.PeekMessageW(ctypes.byref(msg), self.h, 0, 0, 1):
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))

    return NativeEditor()


def check_windows_text(app):
    """Exercise real SendInput packets and echo filtering in the Windows backend."""
    import threading
    from pynput.keyboard import Listener
    from thumbtalk_input import InputHub
    from thumbtalk_platform import KeyboardDelivery

    editor = windows_test_editor()
    editor.setWindowTitle("ThumbTalk Unicode delivery test")
    editor.resize(500, 250)
    editor.show()
    editor.activateWindow()
    editor.setFocus()
    packets = []
    errors = []

    def capture(message, data):
        if data.flags & 0x10:  # LLKHF_INJECTED
            packets.append(data.vkCode)
        return True

    hub = InputHub()
    listener = Listener(win32_event_filter=capture)
    try:
        assert not hub.error, hub.error
        listener.start()
        listener.wait()
        hub.listener.wait()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            editor.pump()
            hub.poll()
            time.sleep(0.01)
        assert editor.hasFocus(), "Unicode test editor did not gain focus"
        assert hub.activity.text_ready(), "Test controller was not neutral"
        version = hub.activity.button_version
        hub.activity.axis("pad", 999, 0, 0.9)
        text = "wasd bags menus /s æøå 😀"
        delivery = KeyboardDelivery(on_key=hub.expect_injected)

        def type_text():
            try:
                delivery._type(text, lambda: hub.activity.text_ready(version))
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=type_text)
        worker.start()
        deadline = time.monotonic() + 45
        while (
            worker.is_alive() or editor.toPlainText() != text
        ) and time.monotonic() < deadline:
            editor.pump()
            hub.poll()
            time.sleep(0.01)
        worker.join(timeout=1)
        assert not worker.is_alive(), "Unicode delivery did not finish"
        assert not errors, errors
        assert editor.toPlainText() == text, repr(editor.toPlainText())
        assert packets and set(packets) == {0xE7}, packets
        assert hub.activity.text_ready(version), (
            "Our text packets were mistaken for player input"
        )
        packets.clear()
        delivery.menu_signal(True, lambda: True)
        delivery.menu_signal(False, lambda: True)
        deadline = time.monotonic() + 2
        while len(packets) < 4 and time.monotonic() < deadline:
            editor.pump()
            time.sleep(0.01)
        assert packets == [0x7A, 0x7A, 0x7B, 0x7B], packets
        # This checks the OS input path in a normal editor, not WoW's security model.
        editor.clear()
        packets.clear()

        clipboard = app.clipboard()
        from PySide6.QtCore import QMimeData

        previous = QMimeData()
        previous.setText("clipboard before ThumbTalk")
        previous.setData("application/x-thumbtalk-test", b"keep this format")
        clipboard.setMimeData(previous)

        def submit_text():
            try:

                def guard():
                    return (
                        hub.activity.text_ready(version) and not hub.physical_modifiers
                    )

                delivery.prepare_chat("native-test-window", True, guard)
                with patch("secrets.token_hex", return_value="aabbccdd"):
                    delivery.draft("/s Move left! æøå 😀", guard)
                delivery.send(guard)
                delivery.cancel(guard)
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=submit_text)
        worker.start()
        deadline = time.monotonic() + 45
        while (
            worker.is_alive()
            or editor.toPlainText()
            != "/ttr aabbccdd 1 1 2f73204d6f7665206c6566742120c3a6c3b8c3a520f09f9880\n/s Move left! æøå 😀\n"
        ) and time.monotonic() < deadline:
            editor.pump()
            hub.poll()
            time.sleep(0.01)
        worker.join(timeout=1)
        assert not worker.is_alive() and not errors, errors
        assert (
            editor.toPlainText()
            == "/ttr aabbccdd 1 1 2f73204d6f7665206c6566742120c3a6c3b8c3a520f09f9880\n/s Move left! æøå 😀\n"
        ), repr(editor.toPlainText())
        assert clipboard.text() == "clipboard before ThumbTalk"
        assert (
            bytes(clipboard.mimeData().data("application/x-thumbtalk-test"))
            == b"keep this format"
        )
        assert packets.count(0x0D) == 4, packets
        assert packets.count(0x1B) == 0, packets
        for key in (0x78, 0x79):  # local review submitted/discarded, no editor mutation
            assert packets.count(key) == 2, packets
        assert 0x74 not in packets and 0x75 not in packets, "Old focus signals remain"
        assert 0x56 not in packets, "Windows text delivery used a V shortcut"
        assert delivery.clipboard is None, (
            "Windows text insertion touched the clipboard"
        )
        assert not hub.physical_modifiers and hub.activity.text_ready(version)
        print(
            "Windows native slash, local-review transport, Unicode send without reopening chat and unchanged clipboard with analog movement held passed.",
            flush=True,
        )
        print(
            "Windows native Unicode packets, accents/surrogates, input-echo guard and F11/F12 signals passed.",
            flush=True,
        )
    finally:
        if "delivery" in locals():
            delivery.close()
        listener.stop()
        listener.join(timeout=1)
        hub.close()
        editor.close()


def check_linux_chat(app):
    """Check real uinput -> Xorg -> Qt events, including our input echo guard."""
    import threading
    from PySide6.QtWidgets import QTextEdit, QLineEdit
    from PySide6.QtCore import QObject, QEvent, Qt
    import re
    import zlib
    from Xlib import X, display
    from thumbtalk_input import InputHub
    from thumbtalk_platform import KeyboardDelivery

    class InboxSimulator(QObject):
        def __init__(self, editor):
            super().__init__(editor)
            self.editor = editor
            self.field = QLineEdit(editor)
            self.field.hide()
            self.field.textChanged.connect(self.received)
            self.history = []
            self.payload = None
            self.copies = 0

        def received(self, value):
            match = re.fullmatch(
                r"TTREVIEW1:aabbccdd:([0-9a-f]+):([0-9a-f]{8});", value
            )
            if match:
                raw = bytes.fromhex(match[1])
                assert f"{zlib.adler32(raw):08x}" == match[2]
                self.payload = raw.decode()
                self.field.setText("TTRECEIVED1:aabbccdd;")
                self.field.selectAll()

        def eventFilter(self, obj, event):
            if event.type() != QEvent.KeyPress:
                return False
            mods = event.modifiers()
            if (
                obj is self.field
                and event.key() == Qt.Key_C
                and mods & Qt.ControlModifier
            ):
                self.copies += 1
            if not (mods & Qt.ControlModifier and mods & Qt.ShiftModifier):
                return False
            key = event.key()
            if key == Qt.Key_F8:
                self.payload = None
                self.field.show()
                self.field.setText("TTREADY1:aabbccdd;")
                self.field.setFocus()
                self.field.selectAll()
            elif key in (Qt.Key_F6, Qt.Key_F7):
                self.field.clearFocus()
                self.field.hide()
                self.editor.setFocus()
                if key == Qt.Key_F7 and self.payload is not None:
                    self.history[:] = [self.payload]
                self.payload = None
            elif key in (Qt.Key_F9, Qt.Key_F10):
                self.history.clear()
            else:
                return False
            return True

    editor = QTextEdit()
    editor.setWindowTitle("ThumbTalk native chat input test")
    editor.show()
    inbox = InboxSimulator(editor)
    app.installEventFilter(inbox)
    app.processEvents()
    connection = display.Display()
    window = connection.create_resource_object("window", int(editor.winId()))
    window.set_input_focus(X.RevertToParent, X.CurrentTime)
    connection.sync()
    hub = InputHub()
    errors = []
    try:
        assert not hub.error, hub.error
        hub.listener.wait()
        deadline = time.monotonic() + 0.4
        while time.monotonic() < deadline:
            app.processEvents()
            hub.poll()
            time.sleep(0.01)
        version = hub.activity.button_version
        hub.activity.axis("pad", 999, 0, 0.9)
        delivery = KeyboardDelivery(on_key=hub.expect_injected)

        def guard():
            return hub.activity.text_ready(version) and not hub.physical_modifiers

        clipboard = app.clipboard()
        from PySide6.QtCore import QMimeData

        previous = QMimeData()
        previous.setText("clipboard before ThumbTalk")
        previous.setData("application/x-thumbtalk-test", b"keep this format")
        clipboard.setMimeData(previous)

        def run_delivery():
            try:
                delivery.draft("/s hello æøå 😀", guard)
            except Exception as error:
                errors.append(error)

        def pump_worker(function, complete):
            worker = threading.Thread(target=function)
            worker.start()
            deadline = time.monotonic() + 30
            while (
                (worker.is_alive() or not complete())
                and not errors
                and time.monotonic() < deadline
            ):
                app.processEvents()
                hub.poll()
                time.sleep(0.01)
            worker.join(timeout=1)
            assert not worker.is_alive() and not errors, errors
            assert complete()

        pump_worker(run_delivery, lambda: inbox.history == ["/s hello æøå 😀"])
        assert editor.toPlainText() == "", "Review entered the ordinary chat editor"
        assert not inbox.field.isVisible(), "Inbox remained visible"
        assert inbox.copies == 2, ("copy event count", inbox.copies)
        assert clipboard.text() == "clipboard before ThumbTalk"
        pump_worker(lambda: delivery.cancel(guard), lambda: not inbox.history)
        assert editor.toPlainText() == "", "Discard entered chat"
        pump_worker(run_delivery, lambda: bool(inbox.history))

        def send_accepted():
            try:
                delivery.prepare_chat("native-test-window", True, guard)
                delivery.send(guard)
            except Exception as error:
                errors.append(error)

        expected = "\n/s hello æøå 😀\n"
        pump_worker(
            send_accepted,
            lambda: editor.toPlainText() == expected and not inbox.history,
        )
        assert guard(), "Injected inbox signals were mistaken for player controls"
        assert clipboard.text() == "clipboard before ThumbTalk"
        from thumbtalk_xclip import XclipClipboard

        assert isinstance(delivery.clipboard, XclipClipboard)
        check_clipboard_replacement(app, delivery)
        print(
            "Linux native inbox copy/paste handshake, local-only review, discard, accepted send, preserved clipboard and input echo passed. Real WoW focus/closing is unverified.",
            flush=True,
        )
    finally:
        if "delivery" in locals():
            delivery.close()
        hub.close()
        connection.close()
        app.removeEventFilter(inbox)
        editor.close()


def check_clipboard_replacement(app, delivery):
    """A copy made by the user during a paste must win over the old clipboard."""
    import threading

    def run(function):
        results, errors = [], []

        def work():
            try:
                results.append(function())
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=work)
        worker.start()
        deadline = time.monotonic() + 45
        while worker.is_alive() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        worker.join(timeout=1)
        assert not worker.is_alive() and not errors, errors
        return results[0]

    lease = run(lambda: delivery.clipboard.acquire("/s clipboard test"))
    app.clipboard().setText("new copy made during paste")
    run(lambda: delivery.clipboard.restore(lease))
    assert app.clipboard().text() == "new copy made during paste"
    # A manager can fetch the lease eagerly, before the paste is armed.
    token = run(lambda: delivery.clipboard.acquire("cached before arm"))
    assert app.clipboard().text() == "cached before arm"
    run(lambda: delivery.clipboard.arm(token))
    run(lambda: delivery.clipboard.wait_for_paste(token))
    run(lambda: delivery.clipboard.restore(token))
    # Steam/Gamescope may take ownership and expose only cached plain text.
    token = run(lambda: delivery.clipboard.acquire("managed clipboard handoff"))
    app.clipboard().setText(app.clipboard().text())
    run(lambda: delivery.clipboard.arm(token))
    run(lambda: delivery.clipboard.wait_for_paste(token))
    run(lambda: delivery.clipboard.restore(token))
    assert app.clipboard().text() == "new copy made during paste"
    # A different user copy must still reject the paste without poisoning
    # the worker or overwriting that copy during cleanup.
    token = run(lambda: delivery.clipboard.acquire("obsolete draft"))
    app.clipboard().setText("new user copy")

    def reject_changed():
        try:
            delivery.clipboard.arm(token)
            # X11 ownership notifications are asynchronous; a replacement may
            # become visible at the read check instead of the arm check.
            delivery.clipboard.wait_for_paste(token)
        except RuntimeError as error:
            assert any(
                part in str(error).lower()
                for part in ("lease changed", "paste was not read")
            ), error
        else:
            raise AssertionError("A changed clipboard was accepted")

    run(reject_changed)
    run(lambda: delivery.clipboard.restore(token))
    assert not delivery.clipboard.closed
    assert app.clipboard().text() == "new user copy"
    token = run(lambda: delivery.clipboard.acquire("next explicit attempt"))
    run(lambda: delivery.clipboard.restore(token))
    assert app.clipboard().text() == "new user copy"
    print(
        "Clipboard eager reads, managed ownership handoff and rejected-lease recovery passed.",
        flush=True,
    )
    print(
        "xclip restores prior plain text and leaves a newer copy unchanged.",
        flush=True,
    )


def check_clipboard_display(app, game, root):
    """Clipboard ownership must follow the game even when Qt overlays use root."""
    import threading
    from thumbtalk_xclip import XclipClipboard

    lease = XclipClipboard()
    root_atom = root.intern_atom("CLIPBOARD")
    game_atom = game.intern_atom("CLIPBOARD")
    original_root = root.get_selection_owner(root_atom)
    errors = []
    acquired = threading.Event()
    release = threading.Event()

    def work():
        try:
            token = lease.acquire("/s separate display test")
            acquired.set()
            if not release.wait(30):
                raise RuntimeError("Clipboard routing check did not release")
            lease.restore(token)
        except Exception as error:
            errors.append(error)
        finally:
            lease.close()

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 40
        while (
            not acquired.is_set() and worker.is_alive() and time.monotonic() < deadline
        ):
            app.processEvents()
            time.sleep(0.01)
        assert acquired.is_set() and not errors, errors
        assert game.get_selection_owner(game_atom), "Clipboard was not on game display"
        assert root.get_selection_owner(root_atom) == original_root, (
            "Clipboard changed on overlay display"
        )
    finally:
        release.set()
        deadline = time.monotonic() + 35
        while worker.is_alive() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert not worker.is_alive() and not errors, errors
    print(
        "Clipboard paste uses game display, independent of overlay display.", flush=True
    )
