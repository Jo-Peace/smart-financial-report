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
from concurrent.futures import ThreadPoolExecutor
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


def _foreign_net_from_row(row: dict) -> int | None:
    name = str(row.get("name") or "").strip()
    if name and name not in FOREIGN_NAMES:
        return None
    buy = _as_int(row.get("buy"))
    sell = _as_int(row.get("sell"))
    if buy is not None and sell is not None:
        return buy - sell
    return _as_int(row.get("buy_sell"))


def _stock_name_map(timeout: float) -> dict[str, str]:
    payload = _get_json({"dataset": DATASET_INFO}, timeout=timeout)
    if not payload:
        return {}
    out: dict[str, str] = {}
    for row in payload.get("data") or []:
        sid = str(row.get("stock_id") or "").strip()
        sname = str(row.get("stock_name") or "").strip()
        if sid and sname:
            out[sid] = sname
    return out


def _attach_prices(stocks: list[dict], top_n: int) -> None:
    ranked = sorted(stocks, key=lambda x: abs(x["foreign_net"]), reverse=True)
    candidates = ranked[: max(top_n * 4, 20)]

    def _price(stock: dict) -> None:
        try:
            import yfinance as yf

            hist = yf.Ticker(f"{stock['id']}.TW").history(period="5d")
            if hist.empty:
                hist = yf.Ticker(f"{stock['id']}.TWO").history(period="5d")
            if not hist.empty:
                close_price = float(hist["Close"].iloc[-1])
                stock["close_price"] = close_price
                stock["est_amount"] = round(stock["foreign_net"] * close_price / 1e8, 2)
                return
        except Exception:
            pass
        stock["close_price"] = None
        stock["est_amount"] = 0

    with ThreadPoolExecutor(max_workers=10) as executor:
        list(executor.map(_price, candidates))


def fetch_foreign_institutional_top(
    date: str,
    top_n: int = 10,
    timeout: float | None = None,
) -> dict[str, Any] | None:
    """
    Same-day foreign net buy/sell ranking via FinMind.

    Returns None if disabled, missing token, or unavailable.
    """
    if not finmind_fallback_enabled():
        return None
    if not finmind_token():
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

    nets: dict[str, int] = {}
    for row in payload["data"]:
        sid = str(row.get("stock_id") or "").strip()
        if not sid:
            continue
        net = _foreign_net_from_row(row)
        if net is None:
            continue
        nets[sid] = net

    if not nets:
        print("  [FinMind] 有回應但無法解析外資買賣超（需 Foreign_Investor 列）。")
        return None

    name_map = _stock_name_map(timeout=min(timeout, 20.0))
    all_stocks = [
        {
            "id": sid,
            "name": name_map.get(sid, sid),
            "foreign_net": foreign_net,
            "trust_net": 0,
            "total_net": foreign_net,
            "est_amount": 0,
            "close_price": None,
        }
        for sid, foreign_net in nets.items()
    ]
    _attach_prices(all_stocks, top_n=top_n)

    if any(s.get("est_amount") for s in all_stocks):
        top_buy = sorted(all_stocks, key=lambda x: x.get("est_amount") or 0, reverse=True)[:top_n]
        top_sell = sorted(all_stocks, key=lambda x: x.get("est_amount") or 0)[:top_n]
    else:
        top_buy = sorted(all_stocks, key=lambda x: x["foreign_net"], reverse=True)[:top_n]
        top_sell = sorted(all_stocks, key=lambda x: x["foreign_net"])[:top_n]

    print(f"  [FinMind] ✅ 備援成功 date={date} stocks={len(all_stocks)}")
    for s in top_buy[:3]:
        print(f"     買 {s['id']} {s['name']}: {s['foreign_net']:+,} 股 ({s.get('est_amount', 0):+.1f}億)")
    for s in top_sell[:3]:
        print(f"     賣 {s['id']} {s['name']}: {s['foreign_net']:+,} 股 ({s.get('est_amount', 0):+.1f}億)")

    return {
        "top_buy": top_buy,
        "top_sell": top_sell,
        "data_date": date,
        "source": "finmind",
    }
