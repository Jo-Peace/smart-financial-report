import argparse
import glob
import json
import os

from modules.analytics_store import AnalyticsStore
from modules.report_paths import iter_structured_data_paths, resolve_daily_artifact


BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS_DIR = os.path.join(BASE_DIR, "reports")


def _date_from_structured_path(path):
    from modules.report_paths import date_from_structured_path

    return date_from_structured_path(path)


def _report_path_for_date(run_date):
    return resolve_daily_artifact(REPORTS_DIR, run_date, "report")


def _one_path_per_date(paths):
    """
    record_daily_run mirrors a date's prediction_targets (key: date + ticker),
    so feed exactly one JSON per date: the final reports/daily/<date>/ copy
    when it exists, otherwise the last legacy copy.
    """
    chosen = {}
    undated = []
    daily_marker = os.sep + "daily" + os.sep
    for path in sorted(paths):
        run_date = _date_from_structured_path(path)
        if not run_date:
            undated.append(path)
            continue
        current = chosen.get(run_date)
        if current and daily_marker in current and daily_marker not in path:
            continue
        chosen[run_date] = path
    return undated + [chosen[d] for d in sorted(chosen)]


def backfill(paths):
    store = AnalyticsStore()
    paths = _one_path_per_date(paths)

    imported = 0
    skipped = 0
    for path in paths:
        run_date = _date_from_structured_path(path)
        if not run_date:
            skipped += 1
            print(f"  ⚠️  無法從檔名判斷日期，略過: {path}")
            continue

        try:
            with open(path, "r", encoding="utf-8") as f:
                structured_data = json.load(f)
        except Exception as e:
            skipped += 1
            print(f"  ⚠️  無法讀取 JSON，略過 {path}: {e}")
            continue

        store.record_daily_run(
            run_date=run_date,
            structured_data=structured_data,
            report_path=_report_path_for_date(run_date),
            structured_data_path=path,
        )
        imported += 1
        print(f"  ✅ 已匯入 {run_date}: {os.path.basename(path)}")

    summary = store.get_summary()
    print("\nAnalytics backfill complete.")
    print(f"  Imported: {imported}")
    print(f"  Skipped: {skipped}")
    print(f"  DB: {summary['db_path']}")
    print(f"  Daily runs: {summary['daily_runs']}")
    print(f"  Predictions: {summary['predictions']}")
    print(f"  Evaluated results: {summary['evaluated_results']}")
    print(f"  Hit rate: {summary['hit_rate'] if summary['hit_rate'] is not None else 'N/A'}")


def main():
    parser = argparse.ArgumentParser(
        description="Backfill local analytics.db from existing structured_data_YYYYMMDD.json files."
    )
    parser.add_argument(
        "--pattern",
        default="all",
        help="Glob pattern for structured data files.",
    )
    args = parser.parse_args()
    if args.pattern == "all":
        backfill(iter_structured_data_paths(REPORTS_DIR))
    else:
        backfill(sorted(glob.glob(args.pattern)))


if __name__ == "__main__":
    main()
