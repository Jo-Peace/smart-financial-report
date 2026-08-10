# Transcript Mining Skill

Purpose: extract useful hooks, repeated phrases, and structure from YouTube transcripts so the channel can learn from prior videos and competitors.

## When To Use

Use this skill when:

- Reviewing your own uploaded video or a competitor video.
- Looking for title hooks, recurring claims, or segment structure.
- You need a transcript without spending time manually scrubbing video.

## Preferred Tools

- `youtube-transcript-api` for transcripts when captions exist.
- `yt-dlp` for metadata, subtitles, or fallback acquisition.

## Output Contract

Return:

- A short transcript summary.
- 3 to 5 hook lines worth reusing or avoiding.
- Any repeated phrases that define the video's angle.
- A simple section map: opening, evidence, turnaround, conclusion.

## Guardrails

- Do not copy transcript text into long quotes.
- Do not treat a transcript as truth; compare it to actual data before reusing it.

