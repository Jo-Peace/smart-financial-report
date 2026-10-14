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
