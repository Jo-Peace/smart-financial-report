# Thumbnail A/B Testing Skill

Purpose: keep thumbnail tests simple, readable, and measurable.

## Output Rule

Generate only two thumbnail concepts:

- A: primary high-confidence concept.
- B: visually different replacement concept.

A and B must not be the same composition with different words.

## Text Rule

Thumbnail prompts must include the exact visible text.

Required text:

- Large title text, max two lines.
- Small date label, e.g. `4/29 台股盤後`.
- One or two short badges.

Good badge examples:

```text
外資大賣
旺宏＋南亞科
主力買賣邏輯
```

Bad badge examples:

```text
南亞科連續兩日爆量：昨日外資大賣股價...
今日大盤雖然震盪但法人可能正在...
```

For Browse-feature traffic, thumbnail text must be even stricter:

- Two lines only.
- Line 1: one core stock or one conflict target.
- Line 2: one large number or sharp contradiction.
- Prefer concrete text such as `2313 華通 / 外資買80.9億`.
- Avoid abstract text such as `資金輪動 / 主力布局`.
- The thumbnail should answer "what is weird here?" in under one second.

## Visual Rule

Default visual family (2026-07-07 CTR fix):

- Use `財經戰情室` as the default `/2` style: transparent data wall, institutional buy/sell beams, K-line and capital arrows, 博士 + 韭菜, strong numeric contrast.
- Pause sports/anime-inspired themes (FIFA, JoJo, ninja turtle, palace drama, Marvel, etc.) as default or primary A until CTR recovers over 2-4 weeks.
- Explicit `/2 主題` may still override, but the agent should warn first.

- Use 博士 and 韭菜 as recurring characters.
- Keep both characters original, not copied from existing IP.
- No real company logos.
- Use high contrast and large readable type.
- Text must be readable at YouTube thumbnail size.
- Do not rely on decorative clutter.
- Keep a stable channel identity even when the visual theme changes:
  date label, two-line headline, one core stock, one data shock, 博士 + 韭菜.

## A/B Difference

Recommended split:

- A: battle, data shock, market clash.
- B: control room, institutional signal, buy/sell dashboard, discovery moment.

The visual layout, character scale, and background should differ between A and B.

Track the thumbnail concept type for future learning:

- Big-number contradiction.
- Validation hit.
- Capital rotation.
- Off-rank accumulation.
- Panic reversal.
- Follow/avoid question.

## Evaluation Rule

- Views below 900: observation only.
- CTR below 5% with sufficient impressions: consider replacing thumbnail.
- High CTR but low retention: thumbnail/title attracted clicks but video opening may not match.
- High views and high comments: consider a follow-up episode.
- If Browse features are the main source, CTR and first 30 seconds retention are the primary thumbnail diagnostics.
- If CTR is low but retention is good, the content may be fine and the thumbnail/title should be repackaged.
- If CTR is high but retention is low, do not simply repeat the thumbnail formula; fix the opening promise.

## Daily Workflow

1. Use thumbnail A first.
2. Record which thumbnail was used.
3. Record 24-hour and 48-hour metrics.
4. Only test B if the sample is large enough or CTR is clearly weak.
