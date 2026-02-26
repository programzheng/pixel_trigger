"""資料模型 — Combo / Action / Profile"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum


# ── 觸發模式 ──────────────────────────────────────────────
class TriggerMode(str, Enum):
    ON_APPEAR = "on_appear"            # 圖片出現時觸發一次
    WHILE_VISIBLE = "while_visible"    # 圖片可見期間持續執行，消失即停止


# ── 動作類型 ──────────────────────────────────────────────
class ActionType(str, Enum):
    # 滑鼠
    CLICK = "click"                    # 單擊
    RAPID_CLICK = "rapid_click"        # 連續快速點擊
    HOLD_MOUSE = "hold_mouse"          # 長按滑鼠鍵
    # 鍵盤
    KEY_PRESS = "key_press"            # 按一下鍵盤
    RAPID_KEY = "rapid_key"            # 連續按鍵
    HOLD_KEY = "hold_key"              # 長按鍵盤鍵


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
        )


# ── Combo ─────────────────────────────────────────────────
@dataclass
class Combo:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = "新組合"
    enabled: bool = True

    # 圖片偵測
    image_path: str = ""           # 模板圖片路徑
    match_threshold: float = 0.8   # 比對閾值

    # 觸發模式
    trigger: TriggerMode = TriggerMode.ON_APPEAR

    # 動作
    action: Action = field(default_factory=Action)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "image_path": self.image_path,
            "match_threshold": self.match_threshold,
            "trigger": self.trigger.value,
            "action": self.action.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> Combo:
        return cls(
            id=d.get("id", uuid.uuid4().hex[:8]),
            name=d.get("name", "新組合"),
            enabled=d.get("enabled", True),
            image_path=d.get("image_path", ""),
            match_threshold=d.get("match_threshold", 0.8),
            trigger=TriggerMode(d.get("trigger", "on_appear")),
            action=Action.from_dict(d.get("action", {})),
        )


# ── Profile ───────────────────────────────────────────────
@dataclass
class Profile:
    name: str = "預設"
    scan_region: tuple[int, int, int, int] | None = None
    scan_interval: float = 0.1
    hotkey: str = "F6"
    combos: list[Combo] = field(default_factory=list)

    def to_dict(self) -> dict:
        region = list(self.scan_region) if self.scan_region else None
        return {
            "name": self.name,
            "scan_region": region,
            "scan_interval": self.scan_interval,
            "hotkey": self.hotkey,
            "combos": [c.to_dict() for c in self.combos],
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
            scan_region=region,
            scan_interval=d.get("scan_interval", 0.1),
            hotkey=d.get("hotkey", "F6"),
            combos=[Combo.from_dict(c) for c in d.get("combos", [])],
        )
