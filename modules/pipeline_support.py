import datetime
import json
import os
import re

from modules.editorial_frame import build_title_fatigue_report
from modules.report_paths import (
    ensure_shared_dir,
    iter_daily_report_paths,
    iter_structured_data_paths,
)
from modules.report_paths import _date_from_structured_path  # path-date helper


# Rule-file budgets for the /2 packaging prompt. The title skill file is loaded whole
# (its Priority Order section holds the A-title hard rule); the playbook keeps its
# undated / 生效中 sections plus the newest dated ones.
TITLE_RULES_MAX_CHARS = 8000
PLAYBOOK_MAX_CHARS = 6000

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
    # Newest readable file wins: a corrupt/truncated latest JSON must not hide
    # every earlier run (the gap check below still decides 驗收 eligibility).
    unreadable = []
    for source_date, latest in reversed(candidates):
        try:
            with open(latest, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("structured_data 不是 JSON 物件")
            break
        except Exception as e:
            print(f"  [優化 B] 無法讀取前次 structured_data（{os.path.basename(latest)}）: {e}")
            unreadable.append(latest)
    else:
        empty_meta["skip_reason"] = f"讀取失敗: {len(unreadable)} 份 structured_data 皆無法解析"
        empty_meta["unreadable_paths"] = unreadable
        return [], None, empty_meta
    try:
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
        if unreadable:
            meta["unreadable_paths"] = unreadable
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


CHANNEL_STORY_PLACEHOLDER_VISUALS = {"", "-", "未指定", "待 /2 設定"}


def _table_cells(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_story_row(line):
    return line.startswith("| 20") and len(_table_cells(line)) >= 9


def upsert_channel_story_row(channel_story_log, cells):
    """
    Upsert one 9-column row keyed by date (cells[0]).

    - same date already present → update the latest such row's first 6 columns, keep
      影片ID / 使用標題 / 觀看數 (and a /2 visual theme when the new one is a placeholder)
    - otherwise insert right after the last main-table row (never after a trailing
      hand-written section), so reruns of /1 no longer duplicate rows.
    Returns "updated" | "inserted" | "unchanged".
    """
    from modules.atomic_io import atomic_write_text

    with open(channel_story_log, "r", encoding="utf-8") as f:
        lines = f.readlines()
    cells = [_safe_table_cell(c) for c in cells][:9]
    while len(cells) < 9:
        cells.append("-")
    date_str = cells[0]

    same_day = [idx for idx, line in enumerate(lines) if _is_story_row(line) and _table_cells(line)[0] == date_str]
    if same_day:
        idx = same_day[-1]
        current = _table_cells(lines[idx])
        merged = list(current[:9])
        for col in range(6):
            if col == 4 and cells[4] in CHANNEL_STORY_PLACEHOLDER_VISUALS and current[4] not in CHANNEL_STORY_PLACEHOLDER_VISUALS:
                continue
            merged[col] = cells[col]
        for col in (6, 7, 8):
            if merged[col] in {"", "-"} and cells[col] not in {"", "-", "待更新"}:
                merged[col] = cells[col]
        new_line = "| " + " | ".join(merged) + " |\n"
        if new_line == lines[idx]:
            return "unchanged"
        lines[idx] = new_line
        action = "updated"
    else:
        row_line = "| " + " | ".join(cells) + " |\n"
        story_rows = [idx for idx, line in enumerate(lines) if _is_story_row(line)]
        if story_rows:
            insert_at = story_rows[-1] + 1
        else:
            insert_at = len(lines)
            for idx, line in enumerate(lines):
                if line.startswith("|---") or line.startswith("|------"):
                    insert_at = idx + 1
                    break
        if insert_at > 0 and not lines[insert_at - 1].endswith("\n"):
            lines[insert_at - 1] += "\n"
        lines.insert(insert_at, row_line)
        action = "inserted"
    atomic_write_text(channel_story_log, "".join(lines))
    return action


def update_channel_story_cells(channel_story_log, date_str, visual_theme="", title_used=""):
    """
    /2 update of the date's row: visual theme (scene only — ban clauses live in
    data/packaging_bans.json) and the title when none is recorded yet. Cells go through
    _safe_table_cell so a half-width「|」can never shift columns. Returns True on change.
    """
    from modules.atomic_io import atomic_write_text

    if not channel_story_log or not os.path.exists(channel_story_log):
        return False
    with open(channel_story_log, "r", encoding="utf-8") as f:
        lines = f.readlines()
    rows = [idx for idx, line in enumerate(lines) if _is_story_row(line) and _table_cells(line)[0] == str(date_str)]
    if not rows:
        return False
    idx = rows[-1]
    cells = _table_cells(lines[idx])
    if visual_theme:
        cells[4] = _safe_table_cell(visual_theme)
    if title_used and cells[7] in {"-", "", "待更新"}:
        cells[7] = _safe_table_cell(title_used)
    new_line = "| " + " | ".join(cells) + " |\n"
    if new_line == lines[idx]:
        return False
    lines[idx] = new_line
    atomic_write_text(channel_story_log, "".join(lines))
    return True


def merge_duplicate_story_rows(content):
    """
    One-time cleanup: collapse main-table rows that share a date AND video (or a date
    with no video) into the last such row, filling its empty cells from earlier ones.
    Rows of the same date with different video IDs are kept. Returns (content, removed).
    """
    lines = content.splitlines(keepends=True)
    groups = {}
    for idx, line in enumerate(lines):
        if not _is_story_row(line):
            continue
        cells = _table_cells(line)
        vid = cells[6] if cells[6] not in {"", "-"} else ""
        groups.setdefault((cells[0], vid), []).append(idx)
    # an ID-less row on a date that also has a video row belongs to that video row
    dated_videos = {}
    for (day, vid), idxs in groups.items():
        if vid:
            dated_videos.setdefault(day, []).append((day, vid))
    for (day, vid), idxs in list(groups.items()):
        if not vid and len(dated_videos.get(day, [])) == 1:
            groups[dated_videos[day][0]] = sorted(idxs + groups[dated_videos[day][0]])
            del groups[(day, vid)]
    drop = set()
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        keep = idxs[-1]
        merged = _table_cells(lines[keep])
        for idx in idxs[:-1]:
            old = _table_cells(lines[idx])
            for col in range(min(len(merged), len(old))):
                if merged[col] in {"", "-", "待更新", "未指定", "待 /2 設定"} and old[col] not in {"", "-", "待更新"}:
                    merged[col] = old[col]
            drop.add(idx)
        lines[keep] = "| " + " | ".join(merged) + " |\n"
    if not drop:
        return content, 0
    return "".join(line for idx, line in enumerate(lines) if idx not in drop), len(drop)


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
    """Upsert the day summary into the channel story log (one row per date)."""
    if not ensure_channel_story_log(channel_story_log, youtube_assets_dir=youtube_assets_dir):
        print("  [優化 C] 頻道敘事紀錄檔無法建立或修復，略過")
        return

    picks = structured_data.get("ai_data_picks", [])
    picks_str = "、".join(picks[:2]) if picks else "無明牌"
    targets = structured_data.get("prediction_targets", [])
    direction_str = "、".join([f"{t.get('name','')}({t.get('direction','多')})" for t in targets[:2]]) if targets else "無"
    index_action = structured_data.get("index_action", "")

    cells = [
        date_str,
        picks_str,
        direction_str,
        prediction_result_summary,
        visual_theme if visual_theme else "未指定",
        str(index_action or "")[:20],
        video_id if video_id else "-",
        title_used if title_used else "-",
        "待更新",
    ]

    try:
        action = upsert_channel_story_row(channel_story_log, cells)
        label = {"updated": "已更新同日列", "inserted": "已新增", "unchanged": "無變更"}.get(action, action)
        print(f"  [優化 C] 頻道敘事紀錄{label}: {channel_story_log}")
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


def _read_tail(path, max_chars=5000, sanitize=False):
    """
    Last ``max_chars`` of a file, starting at a line boundary (never mid-entry).
    ``sanitize`` applies the narrative sanitizer on read.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return ""
    if len(content) > max_chars:
        tail = content[-max_chars:]
        cut = tail.find("\n")
        if cut != -1 and content[-max_chars - 1] != "\n":
            tail = tail[cut + 1:]
        content = tail
    if sanitize:
        from modules.narrative_sanitize import sanitize_narrative_text

        content = sanitize_narrative_text(content)
    return content


def read_recent_dated_entries(path, max_chars=3500, sanitize=False, before_date=None):
    """
    Newest whole ``##``/``###`` YYYY-MM-DD entries of a chronicle file that fit ``max_chars``.

    Entries are chosen by date (not file position), so an out-of-order backfill section is
    never mistaken for "recent", and an entry is never cut in half. ``before_date`` excludes
    that date and later (avoids self-feedback on reruns). Falls back to a line-aligned tail
    when the file has no dated headers.
    """
    from modules.knowledge_manager import split_dated_blocks

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return ""
    _, blocks = split_dated_blocks(content)
    if not blocks:
        return _read_tail(path, max_chars=max_chars, sanitize=sanitize)
    latest = {}
    for day, text in blocks:
        if before_date and day >= str(before_date)[:10]:
            continue
        latest[day] = text
    chosen, used = [], 0
    for day in sorted(latest, reverse=True):
        text = latest[day]
        if used + len(text) + 2 > max_chars:
            break
        chosen.append(text)
        used += len(text) + 2
    result = "\n\n".join(reversed(chosen))
    if sanitize and result:
        from modules.narrative_sanitize import sanitize_narrative_text

        result = sanitize_narrative_text(result)
    return result


_SECTION_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_PINNED_SECTION_RE = re.compile(r"生效中|強制")


def read_markdown_head_sections(path, max_chars=5000, prefer_recent=False):
    """
    Whole ``## `` sections of a rules file that fit ``max_chars`` (shown in file order).

    - default: from the top (skill files keep their current rules at the top)
    - ``prefer_recent=True`` (append-only playbooks): the preamble, undated sections and
      sections marked 生效中／強制 first, then dated sections newest first — a playbook
      appends new rules at the bottom, so a head cut dropped exactly the newest ones
    Skipped sections are listed by title.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return ""
    if len(content) <= max_chars:
        return content
    parts, current = [], []
    for line in content.splitlines(keepends=True):
        if line.startswith("## ") and current:
            parts.append("".join(current))
            current = []
        current.append(line)
    if current:
        parts.append("".join(current))
    order = list(range(len(parts)))
    if prefer_recent:
        def _rank(idx):
            title = parts[idx].splitlines()[0]
            match = _SECTION_DATE_RE.search(title)
            if idx == 0 or not match or _PINNED_SECTION_RE.search(title):
                return (0, "", idx)
            return (1, "".join(match.groups()), -idx)

        pinned = sorted((i for i in order if _rank(i)[0] == 0))
        dated = sorted((i for i in order if _rank(i)[0] == 1), key=lambda i: _rank(i)[1:], reverse=True)
        order = pinned + dated
    keep, used = set(), 0
    for idx in order:
        if used + len(parts[idx]) <= max_chars:
            keep.add(idx)
            used += len(parts[idx])
    out = [parts[i] for i in range(len(parts)) if i in keep]
    skipped = [parts[i].splitlines()[0].lstrip("# ").strip() for i in range(len(parts)) if i not in keep]
    text = "".join(out).rstrip()
    if skipped:
        text += "\n\n（以下章節因長度略過：" + "、".join(skipped[:8]) + "）\n"
    return text


def read_story_table_tail(path, n_rows=12):
    """Channel story header + last ``n_rows`` main-table rows (row-count based, not chars)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return ""
    header = [line for line in lines[:6] if line.startswith("#") or line.startswith("| 日期") or line.startswith("|---")]
    rows = [line for line in lines if _is_story_row(line)]
    return "".join(header) + "".join(rows[-n_rows:])


def _compact_channel_story(channel_story_log, n_rows=15):
    try:
        from modules.youtube_learning import format_compact_channel_story

        return format_compact_channel_story(channel_story_log, n_rows=n_rows)
    except Exception:
        return read_story_table_tail(channel_story_log, n_rows=8)


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
        "title_fatigue_report": build_title_fatigue_report(read_story_table_tail(channel_story_log, n_rows=12)),
        "analytics_summary": _read_tail(analytics_path, max_chars=4500),
        "youtube_performance_patterns": _read_tail(youtube_performance_path, max_chars=5000),
        "channel_story": _compact_channel_story(channel_story_log),
        "channel_story_rows": read_story_table_tail(channel_story_log, n_rows=14),
        "title_packaging_rules": read_markdown_head_sections(title_rules_path, max_chars=TITLE_RULES_MAX_CHARS),
        "local_market_memory": read_recent_dated_entries(local_memory_path, max_chars=3500, sanitize=True),
        "stock_role_taxonomy": _read_tail(role_taxonomy_path, max_chars=2500),
        "audience_profile": audience_profile,
    }
