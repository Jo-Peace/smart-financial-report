import datetime
import json
import os

from modules.editorial_frame import build_title_fatigue_report
from modules.report_paths import (
    ensure_shared_dir,
    iter_daily_report_paths,
    iter_structured_data_paths,
)
from modules.report_paths import _date_from_structured_path  # path-date helper


CHANNEL_STORY_HEADER = (
    "# 頻道敘事紀錄 (Channel Story Log)\n\n"
    "| 日期 | 核心標的 | 預測方向 | 隔日驗收 | 視覺風格 | 標題方向 | 影片ID | 使用標題 | 觀看數 |\n"
    "|------|---------|---------|---------|---------|---------|--------|---------|--------|\n"
)


def _safe_table_cell(value):
    text = str(value or "").replace("\n", " ").replace("|", "｜").strip()
    return text if text else "-"


def ensure_channel_story_log(channel_story_log, youtube_assets_dir=""):
    """Create or repair the channel story log table header."""
    if channel_story_log:
        parent = os.path.dirname(channel_story_log)
        if os.path.basename(parent) == "shared":
            ensure_shared_dir(os.path.dirname(parent))
        else:
            os.makedirs(parent, exist_ok=True)
    elif youtube_assets_dir:
        os.makedirs(youtube_assets_dir, exist_ok=True)

    if not channel_story_log:
        return False

    if not os.path.exists(channel_story_log):
        with open(channel_story_log, "w", encoding="utf-8") as f:
            f.write(CHANNEL_STORY_HEADER)
        return True

    try:
        with open(channel_story_log, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return False

    required_header = "| 日期 | 核心標的 | 預測方向 | 隔日驗收 | 視覺風格 | 標題方向 | 影片ID | 使用標題 | 觀看數 |"
    if required_header in content:
        return True

    backup_path = f"{channel_story_log}.bak"
    try:
        if not os.path.exists(backup_path):
            with open(backup_path, "w", encoding="utf-8") as f:
                f.write(content)
        with open(channel_story_log, "w", encoding="utf-8") as f:
            f.write(CHANNEL_STORY_HEADER)
            if content.strip():
                f.write("\n<!-- Recovered previous content below. Original also saved as .bak. -->\n\n")
                f.write(content.rstrip() + "\n")
        return True
    except Exception:
        return False


def find_previous_report(reports_dir):
    """Find the most recent report file for historical comparison."""
    if not os.path.exists(reports_dir):
        return None

    reports = iter_daily_report_paths(reports_dir)
    if reports:
        latest = reports[-1]
        print(f"  找到前日報告: {os.path.basename(latest)}")
        return latest
    return None


def _parse_iso_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("/", "-")
    # Accept YYYYMMDD from filenames.
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    try:
        return datetime.datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def count_trading_days_between(start_date, end_date):
    """
    Count Mon–Fri sessions strictly after start_date up to and including end_date.

    gap == 1 means previous trading day (or Friday → Monday across weekend).
    TWSE holidays in data/twse_closed_days.json are excluded.
    """
    if start_date is None or end_date is None:
        return None
    if end_date <= start_date:
        return 0
    from modules.twse_calendar import is_twse_session

    count = 0
    cursor = start_date + datetime.timedelta(days=1)
    while cursor <= end_date:
        if is_twse_session(cursor):
            count += 1
        cursor += datetime.timedelta(days=1)
    return count


def validation_max_trading_gap():
    """
    Max trading-day gap allowed for on-air 「昨日/上集驗收」.

    Default 1: only previous trading session predictions are treated as 驗收.
    Override with VALIDATION_MAX_TRADING_GAP (integer).
    """
    raw = os.getenv("VALIDATION_MAX_TRADING_GAP", "1")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 1


def find_previous_structured_data(reports_dir, as_of_date=None):
    """
    Find the most recent structured_data before as_of_date (default: today).

    Returns (prediction_targets, full_data, meta).

    meta keys:
      - source_date: previous report date string
      - trading_days_gap: Mon–Fri sessions between source and as_of
      - onair_validation_eligible: whether content may use 「昨日驗收」
      - skip_reason: human-readable reason when not eligible
      - max_trading_gap: configured threshold
      - path: file path

    On-air eligibility: trading_days_gap <= VALIDATION_MAX_TRADING_GAP (default 1).
    If you skip Fridays / holidays / special-topic-only days, multi-day-old
    prediction_targets are still found for reference but must not be packaged
    as overnight 驗收.
    """
    empty_meta = {
        "source_date": None,
        "trading_days_gap": None,
        "onair_validation_eligible": False,
        "skip_reason": "無前次 structured_data",
        "max_trading_gap": validation_max_trading_gap(),
        "path": None,
    }

    as_of = _parse_iso_date(as_of_date) or datetime.date.today()
    all_jsons = iter_structured_data_paths(reports_dir)
    candidates = []
    for path in all_jsons:
        path_date = _parse_iso_date(_date_from_structured_path(path))
        if path_date is None or path_date >= as_of:
            continue
        candidates.append((path_date, path))
    if not candidates:
        return [], None, empty_meta

    candidates.sort(key=lambda item: item[0])
    source_date, latest = candidates[-1]
    try:
        with open(latest, "r", encoding="utf-8") as f:
            data = json.load(f)
        targets = data.get("prediction_targets", []) or []
        data_date = data.get("date") or source_date.isoformat()
        source_date = _parse_iso_date(data_date) or source_date
        gap = count_trading_days_between(source_date, as_of)
        max_gap = validation_max_trading_gap()
        eligible = gap is not None and gap > 0 and gap <= max_gap
        if gap is None:
            skip_reason = "無法解析前次日期，略過上集驗收"
        elif gap <= 0:
            skip_reason = "前次日期不早於今日，略過驗收"
        elif gap > max_gap:
            skip_reason = (
                f"前次產出 {source_date} 距今 {gap} 個交易日（上限 {max_gap}），"
                f"略過「昨日/上集驗收」避免跨日誤判"
            )
        else:
            skip_reason = ""

        meta = {
            "source_date": source_date.isoformat() if source_date else str(data_date),
            "trading_days_gap": gap,
            "onair_validation_eligible": eligible,
            "skip_reason": skip_reason,
            "max_trading_gap": max_gap,
            "path": latest,
            "target_count": len(targets),
        }
        status = "可上集驗收" if eligible else f"略過上集驗收（{skip_reason}）"
        print(
            f"  [優化 B] 讀取前次 structured_data: {os.path.basename(latest)} "
            f"({meta['source_date']})，預測標的: {len(targets)} 檔；"
            f"交易日距={gap}；{status}"
        )
        return targets, data, meta
    except Exception as e:
        print(f"  [優化 B] 無法讀取前次 structured_data: {e}")
        empty_meta["skip_reason"] = f"讀取失敗: {e}"
        return [], None, empty_meta


def resolve_onair_prediction_targets(targets, validation_meta):
    """
    Targets allowed into content 驗收 + single-day hit-rate tracking.

    When production was skipped for 1+ sessions, return [] so packaging does not
    claim 「昨日點名」against multi-day price moves.
    """
    meta = validation_meta or {}
    if meta.get("onair_validation_eligible"):
        return list(targets or [])
    return []


def append_channel_story_log(
    date_str,
    structured_data,
    visual_theme="",
    prediction_result_summary="待驗收",
    video_id="",
    title_used="",
    youtube_assets_dir="",
    channel_story_log="",
):
    """Append the day summary to the channel story log."""
    if not ensure_channel_story_log(channel_story_log, youtube_assets_dir=youtube_assets_dir):
        print("  [優化 C] 頻道敘事紀錄檔無法建立或修復，略過")
        return

    picks = structured_data.get("ai_data_picks", [])
    picks_str = "、".join(picks[:2]) if picks else "無明牌"
    targets = structured_data.get("prediction_targets", [])
    direction_str = "、".join([f"{t.get('name','')}({t.get('direction','多')})" for t in targets[:2]]) if targets else "無"
    index_action = structured_data.get("index_action", "")

    row = (
        f"| {_safe_table_cell(date_str)} | {_safe_table_cell(picks_str)} | {_safe_table_cell(direction_str)} | "
        f"{_safe_table_cell(prediction_result_summary)} | {_safe_table_cell(visual_theme if visual_theme else '未指定')} | "
        f"{_safe_table_cell(index_action[:20])} | {_safe_table_cell(video_id if video_id else '-')} | "
        f"{_safe_table_cell(title_used if title_used else '-')} | 待更新 |\n"
    )

    try:
        with open(channel_story_log, "a", encoding="utf-8") as f:
            f.write(row)
        print(f"  [優化 C] 頻道敘事紀錄已更新: {channel_story_log}")
    except Exception as e:
        print(f"  [優化 C] 無法寫入頻道敘事紀錄: {e}")


def is_generation_error(content):
    return isinstance(content, str) and content.strip().startswith("Error generating")


def should_expand_news_search(signal_pack, validation_results, event_radar):
    strongest = (signal_pack or {}).get("strongest_anomaly") or {}
    if float(strongest.get("score") or 0) >= 55:
        return True, "本地數據出現高分異常"
    if (signal_pack or {}).get("watch_candidates"):
        return True, "本地數據有明日觀察候選"
    if any(row.get("hit") is True or row.get("hit") is False for row in (validation_results or [])):
        return True, "昨日驗收可形成連續追蹤"
    top_event = (event_radar or [None])[0] or {}
    if (
        top_event.get("freshness") != "stale"
        and int(top_event.get("impact_score") or 0) >= 60
    ):
        return True, "消息面事件雷達命中高分供應鏈事件"
    return False, "無高分數據異常或高分事件，跳過擴大新聞搜尋"


def _read_tail(path, max_chars=5000):
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return content[-max_chars:]
    except Exception:
        return ""


def build_packaging_context(reports_dir, base_dir, channel_story_log, validation_summary=""):
    """Build late-stage YouTube packaging context from local learning records."""
    title_rules_path = os.path.join(base_dir, "project_skills", "youtube_title_packaging.md")
    analytics_path = os.path.join(reports_dir, "analytics_summary.md")
    youtube_performance_path = os.path.join(base_dir, "data", "knowledge_wiki", "youtube_performance_patterns.md")
    local_memory_path = os.path.join(base_dir, "data", "knowledge_wiki", "local_market_memory.md")
    role_taxonomy_path = os.path.join(base_dir, "data", "knowledge_wiki", "stock_role_taxonomy.md")
    audience_profile = (
        "觀眾偏好：標題與封面要具體點名個股；先講情境或矛盾，再回答哪幾檔。"
        "避免只說 AI 族群、資金輪動、前景看好。"
        "但也不要每天硬套背離、退潮、警報或資金流；先判斷今日是驗收日、重大事件日、異常日、續航日、主題日或整理日。"
        "若是整理日，包裝要誠實聚焦等待確認與明日觀察條件。"
        "同一支股票要分清角色：方向指標、彈性攻擊、題材確認、資金載體要分開包裝。"
        "角色判斷必須參照本機 stock_db 的 market_role.evidence，不可只憑印象。"
    )
    return {
        "validation_summary": validation_summary,
        "title_fatigue_report": build_title_fatigue_report(_read_tail(channel_story_log, max_chars=5000)),
        "analytics_summary": _read_tail(analytics_path, max_chars=4500),
        "youtube_performance_patterns": _read_tail(youtube_performance_path, max_chars=5000),
        "channel_story": _read_tail(channel_story_log, max_chars=3500),
        "title_packaging_rules": _read_tail(title_rules_path, max_chars=5000),
        "local_market_memory": _read_tail(local_memory_path, max_chars=3500),
        "stock_role_taxonomy": _read_tail(role_taxonomy_path, max_chars=2500),
        "audience_profile": audience_profile,
    }
