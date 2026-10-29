import tempfile
import unittest
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from calc import Ledger
import profile_manager
from models import Combo, Profile, Statistic
from stat_tracking import ManualLedger, crossing_level, statistic_rows, statistic_value


class StatisticsTests(unittest.TestCase):
    def test_edit_ocr_fields_preserves_other_rows_and_updates_period_totals(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(folder)
            profile, combo = Profile(), Combo()
            stat = Statistic(source="ocr", combo_id=combo.id, metric="net")
            for hour, balance in ((9, 100), (10, 150), (11, 170)):
                ledger.record(profile, combo, balance, str(balance), .9, datetime(2026, 9, 18, hour))
            original = deepcopy(ledger.rows)
            ledger.edit_record(1, datetime(2026, 9, 19, 8), "reset", -30, 900, expected=original[1])
            ledger.load(profile)
            self.assertEqual(ledger.rows[:2], [original[0], original[2]])
            edited = ledger.rows[2]
            self.assertEqual((edited["timestamp"], edited["record_type"], edited["delta"], edited["balance"]),
                             ("20260919080000", "reset", "-30", "900"))
            for date, total in (("20260918", 20), ("20260919", -30)):
                self.assertEqual(statistic_value(stat, statistic_rows(stat, profile.id, ledger.rows, [], date)), total)
            with self.assertRaises(ValueError):
                ledger.edit_record(1, datetime(2026, 9, 19), "change", 0, 0, expected=original[1])
            # New observations use the corrected latest balance.
            row = ledger.record(profile, combo, 910, "910", .9, datetime(2026, 9, 19, 9))
            self.assertEqual(row["delta"], "10")

    def test_balance_only_edit_does_not_change_any_delta(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(folder)
            profile, combo = Profile(), Combo()
            for hour, balance in ((9, 100), (10, 150), (11, 170)):
                ledger.record(profile, combo, balance, str(balance), .9, datetime(2026, 9, 18, hour))
            ledger.edit_record(1, datetime(2026, 9, 18, 10), "change", 50, 500)
            ledger.load(profile)
            for reset in ("00:00", "09:30"):
                stat = Statistic(source="ocr", combo_id=combo.id, reset_time=reset, metric="net")
                values = statistic_rows(stat, profile.id, ledger.rows, [], "20260918")
                self.assertEqual([r["delta"] for r in values], [0, 50, 20] if reset == "00:00" else [50, 20])
            self.assertEqual(ledger.rows[1]["balance"], "500")

    def test_manual_four_fields_are_independent_and_survive_moving_date(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = ManualLedger(folder)
            profile, stat = Profile(), Statistic(metric="net")
            first = ledger.add(profile.id, stat.id, "income", 100, datetime(2026, 9, 18, 9))
            second = ledger.add(profile.id, stat.id, "expense", 20, datetime(2026, 9, 18, 10))
            ledger.edit_record(first["id"], datetime(2026, 9, 19, 11), "expense", 7, 42)
            ledger.load()
            self.assertEqual(next(r for r in ledger.rows if r["id"] == second["id"]), second)
            rows = statistic_rows(stat, profile.id, [], ledger.rows, "20260919")
            self.assertEqual((rows[0]["kind"], rows[0]["delta"], rows[0]["balance"]), ("expense", 7, "42"))
            self.assertEqual(statistic_value(stat, rows), 7)
            self.assertEqual(statistic_value(stat, statistic_rows(stat, profile.id, [], ledger.rows, "20260918")), -20)
            for delta, balance in ((1.5, 1), (1, -1), (True, 1)):
                with self.assertRaises(ValueError):
                    ledger.edit_record(first["id"], datetime(2026, 9, 19), "income", delta, balance)

    def test_cross_date_write_failure_rolls_back_both_files(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = ManualLedger(folder)
            row = ledger.add("profile", "stat", "income", 10, datetime(2026, 9, 18))
            before = {p.name: p.read_bytes() for p in Path(folder).glob("*.csv")}
            replace = Path.replace

            def fail_destination(path, target):
                if path.name == ".20260919.csv.tmp":
                    raise OSError("disk full")
                return replace(path, target)

            with patch.object(Path, "replace", fail_destination):
                with self.assertRaises(OSError):
                    ledger.edit_record(row["id"], datetime(2026, 9, 19), "expense", -20, 100)
            self.assertEqual({p.name: p.read_bytes() for p in Path(folder).glob("*.csv")}, before)
            self.assertEqual(ledger.rows, [row])
            ledger.load()
            self.assertEqual(ledger.rows, [row])

    def test_interrupted_cross_date_edit_recovers_on_load(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = ManualLedger(folder)
            row = ledger.add("profile", "stat", "income", 10, datetime(2026, 9, 18))
            replace = Path.replace

            def interrupt(path, target):
                if path.name == ".20260919.csv.tmp":
                    raise KeyboardInterrupt()
                return replace(path, target)

            with patch.object(Path, "replace", interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    ledger.edit_record(row["id"], datetime(2026, 9, 19), "expense", -20, 100)
            restored = ManualLedger(folder)
            restored.load()
            self.assertEqual(restored.rows, [row])
            self.assertFalse((Path(folder) / ".pending-edit.json").exists())

    def test_same_second_observations_use_latest_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(folder)
            profile, combo = Profile(), Combo()
            stat = Statistic(source="ocr", combo_id=combo.id, metric="net")
            for balance in (100, 150, 170):
                ledger.record(profile, combo, balance, str(balance), 1, datetime(2026, 9, 18, 9))
            ledger.load(profile)
            self.assertEqual(statistic_value(stat, statistic_rows(stat, profile.id, ledger.rows, [], "20260918")), 70)

    def test_record_editor_keeps_fields_independent_and_validates(self):
        import tkinter as tk
        import ttkbootstrap as ttk
        from statistics_ui import RecordEditor
        ttk.Style.instance = None
        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(tk.Toplevel, "wait_window"):
                editor = RecordEditor(root, dict(timestamp="20260918090000", kind="change", delta=50, balance=150), "ocr")
            editor.kind.set("重設")
            editor.balance.set("999")
            self.assertEqual(editor.delta.get(), "50")
            editor.timestamp.set("invalid")
            with patch("statistics_ui.messagebox.showerror") as error:
                editor._save()
                error.assert_called_once()
            self.assertIsNone(editor.result)
            editor.timestamp.set("20260919123456")
            editor._save()
            self.assertEqual(editor.result, dict(timestamp=datetime(2026, 9, 19, 12, 34, 56),
                                                 kind="reset", delta=50, balance=999))
            ttk.Style().theme_use("darkly")
        finally:
            root.destroy()
            ttk.Style.instance = None

    def test_old_profile_settings_migrate_once_without_old_csv(self):
        combo = Combo(detection_mode="background", daily_limit=1200,
                      daily_reset_time="12:00", warning_ratio=0.75)
        old_profile = Profile(combos=[combo]).to_dict()
        del old_profile["statistics"]
        migrated = Profile.from_dict(old_profile)
        self.assertEqual(len(migrated.statistics), 1)
        stat = migrated.statistics[0]
        self.assertEqual((stat.source, stat.combo_id, stat.limit, stat.reset_time, stat.warning_ratio),
                         ("ocr", combo.id, 1200, "12:00", 0.75))
        self.assertEqual(Profile.from_dict(migrated.to_dict()).statistics[0].id, stat.id)
        self.assertEqual(Profile.from_dict(dict(old_profile, statistics=[])).statistics, [])

    def test_default_ledger_ignores_old_calc_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root / "calc"
            old.mkdir()
            old_file = old / "20260918.csv"
            old_file.write_text("old data", encoding="utf-8")
            with patch("calc.PROFILES_DIR", root / "profiles"):
                ledger = Ledger()
                ledger.load()
                self.assertEqual(ledger.rows, [])
                profile, combo = Profile(), Combo()
                row = ledger.record(profile, combo, 100, "100", 1.0, datetime(2026, 9, 18, 9))
                self.assertEqual(row["record_type"], "baseline")
                self.assertEqual(ledger.snapshot(profile.id, combo.id, "20260918")[:3], (0, 0, 0))
                self.assertEqual(ledger.directory, root / "statistics" / "ocr")
            self.assertEqual(old_file.read_text(encoding="utf-8"), "old data")

    def test_profile_migration_save_preserves_original_on_write_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            combo = Combo(detection_mode="background")
            old_data = Profile(combos=[combo]).to_dict()
            del old_data["statistics"]
            path = directory / "old.json"
            path.write_text(json.dumps(old_data), encoding="utf-8")
            with patch("profile_manager.PROFILES_DIR", directory):
                migrated = profile_manager.load_profile(path)
                with patch.object(Path, "replace", side_effect=OSError("disk full")):
                    with self.assertRaises(OSError):
                        profile_manager.save_profile(migrated, path.name)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8")), old_data)
                profile_manager.save_profile(migrated, path.name)
                self.assertEqual(len(profile_manager.load_profile(path).statistics), 1)

    def test_same_ocr_source_supports_different_rules_and_reset_times(self):
        profile, combo = Profile(), Combo()
        rows = [dict(profile_id=profile.id, combo_id=combo.id, timestamp=stamp,
                     balance=str(balance), record_type=kind)
                for stamp, balance, kind in (
                    ("20260923115900", 100, "baseline"),
                    ("20260923130000", 160, "change"),
                    ("20260924110000", 200, "change"),
                    ("20260924120000", 50, "reset"),
                    ("20260924130000", 80, "change"))]
        noon = Statistic(source="ocr", combo_id=combo.id, reset_time="12:00")
        midnight = Statistic(source="ocr", combo_id=combo.id, reset_time="00:00", metric="expense")
        noon_rows = statistic_rows(noon, profile.id, rows, [], "20260923")
        midnight_rows = statistic_rows(midnight, profile.id, rows, [], "20260924")
        self.assertEqual(statistic_value(noon, noon_rows), 40)
        self.assertEqual(statistic_value(midnight, midnight_rows), 0)
        self.assertEqual(statistic_value(noon, statistic_rows(noon, profile.id, rows, [], "20260924")), 30)
        self.assertEqual(crossing_level(Statistic(limit=30), 20, 30), "已達上限")
        self.assertIsNone(crossing_level(Statistic(limit=30), 30, 40))

    def test_manual_records_are_isolated_editable_and_persistent(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = ManualLedger(folder)
            first, second = Profile(), Profile()
            stat = Statistic(metric="net")
            when = datetime(2026, 9, 18, 10)
            income = ledger.add(first.id, stat.id, "income", 100, when)
            ledger.add(first.id, stat.id, "expense", 25, when)
            ledger.add(second.id, stat.id, "income", 500, when)
            ledger.load()
            rows = statistic_rows(stat, first.id, [], ledger.rows, "20260918")
            self.assertEqual(statistic_value(stat, rows), 75)
            ledger.edit(income["id"], "income", 120)
            ledger.load()
            self.assertEqual(statistic_value(stat, statistic_rows(
                stat, first.id, [], ledger.rows, "20260918")), 95)
            ledger.delete(income["id"])
            ledger.load()
            self.assertEqual(statistic_value(stat, statistic_rows(
                stat, first.id, [], ledger.rows, "20260918")), -25)
            with self.assertRaises(ValueError):
                ledger.add(first.id, stat.id, "income", 0, when)


if __name__ == "__main__":
    unittest.main()
