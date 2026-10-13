import ctypes
import threading
import unittest
from unittest.mock import Mock, patch

import input_controller as controller
from automation import HookEngine
from models import Action, ActionType, Combo


class RapidClickTest(unittest.TestCase):
    def test_native_layout_buttons_no_movement_and_zero_interval(self):
        self.assertEqual(ctypes.sizeof(controller._Input), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        for button, flags in controller._CLICK_FLAGS.items():
            stopped = threading.Event()

            def send(count, inputs, size):
                self.assertEqual(count, 2)
                self.assertEqual(size, ctypes.sizeof(controller._Input))
                self.assertEqual(tuple(i.mi.dwFlags for i in inputs), flags)
                self.assertTrue(all(i.type == 0 and i.mi.dx == 0 and i.mi.dy == 0 for i in inputs))
                stopped.set()
                return 2

            rate = Mock()
            with patch.object(controller, "_send_input", side_effect=send) as native, \
                    patch.object(controller.pyautogui, "failSafeCheck") as safe, \
                    patch.object(controller, "_interruptible_sleep") as sleep:
                controller.mouse_rapid_click(button, 1, 0, 0, stopped, on_rate=rate)
            native.assert_called_once()
            safe.assert_called_once()
            sleep.assert_not_called()
            self.assertEqual(rate.call_args.args[0], 1)

    def test_cancelled_or_failsafe_never_sends(self):
        stopped = threading.Event()
        stopped.set()
        with patch.object(controller, "_send_input") as send, \
                patch.object(controller.pyautogui, "failSafeCheck", side_effect=controller.pyautogui.FailSafeException):
            controller.mouse_rapid_click(stop_event=stopped)
            stopped.clear()
            with self.assertRaises(controller.pyautogui.FailSafeException):
                controller.mouse_rapid_click(stop_event=stopped)
            send.assert_not_called()

    def test_partial_insertion_releases_and_failed_click_is_not_counted(self):
        for inserted in (0, 1):
            with patch.object(controller, "_send_input", return_value=inserted) as send, \
                    patch.object(controller.pyautogui, "failSafeCheck"), \
                    patch.object(controller, "release_mouse") as release:
                rate = Mock()
                with self.assertRaisesRegex(OSError, "SendInput"):
                    controller.mouse_rapid_click(on_rate=rate)
                self.assertEqual(release.call_count, inserted)
                send.assert_called_once()
                rate.assert_not_called()

    def test_rate_windows_and_final_remainder(self):
        clock = [0.0]

        def send(*args):
            clock[0] += 0.4
            return 2

        rates = []
        with patch.object(controller.time, "perf_counter", side_effect=lambda: clock[0]), \
                patch.object(controller.pyautogui, "failSafeCheck"), \
                patch.object(controller, "_send_input", side_effect=send):
            controller.mouse_rapid_click(duration=1.9, interval_min=0, interval_max=0,
                                         on_rate=lambda count, elapsed: rates.append((count, elapsed)))
        self.assertEqual([count for count, _ in rates], [3, 2])
        self.assertAlmostEqual(sum(elapsed for _, elapsed in rates), 2)

    def test_positive_interval_is_preserved_and_interruptible(self):
        stopped, sent = threading.Event(), threading.Event()
        with patch.object(controller.pyautogui, "failSafeCheck"), \
                patch.object(controller, "_send_input", side_effect=lambda *args: (sent.set(), 2)[1]) as send:
            worker = threading.Thread(target=controller.mouse_rapid_click,
                                      kwargs=dict(duration=10, interval_min=5, interval_max=5,
                                                  stop_event=stopped))
            worker.start()
            try:
                self.assertTrue(sent.wait(1))
                stopped.set()
                worker.join(1)
                self.assertFalse(worker.is_alive())
                send.assert_called_once()
            finally:
                stopped.set()
                worker.join(1)

    def test_validation_and_zero_duration(self):
        with patch.object(controller, "_send_input") as send:
            for kwargs in (dict(button="invalid"), dict(duration=float("nan")),
                           dict(interval_min=-1), dict(interval_min=2, interval_max=1)):
                with self.assertRaises(ValueError):
                    controller.mouse_rapid_click(**kwargs)
            controller.mouse_rapid_click(duration=0)
            send.assert_not_called()

    def test_both_engine_callers_report_rates(self):
        engine = HookEngine()
        messages = []
        engine._on_log = lambda *args: messages.append(args)
        combo = Combo(action=Action(action_type=ActionType.RAPID_CLICK))
        with patch.object(controller, "mouse_rapid_click") as rapid:
            for execute in (engine._execute_action_once_unlocked, engine._repeat_action):
                engine._last_logs.clear()
                execute(combo, threading.Event())
                rapid.call_args.kwargs["on_rate"](100, 2)
                self.assertIn("50.0 次/秒", messages[-1][1])
                self.assertIn("非遊戲接收量", messages[-1][1])


if __name__ == "__main__":
    unittest.main()
