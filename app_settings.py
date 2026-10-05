"""Persistent application-wide settings."""

from __future__ import annotations

import json
from pathlib import Path


try:
    _BASE_DIR = Path(__compiled__.containing_dir)
except NameError:
    _BASE_DIR = Path(__file__).resolve().parent

SETTINGS_PATH = _BASE_DIR / "settings" / "app_settings.json"


def load_app_settings(path: str | Path | None = None) -> dict:
    settings_path = Path(path) if path is not None else SETTINGS_PATH
    if path is None and not settings_path.exists():
        settings_path = _BASE_DIR / "app_settings.json"
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_app_settings(settings: dict, path: str | Path | None = None):
    settings_path = Path(path) if path is not None else SETTINGS_PATH
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = settings_path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(settings_path)
