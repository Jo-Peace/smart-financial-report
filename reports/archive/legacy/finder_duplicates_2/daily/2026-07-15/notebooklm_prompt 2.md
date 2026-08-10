# NotebookLM 主持人導演卡：2026-07-15 台股盤後

請**只根據已上傳的「NotebookLM 乾淨資料源」**生成繁體中文音頻/影片摘要。
本檔是導演卡（怎麼講），不是第二份數據表；**所有數字必須來自資料源**，此卡若與資料源衝突，以資料源為準。

## 主持人

- A：數據張博士 — 冷靜、只講有數字的證據。
- B：阿明 — 代表散戶追問，反應自然，不要固定驚嘆句。
- 不要加第三位主持人或長篇人設。

## 今日唯一主線（請整集繞它轉）

框架「重大事件日」；大盤上漲 893.6點（+2.00%）；對照 華通 買超 vs 國巨* 賣超；矛盾點：1303 南亞：外資大賣 54.6 億但股價暴漲 8.33%，內資…；收尾盯 6770 力積電

- 編輯框架：重大事件日
- 驗收使用：brief（lead=可開場；brief=中段一句；omit=不要當主鉤子）
- 外部主題：無

## 必講

- 中段一句驗收即可：1301 台塑 (+3.30%, 預測成立，成交量排名第10)
- 主角排序（依 title_priority，優先講這些）：
-   1. 2313 華通 — 彈性攻擊股；P100/E86/F100/V72；證據：華通 (+46.7億)
-   2. 6770 力積電 — 彈性攻擊股；P94/E86/F100/V25；證據：力積電 (+41.3億)
-   3. 2344 華邦電 — 彈性攻擊股；P91/E86/F92/V25；證據：華邦電 (+27.0億)
- 明日條件：6770 力積電（多）；失效：明日守住 MA5

## 禁止

- 不要唸完整法人榜或完整族群清單
- 不要說「資金輪動」空話；要講誰買誰賣與金額
- 不要把短線標的講成長線必漲
- 不要捏造資料源沒有的數字
- 極端報酬（例如單週 +40% 以上）若資料源已標註異常，只可當背景不可當主線
- 不要用長段「先驗收再分析」當開場儀式
- 長線背景若含極端漲幅，禁止當「必漲主線」朗讀

## 節目順序（驗收 brief，預設）

1. 開場：今日一句主線 — 約 20%
2. 中段 1～2 句帶過昨日預測結果 — 約 10%
3. 2～3 個證據股（法人/量價）— 約 45%
4. 明日條件與失效 — 約 20%
5. 收尾 — 約 5%

## 口播注意

- 股票代號可用中文讀法輔助（如 2303 → 二三零三），但不要發明公司故事。
- 大金額講「X 點 Y 億」，不要快速掃完整榜單。
- 英文新聞標題只講重點（誰、做了什麼），不要整句朗讀。
- 優先講資料源「主角股卡片」裡的 2～3 檔，不要平均分配全榜。
- 不要寫成逐字劇本；給可臨場發揮的段落重點即可。

## 本地技能提示（語氣/重點；不得改寫數字）

## Project skill guidance
- market_analysis_memory: Use before generating or evaluating daily market analysis. | Prior prediction context, concrete institutional behavior, valid/low-sample YouTube learning rules.
- event_radar: Use when the workflow needs to decide whether an external event is material enough to affect the report or packaging. | Event score, Taiwan impact mapping, gate decision, and a compact reason the event matters.
- news_extraction: Use when fetched news URLs need clean arti…

## 輸出期望

清楚、可信、可追蹤；聽完要記得：今天主線是什麼、誰買誰賣、明天盯什麼、什麼條件失效。
