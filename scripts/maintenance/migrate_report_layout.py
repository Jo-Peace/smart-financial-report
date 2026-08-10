"""
Migrate legacy reports/ layout to canonical daily/, youtube/, shared/ structure.

Moves files only — never deletes. Re-running is safe: skips targets that already exist.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.report_paths import (  # noqa: E402
    DAILY_ARTIFACTS,
    LEGACY_DAILY_OUTPUTS_DIRNAME,
    LEGACY_YOUTUBE_DIRNAME,
    SHARED_ARTIFACTS,
    YOUTUBE_ARTIFACTS,
    daily_dir,
    ensure_daily_dir,
    ensure_shared_dir,
    ensure_youtube_dir,
    shared_dir,
    youtube_dir,
)

REPORTS_DIR = os.path.join(BASE_DIR, "reports")
ARCHIVE_MISC = os.path.join(REPORTS_DIR, "archive", "misc")

DATE_YMD = re.compile(r"(\d{4}-\d{2}-\d{2})")
DATE_COMPACT = re.compile(r"(\d{8})")


def _resolve_symlink(path: str) -> str:
    if os.path.islink(path):
        target = os.readlink(path)
        if not os.path.isabs(target):
            target = os.path.normpath(os.path.join(os.path.dirname(path), target))
        return target
    return path


def _move(src: str, dst: str, *, dry_run: bool, log: list[str]) -> bool:
    src = _resolve_symlink(src)
    if not os.path.exists(src):
        return False
    if os.path.exists(dst):
        log.append(f"SKIP exists: {os.path.relpath(dst, REPORTS_DIR)}")
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    rel_src = os.path.relpath(src, REPORTS_DIR)
    rel_dst = os.path.relpath(dst, REPORTS_DIR)
    if dry_run:
        log.append(f"DRY-RUN {rel_src} -> {rel_dst}")
    else:
        shutil.move(src, dst)
        log.append(f"MOVED {rel_src} -> {rel_dst}")
    return True


def _date_from_compact(value: str) -> str | None:
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    return None


def _cleanup_stale_symlinks(dry_run: bool, log: list[str]) -> None:
    for name in os.listdir(REPORTS_DIR):
        path = os.path.join(REPORTS_DIR, name)
        if not os.path.islink(path):
            continue
        target = _resolve_symlink(path)
        if os.path.exists(target):
            continue
        rel = os.path.relpath(path, REPORTS_DIR)
        if dry_run:
            log.append(f"DRY-RUN remove stale symlink: {rel}")
        else:
            os.unlink(path)
            log.append(f"REMOVED stale symlink: {rel}")


def migrate_root_daily_files(dry_run: bool, log: list[str]) -> None:
    patterns = [
        (r"^daily_report_V21_(\d{4}-\d{2}-\d{2})\.md$", "report"),
        (r"^structured_data_(\d{8})\.json$", "structured_data"),
        (r"^notebooklm_source_(\d{8})\.md$", "notebooklm_source"),
        (r"^notebooklm_prompt_podcast_(\d{8})\.md$", "notebooklm_prompt"),
    ]
    for name in os.listdir(REPORTS_DIR):
        path = os.path.join(REPORTS_DIR, name)
        if not (os.path.isfile(path) or os.path.islink(path)):
            continue
        for pattern, kind in patterns:
            match = re.match(pattern, name)
            if not match:
                continue
            raw_date = match.group(1)
            date_str = raw_date if "-" in raw_date else _date_from_compact(raw_date)
            if not date_str:
                break
            dst = os.path.join(ensure_daily_dir(REPORTS_DIR, date_str), DAILY_ARTIFACTS[kind])
            _move(path, dst, dry_run=dry_run, log=log)
            break


def migrate_daily_outputs(dry_run: bool, log: list[str]) -> None:
    outputs_root = os.path.join(REPORTS_DIR, LEGACY_DAILY_OUTPUTS_DIRNAME)
    if not os.path.isdir(outputs_root):
        return

    daily_mapping = {
        "daily_report.md": "report",
        "structured_data.json": "structured_data",
        "notebooklm_source.md": "notebooklm_source",
        "notebooklm_prompt.md": "notebooklm_prompt",
    }
    youtube_mapping = {
        "youtube_package.md": "package",
        "yt_thumbnail_prompt_1.md": "thumbnail_1",
        "yt_thumbnail_prompt_2.md": "thumbnail_2",
    }
    legacy_daily = re.compile(r"^daily_report_V21_(\d{4}-\d{2}-\d{2})\.md$")
    legacy_json = re.compile(r"^structured_data_(\d{8})\.json$")
    legacy_source = re.compile(r"^notebooklm_source_(\d{8})\.md$")
    legacy_prompt = re.compile(r"^notebooklm_prompt_podcast_(\d{8})\.md$")
    legacy_package = re.compile(r"^youtube_package_(\d{4}-\d{2}-\d{2})\.md$")
    legacy_thumb = re.compile(r"^yt_thumbnail_prompt_(\d{4}-\d{2}-\d{2})_(\d)\.md$")

    for date_str in sorted(os.listdir(outputs_root)):
        day_dir = os.path.join(outputs_root, date_str)
        if not DATE_YMD.fullmatch(date_str) or not os.path.isdir(day_dir):
            continue
        for name in os.listdir(day_dir):
            src = os.path.join(day_dir, name)
            if not os.path.isfile(src):
                continue
            dst = None
            if name in daily_mapping:
                dst = os.path.join(daily_dir(REPORTS_DIR, date_str), DAILY_ARTIFACTS[daily_mapping[name]])
            elif name in youtube_mapping:
                dst = os.path.join(youtube_dir(REPORTS_DIR, date_str), YOUTUBE_ARTIFACTS[youtube_mapping[name]])
            elif legacy_daily.match(name):
                dst = os.path.join(daily_dir(REPORTS_DIR, date_str), DAILY_ARTIFACTS["report"])
            elif legacy_json.match(name):
                dst = os.path.join(daily_dir(REPORTS_DIR, date_str), DAILY_ARTIFACTS["structured_data"])
            elif legacy_source.match(name):
                dst = os.path.join(daily_dir(REPORTS_DIR, date_str), DAILY_ARTIFACTS["notebooklm_source"])
            elif legacy_prompt.match(name):
                dst = os.path.join(daily_dir(REPORTS_DIR, date_str), DAILY_ARTIFACTS["notebooklm_prompt"])
            elif legacy_package.match(name):
                dst = os.path.join(youtube_dir(REPORTS_DIR, date_str), YOUTUBE_ARTIFACTS["package"])
            elif legacy_thumb.match(name):
                index = legacy_thumb.match(name).group(2)
                dst = os.path.join(youtube_dir(REPORTS_DIR, date_str), YOUTUBE_ARTIFACTS[f"thumbnail_{index}"])
            if dst:
                _move(src, dst, dry_run=dry_run, log=log)


def migrate_root_youtube_files(dry_run: bool, log: list[str]) -> None:
    package_re = re.compile(r"^youtube_package_(\d{4}-\d{2}-\d{2})\.md$")
    thumb_re = re.compile(r"^yt_thumbnail_prompt_(\d{4}-\d{2}-\d{2})_(\d)\.md$")
    for name in os.listdir(REPORTS_DIR):
        path = os.path.join(REPORTS_DIR, name)
        if not (os.path.isfile(path) or os.path.islink(path)):
            continue
        package_match = package_re.match(name)
        if package_match:
            date_str = package_match.group(1)
            dst = os.path.join(youtube_dir(REPORTS_DIR, date_str), YOUTUBE_ARTIFACTS["package"])
            _move(path, dst, dry_run=dry_run, log=log)
            continue
        thumb_match = thumb_re.match(name)
        if thumb_match:
            date_str, index = thumb_match.groups()
            dst = os.path.join(youtube_dir(REPORTS_DIR, date_str), YOUTUBE_ARTIFACTS[f"thumbnail_{index}"])
            _move(path, dst, dry_run=dry_run, log=log)


def migrate_youtube_assets(dry_run: bool, log: list[str]) -> None:
    legacy_dir = os.path.join(REPORTS_DIR, LEGACY_YOUTUBE_DIRNAME)
    if not os.path.isdir(legacy_dir):
        return

    package_re = re.compile(r"^youtube_package_(\d{4}-\d{2}-\d{2})\.md$")
    thumb_re = re.compile(r"^yt_thumbnail_prompt_(\d{4}-\d{2}-\d{2})_(\d)\.md$")
    shared_names = set(SHARED_ARTIFACTS.values()) | {
        "review_due.md",
        "README.md",
    }
    shared_prefixes = ("content_review_",)

    for name in os.listdir(legacy_dir):
        src = os.path.join(legacy_dir, name)
        if not os.path.isfile(src):
            continue
        if name in shared_names or name.startswith(shared_prefixes):
            dst = os.path.join(ensure_shared_dir(REPORTS_DIR), name)
            _move(src, dst, dry_run=dry_run, log=log)
            continue
        package_match = package_re.match(name)
        if package_match:
            date_str = package_match.group(1)
            dst = os.path.join(youtube_dir(REPORTS_DIR, date_str), YOUTUBE_ARTIFACTS["package"])
            _move(src, dst, dry_run=dry_run, log=log)
            continue
        thumb_match = thumb_re.match(name)
        if thumb_match:
            date_str, index = thumb_match.groups()
            dst = os.path.join(youtube_dir(REPORTS_DIR, date_str), YOUTUBE_ARTIFACTS[f"thumbnail_{index}"])
            _move(src, dst, dry_run=dry_run, log=log)


def migrate_misc_to_archive(dry_run: bool, log: list[str]) -> None:
    misc_patterns = [
        re.compile(r"^run_.*\.log$"),
        re.compile(r".*_backup\.md$"),
        re.compile(r".*contaminated_backup\.md$"),
        re.compile(r".*\.html$"),
    ]
    for name in os.listdir(REPORTS_DIR):
        path = os.path.join(REPORTS_DIR, name)
        if not os.path.isfile(path):
            continue
        if not any(pattern.match(name) for pattern in misc_patterns):
            continue
        dst = os.path.join(ARCHIVE_MISC, name)
        _move(path, dst, dry_run=dry_run, log=log)


def migrate_special_topic_strays(dry_run: bool, log: list[str]) -> None:
    special_root = os.path.join(REPORTS_DIR, "special_topics")
    if not os.path.isdir(special_root):
        return
    stray_patterns = [
        re.compile(r"^special_report_.*\.md$"),
        re.compile(r"^notebooklm_.*special.*\.md$"),
        re.compile(r"^youtube_special_package_.*\.md$"),
        re.compile(r"^yt_thumbnail_prompt_special_.*\.md$"),
    ]
    for name in os.listdir(REPORTS_DIR):
        path = os.path.join(REPORTS_DIR, name)
        if not os.path.isfile(path):
            continue
        if not any(pattern.match(name) for pattern in stray_patterns):
            continue
        dst = os.path.join(special_root, name)
        _move(path, dst, dry_run=dry_run, log=log)


def refresh_current_index(dry_run: bool, log: list[str]) -> None:
    current_path = os.path.join(REPORTS_DIR, "CURRENT.md")
    if not os.path.exists(current_path):
        return
    with open(current_path, "r", encoding="utf-8") as f:
        content = f.read()
    match = re.search(r"更新日期：(\d{4}-\d{2}-\d{2})", content)
    if not match:
        return
    date_str = match.group(1)
    from modules.current_index import write_current_index  # noqa: E402

    youtube_ready = all(
        os.path.exists(os.path.join(youtube_dir(REPORTS_DIR, date_str), YOUTUBE_ARTIFACTS[kind]))
        for kind in ("package", "thumbnail_1", "thumbnail_2")
    )
    if dry_run:
        log.append(f"DRY-RUN refresh CURRENT.md for {date_str} (youtube_ready={youtube_ready})")
        return
    write_current_index(REPORTS_DIR, date_str, daily_ready=True, youtube_ready=youtube_ready)
    log.append(f"REFRESHED CURRENT.md for {date_str}")


def main():
    parser = argparse.ArgumentParser(description="Migrate reports/ to daily/, youtube/, shared/ layout.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned moves without changing files.")
    parser.add_argument("--skip-current", action="store_true", help="Do not rewrite CURRENT.md links.")
    args = parser.parse_args()

    log: list[str] = []
    migrate_root_daily_files(args.dry_run, log)
    migrate_root_youtube_files(args.dry_run, log)
    migrate_daily_outputs(args.dry_run, log)
    migrate_youtube_assets(args.dry_run, log)
    migrate_misc_to_archive(args.dry_run, log)
    migrate_special_topic_strays(args.dry_run, log)
    _cleanup_stale_symlinks(args.dry_run, log)
    if not args.skip_current:
        refresh_current_index(args.dry_run, log)

    moved = sum(1 for line in log if line.startswith("MOVED"))
    skipped = sum(1 for line in log if line.startswith("SKIP"))
    print(f"\nMigration {'dry-run' if args.dry_run else 'complete'}: moved={moved}, skipped={skipped}")
    for line in log:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())