"""The update dialog remains usable while the network request is running."""

from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from PySide6.QtWidgets import QApplication
from thumbtalk_updates import RELEASES_URL, UpdateResult
from thumbtalk_updates_ui import UpdateDialog


class UpdateDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for_result(self, dialog):
        deadline = time.monotonic() + 2
        while dialog.checking and time.monotonic() < deadline:
            self.app.processEvents()
            dialog.poll()
            time.sleep(0.005)
        self.assertFalse(dialog.checking)

    def test_no_request_until_clicked_duplicate_checks_disabled_and_window_can_close(
        self,
    ):
        started, finish = threading.Event(), threading.Event()

        def lookup():
            started.set()
            finish.wait(2)
            return UpdateResult(
                "available",
                "New version available",
                RELEASES_URL + "/tag/v1.0.0",
                "1.0.0",
            )

        checker = Mock(side_effect=lookup)
        dialog = UpdateDialog(checker=checker)
        try:
            dialog.show()
            self.app.processEvents()
            checker.assert_not_called()
            dialog.check_button.click()
            self.assertTrue(started.wait(0.5))
            self.assertFalse(dialog.check_button.isEnabled())
            dialog.start_check()
            self.app.processEvents()
            dialog.close()
            self.assertFalse(dialog.isVisible())
            finish.set()
            self.wait_for_result(dialog)
            checker.assert_called_once()
            self.assertTrue(dialog.check_button.isEnabled())
            self.assertEqual(dialog.release_button.text(), "View update on GitHub")
            with patch(
                "thumbtalk_updates_ui.QDesktopServices.openUrl", return_value=True
            ) as browser:
                dialog.release_button.click()
                self.assertEqual(
                    browser.call_args.args[0].toString(), RELEASES_URL + "/tag/v1.0.0"
                )
        finally:
            finish.set()
            dialog.close()

    def test_failed_check_can_be_retried_and_browser_failure_shows_address(self):
        checker = Mock(
            side_effect=[
                RuntimeError("private detail"),
                UpdateResult("no_release", "No release yet"),
            ]
        )
        dialog = UpdateDialog(checker=checker)
        try:
            dialog.start_check()
            self.wait_for_result(dialog)
            self.assertNotIn("private detail", dialog.status.text())
            self.assertTrue(dialog.check_button.isEnabled())
            dialog.start_check()
            self.wait_for_result(dialog)
            self.assertEqual(dialog.status.text(), "No release yet")
            with patch(
                "thumbtalk_updates_ui.QDesktopServices.openUrl", return_value=False
            ):
                dialog.open_release()
            self.assertIn(RELEASES_URL, dialog.status.text())
        finally:
            dialog.close()
