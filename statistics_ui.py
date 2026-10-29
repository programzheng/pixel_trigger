"""Statistics window and editor."""

import tkinter as tk
from dataclasses import replace
from datetime import datetime
from tkinter import messagebox, simpledialog

import ttkbootstrap as ttk
from ttkbootstrap.publisher import Publisher

import profile_manager
from localization import tr
from models import Statistic
from stat_tracking import ManualLedger, accounting_day, crossing_level, period_key, statistic_rows, statistic_value


METRICS = {"income": "收入", "expense": "支出", "net": "淨額", "count": "紀錄次數", "balance": "最新餘額"}
PERIODS = {"day": "日", "week": "週", "month": "月", "year": "年"}
RECORD_KINDS = {"income": "收入", "expense": "支出", "baseline": "基準", "change": "變動", "reset": "重設"}


class StatisticEditor:
    def __init__(self, parent, profile, statistic=None):
        self.result = None
        self.original = statistic
        self.profile = profile
        self.win = tk.Toplevel(parent)
        self.win.title(tr("編輯統計") if statistic else tr("新增統計"))
        self.win.transient(parent)
        self.win.grab_set()
        body = ttk.Frame(self.win, padding=12)
        body.pack(fill="both", expand=True)
        current = statistic or Statistic()
        self.name = tk.StringVar(value=current.name if statistic else "")
        self.metric = tk.StringVar(value=tr(METRICS[current.metric]))
        self.period = tk.StringVar(value=tr(PERIODS[current.period]))
        self.reset_time = tk.StringVar(value=current.reset_time)
        self.limit = tk.StringVar(value="" if current.limit is None else str(current.limit))
        self.warning = tk.StringVar(value=str(current.warning_ratio * 100))
        self.enabled = tk.BooleanVar(value=current.enabled)
        self.sources = [(tr("手動輸入"), "manual", "")]
        self.sources += [(f"OCR：{combo.name} [{combo.id}]", "ocr", combo.id)
                         for combo in profile.combos if combo.detection_mode == "background"]
        if statistic and statistic.source == "ocr" and not any(
                combo_id == statistic.combo_id for _, _, combo_id in self.sources):
            self.sources.append((f"{tr('已刪除 Combo')} [{statistic.combo_id}]", "ocr", statistic.combo_id))
        source_index = next((i for i, (_, kind, combo_id) in enumerate(self.sources)
                             if kind == current.source and combo_id == current.combo_id), 0)
        ttk.Label(body, text=tr("名稱")).grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(body, textvariable=self.name, width=28).grid(row=0, column=1, sticky="ew")
        ttk.Label(body, text=tr("資料來源")).grid(row=1, column=0, sticky="w", pady=4)
        self.source_choice = ttk.Combobox(body, state="readonly" if statistic is None else "disabled",
                                          values=[label for label, _, _ in self.sources], width=30)
        self.source_choice.grid(row=1, column=1, sticky="ew")
        self.source_choice.current(source_index)
        ttk.Label(body, text=tr("計算項目")).grid(row=2, column=0, sticky="w", pady=4)
        self.metric_choice = ttk.Combobox(body, state="readonly", textvariable=self.metric,
                                          values=[tr(value) for value in METRICS.values()], width=28)
        self.metric_choice.grid(row=2, column=1, sticky="ew")
        self.metric_choice.bind("<<ComboboxSelected>>", self._metric_changed)
        ttk.Label(body, text=tr("期間")).grid(row=3, column=0, sticky="w", pady=4)
        ttk.Combobox(body, state="readonly", textvariable=self.period,
                     values=[tr(value) for value in PERIODS.values()], width=28).grid(row=3, column=1, sticky="ew")
        for row, (label, variable) in enumerate(((tr("每日重置 HH:MM"), self.reset_time),
                                                  (tr("上限（留空停用提醒）"), self.limit),
                                                  (tr("預警比例 %"), self.warning)), 4):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(body, textvariable=variable, width=28).grid(row=row, column=1, sticky="ew")
        ttk.Checkbutton(body, text=tr("啟用統計與提醒"), variable=self.enabled).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=4)
        buttons = ttk.Frame(body)
        buttons.grid(row=8, column=0, columnspan=2, pady=10)
        ttk.Button(buttons, text=tr("儲存"), command=self._save).pack(side="left", padx=5)
        ttk.Button(buttons, text=tr("取消"), command=self.win.destroy).pack(side="left", padx=5)
        self.win.wait_window()

    def _metric_changed(self, _event):
        if self.original is None and self.metric.get() != tr("收入") and self.limit.get() == "900":
            self.limit.set("")
        elif self.original is None and self.metric.get() == tr("收入") and not self.limit.get():
            self.limit.set("900")

    def _save(self):
        try:
            _, source, combo_id = self.sources[self.source_choice.current()]
            candidate = replace(self.original or Statistic(), name=self.name.get().strip(),
                                source=source, combo_id=combo_id,
                                metric=next(key for key, value in METRICS.items() if tr(value) == self.metric.get()),
                                period=next(key for key, value in PERIODS.items() if tr(value) == self.period.get()),
                                reset_time=self.reset_time.get().strip(),
                                limit=int(self.limit.get()) if self.limit.get().strip() else None,
                                warning_ratio=float(self.warning.get()) / 100,
                                enabled=self.enabled.get())
            candidate.validate()
        except (ValueError, IndexError, StopIteration) as error:
            messagebox.showerror(tr("統計設定錯誤"), tr(str(error)), parent=self.win)
            return
        self.result = candidate
        self.win.destroy()


class RecordEditor:
    def __init__(self, parent, row, source):
        self.result = None
        self.source = source
        self.kinds = ("income", "expense") if source == "manual" else ("baseline", "change", "reset")
        self.win = tk.Toplevel(parent)
        self.win.title(tr("編輯紀錄"))
        self.win.transient(parent)
        self.win.grab_set()
        self.win.resizable(False, False)
        body = ttk.Frame(self.win, padding=16)
        body.pack(fill="both", expand=True)
        self.timestamp = tk.StringVar(value=row["timestamp"])
        self.kind = tk.StringVar(value=tr(RECORD_KINDS[row["kind"]]))
        self.delta = tk.StringVar(value=str(row["delta"]))
        self.balance = tk.StringVar(value=str(row["balance"]))
        for index, (label, variable) in enumerate((("時間 YYYYMMDDHHMMSS：", self.timestamp),
                                                  ("類型", self.kind), ("變動", self.delta),
                                                  ("金額（餘額）", self.balance))):
            ttk.Label(body, text=tr(label)).grid(row=index, column=0, sticky="w", padx=(0, 10), pady=6)
            widget = (ttk.Combobox(body, state="readonly", textvariable=variable,
                                  values=[tr(RECORD_KINDS[kind]) for kind in self.kinds], width=25)
                      if variable is self.kind else ttk.Entry(body, textvariable=variable, width=28))
            widget.grid(row=index, column=1, sticky="ew", pady=6)
            if variable is self.kind:
                # ttkbootstrap retains a theme callback after destroying a combobox.
                widget.bind("<Destroy>", lambda event: Publisher.unsubscribe(str(event.widget)), add="+")
        ttk.Label(body, text=tr("四欄獨立儲存；變動可正可負。總計依統計項目重新計算。"),
                  wraplength=420, justify="left").grid(row=4, column=0, columnspan=2, sticky="w", pady=8)
        if source == "manual":
            ttk.Label(body, text=tr("手動紀錄的金額可留空。")).grid(row=5, column=0, columnspan=2, sticky="w")
        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, columnspan=2, pady=(12, 0))
        ttk.Button(buttons, text=tr("儲存"), command=self._save).pack(side="left", padx=6)
        ttk.Button(buttons, text=tr("取消"), command=self.win.destroy).pack(side="left", padx=6)
        self.win.bind("<Return>", lambda _event: self._save())
        self.win.bind("<Escape>", lambda _event: self.win.destroy())
        self.win.wait_window()

    def _save(self):
        try:
            stamp = self.timestamp.get().strip()
            when = datetime.strptime(stamp, "%Y%m%d%H%M%S")
            if when.strftime("%Y%m%d%H%M%S") != stamp:
                raise ValueError(tr("請輸入 YYYYMMDDHHMMSS。"))
            kind = next(kind for kind in self.kinds if tr(RECORD_KINDS[kind]) == self.kind.get())
            delta = int(self.delta.get())
            balance = int(self.balance.get()) if self.balance.get().strip() else None
            if (balance is None and self.source == "ocr") or (balance is not None and balance < 0):
                raise ValueError(tr("金額必須是非負整數。"))
        except (ValueError, StopIteration):
            messagebox.showerror(tr("編輯紀錄失敗"), tr("請輸入有效時間、類型、整數變動與非負整數金額。"), parent=self.win)
            return
        self.result = dict(timestamp=when, kind=kind, delta=delta, balance=balance)
        self.win.destroy()


class StatisticsWindow:
    def __init__(self, app):
        self.app = app
        self.manual = ManualLedger()
        self.manual.load()
        self.win = tk.Toplevel(app.root)
        self.win.title(tr("統計"))
        self.win.geometry("880x570")
        self.win.configure(highlightbackground="#ff9800", highlightcolor="#ff9800")
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)
        row = ttk.Frame(self.win, padding=8)
        row.pack(fill="x")
        self.choice = ttk.Combobox(row, state="readonly", width=32)
        self.choice.pack(side="left")
        self.choice.bind("<<ComboboxSelected>>", self._on_choice)
        self.add_stat_button = ttk.Button(row, text=tr("新增統計"), command=self._add_stat)
        self.add_stat_button.pack(side="left", padx=3)
        self.edit_button = ttk.Button(row, text=tr("編輯設定"), command=self._edit_stat)
        self.edit_button.pack(side="left", padx=3)
        self.archive_button = ttk.Button(row, text=tr("封存"), command=self._archive_stat)
        self.archive_button.pack(side="left", padx=3)
        self.date = tk.StringVar(value=datetime.now().strftime("%Y%m%d"))
        ttk.Label(row, text=tr("日期")).pack(side="left", padx=(10, 2))
        ttk.Entry(row, textvariable=self.date, width=10).pack(side="left")
        ttk.Button(row, text=tr("查詢"), command=lambda: self.refresh(force=True)).pack(side="left")
        self.summary = ttk.Label(self.win, justify="left", padding=8)
        self.summary.pack(fill="x")
        buttons = ttk.Frame(self.win, padding=(8, 2))
        buttons.pack(fill="x")
        self.add_button = ttk.Button(buttons, text=tr("新增紀錄"), command=self._add_record)
        self.add_button.pack(side="left", padx=2)
        self.record_edit_button = ttk.Button(buttons, text=tr("編輯紀錄"), command=self._edit_record)
        self.record_edit_button.pack(side="left", padx=2)
        self.record_delete_button = ttk.Button(buttons, text=tr("刪除紀錄"), command=self._delete_record)
        self.record_delete_button.pack(side="left", padx=2)
        self.reset_button = ttk.Button(buttons, text=tr("重設 OCR 基準"), command=self._reset)
        self.reset_button.pack(side="left", padx=8)
        records = ttk.Frame(self.win)
        records.pack(fill="both", expand=True, padx=8, pady=8)
        self.tree = ttk.Treeview(records, columns=("timestamp", "kind", "amount", "balance"), show="headings")
        for column, label in (("timestamp", tr("時間")), ("kind", tr("類型")),
                              ("amount", tr("變動")), ("balance", tr("金額"))):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=170)
        scrollbar = ttk.Scrollbar(records, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda _event: self._edit_record())
        self._view_key = None
        self._set_choices()
        if self._selected():
            self.date.set(accounting_day(datetime.now(), self._selected().reset_time))
        self.refresh(force=True)

    def _on_choice(self, _event):
        stat = self._selected()
        if stat:
            self.date.set(accounting_day(datetime.now(), stat.reset_time))
        self.refresh(force=True)

    def _set_choices(self, selected_id=None):
        stats = self.app.profile.statistics
        names = [stat.name for stat in stats]
        self.choice.config(values=[f"{stat.name}{f' [{stat.id[:8]}]' if names.count(stat.name) > 1 else ''}"
                                   f"{tr('（已封存）') if stat.archived else ''}" for stat in stats])
        index = next((i for i, stat in enumerate(stats) if stat.id == selected_id), 0)
        if stats:
            self.choice.current(index)
        else:
            self.choice.set("")

    def _selected(self):
        index = self.choice.current()
        stats = self.app.profile.statistics
        return stats[index] if 0 <= index < len(stats) else None

    def _source_combo(self, stat):
        return next((combo for combo in self.app.profile.combos if combo.id == stat.combo_id), None)

    def _save_profile(self, old_stats):
        try:
            profile_manager.save_profile(self.app.profile,
                                         self.app._profile_path.name if self.app._profile_path else None)
        except Exception as error:
            self.app.profile.statistics = old_stats
            messagebox.showerror(tr("儲存統計失敗"), tr(str(error)), parent=self.win)
            return False
        return True

    def _add_stat(self):
        if self.app.background.running:
            return
        candidate = StatisticEditor(self.win, self.app.profile).result
        if candidate is None:
            return
        old = self.app.profile.statistics[:]
        self.app.profile.statistics.append(candidate)
        if self._save_profile(old):
            self._set_choices(candidate.id)
            self.refresh(force=True)

    def _edit_stat(self):
        stat = self._selected()
        if stat is None or self.app.background.running:
            return
        candidate = StatisticEditor(self.win, self.app.profile, stat).result
        if candidate is None:
            return
        old = self.app.profile.statistics[:]
        self.app.profile.statistics[self.choice.current()] = candidate
        if self._save_profile(old):
            self._set_choices(candidate.id)
            self.refresh(force=True)

    def _archive_stat(self):
        stat = self._selected()
        if stat is None or stat.archived or self.app.background.running:
            return
        if not messagebox.askyesno(tr("封存統計"),
                                   tr("封存「{name}」？歷史紀錄會保留。").format(name=stat.name),
                                   parent=self.win):
            return
        old = self.app.profile.statistics[:]
        self.app.profile.statistics[self.choice.current()] = replace(stat, archived=True, enabled=False)
        if self._save_profile(old):
            self._set_choices(stat.id)
            self.refresh(force=True)

    def refresh(self, force=False):
        if not self.win.winfo_exists():
            return
        stat = self._selected()
        busy = self.app.background.running
        self.add_stat_button.config(state="disabled" if busy else "normal")
        if stat is None:
            self.win.configure(highlightthickness=0)
            self._refresh_signature = None
            self.summary.config(text=tr("尚未建立統計。") + (tr("請先停止背景偵測，再新增統計。") if busy
                                                     else tr("請按「新增統計」。")))
            self.tree.delete(*self.tree.get_children())
            for button in (self.edit_button, self.archive_button, self.add_button,
                           self.record_edit_button, self.record_delete_button, self.reset_button):
                button.config(state="disabled")
            return
        try:
            date = datetime.strptime(self.date.get(), "%Y%m%d")
            if date.strftime("%Y%m%d") != self.date.get():
                raise ValueError()
        except ValueError:
            self.win.configure(highlightthickness=0)
            self._refresh_signature = None
            self.summary.config(text=tr("請輸入有效日期 YYYYMMDD。"))
            self.tree.delete(*self.tree.get_children())
            return
        with self.app.background.ledger.lock:
            ocr_count = len(self.app.background.ledger.rows)
        signature = (stat.id, self.date.get(), ocr_count, len(self.manual.rows), busy,
                     stat.name, stat.metric, stat.period, stat.reset_time, stat.limit,
                     stat.warning_ratio, stat.enabled, stat.archived)
        if not force and signature == getattr(self, "_refresh_signature", None):
            return
        self._refresh_signature = signature
        combo = self._source_combo(stat)
        with self.app.background.ledger.lock:
            ocr = list(self.app.background.ledger.rows)
        rows = statistic_rows(stat, self.app.profile.id, ocr, self.manual.rows, self.date.get())
        value = statistic_value(stat, rows)
        near_limit = (stat.enabled and not stat.archived and stat.limit is not None
                      and value is not None and value >= stat.limit * stat.warning_ratio)
        self.win.configure(highlightthickness=4 if near_limit else 0)
        state = (tr("已封存") if stat.archived else tr("已停用") if not stat.enabled else
                 tr("已達上限") if stat.limit is not None and value is not None and value >= stat.limit else
                 tr("接近上限") if stat.limit is not None and value is not None
                 and value >= stat.limit * stat.warning_ratio else tr("正常"))
        source = tr("手動") if stat.source == "manual" else (combo.name if combo else tr("已刪除 Combo"))
        self.summary.config(text=f"{stat.name}　{tr(METRICS[stat.metric])}：{value if value is not None else '—'}"
                                 f"{tr('　期間：')}{tr(PERIODS[stat.period])}{tr('　來源：')}{source}"
                                 + tr("\n上限：")
                                 + f"{stat.limit if stat.limit is not None else tr('未設定')}{tr('　狀態：')}{state}"
                                 + (tr("\n背景偵測執行中：紀錄可直接新增、編輯或刪除；統計設定需停止後修改。")
                                    if busy else ""))
        self.edit_button.config(state="disabled" if busy else "normal")
        self.archive_button.config(state="disabled" if busy or stat.archived else "normal")
        self.add_button.config(state="disabled" if not stat.enabled or stat.archived
                               or (stat.source == "ocr" and combo is None) else "normal")
        self.record_edit_button.config(state="disabled" if stat.archived else "normal")
        self.record_delete_button.config(state="disabled" if stat.archived else "normal")
        self.reset_button.config(state="normal" if stat.source == "ocr" and stat.enabled and not stat.archived
                                 and combo and busy
                                 and combo.enabled and combo.id in self.app.background.active_ids else "disabled")
        key = (stat.id, self.date.get(), tuple((row["id"], row["timestamp"], row["kind"], row["delta"], row["balance"]) for row in rows))
        if force or key != self._view_key:
            self.tree.delete(*self.tree.get_children())
            self._display_records = {}
            self._ocr_records = {}
            for row in rows:
                iid = ("m:" if stat.source == "manual" else "o:") + row["id"]
                self._display_records[iid] = dict(row)
                if stat.source == "ocr":
                    self._ocr_records[iid] = dict(ocr[int(row["id"])])
                self.tree.insert("", "end", iid=iid, values=(row["timestamp"], tr(RECORD_KINDS[row["kind"]]),
                                                         f"{int(row['delta']):+d}", row["balance"]))
            self._view_key = key

    def _record_time(self, stat):
        initial = datetime.now()
        if accounting_day(initial, stat.reset_time) != self.date.get():
            try:
                initial = datetime.strptime(self.date.get() + stat.reset_time, "%Y%m%d%H:%M")
            except ValueError:
                messagebox.showerror(tr("日期錯誤"), tr("請先輸入有效日期 YYYYMMDD。"), parent=self.win)
                return None
        value = simpledialog.askstring(tr("紀錄時間"), tr("時間 YYYYMMDDHHMMSS："),
                                       initialvalue=initial.strftime("%Y%m%d%H%M%S"),
                                       parent=self.win)
        if value is None:
            return None
        try:
            when = datetime.strptime(value, "%Y%m%d%H%M%S")
            if when.strftime("%Y%m%d%H%M%S") != value:
                raise ValueError()
            return when
        except ValueError:
            messagebox.showerror(tr("紀錄時間錯誤"), tr("請輸入 YYYYMMDDHHMMSS。"), parent=self.win)
            return None

    def _add_record(self):
        stat = self._selected()
        if stat is None or stat.archived or not stat.enabled:
            return
        combo = self._source_combo(stat)
        if stat.source == "ocr" and combo is None:
            return
        when = self._record_time(stat)
        if when is None:
            return
        if stat.source == "manual":
            income = messagebox.askyesnocancel(tr("紀錄類型"), tr("這筆是收入嗎？選「否」代表支出。"), parent=self.win)
            if income is None:
                return
            amount = simpledialog.askinteger(tr("新增紀錄"), tr("金額："), minvalue=1, parent=self.win)
        else:
            amount = simpledialog.askinteger(tr("新增 OCR 紀錄"), tr("當時餘額："), minvalue=0, parent=self.win)
        if amount is None:
            return
        try:
            before = self._value_at(stat, when)
            if stat.source == "manual":
                self.manual.add(self.app.profile.id, stat.id, "income" if income else "expense", amount, when)
            else:
                self.app.background.ledger.add_record(self.app.profile, combo, when, amount)
            after = self._value_at(stat, when)
            current_day = accounting_day(datetime.now(), stat.reset_time)
            record_day = accounting_day(when, stat.reset_time)
            level = (crossing_level(stat, before, after)
                     if period_key(record_day, stat.period) == period_key(current_day, stat.period) else None)
            if level:
                messagebox.showinfo(tr("統計提醒"), f"{stat.name} {tr(level)}{tr('：')}{after}/{stat.limit}", parent=self.win)
        except (OSError, ValueError) as error:
            messagebox.showerror(tr("新增紀錄失敗"), tr(str(error)), parent=self.win)
        self.refresh(force=True)

    def _value_at(self, stat, when):
        with self.app.background.ledger.lock:
            ocr = list(self.app.background.ledger.rows)
        day = accounting_day(when, stat.reset_time)
        return statistic_value(stat, statistic_rows(stat, self.app.profile.id, ocr, self.manual.rows, day))

    def _edit_record(self):
        selected = self.tree.selection()
        stat = self._selected()
        if stat is None or stat.archived:
            return
        if not selected:
            messagebox.showinfo(tr("編輯紀錄"), tr("請先選取一筆紀錄。"), parent=self.win)
            return
        record_id = selected[0][2:]
        row = dict(self._display_records[selected[0]])
        expected = self._ocr_records.get(selected[0])
        values = RecordEditor(self.win, row, stat.source).result
        if values is None:
            return
        if values == dict(timestamp=datetime.strptime(row["timestamp"], "%Y%m%d%H%M%S"),
                          kind=row["kind"], delta=int(row["delta"]),
                          balance=int(row["balance"]) if row["balance"] != "" else None):
            return
        try:
            if stat.source == "manual":
                self.manual.edit_record(record_id, **values)
            else:
                combo = self._source_combo(stat)
                self.app.background.ledger.edit_record(
                    int(record_id), **values, expected=expected,
                    reset_time=combo.daily_reset_time if combo else stat.reset_time)
            self.date.set(accounting_day(values["timestamp"], stat.reset_time))
        except (OSError, ValueError) as error:
            messagebox.showerror(tr("編輯紀錄失敗"), tr(str(error)), parent=self.win)
        self.refresh(force=True)

    def _delete_record(self):
        selected = self.tree.selection()
        stat = self._selected()
        if stat is None or stat.archived:
            return
        if not selected:
            messagebox.showinfo(tr("刪除紀錄"), tr("請先選取一筆紀錄。"), parent=self.win)
            return
        if not messagebox.askyesno(
                tr("刪除紀錄"), tr("確定刪除選取的紀錄？後續金額變動將重新計算。"), parent=self.win):
            return
        try:
            if stat.source == "manual":
                self.manual.delete(selected[0][2:])
            else:
                self.app.background.ledger.delete_record(int(selected[0][2:]))
        except (OSError, ValueError) as error:
            messagebox.showerror(tr("刪除紀錄失敗"), tr(str(error)), parent=self.win)
        self.refresh(force=True)

    def _reset(self):
        stat = self._selected()
        combo = self._source_combo(stat) if stat else None
        if (stat and stat.enabled and not stat.archived and combo and self.app.background.running
                and combo.id in self.app.background.active_ids
                and messagebox.askyesno(tr("重設基準"), tr("下一個穩定讀值將作為新基準。"), parent=self.win)):
            self.app.background.reset(combo.id)
