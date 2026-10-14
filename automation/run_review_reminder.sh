#!/bin/bash
# launchd entry for the 09:05 review reminder (see automation/README.md).
# Paths are resolved from this file, so moving the repo does not break the job;
# only the plist (which launchd needs as absolute paths) has to be re-installed.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON_BIN:-/usr/local/bin/python3}"
if [ ! -x "$PYTHON" ]; then
    PYTHON="$(command -v python3)"
fi

cd "$REPO_DIR"
exec "$PYTHON" scripts/youtube/review_reminder.py --channel-id UCRWXWHHOs4iSDac1GHLsExw --max-results 25
