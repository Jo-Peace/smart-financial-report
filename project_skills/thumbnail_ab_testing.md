# Thumbnail A/B Testing Skill

Purpose: keep thumbnail tests simple, readable, and measurable.

## Output Rule

Generate only two thumbnail concepts:

- Daily test (since 2026-10-30): A = original title A + its cover headline; B = few-shot title + its OWN cover headline (`FS`). With `FEWSHOT_AB=off`: A = title A, B = title B, each with its own headline (`PK`).
- Same visual world, composition, characters and layout; only title + headline text differ, and A/B headline text must differ (check-youtube-package FAILs if equal). Both prompts carry `- A/B variable: …`.
- The weekly single-variable rotation (H1–H4) is retired; `AB_HYPOTHESIS=H1..H4` forces one by hand.

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

Single-variable tests (2026-10-14 redesign; results were unattributable when title syntax and cover
composition changed at once):

- H1 style: 漫畫場景（當日視覺主題） vs 戰情室數據大字牌 (B draws no metaphor scene; only the composition is
  shared). On a 財經戰情室 day both arms would be war-room pictures, so that day tests the next hypothesis.
- H2 text: 陳述句 (`1303 南亞 / 外資買55.3億`) vs 問句 (`1303 南亞 / 外資為何買55.3億？`).
- H3 characters: 有雙角色（博士＋阿明） vs 無角色純數據.
- H4 number size / position: 大數字左下大字牌 vs 小數字右上角.

Everything not under test stays identical. Overlay must match the title hero.

Track the thumbnail concept type for future learning:

- Big-number contradiction.
- Validation hit.
- Capital rotation.
- Off-rank accumulation.
- Panic reversal.
- Follow/avoid question.

## Evaluation Rule

- Same-age views below 2× the channel median (see youtube_performance_patterns.md): observation only.
- CTR below the channel's own average with sufficient impressions: note it; cover changes go through the next single-variable A/B test, not a manual swap.
- High CTR but low retention: thumbnail/title attracted clicks but video opening may not match.
- High views and high comments: consider a follow-up episode.
- If Browse features are the main source, CTR and first 30 seconds retention are the primary thumbnail diagnostics.
- If CTR is low but retention is good, the content may be fine and the thumbnail/title should be repackaged.
- If CTR is high but retention is low, do not simply repeat the thumbnail formula; fix the opening promise.

## Daily Workflow

1. Render both covers (B uses B's headline text).
2. In YouTube Studio 「測試與比較」 (Test & compare) set up title + thumbnail: A = title A + cover A, B = title B + cover B; do not switch by hand later.
3. When YouTube reports a winner (or no clear difference), run `python3 cli.py youtube record-ab-result --date YYYY-MM-DD --winner a|b|none` (defaults to the day's FS/PK test).
4. Cumulative few-shot vs baseline: `python3 scripts/youtube/fewshot_ab_summary.py`. Change the default only after ≥ 3 decided tests with a majority; never ban from A/B alone.
