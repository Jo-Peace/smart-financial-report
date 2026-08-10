# Review Reminder Automation

This keeps the review habit lightweight.

What runs automatically:

- Update public YouTube video IDs and view counts.
- Write `reports/06_YouTube_Assets/review_due.md` when `/4` or `/5` is due.

What does not run automatically:

- No strategy conclusion is written automatically.
- No playbook rule is changed automatically.
- `/4` and `/5` still need a human-triggered review with Codex.

Schedule:

- The launchd job runs daily at 09:05.
- `scripts/youtube/review_reminder.py` writes a `/4` reminder on Mondays.
- It also writes a `/5` reminder on the first Monday of each month.

Install:

```bash
mkdir -p "$HOME/Library/LaunchAgents"
cp automation/com.shenghan.stockyoutube.review-reminder.plist "$HOME/Library/LaunchAgents/"
launchctl unload "$HOME/Library/LaunchAgents/com.shenghan.stockyoutube.review-reminder.plist" 2>/dev/null || true
launchctl load "$HOME/Library/LaunchAgents/com.shenghan.stockyoutube.review-reminder.plist"
```

Manual test:

```bash
python3 scripts/youtube/review_reminder.py --force-reminder
```
