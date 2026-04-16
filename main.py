import os
import datetime
import glob
from dotenv import load_dotenv
from modules.data_fetcher import DataFetcher
from modules.analyzer import MarketAnalyzer
from modules.notebooklm_generator import generate_notebooklm_prompt

# Load environment variables from .env
load_dotenv()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PROJECT_THEME = os.getenv("PROJECT_THEME", "") # e.g. "中東地緣政治危機" or "GTC 大會"

# BASE Tickers and Topics (這些會永遠保留，其它依當日法人動態加入)
BASE_SYMBOLS = ["^TWII", "2330.TW"]
# 根據觀眾回饋，加入特定熱門族群的關鍵字，讓 AI 每天固定查這些族群的新聞
BASE_TOPICS = [
    "Taiwan Semiconductor", 
    "台股 今日強勢族群 逆勢抗跌",
    "台灣 記憶體族群 報價 反撲 (南亞科, 華邦電)",
    "台灣 矽光子 CPO 族群 (聯亞, 訊芯)",
    "台灣 低軌衛星 網通族群 補跌 反彈"
]

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
    # 1. Fetch Institutional Data (三大法人 — Top 10 動態排名)
    # ========================================
    institutional_data = fetcher.get_institutional_data(top_n=10)
    has_inst_data = institutional_data.get("top_buy") or institutional_data.get("top_sell")
    if not has_inst_data:
        print("  ❌ 無法取得三大法人數據（今日資料尚未發布），停止執行。")
        print("  💡 請等待台灣證交所發布後（通常下午 4 點後）再重新執行。")
        return

    # 動態決定今日分析標的 (大盤 + 台積電 + 外資買賣超前三名)
    dynamic_symbols = BASE_SYMBOLS.copy()
    dynamic_topics = BASE_TOPICS.copy()
    
    if has_inst_data:
        for item in institutional_data.get("top_buy", [])[:3]:
            symbol_yf = f"{item['id']}.TW"
            if symbol_yf not in dynamic_symbols:
                dynamic_symbols.append(symbol_yf)
                dynamic_topics.append(f"Taiwan stock {item['id']} {item['name']}")
                
        for item in institutional_data.get("top_sell", [])[:3]:
            symbol_yf = f"{item['id']}.TW"
            if symbol_yf not in dynamic_symbols:
                dynamic_symbols.append(symbol_yf)
                dynamic_topics.append(f"Taiwan stock {item['id']} {item['name']}")

    # ========================================
    # 2. Fetch Market Data (with Technical Indicators)
    # ========================================
    market_data = {}
    print("\n📊 正在獲取動態股票數據與技術指標...")
    for symbol in dynamic_symbols:
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
    # 2.5 Fetch Commodity Data (油金銀)
    # ========================================
    print("\n🛢️  正在獲取國際商品行情...")
    commodity_data = fetcher.get_commodity_data()

    # ========================================
    # 2.6 Fetch Top Volume Stocks (今日成交量前20名 - 真實市場投票)
    # ========================================
    volume_data = fetcher.get_top_volume_stocks(top_n=20)
            
    # ========================================
    # 2.6 Fetch Macro Economic Events (日曆事件)
    # ========================================
    macro_events = fetcher.get_macro_events()

    # ========================================
    # 2.7 Fetch Tech Catalyst Events (NVIDIA GTC / Fed / 財報 等)
    # ========================================
    tech_catalyst_events = fetcher.get_tech_catalyst_events()

    # ========================================
    # 2.8 Auto Update Stock Database
    # ========================================
    print("\n🔍 正在檢查是否有新股票需要 AI 供應鏈調查...")
    from modules.stock_db_updater import StockDatabaseUpdater
    try:
        updater = StockDatabaseUpdater(GEMINI_API_KEY)
        stocks_to_check = []
        if has_inst_data:
            for s in institutional_data.get("top_buy", []) + institutional_data.get("top_sell", []):
                stocks_to_check.append({"id": s["id"], "name": s["name"]})
        if volume_data:
            for s in volume_data:
                stocks_to_check.append({"id": s["id"], "name": s["name"]})
        updater.update_new_stocks(stocks_to_check)
    except Exception as e:
        print(f"  [Error] 自動資料庫調查失敗: {e}")

    # ========================================
    # 3. Fetch News (加上資金輪動搜尋)
    # ========================================
    news_data = []
    print("\n📰 正在獲取新聞...")
    
    # 加入資金排擠效應專屬搜尋
    dynamic_topics.append("台股 資金輪動 排擠效應 最新分析")
    
    for topic in dynamic_topics:
        query = f"{topic} market news today"
        results = fetcher.get_news(query)
        if results:
            news_data.extend(results)
            print(f"  ✅ {topic}: 找到 {len(results)} 篇文章")
        else:
            print(f"  ⚠️  {topic}: 未找到文章")
            
    # Stock-specific news
    for symbol in dynamic_symbols[:2]:
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
        prev_report_path=prev_report,
        commodity_data=commodity_data,
        macro_events=macro_events,
        tech_catalyst_events=tech_catalyst_events,
        volume_data=volume_data
    )
    
    # ========================================
    # 6. Save Report
    # ========================================
    os.makedirs(REPORTS_DIR, exist_ok=True)
    filename = f"daily_report_V21_{datetime.datetime.now().strftime('%Y-%m-%d')}.md"
    filepath = os.path.join(REPORTS_DIR, filename)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(report_content)
        
    # ========================================
    # 7. Generate Structured Data & NotebookLM Prompt
    # ========================================
    from modules.data_extractor import extract_structured_data
    
    print("\n📦 正在萃取結構化數據 (防止 AI 幻覺)...")
    structured_data = extract_structured_data(GEMINI_API_KEY, report_content)
    
    # Save the structured data json for debugging
    json_filename = f"structured_data_{datetime.datetime.now().strftime('%Y%m%d')}.json"
    json_filepath = os.path.join(REPORTS_DIR, json_filename)
    import json
    with open(json_filepath, "w", encoding="utf-8") as f:
        json.dump(structured_data, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 結構化數據已儲存: {json_filename}")
    
    print("\n🎧 正在基於結構化數據生成 NotebookLM Podcast 腳本指令...")
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    print(f"  📌 目前專案主題標籤: {PROJECT_THEME if PROJECT_THEME else '無 (日常盤勢)'}")
    notebooklm_prompt_content = generate_notebooklm_prompt(GEMINI_API_KEY, structured_data, date_str, current_theme=PROJECT_THEME)
    nl_filename = f"notebooklm_prompt_podcast_{datetime.datetime.now().strftime('%Y%m%d')}.md"
    nl_filepath = os.path.join(REPORTS_DIR, nl_filename)
    
    with open(nl_filepath, "w", encoding="utf-8") as f:
        f.write(notebooklm_prompt_content)
        
    print(f"\n✅ 報告已儲存至: {filepath}")
    print(f"✅ NotebookLM 腳本指令已儲存至: {nl_filepath}")
    
    # ========================================
    # 8. Update Knowledge Wiki (The Second Brain)
    # ========================================
    from modules.knowledge_manager import KnowledgeUpdater
    knowledge_updater = KnowledgeUpdater(GEMINI_API_KEY)
    knowledge_updater.update_knowledge_base(report_content)
    
    print("完成!")

if __name__ == "__main__":
    main()
