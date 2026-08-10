"""
Backfill video IDs and update public YouTube stats from a channel.

This is the smarter path after /2: the user only needs to provide the channel ID
once, and the script can match recent uploads to channel_story_log.md by title.
"""
import argparse
import os
import sys

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.youtube_analytics import update_log_from_channel, update_log_with_stats


from modules.report_paths import resolve_shared_artifact

DEFAULT_LOG = resolve_shared_artifact(os.path.join(BASE_DIR, "reports"), "channel_story_log")


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="從 YouTube 頻道回填影片 ID 並更新觀看數")
    parser.add_argument("--channel-id", default=os.getenv("YOUTUBE_CHANNEL_ID", ""), help="YouTube channel ID")
    parser.add_argument("--log-path", default=DEFAULT_LOG, help="channel_story_log.md path")
    parser.add_argument("--max-results", type=int, default=25, help="抓取近期影片數量")
    args = parser.parse_args()

    api_key = os.getenv("YOUTUBE_API_KEY", "")
    if not api_key:
        raise SystemExit("Missing YOUTUBE_API_KEY in .env")
    if not args.channel_id:
        raise SystemExit("Missing --channel-id or YOUTUBE_CHANNEL_ID in .env")

    print("Step 1: 從頻道近期影片回填缺少的影片 ID")
    update_log_from_channel(
        log_path=args.log_path,
        api_key=api_key,
        channel_id=args.channel_id,
        max_results=args.max_results,
    )

    print("\nStep 2: 用影片 ID 更新公開觀看數")
    update_log_with_stats(args.log_path, api_key, update_all=True)


if __name__ == "__main__":
    main()
