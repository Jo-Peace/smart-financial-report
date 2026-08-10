# stock-youtube 資訊複利地圖

更新：2026-08-04

## 目的（先講清楚）

**資訊複利的主目標：讓預測更精準。**

不是為了給觀眾看、不是為了標題 CTR、不是為了影片包裝。  
YouTube／NotebookLM 只是**次要消費者**：它們可以借用研究結論，但複利系統的成敗用 **命中率、失效條件、可重複 trigger** 衡量。

| 優先 | 問題 | 成功長相 |
|------|------|----------|
| **P0 預測** | 這檔／這類條件，歷史準不準？明天該不該押？ | hit rate ↑、亂槍打鳥 ↓、失效條件清楚 |
| P1 研究記憶 | 類似盤／族群／事件下次能否對照？ | 可查、可回餵 analyzer |
| P2 內容包裝 | 觀眾看不看得懂？（可選） | 標題誠實反映研究，不反向主導研究 |

跨工具個人記憶中樞：`AntiGravity專案/_ai-memory/`（與本專案分離）。

---

## 1. Source of truth（寫入層）— 預測優先

| 資料 | 路徑 | 對預測的用途 |
|------|------|----------------|
| 結構化日報 | `reports/daily/日期/structured_data.json` | 當日 prediction_targets、背離、停損 |
| 預測帳本 | `data/analytics.db` → `predictions` / `prediction_results` | 多 horizon 命中與超額報酬 |
| 命中摘要 | `reports/analytics_summary.md` | 整體 1D/3D/5D/20D 健康度 |
| 個股庫 | `data/stock_db.json` → tracking / market_role | 單檔歷史命中、角色、不該怎麼分類 |
| 市場記憶 | `knowledge_wiki/local_market_memory.md` | 跨日盤面條件（誰買誰賣、主軸） |
| 資金／事件／策略 | `capital_rotation` / `macro_events` / `trading_strategies` | 情境對照，避免只看 1～2 日 |
| 當日入口 | `reports/CURRENT.md` | 人找當天檔 |

### 次要（內容管線，不定義複利成敗）

| 資料 | 路徑 | 說明 |
|------|------|------|
| 頻道敘事／觀看 | `channel_story_log` | 內容成效，**不是**預測準度 |
| 包裝 playbook | `youtube_packaging_playbook.md` | 怎麼說給觀眾聽 |
| YT 實驗 | `youtube_experiment_log.md` | 標題／封面／站外實驗 |

---

## 2. 預測閉環（真正要轉的輪子）

```text
今日 prediction_targets（方向 + trigger + 停損）
  → 寫入 analytics.predictions + stock_db
  → 1D / 3D / 5D / 20D 驗收 → prediction_results + tracking
  → 下次選標的前：research-stock / analytics_summary
  → 調整：誰能進正式預測、什麼 trigger 該降權、角色是否誤判
```

內容閉環（`/2`、views、FB）**平行存在**，不可反過來決定「明天押誰」。

---

## 3. 日常指令（服務預測）

```bash
cd smart_financial_report

# 個股：歷史準不準、觸發條件、停損（0 Gemini）
python3 cli.py maintenance research-stock --ticker 2344

# 全樣本命中健康度
python3 cli.py maintenance analytics-report
# 或看 reports/analytics_summary.md

# 回填缺漏
python3 cli.py maintenance backfill-analytics
python3 cli.py maintenance backfill-market-snapshots
```

選明日觀察標的前建議：

1. 對候選股跑 `research-stock`  
2. 看 hit_rate 與 **哪種 trigger 常中／常掛**  
3. 對照停損是否曾經有效  
4. 再寫進 `prediction_targets`  

---

## 4. 每週 promote（預測側）

1. 本週正式預測命中率（1D／5D）有沒有掉？  
2. 哪 2 個 trigger 該升權／降權？（寫進 trading_strategies 或本地圖備註）  
3. 有沒有「角色誤判」導致亂押權值當彈性股？  
4. **不要**把「某支片觀看高」當成預測變準的證據  

對齊：`AntiGravity專案/_ai-memory/WEEKLY_PROMOTE.md`

---

## 5. 成熟度（對準「預測更準」）

| 能力 | 狀態 |
|------|------|
| 預測寫入 + 多 horizon 驗收 | ✅ 有 |
| 單檔可查歷史命中 | ✅ research-stock |
| 選標前強制看歷史 | ◐ 靠紀律／Agent，尚未 hard-gate |
| 相似歷史盤自動對照 | △ 未做 |
| 用內容 views 反推預測 | ❌ 不應做 |

---

## 6. 給 Agent 的硬規則

1. 討論「複利有沒有效」→ 只報 **預測命中／超額報酬／trigger 品質**。  
2. 觀眾數據、封面、FB 導流 → 標成 **內容變數**，與預測準度脫鉤。  
3. 日報預測段優先引用 `research-stock` / analytics，不要靠模型記憶編造歷史命中率。  
