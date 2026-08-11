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


# 回測解讀預設：歷史上 20D 命中率與超額最穩，短線窗噪音高、僅作節奏參考。
PRIMARY_RELIABILITY_HORIZON = 20
SHORT_HORIZONS = {1, 3, 5}


def _build_interpretation(by_horizon_rows: list[dict], primary_row: dict | None) -> dict[str, Any]:
    """Natural, sample-aware copy for dashboard / UI."""
    try:
        from modules.horizon_reliability import natural_reliability_sentences, reliability_one_liner

        summary = reliability_one_liner(by_horizon_rows) or (
            f"{PRIMARY_RELIABILITY_HORIZON}D 是可信度主指標；"
            "1D/3D/5D 僅作短線節奏參考，噪音高，不單獨當勝負依據。"
        )
        sentences = natural_reliability_sentences(by_horizon_rows)
    except Exception:
        summary = (
            f"{PRIMARY_RELIABILITY_HORIZON}D 是可信度主指標；"
            "1D/3D/5D 僅作短線節奏參考，噪音高，不單獨當勝負依據。"
        )
        sentences = []

    primary_hit = (primary_row or {}).get("hitRatePct")
    primary_n = (primary_row or {}).get("samples")
    return {
        "primaryHorizonDays": PRIMARY_RELIABILITY_HORIZON,
        "summary": summary,
        "sentences": sentences,
        "shortTerm": "1D / 3D / 5D → 內容節奏、隔日驗收用；命中率接近隨機時勿強化敘事。",
        "primary": (
            f"{PRIMARY_RELIABILITY_HORIZON}D → 選標、標題主角、中期追蹤的主要依據"
            + (f"（目前 {_hit_rate_display(primary_hit)}，n={primary_n}）" if primary_hit is not None else "。")
        ),
        "doNot": "不要用 1D 成敗定義系統準不準；本機樣本若顯示 20D 領先，尺子放在 20D。",
    }


def _hit_rate_display(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return str(value)


def _horizon_role(horizon_days: int) -> dict[str, str]:
    """How the UI/script should describe each evaluation window."""
    h = int(horizon_days or 0)
    if h == PRIMARY_RELIABILITY_HORIZON:
        return {
            "role": "primary",
            "label": "主指標",
            "readAs": "可信度主指標：選標／標題主角／中期追蹤以 20D 為準",
        }
    if h in SHORT_HORIZONS:
        return {
            "role": "noise_ref",
            "label": "短線參考",
            "readAs": "短線節奏參考：噪音高，不宜單獨當勝負依據",
        }
    if h >= 60:
        return {
            "role": "structural",
            "label": "結構窗",
            "readAs": "長線結構驗證（樣本通常較少）",
        }
    return {
        "role": "secondary",
        "label": "次指標",
        "readAs": "輔助對照，勿凌駕 20D",
    }


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
    # 個股排行預設用 20D：混入 1/3/5D 會把短線噪音平均進「看起來準」的名單。
    stock_score = cur.execute(
        """
        SELECT p.ticker, p.name,
               COUNT(*) AS samples,
               AVG(r.hit) AS hit_rate,
               AVG(r.excess_return_pct) AS avg_excess,
               MAX(r.evaluated_date) AS latest_eval
        FROM prediction_results r
        JOIN predictions p ON p.id = r.prediction_id
        WHERE r.horizon_days = ?
        GROUP BY p.ticker, p.name
        HAVING COUNT(*) >= 2
        ORDER BY hit_rate DESC, avg_excess DESC, samples DESC
        LIMIT ?
        """,
        (PRIMARY_RELIABILITY_HORIZON, limit),
    ).fetchall()
    # 全窗對照（除錯用；UI 不應當主表）
    stock_score_all = cur.execute(
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

    by_horizon_rows = []
    primary_row = None
    for row in by_horizon:
        role = _horizon_role(row["horizon_days"])
        item = {
            "horizonDays": row["horizon_days"],
            "samples": row["samples"],
            "hitRatePct": _hit_rate(row["hit_rate"]),
            "avgReturnPct": _percent(row["avg_return"]),
            "avgExcessPct": _percent(row["avg_excess"]),
            "role": role["role"],
            "roleLabel": role["label"],
            "readAs": role["readAs"],
            "isPrimary": int(row["horizon_days"] or 0) == PRIMARY_RELIABILITY_HORIZON,
        }
        by_horizon_rows.append(item)
        if item["isPrimary"]:
            primary_row = item

    def _score_rows(rows):
        return [
            {
                "ticker": row["ticker"],
                "name": row["name"] or "",
                "samples": row["samples"],
                "hitRatePct": _hit_rate(row["hit_rate"]),
                "avgExcessPct": _percent(row["avg_excess"]),
                "latestEval": row["latest_eval"],
                "horizonDays": PRIMARY_RELIABILITY_HORIZON,
            }
            for row in rows
        ]

    return {
        "available": True,
        "primaryHorizonDays": PRIMARY_RELIABILITY_HORIZON,
        "interpretation": _build_interpretation(by_horizon_rows, primary_row),
        "primaryHorizon": primary_row,
        "byHorizon": by_horizon_rows,
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
                "isPrimaryHorizon": int(row["horizon_days"] or 0) == PRIMARY_RELIABILITY_HORIZON,
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
        # 主表：20D 個股表現（UI 預設）
        "stockScoreboard": _score_rows(stock_score),
        # 對照：全窗混合（除錯／舊版相容）
        "stockScoreboardAllHorizons": [
            {
                "ticker": row["ticker"],
                "name": row["name"] or "",
                "samples": row["samples"],
                "hitRatePct": _hit_rate(row["hit_rate"]),
                "avgExcessPct": _percent(row["avg_excess"]),
                "latestEval": row["latest_eval"],
                "horizonDays": "all",
            }
            for row in stock_score_all
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
            "解讀權重：20D = 可信度主指標；1D/3D/5D = 短線節奏參考（噪音高）。",
            "stockScoreboard 預設只統計 20D；全窗混合見 stockScoreboardAllHorizons。",
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
