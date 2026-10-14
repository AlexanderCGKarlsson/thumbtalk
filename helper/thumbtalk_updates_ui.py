"""Update checks, downloads and installation without blocking Qt's GUI thread."""

from queue import Empty, Queue
import shutil
import threading

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from thumbtalk_updates import RELEASES_URL, UpdateResult, check_for_updates
from thumbtalk_update_install import (
    UpdateCancelled,
    download_update,
    installation_directory,
    install_update,
    restart_installed,
)
from thumbtalk_version import TEST_BUILD, VERSION


class UpdateDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        checker=check_for_updates,
        downloader=download_update,
        installer=install_update,
        prefix=None,
        prepare_install=None,
        finish_install=None,
    ):
        super().__init__(parent)
        self.checker, self.downloader, self.installer = checker, downloader, installer
        self.prefix = prefix if prefix is not None else installation_directory()
        self.prepare_install = prepare_install or (lambda: True)
        self.finish_install = finish_install or self._finish_install
        self.results = Queue()
        self.cancel = threading.Event()
        self.checking = self.downloading = self.installing = False
        self.package = self.downloaded = None
        self.release_url = RELEASES_URL
        self.setWindowTitle("ThumbTalk · Updates")
        self.setMinimumSize(440, 380)
        self.resize(470, 440)
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        installed = QLabel(f"Installed: {VERSION} · build {TEST_BUILD}")
        installed.setTextFormat(Qt.PlainText)
        layout.addWidget(installed)
        self.status = QLabel("Check GitHub Releases for a newer version.")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Update status")
        layout.addWidget(self.status)
        self.detail = QLabel(
            "Download the update here, then close WoW and choose Install update. "
            "Your saved settings are kept and the installer refreshes existing addons."
            if self.prefix
            else "Source and portable builds update from GitHub. Installed Windows and Linux apps can download and install updates here."
        )
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setAccessibleName("Update download progress")
        self.progress.hide()
        layout.addWidget(self.progress)
        self.action_button = QPushButton("Download update")
        self.action_button.setObjectName("primary")
        self.action_button.clicked.connect(self.take_action)
        self.action_button.hide()
        layout.addWidget(self.action_button)
        self.check_button = QPushButton("Check for updates")
        self.check_button.clicked.connect(self.start_check)
        layout.addWidget(self.check_button)
        self.release_button = QPushButton("Open GitHub Releases")
        self.release_button.clicked.connect(self.open_release)
        layout.addWidget(self.release_button)
        row = QHBoxLayout()
        row.addStretch()
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        row.addWidget(self.close_button)
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.poll)

    def _clear_download(self):
        if self.downloaded:
            shutil.rmtree(self.downloaded.directory, ignore_errors=True)
            self.downloaded = None

    def start_check(self):
        if self.checking or self.downloading or self.installing:
            return
        self._clear_download()
        self.package = None
        self.checking = True
        self.action_button.hide()
        self.progress.hide()
        self.check_button.setEnabled(False)
        self.release_button.setEnabled(False)
        self.status.setText("Checking GitHub Releases…")
        self.timer.start()
        checker, results = self.checker, self.results

        def work():
            try:
                result = checker()
            except Exception:
                result = UpdateResult(
                    "error",
                    "The update check could not finish. Try again or open GitHub Releases.",
                )
            results.put(("check", result))

        threading.Thread(target=work, name="thumbtalk-updates", daemon=True).start()

    def take_action(self):
        if self.installing or self.checking:
            return
        if self.downloading:
            self.cancel.set()
            self.action_button.setEnabled(False)
            self.status.setText("Cancelling download…")
        elif self.downloaded:
            self.start_install()
        else:
            self.start_download()

    def start_download(self):
        if not self.package or not self.prefix or self.downloading or self.installing:
            return
        self.cancel = threading.Event()
        self.downloading = True
        self.check_button.setEnabled(False)
        self.action_button.setText("Cancel download")
        self.action_button.setEnabled(True)
        self.close_button.setText("Cancel and close")
        self.progress.setValue(0)
        self.progress.show()
        self.status.setText("Starting download…")
        self.timer.start()
        downloader, package, results, cancel = (
            self.downloader,
            self.package,
            self.results,
            self.cancel,
        )

        def work():
            try:
                result = downloader(
                    package,
                    cancel,
                    lambda done, total: results.put(("progress", (done, total))),
                )
                if cancel.is_set():
                    shutil.rmtree(result.directory, ignore_errors=True)
                    raise UpdateCancelled()
                results.put(("download", result))
            except UpdateCancelled:
                results.put(
                    (
                        "download_error",
                        "Download cancelled. Your installed version was not changed.",
                    )
                )
            except Exception as error:
                # No transcript or credentials are involved in update requests.
                results.put(
                    ("download_error", "Could not download the update: " + str(error))
                )

        threading.Thread(target=work, name="thumbtalk-download", daemon=True).start()

    def start_install(self):
        if not self.downloaded or self.installing:
            return
        try:
            if not self.prepare_install():
                return
        except Exception as error:
            self.status.setText("Could not prepare for the update: " + str(error))
            return
        self.installing = True
        self.check_button.setEnabled(False)
        self.action_button.setEnabled(False)
        self.close_button.setEnabled(False)
        self.status.setText("Starting the update installer… Keep WoW closed.")
        self.timer.start()
        installer, download, prefix, results = (
            self.installer,
            self.downloaded,
            self.prefix,
            self.results,
        )

        def work():
            try:
                results.put(("installed", installer(download, prefix)))
            except Exception as error:
                results.put(
                    ("install_error", "The update could not finish: " + str(error))
                )

        threading.Thread(target=work, name="thumbtalk-install", daemon=True).start()

    def poll(self):
        while True:
            try:
                kind, result = self.results.get_nowait()
            except Empty:
                break
            if kind == "check":
                self.checking = False
                self.check_button.setEnabled(True)
                self.check_button.setText("Check again")
                self.release_button.setEnabled(True)
                self.status.setText(result.message)
                self.release_url = result.release_url
                self.release_button.setText(
                    "View update on GitHub"
                    if result.state == "available"
                    else "Open GitHub Releases"
                )
                self.package = result.package
                self.action_button.setText("Download update")
                self.action_button.setEnabled(True)
                self.action_button.setVisible(
                    bool(result.state == "available" and self.package and self.prefix)
                )
                if result.state == "available" and self.prefix and not self.package:
                    self.status.setText(
                        result.message
                        + " A matching downloadable package is not available yet. Check again later or open GitHub."
                    )
            elif kind == "progress":
                if not self.cancel.is_set():
                    done, total = result
                    self.progress.setValue(int(done * 100 / total))
                    self.status.setText(
                        f"Downloading update… {done / 1048576:.1f} / {total / 1048576:.1f} MB"
                    )
            elif kind in ("download", "download_error"):
                self.downloading = False
                self.check_button.setEnabled(True)
                self.close_button.setText("Close")
                self.action_button.setEnabled(True)
                if kind == "download" and not self.cancel.is_set():
                    self.downloaded = result
                    self.action_button.setText("Install update")
                    self.progress.setValue(100)
                    self.status.setText(
                        "Download verified. Close WoW before installing."
                    )
                else:
                    if kind == "download":
                        shutil.rmtree(result.directory, ignore_errors=True)
                        result = "Download cancelled. Your installed version was not changed."
                    self.progress.hide()
                    self.action_button.setText("Download update")
                    self.status.setText(result)
            elif kind in ("installed", "install_error"):
                self.installing = False
                self.close_button.setEnabled(True)
                self.check_button.setEnabled(True)
                self.action_button.setEnabled(True)
                if kind == "install_error":
                    self.status.setText(result)
                else:
                    if result == "handoff":
                        # Setup still needs its EXE and log after ThumbTalk exits.
                        self.downloaded = None
                    self.finish_install(result)
        if not (self.checking or self.downloading or self.installing):
            self.timer.stop()

    def _finish_install(self, result):
        if result == "installed":
            try:
                restart_installed(self.prefix)
            except OSError:
                self.status.setText(
                    "The update is installed. Close ThumbTalk and reopen it from your applications menu."
                )
                self.action_button.hide()
                return
            self._clear_download()
        QApplication.closeAllWindows()

    def open_release(self):
        if not QDesktopServices.openUrl(QUrl(self.release_url)):
            self.status.setText("Could not open the browser. Visit " + self.release_url)

    def reject(self):
        # Escape follows the same cancellation/installation rules as the title bar.
        self.close()

    def closeEvent(self, event):
        if self.installing:
            event.ignore()
            return
        self.cancel.set()
        self._clear_download()
        event.accept()
