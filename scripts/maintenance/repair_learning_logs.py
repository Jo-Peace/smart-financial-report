#!/usr/bin/env python3
"""
One-time repair of the local learning logs (no Gemini, no network). Dry-run by default.

  python3 scripts/maintenance/repair_learning_logs.py                 # report only
  python3 scripts/maintenance/repair_learning_logs.py --apply         # write (daily backup first)
  python3 scripts/maintenance/repair_learning_logs.py --apply --rebuild-since 2026-10-01

What it does:
1. capital_rotation / macro_events / trading_strategies: drop AI-era duplicate dates
   (keep the last entry per date).
2. local_market_memory: same de-dup, then put backfilled sections back in date order.
3. channel_story_log: merge rows that share a date AND video (or a date without video).
4. --rebuild-since: re-write wiki + memory entries for dates ≥ DATE from the archived
   structured_data.json using today's verifiable-fields-only builders, which removes old
   Gemini narrative (鎖貨／吸籌／strategy advice) from the entries the analyzer reads.

The local /1 wiki update also runs a one-time version of step 4 automatically
(last 45 days, existing entries only; marker data/knowledge_wiki/.verifiable_rebuild_v1).
"""

from __future__ import annotations

import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.atomic_io import atomic_write_text, backup_file_daily  # noqa: E402
from modules.knowledge_manager import (  # noqa: E402
    dedupe_dated_blocks,
    sort_dated_blocks,
    upsert_dated_block,
)
from modules.pipeline_support import merge_duplicate_story_rows  # noqa: E402

WIKI_DIR = os.path.join(BASE_DIR, "data", "knowledge_wiki")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
CHRONICLES = ("capital_rotation.md", "macro_events.md", "trading_strategies.md")
MEMORY = "local_market_memory.md"


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _write(path, content, apply):
    if not apply:
        return
    backup_file_daily(path, backup_dir=os.path.join(BASE_DIR, "data", "backup"))
    atomic_write_text(path, content)


def _rebuilt_entries(since):
    """{filename: {date: entry}} rebuilt from archived structured_data (verifiable fields only)."""
    from modules.knowledge_manager import rebuilt_entries_since

    return rebuilt_entries_since(REPORTS_DIR, since)


def repair(apply=False, rebuild_since=None):
    report = []
    rebuilt = _rebuilt_entries(rebuild_since) if rebuild_since else {}
    for name in CHRONICLES + (MEMORY,):
        path = os.path.join(WIKI_DIR, name)
        content = _read(path)
        if not content:
            continue
        new, removed = dedupe_dated_blocks(content)
        moved = False
        if name == MEMORY:
            new, moved = sort_dated_blocks(new)
        replaced = 0
        for day, body in sorted((rebuilt.get(name) or {}).items()):
            new, action = upsert_dated_block(new, day, body, replace=True)
            replaced += action in {"replaced", "inserted", "appended"}
        if new != content:
            _write(path, new, apply)
        report.append(f"{name}: 去重 {removed}；{'已重排日期' if moved else '日期順序正常'}；重建 {replaced} 筆")

    from modules.report_paths import resolve_shared_artifact

    log_path = resolve_shared_artifact(REPORTS_DIR, "channel_story_log")
    content = _read(log_path)
    if content:
        new, removed = merge_duplicate_story_rows(content)
        if removed:
            _write(log_path, new, apply)
        report.append(f"channel_story_log.md: 合併重複列 {removed}")
    return report


def main():
    parser = argparse.ArgumentParser(description="一次性修復本機學習紀錄（去重、日期排序、只留可驗證欄位）")
    parser.add_argument("--apply", action="store_true", help="實際寫入（預設只報告）")
    parser.add_argument("--rebuild-since", default=None, help="從此日期起用 structured_data 重建 wiki/記憶條目（YYYY-MM-DD）")
    args = parser.parse_args()
    for line in repair(apply=args.apply, rebuild_since=args.rebuild_since):
        print(("✅ " if args.apply else "🔎 ") + line)
    if not args.apply:
        print("（dry-run：加上 --apply 才會寫入；寫入前自動備份到 data/backup/）")


if __name__ == "__main__":
    main()
