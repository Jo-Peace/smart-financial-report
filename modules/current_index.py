import os
import re

from modules.report_paths import relative_daily_link, relative_youtube_link


SPECIAL_TOPICS_DIRNAME = "special_topics"
RECENT_SPECIAL_LIMIT = 5

_TOPIC_DIR_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+)$")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# Filename prefix -> label, in display order. First match wins.
_SPECIAL_FILE_LABELS = (
    ("special_report_", "報告"),
    ("special_structured_data_", "結構化資料"),
    ("notebooklm_source_", "NotebookLM source"),
    ("notebooklm_studio_prompt_", "NotebookLM studio prompt"),
    ("notebooklm_prompt_", "NotebookLM prompt"),
    ("youtube_special_package_", "YouTube 專題包"),
    ("yt_thumbnail_prompt_", "封面 prompt"),
    ("special_topic_plan_", "專題企劃"),
)
_MAIN_FILE_PREFIXES = ("special_report_", "youtube_special_package_")


def _rel_link(label, relative_path):
    name = os.path.basename(relative_path)
    return f"- {label}：[`{name}`](./{relative_path})"


def _exists(reports_dir, relative_path):
    return os.path.exists(os.path.join(reports_dir, relative_path))


def _qa_lines(reports_dir, required_paths):
    missing = [path for path in required_paths if path and not _exists(reports_dir, path)]
    if not missing:
        return ["- QA：所有索引連結目標皆存在"]
    lines = ["- QA：⚠️ 有索引連結找不到目標檔"]
    lines.extend([f"  - 缺少：`{path}`" for path in missing])
    return lines


def _extract_section(markdown: str, heading: str) -> str:
    pattern = rf"^## {re.escape(heading)}\s*$"
    lines = (markdown or "").splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(pattern, line.strip()):
            start = i + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start, len(lines)):
        if lines[j].startswith("## ") and not lines[j].startswith("### "):
            end = j
            break
    body = "\n".join(lines[start:end]).strip()
    return body


def _section_has_existing_link(reports_dir, body: str) -> bool:
    if not body:
        return False
    for rel in re.findall(r"\]\(\./([^)]+)\)", body):
        if _exists(reports_dir, rel):
            return True
    return False


def _file_label(filename: str) -> str | None:
    for prefix, label in _SPECIAL_FILE_LABELS:
        if filename.startswith(prefix):
            return label
    return None


def _read_title(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            for _ in range(20):
                line = f.readline()
                if not line:
                    break
                if line.startswith("# "):
                    return line[2:].strip()
    except OSError:
        pass
    return ""


def scan_special_topics(reports_dir) -> list[dict]:
    """
    Special topic folders ``special_topics/YYYY-MM-DD_slug/``, newest first.

    Each entry: date, slug, folder (relative), files [(label, rel_path)],
    main (label, rel_path) and title (first heading of the main file).
    """
    root = os.path.join(reports_dir, SPECIAL_TOPICS_DIRNAME)
    try:
        names = os.listdir(root)
    except OSError:
        return []
    topics = []
    for name in names:
        match = _TOPIC_DIR_RE.match(name)
        folder_abs = os.path.join(root, name)
        if not match or not os.path.isdir(folder_abs):
            continue
        folder_rel = f"{SPECIAL_TOPICS_DIRNAME}/{name}"
        files = []
        for filename in sorted(os.listdir(folder_abs)):
            label = _file_label(filename)
            if label and os.path.isfile(os.path.join(folder_abs, filename)):
                files.append((label, f"{folder_rel}/{filename}"))
        files.sort(key=lambda item: [lbl for _, lbl in _SPECIAL_FILE_LABELS].index(item[0]))
        main = None
        for prefix in _MAIN_FILE_PREFIXES:
            main = next((f for f in files if os.path.basename(f[1]).startswith(prefix)), None)
            if main:
                break
        if main is None and files:
            main = files[0]
        title = _read_title(os.path.join(reports_dir, main[1])) if main else ""
        topics.append(
            {
                "date": match.group(1),
                "slug": match.group(2),
                "folder": folder_rel,
                "files": files,
                "main": main,
                "title": title,
            }
        )
    topics.sort(key=lambda t: (t["date"], t["slug"]), reverse=True)
    return topics


def is_current_special_date(topic_date, date_str) -> bool:
    """
    A special topic is "today's" when it was produced on date_str or after the
    previous TWSE session (weekend / holiday specials belong to the next /1).
    """
    from modules.twse_calendar import as_date, prev_session

    topic = as_date(topic_date)
    today = as_date(date_str)
    if topic is None or today is None or topic > today:
        return False
    if topic == today:
        return True
    return topic > prev_session(today)


def _topic_label(topic) -> str:
    return topic.get("title") or f"{topic['date']} {topic['slug']}"


def _topic_lines(topic) -> list[str]:
    lines = [f"- 資料夾：[`{topic['folder']}/`](./{topic['folder']}/)"]
    lines.extend(_rel_link(label, path) for label, path in topic["files"])
    return lines


def _section_latest_link_date(body: str) -> str | None:
    """Newest YYYY-MM-DD found in the section's link targets (not in free text)."""
    dates = []
    for target in re.findall(r"\]\(\./([^)]+)\)", body or ""):
        dates.extend(_DATE_RE.findall(target))
    return max(dates) if dates else None


def _today_special_lines(reports_dir, date_str, topics, previous_markdown="") -> tuple[list[str], set]:
    """
    Lines for「今日獨立專題」and the folders they cover.

    A hand-written section from the previous CURRENT.md is kept only while the
    newest date in its links is still current; stale sections are replaced by
    the folder scan (and their topic drops into「最近完成」).
    """
    current = [t for t in topics if is_current_special_date(t["date"], date_str)]
    previous_body = _extract_section(previous_markdown, "今日獨立專題")
    previous_date = _section_latest_link_date(previous_body)
    if (
        previous_body
        and previous_date
        and _section_has_existing_link(reports_dir, previous_body)
        and is_current_special_date(previous_date, date_str)
    ):
        lines = previous_body.splitlines()
        covered = {t["folder"] for t in topics if t["folder"] in previous_body}
        for topic in current:
            if topic["folder"] not in covered:
                lines.append(f"- 專題（{topic['date']}）：{_topic_label(topic)}")
                lines.extend(_topic_lines(topic))
                covered.add(topic["folder"])
        return lines, covered

    if current:
        lines = []
        for topic in current:
            lines.append(f"- 專題（{topic['date']}）：{_topic_label(topic)}")
            lines.extend(_topic_lines(topic))
        return lines, {t["folder"] for t in current}

    if not topics:
        return [], set()
    latest = topics[0]
    lines = [
        f"- 狀態：{date_str} 沒有新的獨立專題",
        f"- 最近一次獨立專題（{latest['date']}）：{_topic_label(latest)}",
        f"- 資料夾：[`{latest['folder']}/`](./{latest['folder']}/)",
    ]
    return lines, {latest["folder"]}


def _recent_special_lines(topics, exclude_folders, limit=RECENT_SPECIAL_LIMIT) -> list[str]:
    lines = []
    for topic in topics:
        if topic["folder"] in exclude_folders or not topic["main"]:
            continue
        label = f"{topic['date']} {_topic_label(topic)}" if topic.get("title") else _topic_label(topic)
        lines.append(_rel_link(label, topic["main"][1]))
        if len(lines) >= limit:
            break
    return lines


def _data_date_note(date_str, institutional_date=None, volume_date=None):
    """Only claim same-day data when the actual data dates were passed and match."""
    inst = str(institutional_date or "")[:10]
    vol = str(volume_date or "")[:10]
    if inst and vol and inst == vol == date_str:
        return f"本日 /1 已通過同日資料閘門；法人資料與成交量排行皆為 {date_str}。"
    if inst or vol:
        return (
            f"本日 /1 產出日 {date_str}；法人資料日 {inst or '未記錄'}、成交量排行日 {vol or '未記錄'}"
            + ("（與產出日不同，請留意）。" if {inst, vol} - {"", date_str} else "。")
        )
    return f"/1 產出日 {date_str}（本次未記錄法人／成交量的實際資料日）。"


def build_current_index(
    reports_dir,
    date_str,
    *,
    daily_ready=True,
    youtube_ready=False,
    youtube_visual_theme="",
    youtube_note="",
    previous_markdown="",
    institutional_date=None,
    volume_date=None,
):
    daily_paths = {
        "日報": relative_daily_link(reports_dir, date_str, "report"),
        "盤後結構資料": relative_daily_link(reports_dir, date_str, "structured_data"),
        "NotebookLM 日報 prompt": relative_daily_link(reports_dir, date_str, "notebooklm_prompt"),
        "NotebookLM 日報 source": relative_daily_link(reports_dir, date_str, "notebooklm_source"),
    }
    youtube_paths = {
        "YouTube 發佈包": relative_youtube_link(reports_dir, date_str, "package"),
        "A 封面 prompt": relative_youtube_link(reports_dir, date_str, "thumbnail_1"),
        "B 封面 prompt": relative_youtube_link(reports_dir, date_str, "thumbnail_2"),
    }

    lines = [
        "# 今日產出索引",
        "",
        f"最近成功 /1 產出日期：{date_str}",
        "",
        "這份檔案只放最近一次成功產出的入口；尚未跑當日 /1 時，不會假裝有今天的資料。",
        "",
        "## 今日主流程 /1",
        "",
    ]

    if daily_ready:
        for label, path in daily_paths.items():
            lines.append(_rel_link(label, path))
        note = _data_date_note(date_str, institutional_date, volume_date)
        if not (institutional_date or volume_date):
            # /2 and maintenance rewrites do not know the data dates: keep the
            # note /1 wrote for this same date instead of inventing a claim.
            body = _extract_section(previous_markdown, "今日主流程 /1")
            for prev_line in (body or "").splitlines():
                if prev_line.startswith("- 備註：") and date_str in prev_line and "法人資料" in prev_line:
                    note = prev_line[len("- 備註："):]
                    break
        lines.append(f"- 備註：{note}")
    else:
        lines.extend(["- 狀態：待產出", f"- 備註：今天 {date_str} 的盤後日報尚未產生"])

    lines.extend(["", "## 今日盤後 /2", ""])
    if youtube_ready:
        lines.append("- 狀態：已產出")
        for label, path in youtube_paths.items():
            lines.append(_rel_link(label, path))
        if youtube_visual_theme:
            lines.append(f"- 視覺主題：{youtube_visual_theme}")
        if youtube_note:
            lines.append(f"- 備註：{youtube_note}")
    else:
        lines.extend(["- 狀態：待產出", f"- 備註：今天 {date_str} 的盤後 `/2` 尚未產生"])

    required = list(daily_paths.values()) if daily_ready else []
    if youtube_ready:
        required.extend(youtube_paths.values())
    # Topics dated after date_str (e.g. when re-indexing an older day) are ignored.
    topics = [t for t in scan_special_topics(reports_dir) if t["date"] <= str(date_str)[:10]]
    today_special_lines, covered_folders = _today_special_lines(
        reports_dir, date_str, topics, previous_markdown
    )
    recent_lines = _recent_special_lines(topics, covered_folders)
    for line in today_special_lines + recent_lines:
        required.extend(re.findall(r"\]\(\./([^)]+)\)", line))
    lines.extend(["", "## 索引 QA", ""])
    lines.extend(_qa_lines(reports_dir, required))

    lines.extend(
        [
            "",
            "## 今日整理資料夾",
            "",
            f"- [`daily/{date_str}/`](./daily/{date_str}/)",
            f"- [`youtube/{date_str}/`](./youtube/{date_str}/)" if youtube_ready else f"- `youtube/{date_str}/`（待 /2 產出）",
            "- [`shared/`](./shared/)",
            "",
        ]
    )
    if today_special_lines:
        lines.extend(["## 今日獨立專題", "", *today_special_lines, ""])

    lines.extend(["## 最近完成的獨立專題", ""])
    lines.extend(recent_lines or ["- （尚無其他專題）"])

    lines.extend(
        [
            "",
            "## 快速規則",
            "",
            "1. 先開這份檔案。",
            "2. 要看今天主流程，就看「今日主流程 /1」。",
            "3. 要看今天盤後，就看「今日盤後 /2」。",
            "4. 舊檔一律去 `archive/`，不要在根目錄找。",
            "",
        ]
    )
    return "\n".join(lines)


def write_current_index(reports_dir, date_str, **kwargs):
    path = os.path.join(reports_dir, "CURRENT.md")
    previous = ""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                previous = f.read()
        except OSError:
            previous = ""
    kwargs = dict(kwargs)
    kwargs.setdefault("previous_markdown", previous)
    content = build_current_index(reports_dir, date_str, **kwargs)
    os.makedirs(reports_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path
