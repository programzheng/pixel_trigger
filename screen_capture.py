"""螢幕截圖與模板比對模組（使用 Pillow ImageGrab，無執行緒問題）"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import ImageGrab


class ScreenCapture:
    """螢幕截圖 + OpenCV 模板比對 + OCR 文字驗證"""

    def __init__(self):
        # 已載入的模板快取: image_path → (gray_template, w, h, mask_or_None)
        self._cache: dict[str, tuple[np.ndarray, int, int, np.ndarray | None]] = {}
        # easyocr Reader（lazy init，首次使用時才載入）
        self._ocr_reader = None

    def load_template(self, path: str) -> bool:
        """載入模板圖片到快取，回傳是否成功。
        使用 numpy.fromfile + cv2.imdecode 支援中文路徑。
        若圖片有 alpha 通道，會自動提取為 mask 供比對時使用。
        """
        if path in self._cache:
            return True
        try:
            buf = np.fromfile(path, dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
            if img is None:
                return False

            mask: np.ndarray | None = None
            if img.ndim == 2:
                gray = img
            elif img.shape[2] == 4:
                # BGRA — 提取 alpha 通道作為 mask
                alpha = img[:, :, 3]
                mask = alpha if np.any(alpha == 0) else None
                gray = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
            else:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            h, w = gray.shape
            self._cache[path] = (gray, w, h, mask)
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

        template, tw, th, mask = self._cache[image_path]

        fh, fw = frame.shape
        if tw > fw or th > fh:
            return False, 0.0, None

        if mask is not None:
            # TM_CCORR_NORMED 是唯一支援 mask 的正向比對方法
            result = cv2.matchTemplate(
                frame, template, cv2.TM_CCORR_NORMED, mask=mask,
            )
        else:
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

    def get_template_size(self, image_path: str) -> tuple[int, int] | None:
        """回傳已快取模板的 (w, h)，未快取則回傳 None"""
        entry = self._cache.get(image_path)
        return (entry[1], entry[2]) if entry else None

    # ── OCR 文字驗證 ──────────────────────────────────────

    def _ensure_ocr(self):
        """Lazy-load easyocr Reader（首次呼叫時約需數秒）"""
        if self._ocr_reader is None:
            import easyocr
            self._ocr_reader = easyocr.Reader(
                ["ch_tra", "en"], gpu=False, verbose=False,
            )

    def ocr_verify(
        self,
        roi: np.ndarray,
        expected_text: str,
        threshold: float = 0.5,
        match_mode: str = "contains",
        match_ratio: float = 0.6,
    ) -> tuple[bool, float]:
        """對灰階 ROI 區域做 OCR，依匹配模式檢查文字。
        match_mode: "contains"=包含, "exact"=完全符合, "partial"=部分符合
        回傳 (是否找到, 最高信心度)
        """
        if roi.size == 0:
            return False, 0.0
        self._ensure_ocr()
        try:
            results = self._ocr_reader.readtext(roi)
        except Exception:
            return False, 0.0

        best_conf = 0.0
        for _bbox, text, conf in results:
            matched = self._text_match(expected_text, text, match_mode, match_ratio)
            if matched and conf >= threshold:
                return True, conf
            if matched:
                best_conf = max(best_conf, conf)
        return False, best_conf

    @staticmethod
    def _text_match(
        expected: str, detected: str,
        mode: str = "contains", ratio: float = 0.6,
    ) -> bool:
        """依模式比對文字"""
        if mode == "exact":
            return detected.strip() == expected
        elif mode == "partial":
            from difflib import SequenceMatcher
            sim = SequenceMatcher(None, expected, detected).ratio()
            return sim >= ratio
        else:  # contains
            return expected in detected


# ── 獨立 OCR 工具函式（GUI 等外部模組使用）─────────────────

_shared_ocr_reader = None


def ocr_read_image(path: str) -> str:
    """讀取圖片檔中的所有文字，回傳合併字串。首次呼叫需數秒載入模型。
    若 easyocr 未安裝則回傳空字串。
    """
    global _shared_ocr_reader
    if _shared_ocr_reader is None:
        try:
            import easyocr
        except ImportError:
            return ""
        _shared_ocr_reader = easyocr.Reader(
            ["ch_tra", "en"], gpu=False, verbose=False,
        )
    try:
        buf = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if img is None:
            return ""
        results = _shared_ocr_reader.readtext(img)
        return "".join(text for _, text, _ in results)
    except Exception:
        return ""
