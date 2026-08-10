"""
Canonical layout for reports/ outputs with legacy path resolution.

New layout:
  reports/daily/YYYY-MM-DD/{daily_report,structured_data,notebooklm_*}.md/json
  reports/youtube/YYYY-MM-DD/{youtube_package,yt_thumbnail_prompt_1,2}.md
  reports/shared/{channel_story_log,youtube_packaging_playbook,...}.md
"""

from __future__ import annotations

import glob
import os
import re
from datetime import datetime


DAILY_DIRNAME = "daily"
YOUTUBE_DIRNAME = "youtube"
SHARED_DIRNAME = "shared"
LEGACY_YOUTUBE_DIRNAME = "06_YouTube_Assets"
LEGACY_DAILY_OUTPUTS_DIRNAME = "daily_outputs"

DAILY_ARTIFACTS = {
    "report": "daily_report.md",
    "structured_data": "structured_data.json",
    "notebooklm_source": "notebooklm_source.md",
    "notebooklm_prompt": "notebooklm_prompt.md",
}

YOUTUBE_ARTIFACTS = {
    "package": "youtube_package.md",
    "thumbnail_1": "yt_thumbnail_prompt_1.md",
    "thumbnail_2": "yt_thumbnail_prompt_2.md",
}

SHARED_ARTIFACTS = {
    "channel_story_log": "channel_story_log.md",
    "youtube_packaging_playbook": "youtube_packaging_playbook.md",
    "youtube_experiment_log": "youtube_experiment_log.md",
    "operating_cadence": "operating_cadence.md",
    "heygen_quota_reminder": "heygen_quota_reminder.md",
}


def _ymd(date_str: str) -> str:
    return str(date_str).replace("-", "")


def daily_dir(reports_dir: str, date_str: str) -> str:
    return os.path.join(reports_dir, DAILY_DIRNAME, date_str)


def youtube_dir(reports_dir: str, date_str: str) -> str:
    return os.path.join(reports_dir, YOUTUBE_DIRNAME, date_str)


def shared_dir(reports_dir: str) -> str:
    return os.path.join(reports_dir, SHARED_DIRNAME)


def legacy_youtube_dir(reports_dir: str) -> str:
    return os.path.join(reports_dir, LEGACY_YOUTUBE_DIRNAME)


def ensure_daily_dir(reports_dir: str, date_str: str) -> str:
    path = daily_dir(reports_dir, date_str)
    os.makedirs(path, exist_ok=True)
    return path


def ensure_youtube_dir(reports_dir: str, date_str: str) -> str:
    path = youtube_dir(reports_dir, date_str)
    os.makedirs(path, exist_ok=True)
    return path


def ensure_shared_dir(reports_dir: str) -> str:
    path = shared_dir(reports_dir)
    os.makedirs(path, exist_ok=True)
    return path


def _legacy_daily_candidates(reports_dir: str, date_str: str, kind: str) -> list[str]:
    ymd = _ymd(date_str)
    mapping = {
        "report": [
            os.path.join(reports_dir, f"daily_report_V21_{date_str}.md"),
            os.path.join(reports_dir, LEGACY_DAILY_OUTPUTS_DIRNAME, date_str, f"daily_report_V21_{date_str}.md"),
            os.path.join(reports_dir, LEGACY_DAILY_OUTPUTS_DIRNAME, date_str, "daily_report.md"),
        ],
        "structured_data": [
            os.path.join(reports_dir, f"structured_data_{ymd}.json"),
            os.path.join(reports_dir, LEGACY_DAILY_OUTPUTS_DIRNAME, date_str, f"structured_data_{ymd}.json"),
            os.path.join(reports_dir, LEGACY_DAILY_OUTPUTS_DIRNAME, date_str, "structured_data.json"),
        ],
        "notebooklm_source": [
            os.path.join(reports_dir, f"notebooklm_source_{ymd}.md"),
            os.path.join(reports_dir, LEGACY_DAILY_OUTPUTS_DIRNAME, date_str, f"notebooklm_source_{ymd}.md"),
            os.path.join(reports_dir, LEGACY_DAILY_OUTPUTS_DIRNAME, date_str, "notebooklm_source.md"),
        ],
        "notebooklm_prompt": [
            os.path.join(reports_dir, f"notebooklm_prompt_podcast_{ymd}.md"),
            os.path.join(reports_dir, LEGACY_DAILY_OUTPUTS_DIRNAME, date_str, f"notebooklm_prompt_podcast_{ymd}.md"),
            os.path.join(reports_dir, LEGACY_DAILY_OUTPUTS_DIRNAME, date_str, "notebooklm_prompt.md"),
        ],
    }
    return mapping.get(kind, [])


def _legacy_youtube_candidates(reports_dir: str, date_str: str, kind: str) -> list[str]:
    legacy_dir = legacy_youtube_dir(reports_dir)
    outputs_dir = os.path.join(reports_dir, LEGACY_DAILY_OUTPUTS_DIRNAME, date_str)
    mapping = {
        "package": [
            os.path.join(legacy_dir, f"youtube_package_{date_str}.md"),
            os.path.join(outputs_dir, f"youtube_package_{date_str}.md"),
            os.path.join(outputs_dir, "youtube_package.md"),
        ],
        "thumbnail_1": [
            os.path.join(legacy_dir, f"yt_thumbnail_prompt_{date_str}_1.md"),
            os.path.join(outputs_dir, f"yt_thumbnail_prompt_{date_str}_1.md"),
            os.path.join(outputs_dir, "yt_thumbnail_prompt_1.md"),
        ],
        "thumbnail_2": [
            os.path.join(legacy_dir, f"yt_thumbnail_prompt_{date_str}_2.md"),
            os.path.join(outputs_dir, f"yt_thumbnail_prompt_{date_str}_2.md"),
            os.path.join(outputs_dir, "yt_thumbnail_prompt_2.md"),
        ],
    }
    return mapping.get(kind, [])


def resolve_daily_artifact(reports_dir: str, date_str: str, kind: str) -> str | None:
    canonical = os.path.join(daily_dir(reports_dir, date_str), DAILY_ARTIFACTS[kind])
    if os.path.exists(canonical):
        return canonical
    for path in _legacy_daily_candidates(reports_dir, date_str, kind):
        if os.path.exists(path):
            return path
    return None


def resolve_youtube_artifact(reports_dir: str, date_str: str, kind: str) -> str | None:
    canonical = os.path.join(youtube_dir(reports_dir, date_str), YOUTUBE_ARTIFACTS[kind])
    if os.path.exists(canonical):
        return canonical
    for path in _legacy_youtube_candidates(reports_dir, date_str, kind):
        if os.path.exists(path):
            return path
    return None


def daily_artifact_write_path(reports_dir: str, date_str: str, kind: str) -> str:
    ensure_daily_dir(reports_dir, date_str)
    return os.path.join(daily_dir(reports_dir, date_str), DAILY_ARTIFACTS[kind])


def youtube_artifact_write_path(reports_dir: str, date_str: str, kind: str) -> str:
    ensure_youtube_dir(reports_dir, date_str)
    return os.path.join(youtube_dir(reports_dir, date_str), YOUTUBE_ARTIFACTS[kind])


def resolve_shared_artifact(reports_dir: str, key: str) -> str:
    filename = SHARED_ARTIFACTS[key]
    shared_path = os.path.join(shared_dir(reports_dir), filename)
    if os.path.exists(shared_path):
        return shared_path
    legacy_path = os.path.join(legacy_youtube_dir(reports_dir), filename)
    if os.path.exists(legacy_path):
        return legacy_path
    ensure_shared_dir(reports_dir)
    return shared_path


def relative_daily_link(reports_dir: str, date_str: str, kind: str) -> str:
    path = resolve_daily_artifact(reports_dir, date_str, kind)
    if path:
        return os.path.relpath(path, reports_dir)
    return os.path.join(DAILY_DIRNAME, date_str, DAILY_ARTIFACTS[kind]).replace(os.sep, "/")


def relative_youtube_link(reports_dir: str, date_str: str, kind: str) -> str:
    path = resolve_youtube_artifact(reports_dir, date_str, kind)
    if path:
        return os.path.relpath(path, reports_dir)
    return os.path.join(YOUTUBE_DIRNAME, date_str, YOUTUBE_ARTIFACTS[kind]).replace(os.sep, "/")


def date_from_structured_path(path: str) -> str | None:
    return _date_from_structured_path(path)


def _date_from_structured_path(path: str) -> str | None:
    basename = os.path.basename(path)
    if basename == "structured_data.json":
        parent = os.path.basename(os.path.dirname(path))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", parent):
            return parent
    match = re.search(r"structured_data_(\d{8})\.json$", basename)
    if match:
        raw = match.group(1)
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return None


def iter_structured_data_paths(reports_dir: str) -> list[str]:
    patterns = [
        os.path.join(reports_dir, DAILY_DIRNAME, "*", "structured_data.json"),
        os.path.join(reports_dir, "structured_data_????????.json"),
        os.path.join(reports_dir, LEGACY_DAILY_OUTPUTS_DIRNAME, "*", "structured_data*.json"),
        os.path.join(reports_dir, "archive", "*", "structured_data_????????.json"),
    ]
    found = []
    for pattern in patterns:
        found.extend(glob.glob(pattern))
    unique = sorted(set(found), key=lambda path: (_date_from_structured_path(path) or "", path))
    return unique


def iter_daily_report_paths(reports_dir: str) -> list[str]:
    patterns = [
        os.path.join(reports_dir, DAILY_DIRNAME, "*", "daily_report.md"),
        os.path.join(reports_dir, "daily_report_V21_*.md"),
        os.path.join(reports_dir, LEGACY_DAILY_OUTPUTS_DIRNAME, "*", "daily_report*.md"),
    ]
    found = []
    for pattern in patterns:
        found.extend(glob.glob(pattern))
    return sorted(set(found))


def iter_thumbnail_prompt_paths(reports_dir: str, limit: int = 20) -> list[str]:
    patterns = [
        os.path.join(reports_dir, YOUTUBE_DIRNAME, "*", "yt_thumbnail_prompt_*.md"),
        os.path.join(reports_dir, LEGACY_YOUTUBE_DIRNAME, "yt_thumbnail_prompt_*.md"),
        os.path.join(reports_dir, LEGACY_DAILY_OUTPUTS_DIRNAME, "*", "yt_thumbnail_prompt_*.md"),
    ]
    files = []
    for pattern in patterns:
        files.extend(glob.glob(pattern))
    files = sorted(set(files), key=os.path.getmtime, reverse=True)
    return files[:limit]


def extract_date_from_thumbnail_path(path: str) -> str | None:
    parent = os.path.basename(os.path.dirname(path))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", parent):
        return parent
    match = re.search(r"yt_thumbnail_prompt_(\d{4}-\d{2}-\d{2})_", os.path.basename(path))
    if match:
        return match.group(1)
    return None