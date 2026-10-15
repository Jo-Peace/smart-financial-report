"""
外資連續買超天數 — counted in TWSE trading sessions, keyed by the data date.

The old counter (stock_db_updater._refresh_foreign_streaks) compared the stored
``last_foreign_buy_date`` with the *calendar* yesterday (``date.today() - 1``),
so every Monday, the first session after a holiday and every same-day rerun
reset the streak to 1, and it only ran when yesterday_predictions existed
(finding 15/39/63/79: 3037 欣興 was on the buy list 10-02, 10-05~10-08, 10-12
— 6 sessions, 10-09 closed — yet the source said 「外資連續買超：1 日」).

Here the streak is rebuilt from scratch on every /1:

- today's membership comes from the institutional payload in hand
  (``top_buy`` rows; ``top_sell`` rows are 0);
- prior sessions come from ``analytics.db`` ``institutional_flows`` strictly
  *before* the data date, walked back with ``twse_calendar.prev_session``.
  Reading today's rows from the DB would make a rerun see one more day than
  the first run, so the result is idempotent;
- a prior session with no stored flows at all is a data gap: the count stops
  there and the streak is marked incomplete (「至少 N 日」), never invented.

The basis is the stored foreign top-N lists (T86, 上市): a ticker on the buy
list was net-bought that session (``foreign_net`` > 0). Absence from the list
does not prove a net sale, so the streak is a verified lower bound.
"""

from __future__ import annotations

import os
import sqlite3

from modules.tickers import bare_ticker

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "analytics.db")

MAX_LOOKBACK_SESSIONS = 60
STREAK_BASIS = "外資買超榜（上市 T86 前段）連續上榜交易日"


def _connect(db_path):
    if not db_path or not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    conn.row_factory = sqlite3.Row
    return conn


def _positive(value) -> bool:
    try:
        return value is not None and float(value) > 0
    except (TypeError, ValueError):
        return False


def _flows_before(db_path, data_date, sessions):
    """{flow_date: {"buy": set(tickers), "any": True}} for the given prior sessions."""
    out = {}
    if not sessions:
        return out
    conn = _connect(db_path)
    if conn is None:
        return out
    try:
        marks = ",".join(["?"] * len(sessions))
        rows = conn.execute(
            f"""
            SELECT flow_date, ticker, side, rank, foreign_net, est_amount
            FROM institutional_flows
            WHERE flow_date IN ({marks}) AND flow_date < ?
            """,
            [*sessions, data_date],
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    # A list an earlier rerun left a duplicate rank in (before reruns replaced the
    # list) still holds the dropped stock; read only the latest run's rows.
    from modules.analytics_store import stale_flow_rows

    by_list = {}
    for row in rows:
        by_list.setdefault((row["flow_date"], row["side"]), []).append(row)
    stale = {id(row) for group in by_list.values() for row in stale_flow_rows(group)}
    for row in rows:
        day = out.setdefault(row["flow_date"], {"buy": set()})
        if row["side"] != "buy" or id(row) in stale:
            continue
        # Older rows may lack foreign_net; a buy-list row with a positive
        # estimated amount is the same fact.
        if _positive(row["foreign_net"]) or (row["foreign_net"] is None and _positive(row["est_amount"])):
            day["buy"].add(bare_ticker(row["ticker"]))
    return out


def _prior_sessions(data_date, count):
    from modules.twse_calendar import as_date, prev_session

    cursor = as_date(data_date)
    days = []
    if cursor is None:
        return days
    for _ in range(max(0, int(count))):
        cursor = prev_session(cursor)
        days.append(cursor.isoformat())
    return days


def _payload(institutional_data):
    if isinstance(institutional_data, dict) and "data" in institutional_data and "top_buy" not in institutional_data:
        inner = institutional_data.get("data")
        return inner if isinstance(inner, dict) else {}
    return institutional_data if isinstance(institutional_data, dict) else {}


def compute_foreign_buy_streaks(
    institutional_data,
    report_date=None,
    *,
    db_path=DB_PATH,
    max_lookback=MAX_LOOKBACK_SESSIONS,
):
    """
    {ticker: {"days": int, "complete": bool, "since": "YYYY-MM-DD"|None,
              "data_date": "YYYY-MM-DD", "basis": str}}

    Buy-list tickers get days >= 1; sell-list tickers (not also on the buy list)
    get days = 0. Tickers on neither list are omitted (unknown, not zero).
    """
    payload = _payload(institutional_data)
    data_date = str(payload.get("data_date") or report_date or "")[:10]
    if not data_date:
        return {}
    buys = []
    for row in payload.get("top_buy") or []:
        if not isinstance(row, dict):
            continue
        ticker = bare_ticker(row.get("id") or row.get("ticker"))
        if ticker and ticker not in buys:
            buys.append(ticker)
    sells = {
        bare_ticker(row.get("id") or row.get("ticker"))
        for row in payload.get("top_sell") or []
        if isinstance(row, dict)
    }
    sells.discard("")

    sessions = _prior_sessions(data_date, max_lookback)
    flows = _flows_before(db_path, data_date, sessions) if buys else {}

    out = {}
    for ticker in buys:
        days, since, complete = 1, data_date, False
        for session in sessions:
            day = flows.get(session)
            if day is None:
                # No stored flows for that session (no /1 run or no DB): stop, lower bound.
                complete = False
                break
            if ticker in day["buy"]:
                days += 1
                since = session
                continue
            complete = True
            break
        out[ticker] = {
            "days": days,
            "complete": complete,
            "since": since,
            "data_date": data_date,
            "basis": STREAK_BASIS,
        }
    for ticker in sells - set(buys):
        out[ticker] = {
            "days": 0,
            "complete": True,
            "since": None,
            "data_date": data_date,
            "basis": STREAK_BASIS,
        }
    return out


def annotate_foreign_streaks(institutional_data, report_date=None, *, db_path=DB_PATH):
    """
    Compute the streaks and stamp them on the payload rows in place
    (``foreign_buy_streak`` / ``foreign_buy_streak_complete``) so the analyzer
    prompt, local_market_facts and resume_inputs all carry the same number.
    Never raises; returns the streak map.
    """
    try:
        streaks = compute_foreign_buy_streaks(institutional_data, report_date, db_path=db_path)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"  ⚠️  外資連續買超天數計算失敗，略過：{exc}")
        return {}
    payload = _payload(institutional_data)
    for side in ("top_buy", "top_sell"):
        for row in payload.get(side) or []:
            if not isinstance(row, dict):
                continue
            info = streaks.get(bare_ticker(row.get("id") or row.get("ticker")))
            if info is None:
                continue
            row["foreign_buy_streak"] = info["days"]
            row["foreign_buy_streak_complete"] = info["complete"]
    return streaks


def format_streak_text(days, complete=True) -> str:
    """「6 個交易日」 / 「至少 1 個交易日（本機歷史不足）」 / ''."""
    try:
        days = int(days)
    except (TypeError, ValueError):
        return ""
    if days <= 0:
        return ""
    if complete:
        return f"{days} 個交易日"
    return f"至少 {days} 個交易日（本機歷史不足，勿說剛開始買）"


def streak_is_fresh(tracking, as_of) -> bool:
    """
    A stock_db streak may be read only when the session counter wrote it for
    this data date. Only ``foreign_buy_streak_date`` (written by
    stock_db_updater._refresh_foreign_streaks) counts: ``last_foreign_buy_date``
    alone is what the old calendar-yesterday counter left behind, so a legacy
    「1 日」 stamped today is not a session count and reads as unknown.
    """
    tracking = tracking or {}
    stamp = str(tracking.get("foreign_buy_streak_date") or "")[:10]
    return bool(stamp) and stamp == str(as_of or "")[:10]


__all__ = [
    "STREAK_BASIS",
    "annotate_foreign_streaks",
    "compute_foreign_buy_streaks",
    "format_streak_text",
    "streak_is_fresh",
]
