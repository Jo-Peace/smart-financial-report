"""Deprecated wrapper — use `python3 cli.py youtube check-youtube-package`."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from compat._redirect import run_legacy_wrapper

if __name__ == "__main__":
    run_legacy_wrapper("check_youtube_package.py")
