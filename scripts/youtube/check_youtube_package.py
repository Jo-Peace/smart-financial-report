"""
QA check for /2 YouTube packaging output.

Run after regen_assets.py to verify that the YouTube package and thumbnail
prompts contain the expected upload assets, date label, and safety wording.
"""
import argparse
import datetime as dt
import os
import re
import sys


BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
REPORTS_DIR = os.path.join(BASE_DIR, "reports")

from modules.report_paths import resolve_shared_artifact, resolve_youtube_artifact  # noqa: E402

CHANNEL_STORY_LOG = resolve_shared_artifact(REPORTS_DIR, "channel_story_log")

RISKY_IP_TERMS = [
    "Attack on Titan",
    "進擊的巨人",
    "進擊巨人",
    "Big Hero 6",
    "大英雄天團",
    "The Godfather",
    "Godfather",
    "教父",
    "The Great Gatsby",
    "Great Gatsby",
    "Gatsby",
    "TMNT",
    "Ninja Turtles",
    "忍者龜",
    "甄嬛傳",
    "甄嬛传",
]

SAFE_PHRASES = [
    "non-infringing",
    "Do not copy protected characters",
    "no real person likenesses",
    "no company logos",
    "no franchise logos",
    "no direct likenesses",
    "避免真實電影角色",
    "避免真實角色",
    "避免角色名稱",
    "避免 logo",
    "避免直接複製",
    "原創",
    "非侵權",
]


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def date_label(date_str):
    parsed = dt.datetime.strptime(date_str, "%Y-%m-%d")
    return f"{parsed.month}/{parsed.day} 台股盤後"


def status_line(status, message):
    print(f"[{status}] {message}")


def has_any(content, patterns):
    lowered = content.lower()
    return any(pattern.lower() in lowered for pattern in patterns)


def check_file_contains(path, label, checks, failures, warnings):
    content = read_text(path)
    if not content:
        failures.append(f"找不到 {label}: {path}")
        return ""

    status_line("PASS", f"找到 {label}: {os.path.relpath(path, BASE_DIR)}")
    for check_label, pattern, severity in checks:
        matched = re.search(pattern, content, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL)
        if matched:
            status_line("PASS", f"{label} 包含 {check_label}")
        elif severity == "fail":
            failures.append(f"{label} 缺少 {check_label}")
        else:
            warnings.append(f"{label} 可能缺少 {check_label}")
    return content


def main():
    parser = argparse.ArgumentParser(description="檢查 /2 YouTube package 與縮圖 prompt 是否完整")
    parser.add_argument("--date", default=dt.datetime.now().strftime("%Y-%m-%d"), help="YYYY-MM-DD")
    args = parser.parse_args()

    expected_label = date_label(args.date)
    package_path = resolve_youtube_artifact(REPORTS_DIR, args.date, "package")
    thumb_paths = [
        resolve_youtube_artifact(REPORTS_DIR, args.date, f"thumbnail_{index}")
        for index in (1, 2)
    ]

    failures = []
    warnings = []

    print(f"Checking /2 assets for {args.date}")
    print(f"Expected date label: {expected_label}\n")

    package = check_file_contains(
        package_path,
        "YouTube package",
        [
            ("標題 A/B", r"(標題|Title).{0,80}(A|B)|標題A|標題B", "warn"),
            ("說明欄/影片描述", r"說明欄|影片描述|Description|影片說明", "fail"),
            ("置頂留言", r"置頂留言|Pinned comment|Pinned Comment", "fail"),
            ("小日期標題", re.escape(expected_label), "fail"),
        ],
        failures,
        warnings,
    )

    for index, thumb_path in enumerate(thumb_paths, start=1):
        content = check_file_contains(
            thumb_path,
            f"縮圖 Prompt {index}",
            [
                ("小日期標題", re.escape(expected_label), "fail"),
                ("Image Prompt", r"## Image Prompt", "fail"),
                ("Thumbnail Headline Text", r"## Thumbnail Headline Text", "fail"),
            ],
            failures,
            warnings,
        )

        if content and has_any(content, RISKY_IP_TERMS) and not has_any(content, SAFE_PHRASES):
            warnings.append(f"縮圖 Prompt {index} 含 IP 風格詞，但缺少明確原創/避開角色/logo 的安全描述")

    if package and has_any(package, RISKY_IP_TERMS) and not has_any(package, SAFE_PHRASES):
        warnings.append("YouTube package 含 IP 風格詞，但缺少明確原創/避開角色/logo 的安全描述")

    channel_story = read_text(CHANNEL_STORY_LOG)
    if not channel_story:
        warnings.append("找不到 channel_story_log.md，無法確認視覺主題是否回寫")
    elif f"| {args.date} |" not in channel_story:
        warnings.append(f"channel_story_log.md 找不到 {args.date} 的紀錄")
    elif re.search(rf"^\| {re.escape(args.date)} \|.*待 /2 設定", channel_story, flags=re.MULTILINE):
        failures.append("channel_story_log.md 的視覺主題仍是「待 /2 設定」")
    else:
        status_line("PASS", "channel_story_log.md 已有當日紀錄，且視覺主題不是待設定")

    if warnings:
        print("\nWarnings:")
        for item in warnings:
            status_line("WARN", item)

    if failures:
        print("\nFailures:")
        for item in failures:
            status_line("FAIL", item)
        return 1

    print("\nPASS: /2 assets look complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
