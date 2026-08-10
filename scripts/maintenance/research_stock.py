#!/usr/bin/env python3
"""
research_stock.py — 個股預測履歷卡（不消耗 Gemini）

目的：服務「預測更精準」——查歷史命中、trigger、停損、多 horizon 結果。
不是給觀眾看的內容卡；channel_story 僅作可選附註。

Usage:
  python3 cli.py maintenance research-stock --ticker 2344
  python3 scripts/maintenance/research_stock.py --ticker 華邦電
  python3 scripts/maintenance/research_stock.py --ticker 2330 --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.report_paths import resolve_shared_artifact  # noqa: E402

STOCK_DB = os.path.join(BASE_DIR, "data", "stock_db.json")
ANALYTICS_DB = os.path.join(BASE_DIR, "data", "analytics.db")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
CHANNEL_STORY = resolve_shared_artifact(REPORTS_DIR, "channel_story_log")


def _load_stock_db() -> dict:
    if not os.path.exists(STOCK_DB):
        return {}
    with open(STOCK_DB, "r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_ticker(query: str, stock_db: dict) -> tuple[str | None, dict | None]:
    q = (query or "").strip()
    if not q:
        return None, None
    # exact ticker
    if q in stock_db:
        return q, stock_db[q]
    q_up = q.upper().replace(".TW", "").replace(".TWO", "")
    for k, v in stock_db.items():
        if str(k).upper() == q_up:
            return k, v
    # name contains
    hits = []
    for k, v in stock_db.items():
        name = str((v or {}).get("name") or "")
        if q in name or name in q:
            hits.append((k, v))
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        # prefer exact name
        for k, v in hits:
            if str((v or {}).get("name") or "") == q:
                return k, v
        return hits[0]
    # bare digits not in db
    if re.fullmatch(r"\d{4,6}[A-Z]?", q_up):
        return q_up, {}
    return None, None


def _analytics_rows(ticker: str) -> dict:
    out = {
        "predictions": [],
        "results_by_horizon": {},
        "recent_flows": [],
        "recent_volume": [],
    }
    if not os.path.exists(ANALYTICS_DB):
        return out
    con = sqlite3.connect(ANALYTICS_DB)
    con.row_factory = sqlite3.Row
    try:
        preds = con.execute(
            """
            SELECT id, prediction_date, ticker, name, direction, trigger,
                   stop_loss_price, stop_loss_desc
            FROM predictions
            WHERE ticker = ?
            ORDER BY prediction_date DESC
            LIMIT 20
            """,
            (ticker,),
        ).fetchall()
        out["predictions"] = [dict(r) for r in preds]
        ids = [r["id"] for r in preds]
        if ids:
            placeholders = ",".join("?" * len(ids))
            results = con.execute(
                f"""
                SELECT prediction_id, horizon_days, evaluated_date, return_pct,
                       index_return_pct, excess_return_pct, hit, close_price
                FROM prediction_results
                WHERE prediction_id IN ({placeholders})
                ORDER BY evaluated_date DESC
                """,
                ids,
            ).fetchall()
            by_h: dict[int, list] = {}
            for r in results:
                by_h.setdefault(int(r["horizon_days"]), []).append(dict(r))
            summary = {}
            for h, rows in sorted(by_h.items()):
                hits = [x for x in rows if x.get("hit") == 1]
                summary[h] = {
                    "n": len(rows),
                    "hit_rate": round(len(hits) / len(rows), 3) if rows else None,
                    "avg_return": round(
                        sum((x.get("return_pct") or 0) for x in rows) / len(rows), 3
                    )
                    if rows
                    else None,
                    "avg_excess": round(
                        sum((x.get("excess_return_pct") or 0) for x in rows) / len(rows), 3
                    )
                    if rows
                    else None,
                }
            out["results_by_horizon"] = summary
        flows = con.execute(
            """
            SELECT flow_date, side, rank, est_amount, foreign_net
            FROM institutional_flows
            WHERE ticker = ?
            ORDER BY flow_date DESC
            LIMIT 10
            """,
            (ticker,),
        ).fetchall()
        out["recent_flows"] = [dict(r) for r in flows]
        vols = con.execute(
            """
            SELECT ranking_date, rank, volume, pct_change, close_price
            FROM volume_rankings
            WHERE ticker = ?
            ORDER BY ranking_date DESC
            LIMIT 8
            """,
            (ticker,),
        ).fetchall()
        out["recent_volume"] = [dict(r) for r in vols]
    finally:
        con.close()
    return out


def _channel_mentions(ticker: str, name: str, limit: int = 8) -> list[str]:
    if not os.path.exists(CHANNEL_STORY):
        return []
    try:
        text = open(CHANNEL_STORY, "r", encoding="utf-8").read()
    except OSError:
        return []
    lines = []
    keys = [ticker, name] if name else [ticker]
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        if any(k and k in line for k in keys):
            # keep table rows that look like story rows
            if re.search(r"20\d{2}-\d{2}-\d{2}", line):
                lines.append(line.strip())
    return lines[:limit]


def build_card(query: str) -> dict:
    stock_db = _load_stock_db()
    ticker, entry = _resolve_ticker(query, stock_db)
    if not ticker:
        return {"error": f"找不到標的：{query}", "query": query}
    entry = entry or {}
    name = entry.get("name") or ticker
    tracking = entry.get("tracking") or {}
    market_role = entry.get("market_role") or {}
    analytics = _analytics_rows(ticker)
    card = {
        "query": query,
        "ticker": ticker,
        "name": name,
        "sector": entry.get("sector"),
        "supply_chain_role": entry.get("supply_chain_role"),
        "ai_connection": entry.get("ai_connection"),
        "not_classify_as": entry.get("not_classify_as"),
        "market_role": market_role,
        "tracking": tracking,
        "analytics": analytics,
        "channel_story_rows": _channel_mentions(ticker, name),
        "sources": {
            "stock_db": STOCK_DB,
            "analytics_db": ANALYTICS_DB,
            "channel_story": CHANNEL_STORY,
        },
    }
    return card


def format_card(card: dict) -> str:
    if card.get("error"):
        return f"❌ {card['error']}"
    lines = []
    lines.append("=" * 60)
    lines.append(f"  預測履歷卡  {card['ticker']} {card.get('name') or ''}")
    lines.append("  （目標：預測準度｜非內容包裝）")
    lines.append("=" * 60)
    lines.append(f"族群/角色：{card.get('sector') or '—'}")
    lines.append(f"供應鏈：{card.get('supply_chain_role') or '—'}")
    mr = card.get("market_role") or {}
    if mr:
        lines.append(
            f"market_role：{mr.get('label') or mr.get('base_role')} | "
            f"flow={mr.get('latest_flow_state')} | 觀察={mr.get('last_observed')}"
        )
        scores = mr.get("scores") or {}
        if scores:
            lines.append(
                "研究分數："
                f"validation_credibility={scores.get('validation_credibility')} "
                f"elasticity={scores.get('elasticity')} "
                f"foreign_buy={scores.get('foreign_buy_strength')} "
                f"volume={scores.get('volume_confirmation')} "
                f"(title_priority={scores.get('title_priority')} 僅內容用，不決定能不能預測)"
            )
        if mr.get("reason"):
            lines.append(f"角色理由：{mr['reason']}")
    tr = card.get("tracking") or {}
    if tr:
        lines.append("")
        lines.append("【預測追蹤 stock_db】← 主指標")
        lines.append(
            f"  次數={tr.get('prediction_count')} 命中={tr.get('hit_count')} "
            f"命中率={tr.get('hit_rate')} 最近預測日={tr.get('last_recommended')}"
        )
        hist = tr.get("history") or []
        for h in hist[-5:]:
            if isinstance(h, dict):
                lines.append(
                    f"  - {h.get('date')}: {h.get('direction')} | "
                    f"trigger={h.get('trigger') or '—'} | result={h.get('result') or '—'}"
                )
    an = card.get("analytics") or {}
    rh = an.get("results_by_horizon") or {}
    if rh:
        lines.append("")
        lines.append("【多 horizon 命中 analytics】← 主指標")
        for h, s in rh.items():
            lines.append(
                f"  {h}D: n={s.get('n')} hit={s.get('hit_rate')} "
                f"avg_ret={s.get('avg_return')}% excess={s.get('avg_excess')}%"
            )
    preds = an.get("predictions") or []
    if preds:
        lines.append("")
        lines.append("【最近正式預測 + 停損】")
        for p in preds[:6]:
            lines.append(
                f"  - {p.get('prediction_date')} {p.get('direction')} | {p.get('trigger')}"
            )
            if p.get("stop_loss_desc"):
                lines.append(f"    停損：{p.get('stop_loss_desc')}")
    flows = an.get("recent_flows") or []
    if flows:
        lines.append("")
        lines.append("【近期法人榜】（研究用）")
        for f in flows[:6]:
            lines.append(
                f"  - {f.get('flow_date')} {f.get('side')} rank={f.get('rank')} "
                f"est={f.get('est_amount')}"
            )
    vols = an.get("recent_volume") or []
    if vols:
        lines.append("")
        lines.append("【近期成交量榜】（研究用）")
        for v in vols[:5]:
            lines.append(
                f"  - {v.get('ranking_date')} rank={v.get('rank')} "
                f"chg={v.get('pct_change')} close={v.get('close_price')}"
            )
    story = card.get("channel_story_rows") or []
    if story:
        lines.append("")
        lines.append("【附註 channel_story｜非預測準度】")
        for row in story[:3]:
            cells = [c.strip() for c in row.strip("|").split("|")]
            if len(cells) >= 8:
                lines.append(
                    f"  - {cells[0]} views={cells[-1]} | {cells[7][:40]}"
                )
            else:
                lines.append(f"  - {row[:100]}")
    lines.append("")
    lines.append("用法：選 prediction_targets 前看命中與 trigger；views 不當作準度。")
    lines.append("地圖：reports/shared/info_compounding_map.md")
    lines.append("=" * 60)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="個股資訊複利卡")
    parser.add_argument("--ticker", "-t", required=True, help="代號或中文名，如 2344 / 華邦電")
    parser.add_argument("--json", action="store_true", help="輸出 JSON")
    args = parser.parse_args(argv)
    card = build_card(args.ticker)
    if args.json:
        print(json.dumps(card, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_card(card))
    return 1 if card.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
