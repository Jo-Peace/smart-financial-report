from __future__ import annotations

import datetime
import os
import re
import sqlite3

from modules.analytics_store import AnalyticsStore
from modules.market_signal_scorer import load_stock_db


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ANALYTICS_DB = os.path.join(BASE_DIR, "data", "analytics.db")

# 4-6 digit ids with an optional letter (00685L / 00981A), not glued to other
# digits, decimals or a $ price ("收$4320.7" is gold, not a ticker).
TICKER_PATTERN = re.compile(r"(?<![\d.$])(\d{4,6}[A-Z]?)(?![\d.])")
LEADING_TICKER = re.compile(r"^\s*(\d{4,6}[A-Z]?)\b")

TREND_WINDOWS = (5, 10, 20)
# A window's base snapshot may sit this many sessions off the target, since
# local snapshots only exist on days the ticker was in the dynamic universe.
WINDOW_TOLERANCE_SESSIONS = 2
# The latest snapshot must be this fresh (TWSE sessions before as_of).
MAX_STALE_SESSIONS = 2
WINDOW_CLIP_PCT = 30.0
EXTREME_PCT = 40.0
WINDOW_WEIGHTS = {"5D": 0.15, "10D": 0.35, "20D": 0.5}
MIN_DIRECTION_PCT = 3.0
LONG_TERM_PICK_LIMIT = 2
RADAR_LONG_TERM_MIN_IMPACT = 70


def _as_list(value):
    if not value:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def _as_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.datetime.strptime(str(value or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _extract_tickers_from_text(text, known=None):
    """Ticker-looking tokens; when `known` ids are given, keep only those."""
    if not text:
        return []
    found = TICKER_PATTERN.findall(str(text))
    if known:
        found = [t for t in found if t in known]
    return found


def _name_map(stock_db):
    mapping = {}
    for sid, entry in (stock_db or {}).items():
        name = _clean_text((entry or {}).get("name"))
        if len(name) >= 2:
            mapping[name] = str(sid)
    return sorted(mapping.items(), key=lambda item: -len(item[0]))


def _resolve_names(text, name_map):
    """Ticker ids for stock names written without a code (法人榜「南亞科 (+111.9億)」)."""
    blob = re.sub(r"[\(（][^)）]*[\)）]", " ", str(text or ""))
    out = []
    for name, sid in name_map:
        if name in blob:
            out.append(sid)
            blob = blob.replace(name, " ")
    return out


def _short_term_tickers(structured_data):
    out = set()
    for row in _as_list(structured_data.get("prediction_targets")):
        if isinstance(row, dict) and _clean_text(row.get("id")):
            out.add(_clean_text(row.get("id")).upper())
    return out


def _collect_tickers(structured_data, stock_db=None):
    """
    Mid-term candidate pool: validation / prediction ids, plus 法人榜, sectors,
    divergence, AI picks and focus_ranking resolved through stock_db names.
    Commodities are never scanned (gold prices look like 4-digit tickers).
    """
    stock_db = stock_db or {}
    known = {str(k).upper() for k in stock_db}
    name_map = _name_map(stock_db)
    tickers = set()

    def _add(value):
        value = _clean_text(value).upper()
        if value:
            tickers.add(value)

    for key in ("validation_results", "prev_day_picks_result"):
        for row in _as_list(structured_data.get(key)):
            if isinstance(row, dict):
                _add(row.get("id"))
            else:
                match = LEADING_TICKER.match(str(row))
                if match:
                    _add(match.group(1))

    for row in _as_list(structured_data.get("prediction_targets")):
        if isinstance(row, dict):
            _add(row.get("id"))

    for row in _as_list(structured_data.get("focus_ranking")):
        if isinstance(row, dict):
            _add(row.get("ticker"))

    texts = []
    for bucket in (
        structured_data.get("heavyweights_dumped"),
        structured_data.get("safe_havens_bought"),
        structured_data.get("price_volume_divergence"),
        structured_data.get("ai_data_picks"),
    ):
        texts.extend(str(item) for item in _as_list(bucket))
    sectors = structured_data.get("sectors") or {}
    if isinstance(sectors, dict):
        for sector, stocks in sectors.items():
            texts.append(str(sector))
            texts.extend(str(item) for item in _as_list(stocks))
    else:
        # Gemini sometimes returns sectors as a list of strings / dicts.
        texts.extend(str(item) for item in _as_list(sectors))

    for text in texts:
        for ticker in _extract_tickers_from_text(text, known=known or None):
            _add(ticker)
        for ticker in _resolve_names(text, name_map):
            _add(ticker)

    return sorted(tickers)


def _sessions_between(start, end):
    """TWSE sessions in (start, end]; weekday count when the calendar is unavailable."""
    if start is None or end is None or end <= start:
        return 0
    try:
        from modules.twse_calendar import is_twse_session
    except Exception:  # pragma: no cover
        is_twse_session = None
    count = 0
    cursor = start
    while cursor < end:
        cursor += datetime.timedelta(days=1)
        if is_twse_session is not None:
            count += 1 if is_twse_session(cursor) else 0
        elif cursor.weekday() < 5:
            count += 1
    return count


def _today_quotes(market_data):
    """{ticker: (price, pct)} from today's market_data (not yet in market_snapshots during /1)."""
    out = {}
    for symbol, data in (market_data or {}).items():
        if not isinstance(data, dict):
            continue
        ticker = re.sub(r"\.TWO?$", "", str(symbol).strip(), flags=re.I).upper()
        try:
            price = float(data.get("price"))
        except (TypeError, ValueError):
            continue
        if price > 0:
            out[ticker] = (price, data.get("pct_change"))
    return out


def load_point_in_time_trends(tickers, as_of, *, db_path=None, windows=TREND_WINDOWS, today_quotes=None):
    """
    N-session trends from local market_snapshots using only rows dated <= as_of.

    Windows are measured in TWSE sessions (not "N snapshots back", which spans
    months when snapshots are sparse). A window is kept only when a snapshot
    sits within WINDOW_TOLERANCE_SESSIONS of the target base session, and a
    ticker only when its latest snapshot is at most MAX_STALE_SESSIONS old.
    `today_quotes` ({ticker: (price, pct)}) stands in for the as_of snapshot
    that /1 writes only after this step, so /1 and a later regen agree.
    Deterministic: tickers are processed in sorted order.
    """
    today_quotes = today_quotes or {}
    as_of_d = _as_date(as_of)
    db_path = db_path or DEFAULT_ANALYTICS_DB
    tickers = sorted({_clean_text(t).upper() for t in (tickers or []) if _clean_text(t)})
    if not tickers or as_of_d is None or not os.path.exists(db_path):
        return {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    result = {}
    max_window = max(windows or (20,))
    try:
        for ticker in tickers:
            rows = conn.execute(
                """
                SELECT snapshot_date, price, pct_change
                FROM market_snapshots
                WHERE ticker = ? AND snapshot_date <= ? AND price IS NOT NULL AND price > 0
                ORDER BY snapshot_date DESC
                LIMIT 80
                """,
                (ticker, as_of_d.isoformat()),
            ).fetchall()
            rows = list(rows)
            quote = today_quotes.get(ticker)
            if quote and (not rows or str(rows[0][0]) < as_of_d.isoformat()):
                rows.insert(0, (as_of_d.isoformat(), quote[0], quote[1]))
            if not rows:
                continue
            latest_date = _as_date(rows[0][0])
            if latest_date is None or _sessions_between(latest_date, as_of_d) > MAX_STALE_SESSIONS:
                continue
            latest_price = float(rows[0][1])
            dated = []
            for day, price, _pct in rows[1:]:
                parsed = _as_date(day)
                if parsed is None:
                    continue
                gap = _sessions_between(parsed, latest_date)
                if gap > max_window + WINDOW_TOLERANCE_SESSIONS:
                    break
                dated.append((gap, day, float(price)))
            trends = {}
            for window in windows or ():
                best = None
                for gap, day, price in dated:
                    if abs(gap - window) <= WINDOW_TOLERANCE_SESSIONS:
                        key = (abs(gap - window), -gap)
                        if best is None or key < best[0]:
                            best = (key, gap, day, price)
                if best is None:
                    continue
                _, gap, day, base_price = best
                trends[f"{window}D"] = {
                    "from_date": day,
                    "to_date": rows[0][0],
                    "sessions": gap,
                    "base_price": base_price,
                    "latest_price": latest_price,
                    "pct_change": (latest_price - base_price) / base_price * 100,
                }
            result[ticker] = {
                "latest_date": rows[0][0],
                "latest_price": latest_price,
                "latest_pct_change": rows[0][2],
                "trends": trends,
            }
    except sqlite3.Error:
        return result
    finally:
        conn.close()
    return result


def _format_horizon_summary(horizon_rows):
    from modules.horizon_reliability import format_horizon_summary_with_read

    return format_horizon_summary_with_read(horizon_rows)


def _format_recent_results(recent_results):
    if not recent_results:
        return ["- 無"]
    lines = []
    for row in recent_results[:5]:
        horizon = row.get("horizon_days")
        ticker = row.get("ticker", "")
        name = row.get("name", "")
        result_desc = row.get("result_desc") or ""
        ret = row.get("return_pct")
        hit = row.get("hit")
        ret_text = f"{float(ret):+.2f}%" if ret is not None else "N/A"
        hit_text = "命中" if hit == 1 else ("失準" if hit == 0 else "未判定")
        lines.append(f"- {ticker} {name}：{horizon}D {hit_text}；{ret_text}；{result_desc}")
    return lines


def _build_short_term_picks(structured_data):
    picks = []
    for target in _as_list(structured_data.get("prediction_targets"))[:2]:
        if not isinstance(target, dict):
            continue
        picks.append(
            {
                "ticker": _clean_text(target.get("id")),
                "name": _clean_text(target.get("name")),
                "direction": _clean_text(target.get("direction", "多")),
                "time_scale": "短線",
                "horizon": "1~5日",
                "validation_window": "1D / 3D / 5D",
                "reason": _clean_text(target.get("trigger")),
                "invalidation": _clean_text(target.get("stop_loss_desc") or target.get("stop_loss_price") or ""),
            }
        )

    if not picks:
        for pick in _as_list(structured_data.get("ai_data_picks"))[:1]:
            picks.append(
                {
                    "ticker": "",
                    "name": _clean_text(pick),
                    "direction": "觀察",
                    "time_scale": "短線",
                    "horizon": "1~5日",
                    "validation_window": "1D / 3D / 5D",
                    "reason": _clean_text(pick),
                    "invalidation": "若無量價延續則降級觀察",
                }
            )

    return picks


def _trend_pick(ticker, info, stock_db):
    """Signed mid-term trend pick, or None when the trend is not established."""
    trends = info.get("trends") or {}
    if "10D" not in trends and "20D" not in trends:
        return None
    signed = {window: float(trend.get("pct_change", 0.0)) for window, trend in trends.items()}
    long_windows = [signed[w] for w in ("10D", "20D") if w in signed]
    if len(long_windows) == 2 and long_windows[0] * long_windows[1] < 0:
        return None  # 10D and 20D disagree: no mid-term direction
    extreme = any(abs(v) >= EXTREME_PCT for v in signed.values())
    score = 0.0
    for window, pct in signed.items():
        clipped = max(-WINDOW_CLIP_PCT, min(WINDOW_CLIP_PCT, pct))
        score += WINDOW_WEIGHTS.get(window, 0.1) * clipped
    if extreme:
        score *= 0.5
    anchor = signed.get("20D", signed.get("10D"))
    if abs(anchor) < MIN_DIRECTION_PCT:
        # No mid-term direction: emitting a 「觀察」 row would take a pick slot
        # without evidence (and read as if every window were small).
        return None
    direction = "多" if anchor > 0 else "空"
    name = _clean_text((stock_db.get(ticker) or {}).get("name"))
    as_of = info.get("latest_date")
    order = sorted(signed, key=lambda w: int(w[:-1]))
    parts = [f"{w} {signed[w]:+.2f}%" for w in order]
    spans = [
        f"{w} {trends[w].get('from_date')}→{trends[w].get('to_date')}（{trends[w].get('sessions')} 交易日）"
        for w in order
    ]
    label = {"多": "中期偏多", "空": "中期偏空"}[direction]
    return {
        "score": abs(score),
        "pick": {
            "ticker": ticker,
            "name": name,
            "theme": f"{ticker} {name}".strip(),
            "time_scale": "中期",
            "horizon": "2~6週",
            "validation_window": "20D",
            "direction": direction,
            "as_of": as_of,
            "trends": {w: round(signed[w], 2) for w in order},
            "extreme": extreme,
            "thesis": f"{label}：{'、'.join(parts)}（資料截至 {as_of}）"
            + ("；含極端值，僅作背景" if extreme else ""),
            "evidence": "；".join(spans),
            "source": "local_market_snapshots",
        },
    }


def _radar_pick(structured_data, run_date):
    """Event-radar mid-term candidate, worded as unverified and source-gated."""
    from modules.editorial_frame import eligible_radar_events

    eligible, _rejected = eligible_radar_events(
        structured_data.get("supply_chain_event_radar"),
        run_date or datetime.date.today().isoformat(),
        min_impact=RADAR_LONG_TERM_MIN_IMPACT,
    )
    if not eligible:
        return None
    top = eligible[0]
    roles = "、".join(_clean_text(x) for x in _as_list(top.get("impacted_roles"))[:3])
    if not roles:
        return None
    event = _clean_text(top.get("event"))
    return {
        "ticker": "",
        "name": "",
        "theme": event,
        "time_scale": "中長期",
        "horizon": "2~6週 / 1~3個月",
        "validation_window": "20D",
        "direction": "待驗證",
        "as_of": run_date,
        "thesis": f"待驗證事件（影響分數 {top.get('impact_score', 'N/A')}）：可能影響 {roles}；需 20D 族群量價確認，不是結論。",
        "evidence": f"{roles}；來源 {_clean_text(top.get('url')) or '未附網址'}",
        "url": _clean_text(top.get("url")),
        "source": "supply_chain_event_radar",
    }


def _build_long_term_picks(structured_data, horizon_rows, recent_trends, *, stock_db=None, run_date=None):
    """
    Mid-term picks need local evidence: a signed 10D/20D trend (same sign when
    both exist, 20D anchor ≥ MIN_DIRECTION_PCT) from point-in-time snapshots,
    or a source-gated new radar event. Today's short-term picks are excluded
    so the layer adds information. No evidence → one explicit "no mid-term
    axis" row.
    """
    stock_db = stock_db if stock_db is not None else load_stock_db(BASE_DIR)
    picks = []
    radar = _radar_pick(structured_data, run_date or structured_data.get("date"))
    if radar:
        picks.append(radar)

    short_term = _short_term_tickers(structured_data)
    ranked = []
    for ticker in sorted(recent_trends or {}):
        if ticker.upper() in short_term:
            continue
        built = _trend_pick(ticker, recent_trends[ticker] or {}, stock_db)
        if built:
            ranked.append((-built["score"], ticker, built["pick"]))
    ranked.sort(key=lambda item: (item[0], item[1]))
    for _, _, pick in ranked[:LONG_TERM_PICK_LIMIT]:
        picks.append(pick)

    picks = [pick for pick in picks if _clean_text(pick.get("evidence")) not in ("", "無")]
    if not picks:
        picks.append(
            {
                "ticker": "",
                "name": "",
                "theme": "今日無明確長線主軸",
                "time_scale": "中期",
                "horizon": "2~6週 / 1~3個月",
                "validation_window": "20D",
                "direction": "觀察",
                "as_of": run_date or structured_data.get("date"),
                "thesis": "目前資料不足以建立穩定長線主軸，先等多日驗證再升級。",
                "evidence": f"本機快照沒有足夠新的 10D／20D 同向且幅度 ≥{MIN_DIRECTION_PCT:g}% 的趨勢，也沒有通過來源門檻的新事件",
                "placeholder": True,
            }
        )
    return picks


def _long_term_summary(picks):
    real = [pick for pick in picks if not pick.get("placeholder")]
    if not real:
        return "今日無明確中期主軸；等多日驗證再升級。", []
    thesis = "；".join(f"{pick.get('theme')}：{pick.get('thesis')}" for pick in real)
    evidence = [f"{pick.get('theme')}：{pick.get('evidence')}" for pick in real]
    return thesis, evidence


def enrich_structured_data_with_timeframes(
    structured_data, *, db_path=None, stock_db=None, as_of=None, market_data=None
):
    """
    Attach short / mid-term picks. Call after validation_results,
    supply_chain_event_radar and focus_ranking are on structured_data so the
    candidate pool and radar branch see them.
    """
    structured_data = structured_data or {}
    as_of = as_of or structured_data.get("date") or datetime.date.today().isoformat()
    store = AnalyticsStore(db_path) if db_path else AnalyticsStore()
    # Point-in-time like the trends: only results evaluated before as_of, so a
    # regen / resume for a past date does not quote later 驗收 in 時間窗可信度.
    horizon_rows = store.get_horizon_performance_summary(as_of=as_of)
    recent_results = store.get_recent_prediction_results(limit=8, as_of=as_of)
    stock_db = stock_db if stock_db is not None else load_stock_db(BASE_DIR)
    tickers = _collect_tickers(structured_data, stock_db)
    recent_trends = load_point_in_time_trends(
        tickers, as_of, db_path=db_path, today_quotes=_today_quotes(market_data)
    )

    from modules.horizon_reliability import (
        analyze_horizon_reliability,
        natural_reliability_sentences,
        reliability_one_liner,
    )

    reliability = analyze_horizon_reliability(horizon_rows)
    reliability_lines = natural_reliability_sentences(horizon_rows)
    one_liner = reliability_one_liner(horizon_rows)

    structured_data["short_term_horizon"] = "1~5日"
    structured_data["long_term_horizon"] = "2~6週 / 1~3個月"
    structured_data["short_term_validation_window"] = "1D / 3D / 5D"
    structured_data["long_term_validation_window"] = "20D"
    structured_data["short_term_picks"] = _build_short_term_picks(structured_data)
    long_term_picks = _build_long_term_picks(
        structured_data, horizon_rows, recent_trends, stock_db=stock_db, run_date=as_of
    )
    structured_data["long_term_picks"] = long_term_picks
    structured_data["horizon_reliability"] = {
        "primary_leads": reliability.get("primary_leads"),
        "primary_days": reliability.get("primary_days"),
        "one_liner": one_liner,
        "sentences": reliability_lines,
    }
    # Built from today's actual picks (was a fixed template nobody read).
    structured_data["long_term_thesis"], structured_data["long_term_evidence"] = _long_term_summary(long_term_picks)
    structured_data["timeframe_history"] = {
        "horizon_performance": horizon_rows,
        "recent_results": recent_results,
        "recent_trends": recent_trends,
        "tickers": tickers,
        "as_of": as_of,
        "reliability_note": one_liner,
    }
    return structured_data


def format_timeframe_history_for_prompt(structured_data):
    history = structured_data.get("timeframe_history") or {}
    reliability = structured_data.get("horizon_reliability") or {}
    lines = []
    if reliability.get("one_liner"):
        lines.append(f"【時間窗可信度】{reliability['one_liner']}")
        lines.append("")
    lines.extend(_format_horizon_summary(history.get("horizon_performance")))
    lines.extend(["", "最近驗收："])
    lines.extend(_format_recent_results(history.get("recent_results")))
    return "\n".join(line for line in lines if line is not None)
