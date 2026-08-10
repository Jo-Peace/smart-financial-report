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

Use these as recurring sections:

- Yesterday prediction result.
- 1-day validation.
- 3-day validation when available.
- 5-day validation when available.
- Failure reason if prediction missed.

The channel gains credibility by remembering misses, not only wins.

## Main Operator Hypothesis

The daily report may infer possible main-force logic, but must frame it as hypothesis.
