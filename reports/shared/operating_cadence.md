# 內容系統營運節奏

## 每天：Skills / 工作流固化

固定流程：

```text
/1
/2-preflight   ← 強制：先回顧點閱，再定視覺與標題主鉤
/2 視覺主題
python3 scripts/youtube/check_youtube_package.py --date YYYY-MM-DD
```

（可選補強）

```text
python3 scripts/youtube/update_youtube_from_channel.py --channel-id UCRWXWHHOs4iSDac1GHLsExw --max-results 25

# 資訊複利：查今日主角歷史卡（0 Gemini）
python3 cli.py maintenance research-stock --ticker 2344
```

複利地圖：`reports/shared/info_compounding_map.md`  
跨工具記憶中樞：`AntiGravity專案/_ai-memory/`

### `/2` 前置強制 checklist（2026-07-27 納入）

**使用者說 `/2` 或「接著跑 /2」時，Agent 必須先完成下列步驟，才可決定視覺主題或呼叫 `regen-assets`。**

1. **回填／讀取點閱**（約 30 秒）

```bash
cd smart_financial_report
python3 cli.py youtube preflight-views --refresh --recent 15
# 或：python3 scripts/youtube/preflight_views.py --refresh --recent 15
```

2. **寫下決策摘要（回覆使用者時必須出現）**
   - 近窗中位數／平均 views
   - **1 個高樣本**（可複製的標題感或視覺家族）
   - **1 個低樣本**（今天要避的公式／構圖）
   - 近 3 支已用視覺（避免連三日同構圖）
3. **再定今日包裝**
   - 標題主鉤（對齊當日 `editorial_frame` + structured_data 矛盾）
   - 視覺主題（與近 3 日有差異，但仍金融／股票感）
4. **才跑**

```bash
python3 cli.py youtube regen-assets --date YYYY-MM-DD --skip-podcast --skip-wiki --visual-theme "..."
python3 scripts/youtube/check_youtube_package.py --date YYYY-MM-DD
```

規則：

- views 低於 900 只作觀察，不當作 A/B 勝負依據。
- `/1` 已更新觀看數時，preflight 仍建議跑（可省略 `--refresh` 若剛跑完 `/1` 且 log 已新）。
- 不可只靠記憶或「昨天用什麼」直接定視覺。
- **台股配色**：漲／買＝**紅**，跌／賣／崩盤＝**綠**（與美股相反）。封面與 visual-theme 描述禁止美股紅跌綠漲。

成本防呆：

- `/1` 預設在 16:10 前會停止，避免三大法人資料未齊與 Gemini/API 成本浪費。
- 若確定要強制執行，才用 `ALLOW_EARLY_RUN=1 python3 main.py`。
- Gemini 呼叫會寫入 `data/gemini_usage_log.jsonl`。
- 每日 Gemini 上限預設 `8` 次，可用 `GEMINI_DAILY_CALL_LIMIT` 調整；設 `0` 代表關閉上限。
- 查看用量：`python3 scripts/maintenance/check_gemini_usage.py`。

每天只問三～五件事：

- 今天有沒有漏掉可自動檢查的項目？
- 今天有沒有重複人工修正，應該寫進 workflow？
- 今天的 `/2` 是否遵守「個股 + 大數字 + 反直覺資金行為 + 散戶疑問」？
- 今天的 `/2` 是否先看過近窗點閱再定視覺？
- **上架後**：高潛力片有沒有做站外導流（臉書／留言串）？有的話記一筆，避免復盤誤判。

### 上架後導流（可選但建議，2026-07-31 確認有效）

7/27 案例：除雙矛盾標題外，有 **臉書分享 + 貼到很多留言下面**；人工判斷引流**很有用**。Studio：直接/不明 22.9%、搜尋 22.9%、瀏覽僅 14.6%。結論：**導流是獨立變數**，必須與包裝分開記。

建議流程：

```text
/2 上架完成
→ 判斷是否「高潛力」（雙矛盾 + 金額 + 個股）
→ 是：2 小時內 FB 主頁 + 盡量多則相關留言掛連結（comment seeding）
→ channel_story_log「站外導流變數」表 + 當日 PUBLISH.md 備註：站外=FB留言×N
```
## 每週：復盤

快捷：

```text
/4
```

目的：

- 更新 YouTube 公開觀看數。
- 找出有效樣本與低樣本。
- 把可複製標題/封面規則寫回 `youtube_packaging_playbook.md`。

## 每月：實驗

快捷：

```text
/5
```

目的：

- 一次只比較一個大問題。
- 只用足夠樣本判斷勝負。
- 決定哪些標題公式、封面風格、工具流程要長期保留。

## 目前核心公式

```text
個股 + 大數字 + 反直覺資金行為 + 散戶疑問
```

這不是固定文案，而是 `/2` 的優先判斷框架。
