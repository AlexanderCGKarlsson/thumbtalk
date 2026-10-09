"""Listener compatibility checks without a display or input permissions."""

import sys
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_input import InputHub, Learner


class ListenerTests(unittest.TestCase):
    def make_hub(self, listener):
        pygame = SimpleNamespace(display=Mock(), joystick=Mock())
        pygame.joystick.get_count.return_value = 0
        with patch.dict(
            sys.modules,
            {
                "pygame": pygame,
                "pygame._sdl2": SimpleNamespace(controller=Mock()),
                "pynput": SimpleNamespace(),
                "pynput.mouse": SimpleNamespace(Listener=lambda **kwargs: Mock()),
                "pynput.keyboard": SimpleNamespace(Listener=lambda **kwargs: listener),
            },
        ):
            hub = InputHub()
        self.addCleanup(hub.close)
        return hub

    def test_windows_filter_ignores_only_own_tagged_injection(self):
        from thumbtalk_windows_text import INPUT_MARKER

        self.assertFalse(
            InputHub._windows_event(
                0, SimpleNamespace(flags=0x10, dwExtraInfo=INPUT_MARKER)
            )
        )
        self.assertTrue(
            InputHub._windows_event(
                0, SimpleNamespace(flags=0x10, dwExtraInfo=INPUT_MARKER ^ 1)
            )
        )
        self.assertTrue(
            InputHub._windows_event(
                0, SimpleNamespace(flags=0, dwExtraInfo=INPUT_MARKER)
            )
        )

    def test_steam_virtual_controller_is_enabled_before_sdl_initializes(self):
        pygame = SimpleNamespace(display=Mock(), joystick=Mock())
        pygame.joystick.get_count.return_value = 0
        observed = []
        pygame.joystick.init.side_effect = lambda: observed.append(
            os.environ.get("SDL_GAMECONTROLLER_ALLOW_STEAM_VIRTUAL_GAMEPAD")
        )
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.dict(
                sys.modules,
                {"pygame": pygame, "pygame._sdl2": SimpleNamespace(controller=Mock())},
            ),
        ):
            hub = InputHub(keyboard=False)
            try:
                self.assertEqual(observed, ["1"])
                self.assertIn("No controller detected", hub.controller_status())
                self.assertEqual(hub.error, "")
            finally:
                hub.close()

    def test_poll_after_close_does_not_touch_shutdown_video_system(self):
        listener = SimpleNamespace(start=Mock(), stop=Mock(), join=Mock())
        hub = self.make_hub(listener)
        hub.close()
        self.assertEqual(hub.poll(), [])

    def test_listener_without_trust_attribute_can_learn_steam_remap(self):
        listener = SimpleNamespace(
            start=Mock(), stop=Mock(), join=Mock(), canonical=lambda k: k
        )
        hub = self.make_hub(listener)
        self.assertEqual(hub.error, "")
        hub._key(SimpleNamespace(name="f8"), True, injected=True)
        hub._key(SimpleNamespace(name="f8"), False, injected=True)
        learner = Learner()
        self.assertIsNone(learner.feed(*hub.events.get()))
        self.assertEqual(learner.feed(*hub.events.get()), "key:f8")

    def test_x11_function_and_arrow_keys_survive_canonicalization(self):
        listener = SimpleNamespace(
            start=Mock(),
            stop=Mock(),
            join=Mock(),
            canonical=lambda key: SimpleNamespace(vk=119, char=None),
        )
        hub = self.make_hub(listener)
        for name in ("f8", "f7", "left", "right", "up", "down"):
            hub._key(SimpleNamespace(name=name), True, injected=True)
            hub._key(SimpleNamespace(name=name), False, injected=True)
            self.assertEqual(hub.events.get_nowait(), ("key", 0, name, True))
            self.assertEqual(hub.events.get_nowait(), ("key", 0, name, False))

    def test_physical_key_matching_recent_text_still_interrupts_delivery(self):
        listener = SimpleNamespace(
            start=Mock(), stop=Mock(), join=Mock(), canonical=lambda key: key
        )
        hub = self.make_hub(listener)
        key = SimpleNamespace(name=None, char="w")
        hub.expect_injected("w")
        version = hub.activity.version
        with patch("thumbtalk_input.sys.platform", "win32"):
            hub._key(key, True, injected=True)
            self.assertEqual(hub.activity.version, version)
            hub._key(key, True, injected=False)
            self.assertGreater(hub.activity.version, version)
            self.assertFalse(hub.activity.quiet())
            hub._key(key, False, injected=False)
        self.assertFalse(hub.activity.held)

    def test_windows_ctrl_v_echo_is_canonicalized_without_hiding_player_input(self):
        listener = SimpleNamespace(
            start=Mock(),
            stop=Mock(),
            join=Mock(),
            canonical=lambda key: SimpleNamespace(char="v"),
        )
        hub = self.make_hub(listener)
        key = SimpleNamespace(name=None, char="\x16")
        hub.expect_injected("v")
        version = hub.activity.button_version
        with patch("thumbtalk_input.sys.platform", "win32"):
            hub._key(key, True, injected=True)
            hub._key(key, False, injected=True)
            self.assertEqual(hub.activity.button_version, version)
            self.assertTrue(hub.events.empty())
            hub._key(key, True, injected=False)
            self.assertGreater(hub.activity.button_version, version)
            hub._key(key, False, injected=False)
            # An unannounced remap from Steam is still accepted.
            hub.injected_until.clear()
            previous = hub.activity.button_version
            hub._key(key, True, injected=True)
            self.assertGreater(hub.activity.button_version, previous)
            hub._key(key, False, injected=True)
        self.assertFalse(hub.activity.held)

    def test_explicit_permission_denial_is_preserved(self):
        listener = SimpleNamespace(
            start=Mock(), stop=Mock(), join=Mock(), IS_TRUSTED=False
        )
        self.assertIn("permissions", self.make_hub(listener).error)

    def test_listener_start_failure_is_reported(self):
        listener = SimpleNamespace(
            start=Mock(side_effect=RuntimeError("no display")), stop=Mock(), join=Mock()
        )
        self.assertIn("no display", self.make_hub(listener).error)


class CombinationTests(unittest.TestCase):
    def test_guide_button_and_trigger_are_learned_as_one_chord(self):
        learner = Learner()
        self.assertIsNone(learner.feed("pad", 1, "PADSYSTEM", True))
        self.assertIsNone(learner.feed("pad", 1, "PADLTRIGGER", True))
        self.assertEqual(
            learner.feed("pad", 1, "PADLTRIGGER", False), "pad:PADLTRIGGER+PADSYSTEM"
        )

    def test_keyboard_modifier_combination(self):
        learner = Learner()
        self.assertIsNone(learner.feed("key", 0, "ctrl", True))
        self.assertIsNone(learner.feed("key", 0, "f8", True))
        self.assertEqual(learner.feed("key", 0, "f8", False), "key:ctrl+f8")


class MouseBindingTests(unittest.TestCase):
    def test_mouse_remap_learns_and_matches_hold_release(self):
        import queue
        from thumbtalk_chat import (
            BindingMatcher,
            canonical_binding,
            DEFAULT_BINDINGS,
            validate_bindings,
        )

        hub = InputHub.__new__(InputHub)
        from thumbtalk_activity import InputActivity

        hub.activity = InputActivity()
        hub.closed = False
        hub.events = queue.SimpleQueue()
        for native, canonical in [
            ("middle", "middle"),
            ("button8", "x1"),  # Linux/X11 Mouse 4
            ("button9", "x2"),  # Linux/X11 Mouse 5
            ("x1", "x1"),  # Windows Mouse 4
            ("x2", "x2"),  # Windows Mouse 5
        ]:
            for action in ("record", "menu"):
                with self.subTest(native=native, action=action):
                    hub._mouse(0, 0, SimpleNamespace(name=native), True, injected=True)
                    hub._mouse(0, 0, SimpleNamespace(name=native), False, injected=True)
                    learner = Learner()
                    settings = dict(DEFAULT_BINDINGS, **{action: "mouse:" + canonical})
                    validate_bindings(settings)
                    matcher = BindingMatcher(settings)
                    down, up = hub.events.get(), hub.events.get()
                    self.assertEqual(matcher.feed(*down), [(action, True)])
                    self.assertEqual(matcher.feed(*down), [])
                    self.assertEqual(matcher.feed(*up), [(action, False)])
                    self.assertIsNone(learner.feed(*down))
                    self.assertEqual(
                        canonical_binding(learner.feed(*up)), "mouse:" + canonical
                    )
        hub._mouse(0, 0, SimpleNamespace(name="scroll_up"), True)
        self.assertTrue(hub.events.empty())

    def test_mouse_choices_reject_conflicting_and_invalid_signals(self):
        from thumbtalk_chat import DEFAULT_BINDINGS, parse_binding, validate_bindings

        with self.assertRaises(ValueError):
            parse_binding("mouse:wheel")
        with self.assertRaises(ValueError):
            validate_bindings(
                dict(DEFAULT_BINDINGS, record="mouse:middle", confirm="mouse:middle")
            )
