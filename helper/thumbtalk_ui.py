"""Guided setup and a non-activating in-game status/menu overlay."""

from __future__ import annotations

import os
import math
import platform
import signal
import threading
from queue import Queue, Empty
import time
from dataclasses import asdict, replace
from pathlib import Path

if platform.system() == "Linux":
    # Input and game overlay must use the same XWayland session as WoW.
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from PySide6.QtCore import Qt, QTimer, QLockFile, QRectF, QEvent
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPainterPath,
    QPen,
    QPalette,
    QBrush,
    QRadialGradient,
    QLinearGradient,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QScroller,
    QFileDialog,
    QFormLayout,
    QFrame,
    QProgressBar,
    QSizePolicy,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from thumbtalk_chat import (
    ACTIONS,
    CHANNELS,
    DEFAULT_BINDINGS,
    BindingMatcher,
    canonical_binding,
    save_menu_preferences,
)
from thumbtalk_input import InputHub, Learner
from thumbtalk_models import CATALOG, ModelDownloads, model_downloaded
from thumbtalk_pipewire import SYSTEM_AUDIO, available as pipewire_available
from thumbtalk_runtime import Runtime, Recorder
from thumbtalk_audio import (
    play_recording,
    audio_stats,
    input_warning,
    managed_device,
    stop_audio,
    playback_error,
)
from thumbtalk_setup import (
    discover_wow,
    install_addon,
    remember_addon,
    steam_launch_option,
    wow_directories,
    wow_label,
)
from thumbtalk_speech import (
    LANGUAGE_CODES,
    LANGUAGE_NAMES,
    ModelCache,
    PRESETS,
    normalize_language,
    read_config,
    write_config,
)

STYLE = """
QWidget { color:#242729; font-family:'Segoe UI','DejaVu Sans'; font-size:14px; background:transparent; }
QWidget#setupRoot, QMainWindow, QDialog, QMessageBox, QFileDialog { background:#f4f4f1; color:#242729; }
QAbstractItemView, QTreeView, QListView, QTableView { background:#ffffff; alternate-background-color:#f4f4f1; color:#242729; selection-background-color:#dce3d4; selection-color:#242729; }
QHeaderView::section { background:#eef0ea; color:#242729; padding:8px; border:1px solid #d9dcd6; }
QToolButton { background:#f4f4f1; color:#242729; border:1px solid #d9dcd6; border-radius:5px; padding:5px; }
QProgressBar { background:#e3e7de; border:0; border-radius:5px; min-height:10px; max-height:10px; }
QProgressBar::chunk { background:#667f60; border-radius:5px; }
QLabel#binding[state="listening"] { background:#e0ead5; border:2px solid #667f60; }
QLabel#instruction { color:#343d34; font-size:14px; }

QLabel#brand { font-size:22px; font-weight:700; letter-spacing:-0.5px; }
QLabel#heading { font-size:29px; font-weight:700; letter-spacing:-0.6px; }
QLabel#subtle { color:#62666a; }
QLabel#step, QLabel#eyebrow { color:#757a7e; font-size:11px; font-weight:600; letter-spacing:1px; }
QPushButton#progress { color:#7a7e81; padding:6px 4px; border:1px solid transparent; border-radius:8px; font-size:13px; min-height:30px; background:transparent; }
QPushButton#progress[state="active"] { background:#e2e3df; color:#242729; font-weight:600; }
QPushButton#progress[state="done"] { color:#464d49; }
QFrame#card { background:#ffffff; border:1px solid #e1e3df; border-radius:14px; }
QFrame#footer { border-top:1px solid #dedfda; }
QLabel#cardTitle { font-size:16px; font-weight:600; }
QLabel#binding { color:#373c3e; background:#f0f1ed; border:1px solid #e1e3df; border-radius:7px; padding:8px 12px; font-weight:600; }
QPushButton { background:#ffffff; border:1px solid #d9dcd6; border-radius:9px; padding:10px 16px; min-height:22px; font-weight:500; }
QPushButton:hover { background:#eceee8; border-color:#bcc2b9; }
QPushButton:pressed { background:#e0e4db; }
QPushButton:focus { border:2px solid #747f72; }
QPushButton:disabled { color:#929790; background:#eceee8; border-color:#e2e5df; }
QPushButton#primary { background:#292e2c; color:#ffffff; border-color:#292e2c; font-weight:600; }
QPushButton#primary:hover { background:#414944; border-color:#414944; }
QPushButton#primary:pressed { background:#566159; }
QPushButton#primary:disabled { background:#e1e5dd; color:#879080; border-color:#e1e5dd; }
QPushButton#quiet { background:transparent; border:1px solid transparent; color:#626b62; text-align:left; padding-left:4px; }
QPushButton#quiet:hover { color:#242729; background:#e9ece5; }
QPushButton#quiet:focus { border-color:#747f72; }
QLineEdit,QComboBox { background:#f8f9f6; border:1px solid #dfe3da; border-radius:8px; padding:10px 12px; min-height:22px; }
QLineEdit:focus,QComboBox:focus { border-color:#747f72; }
QComboBox::drop-down { border:0; width:30px; }
QComboBox QAbstractItemView { background:#ffffff; color:#242729; selection-background-color:#e3e8dd; selection-color:#242729; padding:6px; border:1px solid #d9dcd6; }
QTextEdit,QListWidget { background:#ffffff; border:1px solid #e1e3df; border-radius:12px; padding:14px; selection-background-color:#dce3d4; selection-color:#242729; }
QListWidget::item { min-height:30px; }
QCheckBox { spacing:10px; min-height:32px; }
QCheckBox::indicator { width:20px; height:20px; }
QScrollArea { border:0; }
QScrollBar:vertical { background:transparent; width:14px; margin:0; }
QScrollBar::handle:vertical { background:#c7cdc2; border-radius:4px; min-height:30px; }
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical { background:transparent; }
QToolTip { background:#292e2c; color:#ffffff; border:0; padding:6px; }
"""


def apply_theme(app):
    """Own the palette as well as CSS, including dialogs on dark Linux desktops."""
    from thumbtalk_branding import apply_branding

    apply_branding(app)
    app.setStyle("Fusion")
    palette = QPalette()
    colors = {
        "Window": "#f4f4f1",
        "WindowText": "#242729",
        "Base": "#ffffff",
        "AlternateBase": "#f4f4f1",
        "Text": "#242729",
        "Button": "#f4f4f1",
        "ButtonText": "#242729",
        "ToolTipBase": "#f4f4f1",
        "ToolTipText": "#242729",
        "Highlight": "#dce3d4",
        "HighlightedText": "#242729",
        "PlaceholderText": "#687066",
        "BrightText": "#ffffff",
        "Light": "#ffffff",
        "Midlight": "#e7eae2",
        "Mid": "#bcc2b8",
        "Dark": "#626b62",
        "Shadow": "#9ba394",
        "Link": "#3c6543",
    }
    for name, color in colors.items():
        palette.setColor(getattr(QPalette.ColorRole, name), QColor(color))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor("#7f877b"))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)


NAMES = LANGUAGE_NAMES

LABELS = {
    "record": "Talk / confirm",
    "menu": "Hold for quick menu / tap to cancel",
    "confirm": "Extra confirm button",
    "cancel": "Extra cancel button",
    "reply": "Talk to last whisper",
    "next_channel": "Next channel (in menu)",
    "previous_channel": "Previous channel (in menu)",
    "next_language": "Next language (in menu)",
    "previous_language": "Previous language (in menu)",
}
PAD_LABELS = {
    "PAD1": "A",
    "PAD2": "B",
    "PAD3": "X",
    "PAD4": "Y",
    "PADSOCIAL": "View",
    "PADFORWARD": "Menu",
    "PADDUP": "D-pad ↑",
    "PADDDOWN": "D-pad ↓",
    "PADDLEFT": "D-pad ←",
    "PADDRIGHT": "D-pad →",
    "PADLSHOULDER": "LB",
    "PADRSHOULDER": "RB",
    "PADSYSTEM": "Xbox / Guide",
    "PADLTRIGGER": "LT / L2",
    "PADRTRIGGER": "RT / R2",
    "PADPADDLE1": "Paddle 1",
    "PADPADDLE2": "Paddle 2",
    "PADPADDLE3": "Paddle 3",
    "PADPADDLE4": "Paddle 4",
}


def pretty_binding(value):
    from thumbtalk_picker import binding_label

    return binding_label(value)


def paragraph(text):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setObjectName("subtle")
    return label


class VoiceMark(QWidget):
    """Small vector mark that stays crisp at handheld display scaling."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(36, 36)
        self.setAccessibleName("ThumbTalk")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#292e2c"))
        painter.drawRoundedRect(QRectF(2, 2, 32, 28), 9, 9)
        tail = QPainterPath()
        tail.moveTo(10, 27)
        tail.lineTo(10, 34)
        tail.lineTo(19, 27)
        painter.drawPath(tail)
        painter.setPen(QPen(QColor("#ffffff"), 2.5, Qt.SolidLine, Qt.RoundCap))
        for x, height in ((12, 6), (18, 14), (24, 9)):
            painter.drawLine(x, int(16 - height / 2), x, int(16 + height / 2))


class Select(QComboBox):
    """A native combobox with a crisp, theme-independent dropdown indicator."""

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(
            QPen(QColor("#626b62"), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        )
        x, y = self.width() - 20, self.height() / 2
        path = QPainterPath()
        path.moveTo(x - 4, y - 2)
        path.lineTo(x, y + 2)
        path.lineTo(x + 4, y - 2)
        painter.drawPath(path)


def card(layout):
    frame = QFrame()
    frame.setObjectName("card")
    inner = QVBoxLayout(frame)
    inner.setContentsMargins(20, 18, 20, 18)
    inner.setSpacing(14)
    layout.addWidget(frame)
    return inner


def form_layout(parent=None):
    form = QFormLayout(parent)
    form.setHorizontalSpacing(24)
    form.setVerticalSpacing(16)
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.WrapLongRows)
    return form


class ModelCheck(QCheckBox):
    """Readable download checkboxes, independent of the system checkbox artwork."""

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        y = (self.height() - 22) / 2
        painter.setPen(QPen(QColor("#48614f" if self.isChecked() else "#929c93"), 1.3))
        painter.setBrush(QColor("#314a39" if self.isChecked() else "#ffffff"))
        painter.drawRoundedRect(QRectF(0, y, 22, 22), 5, 5)
        if self.isChecked():
            path = QPainterPath()
            path.moveTo(5, y + 11)
            path.lineTo(9, y + 15)
            path.lineTo(17, y + 7)
            painter.setPen(
                QPen(QColor("#ffffff"), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            )
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)


class RadialHeader(QWidget):
    """Page controls above the wheel, using the same shoulder buttons as WoW."""

    def __init__(self):
        super().__init__()
        self.view = {}
        self.setFixedHeight(74)

    def set_view(self, view):
        self.view = view
        pages = view.get("pages", 1)
        self.setFixedHeight(74 if pages > 1 else 42)
        self.setAccessibleName(
            view["title"]
            + (
                f". Page {view.get('page', 0) + 1} of {pages}. LB previous page. RB next page."
                if pages > 1
                else ""
            )
        )
        self.update()

    def paintEvent(self, event):
        if not self.view:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pages = self.view.get("pages", 1)
        multiple = pages > 1
        top = 30 if multiple else 0
        panel = QRectF(1, top + 1, self.width() - 2, 40)
        fill = QLinearGradient(0, top, 0, top + 40)
        fill.setColorAt(0, QColor(32, 39, 47, 240))
        fill.setColorAt(1, QColor(15, 21, 28, 240))
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(QColor("#8e8572"), 1))
        painter.drawRoundedRect(panel, 7, 7)
        font = painter.font()
        font.setPixelSize(17)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#fff0ce"))
        painter.drawText(
            QRectF(0, 0, self.width(), 25 if multiple else 40),
            Qt.AlignCenter,
            self.view["title"],
        )
        if not multiple:
            return
        center, y = self.width() / 2, 51
        dots_width = (pages - 1) * 23
        for index in range(pages):
            active = index == self.view.get("page", 0)
            painter.setPen(QPen(QColor("#efd295" if active else "#a9b0b6"), 2))
            painter.setBrush(QColor("#efd295") if active else Qt.NoBrush)
            painter.drawEllipse(
                QRectF(center - dots_width / 2 + index * 23 - 5, y - 5, 10, 10)
            )
        font.setPixelSize(13)
        painter.setFont(font)
        for label, x in (
            ("LB", center - dots_width / 2 - 61),
            ("RB", center + dots_width / 2 + 21),
        ):
            rect = QRectF(x, y - 13, 40, 26)
            painter.setPen(QPen(QColor("#a9b0b6"), 1.2))
            painter.setBrush(QColor("#38434d"))
            painter.drawRoundedRect(rect, 7, 7)
            painter.setPen(QColor("#f5f6f7"))
            painter.drawText(rect, Qt.AlignCenter, label)


class RadialMenu(QWidget):
    """Original vector artwork, inspired by ConsolePort's in-game ring layout."""

    def __init__(self):
        super().__init__()
        self.view = None
        self.setFixedSize(480, 480)

    def set_view(self, view):
        self.view = view
        self.update()

    def icon(self, painter, code, language, color):
        painter.setPen(QPen(color, 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        if language:
            font = painter.font()
            font.setPixelSize(17 if len(code) < 3 else 12)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(QRectF(-22, -15, 44, 30), Qt.AlignCenter, code.upper())
        elif code in ("language", "general"):
            painter.drawEllipse(QRectF(-12, -12, 24, 24))
            painter.drawEllipse(QRectF(-5, -12, 10, 24))
            painter.drawLine(-11, 0, 11, 0)
            painter.drawArc(QRectF(-11, -7, 22, 8), 180 * 16, 180 * 16)
        elif code == "channel":
            painter.drawRoundedRect(QRectF(-13, -11, 22, 16), 4, 4)
            painter.drawRoundedRect(QRectF(-5, -3, 20, 15), 4, 4)
        elif code in (
            "model",
            "accuracy",
            "balanced",
            "economy",
            "parakeet",
            "current",
        ):
            painter.drawRoundedRect(QRectF(-9, -9, 18, 18), 3, 3)
            for offset in (-5, 0, 5):
                painter.drawLine(offset, -13, offset, -9)
                painter.drawLine(offset, 9, offset, 13)
                painter.drawLine(-13, offset, -9, offset)
                painter.drawLine(9, offset, 13, offset)
            painter.drawLine(-5, 1, -2, -3)
            painter.drawLine(-2, -3, 2, 4)
            painter.drawLine(2, 4, 5, -1)
        elif code in ("sending", "immediate"):
            path = QPainterPath()
            path.moveTo(-13, -8)
            path.lineTo(13, 0)
            path.lineTo(-13, 9)
            path.lineTo(-6, 0)
            path.closeSubpath()
            painter.drawPath(path)
            painter.drawLine(-6, 0, 13, 0)
        elif code == "review":
            path = QPainterPath()
            path.moveTo(-14, 0)
            path.quadTo(0, -17, 14, 0)
            path.quadTo(0, 17, -14, 0)
            painter.drawPath(path)
            painter.drawEllipse(QRectF(-4, -4, 8, 8))
        elif code in ("say", "whisper", "reply"):
            if code in ("whisper", "reply"):
                painter.drawRoundedRect(QRectF(-12, -8, 24, 17), 2, 2)
                path = QPainterPath()
                path.moveTo(-11, -7)
                path.lineTo(0, 2)
                path.lineTo(11, -7)
                painter.drawPath(path)
            else:
                # A speaking profile, matching the familiar speaking-head symbol.
                path = QPainterPath()
                path.moveTo(-11, 11)
                path.lineTo(-11, 4)
                path.cubicTo(-18, -3, -12, -13, -5, -12)
                path.cubicTo(1, -12, 3, -8, 3, -5)
                path.lineTo(6, -1)
                path.lineTo(3, 0)
                path.lineTo(3, 4)
                path.quadTo(3, 6, -3, 6)
                path.lineTo(-3, 11)
                path.closeSubpath()
                painter.setBrush(QColor(color.red(), color.green(), color.blue(), 45))
                painter.drawPath(path)
                painter.setBrush(Qt.NoBrush)
                painter.drawArc(QRectF(5, -5, 8, 10), -60 * 16, 120 * 16)
                painter.drawArc(QRectF(5, -9, 16, 18), -55 * 16, 110 * 16)
        elif code in ("party", "lfg"):
            for x, y in ((-7, -3), (7, -3), (0, -7)):
                painter.drawEllipse(QRectF(x - 3, y - 4, 6, 6))
            path = QPainterPath()
            path.moveTo(-14, 11)
            path.cubicTo(-14, -2, -2, -2, -2, 11)
            path.moveTo(2, 11)
            path.cubicTo(2, -2, 14, -2, 14, 11)
            painter.drawPath(path)
        elif code == "trade":
            painter.drawRoundedRect(QRectF(-12, -6, 24, 19), 3, 3)
            painter.drawArc(QRectF(-6, -14, 12, 16), 0, 180 * 16)
            painter.drawLine(-5, 3, 5, 3)
            painter.drawLine(0, -2, 0, 8)
        elif code == "guild":
            path = QPainterPath()
            path.moveTo(0, -13)
            path.lineTo(11, -8)
            path.lineTo(9, 5)
            path.quadTo(5, 11, 0, 14)
            path.quadTo(-5, 11, -9, 5)
            path.lineTo(-11, -8)
            path.closeSubpath()
            painter.drawPath(path)
            painter.drawLine(0, -7, 0, 8)
            painter.drawLine(-5, -2, 5, -2)
        elif code == "raid":
            for sign in (-1, 1):
                painter.drawLine(-10 * sign, 11, 10 * sign, -11)
                painter.drawLine(-10 * sign, 4, -3 * sign, 11)
                painter.drawLine(10 * sign, -11, 9 * sign, -4)
        elif code == "instance":
            path = QPainterPath()
            path.moveTo(-12, 12)
            path.lineTo(-12, -3)
            path.cubicTo(-12, -17, 12, -17, 12, -3)
            path.lineTo(12, 12)
            path.moveTo(-7, 12)
            path.lineTo(-7, -3)
            path.cubicTo(-7, -10, 7, -10, 7, -3)
            path.lineTo(7, 12)
            painter.drawPath(path)
        else:
            path = QPainterPath()
            path.moveTo(-12, -2)
            path.lineTo(-3, -11)
            path.moveTo(-12, -2)
            path.lineTo(-3, 6)
            path.moveTo(-11, -2)
            path.cubicTo(6, -7, 12, 1, 12, 11)
            painter.drawPath(path)

    def paintEvent(self, event):
        if not self.view:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.scale(self.width() / 480, self.height() / 480)
        center = 240
        outer = QRectF(22, 22, 436, 436)
        inner = QRectF(157, 157, 166, 166)
        halo = QRadialGradient(center, center, 239)
        halo.setColorAt(0, QColor(8, 12, 16, 0))
        halo.setColorAt(0.83, QColor(8, 12, 16, 115))
        halo.setColorAt(0.92, QColor(8, 12, 16, 160))
        halo.setColorAt(1, QColor(8, 12, 16, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(halo))
        painter.drawEllipse(QRectF(1, 1, 478, 478))
        items = self.view["items"]
        step = 360 / max(1, len(items))
        language = self.view["title"] == "Language"
        dense = len(items) > 8
        accents = {
            "language": "#e7c783",
            "channel": "#8cd7ed",
            "model": "#baadea",
            "sending": "#9bd4ad",
            "review": "#e7c783",
            "immediate": "#9bd4ad",
            "say": "#8cd7ed",
            "party": "#aac2ff",
            "raid": "#f4aa83",
            "guild": "#9cdbad",
            "instance": "#c5a9ef",
            "whisper": "#e9aed3",
            "reply": "#e9aed3",
            "general": "#8cd7ed",
            "trade": "#e7c783",
            "lfg": "#aac2ff",
        }
        for index, (code, label) in enumerate(items):
            middle = 90 - index * step
            start, span = middle - step / 2 + 0.7, step - 1.4
            selected = code == self.view["selected"]
            accent = QColor("#e7c783" if language else accents.get(code, "#bac8d4"))
            path = QPainterPath()
            path.arcMoveTo(outer, start)
            path.arcTo(outer, start, span)
            path.arcTo(inner, start + span, -span)
            path.closeSubpath()
            surface = QLinearGradient(0, 22, 0, 458)
            surface.setColorAt(
                0, QColor(57, 66, 76, 245) if selected else QColor(32, 39, 47, 238)
            )
            surface.setColorAt(
                1, QColor(29, 39, 49, 245) if selected else QColor(15, 21, 28, 238)
            )
            painter.setBrush(QBrush(surface))
            painter.setPen(QPen(QColor("#080d12"), 5))
            painter.drawPath(path)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#dfc68b" if selected else "#737b82"), 1.7))
            painter.drawPath(path)
            # A narrow illuminated rim makes the selected direction unambiguous.
            if selected:
                painter.setPen(QPen(QColor(231, 199, 131, 35), 9))
                painter.drawArc(
                    outer.adjusted(5, 5, -5, -5),
                    int((start + 1) * 16),
                    int((span - 2) * 16),
                )
                painter.setPen(QPen(QColor("#ebd5a4"), 2.5))
                painter.drawArc(
                    outer.adjusted(5, 5, -5, -5),
                    int((start + 1) * 16),
                    int((span - 2) * 16),
                )
            radians = math.radians(middle)
            dx, dy = math.cos(radians), -math.sin(radians)
            radius = 160 if dense else 148
            x, y = center + radius * dx, center + radius * dy
            painter.save()
            painter.translate(x, y - (8 if dense else 13))
            glow = QRadialGradient(0, 0, 31 if dense else 43)
            glow.setColorAt(
                0,
                QColor(
                    accent.red(), accent.green(), accent.blue(), 50 if selected else 26
                ),
            )
            glow.setColorAt(1, QColor(accent.red(), accent.green(), accent.blue(), 0))
            painter.setBrush(QBrush(glow))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QRectF(-43, -43, 86, 86))
            painter.scale(1.22 if dense else 1.65, 1.22 if dense else 1.65)
            self.icon(painter, code, language, accent)
            painter.restore()
            width = (
                190
                if len(items) <= 2
                else (80 if dense else (86 if len(items) >= 8 else 116))
            )
            label_rect = QRectF(x - width / 2, y + (13 if dense else 19), width, 35)
            font = painter.font()
            font.setPixelSize(12 if dense else 15)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#ffe6ad" if selected else "#eef1f4"))
            # Long language/recipient names stay within their segment; the hub
            # shows the current choice at a larger size.
            label = "LFG" if code == "lfg" and not language else label
            label = painter.fontMetrics().elidedText(label, Qt.ElideRight, int(width))
            painter.drawText(label_rect, Qt.AlignHCenter | Qt.AlignTop, label)
            if selected:
                painter.save()
                painter.translate(center, center)
                painter.rotate(-middle)
                pointer = QPainterPath()
                pointer.moveTo(99, 0)
                pointer.lineTo(88, -5)
                pointer.lineTo(88, 5)
                pointer.closeSubpath()
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor("#ebd5a4"))
                painter.drawPath(pointer)
                painter.restore()
        # Concentric silver rims and a quiet, translucent center frame the choices.
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#9ba0a2"), 1.2))
        painter.drawEllipse(outer.adjusted(-4, -4, 4, 4))
        painter.setPen(QPen(QColor("#a8aaa4"), 2))
        painter.drawEllipse(inner.adjusted(3, 3, -3, -3))
        painter.setPen(QPen(QColor(169, 178, 184, 70), 1))
        painter.setBrush(QColor(14, 20, 27, 175))
        painter.drawEllipse(inner.adjusted(8, 8, -8, -8))
        font = painter.font()
        font.setPixelSize(11)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor("#c0c8d0"))
        painter.drawText(
            QRectF(172, 199, 136, 25),
            Qt.AlignCenter,
            (
                f"CHANNELS  {self.view['page'] + 1} / {self.view['pages']}"
                if self.view.get("pages", 1) > 1
                else self.view["title"].upper()
            ),
        )
        font.setPixelSize(17)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#fff0ce"))
        detail = self.view["detail"]
        # The hub fits two lines, including the automatic-language explanation.
        words, lines, line = detail.split(), [], ""
        for word in words:
            candidate = (line + " " + word).strip()
            if line and painter.fontMetrics().horizontalAdvance(candidate) > 126:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)
        if len(lines) > 2:
            lines = [lines[0], " ".join(lines[1:])]
        detail = "\n".join(
            painter.fontMetrics().elidedText(line, Qt.ElideRight, 126) for line in lines
        )
        painter.drawText(QRectF(177, 228, 126, 47), Qt.AlignCenter, detail)


class Overlay(QWidget):
    def __init__(self, mode="automatic", position="top-center"):
        super().__init__(
            None,
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
            | Qt.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setWindowTitle("ThumbTalk status")
        self.setStyleSheet("Overlay { background:transparent; border:none; }")
        self.panel = QWidget(self)
        self.panel.setObjectName("overlayPanel")
        self.status_mode, self.status_position = mode, position
        self.screen_canvas = False
        self.menu_visible = False
        if platform.system() == "Linux" and QApplication.platformName() == "xcb":
            try:
                from Xlib.display import Display
                from Xlib import Xatom

                display = Display(
                    QApplication.instance().property("thumbtalkOverlayDisplay") or None
                )
                try:
                    atom = display.intern_atom(
                        "GAMESCOPE_XWAYLAND_SERVER_ID", only_if_exists=True
                    )
                    self.screen_canvas = bool(
                        atom
                        and display.screen().root.get_full_property(
                            atom, Xatom.CARDINAL
                        )
                        is not None
                    )
                finally:
                    display.close()
            except Exception:
                pass
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(18, 14, 18, 14)
        self.label = QLabel("ThumbTalk")
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.PlainText)
        layout.addWidget(self.label)
        self.wheel_header = RadialHeader()
        self.wheel_header.hide()
        layout.addWidget(self.wheel_header, 0, Qt.AlignCenter)
        self.wheel = RadialMenu()
        self.wheel.hide()
        layout.addWidget(self.wheel, 0, Qt.AlignCenter)
        self.panel.setFixedWidth(516)
        QApplication.primaryScreen().geometryChanged.connect(self.place_panel)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.dismiss)

    def configure(self, mode, position):
        self.status_mode, self.status_position = mode, position
        if self.menu_visible:
            return
        self.dismiss()

    def dismiss(self):
        self.timer.stop()
        self.panel.hide()
        # Also clear compositor opacity so a retained Gamescope frame cannot
        # leave the last status card painted after the Qt window is hidden.
        self.setWindowOpacity(0)
        self.hide()

    def place_panel(self, *_):
        from thumbtalk_overlay import status_point

        area = QApplication.primaryScreen().geometry()
        x, y = status_point(
            self.status_position,
            area.width(),
            area.height(),
            self.panel.width(),
            self.panel.height(),
        )
        if self.screen_canvas:
            # Gamescope composites external overlays at (0, 0), ignoring move().
            # Position the contents within a transparent output-sized surface.
            self.setGeometry(area)
            if self.menu_visible:
                self.panel.move(
                    self.width() // 2 - self.wheel.x() - self.wheel.width() // 2,
                    self.height() // 2 - self.wheel.y() - self.wheel.height() // 2,
                )
            else:
                self.panel.move(x, y)
        else:
            self.setFixedSize(self.panel.size())
            self.panel.move(0, 0)
            if self.menu_visible:
                self.move(
                    area.center().x() - self.wheel.x() - self.wheel.width() // 2,
                    area.center().y() - self.wheel.y() - self.wheel.height() // 2,
                )
            else:
                self.move(area.left() + x, area.top() + y)

    def message(self, text, persistent=False, menu=None, preview=False):
        from thumbtalk_overlay import idle_status

        if menu is None and not preview:
            if self.status_mode == "off" or (
                self.status_mode == "automatic" and not persistent and idle_status(text)
            ):
                self.menu_visible = False
                self.dismiss()
                return
            persistent = persistent or self.status_mode == "always"
        self.setWindowOpacity(1)
        self.panel.setStyleSheet(
            "QWidget#overlayPanel { background:transparent; border:none; }"
            if menu is not None
            else "QWidget#overlayPanel { background:#f4f4f1; border:1px solid #d9dcd6; border-radius:12px; }"
        )
        self.panel.setFixedWidth(516 if menu is not None else 440)
        self.menu_visible = menu is not None
        self.label.setVisible(menu is None)
        self.wheel_header.setVisible(menu is not None)
        self.wheel.setVisible(menu is not None)
        if menu is not None:
            area = QApplication.primaryScreen().geometry()
            side = max(160, min(480, area.width() - 48, area.height() - 210))
            self.panel.setFixedWidth(side + 36)
            self.wheel.setFixedSize(side, side)
            self.wheel_header.setFixedWidth(side)
            self.wheel_header.set_view(menu)
            self.wheel.set_view(menu)
        self.label.setText("ThumbTalk" if menu is not None else "ThumbTalk\n" + text)
        self.panel.layout().invalidate()
        self.panel.adjustSize()
        # Wrapped text must be measured at the actual panel width. Qt's
        # unconstrained size hint can clip the first/last lines of a review.
        if menu is None:
            height = self.panel.layout().totalHeightForWidth(self.panel.width())
            self.panel.resize(
                self.panel.width(), max(height, self.panel.minimumSizeHint().height())
            )
        self.panel.show()
        self.panel.layout().activate()
        self.place_panel()
        self.show()
        # First show can change child layout geometry; center the wheel using
        # its visible position rather than the hidden widget's initial offset.
        self.panel.layout().activate()
        self.place_panel()
        if platform.system() == "Linux" and QApplication.platformName() == "xcb":
            try:
                from Xlib.display import Display
                from Xlib import Xatom

                display = Display(
                    QApplication.instance().property("thumbtalkOverlayDisplay") or None
                )
                window = display.create_resource_object("window", int(self.winId()))
                window.change_property(
                    display.intern_atom("GAMESCOPE_EXTERNAL_OVERLAY"),
                    Xatom.CARDINAL,
                    32,
                    [1],
                )
                display.sync()
                display.close()
            except Exception as error:
                if not getattr(self, "reported_display_error", False):
                    print("Overlay registration failed: " + str(error), flush=True)
                    self.reported_display_error = True
        self.timer.stop()
        if not persistent:
            self.timer.start(4500)


class Setup(QMainWindow):
    def __init__(self, path, speech, chat, practice=False):
        super().__init__()
        self.path, self.speech, self.chat = path, speech, chat
        self.bindings = dict(chat.bindings)
        self.runtime, self.hub, self.learning = None, None, None
        self.mic_check = None
        self.model_cache = ModelCache()
        self.downloads = None
        self.available_models = set()
        self.comparison = None
        self.playback_until = 0
        self.recording_details_key = None
        self.wow_results = Queue()
        self.wow_searching = False
        self.wow_searched = False
        self.button_monitor = BindingMatcher(self.bindings)
        self.overlay = Overlay(chat.overlay_mode, chat.overlay_position)
        self.runtime_lock = None
        self.setWindowTitle("ThumbTalk · Setup")
        area = QApplication.primaryScreen().availableGeometry()
        self.setMinimumSize(520, 440)
        self.resize(
            min(800, max(520, area.width() - 32)),
            min(720, max(440, area.height() - 48)),
        )
        main = QWidget()
        main.setObjectName("setupRoot")
        self.setCentralWidget(main)
        layout = QVBoxLayout(main)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(12)
        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(VoiceMark())
        title = QLabel("ThumbTalk")
        title.setObjectName("brand")
        header.addWidget(title)
        header.addStretch()
        self.update_button = QPushButton("Check for updates")
        self.update_button.setObjectName("quiet")
        self.update_button.clicked.connect(self.show_updates)
        header.addWidget(self.update_button)
        self.step_label = QLabel()
        self.step_label.setObjectName("step")
        header.addWidget(self.step_label)
        layout.addLayout(header)
        progress = QHBoxLayout()
        progress.setSpacing(6)
        self.progress_labels = []
        for index, name in enumerate(("Voice", "Try it", "Buttons", "Chat", "WoW")):
            label = QPushButton(name)
            label.setObjectName("progress")
            label.setAccessibleName("Go to " + name)
            label.clicked.connect(lambda checked=False, step=index: self.go_step(step))
            progress.addWidget(label, 1)
            self.progress_labels.append(label)
        layout.addLayout(progress)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.voice_page()
        self.practice_page()
        self.buttons_page()
        self.chat_page()
        self.play_page()
        footer = QFrame()
        footer.setObjectName("footer")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(0, 14, 0, 0)
        footer_layout.setSpacing(10)
        self.status = paragraph("Your choices are saved when you continue.")
        self.status.setMinimumHeight(34)
        footer_layout.addWidget(self.status)
        row = QHBoxLayout()
        self.back_button = QPushButton("← Back")
        self.back_button.setObjectName("quiet")
        self.back_button.clicked.connect(lambda: self.move_step(-1))
        row.addWidget(self.back_button)
        row.addStretch()
        self.next_button = QPushButton()
        self.next_button.setObjectName("primary")
        self.next_button.clicked.connect(lambda: self.move_step(1))
        row.addWidget(self.next_button)
        footer_layout.addLayout(row)
        layout.addWidget(footer)
        self.update_navigation()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(15)
        QApplication.instance().installEventFilter(self)
        if practice:
            self.pages.setCurrentIndex(1)
            self.update_navigation()

    def show_updates(self):
        from thumbtalk_updates_ui import UpdateDialog

        if not getattr(self, "updates_dialog", None):
            self.updates_dialog = UpdateDialog(self)
        self.updates_dialog.show()
        self.updates_dialog.raise_()
        self.updates_dialog.activateWindow()
        self.updates_dialog.start_check()

    def update_navigation(self):
        index = self.pages.currentIndex()
        self.step_label.setText(f"SETUP  /  {index + 1} OF 5")
        for position, (label, name) in enumerate(
            zip(self.progress_labels, ("Voice", "Try it", "Buttons", "Chat", "WoW"))
        ):
            marker = str(position + 1)
            label.setText(f"{marker}  {name}")
            label.setProperty(
                "state",
                "active"
                if position == index
                else "done"
                if position < index
                else "next",
            )
            label.style().unpolish(label)
            label.style().polish(label)
        self.back_button.setEnabled(index > 0)
        self.next_button.setText(
            (
                "Test your voice →",
                "Choose buttons →",
                "Choose chat →",
                "Connect WoW →",
                "Save && finish",
            )[index]
        )

    def move_step(self, direction):
        self.go_step(self.pages.currentIndex() + direction)

    def go_step(self, index):
        if index == self.pages.currentIndex():
            return
        if self.comparison:
            self.error("Finish or cancel the model comparison before changing steps.")
            return
        if index == self.pages.count() or self.microphone.currentData():
            if not self.save():
                return
        self.stop_mic_check()
        self.stop_runtime()
        self.cancel_learning()
        if self.hub:
            self.hub.close()
            self.hub = None
        if index == self.pages.count():
            self.close()
            return
        self.pages.setCurrentIndex(max(0, index))
        self.update_navigation()
        if self.pages.currentIndex() == 2:
            self.ensure_hub()
            self.button_monitor = BindingMatcher(self.bindings)
        if self.pages.currentIndex() == 4 and not self.wow_searched:
            self.search_wow()
        self.status.setText(
            (
                "Choose your microphone and the language you speak.",
                "Start the voice test, then tap Record. Say a sentence and tap Stop recording.",
                "Press a rear button now. Detected buttons will appear here.",
                "Choose where your first message should go.",
                "Save your settings, then follow the WoW connection steps above.",
            )[self.pages.currentIndex()]
        )

    def page(self, name):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 8, 12)
        layout.setSpacing(12)
        heading = QLabel(name)
        heading.setObjectName("heading")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        QScroller.grabGesture(
            scroll.viewport(), QScroller.ScrollerGestureType.TouchGesture
        )
        self.pages.addWidget(scroll)
        return layout

    def extra_options(self, layout, label):
        button = QPushButton(label)
        button.setCheckable(True)
        button.setObjectName("quiet")
        panel = QWidget()
        panel.hide()
        button.toggled.connect(panel.setVisible)
        button.toggled.connect(
            lambda expanded: button.setText(
                label.removesuffix(" ▾") + (" ▴" if expanded else " ▾")
            )
        )
        layout.addWidget(button)
        layout.addWidget(panel)
        return QVBoxLayout(panel)

    def voice_page(self):
        layout = self.page("Let’s hear you")
        layout.addWidget(
            paragraph(
                "Choose a microphone and the language you speak. Everything is transcribed on your device."
            )
        )
        fields = card(layout)
        form = form_layout()
        self.microphone = Select()
        self.microphone.addItem("Choose your microphone…", None)
        self.managed_microphone = SYSTEM_AUDIO if pipewire_available() else None
        if self.managed_microphone:
            self.microphone.addItem("System microphone · recommended", SYSTEM_AUDIO)
        try:
            import sounddevice as sd

            devices = sd.query_devices()
            if platform.system() == "Linux" and not self.managed_microphone:
                self.managed_microphone = managed_device(devices)
            for device in sorted(
                devices, key=lambda d: d["name"] != self.managed_microphone
            ):
                if (
                    device["max_input_channels"]
                    and self.microphone.findData(device["name"]) < 0
                ):
                    name = device["name"]
                    label = (
                        ("System microphone · " + name)
                        if name == self.managed_microphone
                        else name
                    )
                    self.microphone.addItem(label, name)
        except Exception as error:
            layout.addWidget(paragraph("Could not list microphones: " + str(error)))
        selected_name = self.chat.input_device
        self.switched_audio_route = bool(
            self.managed_microphone and selected_name and "(hw:" in selected_name
        )
        if self.managed_microphone and (not selected_name or self.switched_audio_route):
            selected_name = self.managed_microphone
        selected = self.microphone.findData(selected_name) if selected_name else -1
        if selected > 0:
            self.microphone.setCurrentIndex(selected)
        elif self.microphone.count() == 2 and not self.chat.input_device:
            self.microphone.setCurrentIndex(1)
        form.addRow("Microphone", self.microphone)
        self.language = Select()
        for code in ("auto",) + tuple(
            sorted(LANGUAGE_CODES, key=lambda c: NAMES.get(c, c))
        ):
            self.language.addItem(NAMES.get(code, code), code)
        self.language.setCurrentIndex(
            self.language.findData(self.speech.language or "auto")
        )
        form.addRow("Starting language", self.language)
        self.preset = Select()
        for name, label in (
            ("accuracy", "Recommended · Whisper Small"),
            ("balanced", "Lighter · Whisper Base"),
            ("economy", "Lightest · Whisper Tiny"),
            ("parakeet", "Experimental · Parakeet V3"),
        ):
            self.preset.addItem(label, name)
        self.preset.setCurrentIndex(self.preset.findData(self.speech.preset))
        fields.addLayout(form)
        self.mic_check_button = QPushButton("Check this microphone")
        self.mic_check_button.clicked.connect(self.check_microphone)
        fields.addWidget(self.mic_check_button)
        self.mic_level = QProgressBar()
        self.mic_level.setRange(0, 100)
        self.mic_level.setTextVisible(False)
        fields.addWidget(self.mic_level)
        self.mic_feedback = paragraph(
            "Using the system microphone route instead of direct hardware. Check it while speaking."
            if self.switched_audio_route
            else "Choose your input above, then check it while speaking."
        )
        fields.addWidget(self.mic_feedback)
        self.favorites_summary = paragraph("")
        fields.addWidget(self.favorites_summary)
        extras = self.extra_options(layout, "Edit favorite languages ▾")
        extras.addWidget(
            paragraph(
                "Choose up to 12 favorites. Only checked languages appear in your in-game wheel."
            )
        )
        self.languages = QListWidget()
        favorite_codes = list(
            dict.fromkeys(
                ("en",)
                + tuple(self.chat.languages)
                + ((self.speech.language,) if self.speech.language else ())
            )
        )
        all_codes = ("auto",) + tuple(
            sorted(LANGUAGE_CODES, key=lambda c: NAMES.get(c, c))
        )
        for code in favorite_codes + [
            code for code in all_codes if code not in favorite_codes
        ]:
            item = QListWidgetItem(NAMES.get(code, code))
            item.setData(Qt.UserRole, code)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                Qt.Checked if code in self.chat.languages else Qt.Unchecked
            )
            self.languages.addItem(item)
        self.languages.setMaximumHeight(180)
        extras.addWidget(self.languages)
        self.languages.itemChanged.connect(self.update_favorites_summary)
        self.language.currentIndexChanged.connect(self.favorite_starting_language)
        self.update_favorites_summary()
        advanced = self.extra_options(layout, "Advanced speech · download models ▾")
        advanced.addWidget(
            paragraph(
                "Tick every model you want to download. Only the model you use is loaded into memory."
            )
        )
        self.model_checks = {}
        saved_models = read_config(self.path).get(
            "model_downloads", [self.speech.preset]
        )
        if not isinstance(saved_models, list):
            saved_models = [self.speech.preset]
        for preset, (name, description) in CATALOG.items():
            check = ModelCheck(name + " · " + description)
            check.setChecked(preset in saved_models)
            check.setMinimumHeight(56)
            self.model_checks[preset] = check
            advanced.addWidget(check)
        self.download_button = QPushButton("Download selected models")
        self.download_button.clicked.connect(self.download_models)
        layout.addWidget(self.download_button)
        self.download_status = paragraph("")
        layout.addWidget(self.download_status)
        active_row = QHBoxLayout()
        active_row.addWidget(QLabel("Use for dictation"))
        active_row.addWidget(self.preset, 1)
        layout.addLayout(active_row)
        self.model_note = paragraph("")
        layout.addWidget(self.model_note)
        self.refresh_models()
        self.preset.currentIndexChanged.connect(self.update_model_options)
        self.update_model_options()
        layout.addStretch()

    def update_favorites_summary(self):
        names = [
            self.languages.item(index).text()
            for index in range(self.languages.count())
            if self.languages.item(index).checkState() == Qt.Checked
        ]
        self.favorites_summary.setText(
            "Favorite languages: "
            + (" · ".join(names) if names else "Choose at least one below.")
        )

    def favorite_starting_language(self):
        code = self.language.currentData()
        for index in range(self.languages.count()):
            item = self.languages.item(index)
            if item.data(Qt.UserRole) == code:
                item.setCheckState(Qt.Checked)
                break

    def refresh_models(self):
        self.available_models = {
            preset for preset in CATALOG if model_downloaded(preset)
        }
        for preset, check in self.model_checks.items():
            name, description = CATALOG[preset]
            state = (
                "Downloaded" if preset in self.available_models else "Not downloaded"
            )
            check.setText(name + " · " + state + "\n" + description)
        for index in range(self.preset.count()):
            preset = self.preset.itemData(index)
            self.preset.model().item(index).setEnabled(preset in self.available_models)
        if not self.available_models:
            self.download_status.setText(
                "Download Whisper Small to get started. Tick optional models under Advanced speech."
            )
        else:
            self.download_status.setText(
                "Downloaded: "
                + ", ".join(
                    CATALOG[p][0] for p in CATALOG if p in self.available_models
                )
            )

    def download_models(self):
        if self.downloads or self.comparison:
            return
        selected = [p for p, check in self.model_checks.items() if check.isChecked()]
        try:
            self.downloads = ModelDownloads(selected)
            self.stop_mic_check()
            self.stop_runtime()
            data = read_config(self.path)
            data.update(version=1, model_downloads=selected)
            write_config(self.path, data)
            self.download_errors = []
            self.download_button.setEnabled(False)
            for check in self.model_checks.values():
                check.setEnabled(False)
            self.downloads.start()
        except Exception as error:
            self.downloads = None
            self.error(error)

    def poll_downloads(self):
        if not self.downloads:
            return
        while True:
            try:
                kind, value = self.downloads.events.get_nowait()
            except Empty:
                break
            if kind == "progress":
                self.download_status.setText(value)
            elif kind == "error":
                self.download_errors.append(value)
            elif kind == "done":
                self.downloads = None
                self.download_button.setEnabled(True)
                for check in self.model_checks.values():
                    check.setEnabled(True)
                self.refresh_models()
                if self.download_errors:
                    self.download_status.setText("\n".join(self.download_errors))
                break

    def update_model_options(self):
        automatic = self.preset.currentData() == "parakeet"
        self.language.setEnabled(not automatic)
        self.languages.setEnabled(not automatic)
        self.model_note.setText(
            "Parakeet always detects language automatically; the starting language is not applied. Recognition may be unreliable for short phrases or when switching languages. "
            "Whisper Small lets you explicitly choose your spoken language."
            if automatic
            else self.preset.currentText()
            + " · local CPU · two inference threads. Choose your spoken language for a more consistent test."
        )

    def practice_page(self):
        layout = self.page("Try your voice")
        layout.setSpacing(10)
        layout.addWidget(
            paragraph("Tap Record, speak, then tap Stop recording. No holding needed.")
        )
        self.practice_button = QPushButton("Start voice test")
        self.practice_button.clicked.connect(self.start_practice)
        controls = QHBoxLayout()
        controls.addWidget(self.practice_button)
        controls.addStretch()
        self.stop_button = QPushButton("Stop test")
        self.stop_button.setObjectName("quiet")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_runtime)
        controls.addWidget(self.stop_button)
        layout.addLayout(controls)
        self.test_talk = QPushButton("Record")
        self.test_talk.setObjectName("primary")
        self.test_talk.setMinimumHeight(60)
        self.test_talk.setEnabled(False)
        self.test_talk.setText("Start the voice test above")
        self.test_talk.setContextMenuPolicy(Qt.PreventContextMenu)
        self.test_talk.clicked.connect(self.toggle_test_recording)
        layout.addWidget(self.test_talk)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("Tap Stop recording to see what you said…")
        self.preview.setFixedHeight(120)
        layout.addWidget(self.preview, 1)
        self.audio_dialog = QDialog(self)
        self.audio_dialog.setWindowTitle("Audio options")
        self.audio_dialog.setModal(True)
        self.audio_dialog.resize(620, 520)
        dialog_layout = QVBoxLayout(self.audio_dialog)
        details_scroll = QScrollArea()
        details_scroll.setWidgetResizable(True)
        details_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        details_page = QWidget()
        audio_layout = QVBoxLayout(details_page)
        details_scroll.setWidget(details_page)
        QScroller.grabGesture(
            details_scroll.viewport(), QScroller.ScrollerGestureType.TouchGesture
        )
        dialog_layout.addWidget(details_scroll)
        done = QPushButton("Done")
        done.setObjectName("primary")
        done.clicked.connect(self.audio_dialog.accept)
        dialog_layout.addWidget(done)
        self.audio_dialog.finished.connect(self.stop_playback)
        self.live_input = paragraph("")
        audio_layout.addWidget(self.live_input)
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Listen through"))
        self.audio_output = Select()
        self.audio_output.addItem("System default output", None)
        if pipewire_available():
            self.audio_output.addItem("System speakers · recommended", SYSTEM_AUDIO)
            self.audio_output.setCurrentIndex(1)
        try:
            import sounddevice as sd

            for device in sd.query_devices():
                if (
                    device.get("max_output_channels", 0)
                    and self.audio_output.findData(device["name"]) < 0
                ):
                    self.audio_output.addItem(device["name"], device["name"])
        except Exception:
            pass
        self.audio_output.currentIndexChanged.connect(self.stop_playback)
        output_row.addWidget(self.audio_output, 1)
        audio_layout.addLayout(output_row)
        self.play_recording = QPushButton("Listen to recording")
        self.play_recording.setEnabled(False)
        self.play_recording.clicked.connect(self.listen_to_recording)
        playback_row = QHBoxLayout()
        playback_row.addWidget(self.play_recording)
        self.compare_button = QPushButton("Compare models")
        self.compare_button.setEnabled(False)
        self.compare_button.clicked.connect(self.start_comparison)
        audio_layout.addWidget(self.compare_button)
        self.compare_status = paragraph("")
        audio_layout.addWidget(self.compare_status)
        self.audio_options = QPushButton("Audio options…")
        self.audio_options.clicked.connect(self.audio_dialog.show)
        playback_row.addWidget(self.audio_options)
        layout.addLayout(playback_row)
        self.audio_warning = QPushButton("Microphone level too high · How to fix")
        self.audio_warning.hide()
        self.audio_warning.clicked.connect(
            lambda: self.confirmation(
                "Check your microphone level",
                "The recording may be distorted. Open your system’s Sound settings and lower the microphone input level, then try again. Start around 30% and adjust until your voice sounds clear.",
            )
        )
        layout.addWidget(self.audio_warning)
        layout.addStretch()
        self.recording_details = paragraph(
            "Hear exactly what the microphone captured if any words look wrong."
        )
        audio_layout.addWidget(self.recording_details)
        self.compare_panel = QWidget()
        self.compare_layout = QVBoxLayout(self.compare_panel)
        self.compare_layout.setContentsMargins(0, 0, 0, 0)
        audio_layout.addWidget(self.compare_panel)
        audio_layout.addStretch()

    def start_comparison(self):
        if self.comparison:
            self.comparison.cancel()
            self.compare_button.setEnabled(False)
            self.compare_status.setText("Stopping comparison…")
            return
        if (
            not self.runtime
            or self.runtime.busy
            or self.runtime.last_audio is None
            or self.runtime.session.state == "recording"
        ):
            return
        from thumbtalk_compare import Comparison
        import gc

        try:
            from thumbtalk_compare import CANDIDATES

            installed = [p for p in CANDIDATES if model_downloaded(p)]
            if len(installed) < 2:
                self.error(
                    "Download at least two models in Advanced speech to compare them."
                )
                return
            comparison = Comparison(
                self.runtime.last_audio, self.speech.language, presets=installed
            )
            self.stop_runtime()
            self.model_cache.clear()
            gc.collect()
            while self.compare_layout.count():
                self.compare_layout.takeAt(0).widget().deleteLater()
            self.compare_layout.addWidget(
                paragraph(
                    "Same recording · downloaded models only · one model at a time. "
                    "Minimize ThumbTalk and play the same WoW scene to check for stutter; return here for results."
                )
            )
            self.comparison = comparison
            self.practice_button.setEnabled(False)
            self.back_button.setEnabled(False)
            self.next_button.setEnabled(False)
            self.compare_button.setText("Cancel comparison")
            self.compare_button.setEnabled(True)
            self.compare_status.setText("Preparing comparison…")
            comparison.start()
        except Exception as error:
            self.error(error)

    def poll_comparison(self):
        if not self.comparison:
            return
        while True:
            try:
                event = self.comparison.events.get_nowait()
            except Empty:
                break
            kind = event.get("kind")
            if kind == "progress":
                self.compare_status.setText(event["message"])
            elif kind == "error":
                self.compare_layout.addWidget(paragraph(event["message"]))
            elif kind == "result":
                from thumbtalk_compare import CANDIDATES

                result = QWidget()
                row = QVBoxLayout(result)
                row.setContentsMargins(0, 8, 0, 8)
                label = CANDIDATES[event["preset"]]
                row.addWidget(
                    paragraph(
                        f"{label} · {event['warm_seconds']:.1f}s warm · {event['peak_mib']:.0f} MiB peak worker memory\n"
                        f"First transcription {event['first_seconds']:.1f}s · CPU {event['cpu_percent']:.0f}% (100% = one core)\n"
                        f"Preparation {event['load_seconds']:.1f}s, including any download"
                    )
                )
                transcript = QTextEdit()
                transcript.setReadOnly(True)
                transcript.setPlainText(event["text"] or "No words detected.")
                transcript.setFixedHeight(90)
                row.addWidget(transcript)
                if not event["consistent"]:
                    row.addWidget(
                        paragraph(
                            "The repeated passes produced different words. Try this model again before choosing it."
                        )
                    )
                if not event["background_priority"]:
                    row.addWidget(
                        paragraph(
                            "The operating system did not allow background priority."
                        )
                    )
                choose = QPushButton("Use " + label)
                choose.clicked.connect(
                    lambda checked=False,
                    preset=event["preset"]: self.choose_compared_model(preset)
                )
                row.addWidget(choose)
                self.compare_layout.addWidget(result)
            elif kind == "done":
                self.comparison = None
                self.compare_button.setText("Compare models")
                self.compare_button.setEnabled(False)
                self.practice_button.setEnabled(True)
                self.update_navigation()
                self.next_button.setEnabled(True)
                self.compare_status.setText(
                    "Comparison stopped. Record again to retry."
                    if event["cancelled"]
                    else "Compare the words you actually said, then choose a model. Memory above is the speech worker only; check WoW for stutter too."
                )
                break

    def choose_compared_model(self, preset):
        if self.comparison:
            self.compare_status.setText(
                "Wait for the comparison to finish, or cancel it first."
            )
            return
        self.preset.setCurrentIndex(self.preset.findData(preset))
        if self.save():
            self.compare_status.setText(
                "Model saved. Start a voice test to try another sentence."
            )

    def stop_playback(self):
        if self.playback_until:
            stop_audio()
        self.playback_until = 0
        self.play_recording.setText("Listen to recording")

    def listen_to_recording(self):
        if self.playback_until:
            self.stop_playback()
            return
        if (
            not self.runtime
            or self.runtime.last_audio is None
            or self.runtime.session.state == "recording"
        ):
            return
        try:
            import numpy as np

            audio = np.asarray(self.runtime.last_audio, dtype="float32")
            if not audio.size:
                return
            duration = play_recording(audio, self.audio_output.currentData())
            self.playback_until = time.monotonic() + duration
            self.play_recording.setText("Stop playback")
        except Exception as error:
            self.stop_playback()
            self.error("Could not play this recording. " + str(error))

    def update_recording_details(self):
        runtime = self.runtime
        if not runtime or not runtime.practice:
            return
        if not runtime.ready:
            seconds = int(time.monotonic() - runtime.loading_started)
            self.recording_details.setText(f"{runtime.load_status}  {seconds}s elapsed")
            return
        audio = runtime.last_audio
        self.compare_button.setEnabled(
            audio is not None
            and len(audio) >= 4000
            and not runtime.busy
            and runtime.session.state != "recording"
        )
        self.play_recording.setEnabled(
            audio is not None
            and len(audio) > 0
            and runtime.session.state != "recording"
        )
        if runtime.session.state == "recording":
            self.stop_playback()
            self.recording_details_key = None
            self.recording_details.setText(
                "Listening now. Finish the last word before releasing."
            )
        elif audio is not None and len(audio):
            import numpy as np

            key = (id(audio), runtime.transcription_seconds)
            if key == self.recording_details_key:
                return
            self.recording_details_key = key
            samples = np.asarray(audio, dtype="float32")
            details = f"{samples.size / 16000:.1f}s recorded · {runtime.speech.model}"
            language = (
                getattr(runtime.transcriber, "last_language", None)
                or runtime.record_language
            )
            if language:
                details += " · " + NAMES.get(language, language)
            if runtime.transcription_seconds is not None:
                details += f" · {runtime.transcription_seconds:.1f}s to transcribe"
            rate = getattr(runtime.recorder, "rate", 16000)
            channel = getattr(runtime.recorder, "selected_channel", 1)
            details += f"\nInput: {runtime.chat.input_device or 'system default'} · {rate} Hz · channel {channel}"
            stats = getattr(runtime.recorder, "stats", None) or audio_stats(samples)
            warning = input_warning(stats)
            if warning:
                details += "\n" + warning
            if getattr(runtime.recorder, "overflows", 0):
                details += "\nAudio gaps detected. Try the system microphone route in Voice and record again."
            self.recording_details.setText(details)
            self.audio_warning.setVisible(bool(warning))
        else:
            self.recording_details.setText(
                "Ready. Tap Record to start, then tap Stop recording when finished."
            )

    def toggle_test_recording(self):
        if not self.runtime or not self.runtime.practice or not self.runtime.ready:
            return
        state = self.runtime.session.state
        if state in ("idle", "preview", "recording"):
            self.screen_talk(state != "recording")
            self.tick()

    def screen_talk(self, down):
        if not self.runtime or not self.runtime.practice:
            return
        try:
            if down:
                self.stop_playback()
            if down and self.runtime.session.state == "preview":
                self.runtime.cancel()
            self.runtime.action("record", down)
        except Exception as error:
            self.error(error)

    def buttons_page(self):
        layout = self.page("Choose your two buttons")
        layout.addWidget(
            paragraph(
                "Choose a keyboard key, mouse click or gamepad button with Change. If a rear button is not detected, use the setup below."
            )
        )
        guide = card(layout)
        heading = QLabel("Rear buttons · one-time setup")
        heading.setObjectName("cardTitle")
        guide_header = QHBoxLayout()
        guide_header.addWidget(heading, 1)
        self.controller_help = QPushButton("Controller connection…")
        self.controller_help.clicked.connect(self.show_controller_connection)
        guide_header.addWidget(self.controller_help)
        guide.addLayout(guide_header)
        if platform.system() == "Linux":
            instructions = (
                "1. Open Steam → Settings → Advanced settings → Non-game controller layouts → Edit.\n"
                "2. Select L5 and assign Keyboard → F8. Set R5 to Keyboard → F7.\n"
                "3. Apply the layout. Return here and press each rear button."
            )
            mapping_help = (
                "F8 and F7 are the keyboard signals Steam sends when you press the rear buttons. "
                "You do not need a keyboard. For playing, use the same mappings in WoW’s controller layout."
            )
        elif platform.system() == "Windows":
            instructions = (
                "1. Open your handheld’s controller app, such as Armoury Crate SE or Legion Space.\n"
                "2. Assign one rear button to keyboard F8 and the other to F7. Apply the profile.\n"
                "3. Return here. Use Change → Listen for a button, then press and release each rear button."
            )
            mapping_help = (
                "Steam is not required. In Armoury Crate, clear Set as Secondary Function for the rear buttons. Use direct key assignments, not sequences. "
                "Keep the same keys in the profiles used for setup and WoW."
            )
        else:
            instructions = (
                "1. Open your controller’s mapping app if its rear buttons are not detected.\n"
                "2. Assign one rear button to keyboard F8 and the other to F7.\n"
                "3. Apply the mapping. Return here and press each rear button."
            )
            mapping_help = (
                "F8 and F7 are suggested keyboard assignments. You do not need a physical keyboard. "
                "Keep the same assignments while playing WoW."
            )
        guide.addWidget(paragraph(instructions))
        guide.addWidget(paragraph(mapping_help))
        self.button_labels = {}
        self.binding_buttons = []
        self.change_buttons = {}
        main_controls = QVBoxLayout()
        main_controls.setSpacing(12)
        layout.addLayout(main_controls)
        extra = self.extra_options(layout, "Extra shortcuts && recording mode ▾")
        content = QWidget()
        extra_form = form_layout(content)
        extra.addWidget(content)
        for action in ("record", "menu") + tuple(
            a for a in ACTIONS if a not in ("record", "menu")
        ):
            row = QHBoxLayout()
            label = QLabel(pretty_binding(self.bindings.get(action, "")))
            label.setWordWrap(True)
            self.button_labels[action] = label
            row.addWidget(label, 1)
            button = QPushButton("Change")
            button.clicked.connect(
                lambda checked=False, action=action: self.choose_binding(action)
            )
            self.binding_buttons.append(button)
            self.change_buttons[action] = button
            row.addWidget(button)
            if action in ("record", "menu"):
                label.setObjectName("binding")
                label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
                description = QVBoxLayout()
                name = QLabel("Talk" if action == "record" else "Menu")
                name.setObjectName("cardTitle")
                description.addWidget(name)
                description.addWidget(
                    paragraph(
                        "Hold to speak · tap to send"
                        if action == "record"
                        else "Hold to choose · tap to cancel"
                    )
                )
                row.insertLayout(0, description, 3)
                card(main_controls).addLayout(row)
            else:
                extra_form.addRow(
                    LABELS.get(
                        action, "Talk to " + action.removeprefix("talk_").title()
                    ),
                    row,
                )
        self.mode = Select()
        self.mode.addItem("Hold to talk (recommended)", "hold")
        self.mode.addItem("Press once to start, again to stop", "toggle")
        self.mode.setCurrentIndex(self.mode.findData(self.chat.mode))
        extra.addWidget(self.mode)
        reset = QPushButton("Restore suggested buttons")
        reset.clicked.connect(self.reset_bindings)
        extra.addWidget(reset)
        self.stop_learning = QPushButton("Cancel button learning")
        self.stop_learning.clicked.connect(self.cancel_learning)
        self.stop_learning.hide()
        layout.addWidget(self.stop_learning)
        self.input_status = paragraph("")
        self.input_status.hide()
        layout.addWidget(self.input_status)
        layout.addStretch()

    def chat_page(self):
        layout = self.page("Where do you want to chat?")
        fields = card(layout)
        form = form_layout()
        self.channel = Select()
        for channel in CHANNELS:
            self.channel.addItem(
                "Whisper · latest conversation"
                if channel == "reply"
                else "Whisper · specific player"
                if channel == "whisper"
                else "Looking for Group"
                if channel == "lfg"
                else channel.title(),
                channel,
            )
        self.channel.setCurrentIndex(self.channel.findData(self.chat.channel))
        form.addRow("Starting channel", self.channel)
        self.recipient = Select()
        self.recipient.setEditable(True)
        self.recipient.addItems(list(self.chat.favorites))
        self.recipient.setEditText(self.chat.recipient)
        form.addRow("Whisper to", self.recipient)
        self.chat_form = form
        fields.addLayout(form)
        self.whisper_hint = paragraph(
            "Enter Character-Realm. You can keep frequent recipients as favorites."
        )
        fields.addWidget(self.whisper_hint)
        self.favorite_button = QPushButton("Save as a favorite")
        self.favorite_button.setObjectName("quiet")
        self.favorite_button.clicked.connect(self.add_favorite)
        fields.addWidget(self.favorite_button)
        self.channel.currentIndexChanged.connect(self.update_chat_fields)
        self.update_chat_fields()
        sending = card(layout)
        review = QLabel("Sending")
        review.setObjectName("cardTitle")
        sending.addWidget(review)
        sending.addWidget(
            paragraph(
                "Review first and tap Talk to send, or send immediately. You can also change this from the in-game Sending menu."
            )
        )
        self.review_warning = paragraph(
            "Linux review uses an experimental private inbox. Update the addon and restart WoW. "
            "Close any typing box before recording. Review does not open WoW chat; "
            "tap Talk to accept or Menu to discard. This candidate still needs in-game testing."
        )
        self.review_warning.setVisible(platform.system() == "Linux")
        sending.addWidget(self.review_warning)
        self.auto_send = QCheckBox("Send immediately after transcription")
        self.auto_send.setChecked(self.chat.auto_send)
        sending.addWidget(self.auto_send)
        self.review_b_cancel = QCheckBox("Use B / Circle to discard a review")
        self.review_b_cancel.setChecked(self.chat.review_b_cancel)
        sending.addWidget(self.review_b_cancel)
        sending.addWidget(
            paragraph(
                "Menu always discards a review. B / Circle is optional and can also trigger its action in WoW."
            )
        )
        sending.addWidget(
            paragraph(
                "ThumbTalk uses your current WoW chat style. "
                "The typing bar may remain open after sending."
            )
        )
        self.display_button = QPushButton("On-screen display…")
        self.display_button.clicked.connect(self.display_settings)
        layout.addWidget(self.display_button)
        layout.addStretch()

    def display_settings(self):
        from thumbtalk_overlay import STATUS_MODES, STATUS_POSITIONS
        from thumbtalk_chat import load_chat, save_chat

        dialog = QDialog(self)
        dialog.setWindowTitle("On-screen display")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addWidget(
            paragraph(
                "Choose when and where speech status appears. The radial stays centred."
            )
        )
        fields = form_layout()
        mode, position = Select(), Select()
        mode.setObjectName("statusMode")
        position.setObjectName("statusPosition")
        for value, label in STATUS_MODES.items():
            mode.addItem(label, value)
        for value, label in STATUS_POSITIONS.items():
            position.addItem(label, value)
        mode.setCurrentIndex(mode.findData(self.chat.overlay_mode))
        position.setCurrentIndex(position.findData(self.chat.overlay_position))
        fields.addRow("Speech status", mode)
        fields.addRow("Position", position)
        layout.addLayout(fields)
        layout.addWidget(
            paragraph(
                "Only when needed shows recording and transcription status, then hides when ready. Hidden turns off the status box. Review stays in WoW chat; the radial still opens."
            )
        )
        preview = QPushButton("Preview position")
        layout.addWidget(preview)

        def refresh():
            visible = mode.currentData() != "off"
            position.setEnabled(visible)
            preview.setEnabled(visible)

        mode.currentIndexChanged.connect(refresh)
        refresh()

        def show_preview():
            self.overlay.configure(mode.currentData(), position.currentData())
            self.overlay.message("Recording · Example", preview=True)

        preview.clicked.connect(show_preview)
        buttons = QHBoxLayout()
        cancel, save = QPushButton("Cancel"), QPushButton("Save")
        save.setObjectName("saveDisplay")
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        cancel.clicked.connect(dialog.reject)

        def apply():
            try:
                changes = {
                    "overlay_mode": mode.currentData(),
                    "overlay_position": position.currentData(),
                }
                save_chat(self.path, replace(load_chat(self.path), **changes))
                self.chat = replace(self.chat, **changes)
                dialog.accept()
            except Exception as error:
                self.error(error)

        save.clicked.connect(apply)
        layout.addWidget(
            paragraph("Restart ThumbTalk to apply these settings in game.")
        )
        dialog.resize(480, 400)
        dialog.exec()
        self.overlay.menu_visible = False
        self.overlay.configure(self.chat.overlay_mode, self.chat.overlay_position)

    def update_chat_fields(self):
        whisper = self.channel.currentData() == "whisper"
        self.chat_form.setRowVisible(self.recipient, whisper)
        self.whisper_hint.setVisible(whisper)
        self.favorite_button.setVisible(whisper)

    def play_page(self):
        layout = self.page("Connect World of Warcraft")
        addon = card(layout)
        title = QLabel("01  ·  Add ThumbTalk to WoW")
        title.setObjectName("cardTitle")
        addon.addWidget(title)
        self.wow_feedback = paragraph(
            "Find your WoW installations automatically — Retail, Classic, or beta."
        )
        addon.addWidget(self.wow_feedback)
        self.wow_locations = Select()
        self.wow_locations.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.wow_locations.currentIndexChanged.connect(self.select_wow)
        self.wow_locations.hide()
        addon.addWidget(self.wow_locations)
        self.wow_path = paragraph("")
        self.wow_path.setTextFormat(Qt.PlainText)
        self.wow_path.hide()
        addon.addWidget(self.wow_path)
        row = QHBoxLayout()
        self.install_addon_button = QPushButton("Install addon")
        self.install_addon_button.setObjectName("primary")
        self.install_addon_button.setEnabled(False)
        self.install_addon_button.clicked.connect(self.install_game_addon)
        row.addWidget(self.install_addon_button)
        self.search_wow_button = QPushButton("Search again")
        self.search_wow_button.clicked.connect(self.search_wow)
        row.addWidget(self.search_wow_button)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse_wow)
        row.addWidget(browse)
        addon.addLayout(row)
        launch = card(layout)
        title = QLabel("02  ·  Start with your game")
        title.setObjectName("cardTitle")
        launch.addWidget(title)
        if platform.system() == "Linux":
            self.copy_launch_button = QPushButton(
                "Copy launch option for Steam (Linux)"
            )
            self.copy_launch_button.clicked.connect(self.copy_launch)
            launch.addWidget(self.copy_launch_button)
            launch = self.extra_options(launch, "Using another launcher? ▾")
        launch.addWidget(
            paragraph(
                "Start ThumbTalk, then open WoW. Leave ThumbTalk running while you play."
            )
        )
        start = QPushButton("Start ThumbTalk for WoW")
        start.clicked.connect(self.start_game)
        launch.addWidget(start)
        if platform.system() == "Windows":
            from thumbtalk_startup import startup_enabled

            self.windows_startup = QCheckBox("Start with Windows")
            self.windows_startup.setObjectName("windowsStartup")
            self.windows_startup.setToolTip(
                "Start the companion minimized when you sign in. Hold Talk to record."
            )
            try:
                self.windows_startup.setChecked(startup_enabled())
            except OSError as error:
                self.windows_startup.setEnabled(False)
                self.windows_startup.setToolTip(
                    "Could not read Windows startup settings: " + str(error)
                )
            self.windows_startup.clicked.connect(self.change_windows_startup)
            launch.addWidget(self.windows_startup)
        layout.addStretch()

    def change_windows_startup(self, enabled):
        from thumbtalk_startup import set_startup

        try:
            if enabled:
                if not model_downloaded(self.preset.currentData()):
                    raise RuntimeError(
                        "Download your selected speech model in Voice before enabling Start with Windows."
                    )
                if not self.save():
                    self.windows_startup.setChecked(False)
                    return
            set_startup(enabled, self.path)
            self.confirmation(
                "Start with Windows enabled"
                if enabled
                else "Start with Windows disabled",
                "ThumbTalk will start minimized when you sign in. Launch WoW normally through Battle.net. Hold Talk when you want to record."
                if enabled
                else "ThumbTalk will no longer start when you sign in. You can still start it from the Start menu.",
            )
        except Exception as error:
            self.windows_startup.setChecked(not enabled)
            self.error(error)

    def values(self):
        if not self.microphone.currentData():
            raise ValueError(
                "Choose your microphone in the Voice step, then use ‘Check this microphone’."
            )
        languages = tuple(
            self.languages.item(i).data(Qt.UserRole)
            for i in range(self.languages.count())
            if self.languages.item(i).checkState() == Qt.Checked
        )
        preset = self.preset.currentData()
        speech = replace(
            self.speech,
            preset=preset,
            **PRESETS[preset],
            language=None
            if preset == "parakeet"
            else normalize_language(self.language.currentData()),
            device="cpu",
            compute_type="int8",
        )
        chat = replace(
            self.chat,
            input_device=self.microphone.currentData(),
            channel=self.channel.currentData(),
            recipient=self.recipient.currentText().strip(),
            mode=self.mode.currentData(),
            languages=languages,
            bindings=dict(self.bindings),
            auto_send=self.auto_send.isChecked(),
            review_b_cancel=self.review_b_cancel.isChecked(),
            classic_chat=False,
            favorites=tuple(
                self.recipient.itemText(i)
                for i in range(self.recipient.count())
                if self.recipient.itemText(i)
            ),
        )
        speech.validate()
        chat.validate()
        return speech, chat

    def error(self, error):
        QMessageBox.warning(self, "Let's fix one thing", str(error))

    def save(self):
        try:
            speech, chat = self.values()
            data = read_config(self.path)
            data.update(
                version=1,
                speech=asdict(speech),
                chat=asdict(chat),
                model_downloads=[
                    p for p, check in self.model_checks.items() if check.isChecked()
                ],
            )
            write_config(self.path, data)
            self.speech, self.chat = speech, chat
            self.status.setText(
                "Saved. New settings apply the next time ThumbTalk starts."
            )
            return True
        except Exception as error:
            self.error(error)
            return False

    def ensure_hub(self, keyboard=True):
        if self.hub is None:
            self.hub = InputHub(keyboard=keyboard)
        return self.hub

    def show_controller_connection(self):
        try:
            hub = self.ensure_hub()
            hub.poll()
            self.confirmation(
                "Controller connection",
                hub.controller_status()
                + "\n\nChange lets you pick keyboard keys, mouse clicks and gamepad buttons. Press and release the button to assign it."
                + (
                    "\n\nSteam may turn a button into a keyboard or mouse action in Desktop Mode. To learn LB as LB, assign it to Left Bumper in the active Steam layout. Rear buttons can use keyboard F8 and F7."
                    if platform.system() == "Linux"
                    else "\n\nRear buttons may need a keyboard assignment in your controller’s own app. Assign F8 and F7 in the profile currently active, then return here to learn them. Steam is not required."
                ),
            )
        except Exception as error:
            self.error(error)

    def choose_binding(self, action):
        if self.runtime:
            self.error("Stop practice before changing a button.")
            return
        from thumbtalk_picker import BindingPicker

        self.cancel_learning()
        dialog = BindingPicker(action, self.bindings, self)
        if dialog.exec() == QDialog.Accepted:
            if dialog.listen_requested:
                self.learn(action)
            elif dialog.value:
                self.assign_binding(action, dialog.value)

    def assign_binding(self, action, value):
        from thumbtalk_chat import validate_bindings

        value = canonical_binding(value)
        updated = dict(self.bindings, **{action: value})
        validate_bindings(updated)
        self.bindings = updated
        self.button_labels[action].setText(pretty_binding(value))
        self.button_monitor = BindingMatcher(self.bindings)
        self.status.setText(
            pretty_binding(value) + " selected. Saved when you continue."
        )

    def learn(self, action):
        if self.runtime:
            self.error("Stop practice before changing a button.")
            return
        try:
            self.ensure_hub()
            self.hub.poll()
            self.cancel_learning()
            self.learning = (action, Learner())
            self.change_buttons[action].setText("Listening…")
            self.button_labels[action].setText("Press a button")
            self.button_labels[action].setProperty("state", "listening")
            self.button_labels[action].style().unpolish(self.button_labels[action])
            self.button_labels[action].style().polish(self.button_labels[action])
            self.status.setText(
                "Listening: hold your button(s) together, then release. For rear buttons, apply the mapping above first."
            )
            self.stop_learning.show()
            self.input_status.show()
            self.input_status.setText(self.hub.controller_status())
            if not self.hub.devices:
                self.button_labels[action].setText("Press a key or mouse button")
                self.status.setText(
                    "No controller detected. Open Controller connection for help, or press a keyboard key or mouse button."
                )
        except Exception as error:
            self.error(error)

    def cancel_learning(self):
        if self.learning:
            action, _ = self.learning
            self.change_buttons[action].setText("Change")
            label = self.button_labels[action]
            label.setText(pretty_binding(self.bindings.get(action, "")))
            label.setProperty("state", "")
            label.style().unpolish(label)
            label.style().polish(label)
        self.learning = None
        self.stop_learning.hide()
        self.input_status.setText(
            "Button learning cancelled. Your previous button is unchanged."
        )

    def reset_bindings(self):
        if self.runtime:
            return
        self.bindings = dict(DEFAULT_BINDINGS)
        self.button_monitor = BindingMatcher(self.bindings)
        for action, label in self.button_labels.items():
            label.setText(pretty_binding(self.bindings.get(action, "")))

    def add_favorite(self):
        from thumbtalk_chat import validate_recipient

        try:
            target = validate_recipient(self.recipient.currentText())
            if self.recipient.findText(target) < 0:
                self.recipient.addItem(target)
            self.status.setText("Favorite added. Choose Next to save it.")
        except ValueError as error:
            self.error(error)

    def search_wow(self):
        if self.wow_searching:
            return
        self.wow_searching = True
        self.wow_searched = True
        self.search_wow_button.setEnabled(False)
        self.wow_feedback.setText("Looking in common game folders and Steam libraries…")
        # The worker only handles paths. Qt widgets are updated on the GUI thread.
        saved, results = self.chat.wow_dir, self.wow_results

        def search():
            try:
                results.put((discover_wow(saved), None))
            except Exception as error:
                results.put(([], str(error)))

        threading.Thread(target=search, daemon=True).start()

    def poll_wow_search(self):
        try:
            paths, error = self.wow_results.get_nowait()
        except Empty:
            return
        self.wow_searching = False
        self.search_wow_button.setEnabled(True)
        # Keep an installation selected manually while the scan was running.
        for path in paths:
            self.add_wow_location(path)
        count = self.wow_locations.count()
        self.wow_feedback.setText(
            "World of Warcraft found. Install the addon below."
            if count == 1
            else "Choose the installation you play, then install the addon."
            if count
            else "Search could not finish. Try again or use Browse."
            if error
            else "World of Warcraft wasn’t found in common locations. Use Browse to choose your World of Warcraft folder."
        )

    def add_wow_location(self, path):
        value = str(path)
        index = self.wow_locations.findData(value)
        if index < 0:
            number = self.wow_locations.count() + 1
            self.wow_locations.addItem(
                f"{wow_label(Path(value))} · Installation {number}", value
            )
            index = self.wow_locations.count() - 1
        self.wow_locations.setVisible(True)
        self.select_wow()
        return index

    def select_wow(self):
        path = self.wow_locations.currentData()
        self.wow_path.setText(path or "")
        self.wow_path.hide()
        self.wow_locations.setToolTip(path or "")
        self.install_addon_button.setEnabled(bool(path))
        self.install_addon_button.setText("Install addon")
        if path and not self.wow_searching:
            self.wow_feedback.setText(
                f"Selected {wow_label(Path(path))}. Install the addon below."
            )

    def browse_wow(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose your World of Warcraft folder",
            self.wow_locations.currentData()
            or self.chat.wow_dir
            or str(
                Path.home() / "Games"
                if (Path.home() / "Games").is_dir()
                else Path.home()
            ),
            options=QFileDialog.ShowDirsOnly | QFileDialog.DontUseNativeDialog,
        )
        if folder:
            paths = wow_directories(Path(folder))
            if not paths:
                self.error(
                    "No WoW client found here. Choose World of Warcraft or a client folder such as _retail_ or _classic_."
                )
                return
            indexes = [self.add_wow_location(path) for path in paths]
            self.wow_locations.setCurrentIndex(indexes[0])
            self.wow_feedback.setText(
                "WoW selected. Install the addon below."
                if len(paths) == 1
                else "Several versions found. Choose the one you play, then install the addon."
            )

    def install_game_addon(self):
        folder = self.wow_locations.currentData()
        if not folder:
            return
        try:
            target = install_addon(Path(folder), self.path.parent / "backups")
            remember_addon(self.path, target.parents[2])
            self.chat = replace(self.chat, wow_dir=str(target.parents[2]))
            self.install_addon_button.setText("Installed ✓")
            self.wow_feedback.setText(
                "ThumbTalk is installed in " + wow_label(Path(folder)) + "."
            )
            saved = self.save()
            self.confirmation(
                "Addon installed",
                "ThumbTalk has been installed for " + wow_label(Path(folder)) + ".\n\n"
                "In WoW, open AddOns and enable ThumbTalk. Restart WoW to load this addon version.\n\n"
                "You can select another WoW installation here and install it there too."
                + (
                    ""
                    if saved
                    else "\n\nThe addon was installed, but your setup settings could not be saved."
                ),
            )
        except Exception as error:
            self.error(error)

    def copy_launch(self):
        try:
            option = steam_launch_option()
        except RuntimeError as error:
            self.error(error)
            return
        if self.save():
            QApplication.clipboard().setText(option)
            self.copy_launch_button.setText("Copied!")
            self.confirmation(
                "Copied!",
                "In Steam, open WoW’s Properties → Launch Options and paste. Keep %command% at the end.\n\nThis launch option is for Steam on Linux, including SteamOS, Bazzite, and CachyOS.",
            )

    def confirmation(self, title, message):
        QMessageBox.information(self, title, message)

    def notify(self, text):
        if (
            self.runtime
            and self.runtime.practice
            and self.runtime.session.state == "preview"
        ):
            text = "Your words are above. Listen to the recording, record again, or choose Next."
        self.status.setText(text)
        if self.runtime and not self.runtime.practice:
            if self.runtime.menu_open and self.runtime.session.state == "idle":
                text = self.runtime.menu_text()
            self.overlay.message(
                text,
                not self.runtime.ready
                or self.runtime.menu_open
                or self.runtime.session.state in ("recording", "transcribing"),
                menu=self.runtime.menu_view()
                if self.runtime.menu_open and self.runtime.session.state == "idle"
                else None,
            )

    def check_microphone(self):
        if not self.microphone.currentData():
            self.mic_feedback.setText("Select your microphone above first.")
            return
        self.stop_mic_check()
        try:
            self.mic_check = Recorder(self.microphone.currentData(), 5)
            self.mic_level.setValue(0)
            self.mic_check.start()
            self.mic_check_started = time.monotonic()
            self.microphone.setEnabled(False)
            self.mic_check_button.setEnabled(False)
            self.mic_feedback.setText(
                "Speak now for five seconds. The bar should move with your voice."
            )
        except Exception as error:
            self.stop_mic_check()
            self.mic_feedback.setText(
                "Could not open this microphone. Choose another input. " + str(error)
            )

    def stop_mic_check(self, report=False):
        recorder, self.mic_check = self.mic_check, None
        try:
            if recorder:
                peak = recorder.peak
                recorder.stop(discard=not report)
                if report:
                    self.mic_feedback.setText(
                        input_warning(recorder.stats)
                        if getattr(recorder, "stats", None)
                        and input_warning(recorder.stats)
                        else "Audio gaps detected. Try the recommended system microphone."
                        if getattr(recorder, "overflows", 0)
                        else "Microphone is receiving sound. Continue to the voice test."
                        if peak > 0.002
                        else "No sound detected. Check mute/input volume, or choose another microphone."
                    )
        except Exception as error:
            self.mic_feedback.setText("Microphone check stopped: " + str(error))
        finally:
            self.microphone.setEnabled(True)
            self.mic_check_button.setEnabled(True)

    def eventFilter(self, watched, event):
        # On Wayland desktops, also accept keys delivered to our focused Qt window.
        # Gameplay still uses the global input hub; this fallback is setup-only.
        if (
            self.hub
            and not self.runtime
            and (self.learning or self.pages.currentIndex() == 2)
            and event.type() in (QEvent.KeyPress, QEvent.KeyRelease)
            and isinstance(watched, QWidget)
            and watched.window() is self
            and not event.isAutoRepeat()
        ):
            key = event.key()
            names = {
                Qt.Key_Left: "left",
                Qt.Key_Right: "right",
                Qt.Key_Up: "up",
                Qt.Key_Down: "down",
                Qt.Key_Control: "ctrl",
                Qt.Key_Shift: "shift",
                Qt.Key_Alt: "alt",
                Qt.Key_Meta: "cmd",
                Qt.Key_Space: "space",
                Qt.Key_Escape: "esc",
                Qt.Key_Return: "enter",
            }
            name = names.get(key)
            if Qt.Key_F1 <= key <= Qt.Key_F24:
                name = "f" + str(key - Qt.Key_F1 + 1)
            elif Qt.Key_A <= key <= Qt.Key_Z or Qt.Key_0 <= key <= Qt.Key_9:
                name = chr(key).lower()
            if name:
                self.hub.events.put(("key", 0, name, event.type() == QEvent.KeyPress))
        return super().eventFilter(watched, event)

    def start_runtime(self, practice, use_saved=False):
        if self.downloads:
            self.error("Wait for the selected models to finish downloading.")
            return
        if not model_downloaded(
            self.speech.preset if use_saved else self.preset.currentData()
        ):
            self.pages.setCurrentIndex(0)
            self.update_navigation()
            self.error(
                "Download your selected model in the Voice step, then start the test."
            )
            return
        if self.comparison:
            return
        self.stop_mic_check()
        if self.runtime:
            return
        if not use_saved and not self.save():
            return
        try:
            if self.runtime_lock is None:
                self.runtime_lock = acquire_lock(self.path)
            self.runtime = Runtime(
                self.speech,
                replace(self.chat, mode="hold", auto_send=False)
                if practice
                else self.chat,
                self.notify,
                self.preview.setPlainText,
                practice=practice,
                hub=self.ensure_hub(keyboard=not practice),
                transcriber_factory=self.model_cache,
                save_preferences=(lambda *_: None)
                if practice
                else self.save_game_preferences,
            )
            self.practice_button.setEnabled(False)
            self.test_talk.setText("Preparing speech… please wait")
            self.stop_button.setEnabled(True)
            self.learning = None
        except Exception as error:
            if self.runtime_lock:
                self.runtime_lock.unlock()
                self.runtime_lock = None
            self.error(error)

    def save_game_preferences(self, speech_changes, chat_changes):
        self.speech, self.chat = save_menu_preferences(
            self.path, speech_changes, chat_changes
        )
        if speech_changes:
            self.preset.setCurrentIndex(self.preset.findData(self.speech.preset))
            self.language.setCurrentIndex(
                self.language.findData(self.speech.language or "auto")
            )
        if "channel" in chat_changes:
            self.channel.setCurrentIndex(self.channel.findData(self.chat.channel))
        if "auto_send" in chat_changes:
            self.auto_send.setChecked(self.chat.auto_send)

    def start_practice(self):
        self.start_runtime(True)

    def start_at_login(self):
        # A second sign-in invocation must not open another microphone/model.
        try:
            self.runtime_lock = acquire_lock(self.path)
        except RuntimeError:
            self.close()
            return False
        self.showMinimized()
        # Use saved device IDs even if devices have not finished appearing at sign-in.
        # Do not overwrite preferences with the setup widgets' fallback selections.
        self.start_runtime(False, use_saved=True)
        if not self.runtime:
            if self.runtime_lock:
                self.runtime_lock.unlock()
                self.runtime_lock = None
            self.showNormal()
        return True

    def start_game(self):
        self.start_runtime(False)
        if self.runtime:
            self.showMinimized()

    def stop_runtime(self):
        self.audio_warning.hide()
        self.compare_button.setEnabled(False)
        self.recording_details_key = None
        self.stop_playback()
        self.play_recording.setEnabled(False)
        self.recording_details.setText("Start a voice test to record and listen back.")
        if self.runtime:
            self.runtime.close()
            self.runtime = None
        if self.runtime_lock:
            self.runtime_lock.unlock()
            self.runtime_lock = None
        self.overlay.dismiss()
        self.test_talk.setEnabled(False)
        self.practice_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.test_talk.setText("Start the voice test above")
        self.status.setText("Stopped. You can change your settings now.")

    def tick(self):
        self.poll_downloads()
        self.poll_comparison()
        self.poll_wow_search()
        try:
            if self.playback_until and playback_error():
                message = playback_error()
                self.stop_playback()
                self.error("System playback could not start. " + message)
            if self.playback_until and time.monotonic() >= self.playback_until:
                self.stop_playback()
            if self.mic_check:
                self.mic_level.setValue(min(100, int(self.mic_check.level * 500)))
                if time.monotonic() - self.mic_check_started >= 5:
                    self.stop_mic_check(report=True)
            if self.runtime:
                self.runtime.poll()
                self.live_input.setText(
                    CATALOG.get(
                        self.runtime.speech.preset, (self.runtime.speech.model,)
                    )[0]
                    + " · "
                    + (
                        "Automatic language"
                        if self.runtime.speech.model == "parakeet-v3"
                        else NAMES.get(
                            self.runtime.language or "auto",
                            self.runtime.language or "auto",
                        )
                    )
                    + "\nMicrophone: "
                    + (self.runtime.chat.input_device or "System default")
                )
                self.update_recording_details()
                self.test_talk.setText(
                    "Stop recording"
                    if self.runtime.session.state == "recording"
                    else "Preparing your words…"
                    if self.runtime.session.state in ("transcribing", "drafting")
                    else "Record"
                    if self.runtime.ready
                    else "Preparing speech… please wait"
                )
                self.test_talk.setEnabled(
                    self.runtime.ready
                    and self.runtime.session.state in ("idle", "recording", "preview")
                )
            elif self.hub:
                events = self.hub.poll()
                if self.learning:
                    action, learner = self.learning
                    if time.monotonic() - learner.started > 20:
                        self.cancel_learning()
                        self.input_status.setText(
                            "No signal received. Check the active controller profile, apply its button mappings, then try again."
                        )
                        self.status.setText(
                            self.hub.controller_status()
                            + " Open Controller connection for help."
                        )
                        return
                    for event in events:
                        value = learner.feed(*event)
                        if value:
                            self.cancel_learning()
                            self.assign_binding(action, value)
                            message = (
                                "Button detected: "
                                + pretty_binding(value)
                                + ". Saved when you continue."
                            )
                            self.input_status.setText(message)
                            self.status.setText(message)
                            break
                elif self.pages.currentIndex() == 2:
                    for event in events:
                        if event[0] in ("key", "mouse", "pad", "raw"):
                            for action, down in self.button_monitor.feed(*event):
                                if down and action in ("record", "menu"):
                                    self.status.setText(
                                        ("Talk" if action == "record" else "Menu")
                                        + " button detected. Your mapping works."
                                    )
        except Exception as error:
            self.status.setText(str(error))

    def closeEvent(self, event):
        if self.downloads:
            self.downloads.cancelled.set()
        if self.comparison:
            self.comparison.cancel()
            self.comparison.thread.join(timeout=3)
        QApplication.instance().removeEventFilter(self)
        self.stop_mic_check()
        self.stop_runtime()
        if self.hub:
            self.hub.close()
        self.overlay.close()
        event.accept()


def acquire_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(path.parent / "runtime.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise RuntimeError(
            "ThumbTalk is already running. Close its other window or stop practice before starting WoW."
        )
    return lock


def run_app(path, speech, chat, headless=False, practice=False, startup=False):
    from thumbtalk_display import overlay_display

    target_display = overlay_display() if headless else None
    app = QApplication.instance() or QApplication(
        ["thumbtalk", "-display", target_display] if target_display else []
    )
    app.setProperty("thumbtalkOverlayDisplay", target_display)
    apply_theme(app)
    app.setApplicationName("ThumbTalk")
    if not headless:
        window = Setup(path, speech, chat, practice)
        if startup:
            if not window.start_at_login():
                return
        else:
            window.show()
        app.exec()
        return
    app.setQuitOnLastWindowClosed(False)
    lock = acquire_lock(path)
    overlay = Overlay(chat.overlay_mode, chat.overlay_position)
    runtime = None
    stopping = False
    print(
        "Displays: game="
        + str(os.environ.get("DISPLAY"))
        + "; overlay="
        + str(target_display),
        flush=True,
    )

    def notify(text):
        print(time.strftime("%H:%M:%S ") + text, flush=True)
        persistent = (
            runtime is None
            or not getattr(runtime, "ready", True)
            or (
                runtime.menu_open
                or runtime.session.state in ("recording", "transcribing")
            )
        )
        if runtime and runtime.menu_open and runtime.session.state == "idle":
            text = runtime.menu_text()
        overlay.message(
            text,
            persistent,
            menu=runtime.menu_view()
            if runtime and runtime.menu_open and runtime.session.state == "idle"
            else None,
        )

    try:
        runtime = Runtime(
            speech,
            chat,
            notify,
            practice=practice,
            save_preferences=(lambda *_: None)
            if practice
            else (
                lambda speech_changes, chat_changes: save_menu_preferences(
                    path, speech_changes, chat_changes
                )
            ),
        )
        timer = QTimer()

        last_poll_error = None

        def poll():
            nonlocal last_poll_error
            if stopping or runtime.closed:
                return
            try:
                runtime.poll()
                last_poll_error = None
            except Exception as error:
                if stopping:
                    return
                if str(error) != last_poll_error:
                    import traceback

                    traceback.print_exc()
                    notify(str(error))
                    last_poll_error = str(error)

        timer.timeout.connect(poll)
        timer.start(15)

        def stop(*args):
            nonlocal stopping
            stopping = True
            print(time.strftime("%H:%M:%S Companion stopping"), flush=True)
            timer.stop()
            runtime.close()
            app.quit()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        app.exec()
    finally:
        if runtime:
            runtime.close()
        overlay.close()
        lock.unlock()
