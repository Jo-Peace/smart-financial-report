#!/usr/bin/env python3
"""
backtest_scores.py — 本機評分回測（IC／分組報酬／信心校準，不消耗任何 API）

Replays the deterministic scorers on local data only and checks whether the
scores rank next-session returns:

- inputs per session come from data/analytics.db (market_snapshots,
  institutional_flows, volume_rankings), point-in-time (rows of that date;
  price stats use snapshot_date < session, strictly before)
- 成交值 rank basis follows live /1: when that session's full tape is in
  daily_closes, the market-wide 成交值前20 (value_ranking) is rebuilt from it
  with the same ranking (DataFetcher._rank_tape_by_value, ETFs excluded;
  成交金額 ≈ close × shares because daily_closes keeps no 成交金額) and passed
  to both the signal pack and the role-score inputs; sessions without the tape
  use the observation-pool basis, as /1 does. The report counts both.
- scores: market_signal_scorer attention / direction (watch) score, and
  role_scorer title_score / elasticity / foreign_buy_strength /
  volume_confirmation. stock_db market_role is always rebuilt point-in-time
  (structural / ETF id / keyword rule + foreign top-5 sell sessions from flows
  up to the previous session — the role /1 would have had), and tracking is
  stripped by default, so roles and hit rates recorded later do not leak in
- forward returns: close on the session → close N TWSE sessions later, from
  local snapshot / volume-board closes; excess return vs ^TWII when available
- metrics: daily cross-sectional Spearman IC (mean, t-stat), pooled tercile
  mean excess return / hit rate, operator-hypothesis confidence grade vs
  directional hit rate (calibration check), and forward-close coverage

Local closes are sparse: a T+N close exists only when the name was in that
day's dynamic universe or share board, and that membership is chosen by T+N
flows / volume. The sample is therefore conditioned on the future (coverage
is reported per horizon); treat IC / t-stats as indicative, not unbiased.

Frame replay (--frames): rebuilds editorial_frame for the last N sessions
from the same point-in-time inputs /1 had (local signal pack + mechanical-flow
calendar, ^TWII snapshot %, that day's radar / validation from
structured_data when reports exist) and reports how often each frame wins;
no frame should own more than MAX_FRAME_SHARE of sessions.

Usage:
  python3 scripts/maintenance/backtest_scores.py
  python3 scripts/maintenance/backtest_scores.py --start 2026-07-01 --horizons 1,5 --json
  python3 scripts/maintenance/backtest_scores.py --frames 30
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.editorial_frame import FRAME_LABELS, build_editorial_frame  # noqa: E402
from modules.local_knowledge_compounder import (  # noqa: E402
    RISK_SELL_TOP_N,
    LocalKnowledgeCompounder,
)
from modules.market_calendar import mechanical_flow_events  # noqa: E402
from modules.market_signal_scorer import build_market_signal_pack  # noqa: E402
from modules.role_scorer import (  # noqa: E402
    build_role_score_inputs,
    load_stock_db,
    rank_focus_stocks,
)

ANALYTICS_DB = os.path.join(BASE_DIR, "data", "analytics.db")
STOCK_DB = os.path.join(BASE_DIR, "data", "stock_db.json")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
# Frame replay guard (finding 17): one frame owning most sessions means the
# selector collapsed (it used to call ~75% of sessions 重大事件日).
MAX_FRAME_SHARE = 0.5
INDEX_TICKERS = ("^TWII", "TWII")
MIN_NAMES_FOR_IC = 5
# A session whose scored names mostly lack a T+N close is flagged in the report.
LOW_COVERAGE = 0.5
SIGNAL_SCORES = ("attention_score", "direction_score")
# Same size as the /1 prompt's 「今日成交值前20名」 (DataFetcher.get_top_value_stocks).
VALUE_TOP_N = 20
VALUE_TOP_ETF_N = 5
ROLE_SCORES = ("title_score", "elasticity", "foreign_buy_strength", "volume_confirmation")
BULLISH_BIAS = ("偏多", "中性偏多", "觀察偏多")
BEARISH_BIAS = ("偏空",)


# ---------------------------------------------------------------------------
# Local data
# ---------------------------------------------------------------------------


def _connect(db_path):
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"找不到本機資料庫：{db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _norm(ticker):
    text = str(ticker or "").strip().upper()
    for suffix in (".TWO", ".TW"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def load_sessions(conn, start=None, end=None):
    rows = conn.execute("SELECT DISTINCT flow_date FROM institutional_flows ORDER BY flow_date").fetchall()
    days = [r[0] for r in rows]
    if start:
        days = [d for d in days if d >= start]
    if end:
        days = [d for d in days if d <= end]
    return days


def load_price_panel(conn):
    """{date: {ticker: close}} from snapshot prices and volume-board closes."""
    panel = defaultdict(dict)
    for row in conn.execute("SELECT snapshot_date, ticker, price FROM market_snapshots WHERE price > 0"):
        panel[row["snapshot_date"]][_norm(row["ticker"])] = float(row["price"])
    for row in conn.execute("SELECT ranking_date, ticker, close_price FROM volume_rankings WHERE close_price > 0"):
        panel[row["ranking_date"]].setdefault(_norm(row["ticker"]), float(row["close_price"]))
    return panel


def session_inputs(conn, day):
    market_data = {}
    for row in conn.execute("SELECT ticker, raw_json FROM market_snapshots WHERE snapshot_date = ?", (day,)):
        ticker = row["ticker"]
        key = ticker if ticker.startswith("^") else f"{ticker}.TW"
        try:
            market_data[key] = json.loads(row["raw_json"])
        except (TypeError, ValueError):
            continue
    institutional = {"top_buy": [], "top_sell": []}
    for row in conn.execute(
        "SELECT side, raw_json FROM institutional_flows WHERE flow_date = ? ORDER BY side, rank", (day,)
    ):
        try:
            item = json.loads(row["raw_json"])
        except (TypeError, ValueError):
            continue
        institutional["top_buy" if row["side"] == "buy" else "top_sell"].append(item)
    volume = []
    for row in conn.execute("SELECT raw_json FROM volume_rankings WHERE ranking_date = ? ORDER BY rank", (day,)):
        try:
            volume.append(json.loads(row["raw_json"]))
        except (TypeError, ValueError):
            continue
    return market_data, institutional, volume


def session_value_ranking(conn, day, top_n=VALUE_TOP_N, etf_n=VALUE_TOP_ETF_N):
    """
    The market-wide 成交值 ranking /1 had on ``day``, rebuilt from that
    session's recorded full tape (daily_closes) with the same ranking as
    DataFetcher.get_top_value_stocks / _attach_trade_value. daily_closes keeps
    close and shares only, so 成交金額 is approximated by close × shares.

    Returns (value_ranking or None, {ticker: stock rank over the whole tape}).
    None when the session has no recorded tape (→ observation-pool basis).
    """
    try:
        rows = conn.execute(
            "SELECT ticker, name, close_price, volume FROM daily_closes "
            "WHERE close_date = ? AND close_price > 0 AND volume > 0",
            (day,),
        ).fetchall()
    except sqlite3.OperationalError:  # older DB without the table
        return None, {}
    if not rows:
        return None, {}
    from modules.data_fetcher import DataFetcher

    tape = [
        {
            "id": _norm(row["ticker"]),
            "name": row["name"] or "",
            "close_price": float(row["close_price"]),
            "volume": int(row["volume"]),
            "trade_value": float(row["close_price"]) * int(row["volume"]),
        }
        for row in rows
    ]
    stocks, etfs = DataFetcher._rank_tape_by_value(tape)

    def _top(ranked, n):
        return [{**row, "rank": i, "trade_value_yi": round(row["trade_value"] / 1e8, 2)} for i, row in enumerate(ranked[:n], start=1)]

    ranking = {
        "stocks": _top(stocks, top_n),
        "etfs": _top(etfs, etf_n),
        "data_date": day,
        "basis": "daily_closes_close_x_shares",
    }
    return ranking, {row["id"]: (i, row["trade_value"]) for i, row in enumerate(stocks, start=1)}


def with_value_ranks(volume, value_ranks):
    """
    Share-board rows with value_rank from the rebuilt tape (what
    _attach_trade_value adds live), so the board and value_ranking share one
    basis; a rank recorded from another basis is dropped.
    """
    out = []
    for item in volume or []:
        row = dict(item)
        for key in ("value_rank", "value_rank_basis", "value_rank_etf"):
            row.pop(key, None)
        known = value_ranks.get(_norm(row.get("id")))
        if known:
            rank, trade_value = known
            row["value_rank"] = rank
            row["value_rank_basis"] = "stocks_ex_etf"
            row["trade_value"] = trade_value
            row["trade_value_yi"] = round(trade_value / 1e8, 2)
        out.append(row)
    return out


def session_value_inputs(conn, day, volume):
    """(volume rows, value_ranking or None, basis "market" / "pool") for one replayed session."""
    value_ranking, value_ranks = session_value_ranking(conn, day)
    if not value_ranking or not value_ranking["stocks"]:
        return volume, None, "pool"
    return with_value_ranks(volume, value_ranks), value_ranking, "market"


def minimal_structured(day, institutional):
    """The same name + 億 lists structured_facts writes, built from local flows."""

    def _items(rows):
        out = []
        for item in rows[:10]:
            name = str(item.get("name") or item.get("id") or "").strip()
            try:
                amount = float(item.get("est_amount") or 0.0)
            except (TypeError, ValueError):
                amount = 0.0
            label = f"{item.get('id')} {name}" if str(item.get("id") or "").startswith("00") else name
            out.append(f"{label} ({amount:+.1f}億)")
        return out

    return {
        "date": day,
        "safe_havens_bought": _items(institutional.get("top_buy") or []),
        "heavyweights_dumped": _items(institutional.get("top_sell") or []),
    }


# ---------------------------------------------------------------------------
# Forward returns
# ---------------------------------------------------------------------------


def _parse(day):
    return datetime.strptime(str(day)[:10], "%Y-%m-%d").date()


def nth_session_after(day, n):
    from modules.twse_calendar import next_session

    cursor = _parse(day)
    for _ in range(n):
        cursor = next_session(cursor)
    return cursor.isoformat()


def forward_return(panel, day, ticker, horizon):
    target = nth_session_after(day, horizon)
    start = panel.get(day, {}).get(ticker)
    end = panel.get(target, {}).get(ticker)
    if not start or not end:
        return None
    raw = (end / start - 1.0) * 100.0
    idx_start = next((panel.get(day, {}).get(t) for t in INDEX_TICKERS if panel.get(day, {}).get(t)), None)
    idx_end = next((panel.get(target, {}).get(t) for t in INDEX_TICKERS if panel.get(target, {}).get(t)), None)
    excess = raw - (idx_end / idx_start - 1.0) * 100.0 if idx_start and idx_end else None
    return {"raw": raw, "excess": excess if excess is not None else raw, "has_index": excess is not None}


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def _ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs, ys):
    if len(xs) < 3:
        return None
    rx, ry = _ranks(list(xs)), _ranks(list(ys))
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy)


def summarize_ic(daily_ics):
    values = [v for v in daily_ics if v is not None]
    n = len(values)
    if not n:
        return {"days": 0, "mean_ic": None, "t_stat": None}
    mean = sum(values) / n
    if n < 2:
        return {"days": n, "mean_ic": round(mean, 4), "t_stat": None}
    std = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    t_stat = mean / (std / math.sqrt(n)) if std > 0 else None
    return {"days": n, "mean_ic": round(mean, 4), "t_stat": round(t_stat, 2) if t_stat is not None else None}


def tercile_table(pairs):
    """Pooled (score, excess) → bottom / middle / top tercile mean excess and hit rate."""
    if len(pairs) < 9:
        return []
    ordered = sorted(pairs, key=lambda p: p[0])
    size = len(ordered)
    cuts = [(0, size // 3), (size // 3, 2 * size // 3), (2 * size // 3, size)]
    out = []
    for label, (lo, hi) in zip(("低", "中", "高"), cuts):
        chunk = [ret for _, ret in ordered[lo:hi]]
        if not chunk:
            continue
        out.append(
            {
                "bucket": label,
                "n": len(chunk),
                "mean_excess": round(sum(chunk) / len(chunk), 3),
                "hit_rate": round(sum(1 for r in chunk if r > 0) / len(chunk), 3),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def _strip_lookahead(stock_db, keep_tracking=False):
    """Drop fields later /1 runs wrote: market_role (always) and tracking (hit rates)."""
    clean = copy.deepcopy(stock_db or {})
    for entry in clean.values():
        if isinstance(entry, dict):
            entry.pop("market_role", None)
            if not keep_tracking:
                entry.pop("tracking", None)
    return clean


def point_in_time_roles(stock_db, compounder, db_path, role_day, role_flows):
    """
    stock_db with market_role.base_role as /1 saw it on the next session: the
    compounder rule applied at `role_day` (the previous flow session) — keyword
    / structural rule plus foreign top-N sell sessions over the flow dates up to
    role_day. role_day None (first session) → keyword / structural rule only.
    """
    sell_prior = compounder._recent_sell_pressure(role_day, db_path=db_path) if role_day else {}
    role_flows = role_flows or {}
    sold = {
        _norm(item.get("id"))
        for rank, item in enumerate(role_flows.get("top_sell") or [], start=1)
        if rank <= RISK_SELL_TOP_N
    }
    bought = {_norm(item.get("id")) for item in role_flows.get("top_buy") or []}
    out = {}
    for sid, entry in (stock_db or {}).items():
        if not isinstance(entry, dict):
            out[sid] = entry
            continue
        norm = _norm(sid)
        sell_sessions = int(sell_prior.get(str(sid)) or 0) + (1 if norm in sold else 0) if role_day else None
        detail = compounder._classify_base_role_detail(
            sid, entry, sell_sessions=sell_sessions, bought_today=norm in bought
        )
        row = dict(entry)
        row["market_role"] = {"base_role": detail["role"], "role_source": detail["source"]}
        out[sid] = row
    return out


def replay(db_path=ANALYTICS_DB, stock_db_path=STOCK_DB, start=None, end=None, horizons=(1, 5), keep_tracking=False):
    conn = _connect(db_path)
    try:
        all_days = load_sessions(conn)
        sessions = load_sessions(conn, start, end)
        panel = load_price_panel(conn)
        base_db = _strip_lookahead(load_stock_db(stock_db_path), keep_tracking=keep_tracking)
        compounder = LocalKnowledgeCompounder(base_dir=BASE_DIR)
        per_score = {name: {h: {"daily_ic": [], "pairs": []} for h in horizons} for name in SIGNAL_SCORES + ROLE_SCORES}
        calibration = {h: defaultdict(lambda: {"n": 0, "hits": 0}) for h in horizons}
        coverage = {h: {"scored": 0, "with_close": 0, "low_days": []} for h in horizons}
        rank_basis_days = {"market": 0, "pool": 0}
        recent_leads = []
        replayed = 0
        for day in sessions:
            market_data, institutional, volume = session_inputs(conn, day)
            if not institutional["top_buy"] and not institutional["top_sell"]:
                continue
            replayed += 1
            volume, value_ranking, basis = session_value_inputs(conn, day, volume)
            rank_basis_days[basis] += 1
            role_day = next((d for d in reversed(all_days) if d < day), None)
            role_flows = session_inputs(conn, role_day)[1] if role_day else None
            stock_db = point_in_time_roles(base_db, compounder, db_path, role_day, role_flows)
            pack = build_market_signal_pack(
                market_data, institutional, volume, stock_db, max_candidates=1000,
                mechanical_flow=mechanical_flow_events(day), value_ranking=value_ranking,
            )
            structured = minimal_structured(day, institutional)
            inputs = build_role_score_inputs(
                structured, stock_db, market_data=market_data, volume_data=volume,
                analytics_db_path=db_path, reports_dir=None, calendar_events=None,
                value_ranking=value_ranking,
            )
            inputs["recent_leads"] = list(reversed(recent_leads[-5:]))
            ranking = rank_focus_stocks(structured, stock_db, limit=1000, role_inputs=inputs)
            if ranking:
                recent_leads.append(ranking[0]["ticker"])

            scores = defaultdict(dict)
            for row in pack.get("top_candidates") or []:
                scores[row["ticker"]]["attention_score"] = row.get("attention_score", row.get("score"))
                scores[row["ticker"]]["direction_score"] = row.get("direction_score", row.get("watch_score"))
            for row in ranking:
                for name in ROLE_SCORES:
                    if row.get(name) is not None:
                        scores[row["ticker"]][name] = row[name]

            for horizon in horizons:
                returns = {}
                for ticker in scores:
                    fwd = forward_return(panel, day, ticker, horizon)
                    if fwd:
                        returns[ticker] = fwd["excess"]
                cov = coverage[horizon]
                cov["scored"] += len(scores)
                cov["with_close"] += len(returns)
                if scores and len(returns) / len(scores) < LOW_COVERAGE:
                    cov["low_days"].append(day)
                for name in per_score:
                    pairs = [(float(scores[t][name]), returns[t]) for t in returns if scores[t].get(name) is not None]
                    bucket = per_score[name][horizon]
                    bucket["pairs"].extend(pairs)
                    if len(pairs) >= MIN_NAMES_FOR_IC:
                        bucket["daily_ic"].append(spearman([p[0] for p in pairs], [p[1] for p in pairs]))
                for hyp in pack.get("operator_hypotheses") or []:
                    bias = str(hyp.get("next_day_bias") or "")
                    sign = 1 if bias.startswith(BULLISH_BIAS) else (-1 if bias.startswith(BEARISH_BIAS) else 0)
                    ret = returns.get(hyp.get("ticker"))
                    if not sign or ret is None:
                        continue
                    cell = calibration[horizon][hyp.get("confidence_grade") or "?"]
                    cell["n"] += 1
                    cell["hits"] += 1 if ret * sign > 0 else 0
    finally:
        conn.close()

    result = {
        "db": db_path,
        "sessions_replayed": replayed,
        "range": [sessions[0], sessions[-1]] if sessions else [],
        "horizons": list(horizons),
        "tracking_stripped": not keep_tracking,
        "roles_point_in_time": True,
        # Sessions replayed with the market-wide 成交值 rank (tape in daily_closes) vs the pool rank.
        "turnover_rank_basis_days": rank_basis_days,
        "scores": {},
        "confidence_calibration": {},
        "coverage": {
            str(horizon): {
                "scored_names": cov["scored"],
                "with_forward_close": cov["with_close"],
                "coverage": round(cov["with_close"] / cov["scored"], 3) if cov["scored"] else None,
                "low_coverage_days": len(cov["low_days"]),
                "low_coverage_sample": cov["low_days"][:5],
            }
            for horizon, cov in coverage.items()
        },
    }
    for name, by_h in per_score.items():
        result["scores"][name] = {}
        for horizon, bucket in by_h.items():
            pairs = bucket["pairs"]
            pooled = spearman([p[0] for p in pairs], [p[1] for p in pairs]) if len(pairs) >= 3 else None
            result["scores"][name][str(horizon)] = {
                "observations": len(pairs),
                **summarize_ic(bucket["daily_ic"]),
                "pooled_ic": round(pooled, 4) if pooled is not None else None,
                "terciles": tercile_table(pairs),
            }
    for horizon, cells in calibration.items():
        result["confidence_calibration"][str(horizon)] = {
            grade: {
                "n": cell["n"],
                "directional_hit_rate": round(cell["hits"] / cell["n"], 3) if cell["n"] else None,
            }
            for grade, cell in sorted(cells.items())
        }
    return result


def _structured_by_date(reports_dir):
    """{date: path} of local structured_data files (empty when reports are absent)."""
    if not reports_dir or not os.path.isdir(reports_dir):
        return {}
    try:
        from modules.report_paths import date_from_structured_path, iter_structured_data_paths

        return {
            day: path
            for path in iter_structured_data_paths(reports_dir)
            for day in [date_from_structured_path(path)]
            if day
        }
    except Exception:
        return {}


def _load_structured(path):
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def replay_frames(db_path=ANALYTICS_DB, stock_db_path=STOCK_DB, reports_dir=REPORTS_DIR, last_n=30, end=None):
    """
    Rebuild editorial_frame for the last `last_n` flow sessions (≤ end) with
    point-in-time inputs and count the winners. Read-only.

    Returns {"sessions", "range", "counts", "shares", "max_frame", "max_share", "per_day"}.
    """
    conn = _connect(db_path)
    try:
        days = load_sessions(conn, end=end)[-max(1, int(last_n)):]
        base_db = _strip_lookahead(load_stock_db(stock_db_path))
        compounder = LocalKnowledgeCompounder(base_dir=BASE_DIR)
        all_days = load_sessions(conn)
        structured_paths = _structured_by_date(reports_dir)
        counts = defaultdict(int)
        per_day = []
        for day in days:
            market_data, institutional, volume = session_inputs(conn, day)
            if not institutional["top_buy"] and not institutional["top_sell"]:
                continue
            volume, value_ranking, _basis = session_value_inputs(conn, day, volume)
            role_day = next((d for d in reversed(all_days) if d < day), None)
            role_flows = session_inputs(conn, role_day)[1] if role_day else None
            stock_db = point_in_time_roles(base_db, compounder, db_path, role_day, role_flows)
            pack = build_market_signal_pack(
                market_data, institutional, volume, stock_db, mechanical_flow=mechanical_flow_events(day),
                value_ranking=value_ranking,
            )
            index_pct = next(
                (
                    (market_data.get(key) or {}).get("pct_change")
                    for key in INDEX_TICKERS
                    if isinstance(market_data.get(key), dict)
                ),
                None,
            )
            structured = _load_structured(structured_paths.get(day))
            frame = build_editorial_frame(
                run_date=day,
                signal_pack=pack,
                validation_results=structured.get("validation_results") or [],
                event_context="",
                channel_story="",
                db_path=db_path,
                validation_meta={},
                event_radar=[ev for ev in structured.get("supply_chain_event_radar") or [] if isinstance(ev, dict)],
                index_pct=index_pct,
            )
            counts[frame["frame"]] += 1
            per_day.append({"date": day, "frame": frame["frame"], "confidence": frame.get("confidence")})
    finally:
        conn.close()
    total = sum(counts.values())
    shares = {frame: round(n / total, 3) for frame, n in counts.items()} if total else {}
    max_frame = max(counts, key=lambda k: (counts[k], k)) if counts else None
    return {
        "sessions": total,
        "range": [per_day[0]["date"], per_day[-1]["date"]] if per_day else [],
        "counts": dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "shares": shares,
        "max_frame": max_frame,
        "max_share": shares.get(max_frame, 0.0) if max_frame else 0.0,
        "per_day": per_day,
    }


def format_frame_report(result):
    lines = [
        "# 主軸框架重播（離線，0 API）",
        f"- 交易日：{' → '.join(result['range']) if result['range'] else '無'}（{result['sessions']} 日）",
    ]
    for frame, n in result["counts"].items():
        lines.append(f"- {FRAME_LABELS.get(frame, frame)}（{frame}）：{n} 日（{result['shares'][frame]:.0%}）")
    verdict = "通過" if result["max_share"] <= MAX_FRAME_SHARE else "未通過"
    lines.append(f"- 最大佔比 {result['max_share']:.0%}（上限 {MAX_FRAME_SHARE:.0%}）：{verdict}")
    return "\n".join(lines)


def format_report(result):
    lines = [
        "# 本機評分回測（離線，0 API）",
        f"- 資料庫：{result['db']}",
        f"- 交易日：{' → '.join(result['range']) if result['range'] else '無'}（重播 {result['sessions_replayed']} 日）",
        f"- stock_db tracking 已移除（避免命中率前視）：{'是' if result['tracking_stripped'] else '否'}",
        "- market_role 依前一交易日可得資料重建（關鍵字規則＋此前外資賣超榜），不用今日 stock_db 的角色。",
        "- 報酬為收盤到 N 個交易日後收盤，扣除同期加權指數；本機收盤價稀疏，樣本小時僅供參考。",
    ]
    basis_days = result.get("turnover_rank_basis_days") or {}
    if basis_days:
        lines.append(
            f"- 成交值名次基準：全市場 {basis_days.get('market', 0)} 日（daily_closes 全日收盤表重建，收盤×股數近似成交金額，同 /1）"
            f"／觀察池 {basis_days.get('pool', 0)} 日（當日無全日收盤表）"
        )
    for horizon, cov in (result.get("coverage") or {}).items():
        ratio = "—" if cov["coverage"] is None else f"{cov['coverage']:.0%}"
        lines.append(
            f"- {horizon}D 覆蓋率：{cov['with_forward_close']}/{cov['scored_names']}（{ratio}）有 T+{horizon} 收盤；"
            f"覆蓋率 <{LOW_COVERAGE:.0%} 的交易日 {cov['low_coverage_days']} 日"
        )
    lines.extend(
        [
            "- 注意：T+N 收盤只存在於當日追蹤池／量榜內的標的，而入池由 T+N 的法人與量決定，樣本受未來條件篩選；IC 與 t 值可能偏誤。",
            "",
        ]
    )
    for name, by_h in result["scores"].items():
        for horizon, stats in by_h.items():
            terciles = "；".join(
                f"{t['bucket']} n={t['n']} 超額 {t['mean_excess']:+.2f}% 勝率 {t['hit_rate']:.0%}"
                for t in stats["terciles"]
            ) or "樣本不足"
            mean_ic = "—" if stats["mean_ic"] is None else f"{stats['mean_ic']:+.3f}"
            t_stat = "—" if stats["t_stat"] is None else f"{stats['t_stat']:+.2f}"
            pooled = "—" if stats["pooled_ic"] is None else f"{stats['pooled_ic']:+.3f}"
            lines.append(
                f"- {name} {horizon}D：日均 IC {mean_ic}（t {t_stat}，{stats['days']} 日）；"
                f"合併 IC {pooled}（{stats['observations']} 筆）；{terciles}"
            )
    lines.append("")
    lines.append("## 主力假說信心等級 vs 方向命中（校準檢查）")
    for horizon, cells in result["confidence_calibration"].items():
        if not cells:
            lines.append(f"- {horizon}D：樣本不足")
            continue
        bits = "；".join(
            f"{grade} n={cell['n']} 命中 {cell['directional_hit_rate']:.0%}"
            for grade, cell in cells.items()
            if cell["directional_hit_rate"] is not None
        )
        lines.append(f"- {horizon}D：{bits}")
    lines.append("")
    lines.append("判讀：IC 長期接近 0 或高分組不贏低分組，代表該分數不該再加權重；等級越高命中率沒有越高，代表信心等級需要重新校準。")
    return "\n".join(lines)


def _parse_horizons(text):
    out = []
    for part in str(text or "").split(","):
        part = part.strip()
        if part.isdigit() and int(part) > 0:
            out.append(int(part))
    return tuple(out) or (1, 5)


def main(argv=None):
    parser = argparse.ArgumentParser(description="本機評分回測（IC／分組報酬／信心校準）")
    parser.add_argument("--db", default=ANALYTICS_DB, help="analytics.db 路徑（唯讀）")
    parser.add_argument("--stock-db", default=STOCK_DB, help="stock_db.json 路徑")
    parser.add_argument("--start", help="起始交易日 YYYY-MM-DD")
    parser.add_argument("--end", help="結束交易日 YYYY-MM-DD")
    parser.add_argument("--horizons", default="1,5", help="交易日 horizon，逗號分隔（預設 1,5）")
    parser.add_argument("--keep-tracking", action="store_true", help="保留 stock_db 命中率（有前視偏差）")
    parser.add_argument("--json", action="store_true", help="輸出 JSON")
    parser.add_argument("--frames", type=int, default=0, help="改跑主軸框架重播：最近 N 個交易日")
    parser.add_argument("--reports-dir", default=REPORTS_DIR, help="structured_data 所在 reports 目錄（框架重播用）")
    args = parser.parse_args(argv)
    if args.frames:
        try:
            frames = replay_frames(
                db_path=args.db,
                stock_db_path=args.stock_db,
                reports_dir=args.reports_dir,
                last_n=args.frames,
                end=args.end,
            )
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps(frames, ensure_ascii=False, indent=2) if args.json else format_frame_report(frames))
        return 0
    try:
        result = replay(
            db_path=args.db,
            stock_db_path=args.stock_db,
            start=args.start,
            end=args.end,
            horizons=_parse_horizons(args.horizons),
            keep_tracking=args.keep_tracking,
        )
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
