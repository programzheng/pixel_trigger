import csv
import tempfile
import unittest
import ttkbootstrap as ttk
import threading
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import cv2

from automation import HookEngine
from calc import BackgroundCalc, FIELDS, LEGACY_FIELDS, Ledger, accounting_day, parse_balance
from gui import App, ComboEditor, DebugOverlay
from models import Combo, Profile, Statistic
from screen_capture import ScreenCapture, _balance_templates, _template_balance
from statistics_ui import StatisticEditor
import tkinter as tk


class CalcTests(unittest.TestCase):
    def setUp(self):
        # Styles belong to the Tcl interpreter created by each test.
        ttk.Style.instance = None

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
            self.assertEqual(restarted.snapshot(profile.id, combo.id, "20260919")[:3], (0, 0, 0))
            self.assertEqual(restarted.snapshot(profile.id, combo.id, "2026")[:3], (316, 40, 276))
            restarted.record(profile, combo, 10, "10", .99, next_day, reset=True)
            restarted.record(profile, combo, 20, "20", .99, next_day)
            self.assertEqual(restarted.snapshot(profile.id, combo.id, "20260919")[:3], (10, 0, 10))
            restarted.load()
            self.assertEqual(restarted.last[(profile.id, combo.id)], 20)
            self.assertEqual(sorted(p.name for p in Path(folder).glob("*.csv")), ["20260918.csv", "20260919.csv"])
            with (Path(folder) / "20260919.csv").open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, FIELDS)
                self.assertEqual(next(reader)["timestamp"], "20260919000102")

    def test_load_migrates_cross_day_change_to_daily_baseline(self):
        with tempfile.TemporaryDirectory() as folder:
            profile, combo = Profile(), Combo()
            rows = [dict(zip(LEGACY_FIELDS, values)) for values in (
                ("20260922235959", profile.id, profile.name, combo.id, combo.name,
                 "baseline", "440", "", "0", "440", ".9"),
                ("20260923083455", profile.id, profile.name, combo.id, combo.name,
                 "change", "614", "440", "174", "614", ".9"),
            )]
            for date in ("20260922", "20260923"):
                with (Path(folder) / f"{date}.csv").open(
                        "w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=LEGACY_FIELDS)
                    writer.writeheader()
                    writer.writerow(next(row for row in rows if row["timestamp"].startswith(date)))
            ledger = Ledger(folder)
            ledger.load()
            self.assertEqual((ledger.rows[1]["record_type"], ledger.rows[1]["delta"]),
                             ("baseline", "0"))
            self.assertEqual(ledger.snapshot(profile.id, combo.id, "20260923")[:3],
                             (0, 0, 0))
            reloaded = Ledger(folder)
            reloaded.load()
            self.assertEqual(reloaded.rows[1]["previous_balance"], "")

    def test_combo_daily_reset_time_uses_noon_boundary_after_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(folder)
            profile = Profile()
            combo = Combo(detection_mode="background", calc_region=(0, 0, 4, 4),
                          daily_reset_time="12:00")
            for when, balance in ((datetime(2026, 9, 23, 11, 59), 500),
                                  (datetime(2026, 9, 23, 12), 600),
                                  (datetime(2026, 9, 24, 11, 59), 650),
                                  (datetime(2026, 9, 24, 12), 700)):
                ledger.record(profile, combo, balance, str(balance), .9, when)
            self.assertEqual(ledger.snapshot(profile.id, combo.id, "20260923")[:3],
                             (50, 0, 50))
            self.assertEqual(accounting_day(datetime(2026, 9, 24, 11, 59), "12:00"),
                             "20260923")
            reloaded = Ledger(folder)
            reloaded.load()
            self.assertEqual(reloaded.snapshot(profile.id, combo.id, "20260923")[:3],
                             (50, 0, 50))
            self.assertEqual([row["record_type"] for row in reloaded.rows],
                             ["baseline", "baseline", "change", "baseline"])

    def test_edit_balance_recalculates_following_deltas_and_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(folder)
            profile, combo = Profile(), Combo()
            now = datetime(2026, 9, 18, 9)
            for balance in (100, 150, 120):
                ledger.record(profile, combo, balance, str(balance), .9, now)
            ledger.edit_balance(1, 130)
            self.assertEqual([int(row["delta"]) for row in ledger.rows], [0, 30, -10])
            self.assertEqual(ledger.snapshot(profile.id, combo.id, "")[:3], (30, 10, 20))
            reloaded = Ledger(folder)
            reloaded.load()
            self.assertEqual([(row["previous_balance"], row["delta"])
                              for row in reloaded.rows], [("", "0"), ("100", "30"), ("130", "-10")])
            self.assertEqual(reloaded.last[(profile.id, combo.id)], 120)

    def test_add_record_inserts_by_time_and_recalculates_following_delta(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(folder)
            profile, combo = Profile(), Combo(daily_reset_time="12:00")
            ledger.record(profile, combo, 212, "212", .9,
                          datetime(2026, 9, 24, 12, 14, 37))
            ledger.record(profile, combo, 137, "137", .9,
                          datetime(2026, 9, 24, 13, 24, 52))
            ledger.add_record(profile, combo, datetime(2026, 9, 24, 13), 280)
            self.assertEqual([(row["balance"], row["delta"]) for row in ledger.rows],
                             [("212", "0"), ("280", "68"), ("137", "-143")])
            self.assertEqual(ledger.snapshot(profile.id, combo.id, "20260924")[:3],
                             (68, 143, -75))
            reloaded = Ledger(folder)
            reloaded.load()
            self.assertEqual([row["balance"] for row in reloaded.rows],
                             ["212", "280", "137"])

    def test_delete_record_recalculates_following_delta_and_baseline(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(folder)
            profile, combo = Profile(), Combo()
            now = datetime(2026, 9, 18, 9)
            for balance in (100, 150, 120):
                ledger.record(profile, combo, balance, str(balance), .9, now)
            ledger.delete_record(1)
            self.assertEqual([(row["balance"], row["delta"])
                              for row in ledger.rows], [("100", "0"), ("120", "20")])
            ledger.delete_record(0)
            self.assertEqual((ledger.rows[0]["record_type"], ledger.rows[0]["delta"]),
                             ("baseline", "0"))
            reloaded = Ledger(folder)
            reloaded.load()
            self.assertEqual([(row["balance"], row["record_type"], row["delta"])
                              for row in reloaded.rows], [("120", "baseline", "0")])

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
            profile = Profile(combos=[combo], statistics=[Statistic(source="ocr", combo_id=combo.id,
                                                                    limit=999)])
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
            warnings = [e[2] for e in events if e[0] == "background_stat" and "上限" in e[2]]
            self.assertEqual(len(warnings), 2)
            self.assertEqual(engine.ledger.snapshot(profile.id, combo.id, "")[:3], (1076, 0, 1076))

    def test_background_confirmation_survives_failed_ocr_between_matching_reads(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = BackgroundCalc(Mock(), Ledger(folder))
            profile, combo = Profile(), Combo()
            readings = iter([(576, "576", .88, None),
                             (None, "OCR 無有效數字", 0, None),
                             (576, "576", .88, None)])
            engine.capture.grab_frame = Mock(return_value=np.zeros((10, 10)))
            engine.capture.read_balance = Mock(side_effect=lambda *_args: next(readings))
            calls = 0
            def wait(_seconds):
                nonlocal calls
                calls += 1
                if calls == 3:
                    engine.stop_event.set()
            engine.stop_event.wait = wait
            engine._loop(profile, [combo])
            self.assertEqual([row["balance"] for row in engine.ledger.rows], ["576"])

    def test_low_confidence_numeric_text_uses_configured_confirmations(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = BackgroundCalc(Mock(), Ledger(folder))
            profile, combo = Profile(), Combo(
                text_threshold=.5, low_confidence_confirmations=4)
            engine.capture.grab_frame = Mock(return_value=np.zeros((10, 10)))
            engine.capture.read_balance = Mock(return_value=(None, "611", .3, None))
            calls = 0
            def wait(_seconds):
                nonlocal calls
                calls += 1
                if calls == 4:
                    engine.stop_event.set()
            engine.stop_event.wait = wait
            engine._loop(profile, [combo])
            self.assertEqual([row["balance"] for row in engine.ledger.rows], ["611"])

    def test_ocr_region_scaling_and_invalid_results(self):
        combo = Combo(image_path="icon", calc_region=(12, -2, 10, 6))
        capture = ScreenCapture()
        capture.match_template = Mock(return_value=(True, 1, (120, 220)))
        capture.get_match_size = Mock(return_value=(20, 20))
        capture.get_template_size = Mock(return_value=(10, 10))
        capture._tesseract_balance = Mock(side_effect=[("324", .99), ("324", .1)])
        frame = np.zeros((100, 100), dtype=np.uint8)
        value, _, _, roi = capture.read_balance(combo, frame, (100, 200, 100, 100))
        self.assertEqual(value, 324)
        self.assertEqual(roi.shape, (12, 20))
        self.assertEqual(capture.get_balance_debug(),
                         (True, 1, (110, 210, 20, 20), (134, 206, 20, 12)))
        self.assertIsNone(capture.read_balance(combo, frame, (100, 200, 100, 100))[0])
        capture.match_template.return_value = (False, .1, None)
        capture.read_balance(combo, frame, (100, 200, 100, 100))
        self.assertEqual(capture.get_balance_debug(), (False, .1, None, None))

    def test_balance_ocr_isolates_white_digits_from_color_frame(self):
        combo = Combo(image_path="icon", calc_region=(0, 0, 4, 4))
        capture = ScreenCapture()
        capture.match_template = Mock(return_value=(True, 1, (2, 2)))
        capture.get_match_size = Mock(return_value=(4, 4))
        capture.get_template_size = Mock(return_value=(4, 4))
        capture._tesseract_balance = Mock()
        frame = np.full((4, 4, 3), (240, 190, 40), dtype=np.uint8)
        frame[1:3, 1:3] = (255, 255, 255)

        def readtext(image):
            self.assertEqual(image.shape, (16, 16))
            self.assertEqual((image.min(), image.max()), (0, 255))
            return "795", .9

        capture._tesseract_balance.side_effect = readtext
        self.assertEqual(capture.read_balance(combo, frame)[0], 795)
        capture._tesseract_balance.assert_called_once()

    def test_fixed_digit_templates_read_scaled_balance(self):
        templates = _balance_templates()
        separator = np.zeros((32, 3), dtype=np.uint8)
        mask = np.concatenate([
            templates["2"][0].astype(np.uint8) * 255, separator,
            templates["8"][0].astype(np.uint8) * 255, separator,
            templates["0"][0].astype(np.uint8) * 255,
        ], axis=1)
        mask = cv2.resize(mask, None, fx=.8, fy=.8, interpolation=cv2.INTER_NEAREST)
        text, confidence = _template_balance(mask, templates)
        self.assertEqual(text, "280")
        self.assertGreater(confidence, .7)

    def test_tesseract_balance_reads_single_number(self):
        capture = ScreenCapture()
        capture._tesseract_path = "tesseract"
        capture._tessdata_dir = None
        output = (b"level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
                  b"5\t1\t1\t1\t1\t1\t0\t0\t20\t10\t83.5\t64\n")
        with patch("screen_capture.subprocess.run", return_value=Mock(returncode=0, stdout=output)):
            self.assertEqual(capture._tesseract_balance(np.zeros((20, 40), dtype=np.uint8)),
                             ("64", .835))

    def test_active_engine_never_processes_calc_combo(self):
        engine = HookEngine()
        engine._capture.match_template = Mock()
        engine._process_combo(Combo(detection_mode="background"), None, None)
        engine._capture.match_template.assert_not_called()

    def test_hotkeys_press_and_profile_rebinding(self):
        with patch.object(App, "_load_initial_profile"):
            app = App()
        with patch("gui.keyboard.add_hotkey", side_effect=range(1, 9)) as add, \
                patch("gui.keyboard.remove_hotkey") as remove:
            try:
                app.root.withdraw()
                app._register_hotkeys()
                self.assertEqual([call.args[0] for call in add.call_args_list], ["F6", "F7", "F9", "F10"])
                self.assertTrue(all(not call.kwargs.get("trigger_on_release", False)
                                    for call in add.call_args_list))
                add.call_args_list[1].args[1]()
                self.assertEqual(app._msg_queue.pop(), ("background_hotkey",))
                app.profile = Profile(hotkey="F8", background_hotkey="F9",
                                      debug_hotkey="F11", background_debug_hotkey="F12")
                app._refresh_hotkeys()
                self.assertEqual([call.args[0] for call in add.call_args_list],
                                 ["F6", "F7", "F9", "F10", "F8", "F9", "F11", "F12"])
                self.assertEqual([call.args[0] for call in remove.call_args_list], [1, 2, 3, 4])
            finally:
                app.on_close()
            self.assertEqual([call.args[0] for call in remove.call_args_list], list(range(1, 9)))
            self.assertEqual(app._hotkeys, [])

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
            profile = Profile(combos=[combo], statistics=[Statistic(source="ocr", combo_id=combo.id)])
            self.assertEqual(Profile.from_dict(profile.to_dict()).to_dict(), profile.to_dict())
            app.background.stop = Mock()
            app.engine._running = True
            with patch.object(app.engine, "stop"):
                app._stop()
            app.background.stop.assert_not_called()
            app.profile = profile
            app._show_calc()
            self.assertIn("900", app._statistics_window.summary.cget("text"))
        finally:
            app.on_close()

    def test_calc_limit_saves_selected_profile_and_rolls_back_on_failure(self):
        with patch.object(App, "_load_initial_profile"):
            app = App()
        try:
            app.root.withdraw()
            combo = Combo(detection_mode="background")
            stat = Statistic(source="ocr", combo_id=combo.id)
            app.profile = Profile(combos=[combo], statistics=[stat])
            other_profile = Profile(statistics=[Statistic(limit=1200)])
            app._show_calc()
            with patch("statistics_ui.StatisticEditor", return_value=Mock(result=replace(stat, limit=1100))), \
                    patch("statistics_ui.profile_manager.save_profile") as save:
                app._statistics_window._edit_stat()
            self.assertEqual(app.profile.statistics[0].limit, 1100)
            self.assertIn("1100", app._statistics_window.summary.cget("text"))
            self.assertEqual(Profile.from_dict(app.profile.to_dict()).statistics[0].limit, 1100)
            self.assertEqual(other_profile.statistics[0].limit, 1200)
            save.assert_called_once()
            with patch("statistics_ui.StatisticEditor", return_value=Mock(result=replace(stat, limit=1500))), \
                    patch("statistics_ui.profile_manager.save_profile", side_effect=OSError("disk full")), \
                    patch("statistics_ui.messagebox.showerror") as error:
                app._statistics_window._edit_stat()
            self.assertEqual(app.profile.statistics[0].limit, 1100)
            error.assert_called_once()
        finally:
            app.on_close()

    def test_ocr_records_remain_editable_during_background_detection(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(App, "_load_initial_profile"):
            app = App()
            try:
                app.root.withdraw()
                combo = Combo(detection_mode="background")
                app.profile = Profile(combos=[combo], statistics=[Statistic(source="ocr", combo_id=combo.id)])
                app.background.ledger = Ledger(folder)
                when = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
                app.background.ledger.record(app.profile, combo, 100, "100", .9, when)
                app._show_calc()
                window = app._statistics_window
                app.background.thread = Mock()
                app.background.thread.is_alive.return_value = True
                window.refresh(force=True)
                self.assertEqual(str(window.add_button.cget("state")), "normal")
                self.assertEqual(str(window.record_edit_button.cget("state")), "normal")
                self.assertEqual(str(window.record_delete_button.cget("state")), "normal")
                self.assertEqual(str(window.edit_button.cget("state")), "disabled")
                self.assertIn("紀錄可直接新增", window.summary.cget("text"))

                later = when.replace(minute=1)
                with patch("statistics_ui.simpledialog.askstring", return_value=later.strftime("%Y%m%d%H%M%S")), \
                        patch("statistics_ui.simpledialog.askinteger", return_value=140):
                    window._add_record()
                self.assertEqual([row["balance"] for row in app.background.ledger.rows], ["100", "140"])

                window.tree.selection_set("o:1")
                with patch("statistics_ui.simpledialog.askinteger", return_value=130):
                    window._edit_record()
                self.assertEqual(app.background.ledger.rows[1]["balance"], "130")

                window.tree.selection_set("o:1")
                with patch("statistics_ui.messagebox.askyesno", return_value=True):
                    window._delete_record()
                self.assertEqual([row["balance"] for row in app.background.ledger.rows], ["100"])
            finally:
                app.background.thread = None
                app.on_close()

    def test_new_statistic_from_empty_profile_and_editor_rules(self):
        with patch.object(App, "_load_initial_profile"):
            app = App()
        try:
            app.root.withdraw()
            app.profile = Profile()
            app._show_calc()
            self.assertIn("尚未建立統計", app._statistics_window.summary.cget("text"))
            with patch("statistics_ui.StatisticEditor", return_value=Mock(result=Statistic(name="手動收入"))), \
                    patch("statistics_ui.profile_manager.save_profile"):
                app._statistics_window._add_stat()
            self.assertEqual(app.profile.statistics[0].name, "手動收入")
            self.assertIn("手動收入", app._statistics_window.summary.cget("text"))
            with patch.object(tk.Toplevel, "wait_window"):
                editor = StatisticEditor(app.root, app.profile)
            editor.name.set("每週支出")
            editor.metric.set("支出")
            editor.period.set("週")
            editor.reset_time.set("12:00")
            editor.limit.set("")
            editor._save()
            self.assertEqual((editor.result.metric, editor.result.period,
                              editor.result.reset_time, editor.result.limit),
                             ("expense", "week", "12:00", None))
            combo = Combo(detection_mode="background")
            with patch.object(tk.Toplevel, "wait_window"):
                existing = StatisticEditor(app.root, Profile(combos=[combo]),
                                           Statistic(source="ocr", combo_id=combo.id))
            self.assertEqual(existing.source_choice.current(), 1)
            existing.win.destroy()
        finally:
            app.on_close()

    def test_save_as_creates_independent_statistic_identity(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(App, "_load_initial_profile"):
            app = App()
            try:
                app.root.withdraw()
                original = app.profile
                original.statistics.append(Statistic(name="手動收入"))
                with patch("gui.profile_manager.PROFILES_DIR", Path(folder)), \
                        patch("gui.simpledialog.askstring", return_value="copy"):
                    app._save_profile_as()
                self.assertNotEqual(app.profile.id, original.id)
                self.assertEqual(app.profile.statistics[0].id, original.statistics[0].id)
                self.assertEqual(app._profile_path.name, "copy.json")
            finally:
                app.on_close()

    def test_single_dialog_saves_all_hotkeys_and_rolls_back_on_failure(self):
        with patch.object(App, "_load_initial_profile"):
            app = App()
        try:
            def complete(dialog, *_args):
                self.assertEqual(len(dialog.entries), 4)
                for entry, value in zip(dialog.entries, ("F8", "F9", "F11", "F12")):
                    entry.delete(0, "end")
                    entry.insert(0, value)
                dialog.ok()
            with patch.object(tk.Toplevel, "wait_window", autospec=True, side_effect=complete), patch("gui.profile_manager.save_profile") as save:
                app._configure_hotkeys()
                self.assertEqual((app.profile.hotkey, app.profile.background_hotkey), ("F8", "F9"))
                self.assertEqual((app.profile.debug_hotkey, app.profile.background_debug_hotkey), ("F11", "F12"))
                save.assert_called_once()
            app.profile.hotkey, app.profile.background_hotkey = "F6", "F7"
            app.profile.debug_hotkey, app.profile.background_debug_hotkey = "F9", "F10"
            with patch.object(tk.Toplevel, "wait_window", autospec=True, side_effect=complete), patch("gui.profile_manager.save_profile", side_effect=OSError("disk full")), patch("gui.messagebox.showerror") as error:
                app._configure_hotkeys()
                self.assertEqual((app.profile.hotkey, app.profile.background_hotkey), ("F6", "F7"))
                self.assertEqual((app.profile.debug_hotkey, app.profile.background_debug_hotkey), ("F9", "F10"))
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
                self.assertEqual(len(app._combo_logs[combo.id]), 1)
                app._msg_queue.append(("background_read", combo.id, "餘額 324"))
                app._poll_queue()
                self.assertEqual(len(app._combo_logs[combo.id]), 1)
                self.assertEqual(app.tree.set(combo.id, "scale"), "80.0%")
                app._stop()
                self.assertEqual(app.tree.set(combo.id, "status"), "餘額 324")
                app._msg_queue.append(("background_status", "目標視窗不可用，背景暫停讀取"))
                app._poll_queue()
                self.assertIn("暫停", app.tree.set(combo.id, "status"))
                app.engine._running = True
                app._msg_queue.append(("status", False))
                app._poll_queue()
                self.assertEqual(app.btn_toggle.cget("style"), "danger.TButton")
                app.engine._running = False
        finally:
            app.on_close()

    def test_image_selection_does_not_run_verification_ocr(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with tempfile.TemporaryDirectory() as folder:
                from PIL import Image
                path = Path(folder) / "icon.png"
                Image.new("RGB", (16, 24), "yellow").save(path)
                with patch.object(tk.Toplevel, "wait_window"):
                    editor = ComboEditor(root, Combo(detection_mode="background"))
                with patch("screen_capture.ocr_read_image") as ocr, \
                        patch("gui.filedialog.askopenfilename", return_value=str(path)):
                    for mode in ("background", "active"):
                        editor.var_detection_mode.set(mode)
                        editor.var_verify_text.set("既有驗證文字")
                        editor._browse_image()
                        self.assertEqual(editor.var_image.get(), str(path))
                        self.assertEqual(editor.var_verify_text.get(), "既有驗證文字")
                        self.assertIn("icon.png (16x24)", editor.lbl_preview.cget("text"))
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
                for values in (("", "F7", "F9", "F10"), ("F7", "F7", "F9", "F10"),
                               ("invalid-key", "F7", "F9", "F10"), ("F9", "F7", "F9", "F10")):
                    fill(values)
                    dialog.ok()
                    self.assertTrue(dialog.winfo_exists())
                    self.assertEqual(tuple(e.get() for e in dialog.entries), values)
                fill(("F8", "F9", "F11", "F12"))
                dialog.ok()
            with patch.object(tk.Toplevel, "wait_window", autospec=True, side_effect=complete), patch("gui.profile_manager.save_profile") as save, patch("gui.messagebox.showerror") as error:
                app._configure_hotkeys()
                self.assertEqual(error.call_count, 4)
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
            app.profile = Profile(combos=[combo], statistics=[Statistic(source="ocr", combo_id=combo.id)])
            app._show_calc()
            with patch.object(BackgroundCalc, "running", new_callable=lambda: property(lambda _: True)), patch.object(app.background, "reset") as reset, patch("gui.messagebox.showinfo"):
                app._update_calc_view()
                self.assertEqual(str(app._statistics_window.reset_button.cget("state")), "disabled")
                app._statistics_window._reset()
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
                self.assertEqual(rects, [])
                app.engine._running = True
                app._debug_mode.set(True)
                app._on_debug_info(active.id, True, .9, (100, 100, 20, 20))
                app._on_debug_mode_toggle()
                self.assertEqual(len(overlay.return_value.update_rects.call_args.args[0]), 1)
                app._stop()
                overlay.return_value.destroy.assert_not_called()
                self.assertEqual(overlay.return_value.update_rects.call_args.args[0], [])
                app._background_debug_mode.set(False)
                app._on_debug_mode_toggle()
                overlay.return_value.destroy.assert_called_once()
        finally:
            app.on_close()

    def test_debug_frame_is_drawn_outside_capture_region(self):
        overlay = DebugOverlay.__new__(DebugOverlay)
        overlay.canvas = Mock()
        overlay._sync_transparency = Mock()
        overlay.update_rects([(10, 20, 30, 40, "OCR", True, .8)])
        self.assertEqual(overlay.canvas.create_rectangle.call_args_list[0].args,
                         (7, 17, 43, 63))


if __name__ == "__main__":
    unittest.main()
