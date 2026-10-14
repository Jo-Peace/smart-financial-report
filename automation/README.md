# Review Reminder Automation

This keeps the review habit lightweight.

What runs automatically:

- Update public YouTube video IDs and view counts.
- Write `reports/shared/review_due.md` when `/4` or `/5` is due.

What does not run automatically:

- No strategy conclusion is written automatically.
- No playbook rule is changed automatically.
- `/4` and `/5` still need a human-triggered review with Codex.

Schedule:

- The launchd job runs daily at 09:05.
- `scripts/youtube/review_reminder.py` writes a `/4` reminder on Mondays.
- It also writes a `/5` reminder on the first Monday of each month.

Interpreter:

- `run_review_reminder.sh` uses `$PYTHON_BIN`, else `/usr/local/bin/python3`
  (the environment with the project deps). `/usr/bin/python3` (3.9) has no
  `google-genai` and must not be used.
- The script resolves the repo from its own location; only the plist carries
  absolute paths (launchd requires them).

Install / re-install (needed after the repo moved from `~/Desktop` to
`~/Developer`; the old job exits with status 78 because its path is gone):

```bash
PLIST="$HOME/Library/LaunchAgents/com.shenghan.stockyoutube.review-reminder.plist"
mkdir -p "$HOME/Library/LaunchAgents"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
# Rewrite the plist paths if the repo is not at the default location:
sed "s#/Users/shenghanchou/Developer/AntiGravity專案/stock-youtube/smart_financial_report#$(pwd)#g" \
  automation/com.shenghan.stockyoutube.review-reminder.plist > "$PLIST"
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl list | grep stockyoutube   # second column is the last exit status (0 = ok)
```

Run the commands from the repo root (`smart_financial_report/`).

Manual test:

```bash
/usr/local/bin/python3 scripts/youtube/review_reminder.py --force-reminder
```

Logs: `/tmp/stock-youtube-review-reminder.{out,err}.log`.
