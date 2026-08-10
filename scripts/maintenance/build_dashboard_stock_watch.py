#!/usr/bin/env python3
"""Build a compact stock-watch JSON for the local AntiGravity dashboard."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[2]
REPORTS_DIR = BASE_DIR / "reports"
DAILY_DIR = REPORTS_DIR / "daily"
DB_PATH = BASE_DIR / "data" / "analytics.db"
DEFAULT_OUTPUT = REPORTS_DIR / "dashboard_stock_watch.json"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _latest_structured_path(date: str | None = None) -> Path | None:
    if date:
        candidate = DAILY_DIR / date / "structured_data.json"
        return candidate if candidate.is_file() else None

    candidates = sorted(
        DAILY_DIR.glob("20??-??-??/structured_data.json"),
        key=lambda path: path.parent.name,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _percent(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _hit_rate(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value) * 100, 1)
    except (TypeError, ValueError):
        return None


def _item_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        ticker = item.get("ticker") or item.get("id") or ""
        name = item.get("name") or ""
        direction = item.get("direction") or ""
        reason = item.get("reason") or item.get("trigger") or item.get("thesis") or ""
        return " ".join(str(v) for v in (ticker, name, direction, reason) if v)
    return str(item)


def _stock_key(ticker: str | None, name: str | None) -> str:
    return (ticker or name or "").strip()


def _parse_stock_label(text: str) -> tuple[str, str]:
    match = re.match(r"\s*(?P<ticker>\d{4})?\s*(?P<name>[^（(]+)", text)
    if not match:
        return "", text.strip()
    return (match.group("ticker") or "").strip(), (match.group("name") or "").strip()


def _recent_structured_dates(limit: int = 20) -> list[str]:
    dates: list[str] = []
    for path in sorted(DAILY_DIR.glob("20??-??-??/structured_data.json"), key=lambda p: p.parent.name, reverse=True):
        dates.append(path.parent.name)
        if len(dates) >= limit:
            break
    return dates


def _build_current_watch(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    tracking: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    seen_tracking: set[str] = set()
    seen_warning: set[str] = set()
    focus_name_by_ticker = {
        str(item.get("ticker") or "").strip(): str(item.get("name") or "").strip()
        for item in data.get("focus_ranking") or []
        if isinstance(item, dict)
    }

    for item in data.get("short_term_picks") or data.get("prediction_targets") or []:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker") or item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        key = _stock_key(ticker, name)
        if not key or key in seen_tracking:
            continue
        seen_tracking.add(key)
        tracking.append(
            {
                "ticker": ticker,
                "name": name,
                "direction": item.get("direction") or "",
                "horizon": item.get("horizon") or data.get("short_term_horizon") or "1~5日",
                "timeScale": item.get("time_scale") or "短線",
                "reason": item.get("reason") or item.get("trigger") or "",
                "invalidation": item.get("invalidation") or item.get("stop_loss_desc") or "",
                "source": "latest_short_term_pick",
            }
        )

    for item in data.get("long_term_picks") or []:
        if not isinstance(item, dict):
            continue
        theme = str(item.get("theme") or item.get("ticker") or item.get("name") or "").strip()
        if not theme:
            continue
        ticker = str(item.get("ticker") or (theme if theme.isdigit() else "")).strip()
        name = str(item.get("name") or focus_name_by_ticker.get(ticker) or theme).strip()
        tracking.append(
            {
                "ticker": ticker,
                "name": name,
                "direction": item.get("direction") or "觀察",
                "horizon": item.get("horizon") or data.get("long_term_horizon") or "2~6週",
                "timeScale": item.get("time_scale") or "中期",
                "reason": item.get("thesis") or item.get("evidence") or "",
                "invalidation": item.get("invalidation") or "需要 20D / 60D 持續驗證",
                "source": "latest_long_term_pick",
            }
        )

    for text in data.get("heavyweights_dumped") or []:
        ticker, name = _parse_stock_label(_item_text(text))
        key = _stock_key(ticker, name)
        if not key or key in seen_warning:
            continue
        seen_warning.add(key)
        warnings.append(
            {
                "ticker": ticker,
                "name": name,
                "direction": "注意",
                "reason": _item_text(text),
                "riskType": "外資賣超 / 權值撤退",
            }
        )

    for text in data.get("price_volume_divergence") or []:
        ticker, name = _parse_stock_label(_item_text(text))
        key = _stock_key(ticker, name)
        if not key or key in seen_warning:
            continue
        seen_warning.add(key)
        warnings.append(
            {
                "ticker": ticker,
                "name": name,
                "direction": "注意",
                "reason": _item_text(text),
                "riskType": "量價 / 籌碼背離",
            }
        )

    focus = []
    for item in data.get("focus_ranking") or []:
        if not isinstance(item, dict):
            continue
        focus.append(
            {
                "ticker": item.get("ticker") or "",
                "name": item.get("name") or "",
                "role": item.get("role_label") or item.get("base_role") or "",
                "titlePriority": item.get("title_priority"),
                "foreignBuyStrength": item.get("foreign_buy_strength"),
                "validationCredibility": item.get("validation_credibility"),
            }
        )

    return tracking[:6], warnings[:6], focus[:6]


def _build_analytics(limit: int = 12) -> dict[str, Any]:
    if not DB_PATH.is_file():
        return {"available": False, "reason": "analytics.db not found"}

    conn = _connect()
    cur = conn.cursor()
    by_horizon = cur.execute(
        """
        SELECT horizon_days, COUNT(*) AS samples, AVG(hit) AS hit_rate,
               AVG(return_pct) AS avg_return, AVG(excess_return_pct) AS avg_excess
        FROM prediction_results
        GROUP BY horizon_days
        ORDER BY horizon_days
        """
    ).fetchall()
    recent_results = cur.execute(
        """
        SELECT p.prediction_date, p.ticker, p.name, p.direction,
               r.horizon_days, r.evaluated_date, r.return_pct,
               r.excess_return_pct, r.hit, p.trigger
        FROM prediction_results r
        JOIN predictions p ON p.id = r.prediction_id
        ORDER BY r.evaluated_date DESC, p.prediction_date DESC, p.ticker, r.horizon_days
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    open_predictions = cur.execute(
        """
        SELECT p.prediction_date, p.ticker, p.name, p.direction, p.trigger
        FROM predictions p
        WHERE NOT EXISTS (
            SELECT 1 FROM prediction_results r WHERE r.prediction_id = p.id
        )
        ORDER BY p.prediction_date DESC, p.ticker
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    stock_score = cur.execute(
        """
        SELECT p.ticker, p.name,
               COUNT(*) AS samples,
               AVG(r.hit) AS hit_rate,
               AVG(r.excess_return_pct) AS avg_excess,
               MAX(r.evaluated_date) AS latest_eval
        FROM prediction_results r
        JOIN predictions p ON p.id = r.prediction_id
        GROUP BY p.ticker, p.name
        HAVING COUNT(*) >= 2
        ORDER BY hit_rate DESC, avg_excess DESC, samples DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()

    return {
        "available": True,
        "byHorizon": [
            {
                "horizonDays": row["horizon_days"],
                "samples": row["samples"],
                "hitRatePct": _hit_rate(row["hit_rate"]),
                "avgReturnPct": _percent(row["avg_return"]),
                "avgExcessPct": _percent(row["avg_excess"]),
            }
            for row in by_horizon
        ],
        "recentResults": [
            {
                "predictionDate": row["prediction_date"],
                "ticker": row["ticker"],
                "name": row["name"] or "",
                "direction": row["direction"] or "",
                "horizonDays": row["horizon_days"],
                "evaluatedDate": row["evaluated_date"],
                "returnPct": _percent(row["return_pct"]),
                "excessPct": _percent(row["excess_return_pct"]),
                "hit": bool(row["hit"]),
                "trigger": row["trigger"] or "",
            }
            for row in recent_results
        ],
        "openPredictions": [
            {
                "predictionDate": row["prediction_date"],
                "ticker": row["ticker"],
                "name": row["name"] or "",
                "direction": row["direction"] or "",
                "trigger": row["trigger"] or "",
            }
            for row in open_predictions
        ],
        "stockScoreboard": [
            {
                "ticker": row["ticker"],
                "name": row["name"] or "",
                "samples": row["samples"],
                "hitRatePct": _hit_rate(row["hit_rate"]),
                "avgExcessPct": _percent(row["avg_excess"]),
                "latestEval": row["latest_eval"],
            }
            for row in stock_score
        ],
    }


def build_payload(date: str | None = None) -> dict[str, Any]:
    structured_path = _latest_structured_path(date)
    structured_data = _read_json(structured_path) if structured_path else {}
    tracking, warnings, focus = _build_current_watch(structured_data)
    analytics = _build_analytics()
    return {
        "generatedAt": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sourceDate": structured_data.get("date") or (structured_path.parent.name if structured_path else ""),
        "sourcePath": str(structured_path.relative_to(BASE_DIR)) if structured_path else "",
        "recentStructuredDates": _recent_structured_dates(),
        "marketSummary": {
            "indexAction": structured_data.get("index_action") or "",
            "editorialFrame": (structured_data.get("editorial_frame") or {}).get("label") or "",
            "shortTermHorizon": structured_data.get("short_term_horizon") or "1~5日",
            "longTermHorizon": structured_data.get("long_term_horizon") or "2~6週 / 1~3個月",
        },
        "currentTracking": tracking,
        "currentWarnings": warnings,
        "focusRanking": focus,
        "analytics": analytics,
        "notes": [
            "此檔為 Dashboard 展示用摘要，不是投資建議。",
            "命中率來自 analytics.db 的 prediction_results；未驗收標的會留在 openPredictions。",
            "今日追蹤股優先取最新 structured_data 的 short_term_picks / prediction_targets。",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build dashboard_stock_watch.json")
    parser.add_argument("--date", help="指定 YYYY-MM-DD；預設使用最新 daily structured_data")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="輸出 JSON 路徑")
    args = parser.parse_args(argv)

    payload = build_payload(args.date)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {output}")
    print(f"Source date: {payload.get('sourceDate') or 'N/A'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
