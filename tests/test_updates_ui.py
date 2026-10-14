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


class UpdateDownloadDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def settle(self, dialog):
        deadline = time.monotonic() + 3
        while (
            dialog.checking or dialog.downloading or dialog.installing
        ) and time.monotonic() < deadline:
            self.app.processEvents()
            dialog.poll()
            time.sleep(0.005)
        self.assertFalse(dialog.checking or dialog.downloading or dialog.installing)

    def make_dialog(self, directory, **kwargs):
        from thumbtalk_updates import UpdatePackage

        p = UpdatePackage(
            "thumbtalk-windows-setup.exe",
            "https://github.com/test",
            "https://github.com/test.sha256",
            100,
        )
        result = UpdateResult(
            "available", "Update available", RELEASES_URL + "/tag/v0.8.3", "0.8.3", p
        )
        return UpdateDialog(checker=lambda: result, prefix=Path(directory), **kwargs)

    def test_download_verified_then_explicit_install_and_handoff(self):
        import tempfile
        from thumbtalk_update_install import DownloadedUpdate

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            def download(p, cancel, progress):
                progress(100, 100)
                path = root / "download" / p.name
                path.parent.mkdir()
                path.write_bytes(b"data")
                return DownloadedUpdate(p, path, "fixture")

            installer, finish, prepare = (
                Mock(return_value="handoff"),
                Mock(),
                Mock(return_value=True),
            )
            dialog = self.make_dialog(
                root,
                downloader=download,
                installer=installer,
                finish_install=finish,
                prepare_install=prepare,
            )
            try:
                dialog.start_check()
                self.settle(dialog)
                self.assertFalse(dialog.action_button.isHidden())
                dialog.action_button.click()
                self.settle(dialog)
                self.assertEqual(dialog.action_button.text(), "Install update")
                self.assertIn("verified", dialog.status.text())
                installer.assert_not_called()
                dialog.action_button.click()
                self.settle(dialog)
                prepare.assert_called_once()
                installer.assert_called_once()
                finish.assert_called_once_with("handoff")
                self.assertIsNone(dialog.downloaded)
                self.assertTrue((root / "download" / dialog.package.name).exists())
            finally:
                dialog.close()

    def test_download_cancel_and_failure_never_enable_installation(self):
        import tempfile
        from thumbtalk_update_install import UpdateCancelled

        with tempfile.TemporaryDirectory() as root:
            entered = threading.Event()

            def download(p, cancel, progress):
                entered.set()
                cancel.wait(2)
                raise UpdateCancelled()

            installer = Mock()
            dialog = self.make_dialog(root, downloader=download, installer=installer)
            try:
                dialog.start_check()
                self.settle(dialog)
                dialog.take_action()
                self.assertTrue(entered.wait(0.5))
                self.assertFalse(dialog.check_button.isEnabled())
                dialog.take_action()
                self.settle(dialog)
                self.assertIn("cancelled", dialog.status.text())
                self.assertEqual(dialog.action_button.text(), "Download update")
                self.assertIsNone(dialog.downloaded)
                installer.assert_not_called()
            finally:
                dialog.close()

    def test_install_failure_stays_visible_and_close_is_blocked_only_during_install(
        self,
    ):
        import tempfile
        from thumbtalk_update_install import DownloadedUpdate

        with tempfile.TemporaryDirectory() as root:
            started, finish = threading.Event(), threading.Event()

            def install(*args):
                started.set()
                finish.wait(2)
                raise OSError("fixture install failure")

            complete = Mock()
            dialog = self.make_dialog(root, installer=install, finish_install=complete)
            try:
                dialog.show()
                dialog.downloaded = DownloadedUpdate(
                    None, Path(root) / "stage" / "fixture", "fixture"
                )
                dialog.start_install()
                self.assertTrue(started.wait(0.5))
                dialog.close()
                self.assertTrue(dialog.isVisible())
                finish.set()
                self.settle(dialog)
                self.assertIn("could not finish", dialog.status.text())
                self.assertTrue(dialog.close_button.isEnabled())
                complete.assert_not_called()
            finally:
                finish.set()
                dialog.close()
