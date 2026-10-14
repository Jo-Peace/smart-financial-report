"""
Full-market (上市 / TWSE-listed) institutional amount ranking.

T86 is share-based. Ranking only the top N*4 names by |shares| drops high-price
names (聯發科 / 台積電 / 奇鋐) that CTBC still lists by 億元.

Scope: T86 ``selectType=ALLBUT0999`` covers TWSE-listed securities only; 上櫃
(TPEx) names never appear here, so labels must say 上市, not 全市場.
"""

from __future__ import annotations

import datetime
import re
import sqlite3
from typing import Callable

MARKET_SCOPE = "TWSE_listed"
MARKET_SCOPE_LABEL = "上市"

# Always try to price these even if share-count is mid-pack.
HIGH_PRICE_ALWAYS = (
    "2330",
    "2454",
    "2317",
    "2308",
    "2382",
    "3711",
    "3037",
    "3661",
    "6669",
    "3008",
    "3017",
    "2327",
    "2376",
    "2357",
    "2408",
    "2344",
    "6770",
    "4958",
    "8046",
    "3189",
    "3533",
    "3105",
    "2368",
    "5483",
    "3443",
    "2303",
    "2881",
    "2882",
    "2891",
    "2603",
    "2609",
    "2615",
)


def _to_float(value) -> float | None:
    text = str(value or "").replace(",", "").replace(" ", "").strip()
    if not text or text in {"--", "---", "—"}:
        return None
    try:
        price = float(text)
    except ValueError:
        return None
    return price if price > 0 else None


def _to_int(value) -> int | None:
    text = str(value or "").replace(",", "").replace(" ", "").strip()
    if not text or text in {"--", "---", "—"}:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def trade_date_iso(value) -> str:
    """「20261013」 / ROC 「1151013」 / 「115/10/13」 → 「2026-10-13」 ("" when unknown)."""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 8:
        year, rest = int(digits[:4]), digits[4:]
    elif len(digits) == 7:
        year, rest = int(digits[:3]) + 1911, digits[3:]
    else:
        return ""
    try:
        return datetime.date(year, int(rest[:2]), int(rest[2:])).isoformat()
    except ValueError:
        return ""


def parse_day_all_payload(payload: dict) -> list[dict]:
    """
    Parse TWSE STOCK_DAY_ALL JSON into {id, name, close_price, volume, trade_value, trade_date}.

    The endpoint answers with the *latest* session whatever ``date`` was asked
    for, so ``trade_date`` comes from the payload itself (row 「日期」 or the
    top-level ``date``) and is "" when the payload does not say.
    """
    if not isinstance(payload, dict):
        return []
    if payload.get("stat") not in (None, "OK"):
        return []
    fields = [str(x) for x in (payload.get("fields") or [])]
    payload_date = trade_date_iso(payload.get("date"))

    def _idx(*names: str, default: int | None = None) -> int | None:
        for name in names:
            if name in fields:
                return fields.index(name)
        return default

    i_id = _idx("證券代號", "代號", default=0)
    i_name = _idx("證券名稱", "名稱", default=1)
    i_vol = _idx("成交股數", default=2)
    i_val = _idx("成交金額", default=None)
    i_close = _idx("收盤價", default=7)
    i_date = _idx("日期")
    rows = []
    for raw in payload.get("data") or []:
        if not raw:
            continue
        try:
            ticker = str(raw[i_id]).strip()
            name = str(raw[i_name]).strip()
        except (IndexError, TypeError):
            continue
        if not ticker or not ticker[0].isdigit():
            continue
        close = _to_float(raw[i_close] if i_close is not None and len(raw) > i_close else None)
        if close is None:
            continue
        volume = _to_int(raw[i_vol] if i_vol is not None and len(raw) > i_vol else None)
        trade_value = _to_int(raw[i_val] if i_val is not None and len(raw) > i_val else None)
        row_date = trade_date_iso(raw[i_date]) if i_date is not None and len(raw) > i_date else ""
        rows.append(
            {
                "id": ticker,
                "name": name,
                "close_price": close,
                "volume": volume,
                "trade_value": trade_value,
                "trade_date": row_date or payload_date,
            }
        )
    return rows


# T86 columns by name. Positional parsing (row[4] / row[10] / row[-1]) shifts
# silently if TWSE inserts a column, so key columns are required by name.
T86_REQUIRED = {
    "id": ("證券代號",),
    "foreign_net": ("外陸資買賣超股數(不含外資自營商)", "外資買賣超股數"),
    "trust_net": ("投信買賣超股數",),
    "total_net": ("三大法人買賣超股數",),
}
T86_OPTIONAL = {
    "name": ("證券名稱",),
    "foreign_dealer_net": ("外資自營商買賣超股數",),
    "dealer_net": ("自營商買賣超股數",),
}


class T86SchemaError(ValueError):
    """T86 payload no longer has the columns we rely on."""


def t86_column_map(fields) -> dict[str, int]:
    names = [str(f).strip() for f in (fields or [])]
    out: dict[str, int] = {}
    missing = []
    for key, candidates in T86_REQUIRED.items():
        hit = next((names.index(c) for c in candidates if c in names), None)
        if hit is None:
            missing.append(candidates[0])
        else:
            out[key] = hit
    if missing:
        raise T86SchemaError(f"T86 欄位異動，缺少：{'、'.join(missing)}")
    for key, candidates in T86_OPTIONAL.items():
        hit = next((names.index(c) for c in candidates if c in names), None)
        if hit is not None:
            out[key] = hit
    return out


def parse_t86_rows(payload: dict) -> list[dict]:
    """
    T86 → [{id, name, foreign_net, foreign_dealer_net, foreign_total_net,
    trust_net, dealer_net, total_net}].

    ``foreign_net`` keeps its historical meaning (外陸資, 不含外資自營商) so
    analytics stay comparable; ``foreign_total_net`` adds 外資自營商.
    Raises T86SchemaError when key columns are missing.
    """
    cols = t86_column_map((payload or {}).get("fields"))

    def num(raw, key):
        idx = cols.get(key)
        if idx is None or idx >= len(raw):
            return None
        text = str(raw[idx]).replace(",", "").replace(" ", "").strip()
        if not text or text in {"--", "---"}:
            return None
        return int(float(text))

    rows = []
    for raw in (payload or {}).get("data") or []:
        try:
            sid = str(raw[cols["id"]]).strip()
            name = str(raw[cols["name"]]).strip() if "name" in cols else sid
            foreign = num(raw, "foreign_net")
            trust = num(raw, "trust_net")
            total = num(raw, "total_net")
        except (ValueError, IndexError, TypeError):
            continue
        if not sid or foreign is None:
            continue
        try:
            foreign_dealer = num(raw, "foreign_dealer_net")
            dealer = num(raw, "dealer_net")
        except (ValueError, TypeError):
            foreign_dealer, dealer = None, None
        rows.append(
            {
                "id": sid,
                "name": name,
                "foreign_net": foreign,
                "foreign_dealer_net": foreign_dealer,
                "foreign_total_net": foreign + (foreign_dealer or 0),
                "trust_net": trust if trust is not None else 0,
                "dealer_net": dealer,
                "total_net": total if total is not None else 0,
                "market": MARKET_SCOPE,
            }
        )
    return rows


def fetch_twse_day_all(date_str: str, timeout: float = 20.0) -> list[dict]:
    """
    One verified full-market close tape for ``date_str`` (listed / 上市 only).

    Rows: {id, name, close_price, volume, trade_value, change, pct_change,
    ex_rights, price_date}. Empty list when no tape for exactly that session
    exists — never another day's prices (STOCK_DAY_ALL ignores its date param).
    """
    from modules.twse_tape import fetch_twse_tape

    tape = fetch_twse_tape(date_str, timeout=timeout)
    if not tape:
        print("  [Info] 全日收盤表未取到同日資料，改用同日本機價＋補價（舊價一律不採用）")
        return []
    return list(tape.get("rows") or [])


def latest_close_map(
    db_path: str,
    close_date: str | None = None,
    dates_out: dict[str, str] | None = None,
) -> dict[str, float]:
    """
    Same-session closes only (``close_date`` required for a useful result).

    The previous version fell back to *any* date in daily_closes and
    market_snapshots, which priced 2026-10-12 外資買賣超 with snapshots from
    2026-08-20 / 09-18. Stale prices are now treated as missing so the caller
    re-prices them for the right session. ``dates_out`` (optional dict)
    receives each price's own date (always ``close_date`` here) so callers can
    stamp ``price_date`` on the rows they price.
    """
    pairs = latest_close_map_with_dates(db_path, close_date)
    if dates_out is not None:
        dates_out.update({sid: day for sid, (_price, day) in pairs.items() if day})
    return {sid: price for sid, (price, _d) in pairs.items()}


def _snapshot_bar_date(raw_json) -> str:
    try:
        import json

        payload = json.loads(raw_json or "{}")
    except (TypeError, ValueError):
        return ""
    return str((payload or {}).get("bar_date") or "")[:10] if isinstance(payload, dict) else ""


def latest_close_map_with_dates(db_path: str, close_date: str | None) -> dict[str, tuple[float, str]]:
    """
    {ticker: (price, price_date)} for exactly ``close_date``; {} when it is missing.

    market_snapshots is keyed by run_date, not by the payload's K-bar date: a
    stale Yahoo bar recorded under today's run_date would otherwise pass as a
    same-session price. Only snapshots whose raw_json ``bar_date`` equals
    ``close_date`` are accepted (rows without bar_date cannot be verified).
    """
    out: dict[str, tuple[float, str]] = {}
    day = str(close_date or "")[:10]
    if not day:
        return out
    try:
        conn = sqlite3.connect(db_path)
        try:
            for ticker, price in conn.execute(
                "SELECT ticker, close_price FROM daily_closes WHERE close_date = ? AND close_price > 0",
                (day,),
            ):
                out[str(ticker)] = (float(price), day)
        except sqlite3.Error:
            pass
        try:
            for ticker, price, raw_json in conn.execute(
                """
                SELECT ticker, price, raw_json FROM market_snapshots
                WHERE snapshot_date = ? AND price IS NOT NULL AND price > 0
                """,
                (day,),
            ):
                ticker = str(ticker or "").strip()
                if ticker and ticker not in out and _snapshot_bar_date(raw_json) == day:
                    out[ticker] = (float(price), day)
        except sqlite3.Error:
            pass
        conn.close()
    except sqlite3.Error:
        return out
    return out


def reference_close_map(db_path: str | None, before_date: str | None, window_days: int = 45) -> dict[str, float]:
    """
    Most recent local close strictly *before* ``before_date`` per ticker.

    Only an order-of-magnitude reference for deciding whether an unpriced name
    could sit on the 億元 leaderboard (pricing coverage / gate). Never used to
    compute est_amount — that stays same-session only.
    """
    out: dict[str, float] = {}
    day = str(before_date or "")[:10]
    if not db_path or not day:
        return out
    try:
        import datetime as _dt

        floor = (_dt.datetime.strptime(day, "%Y-%m-%d").date() - _dt.timedelta(days=window_days)).isoformat()
    except ValueError:
        return out
    queries = (
        (
            "SELECT ticker, close_price FROM daily_closes"
            " WHERE close_date < ? AND close_date >= ? AND close_price > 0 ORDER BY close_date"
        ),
        (
            "SELECT ticker, price FROM market_snapshots"
            " WHERE snapshot_date < ? AND snapshot_date >= ? AND price IS NOT NULL AND price > 0"
            " ORDER BY snapshot_date"
        ),
    )
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error:
        return out
    try:
        for pos, sql in enumerate(queries):
            found: dict[str, float] = {}
            try:
                for ticker, price in conn.execute(sql, (day, floor)):
                    ticker = str(ticker or "").strip()
                    if ticker:
                        found[ticker] = float(price)  # ascending → last write is newest
            except sqlite3.Error:
                continue
            for ticker, price in found.items():
                if pos == 0 or ticker not in out:
                    out[ticker] = price
    finally:
        conn.close()
    return out


def price_lookup_candidates(
    all_stocks: list[dict],
    price_map: dict[str, float],
    *,
    share_cap: int = 80,
    extra_ids: tuple[str, ...] = HIGH_PRICE_ALWAYS,
    price_dates: dict[str, str] | None = None,
    data_date: str | None = None,
) -> list[str]:
    """
    Tickers that still need a same-session price before amount ranking.

    A ticker whose known price_date differs from ``data_date`` counts as
    missing (stale prices must be refreshed, not reused).
    """
    by_shares = sorted(all_stocks, key=lambda x: abs(x.get("foreign_net") or 0), reverse=True)
    wanted = {str(s.get("id") or "").strip() for s in by_shares[:share_cap] if s.get("id")}
    wanted.update(extra_ids)
    have = {str(s.get("id") or "").strip() for s in all_stocks}
    want_date = str(data_date or "")[:10]
    missing = []
    for sid in sorted(wanted):
        if not sid or sid not in have:
            continue
        stale = bool(
            want_date
            and price_dates is not None
            and sid in price_map
            and str(price_dates.get(sid) or "")[:10] != want_date
        )
        if sid not in price_map or stale:
            missing.append(sid)
    return missing


def apply_amount_estimates(
    all_stocks: list[dict],
    price_map: dict[str, float],
    price_dates: dict[str, str] | None = None,
    data_date: str | None = None,
) -> list[dict]:
    """
    est_amount = foreign_net × same-session close / 1e8.

    Unpriced names get ``est_amount=None`` + ``amount_status="unpriced"`` (not
    0), so they cannot masquerade as a flat flow in the middle of the ranking.
    With ``price_dates`` + ``data_date``, a price from another session is
    rejected (``amount_status="stale_price"``) instead of being used.
    """
    want_date = str(data_date or "")[:10]
    priced = []
    for raw in all_stocks:
        stock = dict(raw)
        sid = str(stock.get("id") or "").strip()
        price = price_map.get(sid)
        shares = stock.get("foreign_net") or 0
        price_date = str((price_dates or {}).get(sid) or "")[:10]
        stale = bool(want_date and price_dates is not None and price and price_date != want_date)
        if price and float(price) > 0 and not stale:
            stock["close_price"] = float(price)
            stock["est_amount"] = round(float(shares) * float(price) / 1e8, 2)
            stock["amount_status"] = "priced"
            if price_date:
                stock["price_date"] = price_date
        else:
            stock["close_price"] = None if stale else stock.get("close_price")
            stock["est_amount"] = None
            stock["amount_status"] = "stale_price" if stale else "unpriced"
        priced.append(stock)
    return priced


def _amount(stock: dict) -> float | None:
    value = stock.get("est_amount")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def rank_by_amount(all_stocks: list[dict], top_n: int = 10) -> tuple[list[dict], list[dict]]:
    """Rank priced names only; unpriced rows never enter the 億元 leaderboard."""
    priced = [s for s in all_stocks if _amount(s) is not None]
    top_buy = sorted((s for s in priced if _amount(s) > 0), key=_amount, reverse=True)[:top_n]
    top_sell = sorted((s for s in priced if _amount(s) < 0), key=_amount)[:top_n]
    return top_buy, top_sell


def pricing_coverage(all_stocks: list[dict], top_shares: int = 30) -> dict:
    """How many of the top-|shares| names have a same-session price."""
    by_shares = sorted(all_stocks, key=lambda x: abs(x.get("foreign_net") or 0), reverse=True)[:top_shares]
    if not by_shares:
        return {"checked": 0, "priced": 0, "ratio": 1.0, "unpriced": []}
    unpriced = [
        {"id": s.get("id"), "name": s.get("name"), "foreign_net": s.get("foreign_net")}
        for s in by_shares
        if _amount(s) is None
    ]
    checked = len(by_shares)
    priced = checked - len(unpriced)
    return {
        "checked": checked,
        "priced": priced,
        "ratio": round(priced / checked, 4),
        "unpriced": unpriced[:10],
    }


MATERIAL_MARGIN = 1.25  # reference prices may be a few sessions old
MATERIAL_MIN_YI = 1.0  # cut line when a side has fewer than top_n priced names


def _cut_line(rows: list[dict], top_n: int) -> float:
    amounts = [abs(_amount(s) or 0.0) for s in rows]
    if len(amounts) < top_n or not amounts:
        return MATERIAL_MIN_YI
    return max(min(amounts), MATERIAL_MIN_YI)


def material_unpriced(
    all_stocks: list[dict],
    top_buy: list[dict],
    top_sell: list[dict],
    *,
    top_n: int = 10,
    must_price: tuple[str, ...] = HIGH_PRICE_ALWAYS,
    reference_prices: dict[str, float] | None = None,
) -> list[dict]:
    """
    Unpriced names that could belong on the 億元 leaderboard.

    The share-count coverage check cannot see them: 2026-10-12 台積電
    (-9.4M 股 ≈ -239億, the real #1 seller) is far outside the top 30 by
    |shares|. A name is material when |shares| × reference close (a recent
    local close, never used for est_amount) could clear that side's current
    top-N cut line, or when it is a HIGH_PRICE_ALWAYS name with no reference
    price at all (fail-safe: we cannot rule it out).
    """
    refs = reference_prices or {}
    must = {str(x) for x in must_price or ()}
    buy_cut, sell_cut = _cut_line(top_buy, top_n), _cut_line(top_sell, top_n)
    out = []
    for stock in all_stocks:
        if _amount(stock) is not None:
            continue
        sid = str(stock.get("id") or "").strip()
        shares = stock.get("foreign_net") or 0
        if not sid or not shares:
            continue
        line = buy_cut if shares > 0 else sell_cut
        ref = refs.get(sid)
        if ref and ref > 0:
            ref_amount = round(float(shares) * float(ref) / 1e8, 2)
            if abs(ref_amount) * MATERIAL_MARGIN < line:
                continue
            reason = "reference_above_cut_line"
        elif sid in must:
            ref_amount = None
            reason = "high_price_no_reference"
        else:
            continue
        out.append(
            {
                "id": sid,
                "name": stock.get("name"),
                "foreign_net": shares,
                "reference_amount": ref_amount,
                "cut_line": round(line, 2),
                "reason": reason,
            }
        )
    out.sort(key=lambda r: abs(r.get("reference_amount") or 1e9), reverse=True)
    return out


def yf_same_day_close(sid: str, data_date: str, suffixes: tuple[str, ...] = (".TW", ".TWO")) -> float | None:
    """yfinance close for exactly ``data_date``; None when the newest bar is another session."""
    try:
        import yfinance as yf
    except Exception:  # pragma: no cover - dependency missing
        return None
    want = str(data_date or "")[:10]
    if not sid or not want:
        return None
    for suffix in suffixes:
        try:
            hist = yf.Ticker(f"{sid}{suffix}").history(period="5d")
        except Exception:
            continue
        if hist is None or hist.empty:
            continue
        for pos in range(len(hist.index) - 1, -1, -1):
            idx = hist.index[pos]
            try:
                day = idx.date().isoformat()
            except AttributeError:
                day = str(idx)[:10]
            if day == want:
                try:
                    price = float(hist["Close"].iloc[pos])
                except (TypeError, ValueError, IndexError):
                    return None
                return price if price > 0 else None
        return None
    return None


def price_rows_same_session(
    all_stocks: list[dict],
    data_date: str,
    *,
    tape_rows: list[dict] | None = None,
    db_path: str | None = None,
    lookup_one: Callable[[str], float | None] | None = None,
    share_cap: int = 80,
    extra_ids: tuple[str, ...] = HIGH_PRICE_ALWAYS,
    max_workers: int = 10,
    coverage_top: int = 30,
    top_n: int = 10,
    refill_one: Callable[[str], float | None] | None = None,
    refill_cap: int = 12,
    reference_prices: dict[str, float] | None = None,
) -> tuple[list[dict], dict]:
    """
    Price T86 rows with closes from exactly ``data_date``.

    Order: verified full-market tape rows (price_date == data_date) → local
    daily_closes / market_snapshots for that date → ``lookup_one(sid)`` (should
    return a close dated ``data_date`` or None) for the share leaders + high
    priced names → ``refill_one(sid)`` (e.g. TWSE STOCK_DAY for that session)
    only for names that could still reach the top-``top_n`` 億元 board
    (see material_unpriced). Anything else stays unpriced. Returns
    (rows, pricing_meta); ``pricing_meta["material_unpriced"]`` lists leaders
    that are still unpriced (data_quality_gate fails on it).
    """
    day = str(data_date or "")[:10]
    price_map: dict[str, float] = {}
    price_dates: dict[str, str] = {}
    if db_path and day:
        for sid, (price, pdate) in latest_close_map_with_dates(db_path, day).items():
            price_map[sid], price_dates[sid] = price, pdate
    tape_used = 0
    for row in tape_rows or []:
        sid = str(row.get("id") or "").strip()
        if not sid or not row.get("close_price"):
            continue
        if str(row.get("price_date") or "")[:10] != day:
            continue
        price_map[sid] = float(row["close_price"])
        price_dates[sid] = day
        tape_used += 1
    missing = price_lookup_candidates(
        all_stocks,
        price_map,
        share_cap=share_cap,
        extra_ids=extra_ids,
        price_dates=price_dates,
        data_date=day,
    )
    looked_up = 0
    if missing and lookup_one and day:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
            for sid, price in zip(missing, executor.map(lookup_one, missing)):
                if price and price > 0:
                    price_map[sid] = float(price)
                    price_dates[sid] = day
                    looked_up += 1
    rows = apply_amount_estimates(all_stocks, price_map, price_dates=price_dates, data_date=day)
    if reference_prices is None and db_path and day:
        reference_prices = reference_close_map(db_path, day)
    top_buy, top_sell = rank_by_amount(rows, top_n=top_n)
    material = material_unpriced(
        rows, top_buy, top_sell, top_n=top_n, must_price=extra_ids, reference_prices=reference_prices
    )
    refill_requested = refill_priced = 0
    if material and refill_one and day:
        for item in material[: max(0, int(refill_cap))]:
            sid = item["id"]
            refill_requested += 1
            try:
                price = refill_one(sid)
            except Exception:  # a refill failure must never abort pricing
                price = None
            if price and price > 0:
                price_map[sid] = float(price)
                price_dates[sid] = day
                refill_priced += 1
        if refill_priced:
            rows = apply_amount_estimates(all_stocks, price_map, price_dates=price_dates, data_date=day)
            top_buy, top_sell = rank_by_amount(rows, top_n=top_n)
            material = material_unpriced(
                rows, top_buy, top_sell, top_n=top_n, must_price=extra_ids, reference_prices=reference_prices
            )
    coverage = pricing_coverage(rows, top_shares=coverage_top)
    meta = {
        "price_date": day,
        "tape_rows": tape_used,
        "tape_source": "twse_full_tape" if tape_used else None,
        "lookup_requested": len(missing),
        "lookup_priced": looked_up,
        "priced": sum(1 for r in rows if r.get("amount_status") == "priced"),
        "unpriced": sum(1 for r in rows if r.get("amount_status") != "priced"),
        "coverage_top30": coverage,
        "material_unpriced": material[:10],
        "refill_requested": refill_requested,
        "refill_priced": refill_priced,
        "market_scope": MARKET_SCOPE,
    }
    return rows, meta


def find_trust_conflicts(all_stocks: list[dict], min_abs_yi: float = 5.0) -> list[dict]:
    """外資 / 投信 opposite-side names with enough money to mention."""
    out = []
    for stock in all_stocks:
        if stock.get("amount_status") in {"unpriced", "stale_price"}:
            continue
        foreign = stock.get("foreign_net") or 0
        trust = stock.get("trust_net") or 0
        if foreign * trust >= 0:
            continue
        price = stock.get("close_price")
        foreign_amt = stock.get("est_amount") or 0
        trust_amt = round(float(trust) * float(price) / 1e8, 2) if price else 0.0
        if abs(foreign_amt) < min_abs_yi and abs(trust_amt) < min_abs_yi:
            continue
        out.append(
            {
                "id": stock.get("id"),
                "name": stock.get("name"),
                "foreign_amount": foreign_amt,
                "trust_amount": trust_amt,
                "note": "外資買、投信賣" if foreign > 0 else "外資賣、投信買",
            }
        )
    out.sort(key=lambda x: abs(x.get("foreign_amount") or 0), reverse=True)
    return out[:5]


def format_conflict_line(row: dict) -> str:
    name = str(row.get("name") or row.get("id") or "").strip()
    note = row.get("note") or "土洋對作"
    f_amt = row.get("foreign_amount")
    t_amt = row.get("trust_amount")
    bits = [f"{name}：{note}"]
    if f_amt:
        bits.append(f"外資 {f_amt:+.1f}億")
    if t_amt:
        bits.append(f"投信 {t_amt:+.1f}億")
    return "，".join(bits)


def yfinance_price_lookup(tickers: list[str], lookup_one: Callable[[str], float | None] | None = None) -> dict[str, float]:
    """Optional hook for tests; production passes DataFetcher's per-ticker lookup."""
    out: dict[str, float] = {}
    if not lookup_one:
        return out
    for sid in tickers:
        price = lookup_one(sid)
        if price and price > 0:
            out[sid] = float(price)
    return out
