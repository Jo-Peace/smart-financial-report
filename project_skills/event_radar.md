# Event Radar Skill

Purpose: detect and carry high-impact external events into the daily stock workflow without letting the pipeline become news-driven by default.

## What This Skill Protects

- Do not miss supply-chain-level events such as AMD, NVIDIA, Broadcom, OpenAI, Microsoft, AWS, or major Taiwan investment announcements.
- Do not overreact to ordinary news items.
- Keep the event layer separate from daily market data, then only expand news search when the data gate opens.

## Event Layers

Use three layers:

1. Data-first gate.
2. Low-cost event radar.
3. Expanded news search only when justified.

## When To Use

Use this skill when:

- The daily report needs to decide whether news search should expand.
- The main report needs to explain why a supply-chain event matters even before the stock ranks show it.
- A weekly or weekend special needs structural context around AI, packaging, memory, or industrial metals.

## Event Scoring Rules

- Assign higher weight to official announcements and direct investment / capacity / platform news.
- Assign lower weight to recycled commentary or vague market chatter.
- Prefer Taiwanese supply-chain mapping over generic global headlines.

## Output Contract

The skill must produce:

- An event score.
- Impacted Taiwan roles or sectors.
- A concise explanation of why this event matters.
- A clear gate decision: expand search, keep it lite, or skip.

## Daily Workflow

1. Scan for high-impact events.
2. Keep a cache so the same event is not re-read every day.
3. Expand news search only when the data gate or event score justifies it.
4. Carry the event into the daily report and YouTube packaging only if it is still structurally relevant.
