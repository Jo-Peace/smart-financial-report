"""
Daily workflow smoke check.

Read-only checks for /1 and /2 artifacts:
- reports/CURRENT.md points to existing files
- /1 daily report, structured data, NotebookLM source/prompt exist
- NotebookLM prompt stays compact enough for Audio Overview
- /2 YouTube package and two thumbnail prompts exist when requested
"""

import argparse
import datetime
import os
import re
import sys


BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
REPORTS_DIR = os.path.join(BASE_DIR, "reports")

from modules.report_paths import resolve_daily_artifact, resolve_youtube_artifact  # noqa: E402


def _today():
    return datetime.datetime.now().strftime("%Y-%m-%d")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _exists(path):
    return os.path.exists(path)


def _check_file(label, path, errors, warnings):
    if path and _exists(path):
        print(f"[PASS] {label}: {os.path.relpath(path, BASE_DIR)}")
        return True
    rel = os.path.relpath(path, BASE_DIR) if path else f"(unresolved {label})"
    errors.append(f"{label} missing: {rel}")
    print(f"[FAIL] {label}: {rel}")
    return False


def _current_links(current_path):
    if not _exists(current_path):
        return []
    content = _read(current_path)
    return re.findall(r"\]\((\.\/[^)]+)\)", content)


def check_current(date_str, errors, warnings):
    current_path = os.path.join(REPORTS_DIR, "CURRENT.md")
    if not _check_file("CURRENT.md", current_path, errors, warnings):
        return

    content = _read(current_path)
    if f"更新日期：{date_str}" in content:
        print(f"[PASS] CURRENT date is {date_str}")
    else:
        warnings.append(f"CURRENT.md date is not {date_str}")
        print(f"[WARN] CURRENT date is not {date_str}")

    missing_links = []
    for link in _current_links(current_path):
        rel = link[2:]
        target = os.path.normpath(os.path.join(REPORTS_DIR, rel))
        if not _exists(target):
            missing_links.append(rel)
    if missing_links:
        for rel in missing_links:
            errors.append(f"CURRENT link target missing: {rel}")
            print(f"[FAIL] CURRENT link target missing: {rel}")
    else:
        print("[PASS] CURRENT links point to existing files")


def check_daily_outputs(date_str, errors, warnings):
    paths = [
        ("daily report", resolve_daily_artifact(REPORTS_DIR, date_str, "report")),
        ("structured data", resolve_daily_artifact(REPORTS_DIR, date_str, "structured_data")),
        ("NotebookLM source", resolve_daily_artifact(REPORTS_DIR, date_str, "notebooklm_source")),
        ("NotebookLM prompt", resolve_daily_artifact(REPORTS_DIR, date_str, "notebooklm_prompt")),
    ]
    for label, path in paths:
        _check_file(label, path, errors, warnings)

    prompt_path = resolve_daily_artifact(REPORTS_DIR, date_str, "notebooklm_prompt")
    if _exists(prompt_path):
        content = _read(prompt_path)
        byte_len = len(content.encode("utf-8"))
        if byte_len <= 4500:
            print(f"[PASS] NotebookLM prompt is compact: {byte_len} bytes")
        else:
            warnings.append(f"NotebookLM prompt may be too long: {byte_len} bytes")
            print(f"[WARN] NotebookLM prompt may be too long: {byte_len} bytes")

        blocked_patterns = ["必須逐一念出", "B：「", "A：「在今天的分析開始前"]
        found = [pattern for pattern in blocked_patterns if pattern in content]
        if found:
            errors.append(f"NotebookLM prompt contains over-scripted patterns: {', '.join(found)}")
            print(f"[FAIL] NotebookLM prompt contains over-scripted patterns: {', '.join(found)}")
        else:
            print("[PASS] NotebookLM prompt avoids over-scripted host lines")


def check_youtube_outputs(date_str, errors, warnings):
    paths = [
        ("YouTube package", resolve_youtube_artifact(REPORTS_DIR, date_str, "package")),
        ("thumbnail prompt A", resolve_youtube_artifact(REPORTS_DIR, date_str, "thumbnail_1")),
        ("thumbnail prompt B", resolve_youtube_artifact(REPORTS_DIR, date_str, "thumbnail_2")),
    ]
    for label, path in paths:
        _check_file(label, path, errors, warnings)

    package_path = resolve_youtube_artifact(REPORTS_DIR, date_str, "package")
    if _exists(package_path):
        package = _read(package_path)
        required = ["## 📝 A/B 標題選項", "## 📄 影片描述", "## 💬 置頂留言"]
        missing = [item for item in required if item not in package]
        if missing:
            errors.append(f"YouTube package missing sections: {', '.join(missing)}")
            print(f"[FAIL] YouTube package missing sections: {', '.join(missing)}")
        else:
            print("[PASS] YouTube package contains key sections")


def main():
    parser = argparse.ArgumentParser(description="Check daily /1 and /2 output health.")
    parser.add_argument("--date", default=_today(), help="Date in YYYY-MM-DD format")
    parser.add_argument("--skip-youtube", action="store_true", help="Only check /1 outputs")
    args = parser.parse_args()

    errors = []
    warnings = []
    print(f"Checking daily workflow for {args.date}")
    print(f"Project: {BASE_DIR}\n")

    check_current(args.date, errors, warnings)
    print("")
    check_daily_outputs(args.date, errors, warnings)
    if not args.skip_youtube:
        print("")
        check_youtube_outputs(args.date, errors, warnings)

    print("")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")
    if errors:
        print("Errors:")
        for error in errors:
            print(f"- {error}")
        print("\nFAIL: daily workflow needs attention.")
        return 1

    print("PASS: daily workflow looks healthy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
