"""統一輸入控制模組 — 鍵盤 + 滑鼠"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import random
import time
import winsound
from collections.abc import Callable

import pyautogui

pyautogui.PAUSE = 0


class _MouseInput(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _InputUnion(ctypes.Union):
    # MOUSEINPUT is the largest INPUT union member on both Win32 and Win64.
    _fields_ = [("mi", _MouseInput)]


class _Input(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", wintypes.DWORD), ("data", _InputUnion)]


_send_input = ctypes.WinDLL("user32", use_last_error=True).SendInput
_send_input.argtypes = (wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int)
_send_input.restype = wintypes.UINT
_CLICK_FLAGS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010),
                "middle": (0x0020, 0x0040)}


def play_sound(delay_min=0, delay_max=0, stop_event=None):
    if not _interruptible_sleep(random.uniform(delay_min, delay_max), stop_event):
        return
    if stop_event and stop_event.is_set():
        return
    winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NOSTOP)


# ── 滑鼠操作 ─────────────────────────────────────────────


def release_mouse(button: str = "left"):
    """釋放已按住的滑鼠，即使游標在 fail-safe 角落。"""
    x, y = pyautogui.position()
    pyautogui.platformModule._mouseUp(x, y, button)

def mouse_click(button: str = "left", delay_min: float = 0, delay_max: float = 0, stop_event=None):
    """單擊滑鼠"""
    if delay_max > 0:
        if not _interruptible_sleep(random.uniform(delay_min, delay_max), stop_event):
            return
    if stop_event and stop_event.is_set():
        return
    pyautogui.click(button=button)


def mouse_click_at(x: int, y: int, button: str = "left", stop_event=None):
    """Click a screen position long enough for frame-polled applications to see it."""
    if stop_event and stop_event.is_set():
        return
    pyautogui.mouseDown(x=x, y=y, button=button)
    try:
        _interruptible_sleep(0.05, stop_event)
    finally:
        release_mouse(button)


def mouse_rapid_click(
    button: str = "left",
    duration: float = 1.0,
    interval_min: float = 0.05,
    interval_max: float = 0.15,
    stop_event=None,
    *,
    on_rate: Callable[[int, float], None] | None = None,
):
    """直接發送成對的按下／放開；統計成功送入 Windows 的點擊，非遊戲接收量。"""
    if (button not in _CLICK_FLAGS
            or any(not math.isfinite(v) or v < 0 for v in (duration, interval_min, interval_max))
            or interval_min > interval_max):
        raise ValueError("連點按鍵、持續時間或間隔不合法。")
    inputs = (_Input * 2)(*(_Input(type=0, mi=_MouseInput(dwFlags=flag))
                            for flag in _CLICK_FLAGS[button]))
    sample_start = time.perf_counter()
    end_time = sample_start + duration
    count = 0
    try:
        while time.perf_counter() < end_time:
            if stop_event and stop_event.is_set():
                break
            pyautogui.failSafeCheck()
            if stop_event and stop_event.is_set():
                break
            ctypes.set_last_error(0)
            sent = _send_input(2, inputs, ctypes.sizeof(_Input))
            if sent != 2:
                error = ctypes.get_last_error()
                if sent:
                    release_mouse(button)  # Partial insertion may leave the button down.
                raise OSError(error, f"SendInput 只送出 {sent}/2 個事件；請檢查目標視窗權限。")
            count += 1
            now = time.perf_counter()
            if on_rate is not None and now - sample_start >= 1:
                on_rate(count, now - sample_start)
                count, sample_start = 0, now
            interval = random.uniform(interval_min, interval_max) if interval_max else 0
            remaining = end_time - time.perf_counter()
            if interval > 0 and remaining > 0:
                if not _interruptible_sleep(min(interval, remaining), stop_event):
                    break
    finally:
        if on_rate is not None and count:
            on_rate(count, max(time.perf_counter() - sample_start, 1e-9))


def mouse_hold(
    button: str = "left",
    duration: float = 1.0,
    stop_event=None,
):
    """長按滑鼠鍵"""
    if stop_event and stop_event.is_set():
        return
    try:
        pyautogui.mouseDown(button=button)
        _interruptible_sleep(duration, stop_event)
    finally:
        release_mouse(button)


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
        if not _interruptible_sleep(random.uniform(interval_min, interval_max), stop_event):
            break


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
