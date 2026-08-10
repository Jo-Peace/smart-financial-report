# News Extraction Skill

Purpose: turn fetched news URLs into clean article text and source metadata so the daily report does not depend on noisy snippets.

## When To Use

Use this skill when:

- A news event needs body text, not just a headline.
- Event radar flags a high-impact source and you need to confirm whether the article is primary or recycled.
- The workflow has news URLs from Tavily, RSS, or search results and needs cleaner extraction before analysis.

## Core Rule

Prefer direct article text extraction over headline paraphrase.

Use source order:

1. Official source.
2. Full-text extractor.
3. Readability fallback.
4. Search snippet only as a last resort.

## Output Contract

Return:

- Clean article text.
- Title, source, URL, and publish date when available.
- A short verdict: primary source, secondary report, or recycled commentary.
- One sentence on why the item matters to Taiwan stocks.

