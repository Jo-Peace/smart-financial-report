# Environment flags

Template: `.env.example`. `/1` prints every active non-default or risky flag at
start-up (`modules/runtime_flags.py`), so a stale `.env` entry is visible in the
run log instead of silently changing behaviour.

Risk: **high** = can let wrong data reach the published report; **cost** =
spends API quota; **info** = behaviour/format only.

## /1 daily pipeline

| Flag | Default | Read in | Risk | Meaning |
|---|---|---|---|---|
| `GEMINI_API_KEY` | – | pipeline_context, scripts | – | Required. |
| `TAVILY_API_KEY` | – | pipeline_context, app | – | Required. |
| `YOUTUBE_API_KEY` / `YOUTUBE_CHANNEL_ID` | – / channel id | main, youtube scripts | info | Optional view-count update. |
| `DATA_QUALITY_GATE` (alias `DATA_QUALITY_GATE_MODE`) | `strict` | data_quality_gate | **high** | `warn` only prints failures; `off` skips the gate entirely; any other value acts as `strict` (the banner flags it as a typo). |
| `ALLOW_DATA_QUALITY_FAIL` | off | data_quality_gate | **high** | `1` / `true` / `yes` / `y` continues /1 even when the gate fails (prints a warning); `on` is not accepted. |
| `ALLOW_EARLY_RUN` (alias `FORCE_RUN_BEFORE_1610`) | off | pipeline_context | **high** | `1` / `true` / `yes` / `y`: run before 16:10 when TWSE data may be incomplete (`on` is not accepted). |
| `VALIDATION_MAX_TRADING_GAP` | `1` | pipeline_support | **high** | Max trading days between runs for「昨日/上集驗收」. |
| `GEMINI_DAILY_CALL_LIMIT` | `8` | ai_usage_guard | cost | Daily Gemini call ceiling; `0` disables it. |
| `GEMINI_MODEL` | `gemini-2.5-flash` (pinned `STABLE_DEFAULT_MODEL`) | gemini_client | info | A preview / `-latest` alias prints a one-time warning (output can drift when the alias moves). |
| `GEMINI_FALLBACK_MODELS` | `gemini-2.5-flash`, or `gemini-2.5-flash-lite` when the primary already is that | gemini_client | **high** | Comma list tried once each after an overload / timeout / quota / not-found error of the primary. `none` / `off` / `0` / `false` / empty disables the backup (the banner warns: a 503 then fails the report). |
| `GEMINI_FALLBACK_AFTER_ATTEMPTS` | last attempt | gemini_client | cost | 1-based outer-retry attempt from which the fallback model may be used (a MAX_TOKENS answer or a non-retryable 404 switches at once). |
| `GEMINI_RESERVED_CALLS` | `3` | ai_usage_guard | cost | Calls kept for the priority tasks (/1 report, structured extraction, /2 package); optional tasks stop when the remaining budget reaches it. `0` lets optional calls starve the report (banner warns). |
| `GEMINI_ERROR_CALL_WEIGHT` | `0.25` | ai_usage_guard | cost | Weight of a failed call in the daily budget (success / incomplete = 1, blocked = 0). |
| `GEMINI_LIGHT_THINKING_LEVEL` / `GEMINI_LIGHT_THINKING_BUDGET` | – | gemini_client | cost | Thinking level (gemini-3) / budget (gemini-2.5) for the light tasks only. |
| `GEMINI_HTTP_TIMEOUT_MS` | `180000` | gemini_client | info | Per-call HTTP timeout. |
| `GEMINI_USAGE_LOG_PATH` | `data/gemini_usage_log.jsonl` | ai_usage_guard | info | Usage log location (web: persistent disk). |
| `NEWS_MODE` | `smart` | pipeline_context | cost | `off` skips news/macro searches. |
| `TAVILY_DAILY_SEARCH_LIMIT` | `20` | data_fetcher | cost | Tavily searches per DataFetcher instance. |
| `FINMIND_FALLBACK` / `FINMIND_TOKEN` (alias `FINMIND_API_TOKEN`) | off / – | finmind_client | info | FinMind backup source for TWSE data. |
| `FINMIND_TIMEOUT_SEC` | `30` | finmind_client | info | FinMind HTTP timeout. |
| `KNOWLEDGE_WIKI_MODE` | `local` | knowledge_manager | cost | `ai` uses Gemini, `off` skips the wiki. |
| `NOTEBOOKLM_PROMPT_MODE` | `local` | notebooklm_generator | info | `off` skips NotebookLM prompt files. |
| `NOTEBOOKLM_SKILL_HINTS` | off | notebooklm_generator | info | Add project-skill hints to the director card. |
| `SKIP_MONTHLY_CONSOLIDATE` | off | monthly_consolidate | info | Skip the month-start report consolidation. |
| `PROJECT_THEME` | – | pipeline_context | info | Market/project theme label for /1. |
| `DAILY_VISUAL_THEME` (alias `THUMBNAIL_STYLE`) | – | pipeline_context, thumbnail_generator | info | Set by /2; not preserved between days. |
| `ALLOW_SPORTS_ANIME_THEME` | off | thumbnail_generator | info | Allow sports/anime visual themes. |
| `GEMINI_RESUME_MODEL` | `gemini-2.5-flash` | resume_daily_persist | info | Model for the resume script. |
| `ALLOW_STALE_SESSION` (alias `ALLOW_HOLIDAY_RUN`) | off | pipeline_context | **high** | `1` on a holiday / weekend back-fills the latest past session and relabels run_date to that data date (otherwise /1 exits `8`). On a TWSE session day `1` never relabels: stale T86 keeps the `institutional_stale` gate fail (exit `3`) instead of overwriting the previous day. `YYYY-MM-DD` back-fills exactly that session (relabels only when the T86 data date is that day, on a session day or a holiday); remove it afterwards. |
| `REPORT_FACT_CHECK` | `annotate` | report_fact_check | **high** | Post-generation claim check (ranks, 外資 amounts, %): `annotate` writes inline 「（本機核對：…）」 corrections; `warn` only records them in `fact_check`; `off` skips the check (banner warns). Any other value checks without annotating. |
| `REPORT_LOCAL_TABLES` | `1` | report_fact_check | **high** | `0` / `false` / `off` / `no` stops rebuilding the 法人 / 成交值 tables from local data (Gemini's copy is published; banner warns). |
| `REPORT_SECTION_CHECK` | `strict` | analyzer | **high** | Missing 〔今日主軸〕/〔法人籌碼〕/〔明日觀察焦點〕 retries the report; `warn` only prints, `off` skips the check (banner warns). Other values act as `strict`. |
| `YOUTUBE_PACKAGE_MAX_ATTEMPTS` | `3` | youtube_content_generator | cost | Gemini attempts for the /2 package. |
| `YOUTUBE_VIDEO_DURATION` | – | youtube_content_generator | info | Video length (seconds or `mm:ss`) for chapter timestamps; unset → chapters without times. |
| `MARKET_EXTRAS` | `1` | market_extras | info | `0` skips the TAIFEX / margin / TPEx extras (fail-safe when on). |
| `TWSE_MAX_CONCURRENCY` | `3` | twse_http | info | Parallel TWSE requests. |
| `TWSE_ALLOW_INSECURE_SSL` | `1` | twse_http | **high** | `0` forbids the flagged `verify=False` last resort for TWSE certificate failures; when it is used the gate sees the TLS event. |
| `INFLIGHT_PRICE_FETCH_MAX` | `15` | pipeline_stages | info | Max in-flight ledger names fetched only for the prediction ledger; `0` disables. |
| `PYTHON_BIN` | `/usr/local/bin/python3` | run_daily.sh, automation | info | Interpreter with the project deps. |
| `RUN_DAILY_LOCK_DIR` | `/tmp/stock-youtube-run-daily.lock` | run_daily.sh | info | Overlap lock; fixed path (not `$TMPDIR`) so cron, launchd and manual runs share it. |
| `RUN_DAILY_NOTIFY` | `1` | run_daily.sh | info | `0` disables the macOS notification on a non-zero exit. |

Aliases are still honoured for backward compatibility; `/1` prints a hint to
switch to the preferred name. When both are set, the preferred name wins.

## Web app (`app/main_app.py`, Render)

| Flag | Default | Meaning |
|---|---|---|
| `STOCK_RESEARCH_DB_PATH` | `app/stock_research.db` | SQLite cache / quota DB. Point at a persistent disk on Render. |
| `TRUSTED_PROXY_HOPS` | `1` | Proxies that append to `X-Forwarded-For`; the client IP is that many hops from the right (an internal 10.x / 172.16-31.x / 192.168.x / CGNAT hop at that position falls back to the nearest public hop on its left). |
| `CLIENT_IP_HEADER` | – | Trusted platform header carrying the client IP (overrides XFF). |
| `LOG_CLIENT_IP_HEADERS` | off | `1` logs the raw forwarding headers and the resolved IP once per process (one WARNING line `client-ip headers:`). On for the first Render deploy (render.yaml) to verify `TRUSTED_PROXY_HOPS` / `CLIENT_IP_HEADER`; turn off afterwards. |
| `WEB_GEMINI_MAX_RETRIES` | `1` | Gemini retries per web report (batch default is 3). |
| `WEB_TAVILY_SEARCHES_PER_REPORT` | `2` | Tavily searches per web report. |
| `RENDER_GIT_COMMIT` | – (set by Render) | Shown (7 chars) as `info.commit` in `/healthz` to confirm which commit is live. |
| `TZ` | – | Set to `Asia/Taipei` on Render so every daily counter shares one day boundary. |

## /1 exit codes

`main.py` exits with `modules/exit_codes.py` codes: `0` ok, `2` data not ready
(retry in a few minutes), `3` data quality gate, `4` Gemini report failed,
`5` structured_data extraction failed (report kept; use
`scripts/maintenance/resume_daily_persist.py`), `6` config, `7` before 16:10,
`8` not a TWSE session (holiday / weekend; `ALLOW_STALE_SESSION=1` back-fills
the latest session).
`run_daily.sh` logs the meaning and shows a macOS notification on non-zero.
