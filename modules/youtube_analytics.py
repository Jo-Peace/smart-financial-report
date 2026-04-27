"""
YouTube Analytics Module
Fetches public video stats (views, likes, comments) using YouTube Data API v3.
Requires YOUTUBE_API_KEY in .env (get from Google Cloud Console > APIs & Services).

Note: CTR and impressions require OAuth 2.0 (YouTube Analytics API).
      This module uses the simpler Data API v3 for public stats only.
"""

import os
import requests


YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


def get_video_stats(video_id: str, api_key: str) -> dict | None:
    """Fetch public stats for a single video. Returns None on failure."""
    try:
        resp = requests.get(
            f"{YOUTUBE_API_BASE}/videos",
            params={"part": "statistics,snippet", "id": video_id, "key": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            return None
        stats = items[0]["statistics"]
        snippet = items[0]["snippet"]
        return {
            "title": snippet.get("title", ""),
            "views": int(stats.get("viewCount", 0)),
            "likes": int(stats.get("likeCount", 0)),
            "comments": int(stats.get("commentCount", 0)),
        }
    except Exception as e:
        print(f"  [YouTube Analytics] 無法取得影片 {video_id} 數據: {e}")
        return None


def update_log_with_stats(log_path: str, api_key: str) -> None:
    """
    Scan channel_story_log.md for rows with a video_id but no views yet,
    fetch stats and write them back.
    """
    if not api_key or not os.path.exists(log_path):
        return

    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    new_lines = []
    updated_count = 0

    for line in lines:
        # Skip header, separator, and non-table lines
        if "|" not in line or "---" in line or "日期" in line:
            new_lines.append(line)
            continue

        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        # Expect 9 columns: 日期/核心標的/預測方向/隔日驗收/視覺風格/標題方向/影片ID/使用標題/觀看數
        if len(parts) < 9:
            new_lines.append(line)
            continue

        video_id = parts[6].strip()
        views_field = parts[8].strip()

        if video_id and video_id not in ("-", "") and views_field in ("-", "待更新"):
            stats = get_video_stats(video_id, api_key)
            if stats:
                parts[8] = f"{stats['views']:,}"
                new_line = "| " + " | ".join(parts) + " |\n"
                new_lines.append(new_line)
                print(f"  [YouTube Analytics] {parts[0]} 《{stats['title'][:20]}》: {stats['views']:,} 觀看")
                updated_count += 1
                continue

        new_lines.append(line)

    if updated_count > 0:
        with open(log_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        print(f"  [YouTube Analytics] 已更新 {updated_count} 筆影片觀看數")
    else:
        print("  [YouTube Analytics] 無需更新（無影片ID或已有觀看數）")
