# youtube

放 YouTube 相關的維護、回填與 QA 腳本。

典型用途：

- `preflight_views.py` — **`/2` 前置強制**：回顧近期觀看數
- `check_youtube_package.py`
- `record_youtube_metrics.py`
- `regen_assets.py`
- `review_reminder.py`
- `update_youtube_from_channel.py`
- `update_youtube_learning.py`

## `/2` 前置（點閱回顧）

決定視覺主題與標題主鉤**之前**先跑：

```bash
cd smart_financial_report
python3 cli.py youtube preflight-views --refresh --recent 15
```

會：

1. （可選 `--refresh`）從頻道回填公開觀看數  
2. 印出近窗中位／平均、高／低樣本、近 5 支視覺、決策摘要  
3. 更新 `data/knowledge_wiki/youtube_performance_patterns.md`（若 refresh 成功）

規則細節見：`reports/shared/operating_cadence.md`、`reports/shared/youtube_packaging_playbook.md`。

## YouTube 成效學習

`update_youtube_learning.py` 會讀取 `reports/shared/channel_story_log.md`，
把近期觀看數、標題與視覺風格提煉成：

- `data/knowledge_wiki/youtube_performance_patterns.md`

這份摘要會在 `/2` 產生 YouTube package 前自動更新，並被放進標題/描述/封面包裝 prompt。
`/1` 啟動時也會自動回填近期影片 ID、更新公開觀看數，並重建這份摘要。
手動更新：

```bash
python3 cli.py youtube update-learning
```