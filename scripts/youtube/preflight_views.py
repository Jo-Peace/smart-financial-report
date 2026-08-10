#!/usr/bin/env python3
"""
/2 前置：回顧近期觀看數，輸出可執行的包裝參考。

用途：
  在決定視覺主題、標題主鉤、跑 regen-assets 之前先跑。
  低於 900 views 只作觀察，不當作 A/B 勝負依據。

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


def _parse_log(log_path: Path):
    rows = []
    if not log_path.exists():
        return rows
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| 20"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 9:
            continue
        date, core, pred, val, visual, theme, vid, title, views = cells[:9]
        if views in {"待更新", "-", "", "N/A"}:
            continue
        try:
            view_n = int(str(views).replace(",", "").strip())
        except ValueError:
            continue
        rows.append(
            {
                "date": date,
                "views": view_n,
                "visual": visual,
                "title": title,
                "video_id": vid,
                "core": core,
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


def print_report(rows, recent_n: int = 15) -> dict:
    if not rows:
        print("❌ channel_story_log 沒有可用觀看數列。")
        return {}

    recent = rows[:recent_n]
    vals = [r["views"] for r in recent]
    med = statistics.median(vals)
    mean = sum(vals) / len(vals)

    print("\n" + "=" * 60)
    print("  /2 前置：近期觀看數回顧")
    print("=" * 60)
    print(f"  樣本：最近 {len(recent)} 支有觀看數的影片")
    print(f"  中位數：{med:.0f}　平均：{mean:.1f}　最高：{max(vals)}　最低：{min(vals)}")
    print("  規則：views < 900 只作觀察，不作 A/B 勝負依據\n")

    print("  ## 最近清單（新→舊）")
    for r in recent:
        tag = "觀" if r["views"] < 900 else "可判"
        visual = (r["visual"] or "")[:28]
        title = (r["title"] or "")[:42]
        print(f"  [{tag}] {r['date']}  {r['views']:>5}  {visual:<28}  {title}")

    print("\n  ## 近窗高樣本（最多 5）")
    for r in sorted(recent, key=lambda x: -x["views"])[:5]:
        print(f"  HIGH {r['views']:>5}  {r['date']}  {(r['visual'] or '')[:36]}")
        print(f"       {(r['title'] or '')[:70]}")

    print("\n  ## 近窗低樣本（最多 5）")
    for r in sorted(recent, key=lambda x: x["views"])[:5]:
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

    high = sorted(recent, key=lambda x: -x["views"])[0]
    low = sorted(recent, key=lambda x: x["views"])[0]
    avoid_visuals = [r["visual"] for r in recent[:3] if r.get("visual")]

    # Title opener fatigue (AMD / 驗收 continuous openers)
    print()
    try:
        from modules.packaging_fatigue import format_opener_streak_for_preflight

        for line in format_opener_streak_for_preflight(log_path=str(DEFAULT_LOG)):
            print(line)
    except Exception as exc:
        print(f"  ## 標題開場疲勞\n  - 檢查失敗：{exc}")

    print("\n  ## /2 決策摘要（請寫進今日包裝）")
    print(f"  - 可複製高樣本：{high['date']} / {high['views']} views / {(high['visual'] or '')[:40]}")
    print(f"    標題感：{(high['title'] or '')[:60]}")
    print(f"  - 今日避雷低樣本：{low['date']} / {low['views']} views / {(low['visual'] or '')[:40]}")
    print(f"    標題感：{(low['title'] or '')[:60]}")
    print(f"  - 近 3 支視覺已用：{' ｜ '.join((v or '')[:24] for v in avoid_visuals)}")
    print("  - 下一步：定 1 個標題主鉤 + 1 個差異化視覺 → 再跑 regen-assets")
    print("  - 若開場疲勞 BLOCK：禁止同一 token 開場；前 20 字改金額＋股名＋矛盾")
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
    print_report(rows, recent_n=args.recent)
    if DEFAULT_WIKI.exists():
        print(f"📄 完整成效摘要：{DEFAULT_WIKI.relative_to(BASE_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
