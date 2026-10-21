"""
Automated review reminder for the stock YouTube workflow.

This script is intentionally conservative:
- It may update public YouTube stats.
- It may write a reminder file.
- It does not change strategy/playbook conclusions by itself.
"""
import argparse
import datetime as dt
import os
import subprocess
import sys


BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE_DIR)
from modules.report_paths import ensure_shared_dir, shared_dir  # noqa: E402

REPORTS_DIR = os.path.join(BASE_DIR, "reports")
REMINDER_PATH = os.path.join(shared_dir(REPORTS_DIR), "review_due.md")
DEFAULT_CHANNEL_ID = "UCRWXWHHOs4iSDac1GHLsExw"


def _is_first_monday(today):
    return today.weekday() == 0 and 1 <= today.day <= 7


def _run_youtube_update(channel_id, max_results):
    cmd = [
        sys.executable,
        os.path.join(BASE_DIR, "scripts", "youtube", "update_youtube_from_channel.py"),
        "--channel-id",
        channel_id,
        "--max-results",
        str(max_results),
    ]
    return subprocess.run(
        cmd,
        cwd=BASE_DIR,
        text=True,
        capture_output=True,
        check=False,
    )


def _write_reminder(today, update_result, force=False):
    weekly_due = force or today.weekday() == 0
    monthly_due = force or _is_first_monday(today)

    if not weekly_due and not monthly_due:
        return False

    lines = [
        f"# Review Due - {today.isoformat()}",
        "",
        "Automated data update finished. Strategy changes were not made automatically.",
        "",
        "## Suggested Commands",
        "",
    ]

    if weekly_due:
        lines.append("- Run `/4` for weekly content review.")
    if monthly_due:
        lines.append("- Run `/5` for monthly experiment review.")

    lines.extend(
        [
            "",
            "## YouTube Update Summary",
            "",
            "```text",
            (update_result.stdout or "").strip() or "(no stdout)",
            "",
        ]
    )
    if update_result.stderr:
        lines.extend(["stderr:", update_result.stderr.strip(), ""])
    lines.extend(["```", ""])

    ensure_shared_dir(REPORTS_DIR)
    with open(REMINDER_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return True


def main():
    parser = argparse.ArgumentParser(description="Update YouTube stats and write /4 / /5 reminders when due.")
    parser.add_argument("--channel-id", default=os.getenv("YOUTUBE_CHANNEL_ID", DEFAULT_CHANNEL_ID))
    parser.add_argument("--max-results", type=int, default=25)
    parser.add_argument("--date", default="", help="Override date for testing, YYYY-MM-DD")
    parser.add_argument("--force-reminder", action="store_true", help="Write reminder regardless of weekday")
    args = parser.parse_args()

    today = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    update_result = _run_youtube_update(args.channel_id, args.max_results)
    reminder_written = _write_reminder(today, update_result, force=args.force_reminder)

    print(f"YouTube update exit code: {update_result.returncode}")
    if reminder_written:
        print(f"Reminder written: {REMINDER_PATH}")
    else:
        print("No review reminder due today.")

    return update_result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
