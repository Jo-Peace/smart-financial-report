# smart_financial_report

主線專案：台股日報、YouTube 包裝、NotebookLM 資料源、分析資料庫與自動化工作流。

## 目錄概覽

- `app/`：FastAPI 服務與前端。
- `modules/`：正式程式邏輯與共用模組。
- `automation/`：排程、提醒與常駐腳本。
- `data/`：股池、知識庫、快取。
- `project_skills/`：規則、playbook、skills registry。
- `reports/`：正式輸出、素材、歷史歸檔。
- `docs/`：架構說明、入口清單與工作流說明。
- `scripts/`：一次性維護工具、回填腳本與檢查腳本的 canonical 位置。

## 資料品質閘門（/1 自動）

`main.py` 在呼叫 Gemini 寫日報前會做本機資料檢查（不消耗 AI 額度）。若資料異常會停止 `/1`，避免污染正式產出。

- 預設：`DATA_QUALITY_GATE=strict`
- 只看警告、不擋：`DATA_QUALITY_GATE=warn`
- 關閉：`DATA_QUALITY_GATE=off`
- 強制略過 fail：`ALLOW_DATA_QUALITY_FAIL=1`

## 三大法人備援（可選）

主路徑仍是證交所 T86。收盤後若 T86 尚未就緒，會自動等待重試；仍失敗且你有 FinMind token 時可開備援：

```bash
# .env
FINMIND_FALLBACK=1
FINMIND_TOKEN=你的token
```

- 僅允許**同日**備援，禁止與成交量跨日混用  
- 設計說明：`docs/finmind_fallback_design.md`  
- 未設 token 時不會報錯中斷，只會略過備援  

## 標題開場疲勞（/2 preflight）

`python3 cli.py youtube preflight-views` 會檢查近窗標題是否連續以 `AMD`／`驗收` 開場：

- 連續 2 支：WARN  
- 連續 ≥3 支：BLOCK（`/2` 產標題時會嘗試剝除疲勞開場）

## Knowledge Wiki 模式

`/1` 寫入 `capital_rotation` / `macro_events` / `trading_strategies` 時：

- 預設：`KNOWLEDGE_WIKI_MODE=local`（用 structured_data 本機 append，**不消耗 Gemini**）
- 舊版 AI 敘事：`KNOWLEDGE_WIKI_MODE=ai`
- 完全關閉：`KNOWLEDGE_WIKI_MODE=off`

`local_market_memory` 與個股角色仍由 `LocalKnowledgeCompounder` 更新（本來就是本機）。

## NotebookLM Prompt 模式

每日 / 週末 Podcast 指令預設為**本機模板**（填入 structured_data，不呼叫 Gemini）：

- 預設：`NOTEBOOKLM_PROMPT_MODE=local`
- 跳過寫入：`NOTEBOOKLM_PROMPT_MODE=off`

真正餵給 NotebookLM 的乾淨資料源仍由 `notebooklm_source_builder` 本機產生。

建議上傳分工：

| 檔案 | 角色 |
|------|------|
| `notebooklm_source.md` | **事實稿**（法人榜、主角股卡片、驗收數字、失效條件） |
| `notebooklm_prompt.md` | **導演卡**（主線、必講/禁止、段落比例、可選封面對齊） |

不要把日報全文當 source 重複上傳，避免主持人唸模板解讀。

### 主角量化排序（title_priority）

本機 `modules/role_scorer.py` 對當日標的打分並寫入 `structured_data.focus_ranking`：

- elasticity / foreign_buy_strength / volume_confirmation / validation_credibility  
- **title_priority**：誰該當標題、封面、NotebookLM 主角卡  

方向指標股（如 2330）即使外資大買，title_priority 也會被壓低，避免「權值當飆股」。

### 一鍵重產 NotebookLM（不重跑 /1、不耗 Gemini）

```bash
# 依既有 structured_data 重產 source + prompt
python3 cli.py maintenance regen-notebooklm --date 2026-07-08

# 重算驗收 placement / 主軸後再重產，並寫回 structured_data
python3 cli.py maintenance regen-notebooklm --date 2026-07-08 --refresh-frame

# 附封面主題與標題（Video Overview 第一張對齊）
python3 cli.py maintenance regen-notebooklm --date 2026-07-08 \
  --visual-theme "FIFA 32強" --title-a "主標" --title-b "副標"
```

## Gemini SDK

正式模組一律經 `modules/gemini_client.py` 呼叫 **`google.genai`**（不要再新增 `google.generativeai` 依賴）。

- `/1` 主要 AI 呼叫：日報 (`analyzer`) + 敘事結構化萃取 (`data_extractor`)
- 法人榜 / 大盤 / 商品 / 驗收由本機覆寫；Wiki / NotebookLM prompt 預設本機

## 核心工作流

- `main.py`：每日盤後主流程，現在偏向 orchestrator。
- `modules/pipeline_context.py`：執行環境與路徑設定。
- `modules/pipeline_support.py`：共用 helper。
- `modules/pipeline_stages.py`：動態標的與新聞閘門。
- `modules/pipeline_analysis.py`：驗收、事件雷達、主軸分類與分析段。
- `modules/pipeline_persist.py`：日報、structured data、NotebookLM 與知識庫落盤。

## 入口

- 每日盤後主流程：`main.py`
- 工具總入口：`cli.py`（建議用這個，不要在一堆 root 檔名間找）
  - 列出全部工具：`python3 cli.py list`
  - 每日健康檢查：`python3 cli.py maintenance check-daily-workflow --date YYYY-MM-DD`
  - 盤後 /2：`python3 cli.py youtube regen-assets --date YYYY-MM-DD`
- 美國盤 / 週末特輯：`main_us.py`、`main_weekly.py`、`weekend_special_main.py`
- Web 介面：`app/main_app.py`
- 舊檔名仍可用：`compat/`（會提示改用 `cli.py`）；canonical 實作在 `scripts/`

## 輸出邊界

- 當日工作入口看 `reports/CURRENT.md`
- 當日 /1 產物：`reports/daily/YYYY-MM-DD/`
- 當日 /2 產物：`reports/youtube/YYYY-MM-DD/`
- 共用紀錄：`reports/shared/`（channel_story_log、playbook 等）
- 專題內容：`reports/special_topics/`
- 歷史 / 雜項：`reports/archive/`
- 更完整的工作區地圖看 `docs/README.md` 與 `docs/PROJECT_MAP.md`

## 資訊複利（目標：預測更準）

主目標是 **prediction 命中與 trigger 品質**，不是觀眾內容包裝。  
地圖：`reports/shared/info_compounding_map.md`

```bash
# 選標前查個股預測履歷（0 Gemini）
python3 cli.py maintenance research-stock --ticker 2344
```

跨工具個人記憶：`AntiGravity專案/_ai-memory/`
