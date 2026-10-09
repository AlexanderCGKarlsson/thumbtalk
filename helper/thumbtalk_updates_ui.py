"""Responsive update dialog; network work never runs on Qt's GUI thread."""

from queue import Empty, Queue
import threading

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from thumbtalk_updates import RELEASES_URL, UpdateResult, check_for_updates
from thumbtalk_version import TEST_BUILD, VERSION


class UpdateDialog(QDialog):
    def __init__(self, parent=None, *, checker=check_for_updates):
        super().__init__(parent)
        self.checker = checker
        self.results = Queue()
        self.checking = False
        self.release_url = RELEASES_URL
        self.setWindowTitle("ThumbTalk · Updates")
        self.setMinimumSize(440, 360)
        self.resize(450, 380)
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
        detail = QLabel(
            "After downloading, close WoW and ThumbTalk, run the installer, and update the addon in Setup → WoW. Your saved settings are kept."
        )
        detail.setWordWrap(True)
        layout.addWidget(detail)
        self.check_button = QPushButton("Check for updates")
        self.check_button.clicked.connect(self.start_check)
        layout.addWidget(self.check_button)
        self.release_button = QPushButton("Open GitHub Releases")
        self.release_button.clicked.connect(self.open_release)
        layout.addWidget(self.release_button)
        row = QHBoxLayout()
        row.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        row.addWidget(close)
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.poll)

    def start_check(self):
        if self.checking:
            return
        self.checking = True
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
            # The worker retains only the queue/checker, never a Qt widget.
            results.put(result)

        threading.Thread(target=work, name="thumbtalk-updates", daemon=True).start()

    def poll(self):
        try:
            result = self.results.get_nowait()
        except Empty:
            return
        self.timer.stop()
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

    def open_release(self):
        if not QDesktopServices.openUrl(QUrl(self.release_url)):
            self.status.setText("Could not open the browser. Visit " + self.release_url)
