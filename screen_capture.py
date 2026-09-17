"""螢幕截圖與模板比對模組（使用 Pillow ImageGrab，無執行緒問題）"""

from __future__ import annotations

import os
import sys
import threading
import cv2
import numpy as np
from PIL import ImageGrab

from models import Combo, PositionControl


def find_color_marker(frame: np.ndarray, settings: PositionControl):
    """從 RGB 區域找唯一的色彩標記；多個候選時不猜測。"""
    # ponytail: 顏色與面積需能排除背景；同大小的同色色塊仍需形狀辨識。
    if frame is None or frame.size == 0:
        return None
    hsv = cv2.cvtColor(frame, cv2.COLOR_RGB2HSV).astype(np.int16)
    target = cv2.cvtColor(np.uint8([[settings.color]]), cv2.COLOR_RGB2HSV)[0, 0]
    hue_delta = np.abs(hsv[:, :, 0] - int(target[0]))
    hue_delta = np.minimum(hue_delta, 180 - hue_delta)
    mask = ((hue_delta <= settings.tolerance)
            & (np.abs(hsv[:, :, 1] - int(target[1])) <= 80)
            & (np.abs(hsv[:, :, 2] - int(target[2])) <= 80)).astype(np.uint8)
    _, _, stats, centers = cv2.connectedComponentsWithStats(mask)
    areas = stats[1:, cv2.CC_STAT_AREA]
    area_scale = 1.0
    if settings.max_area and settings.region_space == "template" and settings.region:
        area_scale = frame.shape[0] * frame.shape[1] / (settings.region[2] * settings.region[3])
    eligible = areas >= settings.min_area * area_scale
    if settings.max_area:
        eligible &= areas <= settings.max_area * area_scale
    candidates = np.flatnonzero(eligible) + 1
    if len(candidates) != 1:
        return None
    index = candidates[0]
    return tuple(centers[index]), tuple(int(v) for v in stats[index, :4])


# ponytail: 等比例 0.5–4 倍；範圍不足時再擴大，不處理非等比例拉伸。
MATCH_SCALES = (1.0,) + tuple(
    sorted(({i / 20 for i in range(10, 81)} | {2 / 3, 4 / 3}) - {1.0},
           key=lambda scale: abs(scale - 1.0))
)


def _best_template_match(frame, template, mask):
    mode = cv2.TM_SQDIFF_NORMED if mask is not None else cv2.TM_CCOEFF_NORMED
    result = cv2.matchTemplate(frame, template, mode, mask=mask)
    result[~np.isfinite(result)] = np.inf if mask is not None else -1.0
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
    return (1.0 - min_val, min_loc) if mask is not None else (max_val, max_loc)


def _refine_template_match(frame, template, mask, match, stop_event):
    confidence, loc, size, scale = match
    cx, cy = loc[0] + size[0] // 2, loc[1] + size[1] // 2
    th, tw = template.shape
    seen_sizes = set()
    for offset in (0, 0.01, -0.01, 0.02, -0.02, 0.03, -0.03, 0.04, -0.04, 0.05, -0.05):
        if stop_event is not None and stop_event.is_set():
            break
        candidate_scale = round(scale + offset, 6)
        w, h = round(tw * candidate_scale), round(th * candidate_scale)
        if not 0.5 <= candidate_scale <= 4 or min(w, h) < 2 or (w, h) in seen_sizes:
            continue
        seen_sizes.add((w, h))
        margin = max(4, round(max(w, h) * 0.1))
        x1, y1 = max(0, cx - w // 2 - margin), max(0, cy - h // 2 - margin)
        roi = frame[y1:cy - h // 2 + h + margin, x1:cx - w // 2 + w + margin]
        if roi.shape[0] < h or roi.shape[1] < w:
            continue
        scaled = cv2.resize(template, (w, h), interpolation=(
            cv2.INTER_AREA if candidate_scale < 1 else cv2.INTER_LINEAR))
        scaled_mask = (cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
                       if mask is not None else None)
        if scaled_mask is not None and not np.any(scaled_mask):
            continue
        if scaled_mask is None and np.std(scaled) == 0:
            continue
        score, point = _best_template_match(roi, scaled, scaled_mask)
        if score > confidence:
            confidence = score
            match = (score, (point[0] + x1, point[1] + y1), (w, h), candidate_scale)
        if confidence >= 0.999:
            break
    return match


def _easyocr_model_dir() -> str | None:
    """回傳打包後的模型目錄路徑，開發模式或目錄不存在時回傳 None（使用預設 ~/.EasyOCR/）。"""
    if getattr(sys, '__compiled__', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    local = os.path.join(base, "models")
    return local if os.path.isdir(local) else None


class ScreenCapture:
    """螢幕截圖 + OpenCV 模板比對 + OCR 文字驗證"""

    def __init__(self):
        # 已載入的模板快取: image_path → (gray_template, w, h, mask_or_None)
        self._cache: dict[str, tuple[np.ndarray,
                                     int, int, np.ndarray | None]] = {}
        self._match_state = threading.local()
        self._preferred_scales: dict[str, float] = {}
        self._last_matches: dict[str, tuple] = {}
        self._scale_offsets: dict[str, int] = {}
        self._ocr_lock = threading.Lock()
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
                # BGRA — 保留 alpha 作為 mask，搭配 TM_SQDIFF_NORMED 使用
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
        getattr(self._match_state, "sizes", {}).pop(path, None)
        self._preferred_scales.pop(path, None)
        self._last_matches.pop(path, None)
        self._scale_offsets.pop(path, None)

    def grab_frame(
        self, region: tuple[int, int, int, int] | None = None, *, color: bool = False
    ) -> np.ndarray | None:
        """截取螢幕一次，回傳灰階 frame。每個掃描週期只呼叫一次。
        使用 PIL.ImageGrab — 每次呼叫自行建立/釋放 GDI 資源，無執行緒問題。
        """
        try:
            if region:
                x, y, w, h = region
                screenshot = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
            else:
                screenshot = ImageGrab.grab()
            frame = np.array(screenshot)
            if color:
                return frame
            return cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        except Exception:
            return None

    def match_template(
        self,
        frame: np.ndarray,
        image_path: str,
        threshold: float = 0.8,
        region: tuple[int, int, int, int] | None = None,
        scale_limit: int | None = None,
        stop_event=None,
        exclude_region: tuple[int, int, int, int] | None = None,
    ) -> tuple[bool, float, tuple[int, int] | None]:
        """先比對記住的比例（預設原尺寸），失敗才搜尋並記住新比例。
        回傳 (是否偵測到, 信心度, 螢幕絕對座標中心點 | None)
        """
        if not hasattr(self._match_state, "sizes"):
            self._match_state.sizes = {}
        self._match_state.sizes.pop(image_path, None)
        if image_path not in self._cache:
            if not self.load_template(image_path):
                return False, 0.0, None

        template, tw, th, mask = self._cache[image_path]
        if exclude_region is not None:
            x, y, w, h = exclude_region
            if x < 0 or y < 0 or x + w > tw or y + h > th:
                return False, 0.0, None
            mask = mask.copy() if mask is not None else np.full_like(template, 255)
            mask[y:y + h, x:x + w] = 0
            if not np.any(mask):
                return False, 0.0, None

        fh, fw = frame.shape
        best_conf = 0.0
        best_loc = None
        best_size = None
        best_scale = None
        seen_sizes = set()
        preferred_scale = self._preferred_scales.get(image_path, 1.0)
        last_match = self._last_matches.get(image_path)
        if last_match is not None and last_match[0] == region:
            _, loc, size, scale = last_match
            best_conf, best_loc, best_size, best_scale = _refine_template_match(
                frame, template, mask, (0.0, loc, size, scale), stop_event)
        fallback_scales = MATCH_SCALES[1:]
        offset = self._scale_offsets.get(image_path, 0)
        if scale_limit is not None:
            fallback_scales = tuple(fallback_scales[(offset + i) % len(fallback_scales)]
                                    for i in range(max(0, min(scale_limit, len(fallback_scales)))))
            self._scale_offsets[image_path] = (offset + len(fallback_scales)) % (len(MATCH_SCALES) - 1)
        search_frame = None
        search_ratio = min(1.0, 960 / max(fw, fh))
        scales = () if best_conf >= threshold else (preferred_scale, 1.0) + fallback_scales
        for scale in scales:
            if stop_event is not None and stop_event.is_set():
                return False, 0.0, None
            w, h = round(tw * scale), round(th * scale)
            if w < 2 or h < 2 or w > fw or h > fh or (w, h) in seen_sizes:
                continue
            seen_sizes.add((w, h))
            interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
            scaled = (template if (w, h) == (tw, th) else
                      cv2.resize(template, (w, h), interpolation=interpolation))
            if mask is not None:
                scaled_mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
                if not np.any(scaled_mask):
                    continue
            else:
                scaled_mask = None
                if np.std(scaled) == 0:
                    continue
            # 小圖維持原圖搜尋，避免縮圖後失去細節。
            if (search_ratio < 1.0 and min(w, h) * search_ratio >= 16
                    and scale not in (preferred_scale, 1.0)):
                if search_frame is None:
                    search_frame = cv2.resize(frame, None, fx=search_ratio, fy=search_ratio,
                                              interpolation=cv2.INTER_AREA)
                small_size = (max(2, round(w * search_ratio)), max(2, round(h * search_ratio)))
                small_template = cv2.resize(scaled, small_size, interpolation=cv2.INTER_AREA)
                small_mask = (cv2.resize(scaled_mask, small_size, interpolation=cv2.INTER_NEAREST)
                              if scaled_mask is not None else None)
                if small_mask is not None and not np.any(small_mask):
                    continue
                # ponytail: 每個比例只驗證一個候選位置；重複圖示需驗證多候選時再擴充。
                _, loc = _best_template_match(search_frame, small_template, small_mask)
                margin = int(np.ceil(2 / search_ratio)) + 2
                x, y = round(loc[0] / search_ratio), round(loc[1] / search_ratio)
                x1, y1 = max(0, x - margin), max(0, y - margin)
                roi = frame[y1:min(fh, y + h + margin), x1:min(fw, x + w + margin)]
                if roi.shape[0] < h or roi.shape[1] < w:
                    continue
                confidence, loc = _best_template_match(roi, scaled, scaled_mask)
                loc = (loc[0] + x1, loc[1] + y1)
            else:
                confidence, loc = _best_template_match(frame, scaled, scaled_mask)
            if confidence > best_conf:
                best_conf, best_loc, best_size = confidence, loc, (w, h)
                best_scale = scale
            # 記住的比例或原尺寸符合就沿用，其餘比例選最高分。
            if (scale in (preferred_scale, 1.0) and confidence >= threshold) or confidence >= 0.999:
                break

        # 近似匹配也細調，避免 5% 間隔剛好跳過小圖示的正確尺寸。
        if scales and best_loc is not None and max(0.5, threshold - 0.2) <= best_conf < 0.999:
            best_conf, best_loc, best_size, best_scale = _refine_template_match(
                frame, template, mask, (best_conf, best_loc, best_size, best_scale), stop_event)
        if stop_event is not None and stop_event.is_set():
            return False, 0.0, None
        if best_loc is not None and best_conf >= threshold:
            w, h = best_size
            self._match_state.sizes[image_path] = best_size
            self._preferred_scales[image_path] = best_scale
            self._last_matches[image_path] = (region, best_loc, best_size, best_scale)
            cx = best_loc[0] + w // 2
            cy = best_loc[1] + h // 2
            if region:
                cx += region[0]
                cy += region[1]
            return True, best_conf, (cx, cy)

        return False, best_conf, None

    def position_frame(self, combo: Combo, stop_event=None, *, scan_region=None):
        """定位觸發圖片，將圖片內區域換算為螢幕上的彩色區塊。"""
        settings = combo.position_control
        if settings.region_space == "screen":
            if scan_region is not None:
                x, y, w, h = settings.region
                sx, sy, sw, sh = scan_region
                if not (sx <= x and sy <= y and x + w <= sx + sw and y + h <= sy + sh):
                    return None, None
            return self.grab_frame(settings.region, color=True), settings.region
        region = settings.region
        frame = self.grab_frame(scan_region, color=True)
        if frame is None:
            return None, None
        found, _, center = self.match_template(
            cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY), combo.image_path,
            combo.match_threshold, scale_limit=1, stop_event=stop_event,
            exclude_region=region)
        if not found:
            return None, None
        mw, mh = self.get_match_size(combo.image_path)
        tw, th = self.get_template_size(combo.image_path)
        x, y, w, h = region
        left, top = center[0] - mw // 2, center[1] - mh // 2
        x1, y1 = left + round(x * mw / tw), top + round(y * mh / th)
        x2, y2 = left + round((x + w) * mw / tw), top + round((y + h) * mh / th)
        if not (0 <= x1 < x2 <= frame.shape[1] and 0 <= y1 < y2 <= frame.shape[0]):
            return None, None
        sx, sy = scan_region[:2] if scan_region else (0, 0)
        return frame[y1:y2, x1:x2], (x1 + sx, y1 + sy, x2 - x1, y2 - y1)

    def get_match_size(self, image_path: str) -> tuple[int, int] | None:
        """回傳最近一次成功匹配的實際尺寸，供 OCR 與偵測框線使用。"""
        return getattr(self._match_state, "sizes", {}).get(image_path)

    def get_match_scale(self, image_path: str) -> float | None:
        """回傳本次執行記住的比例；尚未匹配成功則回傳 None。"""
        return self._preferred_scales.get(image_path)

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
                model_storage_directory=_easyocr_model_dir(),
            )

    def ocr_verify(
        self,
        roi: np.ndarray,
        expected_text: str,
        threshold: float = 0.5,
        match_mode: str = "contains",
        match_ratio: float = 0.6,
    ) -> tuple[bool, float, str]:
        """對灰階 ROI 區域做 OCR，依匹配模式檢查文字。
        match_mode: "contains"=包含, "exact"=完全符合, "partial"=部分符合
        回傳 (是否找到, 最高信心度, 偵測到的所有文字)
        """
        if roi.size == 0:
            return False, 0.0, ""

        # ponytail: OCR 共用一份模型並序列化；需要平行 OCR 時再拆 reader。
        with self._ocr_lock:
            self._ensure_ocr()
            try:
                results = self._ocr_reader.readtext(roi)
            except Exception:
                return False, 0.0, ""

        best_conf = 0.0
        all_texts = [text for _bbox, text, _conf in results]
        detected_str = " | ".join(all_texts)
        confs = [conf for _bbox, _text, conf in results]
        avg_conf = sum(confs) / len(confs) if confs else 0.0

        # 先對「所有片段合併」做一次整體比對（解決 OCR 切段問題）
        combined = "".join(all_texts)
        if self._text_match(expected_text, combined, match_mode, match_ratio):
            if avg_conf >= threshold:
                return True, avg_conf, detected_str
            best_conf = max(best_conf, avg_conf)

        # 再逐段比對
        for _bbox, text, conf in results:
            matched = self._text_match(
                expected_text, text, match_mode, match_ratio)
            if matched and conf >= threshold:
                return True, conf, detected_str
            if matched:
                best_conf = max(best_conf, conf)
        return False, best_conf, detected_str

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
            model_storage_directory=_easyocr_model_dir(),
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


def ocr_from_screen(image_path: str, threshold: float = 0.8) -> str:
    """在螢幕上找到模板位置，對該 ROI 做 OCR，回傳偵測文字。
    與 runtime 使用相同管道，確保 verify_text 和執行期結果一致。
    若模板未找到或發生錯誤，回傳空字串。
    """
    global _shared_ocr_reader
    if _shared_ocr_reader is None:
        try:
            import easyocr
        except ImportError:
            return ""
        _shared_ocr_reader = easyocr.Reader(
            ["ch_tra", "en"], gpu=False, verbose=False,
            model_storage_directory=_easyocr_model_dir(),
        )

    try:
        # 截彩色全螢幕
        screenshot = ImageGrab.grab()
        frame_bgr = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
        frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    except Exception:
        return ""

    # 模板比對（找位置）
    sc = ScreenCapture()
    if not sc.load_template(image_path):
        return ""
    found, _conf, pos = sc.match_template(frame_gray, image_path, threshold)
    if not found or pos is None:
        return ""

    # 取彩色 ROI
    size = sc.get_match_size(image_path)
    if size is None:
        return ""
    tw, th = size
    cx, cy = pos
    x1 = max(0, cx - tw // 2)
    y1 = max(0, cy - th // 2)
    roi = frame_bgr[y1:y1 + th, x1:x1 + tw]
    if roi.size == 0:
        return ""

    try:
        results = _shared_ocr_reader.readtext(roi)
        return "".join(text for _, text, _ in results)
    except Exception:
        return ""
