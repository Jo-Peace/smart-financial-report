"""
YouTube Analytics Module
Fetches public video stats (views, likes, comments) using YouTube Data API v3.
Requires YOUTUBE_API_KEY in .env (get from Google Cloud Console > APIs & Services).

Note: CTR and impressions require OAuth 2.0 (YouTube Analytics API).
      This module uses the simpler Data API v3 for public stats only.
"""

import os
import re
import requests


YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


def _normalize_title(text: str) -> str:
    text = str(text or "").lower()
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[|｜!！?？,，.。:：;；「」『』【】\\[\\]()（）\\-—_~～\"'`]", "", text)
    return text


def _title_score(expected: str, actual: str) -> float:
    expected_norm = _normalize_title(expected)
    actual_norm = _normalize_title(actual)
    if not expected_norm or not actual_norm:
        return 0.0
    if expected_norm == actual_norm:
        return 1.0
    if expected_norm in actual_norm or actual_norm in expected_norm:
        return min(len(expected_norm), len(actual_norm)) / max(len(expected_norm), len(actual_norm))

    expected_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", expected.lower()))
    actual_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", actual.lower()))
    if not expected_tokens or not actual_tokens:
        return 0.0
    return len(expected_tokens & actual_tokens) / len(expected_tokens | actual_tokens)


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


def get_channel_recent_videos(channel_id: str, api_key: str, max_results: int = 25) -> list[dict]:
    """Fetch recent public uploads for a channel using YouTube Data API search.list."""
    if not channel_id or not api_key:
        return []

    try:
        search_resp = requests.get(
            f"{YOUTUBE_API_BASE}/search",
            params={
                "part": "snippet",
                "channelId": channel_id,
                "order": "date",
                "type": "video",
                "maxResults": min(max_results, 50),
                "key": api_key,
            },
            timeout=10,
        )
        search_resp.raise_for_status()
        items = search_resp.json().get("items", [])
        video_ids = [
            item.get("id", {}).get("videoId")
            for item in items
            if item.get("id", {}).get("videoId")
        ]
        if not video_ids:
            return []

        stats_resp = requests.get(
            f"{YOUTUBE_API_BASE}/videos",
            params={
                "part": "statistics,snippet",
                "id": ",".join(video_ids),
                "key": api_key,
            },
            timeout=10,
        )
        stats_resp.raise_for_status()
        videos = []
        for item in stats_resp.json().get("items", []):
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            videos.append(
                {
                    "video_id": item.get("id", ""),
                    "title": snippet.get("title", ""),
                    "published_at": snippet.get("publishedAt", ""),
                    "views": int(stats.get("viewCount", 0)),
                    "likes": int(stats.get("likeCount", 0)),
                    "comments": int(stats.get("commentCount", 0)),
                }
            )
        return videos
    except Exception as e:
        print(f"  [YouTube Analytics] 無法取得頻道近期影片: {e}")
        return []


def update_log_from_channel(
    log_path: str,
    api_key: str,
    channel_id: str,
    max_results: int = 25,
    min_title_score: float = 0.72,
) -> list[dict]:
    """
    Fetch recent channel videos, match rows by the saved title, fill video IDs,
    and update public view stats.
    """
    if not api_key or not channel_id or not os.path.exists(log_path):
        return []

    videos = get_channel_recent_videos(channel_id, api_key, max_results=max_results)
    if not videos:
        print("  [YouTube Analytics] 頻道沒有可配對的近期影片")
        return []

    videos_by_date = {}
    for video in videos:
        published_date = str(video.get("published_at", ""))[:10]
        if published_date:
            videos_by_date.setdefault(published_date, []).append(video)

    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    new_lines = []
    results = []
    updated_count = 0
    unmatched_titles = []

    for line in lines:
        if "|" not in line or "---" in line or "日期" in line:
            new_lines.append(line)
            continue

        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) < 9:
            new_lines.append(line)
            continue

        date_str = parts[0]
        video_id = parts[6].strip()
        saved_title = parts[7].strip()
        old_views = parts[8].strip()

        if video_id and video_id not in ("-", ""):
            new_lines.append(line)
            continue

        best_score = 0.0
        best_video = None
        match_reason = "title"
        if saved_title and saved_title not in ("-", "待更新"):
            ranked = sorted(
                ((_title_score(saved_title, video["title"]), video) for video in videos),
                key=lambda item: item[0],
                reverse=True,
            )
            best_score, best_video = ranked[0]

        if not best_video or best_score < min_title_score:
            same_day_videos = videos_by_date.get(date_str, [])
            if len(same_day_videos) == 1:
                best_video = same_day_videos[0]
                best_score = 1.0
                match_reason = "date"
            else:
                candidate_title = best_video["title"] if best_video else ""
                unmatched_titles.append((date_str, saved_title, best_score, candidate_title))
                new_lines.append(line)
                continue

        parts[6] = best_video["video_id"]
        parts[7] = best_video["title"]
        parts[8] = f"{best_video['views']:,}"
        new_lines.append("| " + " | ".join(parts) + " |\n")
        results.append(
            {
                "date": date_str,
                "video_id": best_video["video_id"],
                "title": best_video["title"],
                "old_views": old_views,
                "views": best_video["views"],
                "likes": best_video["likes"],
                "comments": best_video["comments"],
                "score": best_score,
            }
        )
        print(
            f"  [YouTube Analytics] 配對 {date_str}: {best_video['video_id']} "
            f"({match_reason}, {best_score:.2f}) {old_views} → {best_video['views']:,} 觀看"
        )
        updated_count += 1

    if updated_count > 0:
        with open(log_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        print(f"  [YouTube Analytics] 已回填/更新 {updated_count} 筆影片")
    else:
        print("  [YouTube Analytics] 沒有找到可自動回填的影片")

    for date_str, saved_title, score, candidate in unmatched_titles[:5]:
        print(
            f"  [YouTube Analytics] 未配對 {date_str}: score={score:.2f}, "
            f"候選《{candidate[:28]}》 / 紀錄《{saved_title[:28]}》"
        )
    return results


def update_log_with_stats(log_path: str, api_key: str, update_all: bool = True) -> list[dict]:
    """
    Scan channel_story_log.md for rows with a video_id, fetch public stats,
    and write the latest view count back.
    """
    if not api_key or not os.path.exists(log_path):
        return []

    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    new_lines = []
    updated_count = 0
    results = []

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

        should_update = update_all or views_field in ("-", "待更新")
        if video_id and video_id not in ("-", "") and should_update:
            stats = get_video_stats(video_id, api_key)
            if stats:
                old_views = views_field
                parts[8] = f"{stats['views']:,}"
                new_line = "| " + " | ".join(parts) + " |\n"
                new_lines.append(new_line)
                results.append(
                    {
                        "date": parts[0],
                        "video_id": video_id,
                        "title": stats["title"],
                        "old_views": old_views,
                        "views": stats["views"],
                        "likes": stats["likes"],
                        "comments": stats["comments"],
                    }
                )
                print(
                    f"  [YouTube Analytics] {parts[0]} 《{stats['title'][:20]}》: "
                    f"{old_views} → {stats['views']:,} 觀看"
                )
                updated_count += 1
                continue

        new_lines.append(line)

    if updated_count > 0:
        with open(log_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        print(f"  [YouTube Analytics] 已更新 {updated_count} 筆影片觀看數")
    else:
        print("  [YouTube Analytics] 無需更新（無影片ID或已有觀看數）")
    return results
