"""Independent statistics over new OCR and manual records."""

import csv
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from calc import accounting_day, recover_csv_edit, write_csv_dates
from localization import tr
from models import Statistic
from profile_manager import PROFILES_DIR


STATISTICS_DIR = PROFILES_DIR.parent / "statistics"
LEGACY_MANUAL_FIELDS = ["id", "timestamp", "profile_id", "statistic_id", "kind", "amount"]
MANUAL_FIELDS = [*LEGACY_MANUAL_FIELDS, "delta", "balance"]


def period_key(day: str, period: str) -> str:
    date = datetime.strptime(day, "%Y%m%d")
    if period == "day":
        return day
    if period == "week":
        return (date - timedelta(days=date.weekday())).strftime("%Y%m%d")
    return day[:6 if period == "month" else 4]


def statistic_rows(stat: Statistic, profile_id: str, ocr_rows: list[dict],
                   manual_rows: list[dict], selected_day: str) -> list[dict]:
    """Return derived events for one period; OCR deltas follow this statistic's day boundary."""
    wanted = period_key(selected_day, stat.period)
    if stat.source == "manual":
        return [dict(row, delta=int(row.get("delta", int(row["amount"]) * (1 if row["kind"] == "income" else -1))),
                     balance=row.get("balance", "")) for row in sorted(manual_rows, key=lambda item: (item["timestamp"], item["id"]))
                if row["profile_id"] == profile_id and row["statistic_id"] == stat.id
                and period_key(accounting_day(datetime.strptime(row["timestamp"], "%Y%m%d%H%M%S"),
                                              stat.reset_time), stat.period) == wanted]

    previous, previous_day = None, None
    result = []
    # shortcut: scan in-memory history; index by source if long-running profiles make refresh slow.
    source = [(index, row) for index, row in enumerate(ocr_rows)
              if row["profile_id"] == profile_id and row["combo_id"] == stat.combo_id]
    for index, row in sorted(source, key=lambda item: (item[1]["timestamp"], item[0])):
        day = accounting_day(datetime.strptime(row["timestamp"], "%Y%m%d%H%M%S"), stat.reset_time)
        balance = int(row["balance"])
        reference_balance, reference_day = previous, previous_day
        if "reference_timestamp" in row:
            reference_balance = int(row["reference_balance"]) if row["reference_timestamp"] else None
            reference_day = (accounting_day(datetime.strptime(row["reference_timestamp"], "%Y%m%d%H%M%S"),
                                            stat.reset_time) if row["reference_timestamp"] else None)
        delta = (int(row["delta"]) if row.get("manual_override") == "1" else
                 0 if reference_balance is None or day != reference_day or row["record_type"] == "reset"
                 else balance - reference_balance)
        if period_key(day, stat.period) == wanted:
            result.append(dict(row, id=str(index), delta=delta, kind=row["record_type"]))
        previous, previous_day = balance, day
    return result


def statistic_value(stat: Statistic, rows: list[dict]):
    deltas = [int(row["delta"]) for row in rows]
    if stat.metric == "income":
        return sum(max(0, delta) for delta in deltas)
    if stat.metric == "expense":
        return sum(max(0, -delta) for delta in deltas)
    if stat.metric == "net":
        return sum(deltas)
    if stat.metric == "count":
        return sum(delta != 0 for delta in deltas)
    return int(rows[-1]["balance"]) if rows else None


def crossing_level(stat: Statistic, before, after) -> str | None:
    if not stat.enabled or stat.archived or stat.limit is None:
        return None
    before, after = before or 0, after or 0
    if before < stat.limit <= after:
        return "已達上限"
    if before < stat.limit * stat.warning_ratio <= after:
        return "接近上限"
    return None


class ManualLedger:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else STATISTICS_DIR / "manual"
        self.rows: list[dict] = []

    def load(self):
        recover_csv_edit(self.directory)
        rows = []
        seen = set()
        for path in sorted(self.directory.glob("*.csv")):
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames not in (MANUAL_FIELDS, LEGACY_MANUAL_FIELDS):
                    raise ValueError(tr("手動統計 CSV 欄位毀損：{name}").format(name=path.name))
                for row in reader:
                    try:
                        if reader.fieldnames == LEGACY_MANUAL_FIELDS:
                            row.update(delta=str(int(row["amount"]) * (1 if row["kind"] == "income" else -1)), balance="")
                        stamp = datetime.strptime(row["timestamp"], "%Y%m%d%H%M%S")
                        if (stamp.strftime("%Y%m%d%H%M%S") != row["timestamp"]
                                or stamp.strftime("%Y%m%d") != path.stem
                                or row["kind"] not in ("income", "expense")
                                or not row["id"] or not row["profile_id"] or not row["statistic_id"]
                                or row["id"] in seen or not re.fullmatch(r"[0-9]+", row["amount"])
                                or int(row["amount"]) < 1
                                or None in row or any(v is None for v in row.values())
                                or not re.fullmatch(r"[+-]?[0-9]+", row["delta"])
                                or (row["balance"] and not re.fullmatch(r"[0-9]+", row["balance"]))):
                            raise ValueError()
                    except (ValueError, TypeError, KeyError):
                        raise ValueError(tr("手動統計 CSV 紀錄毀損：{name} 第 {line} 行").format(
                            name=path.name, line=reader.line_num)) from None
                    rows.append(row)
                    seen.add(row["id"])
        self.rows = rows

    def add(self, profile_id: str, statistic_id: str, kind: str, amount: int,
            timestamp: datetime | None = None):
        if (not profile_id or not statistic_id or kind not in ("income", "expense")
                or type(amount) is not int or amount < 1):
            raise ValueError("請輸入正整數收入或支出。")
        timestamp = timestamp or datetime.now()
        if type(timestamp) is not datetime:
            raise ValueError("紀錄時間不正確。")
        row = dict(zip(MANUAL_FIELDS, (uuid.uuid4().hex, timestamp.strftime("%Y%m%d%H%M%S"),
                                       profile_id, statistic_id, kind, str(amount),
                                       str(amount if kind == "income" else -amount), "")))
        rows = self.rows + [row]
        self._write_date(row["timestamp"][:8], rows)
        self.rows = rows
        return row

    def edit(self, record_id: str, kind: str, amount: int):
        if kind not in ("income", "expense") or type(amount) is not int or amount < 1:
            raise ValueError("請輸入正整數收入或支出。")
        rows = [dict(row) for row in self.rows]
        row = next((row for row in rows if row["id"] == record_id), None)
        if row is None:
            raise ValueError("找不到手動紀錄。")
        row.update(kind=kind, amount=str(amount), delta=str(amount if kind == "income" else -amount))
        self._write_date(row["timestamp"][:8], rows)
        self.rows = rows

    def edit_record(self, record_id: str, timestamp: datetime, kind: str, delta: int, balance: int | None):
        if (type(timestamp) is not datetime or kind not in ("income", "expense")
                or type(delta) is not int or (balance is not None and (type(balance) is not int or balance < 0))):
            raise ValueError("紀錄欄位格式不正確。")
        rows = [dict(row) for row in self.rows]
        row = next((row for row in rows if row["id"] == record_id), None)
        if row is None:
            raise ValueError("找不到手動紀錄。")
        old_day = row["timestamp"][:8]
        row.update(timestamp=timestamp.strftime("%Y%m%d%H%M%S"), kind=kind,
                   delta=str(delta), balance="" if balance is None else str(balance))
        dates = {day: [item for item in rows if item["timestamp"].startswith(day)]
                 for day in (old_day, row["timestamp"][:8])}
        write_csv_dates(self.directory, MANUAL_FIELDS, dates)
        self.rows = rows

    def delete(self, record_id: str):
        row = next((row for row in self.rows if row["id"] == record_id), None)
        if row is None:
            raise ValueError("找不到手動紀錄。")
        rows = [item for item in self.rows if item["id"] != record_id]
        self._write_date(row["timestamp"][:8], rows)
        self.rows = rows

    def _write_date(self, day: str, rows: list[dict]):
        write_csv_dates(self.directory, MANUAL_FIELDS,
                        {day: [row for row in rows if row["timestamp"].startswith(day)]})
