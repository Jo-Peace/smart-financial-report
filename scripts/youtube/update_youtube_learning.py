import argparse
import os
import sys


BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.youtube_learning import update_youtube_performance_wiki


from modules.report_paths import resolve_shared_artifact

DEFAULT_LOG = resolve_shared_artifact(os.path.join(BASE_DIR, "reports"), "channel_story_log")
DEFAULT_OUTPUT = os.path.join(BASE_DIR, "data", "knowledge_wiki", "youtube_performance_patterns.md")


def main():
    parser = argparse.ArgumentParser(description="從 channel_story_log.md 產生 YouTube 成效學習摘要")
    parser.add_argument("--log-path", default=DEFAULT_LOG)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    output = update_youtube_performance_wiki(args.log_path, args.output)
    print(f"YouTube performance wiki updated: {output}")


if __name__ == "__main__":
    main()
