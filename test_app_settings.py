import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app_settings
from app_settings import load_app_settings, save_app_settings


class AppSettingsTest(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app_settings.json"
            expected = {"window": {
                "width": 1000, "height": 700, "x": 40, "y": 50,
                "maximized": False,
            }}
            save_app_settings(expected, path)
            self.assertEqual(load_app_settings(path), expected)
            self.assertFalse(path.with_suffix(".tmp").exists())

    def test_invalid_or_missing_file_returns_empty_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app_settings.json"
            self.assertEqual(load_app_settings(path), {})
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(load_app_settings(path), {})
            path.write_text(json.dumps([]), encoding="utf-8")
            self.assertEqual(load_app_settings(path), {})

    def test_reads_old_settings_until_new_folder_is_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            old_path = base / "app_settings.json"
            old_path.write_text('{"window": {"width": 900}}', encoding="utf-8")
            new_path = base / "settings" / "app_settings.json"
            with patch.object(app_settings, "_BASE_DIR", base), \
                    patch.object(app_settings, "SETTINGS_PATH", new_path):
                self.assertEqual(load_app_settings(), {"window": {"width": 900}})
                save_app_settings({"window": {"width": 1000}})
                self.assertEqual(load_app_settings(), {"window": {"width": 1000}})
            self.assertTrue(old_path.exists())
