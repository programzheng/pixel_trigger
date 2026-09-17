import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import tkinter as tk
from PIL import Image

from gui import App, ComboEditor
from models import Action, ActionType, Combo, Profile


class ComboScaleDisplayTest(unittest.TestCase):
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
