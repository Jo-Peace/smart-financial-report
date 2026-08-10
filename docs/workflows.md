# 工作流說明

## 每日盤後

入口：`main.py`

流程概念：
1. `pipeline_context` 統一環境、路徑與開關
2. `pipeline_stages` 組裝動態標的與新聞閘門
3. `pipeline_analysis` 做驗收、事件雷達、主軸分類與訊號彙整
4. `pipeline_persist` 產生日報、structured data、NotebookLM source / prompt，並更新知識庫與 analytics
5. `/2` 前強制跑點閱回顧（`cli.py youtube preflight-views`），再定視覺與標題主鉤
6. 最後由 `/2`（`regen-assets`）讀取當日輸出，產出 YouTube 包裝與縮圖素材

## 美國盤 / 週末

- `main_us.py`
- `main_weekly.py`
- `weekend_special_main.py`

用途：
- 補美股與避險訊號
- 做週末專題或週期性摘要

## Web app

入口：`app/main_app.py`

用途：
- 提供查詢與快取報告
- 顯示免費額度與近期報告

## 一次性工具

建議放在：
- `scripts/`

例如：
- backfill
- check
- repair
- prompt regen

## 注意事項

- 正式原始碼不要和輸出檔混在一起
- `reports/` 只放結果、索引與歸檔
- `daily_outputs/` 放每日正式輸出
- `special_topics/` 放單篇專題
- `archive/` 只放歷史，不放流程邏輯
