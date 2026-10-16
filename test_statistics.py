import tempfile
import unittest
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from calc import Ledger
import profile_manager
from models import Combo, Profile, Statistic
from stat_tracking import ManualLedger, crossing_level, statistic_rows, statistic_value


class StatisticsTests(unittest.TestCase):
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
