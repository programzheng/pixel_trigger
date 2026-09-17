"""GUI 介面 — Combo 管理 + Hook 引擎控制"""

from __future__ import annotations

import os
import collections
import time
import warnings
import tkinter as tk

warnings.filterwarnings("ignore", message=".*pin_memory.*")
from tkinter import filedialog, messagebox, simpledialog, ttk
from pathlib import Path

import numpy as np
from PIL import Image, ImageGrab, ImageTk

import profile_manager
from window_target import list_windows
from automation import HookEngine
from screen_capture import find_color_marker, ocr_read_image, ocr_from_screen
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
}

ACTION_LABELS = {
    ActionType.CLICK: "滑鼠 單擊",
    ActionType.RAPID_CLICK: "滑鼠 連續點擊",
    ActionType.HOLD_MOUSE: "滑鼠 長按",
    ActionType.KEY_PRESS: "鍵盤 按一下",
    ActionType.RAPID_KEY: "鍵盤 連續按",
    ActionType.HOLD_KEY: "鍵盤 長按",
}

MOUSE_BTN_LABELS = {
    MouseButton.LEFT: "左鍵",
    MouseButton.RIGHT: "右鍵",
    MouseButton.MIDDLE: "中鍵",
}


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PixelTrigger")
        self.root.geometry("780x660")
        self.root.resizable(True, False)
        self.root.attributes("-topmost", True)

        self.profile: Profile = Profile()
        self.engine = HookEngine(
            on_combo_triggered=self._on_combo_triggered,
            on_cycle_complete=self._on_cycle_complete,
            on_status_change=self._on_status_change,
            on_debug_info=self._on_debug_info,
        )

        # combo 活躍狀態 (用於 GUI 指示燈)
        self._combo_active: dict[str, bool] = {}
        # debug 模式：顯示即時信心度 + 螢幕框線
        self._debug_mode = tk.BooleanVar(value=False)
        # combo_id → (found, confidence, match_rect|None) 最新偵測結果
        self._debug_data: dict[str, tuple] = {}
        # debug 覆蓋層視窗
        self._debug_overlay: "DebugOverlay | None" = None

        # 記住目前 profile 的檔案路徑，儲存時寫回同一檔案
        self._profile_path: Path | None = None

        # ── 跨執行緒通訊用 Queue（worker thread 不碰任何 tkinter）──
        self._msg_queue: collections.deque = collections.deque()

        self._build_ui()
        self._load_initial_profile()
        # 啟動 main thread 的 queue 輪詢
        self._poll_queue()

    # ══════════════════════════════════════════════════════
    #  UI 建構
    # ══════════════════════════════════════════════════════

    def _build_ui(self):
        # --- Profile 列 ---
        frm_profile = tk.Frame(self.root, padx=10, pady=5)
        frm_profile.pack(fill="x")

        tk.Label(frm_profile, text="Profile:").pack(side="left")
        self.cmb_profile = ttk.Combobox(frm_profile, state="readonly", width=20)
        self.cmb_profile.pack(side="left", padx=5)
        self.cmb_profile.bind("<<ComboboxSelected>>", self._on_profile_selected)

        tk.Button(frm_profile, text="儲存", width=5, command=self._save_profile).pack(side="left", padx=2)
        tk.Button(frm_profile, text="另存", width=5, command=self._save_profile_as).pack(side="left", padx=2)
        tk.Button(frm_profile, text="新增", width=5, command=self._new_profile).pack(side="left", padx=2)

        # --- Combo 列表 ---
        frm_combos = tk.LabelFrame(self.root, text="Combo 列表", padx=10, pady=5)
        frm_combos.pack(fill="both", expand=True, padx=10, pady=5)

        # Treeview
        cols = ("enabled", "name", "trigger", "action", "status", "scale")
        self.tree = ttk.Treeview(frm_combos, columns=cols, show="headings", height=8)
        self.tree.heading("enabled", text="啟用")
        self.tree.heading("name", text="名稱")
        self.tree.heading("trigger", text="觸發模式")
        self.tree.heading("action", text="動作")
        self.tree.heading("status", text="狀態")
        self.tree.heading("scale", text="匹配比例")
        self.tree.column("enabled", width=40, anchor="center", stretch=False)
        self.tree.column("name", width=180)
        self.tree.column("trigger", width=140)
        self.tree.column("action", width=150)
        self.tree.column("status", width=80, anchor="center", stretch=False)
        self.tree.column("scale", width=80, anchor="center", stretch=False)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        # Combo 按鈕列
        frm_combo_btns = tk.Frame(frm_combos)
        frm_combo_btns.pack(fill="x", pady=5)
        tk.Button(frm_combo_btns, text="新增 Combo", command=self._add_combo).pack(side="left", padx=2)
        tk.Button(frm_combo_btns, text="編輯", command=self._edit_combo).pack(side="left", padx=2)
        tk.Button(frm_combo_btns, text="刪除", command=self._delete_combo).pack(side="left", padx=2)
        tk.Button(frm_combo_btns, text="啟用/停用", command=self._toggle_combo_enabled).pack(side="left", padx=2)
        tk.Button(frm_combo_btns, text="▲ 上移", command=self._move_combo_up).pack(side="left", padx=2)
        tk.Button(frm_combo_btns, text="▼ 下移", command=self._move_combo_down).pack(side="left", padx=2)

        # --- 偵測區域 ---
        frm_region = tk.LabelFrame(self.root, text="偵測區域 (留空=全螢幕)", padx=10, pady=5)
        frm_region.pack(fill="x", padx=10, pady=5)

        window_row = tk.Frame(frm_region)
        window_row.pack(fill="x", pady=(0, 5))
        tk.Label(window_row, text="目標視窗:").pack(side="left")
        self.cmb_window = ttk.Combobox(window_row, state="readonly", width=55)
        self.cmb_window.pack(side="left", padx=5)
        self.cmb_window.bind("<<ComboboxSelected>>", self._on_window_selected)
        tk.Button(window_row, text="重新整理", command=self._refresh_windows).pack(side="left")
        tk.Label(frm_region, text="選視窗後忽略 XY 並自動跟隨；需保持可見。最小化／關閉會停止。",
                 fg="#666666").pack(anchor="w")
        self._refresh_windows()

        grid = tk.Frame(frm_region)
        grid.pack(fill="x")
        labels = ["X:", "Y:", "寬:", "高:"]
        self.region_vars: list[tk.StringVar] = []
        for i, lbl in enumerate(labels):
            tk.Label(grid, text=lbl).grid(row=0, column=i * 2, sticky="e")
            var = tk.StringVar()
            tk.Entry(grid, textvariable=var, width=7).grid(row=0, column=i * 2 + 1, padx=2)
            self.region_vars.append(var)
        tk.Button(
            grid, text="框選", command=self._select_region,
        ).grid(row=0, column=8, padx=(8, 0))

        # --- 狀態列 ---
        frm_status = tk.Frame(self.root, padx=10, pady=5)
        frm_status.pack(fill="x")

        self.lbl_status = tk.Label(frm_status, text="已停止", fg="#888888",
                                   font=("Microsoft JhengHei", 11))
        self.lbl_status.pack(side="left")
        self.lbl_cycles = tk.Label(frm_status, text="掃描: 0 次", fg="#666666")
        self.lbl_cycles.pack(side="right")
        tk.Checkbutton(
            frm_status, text="Debug 框線", variable=self._debug_mode,
            command=self._on_debug_mode_toggle,
        ).pack(side="right", padx=10)

        # --- 啟動/停止 ---
        frm_ctrl = tk.Frame(self.root, padx=10, pady=10)
        frm_ctrl.pack(fill="x")

        self.btn_toggle = tk.Button(
            frm_ctrl, text="啟動", font=("Microsoft JhengHei", 12, "bold"),
            bg="#4CAF50", fg="white", command=self.toggle, height=2,
        )
        self.btn_toggle.pack(fill="x")

        self.lbl_hotkey = tk.Label(self.root, text="快捷鍵: F6 啟動/停止", fg="#666666")
        self.lbl_hotkey.pack(pady=(0, 5))

    # ══════════════════════════════════════════════════════
    #  Profile 操作
    # ══════════════════════════════════════════════════════

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
        if self.engine.running:
            self._stop()
        try:
            self.profile = profile_manager.load_profile(path)
            self._profile_path = path
        except Exception as e:
            messagebox.showerror("錯誤", f"載入 profile 失敗:\n{e}")
            return
        self._sync_region_to_ui()
        self._refresh_combo_tree()
        self.lbl_hotkey.config(text=f"快捷鍵: {self.profile.hotkey} 啟動/停止")

    def _save_profile(self):
        self._sync_region_from_ui()
        try:
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
        name = simpledialog.askstring("另存 Profile", "輸入新名稱:", parent=self.root)
        if not name:
            return
        self.profile.name = name
        self._sync_region_from_ui()
        self._profile_path = profile_manager.save_profile(self.profile)
        self._refresh_profile_list()
        paths = profile_manager.list_profiles()
        for i, p in enumerate(paths):
            if p.stem == name.replace(" ", "_"):
                self.cmb_profile.current(i)
                break

    def _new_profile(self):
        if self.engine.running:
            self._stop()
        name = simpledialog.askstring("新增 Profile", "輸入名稱:", parent=self.root)
        if not name:
            return
        self.profile = Profile(name=name)
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

    def _on_window_selected(self, event=None):
        if self.engine.running:
            self._stop()
        index = self.cmb_window.current()
        if index == 0:
            self.profile.window_title = ""
            self.profile.window_handle = self.profile.window_pid = 0
        elif 1 <= index <= len(self._windows):
            hwnd, title, pid = self._windows[index - 1]
            self.profile.window_title = title
            self.profile.window_handle, self.profile.window_pid = hwnd, pid

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
        values = []
        for var in self.region_vars:
            val = var.get().strip()
            if not val:
                self.profile.scan_region = None
                return
            try:
                values.append(int(val))
            except ValueError:
                self.profile.scan_region = None
                return
        if len(values) == 4:
            self.profile.scan_region = tuple(values)

    # ══════════════════════════════════════════════════════
    #  Combo 列表
    # ══════════════════════════════════════════════════════

    def _refresh_combo_tree(self):
        self.tree.delete(*self.tree.get_children())
        for combo in self.profile.combos:
            enabled_str = "V" if combo.enabled else "-"
            trigger_str = TRIGGER_LABELS.get(combo.trigger, "?")
            action_str = self._action_summary(combo.action)
            # debug 模式：顯示信心度；否則顯示活躍狀態
            if self._debug_mode.get() and combo.id in self._debug_data:
                found, conf = self._debug_data[combo.id][:2]
                status_str = f"{conf:.2f} O" if found else f"{conf:.2f} X"
            else:
                active = self._combo_active.get(combo.id, False)
                status_str = ">>>" if active else ""
            self.tree.insert("", "end", iid=combo.id, values=(
                enabled_str, combo.name, trigger_str, action_str, status_str,
                self._match_scale_label(combo)
            ))

    def _match_scale_label(self, combo: Combo) -> str:
        scale = self.engine.get_match_scale(combo.image_path)
        return f"{scale * 100:.1f}%" if scale is not None else "—"

    def _action_summary(self, action: Action) -> str:
        label = ACTION_LABELS.get(action.action_type, "?")
        if action.action_type in (ActionType.CLICK, ActionType.RAPID_CLICK, ActionType.HOLD_MOUSE):
            btn = MOUSE_BTN_LABELS.get(action.mouse_button, "?")
            return f"{label} ({btn})"
        else:
            return f"{label} ({action.key or '?'})"

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
        if self.engine.running:
            self._stop()
        combo = Combo()
        if ComboEditor(self.root, combo).result:
            self.profile.combos.append(combo)
            self._refresh_combo_tree()
            self._auto_save()

    def _edit_combo(self):
        if self.engine.running:
            self._stop()
        combo = self._get_selected_combo()
        if combo and ComboEditor(self.root, combo).result:
            self._refresh_combo_tree()
            self._auto_save()

    def _on_tree_double_click(self, event):
        self._edit_combo()

    def _delete_combo(self):
        if self.engine.running:
            self._stop()
        combo = self._get_selected_combo()
        if combo:
            self.profile.combos = [c for c in self.profile.combos if c.id != combo.id]
            self._refresh_combo_tree()
            self._auto_save()

    def _toggle_combo_enabled(self):
        if self.engine.running:
            self._stop()
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
        enabled = [c for c in self.profile.combos if c.enabled and
                   (c.image_path or c.verify_text or c.trigger == TriggerMode.POSITION_CONTROL)]
        if not enabled:
            messagebox.showwarning("提醒", "請啟用至少一個已設定圖片、文字或位置控制的 Combo。")
            return
        self._sync_region_from_ui()
        self.engine.set_profile(self.profile)
        try:
            self.engine.start()
        except ValueError as e:
            messagebox.showerror("設定錯誤", str(e))
            return
        if not self.engine.running:
            return
        self.btn_toggle.config(text="停止", bg="#F44336")
        if self._debug_mode.get():
            self._show_debug_overlay()

    def _stop(self):
        self.engine.stop()
        self._combo_active.clear()
        self._debug_data.clear()
        self._hide_debug_overlay()
        self.btn_toggle.config(text="啟動", bg="#4CAF50")
        self._refresh_combo_tree()

    # ══════════════════════════════════════════════════════
    #  Debug 覆蓋層管理
    # ══════════════════════════════════════════════════════

    def _on_debug_mode_toggle(self):
        if self._debug_mode.get() and self.engine.running:
            self._show_debug_overlay()
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
            if combo.id in self._debug_data:
                found, conf, rect = self._debug_data[combo.id]
                if rect:
                    x, y, w, h = rect
                    rects.append((x, y, w, h, combo.name, found, conf))
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
                self.lbl_cycles.config(text=f"掃描: {count} 次")
                if self._debug_mode.get() and self._debug_data:
                    for cid, data in list(self._debug_data.items()):
                        found, conf = data[0], data[1]
                        if self.tree.exists(cid):
                            s = f"{conf:.2f} O" if found else f"{conf:.2f} X"
                            self.tree.set(cid, "status", s)
                    self._update_debug_overlay()
            elif kind == "status":
                _, running = msg
                if running:
                    self.lbl_status.config(text="運行中", fg="#4CAF50")
                else:
                    self.lbl_status.config(text="已停止", fg="#888888")
                    self.btn_toggle.config(text="啟動", bg="#4CAF50")
                    self._combo_active.clear()
                    self._debug_data.clear()
                    self._hide_debug_overlay()
                    self._refresh_combo_tree()
            elif kind == "hotkey":
                self.toggle()
        for combo in self.profile.combos:
            if self.tree.exists(combo.id):
                self.tree.set(combo.id, "scale", self._match_scale_label(combo))
        self.root.after(150, self._poll_queue)

    # ══════════════════════════════════════════════════════
    #  視窗生命週期
    # ══════════════════════════════════════════════════════

    def run(self):
        self.root.mainloop()

    def on_close(self):
        if self.engine.running:
            self.engine.stop()
        self.root.destroy()


# ══════════════════════════════════════════════════════════
#  Combo 編輯對話框
# ══════════════════════════════════════════════════════════

class ComboEditor:
    """彈出視窗，編輯單一 Combo 的所有屬性"""

    def __init__(self, parent, combo: Combo):
        self.combo = combo
        self.result = False

        self.win = tk.Toplevel(parent)
        self.win.title("編輯 Combo")
        self.win.transient(parent)
        self.win.geometry("420x740")
        self.win.minsize(380, 500)
        self.win.resizable(True, True)
        self.win.grab_set()
        self.win.attributes("-topmost", True)

        pad = {"padx": 10, "pady": 3}

        # 確定/取消 — 先 pack 到底部，確保任何視窗大小都不被擠掉
        frm_btns = tk.Frame(self.win)
        frm_btns.pack(side="bottom", pady=10)
        tk.Button(frm_btns, text="確定", width=10, command=self._ok).pack(side="left", padx=10)
        tk.Button(frm_btns, text="取消", width=10, command=self.win.destroy).pack(side="left", padx=10)

        # ── 可捲動內容區 ──────────────────────────────────────
        _canvas = tk.Canvas(self.win, highlightthickness=0)
        _vsb = ttk.Scrollbar(self.win, orient="vertical", command=_canvas.yview)
        _canvas.configure(yscrollcommand=_vsb.set)
        _vsb.pack(side="right", fill="y")
        _canvas.pack(side="left", fill="both", expand=True)
        sf = tk.Frame(_canvas)
        _win_id = _canvas.create_window((0, 0), window=sf, anchor="nw")
        sf.bind("<Configure>", lambda e: _canvas.configure(scrollregion=_canvas.bbox("all")))
        _canvas.bind("<Configure>", lambda e: _canvas.itemconfigure(_win_id, width=e.width))
        self.win.bind("<MouseWheel>", lambda e: _canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        # 名稱
        tk.Label(sf, text="名稱:").pack(anchor="w", **pad)
        self.var_name = tk.StringVar(value=combo.name)
        tk.Entry(sf, textvariable=self.var_name, width=40).pack(fill="x", **pad)

        # 圖片
        image_start = len(sf.winfo_children())
        frm_img = tk.Frame(sf)
        frm_img.pack(fill="x", **pad)
        tk.Label(frm_img, text="觸發圖片:").pack(side="left")
        self.var_image = tk.StringVar(value=combo.image_path)
        tk.Entry(frm_img, textvariable=self.var_image, width=22).pack(side="left", padx=5, fill="x", expand=True)
        tk.Button(frm_img, text="瀏覽", command=self._browse_image).pack(side="left", padx=2)
        tk.Button(frm_img, text="貼上", command=self._paste_image).pack(side="left", padx=2)

        # 圖片預覽（文字 + 去背按鈕 + 縮圖）
        frm_preview = tk.Frame(sf)
        frm_preview.pack(fill="x", padx=10)
        self.btn_rmbg = tk.Button(
            frm_preview, text="去背", command=self._remove_bg,
        )
        self.btn_rmbg.pack(side="right", padx=5)
        self.btn_rmbg.pack_forget()  # 初始隱藏，有圖才顯示
        self.lbl_preview = tk.Label(frm_preview, text="", fg="#888888", anchor="w")
        self.lbl_preview.pack(side="left", fill="x", expand=True)

        self._preview_photo = None  # 防止 GC 回收 PhotoImage
        self.lbl_thumb = tk.Label(sf, relief="groove")
        self.lbl_thumb.pack(anchor="w", padx=10, pady=2)
        self.lbl_thumb.pack_forget()  # 初始隱藏，有圖才顯示

        # 若已有圖片，顯示預覽
        if combo.image_path:
            self._update_preview(combo.image_path)

        # Ctrl+V 快捷鍵
        self.win.bind("<Control-v>", lambda e: self._paste_image())

        # 比對閾值
        frm_thresh = tk.Frame(sf)
        frm_thresh.pack(fill="x", **pad)
        tk.Label(frm_thresh, text="圖片閾值:").pack(side="left")
        self.var_threshold = tk.StringVar(value=str(combo.match_threshold))
        tk.Entry(frm_thresh, textvariable=self.var_threshold, width=8).pack(side="left", padx=5)
        tk.Label(frm_thresh, text="(0.0~1.0, 越高越嚴格)").pack(side="left")

        # 文字驗證（可選）— 分兩行避免溢出
        frm_vtext1 = tk.Frame(sf)
        frm_vtext1.pack(fill="x", **pad)
        tk.Label(frm_vtext1, text="驗證文字:").pack(side="left")
        self.var_verify_text = tk.StringVar(value=combo.verify_text)
        tk.Entry(frm_vtext1, textvariable=self.var_verify_text, width=20).pack(side="left", padx=5, fill="x", expand=True)
        tk.Label(frm_vtext1, text="(留空=不驗證)").pack(side="left")

        frm_vtext1b = tk.Frame(sf)
        frm_vtext1b.pack(fill="x", padx=10, pady=1)
        tk.Button(frm_vtext1b, text="螢幕截取 OCR", command=self._ocr_from_screen).pack(side="left")

        frm_vtext2 = tk.Frame(sf)
        frm_vtext2.pack(fill="x", **pad)
        tk.Label(frm_vtext2, text="文字閾值:").pack(side="left")
        self.var_text_threshold = tk.StringVar(value=str(combo.text_threshold))
        tk.Entry(frm_vtext2, textvariable=self.var_text_threshold, width=8).pack(side="left", padx=5)
        tk.Label(frm_vtext2, text="(0.0~1.0, OCR 信心度)").pack(side="left")

        # 匹配模式 + 吻合比例
        frm_vtext3 = tk.Frame(sf)
        frm_vtext3.pack(fill="x", **pad)
        tk.Label(frm_vtext3, text="匹配模式:").pack(side="left")
        self.var_text_match_mode = tk.StringVar(value=combo.text_match_mode.value)
        match_labels = {"contains": "包含", "exact": "完全符合", "partial": "部分符合"}
        om = ttk.OptionMenu(
            frm_vtext3, self.var_text_match_mode,
            combo.text_match_mode.value,
            *[m.value for m in TextMatchMode],
            command=lambda _: self._on_match_mode_change(),
        )
        om.pack(side="left", padx=5)
        # 吻合比例（僅部分符合時顯示）
        self.lbl_match_ratio = tk.Label(frm_vtext3, text="吻合比例:")
        self.var_text_match_ratio = tk.StringVar(value=str(combo.text_match_ratio))
        self.ent_match_ratio = tk.Entry(
            frm_vtext3, textvariable=self.var_text_match_ratio, width=5,
        )
        self.lbl_match_ratio_hint = tk.Label(frm_vtext3, text="(0.0~1.0)")
        self._on_match_mode_change()  # 初始顯示/隱藏

        # 觸發模式
        self.image_widgets = sf.winfo_children()[image_start:]
        self.position_image_widgets = (frm_img, frm_preview, frm_thresh)
        self.image_pack = {w: w.pack_info() for w in self.image_widgets if w.winfo_manager() == "pack"}
        self.lbl_trigger = tk.Label(sf, text="觸發模式:")
        self.lbl_trigger.pack(anchor="w", **pad)
        self.var_trigger = tk.StringVar(value=combo.trigger.value)
        frm_trigger = tk.Frame(sf)
        frm_trigger.pack(fill="x", **pad)
        for mode in TriggerMode:
            tk.Radiobutton(
                frm_trigger, text=TRIGGER_LABELS[mode],
                variable=self.var_trigger, value=mode.value,
                command=self._on_action_type_change,
            ).pack(anchor="w")

        # 動作類型
        self.lbl_action = tk.Label(sf, text="動作:")
        self.lbl_action.pack(anchor="w", **pad)
        self.var_action_type = tk.StringVar(value=combo.action.action_type.value)
        frm_at = tk.Frame(sf)
        self.frm_action_types = frm_at
        frm_at.pack(fill="x", **pad)
        action_types = list(ActionType)
        for i, at in enumerate(action_types):
            tk.Radiobutton(
                frm_at, text=ACTION_LABELS[at],
                variable=self.var_action_type, value=at.value,
                command=self._on_action_type_change,
            ).grid(row=i // 3, column=i % 3, sticky="w", padx=5)

        # 動作參數
        self.frm_params = tk.LabelFrame(sf, text="動作參數", padx=10, pady=5)
        self.frm_params.pack(fill="x", padx=10, pady=5)
        self._build_position_ui(sf)

        # 滑鼠按鍵
        self.frm_mouse_btn = tk.Frame(self.frm_params)
        tk.Label(self.frm_mouse_btn, text="滑鼠鍵:").pack(side="left")
        self.var_mouse_btn = tk.StringVar(value=combo.action.mouse_button.value)
        for mb in MouseButton:
            tk.Radiobutton(
                self.frm_mouse_btn, text=MOUSE_BTN_LABELS[mb],
                variable=self.var_mouse_btn, value=mb.value,
            ).pack(side="left", padx=3)

        # 鍵盤按鍵
        self.frm_key = tk.Frame(self.frm_params)
        tk.Label(self.frm_key, text="鍵盤鍵:").pack(side="left")
        self.var_key = tk.StringVar(value=combo.action.key)
        tk.Entry(self.frm_key, textvariable=self.var_key, width=15).pack(side="left", padx=5)
        tk.Label(self.frm_key, text="(如: space, e, f)").pack(side="left")

        # 延遲
        self.frm_delay = tk.Frame(self.frm_params)
        tk.Label(self.frm_delay, text="延遲(秒):").pack(side="left")
        self.var_delay_min = tk.StringVar(value=str(combo.action.delay_min))
        self.var_delay_max = tk.StringVar(value=str(combo.action.delay_max))
        tk.Entry(self.frm_delay, textvariable=self.var_delay_min, width=6).pack(side="left", padx=2)
        tk.Label(self.frm_delay, text="~").pack(side="left")
        tk.Entry(self.frm_delay, textvariable=self.var_delay_max, width=6).pack(side="left", padx=2)

        # 持續時間
        self.frm_duration = tk.Frame(self.frm_params)
        tk.Label(self.frm_duration, text="持續(秒):").pack(side="left")
        self.var_duration = tk.StringVar(value=str(combo.action.duration))
        tk.Entry(self.frm_duration, textvariable=self.var_duration, width=8).pack(side="left", padx=5)

        # 間隔 / 放開間隔
        self.frm_interval = tk.Frame(self.frm_params)
        self._lbl_interval = tk.Label(self.frm_interval, text="間隔(秒):")
        self._lbl_interval.pack(side="left")
        self.var_interval_min = tk.StringVar(value=str(combo.action.interval_min))
        self.var_interval_max = tk.StringVar(value=str(combo.action.interval_max))
        tk.Entry(self.frm_interval, textvariable=self.var_interval_min, width=6).pack(side="left", padx=2)
        tk.Label(self.frm_interval, text="~").pack(side="left")
        tk.Entry(self.frm_interval, textvariable=self.var_interval_max, width=6).pack(side="left", padx=2)

        self._on_action_type_change()

        self.win.wait_window()

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
            self.lbl_preview.config(fg="#333333")
        else:
            self.lbl_preview.config(text="螢幕上找不到模板，請先確認遊戲畫面已顯示", fg="#cc0000")
            return
        # 還原預覽文字
        path = image_path
        try:
            from PIL import Image
            img = Image.open(path)
            w, h = img.size
            self.lbl_preview.config(text=f"{Path(path).name} ({w}x{h})", fg="#333333")
        except Exception:
            pass

    def _update_preview(self, path: str, auto_ocr: bool = False):
        """載入圖片並顯示縮圖預覽。auto_ocr=True 時自動 OCR 帶入驗證文字。"""
        try:
            img = Image.open(path)
        except Exception:
            self.lbl_thumb.pack_forget()
            self.btn_rmbg.pack_forget()
            return
        w, h = img.size
        self.lbl_preview.config(
            text=f"{Path(path).name} ({w}x{h})", fg="#333333",
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
        if hasattr(self, "frm_position"):
            self._preview_position()

        # 更換圖片時自動 OCR 讀取文字帶入
        if auto_ocr and hasattr(self, "var_verify_text"):
            self.lbl_preview.config(text=f"{Path(path).name} ({w}x{h}) OCR 讀取中...")
            self.win.update_idletasks()
            text = ocr_read_image(path)
            self.var_verify_text.set(text)
            self.lbl_preview.config(
                text=f"{Path(path).name} ({w}x{h})", fg="#333333",
            )

    def _browse_image(self):
        path = filedialog.askopenfilename(
            title="選擇觸發圖片",
            initialdir=str(profile_manager.TEMPLATES_DIR),
            filetypes=[("圖片", "*.png *.jpg *.bmp"), ("所有檔案", "*.*")],
        )
        if path:
            self.var_image.set(path)
            self._update_preview(path, auto_ocr=True)

    def _paste_image(self):
        """從剪貼簿貼上圖片，自動存到 templates/ 目錄"""
        try:
            img = ImageGrab.grabclipboard()
        except Exception:
            img = None

        if img is None:
            self.lbl_preview.config(text="剪貼簿中沒有圖片", fg="red")
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
        self._update_preview(str(save_path), auto_ocr=True)

    def _remove_bg(self):
        """開啟色彩去背對話框"""
        image_path = self.var_image.get().strip()
        if not image_path:
            return
        dlg = RemoveBgDialog(self.win, image_path)
        if dlg.result_path:
            self.var_image.set(dlg.result_path)
            self._update_preview(dlg.result_path, auto_ocr=True)

    def _build_position_ui(self, parent):
        settings = self.combo.position_control
        self.frm_position = tk.LabelFrame(parent, text="位置控制設定", padx=8, pady=5)
        tk.Label(self.frm_position, text="直接框選觸發圖片內的路徑，排除固定同色區塊。",
                 wraplength=340, justify="left").pack(anchor="w")
        row = tk.Frame(self.frm_position)
        row.pack(fill="x")
        self.position_region_vars = [tk.StringVar(value=str(v) if v is not None else "")
                                     for v in (settings.region if settings.region_space == "template"
                                               and settings.region else (None,) * 4)]
        for label, var in zip(("X", "Y", "寬", "高"), self.position_region_vars):
            tk.Label(row, text=label).pack(side="left")
            tk.Entry(row, textvariable=var, width=5).pack(side="left")
        tk.Button(self.frm_position, text="框選觸發圖片區塊", command=self._select_position_region).pack(anchor="w")
        self.var_position_color = tk.StringVar(value="#%02x%02x%02x" % settings.color)
        self.var_position_tolerance = tk.StringVar(value=str(settings.tolerance))
        self.var_position_area = tk.StringVar(value=str(settings.min_area))
        self.var_position_press = tk.StringVar(value=f"{settings.press_at * 100:g}")
        self.var_position_release = tk.StringVar(value=f"{settings.release_at * 100:g}")
        for label, var in (("標記顏色 (#RRGGBB)", self.var_position_color),
                           ("顏色容差 (0~90)", self.var_position_tolerance),
                           ("最小面積 (像素)", self.var_position_area),
                           ("按住位置 (%)", self.var_position_press),
                           ("放開位置 (%)", self.var_position_release)):
            row = tk.Frame(self.frm_position)
            row.pack(fill="x")
            tk.Label(row, text=label, width=22, anchor="w").pack(side="left")
            tk.Entry(row, textvariable=var, width=10).pack(side="left")
        self.var_position_preview = tk.BooleanVar(value=True)
        self._position_preview_job = None
        tk.Checkbutton(self.frm_position, text="預覽觸發圖片區塊",
                       variable=self.var_position_preview, command=self._preview_position).pack(anchor="w")
        self.var_position_click = tk.StringVar(value="取色")
        row = tk.Frame(self.frm_position)
        row.pack(fill="x")
        for mode in ("取色", "按住線", "放開線"):
            tk.Radiobutton(row, text=mode, variable=self.var_position_click,
                           value=mode).pack(side="left")
        self.position_canvas = tk.Canvas(self.frm_position, width=340, height=170,
                                         bg="#222222", highlightthickness=0)
        self.position_canvas.pack(anchor="w")
        self.position_canvas.bind("<Button-1>", self._position_preview_click)
        self.lbl_position_status = tk.Label(self.frm_position, text="先框選觸發圖片區塊，再點圖片取色或設定界線。",
                                            wraplength=340, justify="left")
        self.lbl_position_status.pack(anchor="w")
        self._position_frame = None
        self.win.bind("<Destroy>", self._cancel_position_preview, add="+")

    def _position_settings(self):
        if any(not var.get().strip() for var in self.position_region_vars):
            raise ValueError("請先框選觸發圖片的偵測區塊。")
        value = self.var_position_color.get().strip()
        if len(value) != 7 or not value.startswith("#"):
            raise ValueError("標記顏色格式必須是 #RRGGBB。")
        settings = PositionControl(
            region=tuple(int(var.get()) for var in self.position_region_vars),
            color=tuple(int(value[i:i + 2], 16) for i in (1, 3, 5)),
            tolerance=int(self.var_position_tolerance.get()),
            min_area=int(self.var_position_area.get()),
            press_at=self._to_float(self.var_position_press.get()) / 100,
            release_at=self._to_float(self.var_position_release.get()) / 100)
        settings.validate()
        try:
            with Image.open(self.var_image.get().strip()) as image:
                tw, th = image.size
        except (OSError, ValueError):
            raise ValueError("請先指定有效的觸發圖片。") from None
        x, y, w, h = settings.region
        if x < 0 or y < 0 or x + w > tw or y + h > th or w * h == tw * th:
            raise ValueError("請在觸發圖片內框選局部區域，保留部分圖片供定位使用。")
        return settings

    def _select_position_region(self):
        try:
            with Image.open(self.var_image.get().strip()) as image:
                image = image.convert("RGB")
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
        if event.widget == self.win and self._position_preview_job is not None:
            self.win.after_cancel(self._position_preview_job)
            self._position_preview_job = None

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
                frame = np.array(image.convert("RGB").crop((x, y, x + w, y + h)))
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
            for fraction, color in ((settings.press_at, "#00ff88"), (settings.release_at, "#ff5555")):
                x = fraction * (width - 1)
                canvas.create_line(x, 0, x, height, fill=color, width=2)
            marker = find_color_marker(frame, settings)
            if marker:
                (cx, cy), _ = marker
                px, py = cx * scale, cy * scale
                canvas.create_oval(px - 4, py - 4, px + 4, py + 4, outline="cyan", width=2)
                self.lbl_position_status.config(text=f"標記位置：{cx / max(1, w - 1):.1%}；綠線按住、紅線放開。")
            else:
                self.lbl_position_status.config(text="未找到唯一標記：調整顏色／容差／區域。此時會放開。")
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
        if mode == "取色":
            x = min(frame.shape[1] - 1, int(event.x * frame.shape[1] / width))
            y = min(frame.shape[0] - 1, int(event.y * frame.shape[0] / height))
            self.var_position_color.set("#%02x%02x%02x" % tuple(frame[y, x]))
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
        position_mode = trigger == TriggerMode.POSITION_CONTROL.value
        for widget in self.image_widgets:
            widget.pack_forget()
        for widget, options in self.image_pack.items():
            if not position_mode or widget in self.position_image_widgets:
                widget.pack(**{**options, "before": self.lbl_trigger})
        if not position_mode:
            if self.var_image.get().strip():
                self._update_preview(self.var_image.get().strip())
                self.lbl_thumb.pack(before=self.lbl_trigger, anchor="w", padx=10, pady=2)
        self.frm_position.pack_forget()
        self.lbl_action.pack_forget()
        self.frm_action_types.pack_forget()
        if position_mode:
            self.var_action_type.set(ActionType.HOLD_MOUSE.value)
            self.frm_position.pack(fill="x", padx=10, pady=5, before=self.frm_params)
            self.var_position_preview.set(True)
            self._preview_position()
        else:
            self.lbl_action.pack(anchor="w", padx=10, pady=3, before=self.frm_params)
            self.frm_action_types.pack(fill="x", padx=10, pady=3, before=self.frm_params)
            self.var_position_preview.set(False)
        at = self.var_action_type.get()

        is_mouse = at in ("click", "rapid_click", "hold_mouse")
        is_key = at in ("key_press", "rapid_key", "hold_key")
        is_rapid = at in ("rapid_click", "rapid_key")
        is_hold = at in ("hold_mouse", "hold_key")
        is_while_visible = trigger == "while_visible"

        # rapid/hold 都需要顯示持續時間
        # hold + while_visible：持續(秒) = 每次按住多久，放開間隔 = 放開多久後再按
        need_duration = is_rapid or is_hold

        # 先全部隱藏再按需顯示
        for w in (self.frm_mouse_btn, self.frm_key, self.frm_delay,
                  self.frm_duration, self.frm_interval):
            w.pack_forget()

        if is_mouse:
            self.frm_mouse_btn.pack(fill="x", pady=2)
        if is_key:
            self.frm_key.pack(fill="x", pady=2)
        if not position_mode:
            self.frm_delay.pack(fill="x", pady=2)
        if need_duration and not position_mode:
            self.frm_duration.pack(fill="x", pady=2)
        if (is_rapid or is_hold) and not position_mode:
            self._lbl_interval.config(text="放開間隔(秒):" if is_hold else "間隔(秒):")
            self.frm_interval.pack(fill="x", pady=2)

    @staticmethod
    def _to_float(s: str, default: float = 0.0) -> float:
        """解析浮點數，自動處理中文句號等替代字元"""
        s = s.strip().replace("。", ".").replace("，", ".").replace(",", ".")
        return float(s) if s else default

    def _ok(self):
        try:
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
            position_settings = self._position_settings() if trigger == TriggerMode.POSITION_CONTROL else self.combo.position_control

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

            # 只在欄位可見時才解析，否則保留原值
            duration = (self._to_float(self.var_duration.get(), 1.0)
                        if need_duration else self.combo.action.duration)
            interval_min = (self._to_float(self.var_interval_min.get(), 0.05)
                            if is_rapid or is_hold else self.combo.action.interval_min)
            interval_max = (self._to_float(self.var_interval_max.get(), 0.15)
                            if is_rapid or is_hold else self.combo.action.interval_max)

            # ── 全部合法才寫入（避免 partial update）──
            self.combo.name = name
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
            sw // 2, 30, text="拖拉框選偵測區域，ESC 取消",
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

    def __init__(self, parent, image):
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
        tk.Label(self.overlay, text="拖曳框選箭頭移動路徑；保留部分圖片供定位使用。ESC 取消。",
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

        tk.Label(
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
        frm_tol = tk.Frame(self.win)
        frm_tol.pack(fill="x", padx=10, pady=2)
        tk.Label(frm_tol, text="容差:").pack(side="left")
        self.var_tolerance = tk.IntVar(value=30)
        tk.Scale(
            frm_tol, from_=0, to=100, orient="horizontal",
            variable=self.var_tolerance, command=self._on_tolerance_change,
        ).pack(side="left", fill="x", expand=True)

        # ── 狀態 ──
        self.lbl_info = tk.Label(self.win, text="尚未選取背景色", fg="#888888")
        self.lbl_info.pack(padx=10)

        # ── 按鈕 ──
        frm_btns = tk.Frame(self.win)
        frm_btns.pack(pady=10)
        tk.Button(frm_btns, text="確定", width=10, command=self._ok).pack(
            side="left", padx=10,
        )
        tk.Button(frm_btns, text="取消", width=10, command=self.win.destroy).pack(
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
                text=f"背景色: RGB({r}, {g}, {b})", fg="#333333",
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
        self.win.attributes("-transparentcolor", "black")
        self.win.configure(bg="black")

        self.canvas = tk.Canvas(
            self.win, bg="black", highlightthickness=0,
            width=sw, height=sh,
        )
        self.canvas.pack()

        # 設為點擊穿透，不擋住滑鼠操作
        self.win.update_idletasks()
        self._set_click_through()

    def _set_click_through(self):
        try:
            import ctypes
            hwnd = self.win.winfo_id()
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT)
        except Exception:
            pass

    def update_rects(self, rects: list):
        """更新框線。rects: list of (x, y, w, h, name, found, confidence)"""
        self.canvas.delete("all")
        for x, y, w, h, name, found, conf in rects:
            color = "#00FF00" if found else "#FF4444"
            # 框線
            self.canvas.create_rectangle(
                x, y, x + w, y + h,
                outline=color, width=3,
            )
            # 標籤背景（用色塊遮住以利閱讀）
            label = f" {name}  {conf:.2f} "
            self.canvas.create_rectangle(
                x, y - 20, x + len(label) * 7, y,
                fill=color, outline="",
            )
            self.canvas.create_text(
                x + 3, y - 10, text=label.strip(),
                fill="black", anchor="w",
                font=("Arial", 10, "bold"),
            )

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass
