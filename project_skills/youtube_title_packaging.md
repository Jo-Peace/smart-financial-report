# YouTube Title Packaging Skill

Purpose: produce two clean title candidates for daily Taiwan stock videos and keep the learning loop measurable.

## Output Rule

Generate only two title candidates:

- A: control. Full sentence: verified amount + core stock + contradiction in the first 20 characters (use 買／砍, never open with `外資○○億狂砍／狂掃`). Pick the structure family suggested by「標題結構輪替」. Upload A first.
- B: reserve for a separate future title test (never at the same time as the cover A/B test). Same core stock and same verified number, but a different structure family (contradiction clause, short question, telegram). Do not start B with `外資○○億狂砍／狂掃`. Do not use 驗收 / 上集點名 / VERIFIED.

Do not generate C titles. Three-way testing creates too much noise for the current channel scale.

## Learning Threshold

- The threshold is relative: a valid "winner" needs views ≥ 2× the channel median at the same age
  (computed in `data/knowledge_wiki/youtube_performance_patterns.md`). Below that: observation only.
- Videos younger than 72 hours are still collecting views: never a low sample, never a ban source.
- 24-hour data is directional; 48-hour same-age views are the default evaluation point (snapshots are
  recorded automatically on every /1 refresh).

## Browse-First Packaging Rule

If recent channel analytics show most discovery comes from YouTube Browse features
(Home / recommendations), title A must optimize for homepage stopping power before
search keywords.

For browse-first packaging:

- Title A should be instantly understandable while scrolling.
- Use one core stock, one large number, and one contradiction.
- Prioritize "why is this happening?" over a complete market summary.
- Do not average multiple stocks, sectors, and macro ideas into one title.
- Keep the title promise aligned with the video opening; high CTR with weak early
  retention means the hook was too broad or the intro did not answer quickly enough.

Recommended title A structure (2026-07-07):

```text
今日最大已驗證大數字 + 反直覺矛盾 + 核心股名 + 觀眾想知道的答案
```

Title lead rules:

- Do not start with `【驗收】` or `驗收上集點名：`.
- Put a verified number in the first 20 characters when possible.
- Number priority: strongest daily contradiction > top foreign buy > top foreign sell > volume anomaly.
- Validation/continuity may appear in the middle or end, not at the opening.

### Opener keyword fatigue (2026-08-10)

Continuous same *opener* token burns browse CTR even when the rest of the title is fine.

Tracked openers (title start after optional emoji):

- `AMD…` / `🚀AMD…`
- `【驗收】…` / `驗收上集…`

Rules:

- **2 consecutive** same opener → WARN: prefer a different hook today.
- **≥3 consecutive** same opener → BLOCK: do not open with that token.
- On BLOCK/WARN days: lead with **verified amount + core ticker + contradiction**.
- Major-event names (AMD, Fed, 颱風) may appear in description / mid-title, not as the first hook when fatigued.
- `/2` preflight prints trailing opener streak from `channel_story_log`; respect BLOCK before `regen-assets`.

Bad (fatigued streak):

```text
AMD砸3200億利多！外資153億狂掃南亞科…
```

Good (same data, no opener burn):

```text
外資153億狂掃南亞科卻沒進量榜？華航爆量摔9%是利多陷阱嗎｜8/10 AI帶你看股市
```

Examples:

```text
外資狂砸60.2億買聯電，成交量卻沒進榜？｜AI帶你看股市
群創30萬張成交王卻跌！外資賣38億誰在跑？｜AI帶你看股市
外資買超第一竟然查無此人？2313 華通80.9億真空吸籌｜AI帶你看股市
```

## High-Weight Formulas

## Daily Editorial Frame

Before choosing a formula, decide which kind of day the video is covering.
Do not force every episode into capital rotation, divergence, or warning language.

Use one frame only:

- Validation day: prior prediction or named stock has a clear hit/miss.
- Major event day: macro, US market, ADR, Nvidia, Fed, tariff, FX, holiday, or earnings event is the real hook.
- Anomaly day: price/volume/institutional flow contradict each other with specific numbers.
- Continuation day: the same stock/sector has 2-3 days of flow, volume, and price support.
- Theme day: at least three related stocks move with consistent evidence.
- Quiet setup day: signals are ordinary; package the video around confirmation conditions, not alarm.

Rules:

- "資金輪動" is not a frame. It is a market default.
- Use warning words only when the data supports a warning.
- If the report is a quiet setup day, title A should admit that the market needs confirmation.
- Avoid repeating the same emotional grammar across consecutive videos.

## Priority Order

Title generation should check "news leverage" before ordinary daily data.

Priority:

1. Major news leverage.
2. Continuity / validation from prior videos.
3. Daily market data contradiction.
4. Multi-day continuation.
5. Quiet setup / confirmation conditions.
6. Hidden institutional buying/selling logic.

If there is a strong, verified current-event hook, put it in the second half of title A or the
description; title A still opens with the core stock + verified number (one rule, no exceptions).

Useful news leverage:

- War or geopolitical shock.
- US market crash.
- TSMC ADR or Nvidia collapse.
- VIX spike.
- Fed, interest rate, exchange rate.
- Tariffs or Trump policy shock.
- Long weekend or market reopening.
- Major earnings call or investor conference.
- Taiwan futures/night session abnormal move.

### Continuity / Validation

Use when today's data can validate a previous episode, prediction, or named stock.

Pattern:

```text
上集說XX是地雷，但外資今天砸了XX億進去！這是接刀還是提前卡位？｜AI帶你看股市
```

Why it works:

- Creates continuity.
- Shows the channel remembers and verifies prior calls.
- Gives the viewer a reason to follow the series, not only one video.

### Market Reversal With Named Buyer

Use when the market headline and institutional behavior conflict.

Structure (abstract — do not copy wording): market headline → contradiction with a verified
institutional number → the specific stock the video answers. No 暗藏殺機 / 主力鎖定 wording.

Why it works:

- Begins with the market surface (verified index move).
- Adds the contradiction with a verified amount.
- Uses a recognizable core stock or large number.
- Offers a specific answer instead of a hidden-buyer story.

### Holiday / Event Risk (verified event days only)

Use only when the local editorial frame is 重大事件日 and the event radar has a
fresh (freshness=new) event scoring 60 or more, or when the next session is
right after a long market closure.

Structure (abstract, no fixed sample to copy):

```text
事件 → 台股數字 → 觀察條件
（事件名稱）＋（核心股已驗證外資金額或漲跌）＋（下一交易日要看的價位／條件）？｜AI帶你看股市
```

Rules:

- The event and every number must come from the day's structured data, event
  radar or report; no event, no event-led title.
- Risk is stated as numbers and a condition to watch, never as panic words:
  斷頭、逃命、全逃、血洗、崩盤 are banned (the generator rewrites them).
- Older event-led samples are history only; the current high-sample formula
  is 「金額 + 個股 + 矛盾」 (see youtube_performance_patterns).
- The old weekend-special samples (2/28, 3/7) are a different format and are not a template.

## Language Rules

- Use Traditional Chinese.
- Avoid vague "資金輪動".
- If describing money movement, state the concrete action:
  - 外資砍台積電
  - 投信買超
  - 三大法人反手買
  - 成交量爆出異常
- Do not overuse China-market slang such as "穩了", "嗨了", "躺平".
- Numbers must come from the daily structured data or report.

## Daily Workflow

1. Start with title A.
2. Record the exact title variant used.
3. After 24 hours, record early metrics.
4. After 48 hours, record evaluation metrics.
5. Record whether traffic source is mainly Browse features, Suggested videos, or Search.
6. If Browse features dominate, compare CTR and first 30 seconds retention before search performance.
7. If same-age views are below 2× the channel median, keep the record but do not judge.
8. Above that, compare CTR, views, retention, and comments.
9. Covers are tested with Studio "Test & compare" (single variable, see thumbnail_ab_testing.md); a title test runs separately, never together with a cover test.
