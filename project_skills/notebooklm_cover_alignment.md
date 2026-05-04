# NotebookLM Cover Alignment Skill

Purpose: make NotebookLM Video Overview visually consistent with the YouTube thumbnail when possible.

## Goal

NotebookLM video output should start with a cover-like first slide before entering the summary.

The cover should align with:

- YouTube title A.
- Thumbnail A visual theme.
- 博士 and 韭菜 character framing.
- Date label.
- Market theme of the day.

## Prompt Block

Add a short instruction to the NotebookLM input or daily production plan:

```text
請將影片第一張投影片設計成封面頁，不要直接進入內容。
封面主標題請使用：「{主標題}」
封面副標題請使用：「{副標題}」
左上角小字日期請使用：「{M/D 台股盤後}」
封面視覺主題請與 YouTube 封面一致：{視覺主題}。
請保留博士與韭菜兩個角色感，使用高對比深色背景、白色/黃色大字、紅綠 K 線元素。
請不要自行改寫主標題與副標題。
```

## Limits

NotebookLM may not allow exact thumbnail control.

Treat this as alignment guidance, not guaranteed output control.

## Daily Workflow

1. Use the same main title lines as thumbnail A.
2. Use the same daily visual theme.
3. Keep date label consistent.
4. Do not overload NotebookLM with long art prompts.
5. If NotebookLM ignores visual guidance, keep YouTube thumbnail as the primary visual identity.
