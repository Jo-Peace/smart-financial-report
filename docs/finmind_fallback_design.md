# FinMind Fallback 設計（尚未實作）

狀態：**P1 implemented（opt-in）**（2026-08-10）  
實作：`modules/finmind_client.py` + `DataFetcher.get_institutional_data` 備援分支  
目的：在證交所（TWSE）法人短暫不可用時，用 FinMind 作 **唯讀同日備援**，不取代現有主路徑。

## 1. 問題陳述

| 現象 | 影響 |
|------|------|
| 收盤後成交量已出、T86 法人 JSON 仍 empty /「沒有符合條件」 | `/1` 對齊失敗或回落前一交易日 → 跨日污染風險 |
| TWSE SSL／timeout | 重試後仍空 |
| 雲端 Yahoo 失敗時已有 TWSE stock fallback | 法人層尚無第二來源 |

既有防呆（**法人日 ≠ 量能日 → 停止**）必須保留。Fallback 只在「同日資料可驗證」時啟用。

## 2. 目標與非目標

### 目標

1. `get_institutional_data(target_date=YYYY-MM-DD)` 在 TWSE 就緒重試耗盡後，可選 FinMind 同日外資買賣超。  
2. 回傳結構與現有一致：`{top_buy, top_sell, data_date, source}`。  
3. 寫入 `structured_data` / analytics 時標記 `source=finmind` 以便審計。  
4. 預設 **opt-in**（環境變數），避免新依賴破壞現有使用者。

### 非目標（本階段不做）

- 不用 FinMind 重寫日報敘事或取代 Gemini 分析。  
- 不做即時報價主路徑。  
- 不把 API token 寫進 repo。  
- 不一次接入全部 FinMind dataset。

## 3. 資料對照（需在實作前用真實日驗證）

| 本專案欄位 | TWSE T86 | FinMind（候選） |
|------------|----------|-----------------|
| 外資買賣超股數／金額排序 | `T86` 外陸資買賣超 | `TaiwanStockInstitutionalInvestorsBuySell` 或官方文件等同表 |
| 資料日 | `date` | `date` |
| 代號／名稱 | 證券代號、名稱 | stock_id、可能需 join 名稱表 |

**驗收日建議：** 任選 5 個已有 `reports/daily/YYYY-MM-DD/structured_data.json` 的交易日，比對 top5 買／賣代號集合與方向（金額允許 ±容差）。

## 4. 架構

```text
DataFetcher.get_institutional_data
  ├─ TWSE _fetch_institutional_payload (readiness retries)  ← 已強化
  ├─ if OK → return source=twse
  └─ if FINMIND_FALLBACK=1 and target_date set and TWSE empty
        └─ FinMindInstitutionalClient.fetch(date)
              └─ normalize → same shape, source=finmind
```

對齊路徑（`main.py` volume_date）：

1. TWSE `target_date=volume_date`（多段 sleep 就緒）  
2. 仍失敗且 flag 開 → FinMind 同日  
3. 仍無同日 → **停止**（不回落前一交易日混用 volume）

## 5. 設定

```bash
# .env（勿 commit）
FINMIND_FALLBACK=0          # 預設關；1=啟用
FINMIND_TOKEN=              # 若使用需 token 的方案
FINMIND_TIMEOUT_SEC=30
```

`requirements.txt` 僅在實作 PR 加入 `FinMind`（或 httpx 直打 API，二選一）。

## 6. 介面草圖

```python
# modules/finmind_client.py  (future)

class FinMindInstitutionalClient:
    def fetch_foreign_net_top(self, date: str, top_n: int = 10) -> dict:
        """
        Returns:
          {
            "top_buy": [{"id","name","net_shares","est_amount",...}],
            "top_sell": [...],
            "data_date": "YYYY-MM-DD",
            "source": "finmind",
          }
        """
```

金額若 FinMind 只給股數：沿用現有「股數 × 參考價」估算邏輯，或標記 `est_amount=null` 並在閘門降級為 warn（**不得** silently 發明股價）。

## 7. 品質閘門

| 檢查 | 行為 |
|------|------|
| `data_date == volume_date == run_date` | 必須 |
| top_buy/top_sell 非空 | 必須 |
| source 寫入 structured_data meta | 必須 |
| FinMind 與 TWSE 同日 top3 代號 Jaccard < 0.4（抽樣回歸） | CI 或手動腳本 fail |

## 8. 實作階段（後續 PR）

| 階段 | 內容 | 完成定義 |
|------|------|----------|
| P0 | 本 design + TWSE 就緒重試 | ✅ |
| P1 | `finmind_client` + 串 `get_institutional_data` opt-in | ✅ 需 `FINMIND_FALLBACK=1` + `FINMIND_TOKEN` |
| P2 | 對照 TWSE 同日 top 名單腳本 | 待做 |
| P3 | README runbook | 部分（見下方啟用方式） |

### 啟用方式

```bash
# .env
FINMIND_FALLBACK=1
FINMIND_TOKEN=你的token
# optional
FINMIND_TIMEOUT_SEC=30
```

未設 token 時：印提示並略過，不影響 TWSE 主路徑。  
免費帳號若 API 回 400「update user level」，需到 FinMind 提升額度後才能用籌碼表。

## 9. 風險

- API 配額／付費方案變更  
- 欄位語意（含自營商／借券）與 T86 不完全一致 → 排名偏移  
- 過度依賴 fallback 掩蓋 TWSE 真故障 → 需 log 明顯 `source=finmind`

## 10. 決策紀錄

- **2026-08-10：** 先 design、不寫 production 呼叫碼。  
- 主路徑仍是 TWSE；FinMind 僅同日備援。  
- 跨日混用永遠禁止。

## 11. 參考

- FinMind：https://github.com/FinMind/FinMind  
- Agent Skill 教學：https://finmind.github.io/tutor/AgentSkill/  
- 本專案：`modules/data_fetcher.py`、`main.py` 對齊段、`modules/data_quality_gate.py`
