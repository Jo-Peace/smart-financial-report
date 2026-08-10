"""
Closed-loop compounding brief: turn recent views + prediction tracking into
actionable allow/ban rules for /1 packaging and /2 titles.

Writes: reports/shared/weekly_compounding_brief.md
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from modules.packaging_fatigue import (
    analyze_title_opener_streaks,
    build_fatigue_state,
    _parse_channel_story_titles,
)
from modules.role_scorer import HIT_RATE_MIN_SAMPLES, _hit_rate_fraction, load_stock_db, normalize_ticker
from modules.report_paths import ensure_shared_dir, shared_dir

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
DEFAULT_LOG = os.path.join(REPORTS_DIR, "shared", "channel_story_log.md")
DEFAULT_BRIEF = os.path.join(REPORTS_DIR, "shared", "weekly_compounding_brief.md")
DEFAULT_DB = os.path.join(BASE_DIR, "data", "stock_db.json")


def _parse_views(value):
    text = str(value or "").strip()
    if text in {"", "-", "待更新"}:
        return None
    try:
        return int(text.replace(",", ""))
    except ValueError:
        return None


def _story_rows(log_path: str) -> list[dict]:
    if not os.path.exists(log_path):
        return []
    rows = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.startswith("| 20"):
                continue
            parts = [p.strip() for p in line.strip().strip("|").split("|")]
            if len(parts) < 9:
                continue
            views = _parse_views(parts[8])
            rows.append(
                {
                    "date": parts[0],
                    "visual": parts[4],
                    "title": parts[7],
                    "views": views,
                }
            )
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


def _high_sample_title_lessons(rows: list[dict], min_views: int = 900) -> list[str]:
    high = [r for r in rows if (r.get("views") or 0) >= min_views][:8]
    lessons = []
    if not high:
        # fall back to top 3 by views in window even if < 900
        high = sorted([r for r in rows if r.get("views") is not None], key=lambda r: -r["views"])[:3]
        lessons.append("近窗尚無 ≥900 觀看樣本；下列僅作觀察、不作勝負依據。")
    for r in high:
        title = r.get("title") or ""
        bits = []
        if re.search(r"\d+(?:\.\d+)?億", title):
            bits.append("金額")
        if "？" in title or "?" in title:
            bits.append("問句")
        if re.search(r"\b\d{4}\b", title) or re.search(r"[\u4e00-\u9fff]{2,4}", title):
            bits.append("股名/標的")
        if "卻" in title or "竟" in title:
            bits.append("反差")
        lessons.append(
            f"- {r.get('date')} / {r.get('views')} views：{title[:56]}"
            + (f"（含：{'+'.join(bits)}）" if bits else "")
        )
    return lessons


def _banned_openers(channel_story: str) -> list[str]:
    titles = _parse_channel_story_titles(channel_story, limit=10)
    streak = analyze_title_opener_streaks(titles, lookback=8)
    bans = []
    if streak.get("block") and streak.get("trailing_label"):
        bans.append(
            f"BLOCK 開場「{streak['trailing_label']}」連續 {streak.get('trailing_streak')} 支："
            "標題前 20 字改金額+股名+矛盾；事件名可放描述中後段。"
        )
    elif streak.get("warn") and streak.get("trailing_label"):
        bans.append(
            f"WARN 開場「{streak['trailing_label']}」連續 {streak.get('trailing_streak')} 支："
            "今日盡量換鉤。"
        )
    fatigue = build_fatigue_state(channel_story=channel_story)
    for term in fatigue.get("fatigued_title_terms") or []:
        bans.append(f"高頻詞疲勞：少用「{term}」")
    return bans


def _hit_rate_watchlist(stock_db: dict, min_samples: int = HIT_RATE_MIN_SAMPLES) -> tuple[list[str], list[str]]:
    weak, strong = [], []
    for sid, entry in (stock_db or {}).items():
        tracking = (entry or {}).get("tracking") or {}
        n = int(tracking.get("prediction_count") or 0)
        if n < min_samples:
            continue
        rate = _hit_rate_fraction(tracking)
        if rate is None:
            continue
        name = (entry or {}).get("name") or sid
        label = f"{normalize_ticker(sid)} {name}：命中率 {rate:.0%}（n={n}）"
        if rate < 0.35:
            weak.append(label)
        elif rate >= 0.65:
            strong.append(label)
    weak.sort()
    strong.sort()
    return weak[:12], strong[:12]


def build_weekly_compounding_brief(
    *,
    log_path: str = DEFAULT_LOG,
    stock_db_path: str = DEFAULT_DB,
    recent_limit: int = 15,
) -> str:
    rows = _story_rows(log_path)[:recent_limit]
    channel_story = ""
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            channel_story = f.read()

    stock_db = load_stock_db(stock_db_path)
    weak, strong = _hit_rate_watchlist(stock_db)
    bans = _banned_openers(channel_story)
    copy_lessons = _high_sample_title_lessons(rows)

    views = [r["views"] for r in rows if r.get("views") is not None]
    med = sorted(views)[len(views) // 2] if views else None
    avg = sum(views) / len(views) if views else None

    lines = [
        "# 複利閉環簡報（自動）",
        "",
        f"- 產生時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 近窗影片：{len(rows)} 支"
        + (f"；中位觀看 {med}；平均 {avg:.0f}" if views else ""),
        f"- 命中率樣本門檻：n≥{HIT_RATE_MIN_SAMPLES} 才進 title_priority 折扣",
        "",
        "## 本週可複製（標題／選鉤）",
        "",
    ]
    lines.extend(copy_lessons or ["- （尚無可用觀看樣本）"])
    lines.extend(
        [
            "",
            "公式優先：`已驗證金額 + 核心股名 + 反直覺矛盾 + 問句`；≥900 views 才當勝負依據。",
            "",
            "## 本週禁止／降權",
            "",
        ]
    )
    if bans:
        lines.extend(f"- {b}" if not b.startswith("-") else b for b in bans)
    else:
        lines.append("- 開場未達連續疲勞門檻；仍避免無必要的事件名連打。")
    lines.extend(
        [
            "",
            "## 選標（title_priority 命中率閉環）",
            "",
            "### 歷史偏弱（n≥3 且命中率 <35% → 標題主角降權）",
            "",
        ]
    )
    lines.extend(f"- {w}" for w in weak) if weak else lines.append("- （無）")
    lines.extend(
        [
            "",
            "### 歷史偏強（n≥3 且命中率 ≥65% → 可微幅加權）",
            "",
        ]
    )
    lines.extend(f"- {s}" for s in strong) if strong else lines.append("- （無）")
    lines.extend(
        [
            "",
            "## /1 /2 怎麼用這份檔",
            "",
            "1. `/1`：`role_scorer` 已對 n≥3 的標的套用命中率乘數（低命中降 title_priority）。",
            "2. `/2` preflight：讀開場疲勞；BLOCK 日 local 高樣本模板優先於 Gemini 開場。",
            "3. 人工：標題 A 對齊「可複製」；勿用「禁止」開場 token。",
            "",
            "---",
            "由 `modules/compounding_loop.py` 產生；可手動重跑：",
            "`python3 cli.py maintenance compounding-brief`",
            "",
        ]
    )
    return "\n".join(lines)


def write_weekly_compounding_brief(
    *,
    log_path: str = DEFAULT_LOG,
    out_path: str = DEFAULT_BRIEF,
    stock_db_path: str = DEFAULT_DB,
) -> str:
    ensure_shared_dir(REPORTS_DIR)
    text = build_weekly_compounding_brief(
        log_path=log_path,
        stock_db_path=stock_db_path,
    )
    path = out_path or DEFAULT_BRIEF
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path
