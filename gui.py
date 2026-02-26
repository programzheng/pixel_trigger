"""GUI 介面 — Combo 管理 + Hook 引擎控制"""

from __future__ import annotations

import os
import collections
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from pathlib import Path

from PIL import ImageGrab

import profile_manager
from automation import HookEngine
from models import (
    Action,
    ActionType,
    Combo,
    MouseButton,
    Profile,
    TriggerMode,
)

# ── 顯示用對照表 ─────────────────────────────────────────

TRIGGER_LABELS = {
    TriggerMode.ON_APPEAR: "出現時觸發一次",
    TriggerMode.WHILE_VISIBLE: "可見期間持續執行",
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
        self.root.geometry("700x660")
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
        # debug 模式：顯示即時信心度
        self._debug_mode = tk.BooleanVar(value=False)
        # combo_id → (found, confidence) 最新偵測結果
        self._debug_data: dict[str, tuple[bool, float]] = {}

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
        cols = ("enabled", "name", "trigger", "action", "status")
        self.tree = ttk.Treeview(frm_combos, columns=cols, show="headings", height=8)
        self.tree.heading("enabled", text="啟用")
        self.tree.heading("name", text="名稱")
        self.tree.heading("trigger", text="觸發模式")
        self.tree.heading("action", text="動作")
        self.tree.heading("status", text="狀態")
        self.tree.column("enabled", width=40, anchor="center", stretch=False)
        self.tree.column("name", width=180)
        self.tree.column("trigger", width=140)
        self.tree.column("action", width=150)
        self.tree.column("status", width=80, anchor="center", stretch=False)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        # Combo 按鈕列
        frm_combo_btns = tk.Frame(frm_combos)
        frm_combo_btns.pack(fill="x", pady=5)
        tk.Button(frm_combo_btns, text="新增 Combo", command=self._add_combo).pack(side="left", padx=2)
        tk.Button(frm_combo_btns, text="編輯", command=self._edit_combo).pack(side="left", padx=2)
        tk.Button(frm_combo_btns, text="刪除", command=self._delete_combo).pack(side="left", padx=2)
        tk.Button(frm_combo_btns, text="啟用/停用", command=self._toggle_combo_enabled).pack(side="left", padx=2)

        # --- 偵測區域 ---
        frm_region = tk.LabelFrame(self.root, text="偵測區域 (留空=全螢幕)", padx=10, pady=5)
        frm_region.pack(fill="x", padx=10, pady=5)

        grid = tk.Frame(frm_region)
        grid.pack(fill="x")
        labels = ["X:", "Y:", "寬:", "高:"]
        self.region_vars: list[tk.StringVar] = []
        for i, lbl in enumerate(labels):
            tk.Label(grid, text=lbl).grid(row=0, column=i * 2, sticky="e")
            var = tk.StringVar()
            tk.Entry(grid, textvariable=var, width=7).grid(row=0, column=i * 2 + 1, padx=2)
            self.region_vars.append(var)

        # --- 狀態列 ---
        frm_status = tk.Frame(self.root, padx=10, pady=5)
        frm_status.pack(fill="x")

        self.lbl_status = tk.Label(frm_status, text="已停止", fg="#888888",
                                   font=("Microsoft JhengHei", 11))
        self.lbl_status.pack(side="left")
        self.lbl_cycles = tk.Label(frm_status, text="掃描: 0 次", fg="#666666")
        self.lbl_cycles.pack(side="right")
        tk.Checkbutton(
            frm_status, text="Debug", variable=self._debug_mode,
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
        profile_manager.save_profile(self.profile)
        self._refresh_profile_list()
        paths = profile_manager.list_profiles()
        for i, p in enumerate(paths):
            if p.stem == name.replace(" ", "_"):
                self.cmb_profile.current(i)
                break

    def _new_profile(self):
        name = simpledialog.askstring("新增 Profile", "輸入名稱:", parent=self.root)
        if not name:
            return
        self.profile = Profile(name=name)
        self._sync_region_to_ui()
        self._refresh_combo_tree()
        profile_manager.save_profile(self.profile)
        self._refresh_profile_list()

    # ══════════════════════════════════════════════════════
    #  偵測區域
    # ══════════════════════════════════════════════════════

    def _sync_region_to_ui(self):
        if self.profile.scan_region:
            for i, val in enumerate(self.profile.scan_region):
                self.region_vars[i].set(str(val))
        else:
            for var in self.region_vars:
                var.set("")

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
                found, conf = self._debug_data[combo.id]
                status_str = f"{conf:.2f} O" if found else f"{conf:.2f} X"
            else:
                active = self._combo_active.get(combo.id, False)
                status_str = ">>>" if active else ""
            self.tree.insert("", "end", iid=combo.id, values=(
                enabled_str, combo.name, trigger_str, action_str, status_str
            ))

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
        combo = Combo()
        if ComboEditor(self.root, combo).result:
            self.profile.combos.append(combo)
            self._refresh_combo_tree()
            self._auto_save()

    def _edit_combo(self):
        combo = self._get_selected_combo()
        if combo and ComboEditor(self.root, combo).result:
            self._refresh_combo_tree()
            self._auto_save()

    def _on_tree_double_click(self, event):
        self._edit_combo()

    def _delete_combo(self):
        combo = self._get_selected_combo()
        if combo:
            self.profile.combos = [c for c in self.profile.combos if c.id != combo.id]
            self._refresh_combo_tree()
            self._auto_save()

    def _toggle_combo_enabled(self):
        combo = self._get_selected_combo()
        if combo:
            combo.enabled = not combo.enabled
            self._refresh_combo_tree()
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
        enabled = [c for c in self.profile.combos if c.enabled and c.image_path]
        if not enabled:
            messagebox.showwarning("提醒", "沒有可用的 Combo！\n請確認至少有一個 Combo 已啟用且設定了圖片。")
            return
        self._sync_region_from_ui()
        self.engine.set_profile(self.profile)
        self.engine.start()
        self.btn_toggle.config(text="停止", bg="#F44336")

    def _stop(self):
        self.engine.stop()
        self._combo_active.clear()
        self._debug_data.clear()
        self.btn_toggle.config(text="啟動", bg="#4CAF50")
        self._refresh_combo_tree()

    # ══════════════════════════════════════════════════════
    #  引擎回呼 — worker thread 只往 Queue 塞訊息
    # ══════════════════════════════════════════════════════

    def _on_combo_triggered(self, combo_id: str, active: bool):
        self._msg_queue.append(("triggered", combo_id, active))

    def _on_debug_info(self, combo_id: str, found: bool, confidence: float):
        # 直接寫 dict（CPython GIL 保證 dict 寫入原子性）
        self._debug_data[combo_id] = (found, confidence)

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
                    cur = self.tree.item(combo_id, "values")
                    if cur:
                        s = ">>>" if active else ""
                        self.tree.item(combo_id, values=(*cur[:4], s))
            elif kind == "cycle":
                _, count = msg
                self.lbl_cycles.config(text=f"掃描: {count} 次")
                if self._debug_mode.get() and self._debug_data:
                    for cid, (found, conf) in list(self._debug_data.items()):
                        if self.tree.exists(cid):
                            cur = self.tree.item(cid, "values")
                            if cur:
                                s = f"{conf:.2f} O" if found else f"{conf:.2f} X"
                                self.tree.item(cid, values=(*cur[:4], s))
            elif kind == "status":
                _, running = msg
                if running:
                    self.lbl_status.config(text="運行中", fg="#4CAF50")
                else:
                    self.lbl_status.config(text="已停止", fg="#888888")
            elif kind == "hotkey":
                self.toggle()
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
        self.win.geometry("420x500")
        self.win.resizable(False, False)
        self.win.grab_set()
        self.win.attributes("-topmost", True)

        pad = {"padx": 10, "pady": 3}

        # 名稱
        tk.Label(self.win, text="名稱:").pack(anchor="w", **pad)
        self.var_name = tk.StringVar(value=combo.name)
        tk.Entry(self.win, textvariable=self.var_name, width=40).pack(fill="x", **pad)

        # 圖片
        frm_img = tk.Frame(self.win)
        frm_img.pack(fill="x", **pad)
        tk.Label(frm_img, text="觸發圖片:").pack(side="left")
        self.var_image = tk.StringVar(value=combo.image_path)
        tk.Entry(frm_img, textvariable=self.var_image, width=22).pack(side="left", padx=5)
        tk.Button(frm_img, text="瀏覽", command=self._browse_image).pack(side="left", padx=2)
        tk.Button(frm_img, text="貼上", command=self._paste_image).pack(side="left", padx=2)

        # 圖片預覽
        self.lbl_preview = tk.Label(self.win, text="", fg="#888888")
        self.lbl_preview.pack(anchor="w", padx=10)

        # Ctrl+V 快捷鍵
        self.win.bind("<Control-v>", lambda e: self._paste_image())

        # 比對閾值
        frm_thresh = tk.Frame(self.win)
        frm_thresh.pack(fill="x", **pad)
        tk.Label(frm_thresh, text="比對閾值:").pack(side="left")
        self.var_threshold = tk.StringVar(value=str(combo.match_threshold))
        tk.Entry(frm_thresh, textvariable=self.var_threshold, width=8).pack(side="left", padx=5)
        tk.Label(frm_thresh, text="(0.0~1.0, 越高越嚴格)").pack(side="left")

        # 觸發模式
        tk.Label(self.win, text="觸發模式:").pack(anchor="w", **pad)
        self.var_trigger = tk.StringVar(value=combo.trigger.value)
        frm_trigger = tk.Frame(self.win)
        frm_trigger.pack(fill="x", **pad)
        for mode in TriggerMode:
            tk.Radiobutton(
                frm_trigger, text=TRIGGER_LABELS[mode],
                variable=self.var_trigger, value=mode.value,
                command=self._on_action_type_change,
            ).pack(anchor="w")

        # 動作類型
        tk.Label(self.win, text="動作:").pack(anchor="w", **pad)
        self.var_action_type = tk.StringVar(value=combo.action.action_type.value)
        frm_at = tk.Frame(self.win)
        frm_at.pack(fill="x", **pad)
        action_types = list(ActionType)
        for i, at in enumerate(action_types):
            tk.Radiobutton(
                frm_at, text=ACTION_LABELS[at],
                variable=self.var_action_type, value=at.value,
                command=self._on_action_type_change,
            ).grid(row=i // 3, column=i % 3, sticky="w", padx=5)

        # 動作參數
        self.frm_params = tk.LabelFrame(self.win, text="動作參數", padx=10, pady=5)
        self.frm_params.pack(fill="x", padx=10, pady=5)

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

        # 間隔
        self.frm_interval = tk.Frame(self.frm_params)
        tk.Label(self.frm_interval, text="間隔(秒):").pack(side="left")
        self.var_interval_min = tk.StringVar(value=str(combo.action.interval_min))
        self.var_interval_max = tk.StringVar(value=str(combo.action.interval_max))
        tk.Entry(self.frm_interval, textvariable=self.var_interval_min, width=6).pack(side="left", padx=2)
        tk.Label(self.frm_interval, text="~").pack(side="left")
        tk.Entry(self.frm_interval, textvariable=self.var_interval_max, width=6).pack(side="left", padx=2)

        self._on_action_type_change()

        # 確定/取消
        frm_btns = tk.Frame(self.win)
        frm_btns.pack(pady=10)
        tk.Button(frm_btns, text="確定", width=10, command=self._ok).pack(side="left", padx=10)
        tk.Button(frm_btns, text="取消", width=10, command=self.win.destroy).pack(side="left", padx=10)

        self.win.wait_window()

    def _browse_image(self):
        path = filedialog.askopenfilename(
            title="選擇觸發圖片",
            initialdir=str(profile_manager.TEMPLATES_DIR),
            filetypes=[("圖片", "*.png *.jpg *.bmp"), ("所有檔案", "*.*")],
        )
        if path:
            self.var_image.set(path)
            self.lbl_preview.config(text="")

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
        w, h = img.size
        self.lbl_preview.config(text=f"已貼上: {filename} ({w}x{h})", fg="green")

    def _on_action_type_change(self):
        """根據動作類型 + 觸發模式，顯示/隱藏相關參數"""
        at = self.var_action_type.get()
        trigger = self.var_trigger.get()

        is_mouse = at in ("click", "rapid_click", "hold_mouse")
        is_key = at in ("key_press", "rapid_key", "hold_key")
        is_rapid = at in ("rapid_click", "rapid_key")
        is_hold = at in ("hold_mouse", "hold_key")
        is_while_visible = trigger == "while_visible"

        # while_visible + 長按：持續時間由圖片消失決定，不需要手動設定
        need_duration = is_rapid or (is_hold and not is_while_visible)

        # 先全部隱藏再按需顯示
        for w in (self.frm_mouse_btn, self.frm_key, self.frm_duration, self.frm_interval):
            w.pack_forget()

        if is_mouse:
            self.frm_mouse_btn.pack(fill="x")
        if is_key:
            self.frm_key.pack(fill="x", pady=2)
        self.frm_delay.pack(fill="x", pady=2)  # 延遲始終顯示
        if need_duration:
            self.frm_duration.pack(fill="x", pady=2)
        if is_rapid:
            self.frm_interval.pack(fill="x", pady=2)

    @staticmethod
    def _to_float(s: str, default: float = 0.0) -> float:
        """解析浮點數，自動處理中文句號等替代字元"""
        s = s.strip().replace("。", ".").replace("，", ".").replace(",", ".")
        return float(s) if s else default

    def _ok(self):
        try:
            self.combo.name = self.var_name.get().strip() or "新組合"
            self.combo.image_path = self.var_image.get().strip()
            self.combo.match_threshold = self._to_float(self.var_threshold.get(), 0.8)
            self.combo.trigger = TriggerMode(self.var_trigger.get())

            action = self.combo.action
            action.action_type = ActionType(self.var_action_type.get())
            action.mouse_button = MouseButton(self.var_mouse_btn.get())
            action.key = self.var_key.get().strip()
            action.delay_min = self._to_float(self.var_delay_min.get())
            action.delay_max = self._to_float(self.var_delay_max.get())
            action.duration = self._to_float(self.var_duration.get(), 1.0)
            action.interval_min = self._to_float(self.var_interval_min.get(), 0.05)
            action.interval_max = self._to_float(self.var_interval_max.get(), 0.15)

            self.result = True
            self.win.destroy()
        except ValueError as e:
            messagebox.showerror("錯誤", f"參數格式不正確:\n{e}")
