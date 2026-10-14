# YouTube Title Packaging Skill

Purpose: produce two clean title candidates for daily Taiwan stock videos and keep the learning loop measurable.

## Output Rule

Generate only two title candidates:

- A: control. Full sentence, current formula (`外資○○億狂砍／狂掃` + core stock + contradiction). Upload A first.
- B: experiment. Same core stock and same verified number, but fragmented: short question, telegram, or incomplete clause. Do not start B with `外資○○億狂砍／狂掃`. Do not use 驗收 / 上集點名 / VERIFIED.

Do not generate C titles. Three-way testing creates too much noise for the current channel scale.

## Learning Threshold

- Views below 900: observation only. Do not decide A/B winner.
- Views 900 or above: valid sample for title and thumbnail learning.
- 24-hour data is directional.
- 48-hour data is the default evaluation point.

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

If there is a strong current-event hook, title A should use it. Daily stock data should support the current-event claim, not replace it.

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

### Market Reversal With Hidden Buyer

Use when the market headline and institutional behavior conflict.

Pattern:

```text
台股狂飆卻暗藏殺機？台積電大漲外資竟狂砍XX億！主力反手鎖定「這檔」記憶體股｜AI帶你看股市
```

Why it works:

- Begins with a broad market hook.
- Adds contradiction.
- Uses a recognizable core stock or large number.
- Offers a specific hidden answer.

### Holiday Risk Warning

Use when the video is published before/after a long weekend, market closure, election, geopolitical event, tariff shock, or major earnings/Fed event.

Validated example:

```text
228連假後台股崩盤預警!?
```

Why it works:

- Ties the market topic to a concrete calendar event.
- Creates urgency before the next trading session.
- Speaks to viewer anxiety about holding positions through uncertainty.
- Works best when paired with specific risk evidence, not generic fear.

### Geopolitical / US Market Crash Transmission

Use when a war headline, US market selloff, VIX spike, ADR crash, or major global risk could transmit into the next Taiwan trading day.

Validated example:

```text
戰火引爆美股血洗！台積電 ADR 暴跌 9.5% 週一台股開盤面臨「斷頭」危機？資金全逃去哪了！
```

Why it works:

- Starts with a clear global trigger.
- Shows direct transmission into Taiwan stocks.
- Names a familiar asset, such as 台積電 ADR.
- Adds an urgent next-session consequence.
- Ends with a practical investor question.

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
7. If views are below 900, keep the record but do not judge.
8. If views are 900 or above, compare CTR, views, retention, and comments.
