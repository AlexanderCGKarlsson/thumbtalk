"""Controller and remapped keyboard input, with no exclusive device grabs."""

from __future__ import annotations

import os
import sys
import queue
import time
from thumbtalk_chat import MODIFIERS
from thumbtalk_activity import InputActivity

BUTTONS = {
    0: "PAD1",
    1: "PAD2",
    2: "PAD3",
    3: "PAD4",
    4: "PADSOCIAL",
    5: "PADSYSTEM",
    6: "PADFORWARD",
    7: "PADLSTICK",
    8: "PADRSTICK",
    9: "PADLSHOULDER",
    10: "PADRSHOULDER",
    11: "PADDUP",
    12: "PADDDOWN",
    13: "PADDLEFT",
    14: "PADDRIGHT",
    15: "PAD5",
    16: "PADPADDLE1",
    17: "PADPADDLE2",
    18: "PADPADDLE3",
    19: "PADPADDLE4",
    20: "PADBACK",
}


class InputHub:
    def __init__(self, keyboard=True):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
        # SDL 2.30.9+ hides Steam's virtual pad from directly launched desktop
        # apps unless they opt in. This affects only ThumbTalk's SDL instance.
        os.environ.setdefault("SDL_GAMECONTROLLER_ALLOW_STEAM_VIRTUAL_GAMEPAD", "1")
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        import pygame
        from pygame._sdl2 import controller

        self.pygame, self.controller = pygame, controller
        pygame.display.init()
        pygame.joystick.init()
        controller.init()
        self.devices, self.axis = {}, {}
        self.controller_error = ""
        self.sticks = {}
        self.events = queue.SimpleQueue()
        self.activity = InputActivity()
        self.listener = None
        self.mouse_listener = None
        self.mouse_error = ""
        self.error = ""
        self.physical_modifiers = set()
        self.injected_until = {}
        self.closed = False
        for index in range(pygame.joystick.get_count()):
            self._open(index)
        if keyboard:
            try:
                from pynput.keyboard import Listener

                options = {}
                if sys.platform == "win32":
                    options["win32_event_filter"] = self._windows_event
                self.listener = Listener(
                    **options,
                    on_press=lambda k, injected=False: self._key(k, True, injected),
                    on_release=lambda k, injected=False: self._key(k, False, injected),
                )
                self.listener.start()
                # Some pynput backends do not expose a trust flag.
                if getattr(self.listener, "IS_TRUSTED", True) is False:
                    self.error = "Keyboard access is unavailable; check system input permissions."
            except Exception as error:
                self.error = f"Keyboard bindings unavailable: {error}"

            try:
                from pynput.mouse import Listener as MouseListener

                self.mouse_listener = MouseListener(on_click=self._mouse)
                self.mouse_listener.start()
                if getattr(self.mouse_listener, "IS_TRUSTED", True) is False:
                    self.mouse_error = (
                        "Mouse access is unavailable; check system input permissions."
                    )
            except Exception as error:
                self.mouse_error = f"Mouse bindings unavailable: {error}"

    @staticmethod
    def _windows_event(message, data):
        from thumbtalk_windows_text import INPUT_MARKER

        # False filters callbacks in this listener only. It does not suppress
        # delivery to the game, and untagged Steam/hardware input is unaffected.
        return not (data.flags & 0x10 and data.dwExtraInfo == INPUT_MARKER)

    def _mouse(self, x, y, button, down, injected=False):
        # Steam's remapped clicks can be injected, and must still be accepted.
        name = getattr(button, "name", "")
        name = {"button8": "x1", "button9": "x2"}.get(name, name)
        if not self.closed and name in {"left", "middle", "right", "x1", "x2"}:
            self.activity.button("mouse", 0, name, down)
            self.events.put(("mouse", 0, name, down))

    def expect_injected(self, key):
        """Ignore our own echo without rejecting Steam Input remapped keys."""
        name = getattr(key, "name", None) or getattr(key, "char", None) or str(key)
        now = time.monotonic()
        self.injected_until = {
            k: expiry for k, expiry in self.injected_until.items() if expiry > now
        }
        name = name.lower()
        if name in {"ctrl", "ctrl_l", "shift", "shift_l"}:
            base = name.removesuffix("_l")
            self.injected_until[base] = now + 0.25
            self.injected_until[base + "_l"] = now + 0.25
        else:
            self.injected_until[name] = now + (0.25 if sys.platform == "linux" else 1)

    def _key(self, key, down, injected=False):
        name = getattr(key, "name", None) or getattr(key, "char", None)
        if injected and sys.platform == "win32" and self.listener:
            # Windows reports Ctrl+V as a control character (\x16). Compare
            # its canonical key with our announced paste, just as learning does.
            canonical = self.listener.canonical(key)
            name = (
                getattr(canonical, "name", None)
                or getattr(canonical, "char", None)
                or name
            )
        if (
            (injected or sys.platform == "linux")
            and name
            and self.injected_until.get(name.lower(), 0) > time.monotonic()
        ):
            return
        # XRecord sees ydotool's virtual keyboard as ordinary input. Ignore
        # only the exact keys announced for our short native chord, for 250 ms.
        # This filters this listener, not delivery to WoW; it is not input locking.
        raw = getattr(key, "name", None)
        if raw in (
            "ctrl_l",
            "ctrl_r",
            "shift_l",
            "shift_r",
            "alt_l",
            "alt_r",
            "cmd_l",
            "cmd_r",
            "ctrl",
            "shift",
            "alt",
            "cmd",
        ):
            self.physical_modifiers.add(
                raw
            ) if down else self.physical_modifiers.discard(raw)
        if self.listener:
            key = self.listener.canonical(key)
        # X11 canonicalizes function/arrow keys to a virtual key code with no
        # name or character. Keep the original symbolic name in that case.
        name = getattr(key, "name", None) or getattr(key, "char", None) or raw
        if name:
            name = name.lower()
            for mod in MODIFIERS:
                if name in (mod + "_l", mod + "_r"):
                    name = mod
            # Windows supplies a reliable injected marker. Linux's expected
            # chord echoes have already been filtered above; this listener
            # does not suppress any physical events sent to the game.
            if sys.platform == "win32":
                self.activity.button("key", 0, name, down)
            self.events.put(("key", 0, name, down))

    def _open(self, index):
        try:
            if self.controller.is_controller(index):
                device = self.controller.Controller(index)
                ident = device.as_joystick().get_instance_id()
                mapped = True
            else:
                device = self.pygame.joystick.Joystick(index)
                ident = device.get_instance_id()
                mapped = False
            if ident not in self.devices:
                self.devices[ident] = (device, mapped)
        except self.pygame.error as error:
            self.controller_error = str(error)

    def controller_status(self):
        if self.devices:
            names = []
            for device, mapped in self.devices.values():
                try:
                    name = device.name if mapped else device.get_name()
                    names.append(str(name))
                except self.pygame.error:
                    continue
            return "Controller detected: " + (
                ", ".join(dict.fromkeys(names)) or "gamepad"
            )
        if self.controller_error:
            return "Controller could not be opened: " + self.controller_error
        return "No controller detected. Keyboard and mouse buttons are still available."

    def poll(self):
        if self.closed:
            return []
        pg = self.pygame
        result = []
        for event in pg.event.get():
            # Track both sticks and triggers even though only the right stick
            # drives the radial. Tracking does not consume gameplay input.
            if event.type == pg.CONTROLLERAXISMOTION:
                self.activity.axis(
                    "pad", event.instance_id, event.axis, event.value / 32767.0
                )
            elif (
                event.type == pg.JOYHATMOTION
                and event.instance_id in self.devices
                and not self.devices[event.instance_id][1]
            ):
                self.activity.button(
                    "raw", event.instance_id, "hat" + str(event.hat), any(event.value)
                )
            if event.type == pg.JOYDEVICEADDED:
                self._open(event.device_index)
            elif event.type == pg.JOYDEVICEREMOVED:
                self.activity.disconnect(event.instance_id)
                self.sticks.pop(event.instance_id, None)
                item = self.devices.pop(event.instance_id, None)
                if item:
                    device, mapped = item
                    device.quit()
                self.axis = {
                    k: v for k, v in self.axis.items() if k[0] != event.instance_id
                }
                result.extend(
                    [
                        ("disconnect", event.instance_id, "pad", False),
                        ("disconnect", event.instance_id, "raw", False),
                    ]
                )
            elif event.type in (pg.CONTROLLERBUTTONDOWN, pg.CONTROLLERBUTTONUP):
                name = BUTTONS.get(event.button)
                if name:
                    result.append(
                        (
                            "pad",
                            event.instance_id,
                            name,
                            event.type == pg.CONTROLLERBUTTONDOWN,
                        )
                    )
            elif event.type == pg.CONTROLLERAXISMOTION and event.axis in (2, 3):
                pair = list(self.sticks.get(event.instance_id, (0.0, 0.0)))
                pair[event.axis - 2] = max(-1.0, min(1.0, event.value / 32767.0))
                self.sticks[event.instance_id] = tuple(pair)
                result.append(("stick", event.instance_id, "right", tuple(pair)))
            elif event.type == pg.CONTROLLERAXISMOTION and event.axis in (4, 5):
                identity = (event.instance_id, event.axis)
                active = self.axis.get(identity, False)
                # SDL controller values are signed integers, unlike joystick axis events.
                pressed = event.value > (12000 if active else 16000)
                if pressed != active:
                    result.append(
                        (
                            "pad",
                            event.instance_id,
                            "PADLTRIGGER" if event.axis == 4 else "PADRTRIGGER",
                            pressed,
                        )
                    )
                    self.axis[identity] = pressed
            elif event.type in (pg.JOYBUTTONDOWN, pg.JOYBUTTONUP):
                # Mapped pads also emit joystick events. Only expose raw for unmapped pads.
                if (
                    event.instance_id in self.devices
                    and not self.devices[event.instance_id][1]
                ):
                    result.append(
                        (
                            "raw",
                            event.instance_id,
                            str(event.button),
                            event.type == pg.JOYBUTTONDOWN,
                        )
                    )
        for kind, device, button, down in result:
            if kind in ("pad", "raw"):
                self.activity.button(kind, device, button, down)
        while not self.events.empty():
            result.append(self.events.get())
        return result

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.listener:
            self.listener.stop()
            self.listener.join(timeout=1)
        if self.mouse_listener:
            self.mouse_listener.stop()
            self.mouse_listener.join(timeout=1)
        for device, mapped in self.devices.values():
            device.quit()
        self.devices.clear()
        self.controller.quit()
        self.pygame.joystick.quit()
        self.pygame.display.quit()


class Learner:
    """Capture the whole chord on release; ignore keyboard auto-repeat."""

    def __init__(self):
        self.held, self.peak = {}, {}
        self.started = time.monotonic()

    def feed(self, kind, device, button, down):
        if kind not in ("key", "mouse", "pad", "raw"):
            return None
        source = (kind, device)
        held = self.held.setdefault(source, set())
        if down:
            held.add(button)
            self.peak[source] = set(held)
            return None
        peak = self.peak.get(source, set())
        held.discard(button)
        if peak and (kind != "key" or peak - MODIFIERS):
            return (
                kind
                + ":"
                + "+".join(sorted(peak, key=lambda p: (p not in MODIFIERS, p)))
            )
        return None
