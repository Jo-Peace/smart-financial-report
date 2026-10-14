#!/usr/bin/env python3
"""
Compare same-day foreign institutional top lists: TWSE T86 vs FinMind.

Usage:
  python3 cli.py maintenance compare-institutional --date 2026-08-07
  python3 cli.py maintenance compare-institutional --date 2026-08-07 --top 10 --json

Exit codes:
  0  both sources OK and Jaccard(top5 buy ids) >= threshold (default 0.4)
  1  comparison ran but below threshold / partial data
  2  missing date or hard failure to load TWSE
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv

load_dotenv(BASE_DIR / ".env")


def _ids(rows: list, n: int) -> list[str]:
    out = []
    for row in (rows or [])[:n]:
        sid = str(row.get("id") or row.get("ticker") or "").strip()
        if sid:
            out.append(sid)
    return out


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _load_twse(date: str, top_n: int) -> dict | None:
    from modules.data_fetcher import DataFetcher
    from modules.fetch_result import unwrap_fetch_result

    fetcher = DataFetcher(tavily_api_key=os.getenv("TAVILY_API_KEY") or "unused")
    # Short readiness wait for tooling (not full /1 path).
    check = datetime.strptime(date, "%Y-%m-%d")
    raw, data_date = fetcher._fetch_institutional_payload(
        check,
        day_attempts=2,
        label=f"compare:{date}",
    )
    if not raw or not raw.get("data"):
        return None

    def parse_num(s):
        return int(str(s).replace(",", "").replace(" ", ""))

    all_stocks = []
    for row in raw["data"]:
        try:
            all_stocks.append(
                {
                    "id": row[0].strip(),
                    "name": row[1].strip(),
                    "foreign_net": parse_num(row[4]),
                }
            )
        except (ValueError, IndexError):
            continue

    # Share-based rank (fast; amount rank needs Yahoo and is optional noise for id overlap)
    by_shares = sorted(all_stocks, key=lambda x: x["foreign_net"], reverse=True)
    top_buy = by_shares[:top_n]
    top_sell = sorted(all_stocks, key=lambda x: x["foreign_net"])[:top_n]
    return {
        "source": "twse",
        "data_date": data_date or date,
        "top_buy": top_buy,
        "top_sell": top_sell,
        "rank_mode": "foreign_net_shares",
    }


def _load_finmind(date: str, top_n: int) -> dict | None:
    from modules.finmind_client import fetch_foreign_institutional_top

    # Compare tooling always attempts FinMind when token present (force=True).
    fm = fetch_foreign_institutional_top(date, top_n=top_n, force=True)
    if not fm:
        return None
    # Prefer share rank for apples-to-apples with TWSE share sort in this script
    buy = sorted(fm.get("top_buy") or [], key=lambda x: x.get("foreign_net") or 0, reverse=True)[:top_n]
    sell = sorted(fm.get("top_sell") or [], key=lambda x: x.get("foreign_net") or 0)[:top_n]
    # If FinMind already returned amount-sorted lists, rebuild from those stocks if needed
    if not buy and not sell:
        return None
    return {
        "source": "finmind",
        "data_date": fm.get("data_date") or date,
        "top_buy": buy,
        "top_sell": sell,
        "rank_mode": "foreign_net_shares",
    }


def _print_side(label: str, rows: list, n: int = 5) -> None:
    print(f"  [{label}]")
    for i, row in enumerate((rows or [])[:n], 1):
        sid = row.get("id")
        name = row.get("name")
        net = row.get("foreign_net")
        amt = row.get("est_amount")
        extra = f" ({amt:+.1f}億)" if isinstance(amt, (int, float)) and amt else ""
        print(f"    {i}. {sid} {name}: {net:+,} 股{extra}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TWSE vs FinMind 同日外資買賣超 top 對照")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--top", type=int, default=10, help="top N（預設 10）")
    parser.add_argument("--overlap", type=int, default=5, help="Jaccard 計算用前 K 名（預設 5）")
    parser.add_argument(
        "--min-jaccard",
        type=float,
        default=0.4,
        help="top-K buy id 集合 Jaccard 下限（預設 0.4）",
    )
    parser.add_argument("--json", action="store_true", help="輸出 JSON 結果")
    args = parser.parse_args(argv)

    date = args.date.strip()
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        print(f"[Error] 日期格式錯誤: {date}", file=sys.stderr)
        return 2

    print(f"\n=== 法人來源對照 date={date} top={args.top} ===\n")
    try:
        twse = _load_twse(date, args.top)
    except Exception as exc:
        from modules.twse_http import is_connect_failure

        if not is_connect_failure(exc):
            raise
        print(f"[Error] TWSE 連線失敗（網路中斷？）：{exc.__class__.__name__}", file=sys.stderr)
        return 2
    if not twse:
        print("[Error] TWSE T86 無法取得該日資料（可能非交易日或尚未發布）", file=sys.stderr)
        return 2

    print(f"TWSE data_date={twse['data_date']} rank={twse['rank_mode']}")
    _print_side("TWSE 買超", twse["top_buy"])
    _print_side("TWSE 賣超", twse["top_sell"])
    print()

    finmind = _load_finmind(date, args.top)
    result = {
        "date": date,
        "twse": {
            "ok": True,
            "data_date": twse["data_date"],
            "buy_ids": _ids(twse["top_buy"], args.overlap),
            "sell_ids": _ids(twse["top_sell"], args.overlap),
        },
        "finmind": {"ok": False},
        "metrics": {},
        "pass": False,
    }

    if not finmind:
        print("FinMind：不可用（未設 FINMIND_TOKEN、額度不足、或該日無資料）")
        print("→ 對照未完成；TWSE 端可讀。")
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    print(f"FinMind data_date={finmind['data_date']} rank={finmind['rank_mode']}")
    _print_side("FinMind 買超", finmind["top_buy"])
    _print_side("FinMind 賣超", finmind["top_sell"])
    print()

    k = args.overlap
    tw_buy, fm_buy = _ids(twse["top_buy"], k), _ids(finmind["top_buy"], k)
    tw_sell, fm_sell = _ids(twse["top_sell"], k), _ids(finmind["top_sell"], k)
    j_buy = _jaccard(tw_buy, fm_buy)
    j_sell = _jaccard(tw_sell, fm_sell)

    print(f"Overlap top{k} buy  ids TWSE={tw_buy}")
    print(f"                 FinMind={fm_buy}")
    print(f"  Jaccard buy  = {j_buy:.3f}  (min {args.min_jaccard})")
    print(f"Overlap top{k} sell ids TWSE={tw_sell}")
    print(f"                 FinMind={fm_sell}")
    print(f"  Jaccard sell = {j_sell:.3f}")

    passed = j_buy >= args.min_jaccard
    print()
    print("✅ PASS" if passed else "⚠️  BELOW THRESHOLD", f"(buy Jaccard {j_buy:.3f})")

    result["finmind"] = {
        "ok": True,
        "data_date": finmind["data_date"],
        "buy_ids": fm_buy,
        "sell_ids": fm_sell,
    }
    result["metrics"] = {
        "jaccard_buy_topk": round(j_buy, 4),
        "jaccard_sell_topk": round(j_sell, 4),
        "k": k,
        "min_jaccard": args.min_jaccard,
    }
    result["pass"] = passed

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
