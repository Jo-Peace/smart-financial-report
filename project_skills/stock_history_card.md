# Stock History Card Skill

Purpose: improve **prediction quality** by making local hit-rate and trigger history queryable before naming tomorrow's targets.  
Not for audience packaging.

## When to use

- Before writing or accepting `prediction_targets`.
- User asks whether a ticker's past calls were good.
- After a miss: inspect which trigger/stop style failed.
- Weekly: audit which names still deserve formal predictions.

## Command

```bash
python3 cli.py maintenance research-stock --ticker 2344
python3 cli.py maintenance research-stock --ticker 華邦電 --json
```

## Data sources (local only)

1. `data/stock_db.json` — tracking hit_rate, history, market_role  
2. `data/analytics.db` — predictions, multi-horizon results, flows  
3. Map: `reports/shared/info_compounding_map.md`  

Channel story / views are optional footnotes only — **not** prediction evidence.

## Output contract (prediction-first)

Must surface:

1. Overall tracking hit_rate and n  
2. 1D / 3D / 5D / **20D** hit summary when available — **weight 20D as trend reliability**; treat 1–5D as rhythm noise unless n is large  
3. Recent triggers + stop_loss (what we bet and how we invalidated)  
4. Role guidance that affects **whether** it should be a formal prediction (e.g. direction_indicator ≠ elastic rocket)  

May omit or de-emphasize: YouTube titles, view counts.

## Rules

- Prefer this card over model memory for any historical accuracy claim.  
- Do not invent hit rates.  
- Prefer names with stronger **20D / multi-week** evidence for formal `prediction_targets`.  
- High views on a video about a stock ≠ that stock is a good prediction.  
- Goal metric: better next predictions, not better thumbnails.  
