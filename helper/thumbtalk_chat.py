"""Validated controls, destinations, and message state. No device or game imports."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from thumbtalk_speech import read_config, write_config

CHANNELS = (
    "say",
    "party",
    "raid",
    "guild",
    "instance",
    "whisper",
    "reply",
    "general",
    "trade",
    "lfg",
)
PREFIXES = {
    "say": "/s",
    "party": "/p",
    "raid": "/raid",
    "guild": "/g",
    "instance": "/i",
    "reply": "/r",
    "general": "/tts general",
    "trade": "/tts trade",
    "lfg": "/tts lfg",
}
DEFAULT_BINDINGS = {
    "record": "key:f8",
    "confirm": "key:f9",
    "cancel": "key:f10",
    "menu": "key:f7",
    "next_channel": "key:right|pad:PADDRIGHT",
    "previous_channel": "key:left|pad:PADDLEFT",
    "next_language": "key:up|pad:PADDUP",
    "previous_language": "key:down|pad:PADDDOWN",
    "reply": "key:f6",
}
ACTIONS = tuple(DEFAULT_BINDINGS) + tuple("talk_" + c for c in CHANNELS)
PAD_NAMES = frozenset(
    """PAD1 PAD2 PAD3 PAD4 PAD5 PADSOCIAL PADSYSTEM PADFORWARD
PADLSTICK PADRSTICK PADLSHOULDER PADRSHOULDER PADDUP PADDDOWN PADDLEFT PADDRIGHT
PADBACK PADPADDLE1 PADPADDLE2 PADPADDLE3 PADPADDLE4 PADLTRIGGER PADRTRIGGER""".split()
)
MODIFIERS = frozenset(("ctrl", "shift", "alt", "cmd"))
KEY_NAMES = frozenset(
    (
        "enter",
        "space",
        "tab",
        "esc",
        "backspace",
        "delete",
        "insert",
        "home",
        "end",
        "page_up",
        "page_down",
        "up",
        "down",
        "left",
        "right",
    )
)


def parse_binding(value: str) -> tuple[str, frozenset[str]]:
    if not isinstance(value, str) or ":" not in value:
        raise ValueError("Use key:f8, key:ctrl+f8, pad:PADSOCIAL, or raw:4")
    kind, body = value.strip().split(":", 1)
    if kind not in ("key", "mouse", "pad", "raw"):
        raise ValueError("Binding type must be key, mouse, pad, or raw")
    pieces = body.split("+")
    if kind == "pad":
        pieces = [p.upper() for p in pieces]
        valid = all(p in PAD_NAMES for p in pieces)
    elif kind == "mouse":
        pieces = [p.lower() for p in pieces]
        valid = all(p in {"left", "middle", "right", "x1", "x2"} for p in pieces)
    elif kind == "raw":
        valid = all(p.isdigit() and 0 <= int(p) <= 255 for p in pieces)
        if valid:
            pieces = [str(int(p)) for p in pieces]
    else:
        aliases = {
            "control": "ctrl",
            "escape": "esc",
            "return": "enter",
            "win": "cmd",
            "super": "cmd",
        }
        pieces = [aliases.get(p.lower(), p.lower()) for p in pieces]
        valid = all(
            p in MODIFIERS
            or p in KEY_NAMES
            or re.fullmatch(r"[a-z0-9]|f(?:[1-9]|1[0-9]|2[0-4])", p)
            for p in pieces
        )
        valid = valid and any(p not in MODIFIERS for p in pieces)
    if not valid or len(set(pieces)) != len(pieces):
        raise ValueError(f"Invalid or repeated buttons in binding {value!r}")
    return kind, frozenset(pieces)


def canonical_binding(value: str) -> str:
    kind, parts = parse_binding(value)
    return kind + ":" + "+".join(sorted(parts, key=lambda p: (p not in MODIFIERS, p)))


def validate_bindings(bindings: dict) -> None:
    if not isinstance(bindings, dict) or set(bindings) - set(ACTIONS):
        raise ValueError("Unknown binding action")
    parsed = []
    for action, value in bindings.items():
        if not isinstance(value, str):
            raise ValueError("Bindings must be text")
        if not value:
            continue
        for alternative in value.split("|"):
            kind, parts = parse_binding(alternative)
            if kind == "key" and parts & {"f11", "f12", "f13", "f14"}:
                raise ValueError(
                    "F11–F14 are reserved for the addon camera lock. Choose another key."
                )
            if (
                kind == "key"
                and {"ctrl", "shift"} <= parts
                and parts & {"f5", "f6", "f7", "f8", "f9", "f10"}
            ):
                raise ValueError(
                    "Ctrl+Shift+F5–F10 are reserved for chat review. Choose another key."
                )
            for other, other_kind, other_parts in parsed:
                if (
                    action != other
                    and kind == other_kind
                    and (parts <= other_parts or other_parts <= parts)
                ):
                    if "menu" not in (action, other):
                        raise ValueError(
                            f"Bindings for {action} and {other} overlap; choose distinct combinations"
                        )
            parsed.append((action, kind, parts))
    if not bindings.get("record"):
        raise ValueError("Choose a record binding")
    if not bindings.get("confirm") or not bindings.get("cancel"):
        raise ValueError("Choose confirm and cancel bindings")


def validate_recipient(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Whisper recipient must be a character name")
    value = value.strip()
    if not value or len(value) > 100 or not value[0].isalpha() or value.count("-") > 1:
        raise ValueError("Choose a whisper recipient, such as Character-Realm")
    if any(not (c.isalnum() or c in "-'") for c in value) or value.endswith(("-", "'")):
        raise ValueError(
            "Whisper recipient must be Character or Character-Realm, without spaces or commands"
        )
    return value


@dataclass(frozen=True)
class Destination:
    channel: str
    recipient: str = ""

    def validate(self) -> None:
        if self.channel not in CHANNELS:
            raise ValueError("Choose a supported chat channel")
        if self.channel == "whisper":
            validate_recipient(self.recipient)

    @property
    def label(self) -> str:
        return (
            f"Whisper → {self.recipient}"
            if self.channel == "whisper"
            else "Looking for Group"
            if self.channel == "lfg"
            else self.channel.title()
        )

    def message(self, text: str) -> str:
        self.validate()
        text = " ".join(text.split())
        if not text:
            raise ValueError("No speech detected")
        if text.startswith("/") or any(ord(c) < 32 or ord(c) == 127 for c in text):
            raise ValueError("Dictated slash commands are not supported")
        prefix = (
            f"/w {validate_recipient(self.recipient)}"
            if self.channel == "whisper"
            else PREFIXES[self.channel]
        )
        result = f"{prefix} {text}"
        if len(result.encode("utf-8")) > 255:
            raise ValueError(
                "Message is too long for one chat line; record a shorter message"
            )
        return result


@dataclass(frozen=True)
class ChatSettings:
    channel: str = "say"
    recipient: str = ""
    favorites: tuple[str, ...] = ()
    languages: tuple[str, ...] = ("en",)
    mode: str = "hold"
    auto_send: bool = False
    classic_chat: bool = False  # Legacy setting, retained only to load older files.
    review_b_cancel: bool = False
    overlay_mode: str = "automatic"
    overlay_position: str = "top-center"
    max_seconds: int = 30
    input_device: str | None = None
    wow_dir: str = ""
    bindings: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_BINDINGS))

    def validate(self) -> None:
        if not isinstance(self.channel, str) or self.channel not in CHANNELS:
            raise ValueError("Invalid chat destination")
        if not isinstance(self.recipient, str):
            raise ValueError("Recipient must be text")
        if self.recipient:
            validate_recipient(self.recipient)
        if not isinstance(self.favorites, (list, tuple)) or len(self.favorites) > 30:
            raise ValueError("Choose at most 30 whisper favorites")
        for recipient in self.favorites:
            validate_recipient(recipient)
        from thumbtalk_speech import normalize_language

        if (
            not isinstance(self.languages, (list, tuple))
            or not 1 <= len(self.languages) <= 12
        ):
            raise ValueError("Choose between 1 and 12 quick-switch languages")
        normalized = [normalize_language(language) for language in self.languages]
        if len(set(normalized)) != len(normalized):
            raise ValueError("Remove duplicate quick-switch languages")
        if self.mode not in ("toggle", "hold") or type(self.auto_send) is not bool:
            raise ValueError(
                "Recording mode must be toggle/hold; auto_send must be true/false"
            )
        if type(self.classic_chat) is not bool:
            raise ValueError("Classic chat preference must be true/false")
        if type(self.review_b_cancel) is not bool:
            raise ValueError("B-to-discard preference must be true/false")
        if type(self.max_seconds) is not int or not 1 <= self.max_seconds <= 60:
            raise ValueError("Recording limit must be between 1 and 60 seconds")
        if self.input_device is not None and not isinstance(self.input_device, str):
            raise ValueError("Microphone must be a device name")
        if not isinstance(self.wow_dir, str):
            raise ValueError("WoW directory must be a path")
        from thumbtalk_overlay import STATUS_MODES, STATUS_POSITIONS

        if (
            not isinstance(self.overlay_mode, str)
            or self.overlay_mode not in STATUS_MODES
        ):
            raise ValueError("Choose how to show speech status")
        if (
            not isinstance(self.overlay_position, str)
            or self.overlay_position not in STATUS_POSITIONS
        ):
            raise ValueError("Choose a speech status position")
        validate_bindings(self.bindings)


def load_chat(path: Path) -> ChatSettings:
    values = read_config(path).get("chat", {})
    if not isinstance(values, dict) or set(values) - set(
        ChatSettings.__dataclass_fields__
    ):
        raise ValueError("Unknown or invalid chat settings")
    settings = ChatSettings(**values)
    settings.validate()
    return settings


def save_chat(path: Path, settings: ChatSettings) -> None:
    settings.validate()
    data = read_config(path)
    data.update(version=1, chat=asdict(settings))
    write_config(path, data)


def save_menu_preferences(path: Path, speech_changes: dict, chat_changes: dict):
    """Merge a menu choice without overwriting unrelated setup preferences."""
    from thumbtalk_speech import SpeechSettings

    data = read_config(path)
    speech = SpeechSettings(**{**data.get("speech", {}), **speech_changes})
    chat = ChatSettings(**{**data.get("chat", {}), **chat_changes})
    speech.validate()
    chat.validate()
    data.update(version=1, speech=asdict(speech), chat=asdict(chat))
    write_config(path, data)
    return speech, chat


class BindingMatcher:
    """Debounce repeated events; keep controller state separate for each device."""

    def __init__(self, bindings):
        validate_bindings(bindings)
        self.bindings = {
            a: [parse_binding(v) for v in value.split("|")]
            for a, value in bindings.items()
            if value
        }
        self.pressed = {}
        self.active = set()

    def feed(self, kind, device, button, down):
        source = (kind, device)
        held = self.pressed.setdefault(source, set())
        held.add(button) if down else held.discard(button)
        changes = []
        for action, alternatives in self.bindings.items():
            identity = (source, action)
            now = any(
                expected_kind == kind and parts <= held
                for expected_kind, parts in alternatives
            )
            was = identity in self.active
            if now and not was:
                self.active.add(identity)
                changes.append((action, True))
            elif was and not now:
                self.active.remove(identity)
                changes.append((action, False))
        return changes

    def disconnect(self, kind, device):
        self.pressed.pop((kind, device), None)
        released = [
            (action, False)
            for source, action in self.active
            if source == (kind, device)
        ]
        self.active = {entry for entry in self.active if entry[0] != (kind, device)}
        return released

    def modifiers_clear(self):
        return not any(
            kind == "key" and MODIFIERS.intersection(held)
            for (kind, _), held in self.pressed.items()
        )


@dataclass
class Session:
    """Destination and target are frozen when recording starts; stale work cannot send."""

    state: str = "idle"
    generation: int = 0
    destination: Destination | None = None
    target: object = None
    text: str = ""
    started: float = 0

    def begin(self, destination, target):
        if self.state != "idle":
            raise ValueError("Finish or cancel the current message first")
        destination.validate()
        if target is None:
            raise ValueError("Focus the WoW game window before recording")
        self.generation += 1
        self.destination, self.target = destination, target
        self.state, self.text, self.started = "recording", "", time.monotonic()
        return self.generation

    def stop(self):
        if self.state != "recording":
            return None
        self.state = "transcribing"
        return self.generation

    def complete(self, generation, text):
        if generation != self.generation or self.state != "transcribing":
            return False
        self.destination.message(text)  # validate before any typing
        self.text = " ".join(text.split())
        self.state = "preview"
        return True

    def prepare(self, target):
        if self.state != "preview":
            raise ValueError("No message is ready to send")
        if target is None or target != self.target:
            raise ValueError("Return to the same WoW window, then confirm")
        payload = self.destination.message(self.text)
        self.state = "sending"
        return payload

    def cancel(self):
        self.generation += 1
        self.state, self.text, self.target, self.destination = "idle", "", None, None
