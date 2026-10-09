"""Main-thread input dispatch with cancellable speech and delivery workers."""

from __future__ import annotations

import queue
import gc
import math
import threading
import time
from dataclasses import asdict, replace

from thumbtalk_chat import BindingMatcher, Destination, Session
from thumbtalk_input import InputHub
from thumbtalk_menu import RadialNavigator, ROOT_ITEMS, SENDING_ITEMS, STICK_NEUTRAL
from thumbtalk_models import CATALOG, model_downloaded
from thumbtalk_platform import Foreground, KeyboardDelivery, Target, LOCAL_REVIEW_PAUSED
from thumbtalk_camera import MenuCameraLock
from thumbtalk_speech import create_transcriber, LANGUAGE_NAMES, PRESETS
from thumbtalk_audio import device_formats, resample_mono, audio_stats


class Recorder:
    def __init__(self, device=None, max_seconds=30):
        self.device, self.limit = device, max_seconds
        self.stream, self.chunks, self.samples = None, [], 0
        self.lock = threading.Lock()
        self.device_lock = threading.Lock()
        self.disabled = threading.Event()
        self.first_audio = threading.Event()
        self.recording = False
        self.keep_open = False
        self.rate, self.channels, self.selected_channel = 16000, 1, 1
        self.stats = None
        self.level = self.peak = 0.0
        self.overflows = 0

    def prepare(self, keep_open=True):
        with self.device_lock:
            if self.disabled.is_set():
                raise ValueError("The microphone session has closed.")
            self.keep_open = keep_open
            if self.stream is not None:
                return
            self.first_audio.clear()
            self._open()

    def _open(self):
        import sounddevice as sd

        from thumbtalk_pipewire import SYSTEM_AUDIO, CaptureStream

        if self.device == SYSTEM_AUDIO:
            self.rate, self.channels = 48000, 1
            self.stream = CaptureStream(self.callback)
            self.stream.start()
            if not self.first_audio.wait(5):
                error = self.stream.error
                self.stream.close()
                self.stream = None
                raise ValueError(
                    "No audio from the system microphone. Check the default recording device in Sound settings. "
                    + error
                )
            return
        last_error = None
        for self.rate, self.channels in device_formats(self.device, "input"):
            try:
                self.stream = sd.InputStream(
                    device=self.device,
                    samplerate=self.rate,
                    channels=self.channels,
                    dtype="float32",
                    callback=self.callback,
                    blocksize=0,
                    latency="high",
                )
                self.stream.start()
                if not self.first_audio.wait(3):
                    self.stream.close()
                    self.stream = None
                    raise ValueError(
                        "The microphone opened but no audio arrived. Choose another input."
                    )
                return
            except sd.PortAudioError as error:
                last_error = error
                if self.stream is not None:
                    self.stream.close()
                    self.stream = None
                self.chunks, self.samples = [], 0
            except Exception:
                if self.stream is not None:
                    self.stream.close()
                    self.stream = None
                raise
        raise ValueError(
            "Could not open this microphone at a supported rate. Choose another input."
            + (f" {last_error}" if last_error else "")
        )

    def start(self):
        if self.stream is None:
            self.prepare(keep_open=False)
        with self.lock:
            self.chunks, self.samples = [], 0
            self.level = self.peak = 0.0
            self.overflows = 0
            self.stats = None
            self.recording = True

    def close(self):
        self.disabled.set()
        with self.device_lock:
            try:
                if self.stream is not None:
                    self.stream.stop()
            finally:
                if self.stream is not None:
                    self.stream.close()
                    self.stream = None
                with self.lock:
                    self.recording = False
                    self.chunks = []

    def callback(self, data, frames, timing, status):
        if len(data):
            self.first_audio.set()
        with self.lock:
            if not self.recording:
                return
            if status and status.input_overflow:
                self.overflows += 1
            self.level = float(abs(data).max()) if len(data) else 0.0
            self.peak = max(self.peak, self.level)
            count = min(len(data), self.limit * self.rate - self.samples)
            if count > 0:
                self.chunks.append(data[:count].copy())
                self.samples += count

    def stop(self, discard=False):
        import numpy as np

        if self.stream is not None and not self.keep_open:
            try:
                self.stream.stop()
            finally:
                self.stream.close()
                self.stream = None
        with self.lock:
            self.recording = False
            audio = (
                np.concatenate(self.chunks)
                if self.chunks and not discard
                else np.zeros(0, dtype="float32")
            )
            self.chunks = []
        if len(audio):
            # Keep the stronger channel without cancelling opposite-phase mic signals.
            if audio.ndim == 2:
                if not np.isfinite(audio).all():
                    raise ValueError(
                        "The microphone returned invalid audio. Choose another input and try again."
                    )
                energies = np.mean(audio.astype("float64") ** 2, axis=0)
                self.selected_channel = int(np.argmax(energies)) + 1
                audio = audio[:, self.selected_channel - 1]
            self.stats = audio_stats(audio)
            if self.stats["invalid"]:
                raise ValueError(
                    "The microphone returned invalid audio. Choose another input and try again."
                )
            audio = resample_mono(audio, self.rate, 16000)

        return audio


class Runtime:
    def __init__(
        self,
        speech,
        chat,
        notify=print,
        transcript=lambda text: None,
        practice=False,
        hub=None,
        recorder=None,
        foreground=None,
        delivery=None,
        transcriber_factory=create_transcriber,
        model_available=model_downloaded,
        save_preferences=lambda speech_changes, chat_changes: None,
    ):
        self.chat, self.speech = chat, speech
        self.transcriber_factory = transcriber_factory
        self.model_available = model_available
        self.save_preferences = save_preferences
        self.available_models = ()
        self.notify, self.show_transcript, self.practice = notify, transcript, practice
        self.hub = hub or InputHub()
        self.own_hub = hub is None
        if self.hub.error and any("key:" in value for value in chat.bindings.values()):
            if self.own_hub:
                self.hub.close()
            raise RuntimeError(self.hub.error)
        if getattr(self.hub, "mouse_error", "") and any(
            "mouse:" in value for value in chat.bindings.values()
        ):
            self.hub.close()
            raise RuntimeError(self.hub.mouse_error)

        try:
            self.foreground = foreground or (None if practice else Foreground())
            self.delivery = delivery or (
                None
                if practice
                else KeyboardDelivery(
                    on_key=self.hub.expect_injected,
                    log=lambda message: print(
                        time.strftime("%H:%M:%S"), message, flush=True
                    ),
                )
            )
        except Exception:
            if self.own_hub:
                self.hub.close()
            raise
        self.recorder = recorder or Recorder(chat.input_device, chat.max_seconds)
        self.matcher = BindingMatcher(chat.bindings)
        self.session = Session()
        self.camera_lock = (
            None
            if practice
            else MenuCameraLock(self.foreground.current, self.delivery.menu_signal)
        )
        self.destination = Destination(chat.channel, chat.recipient)
        self.language = speech.language
        self.record_language = speech.language
        self.shortcut_held = set()
        self.ignore_record_release = False
        self.menu_open = False
        self.menu_nav = RadialNavigator()
        self.menu_changed = False
        self.events = queue.SimpleQueue()
        self.abort = threading.Event()
        self.closed, self.busy, self.ready, self.draft_ready = (
            False,
            False,
            False,
            False,
        )
        self.deferred = None
        self.input_wait_notified = False
        self.transcriber = None
        self.last_audio = None
        self.transcription_seconds = None
        self.loading_started = time.monotonic()
        self.load_status = "Preparing speech…"
        self.worker = None

        def prepare_session():
            transcriber = transcriber_factory(
                speech, lambda text: self.events.put(("progress", 0, text, None))
            )
            prepare = getattr(self.recorder, "prepare", None)
            if not self.closed and prepare:
                self.events.put(("progress", 0, "Preparing microphone…", None))
                prepare()
            return transcriber

        self._start_worker("loaded", 0, prepare_session)
        self.notify(
            "Preparing speech. The first use downloads a free model; later use works offline."
        )

    def local_review_paused(self):
        return (
            not self.practice
            and getattr(self.delivery, "local_review_available", True) is False
        )

    def menu_view(self):
        nav = self.menu_nav
        if nav.section == "root":
            return {
                "title": "ThumbTalk",
                "items": list(ROOT_ITEMS),
                "selected": nav.hover,
                "detail": dict(ROOT_ITEMS).get(nav.hover, "Choose a menu"),
                "hint": "Right stick: point to open · D-pad + Talk: open\nB: close · Release Menu: close",
            }
        if nav.section == "language":
            automatic = getattr(self.transcriber.model, "automatic_language", False)
            codes = ["auto"] if automatic else list(self.chat.languages)
            current = "auto" if automatic else (self.language or "auto")
            items = [(code, LANGUAGE_NAMES.get(code, code)) for code in codes]
            detail = (
                "Automatic · Parakeet"
                if automatic
                else LANGUAGE_NAMES.get(nav.hover or current, nav.hover or current)
            )
            title = "Language"
        elif nav.section == "model":
            current = self.speech.preset
            if self.speech.model != PRESETS[current]["model"]:
                current = "current"
            items = [(code, CATALOG[code][0]) for code in self.available_models]
            if current not in dict(items):
                items.insert(0, (current, self.speech.model + " (current)"))
            detail = dict(items).get(nav.hover or current, "Downloaded models")
            title = "Speech model"
        elif nav.section == "sending":
            items = list(SENDING_ITEMS)
            current = "immediate" if self.chat.auto_send else "review"
            detail = (
                "Send as soon as transcription finishes"
                if (nav.hover or current) == "immediate"
                else "Tap Talk to send after reviewing"
            )
            if self.local_review_paused() and (nav.hover or current) == "review":
                detail = "Review paused on Linux · choose Send immediately"
            title = "Sending"
        else:
            # The main Whisper item follows WoW's latest-whisper target via /r.
            codes = ["say", "party", "raid", "guild", "instance", "reply"]
            items = [
                (code, "Whisper" if code == "reply" else code.title()) for code in codes
            ]
            if self.chat.recipient:
                items.append(("whisper", "To " + self.chat.recipient))
            pages = (
                items,
                [
                    ("general", "General"),
                    ("trade", "Trade"),
                    ("lfg", "Looking for Group"),
                ],
            )
            items = pages[nav.page % len(pages)]
            current = self.destination.channel
            if current not in dict(items):
                current = None
            detail = (
                "Latest whisper"
                if (nav.hover or current) == "reply"
                else dict(items).get(nav.hover or current, "Choose a channel")
            )
            title = "Chat channel"
        hint = "Right stick: choose · Release Menu: apply\nB / stick click: back · D-pad: cycle"
        if not nav.armed and nav.source is not None:
            hint = "Center the right stick, then point at a choice\nB / stick click: back · Release Menu: close"
        return {
            "title": title,
            "page": nav.page,
            "pages": 2 if nav.section == "channel" else 1,
            "items": items,
            "selected": nav.hover or current,
            "detail": detail,
            "hint": hint,
        }

    def menu_text(self):
        view = self.menu_view()
        return view["title"] + " · " + view["detail"] + "\n" + view["hint"]

    def menu_back(self):
        if self.menu_nav.section == "root":
            self.menu_open = False
            self.notify("Menu closed · " + self.destination.label)
        else:
            self.menu_nav.back()
            self.notify(self.menu_text())

    def apply_menu_selection(self):
        selection = self.menu_nav.selection()
        if selection is None:
            return
        if self.busy or self.session.state != "idle":
            raise ValueError(
                "Finish or cancel the current message before changing settings."
            )
        section, choice = selection
        if section == "language":
            from thumbtalk_speech import normalize_language

            candidate = normalize_language(choice)
            replace(self.speech, language=candidate).validate()
            if (
                candidate
                and candidate not in self.transcriber.model.supported_languages
            ):
                raise ValueError("This speech model does not support that language")
            self.save_preferences({"language": candidate}, {})
            self.language = candidate
            self.speech = replace(self.speech, language=candidate)
        elif section == "model":
            if choice != "current":
                self.change_model(choice)
        elif section == "sending":
            if choice not in dict(SENDING_ITEMS):
                raise ValueError("Choose Review first or Send immediately.")
            immediate = choice == "immediate"
            if not immediate and self.local_review_paused():
                raise ValueError(LOCAL_REVIEW_PAUSED)
            if immediate != self.chat.auto_send:
                self.save_preferences({}, {"auto_send": immediate})
                self.chat = replace(self.chat, auto_send=immediate)
        else:
            destination = Destination(choice, self.chat.recipient)
            destination.validate()
            self.save_preferences({}, {"channel": choice})
            self.destination = destination
            self.chat = replace(self.chat, channel=choice)
        self.menu_changed = True

    def change_model(self, preset):
        if self.busy or self.session.state != "idle":
            raise ValueError(
                "Finish or cancel the current message before changing models."
            )
        if preset not in CATALOG:
            raise ValueError("Choose a downloaded speech model.")
        if (
            self.speech.preset == preset
            and self.speech.model == PRESETS[preset]["model"]
        ):
            return
        if not self.model_available(preset):
            raise ValueError("Download this model in Setup → Voice before switching.")
        previous = replace(self.speech, language=self.language)
        candidate = replace(previous, preset=preset, **PRESETS[preset])
        if preset == "parakeet":
            candidate = replace(
                candidate, language=None, device="cpu", compute_type="int8"
            )
        candidate.validate()
        self.ready = False
        self.loading_started = time.monotonic()
        self.load_status = "Loading " + CATALOG[preset][0] + "…"
        self.transcriber = None  # Drop the old weights before loading a replacement.

        def load():
            clear_cache = getattr(self.transcriber_factory, "clear", None)
            if clear_cache:
                clear_cache()
            gc.collect()

            def progress(message):
                self.events.put(("progress", 0, message, None))

            try:
                transcriber = self.transcriber_factory(
                    candidate, progress, local_files_only=True
                )
                return candidate, transcriber, None
            except Exception as error:
                progress("Could not load that model. Restoring the previous model…")
                if clear_cache:
                    clear_cache()
                gc.collect()
                transcriber = self.transcriber_factory(
                    previous, progress, local_files_only=True
                )
                return previous, transcriber, str(error)

        self._start_worker("model_loaded", 0, load)

    @property
    def menu_open(self):
        return self._menu_open

    @menu_open.setter
    def menu_open(self, value):
        self._menu_open = value
        if self.camera_lock is not None:
            self.camera_lock.update(value and self.session.state == "idle")

    def current_target(self):
        return Target("practice", 1) if self.practice else self.foreground.current()

    def _start_worker(self, kind, token, function):
        if self.busy:
            raise RuntimeError("Please wait for the previous operation")
        self.busy = True

        def run():
            try:
                result = function()
                self.events.put((kind, token, result, None))
            except Exception as error:
                self.events.put((kind, token, None, str(error)))
            finally:
                if self.closed and self.delivery is not None:
                    self.delivery.close()

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def review_cancel_available(self, kind, button):
        # Explicit user assignments take precedence over the review shortcut.
        return not any(
            action != "cancel" and expected_kind == kind and button in parts
            for action, alternatives in self.matcher.bindings.items()
            for expected_kind, parts in alternatives
        )

    def poll(self):
        if self.closed:
            return
        if self.camera_lock is not None:
            self.camera_lock.update(self.menu_open and self.session.state == "idle")
        for event in self.hub.poll():
            kind, device, button, down = event
            identity = (kind, device, button)
            if identity in self.shortcut_held:
                if not down:
                    self.shortcut_held.discard(identity)
                continue
            if (
                self.session.state in ("preview", "interrupted")
                and down
                and (
                    (kind == "pad" and button == "PAD2" and self.chat.review_b_cancel)
                    or (kind == "key" and button == "esc")
                )
                and self.review_cancel_available(kind, button)
            ):
                self.shortcut_held.add(identity)
                self.menu_open = False
                self.cancel()
                continue
            if (
                self.menu_open
                and self.session.state == "idle"
                and self.menu_nav.section == "channel"
                and down
                and (
                    (kind == "pad" and button in ("PADLSHOULDER", "PADRSHOULDER"))
                    or (kind == "key" and button in ("page_up", "page_down"))
                )
                and not any(
                    expected_kind == kind and button in parts
                    for alternatives in self.matcher.bindings.values()
                    for expected_kind, parts in alternatives
                )
                and (kind != "pad" or self.menu_nav.source in (None, device))
            ):
                direction = -1 if button in ("PADLSHOULDER", "page_up") else 1
                if self.menu_nav.change_page(direction, self.menu_view()["pages"]):
                    self.shortcut_held.add(identity)
                    self.notify(self.menu_text())
                continue
            if kind == "stick":
                if self.menu_open and self.session.state == "idle":
                    was_armed = self.menu_nav.armed
                    changed = self.menu_nav.point(
                        device, *down, self.menu_view()["items"], time.monotonic()
                    )
                    if changed or was_armed != self.menu_nav.armed:
                        self.notify(self.menu_text())
                continue
            if (
                self.menu_open
                and self.session.state == "idle"
                and down
                and (
                    (kind == "pad" and button in ("PAD2", "PADRSTICK"))
                    or (kind == "key" and button == "esc")
                )
            ):
                self.menu_back()
                continue
            if kind == "disconnect":
                self.shortcut_held = {
                    held for held in self.shortcut_held if held[:2] != (button, device)
                }
                if self.menu_open and self.menu_nav.source == device:
                    self.menu_open = False
                    self.notify(
                        "Controller disconnected. Menu closed without applying a choice."
                    )
                changes = self.matcher.disconnect(button, device)
                if any(action == "menu" for action, pressed in changes):
                    self.menu_open = False
                if changes and self.session.state == "recording":
                    self.cancel()
                    self.notify("Controller disconnected. Recording discarded.")
                continue
            for action, pressed in self.matcher.feed(*event):
                try:
                    self.action(action, pressed)
                except Exception as error:
                    self.notify(str(error))
        if self.menu_open and self.menu_nav.tick(time.monotonic()):
            self.notify(self.menu_text())
        while not self.events.empty():
            kind, token, result, error = self.events.get()
            if kind == "progress":
                if not self.closed and not self.ready:
                    self.load_status = result
                    self.notify(result)
                continue
            self.busy = False
            if self.closed:
                continue
            if kind == "model_loaded":
                if error:
                    self.load_status = (
                        "Speech could not restart. Reopen ThumbTalk: " + error
                    )
                    self.notify(self.load_status)
                    continue
                settings, self.transcriber, restore_reason = result
                self.speech, self.language, self.ready = (
                    settings,
                    settings.language,
                    True,
                )
                if restore_reason:
                    self.notify("Previous model restored. " + restore_reason)
                else:
                    try:
                        self.save_preferences(asdict(settings), {})
                    except (OSError, ValueError) as save_error:
                        self.notify(
                            "Model ready for this session; could not save: "
                            + str(save_error)
                        )
                    else:
                        self.notify("Ready · " + CATALOG[settings.preset][0])
                continue
            if kind == "loaded":
                if error:
                    self.load_status = "Speech could not start: " + error
                    self.notify(self.load_status)
                else:
                    self.transcriber, self.ready = result, True
                    self.notify("Ready · " + self.destination.label)
                continue
            if token != self.session.generation:
                continue
            if error:
                if kind == "drafted":
                    # Some text may already be in WoW. Keep cancellation available,
                    # but never retry typing or submit an incomplete draft.
                    self.session.state = "interrupted"
                    self.draft_ready = True
                    self.notify(
                        "Review transfer interrupted. Nothing sent. Press Menu to discard."
                    )
                else:
                    self.session.cancel()
                    self.draft_ready = False
                self.notify(error)
                continue
            if kind == "transcribed":
                try:
                    if self.session.complete(token, result):
                        self.show_transcript(self.session.text)
                        self.defer("send" if self.chat.auto_send else "draft")
                except ValueError as error:
                    self.session.cancel()
                    self.notify(str(error))
            elif kind == "drafted":
                self.session.state = "preview"
                self.draft_ready = True
                self.notify("Review requested. Check WoW chat.")
                if self.chat.auto_send:
                    self.defer("send")
            elif kind in ("sent", "cleared"):
                self.session.cancel()
                self.draft_ready = False
                self.show_transcript("")
                self.notify(
                    "Voice test complete."
                    if self.practice
                    else (
                        "Send requested. Check WoW chat."
                        if kind == "sent"
                        else "Discard requested. Check WoW chat."
                    )
                )
        if (
            self.session.state == "recording"
            and time.monotonic() - self.session.started >= self.chat.max_seconds
        ):
            self.ignore_record_release = True
            self.stop_recording()
        if self.deferred and not self.busy:
            action, requested = self.deferred
            activity = getattr(self.hub, "activity", None)
            clearing_held = action == "clear" and (
                bool(self.shortcut_held)
                or any(
                    action in ("cancel", "menu") for _, action in self.matcher.active
                )
            )
            if clearing_held or (
                not self.practice and activity is not None and not activity.text_ready()
            ):
                # Never block the player's controls. Defer our input until all
                # buttons settle, including gamepad triggers that WoW treats
                # as modifiers. Analog stick movement alone does not postpone it.
                self.deferred = (action, time.monotonic())
                if not self.input_wait_notified:
                    self.input_wait_notified = True
                    self.notify(
                        "Waiting for button release to enter chat. You can keep moving."
                    )
            elif time.monotonic() - requested > 3:
                self.deferred = None
                self.notify(
                    "Release modifier keys and return to WoW, then press Talk again."
                )
            elif self.matcher.modifiers_clear() and not self.hub.physical_modifiers:
                self.deferred = None
                try:
                    self._deliver(action)
                except Exception as error:
                    if action == "clear":
                        self.session.cancel()
                    self.notify(str(error))

    def defer(self, action):
        self.input_wait_notified = False
        self.deferred = (action, time.monotonic())

    def action(self, action, down):
        if action == "cancel" and down:
            if self.menu_open:
                self.menu_back()
                return
            self.cancel()
            return
        if not self.ready:
            if down:
                self.notify("Speech is still preparing. Please wait.")
            return
        if action == "menu":
            if self.session.state == "interrupted":
                if down:
                    self.cancel()
                return
            if down:
                if self.session.state not in ("idle", "preview"):
                    self.notify("Finish or cancel the current recording first.")
                    return
                if self.session.state == "preview":
                    self.menu_open, self.menu_changed = True, False
                    self.notify("Release Menu to discard this draft.")
                    return
                sticks = getattr(self.hub, "sticks", {})
                armed = (
                    not any(
                        math.hypot(*pair) >= STICK_NEUTRAL for pair in sticks.values()
                    )
                    if isinstance(sticks, dict)
                    else True
                )
                self.available_models = tuple(
                    preset for preset in CATALOG if self.model_available(preset)
                )
                self.menu_nav = RadialNavigator(armed=armed)
                self.menu_open, self.menu_changed = True, False
                self.notify(self.menu_text())
            else:
                if not self.menu_open:
                    return
                try:
                    if self.session.state == "preview":
                        self.cancel()
                    else:
                        self.apply_menu_selection()
                finally:
                    self.menu_open = False
                self.notify(
                    self.load_status
                    if not self.ready
                    else (
                        dict(SENDING_ITEMS)[
                            "immediate" if self.chat.auto_send else "review"
                        ]
                        if self.menu_nav.section == "sending"
                        else "Ready · " + self.destination.label
                    )
                    + " · "
                    + LANGUAGE_NAMES.get(
                        self.language or "auto", self.language or "Automatic"
                    )
                )
            return
        if self.menu_open:
            if self.session.state != "idle":
                return
            if (
                action
                in (
                    "next_channel",
                    "previous_channel",
                    "next_language",
                    "previous_language",
                )
                and down
            ):
                self.menu_nav.cycle(
                    -1 if action.startswith("previous") else 1,
                    self.menu_view()["items"],
                )
                self.notify(self.menu_text())
            elif action in ("record", "confirm") and down:
                if self.menu_nav.enter():
                    self.notify(self.menu_text())
            return
        if action == "reply":
            action = "talk_reply"
        if action.startswith("talk_"):
            if down and self.session.state == "idle":
                self.destination = Destination(action[5:], self.chat.recipient)
            action = "record"
        if action == "confirm" and not down and self.session.state == "preview":
            self.defer("send" if self.draft_ready else "draft")
        elif action == "record":
            if not down and self.ignore_record_release:
                self.ignore_record_release = False
                return
            if self.menu_open:
                return
            if self.session.state == "preview" and not down:
                self.defer("send" if self.draft_ready else "draft")
            elif self.session.state == "idle" and down:
                if not self.chat.auto_send and self.local_review_paused():
                    self.notify(LOCAL_REVIEW_PAUSED)
                    return
                if self.busy:
                    self.notify(
                        "Finishing the cancelled operation. Try again in a moment."
                    )
                    return
                self.session.begin(self.destination, self.current_target())
                self.record_language = self.language
                try:
                    self.last_audio = None
                    self.show_transcript("")
                    self.transcription_seconds = None
                    self.recorder.start()
                except Exception:
                    self.session.cancel()
                    raise
                self.abort.clear()
                self.draft_ready = False
                self.notify("Recording · " + self.destination.label)
            elif self.session.state == "recording":
                if (self.chat.mode == "hold" and not down) or (
                    self.chat.mode == "toggle" and down
                ):
                    self.ignore_record_release = self.chat.mode == "toggle"
                    self.stop_recording()

    def stop_recording(self):
        token = self.session.stop()
        if token is None:
            return
        try:
            audio = self.recorder.stop()
        except Exception:
            self.session.cancel()
            raise
        if self.practice:
            self.last_audio = audio
        self.notify("Transcribing · " + self.session.destination.label)

        def transcribe():
            self.transcriber.settings = replace(
                self.speech, language=self.record_language
            )
            started = time.monotonic()
            try:
                return self.transcriber.transcribe(audio)
            finally:
                self.transcription_seconds = time.monotonic() - started

        self._start_worker("transcribed", token, transcribe)

    def _deliver(self, action):
        if action == "draft" and self.local_review_paused():
            # Also catch already queued drafts before any style command or
            # native input. Never turn an expected review into a network send.
            self.session.cancel()
            self.draft_ready = False
            self.show_transcript("")
            raise ValueError(LOCAL_REVIEW_PAUSED)
        target = self.current_target()
        if target is None or target != self.session.target:
            raise ValueError("Return to the same WoW window and press Talk again.")
        token = self.session.generation
        activity = None if self.practice else getattr(self.hub, "activity", None)
        activity_version = activity.button_version if activity is not None else None

        def guard():
            return (
                (activity is None or activity.text_ready(activity_version))
                and not self.abort.is_set()
                and not self.closed
                and not self.hub.physical_modifiers
                and self.current_target() == target
            )

        def deliver(operation, *args):
            if self.practice:
                return
            # Do not submit preparatory commands or change the game's settings.
            getattr(self.delivery, operation)(*args)

        if action == "draft":
            if self.session.state != "preview":
                return
            payload = self.session.destination.message(self.session.text)
            self.session.state = "drafting"
            self._start_worker(
                "drafted",
                token,
                lambda: deliver("draft", payload, guard),
            )
        elif action == "send":
            payload = self.session.destination.message(self.session.text)
            self.session.prepare(target)
            self._start_worker(
                "sent",
                token,
                lambda: deliver("send", guard, payload),
            )
        elif action == "clear":
            if self.session.state != "clearing":
                return
            self._start_worker(
                "cleared",
                token,
                lambda: None if self.practice else self.delivery.cancel(guard),
            )

    def cancel(self):
        if self.session.state == "clearing":
            return
        self.abort.set()
        self.deferred = None
        state, target = self.session.state, self.session.target
        if state == "recording":
            self.recorder.stop()
        self.session.cancel()
        self.show_transcript("")
        if (
            not self.practice
            and self.draft_ready
            and not self.busy
            and target == self.current_target()
        ):
            self.abort.clear()
            self.session.state = "clearing"
            self.session.target = target
            # B/Escape may be held: do not inject Ctrl+Shift until released.
            self.defer("clear")
        self.draft_ready = False
        self.notify(
            "Ready for another voice test."
            if self.practice
            else (
                "Release the cancel button to discard the draft."
                if self.session.state == "clearing"
                else "Cancelled. Clear any partial draft in WoW if typing was interrupted."
            )
        )

    def close(self):
        if self.closed:
            return
        if self.camera_lock is not None:
            self.camera_lock.close()
        self.closed = True
        self.last_audio = None
        self.abort.set()
        close_recorder = getattr(self.recorder, "close", None)
        if close_recorder:
            close_recorder()
        elif self.session.state == "recording":
            self.recorder.stop()
        self.session.cancel()
        if self.worker:
            self.worker.join(timeout=2)
        if self.own_hub:
            self.hub.close()
        if not self.busy:
            if self.delivery is not None:
                self.delivery.close()
            if self.foreground is not None:
                self.foreground.close()
