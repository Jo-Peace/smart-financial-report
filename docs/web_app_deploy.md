# Web app (AI 個股研究員 MVP Beta) — Render deploy notes

- Service: `ai-stock-researcher` (Render **free** plan), blueprint `render.yaml`, auto-deploys from `main`.
- URL: https://smart-financial-report.onrender.com — health: `/healthz` (GET/HEAD; `info.commit` shows the deployed git commit).
- Quotas: 3 new reports per IP per day, 20 per site per day (`app/database.py`); cached reports are free.

## Cold start
Free instances sleep after 15 min without traffic; the first request takes ~30–90 s.
The front page shows a "伺服器喚醒中" note when `/api/quota` is slow and retries automatically.

## Optional keep-alive (free)
A free external monitor (e.g. UptimeRobot / cron-job.org) hitting
`https://smart-financial-report.onrender.com/healthz` every 10–14 min keeps the instance warm.
One always-on free service uses ~744 h/month, within Render's 750 free instance hours —
but only if no other free service in the workspace uses hours. `/healthz` does not call
Gemini or Tavily, so pinging costs no API quota. Note: SQLite is ephemeral on free plan
(cache/quotas reset on each deploy/restart), so keeping warm also preserves the day's cache.

## Client IP (quotas)
`LOG_CLIENT_IP_HEADERS=1` logs one `client-ip headers:` line per process start. Check it in
Render Logs, confirm `resolved=` is your real public IP, adjust `TRUSTED_PROXY_HOPS` /
`CLIENT_IP_HEADER` if not, then set `LOG_CLIENT_IP_HEADERS=0`.
