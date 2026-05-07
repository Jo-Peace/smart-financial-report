# Optimization Backlog

Purpose: keep the project memory explicit so future scans do not repeat the same recommendations.

Last updated: 2026-05-07

## Completed Recently

- Deterministic previous prediction validation
  - Local validation now checks previous `prediction_targets` against market data.
  - `prev_day_picks_result` and `validation_results` are overwritten with local results when available.

- YouTube package late-stage context
  - YouTube title/description generation now receives validation results, channel story, analytics summary, title rules, local market memory, and stock role taxonomy.
  - Packaging rules require concrete stock/sector naming and distinguish direction indicators from high-elasticity stocks.

- Thumbnail prompts are local and scenario-based
  - Thumbnail prompt generation no longer calls Gemini when `structured_data` is available.
  - Prompts are scenario briefs, not fixed layout templates.
  - Daily visual theme is user-provided and should not be hard-coded or preserved from prior days.

- Local knowledge compounding
  - `data/knowledge_wiki/local_market_memory.md` records deterministic daily market memory.
  - `data/knowledge_wiki/stock_role_taxonomy.md` records stock role definitions.
  - `data/stock_db.json` has `market_role` with internal `evidence` for objective role decisions.

## Next High-Value Optimizations

1. Data quality guard
   - Stop or warn before report generation when market data looks broken.
   - Examples: many `pct_change` values are `0.00`, volume table missing prices, institutional data missing, or previous validation target cannot be checked.
   - Priority: highest, because it protects credibility.

2. Special topic workflow
   - Build a separate flow for weekend/special videos such as "如果戰爭突然停火，台股誰先噴？"
   - Should produce long-form outline, 60-second Shorts script, stock candidate pool, upside logic, and counter-risk section.
   - Keep it separate from daily report flow.

3. Quantitative role scoring
   - Extend `market_role` with objective scores:
     - elasticity score
     - foreign buying strength
     - volume confirmation
     - validation credibility
   - Useful for ranking "who moves first" topics.

4. Gemini SDK migration
   - Replace deprecated `google.generativeai` usage with the newer `google.genai` SDK.
   - Not urgent because current flow works, but should be handled before dependency drift breaks production.

## Standing Decisions

- OpenAI/DALL-E should not auto-generate thumbnail images. The system only produces prompts; the user manually generates images.
- Thumbnail prompts should be scenario-first, not composition-first.
- Do not preserve a previous visual theme. The daily visual theme comes from the user or current events.
- Stock role judgments must be backed by local data/evidence. Avoid hard-coded subjective claims.
- YouTube package should be generated after content and structured data, so title/description packaging can react to the actual report.

## Avoid Recommending Again As New Work

- "Generate actual thumbnail images automatically" is intentionally removed.
- "Let Gemini decide thumbnail scenario prompts" is intentionally avoided when structured data exists.
- "Use fixed thumbnail layouts" is intentionally avoided.
- "Treat high market-cap direction indicators as the highest-elasticity stocks" is intentionally avoided.
