"""Run source launchers with a fake uv executable, without downloading anything."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SourceLauncherTests(unittest.TestCase):
    def setUp(self):
        self.windows = os.name == "nt"
        shell = (
            (shutil.which("pwsh") or shutil.which("powershell"))
            if self.windows
            else shutil.which("sh")
        )
        if not shell:
            self.skipTest("Native script interpreter is unavailable")
        self.shell = shell
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.checkout = self.root / "checkout with spaces"
        (self.checkout / "helper").mkdir(parents=True)
        self.caller = self.root / "caller directory"
        self.caller.mkdir()
        self.bin = self.root / "tools"
        self.bin.mkdir()
        self.capture = self.root / "invocation.json"
        self.launcher = self.checkout / (
            "run-helper.ps1" if self.windows else "run-helper.sh"
        )
        shutil.copy2(ROOT / self.launcher.name, self.launcher)
        if not self.windows:
            os.symlink(shutil.which("dirname"), self.bin / "dirname")
        self.environment = {
            **os.environ,
            "PATH": str(self.bin),
            "LAUNCHER_CAPTURE": str(self.capture),
            "LAUNCHER_EXIT": "19",
        }

    def fake_uv(self):
        recorder = self.root / "fake_uv.py"
        recorder.write_text(
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "Path(os.environ['LAUNCHER_CAPTURE']).write_text(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd()}))\n"
            "raise SystemExit(int(os.environ['LAUNCHER_EXIT']))\n",
            encoding="utf-8",
        )
        if self.windows:
            (self.bin / "uv.cmd").write_text(
                f'@echo off\n"{sys.executable}" "{recorder}" %*\nexit /b %errorlevel%\n',
                encoding="utf-8",
            )
        else:
            stub = self.bin / "uv"
            stub.write_text(
                f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(recorder))} "$@"\n'
            )
            stub.chmod(0o755)

    def run_launcher(self, arguments=()):
        command = [self.shell]
        if self.windows:
            command += ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
        return subprocess.run(
            [*command, str(self.launcher), *arguments],
            cwd=self.caller,
            env=self.environment,
            text=True,
            capture_output=True,
            timeout=20,
        )

    def test_forwards_arguments_keeps_caller_directory_and_returns_exit_status(self):
        self.fake_uv()
        arguments = ["--config", "settings with spaces.json", "--language", "nb-NO"]
        result = self.run_launcher(arguments)
        self.assertEqual(result.returncode, 19, result.stderr)
        invocation = json.loads(self.capture.read_text())
        forwarded = invocation["args"]
        self.assertEqual(forwarded[:2], ["run", "--project"])
        self.assertEqual(
            Path(forwarded[2]).resolve(), (self.checkout / "helper").resolve()
        )
        self.assertEqual(forwarded[3:7], ["--frozen", "--python", "3.12", "python"])
        self.assertEqual(
            Path(forwarded[7]).resolve(),
            (self.checkout / "helper/thumbtalk_cli.py").resolve(),
        )
        self.assertEqual(forwarded[8:], arguments)
        self.assertEqual(Path(invocation["cwd"]).resolve(), self.caller.resolve())

    def test_missing_uv_explains_requirement_without_bootstrapping(self):
        result = self.run_launcher()
        self.assertEqual(result.returncode, 127, result.stderr)
        self.assertIn("requires uv", result.stderr)
        self.assertFalse(self.capture.exists())
        self.assertFalse((self.checkout / "helper/.venv").exists())
