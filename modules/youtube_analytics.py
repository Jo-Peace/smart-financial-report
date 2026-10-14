"""
YouTube Analytics Module
Fetches public video stats (views, likes, comments) using YouTube Data API v3.
Requires YOUTUBE_API_KEY in .env (get from Google Cloud Console > APIs & Services).

Note: CTR and impressions require OAuth 2.0 (YouTube Analytics API).
      This module uses the simpler Data API v3 for public stats only.
"""

import datetime
import os
import re
import time

import requests


YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
VIDEOS_PER_REQUEST = 50
DEFAULT_TIME_BUDGET_S = 45.0
PLACEHOLDER_IDS = {"", "-"}


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


def _item_to_stats(item: dict) -> dict:
    snippet = item.get("snippet", {}) or {}
    stats = item.get("statistics", {}) or {}
    return {
        "video_id": item.get("id", ""),
        "title": snippet.get("title", ""),
        "published_at": snippet.get("publishedAt", ""),
        "views": int(stats.get("viewCount", 0) or 0),
        "likes": int(stats.get("likeCount", 0) or 0),
        "comments": int(stats.get("commentCount", 0) or 0),
    }


def get_video_stats(video_id: str, api_key: str) -> dict | None:
    """Fetch public stats for a single video (now also returns published_at). None on failure."""
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
        return _item_to_stats(items[0])
    except Exception as e:
        print(f"  [YouTube Analytics] 無法取得影片 {video_id} 數據: {e}")
        return None


def get_videos_stats_batch(video_ids, api_key: str, time_budget_s: float = DEFAULT_TIME_BUDGET_S) -> dict:
    """
    {video_id: stats} using videos.list with up to 50 comma-joined IDs per request.

    Stops when ``time_budget_s`` is spent (remaining IDs keep their old values) and never
    raises; a failed chunk is logged and skipped.
    """
    unique = []
    for vid in video_ids or []:
        vid = str(vid or "").strip()
        if vid and vid not in PLACEHOLDER_IDS and vid not in unique:
            unique.append(vid)
    deadline = time.monotonic() + max(1.0, float(time_budget_s))
    out = {}
    for start in range(0, len(unique), VIDEOS_PER_REQUEST):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print(f"  [YouTube Analytics] 已用完 {time_budget_s:.0f}s 時間預算，其餘 {len(unique) - start} 支沿用舊值")
            break
        chunk = unique[start:start + VIDEOS_PER_REQUEST]
        try:
            resp = requests.get(
                f"{YOUTUBE_API_BASE}/videos",
                params={"part": "statistics,snippet", "id": ",".join(chunk), "key": api_key},
                timeout=min(10.0, max(1.0, remaining)),
            )
            resp.raise_for_status()
            for item in resp.json().get("items", []) or []:
                stats = _item_to_stats(item)
                if stats["video_id"]:
                    out[stats["video_id"]] = stats
        except Exception as e:
            print(f"  [YouTube Analytics] 批次取得 {len(chunk)} 支影片失敗，沿用舊值: {e}")
    return out


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
        stats = get_videos_stats_batch(video_ids, api_key)
        return [stats[vid] for vid in video_ids if vid in stats]
    except Exception as e:
        print(f"  [YouTube Analytics] 無法取得頻道近期影片: {e}")
        return []


def _story_cells(line: str):
    if "|" not in line or "---" in line or "日期" in line:
        return None
    parts = [p.strip() for p in line.strip().strip("|").split("|")]
    if len(parts) < 9 or not line.startswith("| 20"):
        return None
    return parts


def _write_log(log_path: str, lines: list[str]) -> None:
    from modules.atomic_io import atomic_write_text

    atomic_write_text(log_path, "".join(lines))


def _safe_cell(value) -> str:
    text = str(value or "").replace("\n", " ").replace("|", "｜").strip()
    return text or "-"


def match_title_variant(run_date: str, actual_title: str, store=None, min_score: float = 0.72) -> str:
    """
    Compare the live YouTube title with the package's A/B variants for that date and mark
    the matching one as selected. Returns "a"/"b"/…, "manual" (no match), or "" (no data).
    """
    try:
        if store is None:
            from modules.analytics_store import AnalyticsStore

            store = AnalyticsStore()
        variants = store.get_youtube_title_variants(run_date)
    except Exception:
        return ""
    if not variants or not actual_title:
        return ""
    scored = sorted(((_title_score(v["title"], actual_title), v["variant"]) for v in variants), reverse=True)
    best_score, best_variant = scored[0]
    variant = best_variant if best_score >= min_score else None
    try:
        current = [v["variant"] for v in variants if v.get("selected")]
        if current != ([variant] if variant else []):
            store.set_selected_title_variant(run_date, variant)
    except Exception:
        pass
    return variant or "manual"


def _record_snapshots(snapshot_rows, db_path=None) -> int:
    if not snapshot_rows:
        return 0
    try:
        from modules.analytics_store import AnalyticsStore

        store = AnalyticsStore(db_path) if db_path else AnalyticsStore()
        for row in snapshot_rows:
            if row.get("run_date") and row.get("title") and not row.get("title_variant"):
                row["title_variant"] = match_title_variant(row["run_date"], row["title"], store=store)
        return store.record_youtube_metric_snapshots(snapshot_rows)
    except Exception as e:
        print(f"  [YouTube Analytics] 觀看快照寫入 analytics.db 失敗（不影響 log）: {e}")
        return 0


def update_log_from_channel(
    log_path: str,
    api_key: str,
    channel_id: str,
    max_results: int = 25,
    min_title_score: float = 0.72,
    record_snapshots: bool = True,
    db_path: str | None = None,
) -> list[dict]:
    """
    Fetch recent channel videos, match rows by the saved title, fill video IDs,
    and update public view stats.

    A video ID already present on another row is never assigned again (title match or
    date fallback), so one upload cannot be counted under two dates.
    """
    if not api_key or not channel_id or not os.path.exists(log_path):
        return []

    videos = get_channel_recent_videos(channel_id, api_key, max_results=max_results)
    if not videos:
        print("  [YouTube Analytics] 頻道沒有可配對的近期影片")
        return []

    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    used_ids = set()
    for line in lines:
        parts = _story_cells(line)
        if parts and parts[6] not in PLACEHOLDER_IDS:
            used_ids.add(parts[6])

    new_lines = []
    results = []
    updated_count = 0
    unmatched_titles = []

    for line in lines:
        parts = _story_cells(line)
        if parts is None:
            new_lines.append(line)
            continue

        date_str = parts[0]
        video_id = parts[6].strip()
        saved_title = parts[7].strip()
        old_views = parts[8].strip()

        if video_id and video_id not in PLACEHOLDER_IDS:
            new_lines.append(line)
            continue

        available = [video for video in videos if video["video_id"] not in used_ids]
        best_score = 0.0
        best_video = None
        match_reason = "title"
        if saved_title and saved_title not in ("-", "待更新") and available:
            ranked = sorted(
                ((_title_score(saved_title, video["title"]), video) for video in available),
                key=lambda item: item[0],
                reverse=True,
            )
            best_score, best_video = ranked[0]

        if not best_video or best_score < min_title_score:
            same_day_videos = [v for v in available if str(v.get("published_at", ""))[:10] == date_str]
            if len(same_day_videos) == 1:
                best_video = same_day_videos[0]
                best_score = 1.0
                match_reason = "date"
            else:
                candidate_title = best_video["title"] if best_video else ""
                unmatched_titles.append((date_str, saved_title, best_score, candidate_title))
                new_lines.append(line)
                continue

        used_ids.add(best_video["video_id"])
        parts[6] = best_video["video_id"]
        parts[7] = _safe_cell(best_video["title"])
        parts[8] = f"{best_video['views']:,}"
        new_lines.append("| " + " | ".join(parts) + " |\n")
        results.append(
            {
                "date": date_str,
                "run_date": date_str,
                "video_id": best_video["video_id"],
                "title": best_video["title"],
                "published_at": best_video.get("published_at", ""),
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
        _write_log(log_path, new_lines)
        print(f"  [YouTube Analytics] 已回填/更新 {updated_count} 筆影片")
    else:
        print("  [YouTube Analytics] 沒有找到可自動回填的影片")
    if record_snapshots:
        _record_snapshots(results, db_path=db_path)

    for date_str, saved_title, score, candidate in unmatched_titles[:5]:
        print(
            f"  [YouTube Analytics] 未配對 {date_str}: score={score:.2f}, "
            f"候選《{candidate[:28]}》 / 紀錄《{saved_title[:28]}》"
        )
    return results


def update_log_with_stats(
    log_path: str,
    api_key: str,
    update_all: bool = True,
    time_budget_s: float = DEFAULT_TIME_BUDGET_S,
    record_snapshots: bool = True,
    db_path: str | None = None,
) -> list[dict]:
    """
    Refresh view counts for every log row that has a video_id.

    - IDs are de-duplicated and fetched 50 per videos.list call within ``time_budget_s``
      (slow API → remaining rows keep their previous values)
    - the live YouTube title is written back to 使用標題 when it changed (title swaps / B tests)
    - each refresh is also stored as a snapshot (views + publishedAt + hours since publish)
      in analytics.db so learning can compare videos at the same age
    """
    if not api_key or not os.path.exists(log_path):
        return []

    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    wanted = []
    for line in lines:
        parts = _story_cells(line)
        if not parts or parts[6] in PLACEHOLDER_IDS:
            continue
        if update_all or parts[8] in ("-", "待更新"):
            wanted.append(parts[6])
    if not wanted:
        print("  [YouTube Analytics] 無需更新（無影片ID或已有觀看數）")
        return []

    stats_by_id = get_videos_stats_batch(wanted, api_key, time_budget_s=time_budget_s)

    new_lines = []
    updated_count = 0
    results = []
    snapshot_ids = set()
    for line in lines:
        parts = _story_cells(line)
        stats = stats_by_id.get(parts[6]) if parts else None
        if not stats or not (update_all or parts[8] in ("-", "待更新")):
            new_lines.append(line)
            continue
        old_views = parts[8]
        parts[8] = f"{stats['views']:,}"
        if stats.get("title") and _safe_cell(stats["title"]) != parts[7]:
            parts[7] = _safe_cell(stats["title"])
        new_line = "| " + " | ".join(parts) + " |\n"
        if new_line != line:
            updated_count += 1
        new_lines.append(new_line)
        if parts[6] in snapshot_ids:
            continue
        snapshot_ids.add(parts[6])
        results.append(
            {
                "date": parts[0],
                "run_date": parts[0],
                "video_id": parts[6],
                "title": stats["title"],
                "published_at": stats.get("published_at", ""),
                "old_views": old_views,
                "views": stats["views"],
                "likes": stats["likes"],
                "comments": stats["comments"],
            }
        )

    if updated_count > 0:
        _write_log(log_path, new_lines)
        print(f"  [YouTube Analytics] 已更新 {updated_count} 列影片觀看數（{len(stats_by_id)} 支影片、{-(-len(set(wanted)) // VIDEOS_PER_REQUEST)} 次批次請求）")
    else:
        print("  [YouTube Analytics] 觀看數無變化")
    if record_snapshots:
        stored = _record_snapshots(results, db_path=db_path)
        if stored:
            print(f"  [YouTube Analytics] 已寫入 {stored} 筆觀看快照（含上架時長）")
    return results
