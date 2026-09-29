import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

from screen_capture import MATCH_SCALES, ScreenCapture, _run_tesseract
from automation import HookEngine
from models import Combo, Profile


class MultiScaleMatchTest(unittest.TestCase):
    def test_tesseract_tsv_confidence_is_normalized(self):
        output = (b"level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
                  b"5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t87.5\tReady\n")
        completed = Mock(returncode=0, stdout=output)
        with patch("screen_capture.cv2.imencode", return_value=(True, np.array([1], dtype=np.uint8))), \
                patch("screen_capture.subprocess.run", return_value=completed) as run:
            words = _run_tesseract(
                np.zeros((2, 2), dtype=np.uint8), "tesseract.exe", "tessdata",
                psm=6, languages="chi_tra+eng",
            )
        self.assertEqual(words, [("Ready", 0.875)])
        self.assertIn("--tessdata-dir", run.call_args.args[0])
        self.assertEqual(
            run.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)

    def test_threshold_only_changes_acceptance_not_search_or_refinement(self):
        template = np.random.default_rng(31).integers(30, 230, (20, 20), dtype=np.uint8)
        frame = np.zeros((100, 100), dtype=np.uint8)
        for cached in (False, True):
            searches = []
            for threshold in (0.4, 0.5, 0.9):
                capture = ScreenCapture()
                capture._cache["gauge"] = (template, 20, 20, None)
                if cached:
                    capture._last_matches["gauge"] = (None, (0, 0), (20, 20), 1.0)

                def best(frame, resized, mask):
                    return (0.43, (0, 0)) if resized.shape == (20, 20) else (0.7, (7, 9))

                def refine(frame, template, mask, match, stop):
                    return ((0.43, (0, 0), (20, 20), 1.0) if match[0] == 0
                            else (0.72, (7, 9), (30, 30), 1.5))

                with patch("screen_capture.MATCH_SCALES", (1.0, 1.5)), \
                        patch("screen_capture._best_template_match", side_effect=best) as search, \
                        patch("screen_capture._refine_template_match", side_effect=refine) as fine:
                    found, score, center = capture.match_template(frame, "gauge", threshold)
                self.assertEqual(score, 0.72)
                self.assertEqual(found, threshold <= 0.72)
                self.assertEqual(center, (22, 24) if found else None)
                self.assertEqual(fine.call_count, 2 if cached else 1)
                searches.append([call.args[1].shape for call in search.call_args_list])
            self.assertEqual(searches, [[(20, 20), (30, 30)]] * 3)

    def test_fine_scale_and_small_size_change_reuse_original_template(self):
        template = np.full((128, 48), 20, dtype=np.uint8)
        cv2.rectangle(template, (9, 4), (38, 85), 210, -1)
        cv2.circle(template, (24, 109), 9, 210, -1)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "exclamation.png")
            cv2.imwrite(path, template)
            capture = ScreenCapture()
            for scale in (0.53, 0.56):
                w, h = round(48 * scale), round(128 * scale)
                frame = np.full((1024, 1280), 20, dtype=np.uint8)
                frame[521:521 + h, 17:17 + w] = cv2.resize(
                    template, (w, h), interpolation=cv2.INTER_AREA)
                attempts = 1 if scale == 0.56 else len(MATCH_SCALES)
                for _ in range(attempts):
                    found, confidence, pos = capture.match_template(frame, path, 0.99, scale_limit=1)
                    if found:
                        break
                self.assertTrue(found)
                self.assertGreaterEqual(confidence, 0.99)
                self.assertEqual(pos, (17 + w // 2, 521 + h // 2))
                self.assertEqual(capture.get_match_size(path), (w, h))
                self.assertAlmostEqual(capture.get_match_scale(path), scale, delta=0.011)

    def test_bounded_search_learns_and_recovers_scale(self):
        template = np.random.default_rng(14).integers(30, 230, (40, 48), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "template.png")
            cv2.imwrite(path, template)
            capture = ScreenCapture()
            for scale in (0.75, 4.0):
                frame = np.zeros((180, 210), dtype=np.uint8)
                w, h = round(48 * scale), round(40 * scale)
                frame[5:5 + h, 7:7 + w] = cv2.resize(
                    template, (w, h), interpolation=(
                        cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR))
                for _ in range(len(MATCH_SCALES)):
                    with patch("screen_capture.cv2.matchTemplate", wraps=cv2.matchTemplate) as match:
                        found, _, _ = capture.match_template(frame, path, 0.999, scale_limit=1)
                        # 上次位置細調 11 次 + 本輪最多 3 個比例 + 最佳候選細調 11 次。
                        self.assertLessEqual(match.call_count, 25)
                    if found:
                        break
                self.assertTrue(found)
                self.assertAlmostEqual(capture.get_match_scale(path), scale, delta=0.011)
                self.assertEqual(capture.get_match_size(path), (w, h))
                with patch("screen_capture.cv2.matchTemplate", wraps=cv2.matchTemplate) as match:
                    self.assertTrue(capture.match_template(frame, path, 0.999, scale_limit=1)[0])
                    self.assertEqual(match.call_count, 1)

    def test_large_frame_search_and_full_resolution_verification(self):
        template = np.random.default_rng(9).integers(
            30, 230, (40, 48), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "template.png")
            for masked in (False, True):
                image = template
                if masked:
                    image = cv2.cvtColor(template, cv2.COLOR_GRAY2BGRA)
                    image[:6, :, 3] = 0
                cv2.imwrite(path, image)
                for scale in (0.5, 0.75, 1.5, 4.0):
                    with self.subTest(masked=masked, scale=scale):
                        w, h = round(48 * scale), round(40 * scale)
                        frame = np.zeros((1080, 1920), dtype=np.uint8)
                        resized = cv2.resize(template, (w, h), interpolation=(
                            cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR))
                        # 不對齊縮圖像素，接近右下邊界，驗證原圖定位。
                        x, y = 1920 - w - 3, 1080 - h - 5
                        frame[y:y + h, x:x + w] = resized
                        if masked:
                            alpha = cv2.resize(image[:, :, 3], (w, h),
                                               interpolation=cv2.INTER_NEAREST)
                            frame[y:y + h, x:x + w][alpha == 0] = 255
                        capture = ScreenCapture()
                        found, confidence, pos = capture.match_template(frame, path, 0.95)
                        self.assertTrue(found)
                        self.assertGreaterEqual(confidence, 0.95)
                        self.assertEqual(pos, (x + w // 2, y + h // 2))
                        self.assertEqual(capture.get_match_size(path), (w, h))

    def test_first_cycle_reports_progress(self):
        engine = HookEngine()
        progress = Mock(side_effect=lambda count: engine._stop_event.set())
        engine._on_cycle_complete = progress
        engine.set_profile(Profile(combos=[]))
        with patch.object(engine._capture, "grab_frame", return_value=np.zeros((2, 2))):
            engine._scan_loop()
        progress.assert_called_once_with(1)

    def test_remember_scale_and_search_again_on_failure(self):
        template = np.random.default_rng(7).integers(
            30, 230, (40, 48), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "template.png")
            cv2.imwrite(path, template)
            capture = ScreenCapture()
            for scale in (0.75, 1.5, 3.0, 4.0):
                w, h = round(48 * scale), round(40 * scale)
                frame = np.zeros((180, 210), dtype=np.uint8)
                resized = cv2.resize(template, (w, h), interpolation=(
                    cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR))
                frame[5:5 + h, 7:7 + w] = resized
                with patch("screen_capture.cv2.matchTemplate",
                           wraps=cv2.matchTemplate) as match:
                    self.assertTrue(capture.match_template(frame, path, 0.95)[0])
                    self.assertGreater(match.call_count, 1)
                # 輕微畫面變化仍達固定的 0.999 信心門檻，可沿用記住的比例。
                frame[5, 7] = 255 - frame[5, 7]
                with patch("screen_capture.cv2.matchTemplate",
                           wraps=cv2.matchTemplate) as match:
                    self.assertTrue(capture.match_template(frame, path, 0.95)[0])
                    self.assertEqual(match.call_count, 1)
                    self.assertEqual(match.call_args.args[1].shape, (h, w))
                self.assertEqual(capture.get_match_scale(path), scale)
                self.assertFalse(capture.match_template(
                    np.zeros_like(frame), path, 0.95)[0])
                self.assertEqual(capture.get_match_scale(path), scale)
                # 圖示暫時消失不能清掉已學到的比例。
                with patch("screen_capture.cv2.matchTemplate",
                           wraps=cv2.matchTemplate) as match:
                    self.assertTrue(capture.match_template(frame, path, 0.95)[0])
                    self.assertEqual(match.call_count, 1)
            capture.unload_template(path)
            self.assertIsNone(capture.get_match_scale(path))
            with patch("screen_capture.cv2.matchTemplate",
                       wraps=cv2.matchTemplate) as match:
                self.assertTrue(capture.match_template(frame, path, 0.95)[0])
                self.assertGreater(match.call_count, 1)
                self.assertEqual(match.call_args_list[0].args[1].shape, template.shape)

    def test_scaled_matching_and_coordinates(self):
        rng = np.random.default_rng(42)
        template = rng.integers(30, 230, (40, 48), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "template.png")
            for masked in (False, True):
                image = template
                if masked:
                    image = cv2.cvtColor(template, cv2.COLOR_GRAY2BGRA)
                    image[:6, :, 3] = 0
                cv2.imwrite(path, image)
                capture = ScreenCapture()
                for scale in (0.5, 2 / 3, 0.75, 1.0, 1.25, 4 / 3, 1.5, 2.0, 3.0, 4.0):
                    with self.subTest(masked=masked, scale=scale):
                        w, h = round(48 * scale), round(40 * scale)
                        resized = cv2.resize(
                            template, (w, h), interpolation=(
                                cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR))
                        # 半尺寸案例的 frame 小於原範本，仍應匹配成功。
                        frame = np.zeros((h + 10, w + 12), dtype=np.uint8)
                        frame[3:3 + h, 5:5 + w] = resized
                        if masked:
                            alpha = cv2.resize(image[:, :, 3], (w, h),
                                               interpolation=cv2.INTER_NEAREST)
                            frame[3:3 + h, 5:5 + w][alpha == 0] = 255
                        found, confidence, pos = capture.match_template(
                            frame, path, 0.95, (100, 200, frame.shape[1], frame.shape[0]))
                        self.assertTrue(found)
                        self.assertGreaterEqual(confidence, 0.95)
                        self.assertEqual(pos, (105 + w // 2, 203 + h // 2))
                        self.assertEqual(capture.get_match_size(path), (w, h))
                        self.assertEqual(capture.get_template_size(path), (48, 40))
                found, confidence, pos = capture.match_template(
                    np.zeros((90, 110), dtype=np.uint8), path, 0.95)
                self.assertFalse(found)
                self.assertTrue(np.isfinite(confidence))
                self.assertIsNone(pos)
                self.assertIsNone(capture.get_match_size(path))
                capture.unload_template(path)
                self.assertIsNone(capture.get_template_size(path))

            # 執行期 OCR 裁切及 debug 框線都必須使用縮放後的尺寸。
            cv2.imwrite(path, template)
            frame = np.zeros((50, 60), dtype=np.uint8)
            resized = cv2.resize(template, (36, 30), interpolation=cv2.INTER_AREA)
            frame[7:37, 9:45] = resized
            debug = Mock()
            engine = HookEngine(on_debug_info=debug)
            combo = Combo(image_path=path, match_threshold=0.95, verify_text="OK")
            with patch.object(engine._capture, "ocr_verify",
                              return_value=(True, 1.0, "OK")) as ocr, \
                    patch.object(engine, "_execute_action_once") as action:
                for _ in range(len(MATCH_SCALES)):
                    engine._process_combo(combo, frame, (100, 200, 60, 50))
                    if action.called:
                        break
                np.testing.assert_array_equal(ocr.call_args.args[0], resized)
                self.assertEqual(debug.call_args.args[3], (109, 207, 36, 30))
                action.assert_called_once_with(combo)


if __name__ == "__main__":
    unittest.main()
