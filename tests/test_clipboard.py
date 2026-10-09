"""Clipboard protocol failures must not poison the next explicit attempt."""

import json
import queue
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_clipboard import (
    ClipboardLease,
    watch_clipboard_loop,
    RESPONSE_TIMEOUT_SECONDS,
)


class ClipboardProtocolTests(unittest.TestCase):
    def lease(self, responses):
        lease = ClipboardLease.__new__(ClipboardLease)
        lease.closed = False
        lease.lock = threading.Lock()
        lease.responses = queue.Queue()
        replies = iter(responses)

        def write(line):
            request = json.loads(line)
            reply = next(replies)
            lease.responses.put({"id": request["id"], **reply})

        lease.process = Mock(stdin=Mock(write=write), poll=Mock(return_value=None))
        return lease

    def test_rejected_read_preserves_connection_for_next_explicit_lease(self):
        lease = self.lease(
            [{"error": "Paste was not read"}, {"ok": True}, {"ok": True}]
        )
        with self.assertRaisesRegex(RuntimeError, "Paste was not read"):
            lease.wait_for_read("old")
        self.assertFalse(lease.closed)
        lease.restore("old")
        self.assertTrue(lease.acquire("s another message"))
        lease.process.stdin.close.assert_not_called()

    def test_dead_pipe_closes_but_cleanup_does_not_mask_failure(self):
        lease = self.lease([])
        lease.process.stdin.write = Mock(side_effect=BrokenPipeError())
        with self.assertRaisesRegex(RuntimeError, "stopped responding"):
            lease.acquire("s test")
        self.assertTrue(lease.closed)
        lease.restore("old")
        lease.process.stdin.write.assert_called_once()
        lease.process.stdin.close.assert_called_once()

    def test_out_of_order_response_closes_the_connection(self):
        lease = self.lease([{"id": "wrong", "ok": True}])
        with self.assertRaisesRegex(RuntimeError, "response was lost"):
            lease.acquire("s test")
        self.assertTrue(lease.closed)


class ClipboardContainmentTests(unittest.TestCase):
    def test_stalled_worker_is_terminated_and_reaped_on_response_timeout(self):
        lease = ClipboardProtocolTests().lease([])
        lease.process.stdin.write = Mock()
        lease.responses = Mock(get=Mock(side_effect=queue.Empty))
        with self.assertRaisesRegex(RuntimeError, "no automatic retry"):
            lease.acquire("s test")
        lease.responses.get.assert_called_once_with(timeout=RESPONSE_TIMEOUT_SECONDS)
        lease.process.terminate.assert_called_once_with()
        lease.process.wait.assert_called_once_with(timeout=1)
        self.assertTrue(lease.closed)
        lease.restore("old")
        self.assertEqual(lease.process.stdin.write.call_count, 1)

    def test_worker_ignoring_terminate_is_killed_and_reaped(self):
        lease = ClipboardProtocolTests().lease([])
        lease.process.wait.side_effect = [subprocess.TimeoutExpired("worker", 1), 0]
        lease.close(force=True)
        lease.process.terminate.assert_called_once_with()
        lease.process.kill.assert_called_once_with()
        self.assertEqual(lease.process.wait.call_count, 2)

    def test_cleanup_timeout_does_not_replace_original_transfer_error(self):
        lease = ClipboardProtocolTests().lease([])
        lease.process.stdin.write = Mock(side_effect=BrokenPipeError())
        lease.process.wait.side_effect = subprocess.TimeoutExpired("worker", 1)
        with self.assertRaisesRegex(RuntimeError, "stopped responding"):
            lease.acquire("s test")
        lease.process.kill.assert_called_once_with()

    def test_graceful_close_keeps_restored_clipboard_owner_alive(self):
        lease = ClipboardProtocolTests().lease([])
        lease.close()
        lease.process.stdin.close.assert_called_once_with()
        lease.process.terminate.assert_not_called()
        lease.process.kill.assert_not_called()

    def test_watchdog_does_not_kill_an_event_loop_with_recent_heartbeat(self):
        stopped = Mock(wait=Mock(side_effect=[False, False, True]))
        terminate = Mock()
        watch_clipboard_loop([100.0], stopped, clock=lambda: 100.5, terminate=terminate)
        terminate.assert_not_called()

    def test_watchdog_bounds_a_real_stalled_process_without_qt_or_game(self):
        # A process that never updates its GUI heartbeat must exit independently
        # of stdin/Qt. This tests actual process teardown, not a mocked kill.
        script = (
            "import threading,time; from thumbtalk_clipboard import watch_clipboard_loop; "
            "threading.Thread(target=watch_clipboard_loop, "
            "args=([time.monotonic()],threading.Event()), "
            "kwargs={'timeout':0.15,'interval':0.02},daemon=True).start(); "
            "time.sleep(30)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1] / "helper",
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 70, result.stderr.decode())
