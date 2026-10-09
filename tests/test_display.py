import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_display import overlay_display


class DisplayTests(unittest.TestCase):
    def route(self, identities):
        def connect(name):
            d = Mock()
            d.intern_atom.return_value = 42
            value = identities.get(name)
            d.screen.return_value.root.get_full_property.return_value = (
                None if value is None else SimpleNamespace(value=[value])
            )
            return d

        with (
            patch("thumbtalk_display.platform.system", return_value="Linux"),
            patch.dict(os.environ, {"DISPLAY": ":8"}),
            patch.dict(
                sys.modules,
                {
                    "Xlib": SimpleNamespace(Xatom=SimpleNamespace(CARDINAL=6)),
                    "Xlib.display": SimpleNamespace(Display=connect),
                },
            ),
            patch(
                "thumbtalk_display.Path.glob",
                return_value=[Path("/tmp/.X11-unix/X0"), Path("/tmp/.X11-unix/X5")],
            ),
        ):
            result = overlay_display()
            self.assertEqual(os.environ["DISPLAY"], ":8")
            return result

    def test_uses_verified_gamescope_root_without_changing_game_display(self):
        self.assertEqual(self.route({":8": 1, ":0": None, ":5": 0}), ":5")

    def test_desktop_and_single_display_keep_original_display(self):
        self.assertEqual(self.route({":8": None}), ":8")
        self.assertEqual(self.route({":8": 0}), ":8")

    def test_missing_root_does_not_guess_another_display(self):
        self.assertEqual(self.route({":8": 1}), ":8")

    def test_windows_and_mac_do_not_route_x11(self):
        for system in ("Windows", "Darwin"):
            with patch("thumbtalk_display.platform.system", return_value=system):
                self.assertIsNone(overlay_display())
