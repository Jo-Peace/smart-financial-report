#!/bin/bash
set -euo pipefail

cd "/Users/shenghanchou/Desktop/AntiGravity專案/stock-youtube/smart_financial_report"
/usr/bin/python3 scripts/youtube/review_reminder.py --channel-id UCRWXWHHOs4iSDac1GHLsExw --max-results 25
