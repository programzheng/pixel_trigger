"""統一輸入控制模組 — 鍵盤 + 滑鼠"""

from __future__ import annotations

import random
import time

import pyautogui

pyautogui.PAUSE = 0


# ── 滑鼠操作 ─────────────────────────────────────────────

def mouse_click(button: str = "left", delay_min: float = 0, delay_max: float = 0, stop_event=None):
    """單擊滑鼠"""
    if delay_max > 0:
        if not _interruptible_sleep(random.uniform(delay_min, delay_max), stop_event):
            return
    if stop_event and stop_event.is_set():
        return
    pyautogui.click(button=button)


def mouse_rapid_click(
    button: str = "left",
    duration: float = 1.0,
    interval_min: float = 0.05,
    interval_max: float = 0.15,
    stop_event=None,
):
    """連續快速點擊滑鼠"""
    end_time = time.time() + duration
    while time.time() < end_time:
        if stop_event and stop_event.is_set():
            break
        pyautogui.click(button=button)
        time.sleep(random.uniform(interval_min, interval_max))


def mouse_hold(
    button: str = "left",
    duration: float = 1.0,
    stop_event=None,
):
    """長按滑鼠鍵"""
    if stop_event and stop_event.is_set():
        return
    pyautogui.mouseDown(button=button)
    try:
        _interruptible_sleep(duration, stop_event)
    finally:
        pyautogui.mouseUp(button=button)


# ── 鍵盤操作 ─────────────────────────────────────────────

def key_press(key: str, delay_min: float = 0, delay_max: float = 0, stop_event=None):
    """按一下鍵盤鍵"""
    if delay_max > 0:
        if not _interruptible_sleep(random.uniform(delay_min, delay_max), stop_event):
            return
    if stop_event and stop_event.is_set():
        return
    pyautogui.press(key)


def key_rapid_press(
    key: str,
    duration: float = 1.0,
    interval_min: float = 0.05,
    interval_max: float = 0.15,
    stop_event=None,
):
    """連續按鍵盤鍵"""
    end_time = time.time() + duration
    while time.time() < end_time:
        if stop_event and stop_event.is_set():
            break
        pyautogui.press(key)
        time.sleep(random.uniform(interval_min, interval_max))


def key_hold(key: str, duration: float = 1.0, stop_event=None):
    """長按鍵盤鍵"""
    if stop_event and stop_event.is_set():
        return
    pyautogui.keyDown(key)
    try:
        _interruptible_sleep(duration, stop_event)
    finally:
        pyautogui.keyUp(key)


# ── 內部工具 ─────────────────────────────────────────────

def _interruptible_sleep(seconds: float, stop_event=None):
    """可中斷的 sleep"""
    if stop_event is not None:
        return not stop_event.wait(max(0, seconds))
    time.sleep(max(0, seconds))
    return True
