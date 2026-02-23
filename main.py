import os
import datetime
import glob
from dotenv import load_dotenv
from modules.data_fetcher import DataFetcher
from modules.analyzer import MarketAnalyzer

# Load environment variables from .env
load_dotenv()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Target Tickers (Taiwan Local Sectors)
TARGET_SYMBOLS = ["^TWII", "2330.TW", "2454.TW", "2382.TW", "3231.TW", "2376.TW", "2603.TW"]
TARGET_TOPICS = ["Taiwan Semiconductor", "AI Server Supply Chain", "Taiwan Shipping Industry"]

# Reports directory
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


def find_previous_report():
    """Find the most recent report file for historical comparison."""
    if not os.path.exists(REPORTS_DIR):
        return None
    
    reports = sorted(glob.glob(os.path.join(REPORTS_DIR, "daily_report_V21_*.md")))
    if reports:
        latest = reports[-1]
        print(f"  找到前日報告: {os.path.basename(latest)}")
        return latest
    return None


def main():
    print(f"{'='*50}")
    print(f"  智慧財經新聞助理 (V21 Pro)")
    print(f"  日期: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}\n")
    
    if not TAVILY_API_KEY or not GEMINI_API_KEY:
        print("[Error] 請在 .env 檔案中設定 TAVILY_API_KEY 和 GEMINI_API_KEY")
        return
    
    # Initialize Modules
    fetcher = DataFetcher(TAVILY_API_KEY)
    analyzer = MarketAnalyzer(GEMINI_API_KEY)
    
    # ========================================
    # 1. Fetch Market Data (with Technical Indicators)
    # ========================================
    market_data = {}
    print("📊 正在獲取股票數據與技術指標...")
    for symbol in TARGET_SYMBOLS:
        data = fetcher.get_stock_data(symbol)
        if data:
            market_data[symbol] = data
            indicator_str = ""
            if data.get('ma5'):
                indicator_str += f" MA5={data['ma5']}"
            if data.get('ma20'):
                indicator_str += f" MA20={data['ma20']}"
            if data.get('rsi') is not None:
                indicator_str += f" RSI={data['rsi']}"
            print(f"  ✅ {symbol}: ${data['price']} ({data['pct_change']:+.2f}%){indicator_str}")
        else:
            print(f"  ❌ {symbol}: 失敗")
    
    # ========================================
    # 2. Fetch Institutional Data (三大法人 — Top 10 動態排名)
    # ========================================
    institutional_data = fetcher.get_institutional_data(top_n=10)
    has_inst_data = institutional_data.get("top_buy") or institutional_data.get("top_sell")
    if not has_inst_data:
        print("  ⚠️  無法取得三大法人數據（可能尚未發布）")
            
    # ========================================
    # 3. Fetch News
    # ========================================
    news_data = []
    print("\n📰 正在獲取新聞...")
    
    for topic in TARGET_TOPICS:
        query = f"{topic} market news today"
        results = fetcher.get_news(query)
        if results:
            news_data.extend(results)
            print(f"  ✅ {topic}: 找到 {len(results)} 篇文章")
        else:
            print(f"  ⚠️  {topic}: 未找到文章")
            
    # Stock-specific news
    for symbol in TARGET_SYMBOLS[:2]:
        query = f"{symbol} stock news today"
        results = fetcher.get_news(query)
        if results:
            news_data.extend(results)

    # Deduplicate
    unique_news = list({n['url']: n for n in news_data}.values())
    print(f"\n  📋 獨特新聞文章總數: {len(unique_news)}")
    
    # ========================================
    # 4. Historical Comparison
    # ========================================
    print("\n📁 檢查歷史報告...")
    prev_report = find_previous_report()
    
    # ========================================
    # 5. Generate AI Report
    # ========================================
    print("\n🤖 正在使用 Gemini 生成 AI 報告...")
    report_content = analyzer.generate_report(
        market_data, 
        unique_news, 
        institutional_data=institutional_data,
        prev_report_path=prev_report
    )
    
    # ========================================
    # 6. Save Report
    # ========================================
    os.makedirs(REPORTS_DIR, exist_ok=True)
    filename = f"daily_report_V21_{datetime.datetime.now().strftime('%Y-%m-%d')}.md"
    filepath = os.path.join(REPORTS_DIR, filename)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(report_content)
        
    print(f"\n✅ 報告已儲存至: {filepath}")
    print("完成!")

if __name__ == "__main__":
    main()
