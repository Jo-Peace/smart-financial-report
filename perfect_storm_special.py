import os
import datetime
import yfinance as yf
from tavily import TavilyClient
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv("/Users/shenghanchou/Desktop/stock/smart_financial_report/.env")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)
# Use gemini-2.5-flash since it is a deep analytical task
model = genai.GenerativeModel('gemini-2.5-flash')
tavily_client = TavilyClient(api_key=TAVILY_API_KEY)

print("📊 正在獲取過去一個月 (20個交易日) 的資產趨勢數據...")
assets = {
    "^TWII": "台股加權指數",
    "2330.TW": "台積電",
    "2308.TW": "台達電",
    "2605.TW": "新興 (散裝航運)",
    "GC=F": "黃金",
    "SI=F": "白銀"
}

monthly_data_summary = ""
for symbol, name in assets.items():
    try:
        tk = yf.Ticker(symbol)
        hist = tk.history(period="1mo")
        if not hist.empty:
            start_price = hist['Close'].iloc[0]
            end_price = hist['Close'].iloc[-1]
            high_price = hist['High'].max()
            low_price = hist['Low'].min()
            pct_change = ((end_price - start_price) / start_price) * 100
            monthly_data_summary += f"- {name} ({symbol}): 月初 $ {start_price:.2f} -> 月底 $ {end_price:.2f} (月報酬 {pct_change:+.2f}%)，區間高點 ${high_price:.2f}，區間低點 ${low_price:.2f}\n"
    except Exception as e:
        print(f"Error fetching {symbol}: {e}")

print("📰 正在獲取戰爭滿月、彈劾案、4/6 美國總經事件 新聞...")
news_queries = [
    "Middle East war one month economic impact market",
    "impeachment political instability stock market index",
    "April 6th economic catalyst event Fed market prediction"
]
news_context = ""
for q in news_queries:
    try:
        resp = tavily_client.search(q, search_depth="basic", max_results=3, days=14)
        for r in resp.get("results", []):
            news_context += f"- Title: {r['title']}\n  Content: {r['content'][:200]}...\n"
    except Exception as e:
        print(f"Tavily Error: {e}")

print("🤖 正在生成長篇專題深度分析報告與 NotebookLM 腳本...")
prompt = f"""
你是一位深具華爾街總經宏觀視野的「高級資深財務分析師」。
使用者抱怨：「專題不用去抓單日資料，邏輯上應該看一整個月的趨勢！」
這是一份針對【戰爭滿月、彈劾案與 4/6 的完美風暴專題】的長篇深度報告與對應的 Podcast 腳本。

=== 【過去一個月的大盤與資產趨勢數據】 ===
{monthly_data_summary}

=== 【國際總經與政治新聞脈絡】 ===
{news_context}

請產出兩份文件（合併在同一個輸出中，用 '#######' 分隔）：

第一部分：【台股與全球宏觀專題深層報告】
請根據上方「一整個月的數據與波動」，寫出深度長篇報告。不要流水帳報明牌。
必須涵蓋：
1. 戰爭滿月：原物料與黃金白銀在過去一個月的避險吸血效應。
2. 彈劾案的政治風險：為何導致外資「連續一個月」對科技股與權值股進行恐慌性風險重估。
3. 4/6 轉折點：4/6 前的市場將如何波動？散戶該保留多少現金位階？
4. 資金避風港的月度表現：例如新興 (2605) 等傳產/航運為何在一個月內逆勢抗跌。

第二部分：【NotebookLM Podcast 腳本指令】
請寫下一份給 NotebookLM 的語音腳本指令。
- 主持人 A（數據張博士）、主持人 B（熱血阿明）。
- **必須緊扣上面這一個月的宏觀數據，而不是只講今天的點數。**
- 段落 1：回顧這驚心動魄的「戰爭滿月」。
- 段落 2：政治黑天鵝「彈劾案」對外資心理的打擊。
- 段落 3：解析過去一個月，錢怎麼從台積電流向黃金與散裝航運。
- 段落 4：4/6 完美風暴的最後通牒與操作建議。
- 提醒：遇到 2409 請唸"友達"。

字數要豐富、觀點要極度宏觀，具備真正「特別企劃專題」的質感。
"""

response = model.generate_content(prompt)
output_text = response.text

reports_dir = "/Users/shenghanchou/Desktop/stock/smart_financial_report/reports"
os.makedirs(reports_dir, exist_ok=True)
date_str = datetime.datetime.now().strftime("%Y-%m-%d")

parts = output_text.split("#######")
if len(parts) >= 2:
    report_part = parts[0].strip()
    prompt_part = parts[1].strip()
else:
    report_part = output_text
    prompt_part = "解析失敗，請見完整報告"

with open(f"{reports_dir}/perfect_storm_report_{date_str}.md", "w") as f:
    f.write(report_part)
with open(f"{reports_dir}/notebooklm_prompt_perfect_storm_{date_str}.md", "w") as f:
    f.write(prompt_part)

print(f"✅ 專題報告與腳本生成完畢！")
