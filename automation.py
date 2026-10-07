"""Hook 式自動化引擎 — 持續掃描螢幕，依據 Combo 規則觸發動作"""

from __future__ import annotations

import random
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime

import input_controller
from models import ActionType, Combo, Profile, TriggerMode
from screen_capture import ScreenCapture, find_color_marker, color_band_state
from window_target import get_window_region, list_windows


class HookEngine:
    """事件驅動引擎：每個 Combo 是一個 Hook（圖片觸發 → 動作）"""

    def __init__(
        self,
        on_combo_triggered=None,   # callback(combo_id: str, active: bool)
        on_cycle_complete=None,    # callback(cycle_count: int)
        on_status_change=None,     # callback(running: bool)
        on_debug_info=None,        # callback(combo_id: str, found: bool, confidence: float)
        on_log=None,               # callback(combo_id: str, message: str, level: str)
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
        self._once_actions: dict[str, threading.Thread] = {}
        self._once_action_stop_events: dict[str, threading.Event] = {}
        self._visible_rects: dict[str, tuple[int, int, int, int]] = {}
        self._mouse_locks = {button: threading.Lock() for button in ("left", "right", "middle")}

        self.cycle_count = 0

        self._on_combo_triggered = on_combo_triggered
        self._on_cycle_complete = on_cycle_complete
        self._on_status_change = on_status_change
        self._on_debug_info = on_debug_info
        self._on_log = on_log
        self._last_logs = {}

    def _log(self, combo, reason, detail="", category="condition", level="info"):
        if self._on_log is None:
            return
        now = time.monotonic()
        key = (combo.id, category)
        previous = self._last_logs.get(key)
        # 同一條件每秒更新一次數值；原因切換立即記錄。
        if previous and previous[0] == reason and now - previous[1] < 1:
            return
        self._last_logs[key] = (reason, now)
        self._on_log(combo.id, f"[{combo.trigger.value}] {reason} {detail}".rstrip(), level)

    @property
    def running(self) -> bool:
        return self._running

    def set_profile(self, profile: Profile):
        self._profile = profile
        self._prev_visible.clear()
        self._visible_rects.clear()

    def get_match_scale(self, image_path: str) -> float | None:
        return self._capture.get_match_scale(image_path)

    def start(self):
        if self._running or not self._profile or (self._thread and self._thread.is_alive()):
            return
        profile = self._profile
        if (any(combo.enabled and combo.verify_text for combo in profile.combos)
                and not self._capture.ocr_available()):
            raise ValueError("Tesseract OCR runtime or tessdata is unavailable.")
        if profile.window_title:
            if not profile.window_handle:
                matches = [(hwnd, pid) for hwnd, title, pid in list_windows()
                           if title == profile.window_title]
                if len(matches) != 1:
                    raise ValueError("目標視窗不存在或有同名視窗，請重新選擇目標視窗。")
                profile.window_handle, profile.window_pid = matches[0]
            if get_window_region(profile.window_handle, profile.window_pid) is None:
                raise ValueError("目標視窗不可用，請還原視窗或重新選擇。")
        for combo in profile.combos:
            if combo.enabled and combo.detection_mode == "active" and combo.action.action_type == ActionType.PLAY_SOUND:
                if not 0 <= combo.action.delay_min <= combo.action.delay_max < float("inf"):
                    raise ValueError("音效延遲必須是有限非負數，且最小值不能大於最大值。")
            if combo.enabled and combo.detection_mode == "active" and combo.action.action_type == ActionType.CLICK_IMAGE:
                action = combo.action
                if not 0 <= action.target_threshold <= 1:
                    raise ValueError("點擊目標的圖片閾值必須介於 0~1。")
                if not self._capture.load_template(action.target_image_path):
                    raise ValueError("請指定有效的點擊目標圖片。")
        positions = [c for c in self._profile.combos
                     if c.enabled and c.detection_mode == "active" and c.trigger.tracks_marker]
        for combo in positions:
            combo.position_control.validate()
            if combo.position_control.region_space == "template":
                if not self._capture.load_template(combo.image_path):
                    raise ValueError("位置控制請先指定有效的觸發圖片。")
                tw, th = self._capture.get_template_size(combo.image_path)
                x, y, w, h = combo.position_control.region
                if x < 0 or y < 0 or x + w > tw or y + h > th:
                    raise ValueError("箭頭追蹤區域必須在觸發圖片內。")
            action = combo.action
            if (any(not math.isfinite(v) or v < 0 for v in
                    (action.delay_min, action.delay_max, action.duration,
                     action.interval_min, action.interval_max))
                    or action.delay_min > action.delay_max or action.interval_min > action.interval_max):
                raise ValueError("動作時間必須是非負數，且最小值不能大於最大值。")
            if action.action_type in (ActionType.KEY_PRESS, ActionType.RAPID_KEY, ActionType.HOLD_KEY):
                if action.key not in input_controller.pyautogui.KEYBOARD_KEYS:
                    raise ValueError("請指定有效的鍵盤按鍵。")
        self._running = True
        self._stop_event.clear()
        self._last_logs.clear()
        for combo in profile.combos:
            if combo.enabled and combo.detection_mode == "active":
                self._log(combo, "開始偵測", category="lifecycle")
        self.cycle_count = 0
        self._prev_visible = {c.id: False for c in self._profile.combos}
        self._thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._thread.start()
        if self._on_status_change:
            self._on_status_change(True)

    def stop(self):
        if not self._running and not (self._thread and self._thread.is_alive()):
            return
        self._running = False
        self._stop_event.set()
        for evt in list(self._action_stop_events.values()):
            evt.set()
        for evt in list(self._once_action_stop_events.values()):
            evt.set()
        for t in list(self._active_actions.values()):
            t.join(timeout=2)
        self._active_actions.clear()
        self._action_stop_events.clear()
        if self._thread:
            self._thread.join(timeout=5)
            if not self._thread.is_alive():
                self._thread = None
        if self._on_status_change:
            self._on_status_change(False)

    def _scan_loop(self):
        """每個 Combo 同時最多處理一張最新畫面，慢規則不阻塞其他規則。"""
        workers = {}
        try:
            for combo in self._profile.combos:
                if combo.enabled and combo.detection_mode == "active" and combo.trigger.tracks_marker:
                    self._start_continuous_action(combo)
            with ThreadPoolExecutor(max_workers=max(1, len(self._profile.combos))) as pool:
                try:
                    self._scan_frames(pool, workers)
                finally:
                    self._stop_event.set()
        finally:
            self._stop_event.set()
            for combo in self._profile.combos:
                if combo.enabled and combo.detection_mode == "active":
                    self._log(combo, "偵測已停止", category="lifecycle")
            for evt in list(self._action_stop_events.values()):
                evt.set()
            for evt in list(self._once_action_stop_events.values()):
                evt.set()
            for thread in list(self._active_actions.values()):
                thread.join(timeout=2)
            for thread in list(self._once_actions.values()):
                thread.join(timeout=2)
            self._active_actions.clear()
            self._action_stop_events.clear()
            self._once_actions.clear()
            self._once_action_stop_events.clear()
            self._visible_rects.clear()
            self._running = False
            if self._on_status_change:
                self._on_status_change(False)

    def _scan_frames(self, pool, workers):
        """主掃描迴圈 — 每週期只截圖一次"""
        while not self._stop_event.is_set():
            profile = self._profile
            if not profile:
                break

            enabled_combos = [
                c for c in profile.combos
                if c.enabled and c.detection_mode == "active" and not c.trigger.tracks_marker
                and (c.image_path or c.verify_text)
            ]

            # 截圖一次，所有 combo 共用
            region = self._scan_region()
            if self._stop_event.is_set():
                break
            ready_combos = [c for c in enabled_combos
                            if c.id not in workers or workers[c.id].done()]
            if enabled_combos and not ready_combos:
                self._interruptible_sleep(profile.scan_interval)
                continue
            capture_started = time.perf_counter()
            frame = self._capture.grab_frame(region)
            capture_ms = (time.perf_counter() - capture_started) * 1000
            if capture_ms >= 50:
                for combo in ready_combos:
                    self._log(combo, "截圖耗時", f"{capture_ms:.0f} ms",
                              category="capture_timing")
            if frame is None:
                for combo in enabled_combos:
                    self._log(combo, "截圖失敗", level="failure")
                    self._prev_visible[combo.id] = False
                    self._visible_rects.pop(combo.id, None)
                    if combo.trigger == TriggerMode.WHILE_VISIBLE and combo.id in self._active_actions:
                        self._stop_continuous_action(combo.id)
                    if combo.trigger == TriggerMode.ON_APPEAR and combo.id in self._once_action_stop_events:
                        self._once_action_stop_events[combo.id].set()
                    if self._on_debug_info:
                        self._on_debug_info(combo.id, False, 0.0, None)
                self._interruptible_sleep(0.5)
                continue

            for combo in ready_combos:
                if self._stop_event.is_set():
                    break
                worker = workers.get(combo.id)
                if worker is not None:
                    try:
                        worker.result()
                    except Exception as e:
                        self._log(combo, "偵測／動作錯誤", str(e), category="error", level="error")
                        raise
                workers[combo.id] = pool.submit(self._process_combo, combo, frame, region)

            self.cycle_count += 1
            # 每輪回報，GUI 在 main thread 定時更新。
            if self._on_cycle_complete:
                self._on_cycle_complete(self.cycle_count)

            interval = profile.scan_interval if profile else 0.1
            self._interruptible_sleep(interval)

    def _scan_region(self):
        profile = self._profile
        if profile.window_title:
            region = get_window_region(profile.window_handle, profile.window_pid)
            if region is None:
                for combo in profile.combos:
                    if combo.enabled and combo.detection_mode == "active":
                        self._log(combo, "目標視窗不可用（最小化／關閉等），停止偵測", category="lifecycle", level="error")
                self._stop_event.set()
                for event in list(self._action_stop_events.values()):
                    event.set()
            return region
        return profile.scan_region

    def _process_combo(
        self, combo: Combo, frame, region: tuple[int, int, int, int] | None
    ):
        """處理單一 Combo 的偵測與觸發"""
        if combo.detection_mode != "active":
            return
        if self._stop_event.is_set():
            return
        text_conf = 0.0
        confidence = 0.0
        match_rect = None
        reason = ""

        if not combo.image_path and combo.verify_text:
            # ── 純 OCR 模式：無觸發圖片，直接對整個 frame 做 OCR ──
            found = False
            text_ok, text_conf, _detected_text = self._capture.ocr_verify(
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
            reason = "OCR 通過" if text_ok else "OCR 未通過"
        else:
            # ── 圖片比對模式 ──
            match_started = time.perf_counter()
            previous_rect = (self._visible_rects.get(combo.id)
                             if combo.trigger == TriggerMode.WHILE_VISIBLE and not combo.verify_text
                             else None)
            if previous_rect is not None:
                confidence = self._capture.score_template_at(
                    frame, combo.image_path, previous_rect, region)
                found = confidence >= combo.match_threshold
                pos = (previous_rect[0] + previous_rect[2] // 2,
                       previous_rect[1] + previous_rect[3] // 2) if found else None
                if found:
                    match_rect = previous_rect
            else:
                found, confidence, pos = self._capture.match_template(
                    frame, combo.image_path, combo.match_threshold, region,
                    scale_limit=1, stop_event=self._stop_event,
                )
            match_ms = (time.perf_counter() - match_started) * 1000
            if match_ms >= 50:
                mode = "上次位置" if previous_rect is not None else "全畫面"
                self._log(combo, "圖片比對耗時", f"{match_ms:.0f} ms，{mode}",
                          category="match_timing")
            reason = "圖片通過" if found else "圖片定位未通過"

            # 文字驗證（圖片匹配成功 + 有設定 verify_text 時才執行 OCR）
            if found and combo.verify_text and pos:
                size = self._capture.get_match_size(combo.image_path)
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
                    reason = "圖片與 OCR 通過" if text_ok else "圖片通過但 OCR 未通過"

            # 計算螢幕絕對座標框線（供 debug overlay 使用）
            if pos and combo.image_path and match_rect is None:
                size = self._capture.get_match_size(combo.image_path)
                if size:
                    tw, th = size
                    cx, cy = pos
                    match_rect = (cx - tw // 2, cy - th // 2, tw, th)

        if self._stop_event.is_set():
            return
        was_visible = self._prev_visible.get(combo.id, False)
        if self._profile and self._profile.window_title and self._scan_region() != region:
            return
        self._prev_visible[combo.id] = found
        if combo.trigger == TriggerMode.WHILE_VISIBLE:
            if found and match_rect is not None:
                self._visible_rects[combo.id] = match_rect
            else:
                self._visible_rects.pop(combo.id, None)
        detail = f"圖片分數={confidence:.6f}／閾值={combo.match_threshold:.6f}" if combo.image_path else ""
        if combo.verify_text:
            detail += f" OCR分數={text_conf:.3f}／閾值={combo.text_threshold:.3f} 文字模式={combo.text_match_mode.value}"
        if found and combo.trigger == TriggerMode.ON_APPEAR:
            detail += "；已持續可見，等待消失再觸發" if was_visible else "；首次出現，觸發動作"
        elif combo.trigger == TriggerMode.WHILE_VISIBLE:
            detail += "；條件成立，持續動作" if found else "；條件失效，停止動作"
        self._log(combo, reason, detail, level="info" if found else "failure")

        # Debug callback — 每個週期都回報
        if self._on_debug_info:
            self._on_debug_info(combo.id, found, confidence, match_rect)

        # Debug log
        if found:
            extra = f" text_conf={text_conf:.3f}" if combo.verify_text else ""
            print(f"[{datetime.now():%Y%m%d %H:%M:%S}][detect] '{combo.name}' 偵測到! confidence={confidence:.6f}{extra}")
        elif not found and was_visible:
            print(f"[{datetime.now():%Y%m%d %H:%M:%S}][detect] '{combo.name}' 消失了 confidence={confidence:.6f}")

        if combo.trigger == TriggerMode.ON_APPEAR:
            if not found and combo.id in self._once_action_stop_events:
                self._once_action_stop_events[combo.id].set()
            elif found and not was_visible:
                if combo.id not in self._once_actions:
                    stop_event = threading.Event()
                    thread = threading.Thread(target=self._run_action_once,
                                              args=(combo, stop_event, match_rect), daemon=True)
                    self._once_action_stop_events[combo.id] = stop_event
                    self._once_actions[combo.id] = thread
                    thread.start()
                else:
                    self._prev_visible[combo.id] = False

        elif combo.trigger == TriggerMode.WHILE_VISIBLE:
            if found:
                if combo.id not in self._active_actions:
                    self._start_continuous_action(combo)
            else:
                if combo.id in self._active_actions:
                    self._stop_continuous_action(combo.id)

    def _execute_action_once(self, combo: Combo, stop_event=None):
        """執行一次性動作（on_appear 用）"""
        stop_event = self._stop_event if stop_event is None else stop_event
        with self._mouse_action(combo, stop_event) as acquired:
            if acquired:
                self._execute_action_once_unlocked(combo, stop_event)

    def _run_action_once(self, combo: Combo, stop_event: threading.Event, match_rect):
        self._notify_triggered(combo.id, True)
        try:
            if combo.action.action_type == ActionType.CLICK and match_rect is not None:
                delay = random.uniform(combo.action.delay_min, combo.action.delay_max)
                if stop_event.wait(delay) or self._stop_event.is_set():
                    return
                with self._mouse_action(combo, stop_event) as acquired:
                    if not acquired:
                        return
                    region = self._scan_region()
                    if self._stop_event.is_set():
                        return
                    frame = self._capture.grab_frame(region)
                    if (frame is None or self._scan_region() != region
                            or self._capture.score_template_at(
                                frame, combo.image_path, match_rect, region) < combo.match_threshold):
                        self._log(combo, "點擊前圖片已消失，取消動作", category="action")
                        if self._on_debug_info:
                            self._on_debug_info(combo.id, False, 0.0, None)
                        return
                    self._execute_action_once_unlocked(combo, stop_event, click_delay=0)
            else:
                self._execute_action_once(combo, stop_event)
        except Exception as e:
            self._log(combo, "偵測／動作錯誤", str(e), category="error", level="error")
            self._stop_event.set()
        finally:
            self._notify_triggered(combo.id, False)
            if self._once_actions.get(combo.id) is threading.current_thread():
                self._once_actions.pop(combo.id, None)
                self._once_action_stop_events.pop(combo.id, None)

    @contextmanager
    def _mouse_action(self, combo: Combo, stop_event: threading.Event):
        if combo.action.action_type not in (ActionType.CLICK, ActionType.CLICK_IMAGE,
                                           ActionType.RAPID_CLICK, ActionType.HOLD_MOUSE):
            yield True
            return
        lock = self._mouse_locks[combo.action.mouse_button.value]
        wait_started = time.perf_counter()
        while not stop_event.is_set() and not self._stop_event.is_set():
            if lock.acquire(timeout=0.05):
                break
        else:
            yield False
            return
        try:
            wait_ms = (time.perf_counter() - wait_started) * 1000
            if wait_ms >= 50:
                self._log(combo, "滑鼠等待耗時", f"{wait_ms:.0f} ms",
                          category="mouse_timing")
            yield not stop_event.is_set() and not self._stop_event.is_set()
        finally:
            lock.release()

    def _execute_action_once_unlocked(self, combo: Combo, stop_event=None, click_delay=None):
        stop_event = self._stop_event if stop_event is None else stop_event
        action = combo.action
        at = action.action_type
        self._log(combo, "執行動作", at.value, category="input")
        print(f"[{datetime.now():%Y%m%d %H:%M:%S}][action] '{combo.name}' → {at.value}")

        if at == ActionType.CLICK:
            input_controller.mouse_click(
                action.mouse_button.value,
                action.delay_min if click_delay is None else click_delay,
                action.delay_max if click_delay is None else click_delay,
                stop_event,
            )
        elif at == ActionType.CLICK_IMAGE:
            self._click_target_image(combo, stop_event)
        elif at == ActionType.RAPID_CLICK:
            input_controller.mouse_rapid_click(
                action.mouse_button.value,
                action.duration,
                action.interval_min,
                action.interval_max,
                stop_event,
            )
        elif at == ActionType.HOLD_MOUSE:
            input_controller.mouse_hold(
                action.mouse_button.value, action.duration, stop_event
            )
        elif at == ActionType.KEY_PRESS:
            input_controller.key_press(
                action.key, action.delay_min, action.delay_max, stop_event
            )
        elif at == ActionType.RAPID_KEY:
            input_controller.key_rapid_press(
                action.key,
                action.duration,
                action.interval_min,
                action.interval_max,
                stop_event,
            )
        elif at == ActionType.HOLD_KEY:
            input_controller.key_hold(
                action.key, action.duration, stop_event
            )
        elif at == ActionType.PLAY_SOUND:
            input_controller.play_sound(action.delay_min, action.delay_max, stop_event)

    def _click_target_image(self, combo: Combo, stop_event: threading.Event):
        action = combo.action
        self._interruptible_sleep_event(random.uniform(action.delay_min, action.delay_max), stop_event)
        if stop_event.is_set() or self._stop_event.is_set():
            return
        region = self._scan_region()
        if self._stop_event.is_set():
            return
        frame = self._capture.grab_frame(region)
        if frame is None:
            return
        found, _confidence, pos = self._capture.match_template(
            frame, action.target_image_path, action.target_threshold, region,
            scale_limit=1, stop_event=stop_event,
        )
        self._log(combo, "點擊目標圖片通過" if found else "點擊目標圖片未通過",
                  f"分數={_confidence:.6f}／閾值={action.target_threshold:.6f}", category="target", level="info" if found else "failure")
        if (not found or pos is None or stop_event.is_set() or self._stop_event.is_set()
                or self._scan_region() != region):
            return
        input_controller.mouse_click_at(pos[0], pos[1], action.mouse_button.value, stop_event)

    def _start_continuous_action(self, combo: Combo):
        stop_evt = threading.Event()
        self._action_stop_events[combo.id] = stop_evt

        t = threading.Thread(
            target=self._run_continuous_action,
            args=(combo, stop_evt),
            daemon=True,
        )
        self._active_actions[combo.id] = t
        self._notify_triggered(combo.id, not combo.trigger.tracks_marker)
        print(f"[{datetime.now():%Y%m%d %H:%M:%S}][action] '{combo.name}' → {combo.action.action_type.value} 持續中...")
        t.start()

    def _run_continuous_action(self, combo: Combo, stop_event: threading.Event):
        try:
            self._continuous_action_loop(combo, stop_event)
        except Exception as e:
            self._log(combo, "偵測／動作錯誤", str(e), category="error", level="error")
            if combo.trigger.tracks_marker:
                self._stop_event.set()
            raise
        finally:
            if self._active_actions.get(combo.id) is threading.current_thread():
                self._notify_triggered(combo.id, False)

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

        if combo.trigger.tracks_marker:
            self._position_control_loop(combo, stop_event)
            return

        if at == ActionType.PLAY_SOUND:
            self._execute_action_once_unlocked(combo, stop_event)
            self._wait_until_stopped(stop_event)
            return

        if at == ActionType.HOLD_MOUSE:
            import pyautogui
            btn = action.mouse_button.value
            while not stop_event.is_set() and not self._stop_event.is_set():
                with self._mouse_action(combo, stop_event) as acquired:
                    if not acquired:
                        break
                    try:
                        pyautogui.mouseDown(button=btn)
                        self._interruptible_sleep_event(action.duration, stop_event)
                    finally:
                        input_controller.release_mouse(btn)
                if stop_event.is_set() or self._stop_event.is_set():
                    break
                gap = random.uniform(action.interval_min, action.interval_max)
                self._interruptible_sleep_event(gap, stop_event)
            return

        if at == ActionType.HOLD_KEY:
            import pyautogui
            while not stop_event.is_set() and not self._stop_event.is_set():
                pyautogui.keyDown(action.key)
                try:
                    self._interruptible_sleep_event(action.duration, stop_event)
                finally:
                    pyautogui.keyUp(action.key)
                if stop_event.is_set() or self._stop_event.is_set():
                    break
                gap = random.uniform(action.interval_min, action.interval_max)
                self._interruptible_sleep_event(gap, stop_event)
            return

        while not stop_event.is_set() and not self._stop_event.is_set():
            if at == ActionType.CLICK:
                self._interruptible_sleep_event(
                    random.uniform(action.delay_min, action.delay_max), stop_event)
                if stop_event.is_set() or self._stop_event.is_set():
                    break
            with self._mouse_action(combo, stop_event) as acquired:
                if not acquired:
                    break
                self._repeat_action(combo, stop_event)

            if not stop_event.is_set():
                time.sleep(0.02)

    def _repeat_action(self, combo: Combo, stop_event: threading.Event):
        action = combo.action
        at = action.action_type
        if at == ActionType.CLICK:
            input_controller.mouse_click(
                action.mouse_button.value, 0, 0, stop_event
            )
        elif at == ActionType.CLICK_IMAGE:
            self._click_target_image(combo, stop_event)
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
                action.key, action.delay_min, action.delay_max, stop_event
            )
        elif at == ActionType.RAPID_KEY:
            input_controller.key_rapid_press(
                action.key,
                duration=0.5,
                interval_min=action.interval_min,
                interval_max=action.interval_max,
                stop_event=stop_event,
            )

    def _position_control_loop(self, combo: Combo, stop_event: threading.Event):
        settings = combo.position_control
        active = False
        action_stop = threading.Event()
        action_thread = None
        missing_since = None
        try:
            while not stop_event.is_set() and not self._stop_event.is_set():
                if not combo.enabled or combo not in self._profile.combos:
                    break
                region = self._scan_region()
                if self._stop_event.is_set():
                    break
                position_started = time.perf_counter()
                frame, screen_region = self._capture.position_frame(
                    combo, self._stop_event, scan_region=region)
                position_ms = (time.perf_counter() - position_started) * 1000
                if position_ms >= 50:
                    self._log(combo, "定位與截圖耗時", f"{position_ms:.0f} ms",
                              category="position_timing")
                marker = find_color_marker(frame, settings)
                score = self._capture.get_position_confidence()
                detail = f"定位分數={score:.6f}／閾值={combo.match_threshold:.6f}"
                reason = ""
                if frame is None:
                    reason = "圖片定位未通過" if score < combo.match_threshold else "追蹤區域無法擷取"
                elif marker is None:
                    reason = "找不到唯一箭頭（顏色／面積不符或有多個候選）"
                position = marker[0][0] / max(1, frame.shape[1] - 1) if marker else None
                should_run = active
                if combo.trigger == TriggerMode.COLOR_BAND:
                    detected, ratio = color_band_state(frame, marker, settings, active)
                    should_run = detected
                    if frame is None:
                        missing_since = None
                        should_run = False
                    elif ratio is None:
                        now = time.monotonic()
                        if missing_since is None:
                            missing_since = now
                        should_run = active and now - missing_since < settings.band_missing_ms / 1000
                    else:
                        missing_since = None
                    if ratio is None:
                        reason = reason or "有效色帶取樣不足"
                        if should_run:
                            reason += "；漏判容忍中"
                            remaining = max(0, settings.band_missing_ms - (now - missing_since) * 1000)
                            detail += f"；漏判容忍中，維持動作，剩餘約 {remaining:.0f} ms"
                        else:
                            reason += "；停止／等待"
                            detail += "；停止／等待，漏判不能啟動動作"
                    else:
                        reason = ("色帶達啟動比例" if not active and detected else
                                  "色帶未達啟動比例" if not active else
                                  "色帶維持執行" if detected else "色帶達停止比例")
                        detail += f" 色帶={ratio:.3f}／啟動={settings.band_start:.3f}／停止={settings.band_stop:.3f}"
                elif position is None or position >= settings.release_at:
                    should_run = False
                elif position <= settings.press_at:
                    should_run = True
                if combo.trigger == TriggerMode.POSITION_CONTROL and marker:
                    reason = "位置啟動" if position <= settings.press_at else "位置停止" if position >= settings.release_at else "位置在界線間，維持原狀"
                    detail += f" 位置={position:.1%}／啟動={settings.press_at:.1%}／停止={settings.release_at:.1%}；{'執行' if should_run else '等待'}"
                self._log(combo, reason, detail, level="info" if (detected if combo.trigger == TriggerMode.COLOR_BAND else marker is not None) else "failure")
                if self._stop_event.is_set() or stop_event.is_set():
                    break
                if should_run != active:
                    if should_run:
                        # 前一輪動作必須結束後才開下一輪；慢圖片比對不能重疊送出輸入。
                        if action_thread is None or not action_thread.is_alive():
                            action_stop = threading.Event()
                            action_thread = threading.Thread(target=self._controlled_action_loop,
                                                             args=(combo, action_stop), daemon=True)
                            action_thread.start()
                            active = True
                    else:
                        action_stop.set()
                        active = False
                if self._on_debug_info:
                    rect = None
                    if marker:
                        x, y, w, h = marker[1]
                        rect = (screen_region[0] + x, screen_region[1] + y, w, h)
                    self._on_debug_info(combo.id, detected if combo.trigger == TriggerMode.COLOR_BAND else marker is not None,
                                        self._capture.get_position_confidence(), rect)
                self._interruptible_sleep_event(0.04, stop_event)
        finally:
            action_stop.set()
            if action_thread:
                action_thread.join()
            self._notify_triggered(combo.id, False)

    def _controlled_action_loop(self, combo: Combo, stop_event: threading.Event):
        """條件成立期間依一般動作參數執行；單擊類每次成立只執行一次。"""
        action = combo.action
        single = action.action_type in (ActionType.CLICK, ActionType.CLICK_IMAGE, ActionType.KEY_PRESS, ActionType.PLAY_SOUND)
        try:
            while not stop_event.is_set() and not self._stop_event.is_set():
                # 單擊與點圖本身有延遲；其他動作在此套用相同設定。
                if not single and stop_event.wait(random.uniform(action.delay_min, action.delay_max)):
                    break
                with self._mouse_action(combo, stop_event) as acquired:
                    if not acquired or self._stop_event.is_set():
                        break
                    self._notify_triggered(combo.id, True)
                    try:
                        self._execute_action_once_unlocked(combo, stop_event)
                    finally:
                        self._notify_triggered(combo.id, False)
                if single:
                    break
                if stop_event.wait(max(0.02, random.uniform(action.interval_min, action.interval_max))):
                    break
        except Exception as e:
            self._log(combo, "動作錯誤", str(e), category="error", level="error")
            self._stop_event.set()
            raise

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
            time.sleep(min(step, seconds - elapsed))
            elapsed += step

    def _notify_triggered(self, combo_id: str, active: bool):
        if self._profile:
            combo = next((c for c in self._profile.combos if c.id == combo_id), None)
            if combo:
                self._log(combo, "動作開始" if active else "動作結束／取消或等待", category="action")
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
