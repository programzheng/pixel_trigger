import ast
import unittest
from pathlib import Path

from localization import ENGLISH, set_language, tr


class LocalizationTest(unittest.TestCase):
    def tearDown(self):
        set_language("zh-TW")

    def test_translated_ui_literals_have_english_entries(self):
        for name in ("gui.py", "statistics_ui.py", "calc.py", "automation.py",
                     "screen_capture.py", "input_controller.py", "stat_tracking.py", "main.py"):
            tree = ast.parse(Path(__file__).with_name(name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "tr" and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)):
                    self.assertIn(node.args[0].value, ENGLISH, f"{name}:{node.lineno}")

    def test_language_defaults_and_formatted_messages(self):
        set_language("invalid")
        self.assertEqual(tr("儲存"), "儲存")
        set_language("en")
        self.assertEqual(tr("儲存"), "Save")
        self.assertEqual(tr("餘額 {value}").format(value=42), "Balance 42")
