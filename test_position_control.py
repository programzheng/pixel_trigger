import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

import input_controller
import profile_manager
from automation import HookEngine
from gui import App, ComboEditor, ImageRegionSelector
from models import Action, ActionType, Combo, PositionControl, Profile, TriggerMode
from screen_capture import ScreenCapture, find_color_marker


class PositionControlTest(unittest.TestCase):
    def setUp(self):
        self.settings = PositionControl(region_space="screen", region=(10, 20, 101, 40))
        self.combo = Combo(trigger=TriggerMode.POSITION_CONTROL,
                           action=Action(action_type=ActionType.HOLD_MOUSE),
                           position_control=self.settings)

    def frame(self, x):
        frame = np.zeros((40, 101, 3), dtype=np.uint8)
        if x is not None:
            frame[15:25, x - 2:x + 3] = self.settings.color
        return frame

    def test_color_detection_noise_ambiguity_and_hue_wrap(self):
        frame = self.frame(30)
        frame[0, 0] = self.settings.color
        self.assertEqual(find_color_marker(frame, self.settings)[0], (30, 19.5))
        frame[15:25, 70:75] = self.settings.color
        self.assertIsNone(find_color_marker(frame, self.settings))
        self.assertIsNone(find_color_marker(self.frame(None), self.settings))
        self.assertIsNone(find_color_marker(None, self.settings))
        self.settings.color = (255, 0, 0)
        hsv = np.full((8, 8, 3), (179, 255, 255), dtype=np.uint8)
        self.assertIsNotNone(find_color_marker(cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB), self.settings))

    def test_transparent_pixels_are_not_marker_candidates(self):
        frame = cv2.cvtColor(self.frame(30), cv2.COLOR_RGB2RGBA)
        frame[15:25, 70:75, :3] = self.settings.color
        frame[15:25, 70:75, 3] = 0
        self.assertEqual(find_color_marker(frame, self.settings)[0], (30, 19.5))
        frame[:, :, 3] = 0
        self.assertIsNone(find_color_marker(frame, self.settings))

    def test_area_range_excludes_background_and_scales_with_template(self):
        settings = PositionControl(region=(0, 0, 100, 50), color=(255, 170, 41),
                                   tolerance=3, min_area=100, max_area=500)
        frame = np.zeros((50, 100, 3), dtype=np.uint8)
        frame[20:35, 10:30] = settings.color  # 標記：300 像素
        frame[5:45, 60:95] = settings.color  # 同色背景：1400 像素
        frame[0:5, 40:50] = settings.color  # 同色碎片：50 像素
        for scale in (0.5, 1, 2):
            scaled = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
            marker = find_color_marker(scaled, settings)
            self.assertIsNotNone(marker)
            self.assertLess(marker[0][0], 30 * scale)
        frame[20:35, 35:55] = settings.color
        self.assertIsNone(find_color_marker(frame, settings))
        self.assertEqual(PositionControl.from_dict(settings.to_dict()), settings)
        self.assertEqual(PositionControl.from_dict({}).max_area, 0)
        for value in (-1, 50, 1.5):
            settings.max_area = value
            with self.assertRaises(ValueError):
                settings.validate()

    def test_hysteresis_missing_marker_and_stop_release(self):
        engine = HookEngine()
        engine.set_profile(Profile(combos=[self.combo]))
        frames = [self.frame(x) for x in (60, 30, 60, 80, 60, 30, None, 30)]
        sent = []
        sleeps = 0

        def advance(seconds, event):
            nonlocal sleeps
            sleeps += 1
            if sleeps == len(frames):
                event.set()

        with patch.object(engine._capture, "grab_frame", side_effect=frames), \
                patch.object(engine, "_interruptible_sleep_event", side_effect=advance), \
                patch.object(input_controller.pyautogui, "mouseDown", side_effect=lambda **kw: sent.append("down")), \
                patch.object(input_controller, "release_mouse", side_effect=lambda button: sent.append("up")):
            engine._position_control_loop(self.combo, threading.Event())
        self.assertEqual(sent, ["down", "up", "down", "up", "down", "up"])

    def test_capture_exception_releases_and_fail_safe_release_bypasses_guard(self):
        engine = HookEngine()
        engine.set_profile(Profile(combos=[self.combo]))
        with patch.object(engine._capture, "grab_frame", side_effect=[self.frame(30), RuntimeError("capture")]), \
                patch.object(engine, "_interruptible_sleep_event"), \
                patch.object(input_controller.pyautogui, "mouseDown"), \
                patch.object(input_controller, "release_mouse") as release:
            with self.assertRaises(RuntimeError):
                engine._position_control_loop(self.combo, threading.Event())
            release.assert_called_once_with("left")
        with patch.object(input_controller.pyautogui, "position", return_value=(0, 0)), \
                patch.object(input_controller.pyautogui.platformModule, "_mouseUp") as up:
            input_controller.release_mouse()
            up.assert_called_once_with(0, 0, "left")

    def test_profile_roundtrip_validation_and_shared_button(self):
        profile = Profile(combos=[self.combo])
        loaded = Profile.from_dict(json.loads(json.dumps(profile.to_dict())))
        self.assertEqual(loaded, profile)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(profile_manager, "PROFILES_DIR", Path(directory)):
            path = profile_manager.save_profile(profile, "position.json")
            self.assertEqual(profile_manager.load_profile(path), profile)
        self.assertEqual(Combo.from_dict({}).trigger, TriggerMode.ON_APPEAR)
        for press, release in ((0.75, 0.45), (0.5, 0.5), (float("nan"), 0.75)):
            with self.assertRaises(ValueError):
                PositionControl(region=(0, 0, 100, 40), press_at=press, release_at=release).validate()
        engine = HookEngine()
        engine.set_profile(Profile(combos=[self.combo, Combo(image_path="other.png")]))
        with patch("automation.threading.Thread") as thread:
            thread.return_value.is_alive.return_value = False
            engine.start()
            self.assertTrue(engine.running)
            engine.stop()
        self.assertFalse(engine.running)

    def test_shared_click_waits_for_position_release_and_can_be_cancelled(self):
        click_combo = Combo(action=Action(action_type=ActionType.CLICK))
        engine = HookEngine()
        engine.set_profile(Profile(combos=[self.combo, click_combo]))
        held, release, clicked = (threading.Event() for _ in range(3))
        stopped = threading.Event()
        position_thread = threading.Thread(target=engine._position_control_loop,
                                           args=(self.combo, stopped))
        click_thread = threading.Thread(target=engine._execute_action_once, args=(click_combo,))
        with patch.object(engine._capture, "position_frame",
                          side_effect=lambda *a, **kw: (self.frame(80 if release.is_set() else 30),
                                                       self.settings.region)), \
                patch.object(input_controller.pyautogui, "mouseDown", side_effect=lambda **kw: held.set()), \
                patch.object(input_controller, "release_mouse") as up, \
                patch.object(input_controller, "mouse_click", side_effect=lambda *a: clicked.set()), \
                patch.object(input_controller, "key_press") as key:
            try:
                position_thread.start()
                self.assertTrue(held.wait(1))
                click_thread.start()
                self.assertFalse(clicked.wait(0.1))
                engine._execute_action_once(Combo(action=Action(action_type=ActionType.KEY_PRESS, key="space")))
                key.assert_called_once()
                release.set()
                self.assertTrue(clicked.wait(1))
            finally:
                stopped.set()
                engine._stop_event.set()
                position_thread.join(1)
                click_thread.join(1)
            up.assert_called_once_with("left")
        self.assertFalse(position_thread.is_alive())
        self.assertFalse(click_thread.is_alive())
        self.assertFalse(engine._mouse_locks["left"].locked())

        engine._stop_event.clear()
        lock = engine._mouse_locks["left"]
        lock.acquire()
        waiting = threading.Thread(target=engine._execute_action_once, args=(click_combo,))
        with patch.object(input_controller, "mouse_click") as click:
            try:
                waiting.start()
                engine._stop_event.set()
                waiting.join(1)
                self.assertFalse(waiting.is_alive())
                click.assert_not_called()
            finally:
                lock.release()

    def test_position_only_profile_starts_and_stops(self):
        pressed = threading.Event()
        with patch.object(App, "_load_initial_profile"):
            app = App()
        app.root.withdraw()
        app.profile = Profile(combos=[self.combo], scan_interval=0.01)
        app._refresh_combo_tree()
        engine = app.engine
        with patch.object(engine._capture, "grab_frame", return_value=self.frame(30)), \
                patch.object(input_controller.pyautogui, "mouseDown", side_effect=lambda **kw: pressed.set()), \
                patch.object(input_controller, "release_mouse") as release:
            try:
                app._msg_queue.append(("hotkey",))
                app._poll_queue()
                self.assertTrue(pressed.wait(1))
                app._msg_queue.append(("hotkey",))
                app._poll_queue()
                self.assertFalse(engine.running)
            finally:
                engine.stop()
                app.root.destroy()
            release.assert_called_once_with("left")
        self.assertFalse(engine.running)

    def test_editor_preview_mode_switch_and_save(self):
        self.settings.region_space = "template"
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.combo.image_path = str(Path(directory.name) / "trigger.png")
        image = np.zeros((80, 131, 3), dtype=np.uint8)
        image[20:60, 10:111] = self.frame(30)
        Image.fromarray(image).save(self.combo.image_path)
        with patch.object(App, "_load_initial_profile"):
            app = App()
        app.root.withdraw()
        results = []

        original = ComboEditor._build_position_ui

        def build(editor, parent):
            original(editor, parent)

            def check():
                try:
                    editor.var_trigger.set(TriggerMode.ON_APPEAR.value)
                    editor._on_action_type_change()
                    editor.var_trigger.set(TriggerMode.POSITION_CONTROL.value)
                    editor._on_action_type_change()
                    editor.var_position_preview.set(True)
                    editor._preview_position()
                    results.append(editor.lbl_position_status.cget("text"))
                    self.assertEqual(editor.frm_position.winfo_manager(), "pack")
                    self.assertEqual(editor.frm_duration.winfo_manager(), "pack")
                    editor._ok()
                finally:
                    if editor.win.winfo_exists():
                        editor.win.destroy()

            editor.win.after(50, check)

        try:
            with patch.object(ComboEditor, "_build_position_ui", build):
                editor = ComboEditor(app.root, self.combo)
            self.assertTrue(editor.result)
            self.assertIn("30.0%", results[0])
            self.assertEqual(self.combo.position_control, self.settings)
        finally:
            app.root.destroy()

    def test_template_region_follows_match_position_and_scale(self):
        capture = ScreenCapture()
        self.settings.region_space = "template"
        self.combo.image_path = "trigger.png"
        screen = np.zeros((240, 400, 3), dtype=np.uint8)
        screen[70:150, 50:252] = cv2.resize(self.frame(30), (202, 80))
        with patch.object(capture, "grab_frame", return_value=screen), \
                patch.object(capture, "match_template", return_value=(True, 1.0, (160, 110))) as match, \
                patch.object(capture, "get_match_size", return_value=(260, 160)), \
                patch.object(capture, "get_template_size", return_value=(130, 80)):
            frame, region = capture.position_frame(self.combo)
            self.assertEqual(capture.get_position_confidence(), 1.0)
            self.assertEqual(region, (50, 70, 202, 80))
            self.assertEqual(frame.shape, (80, 202, 3))
            self.assertAlmostEqual(find_color_marker(frame, self.settings)[0][0], 60.5)
            frame, region = capture.position_frame(self.combo, scan_region=(-400, 200, 400, 240))
            self.assertEqual(region, (-350, 270, 202, 80))
            self.assertEqual(frame.shape, (80, 202, 3))
        with patch.object(capture, "grab_frame", return_value=screen), \
                patch.object(capture, "match_template", return_value=(False, 0.2, None)):
            self.assertEqual(capture.position_frame(self.combo), (None, None))
            self.assertEqual(capture.get_position_confidence(), 0.2)

    def test_position_debug_reports_locator_score_when_marker_is_missing(self):
        engine = HookEngine()
        engine.set_profile(Profile(combos=[self.combo]))
        settings = self.settings
        settings.region_space = "template"
        self.combo.image_path = "trigger.png"
        self.combo.match_threshold = 0.8
        frame = np.zeros((80, 130, 3), dtype=np.uint8)
        stopped = threading.Event()
        results = []
        engine._on_debug_info = lambda *args: results.append(args)
        with patch.object(engine._capture, "grab_frame", return_value=frame), \
                patch.object(engine._capture, "match_template",
                             side_effect=[(False, 0.37, None), (True, 0.91, (65, 40))]), \
                patch.object(engine._capture, "get_match_size", return_value=(130, 80)), \
                patch.object(engine._capture, "get_template_size", return_value=(130, 80)), \
                patch.object(engine, "_interruptible_sleep_event",
                             side_effect=lambda *args: stopped.set() if len(results) == 2 else None), \
                patch.object(input_controller.pyautogui, "mouseDown") as down:
            engine._position_control_loop(self.combo, stopped)
        self.assertEqual(results, [(self.combo.id, False, 0.37, None),
                                   (self.combo.id, False, 0.91, None)])
        down.assert_not_called()

    def test_image_selector_drag_maps_to_original_pixels(self):
        selector = ImageRegionSelector.__new__(ImageRegionSelector)
        selector._image_size = (1000, 500)
        selector._display_size = (500, 250)
        selector._start_x, selector._start_y = 300, 200
        selector.overlay = unittest.mock.Mock()
        selector._on_release(SimpleNamespace(x=50, y=25))
        self.assertEqual(selector.result, (100, 50, 500, 350))
        selector.overlay.destroy.assert_called_once()

    def test_locator_matches_full_template_and_only_crops_tracking_region(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = str(Path(directory.name) / "trigger.png")
        template = np.random.default_rng(15).integers(30, 230, (80, 130), dtype=np.uint8)
        cv2.imwrite(path, template)
        screen = np.zeros((140, 200), dtype=np.uint8)
        screen[20:100, 40:170] = template
        capture = ScreenCapture()
        self.combo.image_path = path
        self.combo.match_threshold = 0.99
        self.settings.region_space = "template"
        with patch.object(capture, "grab_frame", side_effect=lambda *a, **kw:
                          cv2.cvtColor(screen, cv2.COLOR_GRAY2RGB)):
            frame, region = capture.position_frame(self.combo)
            self.assertEqual(region, (50, 40, 101, 40))
            np.testing.assert_array_equal(frame[:, :, 0], template[20:60, 10:111])
            self.assertGreaterEqual(capture.get_position_confidence(), 0.99)
            screen[40:80, 50:151] = 255
            self.assertEqual(capture.position_frame(self.combo), (None, None))
            self.assertLess(capture.get_position_confidence(), 0.99)
            screen[20:100, 40:170] = template
            self.settings.region = (0, 0, 130, 80)
            frame, region = capture.position_frame(self.combo)
            self.assertEqual(region, (40, 20, 130, 80))
            np.testing.assert_array_equal(frame[:, :, 0], template)


if __name__ == "__main__":
    unittest.main()
