"""
Ground truth + output layout shared by the US / weekly / weekend special scripts
(main_us.py, main_weekly.py, weekend_special_main.py).

Everything the model is asked to talk about must come from a block built here:
- Taiwan session context from the TWSE calendar (no hard-coded 春節 / Sunday);
- TSM ADR premium computed locally (TSM x USD/TWD / 5 vs the 2330 close);
- this week's Taiwan facts aggregated from reports/daily/*/structured_data.json.
Missing data produces an explicit "do not mention" line instead of a gap the
model would fill in.
"""

from __future__ import annotations

import datetime
import glob
import json
import os
import re

from modules.fetch_result import is_fetch_result, unwrap_fetch_result
from modules.report_paths import DAILY_ARTIFACTS, DAILY_DIRNAME, special_topic_dir
from modules.twse_calendar import (
    as_date,
    calendar_coverage,
    closed_name,
    holiday_names_between,
    is_calendar_covered,
    is_twse_session,
    next_session,
    prev_session,
    relative_session_label,
    weekday_zh,
)

ADR_RATIO = 5  # 1 TSM ADR = 5 TWSE shares of 2330
TWSE_CLOSE_TIME = datetime.time(13, 30)
GENERIC_NEXT_LABEL = "下一個交易日"

US_OBSERVATION_SLUG = "us_market_observation"
US_WEEKLY_SLUG = "us_weekly_recap"
WEEKEND_SLUG = "weekend_special"

NO_PREDICTION_RULE = (
    "這是「觀察」不是「預測」：嚴禁使用「預期將…」「必定…」「極可能…」「勢必…」等斷言，"
    "也不得預測台股開高／開低或跳空方向；只能寫「若…則值得留意…」的條件式觀察。"
)
NEUTRAL_WORDING_RULE = (
    "用語中性：依實際漲跌幅描述，不使用「血洗」「崩盤」「大逃殺」「恐慌拋售」等誇張字眼；"
    "跌幅明顯時最多寫「重挫」。"
)
NO_INVENTION_RULE = "資料區塊沒有提供的數字一律不得自行推估或編造；引用新聞數字須註明出處。"


# === Taiwan session context ===

# Asia/Taipei wall clock (the scripts' run date and the 13:30 close check):
# the one implementation is modules.tz; these names stay importable from here.
from modules.tz import TAIPEI_TZ, taipei_now  # noqa: E402,F401


def _holiday_label(name) -> str:
    """'春節封關（僅結算）' / '除夕' / '春節' -> '春節'; other names lose parentheticals."""
    text = re.sub(r"（.*?）|\(.*?\)", "", str(name or "")).strip()
    if "春節" in text or text == "除夕":
        return "春節"
    return text


def _labels(names) -> list[str]:
    out = []
    for name in names:
        label = _holiday_label(name)
        if label and label not in out:
            out.append(label)
    return out


def session_context(today=None, *, now=None) -> dict:
    """
    TWSE calendar facts for ``today`` (default: date of ``now``, else the local date).

    ``now`` is the Asia/Taipei wall clock. On a session day before the 13:30
    close, the last *completed* session is the previous one and today's session
    is the one that reacts to overnight US moves (``reacting_session``).
    Closure reasons are only reported for closed days (``is_holiday_break``);
    on a trading day the holidays before the next session are
    ``upcoming_closure_label``. Dates outside data/twse_closed_days.json set
    ``calendar_known`` False: callers must not name a closure reason or a
    next-session date then.
    """
    day = as_date(today) or as_date(now) or datetime.date.today()
    weekend = day.weekday() >= 5
    day_covered = is_calendar_covered(day)
    open_today = is_twse_session(day) if (weekend or day_covered) else None
    # Wall clock before 13:30 on ``day`` (whatever the calendar says).
    clock_before_close = bool(now is not None and as_date(now) == day and now.time() < TWSE_CLOSE_TIME)
    before_close = bool(open_today and clock_before_close)
    after_close = bool(open_today and now is not None and as_date(now) == day and not before_close)
    last = day if (open_today and not before_close) else prev_session(day)
    nxt = next_session(day)
    coverage = calendar_coverage()
    known = day_covered and is_calendar_covered(last) and is_calendar_covered(nxt)

    closure, upcoming = [], []
    if known and open_today:
        upcoming = _labels(holiday_names_between(day, nxt))
    elif known:
        closure = _labels(holiday_names_between(last, nxt) + ([closed_name(day)] if closed_name(day) else []))

    next_iso = nxt.isoformat() if known else None
    next_label = relative_session_label(day, nxt, future=True) if known else GENERIC_NEXT_LABEL
    return {
        "date": day.isoformat(),
        "weekday": weekday_zh(day),
        "is_weekend": weekend,
        "tw_open_today": open_today,
        "tw_before_close": before_close,
        "clock_before_close": clock_before_close,
        "tw_after_close": after_close,
        "last_session": last.isoformat() if (known or (open_today and not before_close)) else None,
        "next_session": next_iso,
        "next_label": next_label,
        "reacting_session": day.isoformat() if before_close else next_iso,
        "reacting_label": "今天" if before_close else next_label,
        "holiday_names": closure,
        "closure_label": "、".join(closure),
        "is_holiday_break": bool(closure),
        "upcoming_closure_label": "、".join(upcoming),
        "calendar_known": known,
        "calendar_covered_through": coverage[1].isoformat() if coverage else None,
    }


def _unknown_calendar_text(ctx: dict) -> str:
    through = ctx.get("calendar_covered_through") or "（無資料）"
    caveat = (
        f"台股行事曆資料只涵蓋到 {through}，無法確認休市日：本報告不得寫出台股休市原因或下一個交易日日期，"
        "請以證交所公告為準。"
    )
    if ctx["tw_open_today"] is True:
        return f"台股交易日；{caveat}"
    if ctx["tw_open_today"] is False:
        return f"台股{'週末' if ctx.get('is_weekend') else ''}休市；{caveat}"
    return f"無法確認台股是否開市；{caveat}"


def format_session_context(ctx: dict) -> str:
    head = f"今天 {ctx['date']}（{ctx['weekday']}）"
    if not ctx.get("calendar_known", True):
        return head + _unknown_calendar_text(ctx)
    if ctx["tw_open_today"]:
        if ctx.get("tw_before_close"):
            text = (
                f"{head}台股交易日，13:30 尚未收盤；最後完成交易日 {ctx['last_session']}，"
                "前一晚美股的變化反映在今天盤中"
            )
        else:
            text = f"{head}台股正常交易"
        text += f"；下一個交易日 {ctx['next_session']}（{ctx['next_label']}）"
        if ctx.get("upcoming_closure_label"):
            text += f"，中間因{ctx['upcoming_closure_label']}休市"
        return text + "。"
    if ctx["is_holiday_break"]:
        return (
            f"{head}台股因{ctx['closure_label']}休市；最後交易日 {ctx['last_session']}，"
            f"{ctx['next_session']}（{ctx['next_label']}）恢復交易。"
        )
    return (
        f"{head}台股週末休市；最後交易日 {ctx['last_session']}，"
        f"下一個交易日 {ctx['next_session']}（{ctx['next_label']}）。"
    )


def us_reaction_note(ctx: dict) -> str:
    """Which Taiwan session reacts to the US moves in this report (calendar + 13:30 close)."""
    if not ctx.get("calendar_known", True):
        return "台股行事曆未涵蓋今天，美股變化會反映在哪個台股交易日請以證交所公告為準，本報告僅為當日觀察紀錄。"
    if ctx["tw_open_today"] and ctx.get("tw_before_close"):
        return f"前一晚美股的變化會反映在今天（{ctx['date']}）的台股盤中，本報告僅為觀察紀錄。"
    if ctx["tw_open_today"] and ctx.get("tw_after_close"):
        return (
            f"台股今日已收盤，美股接下來的變化會在下一個交易日 {ctx['next_session']}（{ctx['next_label']}）反映，"
            "本報告僅為當日觀察紀錄。"
        )
    if ctx["tw_open_today"]:
        return (
            f"台股今日正常交易：13:30 收盤前，前一晚美股變化反映在今天盤中；收盤後的美股變化會在下一個交易日 "
            f"{ctx['next_session']}（{ctx['next_label']}）反映，本報告僅為當日觀察紀錄。"
        )
    reason = f"因{ctx['closure_label']}休市" if ctx.get("is_holiday_break") else "週末休市"
    return (
        f"台股{reason}，美股的變化會在下一個交易日 {ctx['next_session']}（{ctx['next_label']}）反映；"
        "休市期間美股仍在交易，趨勢隨時可能反轉，本報告僅為當日觀察紀錄。"
    )


def weekly_title(ctx: dict) -> str:
    if ctx["is_holiday_break"]:
        return f"🇺🇸 {ctx['closure_label']}休市期間美股回顧｜台股{ctx['next_label']}開盤前觀察"
    return f"🇺🇸 本週美股回顧｜台股{ctx['next_label']}開盤前觀察"


def us_observation_title(ctx: dict) -> str:
    if ctx["is_holiday_break"]:
        return f"🇺🇸 {ctx['closure_label']}休市美股觀察日記"
    return "🇺🇸 美股盤後觀察日記"


def us_topics(today=None) -> list[str]:
    year = (as_date(today) or datetime.date.today()).year
    return [
        "NVIDIA AI chip demand",
        "TSM TSMC ADR stock",
        "US semiconductor industry outlook",
        f"Federal Reserve interest rate {year}",
    ]


# === TSM ADR premium ===

def _positive(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def compute_adr_premium(tsm_usd, usd_twd, tw_close_twd, ratio=ADR_RATIO, *, tw_date=None) -> dict | None:
    tsm_usd, usd_twd, tw_close_twd = _positive(tsm_usd), _positive(usd_twd), _positive(tw_close_twd)
    if not (tsm_usd and usd_twd and tw_close_twd and ratio):
        return None
    implied = tsm_usd * usd_twd / ratio
    day = as_date(tw_date)
    return {
        "tsm_usd": round(tsm_usd, 2),
        "usd_twd": round(usd_twd, 3),
        "tw_close": round(tw_close_twd, 2),
        "tw_date": day.isoformat() if day else None,  # trade date of the 2330 bar (None = unknown)
        "ratio": ratio,
        "implied_twd": round(implied, 2),
        "premium_pct": round((implied / tw_close_twd - 1) * 100, 2),
    }


def _bar_date(result, data) -> str | None:
    meta = (result.get("meta") or {}) if is_fetch_result(result) else {}
    for value in (meta.get("bar_date"), (data or {}).get("bar_date")):
        day = as_date(value)
        if day:
            return day.isoformat()
    return None


def fetch_adr_premium(fetcher, tsm_usd) -> dict | None:
    """2330.TW latest price (+ its bar date) and USD/TWD (TWD=X) from the fetcher; None on any failure."""
    try:
        tw_result = fetcher.get_stock_data("2330.TW")
        tw = unwrap_fetch_result(tw_result) or {}
        fx = unwrap_fetch_result(fetcher.get_stock_data("TWD=X")) or {}
    except Exception:
        return None
    return compute_adr_premium(tsm_usd, fx.get("price"), tw.get("price"), tw_date=_bar_date(tw_result, tw))


def _tw_price_line(adr: dict, ctx: dict) -> tuple[str, bool]:
    """(2330 price label, is_intraday). Never names a date the calendar / bar cannot back."""
    price, bar_day, today = adr["tw_close"], adr.get("tw_date"), ctx.get("date")
    last = ctx.get("last_session")
    if bar_day and bar_day == today and ctx.get("clock_before_close"):
        return f"台股 2330 盤中報價：{price} 元（{today} 13:30 尚未收盤，不是收盤價）", True
    # Before 13:30 on a session day (or a weekday the calendar cannot classify)
    # an undated quote may be today's intraday price.
    maybe_open = ctx.get("tw_before_close") or (ctx.get("tw_open_today") is None and ctx.get("clock_before_close"))
    if maybe_open and not bar_day:
        prior = f"或 {last} 的收盤價" if last else "或前一交易日收盤價"
        return f"台股 2330 最新報價：{price} 元（今日 13:30 尚未收盤：可能是盤中價{prior}）", True
    if bar_day:
        return f"台股 2330 收盤：{price} 元（交易日 {bar_day}）", False
    suffix = f"，最後交易日 {last}" if last else ""
    return f"台股 2330 最新收盤：{price} 元{suffix}", False


def format_adr_block(adr: dict | None, ctx: dict | None = None) -> str:
    if not adr:
        return "- ADR 換算資料不足（缺 2330 收盤、匯率或 TSM 價格）：本報告不得提及 ADR 溢價／折價數字。"
    side = "溢價" if adr["premium_pct"] >= 0 else "折價"
    price_line, intraday = _tw_price_line(adr, ctx or {})
    basis = "；台股價格為盤中／未收盤報價，收盤後數字會變" if intraday else ""
    return "\n".join(
        [
            f"- 台積電 ADR (TSM)：{adr['tsm_usd']} 美元；USD/TWD：{adr['usd_twd']}；1 ADR = {adr['ratio']} 股",
            f"- ADR 換算每股：{adr['implied_twd']} 元；{price_line}",
            f"- ADR 相對台股{side} {adr['premium_pct']:+.2f}%（本機計算{basis}；提到溢價／折價時只能引用這個數字）",
        ]
    )


# === Previous US observation reports (date-windowed) ===

_US_REPORT_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def find_recent_us_reports(reports_dir, before_date, window_days=7) -> list[tuple[str, str]]:
    """
    US observation reports dated in [before_date - window_days, before_date).

    Reads the special_topics layout, legacy reports/us_market_report_*.md and
    archive/02_US_Reports. The date window (never today, at most window_days
    back) keeps same-day reruns and last season's archived files out.
    """
    before = as_date(before_date) or datetime.date.today()
    start = before - datetime.timedelta(days=window_days)
    patterns = [
        os.path.join(reports_dir, "special_topics", f"*_{US_OBSERVATION_SLUG}", f"special_report_*_{US_OBSERVATION_SLUG}.md"),
        os.path.join(reports_dir, "us_market_report_*.md"),
        os.path.join(reports_dir, "archive", "02_US_Reports", "us_market_report_*.md"),
    ]
    by_date = {}
    for pattern in patterns:
        for path in glob.glob(pattern):
            match = _US_REPORT_DATE_RE.search(os.path.basename(path))
            day = as_date(match.group(1)) if match else None
            if day is None or not (start <= day < before):
                continue
            by_date.setdefault(day.isoformat(), path)
    return sorted(by_date.items())


# === Taiwan week facts from local daily structured_data ===

_AMOUNT_RE = re.compile(r"^(?P<name>.+?)\s*[\(（]\s*(?P<amount>[+-]?\d+(?:\.\d+)?)\s*億\s*[\)）]")


def _parse_amount_item(text):
    match = _AMOUNT_RE.match(str(text or "").strip())
    if not match:
        return None
    return match.group("name").strip(), float(match.group("amount"))


def collect_taiwan_week_facts(reports_dir, as_of_date, days=7) -> dict:
    as_of = as_date(as_of_date) or datetime.date.today()
    start = as_of - datetime.timedelta(days=days)
    chosen = {}
    for path in glob.glob(os.path.join(reports_dir, DAILY_DIRNAME, "*", DAILY_ARTIFACTS["structured_data"])):
        day = as_date(os.path.basename(os.path.dirname(path)))
        if day is not None and start < day <= as_of:
            chosen[day.isoformat()] = path

    rows = []
    buy_totals, sell_totals = {}, {}
    for day in sorted(chosen):
        try:
            with open(chosen[day], "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        top_buy = [str(x) for x in (data.get("safe_havens_bought") or []) if x][:5]
        top_sell = [str(x) for x in (data.get("heavyweights_dumped") or []) if x][:5]
        rows.append({"date": day, "index_action": data.get("index_action") or "", "top_buy": top_buy, "top_sell": top_sell})
        for item, totals in [(x, buy_totals) for x in top_buy] + [(x, sell_totals) for x in top_sell]:
            parsed = _parse_amount_item(item)
            if parsed:
                name, amount = parsed
                total, count = totals.get(name, (0.0, 0))
                totals[name] = (total + amount, count + 1)

    def _leaders(totals, reverse):
        ordered = sorted(totals.items(), key=lambda kv: kv[1][0], reverse=reverse)
        return [{"name": name, "amount_100m": round(total, 1), "days": count} for name, (total, count) in ordered[:5]]

    return {
        "from": (start + datetime.timedelta(days=1)).isoformat(),
        "to": as_of.isoformat(),
        "days": rows,
        "leaders_buy": _leaders(buy_totals, True),
        "leaders_sell": _leaders(sell_totals, False),
        "has_institutional": any(r["top_buy"] or r["top_sell"] for r in rows),
    }


def _series_day(label, as_of: datetime.date) -> datetime.date | None:
    """daily_series dates are 'MM/DD' (no year): resolve against as_of, wrapping at New Year."""
    match = re.match(r"^\s*(\d{1,2})/(\d{1,2})\s*$", str(label or ""))
    if not match:
        return as_date(label)
    month, day = int(match.group(1)), int(match.group(2))
    for year in (as_of.year, as_of.year - 1):
        try:
            candidate = datetime.date(year, month, day)
        except ValueError:  # e.g. 02/29 in a non-leap year
            continue
        if candidate <= as_of + datetime.timedelta(days=1):
            return candidate
    return None


def this_week_index_summary(weekly: dict | None, as_of_date) -> dict | None:
    """
    Close / change of the sessions dated in as_of's own week (Monday..as_of).

    get_weekly_stock_data returns the last N bars, which in a holiday week
    reach back into the previous week. The rows actually traded this week are
    picked by date (the bars themselves are the ground truth, so this also
    works where data/twse_closed_days.json has no coverage); the base is the
    close before the first of them. None when no session falls in the week.
    """
    as_of = as_date(as_of_date) or datetime.date.today()
    monday = as_of - datetime.timedelta(days=as_of.weekday())
    rows = []
    for row in (weekly or {}).get("daily_series") or []:
        day = _series_day(row.get("date"), as_of)
        if day is not None and monday <= day <= as_of:
            rows.append((day, row))
    if not rows:
        return None
    try:
        first, last = rows[0][1], rows[-1][1]
        base = float(first["close"]) - float(first["change"])
        close = float(last["close"])
    except (KeyError, TypeError, ValueError):
        return None
    if base <= 0:
        return None
    change = close - base
    return {
        "week_close": round(close, 2),
        "week_change": round(change, 2),
        "week_pct_change": round(change / base * 100, 2),
        "sessions": len(rows),
        "first_date": rows[0][0].isoformat(),
        "last_date": rows[-1][0].isoformat(),
    }


def format_taiwan_week_block(facts: dict | None, twii: dict | None = None) -> str:
    facts = facts or {}
    lines = []
    if twii and twii.get("week_close") is not None:
        pct = twii.get("week_pct_change")
        pct_text = f"（{pct:+.2f}%）" if isinstance(pct, (int, float)) else ""
        span = ""
        if twii.get("sessions"):
            span = f"（本週 {twii['sessions']} 個交易日：{twii['first_date']}～{twii['last_date']}，與上週最後收盤比較）"
        lines.append(
            f"- 加權指數（^TWII）本週收 {twii['week_close']} 點，週漲跌 {twii.get('week_change')} 點{pct_text}{span}"
        )
    for row in facts.get("days") or []:
        part = f"- {row['date']}：大盤 {row['index_action'] or '未記錄'}"
        if row["top_buy"]:
            part += f"；法人買超榜 {'、'.join(row['top_buy'])}"
        if row["top_sell"]:
            part += f"；賣超榜 {'、'.join(row['top_sell'])}"
        lines.append(part)
    if facts.get("leaders_buy") or facts.get("leaders_sell"):
        def _fmt(rows):
            return "、".join(f"{r['name']} {r['amount_100m']:+.1f}億（{r['days']} 天）" for r in rows)

        lines.append(
            "- 本週進榜加總（只計入當日進入前 5 名的日子，不是完整週累計）："
            f"買超 {_fmt(facts.get('leaders_buy') or []) or '無'}；賣超 {_fmt(facts.get('leaders_sell') or []) or '無'}"
        )
    if not lines:
        return "- 台股本週資料不足：本報告不得提及台股指數點位、法人買賣超或個股籌碼。"
    if not facts.get("has_institutional"):
        lines.append("- 本週沒有本機法人資料：不得描述外資／投信買賣超或個股籌碼。")
    return "\n".join(lines)


# === Weekend special prompt (neutral, ground-truth only) ===

def _format_us_weekly(market_data) -> str:
    lines = []
    for symbol, data in (market_data or {}).items():
        if not data:
            lines.append(f"- {symbol}: 資料未取得")
            continue
        line = (
            f"- {symbol}: 本週收盤 {data.get('week_close')}, 本週漲跌 {data.get('week_change')} "
            f"({data.get('week_pct_change')}%), 本週高 {data.get('week_high')}, 本週低 {data.get('week_low')}"
        )
        series = data.get("daily_series") or []
        if series:
            last = series[-1]
            line += f"  [最後一日 {last.get('date')}: 收 {last.get('close')}, 單日 {last.get('pct_change')}%]"
        lines.append(line)
    return "\n".join(lines) or "- 美股數據未取得"


def _format_news(items, limit=12) -> str:
    lines = []
    for item in (items or [])[:limit]:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        snippet = " ".join(str(item.get("content") or "").split())[:160]
        line = f"- {title}"
        if snippet:
            line += f"\n  摘要：{snippet}"
        if item.get("url"):
            line += f"\n  來源：{item['url']}"
        lines.append(line)
    return "\n".join(lines) or "- 本次未取得相關新聞"


def build_weekend_special_prompt(
    *,
    date_str,
    session_ctx,
    us_market_data,
    taiwan_block,
    adr_block,
    commodity_data=None,
    macro_events=None,
    news=None,
) -> str:
    commodity_lines = [
        f"- {row.get('name')}: {row.get('price')} ({row.get('pct_change'):+.2f}%)"
        for row in (commodity_data or {}).values()
        if row and isinstance(row.get("pct_change"), (int, float))
    ]
    return f"""
You are a macro analyst writing a weekend recap for Taiwan stock investors.
Create a "Weekend Special" report for {date_str}.
CRITICAL: The entire report MUST BE WRITTEN IN TRADITIONAL CHINESE (繁體中文) Markdown.

# 台股行事曆
- {format_session_context(session_ctx)}

# 台股本週數據（本機日報彙整；台股數字唯一來源）
{taiwan_block}

# 美股與波動指標一週數據
{_format_us_weekly(us_market_data)}

# 台積電 ADR 換算（本機計算）
{adr_block}

# 國際商品
{chr(10).join(commodity_lines) or "- 商品數據未取得"}

# 總經行事曆與新聞（標題＋摘要）
{_format_news((macro_events or []) + (news or []), limit=18)}

# 資料使用規則（最重要）
- 台股指數位階、法人買賣超、個股籌碼只能引用「台股本週數據」；該區塊寫「資料不足」時，不得提及具體點位、法人金額或個股籌碼。
- {NO_INVENTION_RULE}
- {NEUTRAL_WORDING_RULE}
- {NO_PREDICTION_RULE}
- 新聞若沒有地緣政治事件，就不要把漲跌歸因於戰爭或地緣政治。

# 報告結構
1. **本週美股回顧**：依 ^GSPC、^IXIC 的週漲跌幅描述整週走勢與最後一個交易日表現；^VIX 依數據描述，不預設恐慌。
2. **總經與避險資產**：只連結新聞中確實出現的事件；黃金、原油、白銀依數據描述。
3. **半導體與台積電 ADR**：NVDA、TSM 的本週表現；ADR 溢價只能引用上方換算區塊。
4. **台股本週回顧與下個交易日觀察重點**：引用台股本週數據（指數、法人榜），列出 2～3 個要觀察的變數與失效條件；不預測開盤方向。
5. **穩健／積極兩種觀察框架**：各 2～3 點，用「追蹤、等待確認、留意」等觀察語，不給買賣指令。

請直接輸出完整報告。
"""


def special_output_dir(reports_dir, date_str, slug) -> str:
    folder = special_topic_dir(reports_dir, date_str, slug)
    os.makedirs(folder, exist_ok=True)
    return folder
