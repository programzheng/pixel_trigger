import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import Mock
from collections import deque

import tkinter as tk
from PIL import Image

from gui import App, ComboEditor
from models import Action, ActionType, Combo, PositionControl, Profile, TriggerMode


class ComboScaleDisplayTest(unittest.TestCase):
    def test_o_sound_only_on_detection_transition_and_when_enabled(self):
        app = App.__new__(App)
        app._debug_data = {}
        app._msg_queue = deque()
        app.engine = SimpleNamespace(running=True)
        app.profile = Profile()
        app.root = Mock()
        app._sound_enabled = Mock()
        app._sound_enabled.get.return_value = True
        with patch("gui.winsound.PlaySound") as sound:
            for found in (False, True, True, False, True):
                app._on_debug_info("combo", found, 0.9)
            app._poll_queue()
            self.assertEqual(sound.call_count, 2)
            app._sound_enabled.get.return_value = False
            app._on_debug_info("other", True, 0.9)
            app._poll_queue()
            self.assertEqual(sound.call_count, 2)
            app._sound_enabled.get.return_value = True
            app.engine.running = False
            app._on_debug_info("stopped", True, 0.9)
            app._poll_queue()
            self.assertEqual(sound.call_count, 2)

    def test_position_selection_and_preview_preserve_transparency(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "transparent.png"
                image = Image.new("RGBA", (50, 40), (255, 170, 41, 0))
                image.paste((255, 170, 41, 255), (10, 10, 20, 20))
                image.save(path)
                combo = Combo(image_path=str(path), trigger=TriggerMode.POSITION_CONTROL,
                              action=Action(action_type=ActionType.HOLD_MOUSE),
                              position_control=PositionControl(region=(0, 0, 50, 40),
                                                               color=(255, 170, 41)))
                with patch.object(tk.Toplevel, "wait_window"):
                    editor = ComboEditor(root, combo)
                try:
                    with patch("gui.ImageRegionSelector") as selector:
                        selector.return_value.result = None
                        editor._select_position_region()
                        selected_image = selector.call_args.args[1]
                        self.assertEqual(selected_image.mode, "RGBA")
                        self.assertEqual(selected_image.getpixel((0, 0))[3], 0)
                    self.assertIn("標記位置", editor.lbl_position_status.cget("text"))
                    self.assertTrue(editor.win.tk.call(str(editor._position_photo),
                                                       "transparency", "get", 0, 0))
                    editor.var_position_color.set("#ffffff")
                    editor._position_preview_click(SimpleNamespace(x=0, y=0))
                    self.assertEqual(editor.var_position_color.get(), "#ffffff")
                    width, height = editor._position_preview_size
                    editor._position_preview_click(SimpleNamespace(x=15 * width // 50,
                                                                   y=15 * height // 40))
                    self.assertEqual(editor.var_position_color.get(), "#ffaa29")
                finally:
                    editor.win.destroy()
        finally:
            root.destroy()

    def test_position_editor_keeps_image_preview_after_save_and_reopen(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "gauge.png"
                Image.new("RGB", (50, 40), "red").save(path)
                combo = Combo(image_path=str(path), trigger=TriggerMode.POSITION_CONTROL,
                              action=Action(action_type=ActionType.HOLD_MOUSE),
                              position_control=PositionControl(region=(0, 0, 50, 40)))
                with patch.object(tk.Toplevel, "wait_window"):
                    editor = ComboEditor(root, combo)
                    self.assertEqual(editor.lbl_thumb.winfo_manager(), "pack")
                    editor._ok()
                    self.assertTrue(editor.result)
                    restored = Combo.from_dict(combo.to_dict())
                    reopened = ComboEditor(root, restored)
                try:
                    self.assertEqual(reopened.var_image.get(), str(path))
                    self.assertEqual(reopened.lbl_thumb.winfo_manager(), "pack")
                    self.assertTrue(reopened.lbl_thumb.cget("image"))
                    self.assertEqual(reopened._position_frame.shape, (40, 50, 4))
                    for mode in (TriggerMode.ON_APPEAR, TriggerMode.POSITION_CONTROL):
                        reopened.var_trigger.set(mode.value)
                        reopened._on_action_type_change()
                        self.assertEqual(reopened.lbl_thumb.winfo_manager(), "pack")
                finally:
                    reopened.win.destroy()
        finally:
            root.destroy()

    def test_image_click_editor_upload_capture_and_save(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "target.png"
                Image.new("RGB", (50, 40), "red").save(path)
                combo = Combo(action=Action(action_type=ActionType.CLICK_IMAGE))
                with patch.object(tk.Toplevel, "wait_window"):
                    editor = ComboEditor(root, combo)
                self.assertEqual(editor.frm_target.winfo_manager(), "pack")
                with patch("gui.filedialog.askopenfilename", return_value=str(path)):
                    editor._browse_image(target=True)
                self.assertEqual(editor.var_target_image.get(), str(path))
                with patch("gui.ImageGrab.grab", return_value=Image.new("RGB", (100, 100), "blue")), \
                        patch("gui.RegionSelector") as selector, \
                        patch("gui.profile_manager.TEMPLATES_DIR", Path(folder)):
                    selector.return_value.result = (10, 20, 30, 40)
                    editor._capture_image()
                    with Image.open(editor.var_image.get()) as captured:
                        self.assertEqual(captured.size, (30, 40))
                        self.assertEqual(captured.getpixel((0, 0)), (0, 0, 255))
                    editor._capture_image(target=True)
                self.assertEqual(root.state(), "withdrawn")
                self.assertNotEqual(editor.var_image.get(), editor.var_target_image.get())
                editor.var_target_threshold.set("0.9")
                editor._ok()
                self.assertTrue(editor.result)
                self.assertEqual(combo.action.target_threshold, 0.9)
                self.assertTrue(Path(combo.action.target_image_path).exists())
        finally:
            root.destroy()

    def test_scale_survives_status_updates_and_refresh(self):
        with patch.object(App, "_load_initial_profile"):
            app = App()
        app.root.withdraw()
        try:
            combo = Combo(image_path="template.png")
            app.profile = Profile(combos=[combo])
            app._refresh_combo_tree()
            self.assertEqual(app.tree.set(combo.id, "scale"), "—")
            with patch.object(app.engine, "get_match_scale", return_value=0.75):
                app._on_combo_triggered(combo.id, True)
                app._poll_queue()
                self.assertEqual(app.tree.set(combo.id, "scale"), "75.0%")
                self.assertEqual(app.tree.set(combo.id, "status"), ">>>")
                app._debug_mode.set(True)
                app._on_debug_info(combo.id, False, 0.2, None)
                app._on_cycle_complete(1)
                app._poll_queue()
                self.assertEqual(app.lbl_cycles.cget("text"), "掃描: 1 次")
                self.assertEqual(app.tree.set(combo.id, "scale"), "75.0%")
                self.assertEqual(app.tree.set(combo.id, "status"), "0.20 X")
                app._refresh_combo_tree()
                self.assertEqual(app.tree.set(combo.id, "scale"), "75.0%")
            with patch.object(app.engine, "get_match_scale", return_value=1.5):
                app._poll_queue()
                self.assertEqual(app.tree.set(combo.id, "scale"), "150.0%")
        finally:
            app.root.destroy()


if __name__ == "__main__":
    unittest.main()
