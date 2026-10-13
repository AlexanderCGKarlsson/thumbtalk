"""Diagnostics stay bounded and cannot turn a log failure into a send retry."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_diagnostics import DeliveryLog


class DeliveryLogTests(unittest.TestCase):
    @patch("builtins.print")
    def test_log_survives_restart_and_rotates_one_previous_file(self, _):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings" / "delivery.log"
            DeliveryLog(path)("insert requested")
            DeliveryLog(path)("submit requested")
            self.assertIn("insert requested", path.read_text())
            self.assertIn("submit requested", path.read_text())
            path.write_text("x" * (256 * 1024))
            DeliveryLog(path)("next attempt")
            self.assertIn("next attempt", path.read_text())
            self.assertEqual(
                path.with_suffix(".previous.log").stat().st_size, 256 * 1024
            )

    @patch("builtins.print")
    def test_unwritable_log_does_not_interrupt_delivery(self, _):
        with tempfile.TemporaryDirectory() as directory:
            # Opening a directory as a file fails on all supported platforms.
            DeliveryLog(Path(directory))("delivery failed")
