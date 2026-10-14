"""Taiwan-session speech labels so scripts do not say 明天開盤 on Friday or holidays."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from modules.twse_calendar import (
    as_date,
    holiday_names_between,
    next_session as _next_session,
    prev_session as _prev_session,
    relative_session_label,
    weekday_zh,
)


def last_output_from_structured(structured_data) -> str | None:
    policy = (structured_data or {}).get("validation_policy") or {}
    source = policy.get("source_date")
    return str(source) if source else None


def next_session(day: date) -> date:
    return _next_session(day)


def prev_session(day: date) -> date:
    return _prev_session(day)


def session_lexicon(day, last_output_date=None) -> dict:
    """Spoken labels for the next/previous TWSE session relative to a report date.

    last_output_date is the previous /1 (or episode) date. If it is not the
    previous trading session, 點名／上集 must not be called 昨天.
    """
    parsed = as_date(day) or date.today()
    nxt = next_session(parsed)
    prv = prev_session(parsed)
    next_is_tomorrow = nxt == parsed + timedelta(days=1)
    prev_is_yesterday = prv == parsed - timedelta(days=1)
    last_ep = as_date(last_output_date)
    episode_is_prev_session = last_ep is not None and last_ep == prv
    episode_gap = last_ep is not None and last_ep != prv

    next_label = relative_session_label(parsed, nxt, future=True)
    prev_label = relative_session_label(parsed, prv, future=False)
    holidays_ahead = holiday_names_between(parsed, nxt)

    if next_is_tomorrow:
        next_open = "明天開盤"
        next_watch = "明天先看"
        next_section = "明日條件"
        next_observe = "明日觀察"
        next_main = "明日主盯"
        forbid_next = []
    else:
        next_open = f"{next_label}開盤"
        next_watch = f"{next_label}先看"
        next_section = f"{next_label}條件"
        next_observe = f"{next_label}觀察"
        next_main = f"{next_label}主盯"
        forbid_next = ["明天開盤", "明天台股", "明日開盤", "明天見盤"]
        if next_label != "下週一":
            forbid_next.extend(["下週一開盤", "下週一見"])

    if prev_is_yesterday:
        forbid_prev = []
    else:
        forbid_prev = ["昨天開盤", "昨天台股開盤"]
        if prev_label != "上週五":
            forbid_prev.append("上週五開盤")

    if episode_gap:
        ep_wd = weekday_zh(last_ep)
        prev_named = "上集點名"
        prev_section = "上集預測節奏"
        prev_result = "上集結果"
        forbid_prev = list(forbid_prev) + [
            "昨天點名",
            "昨日點名",
            "昨天的影片",
            "昨天那集",
            "昨天預測",
        ]
        episode_label = f"{ep_wd}那集"
    elif not prev_is_yesterday:
        prev_named = f"{prev_label}點名"
        prev_section = f"{prev_label}預測節奏"
        prev_result = f"{prev_label}結果"
        forbid_prev = list(forbid_prev) + ["昨天點名"]
        episode_label = f"{prev_label}那集"
    else:
        prev_named = "昨天點名"
        prev_section = "昨日預測節奏"
        prev_result = "昨日結果"
        episode_label = "昨天那集"

    forbid = forbid_next + forbid_prev
    return {
        "date": parsed.isoformat(),
        "weekday": weekday_zh(parsed),
        "next_session_date": nxt.isoformat(),
        "prev_session_date": prv.isoformat(),
        "next_is_tomorrow": next_is_tomorrow,
        "prev_is_yesterday": prev_is_yesterday,
        "next_label": next_label,
        "next_open": next_open,
        "next_watch": next_watch,
        "next_section": next_section,
        "next_observe": next_observe,
        "next_main": next_main,
        "prev_label": prev_label,
        "prev_named": prev_named,
        "prev_section": prev_section,
        "prev_result": prev_result,
        "last_output_date": last_ep.isoformat() if last_ep else None,
        "episode_gap": episode_gap,
        "episode_is_prev_session": episode_is_prev_session or (
            last_ep is None and prev_is_yesterday
        ),
        "episode_label": episode_label,
        "forbid": forbid,
        "ge_ri": "下一交易日" if not next_is_tomorrow else "隔日",
        "holidays_ahead": holidays_ahead,
    }


def speech_rules_block(day, last_output_date=None) -> str:
    lex = session_lexicon(day, last_output_date=last_output_date)
    forbid = "、".join(lex["forbid"]) if lex["forbid"] else ""
    episode_gap = lex.get("episode_gap")
    holidays = lex.get("holidays_ahead") or []
    holiday_text = "、".join(holidays) if holidays else "休市"
    if not lex["next_is_tomorrow"] and not lex["prev_is_yesterday"]:
        body = (
            f"今天是{lex['weekday']}（休市）。下一檔台股請說「{lex['next_open']}」，"
            f"上一檔請說「{lex['prev_label']}」。禁止「明天開盤」「昨天開盤」。"
        )
        if holidays:
            body += f"連休含：{holiday_text}。"
    elif not lex["next_is_tomorrow"]:
        body = (
            f"今天是{lex['weekday']}盤後。明天起台股連休（{holiday_text}）。\n"
            f"- 下一交易日請說「{lex['next_label']}」或「{lex['next_open']}」。\n"
            f"- 禁止口播：{forbid}。\n"
            f"- 「隔日節奏」改說「{lex['ge_ri']}節奏」或「{lex['next_label']}節奏」。\n"
            f"- 上一交易日是昨天，可以用「昨天」。\n"
            f"- 收尾說「{lex['next_label']}見」，不要說「明天見盤」。"
        )
    elif not lex["prev_is_yesterday"]:
        body = (
            f"今天是{lex['weekday']}盤後。\n"
            f"- 上一交易日請說「{lex['prev_label']}」或「{lex['prev_named']}」。\n"
            f"- 禁止口播：{forbid}。昨天是休市，不是開盤日。\n"
            f"- 下一交易日是明天，可以說「{lex['next_open']}」。"
        )
    else:
        body = (
            f"今天是{lex['weekday']}盤後。下一交易日說「明天／明天開盤」；"
            f"台股上一交易日可以說「昨天開盤」。"
        )
        if episode_gap:
            body += (
                f"\n- 上一集影片是{lex.get('episode_label') or '上集'}（{lex.get('last_output_date')}），"
                f"請說「上集」或「{lex['prev_named']}」，不要說「昨天點名／昨天那集／昨天的影片」。"
                f"昨天有開盤但沒有影片。"
            )
        else:
            body += "點名／上集可以說「昨天點名」。"
    if episode_gap and forbid:
        extra = f"\n- 禁止口播：{forbid}。"
        if extra.strip() not in body:
            body += extra
    return f"## 交易日用語（強制，台股週一到週五）\n{body}\n"


def nearby_market_events(day, back_days=5, forward_days=4):
    """Delegate to the standing market calendar (settlement / FOMC / NFP / witching)."""
    from modules.market_calendar import nearby_market_events as _nearby

    return _nearby(day, back_days=back_days, forward_days=forward_days)


def analyzer_calendar_block(day) -> str:
    """Force /1 to treat nearby settlement, prints, and FOMC as constraints."""
    from modules.market_calendar import analyzer_calendar_block as _block

    return _block(day)


def analyzer_session_block(day, last_output_date=None) -> str:
    """Gemini daily-report iron rule (not Markdown director-card heading)."""
    return (
        "================================================================\n"
        "【交易日用語——週五／週一不要說錯開盤；沒做的那集不要叫昨天】\n"
        "================================================================\n"
        + speech_rules_block(day, last_output_date=last_output_date).replace(
            "## 交易日用語（強制，台股週一到週五）\n", ""
        )
        + "日報標題「明日觀察」若下一交易日不是明天，改成該日用語（例如下週二觀察）；"
        + "禁止把休市日講成開盤日。週一口播禁止「昨天開盤」。\n"
        + "若上一集不是上一交易日：點名說「上集」，禁止「昨天點名／昨天那集」。\n"
    )
