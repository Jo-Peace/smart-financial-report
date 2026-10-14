"""Deterministic market calendar for /1 inference constraints.

Tavily news is a supplement. These dated rules are the source of truth for
settlement, US prints, FOMC, NFP, and quadruple witching near the report date.
TWSE closed days live in data/twse_closed_days.json and modules/twse_calendar.py.
3rd-Wednesday settlement may still need a one-off if a holiday shifts TAIFEX.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

PRINTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "market_calendar_prints.json",
)


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


# Official 2026 FOMC *decision* days (2:00pm ET → Taiwan ~02:00 next calendar day).
FOMC_DECISION_DATES = (
    "2026-01-28",
    "2026-03-18",
    "2026-05-06",
    "2026-06-17",
    "2026-07-29",
    "2026-09-16",
    "2026-10-28",
    "2026-12-16",
)

# Already-printed numbers. Add a row when BLS/Fed actually prints; do not invent.
KNOWN_PRINTS = (
    {
        "date": "2026-09-10",
        "kind": "ppi",
        "priority": 2,
        "text": "美國 PPI 8月已公布（BLS）：月增 +0.4%、年增 +5.4%。屬已知數，禁止再預測。",
    },
    {
        "date": "2026-09-11",
        "kind": "cpi",
        "priority": 2,
        "text": "美國 CPI 8月已公布（BLS）：整體月增 +0.4%／年增 3.4%；核心月增 +0.3%（偏熱）。屬已知數，禁止再預測。台股是隔日盤才完整定價。",
    },
)

# Corporate / one-off actions that distort the tape.
ONE_OFFS = (
    {
        "date": "2026-09-16",
        "kind": "ex_div",
        "priority": 3,
        "text": "台積電除息 7 元。權值缺口是機械性的，不要寫成棄守。",
    },
)


def _prev_twse(day: date) -> date:
    from modules.twse_calendar import prev_session

    return prev_session(day)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th weekday of month. Monday=0."""
    first = date(year, month, 1)
    delta = (weekday - first.weekday()) % 7
    return first + timedelta(days=delta + 7 * (n - 1))


def _months_around(day: date, back=1, forward=2):
    y, m = day.year, day.month
    out = []
    start = y * 12 + (m - 1) - back
    end = y * 12 + (m - 1) + forward
    for serial in range(start, end + 1):
        out.append((serial // 12, serial % 12 + 1))
    return out


def _generated_events(day: date) -> list[dict]:
    rows = []
    for year, month in _months_around(day):
        settle = nth_weekday(year, month, 2, 3)  # 3rd Wednesday
        rows.append(
            {
                "date": settle.isoformat(),
                "kind": "tx_settlement",
                "priority": 3,
                "text": (
                    f"台指期／台指選擇權月結算（第三個星期三）。"
                    f"最後結算價為現貨 13:00–13:25 均價。權值大買大賣先問轉倉／對沖。"
                ),
            }
        )
        eve = _prev_twse(settle)
        rows.append(
            {
                "date": eve.isoformat(),
                "kind": "tx_settlement_eve",
                "priority": 3,
                "text": (
                    f"台指期月結算 T-1（最後交易日 {settle.isoformat()}）。"
                    f"今日法人對權值的大買大賣，先問轉倉／對沖，再問基本面棄守。"
                ),
            }
        )
        nfp = nth_weekday(year, month, 4, 1)  # 1st Friday
        rows.append(
            {
                "date": nfp.isoformat(),
                "kind": "nfp",
                "priority": 2,
                "text": (
                    "美國非農 NFP（通常 8:30 ET＝台灣晚間約 20:30，台股已收盤）。"
                    "今日盤中禁止把非農當已公布結果；隔日開盤才定價。"
                ),
            }
        )
        if month in (3, 6, 9, 12):
            witch = nth_weekday(year, month, 4, 3)  # 3rd Friday
            rows.append(
                {
                    "date": witch.isoformat(),
                    "kind": "quad_witching",
                    "priority": 2,
                    "text": (
                        "美股四巫日（季月第三個週五：指數期貨／選擇權等到期）。"
                        "不是台指期結算日。台股多在下一個交易日吃 ADR／夜盤。"
                    ),
                }
            )

    try:
        from modules.twse_calendar import iter_closed_days

        for row in iter_closed_days():
            rows.append(
                {
                    "date": row["date"],
                    "kind": "twse_closed",
                    "priority": 3,
                    "text": (
                        f"台股休市（{row.get('name') or '國定假日'}）。"
                        f"禁止把這天講成開盤；下一交易日用語跟 session_calendar，不要猜明天／下週一。"
                    ),
                }
            )
    except Exception:
        pass

    for raw in FOMC_DECISION_DATES:
        rows.append(
            {
                "date": raw,
                "kind": "fomc_us",
                "priority": 3,
                "text": (
                    "FOMC 利率決策（美東 14:00，台灣約次日凌晨 02:00）。"
                    "在結果出來前，禁止把升息／不升息寫成已發生。"
                ),
            }
        )
        us_day = as_date(raw)
        if us_day:
            tw_day = us_day + timedelta(days=1)
            rows.append(
                {
                    "date": tw_day.isoformat(),
                    "kind": "fomc_tw",
                    "priority": 3,
                    "text": (
                        f"FOMC 結果對應的台灣交易日（決策日 {raw} 美東午後）。"
                        f"未核對聲明前，禁止把升息／不升息寫成已發生。"
                    ),
                }
            )
    return rows


def _load_json_events() -> list[dict]:
    if not os.path.exists(PRINTS_PATH):
        return []
    try:
        with open(PRINTS_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    rows = []
    for key in ("prints", "one_offs"):
        for item in payload.get(key) or []:
            if isinstance(item, dict) and item.get("date") and item.get("text"):
                rows.append(item)
    return rows


def all_event_templates(day) -> list[dict]:
    parsed = as_date(day) or date.today()
    extras = _load_json_events()
    if not extras:
        extras = list(KNOWN_PRINTS) + list(ONE_OFFS)
    extra_keys = {(str(item.get("date")), str(item.get("kind") or "")) for item in extras}
    rows = [
        item
        for item in _generated_events(parsed)
        if (item["date"], item.get("kind") or "") not in extra_keys
    ]
    rows.extend(extras)
    return rows


def nearby_market_events(day, back_days=5, forward_days=4) -> list[dict]:
    """Events from a few calendar days before the report date through a few after."""
    parsed = as_date(day) or date.today()
    start = parsed - timedelta(days=back_days)
    end = parsed + timedelta(days=forward_days)
    keyed = {}
    for item in all_event_templates(parsed):
        event_day = as_date(item["date"])
        if event_day is None or event_day < start or event_day > end:
            continue
        if event_day < parsed:
            when = "已發生"
        elif event_day == parsed:
            when = "今天"
        else:
            when = "尚未發生"
        key = (event_day.isoformat(), item.get("kind") or item["text"][:24])
        keyed[key] = {
            "date": event_day.isoformat(),
            "when": when,
            "kind": item.get("kind") or "",
            "priority": int(item.get("priority") or 1),
            "text": item["text"],
        }
    rows = list(keyed.values())
    rows.sort(key=lambda row: (row["date"], -row["priority"], row["kind"]))
    return rows


def calendar_one_liner(day) -> str:
    rows = nearby_market_events(day)
    live = [row for row in rows if row["when"] in {"今天", "尚未發生"} and row["priority"] >= 2]
    live = live[:4]
    if not live:
        past = [row for row in rows if row["when"] == "已發生" and row["priority"] >= 2][-2:]
        if not past:
            return ""
        return "；".join(f"{row['date']} {row['text'].split('。')[0]}" for row in past)
    parts = []
    for row in live:
        head = row["text"].split("。")[0]
        parts.append(f"{row['date']}（{row['when']}）{head}")
    return "；".join(parts)


def analyzer_calendar_block(day) -> str:
    rows = nearby_market_events(day)
    if not rows:
        return ""
    lines = [
        "================================================================",
        "【日曆約束——解讀用，不是標題鉤子】",
        "================================================================",
        "以下是本機日曆事實（結算／除息／FOMC／非農／四巫／已公布指數）。",
        "寫日報時必須當解讀約束，禁止當開場主鉤子，除非當日最強可驗證異常",
        "就是期現價差、結算均價窗、除息缺口或該指數本身。",
        "禁止預測已公布的 CPI／PPI／非農；禁止把未公布的 FOMC／非農寫成結果。",
        "四巫日是美股季月第三個週五，不是台指期結算（第三個週三）。",
        "權值大賣超若落在結算 T-1／除息前夕，先寫機械性可能，再寫主力換股假說。",
        "正文必須有短段「〔日曆約束〕」（3～6 句），用下面事實，不要發明日期。",
    ]
    for row in rows:
        lines.append(f"- {row['date']}（{row['when']}）：{row['text']}")
    return "\n".join(lines) + "\n"


def packaging_calendar_block(day) -> str:
    rows = nearby_market_events(day)
    one = calendar_one_liner(day)
    lines = [
        "本機日曆（解讀用，不是預設標題開場）：",
        one or "（近窗無高優先日曆）",
        "標題規則：不要用「升息／非農／四巫日／結算」當開場，除非當日最大已驗證矛盾就是它",
        "（例如除息金額對上外資砍超）。禁止把未公布的 FOMC／非農寫進標題當結果。",
        "四巫日 ≠ 台指期結算。",
    ]
    for row in rows[:8]:
        lines.append(f"- {row['date']}（{row['when']}／{row['kind']}）{row['text']}")
    return "\n".join(lines)


def events_for_structured(day) -> list[dict]:
    """JSON-safe nearby events for structured_data.calendar_events."""
    return nearby_market_events(day)
