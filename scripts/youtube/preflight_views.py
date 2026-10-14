#!/usr/bin/env python3
"""
/2 前置：回顧近期觀看數，輸出可執行的包裝參考。

用途：
  在決定視覺主題、標題主鉤、跑 regen-assets 之前先跑。
  未達頻道中位數×2（同齡）只作觀察，不當作 A/B 勝負依據；上架未滿 72 小時不列低樣本。

執行：
  cd smart_financial_report
  python3 scripts/youtube/preflight_views.py
  python3 scripts/youtube/preflight_views.py --refresh --max-results 30
"""

from __future__ import annotations

import argparse
import os
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv

load_dotenv(BASE_DIR / ".env")

REPORTS_DIR = BASE_DIR / "reports"
DEFAULT_LOG = REPORTS_DIR / "shared" / "channel_story_log.md"
DEFAULT_WIKI = BASE_DIR / "data" / "knowledge_wiki" / "youtube_performance_patterns.md"
DEFAULT_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID", "UCRWXWHHOs4iSDac1GHLsExw")


def _parse_log(log_path: Path, recent_limit: int = 30):
    """De-duplicated rows (one per video) with age / maturity, newest first."""
    if not log_path.exists():
        return []
    from modules.youtube_learning import learning_window

    window = learning_window(str(log_path), recent_limit=recent_limit)
    rows = []
    for r in window["rows"]:
        if r.get("views") is None:
            continue
        rows.append(
            {
                "date": r["date"],
                "views": r["views"],
                "visual": r.get("visual_style", ""),
                "title": r.get("title", ""),
                "video_id": r.get("video_id", ""),
                "core": r.get("target", ""),
                "mature": r.get("mature", True),
                "age_hours": r.get("age_hours"),
                "offsite": r.get("offsite", ""),
                "baseline": window["baseline"],
            }
        )
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


def _keyword_hits(title: str):
    keys = ["AMD", "驗收", "影子", "狂砍", "鴻海", "緯創", "外資", "大盤", "崩", "漲停"]
    return [k for k in keys if k in (title or "")]


def refresh_channel(log_path: Path, channel_id: str, max_results: int) -> None:
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        print("⚠️  未設定 YOUTUBE_API_KEY，略過頻道回填，只讀現有 log。")
        return
    from modules.youtube_analytics import update_log_from_channel, update_log_with_stats
    from modules.youtube_learning import update_youtube_performance_wiki

    print(f"📺 回填頻道影片與觀看數（max={max_results}）...")
    if channel_id:
        update_log_from_channel(
            log_path=str(log_path),
            api_key=api_key,
            channel_id=channel_id,
            max_results=max_results,
        )
    update_log_with_stats(str(log_path), api_key, update_all=True)
    try:
        update_youtube_performance_wiki(str(log_path), str(DEFAULT_WIKI))
        print(f"✅ 已更新成效摘要：{DEFAULT_WIKI.relative_to(BASE_DIR)}")
    except Exception as exc:
        print(f"⚠️  成效摘要更新失敗：{exc}")


def print_report(rows, recent_n: int = 15, log_path: Path = DEFAULT_LOG) -> dict:
    if not rows:
        print("❌ channel_story_log 沒有可用觀看數列。")
        return {}

    recent = rows[:recent_n]
    vals = [r["views"] for r in recent]
    med = statistics.median(vals)
    mean = sum(vals) / len(vals)
    mature = [r for r in recent if r.get("mature") and not r.get("offsite")] or recent
    mature_med = statistics.median([r["views"] for r in mature])
    win_threshold = 2 * mature_med

    print("\n" + "=" * 60)
    print("  /2 前置：近期觀看數回顧")
    print("=" * 60)
    print(f"  樣本：最近 {len(recent)} 支有觀看數的影片")
    print(f"  中位數：{med:.0f}　平均：{mean:.1f}　最高：{max(vals)}　最低：{min(vals)}")
    print(f"  規則：未達中位數×2（≈{win_threshold:.0f} views）只作觀察；上架 <72h 標「新」，不列低樣本\n")

    print("  ## 最近清單（新→舊）")
    for r in recent:
        tag = "新" if not r.get("mature") else ("觀" if r["views"] < win_threshold else "可判")
        visual = (r["visual"] or "")[:28]
        title = (r["title"] or "")[:42]
        print(f"  [{tag}] {r['date']}  {r['views']:>5}  {visual:<28}  {title}")

    print("\n  ## 近窗高樣本（最多 5）")
    for r in sorted(recent, key=lambda x: -x["views"])[:5]:
        print(f"  HIGH {r['views']:>5}  {r['date']}  {(r['visual'] or '')[:36]}")
        print(f"       {(r['title'] or '')[:70]}")

    print("\n  ## 近窗低樣本（最多 5；僅上架 ≥72 小時）")
    for r in sorted(mature, key=lambda x: x["views"])[:5]:
        print(f"  LOW  {r['views']:>5}  {r['date']}  {(r['visual'] or '')[:36]}")
        print(f"       {(r['title'] or '')[:70]}")

    # keyword rough averages on recent window
    print("\n  ## 近窗標題關鍵詞均視（粗估）")
    for kw in ["AMD", "驗收", "影子", "狂砍", "鴻海", "緯創", "外資"]:
        subset = [r for r in recent if kw in (r["title"] or "")]
        if subset:
            avg = sum(r["views"] for r in subset) / len(subset)
            print(f"  {kw:6} n={len(subset):2} avg={avg:6.1f}")

    # recent visuals to avoid pure repeat
    print("\n  ## 近 5 支視覺（避免連三日同構圖）")
    for r in recent[:5]:
        print(f"  {r['date']}  {r['views']:>5}  {(r['visual'] or '')[:50]}")

    high = sorted(mature, key=lambda x: -x["views"])[0]
    low = sorted(mature, key=lambda x: x["views"])[0]
    avoid_visuals = [r["visual"] for r in recent[:3] if r.get("visual")]

    # Title opener fatigue (AMD / 驗收 continuous openers)
    print()
    try:
        from modules.packaging_fatigue import format_opener_streak_for_preflight

        for line in format_opener_streak_for_preflight(log_path=str(log_path)):
            print(line)
    except Exception as exc:
        print(f"  ## 標題開場疲勞\n  - 檢查失敗：{exc}")

    # Structured packaging bans (TTL / caps / top-quartile exemptions) + recently used scenes
    print()
    try:
        from modules.packaging_bans import (
            format_bans_for_title_prompt,
            load_bans,
            recent_visual_objects,
            recent_visual_rules,
            refresh_status,
        )
        from modules.youtube_learning import learning_window

        state = refresh_status(load_bans(), window=learning_window(str(log_path)))
        for line in format_bans_for_title_prompt(state).splitlines():
            print(f"  {line}")
        scenes = recent_visual_objects(state)
        if scenes:
            print(f"  - 近期已用場景（選新主題時避開，不會進生圖 prompt）：{'／'.join(scenes)}")
        visual_rules = recent_visual_rules(state)
        if visual_rules:
            print(f"  - 畫面禁忌（人工參考；封面 prompt 另有固定 IP／Logo 防護）：{'；'.join(visual_rules)}")
    except Exception as exc:
        print(f"  ## 包裝禁令\n  - 讀取失敗：{exc}")

    # Closed-loop brief for humans + /2
    print()
    try:
        from modules.compounding_loop import write_weekly_compounding_brief, DEFAULT_BRIEF

        brief_path = write_weekly_compounding_brief(log_path=str(DEFAULT_LOG))
        print(f"  ## 複利閉環簡報")
        print(f"  - 已更新：{Path(brief_path).relative_to(BASE_DIR)}")
        # Print first actionable section heads
        text = Path(brief_path).read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("## ") or line.startswith("- BLOCK") or line.startswith("- WARN"):
                print(f"  {line}")
    except Exception as exc:
        print(f"  ## 複利閉環簡報\n  - 更新失敗：{exc}")

    print("\n  ## /2 決策摘要（請寫進今日包裝）")
    print(f"  - 可複製高樣本：{high['date']} / {high['views']} views / {(high['visual'] or '')[:40]}")
    print(f"    標題感：{(high['title'] or '')[:60]}")
    print(f"  - 今日避雷低樣本：{low['date']} / {low['views']} views / {(low['visual'] or '')[:40]}")
    print(f"    標題感：{(low['title'] or '')[:60]}")
    print(f"  - 近 3 支視覺已用：{' ｜ '.join((v or '')[:24] for v in avoid_visuals)}")
    print("  - 下一步：定 1 個標題主鉤 + 1 個差異化視覺 → 再跑 regen-assets")
    print("  - --visual-theme 只寫正向場景；「禁止…」子句會自動拆進 data/packaging_bans.json（5 交易日到期）")
    print("  - 若開場疲勞 BLOCK：禁止同一 token 開場；local 高樣本模板優先")
    try:
        from modules.role_scorer import HIT_RATE_MIN_SAMPLES, HIT_RATE_MULT_MAX, HIT_RATE_MULT_MIN

        print(
            f"  - title_priority：n≥{HIT_RATE_MIN_SAMPLES} 命中率會自動乘上 "
            f"{HIT_RATE_MULT_MIN:.2f}–{HIT_RATE_MULT_MAX:.2f} 主角權重（見 role_scorer）"
        )
    except Exception:
        print("  - title_priority：命中率樣本足夠時會自動加減主角權重（見 role_scorer）")
    try:
        from modules.analytics_store import AnalyticsStore
        from modules.horizon_reliability import reliability_one_liner

        note = reliability_one_liner(AnalyticsStore().get_horizon_performance_summary())
        if note:
            print(f"  - 時間窗：{note}")
    except Exception:
        pass
    print("=" * 60 + "\n")

    return {
        "median": med,
        "mean": mean,
        "high": high,
        "low": low,
        "avoid_visuals": avoid_visuals,
        "recent": recent,
    }


def main():
    parser = argparse.ArgumentParser(description="/2 前置：近期觀看數回顧")
    parser.add_argument("--refresh", action="store_true", help="先從頻道 API 回填觀看數")
    parser.add_argument("--max-results", type=int, default=30, help="回填時抓取近期影片數")
    parser.add_argument("--recent", type=int, default=15, help="回顧最近幾支")
    parser.add_argument("--log-path", default=str(DEFAULT_LOG))
    parser.add_argument("--channel-id", default=DEFAULT_CHANNEL_ID)
    args = parser.parse_args()

    log_path = Path(args.log_path)
    if args.refresh:
        refresh_channel(log_path, args.channel_id, args.max_results)

    rows = _parse_log(log_path)
    print_report(rows, recent_n=args.recent, log_path=log_path)
    if DEFAULT_WIKI.exists():
        print(f"📄 完整成效摘要：{DEFAULT_WIKI.relative_to(BASE_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
