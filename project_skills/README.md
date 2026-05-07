# Project Skills Registry

This folder stores local playbooks for the stock-youtube pipeline.

The registry is intentionally lightweight:

- `registry.json` is the machine-readable source of truth.
- Each skill body remains a Markdown playbook.
- Python code can select skills by `tags` or `applies_to`.
- This does not import external Project Golem code or license terms.

## Naming

Use stable snake_case IDs:

```text
youtube_title_packaging
thumbnail_ab_testing
notebooklm_cover_alignment
market_analysis_memory
```

## Registry Fields

- `id`: stable skill identifier.
- `path`: Markdown playbook path.
- `priority`: lower number runs earlier.
- `tags`: high-level trigger tags.
- `applies_to`: pipeline surfaces that should load this skill.
- `trigger`: when to apply the skill.
- `output_contract`: what the skill must produce or protect.
- `human_approval`: set true for skills that may publish, delete, spend money, or post externally.

## Current Policy

Automation may generate reports, packages, prompts, and reply drafts.

Automation should not publish YouTube replies, change public titles, or spend ad budget without human approval.
