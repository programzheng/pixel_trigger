"""Color-band classification checks."""

import math
from pathlib import Path
import unittest

import cv2
import numpy as np
from PIL import Image

from models import PositionControl
from screen_capture import find_color_marker, color_band_samples, color_band_state


def background_action(frame, marker_rect):
    marker = ((0, 0), marker_rect) if marker_rect is not None else None
    active, ratio = color_band_state(frame, marker, PositionControl())
    return ("hold" if active else "release"), ratio


def gauge_frame(angle, scale=1, brightness=1):
    frame = np.zeros((180, 240, 3), dtype=np.uint8)
    for end, color in ((335, (220, 65, 35)), (322, (220, 170, 65)), (300, (115, 170, 75))):
        cv2.ellipse(frame, (120, 150), (100, 100), 0, 205, end, color, 26)
    theta = math.radians(angle)
    radial = np.array([math.cos(theta), math.sin(theta)])
    tangent = np.array([-radial[1], radial[0]])
    center = np.array([120, 150]) + radial * 100
    points = np.rint([center + radial * 8, center - radial * 6 + tangent * 7,
                      center - radial * 6 - tangent * 7]).astype(np.int32)
    cv2.fillPoly(frame, [points], (255, 170, 41))
    rect = cv2.boundingRect(points)
    if scale != 1:
        frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        rect = tuple(round(v * scale) for v in rect)
    return np.uint8(frame.astype(float) * brightness), rect


class BackgroundColorExperiment(unittest.TestCase):
    def test_preview_samples_match_runtime_ratio(self):
        settings = PositionControl(band_margin=1)
        frame = np.full((40, 40, 3), (115, 170, 75), dtype=np.uint8)
        frame[:, :20] = (220, 65, 35)
        marker = ((20, 20), (15, 15, 10, 10))
        x, y, valid, matched = color_band_samples(frame, marker, settings)
        _, ratio = color_band_state(frame, marker, settings)
        self.assertEqual((x, y), (5, 5))
        self.assertFalse(valid[10:20, 10:20].any())  # 箭頭不參與比例
        self.assertTrue(matched.any())
        self.assertTrue((valid & ~matched).any())
        self.assertEqual(ratio, np.count_nonzero(matched) / np.count_nonzero(valid))

    def test_hysteresis_transparency_and_serialization(self):
        from models import Combo, TriggerMode
        settings = PositionControl(band_start=0.8, band_stop=0.6)
        frame = np.full((60, 60, 4), (115, 170, 75, 255), dtype=np.uint8)
        marker = ((30, 30), (25, 25, 10, 10))
        frame[:, :25, :3] = (220, 65, 35)
        inactive, ratio = color_band_state(frame, marker, settings)
        active, _ = color_band_state(frame, marker, settings, active=True)
        self.assertGreater(ratio, 0.6)
        self.assertLess(ratio, 0.8)
        self.assertFalse(inactive)
        self.assertTrue(active)
        frame[:, :, 3] = 0
        self.assertEqual(color_band_state(frame, marker, settings, True), (False, None))
        combo = Combo(trigger=TriggerMode.COLOR_BAND, position_control=settings)
        self.assertEqual(Combo.from_dict(combo.to_dict()), combo)
        settings.band_stop = settings.band_start
        with self.assertRaises(ValueError):
            settings.validate()

    def test_curved_gauge_colors_at_different_scales_and_brightness(self):
        for angle, expected in ((-125, "hold"), (-50, "release"), (-32, "release"), (-60, "release")):
            for scale in (0.5, 1, 2):
                for brightness in (0.7, 1):
                    with self.subTest(angle=angle, scale=scale, brightness=brightness):
                        frame, rect = gauge_frame(angle, scale, brightness)
                        self.assertEqual(background_action(frame, rect)[0], expected)

    def test_missing_marker_and_uncolored_background_release(self):
        self.assertEqual(background_action(None, None)[0], "release")
        frame = np.full((60, 60, 3), 255, dtype=np.uint8)
        self.assertEqual(background_action(frame, (20, 20, 10, 10))[0], "release")

    def test_color_detector_keeps_arrow_in_same_color_band(self):
        settings = PositionControl(region_space="screen", color=(255, 170, 41),
                                   tolerance=15, min_area=40, max_area=500)
        green, _ = gauge_frame(-125)
        yellow, _ = gauge_frame(-50)
        self.assertIsNotNone(find_color_marker(green, settings))
        self.assertIsNotNone(find_color_marker(yellow, settings))

        frame = np.zeros((40, 40, 3), dtype=np.uint8)
        frame[5:15, 5:15] = settings.color
        target = cv2.cvtColor(np.uint8([[settings.color]]), cv2.COLOR_RGB2HSV)[0, 0]
        loose_color = np.uint8([[[target[0], target[1] - 50, target[2]]]])
        frame[20:30, 20:30] = cv2.cvtColor(loose_color, cv2.COLOR_HSV2RGB)[0, 0]
        self.assertIsNotNone(find_color_marker(frame, settings))

    def test_available_real_green_zone_image(self):
        path = Path(__file__).parent / "dist/pyinstaller/pixel_trigger/templates/fish_hold.png"
        if not path.exists():
            self.skipTest("本機碼表截圖不存在；模擬測試仍可獨立執行")
        frame = np.array(Image.open(path).convert("RGB"))
        settings = PositionControl(region_space="template", region=(10, 3, 276, 99),
                                   color=(255, 170, 41), tolerance=5,
                                   min_area=150, max_area=280)
        marker = find_color_marker(frame, settings)
        self.assertIsNotNone(marker)
        action, ratios = background_action(frame, marker[1])
        print("Real green-zone background:", action, ratios)
        self.assertEqual(action, "hold")


if __name__ == "__main__":
    unittest.main(verbosity=2)
