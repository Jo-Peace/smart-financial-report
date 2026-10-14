#!/usr/bin/env python3
"""
similar_sessions.py — 本機相似盤查詢（不消耗 Gemini）

對某一日的 structured_data（或最新一日）找出最多 5 個結構相近的歷史交易日（只看查詢日之前），
並列出當時預測與隔日節奏結果。供 /1 注入與人工核對。

Usage:
  python3 cli.py maintenance similar-sessions --date 2026-09-01
  python3 cli.py maintenance similar-sessions --date 2026-09-01 --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.report_paths import daily_dir  # noqa: E402
from modules.similar_sessions import (  # noqa: E402
    find_similar_for_structured,
    format_for_prompt,
    load_history,
    no_analog_reason,
)


def _load_day(reports_dir: str, date_str: str) -> dict:
    path = os.path.join(daily_dir(reports_dir, date_str), "structured_data.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"找不到 {path}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("structured_data 不是物件")
    data.setdefault("date", date_str)
    return data


def _latest_date(reports_dir: str) -> str | None:
    history = load_history(reports_dir)
    return history[-1]["date"] if history else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="本機相似盤查詢")
    parser.add_argument("--date", "-d", help="YYYY-MM-DD，預設最新有 structured_data 的日期")
    parser.add_argument("--json", action="store_true", help="輸出 JSON")
    args = parser.parse_args(argv)
    reports_dir = os.path.join(BASE_DIR, "reports")
    date_str = args.date or _latest_date(reports_dir)
    if not date_str:
        print("沒有可用的 structured_data", file=sys.stderr)
        return 1
    data = _load_day(reports_dir, date_str)
    diagnostics: dict = {}
    matches = find_similar_for_structured(data, reports_dir, exclude_date=date_str, diagnostics=diagnostics)
    payload = {"date": date_str, "matches": matches}
    if not matches:
        payload["no_analog"] = {"reason": no_analog_reason(diagnostics), **diagnostics}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"查詢日：{date_str}")
        print(format_for_prompt(matches, no_analog=no_analog_reason(diagnostics, with_date=False) if not matches else None))
        if not matches:
            # Human check only (the /1 prompt never gets the near-miss date).
            print(f"\n[診斷] {no_analog_reason(diagnostics)}")
            print(
                f"[診斷] 比對 {diagnostics.get('compared', 0)} 個歷史盤，其中 {diagnostics.get('comparable', 0)} 個大盤可比；"
                f"排除最近交易日 {', '.join(diagnostics.get('excluded_recent') or []) or '無'}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
