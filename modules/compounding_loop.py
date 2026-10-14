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
from modules.role_scorer import (
    HIT_RATE_MIN_SAMPLES,
    HIT_RATE_MULT_MAX,
    HIT_RATE_MULT_MIN,
    _hit_rate_fraction,
    load_stock_db,
    normalize_ticker,
)
from modules.report_paths import ensure_shared_dir, shared_dir

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
DEFAULT_LOG = os.path.join(REPORTS_DIR, "shared", "channel_story_log.md")
DEFAULT_BRIEF = os.path.join(REPORTS_DIR, "shared", "weekly_compounding_brief.md")
DEFAULT_DB = os.path.join(BASE_DIR, "data", "stock_db.json")


def _learning_window(log_path: str, recent_limit: int):
    """Shared same-age / offsite-excluded learning window (same basis as youtube_performance_patterns)."""
    from modules.youtube_learning import learning_window

    try:
        return learning_window(log_path, recent_limit=recent_limit)
    except Exception:
        return None


def _load_active_bans(bans_path: str | None, window):
    """Packaging-ban registry with today's status (expiry / exemptions); None when unavailable."""
    try:
        from modules.packaging_bans import DEFAULT_BANS_PATH, load_bans, refresh_status

        return refresh_status(load_bans(bans_path or DEFAULT_BANS_PATH), window=window)
    except Exception:
        return None


def _high_sample_title_lessons(window, min_views=None, top_n: int = 8, bans_state=None) -> list[str]:
    """
    High samples from the comparable sample (views@48h when enough same-age snapshots
    exist, else mature cumulative views; videos < 72h and 站外-seeded videos excluded),
    thresholded at 2× that sample's median — never cumulative views across ages.

    A title that breaks an *active* packaging ban (data/packaging_bans.json, e.g. a
    「被砍」 structure or an 「AMD開場」 opener) is never offered as a positive
    example, however many views it had; the skipped count is printed.
    """
    sample = (window or {}).get("sample") or []
    if not sample:
        return ["- （尚無可比較的成熟影片樣本；不輸出可複製公式）"]
    baseline = window.get("baseline") or {}
    threshold = min_views if min_views is not None else baseline.get("win_threshold")
    ranked = sorted(sample, key=lambda r: -r["metric"])

    def _pick(limit, eligible):
        """First ``limit`` eligible rows that break no active ban, plus the banned ones passed over."""
        picked, skipped = [], []
        for r in ranked:
            if len(picked) >= limit:
                break
            if not eligible(r):
                continue
            if bans_state:
                from modules.packaging_bans import title_ban_hits

                hits = title_ban_hits(r.get("title"), bans_state)
                if hits:
                    skipped.append(hits[0].get("pattern"))
                    continue
            picked.append(r)
        return picked, skipped

    high, skipped = _pick(top_n, lambda r: threshold is not None and r["metric"] >= threshold)
    lessons = [f"- 基準：{window.get('basis')}；勝出門檻 ≥ 中位數×2 = {threshold:.0f}"]
    if not high:
        high, skipped = _pick(3, lambda r: True)
        lessons.append(f"- 近窗尚無 ≥{threshold:.0f}（中位數×2）且未違反禁令的樣本；下列僅作觀察、不作勝負依據。")
    from modules.youtube_learning import strip_brand

    for r in high:
        lessons.append(f"- {r.get('date')} / {int(round(r['metric'])):,}：{strip_brand(r.get('title'))[:56]}")
    if skipped:
        patterns = "、".join(sorted({str(p) for p in skipped if p})[:4])
        lessons.append(f"- 已略過 {len(skipped)} 支違反有效包裝禁令的標題（{patterns}；data/packaging_bans.json），不列為可複製範例。")
    return lessons


def _lift_lines(window) -> list[str]:
    """Features that separate the high and low groups (not keywords merely present in the top)."""
    from modules.youtube_learning import lift_lessons, load_stock_names

    sample = (window or {}).get("sample") or []
    try:
        lines, _ = lift_lessons(sample, load_stock_names())
    except Exception:
        lines = []
    return lines or ["- （樣本不足，不輸出標題特徵結論）"]


def _banned_openers(channel_story: str) -> list[str]:
    titles = _parse_channel_story_titles(channel_story, limit=10)
    streak = analyze_title_opener_streaks(titles, lookback=8)
    bans = []
    if streak.get("block") and streak.get("trailing_label"):
        bans.append(
            f"BLOCK 開場「{streak['trailing_label']}」連續 {streak.get('trailing_streak')} 支："
            "標題前 20 字改金額+股名+矛盾；事件名可放描述中後段。"
        )
    elif streak.get("warn") and streak.get("trailing_label") == "股名金額開場":
        bans.append(f"WARN {streak.get('advice')}")  # skeleton streak: rotate B's structure, keep A
    elif streak.get("warn") and streak.get("trailing_label"):
        bans.append(
            f"WARN 開場「{streak['trailing_label']}」連續 {streak.get('trailing_streak')} 支："
            "今日盡量換鉤。"
        )
    fatigue = build_fatigue_state(channel_story=channel_story)
    for term in fatigue.get("fatigued_title_terms") or []:
        bans.append(f"高頻詞疲勞：少用「{term}」")
    return bans


# Watchlist bands for the weekly brief (the title multiplier itself is continuous).
WEAK_HIT_RATE = 0.35
STRONG_HIT_RATE = 0.65


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
        if rate < WEAK_HIT_RATE:
            weak.append(label)
        elif rate >= STRONG_HIT_RATE:
            strong.append(label)
    weak.sort()
    strong.sort()
    return weak[:12], strong[:12]


def build_weekly_compounding_brief(
    *,
    log_path: str = DEFAULT_LOG,
    stock_db_path: str = DEFAULT_DB,
    recent_limit: int = 30,
    bans_path: str | None = None,
) -> str:
    window = _learning_window(log_path, recent_limit)
    channel_story = ""
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            channel_story = f.read()

    stock_db = load_stock_db(stock_db_path)
    weak, strong = _hit_rate_watchlist(stock_db)
    bans = _banned_openers(channel_story)
    copy_lessons = _high_sample_title_lessons(window, bans_state=_load_active_bans(bans_path, window))
    lift_lines = _lift_lines(window)
    baseline = (window or {}).get("baseline") or {}
    sample_n = len((window or {}).get("sample") or [])

    horizon_note = ""
    try:
        from modules.analytics_store import AnalyticsStore
        from modules.horizon_reliability import format_reliability_brief, reliability_one_liner

        horizon_rows = AnalyticsStore().get_horizon_performance_summary()
        horizon_note = reliability_one_liner(horizon_rows)
        horizon_brief = format_reliability_brief(horizon_rows)
    except Exception:
        horizon_rows = []
        horizon_brief = "- （尚無時間窗回測）"

    lines = [
        "# 複利閉環簡報（自動）",
        "",
        f"- 產生時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 近窗影片：{len((window or {}).get('rows') or [])} 支（可比較 {sample_n} 支）"
        + (f"；中位 {baseline['median']:.0f}（{window.get('basis')}）" if baseline.get("median") is not None else ""),
        f"- 命中率樣本門檻：n≥{HIT_RATE_MIN_SAMPLES} 才進 title_priority 折扣",
    ]
    if horizon_note:
        lines.append(f"- 時間窗：{horizon_note}")
    lines.extend(
        [
            "",
            "## 時間窗可信度（本機回測）",
            "",
        ]
    )
    lines.extend(horizon_brief.splitlines() if horizon_brief else ["- （尚無）"])
    lines.extend(
        [
            "",
            "## 本週可複製（標題／選鉤）",
            "",
        ]
    )
    lines.extend(copy_lessons)
    lines.extend(["", "## 標題特徵對照（高觀看組 vs 低觀看組，本機計算）", ""])
    lines.extend(lift_lines)
    lines.extend(
        [
            "",
            "只有上方對照有差異的特徵才可當方向；≥ 頻道中位數×2 才當勝負依據（見 youtube_performance_patterns）。",
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
            f"### 歷史偏弱（stock_db 1D 驗收；n≥{HIT_RATE_MIN_SAMPLES} 且命中率 <{WEAK_HIT_RATE:.0%} → 標題主角降權，"
            f"最低 ×{HIT_RATE_MULT_MIN:.2f}）",
            "",
        ]
    )
    lines.extend(f"- {w}" for w in weak) if weak else lines.append("- （無）")
    lines.extend(
        [
            "",
            f"### 歷史偏強（stock_db 1D 驗收；n≥{HIT_RATE_MIN_SAMPLES} 且命中率 ≥{STRONG_HIT_RATE:.0%} → 最多 ×{HIT_RATE_MULT_MAX:.2f} 微幅加權）",
            "",
        ]
    )
    lines.extend(f"- {s}" for s in strong) if strong else lines.append("- （無）")
    lines.extend(
        [
            "",
            "## /1 /2 怎麼用這份檔",
            "",
            f"1. `/1`：`role_scorer` 已對 n≥{HIT_RATE_MIN_SAMPLES} 的標的套用命中率乘數"
            f"（{HIT_RATE_MULT_MIN:.2f}–{HIT_RATE_MULT_MAX:.2f}，低命中降 title_priority）。",
            "2. `/2` preflight：讀開場疲勞；BLOCK 日 local 高樣本模板優先於 Gemini 開場。",
            "3. 時間窗：只有本機回測（交易日窗、扣同窗大盤）某窗的 95% 信賴區間下界高於 50% 基準，"
            "才可說該窗較準；否則並列 1D/3D/5D/20D，勿把任一窗當成敗尺子。",
            "4. 人工：標題 A 對齊「可複製」；勿用「禁止」開場 token。",
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
