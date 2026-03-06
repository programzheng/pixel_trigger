"""Hook 式自動化引擎 — 持續掃描螢幕，依據 Combo 規則觸發動作"""

from __future__ import annotations

import random
import threading
import time
from datetime import datetime

import input_controller
from models import ActionType, Combo, Profile, TriggerMode
from screen_capture import ScreenCapture


class HookEngine:
    """事件驅動引擎：每個 Combo 是一個 Hook（圖片觸發 → 動作）"""

    def __init__(
        self,
        on_combo_triggered=None,   # callback(combo_id: str, active: bool)
        on_cycle_complete=None,    # callback(cycle_count: int)
        on_status_change=None,     # callback(running: bool)
        on_debug_info=None,        # callback(combo_id: str, found: bool, confidence: float)
    ):
        self._profile: Profile | None = None
        self._running = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._capture = ScreenCapture()

        # combo_id → 上一輪是否偵測到
        self._prev_visible: dict[str, bool] = {}
        # combo_id → while_visible 動作 thread
        self._active_actions: dict[str, threading.Thread] = {}
        self._action_stop_events: dict[str, threading.Event] = {}

        self.cycle_count = 0

        self._on_combo_triggered = on_combo_triggered
        self._on_cycle_complete = on_cycle_complete
        self._on_status_change = on_status_change
        self._on_debug_info = on_debug_info

    @property
    def running(self) -> bool:
        return self._running

    def set_profile(self, profile: Profile):
        self._profile = profile
        self._prev_visible.clear()

    def start(self):
        if self._running or not self._profile:
            return
        self._running = True
        self._stop_event.clear()
        self.cycle_count = 0
        self._prev_visible = {c.id: False for c in self._profile.combos}
        self._thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._thread.start()
        if self._on_status_change:
            self._on_status_change(True)

    def stop(self):
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        for evt in self._action_stop_events.values():
            evt.set()
        for t in self._active_actions.values():
            t.join(timeout=2)
        self._active_actions.clear()
        self._action_stop_events.clear()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        if self._on_status_change:
            self._on_status_change(False)

    def _scan_loop(self):
        """主掃描迴圈 — 每週期只截圖一次"""
        while not self._stop_event.is_set():
            profile = self._profile
            if not profile:
                break

            enabled_combos = [
                c for c in profile.combos
                if c.enabled and (c.image_path or c.verify_text)
            ]

            # 截圖一次，所有 combo 共用
            frame = self._capture.grab_frame(profile.scan_region)
            if frame is None:
                self._interruptible_sleep(0.5)
                continue

            for combo in enabled_combos:
                if self._stop_event.is_set():
                    break
                self._process_combo(combo, frame, profile.scan_region)

            self.cycle_count += 1
            # 每 10 次才更新 GUI，避免堵塞 tkinter
            if self.cycle_count % 10 == 0 and self._on_cycle_complete:
                self._on_cycle_complete(self.cycle_count)

            interval = profile.scan_interval if profile else 0.1
            self._interruptible_sleep(interval)

        self._running = False

    def _process_combo(
        self, combo: Combo, frame, region: tuple[int, int, int, int] | None
    ):
        """處理單一 Combo 的偵測與觸發"""
        text_conf = 0.0
        confidence = 0.0
        match_rect = None

        if not combo.image_path and combo.verify_text:
            # ── 純 OCR 模式：無觸發圖片，直接對整個 frame 做 OCR ──
            found = False
            text_ok, text_conf = self._capture.ocr_verify(
                frame, combo.verify_text, combo.text_threshold,
                match_mode=combo.text_match_mode.value,
                match_ratio=combo.text_match_ratio,
            )
            if text_ok:
                found = True
                confidence = text_conf
                print(
                    f"[{datetime.now():%Y%m%d %H:%M:%S}][ocr] '{combo.name}' "
                    f"文字偵測到! text_conf={text_conf:.3f}"
                )
        else:
            # ── 圖片比對模式 ──
            found, confidence, pos = self._capture.match_template(
                frame, combo.image_path, combo.match_threshold, region
            )

            # 文字驗證（圖片匹配成功 + 有設定 verify_text 時才執行 OCR）
            if found and combo.verify_text and pos:
                size = self._capture.get_template_size(combo.image_path)
                if size:
                    tw, th = size
                    # pos 是螢幕絕對座標中心，轉回 frame 相對座標
                    fx = pos[0] - tw // 2
                    fy = pos[1] - th // 2
                    if region:
                        fx -= region[0]
                        fy -= region[1]
                    roi = frame[max(0, fy):fy + th, max(0, fx):fx + tw]
                    text_ok, text_conf, detected_text = self._capture.ocr_verify(
                        roi, combo.verify_text, combo.text_threshold,
                        match_mode=combo.text_match_mode.value,
                        match_ratio=combo.text_match_ratio,
                    )
                    if not text_ok:
                        found = False
                        print(
                            f"[{datetime.now():%Y%m%d %H:%M:%S}][ocr] '{combo.name}' "
                            f"圖片匹配但文字不符 text_conf={text_conf:.3f} "
                            f"detected='{detected_text}'"
                        )

            # 計算螢幕絕對座標框線（供 debug overlay 使用）
            if pos and combo.image_path:
                size = self._capture.get_template_size(combo.image_path)
                if size:
                    tw, th = size
                    cx, cy = pos
                    match_rect = (cx - tw // 2, cy - th // 2, tw, th)

        was_visible = self._prev_visible.get(combo.id, False)
        self._prev_visible[combo.id] = found

        # Debug callback — 每個週期都回報
        if self._on_debug_info:
            self._on_debug_info(combo.id, found, confidence, match_rect)

        # Debug log
        if found:
            extra = f" text_conf={text_conf:.3f}" if combo.verify_text else ""
            print(f"[{datetime.now():%Y%m%d %H:%M:%S}][detect] '{combo.name}' 偵測到! confidence={confidence:.3f}{extra}")
        elif not found and was_visible:
            print(f"[{datetime.now():%Y%m%d %H:%M:%S}][detect] '{combo.name}' 消失了 confidence={confidence:.3f}")

        if combo.trigger == TriggerMode.ON_APPEAR:
            if found and not was_visible:
                self._notify_triggered(combo.id, True)
                self._execute_action_once(combo)
                self._notify_triggered(combo.id, False)

        elif combo.trigger == TriggerMode.WHILE_VISIBLE:
            if found:
                if combo.id not in self._active_actions:
                    self._start_continuous_action(combo)
            else:
                if combo.id in self._active_actions:
                    self._stop_continuous_action(combo.id)

    def _execute_action_once(self, combo: Combo):
        """執行一次性動作（on_appear 用）"""
        action = combo.action
        at = action.action_type
        print(f"[{datetime.now():%Y%m%d %H:%M:%S}][action] '{combo.name}' → {at.value}")

        if at == ActionType.CLICK:
            input_controller.mouse_click(
                action.mouse_button.value, action.delay_min, action.delay_max
            )
        elif at == ActionType.RAPID_CLICK:
            input_controller.mouse_rapid_click(
                action.mouse_button.value,
                action.duration,
                action.interval_min,
                action.interval_max,
                self._stop_event,
            )
        elif at == ActionType.HOLD_MOUSE:
            input_controller.mouse_hold(
                action.mouse_button.value, action.duration, self._stop_event
            )
        elif at == ActionType.KEY_PRESS:
            input_controller.key_press(
                action.key, action.delay_min, action.delay_max
            )
        elif at == ActionType.RAPID_KEY:
            input_controller.key_rapid_press(
                action.key,
                action.duration,
                action.interval_min,
                action.interval_max,
                self._stop_event,
            )
        elif at == ActionType.HOLD_KEY:
            input_controller.key_hold(
                action.key, action.duration, self._stop_event
            )

    def _start_continuous_action(self, combo: Combo):
        stop_evt = threading.Event()
        self._action_stop_events[combo.id] = stop_evt

        t = threading.Thread(
            target=self._continuous_action_loop,
            args=(combo, stop_evt),
            daemon=True,
        )
        self._active_actions[combo.id] = t
        self._notify_triggered(combo.id, True)
        print(f"[{datetime.now():%Y%m%d %H:%M:%S}][action] '{combo.name}' → {combo.action.action_type.value} 持續中...")
        t.start()

    def _stop_continuous_action(self, combo_id: str):
        evt = self._action_stop_events.pop(combo_id, None)
        if evt:
            evt.set()
        t = self._active_actions.pop(combo_id, None)
        if t:
            t.join(timeout=2)
        self._notify_triggered(combo_id, False)

    def _continuous_action_loop(self, combo: Combo, stop_event: threading.Event):
        """while_visible 持續動作迴圈。
        長按類：按下一次，圖片消失才放開。
        其他：持續循環執行直到圖片消失。
        """
        action = combo.action
        at = action.action_type

        if at == ActionType.HOLD_MOUSE:
            import pyautogui
            btn = action.mouse_button.value
            while not stop_event.is_set() and not self._stop_event.is_set():
                pyautogui.mouseDown(button=btn)
                self._interruptible_sleep_event(action.duration, stop_event)
                pyautogui.mouseUp(button=btn)
                if stop_event.is_set() or self._stop_event.is_set():
                    break
                gap = random.uniform(action.interval_min, action.interval_max)
                self._interruptible_sleep_event(gap, stop_event)
            return

        if at == ActionType.HOLD_KEY:
            import pyautogui
            while not stop_event.is_set() and not self._stop_event.is_set():
                pyautogui.keyDown(action.key)
                self._interruptible_sleep_event(action.duration, stop_event)
                pyautogui.keyUp(action.key)
                if stop_event.is_set() or self._stop_event.is_set():
                    break
                gap = random.uniform(action.interval_min, action.interval_max)
                self._interruptible_sleep_event(gap, stop_event)
            return

        while not stop_event.is_set() and not self._stop_event.is_set():
            if at == ActionType.CLICK:
                input_controller.mouse_click(
                    action.mouse_button.value, action.delay_min, action.delay_max
                )
            elif at == ActionType.RAPID_CLICK:
                input_controller.mouse_rapid_click(
                    action.mouse_button.value,
                    duration=0.5,
                    interval_min=action.interval_min,
                    interval_max=action.interval_max,
                    stop_event=stop_event,
                )
            elif at == ActionType.KEY_PRESS:
                input_controller.key_press(
                    action.key, action.delay_min, action.delay_max
                )
            elif at == ActionType.RAPID_KEY:
                input_controller.key_rapid_press(
                    action.key,
                    duration=0.5,
                    interval_min=action.interval_min,
                    interval_max=action.interval_max,
                    stop_event=stop_event,
                )

            if not stop_event.is_set():
                time.sleep(0.02)

    def _wait_until_stopped(self, stop_event: threading.Event):
        while not stop_event.is_set() and not self._stop_event.is_set():
            time.sleep(0.05)

    def _interruptible_sleep_event(self, seconds: float, stop_event: threading.Event):
        """同時監聽 combo stop_event 與全局 _stop_event 的可中斷等待"""
        step = 0.05
        elapsed = 0.0
        while elapsed < seconds:
            if stop_event.is_set() or self._stop_event.is_set():
                return
            time.sleep(step)
            elapsed += step

    def _notify_triggered(self, combo_id: str, active: bool):
        if self._on_combo_triggered:
            self._on_combo_triggered(combo_id, active)

    def _interruptible_sleep(self, seconds: float):
        step = 0.05
        elapsed = 0.0
        while elapsed < seconds:
            if self._stop_event.is_set():
                return
            time.sleep(step)
            elapsed += step
