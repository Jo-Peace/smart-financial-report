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

    def generate_report(self, market_data, news_data, institutional_data=None, prev_report_path=None):
        """
        Generates a Markdown report using Gemini with enhanced data.
        """
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        
        # === Stock Data Summary ===
        data_summary = ""
        for symbol, data in market_data.items():
            if data:
                line = f"- {symbol}: 價格 {data['price']}, 漲跌 {data['change']} ({data['pct_change']}%), 成交量 {data['volume']}"
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
            
        # === Institutional Data Summary ===
        inst_summary = ""
        if institutional_data and (institutional_data.get("top_buy") or institutional_data.get("top_sell")):
            data_date = institutional_data.get("data_date", "未知")
            inst_summary = f"\n# 三大法人買賣超（動態排名 - 資料日期：{data_date}）\n"
            inst_summary += "\n## 外資買超前10名\n"
            for s in institutional_data.get("top_buy", []):
                inst_summary += f"- {s['id']} {s['name']}: 外資 {s['foreign_net']:+,}, 投信 {s['trust_net']:+,}, 合計 {s['total_net']:+,}\n"
            inst_summary += "\n## 外資賣超前10名\n"
            for s in institutional_data.get("top_sell", []):
                inst_summary += f"- {s['id']} {s['name']}: 外資 {s['foreign_net']:+,}, 投信 {s['trust_net']:+,}, 合計 {s['total_net']:+,}\n"
        
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
        {hist_section}
        
        # 報告要求
        1. **市場快照**: 建立 Markdown 表格，欄位包含：代碼, 公司, 價格, 漲跌, 漲跌幅, 成交量, MA5, MA20, RSI。
        2. **三大法人動態**: 若有三大法人數據，分別建立「外資買超前10名」和「外資賣超前10名」兩張表格（欄位：代碼, 公司, 外資買賣超, 投信買賣超, 合計）。【警告】必須明確標示資料的「日期」。如果法人資料日期是昨天，絕對不可將其解釋為「今日」的資金動向！
        3. **技術面分析**: 根據 MA5/MA20 的相對位置（黃金交叉/死亡交叉）和 RSI 數值判斷個股是否超買(>70)/超賣(<30)。
        4. **焦點新聞**: 將新聞整理為分類的重點摘要。特別留意新聞中提到的「逆勢上揚」或「抗跌強勢」族群。
        5. **綜合分析**: 結合價格走勢、技術指標、新聞與法人動向，給出市場研判。【致命錯誤警告】千萬不能只看交易量就判斷是看好或防禦，交易量大也可能是倒貨。必須嚴格基於「股價漲跌」與「新聞」去判斷族群強弱。禁止自行發明或猜測未提供的個股表現（例如：若數據沒有記憶體，且新聞沒提，就不要寫資金流向記憶體）！
        6. **🔍 盤面歸因分析（Why Behind the Move）**: 這是報告中最重要的深度章節。請用「結果 ← 原因」的邏輯，將今日盤面的關鍵現象連結回背後的驅動力。分三個層次分析：
           - **宏觀因素**：國際局勢（關稅、聯準會、地緣政治）、匯率變動、全球資金流向等，是否影響今日盤面？
           - **產業催化劑**：重大法說會、外資報告上下修、產業供需數據（缺貨/庫存）、新產品發表等，哪些是今日類股表現的觸發點？
           - **籌碼與結構因素**：期貨結算日效應、年節假期效應、選擇權最大痛點、融資融券變化等技術性因素。
           每個歸因請寫成一句話格式：「【現象】某某類股大漲/大跌 ← 【原因】因為某某事件/數據/消息」。至少列出 3-5 個歸因。
        {"7. **與前日比較**: 對比今日與前日數據的變化趨勢。" if prev_report_path else ""}
        9. **📢 節目口播贊助（約報告 1 分鐘處）**:
           - 請在「市場快照」或「三大法人動態」結束後，也就是大約播報 1 分鐘的地方，插入一段自然的「口播廣告（ad-read）」。
           - 範例語氣：「在繼續分析焦點新聞之前，插播一下！如果你覺得每天這個客製化的 AI 財經報告對你有幫助，這套系統現在開放免費測試中！**免費試用連結已經放在說明欄**，趕快點擊連結去生成你手中持股的專屬分析報告吧！另外提醒，這個網站功能還在 Beta 測試階段，分析結果僅供參考，不構成投資建議喔！好，我們回到個股新聞...」
        10. **特別企劃呼應 (Callback)**: 這是非常重要的指令！請在報告開頭的引言或適當的分析段落中，用財經 Podcast 主持人的口吻，主動「呼應」我們前幾天發布的「戰爭與地緣政治對股市影響」特別節目。提及諸如 1991 波灣戰爭、2003 美伊戰爭的歷史回測，以及「買在砲聲隆隆時（利空出盡）」的觀念。請將這個觀念巧妙地與今日台股（如台積電、反彈群族或避險資金板塊）的走勢或市場情緒做連結。
        11. **總結與個股點評 (Conclusion & Stock Focus)**: 在結尾處，請具體點名 1 到 2 檔今日表現最具代表性的個股（例如台積電、長榮、或是今日大漲/大跌的個股），並必須將該個股的表現「放大到整個族群與板塊的整體狀況」。請特別分析目前該族群的連動性，例如：(1) **「齊漲齊跌」**（如過去 AI 族群一起爆發），或是 (2) **「族群內部分化」**（例如資金僅集中在 HBM，傳統記憶體卻疲弱）。提醒聽眾注意這兩種不同的現象，有助於聽眾從單一個股判斷整體產業的真實熱度。
        12. **語氣**: 專業、簡潔、客觀。
        13. **格式**: 乾淨的 Markdown。
        13. **數字格式（重要！）**: 在報告正文（非表格）中提及關鍵數字時，必須在阿拉伯數字後加上中文括號標註，以確保語音朗讀正確。範例：
           - 指數：33,605 點（三萬三千六百零五點）
           - 股價：1,915 元（一千九百一十五元）
           - 張數：12,634 張（一萬兩千六百三十四張）
           - 金額：449.6 億美元（四百四十九點六億美元）
           - 表格內的數字不需要加中文標註。
        
        請生成完整報告。
        """
        
        return self._call_gemini_with_retry(prompt)
