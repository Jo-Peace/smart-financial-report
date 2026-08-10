# 資料歸納盤點（Data Inventory）

更新時間：2026-08-05 17:09:57

這份檔案記錄 reports 歸檔後，資訊複利相關資料是否完整。

## 盤點摘要

- structured_data 檔案數：143
- daily report 檔案數：93
- 可回溯日期數（依 structured_data）：93
- 日期範圍：2026-03-06 ~ 2026-08-05
- analytics.db daily_runs：93
- analytics 已指向 canonical daily/ 路徑：93 / 93
- local_market_memory 日期數：93
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

- ANALYTICS path updated: 2026-06-15
- ANALYTICS path updated: 2026-06-16
- ANALYTICS path updated: 2026-06-17
- ANALYTICS path updated: 2026-06-18
- ANALYTICS path updated: 2026-06-22
- ANALYTICS path updated: 2026-06-23
- ANALYTICS path updated: 2026-06-24
- ANALYTICS path updated: 2026-06-25
- ANALYTICS path updated: 2026-06-26
- ANALYTICS path updated: 2026-06-29
- ANALYTICS path updated: 2026-06-30
- ANALYTICS path updated: 2026-07-01
- ANALYTICS path updated: 2026-07-02
- ANALYTICS path updated: 2026-07-03
- ANALYTICS path updated: 2026-07-07
- ANALYTICS path updated: 2026-07-08
- ANALYTICS path updated: 2026-07-13
- ANALYTICS path updated: 2026-07-14
- ANALYTICS path updated: 2026-07-15
- ANALYTICS path updated: 2026-07-20
- ANALYTICS path updated: 2026-07-21
- ANALYTICS path updated: 2026-07-22
- ANALYTICS path updated: 2026-07-23
- ANALYTICS path updated: 2026-07-24
- ANALYTICS path updated: 2026-07-27
- ANALYTICS path updated: 2026-07-28
- ANALYTICS path updated: 2026-07-29
- ANALYTICS path updated: 2026-07-30
- ANALYTICS path updated: 2026-07-31
- ANALYTICS path updated: 2026-08-04
