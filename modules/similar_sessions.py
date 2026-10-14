"""Find analog trading days from local structured_data.

No embeddings. Query today's tape (index + foreign buy/sell leaders) against
historical daily cards, then attach that day's prediction and next-day rhythm.

Used by /1 before Gemini so the report can cite real analog dates instead of
the last 3500 chars of wiki.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import Any

from modules.report_paths import date_from_structured_path, iter_structured_data_paths
from modules.tickers import bare_ticker

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOCK_DB_PATH = os.path.join(BASE_DIR, "data", "stock_db.json")
DEFAULT_REPORTS_DIR = os.path.join(BASE_DIR, "reports")
DEFAULT_ANALYTICS_DB = os.path.join(BASE_DIR, "data", "analytics.db")

INDEX_PCT_RE = re.compile(r"（([+-]?\d+(?:\.\d+)?)%）")
AMOUNT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*億")
TICKER_RE = re.compile(r"\b(\d{4,6}[A-Z]?)\b", re.I)
TOP_N = 5
TOP_K = 5
MIN_SCORE = 45
# Sessions right before the query are autocorrelation, not analogs.
EXCLUDE_RECENT_SESSIONS = 5
# Index weights / ETFs sit on the foreign sell list most days (2330 led it on
# ~1/3 of local sessions), so they carry no analog information.
DIRECTION_TICKERS = {"2330", "2317", "2454"}
# Index part of the score. A flat session (|%| <= 0.08) is a dead band, not a
# direction: it is comparable with any quiet session (|%| < QUIET_MOVE_PCT) of
# either sign. Requiring "flat == flat" left a flat query only the handful of
# other ±0.08% days (6 of 133 local sessions), so 2026-10-12 had no candidate.
DIRECTIONAL_BASE = 22
QUIET_BASE = 12
QUIET_MOVE_PCT = 0.5


def _norm_ticker(value) -> str:
    return bare_ticker(value)


def _is_noise_key(key: str) -> bool:
    key = str(key or "")
    return key in DIRECTION_TICKERS or key.startswith("00")


def _clean(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def load_stock_maps(stock_db: dict | None = None) -> tuple[dict[str, str], dict[str, str]]:
    if stock_db is None:
        stock_db = {}
        if os.path.isfile(STOCK_DB_PATH):
            try:
                with open(STOCK_DB_PATH, encoding="utf-8") as fh:
                    loaded = json.loads(fh.read())
            except (OSError, json.JSONDecodeError):
                loaded = {}
            if isinstance(loaded, dict):
                stock_db = loaded
    name_to_ticker: dict[str, str] = {}
    ticker_to_name: dict[str, str] = {}
    for ticker, info in stock_db.items():
        sid = _norm_ticker(ticker)
        name = _clean((info or {}).get("name") if isinstance(info, dict) else "")
        if sid:
            ticker_to_name[sid] = name or ticker_to_name.get(sid, "")
        if name:
            name_to_ticker[name] = sid
    return name_to_ticker, ticker_to_name


def parse_index_pct(text) -> float | None:
    blob = _clean(text)
    if not blob:
        return None
    match = INDEX_PCT_RE.search(blob)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", blob)
    if not match:
        return None
    try:
        pct = float(match.group(1))
    except ValueError:
        return None
    if "跌" in blob and pct > 0:
        return -pct
    return pct


def index_dir(pct: float | None, text: str | None = None) -> str | None:
    if pct is None:
        # Many index_action strings only give points ("下跌 795.17點").
        blob = _clean(text)
        if re.search(r"(上漲|大漲|小漲|微漲|狂飆|飆升|強漲|強彈|反彈|回升|收紅|走高)", blob):
            return "up"
        if re.search(r"(下跌|大跌|小跌|微跌|慘跌|重挫|收黑|走低)", blob):
            return "down"
        if re.search(r"(平盤|持平)", blob):
            return "flat"
        return None
    if pct > 0.08:
        return "up"
    if pct < -0.08:
        return "down"
    return "flat"


def parse_flow_item(text, name_to_ticker: dict[str, str] | None = None) -> dict[str, Any]:
    raw = _clean(text)
    name_to_ticker = name_to_ticker or {}
    amount = None
    amt_match = AMOUNT_RE.search(raw)
    if amt_match:
        try:
            amount = float(amt_match.group(1))
        except ValueError:
            amount = None
    ticker = ""
    for match in TICKER_RE.finditer(raw):
        token = _norm_ticker(match.group(1))
        if token.startswith("00") and len(token) >= 4:
            ticker = token
            break
        if len(token) == 4:
            ticker = token
            break
        ticker = token
    label = raw
    if amt_match:
        label = raw[: amt_match.start()]
    label = re.sub(r"[（(].*$", " ", label)
    label = re.sub(r"[：:].*$", "", label)
    name = _clean(label).strip(" -·,，()（）")
    if not ticker and name in name_to_ticker:
        ticker = name_to_ticker[name]
    if not name and ticker:
        name = ticker
    return {
        "ticker": ticker,
        "name": name,
        "amount": amount,
        "raw": raw,
    }


def _flow_list(items, name_to_ticker, limit=TOP_N) -> list[dict[str, Any]]:
    out = []
    for item in items or []:
        if isinstance(item, dict):
            ticker = _norm_ticker(item.get("id") or item.get("ticker"))
            name = _clean(item.get("name") or "")
            amount = item.get("est_amount")
            try:
                amount = float(amount) if amount not in (None, "") else None
            except (TypeError, ValueError):
                amount = None
            if item.get("raw"):
                raw = _clean(item.get("raw"))
            elif amount is not None:
                raw = _clean(f"{name} ({amount:+.1f}億)")
            else:
                raw = name
            parsed = {
                "ticker": ticker,
                "name": name or ticker,
                "amount": amount,
                "raw": raw,
            }
        else:
            parsed = parse_flow_item(item, name_to_ticker)
        if not parsed.get("ticker") and not parsed.get("name"):
            continue
        out.append(parsed)
        if len(out) >= limit:
            break
    return out


def _prediction_rows(items) -> list[dict[str, str]]:
    rows = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        ticker = _norm_ticker(item.get("id") or item.get("ticker"))
        name = _clean(item.get("name") or "")
        if not ticker and not name:
            continue
        rows.append(
            {
                "ticker": ticker,
                "name": name,
                "direction": _clean(item.get("direction") or ""),
                "trigger": _clean(item.get("trigger") or item.get("reason") or "")[:120],
            }
        )
    return rows[:4]


def _result_lines(items) -> list[str]:
    lines = []
    for item in items or []:
        if isinstance(item, dict):
            ticker = _clean(item.get("id") or item.get("ticker") or "")
            name = _clean(item.get("name") or "")
            result = _clean(item.get("result") or "")
            pct = item.get("pct_change")
            if isinstance(pct, (int, float)):
                pct_text = f"{pct:+.2f}%"
            else:
                pct_text = _clean(pct)
            bit = " ".join(x for x in (ticker, name) if x)
            extra = " ".join(x for x in (result, pct_text) if x)
            line = f"{bit}：{extra}".strip("：")
        else:
            line = _clean(item)
        if line:
            lines.append(line[:160])
        if len(lines) >= 4:
            break
    return lines


def _keys(rows: list[dict[str, Any]]) -> set[str]:
    keys = set()
    for row in rows:
        ticker = _norm_ticker(row.get("ticker"))
        if ticker:
            keys.add(ticker)
        elif row.get("name"):
            keys.add("n:" + str(row["name"]))
    return keys


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def card_from_structured(data: dict | None, stock_db: dict | None = None) -> dict[str, Any] | None:
    data = data or {}
    date_str = str(data.get("date") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        return None
    name_to_ticker, ticker_to_name = load_stock_maps(stock_db)
    bought = _flow_list(data.get("safe_havens_bought"), name_to_ticker)
    sold = _flow_list(data.get("heavyweights_dumped"), name_to_ticker)
    for row in bought + sold:
        if row.get("ticker") and not row.get("name"):
            row["name"] = ticker_to_name.get(row["ticker"], row["ticker"])
    index_action = _clean(data.get("index_action") or "")
    pct = parse_index_pct(index_action)
    frame = data.get("editorial_frame") or {}
    frame_label = ""
    if isinstance(frame, dict):
        frame_label = _clean(frame.get("label") or frame.get("frame") or "")
    return {
        "date": date_str,
        "index_action": index_action,
        "index_pct": pct,
        "index_dir": index_dir(pct, index_action),
        "bought": bought,
        "sold": sold,
        "predictions": _prediction_rows(data.get("prediction_targets")),
        "prev_day_picks_result": _result_lines(data.get("prev_day_picks_result")),
        "frame_label": frame_label,
        "next_date": None,
        "next_day_results": [],
    }


def card_from_live(
    date_str: str,
    market_data: dict | None = None,
    institutional_data: dict | None = None,
    editorial_frame: dict | None = None,
    stock_db: dict | None = None,
) -> dict[str, Any] | None:
    from modules.structured_facts import build_local_structured_facts

    facts = build_local_structured_facts(
        date_str=date_str,
        market_data=market_data,
        institutional_data=institutional_data,
    )
    if editorial_frame:
        facts["editorial_frame"] = editorial_frame
    return card_from_structured(facts, stock_db=stock_db)


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.loads(fh.read())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _index_pct_by_date(db_path: str | None) -> dict[str, float]:
    """^TWII daily % from local market_snapshots (fills index_action without a %)."""
    if not db_path or not os.path.exists(db_path):
        return {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    try:
        rows = conn.execute(
            "SELECT snapshot_date, pct_change FROM market_snapshots "
            "WHERE ticker IN ('^TWII', 'TWII') AND pct_change IS NOT NULL"
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    return {str(day): float(pct) for day, pct in rows}


def load_history(
    reports_dir: str | None = None,
    stock_db: dict | None = None,
    *,
    before_date: str | None = None,
    index_db_path: str | None = DEFAULT_ANALYTICS_DB,
) -> list[dict[str, Any]]:
    """
    Historical daily cards. With `before_date` only sessions strictly before it
    are loaded (and next-day links stay inside that window), so a query for a
    past date never sees its own future. A card whose index_action has no %
    takes the ^TWII % from local snapshots when available.
    """
    reports_dir = reports_dir or DEFAULT_REPORTS_DIR
    index_pct = _index_pct_by_date(index_db_path)
    by_date: dict[str, dict[str, Any]] = {}
    paths = sorted(
        iter_structured_data_paths(reports_dir),
        key=lambda path: (
            date_from_structured_path(path) or "",
            0 if "/daily/" in path.replace("\\", "/") else 1,
            path,
        ),
    )
    for path in paths:
        date_str = date_from_structured_path(path)
        if not date_str or date_str in by_date:
            continue
        if before_date and date_str >= str(before_date):
            continue
        data = _read_json(path)
        if not data:
            continue
        if not data.get("date"):
            data["date"] = date_str
        card = card_from_structured(data, stock_db=stock_db)
        if card:
            if card.get("index_pct") is None and date_str in index_pct:
                card["index_pct"] = index_pct[date_str]
                card["index_dir"] = index_dir(card["index_pct"])
            by_date[date_str] = card
    dates = sorted(by_date)
    for i, date_str in enumerate(dates):
        if i + 1 >= len(dates):
            continue
        nxt = by_date[dates[i + 1]]
        by_date[date_str]["next_date"] = nxt["date"]
        by_date[date_str]["next_day_results"] = list(nxt.get("prev_day_picks_result") or [])
        by_date[date_str]["next_index_dir"] = nxt.get("index_dir")
        by_date[date_str]["next_index_pct"] = nxt.get("index_pct")
    return [by_date[d] for d in dates]


def index_match(query: dict, hist: dict) -> tuple[int | None, str]:
    """
    (index base points, reason), base None when the two sessions are not
    comparable: same direction (22), both flat (12), or flat vs a quiet move of
    either sign (12, needs that move's %). Up vs down never matches.
    """
    q_dir = query.get("index_dir")
    h_dir = hist.get("index_dir")
    if not q_dir or not h_dir:
        return None, "大盤方向不明，不比對"
    if q_dir == h_dir:
        return (DIRECTIONAL_BASE, "大盤同向") if q_dir != "flat" else (QUIET_BASE, "大盤同為平盤")
    if "flat" in (q_dir, h_dir):
        other = hist.get("index_pct") if q_dir == "flat" else query.get("index_pct")
        if other is not None and abs(float(other)) < QUIET_MOVE_PCT:
            return QUIET_BASE, "大盤都是小幅震盪（平盤 vs 小漲跌）"
        return None, "大盤一邊平盤、一邊明顯漲跌"
    return None, "大盤方向相反"


def pair_threshold(base: int | None, min_score: int = MIN_SCORE) -> int | None:
    """
    Score a pair needs to count as an analog. The flow evidence required is the
    same for every pair (min_score − DIRECTIONAL_BASE points), so a quiet pair
    with its smaller index base needs the same overlap as a directional one.
    """
    if base is None:
        return None
    return int(min_score) - (DIRECTIONAL_BASE - int(base))


def score_pair(query: dict, hist: dict) -> tuple[int, list[str]]:
    """
    Structural similarity. Requires comparable index moves (index_match);
    index weights and ETFs are dropped before comparing flow lists (they are
    on the sell list most days); a missing index % never earns the small-move
    bonus.
    """
    base, index_reason = index_match(query, hist)
    if base is None:
        return 0, [index_reason]
    score = base
    reasons: list[str] = [index_reason]

    q_pct = query.get("index_pct")
    h_pct = hist.get("index_pct")
    if q_pct is not None and h_pct is not None:
        if abs(q_pct) >= 1.0 and abs(h_pct) >= 1.0:
            score += 8
            reasons.append("都是大波動日")
        elif abs(q_pct) < 0.35 and abs(h_pct) < 0.35:
            score += 4

    def _signal_keys(rows):
        return {key for key in _keys(rows) if not _is_noise_key(key)}

    q_buy, q_sell = _signal_keys(query.get("bought") or []), _signal_keys(query.get("sold") or [])
    h_buy, h_sell = _signal_keys(hist.get("bought") or []), _signal_keys(hist.get("sold") or [])
    jb, js = _jaccard(q_buy, h_buy), _jaccard(q_sell, h_sell)
    score += round(jb * 38)
    score += round(js * 38)
    same_buy = sorted(x for x in (q_buy & h_buy) if not str(x).startswith("n:"))
    same_sell = sorted(x for x in (q_sell & h_sell) if not str(x).startswith("n:"))
    if same_buy:
        reasons.append("同買 " + "、".join(same_buy[:4]))
    if same_sell:
        reasons.append("同賣 " + "、".join(same_sell[:4]))

    def _lead(rows):
        for row in rows or []:
            ticker = _norm_ticker(row.get("ticker"))
            if ticker and not _is_noise_key(ticker):
                return ticker
        return ""

    q_buy_lead, h_buy_lead = _lead(query.get("bought")), _lead(hist.get("bought"))
    q_sell_lead, h_sell_lead = _lead(query.get("sold")), _lead(hist.get("sold"))
    if q_buy_lead and q_buy_lead == h_buy_lead:
        score += 12
        reasons.append("買超領頭相同（排除權值/ETF）")
    if q_sell_lead and q_sell_lead == h_sell_lead:
        score += 12
        reasons.append("賣超領頭相同（排除權值/ETF）")

    reversal = _jaccard(q_sell, h_buy) + _jaccard(q_buy, h_sell)
    similar_flow = jb + js
    if reversal > similar_flow + 0.12:
        score = round(score * 0.4)
        reasons.append("較像隔日翻臉，不是同構盤")

    q_pred = {row.get("ticker") for row in (query.get("predictions") or []) if row.get("ticker")}
    h_pred = {row.get("ticker") for row in (hist.get("predictions") or []) if row.get("ticker")}
    if q_pred and h_pred and q_pred & h_pred:
        score += 6
        reasons.append("預測標的重疊")

    return int(score), reasons


def _flow_preview(rows: list[dict[str, Any]], limit=3) -> str:
    bits = []
    for row in rows[:limit]:
        name = row.get("name") or row.get("ticker") or ""
        ticker = row.get("ticker") or ""
        label = f"{ticker} {name}".strip() if ticker and name and ticker not in name else (name or ticker)
        amount = row.get("amount")
        if amount is not None:
            label = f"{label} ({amount:+.1f}億)"
        bits.append(label)
    return "、".join(bits) if bits else "（無）"


def _match_payload(hist: dict, score: int, reasons: list[str]) -> dict[str, Any]:
    preds = []
    for row in hist.get("predictions") or []:
        preds.append(
            {
                "ticker": row.get("ticker") or "",
                "name": row.get("name") or "",
                "direction": row.get("direction") or "",
                "trigger": row.get("trigger") or "",
            }
        )
    return {
        "date": hist.get("date"),
        "score": score,
        "reasons": reasons[:5],
        "index_action": hist.get("index_action") or "",
        "index_pct": hist.get("index_pct"),
        "bought": _flow_preview(hist.get("bought") or []),
        "sold": _flow_preview(hist.get("sold") or []),
        "predictions": preds,
        "next_date": hist.get("next_date"),
        "next_day_results": list(hist.get("next_day_results") or [])[:4],
        "next_index_dir": hist.get("next_index_dir"),
        "frame_label": hist.get("frame_label") or "",
    }


def _recent_session_dates(query_date: str, sessions: int) -> set[str]:
    """The `sessions` TWSE sessions right before query_date (autocorrelation window)."""
    out: set[str] = set()
    if not query_date or sessions <= 0:
        return out
    try:
        from modules.twse_calendar import prev_session

        cursor = query_date
        for _ in range(sessions):
            cursor = prev_session(cursor).isoformat()
            out.add(cursor)
    except Exception:
        return out
    return out


def find_similar(
    query: dict | None,
    history: list[dict[str, Any]] | None = None,
    *,
    exclude_date: str | None = None,
    top_k: int = TOP_K,
    min_score: int = MIN_SCORE,
    exclude_recent_sessions: int = EXCLUDE_RECENT_SESSIONS,
    diagnostics: dict | None = None,
) -> list[dict[str, Any]]:
    """
    Point-in-time analogs (only sessions before the query, minus the
    ``exclude_recent_sessions`` right before it) whose score reaches
    pair_threshold. ``diagnostics`` (optional dict) is filled with why nothing
    matched: compared / comparable counts and the closest near miss
    (see no_analog_reason).
    """
    diag = diagnostics if diagnostics is not None else {}
    diag.update({"history_sessions": len(history or []), "compared": 0, "comparable": 0, "best": None})
    if not query:
        diag["query_missing"] = True
        return []
    query_date = str(query.get("date") or exclude_date or "")
    diag["query_index_dir"] = query.get("index_dir")
    exclude = {exclude_date or "", query_date}
    recent = _recent_session_dates(query_date, exclude_recent_sessions)
    exclude |= recent
    diag["excluded_recent"] = sorted(recent)
    ranked = []
    best = None
    for hist in history or []:
        hist_date = str(hist.get("date") or "")
        if hist_date in exclude:
            continue
        if query_date and hist_date >= query_date:
            continue  # point-in-time: never compare with a future session
        diag["compared"] += 1
        base, _index_reason = index_match(query, hist)
        needed = pair_threshold(base, min_score)
        if needed is None:
            continue
        diag["comparable"] += 1
        score, reasons = score_pair(query, hist)
        if score < needed:
            gap = score - needed
            if best is None or gap > best["score"] - best["needed"]:
                best = {"date": hist_date, "score": score, "needed": needed, "reasons": reasons[:4]}
            continue
        ranked.append((score - needed, score, hist_date, reasons, hist))
    diag["best"] = best
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [_match_payload(hist, score, reasons) for _margin, score, _date, reasons, hist in ranked[:top_k]]


def no_analog_reason(diagnostics: dict | None, *, with_date: bool = True) -> str:
    """
    One-line reason for an empty find_similar result. ``with_date=False`` (the
    /1 prompt) leaves the near-miss date and its shared names out, so neither
    can be cited as an analog.
    """
    diag = diagnostics or {}
    if diag.get("query_missing"):
        return "查詢日沒有可比對的盤勢卡（日期或大盤資料缺）"
    if not diag.get("query_index_dir"):
        return "查詢日大盤漲跌不明，不比對"
    if not diag.get("compared"):
        return "本機沒有查詢日之前、且早於最近 5 個交易日的歷史盤"
    if not diag.get("comparable"):
        return f"{diag['compared']} 個歷史盤都沒有大盤同向或同為小幅震盪的日子"
    best = diag.get("best") or {}
    if not best:
        return "沒有分數夠高的歷史盤"
    if not with_date:
        return (
            f"大盤可比的 {diag['comparable']} 個歷史盤中最高分 {best['score']}，未達門檻 {best['needed']}"
            "（買賣超名單重疊不足；權值股與 ETF 不計）"
        )
    reasons = "、".join(best.get("reasons") or []) or "流向重疊不足"
    return (
        f"最接近的歷史盤 {best['date']} 分數 {best['score']}，未達門檻 {best['needed']}（{reasons}）；"
        "台積電／鴻海／聯發科與 ETF 不計入流向比對"
    )


def summarize_outcomes(matches: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Pooled next-session outcome of the analogs (prediction hits, index direction)."""
    hits = misses = up = down = flat = with_next = 0
    for row in matches or []:
        if row.get("next_date"):
            with_next += 1
        for line in row.get("next_day_results") or []:
            text = str(line)
            if "未成立" in text or "失準" in text:
                misses += 1
            elif "成立" in text or "命中" in text:
                hits += 1
        direction = row.get("next_index_dir")
        if direction == "up":
            up += 1
        elif direction == "down":
            down += 1
        elif direction == "flat":
            flat += 1
    return {
        "n": len(matches or []),
        "with_next_day": with_next,
        "prediction_hits": hits,
        "prediction_misses": misses,
        "next_index_up": up,
        "next_index_down": down,
        "next_index_flat": flat,
    }


def find_similar_for_structured(
    data: dict | None,
    reports_dir: str | None = None,
    *,
    exclude_date: str | None = None,
    stock_db: dict | None = None,
    history: list[dict[str, Any]] | None = None,
    diagnostics: dict | None = None,
) -> list[dict[str, Any]]:
    query = card_from_structured(data, stock_db=stock_db)
    if history is None:
        history = load_history(reports_dir, stock_db=stock_db, before_date=(query or {}).get("date"))
    return find_similar(
        query, history, exclude_date=exclude_date or (data or {}).get("date"), diagnostics=diagnostics
    )


def find_similar_for_live(
    date_str: str,
    *,
    market_data: dict | None = None,
    institutional_data: dict | None = None,
    editorial_frame: dict | None = None,
    reports_dir: str | None = None,
    stock_db: dict | None = None,
    diagnostics: dict | None = None,
) -> list[dict[str, Any]]:
    query = card_from_live(
        date_str,
        market_data=market_data,
        institutional_data=institutional_data,
        editorial_frame=editorial_frame,
        stock_db=stock_db,
    )
    history = load_history(reports_dir, stock_db=stock_db, before_date=date_str)
    return find_similar(query, history, exclude_date=date_str, diagnostics=diagnostics)


def format_for_prompt(matches: list[dict[str, Any]] | None, no_analog: str | None = None) -> str:
    header = (
        "# 本機相似盤（結構化比對，0 Gemini）\n"
        "規則：只能引用下列日期與數字。沒有列到的歷史日禁止寫進報告，也禁止說「史上第一次」來迴避對照。\n"
        "相似盤是弱類比（已排除權值股／ETF、要求大盤同向或同為小幅震盪、排除前 5 個交易日）。"
        "只有至少 3 個相似盤、且彙總的隔日預測多數失準時，才可據此降權 prediction_targets；"
        "1～2 個個案只能當背景對照。"
    )
    if not matches:
        why = f"\n（原因：{no_analog}）" if no_analog else ""
        return header + "\n（未找到足夠相似的歷史盤。禁止編造日期。寫「本日無足夠相似歷史盤可對照。」一句即可。）" + why
    summary = summarize_outcomes(matches)
    judged = summary["prediction_hits"] + summary["prediction_misses"]
    blocks = [
        header,
        (
            f"## 彙總（{summary['n']} 個相似盤；有隔日資料 {summary['with_next_day']} 個）\n"
            f"- 隔日預測：成立 {summary['prediction_hits']}／{judged}"
            + ("（樣本不足，不可據此降權）" if summary["n"] < 3 or judged < 3 else "")
            + f"\n- 隔日大盤：漲 {summary['next_index_up']}、跌 {summary['next_index_down']}、平 {summary['next_index_flat']}"
        ),
    ]
    for row in matches:
        pred_bits = []
        for pred in row.get("predictions") or []:
            bit = " ".join(x for x in (pred.get("ticker"), pred.get("name"), pred.get("direction")) if x)
            trigger = pred.get("trigger") or ""
            pred_bits.append(f"{bit}｜{trigger}" if trigger else bit)
        results = row.get("next_day_results") or []
        next_date = row.get("next_date") or "尚無下一交易日"
        result_text = "；".join(results) if results else "尚無隔日節奏結果"
        reason_text = "、".join(row.get("reasons") or []) or "結構相近"
        blocks.append(
            f"## {row.get('date')}（分數 {row.get('score')}；{reason_text}）\n"
            f"- 大盤：{row.get('index_action') or '（無）'}\n"
            f"- 買超：{row.get('bought') or '（無）'}\n"
            f"- 賣超：{row.get('sold') or '（無）'}\n"
            f"- 當時預測：{'；'.join(pred_bits) if pred_bits else '（當日無正式預測）'}\n"
            f"- 隔日節奏（{next_date}）：{result_text}"
        )
    return "\n\n".join(blocks)


def attach_similar_sessions(structured_data: dict | None, reports_dir: str | None = None) -> dict:
    structured_data = structured_data if isinstance(structured_data, dict) else {}
    try:
        matches = find_similar_for_structured(structured_data, reports_dir)
    except Exception:
        matches = []
    structured_data["similar_sessions"] = matches
    return structured_data
