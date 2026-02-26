"""螢幕截圖與模板比對模組（使用 Pillow ImageGrab，無執行緒問題）"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import ImageGrab


class ScreenCapture:
    """螢幕截圖 + OpenCV 模板比對"""

    def __init__(self):
        # 已載入的模板快取: image_path → (gray_template, w, h)
        self._cache: dict[str, tuple[np.ndarray, int, int]] = {}

    def load_template(self, path: str) -> bool:
        """載入模板圖片到快取，回傳是否成功。
        使用 numpy.fromfile + cv2.imdecode 支援中文路徑。
        """
        if path in self._cache:
            return True
        try:
            buf = np.fromfile(path, dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if img is None:
                return False
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            h, w = gray.shape
            self._cache[path] = (gray, w, h)
            return True
        except Exception:
            return False

    def unload_template(self, path: str):
        """從快取移除模板"""
        self._cache.pop(path, None)

    def grab_frame(
        self, region: tuple[int, int, int, int] | None = None
    ) -> np.ndarray | None:
        """截取螢幕一次，回傳灰階 frame。每個掃描週期只呼叫一次。
        使用 PIL.ImageGrab — 每次呼叫自行建立/釋放 GDI 資源，無執行緒問題。
        """
        try:
            if region:
                x, y, w, h = region
                screenshot = ImageGrab.grab(bbox=(x, y, x + w, y + h))
            else:
                screenshot = ImageGrab.grab()
            frame = np.array(screenshot)
            return cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        except Exception:
            return None

    def match_template(
        self,
        frame: np.ndarray,
        image_path: str,
        threshold: float = 0.8,
        region: tuple[int, int, int, int] | None = None,
    ) -> tuple[bool, float, tuple[int, int] | None]:
        """在已截取的 frame 中偵測模板圖片。
        回傳 (是否偵測到, 信心度, 螢幕絕對座標中心點 | None)
        """
        if image_path not in self._cache:
            if not self.load_template(image_path):
                return False, 0.0, None

        template, tw, th = self._cache[image_path]

        fh, fw = frame.shape
        if tw > fw or th > fh:
            return False, 0.0, None

        result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val >= threshold:
            cx = max_loc[0] + tw // 2
            cy = max_loc[1] + th // 2
            if region:
                cx += region[0]
                cy += region[1]
            return True, max_val, (cx, cy)

        return False, max_val, None
