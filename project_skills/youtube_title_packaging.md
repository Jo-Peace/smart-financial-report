# YouTube Title Packaging Skill

Purpose: produce two clean title candidates for daily Taiwan stock videos and keep the learning loop measurable.

## Output Rule

Generate only two title candidates:

- A: primary title, highest-confidence formula.
- B: replacement title, different angle for testing after 48 hours.

Do not generate C titles. Three-way testing creates too much noise for the current channel scale.

## Learning Threshold

- Views below 900: observation only. Do not decide A/B winner.
- Views 900 or above: valid sample for title and thumbnail learning.
- 24-hour data is directional.
- 48-hour data is the default evaluation point.

## High-Weight Formulas

## Priority Order

Title generation should check "news leverage" before ordinary daily data.

Priority:

1. Major news leverage.
2. Continuity / validation from prior videos.
3. Daily market data contradiction.
4. Hidden institutional buying/selling logic.

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
5. If views are below 900, keep the record but do not judge.
6. If views are 900 or above, compare CTR, views, retention, and comments.
