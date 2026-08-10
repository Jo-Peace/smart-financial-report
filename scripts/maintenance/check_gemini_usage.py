import argparse
import collections
import json
import os

from modules.ai_usage_guard import get_today_gemini_call_count, usage_log_path


def main():
    parser = argparse.ArgumentParser(description="查看 Gemini API 本機呼叫紀錄")
    parser.add_argument("--date", default="", help="指定日期 YYYY-MM-DD；預設列出全部摘要並顯示今日總數")
    args = parser.parse_args()

    path = usage_log_path()
    if not os.path.exists(path):
        print("尚無 Gemini 呼叫紀錄。")
        return

    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if args.date and row.get("date") != args.date:
                continue
            rows.append(row)

    if not rows:
        print(f"找不到符合日期的紀錄：{args.date}")
        return

    by_date = collections.Counter(row.get("date", "") for row in rows)
    by_task = collections.Counter(row.get("task", "") for row in rows)

    print(f"Log: {path}")
    print(f"今日 Gemini calls: {get_today_gemini_call_count()}")
    print("\n依日期：")
    for date, count in sorted(by_date.items()):
        print(f"- {date}: {count}")

    print("\n依任務：")
    for task, count in by_task.most_common():
        print(f"- {task}: {count}")

    print("\n最近 10 筆：")
    for row in rows[-10:]:
        print(
            f"- {row.get('date')} {row.get('time')} "
            f"{row.get('script')} {row.get('task')} "
            f"{row.get('model')} {row.get('status')} "
            f"prompt={row.get('prompt_chars')} response={row.get('response_chars')}"
        )


if __name__ == "__main__":
    main()
