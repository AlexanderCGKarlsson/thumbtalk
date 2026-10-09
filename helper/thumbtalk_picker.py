"""Touch-friendly choices for the signals Steam or a controller sends."""

import platform
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QWidget,
    QTabWidget,
    QSizePolicy,
)
from thumbtalk_chat import canonical_binding, validate_bindings

PAD_LABELS = {
    "PAD1": "A / Cross",
    "PAD2": "B / Circle",
    "PAD3": "X / Square",
    "PAD4": "Y / Triangle",
    "PADLSHOULDER": "LB / L1",
    "PADRSHOULDER": "RB / R1",
    "PADLTRIGGER": "LT / L2",
    "PADRTRIGGER": "RT / R2",
    "PADLSTICK": "Left stick click",
    "PADRSTICK": "Right stick click",
    "PADSOCIAL": "View / Select",
    "PADFORWARD": "Menu / Start",
    "PADSYSTEM": "Xbox / Guide",
    "PADDUP": "D-pad ↑",
    "PADDDOWN": "D-pad ↓",
    "PADDLEFT": "D-pad ←",
    "PADDRIGHT": "D-pad →",
    "PADPADDLE1": "Paddle 1",
    "PADPADDLE2": "Paddle 2",
    "PADPADDLE3": "Paddle 3",
    "PADPADDLE4": "Paddle 4",
}
MOUSE_LABELS = {
    "left": "Left Mouse Click",
    "middle": "Middle Mouse Click",
    "right": "Right Mouse Click",
    "x1": "Mouse 4 Click",
    "x2": "Mouse 5 Click",
}
KEY_LABELS = {
    "ctrl": "Ctrl",
    "shift": "Shift",
    "alt": "Alt",
    "cmd": "Cmd" if platform.system() == "Darwin" else "Win",
    "page_up": "PgUp",
    "page_down": "PgDn",
    "backspace": "Backspace",
    "space": "Space",
    "enter": "Enter",
    "esc": "Esc",
    "tab": "Tab",
    "up": "↑",
    "down": "↓",
    "left": "←",
    "right": "→",
}


def binding_label(value):
    if not value:
        return "Not assigned"
    alternatives = []
    for entry in value.split("|"):
        kind, body = entry.split(":", 1)
        labels = (
            MOUSE_LABELS
            if kind == "mouse"
            else PAD_LABELS
            if kind == "pad"
            else KEY_LABELS
        )
        prefix = {
            "key": "Keyboard ",
            "pad": "Controller ",
            "raw": "Controller button ",
        }.get(kind, "")
        alternatives.append(
            prefix + " + ".join(labels.get(p, p.upper()) for p in body.split("+"))
        )
    return " or ".join(alternatives)


class MousePreview(QWidget):
    def __init__(self, picker):
        super().__init__()
        self.picker = picker
        self.setMinimumHeight(180)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#e4e7df"))
        painter.drawEllipse(QRectF(-77, -84, 154, 180))
        painter.setBrush(QColor("#ffffff"))
        painter.setPen(QPen(QColor("#a9b0a3"), 2))
        painter.drawRoundedRect(QRectF(-49, -77, 98, 154), 45, 45)
        active = {
            part
            for (kind, part), button in self.picker.buttons.items()
            if kind == "mouse" and button.isChecked()
        }
        for part, rect in [
            ("left", QRectF(-41, -59, 30, 56)),
            ("right", QRectF(11, -59, 30, 56)),
            ("middle", QRectF(-5, -48, 10, 29)),
            ("x1", QRectF(-55, -12, 5, 20)),
            ("x2", QRectF(-55, 14, 5, 20)),
        ]:
            painter.setBrush(QColor("#687e5d" if part in active else "#e4e7df"))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 5, 5)
        painter.setPen(QPen(QColor("#c4cabb"), 1))
        painter.drawLine(-40, 1, 40, 1)


class BindingPicker(QDialog):
    def __init__(self, action, bindings, parent=None):
        super().__init__(parent)
        self.action, self.bindings = action, dict(bindings)
        self.value = None
        self.listen_requested = False
        self.buttons = {}
        self.setWindowTitle(
            "Choose "
            + ("Talk" if action == "record" else action.replace("_", " ").title())
            + " button"
        )
        self.resize(760, 590)
        self.setStyleSheet("""
            QTabWidget::pane { border:0; background:#f4f4f1; }
            QTabBar::tab { background:#e9ece5; color:#535b53; padding:12px 24px; margin:0 4px 10px 0; border-radius:8px; }
            QTabBar::tab:selected { background:#292e2c; color:#ffffff; }
            QPushButton#pick { padding:5px 3px; min-height:30px; }
            QPushButton#pick:checked { background:#dce6d3; border:2px solid #687e5d; color:#242729; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        title = QLabel(self.windowTitle())
        title.setObjectName("cardTitle")
        layout.addWidget(title)
        instruction = QLabel(
            "Pick the same command you assigned in Steam. Select several buttons for a combination."
        )
        instruction.setWordWrap(True)
        layout.addWidget(instruction)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("bindingTabs")
        layout.addWidget(self.tabs, 1)
        keyboard_rows = [
            ["esc"] + [f"f{i}" for i in range(1, 11)],
            list("1234567890"),
            list("qwertyuiop"),
            list("asdfghjkl"),
            list("zxcvbnm"),
            ["ctrl", "shift", "alt", "cmd", "space", "enter", "tab", "backspace"],
        ]
        navigation_rows = [
            ["insert", "home", "page_up"],
            ["delete", "end", "page_down"],
            ["left", "up", "down", "right"],
        ]
        self.add_page("Keyboard", "key", keyboard_rows, KEY_LABELS)
        self.add_page("Navigation", "key", navigation_rows, KEY_LABELS)
        self.add_page(
            "Mouse", "mouse", [["left", "middle", "right"], ["x1", "x2"]], MOUSE_LABELS
        )
        keys = list(PAD_LABELS)
        self.add_page(
            "Gamepad",
            "pad",
            [keys[i : i + 4] for i in range(0, len(keys), 4)],
            PAD_LABELS,
        )
        self.summary = QLabel("Choose a button above.")
        self.summary.setWordWrap(True)
        self.summary.setMinimumHeight(44)
        layout.addWidget(self.summary)
        footer = QHBoxLayout()
        listen = QPushButton("Listen for a button…")
        listen.setObjectName("listenBinding")
        listen.clicked.connect(self.listen)
        footer.addWidget(listen)
        footer.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.use = QPushButton("Use this button")
        self.use.setObjectName("primary")
        self.use.setEnabled(False)
        self.use.clicked.connect(self.accept)
        footer.addWidget(self.use)
        layout.addLayout(footer)
        # Show the current binding, and keep all edits local until confirmed.
        first = bindings.get(action, "").split("|")[0]
        if ":" in first:
            kind, body = first.split(":", 1)
            for part in body.split("+"):
                button = self.buttons.get((kind, part))
                if button is not None:
                    button.setChecked(True)
                    self.tabs.setCurrentIndex(button.property("page"))
        self.changed()

    def add_page(self, title, kind, rows, labels):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        grid = QGridLayout()
        grid.setSpacing(6)
        outer.addLayout(grid)
        columns = max(map(len, rows))
        for row, values in enumerate(rows):
            key_row = QHBoxLayout() if title == "Keyboard" else None
            if key_row is not None:
                key_row.setSpacing(6)
                grid.addLayout(key_row, row, 0, 1, columns)
            for column, key in enumerate(values):
                button = QPushButton(labels.get(key, key.upper()))
                button.setObjectName("pick")
                button.setProperty("signal", kind + ":" + key)
                button.setProperty("page", self.tabs.count())
                button.setCheckable(True)
                button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
                button.setMinimumWidth(0)
                button.clicked.connect(
                    lambda checked=False, kind=kind: self.changed(kind)
                )
                if (
                    kind == "mouse"
                    and key in ("x1", "x2")
                    and platform.system() == "Darwin"
                ):
                    button.setEnabled(False)
                    button.setToolTip(
                        "Map this button to a keyboard key in your mouse settings on macOS."
                    )
                self.buttons[kind, key] = button
                if key_row is not None:
                    key_row.addWidget(button, 2 if key in {"backspace", "space"} else 1)
                else:
                    grid.addWidget(button, row, column)
        for column in range(columns):
            grid.setColumnStretch(column, 1)
        if kind == "mouse":
            outer.addWidget(MousePreview(self), 1)
        else:
            outer.addStretch()
        if kind == "pad":
            note = QLabel(
                "For Steam rear-button remaps, choose the keyboard key or mouse click Steam sends."
            )
            note.setWordWrap(True)
            outer.addWidget(note)
        self.tabs.addTab(page, title)

    def changed(self, kind=None):
        if kind:
            for (other, key), button in self.buttons.items():
                if other != kind:
                    button.setChecked(False)
        selected = [(k, part) for (k, part), b in self.buttons.items() if b.isChecked()]
        self.value = None
        if selected:
            try:
                value = canonical_binding(
                    selected[0][0] + ":" + "+".join(part for _, part in selected)
                )
                validate_bindings(dict(self.bindings, **{self.action: value}))
                self.value = value
                self.summary.setText(binding_label(value))
            except ValueError as error:
                self.summary.setText(str(error))
        else:
            self.summary.setText("Choose a button above.")
        self.use.setEnabled(self.value is not None)
        for preview in self.findChildren(MousePreview):
            preview.update()

    def listen(self):
        self.listen_requested = True
        self.accept()
