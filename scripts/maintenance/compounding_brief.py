#!/usr/bin/env python3
"""Regenerate reports/shared/weekly_compounding_brief.md"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from modules.compounding_loop import (  # noqa: E402
    DEFAULT_BRIEF,
    DEFAULT_LOG,
    write_weekly_compounding_brief,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="產生複利閉環簡報 weekly_compounding_brief.md")
    parser.add_argument("--log-path", default=str(DEFAULT_LOG))
    parser.add_argument("--out", default=str(DEFAULT_BRIEF))
    args = parser.parse_args(argv)
    path = write_weekly_compounding_brief(log_path=args.log_path, out_path=args.out)
    print(f"✅ 已寫入 {os.path.relpath(path, BASE_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
