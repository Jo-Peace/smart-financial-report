# Market Analysis Memory Skill

Purpose: compound daily market analysis, prediction validation, and video packaging lessons without relying on model memory.

## Data Sources

Use local project records first:

- `data/analytics.db`
- `reports/structured_data_YYYYMMDD.json`
- `reports/analytics_summary.md`
- `reports/06_YouTube_Assets/channel_story_log.md`
- `reports/06_YouTube_Assets/youtube_package_YYYY-MM-DD.md`

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

Good framing:

```text
從三大法人買賣超與成交量來看，今天比較像是外資降低權值股曝險，同時測試記憶體低位籌碼。
```

Avoid:

```text
主力一定在拉抬。
資金輪動到記憶體。
```

## YouTube Memory Rules

- 900 views or above: valid learning sample.
- Below 900 views: observation only.
- Preserve exact title and thumbnail variant used.
- Keep notes on why a title may have worked.

Known valid formulas:

- Continuity validation: "上集說..."
- Market contradiction: "台股狂飆卻暗藏殺機..."

## Daily Workflow

1. Load yesterday structured data.
2. Load analytics summary.
3. Validate prior predictions.
4. Build today's main operator hypothesis.
5. Generate A/B title and thumbnail plan.
6. Record packaging choices and later metrics into analytics DB.
