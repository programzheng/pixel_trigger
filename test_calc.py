import csv
import tempfile
import unittest
import threading
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from automation import HookEngine
from calc import BackgroundCalc, FIELDS, Ledger, parse_balance
from gui import App, ComboEditor
from models import Combo, Profile
from screen_capture import ScreenCapture
import tkinter as tk


class CalcTests(unittest.TestCase):
    def test_balance_parser(self):
        self.assertEqual(parse_balance("1,234"), 1234)
        for value in ("", "12,34", "1 | 2", "-4", "1.5", "O24"):
            with self.assertRaises(ValueError):
                parse_balance(value)

    def test_daily_rotation_restart_reset_and_totals(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(folder)
            profile = Profile()
            combo = Combo(detection_mode="background", calc_region=(20, 0, 40, 20))
            first = datetime(2026, 9, 18, 9)
            for balance in (324, 450, 410, 600):
                ledger.record(profile, combo, balance, str(balance), .99, first)
            self.assertIsNone(ledger.record(profile, combo, 600, "600", .99, first))
            self.assertEqual(ledger.snapshot(profile.id, combo.id, "20260918")[:3], (316, 40, 276))
            restarted = Ledger(folder)
            restarted.load()
            next_day = datetime(2026, 9, 19, 0, 1, 2)
            restarted.record(profile, combo, 650, "650", .99, next_day)
            self.assertEqual(restarted.snapshot(profile.id, combo.id, "20260919")[:3], (50, 0, 50))
            self.assertEqual(restarted.snapshot(profile.id, combo.id, "2026")[:3], (366, 40, 326))
            restarted.record(profile, combo, 10, "10", .99, next_day, reset=True)
            restarted.record(profile, combo, 20, "20", .99, next_day)
            self.assertEqual(restarted.snapshot(profile.id, combo.id, "20260919")[:3], (60, 0, 60))
            restarted.load()
            self.assertEqual(restarted.last[(profile.id, combo.id)], 20)
            self.assertEqual(sorted(p.name for p in Path(folder).glob("*.csv")), ["20260918.csv", "20260919.csv"])
            with (Path(folder) / "20260919.csv").open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, FIELDS)
                self.assertEqual(next(reader)["timestamp"], "20260919000102")

    def test_write_failure_keeps_last_balance(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(folder)
            profile, combo = Profile(), Combo()
            ledger.record(profile, combo, 324, "324", .9)
            with patch.object(Path, "open", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    ledger.record(profile, combo, 450, "450", .9)
            self.assertEqual(ledger.last[(profile.id, combo.id)], 324)
            self.assertEqual(len(ledger.rows), 1)
            path = next(Path(folder).glob("*.csv"))
            with path.open("a", encoding="utf-8") as stream:
                stream.write("broken\n")
            with self.assertRaises(ValueError):
                ledger.load()

    def test_background_stability_dedup_and_warning(self):
        with tempfile.TemporaryDirectory() as folder:
            events = []
            engine = BackgroundCalc(lambda *event: events.append(event), Ledger(folder))
            combo = Combo(detection_mode="background", calc_region=(0, 0, 4, 4), daily_limit=999)
            profile = Profile(combos=[combo])
            values = iter([324, 324, 1200, 1200, 1300, 1300, 1400, 1400, 1400])
            engine.capture.grab_frame = Mock(return_value=np.zeros((10, 10)))
            engine.capture.read_balance = Mock(side_effect=lambda *args: (v := next(values), str(v), .99, None))
            calls = 0
            def wait(_):
                nonlocal calls
                calls += 1
                if calls == 9:
                    engine.stop_event.set()
            engine.stop_event.wait = wait
            engine._loop(profile, [combo])
            self.assertEqual([int(row["balance"]) for row in engine.ledger.rows], [324, 1200, 1300, 1400])
            warnings = [e[2] for e in events if e[0] == "background_record" and "上限" in e[2]]
            self.assertEqual(len(warnings), 2)
            self.assertEqual(engine.ledger.snapshot(profile.id, combo.id, "")[:3], (1076, 0, 1076))

    def test_ocr_region_scaling_and_invalid_results(self):
        combo = Combo(image_path="icon", calc_region=(12, -2, 10, 6))
        capture = ScreenCapture()
        capture.match_template = Mock(return_value=(True, 1, (120, 220)))
        capture.get_match_size = Mock(return_value=(20, 20))
        capture.get_template_size = Mock(return_value=(10, 10))
        capture._ocr_reader = Mock()
        capture._ocr_reader.readtext.return_value = [(None, "324", .99)]
        frame = np.zeros((100, 100), dtype=np.uint8)
        value, _, _, roi = capture.read_balance(combo, frame, (100, 200, 100, 100))
        self.assertEqual(value, 324)
        self.assertEqual(roi.shape, (12, 20))
        self.assertEqual(capture.get_balance_debug(),
                         (True, 1, (110, 210, 20, 20), (134, 206, 20, 12)))
        capture._ocr_reader.readtext.return_value = [(None, "324", .1)]
        self.assertIsNone(capture.read_balance(combo, frame, (100, 200, 100, 100))[0])
        capture.match_template.return_value = (False, .1, None)
        capture.read_balance(combo, frame, (100, 200, 100, 100))
        self.assertEqual(capture.get_balance_debug(), (False, .1, None, None))

    def test_active_engine_never_processes_calc_combo(self):
        engine = HookEngine()
        engine._capture.match_template = Mock()
        engine._process_combo(Combo(detection_mode="background"), None, None)
        engine._capture.match_template.assert_not_called()

    def test_hotkeys_release_and_profile_rebinding(self):
        with patch.object(App, "_load_initial_profile"):
            app = App()
        try:
            app.root.withdraw()
            with patch("gui.keyboard.add_hotkey", side_effect=[1, 2, 3, 4]) as add, patch("gui.keyboard.remove_hotkey") as remove:
                app._register_hotkeys()
                self.assertTrue(all(call.kwargs["trigger_on_release"] for call in add.call_args_list))
                add.call_args_list[1].args[1]()
                self.assertEqual(app._msg_queue.pop(), ("background_hotkey",))
                app.profile = Profile(hotkey="F8", background_hotkey="F9")
                app._refresh_hotkeys()
                self.assertEqual([call.args[0] for call in remove.call_args_list], [1, 2])
                app._unregister_hotkeys()
        finally:
            app.on_close()

    def test_ui_profile_roundtrip_and_separate_controls(self):
        with patch.object(App, "_load_initial_profile"):
            app = App()
        try:
            app.root.withdraw()
            combo = Combo(detection_mode="background", calc_region=(20, 0, 40, 20))
            with patch.object(tk.Toplevel, "wait_window"):
                editor = ComboEditor(app.root, combo)
            self.assertEqual(editor.frm_calc.winfo_manager(), "pack")
            self.assertEqual(editor.frm_params.winfo_manager(), "")
            editor.var_detection_mode.set("active")
            editor._on_detection_mode_change()
            self.assertEqual(editor.frm_params.winfo_manager(), "pack")
            editor.win.destroy()
            profile = Profile(combos=[combo])
            self.assertEqual(Profile.from_dict(profile.to_dict()).to_dict(), profile.to_dict())
            app.background.stop = Mock()
            app.engine._running = True
            with patch.object(app.engine, "stop"):
                app._stop()
            app.background.stop.assert_not_called()
            app.profile = profile
            app._show_calc()
            self.assertIn("999", app._calc_summary.cget("text"))
        finally:
            app.on_close()

    def test_single_dialog_saves_both_hotkeys_and_rolls_back_on_failure(self):
        with patch.object(App, "_load_initial_profile"):
            app = App()
        try:
            def complete(dialog, *_args):
                self.assertEqual(len(dialog.entries), 2)
                for entry, value in zip(dialog.entries, ("F8", "F9")):
                    entry.delete(0, "end")
                    entry.insert(0, value)
                dialog.ok()
            with patch.object(tk.Toplevel, "wait_window", autospec=True, side_effect=complete), patch("gui.profile_manager.save_profile") as save:
                app._configure_hotkeys()
                self.assertEqual((app.profile.hotkey, app.profile.background_hotkey), ("F8", "F9"))
                save.assert_called_once()
            app.profile.hotkey, app.profile.background_hotkey = "F6", "F7"
            with patch.object(tk.Toplevel, "wait_window", autospec=True, side_effect=complete), patch("gui.profile_manager.save_profile", side_effect=OSError("disk full")), patch("gui.messagebox.showerror") as error:
                app._configure_hotkeys()
                self.assertEqual((app.profile.hotkey, app.profile.background_hotkey), ("F6", "F7"))
                error.assert_called_once()
        finally:
            app.on_close()

    def test_background_results_and_scale_survive_active_stop(self):
        with patch.object(App, "_load_initial_profile"):
            app = App()
        try:
            app.root.withdraw()
            combo = Combo(detection_mode="background", image_path="icon")
            app.profile = Profile(combos=[combo])
            app._refresh_combo_tree()
            self.assertEqual(app.tree.set(combo.id, "trigger"), "背景：金額記帳")
            self.assertEqual(app.tree.set(combo.id, "action"), "OCR 金額記錄")
            with patch.object(app.background.capture, "get_match_scale", return_value=.8), patch.object(app.engine, "get_match_scale", return_value=1.2):
                app._msg_queue.append(("background_read", combo.id, "餘額 324"))
                app._poll_queue()
                self.assertEqual(app.tree.set(combo.id, "scale"), "80.0%")
                app._stop()
                self.assertEqual(app.tree.set(combo.id, "status"), "餘額 324")
                app._msg_queue.append(("background_status", "目標視窗不可用，背景暫停讀取"))
                app._poll_queue()
                self.assertIn("暫停", app.tree.set(combo.id, "status"))
                app.engine._running = True
                app._msg_queue.append(("status", False))
                app._poll_queue()
                self.assertEqual(app.btn_toggle.cget("bg"), "#F44336")
                app.engine._running = False
        finally:
            app.on_close()

    def test_background_image_does_not_run_hidden_verification_ocr(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with tempfile.TemporaryDirectory() as folder:
                from PIL import Image
                path = Path(folder) / "icon.png"
                Image.new("RGB", (16, 24), "yellow").save(path)
                with patch.object(tk.Toplevel, "wait_window"):
                    editor = ComboEditor(root, Combo(detection_mode="background"))
                with patch("gui.ocr_read_image") as ocr:
                    editor._update_preview(str(path), auto_ocr=True)
                    ocr.assert_not_called()
                self.assertEqual(editor.lbl_image.cget("text"), "定位圖片:")
                self.assertEqual(editor.frm_params.winfo_manager(), "")
                self.assertEqual(editor._active_widgets[-1].winfo_manager(), "")
                editor.win.destroy()
        finally:
            root.destroy()

    def test_both_toggle_buttons_fit_a_shorter_window(self):
        with patch.object(App, "_load_initial_profile"):
            app = App()
        try:
            app.root.geometry("900x650")
            app.root.update()
            bottom = app.root.winfo_rooty() + app.root.winfo_height()
            for button in (app.btn_toggle, app.btn_background):
                self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), bottom)
                self.assertTrue(button.winfo_viewable())
            self.assertIn("背景: F7", app.lbl_hotkey.cget("text"))
        finally:
            app.on_close()

    def test_stop_does_not_join_ocr_or_record_a_cancelled_result(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = BackgroundCalc(Mock(), Ledger(folder))
            combo = Combo(name="original", detection_mode="background", calc_region=(0, 0, 4, 4))
            profile = Profile(combos=[combo])
            entered, release = threading.Event(), threading.Event()
            seen = []
            def read(snapshot, *_args):
                seen.append(snapshot)
                entered.set()
                release.wait(2)
                return 324, "324", .99, None
            engine.capture.load_template = Mock(return_value=True)
            engine.capture.grab_frame = Mock(return_value=np.zeros((10, 10)))
            engine.capture.read_balance = read
            try:
                engine.start(profile)
                self.assertTrue(entered.wait(1))
                combo.name = "edited"
                self.assertEqual(seen[0].name, "original")
                with patch.object(engine.thread, "join", side_effect=AssertionError("UI must not join OCR")):
                    engine.stop()
                self.assertFalse(engine.running)
                self.assertTrue(engine.busy)
                with self.assertRaises(ValueError):
                    engine.start(profile)
                with self.assertRaises(ValueError):
                    engine.reset(combo.id)
            finally:
                release.set()
                if engine.thread:
                    engine.thread.join(2)
            self.assertFalse(engine.busy)
            self.assertEqual(engine.ledger.rows, [])

    def test_invalid_hotkeys_keep_the_same_dialog_open(self):
        with patch.object(App, "_load_initial_profile"):
            app = App()
        try:
            def complete(dialog, *_args):
                def fill(values):
                    for entry, value in zip(dialog.entries, values):
                        entry.delete(0, "end")
                        entry.insert(0, value)
                for values in (("", "F7"), ("F7", "F7"), ("invalid-key", "F7")):
                    fill(values)
                    dialog.ok()
                    self.assertTrue(dialog.winfo_exists())
                    self.assertEqual(tuple(e.get() for e in dialog.entries), values)
                fill(("F8", "F9"))
                dialog.ok()
            with patch.object(tk.Toplevel, "wait_window", autospec=True, side_effect=complete), patch("gui.profile_manager.save_profile") as save, patch("gui.messagebox.showerror") as error:
                app._configure_hotkeys()
                self.assertEqual(error.call_count, 3)
                save.assert_called_once()
                self.assertEqual((app.profile.hotkey, app.profile.background_hotkey), ("F8", "F9"))
        finally:
            app.on_close()

    def test_invalid_scan_scope_never_replaces_the_saved_region(self):
        with patch.object(App, "_load_initial_profile"):
            app = App()
        try:
            app.root.withdraw()
            app.profile.scan_region = (10, 20, 100, 100)
            for values in (("10", "", "100", "100"), ("x", "20", "100", "100"),
                           ("10", "20", "0", "100"), ("10", "20", "100", "-1")):
                for var, value in zip(app.region_vars, values):
                    var.set(value)
                with self.assertRaises(ValueError):
                    app._sync_region_from_ui()
                self.assertEqual(app.profile.scan_region, (10, 20, 100, 100))
            with patch("gui.messagebox.showerror") as error, patch.object(app.engine, "start") as start:
                app.profile.combos = [Combo(image_path="icon")]
                app._start()
                start.assert_not_called()
                error.assert_called_once()
            for var in app.region_vars:
                var.set("")
            app._sync_region_from_ui()
            self.assertIsNone(app.profile.scan_region)
        finally:
            app.on_close()

    def test_calc_preview_and_selection_use_configured_scope(self):
        root = tk.Tk()
        root.withdraw()
        try:
            region = (100, 200, 80, 60)
            profile = Profile(scan_region=region)
            combo = Combo(image_path="icon", detection_mode="background", calc_region=(12, 0, 10, 10))
            with patch.object(tk.Toplevel, "wait_window"):
                editor = ComboEditor(root, combo, profile)
            capture = Mock()
            capture.grab_frame.return_value = np.zeros((60, 80), dtype=np.uint8)
            capture.match_template.return_value = (True, 1, (120, 220))
            capture.get_match_size.return_value = (20, 20)
            capture.get_template_size.return_value = (10, 10)
            with patch("gui.ScreenCapture", return_value=capture), patch("gui.RegionSelector") as selector:
                selector.return_value.result = (134, 210, 20, 20)
                editor._select_calc_region()
                self.assertEqual(tuple(int(v.get()) for v in editor.calc_region_vars), (12, 0, 10, 10))
                capture.grab_frame.assert_called_with(region)
                self.assertEqual(capture.match_template.call_args.args[-1], region)
                capture.read_balance.return_value = (324, "324", .99, None)
                with patch("gui.threading.Thread") as thread:
                    thread.side_effect = lambda target, **kwargs: Mock(start=target)
                    editor._test_calc()
                self.assertEqual(capture.read_balance.call_args.args[-1], region)
                self.assertEqual(root.state(), "withdrawn")
            profile.window_title = "game"
            with patch("gui.capture_region", return_value=None):
                capture.grab_frame.reset_mock()
                with self.assertRaises(ValueError):
                    editor._calc_frame(capture)
                capture.grab_frame.assert_not_called()
            editor.win.destroy()
        finally:
            root.destroy()

    def test_disabled_combo_cannot_reset_baseline(self):
        with patch.object(App, "_load_initial_profile"):
            app = App()
        try:
            app.root.withdraw()
            combo = Combo(enabled=False, detection_mode="background")
            app.profile = Profile(combos=[combo])
            app._show_calc()
            with patch.object(BackgroundCalc, "running", new_callable=lambda: property(lambda _: True)), patch.object(app.background, "reset") as reset, patch("gui.messagebox.showinfo"):
                app._update_calc_view()
                self.assertEqual(app._calc_reset.cget("state"), "disabled")
                app._reset_calc()
                reset.assert_not_called()
        finally:
            app.on_close()

    def test_background_debug_frames_are_independent_of_active_detection(self):
        with patch.object(App, "_load_initial_profile"):
            app = App()
        try:
            app.root.withdraw()
            background, active = Combo(detection_mode="background"), Combo()
            app.profile = Profile(combos=[background, active])
            app._refresh_combo_tree()
            app._background_debug_mode.set(True)
            with patch.object(BackgroundCalc, "running", new_callable=lambda: property(lambda _: True)), patch("gui.DebugOverlay") as overlay:
                app._msg_queue.append(("background_debug", background.id, True, .95, (10, 20, 16, 24), True, .8, (30, 20, 30, 24)))
                app._poll_queue()
                rects = overlay.return_value.update_rects.call_args.args[0]
                self.assertEqual(len(rects), 2)
                self.assertEqual(rects[0][:4], (10, 20, 16, 24))
                self.assertEqual(rects[1][:4], (30, 20, 30, 24))
                self.assertTrue(all(rect[-2] for rect in rects))
                app._msg_queue.append(("background_debug", background.id, False, .1, None, False, 0, None))
                app._poll_queue()
                rects = overlay.return_value.update_rects.call_args.args[0]
                self.assertEqual(len(rects), 2)
                self.assertFalse(any(rect[-2] for rect in rects))
                app.engine._running = True
                app._debug_mode.set(True)
                app._on_debug_info(active.id, True, .9, (100, 100, 20, 20))
                app._on_debug_mode_toggle()
                self.assertEqual(len(overlay.return_value.update_rects.call_args.args[0]), 3)
                app._stop()
                overlay.return_value.destroy.assert_not_called()
                self.assertEqual(len(overlay.return_value.update_rects.call_args.args[0]), 2)
                app._background_debug_mode.set(False)
                app._on_debug_mode_toggle()
                overlay.return_value.destroy.assert_called_once()
        finally:
            app.on_close()


if __name__ == "__main__":
    unittest.main()
