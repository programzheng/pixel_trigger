"""Visible-screen balance tracking and daily CSV ledger."""

import csv
import re
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from profile_manager import PROFILES_DIR
from screen_capture import ScreenCapture
from window_target import capture_region, get_window_region


FIELDS = ["timestamp", "profile_id", "profile_name", "combo_id", "combo_name",
          "record_type", "balance", "previous_balance", "delta", "ocr_text",
          "ocr_confidence"]


def parse_balance(text):
    text = text.strip()
    if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", text):
        raise ValueError("OCR 必須是單一非負整數。")
    return int(text.replace(",", ""))


def validate_combo(combo):
    region = combo.calc_region
    if (not region or len(region) != 4 or any(type(v) is not int for v in region)
            or min(region[2:]) < 1):
        raise ValueError("請設定數字區域的 X、Y 偏移及正整數寬高。")
    if type(combo.daily_limit) is not int or combo.daily_limit < 1:
        raise ValueError("每日上限必須是正整數。")
    if not 0 < combo.warning_ratio <= 1 or not 0 <= combo.text_threshold <= 1:
        raise ValueError("預警比例與 OCR 信心度設定不正確。")
    if (type(combo.low_confidence_confirmations) is not int
            or not 2 <= combo.low_confidence_confirmations <= 10):
        raise ValueError("低信心確認次數必須是 2~10 的整數。")
    if not 0 <= combo.match_threshold <= 1:
        raise ValueError("圖片閾值必須介於 0~1。")


class Ledger:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else PROFILES_DIR.parent / "calc"
        self.lock = threading.RLock()
        self.rows = []
        self.last = {}

    def load(self):
        with self.lock:
            rows, last = [], {}
            for path in sorted(self.directory.glob("*.csv")):
                with path.open(encoding="utf-8-sig", newline="") as stream:
                    reader = csv.DictReader(stream)
                    if reader.fieldnames != FIELDS:
                        raise ValueError(f"CSV 欄位毀損：{path.name}")
                    for row in reader:
                        try:
                            stamp = datetime.strptime(row["timestamp"], "%Y%m%d%H%M%S")
                            if len(row["timestamp"]) != 14 or stamp.strftime("%Y%m%d") != path.stem:
                                raise ValueError()
                            balance = parse_balance(row["balance"])
                            previous = parse_balance(row["previous_balance"]) if row["previous_balance"] else None
                            delta = int(row["delta"])
                            confidence = float(row["ocr_confidence"])
                            key = (row["profile_id"], row["combo_id"])
                            if (None in row or any(v is None for v in row.values())
                                    or not all(key) or not 0 <= confidence <= 1
                                    or row["record_type"] not in ("baseline", "change", "reset")
                                    or (row["record_type"] != "change" and delta != 0)
                                    or (row["record_type"] == "change" and
                                        (previous != last.get(key) or delta != balance - previous))):
                                raise ValueError()
                        except (ValueError, TypeError, KeyError):
                            raise ValueError(f"CSV 紀錄毀損：{path.name} 第 {reader.line_num} 行") from None
                        rows.append(row)
                        last[key] = balance
            self.rows, self.last = rows, last

    def record(self, profile, combo, balance, text, confidence, now=None, reset=False):
        now = now or datetime.now()
        key = (profile.id, combo.id)
        with self.lock:
            previous = self.last.get(key)
            if previous == balance and not reset:
                return None
            kind = "reset" if reset else "baseline" if previous is None else "change"
            delta = balance - previous if kind == "change" else 0
            row = dict(zip(FIELDS, [now.strftime("%Y%m%d%H%M%S"), profile.id,
                       profile.name, combo.id, combo.name, kind, str(balance),
                       str(previous) if previous is not None else "", str(delta), text,
                       str(confidence)]))
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"{now:%Y%m%d}.csv"
            header = not path.exists() or path.stat().st_size == 0
            with path.open("a", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=FIELDS)
                if header:
                    writer.writeheader()
                writer.writerow(row)
                stream.flush()
            self.rows.append(row)
            self.last[key] = balance
            return row

    def snapshot(self, profile_id, combo_id, prefix):
        # ponytail: in-memory CSV scan; use SQLite if retained history makes this slow.
        with self.lock:
            rows = [dict(r) for r in self.rows if r["profile_id"] == profile_id
                    and r["combo_id"] == combo_id and r["timestamp"].startswith(prefix)]
        income = sum(max(0, int(r["delta"])) for r in rows)
        expense = sum(max(0, -int(r["delta"])) for r in rows)
        return income, expense, income - expense, rows

    def edit_balance(self, index, balance):
        if type(balance) is not int or balance < 0:
            raise ValueError("身上金額必須是非負整數。")
        with self.lock:
            if not 0 <= index < len(self.rows):
                raise ValueError("找不到要編輯的紀錄。")
            rows = deepcopy(self.rows)
            old_balance = int(rows[index]["balance"])
            rows[index]["balance"] = str(balance)
            rows[index]["ocr_text"] = f"manual:{balance}"
            rows[index]["ocr_confidence"] = "1.0"
            self._replace_rows(rows)
            return old_balance

    def delete_record(self, index):
        with self.lock:
            if not 0 <= index < len(self.rows):
                raise ValueError("找不到要刪除的紀錄。")
            rows = deepcopy(self.rows)
            deleted = rows.pop(index)
            self._replace_rows(rows)
            return deleted

    def _replace_rows(self, rows):
        last = {}
        for row in rows:
            key = (row["profile_id"], row["combo_id"])
            previous = last.get(key)
            if previous is None and row["record_type"] == "change":
                row["record_type"] = "baseline"
            if row["record_type"] == "change":
                row["previous_balance"] = str(previous)
                row["delta"] = str(int(row["balance"]) - previous)
            else:
                row["previous_balance"] = str(previous) if previous is not None else ""
                row["delta"] = "0"
            last[key] = int(row["balance"])

        by_date = {}
        for row in rows:
            by_date.setdefault(row["timestamp"][:8], []).append(row)
        self.directory.mkdir(parents=True, exist_ok=True)
        pending = []
        try:
            for date, dated_rows in by_date.items():
                path = self.directory / f"{date}.csv"
                temp = path.with_name(f".{path.name}.tmp")
                with temp.open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=FIELDS)
                    writer.writeheader()
                    writer.writerows(dated_rows)
                pending.append((temp, path))
            for temp, path in pending:
                temp.replace(path)
            for path in self.directory.glob("*.csv"):
                if path.stem not in by_date:
                    path.unlink()
        finally:
            for temp, _path in pending:
                temp.unlink(missing_ok=True)
        self.rows, self.last = rows, last


class BackgroundCalc:
    def __init__(self, notify, ledger=None):
        self.notify = notify
        self.ledger = ledger or Ledger()
        self.capture = ScreenCapture()
        self.thread = None
        self.stop_event = threading.Event()
        self.reset_ids = set()
        self.reset_lock = threading.Lock()
        self.active_ids = set()

    @property
    def running(self):
        return self.busy and not self.stop_event.is_set()

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, profile):
        if self.running:
            return
        if self.busy:
            raise ValueError("上一輪 OCR 正在停止，請稍後再啟動背景偵測。")
        combos = [c for c in profile.combos if c.enabled and c.detection_mode == "background"]
        if not combos:
            raise ValueError("請啟用至少一個背景記帳 Combo。")
        for combo in combos:
            validate_combo(combo)
            if not self.capture.load_template(combo.image_path):
                raise ValueError(f"{combo.name}：請設定不含數字的定位圖片。")
        capture_region(profile)
        self.ledger.load()
        # Freeze identity/settings so a cancelled OCR cannot follow later UI edits.
        profile = deepcopy(profile)
        combos = [c for c in profile.combos if c.enabled and c.detection_mode == "background"]
        self.active_ids = {c.id for c in combos}
        self.stop_event.clear()
        with self.reset_lock:
            self.reset_ids.clear()
        self.thread = threading.Thread(target=self._loop, args=(profile, combos), daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def reset(self, combo_id):
        if not self.running or combo_id not in self.active_ids:
            raise ValueError("此 Combo 未在背景偵測中，無法重設基準。")
        with self.reset_lock:
            if self.stop_event.is_set():
                return None
            self.reset_ids.add(combo_id)

    def accept(self, profile, combo, value, text, confidence, now):
        # Hold the reset lock until the write succeeds so a new reset is never lost.
        with self.reset_lock:
            if self.stop_event.is_set():
                return None
            reset = combo.id in self.reset_ids
            row = self.ledger.record(profile, combo, value, text, confidence, now, reset)
            if reset:
                self.reset_ids.remove(combo.id)
        return row

    def _loop(self, profile, combos):
        pending, warned = {}, set()
        try:
            while not self.stop_event.is_set():
                region = capture_region(profile)
                if profile.window_title and region is None:
                    pending.clear()
                    self._notify("background_status", "目標視窗不可用，背景暫停讀取")
                    self.stop_event.wait(1)
                    continue
                frame = self.capture.grab_frame(region)
                captured_at = datetime.now()
                if frame is None:
                    pending.clear()
                    self._notify("background_status", "截圖失敗，等待重試")
                else:
                    self._notify("background_status", "背景偵測中")
                    for combo in combos:
                        if self.stop_event.is_set():
                            break
                        value, text, confidence, _roi = self.capture.read_balance(combo, frame, region)
                        now = captured_at
                        if self.stop_event.is_set():
                            break
                        if profile.window_title and get_window_region(profile.window_handle, profile.window_pid) != region:
                            pending.clear()
                            break
                        found, score, icon_rect, number_rect = self.capture.get_balance_debug()
                        self._notify("background_debug", combo.id, found, score, icon_rect,
                                     value is not None, confidence, number_rect)
                        if value is None:
                            try:
                                value = parse_balance(text)
                            except ValueError:
                                self._notify("background_read", combo.id, f"未取得有效數字：{text}")
                                continue
                        previous, confirmations = pending.get(combo.id, (None, 0))
                        confirmations = confirmations + 1 if previous == value else 1
                        pending[combo.id] = (value, confirmations)
                        required = (2 if confidence >= combo.text_threshold
                                    else combo.low_confidence_confirmations)
                        if confirmations < required:
                            self._notify("background_read", combo.id,
                                         f"讀到 {value}，等待再次確認 {confirmations}/{required}")
                            continue
                        row = self.accept(profile, combo, value, text, confidence, now)
                        if row:
                            self._notify("background_record", combo.id, f"餘額 {value}，變動 {int(row['delta']):+d}")
                        income = self.ledger.snapshot(profile.id, combo.id, now.strftime("%Y%m%d"))[0]
                        level = "已達上限" if income >= combo.daily_limit else "接近上限" if income >= combo.daily_limit * combo.warning_ratio else None
                        self._notify("background_read", combo.id,
                                    f"餘額 {value}；今日收入 {income}/{combo.daily_limit}"
                                    + (f"；{level}" if level else ""))
                        key = (combo.id, now.strftime("%Y%m%d"), level)
                        if level and key not in warned:
                            # Prior saved rows reveal whether this threshold was already crossed.
                            prior = income - max(0, int(row["delta"])) if row else income
                            threshold = combo.daily_limit if level == "已達上限" else combo.daily_limit * combo.warning_ratio
                            if prior < threshold:
                                self._notify("background_record", combo.id, f"{level}：{income}/{combo.daily_limit}")
                            warned.add(key)
                self.stop_event.wait(1)
        except Exception as error:
            self._notify("background_error", str(error))
        finally:
            self.stop_event.set()
            self.notify("background_status", "背景偵測已停止")

    def _notify(self, *message):
        if not self.stop_event.is_set():
            self.notify(*message)
