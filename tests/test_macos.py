import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_platform import Foreground, Target
from thumbtalk_setup import wow_search_roots


class MacTests(unittest.TestCase):
    def test_foreground_guard_uses_wow_process_and_its_visible_window(self):
        app = Mock()
        app.executableURL.return_value.lastPathComponent.return_value = (
            "World of Warcraft"
        )
        app.processIdentifier.return_value = 42
        workspace = Mock()
        workspace.frontmostApplication.return_value = app
        quartz = SimpleNamespace(
            kCGWindowListOptionOnScreenOnly=1,
            kCGWindowListExcludeDesktopElements=2,
            kCGNullWindowID=0,
            kCGWindowOwnerPID="pid",
            kCGWindowLayer="layer",
            kCGWindowNumber="window",
            CGWindowListCopyWindowInfo=Mock(
                return_value=[
                    {"pid": 5, "layer": 0, "window": 1},
                    {"pid": 42, "layer": 0, "window": 9},
                ]
            ),
        )
        cocoa = SimpleNamespace(NSWorkspace=Mock())
        cocoa.NSWorkspace.sharedWorkspace.return_value = workspace
        with (
            patch("thumbtalk_platform.platform.system", return_value="Darwin"),
            patch.dict(sys.modules, {"AppKit": cocoa, "Quartz": quartz}),
        ):
            foreground = Foreground()
            self.assertEqual(foreground.current(), Target("Darwin", 9, 42))
            app.executableURL.return_value.lastPathComponent.return_value = "Safari"
            self.assertIsNone(foreground.current())
            app.executableURL.return_value.lastPathComponent.return_value = (
                "World of Warcraft"
            )
            quartz.CGWindowListCopyWindowInfo.return_value = []
            self.assertIsNone(foreground.current())

    def test_macos_discovery_includes_applications(self):
        roots = wow_search_roots(home=Path("/Users/test"), system="Darwin", environ={})
        self.assertIn(Path("/Applications/World of Warcraft"), roots)
        self.assertIn(Path("/Users/test/Applications/World of Warcraft"), roots)
