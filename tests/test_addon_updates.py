"""Installer addon refresh: only existing owned addons, with backups."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_setup import install_addon, remember_addon, update_installed_addons


class AddonUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config = self.root / "settings.json"
        self.config.write_text(json.dumps({"version": 1, "language": "sv"}))

    def client(self, name, base="World of Warcraft", installed=True):
        client = self.root / base / name
        (client / "Interface").mkdir(parents=True)
        if installed:
            install_addon(client, self.root / "backups")
        return client

    def test_updates_legacy_beta_and_retail_but_does_not_install_new_clients(self):
        beta = self.client("_classic_beta_")
        retail = self.client("_retail_")
        untouched = self.client("_classic_", installed=False)
        self.config.write_text(
            json.dumps({"version": 1, "chat": {"wow_dir": str(beta)}})
        )
        for client in (beta, retail):
            (client / "Interface/AddOns/ThumbTalk/ThumbTalk.lua").write_text(
                "old addon"
            )
        report = update_installed_addons(self.config, roots=[])
        self.assertEqual(set(report["updated"]), {str(beta), str(retail)})
        self.assertEqual(report["errors"], [])
        self.assertFalse((untouched / "Interface/AddOns/ThumbTalk").exists())
        backups = list((self.root / "backups").glob("addon-*/ThumbTalk/ThumbTalk.lua"))
        self.assertEqual(len(backups), 2)
        self.assertTrue(all(p.read_text() == "old addon" for p in backups))
        again = update_installed_addons(self.config, roots=[])
        self.assertEqual(again["updated"], [])
        self.assertEqual(set(again["current"]), {str(beta), str(retail)})
        self.assertEqual(len(list((self.root / "backups").iterdir())), 2)

    def test_remembers_multiple_custom_installs_without_changing_preferences(self):
        first = self.client("_retail_", "Custom A")
        second = self.client("_classic_", "Custom B")
        for client in (first, second, first):
            remember_addon(self.config, client)
        data = json.loads(self.config.read_text())
        self.assertEqual(data["language"], "sv")
        self.assertEqual(data["addon_installs"], [str(first), str(second)])
        report = update_installed_addons(self.config, roots=[])
        self.assertEqual(set(report["current"]), {str(first), str(second)})

    def test_rejects_unrelated_metadata_and_linked_addon_files(self):
        first = self.client("_retail_")
        second = self.client("_classic_")
        remember_addon(self.config, first)
        remember_addon(self.config, second)
        toc = first / "Interface/AddOns/ThumbTalk/ThumbTalk.toc"
        toc.write_text("## Title: Someone else's addon\n")
        outside = self.root / "keep.lua"
        outside.write_text("keep")
        linked = second / "Interface/AddOns/ThumbTalk/ThumbTalk.lua"
        linked.unlink()
        linked.symlink_to(outside)
        report = update_installed_addons(self.config, roots=[])
        self.assertEqual(len(report["errors"]), 2)
        self.assertEqual(report["updated"], [])
        self.assertEqual(outside.read_text(), "keep")
        self.assertEqual(toc.read_text(), "## Title: Someone else's addon\n")

    def test_cli_updates_without_initializing_speech_or_input(self):
        from thumbtalk_cli import main

        with (
            patch(
                "sys.argv",
                ["thumbtalk", "--update-addons", "--config", str(self.config)],
            ),
            patch(
                "thumbtalk_setup.update_installed_addons",
                return_value={"updated": ["retail"], "current": [], "errors": []},
            ) as update,
            patch("thumbtalk_cli.resolve_settings") as speech,
        ):
            main()
        update.assert_called_once_with(self.config)
        speech.assert_not_called()
