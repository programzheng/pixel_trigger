"""GUI 介面 — Combo 管理 + Hook 引擎控制"""

from __future__ import annotations

import os
import collections
import logging
import sys
from logging.handlers import TimedRotatingFileHandler
import time
import warnings
import threading
import keyboard
import tkinter as tk
from datetime import datetime, timedelta

warnings.filterwarnings("ignore", message=".*pin_memory.*")
from tkinter import filedialog, messagebox, simpledialog

from pathlib import Path

import ttkbootstrap as ttk
import numpy as np
from PIL import Image, ImageGrab, ImageTk

from app_settings import load_app_settings, save_app_settings
import profile_manager
from window_target import capture_region, list_windows
from automation import HookEngine
from calc import BackgroundCalc, accounting_day, validate_combo
from screen_capture import ScreenCapture
from screen_capture import find_color_marker, color_band_state, ocr_from_screen
from models import (
    Action,
    ActionType,
    Combo,
    MouseButton,
    Profile,
    PositionControl,
    TextMatchMode,
    TriggerMode,
)

# ── 顯示用對照表 ─────────────────────────────────────────

TRIGGER_LABELS = {
    TriggerMode.ON_APPEAR: "出現時觸發一次",
    TriggerMode.WHILE_VISIBLE: "可見期間持續執行",
    TriggerMode.POSITION_CONTROL: "位置控制",
    TriggerMode.COLOR_BAND: "色帶判斷（實驗）",
}

ACTION_LABELS = {
    ActionType.CLICK: "滑鼠 單擊",
    ActionType.CLICK_IMAGE: "滑鼠 點擊圖片",
    ActionType.RAPID_CLICK: "滑鼠 連續點擊",
    ActionType.HOLD_MOUSE: "滑鼠 長按",
    ActionType.KEY_PRESS: "鍵盤 按一下",
    ActionType.RAPID_KEY: "鍵盤 連續按",
    ActionType.HOLD_KEY: "鍵盤 長按",
    ActionType.PLAY_SOUND: "播放音效",
}

MOUSE_BTN_LABELS = {
    MouseButton.LEFT: "左鍵",
    MouseButton.RIGHT: "右鍵",
    MouseButton.MIDDLE: "中鍵",
}


def _third_party_notices():
    for base in (Path(__file__).resolve().parent, Path(sys.executable).resolve().parent):
        path = base / "THIRD_PARTY_NOTICES.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    raise FileNotFoundError("THIRD_PARTY_NOTICES.txt was not found.")


def _create_log_logger():
    log_dir = Path.cwd() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.Logger("pixel_trigger", level=logging.INFO)
    # 使用本機午夜輪替；backupCount=0 保留歷史檔，不自動刪除。
    handler = TimedRotatingFileHandler(
        log_dir / "pixel_trigger.log", when="midnight", interval=1,
        backupCount=0, encoding="utf-8", delay=True,
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)
    return logger


def _system_theme():
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            light, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "darkly" if light == 0 else "litera"
    except (ImportError, OSError):
        return "litera"


def _initial_window_geometry(
        screen_width: int, screen_height: int) -> tuple[int, int, int, int]:
    """Return a centered initial size that fits the current display."""
    max_width = max(1, int(screen_width * 0.94))
    max_height = max(1, int(screen_height * 0.86))
    width = min(1200, max_width, max(900, int(screen_width * 0.75)))
    height = min(900, max_height, max(700, int(screen_height * 0.82)))
    x = max(0, (screen_width - width) // 2)
    y = max(0, (screen_height - height) // 2)
    return width, height, x, y


def _saved_window_geometry(
        settings: dict, screen_width: int, screen_height: int
        ) -> tuple[int, int, int, int, bool]:
    fallback = _initial_window_geometry(screen_width, screen_height)
    window = settings.get("window") if isinstance(settings, dict) else None
    if not isinstance(window, dict):
        return (*fallback, False)

    values = [window.get(key) for key in ("width", "height", "x", "y")]
    if any(type(value) is not int for value in values):
        return (*fallback, False)

    max_width = max(1, int(screen_width * 0.94))
    max_height = max(1, int(screen_height * 0.86))
    width = min(max_width, max(min(700, max_width), values[0]))
    height = min(max_height, max(min(500, max_height), values[1]))
    x, y = values[2:]
    if x + 80 < 0 or x > screen_width - 80 or y + 40 < 0 or y > screen_height - 40:
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
    else:
        x = min(max(0, x), screen_width - width)
        y = min(max(0, y), screen_height - height)
    return width, height, x, y, window.get("maximized") is True


class App:
    def __init__(self):
        self.root = ttk.Window(themename=_system_theme())
        self._theme_manual = False
        self.root.title("PixelTrigger")
        width, height, x, y, maximized = _saved_window_geometry(
            load_app_settings(), self.root.winfo_screenwidth(),
            self.root.winfo_screenheight())
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self._normal_geometry = (width, height, x, y)
        self._window_save_job = None
        self.root.resizable(True, True)
        self.root.attributes("-topmost", True)
        self.root.bind("<Configure>", self._remember_window_geometry, add="+")
        if maximized:
            self.root.after_idle(lambda: self.root.state("zoomed"))

        self.profile: Profile = Profile()
        self._file_logger = _create_log_logger()
        self.root.bind("<Destroy>", self._close_log_files, add="+")
        self.engine = HookEngine(
            on_combo_triggered=self._on_combo_triggered,
            on_cycle_complete=self._on_cycle_complete,
            on_status_change=self._on_status_change,
            on_debug_info=self._on_debug_info,
            on_log=self._on_log,
        )

        # combo 活躍狀態 (用於 GUI 指示燈)
        self._combo_active: dict[str, bool] = {}
        self._background_results: dict[str, str] = {}
        # debug 模式：顯示即時信心度 + 螢幕框線
        self._debug_mode = tk.BooleanVar(value=False)
        self._background_debug_mode = tk.BooleanVar(value=False)
        self._background_debug_data = {}
        # combo_id → (found, confidence, match_rect|None) 最新偵測結果
        self._debug_data: dict[str, tuple] = {}
        self._combo_logs = {}
        self._log_window = None
        # debug 覆蓋層視窗
        self._debug_overlay: "DebugOverlay | None" = None

        # 記住目前 profile 的檔案路徑，儲存時寫回同一檔案
        self._profile_path: Path | None = None

        # ── 跨執行緒通訊用 Queue（worker thread 不碰任何 tkinter）──
        self._msg_queue: collections.deque = collections.deque()
        self.background = BackgroundCalc(lambda *msg: self._msg_queue.append(msg))
        self._hotkeys = []
        self._hotkeys_enabled = False
        self._calc_window = None
        self._closing = False

        self._build_ui()
        self._load_initial_profile()
        # 啟動 main thread 的 queue 輪詢
        self._poll_queue()
        self._follow_system_theme()

    # ══════════════════════════════════════════════════════
    #  UI 建構
    # ══════════════════════════════════════════════════════

    def _build_ui(self):
        # Reserve the controls first so a shorter window keeps both toggles visible.
        frm_ctrl = ttk.Frame(self.root, padding=(10, 10))
        frm_ctrl.pack(side="bottom", fill="x")
        # --- Profile 列 ---
        frm_profile = ttk.Frame(self.root, padding=(10, 5))
        frm_profile.pack(fill="x")

        ttk.Label(frm_profile, text="Profile:").pack(side="left")
        self.cmb_profile = ttk.Combobox(frm_profile, state="readonly", width=20)
        self.cmb_profile.pack(side="left", padx=5)
        self.cmb_profile.bind("<<ComboboxSelected>>", self._on_profile_selected)

        ttk.Button(frm_profile, text="儲存", width=5, command=self._save_profile).pack(side="left", padx=2)
        ttk.Button(frm_profile, text="另存", width=5, command=self._save_profile_as).pack(side="left", padx=2)
        ttk.Button(frm_profile, text="新增", width=5, command=self._new_profile).pack(side="left", padx=2)
        self.btn_theme = ttk.Button(
            frm_profile,
            text="切換亮色" if self.root.style.theme.name == "darkly" else "切換深色",
            bootstyle="primary",
            command=self._toggle_theme,
        )
        self.btn_theme.pack(side="right", padx=2)

        # --- Combo 列表 ---
        frm_combos = ttk.Labelframe(self.root, text="Combo 列表", padding=(10, 5))
        frm_combos.pack(fill="both", expand=True, padx=10, pady=5)

        # Treeview
        cols = ("enabled", "name", "trigger", "action", "status", "scale")
        self.tree = ttk.Treeview(frm_combos, columns=cols, show="headings", height=8)
        self.tree.heading("enabled", text="啟用")
        self.tree.heading("name", text="名稱")
        self.tree.heading("trigger", text="偵測／觸發模式")
        self.tree.heading("action", text="動作")
        self.tree.heading("status", text="偵測結果")
        self.tree.heading("scale", text="匹配比例")
        self.tree.column("enabled", width=60, anchor="center", stretch=False)
        self.tree.column("name", width=180)
        self.tree.column("trigger", width=140)
        self.tree.column("action", width=150)
        self.tree.column("status", width=220, minwidth=100, anchor="w")
        self.tree.column("scale", width=100, anchor="center", stretch=False)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        # Combo 按鈕列
        frm_combo_btns = ttk.Frame(frm_combos)
        frm_combo_btns.pack(fill="x", pady=5)
        ttk.Button(frm_combo_btns, text="新增 Combo", bootstyle="success", command=self._add_combo).pack(side="left", padx=2)
        ttk.Button(frm_combo_btns, text="編輯", command=self._edit_combo).pack(side="left", padx=2)
        ttk.Button(frm_combo_btns, text="刪除", bootstyle="danger-outline", command=self._delete_combo).pack(side="left", padx=2)
        ttk.Button(frm_combo_btns, text="啟用/停用", command=self._toggle_combo_enabled).pack(side="left", padx=2)
        ttk.Button(frm_combo_btns, text="▲ 上移", command=self._move_combo_up).pack(side="left", padx=2)
        ttk.Button(frm_combo_btns, text="▼ 下移", command=self._move_combo_down).pack(side="left", padx=2)
        ttk.Button(frm_combo_btns, text="Log", command=self._show_combo_log).pack(side="left", padx=2)
        ttk.Button(frm_combo_btns, text="全部 Log", command=self._show_log).pack(side="left", padx=2)

        # --- 偵測區域 ---
        frm_region = ttk.Labelframe(self.root, text="偵測區域 (留空=全螢幕)", padding=(10, 5))
        frm_region.pack(fill="x", padx=10, pady=5)

        window_row = ttk.Frame(frm_region)
        window_row.pack(fill="x", pady=(0, 5))
        ttk.Label(window_row, text="目標視窗:").pack(side="left")
        self.cmb_window = ttk.Combobox(window_row, state="readonly", width=55)
        self.cmb_window.pack(side="left", padx=5)
        self.cmb_window.bind("<<ComboboxSelected>>", self._on_window_selected)
        ttk.Button(window_row, text="重新整理", command=self._refresh_windows).pack(side="left")
        ttk.Label(frm_region, text="選視窗後忽略 XY 並自動跟隨；需保持可見。最小化／關閉：主動停止，背景暫停讀取。",
                 bootstyle="default").pack(anchor="w")
        grid = ttk.Frame(frm_region)
        grid.pack(fill="x")
        labels = ["X:", "Y:", "寬:", "高:"]
        self.region_vars: list[tk.StringVar] = []
        self.region_controls: list[tk.Widget] = []
        for i, lbl in enumerate(labels):
            ttk.Label(grid, text=lbl).grid(row=0, column=i * 2, sticky="e")
            var = tk.StringVar()
            entry = ttk.Entry(grid, textvariable=var, width=7)
            entry.grid(row=0, column=i * 2 + 1, padx=2)
            self.region_controls.append(entry)
            self.region_vars.append(var)
        select_button = ttk.Button(
            grid, text="框選", command=self._select_region,
        )
        select_button.grid(row=0, column=8, padx=(8, 0))
        self.region_controls.append(select_button)
        self._refresh_windows()

        # --- 狀態列 ---
        frm_status = ttk.Frame(self.root, padding=(10, 5))
        frm_status.pack(fill="x")

        self.lbl_cycles = ttk.Label(frm_status, text="主動掃描: 0 次", bootstyle="default")
        self.lbl_cycles.pack(side="right")

        # --- 啟動/停止 ---

        self.btn_toggle = ttk.Button(
            frm_ctrl, text="啟動主動偵測", bootstyle="success",
            padding=(12, 14), command=self.toggle,
        )
        self.btn_toggle.pack(fill="x")

        self.lbl_hotkey = ttk.Label(frm_ctrl, text="", bootstyle="default")
        self.lbl_hotkey.pack(side="bottom", pady=(5, 0))
        self.btn_background = ttk.Button(
            frm_ctrl, text="啟動背景偵測", bootstyle="success",
            padding=(12, 14), command=self._toggle_background,
        )
        self.btn_background.pack(fill="x", pady=4)
        frm_debug = ttk.Frame(frm_ctrl)
        frm_debug.pack(fill="x", pady=4)
        ttk.Checkbutton(
            frm_debug, text="主動 Debug 框線", variable=self._debug_mode,
            command=self._on_debug_mode_toggle,
        ).pack(side="left", padx=10)
        ttk.Checkbutton(
            frm_debug, text="背景 Debug 框線", variable=self._background_debug_mode,
            command=self._on_debug_mode_toggle,
        ).pack(side="left", padx=10)
        ttk.Button(frm_ctrl, text="記帳統計", command=self._show_calc).pack(side="left")
        ttk.Button(frm_ctrl, text="設定快捷鍵", command=self._configure_hotkeys).pack(side="left", padx=5)
        ttk.Button(frm_ctrl, text="關於／第三方授權", command=self._show_about).pack(side="right")
        self._refresh_hotkeys()

    # ══════════════════════════════════════════════════════
    #  Profile 操作
    # ══════════════════════════════════════════════════════

    def _toggle_theme(self):
        self._theme_manual = True
        dark = self.root.style.theme.name == "litera"
        self.root.style.theme_use("darkly" if dark else "litera")
        self.btn_theme.config(text="切換亮色" if dark else "切換深色")

    def _show_about(self):
        try:
            notices = _third_party_notices()
        except OSError as error:
            messagebox.showerror("第三方授權", str(error), parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title("Pixel Trigger 0.2.0 — 第三方授權")
        win.geometry("760x560")
        text = tk.Text(win, wrap="word")
        scroll = ttk.Scrollbar(win, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True, padx=(10, 0), pady=10)
        text.insert("1.0", notices)
        text.configure(state="disabled")

    def _follow_system_theme(self):
        if self._theme_manual or self._closing:
            return
        theme = _system_theme()
        if self.root.style.theme.name != theme:
            self.root.style.theme_use(theme)
            self.btn_theme.config(text="切換亮色" if theme == "darkly" else "切換深色")
        self.root.after(2000, self._follow_system_theme)

    def _stop_all(self):
        if self.engine.running:
            self._stop()
        if hasattr(self, "background"):
            self.background.stop()
            self.btn_background.config(text="啟動背景偵測", bootstyle="success")
        if self._calc_window is not None and self._calc_window.winfo_exists():
            self._calc_window.destroy()
        self._msg_queue = collections.deque(
            m for m in self._msg_queue
            if not m[0].startswith("background") and m[0] not in ("status", "cycle", "triggered"))
        self._background_debug_data.clear()
        self._on_debug_mode_toggle()

    def _unregister_hotkeys(self):
        for handle in self._hotkeys:
            keyboard.remove_hotkey(handle)
        self._hotkeys.clear()

    def _register_hotkeys(self):
        self._hotkeys_enabled = True
        self._unregister_hotkeys()
        try:
            active = tuple(frozenset(step) for step in keyboard.parse_hotkey(self.profile.hotkey))
            background = tuple(frozenset(step) for step in keyboard.parse_hotkey(self.profile.background_hotkey))
            if active == background:
                raise ValueError("主動與背景快捷鍵不可相同。")
            for key, event in ((self.profile.hotkey, "hotkey"),
                               (self.profile.background_hotkey, "background_hotkey")):
                self._hotkeys.append(keyboard.add_hotkey(
                    key, lambda event=event: self._msg_queue.append((event,))))
        except (ValueError, KeyError) as error:
            self._unregister_hotkeys()
            messagebox.showerror("快捷鍵設定錯誤", str(error))

    def _refresh_hotkeys(self):
        self.lbl_hotkey.config(text=f"主動: {self.profile.hotkey}　背景: {self.profile.background_hotkey}")
        if self._hotkeys_enabled:
            self._register_hotkeys()

    def _configure_hotkeys(self):
        profile = self.profile
        app = self

        class HotkeyDialog(simpledialog.Dialog):
            def body(self, parent):
                self.entries = []
                for row, (label, value) in enumerate((
                        ("主動偵測快捷鍵:", profile.hotkey),
                        ("背景偵測快捷鍵:", profile.background_hotkey))):
                    ttk.Label(parent, text=label).grid(row=row, column=0, padx=8, pady=6)
                    entry = ttk.Entry(parent, width=24)
                    entry.insert(0, value)
                    entry.grid(row=row, column=1, padx=8, pady=6)
                    self.entries.append(entry)
                ttk.Label(parent, text="例如 F6、F7、ctrl+shift+b").grid(row=2, column=0, columnspan=2, pady=5)
                return self.entries[0]

            def apply(self):
                self.result = tuple(entry.get().strip() for entry in self.entries)

            def validate(self):
                active, background = (entry.get().strip() for entry in self.entries)
                try:
                    if not active or not background:
                        raise ValueError("快捷鍵不可留空。")
                    if (tuple(frozenset(step) for step in keyboard.parse_hotkey(active))
                            == tuple(frozenset(step) for step in keyboard.parse_hotkey(background))):
                        raise ValueError("兩個快捷鍵不可相同。")
                    old = profile.hotkey, profile.background_hotkey
                    profile.hotkey, profile.background_hotkey = active, background
                    try:
                        profile_manager.save_profile(profile, app._profile_path.name if app._profile_path else None)
                    except Exception:
                        profile.hotkey, profile.background_hotkey = old
                        raise
                    return True
                except Exception as error:
                    messagebox.showerror("快捷鍵設定錯誤", str(error), parent=self)
                    return False

        result = HotkeyDialog(self.root, "設定主動／背景快捷鍵").result
        if result is None:
            return
        self._refresh_hotkeys()

    def _toggle_background(self):
        if self.background.running:
            self.background.stop()
            self.btn_background.config(text="啟動背景偵測", bootstyle="success")
            self._background_debug_data.clear()
            self._on_debug_mode_toggle()
            return
        try:
            self._sync_region_from_ui()
            # Persist identity before the first CSV record, including legacy profiles.
            profile_manager.save_profile(self.profile, self._profile_path.name if self._profile_path else None)
            self.background.start(self.profile)
            self.btn_background.config(text="停止背景偵測", bootstyle="danger")
            self._on_debug_mode_toggle()
        except Exception as error:
            messagebox.showerror("背景偵測設定錯誤", str(error))

    def _show_calc(self):
        if self._calc_window is not None and self._calc_window.winfo_exists():
            self._calc_window.lift()
            return
        try:
            if not self.background.busy:
                self.background.ledger.load(self.profile)
        except Exception as error:
            messagebox.showerror("CSV 載入失敗", str(error))
            return
        win = self._calc_window = tk.Toplevel(self.root)
        win.title("金額記帳統計")
        win.geometry("820x540")
        row = ttk.Frame(win)
        row.pack(fill="x", padx=10, pady=10)
        self._calc_combos = [c for c in self.profile.combos if c.detection_mode == "background"]
        self._calc_choice = ttk.Combobox(row, state="readonly", values=[f"{c.name} [{c.id}]" for c in self._calc_combos])
        self._calc_choice.pack(side="left")
        if self._calc_combos:
            self._calc_choice.current(0)
        ttk.Label(row, text="日期 YYYYMMDD:").pack(side="left", padx=5)
        reset_time = self._calc_combos[0].daily_reset_time if self._calc_combos else "00:00"
        self._calc_date = tk.StringVar(value=accounting_day(datetime.now(), reset_time))
        self._calc_choice.bind("<<ComboboxSelected>>", lambda _event: self._calc_date.set(
            accounting_day(datetime.now(), self._calc_combos[self._calc_choice.current()].daily_reset_time)))
        ttk.Entry(row, textvariable=self._calc_date, width=10).pack(side="left")
        self._calc_period = tk.StringVar(value="Daily")
        ttk.OptionMenu(row, self._calc_period, "Daily", "Daily", "Month", "Years").pack(side="left")
        self._calc_reset = ttk.Button(row, text="重設基準", command=self._reset_calc)
        self._calc_reset.pack(side="left", padx=5)
        ttk.Button(row, text="在選取位置新增", command=self._add_calc_record).pack(side="left")
        ttk.Button(row, text="編輯選取紀錄", command=self._edit_calc_record).pack(side="left")
        ttk.Button(row, text="刪除選取紀錄", command=self._delete_calc_record).pack(side="left", padx=5)
        self._calc_summary = ttk.Label(win, justify="left", anchor="w")
        self._calc_summary.pack(fill="x", padx=10, pady=5)
        columns = ("timestamp", "record_type", "balance", "delta")
        self._calc_tree = ttk.Treeview(win, columns=columns, show="headings")
        for column, label in zip(columns, ("時間", "類型", "身上金額", "變動")):
            self._calc_tree.heading(column, text=label)
            self._calc_tree.column(column, width=160)
        scrollbar = ttk.Scrollbar(win, command=self._calc_tree.yview)
        self._calc_tree.configure(yscrollcommand=scrollbar.set)
        self._calc_tree.bind("<Double-1>", lambda _event: self._edit_calc_record())
        scrollbar.pack(side="right", fill="y")
        self._calc_tree.pack(fill="both", expand=True, padx=10, pady=5)
        self._calc_view_key = None
        self._update_calc_view()

    def _update_calc_view(self):
        if self._calc_window is None or not self._calc_window.winfo_exists():
            return
        index = self._calc_choice.current()
        if not 0 <= index < len(self._calc_combos):
            self._calc_reset.config(state="disabled")
            self._calc_summary.config(text="尚未設定背景記帳 Combo。")
            return
        combo = self._calc_combos[index]
        self._calc_reset.config(state="normal" if combo.enabled and self.background.running
                                and combo.id in self.background.active_ids else "disabled")
        date = self._calc_date.get()
        try:
            datetime.strptime(date, "%Y%m%d")
            if len(date) != 8:
                raise ValueError()
        except ValueError:
            self._calc_summary.config(text="請輸入有效日期 YYYYMMDD。")
            self._calc_tree.delete(*self._calc_tree.get_children())
            self._calc_view_key = None
            return
        ledger = self.background.ledger
        period = self._calc_period.get()
        prefix = date[:{"Daily": 8, "Month": 6, "Years": 4}[period]]
        with ledger.lock:
            records = [(index, dict(record)) for index, record in enumerate(ledger.rows)
                       if record["profile_id"] == self.profile.id
                       and record["combo_id"] == combo.id
                       and record["day"].startswith(prefix)]
        daily = ledger.snapshot(self.profile.id, combo.id, date[:8])[:3]
        monthly = ledger.snapshot(self.profile.id, combo.id, date[:6])[:3]
        yearly = ledger.snapshot(self.profile.id, combo.id, date[:4])[:3]
        today = ledger.snapshot(
            self.profile.id, combo.id, accounting_day(datetime.now(), combo.daily_reset_time))[0]
        state = "已達上限" if today >= combo.daily_limit else "接近上限" if today >= combo.daily_limit * combo.warning_ratio else "額度正常"
        self._calc_summary.config(text=(
            f"Daily 收入 {daily[0]}　支出 {daily[1]}　淨利 {daily[2]}\n"
            f"Month 收入 {monthly[0]}　支出 {monthly[1]}　淨利 {monthly[2]}\n"
            f"Years 收入 {yearly[0]}　支出 {yearly[1]}　淨利 {yearly[2]}\n"
            f"今日正收入：{today}/{combo.daily_limit}（{today / combo.daily_limit:.1%}）　"
            f"{'剩餘 ' + str(combo.daily_limit - today) if today <= combo.daily_limit else '超額 ' + str(today - combo.daily_limit)}　{state}\n"
            f"CSV：{ledger.directory}"))
        key = (combo.id, prefix, tuple((index, record["balance"], record["delta"])
                                      for index, record in records))
        if key != self._calc_view_key:
            self._calc_tree.delete(*self._calc_tree.get_children())
            for index, record in records:
                self._calc_tree.insert("", "end", iid=str(index), values=(
                    record["timestamp"],
                    {"baseline": "建立基準", "change": "金額變動", "reset": "重設基準"}[record["record_type"]],
                    record["balance"], record["delta"],
                ))
            self._calc_view_key = key

    def _add_calc_record(self):
        index = self._calc_choice.current()
        if not 0 <= index < len(self._calc_combos):
            return
        selected = self._calc_tree.selection()
        if not selected:
            messagebox.showinfo("提示", "請先選取插入位置的參考紀錄。",
                                parent=self._calc_window)
            return
        before = messagebox.askyesnocancel(
            "插入位置", "按「是」插入選取紀錄之前；按「否」插入之後。",
            parent=self._calc_window)
        if before is None:
            return
        items = list(self._calc_tree.get_children())
        selected_at = items.index(selected[0])
        neighbor_at = selected_at - 1 if before else selected_at + 1
        selected_time = datetime.strptime(
            self._calc_tree.set(selected[0], "timestamp"), "%Y%m%d%H%M%S")
        if 0 <= neighbor_at < len(items):
            neighbor_time = datetime.strptime(
                self._calc_tree.set(items[neighbor_at], "timestamp"), "%Y%m%d%H%M%S")
            initial_time = selected_time + (neighbor_time - selected_time) / 2
        else:
            initial_time = selected_time + timedelta(seconds=-1 if before else 1)
        timestamp = simpledialog.askstring(
            "新增計算紀錄", "時間 YYYYMMDDHHMMSS：",
            initialvalue=initial_time.strftime("%Y%m%d%H%M%S"), parent=self._calc_window)
        if timestamp is None:
            return
        try:
            when = datetime.strptime(timestamp, "%Y%m%d%H%M%S")
            if len(timestamp) != 14 or when.strftime("%Y%m%d%H%M%S") != timestamp:
                raise ValueError()
        except ValueError:
            messagebox.showerror("新增失敗", "時間必須是 YYYYMMDDHHMMSS。",
                                 parent=self._calc_window)
            return
        balance = simpledialog.askinteger(
            "新增計算紀錄", "輸入當時的身上金額：", minvalue=0,
            parent=self._calc_window)
        if balance is None:
            return
        try:
            self.background.ledger.add_record(
                self.profile, self._calc_combos[index], when, balance)
        except (OSError, ValueError) as error:
            messagebox.showerror("新增失敗", str(error), parent=self._calc_window)
            return
        self._calc_view_key = None
        self._update_calc_view()

    def _edit_calc_record(self):
        selected = self._calc_tree.selection()
        if not selected:
            messagebox.showinfo("提示", "請先選取一筆計算紀錄。", parent=self._calc_window)
            return
        current = int(self._calc_tree.set(selected[0], "balance"))
        balance = simpledialog.askinteger(
            "編輯身上金額", "輸入正確的身上金額：", initialvalue=current,
            minvalue=0, parent=self._calc_window)
        if balance is None or balance == current:
            return
        try:
            self.background.ledger.edit_balance(int(selected[0]), balance)
        except (OSError, ValueError) as error:
            messagebox.showerror("編輯失敗", str(error), parent=self._calc_window)
            return
        self._calc_view_key = None
        self._update_calc_view()

    def _delete_calc_record(self):
        selected = self._calc_tree.selection()
        if not selected:
            messagebox.showinfo("提示", "請先選取一筆計算紀錄。", parent=self._calc_window)
            return
        values = self._calc_tree.item(selected[0], "values")
        if not messagebox.askyesno(
                "刪除計算紀錄", f"確定刪除 {values[0]}、身上金額 {values[2]} 的紀錄？\n後續變動將重新計算。",
                parent=self._calc_window):
            return
        try:
            self.background.ledger.delete_record(int(selected[0]))
        except (OSError, ValueError) as error:
            messagebox.showerror("刪除失敗", str(error), parent=self._calc_window)
            return
        self._calc_view_key = None
        self._update_calc_view()

    def _reset_calc(self):
        index = self._calc_choice.current()
        if not 0 <= index < len(self._calc_combos):
            return
        if not self.background.running:
            messagebox.showinfo("提示", "請先啟動背景偵測，再重設基準。")
            return
        combo = self._calc_combos[index]
        if not combo.enabled or combo.id not in self.background.active_ids:
            messagebox.showinfo("提示", "此 Combo 未啟用背景偵測，無法重設基準。")
            return
        if messagebox.askyesno("重設基準", f"{combo.name} 下一個穩定讀值將作為新基準，既有收入不清除。"):
            self.background.reset(combo.id)

    def _load_initial_profile(self):
        profile_manager.init_default_profiles()
        self._refresh_profile_list()
        paths = profile_manager.list_profiles()
        if paths:
            self._load_profile_from_path(paths[0])

    def _refresh_profile_list(self):
        paths = profile_manager.list_profiles()
        names = [p.stem for p in paths]
        self.cmb_profile["values"] = names
        if names:
            self.cmb_profile.current(0)

    def _on_profile_selected(self, event=None):
        idx = self.cmb_profile.current()
        paths = profile_manager.list_profiles()
        if 0 <= idx < len(paths):
            self._load_profile_from_path(paths[idx])

    def _load_profile_from_path(self, path: Path):
        self._stop_all()
        try:
            self.profile = profile_manager.load_profile(path)
            self._profile_path = path
            self._background_results.clear()
        except Exception as e:
            messagebox.showerror("錯誤", f"載入 profile 失敗:\n{e}")
            return
        self._sync_region_to_ui()
        self._refresh_combo_tree()
        self._refresh_hotkeys()

    def _save_profile(self):
        self._stop_all()
        try:
            self._sync_region_from_ui()
            filename = self._profile_path.name if self._profile_path else None
            profile_manager.save_profile(self.profile, filename)
        except Exception as e:
            messagebox.showerror("錯誤", str(e))

    def _auto_save(self):
        """自動儲存目前 profile（combo 變更後呼叫）"""
        try:
            filename = self._profile_path.name if self._profile_path else None
            profile_manager.save_profile(self.profile, filename)
        except Exception:
            pass

    def _save_profile_as(self):
        self._stop_all()
        try:
            self._sync_region_from_ui()
        except ValueError as error:
            messagebox.showerror("偵測範圍設定錯誤", str(error))
            return
        name = simpledialog.askstring("另存 Profile", "輸入新名稱:", parent=self.root)
        if not name:
            return
        self.profile.name = name
        self._profile_path = profile_manager.save_profile(self.profile)
        self._refresh_hotkeys()
        self._refresh_profile_list()
        paths = profile_manager.list_profiles()
        for i, p in enumerate(paths):
            if p.stem == name.replace(" ", "_"):
                self.cmb_profile.current(i)
                break

    def _new_profile(self):
        self._stop_all()
        name = simpledialog.askstring("新增 Profile", "輸入名稱:", parent=self.root)
        if not name:
            return
        self.profile = Profile(name=name)
        self._background_results.clear()
        self._refresh_hotkeys()
        self._sync_region_to_ui()
        self._refresh_combo_tree()
        self._profile_path = profile_manager.save_profile(self.profile)
        self._refresh_profile_list()
        paths = profile_manager.list_profiles()
        for i, p in enumerate(paths):
            if p == self._profile_path:
                self.cmb_profile.current(i)
                break

    # ══════════════════════════════════════════════════════
    #  偵測區域
    # ══════════════════════════════════════════════════════

    def _sync_region_to_ui(self):
        self._refresh_windows()
        if self.profile.scan_region:
            for i, val in enumerate(self.profile.scan_region):
                self.region_vars[i].set(str(val))
        else:
            for var in self.region_vars:
                var.set("")

    def _refresh_windows(self):
        self._windows = list_windows()
        values = ["手動 XY／全螢幕"] + [
            f"{title} [PID {pid}, HWND {hwnd}]" for hwnd, title, pid in self._windows]
        selected = 0
        if self.profile.window_title:
            matches = [i + 1 for i, (hwnd, title, pid) in enumerate(self._windows)
                       if (hwnd == self.profile.window_handle and pid == self.profile.window_pid)
                       or (not self.profile.window_handle and title == self.profile.window_title)]
            if len(matches) == 1:
                selected = matches[0]
            else:
                values.append(f"請重新選擇：{self.profile.window_title}")
                selected = len(values) - 1
        self.cmb_window["values"] = values
        self.cmb_window.current(selected)
        self._update_region_controls()

    def _update_region_controls(self):
        state = "disabled" if self.profile.window_title else "normal"
        for control in self.region_controls:
            control.config(state=state)

    def _on_window_selected(self, event=None):
        self._stop_all()
        index = self.cmb_window.current()
        if index == 0:
            self.profile.window_title = ""
            self.profile.window_handle = self.profile.window_pid = 0
        elif 1 <= index <= len(self._windows):
            hwnd, title, pid = self._windows[index - 1]
            self.profile.window_title = title
            self.profile.window_handle, self.profile.window_pid = hwnd, pid
        self._update_region_controls()

    def _select_region(self):
        """隱藏主視窗 → 全螢幕框選 → 填入座標"""
        self.cmb_window.current(0)
        self._on_window_selected()
        self.root.withdraw()
        self.root.update_idletasks()
        # 短暫 delay 讓主視窗完全消失
        self.root.after(200, self._open_region_selector)

    def _open_region_selector(self):
        sel = RegionSelector(self.root)
        self.root.deiconify()
        if sel.result:
            x, y, w, h = sel.result
            self.region_vars[0].set(str(x))
            self.region_vars[1].set(str(y))
            self.region_vars[2].set(str(w))
            self.region_vars[3].set(str(h))

    def _sync_region_from_ui(self):
        if self.profile.window_title:
            return
        values = [var.get().strip() for var in self.region_vars]
        region = None
        if any(values):
            if not all(values):
                raise ValueError("請完整填入 X、Y、寬、高；全部留空才是全螢幕。")
            try:
                region = tuple(int(value) for value in values)
            except ValueError:
                raise ValueError("X、Y、寬、高必須是整數。") from None
            if min(region[2:]) <= 0:
                raise ValueError("寬與高必須大於 0。")
        if region != self.profile.scan_region:
            self._stop_all()
            self.profile.scan_region = region

    # ══════════════════════════════════════════════════════
    #  Combo 列表
    # ══════════════════════════════════════════════════════

    def _refresh_combo_tree(self):
        self.tree.delete(*self.tree.get_children())
        for combo in self.profile.combos:
            enabled_str = "V" if combo.enabled else "-"
            trigger_str = "背景：金額記帳" if combo.detection_mode == "background" else "主動：" + TRIGGER_LABELS.get(combo.trigger, "?")
            action_str = "OCR 金額記錄" if combo.detection_mode == "background" else self._action_summary(combo.action)
            # debug 模式：顯示信心度；否則顯示活躍狀態
            if combo.detection_mode == "background":
                status_str = self._background_results.get(combo.id, "")
            elif self._debug_mode.get() and combo.id in self._debug_data:
                found, conf = self._debug_data[combo.id][:2]
                status_str = f"{conf:.6f} O" if found else f"{conf:.6f} X"
            else:
                active = self._combo_active.get(combo.id, False)
                status_str = ">>>" if active else ""
            self.tree.insert("", "end", iid=combo.id, values=(
                enabled_str, combo.name, trigger_str, action_str, status_str,
                self._match_scale_label(combo)
            ))

    def _match_scale_label(self, combo: Combo) -> str:
        scale = (self.background.capture.get_match_scale(combo.image_path)
                 if combo.detection_mode == "background" else self.engine.get_match_scale(combo.image_path))
        return f"{scale * 100:.1f}%" if scale is not None else "—"

    def _action_summary(self, action: Action) -> str:
        label = ACTION_LABELS.get(action.action_type, "?")
        if action.action_type == ActionType.PLAY_SOUND:
            return label
        if action.action_type in (ActionType.CLICK, ActionType.CLICK_IMAGE, ActionType.RAPID_CLICK, ActionType.HOLD_MOUSE):
            btn = MOUSE_BTN_LABELS.get(action.mouse_button, "?")
            return f"{label} ({btn})"
        else:
            return f"{label} ({action.key or '?'})"

    def _show_combo_log(self):
        combo = self._get_selected_combo()
        if combo:
            self._show_log(combo.id)

    def _show_log(self, combo_id=None):
        if self._log_window is None or not self._log_window.winfo_exists():
            win = tk.Toplevel(self.root)
            self._log_window = win
            win.title("Log")
            win.transient(self.root)
            win.geometry("1000x500")
            self._log_all = tk.BooleanVar(win, value=True)
            self._log_pause = tk.BooleanVar(win, value=False)
            self._log_filter = tk.StringVar(win, value="全部紀錄")
            self._log_previous = None
            self._log_choices = []
            controls = ttk.Frame(win)
            controls.pack(fill="x", padx=8, pady=5)
            ttk.Checkbutton(controls, text="全部 Combo", variable=self._log_all,
                           command=self._update_log_view).pack(side="left")
            filter_box = ttk.Combobox(controls, state="readonly", width=20,
                textvariable=self._log_filter, values=("全部紀錄", "條件失敗與錯誤", "僅錯誤"))
            filter_box.pack(side="left", padx=8)
            filter_box.bind("<<ComboboxSelected>>", lambda e: self._update_log_view())
            ttk.Checkbutton(controls, text="暫停捲動", variable=self._log_pause).pack(side="left")
            ttk.Button(controls, text="複製顯示紀錄", command=self._copy_log).pack(side="right")
            ttk.Button(controls, text="清除全部紀錄", command=self._clear_logs).pack(side="right", padx=5)
            ttk.Label(win, text="左側可多選 Combo；每個 Combo 保留最近 500 筆，依時間合併顯示。",
                     anchor="w").pack(fill="x", padx=8)
            ttk.Label(win, text=f"檔案：{self._file_logger.handlers[0].baseFilename}\n每天午夜輪替；清除僅影響介面紀錄。",
                     anchor="w", justify="left", wraplength=950).pack(fill="x", padx=8)
            body = ttk.Frame(win)
            body.pack(fill="both", expand=True, padx=8, pady=5)
            self._log_selector = tk.Listbox(body, selectmode="multiple", exportselection=False, width=22)
            self._log_selector.pack(side="left", fill="y", padx=(0, 8))
            self._log_selector.bind("<<ListboxSelect>>", self._select_log_combos)
            self._log_text = tk.Text(body, wrap="word", state="disabled")
            scroll = ttk.Scrollbar(body, command=self._log_text.yview)
            self._log_text.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            self._log_text.pack(fill="both", expand=True)
        self._update_log_view()
        self._log_all.set(combo_id is None)
        if combo_id is not None:
            self._log_selector.selection_clear(0, "end")
            for index, (cid, _) in enumerate(self._log_choices):
                if cid == combo_id:
                    self._log_selector.selection_set(index)
                    self._log_selector.see(index)
        self._update_log_view()
        self._log_window.lift()

    def _select_log_combos(self, event=None):
        self._log_all.set(False)
        self._update_log_view()

    def _clear_logs(self):
        self._combo_logs.clear()
        self._update_log_view()

    def _copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self._log_text.get("1.0", "end-1c"))

    def _on_log(self, combo_id, message, level="info"):
        # Worker 只寫入有上限的 buffer；Tk 元件由 main thread 更新。
        name = next((c.name for c in self.profile.combos if c.id == combo_id), combo_id)
        records = self._combo_logs.setdefault(combo_id, collections.deque(maxlen=500))
        records.append((time.monotonic_ns(), datetime.now().strftime("%H:%M:%S.%f")[:-3],
                        name, level, message))
        severity = {"failure": logging.WARNING, "error": logging.ERROR}.get(level, logging.INFO)
        self._file_logger.log(severity, "[%s] [id=%s] %s", name, combo_id, message)

    def _update_log_view(self):
        win = getattr(self, "_log_window", None)
        if win is None or not win.winfo_exists():
            return
        selected = {self._log_choices[i][0] for i in self._log_selector.curselection()}
        choices = {c.id: c.name for c in self.profile.combos}
        snapshots = [(cid, list(records)) for cid, records in list(self._combo_logs.items())]
        for cid, records in snapshots:
            if records:
                choices.setdefault(cid, records[-1][2])
        choices = list(choices.items())
        if choices != self._log_choices:
            self._log_selector.delete(0, "end")
            for index, (cid, name) in enumerate(choices):
                self._log_selector.insert("end", name)
                if cid in selected:
                    self._log_selector.selection_set(index)
            self._log_choices = choices
        mode = self._log_filter.get()
        records = [record for cid, items in snapshots
                   if self._log_all.get() or cid in selected
                   for record in items
                   if mode == "全部紀錄" or record[3] == "error"
                   or (mode == "條件失敗與錯誤" and record[3] == "failure")]
        records.sort(key=lambda record: record[0])
        content = "\n".join(f"{stamp} [{name}] [{level}] {message}"
                            for _, stamp, name, level, message in records)
        if content == self._log_previous:
            return
        text = self._log_text
        position = text.yview()[0]
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("1.0", content)
        text.configure(state="disabled")
        if self._log_pause.get():
            text.yview_moveto(position)
        else:
            text.see("end")
        self._log_previous = content

    def _get_selected_combo(self) -> Combo | None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "請先選擇一個 Combo")
            return None
        combo_id = sel[0]
        for c in self.profile.combos:
            if c.id == combo_id:
                return c
        return None

    def _add_combo(self):
        self._stop_all()
        try:
            self._sync_region_from_ui()
        except ValueError as error:
            messagebox.showerror("偵測範圍設定錯誤", str(error))
            return
        combo = Combo()
        if ComboEditor(self.root, combo, self.profile).result:
            self.profile.combos.append(combo)
            self._refresh_combo_tree()
            self._auto_save()

    def _edit_combo(self):
        self._stop_all()
        try:
            self._sync_region_from_ui()
        except ValueError as error:
            messagebox.showerror("偵測範圍設定錯誤", str(error))
            return
        combo = self._get_selected_combo()
        if combo and ComboEditor(self.root, combo, self.profile).result:
            self._refresh_combo_tree()
            self._auto_save()

    def _on_tree_double_click(self, event):
        self._edit_combo()

    def _delete_combo(self):
        self._stop_all()
        combo = self._get_selected_combo()
        if combo:
            self.profile.combos = [c for c in self.profile.combos if c.id != combo.id]
            self._refresh_combo_tree()
            self._auto_save()

    def _toggle_combo_enabled(self):
        self._stop_all()
        combo = self._get_selected_combo()
        if combo:
            combo.enabled = not combo.enabled
            self._refresh_combo_tree()
            self._auto_save()

    def _move_combo_up(self):
        combo = self._get_selected_combo()
        if combo:
            idx = next((i for i, c in enumerate(self.profile.combos) if c.id == combo.id), None)
            if idx is not None and idx > 0:
                self.profile.combos[idx - 1], self.profile.combos[idx] = self.profile.combos[idx], self.profile.combos[idx - 1]
                self._refresh_combo_tree()
                self.tree.selection_set(combo.id)
                self._auto_save()

    def _move_combo_down(self):
        combo = self._get_selected_combo()
        if combo:
            idx = next((i for i, c in enumerate(self.profile.combos) if c.id == combo.id), None)
            if idx is not None and idx < len(self.profile.combos) - 1:
                self.profile.combos[idx], self.profile.combos[idx + 1] = self.profile.combos[idx + 1], self.profile.combos[idx]
                self._refresh_combo_tree()
                self.tree.selection_set(combo.id)
                self._auto_save()

    # ══════════════════════════════════════════════════════
    #  引擎控制
    # ══════════════════════════════════════════════════════

    def toggle(self):
        if self.engine.running:
            self._stop()
        else:
            self._start()

    def _start(self):
        enabled = [c for c in self.profile.combos if c.enabled and c.detection_mode == "active" and
                   (c.image_path or c.verify_text or c.trigger.tracks_marker)]
        if not enabled:
            messagebox.showwarning("提醒", "請啟用至少一個已設定圖片、文字或位置控制的主動偵測 Combo。")
            return
        try:
            self._sync_region_from_ui()
            self.engine.set_profile(self.profile)
            self.engine.start()
        except ValueError as e:
            messagebox.showerror("設定錯誤", str(e))
            return
        if not self.engine.running:
            return
        self.btn_toggle.config(text="停止主動偵測", bootstyle="danger")
        self._on_debug_mode_toggle()

    def _stop(self):
        self.engine.stop()
        self._combo_active.clear()
        self._debug_data.clear()
        self._on_debug_mode_toggle()
        self.btn_toggle.config(text="啟動主動偵測", bootstyle="success")
        self._refresh_combo_tree()

    # ══════════════════════════════════════════════════════
    #  Debug 覆蓋層管理
    # ══════════════════════════════════════════════════════

    def _on_debug_mode_toggle(self):
        if ((self._debug_mode.get() and self.engine.running)
                or (self._background_debug_mode.get() and self.background.running)):
            self._show_debug_overlay()
            self._update_debug_overlay()
        else:
            self._hide_debug_overlay()

    def _show_debug_overlay(self):
        if self._debug_overlay is None:
            self._debug_overlay = DebugOverlay(self.root)

    def _hide_debug_overlay(self):
        if self._debug_overlay is not None:
            self._debug_overlay.destroy()
            self._debug_overlay = None

    def _update_debug_overlay(self):
        if self._debug_overlay is None:
            return
        rects = []
        for combo in self.profile.combos:
            if not combo.enabled:
                continue
            if (combo.detection_mode == "active" and self.engine.running
                    and self._debug_mode.get() and combo.id in self._debug_data):
                found, conf, rect = self._debug_data[combo.id]
                if rect:
                    x, y, w, h = rect
                    rects.append((x, y, w, h, combo.name, found, conf))
            elif (combo.detection_mode == "background" and self.background.running
                  and self._background_debug_mode.get() and combo.id in self._background_debug_data):
                found, conf, icon_rect, number_found, number_conf, number_rect = self._background_debug_data[combo.id]
                for label, rect, valid, confidence in (
                        ("定位", icon_rect, found, conf),
                        ("數字", number_rect, number_found, number_conf)):
                    if rect:
                        rects.append((*rect, f"{combo.name} {label}", valid, confidence))
        self._debug_overlay.update_rects(rects)

    # ══════════════════════════════════════════════════════
    #  引擎回呼 — worker thread 只往 Queue 塞訊息
    # ══════════════════════════════════════════════════════

    def _on_combo_triggered(self, combo_id: str, active: bool):
        self._msg_queue.append(("triggered", combo_id, active))

    def _on_debug_info(self, combo_id: str, found: bool, confidence: float, match_rect=None):
        # 直接寫 dict（CPython GIL 保證 dict 寫入原子性）
        self._debug_data[combo_id] = (found, confidence, match_rect)

    def _on_cycle_complete(self, count: int):
        self._msg_queue.append(("cycle", count))

    def _on_status_change(self, running: bool):
        self._msg_queue.append(("status", running))

    # ══════════════════════════════════════════════════════
    #  Main thread Queue 輪詢 — 唯一碰 tkinter 的地方
    # ══════════════════════════════════════════════════════

    def _poll_queue(self):
        """每 150ms 從 queue 取出所有訊息，在 main thread 更新 GUI"""
        while self._msg_queue:
            msg = self._msg_queue.popleft()
            kind = msg[0]
            if kind == "triggered":
                _, combo_id, active = msg
                self._combo_active[combo_id] = active
                if not self._debug_mode.get() and self.tree.exists(combo_id):
                    self.tree.set(combo_id, "status", ">>>" if active else "")
            elif kind == "cycle":
                _, count = msg
                self.lbl_cycles.config(text=f"主動掃描: {count} 次")
                if self._debug_mode.get() and self._debug_data:
                    for cid, data in list(self._debug_data.items()):
                        found, conf = data[0], data[1]
                        if self.tree.exists(cid):
                            s = f"{conf:.2f} O" if found else f"{conf:.2f} X"
                            self.tree.set(cid, "status", s)
            elif kind == "status":
                running = self.engine.running
                self.btn_toggle.config(
                    text="停止主動偵測" if running else "啟動主動偵測",
                    bootstyle="danger" if running else "success",
                )
                if not running:
                    self._combo_active.clear()
                    self._debug_data.clear()
                    self._on_debug_mode_toggle()
                    self._refresh_combo_tree()
            elif kind == "background_hotkey":
                self._toggle_background()
            elif kind == "background_status":
                self.btn_background.config(
                    text="停止背景偵測" if self.background.running else "啟動背景偵測",
                    bootstyle="danger" if self.background.running else "success",
                )
                if msg[1] not in ("背景偵測中", "背景偵測已停止"):
                    for combo in self.profile.combos:
                        if combo.enabled and combo.detection_mode == "background":
                            self._background_results[combo.id] = msg[1]
                            if self.tree.exists(combo.id):
                                self.tree.set(combo.id, "status", msg[1])
                            if combo.id in self._background_debug_data:
                                data = self._background_debug_data[combo.id]
                                self._background_debug_data[combo.id] = (False, 0.0, data[2], False, 0.0, data[5])
                if not self.background.running:
                    self._background_debug_data.clear()
            elif kind == "background_debug":
                cid = msg[1]
                previous = self._background_debug_data.get(cid, (False, 0.0, None, False, 0.0, None))
                found, conf, icon_rect, number_found, number_conf, number_rect = msg[2:]
                self._background_debug_data[cid] = (
                    found, conf, icon_rect or previous[2], number_found, number_conf,
                    number_rect or previous[5],
                )
            elif kind in ("background_read", "background_record"):
                _, cid, message = msg
                previous = self._background_results.get(cid)
                self._background_results[cid] = message
                if self.tree.exists(cid):
                    self.tree.set(cid, "status", message)
                if kind == "background_record" or message != previous:
                    level = "failure" if message.startswith("未取得有效數字") else "info"
                    self._on_log(cid, message, level)
            elif kind == "background_error":
                messagebox.showerror("背景記帳已停止", msg[1])
            elif kind == "hotkey":
                self.toggle()
        for combo in self.profile.combos:
            if self.tree.exists(combo.id):
                self.tree.set(combo.id, "scale", self._match_scale_label(combo))
        self._update_log_view()
        self._update_calc_view()
        self._on_debug_mode_toggle()
        self.btn_background.config(state="disabled" if self._closing or (
            self.background.busy and not self.background.running) else "normal")
        self.root.after(150, self._poll_queue)

    # ══════════════════════════════════════════════════════
    #  視窗生命週期
    # ══════════════════════════════════════════════════════

    def run(self):
        self.root.mainloop()

    def _close_log_files(self, event):
        if event.widget == self.root:
            for handler in self._file_logger.handlers:
                handler.close()

    def _remember_window_geometry(self, event=None):
        if event is not None and event.widget != self.root:
            return
        state = self.root.state()
        if state == "normal":
            self._normal_geometry = (
                self.root.winfo_width(), self.root.winfo_height(),
                self.root.winfo_x(), self.root.winfo_y())
        if event is not None and state in ("normal", "zoomed"):
            if self._window_save_job is not None:
                self.root.after_cancel(self._window_save_job)
            self._window_save_job = self.root.after(1000, self._save_window_settings_later)

    def _save_window_settings_later(self):
        self._window_save_job = None
        try:
            self._save_window_settings()
        except OSError as error:
            self._file_logger.warning("Unable to save app settings: %s", error)

    def _save_window_settings(self):
        state = self.root.state()
        if state == "withdrawn":
            return
        if state == "normal":
            self._remember_window_geometry()
        width, height, x, y = self._normal_geometry
        save_app_settings({"window": {
            "width": width,
            "height": height,
            "x": x,
            "y": y,
            "maximized": state == "zoomed",
        }})

    def on_close(self):
        self._closing = True
        if self._window_save_job is not None:
            self.root.after_cancel(self._window_save_job)
            self._window_save_job = None
        self._unregister_hotkeys()
        self.btn_toggle.config(state="disabled")
        self.btn_background.config(state="disabled")
        self._stop_all()
        if self.background.busy:
            self.root.after(100, self.on_close)
            return
        try:
            self._save_window_settings()
        except OSError as error:
            self._file_logger.warning("Unable to save app settings: %s", error)
        for job in self.root.tk.call("after", "info"):
            self.root.after_cancel(job)
        self.root.destroy()


# ══════════════════════════════════════════════════════════
#  Combo 編輯對話框
# ══════════════════════════════════════════════════════════

class ComboEditor:
    """彈出視窗，編輯單一 Combo 的所有屬性"""

    def __init__(self, parent, combo: Combo, profile: Profile | None = None):
        self.combo = combo
        self.profile = profile or Profile()
        self.result = False
        self._calc_preview_job = None

        self.win = tk.Toplevel(parent)
        self.win.title("編輯 Combo")
        self.win.transient(parent)
        self.win.geometry("560x740")
        self.win.minsize(520, 500)
        self.win.resizable(True, True)
        self.win.grab_set()
        self.win.attributes("-topmost", True)

        pad = {"padx": 10, "pady": 3}

        # 確定/取消 — 先 pack 到底部，確保任何視窗大小都不被擠掉
        frm_btns = ttk.Frame(self.win)
        frm_btns.pack(side="bottom", pady=10)
        ttk.Button(frm_btns, text="確定", bootstyle="primary", width=10, command=self._ok).pack(side="left", padx=10)
        ttk.Button(frm_btns, text="取消", width=10, command=self.win.destroy).pack(side="left", padx=10)

        # ── 可捲動內容區 ──────────────────────────────────────
        _canvas = tk.Canvas(self.win, highlightthickness=0)
        _vsb = ttk.Scrollbar(self.win, orient="vertical", command=_canvas.yview)
        _canvas.configure(yscrollcommand=_vsb.set)
        _vsb.pack(side="right", fill="y")
        _canvas.pack(side="left", fill="both", expand=True)
        sf = ttk.Frame(_canvas)
        _win_id = _canvas.create_window((0, 0), window=sf, anchor="nw")
        sf.bind("<Configure>", lambda e: _canvas.configure(scrollregion=_canvas.bbox("all")))
        _canvas.bind("<Configure>", lambda e: _canvas.itemconfigure(_win_id, width=e.width))
        self.win.bind("<MouseWheel>", lambda e: _canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        # 名稱
        ttk.Label(sf, text="名稱:").pack(anchor="w", **pad)
        self.var_name = tk.StringVar(value=combo.name)
        ttk.Entry(sf, textvariable=self.var_name, width=40).pack(fill="x", **pad)
        self.var_detection_mode = tk.StringVar(value=combo.detection_mode)
        mode_row = ttk.Frame(sf)
        mode_row.pack(fill="x", **pad)
        for mode, label in (("active", "主動偵測"), ("background", "背景偵測（記帳）")):
            ttk.Radiobutton(mode_row, text=label, value=mode, variable=self.var_detection_mode,
                           command=self._on_detection_mode_change).pack(side="left")

        # 圖片
        image_start = len(sf.winfo_children())
        frm_img = ttk.Frame(sf)
        frm_img.pack(fill="x", **pad)
        self.lbl_image = ttk.Label(frm_img, text="觸發圖片:")
        self.lbl_image.pack(side="left")
        self.var_image = tk.StringVar(value=combo.image_path)
        ttk.Entry(frm_img, textvariable=self.var_image, width=12).pack(side="left", padx=5, fill="x", expand=True)
        ttk.Button(frm_img, text="瀏覽", command=self._browse_image).pack(side="left", padx=2)
        ttk.Button(frm_img, text="貼上", command=self._paste_image).pack(side="left", padx=2)
        ttk.Button(frm_img, text="框選", command=self._capture_image).pack(side="left", padx=2)

        # 圖片預覽（文字 + 去背按鈕 + 縮圖）
        frm_preview = ttk.Frame(sf)
        frm_preview.pack(fill="x", padx=10)
        self.btn_rmbg = ttk.Button(
            frm_preview, text="去背", command=self._remove_bg,
        )
        self.btn_rmbg.pack(side="right", padx=5)
        self.btn_rmbg.pack_forget()  # 初始隱藏，有圖才顯示
        self.lbl_preview = ttk.Label(frm_preview, text="", bootstyle="default", anchor="w")
        self.lbl_preview.pack(side="left", fill="x", expand=True)

        self._preview_photo = None  # 防止 GC 回收 PhotoImage
        self.lbl_thumb = ttk.Label(sf, relief="groove")
        self.lbl_thumb.pack(anchor="w", padx=10, pady=2)
        self.lbl_thumb.pack_forget()  # 初始隱藏，有圖才顯示

        # 若已有圖片，顯示預覽
        if combo.image_path:
            self._update_preview(combo.image_path)

        # Ctrl+V 快捷鍵
        self.win.bind("<Control-v>", lambda e: self._paste_image())

        # 比對閾值
        frm_thresh = ttk.Frame(sf)
        frm_thresh.pack(fill="x", **pad)
        ttk.Label(frm_thresh, text="圖片閾值:").pack(side="left")
        self.var_threshold = tk.StringVar(value=str(combo.match_threshold))
        ttk.Entry(frm_thresh, textvariable=self.var_threshold, width=8).pack(side="left", padx=5)
        ttk.Label(
            frm_thresh,
            text="(0.0~1.0；1.0 僅接受完全匹配，建議最高 0.99)",
        ).pack(side="left")

        # 文字驗證（可選）— 分兩行避免溢出
        frm_vtext1 = ttk.Frame(sf)
        frm_vtext1.pack(fill="x", **pad)
        ttk.Label(frm_vtext1, text="驗證文字:").pack(side="left")
        self.var_verify_text = tk.StringVar(value=combo.verify_text)
        ttk.Entry(frm_vtext1, textvariable=self.var_verify_text, width=20).pack(side="left", padx=5, fill="x", expand=True)
        ttk.Label(frm_vtext1, text="(留空=不驗證)").pack(side="left")

        frm_vtext1b = ttk.Frame(sf)
        frm_vtext1b.pack(fill="x", padx=10, pady=1)
        ttk.Button(frm_vtext1b, text="螢幕截取 OCR", command=self._ocr_from_screen).pack(side="left")

        frm_vtext2 = ttk.Frame(sf)
        frm_vtext2.pack(fill="x", **pad)
        ttk.Label(frm_vtext2, text="文字閾值:").pack(side="left")
        self.var_text_threshold = tk.StringVar(value=str(combo.text_threshold))
        ttk.Entry(frm_vtext2, textvariable=self.var_text_threshold, width=8).pack(side="left", padx=5)
        ttk.Label(frm_vtext2, text="(0.0~1.0, OCR 信心度)").pack(side="left")

        # 匹配模式 + 吻合比例
        frm_vtext3 = ttk.Frame(sf)
        frm_vtext3.pack(fill="x", **pad)
        ttk.Label(frm_vtext3, text="匹配模式:").pack(side="left")
        self.var_text_match_mode = tk.StringVar(value=combo.text_match_mode.value)
        om = ttk.OptionMenu(
            frm_vtext3, self.var_text_match_mode,
            combo.text_match_mode.value,
            *[m.value for m in TextMatchMode],
            command=lambda _: self._on_match_mode_change(),
        )
        om.pack(side="left", padx=5)
        # 吻合比例（僅部分符合時顯示）
        self.lbl_match_ratio = ttk.Label(frm_vtext3, text="吻合比例:")
        self.var_text_match_ratio = tk.StringVar(value=str(combo.text_match_ratio))
        self.ent_match_ratio = ttk.Entry(
            frm_vtext3, textvariable=self.var_text_match_ratio, width=5,
        )
        self.lbl_match_ratio_hint = ttk.Label(frm_vtext3, text="(0.0~1.0)")
        self._on_match_mode_change()  # 初始顯示/隱藏

        # 觸發模式
        self.image_widgets = sf.winfo_children()[image_start:]
        self.position_image_widgets = (frm_img, frm_preview, frm_thresh)
        self.image_pack = {w: w.pack_info() for w in self.image_widgets if w.winfo_manager() == "pack"}
        self.lbl_trigger = ttk.Label(sf, text="觸發模式:")
        self.lbl_trigger.pack(anchor="w", **pad)
        self.var_trigger = tk.StringVar(value=combo.trigger.value)
        frm_trigger = ttk.Frame(sf)
        frm_trigger.pack(fill="x", **pad)
        for mode in TriggerMode:
            ttk.Radiobutton(
                frm_trigger, text=TRIGGER_LABELS[mode],
                variable=self.var_trigger, value=mode.value,
                command=self._on_action_type_change,
            ).pack(anchor="w")

        # 動作類型
        self.lbl_action = ttk.Label(sf, text="動作:")
        self.lbl_action.pack(anchor="w", **pad)
        self.var_action_type = tk.StringVar(value=combo.action.action_type.value)
        frm_at = ttk.Frame(sf)
        self.frm_action_types = frm_at
        frm_at.pack(fill="x", **pad)
        action_types = list(ActionType)
        for i, at in enumerate(action_types):
            ttk.Radiobutton(
                frm_at, text=ACTION_LABELS[at],
                variable=self.var_action_type, value=at.value,
                command=self._on_action_type_change,
            ).grid(row=i // 3, column=i % 3, sticky="w", padx=5)

        # 動作參數
        self.frm_params = ttk.Labelframe(sf, text="動作參數", padding=(10, 5))
        self.frm_params.pack(fill="x", padx=10, pady=5)
        self._build_position_ui(sf)

        self.frm_target = ttk.Labelframe(self.frm_params, text="點擊目標圖片")
        self.var_target_image = tk.StringVar(value=combo.action.target_image_path)
        ttk.Entry(self.frm_target, textvariable=self.var_target_image).pack(fill="x", padx=3, pady=3)
        target_buttons = ttk.Frame(self.frm_target)
        target_buttons.pack(fill="x")
        ttk.Button(target_buttons, text="瀏覽", command=lambda: self._browse_image(target=True)).pack(side="left")
        ttk.Button(target_buttons, text="框選螢幕", command=lambda: self._capture_image(target=True)).pack(side="left")
        ttk.Label(target_buttons, text="閾值:").pack(side="left", padx=3)
        self.var_target_threshold = tk.StringVar(value=str(combo.action.target_threshold))
        ttk.Entry(target_buttons, textvariable=self.var_target_threshold, width=5).pack(side="left")
        ttk.Button(self.frm_target, text="框選觸發圖片內的圖",
                   command=self._select_target_region).pack(anchor="w", pady=3)
        self.lbl_target_thumb = ttk.Label(self.frm_target)
        self.lbl_target_thumb.pack(anchor="w")
        ttk.Label(self.frm_target, text="找到目標後點擊中心；找不到就不點擊。", bootstyle="default").pack(anchor="w")
        if combo.action.target_image_path:
            self._update_target_preview(combo.action.target_image_path)

        # 滑鼠按鍵
        self.frm_mouse_btn = ttk.Frame(self.frm_params)
        ttk.Label(self.frm_mouse_btn, text="滑鼠鍵:").pack(side="left")
        self.var_mouse_btn = tk.StringVar(value=combo.action.mouse_button.value)
        for mb in MouseButton:
            ttk.Radiobutton(
                self.frm_mouse_btn, text=MOUSE_BTN_LABELS[mb],
                variable=self.var_mouse_btn, value=mb.value,
            ).pack(side="left", padx=3)

        # 鍵盤按鍵
        self.frm_key = ttk.Frame(self.frm_params)
        ttk.Label(self.frm_key, text="鍵盤鍵:").pack(side="left")
        self.var_key = tk.StringVar(value=combo.action.key)
        ttk.Entry(self.frm_key, textvariable=self.var_key, width=15).pack(side="left", padx=5)
        ttk.Label(self.frm_key, text="(如: space, e, f)").pack(side="left")

        # 延遲
        self.frm_delay = ttk.Frame(self.frm_params)
        ttk.Label(self.frm_delay, text="延遲(秒):").pack(side="left")
        self.var_delay_min = tk.StringVar(value=str(combo.action.delay_min))
        self.var_delay_max = tk.StringVar(value=str(combo.action.delay_max))
        ttk.Entry(self.frm_delay, textvariable=self.var_delay_min, width=6).pack(side="left", padx=2)
        ttk.Label(self.frm_delay, text="~").pack(side="left")
        ttk.Entry(self.frm_delay, textvariable=self.var_delay_max, width=6).pack(side="left", padx=2)

        # 持續時間
        self.frm_duration = ttk.Frame(self.frm_params)
        ttk.Label(self.frm_duration, text="持續(秒):").pack(side="left")
        self.var_duration = tk.StringVar(value=str(combo.action.duration))
        ttk.Entry(self.frm_duration, textvariable=self.var_duration, width=8).pack(side="left", padx=5)

        # 間隔 / 放開間隔
        self.frm_interval = ttk.Frame(self.frm_params)
        self._lbl_interval = ttk.Label(self.frm_interval, text="間隔(秒):")
        self._lbl_interval.pack(side="left")
        self.var_interval_min = tk.StringVar(value=str(combo.action.interval_min))
        self.var_interval_max = tk.StringVar(value=str(combo.action.interval_max))
        ttk.Entry(self.frm_interval, textvariable=self.var_interval_min, width=6).pack(side="left", padx=2)
        ttk.Label(self.frm_interval, text="~").pack(side="left")
        ttk.Entry(self.frm_interval, textvariable=self.var_interval_max, width=6).pack(side="left", padx=2)

        self._on_action_type_change()
        self._active_widgets = (self.lbl_trigger, frm_trigger, self.lbl_action,
                                self.frm_action_types, self.frm_params, self.frm_position,
                                frm_vtext1, frm_vtext1b, frm_vtext2, frm_vtext3)
        self._active_pack = {w: w.pack_info() for w in self._active_widgets if w.winfo_manager() == "pack"}
        self.frm_calc = ttk.Labelframe(sf, text="背景金額記帳", padding=(8, 5))
        ttk.Label(self.frm_calc, text="定位圖請只框選固定圖示，不包含變動數字。\n數字區域相對於定位圖左上角，可超出定位圖。",
                 justify="left", wraplength=340).pack(anchor="w")
        row = ttk.Frame(self.frm_calc)
        row.pack(fill="x")
        self.calc_region_vars = []
        for label, value in zip(("X", "Y", "寬", "高"), combo.calc_region or (0, 0, 0, 0)):
            ttk.Label(row, text=label).pack(side="left")
            var = tk.StringVar(value=str(value))
            self.calc_region_vars.append(var)
            ttk.Entry(row, textvariable=var, width=5).pack(side="left")
        ttk.Button(self.frm_calc, text="在螢幕框選數字區域", command=self._select_calc_region).pack(anchor="w")
        self.var_daily_limit = tk.StringVar(value=str(combo.daily_limit))
        self.var_daily_reset_time = tk.StringVar(value=combo.daily_reset_time)
        self.var_warning_ratio = tk.StringVar(value=str(combo.warning_ratio * 100))
        self.var_low_confidence_confirmations = tk.StringVar(
            value=str(combo.low_confidence_confirmations))
        for label, var in (("每日收入上限", self.var_daily_limit),
                           ("每日重置時間 HH:MM", self.var_daily_reset_time),
                           ("預警比例 %", self.var_warning_ratio),
                           ("OCR 信心度 0~1", self.var_text_threshold),
                           ("低信心確認次數 2~10", self.var_low_confidence_confirmations)):
            row = ttk.Frame(self.frm_calc)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label).pack(side="left")
            ttk.Entry(row, textvariable=var, width=8).pack(side="right")
        ttk.Button(self.frm_calc, text="測試讀值（不記帳）", command=self._test_calc).pack(anchor="w")
        self.lbl_calc_preview = ttk.Label(self.frm_calc, text="", wraplength=340)
        self.lbl_calc_preview.pack()
        self._on_detection_mode_change()
        self.win.wait_window()

    def _on_detection_mode_change(self):
        background = self.var_detection_mode.get() == "background"
        self.lbl_image.config(text="定位圖片:" if background else "觸發圖片:")
        self.frm_calc.pack_forget()
        for widget in self._active_widgets:
            widget.pack_forget()
        if background:
            self.var_position_preview.set(False)
            self._preview_position()
            self.frm_calc.pack(fill="x", padx=10, pady=5)
            for widget in self.position_image_widgets:
                if widget in self.image_pack:
                    widget.pack(**{**self.image_pack[widget], "before": self.frm_calc})
        else:
            for widget, options in self._active_pack.items():
                widget.pack(**options)
            self._on_action_type_change()

    def _calc_settings(self):
        from dataclasses import replace
        candidate = replace(self.combo, name=self.var_name.get().strip() or "新組合",
                            detection_mode="background", image_path=self.var_image.get().strip(),
                            calc_region=tuple(int(v.get()) for v in self.calc_region_vars),
                            daily_limit=int(self.var_daily_limit.get()),
                            daily_reset_time=self.var_daily_reset_time.get().strip(),
                            warning_ratio=float(self.var_warning_ratio.get()) / 100,
                            low_confidence_confirmations=int(
                                self.var_low_confidence_confirmations.get()),
                            text_threshold=float(self.var_text_threshold.get()),
                            match_threshold=float(self.var_threshold.get()))
        validate_combo(candidate)
        return candidate

    def _select_calc_region(self):
        root = self.win.master
        root_state = root.state()
        try:
            self.win.withdraw()
            root.withdraw()
            root.update()
            time.sleep(0.2)
            capture = ScreenCapture()
            frame, region = self._calc_frame(capture)
            path = self.var_image.get().strip()
            found, _, pos = capture.match_template(frame, path, float(self.var_threshold.get()), region)
            if not found:
                raise ValueError("找不到定位圖片，請先讓目標圖示顯示於螢幕。")
            mw, mh = capture.get_match_size(path)
            tw, th = capture.get_template_size(path)
            selector = RegionSelector(root)
            if selector.result:
                x, y, w, h = selector.result
                if region and (x < region[0] or y < region[1]
                        or x + w > region[0] + region[2] or y + h > region[1] + region[3]):
                    raise ValueError("數字區域必須位於目前設定的偵測範圍內。")
                values = (round((x - (pos[0] - mw // 2)) * tw / mw),
                          round((y - (pos[1] - mh // 2)) * th / mh),
                          max(1, round(w * tw / mw)), max(1, round(h * th / mh)))
                for var, value in zip(self.calc_region_vars, values):
                    var.set(str(value))
        except Exception as error:
            messagebox.showerror("數字框選失敗", str(error), parent=self.win)
        finally:
            root.state(root_state)
            self.win.deiconify()
            self.win.grab_set()

    def _test_calc(self):
        root = self.win.master
        root_state = root.state()
        try:
            if self._calc_preview_job is not None:
                self.win.after_cancel(self._calc_preview_job)
                self._calc_preview_job = None
            combo = self._calc_settings()
            self.win.withdraw()
            self.win.master.withdraw()
            self.win.update()
            time.sleep(0.2)
            capture = ScreenCapture()
            frame, region = self._calc_frame(capture)
            # OCR runs off the Tk thread; poll its result on the Tk thread.
            result = collections.deque()
            def read():
                try:
                    result.append(capture.read_balance(combo, frame, region))
                except Exception as error:
                    result.append(error)
            threading.Thread(target=read, daemon=True).start()
            self.lbl_calc_preview.config(image="", text="OCR 讀取中…")
            def poll():
                self._calc_preview_job = None
                if not self.win.winfo_exists():
                    return
                if not result:
                    self._calc_preview_job = self.win.after(100, poll)
                    return
                item = result.popleft()
                if isinstance(item, Exception):
                    self.lbl_calc_preview.config(text=str(item))
                    return
                value, text, confidence, roi = item
                if roi is not None:
                    self._calc_photo = ImageTk.PhotoImage(Image.fromarray(roi).resize((roi.shape[1] * 3, roi.shape[0] * 3)))
                    self.lbl_calc_preview.config(image=self._calc_photo, compound="top")
                self.lbl_calc_preview.config(text=f"金額：{value}　OCR：{text}\n信心度：{confidence:.2f}")
            self._calc_preview_job = self.win.after(100, poll)
        except Exception as error:
            messagebox.showerror("讀值失敗", str(error), parent=self.win)
        finally:
            root.state(root_state)
            self.win.deiconify()
            self.win.grab_set()

    def _calc_frame(self, capture):
        region = capture_region(self.profile)
        if self.profile.window_title and region is None:
            raise ValueError("目標視窗不可用，請還原視窗或重新選擇。")
        frame = capture.grab_frame(region)
        if frame is None:
            raise ValueError("無法截圖。")
        return frame, region

    def _ocr_from_screen(self):
        """截螢幕 ROI 做 OCR，填入驗證文字欄（與 runtime 相同管道）。"""
        image_path = self.var_image.get().strip()
        if not image_path:
            return
        try:
            threshold = float(self.var_threshold.get())
        except ValueError:
            threshold = 0.8
        # 隱藏視窗避免遮住遊戲畫面，截圖後再還原
        self.win.withdraw()
        self.win.update_idletasks()
        text = ocr_from_screen(image_path, threshold)
        self.win.deiconify()
        if text:
            self.var_verify_text.set(text)
            self.lbl_preview.config(bootstyle="default")
        else:
            self.lbl_preview.config(text="螢幕上找不到模板，請先確認遊戲畫面已顯示", bootstyle="danger")
            return
        # 還原預覽文字
        path = image_path
        try:
            from PIL import Image
            img = Image.open(path)
            w, h = img.size
            self.lbl_preview.config(text=f"{Path(path).name} ({w}x{h})", bootstyle="default")
        except Exception:
            pass

    def _update_preview(self, path: str):
        """載入圖片並顯示縮圖預覽。"""
        try:
            img = Image.open(path)
        except Exception:
            self.lbl_thumb.pack_forget()
            self.btn_rmbg.pack_forget()
            return
        w, h = img.size
        self.lbl_preview.config(
            text=f"{Path(path).name} ({w}x{h})", bootstyle="default",
        )
        self.btn_rmbg.pack(side="right", padx=5)
        # 縮放到最大 120px 高
        max_h = 120
        if h > max_h:
            ratio = max_h / h
            img = img.resize((int(w * ratio), max_h), Image.LANCZOS)
        self._preview_photo = ImageTk.PhotoImage(img)
        self.lbl_thumb.config(image=self._preview_photo)
        self.lbl_thumb.pack(anchor="w", padx=10, pady=2)
        if hasattr(self, "frm_calc") and self.var_detection_mode.get() == "background":
            self.lbl_thumb.pack(before=self.frm_calc, anchor="w", padx=10, pady=2)
        if hasattr(self, "frm_position"):
            self._preview_position()

    def _update_target_preview(self, path: str):
        try:
            with Image.open(path) as source:
                img = source.copy()
            img.thumbnail((300, 100))
            self._target_photo = ImageTk.PhotoImage(img)
            self.lbl_target_thumb.config(image=self._target_photo, text="")
        except Exception:
            self.lbl_target_thumb.config(image="", text="無法讀取目標圖片")

    def _select_target_region(self):
        try:
            with Image.open(self.var_image.get().strip()) as image:
                image = image.convert("RGBA")
        except (OSError, ValueError):
            messagebox.showinfo("提示", "請先指定有效的觸發圖片。", parent=self.win)
            return
        try:
            selector = ImageRegionSelector(
                self.win, image, hint="拖曳框選要點擊的按鈕或圖片。ESC 取消。")
        finally:
            self.win.grab_set()
        if not selector.result:
            return
        x, y, w, h = selector.result
        try:
            profile_manager.ensure_dirs()
            path = profile_manager.TEMPLATES_DIR / f"template_{time.time_ns()}.png"
            image.crop((x, y, x + w, y + h)).save(path, "PNG")
        except OSError as e:
            messagebox.showerror("錯誤", f"儲存目標圖片失敗:\n{e}", parent=self.win)
            return
        self.var_target_image.set(str(path))
        self._update_target_preview(str(path))

    def _capture_image(self, target: bool = False):
        root = self.win.master
        root_state = root.state()
        try:
            self.win.withdraw()
            root.withdraw()
            root.update()
            time.sleep(0.2)
            screenshot = ImageGrab.grab()
            selector = RegionSelector(root)
            if selector.result:
                x, y, w, h = selector.result
                profile_manager.ensure_dirs()
                path = profile_manager.TEMPLATES_DIR / f"template_{time.time_ns()}.png"
                screenshot.crop((x, y, x + w, y + h)).save(path, "PNG")
                if target:
                    self.var_target_image.set(str(path))
                    self._update_target_preview(str(path))
                else:
                    self.var_image.set(str(path))
                    self._update_preview(str(path))
        except Exception as e:
            messagebox.showerror("錯誤", f"擷取圖片失敗:\n{e}", parent=self.win)
        finally:
            root.state(root_state)
            self.win.deiconify()
            self.win.grab_set()

    def _browse_image(self, target: bool = False):
        path = filedialog.askopenfilename(
            title="選擇點擊目標圖片" if target else "選擇定位圖片" if self.var_detection_mode.get() == "background" else "選擇觸發圖片",
            initialdir=str(profile_manager.TEMPLATES_DIR),
            filetypes=[("圖片", "*.png *.jpg *.bmp"), ("所有檔案", "*.*")],
        )
        if path:
            if target:
                self.var_target_image.set(path)
                self._update_target_preview(path)
            else:
                self.var_image.set(path)
                self._update_preview(path)

    def _paste_image(self):
        """從剪貼簿貼上圖片，自動存到 templates/ 目錄"""
        try:
            img = ImageGrab.grabclipboard()
        except Exception:
            img = None

        if img is None:
            self.lbl_preview.config(text="剪貼簿中沒有圖片", bootstyle="danger")
            return

        profile_manager.TEMPLATES_DIR.mkdir(exist_ok=True)

        # 純英數檔名（避免中文路徑導致 OpenCV 讀取失敗）
        timestamp = int(time.time())
        filename = f"template_{timestamp}.png"
        save_path = profile_manager.TEMPLATES_DIR / filename

        try:
            img.save(str(save_path), "PNG")
        except Exception as e:
            messagebox.showerror("錯誤", f"儲存圖片失敗:\n{e}")
            return

        self.var_image.set(str(save_path))
        self._update_preview(str(save_path))

    def _remove_bg(self):
        """開啟色彩去背對話框"""
        image_path = self.var_image.get().strip()
        if not image_path:
            return
        dlg = RemoveBgDialog(self.win, image_path)
        if dlg.result_path:
            self.var_image.set(dlg.result_path)
            self._update_preview(dlg.result_path)

    def _build_position_ui(self, parent):
        settings = self.combo.position_control
        self.frm_position = ttk.Labelframe(parent, text="位置控制設定", padding=(8, 5))
        ttk.Label(self.frm_position, text="完整觸發圖片用於定位；框選區域只用來追蹤箭頭。",
                 wraplength=340, justify="left").pack(anchor="w")
        row = ttk.Frame(self.frm_position)
        row.pack(fill="x")
        self.position_region_vars = [tk.StringVar(value=str(v) if v is not None else "")
                                     for v in (settings.region if settings.region_space == "template"
                                               and settings.region else (None,) * 4)]
        for label, var in zip(("X", "Y", "寬", "高"), self.position_region_vars):
            ttk.Label(row, text=label).pack(side="left")
            ttk.Entry(row, textvariable=var, width=5).pack(side="left")
        ttk.Button(self.frm_position, text="框選觸發圖片區塊", command=self._select_position_region).pack(anchor="w")
        self.var_position_color = tk.StringVar(value="#%02x%02x%02x" % settings.color)
        self.var_position_tolerance = tk.StringVar(value=str(settings.tolerance))
        self.var_position_area = tk.StringVar(value=str(settings.min_area))
        self.var_position_max_area = tk.StringVar(value=str(settings.max_area))
        self.var_position_press = tk.StringVar(value=f"{settings.press_at * 100:g}")
        self.var_position_release = tk.StringVar(value=f"{settings.release_at * 100:g}")
        self.var_band_color = tk.StringVar(value="#%02x%02x%02x" % settings.band_color)
        self.var_band_tolerance = tk.StringVar(value=str(settings.band_tolerance))
        self.var_band_start = tk.StringVar(value=f"{settings.band_start:g}")
        self.var_band_stop = tk.StringVar(value=f"{settings.band_stop:g}")
        self.var_band_margin = tk.StringVar(value=str(settings.band_margin))
        self.var_band_missing_ms = tk.StringVar(value=str(settings.band_missing_ms))
        self.position_only_rows = []
        self.band_only_rows = []
        for label, var in (("標記顏色 (#RRGGBB)", self.var_position_color),
                           ("顏色容差 (0~90)", self.var_position_tolerance),
                           ("最小面積 (像素)", self.var_position_area),
                           ("最大面積 (0=不限)", self.var_position_max_area),
                           ("啟動位置 (%)", self.var_position_press),
                           ("停止位置 (%)", self.var_position_release),
                           ("執行色帶 (#RRGGBB)", self.var_band_color),
                           ("色帶色相容差 (0~90)", self.var_band_tolerance),
                           ("啟動色帶比例 (0~1)", self.var_band_start),
                           ("停止色帶比例 (0~1)", self.var_band_stop),
                           ("取樣範圍 (箭頭倍數)", self.var_band_margin),
                           ("漏判容忍時間 (ms)", self.var_band_missing_ms)):
            row = ttk.Frame(self.frm_position)
            row.pack(fill="x")
            ttk.Label(row, text=label, width=22, anchor="w").pack(side="left")
            ttk.Entry(row, textvariable=var, width=10).pack(side="left")
            if var in (self.var_position_press, self.var_position_release):
                self.position_only_rows.append(row)
            if var in (self.var_band_color, self.var_band_tolerance, self.var_band_start,
                       self.var_band_stop, self.var_band_margin, self.var_band_missing_ms):
                self.band_only_rows.append(row)
        self.lbl_band_hint = ttk.Label(self.frm_position, wraplength=340, justify="left",
            text="色帶判斷：排除箭頭與白／暗色後取樣。漏判時在容忍時間內維持動作；0 表示立即停止。辨識到停止比例仍立即停止。同色背景可能使箭頭辨識失敗。")
        self.lbl_control_hint = ttk.Label(self.frm_position, wraplength=340, justify="left",
            text="條件成立時：單擊／按一下／點圖／音效執行一次；連點／長按依時間與間隔重複。條件失效立即取消。")
        self.lbl_control_hint.pack(anchor="w")
        self.var_position_preview = tk.BooleanVar(value=True)
        self._position_preview_job = None
        ttk.Checkbutton(self.frm_position, text="預覽觸發圖片區塊",
                       variable=self.var_position_preview, command=self._preview_position).pack(anchor="w")
        self.var_position_click = tk.StringVar(value="取色")
        row = ttk.Frame(self.frm_position)
        row.pack(fill="x")
        self.position_tools = []
        for mode in ("取色", "按住線", "放開線", "色帶取色"):
            label = {"按住線": "啟動線", "放開線": "停止線"}.get(mode, mode)
            button = ttk.Radiobutton(row, text=label, variable=self.var_position_click, value=mode)
            button.pack(side="left")
            self.position_tools.append(button)
        self.position_canvas = tk.Canvas(self.frm_position, width=340, height=170,
                                         bg="#222222", highlightthickness=0)
        self.position_canvas.pack(anchor="w")
        self.position_canvas.bind("<Button-1>", self._position_preview_click)
        self.lbl_position_status = ttk.Label(self.frm_position, text="先框選觸發圖片區塊，再點圖片取色或設定界線。",
                                            wraplength=340, justify="left")
        self.lbl_position_status.pack(anchor="w")
        self._position_frame = None
        self.win.bind("<Destroy>", self._cancel_position_preview, add="+")
        for var in (self.var_band_color, self.var_band_tolerance, self.var_band_start,
                    self.var_band_stop, self.var_band_margin, self.var_band_missing_ms, self.var_position_color,
                    self.var_position_tolerance, self.var_position_area,
                    self.var_position_max_area, *self.position_region_vars):
            var.trace_add("write", self._refresh_band_preview)

    def _refresh_band_preview(self, *args):
        if self.var_trigger.get() == TriggerMode.COLOR_BAND.value:
            self._preview_position()

    def _position_settings(self):
        if any(not var.get().strip() for var in self.position_region_vars):
            raise ValueError("請先框選觸發圖片的偵測區塊。")
        value = self.var_position_color.get().strip()
        if len(value) != 7 or not value.startswith("#"):
            raise ValueError("標記顏色格式必須是 #RRGGBB。")
        band_color = self.var_band_color.get().strip()
        if len(band_color) != 7 or not band_color.startswith("#"):
            raise ValueError("色帶顏色格式必須是 #RRGGBB。")
        settings = PositionControl(
            region=tuple(int(var.get()) for var in self.position_region_vars),
            color=tuple(int(value[i:i + 2], 16) for i in (1, 3, 5)),
            tolerance=int(self.var_position_tolerance.get()),
            min_area=int(self.var_position_area.get()),
            max_area=int(self.var_position_max_area.get()),
            press_at=self._to_float(self.var_position_press.get()) / 100,
            release_at=self._to_float(self.var_position_release.get()) / 100,
            band_color=tuple(int(band_color[i:i + 2], 16) for i in (1, 3, 5)),
            band_tolerance=int(self.var_band_tolerance.get()),
            band_start=self._to_float(self.var_band_start.get()),
            band_stop=self._to_float(self.var_band_stop.get()),
            band_margin=self._to_float(self.var_band_margin.get()),
            band_missing_ms=int(self.var_band_missing_ms.get()))
        settings.validate()
        try:
            with Image.open(self.var_image.get().strip()) as image:
                tw, th = image.size
        except (OSError, ValueError):
            raise ValueError("請先指定有效的觸發圖片。") from None
        x, y, w, h = settings.region
        if x < 0 or y < 0 or x + w > tw or y + h > th:
            raise ValueError("箭頭追蹤區域必須在觸發圖片內。")
        return settings

    def _select_position_region(self):
        try:
            with Image.open(self.var_image.get().strip()) as image:
                image = image.convert("RGBA")
        except (OSError, ValueError):
            messagebox.showinfo("提示", "請先指定有效的觸發圖片。", parent=self.win)
            return
        selector = ImageRegionSelector(self.win, image)
        self.win.grab_set()
        if selector.result:
            for var, value in zip(self.position_region_vars, selector.result):
                var.set(str(value))
            self.var_position_preview.set(True)
            self._preview_position()

    def _cancel_position_preview(self, event):
        if event.widget == self.win:
            for attribute in ("_position_preview_job", "_calc_preview_job"):
                job = getattr(self, attribute)
                if job is not None:
                    self.win.after_cancel(job)
                    setattr(self, attribute, None)

    def _preview_position(self):
        if self._position_preview_job is not None:
            self.win.after_cancel(self._position_preview_job)
            self._position_preview_job = None
        if not self.var_position_preview.get():
            return
        try:
            settings = self._position_settings()
            with Image.open(self.var_image.get().strip()) as image:
                x, y, w, h = settings.region
                frame = np.array(image.convert("RGBA").crop((x, y, x + w, y + h)))
            self._position_frame = frame
            if frame is None:
                raise ValueError("無法擷取畫面。")
            h, w = frame.shape[:2]
            scale = min(340 / w, 170 / h)
            width, height = max(1, round(w * scale)), max(1, round(h * scale))
            self._position_preview_size = (width, height)
            self._position_photo = ImageTk.PhotoImage(Image.fromarray(frame).resize((width, height)))
            canvas = self.position_canvas
            canvas.delete("all")
            canvas.create_image(0, 0, image=self._position_photo, anchor="nw")
            band_mode = self.var_trigger.get() == TriggerMode.COLOR_BAND.value
            for fraction, color in (() if band_mode else
                                    ((settings.press_at, "#00ff88"), (settings.release_at, "#ff5555"))):
                x = fraction * (width - 1)
                canvas.create_line(x, 0, x, height, fill=color, width=2)
            marker = find_color_marker(frame, settings)
            if marker:
                (cx, cy), _ = marker
                px, py = cx * scale, cy * scale
                canvas.create_oval(px - 4, py - 4, px + 4, py + 4, outline="cyan", width=2)
                self.lbl_position_status.config(text=f"標記位置：{cx / max(1, w - 1):.1%}；綠線啟動、紅線停止。")
            else:
                self.lbl_position_status.config(text="未找到唯一標記：調整顏色／容差／區域。此時會停止動作。")
            if band_mode:
                active, ratio = color_band_state(frame, marker, settings)
                continues, _ = color_band_state(frame, marker, settings, active=True)
                text = ("箭頭或色帶取樣不足：停止" if ratio is None else
                        f"執行色帶比例：{ratio:.3f}\n"
                        f"原本未執行：{'啟動' if active else '維持停止'}\n"
                        f"原本執行中：{'繼續執行' if continues else '停止'}")
                text += "\n預覽僅測設定圖片，不執行動作。"
                self.lbl_position_status.config(text=text)
        except (ValueError, TypeError, OSError) as e:
            self._position_frame = None
            self.position_canvas.delete("all")
            self.lbl_position_status.config(text=str(e))

    def _position_preview_click(self, event):
        frame = self._position_frame
        if frame is None:
            return
        width, height = self._position_preview_size
        if not (0 <= event.x < width and 0 <= event.y < height):
            return
        mode = self.var_position_click.get()
        if mode in ("取色", "色帶取色"):
            x = min(frame.shape[1] - 1, int(event.x * frame.shape[1] / width))
            y = min(frame.shape[0] - 1, int(event.y * frame.shape[0] / height))
            if frame.shape[2] == 4 and frame[y, x, 3] == 0:
                self.lbl_position_status.config(text="透明區域不能取色，請點選可見的箭頭。")
                return
            variable = self.var_band_color if mode == "色帶取色" else self.var_position_color
            variable.set("#%02x%02x%02x" % tuple(frame[y, x, :3]))
        else:
            var = self.var_position_press if mode == "按住線" else self.var_position_release
            var.set(f"{event.x / max(1, width - 1) * 100:.1f}")
        self._preview_position()

    def _on_match_mode_change(self):
        """部分符合時才顯示吻合比例欄位"""
        if self.var_text_match_mode.get() == "partial":
            self.lbl_match_ratio.pack(side="left", padx=(10, 0))
            self.ent_match_ratio.pack(side="left", padx=2)
            self.lbl_match_ratio_hint.pack(side="left")
        else:
            self.lbl_match_ratio.pack_forget()
            self.ent_match_ratio.pack_forget()
            self.lbl_match_ratio_hint.pack_forget()

    def _on_action_type_change(self):
        """根據動作類型 + 觸發模式，顯示/隱藏相關參數"""
        at = self.var_action_type.get()
        trigger = self.var_trigger.get()
        position_mode = TriggerMode(trigger).tracks_marker
        band_mode = trigger == TriggerMode.COLOR_BAND.value
        self.frm_position.config(text="色帶判斷設定" if band_mode else "位置控制設定")
        for row in self.position_only_rows + self.band_only_rows:
            row.pack_forget()
        for row in self.band_only_rows if band_mode else self.position_only_rows:
            row.pack(fill="x", before=self.lbl_control_hint)
        self.lbl_band_hint.pack_forget()
        if band_mode:
            self.lbl_band_hint.pack(anchor="w", before=self.lbl_control_hint)
        for button in self.position_tools:
            button.pack_forget()
        for button in ([self.position_tools[0], self.position_tools[3]] if band_mode else self.position_tools[:3]):
            button.pack(side="left")
        self.var_position_click.set("取色")
        for widget in self.image_widgets:
            widget.pack_forget()
        for widget, options in self.image_pack.items():
            if not position_mode or widget in self.position_image_widgets:
                widget.pack(**{**options, "before": self.lbl_trigger})
        if self.var_image.get().strip():
            self._update_preview(self.var_image.get().strip())
            self.lbl_thumb.pack(before=self.lbl_trigger, anchor="w", padx=10, pady=2)
        self.frm_position.pack_forget()
        self.lbl_action.pack_forget()
        self.frm_action_types.pack_forget()
        if position_mode:
            self.frm_position.pack(fill="x", padx=10, pady=5, before=self.frm_params)
            self.var_position_preview.set(True)
            self._preview_position()
        else:
            self.var_position_preview.set(False)
        self.lbl_action.pack(anchor="w", padx=10, pady=3, before=self.frm_params)
        self.frm_action_types.pack(fill="x", padx=10, pady=3, before=self.frm_params)
        at = self.var_action_type.get()

        is_mouse = at in ("click", "click_image", "rapid_click", "hold_mouse")
        is_key = at in ("key_press", "rapid_key", "hold_key")
        is_rapid = at in ("rapid_click", "rapid_key")
        is_hold = at in ("hold_mouse", "hold_key")

        # rapid/hold 都需要顯示持續時間
        # hold + while_visible：持續(秒) = 每次按住多久，放開間隔 = 放開多久後再按
        need_duration = is_rapid or is_hold

        # 先全部隱藏再按需顯示
        for w in (self.frm_mouse_btn, self.frm_key, self.frm_delay,
                  self.frm_duration, self.frm_interval, self.frm_target):
            w.pack_forget()

        if is_mouse:
            self.frm_mouse_btn.pack(fill="x", pady=2)
        if at == "click_image":
            self.frm_target.pack(fill="x", pady=2)
        if is_key:
            self.frm_key.pack(fill="x", pady=2)
        self.frm_delay.pack(fill="x", pady=2)
        if need_duration:
            self.frm_duration.pack(fill="x", pady=2)
        if is_rapid or is_hold:
            self._lbl_interval.config(text="放開間隔(秒):" if is_hold else "間隔(秒):")
            self.frm_interval.pack(fill="x", pady=2)

    @staticmethod
    def _to_float(s: str, default: float = 0.0) -> float:
        """解析浮點數，自動處理中文句號等替代字元"""
        s = s.strip().replace("。", ".").replace("，", ".").replace(",", ".")
        return float(s) if s else default

    def _ok(self):
        try:
            if self.var_detection_mode.get() == "background":
                candidate = self._calc_settings()
                if not ScreenCapture().load_template(candidate.image_path):
                    raise ValueError("請設定有效的固定圖示定位圖片。")
                for name in ("name", "detection_mode", "image_path", "calc_region", "daily_limit",
                             "daily_reset_time",
                             "warning_ratio", "low_confidence_confirmations",
                             "text_threshold", "match_threshold"):
                    setattr(self.combo, name, getattr(candidate, name))
                self.result = True
                self.win.destroy()
                return
            # ── 先解析所有值（可能 raise ValueError）──
            name = self.var_name.get().strip() or "新組合"
            image_path = self.var_image.get().strip()
            match_threshold = self._to_float(self.var_threshold.get(), 0.8)
            verify_text = self.var_verify_text.get().strip()
            text_threshold = self._to_float(self.var_text_threshold.get(), 0.5)
            text_match_mode = TextMatchMode(self.var_text_match_mode.get())
            text_match_ratio = (
                self._to_float(self.var_text_match_ratio.get(), 0.6)
                if text_match_mode == TextMatchMode.PARTIAL
                else self.combo.text_match_ratio
            )
            trigger = TriggerMode(self.var_trigger.get())
            position_settings = self._position_settings() if trigger.tracks_marker else self.combo.position_control

            at = self.var_action_type.get()
            action_type = ActionType(at)
            is_mouse = at in ("click", "rapid_click", "hold_mouse")
            is_rapid = at in ("rapid_click", "rapid_key")
            is_hold = at in ("hold_mouse", "hold_key")
            need_duration = is_rapid or is_hold

            mouse_button = MouseButton(self.var_mouse_btn.get())
            key = self.var_key.get().strip()
            delay_min = self._to_float(self.var_delay_min.get())
            delay_max = self._to_float(self.var_delay_max.get())
            if action_type == ActionType.PLAY_SOUND and not 0 <= delay_min <= delay_max < float("inf"):
                raise ValueError("音效延遲必須是有限非負數，且最小值不能大於最大值。")

            target_image_path = self.var_target_image.get().strip()
            target_threshold = self.combo.action.target_threshold
            if action_type == ActionType.CLICK_IMAGE:
                target_threshold = self._to_float(self.var_target_threshold.get(), 0.8)
                if not 0 <= target_threshold <= 1:
                    raise ValueError("點擊目標的圖片閾值必須介於 0~1。")
                if not 0 <= delay_min <= delay_max:
                    raise ValueError("延遲必須符合：0 ≤ 最小延遲 ≤ 最大延遲。")
                try:
                    with Image.open(target_image_path) as img:
                        img.load()
                except Exception:
                    raise ValueError("請上傳或框選有效的點擊目標圖片。") from None

            # 只在欄位可見時才解析，否則保留原值
            duration = (self._to_float(self.var_duration.get(), 1.0)
                        if need_duration else self.combo.action.duration)
            interval_min = (self._to_float(self.var_interval_min.get(), 0.05)
                            if is_rapid or is_hold else self.combo.action.interval_min)
            interval_max = (self._to_float(self.var_interval_max.get(), 0.15)
                            if is_rapid or is_hold else self.combo.action.interval_max)

            # ── 全部合法才寫入（避免 partial update）──
            self.combo.name = name
            self.combo.detection_mode = "active"
            self.combo.image_path = image_path
            self.combo.match_threshold = match_threshold
            self.combo.verify_text = verify_text
            self.combo.text_threshold = text_threshold
            self.combo.text_match_mode = text_match_mode
            self.combo.text_match_ratio = text_match_ratio
            self.combo.trigger = trigger
            self.combo.position_control = position_settings

            action = self.combo.action
            action.action_type = action_type
            action.mouse_button = mouse_button
            action.key = key
            action.delay_min = delay_min
            action.delay_max = delay_max
            action.duration = duration
            action.interval_min = interval_min
            action.interval_max = interval_max
            action.target_image_path = target_image_path
            action.target_threshold = target_threshold

            self.result = True
            self.win.destroy()
        except ValueError as e:
            messagebox.showerror("錯誤", f"參數格式不正確:\n{e}")


# ══════════════════════════════════════════════════════════
#  螢幕區域框選
# ══════════════════════════════════════════════════════════

class RegionSelector:
    """全螢幕半透明覆蓋層，用滑鼠拖拉框選區域，回傳 (x, y, w, h) 或 None"""

    def __init__(self, parent):
        self.result: tuple[int, int, int, int] | None = None
        self._start_x = 0
        self._start_y = 0
        self._rect_id = None

        self.overlay = tk.Toplevel(parent)
        self.overlay.attributes("-fullscreen", True)
        self.overlay.attributes("-alpha", 0.3)
        self.overlay.attributes("-topmost", True)
        self.overlay.configure(bg="black")

        self.canvas = tk.Canvas(
            self.overlay, cursor="cross", highlightthickness=0,
            bg="black",
        )
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.overlay.bind("<Escape>", lambda e: self._cancel())

        # 提示文字
        sw = self.overlay.winfo_screenwidth()
        self.canvas.create_text(
            sw // 2, 30, text="拖曳框選區域，ESC 取消",
            fill="white", font=("Microsoft JhengHei", 16),
        )

        self.overlay.grab_set()
        self.overlay.wait_window()

    def _on_press(self, event):
        self._start_x = event.x
        self._start_y = event.y
        if self._rect_id:
            self.canvas.delete(self._rect_id)
        self._rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y,
            outline="red", width=2,
        )

    def _on_drag(self, event):
        if self._rect_id:
            self.canvas.coords(
                self._rect_id,
                self._start_x, self._start_y, event.x, event.y,
            )

    def _on_release(self, event):
        x1 = min(self._start_x, event.x)
        y1 = min(self._start_y, event.y)
        x2 = max(self._start_x, event.x)
        y2 = max(self._start_y, event.y)
        w = x2 - x1
        h = y2 - y1
        if w > 5 and h > 5:  # 太小視為誤觸
            self.result = (x1, y1, w, h)
        self.overlay.destroy()

    def _cancel(self):
        self.result = None
        self.overlay.destroy()


# ══════════════════════════════════════════════════════════
#  色彩去背對話框
# ══════════════════════════════════════════════════════════

class ImageRegionSelector(RegionSelector):
    """在觸發圖片上拖曳框選，回傳原圖像素座標。"""

    def __init__(self, parent, image, hint="拖曳框選箭頭移動路徑；完整圖片仍用於定位。ESC 取消。"):
        self.result = None
        self._start_x = self._start_y = 0
        self._rect_id = None
        self._image_size = image.size
        scale = min(1, 800 / image.width, 500 / image.height,
                    (parent.winfo_screenwidth() - 100) / image.width,
                    (parent.winfo_screenheight() - 160) / image.height)
        self._display_size = (max(1, round(image.width * scale)),
                              max(1, round(image.height * scale)))
        self.overlay = tk.Toplevel(parent)
        self.overlay.title("框選觸發圖片區塊")
        self.overlay.transient(parent)
        self.overlay.attributes("-topmost", True)
        self.overlay.resizable(False, False)
        ttk.Label(self.overlay, text=hint,
                 wraplength=max(200, self._display_size[0])).pack(padx=10, pady=8)
        self._photo = ImageTk.PhotoImage(image.resize(self._display_size))
        self.canvas = tk.Canvas(self.overlay, width=self._display_size[0],
                                height=self._display_size[1], cursor="cross", highlightthickness=0)
        self.canvas.pack(padx=10, pady=(0, 10))
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw")
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.overlay.bind("<Escape>", lambda e: self._cancel())
        self.overlay.grab_set()
        self.overlay.wait_window()

    def _on_release(self, event):
        dw, dh = self._display_size
        iw, ih = self._image_size
        x1, x2 = sorted((self._start_x, event.x))
        y1, y2 = sorted((self._start_y, event.y))
        x1, x2 = (round(max(0, min(dw, x)) * iw / dw) for x in (x1, x2))
        y1, y2 = (round(max(0, min(dh, y)) * ih / dh) for y in (y1, y2))
        if x2 - x1 >= 6 and y2 - y1 >= 6:
            self.result = (x1, y1, x2 - x1, y2 - y1)
            self.overlay.destroy()


class RemoveBgDialog:
    """點選背景色 → 移除相似顏色 → 存為帶 alpha 的 PNG"""

    PREVIEW_MAX = 400  # 預覽最大邊長

    def __init__(self, parent, image_path: str):
        self.image_path = image_path
        self.result_path: str | None = None

        self.original = Image.open(image_path).convert("RGBA")
        self._bg_color: tuple[int, int, int] | None = None
        self._result_image: Image.Image | None = None

        self.win = tk.Toplevel(parent)
        self.win.title("色彩去背")
        self.win.resizable(False, False)
        self.win.grab_set()
        self.win.attributes("-topmost", True)

        ttk.Label(
            self.win, text="點擊圖片選取背景色，拖曳滑桿調整容差",
        ).pack(padx=10, pady=5)

        # ── 縮放比例 ──
        w, h = self.original.size
        self._scale = min(1.0, self.PREVIEW_MAX / max(w, h))
        disp_w = int(w * self._scale)
        disp_h = int(h * self._scale)

        # ── Canvas 顯示圖片 ──
        self.canvas = tk.Canvas(self.win, width=disp_w, height=disp_h)
        self.canvas.pack(padx=10, pady=5)
        self.canvas.bind("<Button-1>", self._on_click)

        self._display_original()

        # ── 容差滑桿 ──
        frm_tol = ttk.Frame(self.win)
        frm_tol.pack(fill="x", padx=10, pady=2)
        ttk.Label(frm_tol, text="容差:").pack(side="left")
        self.var_tolerance = tk.IntVar(value=30)
        tk.Scale(
            frm_tol, from_=0, to=100, orient="horizontal",
            variable=self.var_tolerance, command=self._on_tolerance_change,
        ).pack(side="left", fill="x", expand=True)

        # ── 狀態 ──
        self.lbl_info = ttk.Label(self.win, text="尚未選取背景色", bootstyle="default")
        self.lbl_info.pack(padx=10)

        # ── 按鈕 ──
        frm_btns = ttk.Frame(self.win)
        frm_btns.pack(pady=10)
        ttk.Button(frm_btns, text="確定", bootstyle="primary", width=10, command=self._ok).pack(
            side="left", padx=10,
        )
        ttk.Button(frm_btns, text="取消", width=10, command=self.win.destroy).pack(
            side="left", padx=10,
        )

        self.win.wait_window()

    # ── 繪製原圖 ──

    def _display_original(self):
        disp_w = int(self.original.size[0] * self._scale)
        disp_h = int(self.original.size[1] * self._scale)
        disp = self.original.resize((disp_w, disp_h), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(disp)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)

    # ── 使用者點擊選色 ──

    def _on_click(self, event):
        ox = int(event.x / self._scale)
        oy = int(event.y / self._scale)
        w, h = self.original.size
        if 0 <= ox < w and 0 <= oy < h:
            r, g, b, _a = self.original.getpixel((ox, oy))
            self._bg_color = (r, g, b)
            self.lbl_info.config(
                text=f"背景色: RGB({r}, {g}, {b})", bootstyle="default",
            )
            self._apply_removal()

    def _on_tolerance_change(self, _value):
        if self._bg_color:
            self._apply_removal()

    # ── 去背演算法 ──

    def _apply_removal(self):
        if self._bg_color is None:
            return
        tolerance = self.var_tolerance.get()
        arr = np.array(self.original, dtype=np.float32)
        bg = np.array(self._bg_color, dtype=np.float32)
        # 歐氏色彩距離
        dist = np.sqrt(np.sum((arr[:, :, :3] - bg) ** 2, axis=2))
        threshold = tolerance * 2.55  # 0~100 → 0~255
        # 建立結果
        result = np.array(self.original)
        result[:, :, 3] = np.where(dist <= threshold, 0, 255).astype(np.uint8)
        self._result_image = Image.fromarray(result, "RGBA")
        self._display_result(result)

    def _display_result(self, result_arr: np.ndarray):
        """在 canvas 上顯示去背預覽（透明區域→洋紅）"""
        preview = result_arr.copy()
        transparent = preview[:, :, 3] == 0
        preview[transparent, 0] = 255
        preview[transparent, 1] = 0
        preview[transparent, 2] = 255
        preview[transparent, 3] = 255

        disp_w = int(self.original.size[0] * self._scale)
        disp_h = int(self.original.size[1] * self._scale)
        disp = Image.fromarray(preview, "RGBA").resize(
            (disp_w, disp_h), Image.LANCZOS,
        )
        self._photo = ImageTk.PhotoImage(disp)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)

    # ── 儲存結果 ──

    def _ok(self):
        if self._result_image is None:
            self.win.destroy()
            return
        base = Path(self.image_path)
        stem = base.stem
        if stem.endswith("_nobg"):
            save_name = stem + ".png"
        else:
            save_name = stem + "_nobg.png"
        save_path = base.parent / save_name
        self._result_image.save(str(save_path), "PNG")
        self.result_path = str(save_path)
        self.win.destroy()


# ══════════════════════════════════════════════════════════
#  Debug 覆蓋層 — 全螢幕透明視窗顯示偵測框線
# ══════════════════════════════════════════════════════════

class DebugOverlay:
    """在螢幕上疊加透明框線，標示每個 Combo 的偵測位置。
    綠色框 = 已偵測到；紅色框 = 未偵測到（最後已知位置）。
    視窗設為點擊穿透，不影響正常操作。
    """

    def __init__(self, root: tk.Tk):
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        self.win.geometry(f"{sw}x{sh}+0+0")
        self.win.attributes("-topmost", True)

        self.canvas = tk.Canvas(
            self.win, bg="black", highlightthickness=0,
            width=sw, height=sh,
        )
        self.canvas.pack()

        # 設為點擊穿透，不擋住滑鼠操作
        self.win.update_idletasks()
        self._sync_transparency()
        self._set_click_through()

    def _sync_transparency(self):
        background = self.canvas.cget("background")
        self.win.configure(bg=background)
        self.win.attributes("-transparentcolor", background)

    def _set_click_through(self):
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.win.winfo_id())
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT)
        except Exception:
            pass

    def update_rects(self, rects: list):
        """更新框線。rects: list of (x, y, w, h, name, found, confidence)"""
        self._sync_transparency()
        self.canvas.delete("all")
        for x, y, w, h, name, found, conf in rects:
            color = "#00FF00" if found else "#FF4444"
            edge = 3
            # 框線
            self.canvas.create_rectangle(
                x - edge, y - edge, x + w + edge, y + h + edge,
                outline=color, width=2,
            )
            # 標籤背景（用色塊遮住以利閱讀）
            label = f" {name}  {conf:.6f} "
            self.canvas.create_rectangle(
                x - edge, y - edge - 20, x + len(label) * 7, y - edge,
                fill=color, outline="",
            )
            self.canvas.create_text(
                x, y - edge - 10, text=label.strip(),
                fill="black", anchor="w",
                font=("Arial", 10, "bold"),
            )

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass
