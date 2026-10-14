#!/bin/bash
# ============================================
# Smart Financial News Agent - Daily Runner (/1)
# ============================================
# Wraps main.py with: an explicit interpreter, a dependency check, a lock so
# two runs never overlap, and exit-code aware logging + a macOS notification.
#
# Interpreter: $PYTHON_BIN, else /usr/local/bin/python3 (the environment with
# the project deps). Do NOT rely on a bare `python3` under cron/launchd: their
# PATH resolves /usr/bin/python3 (3.9, no google-genai) and /1 fails.
#
# Optional crontab entry (Mon-Fri 16:15, after institutional data release):
#   15 16 * * 1-5 /Users/shenghanchou/Developer/AntiGravity專案/stock-youtube/smart_financial_report/run_daily.sh
#
# Exit codes (modules/exit_codes.py): 0 ok, 2 data not ready (retry later),
# 3 data quality gate, 4 Gemini failed, 5 structured_data failed, 6 config,
# 7 before 16:10, 8 not a TWSE session (holiday), 75 another run holds the lock.
# ============================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/reports/archive/misc"
LOG_FILE="$LOG_DIR/run_$(date +%Y-%m-%d).log"
# Fixed lock path: cron has no TMPDIR while Terminal/launchd use a per-user
# /var/folders/... TMPDIR, so a TMPDIR-based lock would let a manual and a
# scheduled run overlap. RUN_DAILY_LOCK_DIR overrides it (tests).
LOCK_DIR="${RUN_DAILY_LOCK_DIR:-/tmp/stock-youtube-run-daily.lock}"

mkdir -p "$LOG_DIR"

log() {
    echo "$*" >> "$LOG_FILE"
}

notify() {
    # Best effort: a failed run should not stay silent when nobody reads the log.
    # RUN_DAILY_NOTIFY=0 disables it (tests / headless runs).
    [ "${RUN_DAILY_NOTIFY:-1}" = "0" ] && return 0
    command -v osascript >/dev/null 2>&1 || return 0
    osascript -e "display notification \"$1\" with title \"stock-youtube /1\"" >/dev/null 2>&1 || true
}

resolve_python() {
    local candidate
    for candidate in "${PYTHON_BIN:-}" /usr/local/bin/python3 "$(command -v python3 || true)"; do
        if [ -n "$candidate" ] && [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

acquire_lock() {
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        echo $$ > "$LOCK_DIR/pid"
        return 0
    fi
    local holder
    holder="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
        return 1
    fi
    # Stale lock left by a killed run.
    rm -rf "$LOCK_DIR"
    mkdir "$LOCK_DIR" 2>/dev/null || return 1
    echo $$ > "$LOCK_DIR/pid"
}

log "========================================"
log "執行時間: $(date '+%Y-%m-%d %H:%M:%S')"
log "========================================"

if ! acquire_lock; then
    log "另一個 /1 仍在執行（${LOCK_DIR}），略過本次。"
    exit 75
fi
trap 'rm -rf "$LOCK_DIR"' EXIT

if ! PYTHON="$(resolve_python)"; then
    log "❌ 找不到可用的 python3（可設定 PYTHON_BIN）"
    notify "找不到 python3，/1 未執行"
    exit 6
fi
if ! "$PYTHON" -c "import google.genai, yfinance, tavily, dotenv" >/dev/null 2>&1; then
    log "❌ ${PYTHON} 缺少相依套件（pip install -r requirements.txt -c constraints.txt）"
    notify "${PYTHON} 缺少相依套件，/1 未執行"
    exit 6
fi
log "Python: ${PYTHON}"

cd "$SCRIPT_DIR"
rc=0
"$PYTHON" main.py >> "$LOG_FILE" 2>&1 || rc=$?

meaning="$("$PYTHON" -c 'import sys; from modules.exit_codes import describe; print(describe(sys.argv[1]))' "$rc" 2>/dev/null || echo "未知")"
log "執行完成: 退出碼 ${rc}（${meaning}）"
if [ "$rc" -ne 0 ]; then
    notify "/1 退出碼 ${rc}：${meaning}"
fi
exit "$rc"
