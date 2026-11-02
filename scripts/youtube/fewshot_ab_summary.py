"""
Cumulative few-shot vs baseline packaging results (local, read-only).

  python3 scripts/youtube/fewshot_ab_summary.py

Counts ab_test_results rows with hypothesis_id=FS; which arm was few-shot comes from
each day's reports/youtube/<date>/fewshot_ab.json (default b).
"""

import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.cover_ab_test import load_results  # noqa: E402
from modules.fewshot_bank import DEFAULT_REPORTS_DIR, read_day_meta, summarize_fewshot  # noqa: E402

MIN_DECIDED = 3


def format_summary(fs) -> list[str]:
    decided = fs["fewshot_wins"] + fs["baseline_wins"]
    if decided < MIN_DECIDED:
        verdict = f"樣本不足（有勝負 {decided}/{MIN_DECIDED}），先不下結論"
    elif fs["fewshot_wins"] * 2 > decided:
        verdict = "few-shot 過半勝出：可考慮升為預設（手動）"
    elif fs["baseline_wins"] * 2 > decided:
        verdict = "baseline 過半勝出：few-shot 沒有幫助"
    else:
        verdict = "平手，繼續測"
    return [
        f"few-shot vs baseline：n={fs['n']}，few-shot 勝 {fs['fewshot_wins']}／baseline 勝 {fs['baseline_wins']}"
        f"／無明顯差異 {fs['no_difference']} → {verdict}",
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description="few-shot vs baseline 累積勝率")
    parser.add_argument("--db", default="")
    parser.add_argument("--reports-dir", default=DEFAULT_REPORTS_DIR)
    args = parser.parse_args(argv)
    rows = load_results(args.db or None)
    metas = {r.get("run_date"): read_day_meta(args.reports_dir, r.get("run_date")) for r in rows}
    for line in format_summary(summarize_fewshot(rows, metas)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
