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

## 美國盤 / 週末（手動，皆呼叫 Gemini）

- `python3 cli.py special us-daily`（`main_us.py`）：美股盤後觀察日記
- `python3 cli.py special us-weekly`（`main_weekly.py`）：美股週報文案 + A/B 標題與封面 prompt
- `python3 cli.py special weekend`（`weekend_special_main.py`）：週末特輯

輸出：`reports/special_topics/YYYY-MM-DD_{us_market_observation|us_weekly_recap|weekend_special}/`。
休市原因、下一交易日、ADR 溢價與本週台股法人／指數由本機計算（`modules/special_context.py`），缺資料時 prompt 明令不得提及。
日期與 13:30 收盤判斷用台北時間：台股收盤前執行時，美股變化寫成「反映在今天盤中」，2330 價格標成盤中報價；
`data/twse_closed_days.json` 未涵蓋的年份不寫休市原因或下一交易日日期（每年需補上新年度休市日）。
週末特輯的 ^TWII 週漲跌只計本週實際交易日；結構化資料萃取失敗時保留報告、寫 `structured_data_failed_*.json`、
不產生導演卡並以退出碼 5 結束。

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
