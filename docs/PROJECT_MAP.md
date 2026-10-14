# smart_financial_report 專案地圖

## 閱讀順序

1. `reports/CURRENT.md` — 今日入口
2. `reports/shared/data_inventory.md` — 資料歸納與複利盤點
3. `main.py` — 每日 /1
4. `cli.py` — 工具總入口（含 /2 `youtube regen-assets`）
5. `docs/ROOT_ENTRYPOINTS_AND_WRAPPERS.md` — 入口與舊檔名對照

## 目錄角色

### `modules/`
核心流程：資料擷取、分析、驗收、落盤、YouTube / NotebookLM 輔助。

### `scripts/`
非主流程工具（canonical 實作）：

- `maintenance/` — 檢查、回填、reports 搬移與殘留整理
- `youtube/` — /2、成效回填、提醒
- `legacy/` — 歷史一次性腳本

### `reports/` 輸出結構

| 區域 | 角色 |
|------|------|
| `daily/YYYY-MM-DD/` | /1 正式產物 |
| `youtube/YYYY-MM-DD/` | /2 正式產物 |
| `shared/` | 頻道敘事、playbook、資料盤點 |
| `special_topics/` | 專題 active 區 |
| `archive/` | 歷史與 legacy 殘留歸檔 |

### 根目錄 Python

- `main.py` — 每日 /1 主流程
- `main_us.py`、`main_weekly.py`、`weekend_special_main.py` — 手動專題腳本（`cli.py special ...`，輸出到 `special_topics/`）
- `cli.py` — 所有工具

## Source of truth（資訊複利）

完整地圖：`reports/shared/info_compounding_map.md`  
查個股卡：`python3 cli.py maintenance research-stock --ticker 2344`  
跨工具個人記憶：`AntiGravity專案/_ai-memory/`（非本 repo）

| 資料 | 位置 |
|------|------|
| 當日工作入口 | `reports/CURRENT.md` |
| 日報結構化 | `reports/daily/日期/structured_data.json` |
| 預測與命中率 | `data/analytics.db` |
| 市場記憶 | `data/knowledge_wiki/local_market_memory.md` |
| 個股角色 | `data/stock_db.json` |
| 頻道敘事 | `reports/shared/channel_story_log.md` |
| 複利地圖 | `reports/shared/info_compounding_map.md` |

## 清理原則

- 新功能放 `modules/` 或 `scripts/`，不堆 root
- 舊檔**搬進 archive**，不刪除
- 整理殘留用：`python3 cli.py maintenance consolidate-residuals`
- 文件與程式路徑保持一致