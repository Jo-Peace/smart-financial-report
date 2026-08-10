# Reports 工作區說明

這裡放每日分析輸出、NotebookLM 素材、YouTube 上架包與累積規則。

## 今日入口（先看這裡）

**[`CURRENT.md`](./CURRENT.md)** — 只連今天要的檔：

| 路徑 | 內容 |
|------|------|
| `daily/2026-08-05/` | 今日 /1：日報、structured data、NotebookLM |
| `youtube/2026-08-05/` | 今日 /2：上架包、封面 prompt |
| `shared/` | 頻道敘事、playbook、資料盤點 |

## 目錄怎麼用

| 路徑 | 用途 | 打開時機 |
|------|------|----------|
| `CURRENT.md` | 今日工作入口 | **每次先開** |
| `daily/日期/` | /1 正式產物（資訊複利主來源） | 查某日日報 |
| `youtube/日期/` | /2 正式產物 | 上架／生封面 |
| `shared/` | 跨日共用紀錄 | 標題規則、成效 |
| `special_topics/日期_slug/` | 週末／專題（一題一夾） | 專題片；索引見 `special_topics/README.md` |
| `archive/` | 歷史、legacy、Finder 重複檔 | 回看／比對（不刪） |
| `analytics_summary.md` | 本機命中率摘要 | 複利健康檢查 |
| `dashboard_stock_watch.json` | 儀表板用 | 自動／工具讀取 |

## 今天整理了什麼（2026-08-05）

1. **191 個 Finder「 2」重複檔** → 搬到 `archive/legacy/finder_duplicates_2/`（幾乎全是與正本相同）
2. **special_topics 扁平亂檔** → 收成 8 個專題資料夾 + `special_topics/README.md`
3. **根目錄無關 `星運logo_square.png`** → `archive/misc/`
4. 跑過 `consolidate-residuals`（analytics 路徑對齊）

**沒有搬** `daily/`、`youtube/` 底下的日期資料夾：程式路徑與 analytics 仍依賴 `daily/YYYY-MM-DD/`。

## 資訊複利（整理時只搬不刪）

1. `daily/日期/structured_data.json` → analytics.db、/2、驗收
2. `data/analytics.db`（在專案 data/，不在 reports/）
3. `data/knowledge_wiki/`、`data/stock_db.json`
4. `shared/channel_story_log.md`

盤點：`shared/data_inventory.md`

## 健康檢查

```bash
python3 cli.py maintenance check-daily-workflow --date YYYY-MM-DD
python3 cli.py maintenance consolidate-residuals --dry-run
```

## 歸檔原則

- 舊資料進 `archive/`，不刪
- 正式讀取走 canonical 路徑（`modules/report_paths.py`）
- 地圖：`../docs/PROJECT_MAP.md`
