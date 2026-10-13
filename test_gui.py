import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import tkinter as tk
import ttkbootstrap as ttk
from PIL import Image

from gui import (
    App,
    ComboEditor,
    DebugOverlay,
    _app_title,
    _initial_window_geometry,
    _saved_window_geometry,
    _system_theme,
)
from models import Action, ActionType, Combo, PositionControl, Profile, TriggerMode


class ComboScaleDisplayTest(unittest.TestCase):
    def test_app_title_uses_project_version(self):
        with patch("gui.tomllib.load", return_value={"project": {"version": "1.2.3"}}):
            self.assertEqual(_app_title(), "PixelTrigger v1.2.3")

    def setUp(self):
        # Each test owns a new Tcl interpreter; styles cannot cross roots.
        ttk.Style.instance = None
        theme_patch = patch("gui._system_theme", return_value="litera")
        self.system_theme = theme_patch.start()
        self.addCleanup(theme_patch.stop)

    def test_system_theme_registry_and_fallback(self):
        import winreg
        with patch.object(winreg, "OpenKey"), patch.object(winreg, "QueryValueEx") as read:
            for value, expected in ((0, "darkly"), (1, "litera")):
                read.return_value = value, winreg.REG_DWORD
                self.assertEqual(_system_theme(), expected)
            read.side_effect = FileNotFoundError()
            self.assertEqual(_system_theme(), "litera")

    def test_initial_window_geometry_scales_and_stays_on_screen(self):
        self.assertEqual(_initial_window_geometry(1920, 1080), (1200, 885, 360, 97))
        self.assertEqual(_initial_window_geometry(1280, 720), (960, 619, 160, 50))

        width, height, x, y = _initial_window_geometry(800, 600)
        self.assertEqual((width, height, x, y), (752, 516, 24, 42))
        self.assertLessEqual(width + x, 800)
        self.assertLessEqual(height + y, 600)

    def test_saved_window_geometry_restores_or_falls_back_safely(self):
        settings = {"window": {
            "width": 1000, "height": 700, "x": 120, "y": 80,
            "maximized": True,
        }}
        self.assertEqual(
            _saved_window_geometry(settings, 1920, 1080),
            (1000, 700, 120, 80, True),
        )

        settings["window"].update(width=3000, height=2000, x=4000, y=3000)
        self.assertEqual(
            _saved_window_geometry(settings, 1280, 720),
            (1203, 619, 38, 50, True),
        )
        self.assertEqual(
            _saved_window_geometry({"window": {"width": "bad"}}, 1280, 720),
            (*_initial_window_geometry(1280, 720), False),
        )

    def test_window_changes_save_after_a_quiet_second(self):
        with patch.object(App, "_load_initial_profile"), \
                patch("gui.save_app_settings") as save:
            app = App()
            try:
                app.root.update()
                app.root.geometry("900x700+100+100")
                app.root.update()
                app.root.geometry("950x720+120+110")
                app.root.update()
                save.assert_not_called()
                app.root.after(1100, app.root.quit)
                app.root.mainloop()
                save.assert_called_once()
                window = save.call_args.args[0]["window"]
                self.assertEqual(
                    (window["width"], window["height"], window["x"], window["y"]),
                    (950, 720, 120, 110),
                )
            finally:
                app.on_close()

    def test_global_hotkeys_queue_events_on_key_press(self):
        with patch.object(App, "_load_initial_profile"):
            app = App()
        app.root.withdraw()
        callbacks = []
        with patch("gui.keyboard.add_hotkey",
                   side_effect=lambda key, callback: callbacks.append(callback) or key), \
                patch("gui.keyboard.remove_hotkey"):
            try:
                app._register_hotkeys()
                self.assertEqual(len(callbacks), 4)
                callbacks[0]()
                callbacks[1]()
                self.assertEqual(list(app._msg_queue)[-2:], [("hotkey",),
                                                            ("background_hotkey",)])
                app._msg_queue.clear()
                for expected in (True, False):
                    callbacks[2]()
                    callbacks[3]()
                    app._poll_queue()
                    self.assertEqual(app._debug_mode.get(), expected)
                    self.assertEqual(app._background_debug_mode.get(), expected)
                app.profile.debug_hotkey = "ctrl+shift+d"
                app.profile.background_debug_hotkey = "ctrl+shift+b"
                app._register_hotkeys()
                self.assertEqual(app._hotkeys[-2:], ["ctrl+shift+d", "ctrl+shift+b"])
            finally:
                app.on_close()

    def test_hotkeys_reject_duplicates_blank_and_invalid_keys(self):
        App._validate_hotkeys(["F6", "F7", "F9", "F10"])
        for keys in (["F6", "F7", "f6", "F10"],
                     ["F6", "F7", " ", "F10"],
                     ["F6", "F7", "not-a-real-key", "F10"]):
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                App._validate_hotkeys(keys)

    def test_system_theme_follows_changes_until_manual_switch(self):
        self.system_theme.return_value = "darkly"
        with patch.object(App, "_load_initial_profile"):
            app = App()
        app.root.withdraw()
        try:
            self.assertEqual(app.root.style.theme.name, "darkly")
            self.system_theme.return_value = "litera"
            app._follow_system_theme()
            self.assertEqual(app.root.style.theme.name, "litera")
            app.btn_theme.invoke()
            app._follow_system_theme()
            self.assertEqual(app.root.style.theme.name, "darkly")
        finally:
            app.on_close()

    def test_theme_button_switches_live_windows_without_changing_combos(self):
        with patch.object(App, "_load_initial_profile"):
            app = App()
        app.root.withdraw()
        try:
            combo = Combo(name="主題測試")
            app.profile = Profile(combos=[combo])
            app._refresh_combo_tree()
            app.tree.selection_set(combo.id)
            app._show_log()
            with patch.object(tk.Toplevel, "wait_window"):
                editor = ComboEditor(app.root, combo)
            for theme, text in (("darkly", "切換亮色"), ("litera", "切換深色")):
                app.btn_theme.invoke()
                app.root.update_idletasks()
                self.assertEqual(app.root.style.theme.name, theme)
                self.assertEqual(app.btn_theme.cget("text"), text)
                self.assertEqual(app.tree.selection(), (combo.id,))
                self.assertIs(app.profile.combos[0], combo)
                self.assertEqual(editor.lbl_preview.cget("foreground"), "")
                self.assertEqual(app.root.style.lookup("TLabel", "foreground"),
                                 app.root.style.colors.fg)
                self.assertEqual(app._log_text.cget("background"),
                                 app.root.style.colors.inputbg)
            editor.win.destroy()
        finally:
            app.on_close()

    def test_debug_overlay_transparency_follows_theme_canvas(self):
        with patch.object(App, "_load_initial_profile"):
            app = App()
        app.root.withdraw()
        overlay = DebugOverlay(app.root)
        try:
            for theme in ("darkly", "litera"):
                app.root.style.theme_use(theme)
                app.root.update_idletasks()
                overlay.update_rects([])
                background = overlay.canvas.cget("background")
                self.assertEqual(overlay.win.cget("background"), background)
                self.assertEqual(overlay.win.attributes("-transparentcolor"), background)
        finally:
            overlay.destroy()
            app.on_close()

    def test_debug_overlay_is_click_through(self):
        import ctypes

        with patch.object(App, "_load_initial_profile"):
            app = App()
        app.root.withdraw()
        overlay = DebugOverlay(app.root)
        try:
            hwnd = ctypes.windll.user32.GetParent(overlay.win.winfo_id())
            self.assertTrue(ctypes.windll.user32.GetWindowLongW(hwnd, -20) & 0x20)
        finally:
            overlay.destroy()
            app.on_close()

    def test_combo_log_view_is_bounded_live_and_separate(self):
        with patch.object(App, "_load_initial_profile"):
            app = App()
        app.root.withdraw()
        self.assertIsInstance(app.root, ttk.Window)
        self.assertEqual(app.root.style.theme.name, "litera")
        self.assertEqual(app.btn_toggle.cget("style"), "success.TButton")
        self.assertEqual(app.btn_background.cget("style"), "success.TButton")
        try:
            combo, other = Combo(name="釣魚"), Combo(name="其他")
            app.profile = Profile(combos=[combo, other])
            app._refresh_combo_tree()
            app.tree.selection_set(combo.id)
            for index in range(510):
                app._on_log(combo.id, f"測試 {index}")
            app._on_log(other.id, "另一個 Combo 的錯誤", "error")
            app._show_combo_log()
            win, text = app._log_window, app._log_text
            content = text.get("1.0", "end")
            self.assertEqual(len(app._combo_logs[combo.id]), 500)
            self.assertIn("測試 509", content)
            self.assertNotIn("另一個 Combo 的錯誤", content)
            app._on_log(combo.id, "色帶未達啟動比例", "failure")
            app._update_log_view()
            self.assertIn("色帶未達啟動比例", text.get("1.0", "end"))
            app._show_combo_log()
            self.assertIs(app._log_window, win)
            app._log_selector.selection_set(1)
            app._select_log_combos()
            content = text.get("1.0", "end")
            self.assertIn("另一個 Combo 的錯誤", content)
            self.assertLess(content.index("另一個 Combo 的錯誤"), content.index("色帶未達啟動比例"))
            app._log_filter.set("條件失敗與錯誤")
            app._update_log_view()
            content = text.get("1.0", "end")
            self.assertNotIn("測試 509", content)
            self.assertIn("色帶未達啟動比例", content)
            app._log_filter.set("僅錯誤")
            app._update_log_view()
            content = text.get("1.0", "end")
            self.assertIn("另一個 Combo 的錯誤", content)
            self.assertNotIn("色帶未達啟動比例", content)
            app._copy_log()
            self.assertEqual(app.root.clipboard_get(), text.get("1.0", "end-1c"))
            app._log_pause.set(True)
            with patch.object(text, "see") as scroll:
                app._on_log(combo.id, "新錯誤", "error")
                app._update_log_view()
                scroll.assert_not_called()
            app._show_log()
            self.assertTrue(app._log_all.get())
            self.assertIs(app._log_window, win)
            app._clear_logs()
            self.assertEqual(text.get("1.0", "end-1c"), "")
        finally:
            for job in app.root.tk.call("after", "info"):
                app.root.after_cancel(job)
            app.root.destroy()

    def test_color_band_test_adjusts_tolerance_without_actions(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "gauge.png"
                image = Image.new("RGB", (60, 60), (115, 170, 75))
                image.paste((255, 170, 41), (25, 25, 35, 35))
                image.paste((230, 50, 40), (20, 20, 22, 25))
                image.save(path)
                combo = Combo(image_path=str(path), trigger=TriggerMode.COLOR_BAND,
                              position_control=PositionControl(region=(0, 0, 60, 60),
                                      color=(255, 170, 41), band_color=(75, 170, 115)))
                with patch.object(tk.Toplevel, "wait_window"):
                    editor = ComboEditor(root, combo)
                try:
                    self.assertEqual(len(editor.position_canvas.find_all()), 3)  # 圖片、取樣框、箭頭
                    editor.var_band_tolerance.set("0")
                    self.assertIn("原本執行中：停止", editor.lbl_position_status.cget("text"))
                    no_match = editor.win.tk.call(str(editor._position_photo), "get", 108, 62)
                    editor.var_band_tolerance.set("30")
                    self.assertIn("原本未執行：啟動", editor.lbl_position_status.cget("text"))
                    matched = editor.win.tk.call(str(editor._position_photo), "get", 108, 62)
                    self.assertNotEqual(no_match, matched)
                    ratio_text = editor.lbl_position_status.cget("text").splitlines()[0]
                    editor.var_band_start.set("1")
                    self.assertIn("原本未執行：維持停止", editor.lbl_position_status.cget("text"))
                    self.assertEqual(editor.lbl_position_status.cget("text").splitlines()[0], ratio_text)
                    self.assertEqual(editor.win.tk.call(str(editor._position_photo), "get", 108, 62), matched)
                    editor.var_position_color.set("#0000ff")
                    self.assertIn("未找到唯一箭頭", editor.lbl_position_status.cget("text"))
                    self.assertEqual(len(editor.position_canvas.find_all()), 1)
                    editor.var_position_color.set("#ffaa29")
                    self.assertFalse(any(isinstance(w, tk.Toplevel) for w in editor.win.winfo_children()))
                    editor.var_band_start.set("")
                    self.assertFalse("原本未執行：啟動" in editor.lbl_position_status.cget("text"))
                    editor.var_band_start.set("0.8")
                    self.assertIn("原本未執行：啟動", editor.lbl_position_status.cget("text"))
                    self.assertEqual(combo.position_control.band_tolerance, 15)
                    editor.var_band_missing_ms.set("325")
                    editor._ok()
                    self.assertTrue(editor.result)
                    self.assertEqual(combo.position_control.band_start, 0.8)
                    restored = Combo.from_dict(combo.to_dict())
                    self.assertEqual(restored.position_control.band_missing_ms, 325)
                    with patch.object(tk.Toplevel, "wait_window"):
                        editor = ComboEditor(root, restored)
                    self.assertEqual(editor.var_band_start.get(), "0.8")
                    self.assertEqual(editor.var_band_missing_ms.get(), "325")
                finally:
                    editor.win.destroy()
        finally:
            root.destroy()

    def test_sound_action_editor_save_and_reopen(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "trigger.png"
                Image.new("RGB", (50, 40), "red").save(path)
                combo = Combo(image_path=str(path), action=Action(action_type=ActionType.PLAY_SOUND),
                              position_control=PositionControl(region=(0, 0, 50, 40)))
                with patch.object(tk.Toplevel, "wait_window"):
                    editor = ComboEditor(root, combo)
                    for trigger in TriggerMode:
                        editor.var_trigger.set(trigger.value)
                        editor._on_action_type_change()
                        self.assertEqual(editor.var_action_type.get(), ActionType.PLAY_SOUND.value)
                        self.assertEqual(editor.frm_delay.winfo_manager(), "pack")
                        for widget in (editor.frm_mouse_btn, editor.frm_key, editor.frm_duration,
                                       editor.frm_interval, editor.frm_target):
                            self.assertEqual(widget.winfo_manager(), "")
                    editor.var_trigger.set(TriggerMode.ON_APPEAR.value)
                    editor._ok()
                    self.assertTrue(editor.result)
                    restored = Combo.from_dict(combo.to_dict())
                    editor = ComboEditor(root, restored)
                try:
                    self.assertEqual(editor.var_action_type.get(), ActionType.PLAY_SOUND.value)
                    self.assertEqual(restored.action.action_type, ActionType.PLAY_SOUND)
                finally:
                    editor.win.destroy()
        finally:
            root.destroy()

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
                trigger = Image.new("RGBA", (60, 50), (0, 0, 0, 0))
                trigger.paste((255, 200, 0, 255), (20, 25, 40, 40))
                trigger.save(editor.var_image.get())
                with patch("gui.ImageRegionSelector") as selector, \
                        patch("gui.profile_manager.TEMPLATES_DIR", Path(folder)):
                    selector.return_value.result = (15, 20, 30, 25)
                    button = next(w for w in editor.frm_target.winfo_children()
                                  if isinstance(w, ttk.Button)
                                  and w.cget("text") == "框選觸發圖片內的圖")
                    button.invoke()
                    selected_path = editor.var_target_image.get()
                    with Image.open(selected_path) as cropped:
                        self.assertEqual(cropped.tobytes(), trigger.crop((15, 20, 45, 45)).tobytes())
                        self.assertEqual(cropped.size, (30, 25))
                        self.assertEqual(cropped.mode, "RGBA")
                    self.assertTrue(editor.lbl_target_thumb.cget("image"))
                    selector.return_value.result = None
                    button.invoke()
                    self.assertEqual(editor.var_target_image.get(), selected_path)
                    self.assertIs(editor.win.grab_current(), editor.win)
                    selector.return_value.result = (15, 20, 30, 25)
                    with patch.object(Image.Image, "save", side_effect=OSError("write failed")), \
                            patch("gui.messagebox.showerror") as error:
                        button.invoke()
                        error.assert_called_once()
                    self.assertEqual(editor.var_target_image.get(), selected_path)
                    trigger_path = editor.var_image.get()
                    editor.var_image.set("")
                    selector.reset_mock()
                    with patch("gui.messagebox.showinfo") as info:
                        button.invoke()
                        info.assert_called_once()
                    selector.assert_not_called()
                    editor.var_image.set(trigger_path)
                editor.var_target_threshold.set("0.9")
                editor._ok()
                self.assertTrue(editor.result)
                self.assertEqual(combo.action.target_threshold, 0.9)
                self.assertTrue(Path(combo.action.target_image_path).exists())
                restored = Combo.from_dict(combo.to_dict())
                with patch.object(tk.Toplevel, "wait_window"):
                    reopened = ComboEditor(root, restored)
                self.assertEqual(reopened.var_target_image.get(), selected_path)
                self.assertTrue(reopened.lbl_target_thumb.cget("image"))
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
                self.assertEqual(app.lbl_cycles.cget("text"), "主動掃描: 1 次")
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
