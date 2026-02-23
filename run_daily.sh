#!/bin/bash
# ============================================
# Smart Financial News Agent - Daily Runner
# ============================================
# Usage: Add to crontab for automatic daily execution
# Example crontab entry (runs Mon-Fri at 3:45 PM, after institutional data release):
#   45 15 * * 1-5 /Users/shenghanchou/Desktop/bug/smart_financial_report/run_daily.sh
#
# To set up:
#   1. chmod +x run_daily.sh
#   2. crontab -e
#   3. Add the line above
# ============================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$SCRIPT_DIR/reports/run_$(date +%Y-%m-%d).log"

# Ensure reports directory exists
mkdir -p "$SCRIPT_DIR/reports"

echo "========================================" >> "$LOG_FILE"
echo "執行時間: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"

cd "$SCRIPT_DIR"
python3 main.py >> "$LOG_FILE" 2>&1

echo "執行完成: 退出碼 $?" >> "$LOG_FILE"
