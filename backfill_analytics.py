import argparse
import glob
import json
import os

from modules.analytics_store import AnalyticsStore


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(BASE_DIR, "reports")


def _date_from_structured_path(path):
    basename = os.path.basename(path)
    value = basename.replace("structured_data_", "").replace(".json", "")
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return None


def _report_path_for_date(run_date):
    candidate = os.path.join(REPORTS_DIR, f"daily_report_V21_{run_date}.md")
    return candidate if os.path.exists(candidate) else None


def backfill(pattern):
    store = AnalyticsStore()
    paths = sorted(glob.glob(pattern))

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
        default=os.path.join(REPORTS_DIR, "structured_data_*.json"),
        help="Glob pattern for structured data files.",
    )
    args = parser.parse_args()
    backfill(args.pattern)


if __name__ == "__main__":
    main()
