import os
import re

from modules.report_paths import relative_daily_link, relative_youtube_link


TODAY_SPECIAL_FILES = [
    (
        "資料夾",
        "special_topics/2026-09-11_cpi_core_scenarios/",
    ),
    (
        "報告",
        "special_topics/2026-09-11_cpi_core_scenarios/special_report_2026-09-11_cpi_core_scenarios.md",
    ),
    (
        "NotebookLM source",
        "special_topics/2026-09-11_cpi_core_scenarios/notebooklm_source_special_20260911_cpi_core_scenarios.md",
    ),
    (
        "NotebookLM prompt",
        "special_topics/2026-09-11_cpi_core_scenarios/notebooklm_prompt_special_20260911_cpi_core_scenarios.md",
    ),
    (
        "YouTube 專題包",
        "special_topics/2026-09-11_cpi_core_scenarios/youtube_special_package_2026-09-11_cpi_core_scenarios.md",
    ),
    (
        "封面 prompt",
        "special_topics/2026-09-11_cpi_core_scenarios/yt_thumbnail_prompt_special_2026-09-11_cpi_core_scenarios.md",
    ),
]

RECENT_SPECIAL_LINKS = [
    (
        "iPhone 18 供應鏈專題",
        "special_topics/2026-09-10_iphone18_taiwan_supply_chain/special_report_2026-09-10_iphone18_taiwan_supply_chain.md",
    ),
]

SPECIAL_TOPIC_LINKS = [
    (
        "SpaceX 專題報告",
        "special_topics/2026-06-27_spacex_taiwan_supply_chain/special_report_2026-06-27_spacex_taiwan_supply_chain.md",
    ),
    (
        "SpaceX NotebookLM prompt",
        "special_topics/2026-06-27_spacex_taiwan_supply_chain/notebooklm_prompt_special_20260627_spacex_taiwan_supply_chain.md",
    ),
    (
        "SpaceX NotebookLM source",
        "special_topics/2026-06-27_spacex_taiwan_supply_chain/notebooklm_source_special_20260627_spacex_taiwan_supply_chain.md",
    ),
    (
        "SpaceX 上架包",
        "special_topics/2026-06-27_spacex_taiwan_supply_chain/youtube_special_package_2026-06-27_spacex_taiwan_supply_chain.md",
    ),
    (
        "SpaceX 封面 prompt",
        "special_topics/2026-06-27_spacex_taiwan_supply_chain/yt_thumbnail_prompt_special_2026-06-27_spacex_taiwan_supply_chain.md",
    ),
]


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


def _default_today_special_lines(reports_dir) -> list[str]:
    if not any(_exists(reports_dir, path) for _, path in TODAY_SPECIAL_FILES):
        return []
    lines = ["- 狀態：已產出（情境短片，非單點預測；CPI 已公布，專題改作對照用）"]
    for label, path in TODAY_SPECIAL_FILES:
        if _exists(reports_dir, path):
            if path.endswith("/"):
                name = os.path.basename(path.rstrip("/"))
                lines.append(f"- {label}：[`{name}`](./{path.rstrip('/')}/)")
            else:
                lines.append(_rel_link(label, path))
    return lines


def _recent_special_lines(reports_dir, previous_markdown="") -> list[str]:
    seen = set()
    lines = []
    prev_body = _extract_section(previous_markdown, "最近完成的獨立專題")
    for label, path in RECENT_SPECIAL_LINKS + SPECIAL_TOPIC_LINKS:
        if _exists(reports_dir, path) and path not in seen:
            seen.add(path)
            lines.append(_rel_link(label, path))
    for match in re.finditer(r"^- (.+?)：\[`[^`]+`\]\(\./([^)]+)\)", prev_body or "", flags=re.M):
        label, path = match.group(1), match.group(2)
        if path not in seen and _exists(reports_dir, path):
            seen.add(path)
            lines.append(_rel_link(label, path))
    return lines


def build_current_index(
    reports_dir,
    date_str,
    *,
    daily_ready=True,
    youtube_ready=False,
    youtube_visual_theme="",
    youtube_note="",
    previous_markdown="",
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
        lines.append(f"- 備註：本日 /1 已通過同日資料閘門；法人資料與成交量排行皆為 {date_str}。")
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
    required.extend([path for _, path in TODAY_SPECIAL_FILES if not path.endswith("/")])
    required.extend([path for _, path in RECENT_SPECIAL_LINKS])
    required.extend([path for _, path in SPECIAL_TOPIC_LINKS])
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
    today_special_body = _extract_section(previous_markdown, "今日獨立專題")
    if not _section_has_existing_link(reports_dir, today_special_body):
        today_special_lines = _default_today_special_lines(reports_dir)
        today_special_body = "\n".join(today_special_lines)
    if today_special_body:
        lines.extend(["## 今日獨立專題", "", today_special_body, ""])

    recent_lines = _recent_special_lines(reports_dir, previous_markdown)
    lines.extend(["## 最近完成的獨立專題", ""])
    if recent_lines:
        lines.extend(recent_lines)
    else:
        for label, path in SPECIAL_TOPIC_LINKS:
            lines.append(_rel_link(label, path))

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
