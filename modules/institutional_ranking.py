"""
Full-market institutional amount ranking.

T86 is share-based. Ranking only the top N*4 names by |shares| drops high-price
names (聯發科 / 台積電 / 奇鋐) that CTBC still lists by 億元.
"""

from __future__ import annotations

import sqlite3
from typing import Callable

import requests

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


def parse_day_all_payload(payload: dict) -> list[dict]:
    """Parse TWSE STOCK_DAY_ALL JSON into {id, name, close_price, volume}."""
    if not isinstance(payload, dict):
        return []
    if payload.get("stat") not in (None, "OK"):
        return []
    fields = [str(x) for x in (payload.get("fields") or [])]

    def _idx(*names: str, default: int | None = None) -> int | None:
        for name in names:
            if name in fields:
                return fields.index(name)
        return default

    i_id = _idx("證券代號", "代號", default=0)
    i_name = _idx("證券名稱", "名稱", default=1)
    i_vol = _idx("成交股數", default=2)
    i_close = _idx("收盤價", default=7)
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
        rows.append({"id": ticker, "name": name, "close_price": close, "volume": volume})
    return rows


def fetch_twse_day_all(date_str: str, timeout: float = 8.0) -> list[dict]:
    """One TWSE call for all listed closes. Empty list on timeout / bad payload."""
    ymd = str(date_str or "").replace("-", "")[:8]
    if len(ymd) != 8:
        return []
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?date={ymd}&response=json"
    headers = {"User-Agent": "Mozilla/5.0"}
    last_err = None
    for _ in range(2):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            payload = resp.json()
            rows = parse_day_all_payload(payload)
            if rows:
                return rows
        except (requests.RequestException, ValueError, TypeError) as exc:
            last_err = exc
            continue
    if last_err:
        print(f"  [Info] 全日收盤表未取到（{last_err.__class__.__name__}），改用本機價＋補價")
    else:
        print("  [Info] 全日收盤表未取到，改用本機價＋補價")
    return []


def latest_close_map(db_path: str, close_date: str | None = None) -> dict[str, float]:
    """Newest close per ticker. Prefer daily_closes (full tape), then snapshots."""
    out: dict[str, float] = {}
    try:
        conn = sqlite3.connect(db_path)
        if close_date:
            for ticker, price in conn.execute(
                """
                SELECT ticker, close_price FROM daily_closes
                WHERE close_date = ? AND close_price > 0
                """,
                (str(close_date)[:10],),
            ):
                out[str(ticker)] = float(price)
        if not out:
            for ticker, price in conn.execute(
                """
                SELECT ticker, close_price FROM daily_closes
                WHERE close_price > 0
                ORDER BY close_date DESC
                """
            ):
                ticker = str(ticker)
                if ticker not in out:
                    out[ticker] = float(price)
        for row in conn.execute(
            """
            SELECT ticker, price, snapshot_date
            FROM market_snapshots
            WHERE price IS NOT NULL AND price > 0
            ORDER BY snapshot_date DESC
            """
        ):
            ticker = str(row[0] or "").strip()
            if not ticker or ticker in out:
                continue
            try:
                price = float(row[1])
            except (TypeError, ValueError):
                continue
            if price > 0:
                out[ticker] = price
        conn.close()
    except sqlite3.Error:
        return out
    return out


def price_lookup_candidates(
    all_stocks: list[dict],
    price_map: dict[str, float],
    *,
    share_cap: int = 80,
    extra_ids: tuple[str, ...] = HIGH_PRICE_ALWAYS,
) -> list[str]:
    """Tickers that still need a live price before amount ranking."""
    by_shares = sorted(all_stocks, key=lambda x: abs(x.get("foreign_net") or 0), reverse=True)
    wanted = {str(s.get("id") or "").strip() for s in by_shares[:share_cap] if s.get("id")}
    wanted.update(extra_ids)
    have = {str(s.get("id") or "").strip() for s in all_stocks}
    missing = []
    for sid in wanted:
        if sid and sid in have and sid not in price_map:
            missing.append(sid)
    return missing


def apply_amount_estimates(all_stocks: list[dict], price_map: dict[str, float]) -> list[dict]:
    priced = []
    for raw in all_stocks:
        stock = dict(raw)
        sid = str(stock.get("id") or "").strip()
        price = price_map.get(sid)
        shares = stock.get("foreign_net") or 0
        if price and float(price) > 0:
            stock["close_price"] = float(price)
            stock["est_amount"] = round(float(shares) * float(price) / 1e8, 2)
        else:
            stock.setdefault("close_price", None)
            stock["est_amount"] = 0
        priced.append(stock)
    return priced


def rank_by_amount(all_stocks: list[dict], top_n: int = 10) -> tuple[list[dict], list[dict]]:
    top_buy = sorted(all_stocks, key=lambda x: x.get("est_amount") or 0, reverse=True)[:top_n]
    top_sell = sorted(all_stocks, key=lambda x: x.get("est_amount") or 0)[:top_n]
    return top_buy, top_sell


def find_trust_conflicts(all_stocks: list[dict], min_abs_yi: float = 5.0) -> list[dict]:
    """外資 / 投信 opposite-side names with enough money to mention."""
    out = []
    for stock in all_stocks:
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
