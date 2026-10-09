import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "helper"))
from thumbtalk_camera import MenuCameraLock
from thumbtalk_chat import ChatSettings, validate_bindings


class CameraTests(unittest.TestCase):
    def test_open_renew_close_and_no_idle_signals(self):
        clock, focused, signal = Mock(return_value=0), Mock(return_value="wow"), Mock()
        lock = MenuCameraLock(focused, signal, clock)
        lock.update(False)
        signal.assert_not_called()
        lock.update(True)
        self.assertTrue(signal.call_args.args[0])
        self.assertTrue(signal.call_args.args[1]())
        clock.return_value = 0.2
        lock.update(True)
        self.assertEqual(signal.call_count, 1)
        clock.return_value = 0.4
        lock.update(True)
        self.assertEqual(signal.call_count, 2)
        lock.update(False)
        self.assertFalse(signal.call_args.args[0])
        lock.update(False)
        self.assertEqual(signal.call_count, 3)

    def test_focus_loss_never_signals_other_app_and_invalidates_pending_guard(self):
        clock, focused, signal = Mock(return_value=0), Mock(return_value="wow"), Mock()
        lock = MenuCameraLock(focused, signal, clock)
        lock.update(True)
        guard = signal.call_args.args[1]
        focused.return_value = None
        self.assertFalse(guard())
        clock.return_value = 0.5
        lock.update(True)
        lock.close()
        self.assertEqual(signal.call_count, 1)
        focused.return_value = "wow"
        lock.update(True)
        self.assertEqual(signal.call_count, 2)
        lock.close()
        self.assertFalse(signal.call_args.args[0])

    def test_release_failure_is_left_to_addon_timeout(self):
        signal = Mock()
        lock = MenuCameraLock(lambda: "wow", signal)
        lock.update(True)
        signal.side_effect = RuntimeError("lost keyboard connection")
        lock.close()
        self.assertIsNone(lock.target)

    def test_camera_signal_keys_cannot_be_assigned_to_user_actions(self):
        for key in ("f11", "ctrl+f12", "f13", "f14"):
            with self.assertRaisesRegex(ValueError, "reserved"):
                validate_bindings({**ChatSettings().bindings, "menu": "key:" + key})
