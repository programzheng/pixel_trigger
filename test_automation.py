import threading
import unittest
from unittest.mock import patch

import numpy as np

import input_controller
from automation import HookEngine
from models import Combo, Profile


class ResponsiveAutomationTest(unittest.TestCase):
    def test_slow_combo_does_not_block_fast_combo_or_duplicate_work(self):
        slow = Combo(image_path="slow.png")
        fast = Combo(image_path="fast.png")
        entered, release, detected, progressed = (threading.Event() for _ in range(4))
        engine = HookEngine(on_cycle_complete=lambda count: progressed.set() if count >= 3 else None)
        engine.set_profile(Profile(combos=[slow, fast], scan_interval=0.01))
        slow_calls = []

        def process(combo, frame, region):
            if combo.id == slow.id:
                slow_calls.append(combo.id)
                entered.set()
                release.wait(2)
            else:
                detected.set()

        with patch.object(engine._capture, "grab_frame", return_value=np.zeros((2, 2))), \
                patch.object(engine, "_process_combo", side_effect=process):
            try:
                engine.start()
                self.assertTrue(entered.wait(1))
                self.assertTrue(detected.wait(1))
                self.assertTrue(progressed.wait(1))
                self.assertFalse(release.is_set())
                self.assertEqual(slow_calls, [slow.id])
            finally:
                engine._stop_event.set()
                release.set()
                engine.stop()
        self.assertFalse(engine.running)
        self.assertIsNone(engine._thread)

    def test_delayed_input_is_cancelled_and_held_input_is_released(self):
        for action, method in ((input_controller.mouse_click, "click"),
                               (input_controller.key_press, "press")):
            stopped = threading.Event()
            timer = threading.Timer(0.01, stopped.set)
            with patch.object(input_controller.pyautogui, method) as send:
                timer.start()
                try:
                    action("left" if method == "click" else "space", 0.5, 0.5, stopped)
                finally:
                    timer.join()
                send.assert_not_called()
        for action, down, up in ((input_controller.mouse_hold, "mouseDown", "mouseUp"),
                                 (input_controller.key_hold, "keyDown", "keyUp")):
            with patch.object(input_controller.pyautogui, down), \
                    patch.object(input_controller.pyautogui, up) as released, \
                    patch.object(input_controller, "_interruptible_sleep", side_effect=RuntimeError), \
                    self.assertRaises(RuntimeError):
                action("left" if down == "mouseDown" else "space")
            released.assert_called_once()
