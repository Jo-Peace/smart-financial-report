"""
Run consolidate-residuals automatically on the first /1 of each month.
"""

from __future__ import annotations

import datetime
import json
import os

from modules.report_paths import ensure_shared_dir, shared_dir

STATE_FILENAME = "monthly_consolidate_state.json"


def _state_path(reports_dir: str) -> str:
    return os.path.join(shared_dir(reports_dir), STATE_FILENAME)


def _read_state(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_state(reports_dir: str, month_key: str, now: datetime.datetime) -> None:
    ensure_shared_dir(reports_dir)
    path = _state_path(reports_dir)
    payload = {
        "last_month": month_key,
        "last_run_at": now.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _skip_enabled() -> bool:
    return os.getenv("SKIP_MONTHLY_CONSOLIDATE", "").strip().lower() in {"1", "true", "yes", "y"}


def maybe_run_before_daily_main(reports_dir: str, *, now: datetime.datetime | None = None) -> dict:
    """
    On the first successful /1 entry each month, run consolidate-residuals first.

    Uses --skip-memory by default to keep monthly housekeeping fast; /1 already
    appends market memory for the current day.
    """
    now = now or datetime.datetime.now()
    month_key = now.strftime("%Y-%m")
    state_path = _state_path(reports_dir)

    if _skip_enabled():
        return {"ran": False, "reason": "SKIP_MONTHLY_CONSOLIDATE"}

    state = _read_state(state_path)
    if state.get("last_month") == month_key:
        return {"ran": False, "reason": "already_ran_this_month", "month": month_key}

    print("\n🗂️  本月首次 /1：先整理 reports 殘留並對齊複利資料...")
    try:
        from scripts.maintenance.consolidate_report_residuals import run_consolidate

        stats = run_consolidate(skip_memory=True, dry_run=False, verbose=True)
        _write_state(reports_dir, month_key, now)
        print("  ✅ 本月歸納整理完成，接著執行 /1\n")
        return {"ran": True, "month": month_key, "stats": {k: v for k, v in stats.items() if k != "log"}}
    except Exception as exc:
        print(f"  ⚠️  本月歸納整理失敗，/1 仍會繼續：{exc}\n")
        return {"ran": False, "month": month_key, "error": str(exc)}