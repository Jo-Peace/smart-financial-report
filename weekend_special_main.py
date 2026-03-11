import os
import datetime
import json
from dotenv import load_dotenv
from modules.data_fetcher import DataFetcher
from modules.analyzer import MarketAnalyzer
from modules.notebooklm_generator import generate_weekend_special_prompt
from modules.data_extractor import extract_weekend_structured_data

# Load environment variables
load_dotenv()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Weekend Special Tickers
US_SYMBOLS = ["^GSPC", "^IXIC", "^VIX", "NVDA", "TSM"]
US_TOPICS = [
    "US stock market drop panic sell off reason",
    "Nasdaq SP500 tech stocks plunge",
    "Global Geopolitical tension war impact stock market",
    "Gold crude oil safe haven asset surge war",
    "Taiwan stock market monday open prediction US market drop"
]

REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

def main():
    print(f"{'='*50}")
    print(f"  週末特輯：美股大逃殺與避險生存指南")
    print(f"  日期: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}\n")
    
    if not TAVILY_API_KEY or not GEMINI_API_KEY:
        print("[Error] 請在 .env 檔案中設定 TAVILY_API_KEY 和 GEMINI_API_KEY")
        return
    
    fetcher = DataFetcher(TAVILY_API_KEY)
    analyzer = MarketAnalyzer(GEMINI_API_KEY)
    
    # 1. Fetch US Market Data (Weekly Summary)
    market_data = {}
    print("\n📊 正在獲取美股與避險指標一週動態數據 (S&P500, Nasdaq, VIX, NVDA, TSM)...")
    for symbol in US_SYMBOLS:
        data = fetcher.get_weekly_stock_data(symbol)
        if data:
            market_data[symbol] = data
            print(f"  ✅ {symbol}: 週收盤 ${data['week_close']} (本週漲跌 {data['week_pct_change']:+.2f}%)")
        else:
            print(f"  ❌ {symbol}: 數據獲取失敗")
            
    # 2. Fetch Commodities
    print("\n🛢️  正在獲取國際商品行情 (黃金、原油、白銀)...")
    commodity_data = fetcher.get_commodity_data()
    
    # 3. Fetch Macro Events
    macro_events = fetcher.get_macro_events()
    
    # 4. Fetch Targeted News (War & US Drop)
    news_data = []
    print("\n📰 正在獲取國際總經與戰爭地緣政治新聞...")
    for topic in US_TOPICS:
        results = fetcher.get_news(f"{topic} latest news")
        if results:
            news_data.extend(results)
            print(f"  ✅ {topic[:25]}... : 找到 {len(results)} 篇")
            
    unique_news = list({n['url']: n for n in news_data}.values())
    print(f"\n  📋 獨特新聞文章總數: {len(unique_news)}")
    
    # 5. Generate Weekend Report (in Traditional Chinese)
    print("\n🤖 正在使用 Gemini 生成週末特輯分析報告 (繁體中文)...")
    report_content = analyzer.generate_weekend_special_report(
        market_data,
        unique_news,
        commodity_data=commodity_data,
        macro_events=macro_events
    )
    
    os.makedirs(REPORTS_DIR, exist_ok=True)
    date_str = datetime.datetime.now().strftime('%Y-%m-%d')
    report_filename = f"weekend_special_report_{date_str}.md"
    report_filepath = os.path.join(REPORTS_DIR, report_filename)
    
    with open(report_filepath, "w", encoding="utf-8") as f:
        f.write(report_content)
    print(f"  ✅ 週末報告已儲存: {report_filename}")
        
    # 6. Extract Structured JSON Native
    print("\n📦 正在萃取週末結構化數據 (建構腳本藍圖)...")
    structured_data = extract_weekend_structured_data(GEMINI_API_KEY, report_content)
    
    json_filename = f"weekend_structured_data_{date_str.replace('-', '')}.json"
    json_filepath = os.path.join(REPORTS_DIR, json_filename)
    with open(json_filepath, "w", encoding="utf-8") as f:
        json.dump(structured_data, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 結構化數據已儲存: {json_filename}")
    
    # 7. Generate NotebookLM Prompt for the Weekend
    print("\n🎧 正在生成 NotebookLM 週末特輯 Podcast 腳本指令...")
    podcast_prompt = generate_weekend_special_prompt(GEMINI_API_KEY, structured_data, date_str)
    
    nl_filename = f"weekend_notebooklm_prompt_{date_str.replace('-', '')}.md"
    nl_filepath = os.path.join(REPORTS_DIR, nl_filename)
    
    with open(nl_filepath, "w", encoding="utf-8") as f:
        f.write(podcast_prompt)
        
    print(f"\n✅ 週末 NotebookLM 腳本指令已儲存至: {nl_filepath}")
    print("週末特輯生成完畢！")

if __name__ == "__main__":
    main()
