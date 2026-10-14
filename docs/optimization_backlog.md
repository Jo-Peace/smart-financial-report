# Optimization Backlog

Purpose: keep the project memory explicit so future scans do not repeat the same recommendations.

Last updated: 2026-08-04 (AI memory hub + research-stock)

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

- Data quality guard
  - `modules/data_quality_gate.py` blocks or warns before Gemini spend when TWSE payloads look broken.
  - Modes: `DATA_QUALITY_GATE=strict|warn|off`, override with `ALLOW_DATA_QUALITY_FAIL=1`.

- Structured facts overlay + fixed-sleep removal (2026-07-09)
  - `modules/structured_facts.py` builds `index_action` / 法人買賣超榜 / 商品 / 本機驗收 from already-fetched market payloads.
  - `extract_structured_data` injects ground truth into the prompt and force-merges local-owned fields after Gemini returns.
  - Removed fixed `time.sleep(5/8)` waits in `/1` and `pipeline_persist`; rate limits rely on tenacity retry.

- Knowledge Wiki local-first (2026-07-09)
  - Default `KNOWLEDGE_WIKI_MODE=local`: append capital_rotation / macro_events / trading_strategies from structured_data (0 Gemini).
  - Weekly digest on Friday uses local compression in local mode.
  - Legacy Gemini path remains: `KNOWLEDGE_WIKI_MODE=ai`. Disable with `off`.

- NotebookLM prompt fully local (2026-07-09)
  - Removed dead Gemini path after early-return in `generate_notebooklm_prompt`.
  - Enhanced compact template with editorial frame, sectors, event radar, skill context.
  - Weekend special prompt is now local template too (`build_weekend_notebooklm_prompt`).
  - `NOTEBOOKLM_PROMPT_MODE=local|off` (default local).

- Gemini SDK migration + slimmer structured extract (2026-07-09)
  - Production modules use `modules/gemini_client.py` (`google.genai`) only.
  - Migrated: analyzer, data_extractor, youtube_content_generator, stock_db_updater, knowledge_manager (ai mode), thumbnail_generator.
  - Structured extract prompt now requests narrative keys only; local facts still force-merged.
  - `google-generativeai` kept only for `scripts/legacy/*`.

- Validation (驗收) fatigue demotion (2026-07-09)
  - Editorial frame no longer auto-selects `validation_day` whenever any hit/miss exists.
  - New `validation_placement`: `lead` | `brief` | `omit` based on story score + title fatigue.
  - Packaging / NotebookLM / thumbnails only lead with 驗收 when placement=lead.
  - 「驗收」removed from packaging fatigue exemptions.

- NotebookLM source/prompt content split (2026-07-09)
  - `notebooklm_source` is facts-only (no director script, no template「結構性分化」commentary).
  - Event radar deduped (max 2); extreme short-window returns annotated.
  - `notebooklm_prompt` is a director card: one mainline, must-cover / must-not, segment %, no full leaderboard dump.

- NotebookLM pack v2 (2026-07-10)
  - Protagonist stock cards in source (role / amount / evidence / invalidation).
  - Director card optional cover alignment (title A/B + visual theme).
  - Daily report (`analyzer`) respects `validation_placement` + editorial frame label.
  - CLI: `python3 cli.py maintenance regen-notebooklm --date … [--refresh-frame]`.

- Quantitative market_role scores (2026-07-10)
  - `modules/role_scorer.py`: elasticity / foreign_buy_strength / volume_confirmation / validation_credibility / title_priority.
  - Written into `stock_db.market_role.scores` via LocalKnowledgeCompounder.
  - `structured_data.focus_ranking` for NotebookLM cards, director must-cover, thumbnail preference.
  - Direction indicators / ETFs down-ranked as title heroes.

## Completed 2026-08-04

- AI memory hub at workspace `AntiGravity專案/_ai-memory/` (personal / projects / tools / backup / weekly promote).
- `reports/shared/info_compounding_map.md` single map for stock-youtube compounding.
- `cli.py maintenance research-stock` local stock history card (stock_db + analytics + channel_story).
- Skill `stock_history_card` registered; market_analysis_memory paths updated to daily/youtube layout.

## Completed 2026-10-13 (prediction ledger measurement)

- `analytics_store` ledger contract `sessions_v2`: horizon = N TWSE sessions after the prediction-day close; scored only at the target session close (else `status=missing`, never a later day or the daily pct_change); index return over the same TWII window; numeric `stop_loss_price` checked on the close path (`stopped`; judged on the sessions it lasted, and a stopped loss is always a miss); results are upserted every run, so reruns / backfills recompute them. The daily `/1` path only writes new or `sessions_v2` rows and never touches legacy rows.
- One hit rule (`prediction_validator.classify_hit`) for on-air validation, the ledger and stock_db tracking: excess vs TWII beyond a sqrt(sessions) dead band; neutral / non-directional / missing rows are not in denominators.
- Predictions keyed by (prediction_date, ticker); stock_db tracking idempotent per (prediction_date, sid).
- 「X 窗較準」 is published only when the cluster-adjusted 95% Wilson lower bound beats the 50% baseline (`horizon_reliability`); otherwise the fixed 「樣本不足以判定」 sentence. Stored legacy one-liners are not republished.
- In-flight predictions are fetched every session for the ledger only (`INFLIGHT_PRICE_FETCH_MAX`, default 15, 0 = off); they never join today's analysis universe (`pipeline_stages.ledger_only_symbols` / `fetch_market_universe`).
- Historical re-grade: `python3 scripts/maintenance/recompute_prediction_results.py` (dry-run; `--apply` backs up first). This is the only path that converts legacy calendar-day rows. `data/twse_closed_days.json` must list next year's holidays before windows cross the year end (the ledger and analytics_summary warn).

## Completed 2026-10-14 (integration final fixes)

- One 成交值 rank per /1 prompt: the market-wide tape rank (`value_ranking`, ETFs excluded) for the signal pack, role scores and the fact check; the 觀察池 pool rank only when the tape is missing, always labelled as such. Role scores map a market rank with its own curve (top 20 confirmed, never < 50 = thin), the pool rank keeps the steep one; `backtest_scores.py` rebuilds the same basis from `daily_closes` and reports how many sessions used each. A bare 「全市場第一」 takes its list from the keyword nearest to it.
- stock_db tracking takes its verdicts from the on-air `validate_prediction_targets` rows (stop-loss / stale bar / excess rule); one direction parser (`prediction_validator.direction_sign`).
- TWSE connect failures (DNS / refused / connect timeout) fail fast without tripping on a blip: 3 tries (1 s, 2 s apart) per call; the per-host circuit opens only after ≥ 2 failed calls spanning ≥ 5 s, stays open 10 s, then lets one single-attempt probe through; T86 gets two short connect-recovery waits (5 s, 10 s) before NETWORK. "Not published yet" keeps its readiness waits.
- Similar sessions: a flat session is comparable with quiet moves of either sign; an empty result prints the closest near miss (CLI) / a date-free reason (prompt).
- Hand-written /2 rules in `youtube_performance_patterns.md` live in a marked block that regeneration keeps (one lost marker still keeps the block, with a warning); the legacy migration skips rules the pre-090535f generator wrote (`RETIRED_GENERATED_PACKAGING_RULES`) and prints what it moved. Legacy ledger rows (method NULL) produce a one-line /1 warning, never a silent migration.

## Next High-Value Optimizations

1. Special topic CLI productization
   - Workflow docs and `weekend_special_main.py` exist; align paths and expose via `cli.py`.

3. Step-level resume for `/1` persist
   - `resume_daily_persist.py` is partial; make report → structured → notebooklm → wiki independently re-runnable.

4. Drop `google-generativeai` after legacy scripts are retired or rewritten.
5. Packaging positive feedback loop (high-view formulas → whitelist rotation).
6. Lightweight relationship edges + similar-session query (after research-stock usage proves value).
7. Optional weekly HTML graph view of capital rotation (consume edges; do not start with Neo4j).

## Standing Decisions

- OpenAI/DALL-E should not auto-generate thumbnail images. The system only produces prompts; the user manually generates images.
- Thumbnail prompts should be scenario-first, not composition-first.
- Do not preserve a previous visual theme. The daily visual theme comes from the user or current events.
- Stock role judgments must be backed by local data/evidence. Avoid hard-coded subjective claims.
- YouTube package should be generated after content and structured data, so title/description packaging can react to the actual report.
- Fixed pre-call sleeps are intentionally removed; use retry/backoff on 429 instead of unconditional waits.
- Knowledge wiki daily append defaults to local structured_data; AI wiki is opt-in via `KNOWLEDGE_WIKI_MODE=ai`.
- NotebookLM daily/weekend prompts are local templates; do not reintroduce Gemini expansion unless NotebookLM quality regresses.
- New Gemini call sites must go through `modules/gemini_client.py` (google.genai), not `google.generativeai`.
- Do not re-add automatic daily「驗收日」main-frame or title exemption for 驗收; validation is a beat, not a monopoly.
- Do not publish a "20D（或任一窗）較準／成敗尺子" claim from raw hit rates; only the CI-vs-baseline gate in `horizon_reliability` may make that claim.

## Avoid Recommending Again As New Work

- "Generate actual thumbnail images automatically" is intentionally removed.
- "Let Gemini decide thumbnail scenario prompts" is intentionally avoided when structured data exists.
- "Use fixed thumbnail layouts" is intentionally avoided.
- "Treat high market-cap direction indicators as the highest-elasticity stocks" is intentionally avoided.
- "Add unconditional sleep before every Gemini call" is intentionally avoided; retry handles rate limits.
- "Data quality guard before /1" is already implemented.
