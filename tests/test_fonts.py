import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper"))
from thumbtalk_fonts import configure_bundled_fonts  # noqa: E402


class BundledFontsTests(unittest.TestCase):
    def test_frozen_linux_uses_private_rules_without_changing_host_file(self):
        with tempfile.TemporaryDirectory(prefix="font test ") as temp:
            root = Path(temp)
            config = root / "fontconfig/fonts.conf"
            config.parent.mkdir()
            config.write_bytes((ROOT / "packaging/fonts.conf").read_bytes())
            host = root / "host.conf"
            host.write_text("untouched host rules")
            with (
                patch.object(sys, "platform", "linux"),
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "_MEIPASS", temp, create=True),
                patch.dict(
                    os.environ,
                    {
                        "FONTCONFIG_FILE": str(host),
                        "FONTCONFIG_PATH": "/host/rules",
                        "FONTCONFIG_SYSROOT": "/steam/runtime",
                        "XDG_DATA_HOME": "/user/fonts",
                    },
                ),
            ):
                configure_bundled_fonts()
                self.assertEqual(os.environ["FONTCONFIG_FILE"], str(config))
                self.assertEqual(os.environ["FONTCONFIG_PATH"], str(config.parent))
                self.assertNotIn("FONTCONFIG_SYSROOT", os.environ)
                self.assertEqual(os.environ["XDG_DATA_HOME"], "/user/fonts")
                self.assertEqual(host.read_text(), "untouched host rules")

    def test_source_windows_and_macos_keep_native_configuration(self):
        for platform, frozen in (("linux", False), ("win32", True), ("darwin", True)):
            with (
                self.subTest(platform=platform),
                patch.object(sys, "platform", platform),
                patch.object(sys, "frozen", frozen, create=True),
                patch.dict(
                    os.environ,
                    {"FONTCONFIG_FILE": "custom", "FONTCONFIG_SYSROOT": "custom-root"},
                ),
            ):
                before = dict(os.environ)
                configure_bundled_fonts()
                self.assertEqual(dict(os.environ), before)

    def test_missing_bundle_config_is_reported_without_hiding_errors(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(sys, "platform", "linux"),
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "_MEIPASS", temp, create=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "font configuration is missing"):
                configure_bundled_fonts()

    def test_config_reads_system_and_user_fonts_without_host_rule_includes(self):
        tree = ElementTree.parse(ROOT / "packaging/fonts.conf")
        self.assertFalse(tree.findall(".//include"))
        self.assertIn("/usr/share/fonts", [d.text for d in tree.findall("dir")])
        self.assertEqual(tree.find("dir[@prefix='xdg']").text, "fonts")
        self.assertEqual(
            tree.find("cachedir[@prefix='xdg']").text, "thumbtalk/fontconfig"
        )
