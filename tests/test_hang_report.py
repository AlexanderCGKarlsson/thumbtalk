"""Bounded process diagnostics and one-run launcher restoration."""

import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "packaging" / "diagnostics"


def load(name):
    spec = importlib.util.spec_from_file_location(name, DIRECTORY / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


arm = load("arm")
collector = load("collect")


@unittest.skipUnless(os.name == "posix", "POSIX diagnostic launcher and file modes")
class HangReportTests(unittest.TestCase):
    def test_arming_and_undo_preserve_exact_launcher_and_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp)
            launcher = prefix / "launch-with-thumbtalk.sh"
            original = b"#!/bin/bash\nprintf original\n"
            launcher.write_bytes(original)
            launcher.chmod(0o751)
            arm.arm(prefix, DIRECTORY)
            with self.assertRaisesRegex(ValueError, "already armed"):
                arm.arm(prefix, DIRECTORY)
            self.assertTrue(arm.disarm(prefix))
            self.assertEqual(launcher.read_bytes(), original)
            self.assertEqual(stat.S_IMODE(launcher.stat().st_mode), 0o751)
            self.assertFalse(arm.disarm(prefix))

    def test_disarm_does_not_overwrite_a_newly_changed_launcher(self):
        with tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp)
            launcher = prefix / "launch-with-thumbtalk.sh"
            launcher.write_text("original")
            arm.arm(prefix, DIRECTORY)
            launcher.write_text("updated externally")
            with self.assertRaisesRegex(ValueError, "changed"):
                arm.disarm(prefix)
            self.assertEqual(launcher.read_text(), "updated externally")
            self.assertEqual(
                (prefix / "launch-with-thumbtalk.sh.before-diagnostics").read_text(),
                "original",
            )

    def test_launch_restores_original_and_preserves_arguments_and_exit_code(self):
        with tempfile.TemporaryDirectory(prefix="thumbtalk space ") as temp:
            prefix = Path(temp)
            launcher = prefix / "launch-with-thumbtalk.sh"
            original = (
                b'#!/bin/bash\nprintf "%s\\n" "$@" > "$TEST_ARG_OUTPUT"\nexit 7\n'
            )
            launcher.write_bytes(original)
            launcher.chmod(0o751)
            arm.arm(prefix, DIRECTORY)
            # Stub only the sampler so this test never starts a background process.
            (prefix / "hang-diagnostics/collect.py").write_text("raise SystemExit(0)\n")
            output = prefix / "arguments.txt"
            args = ["space here", "literal $(command)", "semi;colon", 'a"quote']
            env = dict(
                os.environ,
                XDG_STATE_HOME=str(prefix / "state"),
                TEST_ARG_OUTPUT=str(output),
            )
            result = subprocess.run(
                [str(launcher), *args], env=env, capture_output=True, timeout=5
            )
            self.assertEqual(result.returncode, 7, result.stderr.decode())
            self.assertEqual(output.read_text().splitlines(), args)
            self.assertEqual(launcher.read_bytes(), original)
            self.assertFalse(
                (prefix / "launch-with-thumbtalk.sh.before-diagnostics").exists()
            )

    def test_snapshot_classifies_wine_without_recording_arguments_or_other_apps(self):
        with tempfile.TemporaryDirectory() as temp:
            proc = Path(temp)
            main = proc / "123"
            task = main / "task/123"
            task.mkdir(parents=True)
            fields = ["S", "1"] + ["0"] * 18
            fields[11], fields[12], fields[19] = "4", "5", "123456"
            data = "123 (name with (parentheses)) " + " ".join(fields)
            (main / "comm").write_text("MainThread\n")
            (main / "cmdline").write_bytes(
                b"/wine64\0C:\\Games\\WowB.exe\0secret-argument\0"
            )
            (main / "stat").write_text(data)
            (task / "stat").write_text(data)
            (task / "wchan").write_text("futex_wait_queue")
            unrelated = proc / "456"
            unrelated.mkdir()
            (unrelated / "comm").write_text("browser")
            report = collector.snapshot(proc)
            self.assertEqual(len(report), 1)
            self.assertEqual(report[0]["kind"], "wowb.exe")
            self.assertEqual(report[0]["threads"][0]["user_ticks"], 4)
            encoded = json.dumps(report)
            self.assertNotIn("secret", encoded)
            self.assertNotIn("Games", encoded)
            self.assertNotIn("browser", encoded)

    @unittest.skipUnless(sys.platform == "linux", "Linux /proc integration")
    def test_live_sampler_records_a_controlled_process_and_exits_within_limit(self):
        script = "from pathlib import Path; import time; Path('/proc/self/comm').write_text('WowB.exe'); print('ready',flush=True); time.sleep(10)"
        process = subprocess.Popen(
            [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True
        )
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            with tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / "report.jsonl"
                result = subprocess.run(
                    [
                        sys.executable,
                        str(DIRECTORY / "collect.py"),
                        "--parent",
                        str(os.getpid()),
                        "--output",
                        str(output),
                        "--seconds",
                        "1",
                    ],
                    timeout=6,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                rows = [json.loads(line) for line in output.read_text().splitlines()]
                self.assertTrue(
                    any(
                        item["pid"] == process.pid
                        for row in rows
                        for item in row.get("processes", [])
                    )
                )
                self.assertEqual(rows[-1], {"finished": "time limit"})
                self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        finally:
            process.terminate()
            process.wait(timeout=3)
            process.stdout.close()
