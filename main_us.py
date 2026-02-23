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

# US Market Targets (春節休市期間觀察美股動態)
US_SYMBOLS = [
    "^GSPC",    # S&P 500
    "^IXIC",    # NASDAQ
    "^SOX",     # 費城半導體指數
    "TSM",      # 台積電 ADR
    "NVDA",     # NVIDIA
    "AMD",      # AMD
    "AVGO",     # Broadcom
]

US_TOPICS = [
    "NVIDIA AI chip demand",
    "TSM TSMC ADR stock",
    "US semiconductor industry outlook",
    "Federal Reserve interest rate 2026",
]

# Reports directory
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


def find_previous_us_report():
    """Find the most recent US report for historical comparison."""
    if not os.path.exists(REPORTS_DIR):
        return None
    reports = sorted(glob.glob(os.path.join(REPORTS_DIR, "us_market_report_*.md")))
    if reports:
        latest = reports[-1]
        print(f"  找到前日美股報告: {os.path.basename(latest)}")
        return latest
    return None


def generate_us_prompt(date_str, data_summary, news_summary, hist_section=""):
    """Custom prompt for US market report with Taiwan investor perspective."""
    return f"""
    You are a professional financial analyst helping Taiwan investors track the US market during Lunar New Year break.
    Create a US market OBSERVATION report for {date_str} in Traditional Chinese (繁體中文) Markdown format.
    
    This report is produced during the Taiwan stock market Lunar New Year break (春節休市).
    The goal is to help Taiwan investors TRACK daily changes — NOT to make final predictions,
    since the market may change significantly before TWSE reopens.
    
    # 美股數據（含技術指標）
    {data_summary}
    
    # 相關新聞
    {news_summary}
    {hist_section}
    
    # 報告要求
    1. **美股快照**: 建立 Markdown 表格，欄位：代碼, 名稱, 價格, 漲跌, 漲跌幅, 成交量, MA5, MA20, RSI。
    2. **重點觀察 — 台積電 ADR (TSM)**: 分析 TSM 最新表現與技術指標，說明目前 ADR 相對台股封關價的狀態。
    3. **半導體族群**: 分析費半指數、NVIDIA、AMD、Broadcom 的走勢，點出值得留意的訊號。
    4. **總經觀察**: 分析 S&P 500 與 NASDAQ 的走勢、Fed 利率方向對資金流向的影響。
    5. **觀察筆記（重要語氣指引！）**: 
       - 用「目前觀察到...」「若此趨勢持續...」「值得留意的是...」等語句
       - **嚴禁使用**「預期將...」「必定...」「建議買入/賣出」等斷言式用語
       - 明確提醒：「春節期間美股仍在交易中，趨勢隨時可能反轉，本報告僅為當日觀察紀錄。」
       - 列出「目前對台股有利的訊號」和「目前對台股不利的訊號」兩組，讓觀眾自行判斷
    {"6. **與前日比較**: 用表格對比今日與前日數據的變化，標註趨勢方向。" if hist_section else ""}
    7. **語氣**: 像寫「觀察日記」而非「投資報告」。專業但謙遜，承認不確定性。
    8. **格式**: 乾淨的 Markdown。
    9. **數字格式（重要！）**: 在報告正文（非表格）中提及關鍵數字時，在阿拉伯數字後加上中文括號標註，以確保語音朗讀正確。範例：
       - 指數：5,800 點（五千八百點）
       - 股價：185.50 美元（一百八十五點五美元）
       - 表格內的數字不需要加中文標註。
    
    請生成完整報告。標題使用：「🇺🇸 春節美股觀察日記」。
    """


def main():
    print(f"{'='*50}")
    print(f"  🇺🇸 春節美股觀察報告")
    print(f"  日期: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}\n")
    
    if not TAVILY_API_KEY or not GEMINI_API_KEY:
        print("[Error] 請在 .env 檔案中設定 TAVILY_API_KEY 和 GEMINI_API_KEY")
        return
    
    fetcher = DataFetcher(TAVILY_API_KEY)
    analyzer = MarketAnalyzer(GEMINI_API_KEY)
    
    # ========================================
    # 1. Fetch US Market Data
    # ========================================
    market_data = {}
    print("📊 正在獲取美股數據與技術指標...")
    for symbol in US_SYMBOLS:
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
    # 2. Fetch News
    # ========================================
    news_data = []
    print("\n📰 正在獲取美股相關新聞...")
    for topic in US_TOPICS:
        query = f"{topic} market news today"
        results = fetcher.get_news(query)
        if results:
            news_data.extend(results)
            print(f"  ✅ {topic}: 找到 {len(results)} 篇文章")
        else:
            print(f"  ⚠️  {topic}: 未找到文章")
    
    # Deduplicate
    unique_news = list({n['url']: n for n in news_data}.values())
    print(f"\n  📋 獨特新聞文章總數: {len(unique_news)}")
    
    # ========================================
    # 3. Historical Comparison
    # ========================================
    print("\n📁 檢查歷史報告...")
    prev_report = find_previous_us_report()
    hist_section = ""
    if prev_report:
        try:
            with open(prev_report, "r", encoding="utf-8") as f:
                hist_section = f"\n# 前日報告（供比較用）\n{f.read()[:2000]}\n"
        except Exception:
            pass
    
    # ========================================
    # 4. Prepare Data & Generate Report
    # ========================================
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    
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
    
    news_summary = ""
    for item in unique_news:
        news_summary += f"- {item['title']} ({item['url']})\n"
    
    prompt = generate_us_prompt(date_str, data_summary, news_summary, hist_section)
    
    print("\n🤖 正在使用 Gemini 生成美股觀察報告...")
    report_content = analyzer._call_gemini_with_retry(prompt)
    
    # ========================================
    # 5. Save Report
    # ========================================
    os.makedirs(REPORTS_DIR, exist_ok=True)
    filename = f"us_market_report_{date_str}.md"
    filepath = os.path.join(REPORTS_DIR, filename)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(report_content)
    
    print(f"\n✅ 美股觀察報告已儲存至: {filepath}")
    print("完成!")


if __name__ == "__main__":
    main()
