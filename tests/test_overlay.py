import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_chat import ChatSettings, load_chat, save_chat
from thumbtalk_overlay import STATUS_POSITIONS, status_point


class OverlayTests(unittest.TestCase):
    def test_old_settings_hide_idle_and_use_top_centre(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"version": 1, "chat": {"channel": "party"}}')
            chat = load_chat(path)
            self.assertEqual(
                (chat.overlay_mode, chat.overlay_position), ("automatic", "top-center")
            )
            save_chat(
                path, replace(chat, overlay_mode="off", overlay_position="bottom-left")
            )
            saved = load_chat(path)
            self.assertEqual(
                (saved.channel, saved.overlay_mode, saved.overlay_position),
                ("party", "off", "bottom-left"),
            )

    def test_all_positions_stay_on_screen(self):
        for width, height in ((1280, 800), (1920, 1080), (640, 480)):
            for position in STATUS_POSITIONS:
                x, y = status_point(position, width, height, 440, 90)
                self.assertTrue(0 <= x <= width - 440)
                self.assertTrue(0 <= y <= height - 90)
        self.assertEqual(status_point("top-center", 1920, 1080, 440, 90), (740, 24))
        self.assertEqual(status_point("bottom-left", 1920, 1080, 440, 90), (24, 966))

    def test_invalid_preferences_are_rejected(self):
        for field, value in (
            ("overlay_mode", "wrong"),
            ("overlay_mode", []),
            ("overlay_position", "middle"),
        ):
            with self.assertRaises(ValueError):
                replace(ChatSettings(), **{field: value}).validate()


class ReviewOverlayTests(unittest.TestCase):
    def test_hidden_status_stays_hidden_during_chat_review(self):
        from PySide6.QtWidgets import QApplication
        from thumbtalk_ui import Overlay

        app = QApplication.instance() or QApplication([])
        overlay = Overlay("off", "top-center")
        try:
            overlay.message("Review in WoW chat. Tap Talk to send", persistent=True)
            app.processEvents()
            self.assertFalse(overlay.isVisible())
            overlay.message("Send requested. Check WoW chat.")
            self.assertFalse(overlay.isVisible())
        finally:
            overlay.close()

    def test_visible_status_is_plain_text_and_fits(self):
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt
        from thumbtalk_ui import Overlay

        app = QApplication.instance() or QApplication([])
        overlay = Overlay("always", "top-center")
        try:
            overlay.message(
                "<b>Review in WoW chat</b>\nTap Talk to send · B or Menu to discard.",
                persistent=True,
            )
            app.processEvents()
            self.assertTrue(overlay.isVisible())
            self.assertEqual(overlay.label.textFormat(), Qt.PlainText)
            self.assertGreaterEqual(
                overlay.label.height(),
                overlay.label.heightForWidth(overlay.label.width()),
            )
        finally:
            overlay.close()

    def test_radial_and_top_pager_fit_small_scaled_screens(self):
        from unittest.mock import patch, Mock
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QPoint, QRect
        from thumbtalk_ui import Overlay

        app = QApplication.instance() or QApplication([])
        for height in (480, 600, 800):
            screen = Mock()
            area = QRect(0, 0, 800, height)
            screen.geometry.return_value = area
            with patch("thumbtalk_ui.QApplication.primaryScreen", return_value=screen):
                overlay = Overlay()
                try:
                    overlay.message(
                        "Menu",
                        menu={
                            "title": "Channels",
                            "items": [("say", "Say"), ("party", "Party")],
                            "selected": "say",
                            "detail": "Say",
                            "page": 0,
                            "pages": 2,
                        },
                    )
                    app.processEvents()
                    overlay.place_panel()
                    header = overlay.wheel_header.mapToGlobal(QPoint(0, 0))
                    bottom = overlay.wheel.mapToGlobal(
                        QPoint(overlay.wheel.width(), overlay.wheel.height())
                    )
                    center = overlay.wheel.mapToGlobal(
                        QPoint(overlay.wheel.width() // 2, overlay.wheel.height() // 2)
                    )
                    self.assertGreaterEqual(header.y(), 0)
                    self.assertLessEqual(bottom.y(), height)
                    self.assertLessEqual(abs(center.y() - area.center().y()), 1)
                    self.assertLessEqual(abs(center.x() - area.center().x()), 1)
                finally:
                    overlay.close()
