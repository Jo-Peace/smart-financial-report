"""TWSE session calendar: weekends + listed public holidays."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

CLOSED_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "twse_closed_days.json",
)

_WEEKDAY_ZH = "一二三四五六日"
_CACHE: dict[str, dict] | None = None


def as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def weekday_zh(day: date) -> str:
    return "週" + _WEEKDAY_ZH[day.weekday()]


def _load_closed() -> dict[str, dict]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    rows = {}
    try:
        with open(CLOSED_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
        for item in payload.get("closed_days") or []:
            key = str(item.get("date") or "").strip()[:10]
            if key:
                rows[key] = {
                    "date": key,
                    "name": str(item.get("name") or "休市").strip() or "休市",
                }
    except (OSError, json.JSONDecodeError):
        rows = {}
    _CACHE = rows
    return rows


def iter_closed_days() -> list[dict]:
    return list(_load_closed().values())


def _covered_years() -> set[int]:
    return {int(key[:4]) for key in _load_closed() if key[:4].isdigit()}


def calendar_covers(day) -> bool:
    """
    The one coverage check: True when data/twse_closed_days.json lists at least
    one closed day in ``day``'s year.

    Every TWSE year has weekday holidays (元旦, 春節…). No entry for the year
    means the calendar was never updated, so is_twse_session would treat real
    holidays as sessions.
    """
    parsed = as_date(day)
    return bool(parsed and parsed.year in _covered_years())


def calendar_coverage() -> tuple[date, date] | None:
    """
    (first, last) day of the years the closed-day list covers (None = no data).
    Descriptive only; whether a given day is covered is calendar_covers(day).
    """
    years = sorted(_covered_years())
    if not years:
        return None
    return date(years[0], 1, 1), date(years[-1], 12, 31)


def covered_through() -> date | None:
    """Dec 31 of the last year listed in twse_closed_days.json (None when empty)."""
    coverage = calendar_coverage()
    return coverage[1] if coverage else None


def is_calendar_covered(day) -> bool:
    """Backward-compatible alias of calendar_covers."""
    return calendar_covers(day)


def coverage_warning(day) -> str:
    """
    Non-empty when `day` is outside the years the closed-day list covers
    (calendar_covers): session counting then treats unlisted holidays (New
    Year, Lunar New Year) as trading days, so ledger targets and trend windows
    may be miscounted.
    """
    parsed = as_date(day)
    if parsed is None:
        return ""
    end = covered_through()
    if end is None:
        return "data/twse_closed_days.json 沒有休市日資料；交易日窗只排除週末，可能把假日算成交易日。"
    if calendar_covers(parsed):
        return ""
    if parsed > end:
        return (
            f"data/twse_closed_days.json 只列到 {end.year} 年休市日；{parsed.isoformat()} 的交易日窗"
            f"可能把假日算成交易日（驗收目標日/趨勢窗會錯位），請補 {end.year + 1} 年休市日。"
        )
    return (
        f"data/twse_closed_days.json 沒有 {parsed.year} 年休市日；{parsed.isoformat()} 的交易日窗"
        f"可能把假日算成交易日，請補 {parsed.year} 年休市日。"
    )


def closed_name(day) -> str | None:
    parsed = as_date(day)
    if parsed is None:
        return None
    row = _load_closed().get(parsed.isoformat())
    return str(row["name"]) if row else None


def is_twse_session(day) -> bool:
    parsed = as_date(day)
    if parsed is None:
        return False
    if parsed.weekday() >= 5:
        return False
    return closed_name(parsed) is None


def next_session(day, limit=21) -> date:
    parsed = as_date(day) or date.today()
    cursor = parsed
    for _ in range(limit):
        cursor += timedelta(days=1)
        if is_twse_session(cursor):
            return cursor
    return parsed + timedelta(days=3)


def prev_session(day, limit=21) -> date:
    parsed = as_date(day) or date.today()
    cursor = parsed
    for _ in range(limit):
        cursor -= timedelta(days=1)
        if is_twse_session(cursor):
            return cursor
    return parsed - timedelta(days=3)


def holiday_names_between(start, end) -> list[str]:
    """Weekday holiday names strictly after start and before end."""
    a = as_date(start)
    b = as_date(end)
    if a is None or b is None or b <= a:
        return []
    names = []
    cursor = a + timedelta(days=1)
    while cursor < b:
        name = closed_name(cursor)
        if name:
            names.append(name)
        cursor += timedelta(days=1)
    return names


def relative_session_label(today, target, *, future: bool) -> str:
    today_d = as_date(today) or date.today()
    target_d = as_date(target)
    if target_d is None:
        return "下一交易日" if future else "上一交易日"
    delta = (target_d - today_d).days
    if future and delta == 1:
        return "明天"
    if (not future) and delta == -1:
        return "昨天"
    wd = weekday_zh(target_d)
    same_week = target_d.isocalendar()[:2] == today_d.isocalendar()[:2]
    if same_week:
        return wd
    prefix = "下" if future else "上"
    return prefix + wd
