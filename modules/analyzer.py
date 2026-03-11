import os
import google.generativeai as genai
import datetime
import time

class MarketAnalyzer:
    def __init__(self, api_key):
        genai.configure(api_key=api_key)
        # 讀取環境變數，本地端預設可設為 gemini-pro-latest，網站端若未設定則預設使用 gemini-flash-latest
        model_name = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
        self.model = genai.GenerativeModel(model_name)

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

    def generate_report(self, market_data, news_data, institutional_data=None, prev_report_path=None, commodity_data=None, macro_events=None):
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
                
        # === Historical Comparison ===
        hist_section = ""
        if prev_report_path:
            try:
                with open(prev_report_path, "r", encoding="utf-8") as f:
                    prev_content = f.read()
                hist_section = f"\n# 前日報告（供比較用）\n{prev_content[:2000]}\n"
            except Exception:
                hist_section = ""
            
        prompt = f"""
        You are a professional financial analyst specializing in Taiwan stock market.
        Create a daily financial report for {date_str} in Traditional Chinese (繁體中文) Markdown format.
        
        # 股票數據（含技術指標）
        {data_summary}
        
        # 新聞
        {news_summary}
        {inst_summary}
        {commodity_summary}
        # 總經事件
        {macro_summary}
        {hist_section}
        
        # 報告要求：【華爾街頂級操盤手視角】
        1. **市場快照**: 建立 Markdown 表格，欄位：代碼, 公司, 價格, 漲跌, 漲跌幅, 成交量, 爆量比(量增比), MA5, MA20, RSI。
        2. **三大法人動態**: 建立外資買賣超前10名表格（含估計金額）。必須明確標示資料「日期」。
        
        【以下為頂級分析核心，報告品質好壞完全取決於這三點是否深刻】
        
        3. **籌碼層次與技術面 (Volume Profile & Flow)**: 
           - 不要只報價格和均線。必須分析「量增比 (vol_ratio)」。
           - 如果某檔權值股下跌但爆量 (> 1.5x)，必須判斷這是「恐慌性拋售 (Panic Dump)」還是「主力在關鍵成本區自救護盤 (Institutional Self-Rescue)」。找出誰在接刀、誰在倒貨，結合外資淨額分析。
        
        4. **資金排擠效應 (Sector Rotation / Capital Displacement)**: 
           - 華爾街看的是資金流向。必須根據新聞和股價表現，具體寫出「水庫的水從哪裡被抽走，流向了哪裡」。
           - 【強制要求】如果大盤資金集中在特定族群（例如記憶體反撲、或 CPO 矽光子），必須指出哪些邊緣族群（如衛星網通、航運）因此遭遇「流動性枯竭 / 資金排擠」而下跌。不要單獨解釋下跌，要把漲跌建立在資金挪移的因果關係上。
        
        5. **事件驅動與總經預期 (Event-Driven Expectation)**: 
           - 股市是買預期、賣事實。結合提供的【總經事件】(如 CPI, Fed 決策, 重大財報)。
           - 必須在分析中寫出：「法人今日在台股的佈局（如大買黃金/債券、大賣半導體），是為了防禦/押注即將到來的 XXX 事件」。讓盤面動作與未來日曆掛鉤。
        
        6. **🔍 盤面深層歸因分析（Why Behind the Move）**: 
           用「結果 ← 原因」邏輯，連結上述三點。例如：「【現象】低軌衛星族群無量大跌 ← 【原因】遭遇資金排擠效應，主力資金全面撤出轉往記憶體族群抄底。」
           
        7. **🎯 今日資金匯聚焦點族群與強勢領頭羊**: 
           明確點名今日「吸血」最強的主流族群。
           【強制分析熱門族群】請務必在文中特別關注並追蹤「**記憶體族群 (南亞科/華邦電大反撲)**」、「**矽光子 CPO 族群**」，以及「**低軌衛星網通族群**」的資金流向與當日表現。

        8. **🛢️ 國際商品與避險資產（極度重要）**: 
           若黃金或原油有漲幅，必須開闢段落將其與「這週的總經事件」或「地緣政治戰爭風險」做強制且正面的連結。誇讚資金提早卡位避險的敏銳度。

        9. **💬 專業術語與位階語感拿捏（極度重要）**:
           分析個股暴漲時，必須根據其「股價與 MA20 的關係」來決定用詞：
           - 若股價剛從 MA20 下方站上，或從低檔剛帶量上漲：禁止使用「全面噴發、主升段、狂飆」等詞。請改用「絕地大反攻、主力自救、跌深強彈、帶量突圍、底部成型」。
           - 若股價已經站穩 MA20 上方並持續創高：這時才能使用「全面噴發、狂飆、強勢點火、主升段確認」等詞彙。
           確保報告的語感符合老手對「時間尺度」與「長線位階」的敏銳度，不輕易對初次反彈使用誇大字眼。
           
        9. **📢 節目口播贊助（約報告 1 分鐘處）**:
           - 範例語氣：「插播一下！如果你覺得每天這個客製化的 AI 財經報告對你有幫助，這套系統現在開放免費測試中！**免費試用連結已經放在說明欄**，另外提醒，這個網站功能還在 Beta 階段，分析結果僅供參考，不構成投資建議喔！好，我們回到個股新聞...」
           
        10. **特別企劃呼應 (Callback)**: 
            主動「呼應」前幾天發布的「戰爭與地緣政治對股市影響」特別節目。提及 1991 波灣戰爭、2003 美伊戰爭的歷史回測，以及「買在砲聲隆隆時（利空出盡）」的觀念。
            
        11. **總結與華爾街實戰策略 (Conclusion & Strategy)**: 
            結尾給出兩種不同風格的操作建議：
            - **(1) 機構級建倉（穩健）**：基於法人的籌碼成本區，尋找打底完成、準備承接事件紅利的標的。
            - **(2) 游資狙擊（波段）**：跟隨今日資金排擠流向，尋找資金爆發初期的動能股。
            
        12. **數值讀音要求**: 
            正文（非表格）提及關鍵數字時，必須加中文括號標註。如：33,605 點（三萬三千六百零五點）、1.5 倍（一點五倍）。
        14. **語氣**: 專業、簡潔、客觀。
        15. **格式**: 乾淨的 Markdown。
        16. **數字格式（重要！）**: 在報告正文（非表格）中提及關鍵數字時，必須在阿拉伯數字後加上中文括號標註，以確保語音朗讀正確。範例：
           - 指數：33,605 點（三萬三千六百零五點）
           - 股價：1,915 元（一千九百一十五元）
           - 張數：12,634 張（一萬兩千六百三十四張）
           - 金額：449.6 億美元（四百四十九點六億美元）
           - 表格內的數字不需要加中文標註。
        
        請生成完整報告。
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
