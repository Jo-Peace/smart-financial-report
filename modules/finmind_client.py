"""
Optional FinMind institutional fallback (opt-in).

Enable with:
  FINMIND_FALLBACK=1
  FINMIND_TOKEN=...   # required for chip datasets

TWSE remains primary. Same-day only; never mixes dates.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

FINMIND_API = "https://api.finmindtrade.com/api/v4/data"
DATASET_INST = "TaiwanStockInstitutionalInvestorsBuySell"
DATASET_INFO = "TaiwanStockInfo"

FOREIGN_NAMES = {
    "Foreign_Investor",
    "ForeignInvestor",
    "外陸資",
    "外資",
    "Foreign Investors",
}
FOREIGN_DEALER_NAMES = {"Foreign_Dealer_Self", "外資自營商"}
TRUST_NAMES = {"Investment_Trust", "InvestmentTrust", "投信"}
DEALER_NAMES = {"Dealer_self", "Dealer_Self", "Dealer_Hedging", "Dealer", "自營商", "自營商(自行買賣)", "自營商(避險)"}

# FinMind covers 上市 + 上櫃 in one dataset.
MARKET_SCOPE = "TWSE_TPEx"


def finmind_fallback_enabled() -> bool:
    flag = (os.getenv("FINMIND_FALLBACK") or "0").strip().lower()
    return flag in {"1", "true", "yes", "on"}


def finmind_token() -> str:
    return (os.getenv("FINMIND_TOKEN") or os.getenv("FINMIND_API_TOKEN") or "").strip()


def _get_json(params: dict, timeout: float) -> dict | None:
    token = finmind_token()
    if token:
        params = {**params, "token": token}
    try:
        resp = requests.get(FINMIND_API, params=params, timeout=timeout)
    except requests.RequestException as exc:
        logger.warning("FinMind request failed: %s", exc)
        print(f"  [FinMind] 連線失敗：{exc}")
        return None
    try:
        payload = resp.json()
    except ValueError:
        logger.warning("FinMind non-JSON status=%s", resp.status_code)
        print(f"  [FinMind] 非 JSON 回應 HTTP {resp.status_code}")
        return None

    status = payload.get("status")
    if resp.status_code != 200 or (status not in (None, 200, "200") and not payload.get("data")):
        msg = payload.get("msg") or payload.get("message") or resp.text[:160]
        print(f"  [FinMind] API 無法使用：{msg}")
        return None
    return payload


def _as_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _row_net(row: dict) -> int | None:
    buy = _as_int(row.get("buy"))
    sell = _as_int(row.get("sell"))
    if buy is not None and sell is not None:
        return buy - sell
    return _as_int(row.get("buy_sell"))


def _foreign_net_from_row(row: dict) -> int | None:
    name = str(row.get("name") or "").strip()
    if name and name not in FOREIGN_NAMES:
        return None
    return _row_net(row)


def _category(name: str) -> str | None:
    name = str(name or "").strip()
    if not name or name in FOREIGN_NAMES:
        return "foreign_net"
    if name in FOREIGN_DEALER_NAMES:
        return "foreign_dealer_net"
    if name in TRUST_NAMES:
        return "trust_net"
    if name in DEALER_NAMES:
        return "dealer_net"
    return None


def aggregate_institutional_rows(rows: list[dict]) -> dict[str, dict]:
    """
    FinMind rows (one per stock × investor type) → per-stock nets.

    ``foreign_net`` keeps the TWSE T86 meaning (外陸資, 不含外資自營商);
    ``total_net`` is the sum of every investor type (三大法人合計), so 合計 means
    the same thing on both paths.
    """
    out: dict[str, dict] = {}
    for row in rows or []:
        sid = str(row.get("stock_id") or "").strip()
        if not sid:
            continue
        key = _category(row.get("name"))
        net = _row_net(row)
        if key is None or net is None:
            continue
        rec = out.setdefault(
            sid,
            {"foreign_net": None, "foreign_dealer_net": 0, "trust_net": 0, "dealer_net": 0},
        )
        if key == "foreign_net":
            rec["foreign_net"] = (rec["foreign_net"] or 0) + net
        else:
            rec[key] += net
    for rec in out.values():
        rec["total_net"] = (rec["foreign_net"] or 0) + rec["foreign_dealer_net"] + rec["trust_net"] + rec["dealer_net"]
    return {sid: rec for sid, rec in out.items() if rec["foreign_net"] is not None}


# TaiwanStockInfo.type → the row's market (T86 rows say TWSE_listed).
INFO_MARKETS = {"twse": "TWSE_listed", "tpex": "TPEx"}


def parse_stock_info(rows) -> dict[str, dict]:
    """TaiwanStockInfo rows → {stock_id: {"name", "market"}} (market None when unknown)."""
    out: dict[str, dict] = {}
    for row in rows or []:
        sid = str(row.get("stock_id") or "").strip()
        sname = str(row.get("stock_name") or "").strip()
        if not sid:
            continue
        rec = out.setdefault(sid, {"name": "", "market": None})
        if sname and not rec["name"]:
            rec["name"] = sname
        market = INFO_MARKETS.get(str(row.get("type") or "").strip().lower())
        if market and not rec["market"]:
            rec["market"] = market
    return out


def _stock_info_map(timeout: float) -> dict[str, dict]:
    payload = _get_json({"dataset": DATASET_INFO}, timeout=timeout)
    if not payload:
        return {}
    return parse_stock_info(payload.get("data"))


def _stock_name_map(timeout: float) -> dict[str, str]:
    return {sid: rec["name"] for sid, rec in _stock_info_map(timeout).items() if rec.get("name")}


def _price_rows(stocks: list[dict], top_n: int, date: str) -> tuple[list[dict], dict]:
    """Same-session pricing shared with the TWSE path (no stale prices, no est_amount=0)."""
    from modules.institutional_ranking import price_rows_same_session, yf_same_day_close

    tape_rows: list[dict] = []
    try:
        from modules.twse_tape import fetch_twse_tape

        tape = fetch_twse_tape(date, quiet=True)
        tape_rows = list((tape or {}).get("rows") or [])
    except Exception:
        logger.debug("FinMind pricing: TWSE tape unavailable", exc_info=True)
    rows, meta = price_rows_same_session(
        stocks,
        date,
        tape_rows=tape_rows,
        lookup_one=lambda sid: yf_same_day_close(sid, date),
        share_cap=max(top_n * 4, 40),
    )
    meta["market_scope"] = MARKET_SCOPE
    return rows, meta


def fetch_foreign_institutional_top(
    date: str,
    top_n: int = 10,
    timeout: float | None = None,
    *,
    force: bool = False,
) -> dict[str, Any] | None:
    """
    Same-day foreign net buy/sell ranking via FinMind.

    Returns None if disabled, missing token, or unavailable.
    force=True: used by compare tooling; skips FINMIND_FALLBACK gate (token still required).
    """
    if not force and not finmind_fallback_enabled():
        return None
    if not finmind_token():
        if force:
            print("  [FinMind] 未設定 FINMIND_TOKEN，無法對照。")
        else:
            print("  [FinMind] FINMIND_FALLBACK=1 但未設定 FINMIND_TOKEN，略過備援。")
        return None

    timeout = float(timeout or os.getenv("FINMIND_TIMEOUT_SEC") or 30)
    date = str(date).strip()[:10]
    print(f"  [FinMind] 嘗試同日法人備援 date={date}…")

    payload = _get_json(
        {
            "dataset": DATASET_INST,
            "start_date": date,
            "end_date": date,
        },
        timeout=timeout,
    )
    if not payload or not payload.get("data"):
        return None

    nets = aggregate_institutional_rows(payload["data"])
    if not nets:
        print("  [FinMind] 有回應但無法解析外資買賣超（需 Foreign_Investor 列）。")
        return None

    from modules.institutional_ranking import find_trust_conflicts, rank_by_amount

    info_map = _stock_info_map(timeout=min(timeout, 20.0))
    all_stocks = [
        {
            "id": sid,
            "name": (info_map.get(sid) or {}).get("name") or sid,
            **rec,
            # 上櫃 names need a .TWO Yahoo symbol downstream (build_dynamic_universe).
            "market": (info_map.get(sid) or {}).get("market"),
            "est_amount": None,
            "close_price": None,
        }
        for sid, rec in nets.items()
    ]
    all_stocks, pricing = _price_rows(all_stocks, top_n=top_n, date=date)
    top_buy, top_sell = rank_by_amount(all_stocks, top_n=top_n)
    if not top_buy and not top_sell:
        # No same-session price at all: an 億元 leaderboard cannot be built
        # honestly (the old code wrote est_amount=0 and ranked by shares).
        print(f"  [FinMind] {date} 無任何同日股價可估算金額，不使用此備援。")
        return None
    pricing["basis"] = "amount"
    trust_conflicts = find_trust_conflicts(all_stocks)

    def _amt(row):
        value = row.get("est_amount")
        return f"{value:+.1f}億" if isinstance(value, (int, float)) else "未定價"

    print(f"  [FinMind] ✅ 備援成功 date={date} stocks={len(all_stocks)}（上市＋上櫃，排序依據：{pricing['basis']}）")
    for s in top_buy[:3]:
        print(f"     買 {s['id']} {s['name']}: {s['foreign_net']:+,} 股 ({_amt(s)})")
    for s in top_sell[:3]:
        print(f"     賣 {s['id']} {s['name']}: {s['foreign_net']:+,} 股 ({_amt(s)})")

    return {
        "top_buy": top_buy,
        "top_sell": top_sell,
        "trust_conflicts": trust_conflicts,
        "data_date": date,
        "source": "finmind",
        "market_scope": MARKET_SCOPE,
        "pricing": pricing,
    }
