import os
import datetime
import glob
import json
from dotenv import load_dotenv
from modules.data_fetcher import DataFetcher
from modules.analyzer import MarketAnalyzer
from modules.notebooklm_generator import generate_notebooklm_prompt

# Load environment variables from .env
load_dotenv()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
PROJECT_THEME = os.getenv("PROJECT_THEME", "") # e.g. "中東地緣政治危機" or "GTC 大會"

# BASE Tickers (永遠保留)
BASE_SYMBOLS = ["^TWII", "2330.TW"]

# [優化 E] BASE_TOPICS 精簡為必搜主題（高信噪比）
# 族群專屬搜尋改為「動態觸發」：只有外資買超出現時才搜
BASE_TOPICS = [
    "Taiwan Semiconductor",
    "台股 今日強勢族群 逆勢抗跌",
    "台股 資金輪動 排擠效應 最新分析",
]

# Reports directory
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
YOUTUBE_ASSETS_DIR = os.path.join(REPORTS_DIR, "06_YouTube_Assets")
CHANNEL_STORY_LOG = os.path.join(YOUTUBE_ASSETS_DIR, "channel_story_log.md")


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


def find_yesterday_structured_data():
    """
    [優化 B] 找出最近一份 structured_data_YYYYMMDD.json（昨日），
    載入後回傳其中的 prediction_targets 列表。
    """
    all_jsons = sorted(glob.glob(os.path.join(REPORTS_DIR, "structured_data_????????.json")))
    # 也找 archive 目錄
    archive_jsons = sorted(glob.glob(os.path.join(REPORTS_DIR, "archive", "*", "structured_data_????????.json")))
    all_jsons = sorted(set(all_jsons + archive_jsons))

    today_str = datetime.datetime.now().strftime("%Y%m%d")
    # 取今天以前的最新一份
    candidates = [f for f in all_jsons if today_str not in f]
    if not candidates:
        return [], None

    latest = candidates[-1]
    try:
        with open(latest, "r", encoding="utf-8") as f:
            data = json.load(f)
        targets = data.get("prediction_targets", [])
        print(f"  [優化 B] 讀取昨日 structured_data: {os.path.basename(latest)}，預測標的: {len(targets)} 檔")
        return targets, data
    except Exception as e:
        print(f"  [優化 B] 無法讀取昨日 structured_data: {e}")
        return [], None


def append_channel_story_log(date_str, structured_data, visual_theme="", prediction_result_summary="待驗收", video_id="", title_used=""):
    """
    [優化 C] 每日執行後，將本日重點 append 至 channel_story_log.md。
    提供「頻道敘事連貫性」給下次 /2 指令參考。
    """
    os.makedirs(YOUTUBE_ASSETS_DIR, exist_ok=True)

    # 初始化表頭（如果檔案不存在）
    if not os.path.exists(CHANNEL_STORY_LOG):
        header = "# 頻道敘事紀錄 (Channel Story Log)\n\n"
        header += "| 日期 | 核心標的 | 預測方向 | 隔日驗收 | 視覺風格 | 標題方向 | 影片ID | 使用標題 | 觀看數 |\n"
        header += "|------|---------|---------|---------|---------|---------|--------|---------|--------|\n"
        with open(CHANNEL_STORY_LOG, "w", encoding="utf-8") as f:
            f.write(header)

    # 整理本日資料
    picks = structured_data.get("ai_data_picks", [])
    picks_str = "、".join(picks[:2]) if picks else "無明牌"

    targets = structured_data.get("prediction_targets", [])
    direction_str = "、".join([f"{t.get('name','')}({t.get('direction','多')})" for t in targets[:2]]) if targets else "無"

    index_action = structured_data.get("index_action", "")

    row = f"| {date_str} | {picks_str} | {direction_str} | {prediction_result_summary} | {visual_theme if visual_theme else '未指定'} | {index_action[:20]} | {video_id if video_id else '-'} | {title_used if title_used else '-'} | 待更新 |\n"

    try:
        with open(CHANNEL_STORY_LOG, "a", encoding="utf-8") as f:
            f.write(row)
        print(f"  [優化 C] 頻道敘事紀錄已更新: {CHANNEL_STORY_LOG}")
    except Exception as e:
        print(f"  [優化 C] 無法寫入頻道敘事紀錄: {e}")


def main():
    print(f"{'='*50}")
    print(f"  智慧財經新聞助理 (V21 Pro)")
    print(f"  日期: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}\n")
    
    if not TAVILY_API_KEY or not GEMINI_API_KEY:
        print("[Error] 請在 .env 檔案中設定 TAVILY_API_KEY 和 GEMINI_API_KEY")
        return

    # ========================================
    # 0. Update yesterday's video performance stats
    # ========================================
    if YOUTUBE_API_KEY:
        print("\n📺 正在更新昨日影片觀看數...")
        from modules.youtube_analytics import update_log_with_stats
        update_log_with_stats(CHANNEL_STORY_LOG, YOUTUBE_API_KEY)
    else:
        print("\n⚠️  未設定 YOUTUBE_API_KEY，跳過影片觀看數更新。")
        print("   請在 .env 加入 YOUTUBE_API_KEY=... 以啟用自動觀看數追蹤。")
    
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

    # ========================================
    # [優化 B] 讀取昨日 structured_data，取得預測標的供後續驗收
    # ========================================
    print("\n🔍 [優化 B] 讀取昨日預測標的...")
    yesterday_predictions, yesterday_structured = find_yesterday_structured_data()

    # 動態決定今日分析標的 (大盤 + 台積電 + 外資買賣超前三名)
    dynamic_symbols = BASE_SYMBOLS.copy()
    dynamic_topics = BASE_TOPICS.copy()

    if has_inst_data:
        for item in institutional_data.get("top_buy", [])[:3]:
            symbol_yf = f"{item['id']}.TW"
            if symbol_yf not in dynamic_symbols:
                dynamic_symbols.append(symbol_yf)
                # [優化 E] 加入族群專屬搜尋（動態觸發）
                dynamic_topics.append(f"Taiwan stock {item['id']} {item['name']} 外資買超 原因")

        for item in institutional_data.get("top_sell", [])[:3]:
            symbol_yf = f"{item['id']}.TW"
            if symbol_yf not in dynamic_symbols:
                dynamic_symbols.append(symbol_yf)
                dynamic_topics.append(f"Taiwan stock {item['id']} {item['name']} 外資賣超 原因")

    # [優化 B] 確保昨日預測標的也被抓取技術指標
    if yesterday_predictions:
        for pred in yesterday_predictions:
            pred_symbol = f"{pred.get('id', '')}.TW"
            if pred_symbol not in dynamic_symbols and pred.get('id'):
                dynamic_symbols.append(pred_symbol)

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
    # 2.8 Auto Update Stock Database + [優化 D] 績效追蹤
    # ========================================
    print("\n🔍 正在檢查是否有新股票需要 AI 供應鏈調查...")
    from modules.stock_db_updater import StockDatabaseUpdater
    updater = None
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

        # [優化 D] 用昨日 prediction_targets 更新今日驗收結果
        if yesterday_predictions:
            print("\n📊 [優化 D] 正在更新昨日預測標的的績效追蹤...")
            updater.update_tracking_stats(
                yesterday_predictions=yesterday_predictions,
                todays_market_data=market_data,
                todays_institutional_data=institutional_data
            )
    except Exception as e:
        print(f"  [Error] 自動資料庫調查/績效追蹤失敗: {e}")

    # ========================================
    # 3. [優化 E] Fetch News 精準優先策略
    # ========================================
    news_data = []
    print("\n📰 正在獲取新聞（精準優先策略）...")

    for topic in dynamic_topics:
        query = f"{topic} market news today"
        results = fetcher.get_news(query)
        if results:
            news_data.extend(results)
            print(f"  ✅ {topic}: 找到 {len(results)} 篇文章")
        else:
            print(f"  ⚠️  {topic}: 未找到文章")

    # 大盤 + 台積電 股價新聞
    for symbol in dynamic_symbols[:2]:
        query = f"{symbol} stock news today"
        results = fetcher.get_news(query)
        if results:
            news_data.extend(results)

    # Deduplicate
    unique_news = list({n['url']: n for n in news_data}.values())
    print(f"\n  📋 獨特新聞文章總數: {len(unique_news)}")

    # ========================================
    # 3.5 [深度押注] 每日焦點股深挖
    # ========================================
    deep_dive_data = None
    top_buy_list = institutional_data.get("top_buy", [])
    if top_buy_list:
        focus_stock = top_buy_list[0]  # 外資買超金額第一名
        focus_id = focus_stock.get("id", "")
        focus_name = focus_stock.get("name", "")
        print(f"\n🎯 [深度押注] 今日焦點股：{focus_id} {focus_name}")
        try:
            deep_dive_data = fetcher.get_deep_dive_data(
                stock_id=focus_id,
                stock_name=focus_name,
                institutional_data=institutional_data
            )
        except Exception as e:
            print(f"  ⚠️  深度押注資料抓取失敗: {e}")

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
        volume_data=volume_data,
        deep_dive_data=deep_dive_data
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
    # 7.5 Generate YouTube Content Package (titles + description + pinned comment)
    # ========================================
    from modules.youtube_content_generator import generate_youtube_package, save_youtube_package

    print("\n📢 正在生成 YouTube 上架素材（標題/描述/置頂留言）...")
    yt_package = generate_youtube_package(GEMINI_API_KEY, structured_data, report_content, date_str)
    if yt_package.get("titles"):
        yt_package_path = save_youtube_package(yt_package, YOUTUBE_ASSETS_DIR, date_str)
        print(f"  ✅ YouTube 素材包已儲存: {os.path.basename(yt_package_path)}")
        for i, title in enumerate(yt_package["titles"]):
            print(f"     標題{'ABC'[i]}: {title}")
    else:
        print("  ⚠️  YouTube 素材生成失敗，跳過。")

    # ========================================
    # 7.6 Generate Thumbnails with DALL-E 3 (requires OPENAI_API_KEY)
    # ========================================
    if OPENAI_API_KEY:
        from modules.thumbnail_generator import generate_ab_test_thumbnails, print_ab_test_summary

        print("\n🎨 正在用 DALL-E 3 生成 YouTube 縮圖背景...")
        ab_results = generate_ab_test_thumbnails(
            gemini_api_key=GEMINI_API_KEY,
            openai_api_key=OPENAI_API_KEY,
            report_content=report_content,
            reports_dir=YOUTUBE_ASSETS_DIR,
            styles=["dc_comics", "cyberpunk"],
            num_titles=3,
        )
        print_ab_test_summary(ab_results)
    else:
        print("\n⚠️  未設定 OPENAI_API_KEY，跳過 DALL-E 3 縮圖生成。")
        print("   請在 .env 加入 OPENAI_API_KEY=sk-... 以啟用自動縮圖。")

    # ========================================
    # 8. Update Knowledge Wiki (The Second Brain) [優化 A]
    # ========================================
    from modules.knowledge_manager import KnowledgeUpdater
    knowledge_updater = KnowledgeUpdater(GEMINI_API_KEY)
    knowledge_updater.update_knowledge_base(report_content)

    # ========================================
    # 9. [優化 C] 更新頻道敘事紀錄 (Channel Story Log)
    # ========================================
    print("\n📺 [優化 C] 更新頻道敘事紀錄...")
    append_channel_story_log(
        date_str=date_str,
        structured_data=structured_data,
        visual_theme=PROJECT_THEME,
        prediction_result_summary="待驗收"
    )

    print("\n" + "="*50)
    print("  ✅ 所有任務完成！")
    print(f"  📄 日報: {filename}")
    print(f"  🎙️  Podcast 腳本: {nl_filename}")
    print(f"  📺 頻道紀錄: {os.path.basename(CHANNEL_STORY_LOG)}")
    print("  🧠 Knowledge Wiki 已更新 (Append-Only)")
    print("="*50)

if __name__ == "__main__":
    main()
