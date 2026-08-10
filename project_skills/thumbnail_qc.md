# Thumbnail QC Skill

Purpose: verify that generated YouTube thumbnails are readable, distinct, and consistent with the selected visual theme.

## When To Use

Use this skill when:

- A thumbnail prompt is generated and you need a quality check.
- You want to compare two thumbnail variants before publishing.
- A final image needs a quick visual sanity check for text, contrast, and layout.

## Preferred Tooling

- Use Playwright for browser screenshot checks when the thumbnail is rendered in HTML or a preview page.
- Use local image inspection when the file already exists.

## Output Contract

Return:

- A readability verdict.
- Whether the two variants are visually distinct.
- Whether the headline text is legible at small size.
- Whether the theme matches the intended episode frame.
- One concrete fix if the thumbnail is weak.

## Guardrails

- Do not accept low-contrast text.
- Do not accept near-duplicate A/B thumbnails.
- Do not let the visual theme drift away from the episode's main frame.

