# Market Analysis Memory Skill

Purpose: compound daily market analysis and **prediction validation** so future calls get sharper.  
Audience packaging is secondary and must not define compounding success.

## Data Sources

Use local project records first (canonical paths as of 2026-08):

- `data/analytics.db`
- `data/stock_db.json`
- `data/knowledge_wiki/local_market_memory.md`
- `reports/daily/YYYY-MM-DD/structured_data.json`
- `reports/analytics_summary.md`
- `reports/shared/channel_story_log.md`
- `reports/shared/info_compounding_map.md`
- `reports/youtube/YYYY-MM-DD/youtube_package.md`

Cross-tool personal prefs (not project facts):

- `AntiGravity專案/_ai-memory/personal/PROFILE.md`
- `~/.hermes/memories/USER.md` / `MEMORY.md`

## Query before claiming history

```bash
python3 cli.py maintenance research-stock --ticker 2344
```

Prefer research-stock + analytics over model memory for hit rates and past triggers.

## Market Memory Rules

- Always check whether today's data validates a previous prediction.
- Prefer concrete institutional behavior over broad labels.
- Avoid saying only "資金輪動".
- Explain the actual action:
  - which stock was sold
  - which stock was bought
  - which institution acted
  - whether volume confirms or contradicts price

## Prediction Validation

Time-scale rule (local sample): **≈20 trading days = trend / primary reliability**; **1D–5D = rhythm only**.

Use these as recurring sections:

- Yesterday result as **short-horizon rhythm** (not the system scoreboard).
- 1-day / 3-day / 5-day when available — rhythm notes only.
- **20D / multi-week trend** when available — primary hit/miss for “was the thesis right?”
- Failure reason if prediction missed (separate rhythm miss vs trend miss when possible).

The channel gains credibility by remembering misses, not only wins. Do not call the system broken solely because a 1D print missed.

## Main Operator Hypothesis

The daily report may infer possible main-force logic, but must frame it as hypothesis.
