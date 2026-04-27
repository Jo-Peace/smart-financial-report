import os
import google.generativeai as genai
import datetime

def generate_notebooklm_prompt(api_key, structured_data, date_str=None, current_theme=""):
    """
    Generates a NotebookLM Podcast Prompt based on the EXTRACTED STRUCTURED DATA.
    Uses a detective storytelling approach: find the anomaly, investigate, reveal.
    """
    genai.configure(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    model = genai.GenerativeModel(model_name)

    if not date_str:
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")

    # Calculate Day of Week Context
    try:
        date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        weekday_idx = date_obj.weekday()
    except ValueError:
        weekday_idx = datetime.datetime.now().weekday()

    weekday_context = ""
    if weekday_idx == 0:  # Monday
        weekday_context = "今天是週一。開場加入：「大家週一剛開工，我們快速講重點」，節奏要更精簡。"
    elif weekday_idx >= 4:  # Friday or Weekend
        weekday_context = "今天是週末。焦點微調為「本週盤勢回顧與下週推演」，主持人進行宏觀覆盤。"
    else:
        weekday_context = "這是平常日，維持高節奏、活潑的日常盤後分析。"

    # Setup Thursday Ad Context
    if weekday_idx == 3:  # Thursday
        ad_instruction = "\n- **工商服務（必須由 A 以自然閒聊口吻念出）**：「插播一下！如果你覺得每天這個客製化的 AI 財經報告對你有幫助，這套系統現在開放免費測試中！免費試用連結已經放在說明欄。另外提醒，網站功能還在 Beta 階段，分析結果僅供參考，不構成投資建議喔！」"
    else:
        ad_instruction = ""

    # Setup Theme Context
    theme_context = ""
    if current_theme:
        theme_context = (
            f"\n**【目前市場核心主題】：{current_theme}**\n"
            f"- 請 A 在分析資金流向時，嘗試連結此主題（但只在有數據支持時）。\n"
            f"- 請 B 針對此主題提出散戶的質疑：「這題材吵太久了吧？」或「現在進場是不是接隕石？」\n"
        )

    # Format the sectors for the prompt
    sectors_formatted = ""
    for sector, stocks in structured_data.get("sectors", {}).items():
        sectors_formatted += f"  - [族群: {sector}]: {', '.join(stocks)}\n"

    # 防呆機制：確保 prev_day_picks_result 與 price_volume_divergence 確實是陣列
    prev_picks = structured_data.get('prev_day_picks_result', [])
    if isinstance(prev_picks, str):
        prev_picks = [prev_picks]
    prev_picks_str = ', '.join(prev_picks) if prev_picks else '無前日明牌可驗收'

    price_div = structured_data.get('price_volume_divergence', [])
    if isinstance(price_div, str):
        price_div = [price_div]
    price_div_str = ', '.join(price_div) if price_div else '無明顯背離'

    prompt = f"""你是一個台股財經節目的 Podcast 腳本設計師。
你的任務是：根據以下的驗證數據，產出一個 Google NotebookLM 的音頻概述提示詞（Audio Overview Prompt）。
這個 Prompt 將用來指揮兩位 AI 主持人錄製今日盤後分析 Podcast。

--- 今日已驗證數據（不得偏離或捏造）---
日期：{structured_data.get('date', date_str)}
大盤狀況：{structured_data.get('index_action', 'N/A')}
資金撤出的權值股：{', '.join(structured_data.get('heavyweights_dumped', []))}
資金流入的板塊：{', '.join(structured_data.get('safe_havens_bought', []))}
商品行情：{', '.join(structured_data.get('commodities', []))}
AI 數據觀察焦點：{', '.join(structured_data.get('ai_data_picks', []))}
昨日預測驗收結果：{prev_picks_str}
籌碼與量價背離現象：{price_div_str}

族群與個股（必須嚴格對應，不得混淆）：
{sectors_formatted}

穩健策略：{structured_data.get('conservative_strategy', 'N/A')}
波段策略：{structured_data.get('aggressive_strategy', 'N/A')}
-----------------------------------------------------------

動態語境指引：
- 時間考量：{weekday_context}
{theme_context}

請根據以上數據，產出以下格式的 NotebookLM Podcast 提示詞（僅輸出最終 Markdown 內容）：

---

# NotebookLM Podcast Prompt：{date_str} 台股盤後深度調查

你正在製作每日台股財經 Podcast。語氣活潑、有懸念感，數據必須精準，絕不憑空捏造。全程繁體中文，英文專有名詞直接念出。

---

## 🎙️ 主持人設定

**主持人 A（數據張博士・偵探派）**
- 風格：冷靜中帶有「查案興奮感」。擅長從數字找矛盾，不確定時主動承認「這個我也不確定原因」。
- 口頭禪：「數字告訴我們...」、「奇怪的事情出現了...」、「等等，這裡有個矛盾」

**主持人 B（熱血阿明・散戶代言人）**
- 風格：代表「今天散戶的真實心理狀態」，每集根據盤面情緒量身設計：
  - 大漲盤 → FOMO：「我要不要追？！」
  - 大跌盤 → 恐慌：「完蛋了，我要不要跑？」
  - 盤整縮量 → 茫然：「博士，這種盤你叫我怎麼辦...」
- **每集 B 的情緒反應必須不同，禁止固定說「哇！這也太猛了吧！」**

---

## 🎬 節目結構（故事主線：從異常發現，到解答結束）

### 【段落 0】昨日預測驗收（誠信時刻——任何情況不得省略）
- A：「在今天的分析開始前，先驗收昨天的預測。」
- 根據「昨日預測驗收結果」誠實播報。如果有具體驗證數據，A **必須逐一念出標的名稱與精確數字**（如漲跌幅或成交量）：
  - 命中 → A「昨天說XXX，今天漲了X%，預測成立。」B「博士這次沒騙我，四個全中！」
  - 失準 → A「昨天說XXX，今天跌了X%，分析失準，原因推測是...[據實說]」B「說錯了你敢說，這樣我才信你！」
  - 若明確標示無明牌 → A「昨天沒有明確推薦，今天無可驗收，直接進分析。」
- **這段的目的是建立信譽。說錯就說錯，命中就必須把數字講清楚。**

### 【段落 1】今日最大異常：一句話吊住聽眾
- **開場白（必要）**：A：「Welcome to『AI帶你看股市』，只看數據，不看情緒。」
- **立刻切入今日最強異常**——從數據中找最反直覺的現象：
  - 成交量前幾名和外資買超是否矛盾？
  - 大盤漲/跌，但某個現象違反直覺？
  - 族群內部有沒有逆勢個股分歧？
- A 用一句話點出矛盾（30秒以內），後面所有分析圍繞它展開。
- B 根據今日盤面情緒真實回應（不是制式「哇」）。

### 【段落 2】偵探展開：成交量帶路 → 法人對比 → 一致或矛盾
- A 帶著 B 一起「調查」：
  1. 今日成交量前幾名是誰？（只舉 2-3 支，不要唸清單）
  2. 外資買賣超說什麼？和成交量一致嗎？
  3. **強制檢查「籌碼與量價背離現象」**：若數據中提到有背離（例如某股股價大漲但外資大賣），A 必須點出這件「奇怪的事情」並深入探討矛盾點，引導出「短線追高、外資趁機出貨」或「外資逢低建倉」等警示。
- B 問出散戶最想問的問題（根據今日異常量身設計，不套公版）
- 族群分析：只在有 3 支以上同向數據時說「族群性」，否則說「部分個股」。每族群最多舉 1-2 支例子。

### 【段落 3】今日唯一重要結論 + AI 數據觀察焦點
- A 說出今集「唯一重要結論」——一句話，讓聽眾明天起床還記得。
- B：「博士，今天有沒有什麼是我明天值得特別盯著看的？」
- AI 觀察焦點（最多 1 支，嚴格門檻）：
  - 條件：外資連續多日買超 OR 爆量突破（vol_ratio > 2x）OR 跌深有籌碼保護
  - 禁止：台積電（2330）、鴻海（2317）、聯發科（2454）
  - 若無符合 → A「今天數據沒有安全標的，現金為王，等訊號。」
  - 若有推薦 → A 說停損點（「跌破MA20就出場」）

### 【段落 4】操作建議 + 心態結尾
- 穩健型：[填入穩健策略]
- 波段型：[填入波段策略]
- A 說一句根據今日盤面、散戶最容易犯的錯（追高？砍底？）{ad_instruction}
- **結語（必要）**：「記住：AI帶你看股市，只看數據，不看情緒。覺得有收穫就訂閱分享，留言告訴我們你今天在關注什麼！下次見！」

---

## 🚫 絕對規則（違反 = 失敗）
1. 數字必須與驗證數據完全一致，不得捏造
2. 大數字與股票代號語音防呆：對於超過百萬的龐大數字或股票代號，必須強制寫出正確的中文發音括號（如：1,200,000 (一百二十萬)，2317 (二三一七)）以防語音念錯。
3. 族群歸屬不得混淆（一支股票跌 ≠ 整族群跌）
4. 每族群最多舉 1-2 支個股，不要唸清單
5. **B 的情緒反應每集必須根據今天盤面量身設計，禁止固定台詞**
6. 商品（黃金、油）解讀必須加「可能原因之一」，不得直接下定論
7. **PHONETIC：2409 必須念「友達」，9910 必須念「豐泰」等重要正音**
8. **嚴格語言與口音限制：全程必須使用繁體中文，且發音必須是標準的台灣國語口音（Taiwanese Mandarin）。嚴禁出現中國大陸用語與兒化音。**

"""

    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Error generating NotebookLM prompt: {e}"


def generate_weekend_special_prompt(api_key, structured_data, date_str=None):
    """
    Generates a NotebookLM Podcast Prompt for the Weekend Special (US Market & Macro Impact).
    Hosts speak in Traditional Chinese. Uses detective storytelling format.
    """
    genai.configure(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    model = genai.GenerativeModel(model_name)

    if not date_str:
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")

    # Format sectors locally
    sectors_formatted_ws = ""
    for sector, stocks in structured_data.get("sectors", {}).items():
        sectors_formatted_ws += f"  - [族群: {sector}]: {', '.join(stocks)}\n"

    prompt = f"""你是一個台股財經 Podcast 腳本設計師，專為週末特別節目撰寫提示詞。

--- 本週驗證數據 ---
日期：{structured_data.get('date', date_str)}
大盤狀況：{structured_data.get('index_action', 'N/A')}
資金撤出：{', '.join(structured_data.get('heavyweights_dumped', []))}
資金流入：{', '.join(structured_data.get('safe_havens_bought', []))}
商品行情：{', '.join(structured_data.get('commodities', []))}
AI 觀察焦點：{', '.join(structured_data.get('ai_data_picks', []))}

族群與個股：
{sectors_formatted_ws}
穩健策略：{structured_data.get('conservative_strategy', 'N/A')}
波段策略：{structured_data.get('aggressive_strategy', 'N/A')}
-----------------------------------------------------------

請根據以上數據，產出週末特別版提示詞（僅輸出最終 Markdown 內容）：

---

# 週末特別版 NotebookLM Prompt：{date_str} 本週台股深度覆盤

你正在製作週末特別版台股財經 Podcast。重點：本週盤勢是什麼故事？下週要注意什麼？全程繁體中文。

---

## 🎙️ 主持人設定

**主持人 A（數據張博士・偵探派）**：冷靜分析，找本週最大資金脈絡，帶出「下週觀察方向」。

**主持人 B（熱血阿明・散戶代言人）**：代表散戶「這週被市場整了嗎？」的心理，情緒根據本週盤面決定。

---

## 🎬 週末特別版結構

### 【段落 1】本週盤面最大故事
- 不要逐日回顧，直接找本週「最反直覺的數據現象」當開場：
  - 哪個族群本週最強？成交量支持嗎？
  - 本週外資行為和盤面一致嗎？
- B 的反應根據本週盤面情緒設計（大漲週 vs 大跌週 vs 詭異盤整週）

### 【段落 2】資金脈絡：本週的錢從哪裡來，往哪裡去？
- 用成交量數據說話，不用主觀敘事
- 族群分析：只在有 3 支以上同向數據時說「族群性」，否則說「部分個股」

### 【段落 3】下週前瞻：一個具體觀察方向
- A 提出下週最值得關注的ONE個變數（事件、技術位、或數據）
- B 問：「那我週一開盤第一件事要做什麼？」A 給出具體建議。

### 【段落 4】週末作業 + 結語
- 穩健策略：[填入穩健建議]
- 波段策略：[填入波段建議]
- **結語**：「記住：AI帶你看股市，只看數據，不看情緒。週末好好休息，下週見！」

---

## 🚫 絕對規則
1. 數字必須與驗證數據完全一致
2. 族群歸屬不得混淆
3. 每族群最多舉 1-2 支個股
4. B 的情緒必須根據本週真實盤面設計
5. 商品解讀必須加「可能原因之一」
6. **嚴格語言與口音限制：全程必須使用繁體中文，且發音必須是標準的台灣國語口音（Taiwanese Mandarin）。嚴禁出現中國大陸用語與兒化音。**

"""

    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Error generating weekend NotebookLM prompt: {e}"
