"""資料模型 — Combo / Action / Profile"""

from __future__ import annotations

import uuid
import math
from dataclasses import dataclass, field
from enum import Enum


# ── 觸發模式 ──────────────────────────────────────────────
class TriggerMode(str, Enum):
    ON_APPEAR = "on_appear"            # 圖片出現時觸發一次
    WHILE_VISIBLE = "while_visible"    # 圖片可見期間持續執行，消失即停止
    POSITION_CONTROL = "position_control"
    COLOR_BAND = "color_band"

    @property
    def tracks_marker(self) -> bool:
        return self in (TriggerMode.POSITION_CONTROL, TriggerMode.COLOR_BAND)


# ── 文字匹配模式 ────────────────────────────────────────────
class TextMatchMode(str, Enum):
    CONTAINS = "contains"        # 包含（子字串）
    EXACT = "exact"              # 完全符合
    PARTIAL = "partial"          # 部分符合（字元相似度）


# ── 動作類型 ──────────────────────────────────────────────
class ActionType(str, Enum):
    # 滑鼠
    CLICK = "click"                    # 單擊
    CLICK_IMAGE = "click_image"        # 尋找並點擊另一張圖片
    RAPID_CLICK = "rapid_click"        # 連續快速點擊
    HOLD_MOUSE = "hold_mouse"          # 長按滑鼠鍵
    # 鍵盤
    KEY_PRESS = "key_press"            # 按一下鍵盤
    RAPID_KEY = "rapid_key"            # 連續按鍵
    HOLD_KEY = "hold_key"              # 長按鍵盤鍵
    PLAY_SOUND = "play_sound"          # 播放系統音效


class MouseButton(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


# ── Action ────────────────────────────────────────────────
@dataclass
class Action:
    action_type: ActionType = ActionType.CLICK

    # 滑鼠按鍵（CLICK / RAPID_CLICK / HOLD_MOUSE 用）
    mouse_button: MouseButton = MouseButton.LEFT

    # 鍵盤按鍵（KEY_PRESS / RAPID_KEY / HOLD_KEY 用）
    key: str = ""

    # 觸發前延遲範圍（秒）
    delay_min: float = 0.0
    delay_max: float = 0.0

    # 持續時間（HOLD_MOUSE / HOLD_KEY / RAPID_CLICK / RAPID_KEY 用，秒）
    # while_visible 模式下此欄無效，會持續到圖片消失
    duration: float = 1.0

    # 連續點擊/按鍵間隔範圍（RAPID_CLICK / RAPID_KEY 用，秒）
    interval_min: float = 0.05
    interval_max: float = 0.15

    target_image_path: str = ""
    target_threshold: float = 0.8

    def to_dict(self) -> dict:
        return {
            "action_type": self.action_type.value,
            "mouse_button": self.mouse_button.value,
            "key": self.key,
            "delay_min": self.delay_min,
            "delay_max": self.delay_max,
            "duration": self.duration,
            "interval_min": self.interval_min,
            "interval_max": self.interval_max,
            "target_image_path": self.target_image_path,
            "target_threshold": self.target_threshold,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Action:
        return cls(
            action_type=ActionType(d.get("action_type", "click")),
            mouse_button=MouseButton(d.get("mouse_button", "left")),
            key=d.get("key", ""),
            delay_min=d.get("delay_min", 0.0),
            delay_max=d.get("delay_max", 0.0),
            duration=d.get("duration", 1.0),
            interval_min=d.get("interval_min", 0.05),
            interval_max=d.get("interval_max", 0.15),
            target_image_path=d.get("target_image_path", ""),
            target_threshold=d.get("target_threshold", 0.8),
        )


@dataclass
class PositionControl:
    region_space: str = "template"
    region: tuple[int, int, int, int] | None = None
    color: tuple[int, int, int] = (255, 185, 50)  # RGB
    tolerance: int = 15  # HSV 色相容差，0~90
    min_area: int = 20
    press_at: float = 0.45
    release_at: float = 0.75
    max_area: int = 0  # 0 = 不限制；模板模式以原圖像素面積設定
    band_color: tuple[int, int, int] = (115, 170, 75)
    band_tolerance: int = 15
    band_start: float = 0.8
    band_stop: float = 0.6
    band_margin: float = 0.5  # 箭頭外框尺寸的倍數
    band_missing_ms: int = 150

    def validate(self):
        if self.region_space not in ("screen", "template"):
            raise ValueError("位置控制區域格式不正確。")
        if (self.region is None or len(self.region) != 4
                or any(type(v) is not int for v in self.region)
                or min(self.region[2:]) < 6):
            raise ValueError("請設定位置控制的偵測區域（寬高至少 6）。")
        if (len(self.color) != 3 or any(type(v) is not int or not 0 <= v <= 255
                                       for v in self.color)):
            raise ValueError("標記顏色必須是 RGB 0~255。")
        if type(self.tolerance) is not int or not 0 <= self.tolerance <= 90:
            raise ValueError("顏色容差必須介於 0~90。")
        if type(self.min_area) is not int or self.min_area < 1:
            raise ValueError("最小面積必須是正整數。")
        if (type(self.max_area) is not int or self.max_area < 0
                or 0 < self.max_area < self.min_area):
            raise ValueError("最大面積必須是 0（不限制），或不小於最小面積的整數。")
        if not 0 <= self.press_at < self.release_at <= 1:
            raise ValueError("界線必須符合：0 ≤ 按住位置 < 放開位置 ≤ 100%。")
        if (len(self.band_color) != 3 or any(type(v) is not int or not 0 <= v <= 255
                                            for v in self.band_color)):
            raise ValueError("色帶顏色必須是 RGB 0~255。")
        if type(self.band_tolerance) is not int or not 0 <= self.band_tolerance <= 90:
            raise ValueError("色帶容差必須介於 0~90。")
        if not 0 <= self.band_stop < self.band_start <= 1:
            raise ValueError("色帶比例必須符合：0 ≤ 停止比例 < 啟動比例 ≤ 100%。")
        if not math.isfinite(self.band_margin) or not 0.1 <= self.band_margin <= 3:
            raise ValueError("取樣範圍必須介於箭頭大小的 0.1~3 倍。")
        if type(self.band_missing_ms) is not int or self.band_missing_ms < 0:
            raise ValueError("漏判容忍時間必須是非負整數（毫秒）。")

    def to_dict(self) -> dict:
        return {"region_space": self.region_space, "region": self.region, "color": self.color,
                "tolerance": self.tolerance, "min_area": self.min_area, "max_area": self.max_area,
                "press_at": self.press_at, "release_at": self.release_at,
                "band_color": self.band_color, "band_tolerance": self.band_tolerance,
                "band_start": self.band_start, "band_stop": self.band_stop,
                "band_margin": self.band_margin, "band_missing_ms": self.band_missing_ms}

    @classmethod
    def from_dict(cls, d: dict) -> PositionControl:
        return cls(region_space=d.get("region_space", "screen" if d.get("region") else "template"),
                   region=tuple(d["region"]) if d.get("region") else None,
                   color=tuple(d.get("color", (255, 185, 50))),
                   tolerance=d.get("tolerance", 15), min_area=d.get("min_area", 20),
                   press_at=d.get("press_at", 0.45), release_at=d.get("release_at", 0.75),
                   max_area=d.get("max_area", 0),
                   band_color=tuple(d.get("band_color", (115, 170, 75))),
                   band_tolerance=d.get("band_tolerance", 15),
                   band_start=d.get("band_start", 0.8), band_stop=d.get("band_stop", 0.6),
                   band_margin=d.get("band_margin", 0.5),
                   band_missing_ms=d.get("band_missing_ms", 150))


# ── Combo ─────────────────────────────────────────────────
@dataclass
class Combo:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = "新組合"
    enabled: bool = True
    detection_mode: str = "active"
    calc_region: tuple[int, int, int, int] | None = None
    daily_limit: int = 999
    warning_ratio: float = 0.9

    # 圖片偵測
    image_path: str = ""           # 模板圖片路徑
    match_threshold: float = 0.8   # 圖片比對閾值

    # 文字驗證（可選，留空則不做 OCR）
    verify_text: str = ""          # 期望在圖片區域內出現的文字
    text_threshold: float = 0.5    # 文字辨識信心度閾值
    text_match_mode: TextMatchMode = TextMatchMode.CONTAINS  # 文字匹配模式
    text_match_ratio: float = 0.6  # 部分符合時的最低吻合比例

    # 觸發模式
    trigger: TriggerMode = TriggerMode.ON_APPEAR

    # 動作
    action: Action = field(default_factory=Action)
    position_control: PositionControl = field(default_factory=PositionControl)

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "image_path": self.image_path,
            "match_threshold": self.match_threshold,
            "trigger": self.trigger.value,
            "action": self.action.to_dict(),
        }
        # 只在有設定時才寫入，保持 JSON 簡潔
        if self.detection_mode == "background":
            d.update(detection_mode=self.detection_mode, calc_region=self.calc_region,
                     daily_limit=self.daily_limit, warning_ratio=self.warning_ratio,
                     text_threshold=self.text_threshold)
        if self.trigger.tracks_marker:
            d["position_control"] = self.position_control.to_dict()
        if self.verify_text:
            d["verify_text"] = self.verify_text
            d["text_threshold"] = self.text_threshold
            d["text_match_mode"] = self.text_match_mode.value
            d["text_match_ratio"] = self.text_match_ratio
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Combo:
        return cls(
            id=d.get("id", uuid.uuid4().hex[:8]),
            name=d.get("name", "新組合"),
            enabled=d.get("enabled", True),
            detection_mode=d.get("detection_mode", "active"),
            calc_region=tuple(d["calc_region"]) if d.get("calc_region") else None,
            daily_limit=d.get("daily_limit", 999),
            warning_ratio=d.get("warning_ratio", 0.9),
            image_path=d.get("image_path", ""),
            match_threshold=d.get("match_threshold", 0.8),
            verify_text=d.get("verify_text", ""),
            text_threshold=d.get("text_threshold", 0.5),
            text_match_mode=TextMatchMode(d.get("text_match_mode", "contains")),
            text_match_ratio=d.get("text_match_ratio", 0.6),
            trigger=TriggerMode(d.get("trigger", "on_appear")),
            action=Action.from_dict(d.get("action", {})),
            position_control=PositionControl.from_dict(d.get("position_control", {})),
        )


# ── Profile ───────────────────────────────────────────────
@dataclass
class Profile:
    name: str = "預設"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    background_hotkey: str = "F7"
    scan_region: tuple[int, int, int, int] | None = None
    scan_interval: float = 0.1
    hotkey: str = "F6"
    combos: list[Combo] = field(default_factory=list)
    window_title: str = ""
    window_handle: int = field(default=0, repr=False, compare=False)
    window_pid: int = field(default=0, repr=False, compare=False)

    def to_dict(self) -> dict:
        region = list(self.scan_region) if self.scan_region else None
        return {
            "name": self.name,
            "id": self.id,
            "background_hotkey": self.background_hotkey,
            "scan_region": region,
            "scan_interval": self.scan_interval,
            "hotkey": self.hotkey,
            "combos": [c.to_dict() for c in self.combos],
            "window_title": self.window_title,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Profile:
        region = d.get("scan_region")
        if region and len(region) == 4:
            region = tuple(region)
        else:
            region = None
        return cls(
            name=d.get("name", "預設"),
            id=d.get("id", uuid.uuid4().hex),
            background_hotkey=d.get("background_hotkey", "F7"),
            scan_region=region,
            scan_interval=d.get("scan_interval", 0.1),
            hotkey=d.get("hotkey", "F6"),
            combos=[Combo.from_dict(c) for c in d.get("combos", [])],
            window_title=d.get("window_title", ""),
        )
