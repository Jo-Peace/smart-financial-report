"""
Build the private few-shot title/cover bank (data/fewshot_title_bank.json, chmod 600).

  python3 scripts/youtube/build_fewshot_bank.py [--per-regime 5]

Local only (reads data/analytics.db + reports/youtube/*/yt_thumbnail_prompt_1.md).
"""

import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.fewshot_bank import DEFAULT_BANK_PATH, PER_REGIME, build_bank, save_private_json  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="建立 few-shot 標題範例庫（私有）")
    parser.add_argument("--per-regime", type=int, default=PER_REGIME)
    parser.add_argument("--db", default="")
    parser.add_argument("--out", default=DEFAULT_BANK_PATH)
    args = parser.parse_args(argv)
    bank = build_bank(db_path=args.db or None, per_regime=args.per_regime)
    save_private_json(args.out, bank)
    print(f"✅ few-shot bank：{args.out}（metric={bank['metric']}，候選 {bank['videos_considered']} 支）")
    for regime, items in bank["regimes"].items():
        print(f"   {regime}：{len(items)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
