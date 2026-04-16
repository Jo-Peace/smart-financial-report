import os
import google.generativeai as genai
import datetime
import time
import json

class MarketAnalyzer:
    def __init__(self, api_key):
        genai.configure(api_key=api_key)
        # 讀取環境變數，本地端預設可設為 gemini-pro-latest，網站端若未設定則預設使用 gemini-flash-latest
        model_name = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
        self.model = genai.GenerativeModel(model_name)
        self.stock_db = self._load_stock_db()

    def _load_stock_db(self):
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "stock_db.json")
        try:
            with open(db_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Warning] Could not load stock_db.json: {e}")
            return {}


    def _call_gemini_with_retry(self, prompt, max_retries=3):
        """
        Calls Gemini with exponential backoff retry on 429/5xx errors.
        Retries: 10s, 30s, 60s
        """
        wait_times = [10, 30, 60]
        
        for attempt in range(max_retries + 1):
            try:
                response = self.model.generate_content(prompt)
                return response.text
            except Exception as e:
                error_str = str(e)
                is_retryable = "429" in error_str or "500" in error_str or "503" in error_str
                
                if is_retryable and attempt < max_retries:
                    wait = wait_times[attempt]
                    print(f"  [Retry] Gemini API 錯誤 (嘗試 {attempt + 1}/{max_retries})，等待 {wait} 秒後重試...")
                    time.sleep(wait)
                else:
                    return f"Error generating report: {e}"

    def generate_report(self, market_data, news_data, institutional_data=None, prev_report_path=None, commodity_data=None, macro_events=None, tech_catalyst_events=None, volume_data=None):
        """
        Generates a Markdown report using Gemini with enhanced data.
        """
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        
        # === Stock Data Summary ===
        data_summary = ""
        for symbol, data in market_data.items():
            if data:
                line = f"- {symbol}: 價格 {data['price']}, 漲跌 {data['change']} ({data['pct_change']}%), 成交量 {data['volume']} (5日均量 {data.get('avg_vol_5d', 'N/A')}, 量增比 {data.get('vol_ratio', 'N/A')}x)"
                if data.get('ma5'):
                    line += f", MA5={data['ma5']}"
                if data.get('ma20'):
                    line += f", MA20={data['ma20']}"
                if data.get('rsi') is not None:
                    line += f", RSI={data['rsi']}"
                data_summary += line + "\n"
            else:
                data_summary += f"- {symbol}: 數據無法取得\n"
                
        # === News Summary ===
        news_summary = ""
        for item in news_data:
            news_summary += f"- {item['title']} ({item['url']})\n"
            
        # === Institutional Data Summary (with dollar amounts) ===
        inst_summary = ""
        if institutional_data and (institutional_data.get("top_buy") or institutional_data.get("top_sell")):
            data_date = institutional_data.get("data_date", "未知")
            inst_summary = f"\n# 三大法人買賣超（依金額排序 - 資料日期：{data_date}）\n"
            inst_summary += "\n## 外資買超前10名（依估計金額排序）\n"
            for s in institutional_data.get("top_buy", []):
                amt = s.get('est_amount', 0)
                amt_str = f", 估計金額 {amt:+.1f}億" if amt else ""
                inst_summary += f"- {s['id']} {s['name']}: 外資 {s['foreign_net']:+,}股, 投信 {s['trust_net']:+,}股, 合計 {s['total_net']:+,}股{amt_str}\n"
            inst_summary += "\n## 外資賣超前10名（依估計金額排序）\n"
            for s in institutional_data.get("top_sell", []):
                amt = s.get('est_amount', 0)
                amt_str = f", 估計金額 {amt:+.1f}億" if amt else ""
                inst_summary += f"- {s['id']} {s['name']}: 外資 {s['foreign_net']:+,}股, 投信 {s['trust_net']:+,}股, 合計 {s['total_net']:+,}股{amt_str}\n"
        
        # === Commodity Data ===
        commodity_summary = ""
        if commodity_data:
            commodity_summary = "\n# 國際商品行情\n"
            for sym, cdata in commodity_data.items():
                commodity_summary += f"- {cdata['name']}: ${cdata['price']} ({cdata['pct_change']:+.2f}%)\n"
        
        # === Macro Events ===
        macro_summary = ""
        if macro_events:
            macro_summary = "\n# 近期全球重要總經事件與財曆 (Macro Calendar)\n"
            for item in macro_events:
                macro_summary += f"- {item['title']} ({item['url']})\n"
                
        # === Tech Catalyst Events ===
        catalyst_summary = ""
        if tech_catalyst_events:
            catalyst_summary = "\n# 重大科技/總經催化劍事件（趁近 14 天）\n"
            for ev in tech_catalyst_events:
                catalyst_summary += f"- {ev['event']}\n  └→ {ev['snippet']}\n"

        # === Volume Ranking Data (今日成交量排名 - 真實市場投票) ===
        volume_summary = ""
        if volume_data:
            volume_summary = "\n# 📊 今日成交量前20名（真實市場行動，分析必須以此為主軸）\n"
            volume_summary += "⚠️ 警示：這才是今日市場『最有共識』的實際行動。外資買超金額≠成交量。\n"
            for s in volume_data[:20]:
                volume_summary += f"- 第{s['rank']}名: {s['id']} {s['name']} | 成交量 {s['volume']:,} 股 | 收盤 {s.get('close_price', 'N/A')} | 漲跌 {s.get('pct_change', 0):+.2f}%\n"

        # === Historical Comparison ===
        hist_section = ""
        if prev_report_path:
            try:
                with open(prev_report_path, "r", encoding="utf-8") as f:
                    prev_content = f.read()
                hist_section = f"\n# 前日報告（供昨日預測驗收用）\n{prev_content[:3000]}\n"
            except Exception:
                hist_section = ""
            
        # 廣告插播：僅周四放送
        is_thursday = datetime.datetime.now().weekday() == 3  # 0=Mon, 3=Thu
        if is_thursday:
            ad_instruction = "           - 範例語氣：「插播一下！如果你覺得每天這個客製化的 AI 財經報告對你有幫助，這套系統現在開放免費測試中！**免費試用連結已經放在說明欄**，另外提醒，這個網站功能還在 Beta 階段，分析結果僅供參考，不構成投資建議喔！好，我們回到個股新聞...」"
        else:
            ad_instruction = "           - 今日非週四，請「完全略過」此廳播段落，不要在報告中出現任何廣告詞外內容。"
        # === Dynamic Stock Database (Lazy Loading) ===
        # Extract active tickers from today's data to avoid bloating the prompt
        active_tickers = set()
        
        # from market_data keys e.g. "2330.TW"
        for symbol in market_data.keys():
            active_tickers.add(symbol.replace('.TW', '').replace('.TWO', ''))
            
        # from volume_data
        if volume_data:
            for s in volume_data:
                active_tickers.add(str(s['id']))
                
        # from institutional_data
        if institutional_data:
            for s in institutional_data.get('top_buy', []):
                active_tickers.add(str(s['id']))
            for s in institutional_data.get('top_sell', []):
                active_tickers.add(str(s['id']))
                
        stock_db_str = ""
        if self.stock_db:
            appended_count = 0
            for sid, info in self.stock_db.items():
                if sid in active_tickers:
                    stock_db_str += f"- {sid} {info.get('name', '')}：分類為「{info.get('sector', '')}」\n"
                    stock_db_str += f"  └→ 角色：{info.get('supply_chain_role', '')}\n"
                    not_class = "、".join(info.get('not_classify_as', []))
                    if not_class:
                        stock_db_str += f"  🚫 絕對禁止分類為：{not_class}\n"
                    appended_count += 1
            if appended_count == 0:
                stock_db_str = "（今日主力個股尚無特殊之自訂分類，請依常理判斷）"
        else:
            stock_db_str = "（目前資料庫為空，請依常理判斷）"
            
        # === Second Brain: Historical Knowledge Wiki ===
        wiki_str = ""
        wiki_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "knowledge_wiki")
        if os.path.exists(wiki_dir):
            for filename in os.listdir(wiki_dir):
                if filename.endswith(".md"):
                    try:
                        with open(os.path.join(wiki_dir, filename), "r", encoding="utf-8") as f:
                            wiki_str += f"\n=== {filename} ===\n{f.read()}\n"
                    except Exception as e:
                        print(f"  [Warning] 無法讀取 Wiki 檔案 {filename}: {e}")
        if not wiki_str.strip():
            wiki_str = "（尚未累積任何歷史知識）"

        prompt = f"""
你是一個台股財金節目的「首席偵探型分析師」。
你的工作不是寫報告。是找到今天「最讓人意外的數據現象」，然後用說故事的方式把它說得讓人無法跳過。

日期：{date_str}（繁體中文，數字需加括號標注中文讀音）

================================================================
【原始數據輸入——這些是事實，禁止在事實之外捏造】
================================================================

## 今日成交量前20名（市場真實行動，最重要的數據）
{volume_summary if volume_summary else '（成交量數據未能取得，改以外資買超數據為輔）'}

## 個股技術指標（含均線與量增比）
{data_summary}

## 三大法人數據
{inst_summary}

## 國際商品
{commodity_summary}

## 新聞資訊
{news_summary}

## 催化劑事件
{catalyst_summary}

## 前日報告（用於昨日預測驗收）
{hist_section}

================================================================
【我們的內部私有財經歷史記憶庫 (The Second Brain)】
================================================================
這些是我們在過去追蹤到的市場敘事、大事件、資金輪動趨勢。
你在分析今日數據時，必須以此為「既有認知基礎」。
- 如果今天的數據「延續」了記憶庫裡的趨勢，請在報告中點出這個連貫性（例如「正如我們此前的觀察...」）。
- 如果今天的數據「打破或反轉」了過去的敘事，這就是今日「最強異常」的絕佳題材！
{wiki_str}

================================================================
STEP 0【昨日預測驗收】最高優先，必須第一個出現，不得省略
================================================================
從前日報告的「AI 數據抓漏」段落提取預測標的，與今日實際表現誠實比對：
- 命中：寫「✅ 昨日預測 XXX，今日實際 +X%，預測成立。」
- 失準：寫「❌ 昨日預測 XXX，今日實際 -X%，預測失敗。原因推測：[具體分析]」
- 無可驗收：寫「本日無昨日預測標的可驗收。」
此段是頻道信譽的命脈。失準時不得省略或美化。

================================================================
STEP 1【找出今日的最強異常】整集的靈魂，只選一個現象
================================================================
從以下類型找出最反直覺、最有衝突感的數據現象：
A) 量價矛盾：大成交量但股價小漲（分配訊號）？縮量但大漲（假突破）？
B) 法人 vs 市場矛盾：外資大買的股票，成交量排名卻在20名以外？
C) 族群內部分歧：多支同族群個股同向，但關鍵個股逆勢？（需3支以上才能說「族群」）
D) 技術位攻防：MA20、月線有無被突破或失守？
E) 昨日預測 vs 今日現實的反差

選出最值得追問的ONE個。所有後續分析圍繞它展開。

================================================================
STEP 2【今日勾魂開場句——讓人停下來的問題】
================================================================
用最強異常設計一個帶衝突感的問題，例如：
「外資今天買進台積電 358 億，但成交量前三名沒有台積電。那錢去哪了？」
「大盤漲了X點，但成交量冠軍是一支下跌的股票。誰在逆勢操作？」
「昨天我們說 XXX 會漲，但它今天跌了。判斷哪裡出了問題？」

必須：(1) 基於真實數據 (2) 有內在衝突或懸念 (3) 讓人想知道答案

================================================================
【台股供應鏈族群知識庫——必須熟記，不得用產品名稱亂分類】
================================================================
以下是台股供應鏈的正確分類字典，當分析到這些股票時，必須強制使用這裡的「供應鏈角色」：

{stock_db_str}

📌 正確的族群分類原則：
「這家公司的產品，有沒有進入AI伺服器的BOM表（物料清單）？」
→ 有 = AI供應鏈族群；沒有 = 才是傳產

================================================================
STEP 3【偵探式展開——成交量帶路，法人對比，新聞輔助】
================================================================
順序：
1. 成交量前10名完整列表（這才是市場真正的投票結果）
2. 外資買賣超對比（機構的官方說法）
3. 兩者一致？→ 趨勢確認。兩者矛盾？→ 這才是最值得挖掘的地方
4. 新聞事件作補充（只是「可能原因之一」，不是確定答案）
5. 族群分析（只在有3支以上個股同向時，才能下「族群性」結論）
6. 【強制應用上方知識庫】：分析個股所屬族群時，先比對知識庫，用「供應鏈角色」分類，而非產品外觀

================================================================
🚫 五條鐵律，違反即失敗：
================================================================


1. 不能用單一個股代表整個族群
2. 黃金漲不等於 Fed 避險；油跌不等於需求崩潰，要加「可能原因之一」
3. 數據無法解釋的現象，直接寫「目前無法判斷，需持續觀察」，禁止捏造
4. 詞彙每週各限一次：「史詩級修復」「板塊重新定價」「機構級建倉」「主升段點火」
5. 股價剛從MA20下站上→用「跌深強彈」「底部成型」；已站穩且持續創高→才能用「主升段確認」

================================================================
STEP 4【今日唯一重要結論——觀眾明天還記得的那句話】
================================================================
整集只允許一個核心結論。必須具體、有可驗證的方向、和明天的行動有關。
例如：「今天的關鍵不在台積電漲多少，而在成交量前3名是面板和衛星股，
但法人偏偏在賣這些——這種量價背離，通常是散戶接最後一棒前的最後警告。」

================================================================
STEP 5【明日觀察焦點——AI 數據抓漏，嚴格門檻】
================================================================
推薦1支（最多2支）明日值得追蹤標的，需符合至少一項：
- 外資連續多日買超（不只今日單日）
- 成交量異常放大且爆量突破（vol_ratio > 2x）
- 跌深有籌碼保護（外資買超 + 技術支撐）

禁止推薦台積電（2330）、鴻海（2317）、聯發科（2454）等超級權值股。
若無符合標的：直接說「今日數據無安全明牌，現金為王，靜待訊號。」
若有推薦：給出停損點（例如：「收盤跌破 MA20 出場」）。

================================================================
STEP 6【技術位與商品——簡短補充，不是主角】
================================================================
商品每行一句，必須加「可能反映了...，需後續驗證」。
技術位：哪支股票今天剛突破或跌破 MA20？為什麼重要？

================================================================
【輸出格式——Markdown，繁體中文，正文數字加中文讀音括號，表格內不需要】
================================================================

## 〔0〕昨日預測驗收

---

## 〔今日最強異常〕[直接把勾魂問題當標題]
（1-2段，150字以內）

---

## 〔成交量排名〕今日最真實的市場投票
| 排名 | 代號 | 名稱 | 成交量 | 漲跌幅 | 今日角色 |
（前10名，最後欄說明它在今日故事中的意義）

---

## 〔法人籌碼〕外資買賣超（資料日期：XXXX-XX-XX）
（前10買超 + 前10賣超，標注估計金額）

---

## 〔量價對比〕法人說的 vs 市場做的
（成交量排名 vs 外資買超的一致或矛盾——這是今集最有料的段落）

---

## 〔資金脈絡〕錢從哪來、往哪去
（根據真實數據說一個有頭有尾的資金故事）

---

## 〔技術位快報〕誰剛突破、誰剛失守 MA20
（每項最多一行，商品加「可能反映了...，需驗證」）

---

## 〔今日唯一重要結論〕
（100字以內，讓觀眾明天還記得的那句話）

---

## 〔明日觀察焦點〕AI 數據抓漏
（標的 + 理由 + 停損點，或說「今日無安全明牌，現金為王。」）

---

## 〔操作建議〕
- **穩健型**：...
- **波段型**：...
- **防禦股驗證**：找跌幅 < -0.5% 且 vol_ratio < 1.2x 的個股。找不到就說「今日無防禦角落，現金為王。」
{ad_instruction}

---

語氣：像一個真正在查案的人。好奇、謹慎、偶爾有「我看懂了！」的興奮感。
不知道的地方直接說不知道——這比假裝知道更有說服力，也更讓人信任。
        """


        return self._call_gemini_with_retry(prompt)


    def generate_weekend_special_report(self, market_data, news_data, commodity_data=None, macro_events=None):
        """
        Generates a Weekend Special Markdown report focusing on the US Market and Geopolitical events.
        Crucially, this report is generated entirely in TRADITIONAL CHINESE.
        """
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        
        # === US Stock Data Summary (Weekly View) ===
        data_summary = ""
        for symbol, data in market_data.items():
            if data:
                line = f"- {symbol}: 本週收盤 ${data.get('week_close', data.get('price'))}, 本週漲跌 {data.get('week_change', 'N/A')} ({data.get('week_pct_change', 'N/A')}%), 本週高點 ${data.get('week_high', 'N/A')}, 本週低點 ${data.get('week_low', 'N/A')}"
                if 'daily_series' in data and data['daily_series']:
                    friday = data['daily_series'][-1]
                    line += f"  [週五單日表現: 價格 ${friday['close']}, 跌幅 {friday['pct_change']}%]"
                data_summary += line + "\n"
            else:
                data_summary += f"- {symbol}: Data unavailable\n"
                
        # === News Summary ===
        news_summary = ""
        for item in news_data:
            news_summary += f"- {item['title']} ({item['url']})\n"
        
        # === Commodity Data ===
        commodity_summary = ""
        if commodity_data:
            commodity_summary = "\n# Global Commodities & Safe Havens\n"
            for sym, cdata in commodity_data.items():
                commodity_summary += f"- {cdata['name']}: ${cdata['price']} ({cdata['pct_change']:+.2f}%)\n"
        
        # === Macro Events ===
        macro_summary = ""
        if macro_events:
            macro_summary = "\n# Macro Events & Geopolitics\n"
            for item in macro_events:
                macro_summary += f"- {item['title']} ({item['url']})\n"
                
        prompt = f"""
        You are an elite Wall Street macro analyst and geopolitical strategist.
        Create a "Weekend Special" financial report for {date_str}.
        CRITICAL: The entire report MUST BE WRITTEN IN TRADITIONAL CHINESE (繁體中文).
        
        # US Indices & Key Stock Data (Weekly Performance + Friday Action)
        {data_summary}
        
        # Global News (Geopolitics & Market Drops)
        {news_summary}
        {commodity_summary}
        # Macro & Geopolitical Events
        {macro_summary}
        
        # Report Requirements (Elite Macro Perspective)
        
        1. **一週總結與週五血洗 (The Weekly Story & Friday Bloodbath)**: 開篇以總體角度回顧本「整週」美股的情境變化（參考 S&P 500 ^GSPC 與 Nasdaq ^IXIC 的全週漲跌幅），並將焦點強力收束在地緣政治爆發導致的「週五大拋售」。分析恐慌指數 VIX 的飆升。不要只寫週五，要寫出「一週總結+週五恐慌拋售」的層次感。
        
        2. **戰火陰霾 (The War Narrative & Safe Havens)**: 將股市下跌與提供的地緣政治/戰爭新聞強烈連結。
           - 點出避險資產（黃金、原油、白銀）的異動，資金是否正在逃命？
           - 帶入歷史借鑑。這跟 1991 波灣戰爭或 2022 烏俄戰爭有何異同？提及「買在砲聲隆隆時」的華爾街概念。
           
        3. **科技股重災區 (Tech Damage)**: 針對輝達 (NVDA) 與台積電 ADR (TSM) 的全週跌幅與週五重摔做深度點評。為何最賺錢的公司最先被提款？（解釋流動性變現與去槓桿的邏輯）。
        
        4. **台股週一生存指南 (Monday Survival Guide)**: 
           - 基於美股大逃殺與戰火敘事，預判下週一台股開盤的「心理面與結構面」衝擊。
           - 哪些台股族群是絕對的「重災區」（如高估值 AI 硬體）？
           - 哪些又是資金可能的「避風港」（如防禦型、內需、原物料）？
           
        5. **週末實戰策略 (Weekend Strategy Checklist)**: 結合以上分析，給出兩套具體的週末沉澱與下週應對策略：
           - **防禦姿態 (The Defensive Posture)**: 資金該往哪裡躲？
           - **禿鷹戰法 (The Vulture Strategy)**: 觀察哪些明確指標，才知道恐慌殺盤已竭盡，可以開始大口咬進優質錯殺股？
           
        Please generate the comprehensive Traditional Chinese Markdown report now.
        """
        
        return self._call_gemini_with_retry(prompt)
