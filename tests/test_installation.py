import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper"))
from thumbtalk_setup import (  # noqa: E402
    discover_wow,
    install_addon,
    wow_directory,
    wow_directories,
    wow_label,
    wow_search_roots,
)


class AddonTests(unittest.TestCase):
    def test_install_update_and_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            wow = root / "_classic_beta_"
            (wow / "Interface").mkdir(parents=True)
            target = install_addon(wow, root / "backups")
            self.assertTrue((target / "ThumbTalk.toc").is_file())
            (target / "ThumbTalk.lua").write_text("previous version")
            install_addon(wow, root / "backups")
            backups = list((root / "backups").glob("addon-*/ThumbTalk/ThumbTalk.lua"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), "previous version")

    def test_unrelated_addon_directory_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            wow = root / "_classic_beta_"
            target = wow / "Interface/AddOns/ThumbTalk"
            target.mkdir(parents=True)
            marker = target / "keep.txt"
            marker.write_text("keep")
            with self.assertRaises(ValueError):
                install_addon(wow, root / "backups")
            self.assertEqual(marker.read_text(), "keep")


class WowDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()

    def game(self, relative):
        game = self.home / relative / "_classic_beta_"
        (game / "Interface").mkdir(parents=True)
        return game

    def test_linux_steam_proton_flatpak_and_bottles(self):
        games = [
            self.game(path)
            for path in (
                ".local/share/Steam/steamapps/compatdata/123/pfx/drive_c/Program Files (x86)/World of Warcraft",
                ".var/app/com.valvesoftware.Steam/data/Steam/steamapps/compatdata/456/pfx/drive_c/Program Files/World of Warcraft",
                ".var/app/com.usebottles.bottles/data/bottles/bottles/battlenet/drive_c/Program Files/World of Warcraft",
                "Games/battlenet/drive_c/Program Files (x86)/World of Warcraft",
            )
        ]
        roots = wow_search_roots(self.home, "Linux", {})
        self.assertEqual(set(discover_wow(roots=roots)), set(games))

    def test_external_steam_library_from_vdf(self):
        game = self.game(
            "SD Card/SteamLibrary/steamapps/compatdata/7/pfx/drive_c/World of Warcraft"
        )
        steam = self.home / ".local/share/Steam/steamapps"
        steam.mkdir(parents=True)
        library = self.home / "SD Card/SteamLibrary"
        (steam / "libraryfolders.vdf").write_text(
            '"libraryfolders" { "1" { "path" "' + str(library) + '" } }'
        )
        roots = wow_search_roots(self.home, "Linux", {})
        self.assertIn(game, discover_wow(roots=roots))

    def test_windows_program_files(self):
        game = self.game("Program Files (x86)/World of Warcraft")
        roots = wow_search_roots(
            self.home,
            "Windows",
            {"ProgramFiles(x86)": str(self.home / "Program Files (x86)")},
        )
        self.assertEqual(discover_wow(roots=roots), [game])

    def test_saved_first_deduplicated_and_no_link_traversal(self):
        first = self.game("Games/World of Warcraft")
        second = self.game("custom/World of Warcraft")
        alias = self.home / "Games alias"
        alias.symlink_to(self.home / "Games", target_is_directory=True)
        (self.home / "Games/loop").symlink_to(self.home, target_is_directory=True)
        result = discover_wow(str(second), roots=[self.home / "Games", alias, second])
        self.assertEqual(result, [second, first])
        self.assertEqual(discover_wow(roots=[self.home / "Games"]), [first])

    def test_unrelated_missing_folder_and_limits(self):
        (self.home / "Games/Another Game/Interface").mkdir(parents=True)
        self.assertEqual(
            discover_wow(roots=[self.home / "Games", self.home / "missing"]), []
        )
        game = self.game("Games/World of Warcraft")
        self.assertEqual(discover_wow(roots=[self.home], max_directories=1), [])
        self.assertEqual(
            discover_wow(str(game), roots=[self.home], max_seconds=0), [game]
        )
        self.assertIsNone(wow_directory(self.home / "Games/WoW"))

    def test_browse_accepts_parent_and_installs_only_beta(self):
        game = self.game("World of Warcraft")
        retail = game.parent / "_retail_/Interface"
        retail.mkdir(parents=True)
        self.assertIsNone(wow_directory(game.parent))
        with self.assertRaises(ValueError):
            install_addon(game.parent, self.home / "backups")
        target = install_addon(game, self.home / "backups")
        self.assertTrue((target / "ThumbTalk.toc").is_file())
        self.assertFalse((retail / "AddOns/ThumbTalk").exists())

    def test_all_clients_under_one_root_are_found_and_named(self):
        root = self.home / "Games/World of Warcraft"
        names = ["_retail_", "_classic_", "_classic_era_", "_classic_beta_"]
        for name in names:
            (root / name / "Interface").mkdir(parents=True)
        expected = [root / name for name in names]
        self.assertEqual(wow_directories(root), expected)
        self.assertEqual(discover_wow(roots=[self.home / "Games"]), expected)
        self.assertEqual(wow_label(expected[0]), "WoW Retail")
        self.assertEqual(wow_label(expected[1]), "WoW Classic")
        self.assertEqual(wow_label(expected[2]), "WoW Classic Era / Hardcore")
        for game in expected:
            target = install_addon(game, self.home / "backups")
            self.assertTrue((target / "ThumbTalk.toc").is_file())

    def test_single_client_parent_is_accepted(self):
        game = self.game("World of Warcraft")
        self.assertEqual(wow_directory(game.parent), game)
        self.assertEqual(
            install_addon(game.parent, self.home / "backups"),
            game / "Interface/AddOns/ThumbTalk",
        )


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "Bash installer")
class LinuxInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.app = self.bundle / "thumbtalk"
        self.app.write_text("#!/bin/sh\nexit 0\n")
        self.app.chmod(0o755)
        shutil.copy2(ROOT / "packaging/launch-with-thumbtalk.sh", self.bundle)
        self.prefix = self.root / "a folder with spaces" / "thumbtalk"
        self.environment = {
            **os.environ,
            "XDG_DATA_HOME": str(self.root / "data"),
            "XDG_STATE_HOME": str(self.root / "state"),
        }

    def install(self, success=True):
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "install.sh"),
                "--bundle",
                str(self.bundle),
                "--prefix",
                str(self.prefix),
                "--no-launch",
            ],
            env=self.environment,
            capture_output=True,
            text=True,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def test_install_and_update_preserve_user_files(self):
        self.install()
        (self.prefix / "preferences.json").write_text("keep")
        self.app.write_text("#!/bin/sh\n# new version\nexit 0\n")
        self.install()
        self.assertIn("new version", (self.prefix / "app/thumbtalk").read_text())
        self.assertEqual((self.prefix / "preferences.json").read_text(), "keep")
        desktop = (self.root / "data/applications/thumbtalk.desktop").read_text()
        self.assertIn(
            'Exec="' + str(self.prefix / "app/thumbtalk") + '" --setup', desktop
        )
        self.assertFalse(list(self.prefix.glob(".install.*")))
        self.assertFalse((self.prefix / "app.previous").exists())

    def test_installer_calls_addon_refresh_and_reports_partial_failure(self):
        calls = self.root / "calls"
        self.environment["CALLS"] = str(calls)
        self.app.write_text(
            '#!/bin/sh\necho "$1" >> "$CALLS"\nif [ "$1" = --update-addons ]; then exit 2; fi\nexit 0\n'
        )
        result = self.install()
        self.assertIn("--update-addons", calls.read_text().splitlines())
        self.assertIn("Some addons need an update", result.stdout)
        self.assertIn("ThumbTalk installed", result.stdout)

    def test_broken_update_preserves_working_app(self):
        self.install()
        self.app.write_text("#!/bin/sh\nexit 3\n")
        self.install(success=False)
        self.assertEqual(
            subprocess.run([str(self.prefix / "app/thumbtalk")]).returncode, 0
        )

    def test_refuses_unrelated_install_folder(self):
        self.prefix.mkdir(parents=True)
        (self.prefix / "keep.txt").write_text("keep")
        self.install(success=False)
        self.assertEqual((self.prefix / "keep.txt").read_text(), "keep")

    def test_overlapping_install_is_reported_without_failure(self):
        ready = self.root / "ready"
        release = self.root / "release"
        self.environment.update(READY=str(ready), RELEASE=str(release))
        self.app.write_text(
            '#!/bin/sh\ntouch "$READY"\n'
            'while [ ! -f "$RELEASE" ]; do sleep 0.05; done\n'
        )
        process = subprocess.Popen(
            [
                "bash",
                str(ROOT / "install.sh"),
                "--bundle",
                str(self.bundle),
                "--prefix",
                str(self.prefix),
                "--no-launch",
            ],
            env=self.environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "First installer did not reach validation")
            result = self.install()
            self.assertIn("already running", result.stdout)
            self.assertNotIn("not a ThumbTalk installation", result.stderr)
        finally:
            release.touch()
            stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("ThumbTalk installed", stdout)
        self.assertFalse(Path(str(self.prefix) + ".install-lock").exists())
        self.install()

    def test_app_startup_failure_is_not_reported_as_install_failure(self):
        self.app.write_text(
            '#!/bin/sh\nif [ "$1" = --setup ]; then echo "startup failed"; '
            "exit 42; fi\nexit 0\n"
        )
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "install.sh"),
                "--bundle",
                str(self.bundle),
                "--prefix",
                str(self.prefix),
            ],
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ThumbTalk installed", result.stdout)
        self.assertFalse(Path(str(self.prefix) + ".install-lock").exists())
        log = self.root / "state/thumbtalk/setup.log"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if log.exists() and "startup failed" in log.read_text():
                break
            time.sleep(0.01)
        self.assertIn("startup failed", log.read_text())

    def test_failed_install_releases_lock_for_retry(self):
        self.app.write_text("#!/bin/sh\nexit 3\n")
        self.install(success=False)
        self.assertFalse(Path(str(self.prefix) + ".install-lock").exists())
        self.app.write_text("#!/bin/sh\nexit 0\n")
        self.install()

    def test_game_exit_status_and_companion_lifetime(self):
        self.install()
        helper = self.prefix / "app/thumbtalk"
        helper.write_text(
            '#!/bin/sh\ntrap "exit 0" TERM INT\nwhile :; do sleep 0.05; done\n'
        )
        game = self.root / "game.sh"
        output = self.root / "game-argument.txt"
        game.write_text('#!/bin/sh\nprintf "%s" "$1" > "$2"\nsleep 0.1\nexit 7\n')
        game.chmod(0o755)
        result = subprocess.run(
            [
                "bash",
                str(self.prefix / "launch-with-thumbtalk.sh"),
                str(game),
                "argument with spaces",
                str(output),
            ],
            env=self.environment,
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 7)
        self.assertEqual(output.read_text(), "argument with spaces")


if __name__ == "__main__":
    unittest.main()


class FreshClientTests(unittest.TestCase):
    def test_saved_beta_finds_retail_sibling_without_interface(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "World of Warcraft"
            beta = root / "_classic_beta_"
            retail = root / "_retail_"
            (beta / "Interface").mkdir(parents=True)
            retail.mkdir()
            (retail / "Wow.exe").write_bytes(b"executable fixture")
            self.assertEqual(
                discover_wow(str(beta), roots=[], max_seconds=0), [beta, retail]
            )
            self.assertEqual(wow_directories(root), [retail, beta])
            target = install_addon(retail, Path(directory).resolve() / "backups")
            self.assertTrue((target / "ThumbTalk.toc").is_file())

    def test_empty_client_folder_is_not_an_install(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory).resolve() / "_retail_"
            folder.mkdir()
            self.assertEqual(wow_directories(folder), [])
            (folder / "World of Warcraft.app").mkdir()
            self.assertEqual(wow_directories(folder), [folder])
