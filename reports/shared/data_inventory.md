# 資料歸納盤點（Data Inventory）

更新時間：2026-10-01 16:31:22

這份檔案記錄 reports 歸檔後，資訊複利相關資料是否完整。

## 盤點摘要

- structured_data 檔案數：176
- daily report 檔案數：126
- 可回溯日期數（依 structured_data）：126
- 日期範圍：2026-03-06 ~ 2026-09-30
- analytics.db daily_runs：126
- analytics 已指向 canonical daily/ 路徑：126 / 126
- local_market_memory 日期數：126
- 06_YouTube_Assets 殘留檔案數：0

## 複利資料來源（請優先維護這些）

| 系統 | 路徑 | 用途 |
|------|------|------|
| 日報結構化 | `reports/daily/YYYY-MM-DD/structured_data.json` | 預測、驗收、/2 包裝 |
| 本機 analytics | `data/analytics.db` | 命中率、歷史預測追蹤 |
| 市場記憶 | `data/knowledge_wiki/local_market_memory.md` | 跨日敘事與脈絡 |
| 個股角色 | `data/stock_db.json` + `stock_role_taxonomy.md` | 包裝角色判斷 |
| 頻道敘事 | `reports/shared/channel_story_log.md` | 標題/封面疲勞、成效 |

## 歸檔原則

- 正式讀取一律走 `daily/`、`youtube/`、`shared/`
- 重複的舊檔移到 `archive/legacy/`，不刪除
- 專題素材留在 `special_topics/`

## 最近一次整理動作

- （無）
