import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from gui import _create_log_logger


class DailyLogTest(unittest.TestCase):
    def test_utf8_midnight_rotation_and_restart_append(self):
        with tempfile.TemporaryDirectory() as folder, patch("gui.Path.cwd", return_value=Path(folder)):
            logger = _create_log_logger()
            handler = logger.handlers[0]
            path = Path(folder) / "logs" / "pixel_trigger.log"
            try:
                logger.warning("[釣魚] 色帶未達啟動比例")
                self.assertIn("[WARNING] [釣魚]", path.read_text(encoding="utf-8"))
                self.assertEqual(handler.backupCount, 0)
                # 模擬已跨過本機午夜，下一筆紀錄應輪替昨天的檔案。
                handler.rolloverAt = int(datetime.now().replace(
                    hour=0, minute=0, second=0, microsecond=0).timestamp())
                suffix = time.strftime("%Y-%m-%d", time.localtime(handler.rolloverAt - handler.interval))
                logger.error("[釣魚] 新的一天")
                archive = path.with_name(path.name + "." + suffix)
                self.assertIn("色帶未達啟動比例", archive.read_text(encoding="utf-8"))
                self.assertNotIn("新的一天", archive.read_text(encoding="utf-8"))
                current = path.read_text(encoding="utf-8")
                self.assertIn("[ERROR] [釣魚] 新的一天", current)
                self.assertNotIn("色帶未達啟動比例", current)
            finally:
                handler.close()
            restarted = _create_log_logger()
            try:
                restarted.info("重新啟動")
                current = path.read_text(encoding="utf-8")
                self.assertIn("新的一天", current)
                self.assertIn("重新啟動", current)
                self.assertIn("色帶未達啟動比例", archive.read_text(encoding="utf-8"))
            finally:
                restarted.handlers[0].close()


if __name__ == "__main__":
    unittest.main()
