# Special Topic Workflow

Last updated: 2026-05-08

Purpose: define a repeatable workflow for weekend or special videos that are not tied to a single trading day. This is separate from the daily report flow.

## When To Use

Use this workflow when the video is driven by a broader question, event, or thesis:

- "如果戰爭突然停火，台股誰先噴？"
- "AI 股是不是泡沫？"
- "關稅戰解除，哪個族群最先反彈？"
- "美股大跌後，台股週一該怎麼看？"
- "一個月資金到底從台積電流去哪裡？"

Do not use this workflow for ordinary daily after-market reports.

## Core Principle

Special topics should not rely on only one day of market data. The default evidence window is:

- 20 trading days for stock and index trend.
- 1 to 3 months when the topic is macro, geopolitical, or structural.
- Latest daily data only as a final confirmation layer, not the main argument.

## Required Inputs

- Topic question.
- Event context.
- Time window.
- Candidate asset universe.
- Relevant market indicators.
- Local memory from recent daily reports.
- YouTube packaging lessons.

## Default Asset Universe

For Taiwan market special topics:

- Index: `^TWII`
- AI / semiconductor: `2330.TW`, `2317.TW`, `2382.TW`, `3231.TW`, `2308.TW`
- Memory / storage: `2408.TW`, `2337.TW`, `2344.TW`
- Power / energy / infrastructure: `2301.TW`, `1513.TW`, `1504.TW`
- Financials: `2881.TW`, `2882.TW`, `2884.TW`, `2886.TW`
- Shipping / reopening / risk reversal: `2603.TW`, `2605.TW`, `2615.TW`
- Defensive or ETF reference: `0050.TW`, `00878.TW`, `00919.TW`

The universe should be edited per topic. Do not force all symbols into every report.

## Output Package

Every special topic should produce these files:

- `reports/special_topics/special_report_YYYY-MM-DD_TOPIC.md`
- `reports/special_topics/special_structured_data_YYYYMMDD_TOPIC.json`
- `reports/special_topics/notebooklm_prompt_special_YYYYMMDD_TOPIC.md`
- `reports/06_YouTube_Assets/youtube_special_package_YYYY-MM-DD_TOPIC.md`

## Report Structure

1. One-sentence thesis
2. Why this topic matters now
3. 20-day or 1-3 month evidence table
4. Capital rotation map
5. Candidate stock pool
6. Upside logic by scenario
7. Counter-risk section
8. Ranking: who moves first, who confirms later
9. Action plan for conservative and aggressive viewers
10. What would invalidate the thesis

## Candidate Stock Pool Rules

Each candidate must include:

- Stock code and name.
- Role in the topic.
- Evidence window.
- Price trend.
- Volume trend.
- Institutional flow when available.
- Why it might move first.
- What would prove the idea wrong.

Avoid treating a large-cap direction indicator as the highest-upside stock unless the data supports it.

## Structured Data Schema

The special structured data should include:

```json
{
  "date": "YYYY-MM-DD",
  "topic": "",
  "thesis": "",
  "time_window": "",
  "event_context": [],
  "market_indicators": [],
  "capital_rotation": [],
  "candidate_pool": [
    {
      "id": "",
      "name": "",
      "role": "",
      "evidence": "",
      "upside_logic": "",
      "counter_risk": "",
      "move_order": ""
    }
  ],
  "ranked_first_movers": [],
  "conservative_strategy": "",
  "aggressive_strategy": "",
  "invalidation_signals": [],
  "youtube_angles": []
}
```

## YouTube Packaging Rules

Special-topic titles should sell the question and the ranking, not only daily price action.

Good formulas:

- `事件假設 + 台股最大疑問 + 誰先動 + AI 排名`
- `如果 X 發生 + 這幾檔誰先噴 + 反方風險`
- `市場以為是 A + 數據顯示資金可能先去 B`

Thumbnail copy should be 4-8 Chinese characters:

- `誰先噴？`
- `停火受惠股`
- `AI泡沫？`
- `資金換邊`
- `先噴名單`

## Production Checklist

Before generation:

- Confirm topic question.
- Choose time window.
- Choose asset universe.
- Load local market memory.
- Load recent YouTube packaging lessons.

During generation:

- Use multi-day evidence.
- Separate thesis, evidence, and speculation.
- Include counter-risk.
- Rank candidate stocks by reason, not hype.

After generation:

- Produce NotebookLM prompt.
- Produce YouTube special package.
- Record title and thumbnail experiment in the channel log.
- If published, record metrics after 30 minutes, 2 hours, and 48 hours.

## Initial Priority Topics

1. 如果戰爭突然停火，台股誰先噴？
2. AI 伺服器供應鏈漲多後，資金下一站在哪？
3. 台積電被外資提款後，真正接手的是誰？
4. 記憶體、面板、聯電這類低價電子股，是反彈還是誘多？
5. 台股站上高位後，哪一類股票最怕回檔？

