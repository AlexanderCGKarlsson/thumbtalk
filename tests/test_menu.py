import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_menu import RadialNavigator, ROOT_ITEMS


class RadialNavigationTests(unittest.TestCase):
    def test_dwell_enters_category_then_requires_center_before_child(self):
        nav = RadialNavigator()
        nav.point(7, 0, -1, ROOT_ITEMS, 1)
        self.assertFalse(nav.tick(1.1))
        self.assertTrue(nav.tick(1.19))
        self.assertEqual(nav.section, "language")
        children = [("en", "English"), ("sv", "Swedish")]
        nav.point(7, 0, -1, children, 2)
        self.assertIsNone(nav.selection())
        nav.point(7, 0, 0, children, 3)
        nav.point(7, 0, 1, children, 4)
        self.assertEqual(nav.selection(), ("language", "sv"))

    def test_drift_and_deflected_stick_at_open_cannot_choose(self):
        nav = RadialNavigator(armed=False)
        nav.point(1, 0, -1, ROOT_ITEMS, 1)
        self.assertFalse(nav.tick(3))
        nav.point(1, 0, 0, ROOT_ITEMS, 4)
        nav.point(1, 0.1, -0.1, ROOT_ITEMS, 5)
        self.assertIsNone(nav.hover)
        self.assertFalse(nav.tick(7))

    def test_back_discards_hover_and_retained_direction(self):
        nav = RadialNavigator()
        nav.cycle(1, ROOT_ITEMS)
        nav.enter()
        nav.cycle(1, [("sv", "Swedish")])
        nav.back()
        self.assertEqual(nav.section, "root")
        self.assertIsNone(nav.selection())
        self.assertFalse(nav.armed)

    def test_other_controller_does_not_change_selected_slice(self):
        nav = RadialNavigator()
        nav.point(1, 0, -1, ROOT_ITEMS, 1)
        nav.point(2, 0, 1, ROOT_ITEMS, 1.1)
        self.assertEqual(nav.hover, "language")

    def test_dpad_does_not_auto_enter_while_held(self):
        nav = RadialNavigator()
        nav.cycle(1, ROOT_ITEMS)
        self.assertFalse(nav.tick(10))
        self.assertTrue(nav.enter())

    def test_extension_branches_support_nested_back_navigation(self):
        nav = RadialNavigator(branches={"root": ("settings",), "settings": ("models",)})
        nav.cycle(1, [("settings", "Settings")])
        nav.enter()
        nav.cycle(1, [("models", "Models")])
        nav.enter()
        self.assertEqual(nav.section, "models")
        nav.back()
        self.assertEqual(nav.section, "settings")
        nav.back()
        self.assertEqual(nav.section, "root")

    def test_pages_wrap_and_clear_previous_hover(self):
        nav = RadialNavigator()
        nav.hover = "channel"
        nav.enter()
        nav.hover = "party"
        self.assertTrue(nav.change_page(-1, 2))
        self.assertEqual(nav.page, 1)
        self.assertIsNone(nav.selection())
        self.assertFalse(nav.armed)
        nav.change_page(1, 2)
        self.assertEqual(nav.page, 0)
        nav.change_page(1, 2)
        nav.back()
        self.assertEqual(nav.page, 0)

    def test_directions_match_clockwise_visual_positions(self):
        nav = RadialNavigator()
        items = [(str(i), str(i)) for i in range(6)]
        for index in range(6):
            angle = math.radians(90 - index * 60)
            nav.point(1, math.cos(angle), -math.sin(angle), items, index)
            self.assertEqual(nav.hover, str(index))

    def test_small_cardinal_and_diagonal_movements_select(self):
        nav = RadialNavigator()
        items = [(str(i), str(i)) for i in range(8)]
        for index in range(8):
            angle = math.radians(90 - index * 45)
            nav.point(1, 0.30 * math.cos(angle), -0.30 * math.sin(angle), items, index)
            self.assertEqual(nav.hover, str(index))

    def test_partial_return_does_not_select_child_until_centered(self):
        nav = RadialNavigator()
        nav.point(1, 0, 0.30, ROOT_ITEMS, 1)
        self.assertTrue(nav.tick(1.19))
        children = [("say", "Say"), ("party", "Party")]
        nav.point(1, 0, 0.22, children, 2)
        nav.point(1, 0, 0.30, children, 3)
        self.assertIsNone(nav.selection())
        nav.point(1, 0, 0.16, children, 4)
        nav.point(1, 0, 0.30, children, 5)
        self.assertEqual(nav.selection(), ("model", "party"))

    def test_small_drift_cannot_enter_category(self):
        nav = RadialNavigator()
        for value in (0.16, 0.20, 0.27):
            nav.point(1, 0, value, ROOT_ITEMS, 1)
            self.assertFalse(nav.tick(3))
            self.assertIsNone(nav.hover)
