import threading
import unittest
from unittest.mock import patch

import numpy as np

import input_controller
from automation import HookEngine
from models import Action, ActionType, Combo, Profile, TriggerMode, PositionControl


class ResponsiveAutomationTest(unittest.TestCase):
    def test_sound_is_a_combo_action_with_delay_cancellation_and_rearming(self):
        combo = Combo(image_path="trigger.png", action=Action(action_type=ActionType.PLAY_SOUND))
        engine = HookEngine()
        engine.set_profile(Profile(combos=[combo]))
        frame = np.zeros((60, 60))
        with patch.object(engine._capture, "match_template", side_effect=[
                (False, 0.1, None), (True, 0.9, (30, 30)), (True, 0.9, (30, 30)),
                (False, 0.1, None), (True, 0.9, (30, 30))]), \
                patch.object(engine._capture, "get_match_size", return_value=(50, 40)), \
                patch.object(input_controller.winsound, "PlaySound") as sound:
            for _ in range(5):
                engine._process_combo(combo, frame, None)
            self.assertEqual(sound.call_count, 2)
            combo.action.action_type = ActionType.CLICK
            with patch.object(input_controller, "mouse_click"):
                engine._execute_action_once(combo)
            self.assertEqual(sound.call_count, 2)
            combo.action.action_type = ActionType.PLAY_SOUND
            stopped = threading.Event()
            stopped.set()
            engine._execute_action_once_unlocked(combo, stopped)
            self.assertEqual(sound.call_count, 2)
            stopped.clear()
            combo.trigger = TriggerMode.WHILE_VISIBLE
            with patch.object(engine, "_wait_until_stopped", side_effect=lambda evt: evt.set()):
                engine._continuous_action_loop(combo, stopped)
            self.assertEqual(sound.call_count, 3)
            stopped.clear()
            combo.action.delay_min = combo.action.delay_max = 1
            with patch.object(input_controller, "_interruptible_sleep", return_value=False) as delay:
                engine._execute_action_once_unlocked(combo, stopped)
                delay.assert_called_once_with(1, stopped)
            self.assertEqual(sound.call_count, 3)

    def test_combo_logs_distinguish_modes_and_throttle_repeated_reasons(self):
        records = []
        engine = HookEngine(on_log=lambda cid, message, level: records.append((cid, message)))
        combo = Combo(image_path="gauge.png")
        engine.set_profile(Profile(combos=[combo]))
        with patch("automation.time.monotonic", side_effect=(0, 0.1, 0.2, 1.3)):
            engine._log(combo, "定位失敗", "分數=0.2")
            engine._log(combo, "定位失敗", "分數=0.3")
            engine._log(combo, "箭頭失敗")
            engine._log(combo, "箭頭失敗")
        self.assertEqual(len(records), 3)
        for mode in TriggerMode:
            combo.trigger = mode
            engine._last_logs.clear()
            records.clear()
            stopped = threading.Event()
            if mode.tracks_marker:
                with patch.object(engine._capture, "position_frame", return_value=(None, None)), \
                        patch.object(engine, "_interruptible_sleep_event", side_effect=lambda *a: stopped.set()):
                    engine._position_control_loop(combo, stopped)
                self.assertTrue(any("圖片定位未通過" in message for _, message in records))
            else:
                with patch.object(engine._capture, "match_template", return_value=(False, 0.3, None)):
                    engine._process_combo(combo, np.zeros((60, 60)), None)
                self.assertIn("圖片定位未通過", records[0][1])
            self.assertTrue(all(cid == combo.id and mode.value in message for cid, message in records))
        records.clear()
        combo.trigger = TriggerMode.ON_APPEAR
        combo.image_path = ""
        combo.verify_text = "魚"
        with patch.object(engine._capture, "ocr_verify", return_value=(False, 0.4, "")):
            engine._process_combo(combo, np.zeros((60, 60)), None)
        self.assertIn("OCR 未通過", records[0][1])

    def test_color_band_missing_timeout_and_valid_stop(self):
        for timeout in (0, 150, 400):
            with self.subTest(timeout=timeout):
                settings = PositionControl(region=(0, 0, 60, 60), band_missing_ms=timeout)
                combo = Combo(trigger=TriggerMode.COLOR_BAND, position_control=settings)
                engine = HookEngine()
                engine.set_profile(Profile(combos=[combo]))
                stopped = threading.Event()
                debug, states, events = [], [], []
                engine._on_debug_info = lambda *args: debug.append(args[1])

                def new_thread(*args, **kwargs):
                    from unittest.mock import Mock
                    events.append(kwargs["args"][1])
                    thread = Mock()
                    thread.is_alive.return_value = False
                    return thread

                def tick(*args):
                    states.append(events[-1].is_set())
                    if len(states) == 6:
                        stopped.set()

                # 啟動、首次漏判、容忍期間、超時、恢復、有效停止。
                clock = (1, 1 + timeout / 2000, 1 + timeout / 1000 + 0.01)
                with patch("automation.color_band_state", side_effect=[(True, 1), (False, None),
                        (False, None), (False, None), (True, 1), (False, 0)]), \
                        patch("automation.find_color_marker", return_value=None), \
                        patch.object(engine._capture, "position_frame", return_value=(None, None)), \
                        patch("automation.time.monotonic", side_effect=clock), \
                        patch("automation.threading.Thread", side_effect=new_thread), \
                        patch.object(engine, "_interruptible_sleep_event", side_effect=tick):
                    engine._position_control_loop(combo, stopped)
                self.assertEqual(states, [False, timeout == 0, timeout == 0, True, False, True])
                self.assertEqual(debug, [True, False, False, False, True, False])
                self.assertTrue(all(event.is_set() for event in events))
                self.assertEqual(PositionControl.from_dict(settings.to_dict()).band_missing_ms, timeout)
        self.assertEqual(PositionControl.from_dict({}).band_missing_ms, 150)
        for invalid in (-1, 1.5, True):
            settings.band_missing_ms = invalid
            with self.assertRaises(ValueError):
                settings.validate()

    def test_marker_modes_support_every_action_and_cancel(self):
        for trigger in (TriggerMode.POSITION_CONTROL, TriggerMode.COLOR_BAND):
            for action_type, method in ((ActionType.CLICK, "mouse_click"),
                                      (ActionType.RAPID_CLICK, "mouse_rapid_click"),
                                      (ActionType.HOLD_MOUSE, "mouse_hold"),
                                      (ActionType.KEY_PRESS, "key_press"),
                                      (ActionType.RAPID_KEY, "key_rapid_press"),
                                      (ActionType.HOLD_KEY, "key_hold"),
                                      (ActionType.CLICK_IMAGE, "_click_target_image"),
                                      (ActionType.PLAY_SOUND, "play_sound")):
                with self.subTest(trigger=trigger, action=action_type):
                    engine = HookEngine()
                    combo = Combo(trigger=trigger, action=Action(action_type=action_type,
                                  delay_min=0, delay_max=0))
                    stopped = threading.Event()
                    target = engine if action_type == ActionType.CLICK_IMAGE else input_controller
                    with patch.object(target, method, side_effect=lambda *args: stopped.set()) as run:
                        engine._controlled_action_loop(combo, stopped)
                        run.assert_called_once()
                        self.assertIs(run.call_args.args[-1], stopped)
                        engine._controlled_action_loop(combo, stopped)
                        run.assert_called_once()

    def test_click_image_matches_target_and_cancels_without_clicking(self):
        combo = Combo(image_path="trigger.png", action=Action(
            action_type=ActionType.CLICK_IMAGE, target_image_path="target.png",
            target_threshold=0.9))
        profile = Profile(combos=[combo], scan_region=(100, 200, 400, 300))
        restored = Profile.from_dict(profile.to_dict())
        self.assertEqual(restored.combos[0].action, combo.action)
        self.assertEqual(Action.from_dict({}).target_image_path, "")
        engine = HookEngine()
        engine.set_profile(profile)
        stopped = threading.Event()
        frame = np.zeros((300, 400))
        with patch.object(engine._capture, "grab_frame", return_value=frame), \
                patch.object(engine._capture, "match_template", return_value=(True, 0.95, (250, 350))) as match, \
                patch.object(input_controller.pyautogui, "click") as click:
            engine._execute_action_once(combo)
            click.assert_called_once_with(x=250, y=350, button="left")
            self.assertEqual(match.call_args.args[1:4], ("target.png", 0.9, profile.scan_region))
            click.reset_mock()
            match.return_value = (False, 0.2, (250, 350))
            engine._click_target_image(combo, stopped)
            click.assert_not_called()
            match.return_value = (True, 0.95, (250, 350))
            match.side_effect = lambda *args, **kwargs: (stopped.set() or (True, 0.95, (250, 350)))
            engine._click_target_image(combo, stopped)
            click.assert_not_called()
            stopped.clear()
            match.side_effect = None
            combo.trigger = TriggerMode.WHILE_VISIBLE
            with patch.object(engine, "_click_target_image", side_effect=lambda *args: stopped.set()) as target:
                engine._continuous_action_loop(combo, stopped)
                target.assert_called_once_with(combo, stopped)

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
                    patch.object(input_controller if up == "mouseUp" else input_controller.pyautogui,
                                 "release_mouse" if up == "mouseUp" else up) as released, \
                    patch.object(input_controller, "_interruptible_sleep", side_effect=RuntimeError), \
                    self.assertRaises(RuntimeError):
                action("left" if down == "mouseDown" else "space")
            released.assert_called_once()
