import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import tkinter as tk
import ttkbootstrap as ttk
from PIL import Image

from automation import HookEngine
from gui import ComboEditor
from models import Combo, PositionControl, Profile, TriggerImage, TriggerMode
from screen_capture import ScreenCapture


class MultiImageTest(unittest.TestCase):
    def combo(self, **kwargs):
        return Combo(trigger_images=[TriggerImage("a.png", region=(0, 0, 10, 10)),
                                     TriggerImage("b.png", region=(5, 4, 12, 8), color=(0, 0, 255))],
                     **kwargs)

    def test_roundtrip_independent_regions_and_shared_parameters(self):
        combo = self.combo(position_control=PositionControl(tolerance=30, band_start=.9))
        combo.trigger_images[1].calc_region = (20, 5, 10, 8)
        saved = combo.to_dict()
        restored = Combo.from_dict(saved)
        self.assertEqual(restored.trigger_images, combo.trigger_images)
        # Common control parameters are persisted when relevant to the mode.
        combo.trigger = TriggerMode.COLOR_BAND
        restored = Combo.from_dict(combo.to_dict())
        for item in restored.image_items():
            self.assertEqual(restored.for_image(item).position_control.tolerance, 30)
            self.assertEqual(restored.for_image(item).position_control.band_start, .9)
        self.assertEqual(restored.for_image(restored.image_items()[1]).position_control.color, (0, 0, 255))
        self.assertEqual(saved["image_path"], "a.png")
        old = Combo.from_dict({"image_path": "old.png", "position_control": {
            "region_space": "template", "region": [1, 2, 8, 9], "color": [1, 2, 3]}})
        self.assertEqual(old.image_items()[0].region, (1, 2, 8, 9))
        self.assertEqual(old.image_items()[0].color, (1, 2, 3))
        self.assertEqual(Combo.from_dict({"image_path": "old.png", "trigger_images": []}).image_items(), [])
        with self.assertRaises(ValueError):
            Combo.from_dict({"trigger_images": "invalid"})

    def test_priority_ocr_fallback_and_combo_visibility(self):
        combo = self.combo(verify_text="OK", trigger=TriggerMode.WHILE_VISIBLE)
        engine = HookEngine()
        frame = np.zeros((40, 40), dtype=np.uint8)
        with patch.object(engine._capture, "match_template", return_value=(True, .95, (10, 10))) as match, \
                patch.object(engine._capture, "get_match_size", return_value=(8, 8)), \
                patch.object(engine._capture, "ocr_verify", side_effect=[(False, .2, "no"), (True, .9, "OK")]):
            result = engine._detect_combo(combo, frame, None)
        self.assertTrue(result[0])
        self.assertEqual([call.args[1] for call in match.call_args_list], ["a.png", "b.png"])
        self.assertEqual(engine._capture.get_combo_match(combo.id), "b.png")
        combo.verify_text = ""
        with patch.object(engine._capture, "match_template", return_value=(True, .95, (10, 10))) as match, \
                patch.object(engine._capture, "get_match_size", return_value=(8, 8)):
            engine._detect_combo(combo, frame, None)
            self.assertEqual(match.call_count, 1)
            self.assertEqual(engine._capture.get_combo_match(combo.id), "a.png")

        def start(_combo):
            engine._active_actions[combo.id] = Mock()

        with patch.object(engine._capture, "match_template", side_effect=[
                (True, .9, (10, 10)), (False, .1, None), (True, .9, (10, 10)),
                (False, .1, None), (False, .2, None)]), \
                patch.object(engine._capture, "get_match_size", return_value=(8, 8)), \
                patch.object(engine, "_start_continuous_action", side_effect=start) as started, \
                patch.object(engine, "_stop_continuous_action") as stopped:
            engine._process_combo(combo, frame, None)
            engine._process_combo(combo, frame, None)
            self.assertTrue(engine._prev_visible[combo.id])
            stopped.assert_not_called()
            engine._process_combo(combo, frame, None)
            started.assert_called_once()
            stopped.assert_called_once_with(combo.id)

    def test_position_uses_selected_image_region_and_scale(self):
        combo = self.combo(trigger=TriggerMode.COLOR_BAND)
        capture = ScreenCapture()
        with patch.object(capture, "grab_frame", return_value=np.zeros((100, 100, 3), dtype=np.uint8)), \
                patch.object(capture, "match_template", side_effect=[(False, .1, None), (True, .95, (40, 40))]), \
                patch.object(capture, "get_match_size", return_value=(40, 40)), \
                patch.object(capture, "get_template_size", return_value=(20, 20)):
            frame, region = capture.position_frame(combo, scan_region=(100, 200, 100, 100))
        self.assertEqual(region, (130, 228, 24, 16))
        self.assertEqual(frame.shape[:2], (16, 24))
        self.assertEqual(capture.get_position_combo().position_control.color, (0, 0, 255))
        self.assertEqual(capture.get_combo_match(combo.id), "b.png")

    def test_delayed_click_accepts_replacement_image(self):
        combo = self.combo()
        engine = HookEngine()
        engine.set_profile(Profile(combos=[combo]))
        with patch.object(engine._capture, "grab_frame", return_value=np.zeros((30, 30))), \
                patch.object(engine._capture, "match_template", side_effect=[(False, .1, None), (True, .9, (10, 10))]), \
                patch.object(engine._capture, "get_match_size", return_value=(8, 8)), \
                patch.object(engine, "_execute_action_once_unlocked") as action:
            engine._run_action_once(combo, threading.Event(), (2, 2, 8, 8))
            action.assert_called_once()

    def test_once_rearms_only_after_all_images_disappear(self):
        combo = self.combo()
        engine = HookEngine()
        frame = np.zeros((40, 40))
        with patch.object(engine._capture, "match_template", side_effect=[
                (True, .9, (10, 10)), (False, .1, None), (True, .9, (10, 10)),
                (False, .1, None), (False, .1, None), (True, .9, (10, 10))]), \
                patch.object(engine._capture, "get_match_size", return_value=(8, 8)), \
                patch("automation.threading.Thread") as thread:
            engine._process_combo(combo, frame, None)
            engine._once_actions.clear()  # The first action has completed.
            engine._process_combo(combo, frame, None)
            self.assertEqual(thread.call_count, 1)
            engine._process_combo(combo, frame, None)
            engine._process_combo(combo, frame, None)
            self.assertEqual(thread.call_count, 2)

    def test_balance_reads_selected_image_region_only_once(self):
        combo = self.combo(detection_mode="background")
        combo.trigger_images[0].calc_region = (0, 0, 2, 2)
        combo.trigger_images[1].calc_region = (4, 2, 8, 6)
        capture = ScreenCapture()
        with patch.object(capture, "match_template", side_effect=[(False, .1, None), (True, .9, (20, 20))]), \
                patch.object(capture, "get_match_size", return_value=(20, 20)), \
                patch.object(capture, "get_template_size", return_value=(20, 20)), \
                patch.object(capture, "_tesseract_balance", return_value=("123", .95)) as ocr:
            value, _, _, roi = capture.read_balance(combo, np.zeros((50, 50), dtype=np.uint8))
        self.assertEqual(value, 123)
        self.assertEqual(roi.shape, (6, 8))
        ocr.assert_called_once()
        self.assertEqual(capture.get_combo_match(combo.id), "b.png")

    def test_invalid_second_region_names_the_image(self):
        combo = self.combo(trigger=TriggerMode.COLOR_BAND)
        capture = ScreenCapture()
        with patch.object(capture, "load_template", return_value=True), \
                patch.object(capture, "get_template_size", return_value=(10, 10)):
            with self.assertRaisesRegex(ValueError, "b.png"):
                capture.validate_trigger_images(combo)

    def test_editor_switch_preserves_image_drafts_and_shared_values(self):
        ttk.Style.instance = None
        root = tk.Tk()
        root.withdraw()
        try:
            with tempfile.TemporaryDirectory() as directory:
                combo = self.combo(trigger=TriggerMode.COLOR_BAND)
                for index, item in enumerate(combo.trigger_images):
                    path = Path(directory) / f"{index}.png"
                    Image.new("RGB", (40, 40), "green").save(path)
                    item.image_path = str(path)
                original = combo.to_dict()
                with patch.object(tk.Toplevel, "wait_window"):
                    editor = ComboEditor(root, combo)
                try:
                    editor.var_position_color.set("#ff0000")
                    editor.var_position_tolerance.set("28")
                    editor._include_polygon = ((0., 0.), (1., 0.), (1., 1.))
                    editor.image_list.selection_clear(0, "end")
                    editor.image_list.selection_set(1)
                    editor._select_image()
                    self.assertEqual(editor.var_position_color.get(), "#0000ff")
                    self.assertEqual(editor.var_position_tolerance.get(), "28")
                    self.assertEqual(editor._include_polygon, ())
                    self.assertEqual(combo.to_dict(), original)
                    editor.image_list.selection_clear(0, "end")
                    editor.image_list.selection_set(0)
                    editor._select_image()
                    self.assertEqual(editor.var_position_color.get(), "#ff0000")
                    self.assertEqual(len(editor._include_polygon), 3)
                    with patch.object(ScreenCapture, "grab_frame", return_value=np.zeros((20, 20, 3), dtype=np.uint8)):
                        editor.var_region_space.set("screen")
                        editor._change_region_space()
                        for var, value in zip(editor.position_region_vars, (10, 20, 20, 20)):
                            var.set(str(value))
                        editor.var_region_space.set("template")
                        editor._change_region_space()
                    self.assertEqual(editor.var_position_color.get(), "#ff0000")
                    self.assertEqual(len(editor._include_polygon), 3)
                    self.assertEqual(editor._screen_position.region, (10, 20, 20, 20))
                    with patch("gui.messagebox.showerror") as error:
                        editor._ok()
                        error.assert_not_called()
                    self.assertTrue(editor.result)
                    self.assertEqual(combo.trigger_images[0].color, (255, 0, 0))
                    self.assertEqual(combo.trigger_images[1].color, (0, 0, 255))
                    self.assertEqual(combo.position_control.tolerance, 28)
                finally:
                    if editor.win.winfo_exists():
                        editor.win.destroy()
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
