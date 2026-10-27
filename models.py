"""資料模型 — Combo / Action / Profile"""

from __future__ import annotations

import uuid
import math
from dataclasses import dataclass, field, replace
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
    ignore_polygon: tuple[tuple[float, float], ...] = ()  # 偵測區塊內的相對座標，0~1
    include_polygon: tuple[tuple[float, float], ...] = ()  # 空值保留整個偵測區塊

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
            raise ValueError("色帶比例必須符合：0 ≤ 停止比例 < 啟動比例 ≤ 1。")
        if not math.isfinite(self.band_margin) or not 0.1 <= self.band_margin <= 3:
            raise ValueError("取樣範圍必須介於箭頭大小的 0.1~3 倍。")
        if type(self.band_missing_ms) is not int or self.band_missing_ms < 0:
            raise ValueError("漏判容忍時間必須是非負整數（毫秒）。")
        self.validate_polygons()

    def validate_polygons(self):
        for points in (self.ignore_polygon, self.include_polygon):
            self._validate_polygon(points)

    @staticmethod
    def _validate_polygon(points):
        if not isinstance(points, (tuple, list)):
            raise ValueError("圈選區域必須是多邊形座標。")
        if points:
            if (not 3 <= len(points) <= 128 or any(
                    not isinstance(p, (tuple, list)) or len(p) != 2 or any(
                        type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
                        for v in p) for p in points)):
                raise ValueError("圈選區域需有 3~128 個頂點，座標必須介於 0~1。")
            def cross(a, b, c):
                return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

            edges = list(zip(points, points[1:] + points[:1]))
            area = sum(a[0] * b[1] - b[0] * a[1] for a, b in edges)
            if abs(area) < 1e-10 or len({tuple(p) for p in points}) != len(points):
                raise ValueError("圈選區域不能退化成直線或含重複頂點。")
            for i, (a, b) in enumerate(edges):
                for j, (c, d) in enumerate(edges[i + 1:], i + 1):
                    if j == i + 1 or (i == 0 and j == len(edges) - 1):
                        continue
                    if (max(a[0], b[0]) >= min(c[0], d[0])
                            and max(c[0], d[0]) >= min(a[0], b[0])
                            and max(a[1], b[1]) >= min(c[1], d[1])
                            and max(c[1], d[1]) >= min(a[1], b[1])
                            and cross(a, b, c) * cross(a, b, d) <= 0
                            and cross(c, d, a) * cross(c, d, b) <= 0):
                        raise ValueError("圈選區域的邊不可交叉。")

    def to_dict(self) -> dict:
        return {"region_space": self.region_space, "region": self.region, "color": self.color,
                "tolerance": self.tolerance, "min_area": self.min_area, "max_area": self.max_area,
                "press_at": self.press_at, "release_at": self.release_at,
                "band_color": self.band_color, "band_tolerance": self.band_tolerance,
                "band_start": self.band_start, "band_stop": self.band_stop,
                "band_margin": self.band_margin, "band_missing_ms": self.band_missing_ms,
                "ignore_polygon": self.ignore_polygon, "include_polygon": self.include_polygon}

    @classmethod
    def from_dict(cls, d: dict) -> PositionControl:
        settings = cls(region_space=d.get("region_space", "screen" if d.get("region") else "template"),
                   region=tuple(d["region"]) if d.get("region") else None,
                   color=tuple(d.get("color", (255, 185, 50))),
                   tolerance=d.get("tolerance", 15), min_area=d.get("min_area", 20),
                   press_at=d.get("press_at", 0.45), release_at=d.get("release_at", 0.75),
                   max_area=d.get("max_area", 0),
                   band_color=tuple(d.get("band_color", (115, 170, 75))),
                   band_tolerance=d.get("band_tolerance", 15),
                   band_start=d.get("band_start", 0.8), band_stop=d.get("band_stop", 0.6),
                   band_margin=d.get("band_margin", 0.5),
                   band_missing_ms=d.get("band_missing_ms", 150),
                   ignore_polygon=d.get("ignore_polygon", ()),
                   include_polygon=d.get("include_polygon", ()))
        settings.validate_polygons()
        settings.ignore_polygon = tuple(tuple(p) for p in settings.ignore_polygon)
        settings.include_polygon = tuple(tuple(p) for p in settings.include_polygon)
        return settings


@dataclass
class TriggerImage:
    image_path: str = ""
    region: tuple[int, int, int, int] | None = None
    color: tuple[int, int, int] = (255, 185, 50)
    band_color: tuple[int, int, int] = (115, 170, 75)
    ignore_polygon: tuple[tuple[float, float], ...] = ()
    include_polygon: tuple[tuple[float, float], ...] = ()
    calc_region: tuple[int, int, int, int] | None = None

    @classmethod
    def from_combo(cls, combo: Combo) -> TriggerImage:
        p = combo.position_control
        return cls(combo.image_path, p.region, p.color, p.band_color,
                   p.ignore_polygon, p.include_polygon, combo.calc_region)

    def position_settings(self, shared: PositionControl) -> PositionControl:
        return replace(shared, region=self.region, color=self.color,
                       band_color=self.band_color, ignore_polygon=self.ignore_polygon,
                       include_polygon=self.include_polygon)

    def to_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, data: dict) -> TriggerImage:
        if not isinstance(data, dict) or not isinstance(data.get("image_path"), str):
            raise ValueError("Invalid trigger image")
        region = data.get("region")
        calc_region = data.get("calc_region")
        for value in (region, calc_region):
            if value is not None and (not isinstance(value, (list, tuple)) or len(value) != 4
                                      or any(type(v) is not int for v in value)):
                raise ValueError("Invalid trigger image region")
        settings = PositionControl.from_dict(data)
        return cls(data["image_path"], tuple(region) if region is not None else None,
                   settings.color, settings.band_color, settings.ignore_polygon,
                   settings.include_polygon, tuple(calc_region) if calc_region is not None else None)


# ── Combo ─────────────────────────────────────────────────
@dataclass
class Combo:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = "新組合"
    enabled: bool = True
    detection_mode: str = "active"
    calc_region: tuple[int, int, int, int] | None = None
    daily_limit: int = 900
    daily_reset_time: str = "00:00"
    warning_ratio: float = 0.9
    low_confidence_confirmations: int = 3

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
    trigger_images: list[TriggerImage] = field(default_factory=list)

    def image_items(self) -> list[TriggerImage]:
        return self.trigger_images or ([TriggerImage.from_combo(self)] if self.image_path else [])

    def for_image(self, item: TriggerImage) -> Combo:
        return replace(self, image_path=item.image_path, trigger_images=[],
                       position_control=item.position_settings(self.position_control),
                       calc_region=item.calc_region)

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
                     daily_limit=self.daily_limit, daily_reset_time=self.daily_reset_time,
                     warning_ratio=self.warning_ratio,
                     text_threshold=self.text_threshold,
                     low_confidence_confirmations=self.low_confidence_confirmations)
        if self.trigger.tracks_marker:
            d["position_control"] = self.position_control.to_dict()
        if self.verify_text:
            d["verify_text"] = self.verify_text
            d["text_threshold"] = self.text_threshold
            d["text_match_mode"] = self.text_match_mode.value
            d["text_match_ratio"] = self.text_match_ratio
        items = self.image_items()
        if items:
            d["trigger_images"] = [item.to_dict() for item in items]
            d["image_path"] = items[0].image_path
            if self.detection_mode == "background":
                d["calc_region"] = items[0].calc_region
            if self.trigger.tracks_marker and self.position_control.region_space == "template":
                d["position_control"] = items[0].position_settings(self.position_control).to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Combo:
        items = d.get("trigger_images", [])
        if not isinstance(items, list):
            raise ValueError("Invalid trigger images")
        items = [TriggerImage.from_dict(item) for item in items]
        if "trigger_images" in d:
            d = {**d, "image_path": items[0].image_path if items else ""}
        return cls(
            id=d.get("id", uuid.uuid4().hex[:8]),
            name=d.get("name", "新組合"),
            enabled=d.get("enabled", True),
            detection_mode=d.get("detection_mode", "active"),
            calc_region=tuple(d["calc_region"]) if d.get("calc_region") else None,
            daily_limit=d.get("daily_limit", 900),
            daily_reset_time=d.get("daily_reset_time", "00:00"),
            warning_ratio=d.get("warning_ratio", 0.9),
            low_confidence_confirmations=d.get("low_confidence_confirmations", 3),
            image_path=d.get("image_path", ""),
            match_threshold=d.get("match_threshold", 0.8),
            verify_text=d.get("verify_text", ""),
            text_threshold=d.get("text_threshold", 0.5),
            text_match_mode=TextMatchMode(d.get("text_match_mode", "contains")),
            text_match_ratio=d.get("text_match_ratio", 0.6),
            trigger=TriggerMode(d.get("trigger", "on_appear")),
            action=Action.from_dict(d.get("action", {})),
            position_control=PositionControl.from_dict(d.get("position_control", {})),
            trigger_images=items,
        )


# ── Statistics ────────────────────────────────────────────
@dataclass
class Statistic:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    name: str = "新統計"
    source: str = "manual"
    combo_id: str = ""
    metric: str = "income"
    period: str = "day"
    reset_time: str = "00:00"
    limit: int | None = 900
    warning_ratio: float = 0.9
    enabled: bool = True
    archived: bool = False

    def validate(self):
        from datetime import datetime

        if type(self.id) is not str or not self.id:
            raise ValueError("統計 ID 不正確。")
        if type(self.name) is not str or not self.name.strip():
            raise ValueError("統計名稱不可為空。")
        if self.source not in ("ocr", "manual") or (self.source == "ocr" and not self.combo_id):
            raise ValueError("統計來源不正確。")
        if self.metric not in ("income", "expense", "net", "count", "balance"):
            raise ValueError("統計項目不正確。")
        if self.metric == "balance" and self.source != "ocr":
            raise ValueError("最新餘額只能使用 OCR 來源。")
        if self.period not in ("day", "week", "month", "year"):
            raise ValueError("統計期間不正確。")
        try:
            if datetime.strptime(self.reset_time, "%H:%M").strftime("%H:%M") != self.reset_time:
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError("每日重置時間必須是 HH:MM。") from None
        if self.limit is not None and (type(self.limit) is not int or self.limit < 1):
            raise ValueError("上限必須是正整數。")
        if (type(self.warning_ratio) not in (int, float)
                or not 0 < self.warning_ratio <= 1):
            raise ValueError("預警比例必須大於 0 且不超過 100%。")
        if type(self.enabled) is not bool or type(self.archived) is not bool:
            raise ValueError("統計狀態不正確。")

    def to_dict(self) -> dict:
        return {key: getattr(self, key) for key in (
            "id", "name", "source", "combo_id", "metric", "period", "reset_time",
            "limit", "warning_ratio", "enabled", "archived")}

    @classmethod
    def from_dict(cls, d: dict) -> Statistic:
        statistic = cls(**{key: d[key] for key in cls.__dataclass_fields__ if key in d})
        statistic.validate()
        return statistic


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
    statistics: list[Statistic] = field(default_factory=list)
    window_title: str = ""
    window_handle: int = field(default=0, repr=False, compare=False)
    window_pid: int = field(default=0, repr=False, compare=False)
    debug_hotkey: str = "F9"
    background_debug_hotkey: str = "F10"

    def to_dict(self) -> dict:
        region = list(self.scan_region) if self.scan_region else None
        return {
            "name": self.name,
            "id": self.id,
            "background_hotkey": self.background_hotkey,
            "debug_hotkey": self.debug_hotkey,
            "background_debug_hotkey": self.background_debug_hotkey,
            "scan_region": region,
            "scan_interval": self.scan_interval,
            "hotkey": self.hotkey,
            "combos": [c.to_dict() for c in self.combos],
            "statistics": [s.to_dict() for s in self.statistics],
            "window_title": self.window_title,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Profile:
        region = d.get("scan_region")
        if region and len(region) == 4:
            region = tuple(region)
        else:
            region = None
        combos = [Combo.from_dict(c) for c in d.get("combos", [])]
        if "statistics" in d:
            statistics = [Statistic.from_dict(s) for s in d["statistics"]]
        else:
            statistics = [Statistic(id=f"legacy-{c.id}", name=c.name, source="ocr",
                                    combo_id=c.id, reset_time=c.daily_reset_time,
                                    limit=c.daily_limit if type(c.daily_limit) is int and c.daily_limit > 0 else 900,
                                    warning_ratio=c.warning_ratio if type(c.warning_ratio) in (int, float)
                                    and 0 < c.warning_ratio <= 1 else 0.9)
                          for c in combos if c.detection_mode == "background"]
        if len({s.id for s in statistics}) != len(statistics):
            raise ValueError("Profile 的統計 ID 重複。")
        return cls(
            name=d.get("name", "預設"),
            id=d.get("id", uuid.uuid4().hex),
            background_hotkey=d.get("background_hotkey", "F7"),
            debug_hotkey=d.get("debug_hotkey", "F9"),
            background_debug_hotkey=d.get("background_debug_hotkey", "F10"),
            scan_region=region,
            scan_interval=d.get("scan_interval", 0.1),
            hotkey=d.get("hotkey", "F6"),
            combos=combos,
            statistics=statistics,
            window_title=d.get("window_title", ""),
        )
