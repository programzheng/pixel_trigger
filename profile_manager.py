"""Profile JSON 讀寫管理"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from models import (
    Action,
    ActionType,
    Combo,
    MouseButton,
    Profile,
    TriggerMode,
)

# 打包後使用 exe 所在目錄，開發模式使用腳本所在目錄
_BASE_DIR = (
    Path(sys.executable).parent
    if getattr(sys, "__compiled__", False)
    else Path(__file__).parent
)

PROFILES_DIR = _BASE_DIR / "profiles"
TEMPLATES_DIR = _BASE_DIR / "templates"


def ensure_dirs():
    PROFILES_DIR.mkdir(exist_ok=True)
    TEMPLATES_DIR.mkdir(exist_ok=True)


def save_profile(profile: Profile, filename: str | None = None) -> Path:
    """儲存 profile 到 JSON 檔案，回傳路徑"""
    ensure_dirs()
    if filename is None:
        filename = profile.name.replace(" ", "_") + ".json"
    path = PROFILES_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile.to_dict(), f, ensure_ascii=False, indent=2)
    return path


def load_profile(path: str | Path) -> Profile:
    """從 JSON 載入 profile"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return Profile.from_dict(data)


def list_profiles() -> list[Path]:
    """列出 profiles 目錄下所有 JSON 檔案"""
    ensure_dirs()
    return sorted(PROFILES_DIR.glob("*.json"))


def create_default_fishing() -> Profile:
    """建立預設 釣魚 profile 作為範例"""
    return Profile(
        name="fishing",
        scan_region=None,
        scan_interval=0.1,
        hotkey="F6",
        combos=[
            Combo(
                name="偵測驚嘆號 → 點擊收竿",
                enabled=True,
                image_path="",
                match_threshold=0.8,
                trigger=TriggerMode.ON_APPEAR,
                action=Action(
                    action_type=ActionType.CLICK,
                    mouse_button=MouseButton.LEFT,
                    delay_min=0.1,
                    delay_max=0.3,
                ),
            ),
            Combo(
                name="拉魚 → 連續點擊",
                enabled=True,
                image_path="",
                match_threshold=0.8,
                trigger=TriggerMode.WHILE_VISIBLE,
                action=Action(
                    action_type=ActionType.RAPID_CLICK,
                    mouse_button=MouseButton.LEFT,
                    interval_min=0.05,
                    interval_max=0.15,
                ),
            ),
        ],
    )


def init_default_profiles():
    """初始化：若 profiles 目錄為空，建立預設範例"""
    ensure_dirs()
    if not list_profiles():
        profile = create_default_fishing()
        save_profile(profile, "fishing.json")
