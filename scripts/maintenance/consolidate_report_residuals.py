"""
Consolidate legacy report residuals without losing compounding data.

Order of operations:
  1. Ensure canonical daily/youtube artifacts exist (copy from legacy only if missing)
  2. Reconcile analytics.db paths + backfill missing daily_runs from structured_data
  3. Optionally backfill local knowledge memory from historical structured_data
  4. Archive duplicate / stray legacy files (move only, never delete)
  5. Write inventory manifest under reports/shared/
"""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import re
import shutil
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.analytics_store import AnalyticsStore  # noqa: E402
from modules.report_paths import (  # noqa: E402
    DAILY_ARTIFACTS,
    LEGACY_DAILY_OUTPUTS_DIRNAME,
    LEGACY_YOUTUBE_DIRNAME,
    YOUTUBE_ARTIFACTS,
    daily_artifact_write_path,
    daily_dir,
    date_from_structured_path,
    iter_daily_report_paths,
    iter_structured_data_paths,
    resolve_daily_artifact,
    resolve_youtube_artifact,
    shared_dir,
    youtube_dir,
)

REPORTS_DIR = os.path.join(BASE_DIR, "reports")
ARCHIVE_LEGACY = os.path.join(REPORTS_DIR, "archive", "legacy")
ARCHIVE_LEGACY_YOUTUBE = os.path.join(ARCHIVE_LEGACY, LEGACY_YOUTUBE_DIRNAME)
ARCHIVE_LEGACY_DAILY_OUTPUTS = os.path.join(ARCHIVE_LEGACY, LEGACY_DAILY_OUTPUTS_DIRNAME)
ARCHIVE_LEGACY_THUMBNAILS = os.path.join(ARCHIVE_LEGACY, "thumbnail_images")
SPECIAL_TOPICS_DIR = os.path.join(REPORTS_DIR, "special_topics")
INVENTORY_PATH = os.path.join(shared_dir(REPORTS_DIR), "data_inventory.md")

DATE_YMD = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MEMORY_DATE_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2})", re.MULTILINE)
ARCHIVE_DAILY_DIR = os.path.join(REPORTS_DIR, "archive", "01_Daily_Reports")
ARCHIVE_PROMPT_DIR = os.path.join(REPORTS_DIR, "archive", "03_NotebookLM_Prompts")


def _log(log: list[str], message: str) -> None:
    log.append(message)


def _same_file(a: str, b: str) -> bool:
    if not os.path.exists(a) or not os.path.exists(b):
        return False
    try:
        return filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def _copy_if_missing(src: str, dst: str, *, dry_run: bool, log: list[str]) -> bool:
    if not os.path.exists(src):
        return False
    if os.path.exists(dst):
        return False
    rel_src = os.path.relpath(src, REPORTS_DIR)
    rel_dst = os.path.relpath(dst, REPORTS_DIR)
    if dry_run:
        _log(log, f"DRY-RUN fill canonical: {rel_src} -> {rel_dst}")
    else:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        _log(log, f"FILLED canonical: {rel_src} -> {rel_dst}")
    return True


def _archive_move(src: str, dst: str, *, dry_run: bool, log: list[str], reason: str) -> bool:
    if not os.path.exists(src):
        return False
    rel_src = os.path.relpath(src, REPORTS_DIR)
    rel_dst = os.path.relpath(dst, REPORTS_DIR)
    if os.path.exists(dst):
        if _same_file(src, dst):
            if dry_run:
                _log(log, f"DRY-RUN remove duplicate (same content): {rel_src}")
            else:
                os.remove(src)
                _log(log, f"REMOVED duplicate (archived copy exists): {rel_src}")
            return True
        _log(log, f"SKIP archive conflict: {rel_src} vs {rel_dst} ({reason})")
        return False
    if dry_run:
        _log(log, f"DRY-RUN archive [{reason}]: {rel_src} -> {rel_dst}")
    else:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        _log(log, f"ARCHIVED [{reason}]: {rel_src} -> {rel_dst}")
    return True


def promote_archive_to_canonical(dry_run: bool, log: list[str]) -> dict:
    """Copy historical archive bundles into daily/ when canonical is missing."""
    stats = {"promoted": 0}
    if not os.path.isdir(ARCHIVE_DAILY_DIR):
        return stats

    json_re = re.compile(r"^structured_data_(\d{8})\.json$")
    report_re = re.compile(r"^daily_report_V21_(\d{4}-\d{2}-\d{2})\.md$")
    source_re = re.compile(r"^notebooklm_source_(\d{8})\.md$")
    prompt_re = re.compile(r"^notebooklm_prompt_podcast_(\d{8})\.md$")

    archive_reports = {}
    for name in os.listdir(ARCHIVE_DAILY_DIR):
        match = report_re.match(name)
        if match:
            archive_reports[match.group(1)] = os.path.join(ARCHIVE_DAILY_DIR, name)

    for name in os.listdir(ARCHIVE_DAILY_DIR):
        match = json_re.match(name)
        if not match:
            continue
        ymd = match.group(1)
        date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        pairs = [
            (os.path.join(ARCHIVE_DAILY_DIR, name), daily_artifact_write_path(REPORTS_DIR, date_str, "structured_data")),
            (archive_reports.get(date_str), daily_artifact_write_path(REPORTS_DIR, date_str, "report")),
            (os.path.join(ARCHIVE_DAILY_DIR, f"notebooklm_source_{ymd}.md"), daily_artifact_write_path(REPORTS_DIR, date_str, "notebooklm_source")),
            (os.path.join(ARCHIVE_DAILY_DIR, f"notebooklm_prompt_podcast_{ymd}.md"), daily_artifact_write_path(REPORTS_DIR, date_str, "notebooklm_prompt")),
        ]
        for alt_prompt_dir in (ARCHIVE_PROMPT_DIR,):
            if os.path.isdir(alt_prompt_dir):
                pairs.append(
                    (os.path.join(alt_prompt_dir, f"notebooklm_prompt_podcast_{ymd}.md"), daily_artifact_write_path(REPORTS_DIR, date_str, "notebooklm_prompt"))
                )
        for src, dst in pairs:
            if not src or not os.path.exists(src):
                continue
            if _copy_if_missing(src, dst, dry_run=dry_run, log=log):
                stats["promoted"] += 1

    for name in os.listdir(ARCHIVE_DAILY_DIR):
        match = source_re.match(name)
        if not match:
            continue
        ymd = match.group(1)
        date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        src = os.path.join(ARCHIVE_DAILY_DIR, name)
        dst = daily_artifact_write_path(REPORTS_DIR, date_str, "notebooklm_source")
        if _copy_if_missing(src, dst, dry_run=dry_run, log=log):
            stats["promoted"] += 1

    for name in os.listdir(ARCHIVE_DAILY_DIR):
        match = prompt_re.match(name)
        if not match:
            continue
        ymd = match.group(1)
        date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        src = os.path.join(ARCHIVE_DAILY_DIR, name)
        dst = daily_artifact_write_path(REPORTS_DIR, date_str, "notebooklm_prompt")
        if _copy_if_missing(src, dst, dry_run=dry_run, log=log):
            stats["promoted"] += 1

    return stats


def ensure_canonical_from_legacy(dry_run: bool, log: list[str]) -> dict:
    stats = {"filled": 0, "already_complete": 0}
    legacy_outputs = os.path.join(REPORTS_DIR, LEGACY_DAILY_OUTPUTS_DIRNAME)
    if not os.path.isdir(legacy_outputs):
        return stats

    def _resolve_kind(date_str: str, name: str) -> str | None:
        ymd = date_str.replace("-", "")
        exact = {
            f"daily_report_V21_{date_str}.md": "report",
            f"structured_data_{ymd}.json": "structured_data",
            f"notebooklm_source_{ymd}.md": "notebooklm_source",
            f"notebooklm_prompt_podcast_{ymd}.md": "notebooklm_prompt",
            "daily_report.md": "report",
            "structured_data.json": "structured_data",
            "notebooklm_source.md": "notebooklm_source",
            "notebooklm_prompt.md": "notebooklm_prompt",
            f"youtube_package_{date_str}.md": "package",
            "youtube_package.md": "package",
            f"yt_thumbnail_prompt_{date_str}_1.md": "thumbnail_1",
            f"yt_thumbnail_prompt_{date_str}_2.md": "thumbnail_2",
            "yt_thumbnail_prompt_1.md": "thumbnail_1",
            "yt_thumbnail_prompt_2.md": "thumbnail_2",
        }
        return exact.get(name)

    for date_str in sorted(os.listdir(legacy_outputs)):
        day_dir = os.path.join(legacy_outputs, date_str)
        if not DATE_YMD.match(date_str) or not os.path.isdir(day_dir):
            continue
        for name in os.listdir(day_dir):
            src = os.path.join(day_dir, name)
            if not os.path.isfile(src):
                continue
            kind = _resolve_kind(date_str, name)
            if not kind:
                continue
            if kind in DAILY_ARTIFACTS:
                dst = daily_artifact_write_path(REPORTS_DIR, date_str, kind)
            else:
                dst = os.path.join(youtube_dir(REPORTS_DIR, date_str), YOUTUBE_ARTIFACTS[kind])
            if _copy_if_missing(src, dst, dry_run=dry_run, log=log):
                stats["filled"] += 1
            elif os.path.exists(dst):
                stats["already_complete"] += 1
    return stats


def reconcile_analytics(dry_run: bool, log: list[str]) -> dict:
    store = AnalyticsStore()
    stats = {"paths_updated": 0, "backfilled": 0, "skipped": 0}

    conn = store._connect()
    cur = conn.cursor()
    cur.execute("SELECT run_date, report_path, structured_data_path FROM daily_runs ORDER BY run_date")
    rows = cur.fetchall()
    conn.close()

    for row in rows:
        run_date = row["run_date"]
        new_report = resolve_daily_artifact(REPORTS_DIR, run_date, "report")
        new_json = resolve_daily_artifact(REPORTS_DIR, run_date, "structured_data")
        if not new_report and not new_json:
            stats["skipped"] += 1
            continue
        if row["report_path"] == new_report and row["structured_data_path"] == new_json:
            continue
        if dry_run:
            _log(log, f"DRY-RUN analytics path update {run_date}")
        else:
            conn = store._connect()
            conn.execute(
                """
                UPDATE daily_runs
                SET report_path = ?, structured_data_path = ?
                WHERE run_date = ?
                """,
                (new_report, new_json, run_date),
            )
            conn.commit()
            conn.close()
            _log(log, f"ANALYTICS path updated: {run_date}")
        stats["paths_updated"] += 1

    existing_dates = {row["run_date"] for row in rows}
    for json_path in iter_structured_data_paths(REPORTS_DIR):
        run_date = date_from_structured_path(json_path)
        if not run_date or run_date in existing_dates:
            continue
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                structured_data = json.load(f)
        except Exception as exc:
            _log(log, f"SKIP analytics backfill {json_path}: {exc}")
            stats["skipped"] += 1
            continue
        report_path = resolve_daily_artifact(REPORTS_DIR, run_date, "report")
        if dry_run:
            _log(log, f"DRY-RUN analytics backfill {run_date} from {os.path.basename(json_path)}")
        else:
            store.record_daily_run(
                run_date=run_date,
                structured_data=structured_data,
                report_path=report_path,
                structured_data_path=json_path,
            )
            _log(log, f"ANALYTICS backfilled: {run_date}")
        stats["backfilled"] += 1
        existing_dates.add(run_date)

    return stats


def backfill_local_memory(dry_run: bool, log: list[str]) -> dict:
    memory_path = os.path.join(BASE_DIR, "data", "knowledge_wiki", "local_market_memory.md")
    existing = set()
    if os.path.exists(memory_path):
        with open(memory_path, "r", encoding="utf-8") as f:
            existing = set(MEMORY_DATE_RE.findall(f.read()))

    stats = {"memory_backfilled": 0, "memory_skipped": 0}
    from modules.local_knowledge_compounder import LocalKnowledgeCompounder

    compounder = LocalKnowledgeCompounder(base_dir=BASE_DIR)
    for json_path in iter_structured_data_paths(REPORTS_DIR):
        run_date = date_from_structured_path(json_path)
        if not run_date or run_date in existing:
            stats["memory_skipped"] += 1
            continue
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                structured_data = json.load(f)
        except Exception as exc:
            _log(log, f"SKIP memory backfill {run_date}: {exc}")
            continue
        report_path = resolve_daily_artifact(REPORTS_DIR, run_date, "report")
        report_content = ""
        if report_path and os.path.exists(report_path):
            with open(report_path, "r", encoding="utf-8") as f:
                report_content = f.read()
        if dry_run:
            _log(log, f"DRY-RUN memory backfill {run_date}")
        else:
            compounder.update_from_structured_data(
                structured_data=structured_data,
                report_content=report_content,
                report_date=run_date,
            )
            _log(log, f"MEMORY backfilled: {run_date}")
        stats["memory_backfilled"] += 1
        existing.add(run_date)
    return stats


def _cleanup_stale_symlinks_in(dry_run: bool, log: list[str], directory: str) -> None:
    if not os.path.isdir(directory):
        return
    for root, _dirs, files in os.walk(directory):
        for name in files:
            path = os.path.join(root, name)
            if not os.path.islink(path):
                continue
            target = os.readlink(path)
            if not os.path.isabs(target):
                target = os.path.normpath(os.path.join(os.path.dirname(path), target))
            if os.path.exists(target):
                continue
            rel = os.path.relpath(path, REPORTS_DIR)
            if dry_run:
                _log(log, f"DRY-RUN remove stale symlink: {rel}")
            else:
                os.unlink(path)
                _log(log, f"REMOVED stale symlink: {rel}")


def archive_legacy_residuals(dry_run: bool, log: list[str]) -> dict:
    stats = {"archived": 0, "removed_dup": 0, "special_moved": 0}

    legacy_youtube = os.path.join(REPORTS_DIR, LEGACY_YOUTUBE_DIRNAME)
    if os.path.isdir(legacy_youtube):
        package_re = re.compile(r"^youtube_package_(\d{4}-\d{2}-\d{2})\.md$")
        thumb_re = re.compile(r"^yt_thumbnail_prompt_(\d{4}-\d{2}-\d{2})_(\d)\.md$")
        for name in os.listdir(legacy_youtube):
            src = os.path.join(legacy_youtube, name)
            if not os.path.isfile(src):
                continue
            if name.startswith("youtube_special_package_") or name.startswith("yt_thumbnail_prompt_special_"):
                dst = os.path.join(SPECIAL_TOPICS_DIR, name)
                if _archive_move(src, dst, dry_run=dry_run, log=log, reason="special_topic"):
                    stats["special_moved"] += 1
                continue
            if name.lower().endswith(".png"):
                dst = os.path.join(ARCHIVE_LEGACY_THUMBNAILS, name)
                if _archive_move(src, dst, dry_run=dry_run, log=log, reason="thumbnail_png"):
                    stats["archived"] += 1
                continue
            package_match = package_re.match(name)
            if package_match:
                date_str = package_match.group(1)
                canonical = resolve_youtube_artifact(REPORTS_DIR, date_str, "package")
                if canonical:
                    dst = os.path.join(ARCHIVE_LEGACY_YOUTUBE, name)
                    if _archive_move(src, dst, dry_run=dry_run, log=log, reason="duplicate_youtube_package"):
                        stats["archived"] += 1
                continue
            thumb_match = thumb_re.match(name)
            if thumb_match:
                date_str, index = thumb_match.groups()
                canonical = resolve_youtube_artifact(REPORTS_DIR, date_str, f"thumbnail_{index}")
                if canonical:
                    dst = os.path.join(ARCHIVE_LEGACY_YOUTUBE, name)
                    if _archive_move(src, dst, dry_run=dry_run, log=log, reason="duplicate_thumbnail_prompt"):
                        stats["archived"] += 1

    legacy_outputs = os.path.join(REPORTS_DIR, LEGACY_DAILY_OUTPUTS_DIRNAME)
    if os.path.isdir(legacy_outputs):
        for root, _dirs, files in os.walk(legacy_outputs):
            for name in files:
                src = os.path.join(root, name)
                rel = os.path.relpath(src, legacy_outputs)
                dst = os.path.join(ARCHIVE_LEGACY_DAILY_OUTPUTS, rel)
                if name in {"README.md"}:
                    continue
                if name in {"channel_story_log.md", "analytics_summary.md"}:
                    shared_target = os.path.join(shared_dir(REPORTS_DIR), name)
                    if os.path.exists(shared_target):
                        if _archive_move(src, dst, dry_run=dry_run, log=log, reason="stale_shared_copy"):
                            stats["archived"] += 1
                        continue
                _archive_move(src, dst, dry_run=dry_run, log=log, reason="daily_outputs_residual")

        for date_str in sorted(os.listdir(legacy_outputs)):
            day_dir = os.path.join(legacy_outputs, date_str)
            if os.path.isdir(day_dir) and not os.listdir(day_dir):
                if dry_run:
                    _log(log, f"DRY-RUN remove empty dir daily_outputs/{date_str}")
                else:
                    os.rmdir(day_dir)
                    _log(log, f"REMOVED empty dir daily_outputs/{date_str}")

    _cleanup_stale_symlinks_in(dry_run, log, os.path.join(REPORTS_DIR, LEGACY_DAILY_OUTPUTS_DIRNAME))
    return stats


def build_inventory(log: list[str]) -> dict:
    structured_paths = iter_structured_data_paths(REPORTS_DIR)
    report_paths = iter_daily_report_paths(REPORTS_DIR)
    dates = sorted({date_from_structured_path(p) for p in structured_paths if date_from_structured_path(p)})

    store = AnalyticsStore()
    summary = store.get_summary()

    memory_path = os.path.join(BASE_DIR, "data", "knowledge_wiki", "local_market_memory.md")
    memory_dates = []
    if os.path.exists(memory_path):
        with open(memory_path, "r", encoding="utf-8") as f:
            memory_dates = MEMORY_DATE_RE.findall(f.read())

    conn = store._connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM daily_runs WHERE report_path LIKE '%/daily/%'")
    canonical_path_rows = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM daily_runs")
    total_rows = cur.fetchone()[0]
    conn.close()

    legacy_youtube_left = 0
    legacy_yt_dir = os.path.join(REPORTS_DIR, LEGACY_YOUTUBE_DIRNAME)
    if os.path.isdir(legacy_yt_dir):
        legacy_youtube_left = len([n for n in os.listdir(legacy_yt_dir) if os.path.isfile(os.path.join(legacy_yt_dir, n))])

    inventory = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "structured_data_files": len(structured_paths),
        "daily_report_files": len(report_paths),
        "canonical_daily_dates": len(dates),
        "analytics_daily_runs": summary["daily_runs"],
        "analytics_canonical_paths": canonical_path_rows,
        "analytics_total_paths": total_rows,
        "local_memory_dates": len(memory_dates),
        "legacy_youtube_files_remaining": legacy_youtube_left,
        "date_range": (dates[0], dates[-1]) if dates else ("", ""),
    }
    return inventory


def write_inventory(inventory: dict, log: list[str], *, dry_run: bool) -> None:
    start, end = inventory["date_range"]
    lines = [
        "# 資料歸納盤點（Data Inventory）",
        "",
        f"更新時間：{inventory['generated_at']}",
        "",
        "這份檔案記錄 reports 歸檔後，資訊複利相關資料是否完整。",
        "",
        "## 盤點摘要",
        "",
        f"- structured_data 檔案數：{inventory['structured_data_files']}",
        f"- daily report 檔案數：{inventory['daily_report_files']}",
        f"- 可回溯日期數（依 structured_data）：{inventory['canonical_daily_dates']}",
        f"- 日期範圍：{start or 'N/A'} ~ {end or 'N/A'}",
        f"- analytics.db daily_runs：{inventory['analytics_daily_runs']}",
        f"- analytics 已指向 canonical daily/ 路徑：{inventory['analytics_canonical_paths']} / {inventory['analytics_total_paths']}",
        f"- local_market_memory 日期數：{inventory['local_memory_dates']}",
        f"- 06_YouTube_Assets 殘留檔案數：{inventory['legacy_youtube_files_remaining']}",
        "",
        "## 複利資料來源（請優先維護這些）",
        "",
        "| 系統 | 路徑 | 用途 |",
        "|------|------|------|",
        "| 日報結構化 | `reports/daily/YYYY-MM-DD/structured_data.json` | 預測、驗收、/2 包裝 |",
        "| 本機 analytics | `data/analytics.db` | 命中率、歷史預測追蹤 |",
        "| 市場記憶 | `data/knowledge_wiki/local_market_memory.md` | 跨日敘事與脈絡 |",
        "| 個股角色 | `data/stock_db.json` + `stock_role_taxonomy.md` | 包裝角色判斷 |",
        "| 頻道敘事 | `reports/shared/channel_story_log.md` | 標題/封面疲勞、成效 |",
        "",
        "## 歸檔原則",
        "",
        "- 正式讀取一律走 `daily/`、`youtube/`、`shared/`",
        "- 重複的舊檔移到 `archive/legacy/`，不刪除",
        "- 專題素材留在 `special_topics/`",
        "",
        "## 最近一次整理動作",
        "",
    ]
    if log:
        lines.extend([f"- {line}" for line in log[-30:]])
    else:
        lines.append("- （無）")

    content = "\n".join(lines) + "\n"
    if dry_run:
        return
    os.makedirs(os.path.dirname(INVENTORY_PATH), exist_ok=True)
    with open(INVENTORY_PATH, "w", encoding="utf-8") as f:
        f.write(content)


def run_consolidate(
    *,
    dry_run: bool = False,
    skip_memory: bool = False,
    skip_analytics: bool = False,
    verbose: bool = True,
) -> dict:
    """Run all consolidation phases; safe to call from main.py or cli."""
    log: list[str] = []

    if verbose:
        print("Phase 0: promote archive history into daily/ (for compounding)")
    promote_stats = promote_archive_to_canonical(dry_run, log)
    if verbose:
        print(f"  promoted={promote_stats['promoted']}")

    if verbose:
        print("Phase 1: ensure canonical coverage from legacy bundles")
    ensure_stats = ensure_canonical_from_legacy(dry_run, log)
    if verbose:
        print(f"  filled={ensure_stats['filled']} already_complete={ensure_stats['already_complete']}")

    if skip_analytics:
        analytics_stats = {}
    else:
        if verbose:
            print("Phase 2: reconcile analytics.db paths and backfill missing runs")
        analytics_stats = reconcile_analytics(dry_run, log)
        if verbose:
            print(f"  paths_updated={analytics_stats['paths_updated']} backfilled={analytics_stats['backfilled']}")

    if skip_memory:
        memory_stats = {}
    else:
        if verbose:
            print("Phase 3: backfill local_market_memory from structured_data")
        memory_stats = backfill_local_memory(dry_run, log)
        if verbose:
            print(f"  memory_backfilled={memory_stats['memory_backfilled']} skipped={memory_stats['memory_skipped']}")

    if verbose:
        print("Phase 4: archive duplicate legacy residuals")
    archive_stats = archive_legacy_residuals(dry_run, log)
    if verbose:
        print(f"  archived={archive_stats['archived']} special_moved={archive_stats['special_moved']}")

    inventory = build_inventory(log)
    write_inventory(inventory, log, dry_run=dry_run)
    if verbose and not dry_run:
        print(f"Phase 5: inventory written to {os.path.relpath(INVENTORY_PATH, BASE_DIR)}")

    summary = {
        **promote_stats,
        **ensure_stats,
        **analytics_stats,
        **memory_stats,
        **archive_stats,
        **inventory,
        "log": log,
    }
    if verbose:
        print("\nSummary")
        print(json.dumps({k: v for k, v in summary.items() if k != "log"}, ensure_ascii=False, indent=2))
        for line in log:
            print(f"  {line}")
    return summary


def main():
    parser = argparse.ArgumentParser(description="Consolidate legacy report residuals while preserving data compounding.")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions without writing.")
    parser.add_argument("--skip-memory", action="store_true", help="Skip local_market_memory backfill.")
    parser.add_argument("--skip-analytics", action="store_true", help="Skip analytics reconcile/backfill.")
    args = parser.parse_args()

    run_consolidate(
        dry_run=args.dry_run,
        skip_memory=args.skip_memory,
        skip_analytics=args.skip_analytics,
        verbose=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())