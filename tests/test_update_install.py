"""Download integrity, cancellation, package boundaries and installer handoff."""

import hashlib
import io
import json
import shutil
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_updates import REPOSITORY, UpdatePackage, release_package
from thumbtalk_update_install import (
    DownloadedUpdate,
    ReleaseRedirects,
    UpdateCancelled,
    download_update,
    extract_linux_update,
    installation_directory,
    installer_environment,
    install_update,
)


def package(data=b"installer fixture", name="thumbtalk-windows-setup.exe"):
    digest = hashlib.sha256(data).hexdigest()
    url = f"https://github.com/{REPOSITORY}/releases/download/v0.8.3/{name}"
    return UpdatePackage(name, url, url + ".sha256", len(data), digest)


class PackageTests(unittest.TestCase):
    def release(self):
        return {
            "tag_name": "v0.8.3",
            "assets": [
                {
                    "name": name,
                    "size": 123,
                    "browser_download_url": "https://untrusted.example/file",
                }
                for name in (
                    "thumbtalk-windows-setup.exe",
                    "thumbtalk-windows-setup.exe.sha256",
                    "ThumbTalk-0.8.3-build4-linux.zip",
                    "ThumbTalk-0.8.3-build4-linux.zip.sha256",
                )
            ],
        }

    def test_platform_asset_selection_uses_our_repo_and_exact_version(self):
        for system, name in [
            ("Windows", "thumbtalk-windows-setup.exe"),
            ("Linux", "ThumbTalk-0.8.3-build4-linux.zip"),
        ]:
            p = release_package(self.release(), system=system, machine="x86_64")
            self.assertEqual(p.name, name)
            self.assertEqual(
                p.url,
                f"https://github.com/{REPOSITORY}/releases/download/v0.8.3/{name}",
            )
        self.assertIsNone(
            release_package(self.release(), system="Darwin", machine="x86_64")
        )
        self.assertIsNone(
            release_package(self.release(), system="Linux", machine="aarch64")
        )

    def test_missing_duplicate_or_invalid_assets_do_not_enable_installation(self):
        for change in ("checksum", "duplicate", "size", "digest"):
            r = self.release()
            if change == "checksum":
                r["assets"].pop(1)
            elif change == "duplicate":
                r["assets"].append(r["assets"][0])
            elif change == "size":
                r["assets"][0]["size"] = 2**40
            else:
                r["assets"][0]["digest"] = "bad"
            self.assertIsNone(release_package(r, system="Windows", machine="AMD64"))

    def test_installed_marker_required_and_portable_or_source_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            exe = root / "app/thumbtalk.exe"
            exe.parent.mkdir()
            exe.touch()
            self.assertIsNone(
                installation_directory(executable=exe, frozen=True, system="Windows")
            )
            (root / ".thumbtalk-install").touch()
            self.assertEqual(
                installation_directory(executable=exe, frozen=True, system="Windows"),
                root,
            )
            self.assertIsNone(
                installation_directory(executable=exe, frozen=False, system="Windows")
            )
            self.assertIsNone(
                installation_directory(
                    executable=root / "thumbtalk.exe", frozen=True, system="Windows"
                )
            )


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = b"payload" * 100000
        self.package = package(self.data)
        self.cancel = threading.Event()

    def opener(self, data=None, checksum=None):
        return Mock(
            side_effect=[
                io.BytesIO(
                    checksum
                    if checksum is not None
                    else (
                        self.package.digest + "  " + self.package.name + "\r\n"
                    ).encode()
                ),
                io.BytesIO(self.data if data is None else data),
            ]
        )

    def fetch(self, **kwargs):
        return download_update(
            self.package,
            self.cancel,
            kwargs.pop("progress", Mock()),
            temp_parent=self.root,
            **kwargs,
        )

    def test_download_progress_checksum_and_windows_line_endings(self):
        progress = Mock()
        result = self.fetch(opener=self.opener(), progress=progress)
        self.assertEqual(result.path.read_bytes(), self.data)
        self.assertEqual(result.sha256, self.package.digest)
        self.assertEqual(progress.call_args.args, (len(self.data), len(self.data)))
        self.assertFalse(list(result.directory.glob("*.part")))

    def test_corrupt_short_or_oversized_download_is_removed(self):
        for data in (b"bad", self.data + b"x", b"x" * len(self.data)):
            with self.assertRaises(ValueError):
                self.fetch(opener=self.opener(data=data))
            self.assertEqual(list(self.root.iterdir()), [])

    def test_checksum_name_and_digest_must_match_metadata(self):
        for checksum in (
            b"bad",
            b"x" * 4097,
            (self.package.digest + " wrong.exe").encode(),
            ("0" * 64 + " " + self.package.name).encode(),
        ):
            request = self.opener(checksum=checksum)
            with self.assertRaises(ValueError):
                self.fetch(opener=request)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(list(self.root.iterdir()), [])

    def test_network_failure_and_cancellation_keep_no_partial_update(self):
        with self.assertRaises(OSError):
            self.fetch(opener=Mock(side_effect=OSError("offline")))
        self.assertEqual(list(self.root.iterdir()), [])

        def cancel_progress(done, total):
            if done:
                self.cancel.set()

        with self.assertRaises(UpdateCancelled):
            self.fetch(opener=self.opener(), progress=cancel_progress)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_disallowed_redirect_is_rejected_before_request(self):
        from urllib.request import Request

        redirect = ReleaseRedirects()
        for url in (
            "http://github.com/file",
            "https://example.com/file",
            "file:///tmp/file",
        ):
            with self.assertRaises(ValueError):
                redirect.redirect_request(
                    Request(self.package.url), None, 302, "", {}, url
                )


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prefix = self.root / "installed app"
        self.prefix.mkdir()
        (self.prefix / ".thumbtalk-install").touch()

    def downloaded(self, data, name="thumbtalk-windows-setup.exe"):
        p = package(data, name)
        folder = self.root / "download"
        folder.mkdir(exist_ok=True)
        path = folder / name
        path.write_bytes(data)
        return DownloadedUpdate(p, path, p.digest)

    def linux_zip(self, extra=None):
        data = io.BytesIO()
        folder = "ThumbTalk-0.8.3-build4-linux"
        with zipfile.ZipFile(data, "w") as archive:
            for name in (
                "thumbtalk-linux-x86_64.tar.gz",
                "thumbtalk-linux-x86_64.tar.gz.sha256",
                "install.sh",
                "install-local.sh",
                "Install-ThumbTalk.desktop",
                "VERSION.txt",
            ):
                archive.writestr(folder + "/" + name, "fixture")
            if extra:
                archive.writestr(*extra)
        return self.downloaded(data.getvalue(), folder + ".zip")

    def test_windows_handoff_targets_existing_install_without_forced_shutdown(self):
        download = self.downloaded(b"fixture")
        with patch("thumbtalk_update_install.launch_windows_installer") as launch:
            self.assertEqual(
                install_update(download, self.prefix, system="Windows"), "handoff"
            )
        args = launch.call_args.args[0]
        self.assertEqual(args[0], str(download.path))
        self.assertIn("/DIR=" + str(self.prefix), args)
        self.assertIn("/NOFORCECLOSEAPPLICATIONS", args)
        self.assertNotIn("/VERYSILENT", args)

    def test_replaced_download_is_not_executed(self):
        download = self.downloaded(b"fixture")
        download.path.write_bytes(b"replaced")
        with patch("thumbtalk_update_install.launch_windows_installer") as launch:
            with self.assertRaises(ValueError):
                install_update(download, self.prefix, system="Windows")
        launch.assert_not_called()

    def test_outer_zip_rejects_traversal_links_or_extra_files(self):
        link = zipfile.ZipInfo("ThumbTalk-0.8.3-build4-linux/evil")
        link.external_attr = 0o120777 << 16
        for extra in (("../outside", "evil"), ("/outside", "evil"), (link, "target")):
            with self.assertRaises(ValueError):
                extract_linux_update(self.linux_zip(extra))
        self.assertFalse((self.root / "outside").exists())

    def test_linux_uses_matching_offline_installer_and_preserves_failure_log(self):
        download = self.linux_zip()
        with patch(
            "thumbtalk_update_install.subprocess.run", return_value=Mock(returncode=7)
        ) as run:
            with self.assertRaisesRegex(RuntimeError, "installer.log"):
                install_update(download, self.prefix, system="Linux")
        args = run.call_args.args[0]
        self.assertEqual(args[0], "/bin/bash")
        self.assertEqual(args[-3:], ["--prefix", str(self.prefix), "--no-launch"])
        self.assertTrue(Path(args[1]).is_file())
        self.assertTrue((download.directory / "installer.log").exists())

    def test_installer_environment_does_not_use_bundled_libraries(self):
        with patch.dict(
            os.environ,
            {
                "LD_LIBRARY_PATH": "/bundle",
                "LD_LIBRARY_PATH_ORIG": "/system",
                "QT_PLUGIN_PATH": "/bundle/qt",
                "FONTCONFIG_FILE": "/bundle/fonts",
            },
        ):
            env = installer_environment()
        self.assertEqual(env["LD_LIBRARY_PATH"], "/system")
        self.assertNotIn("QT_PLUGIN_PATH", env)
        self.assertNotIn("FONTCONFIG_FILE", env)
        self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")


@unittest.skipUnless(
    os.name == "posix" and shutil.which("bash"), "Bash installer integration"
)
class OfflineUpdateTests(unittest.TestCase):
    def test_verified_offline_update_runs_real_installer_and_preserves_settings(self):
        import tarfile

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = root / "ThumbTalk"
            bundle.mkdir()
            app = bundle / "thumbtalk"
            app.write_text("#!/bin/sh\nexit 0\n")
            app.chmod(0o755)
            repo = Path(__file__).resolve().parents[1]
            shutil.copy2(repo / "packaging/launch-with-thumbtalk.sh", bundle)
            (bundle / "BUILD.json").write_text(json.dumps({"version": "0.8.3"}))
            tar = root / "thumbtalk-linux-x86_64.tar.gz"
            with tarfile.open(tar, "w:gz") as archive:
                archive.add(bundle, arcname="ThumbTalk")
            outer = root / "ThumbTalk-0.8.3-build4-linux.zip"
            folder = outer.stem
            with zipfile.ZipFile(outer, "w") as archive:
                archive.write(tar, folder + "/" + tar.name)
                archive.writestr(
                    folder + "/" + tar.name + ".sha256",
                    hashlib.sha256(tar.read_bytes()).hexdigest()
                    + "  "
                    + tar.name
                    + "\n",
                )
                for name, source in [
                    ("install.sh", "install.sh"),
                    ("install-local.sh", "packaging/install-local.sh"),
                    (
                        "Install-ThumbTalk.desktop",
                        "packaging/Install-ThumbTalk-Local.desktop",
                    ),
                ]:
                    archive.write(repo / source, folder + "/" + name)
                archive.writestr(folder + "/VERSION.txt", "0.8.3 build 4")
            prefix = root / "existing custom install"
            (prefix / "app").mkdir(parents=True)
            (prefix / ".thumbtalk-install").touch()
            (prefix / "app/old.txt").write_text("old app")
            (prefix / "settings.json").write_text("saved preferences")
            p = package(outer.read_bytes(), outer.name)
            downloaded = DownloadedUpdate(p, outer, p.digest)
            with patch.dict(
                os.environ,
                {
                    "XDG_DATA_HOME": str(root / "data"),
                    "XDG_STATE_HOME": str(root / "state"),
                },
            ):
                self.assertEqual(
                    install_update(downloaded, prefix, system="Linux"), "installed"
                )
            self.assertEqual(
                (prefix / "settings.json").read_text(), "saved preferences"
            )
            self.assertEqual(
                json.loads((prefix / "app/BUILD.json").read_text())["version"], "0.8.3"
            )
            self.assertFalse((prefix / "app/old.txt").exists())
            self.assertFalse((prefix / "app.previous").exists())
