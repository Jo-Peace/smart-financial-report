import os
import sys
import logging
from dotenv import load_dotenv
from modules.data_fetcher import DataFetcher
from modules.analyzer import MarketAnalyzer
from modules.fetch_result import unwrap_fetch_result
from modules.project_skill_registry import format_skill_summary, select_skills
from modules.ai_usage_guard import print_today_usage
from modules.pipeline_context import build_pipeline_context
from modules.pipeline_support import (
    find_previous_report,
    find_previous_structured_data,
    is_generation_error,
)
from modules.pipeline_stages import (
    build_dynamic_universe,
    fetch_market_universe,
    inflight_prediction_symbols,
    ledger_only_symbols,
)
from modules.pipeline_analysis import build_analysis_bundle
from modules.pipeline_persist import persist_daily_artifacts
from modules.monthly_consolidate import maybe_run_before_daily_main
from modules.data_quality_gate import apply_data_quality_gate, drop_off_session_bars, evaluate_data_quality
from modules.exit_codes import (
    EXIT_CONFIG,
    EXIT_DATA_NOT_READY,
    EXIT_GEMINI_FAILED,
    EXIT_NOT_SESSION,
    EXIT_OK,
    EXIT_PERSIST_FAILED,
    EXIT_QUALITY_GATE,
    EXIT_TOO_EARLY,
    normalize as normalize_exit_code,
)
from modules.runtime_flags import print_active_flags
from modules.tz import now_tpe_naive
from modules.twse_tape import pct_text
from modules.pipeline_context import allow_stale_session, session_guard, stale_relabel_decision, stale_session_target
from modules.twse_calendar import is_twse_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# Load environment variables from .env
load_dotenv()
CTX = build_pipeline_context()

TAVILY_API_KEY = CTX.tavily_api_key
GEMINI_API_KEY = CTX.gemini_api_key
YOUTUBE_API_KEY = CTX.youtube_api_key
YOUTUBE_CHANNEL_ID = CTX.youtube_channel_id
PROJECT_THEME = CTX.project_theme
DAILY_VISUAL_THEME = CTX.daily_visual_theme
ALLOW_EARLY_RUN = CTX.allow_early_run
NEWS_MODE = CTX.news_mode
TAVILY_DAILY_SEARCH_LIMIT = CTX.tavily_daily_search_limit
BASE_SYMBOLS = CTX.base_symbols
BASE_TOPICS = CTX.base_topics
REPORTS_DIR = CTX.reports_dir
YOUTUBE_ASSETS_DIR = CTX.youtube_assets_dir
CHANNEL_STORY_LOG = CTX.channel_story_log
YOUTUBE_PERFORMANCE_WIKI = os.path.join(CTX.base_dir, "data", "knowledge_wiki", "youtube_performance_patterns.md")


def main():
    # Asia/Taipei wall clock regardless of host TZ (16:10 cutoff + run_date).
    now = now_tpe_naive()
    print(f"{'='*50}")
    print(f"  智慧財經新聞助理 (V21 Pro)")
    print(f"  日期: {now.strftime('%Y-%m-%d %H:%M')}（Asia/Taipei）")
    print(f"{'='*50}\n")
    print_active_flags(prefix="  ")
    try:
        # Read-only: hit-rate stats only count sessions_v2 rows; never migrated here.
        from modules.analytics_store import ledger_legacy_status, legacy_ledger_warning

        ledger = ledger_legacy_status()
        if ledger.get("legacy"):
            print(f"  {legacy_ledger_warning(ledger['legacy'], ledger.get('current'))}")
    except Exception:
        pass

    cutoff = now.replace(hour=16, minute=10, second=0, microsecond=0)
    if now < cutoff and not ALLOW_EARLY_RUN:
        print("  ⛔ 16:10 前不執行 /1，避免資料未齊與 Gemini/API 成本浪費。")
        print("  若確定要強制執行，請設定 ALLOW_EARLY_RUN=1 後再跑。")
        print_today_usage(prefix="  ")
        return EXIT_TOO_EARLY

    print_today_usage(prefix="  ")
    run_date = now.strftime("%Y-%m-%d")
    stale_ok = allow_stale_session()
    session_ok, session_msg = session_guard(run_date, allow_stale=stale_ok)
    if session_msg:
        print(f"  {'⚠️ ' if session_ok else '⛔'} {session_msg}")
    if not session_ok:
        print("  若要補跑最近交易日，請設定 ALLOW_STALE_SESSION=1（產出會標為該資料日）。")
        return EXIT_NOT_SESSION
    
    if not TAVILY_API_KEY or not GEMINI_API_KEY:
        print("[Error] 請在 .env 檔案中設定 TAVILY_API_KEY 和 GEMINI_API_KEY")
        return EXIT_CONFIG

    maybe_run_before_daily_main(REPORTS_DIR, now=now)

    try:
        active_skills = select_skills(
            [
                "market_analysis",
                "prediction_validation",
                "notebooklm_prompt",
                "event_radar",
                "news",
                "supply_chain",
            ],
            base_dir=os.path.dirname(os.path.abspath(__file__)),
        )
        print(f"\n🧩 Project skills loaded: {format_skill_summary(active_skills)}")
    except Exception as e:
        print(f"\n⚠️  Project skills registry unavailable: {e}")
        active_skills = []

    # ========================================
    # 0. Update yesterday's video performance stats
    # ========================================
    if YOUTUBE_API_KEY:
        print("\n📺 正在更新 YouTube 近期影片與公開觀看數...")
        from modules.youtube_analytics import update_log_from_channel, update_log_with_stats
        from modules.youtube_learning import update_youtube_performance_wiki

        if YOUTUBE_CHANNEL_ID:
            update_log_from_channel(
                log_path=CHANNEL_STORY_LOG,
                api_key=YOUTUBE_API_KEY,
                channel_id=YOUTUBE_CHANNEL_ID,
                max_results=50,
            )
        update_log_with_stats(CHANNEL_STORY_LOG, YOUTUBE_API_KEY, update_all=True)
        try:
            update_youtube_performance_wiki(CHANNEL_STORY_LOG, YOUTUBE_PERFORMANCE_WIKI)
            print(f"  ✅ YouTube 成效學習摘要已更新: {os.path.basename(YOUTUBE_PERFORMANCE_WIKI)}")
        except Exception as e:
            print(f"  ⚠️  YouTube 成效學習摘要更新失敗，略過：{e}")
    else:
        print("\n⚠️  未設定 YOUTUBE_API_KEY，跳過影片觀看數更新。")
        print("   請在 .env 加入 YOUTUBE_API_KEY=... 以啟用自動觀看數追蹤。")
    
    # Initialize Modules
    fetcher = DataFetcher(TAVILY_API_KEY, tavily_search_limit=TAVILY_DAILY_SEARCH_LIMIT)
    print(f"  📰 NEWS_MODE={NEWS_MODE}，Tavily 搜尋上限={TAVILY_DAILY_SEARCH_LIMIT}")
    analyzer = MarketAnalyzer(GEMINI_API_KEY)
    
    # ========================================
    # 1. Fetch Institutional Data (三大法人 — Top 10 動態排名)
    # ========================================
    from modules.twse_http import insecure_events, reset_insecure_events

    reset_insecure_events()
    institutional_data = unwrap_fetch_result(
        fetcher.get_institutional_data(top_n=10),
        default={"top_buy": [], "top_sell": [], "data_date": None},
    )
    has_inst_data = institutional_data.get("top_buy") or institutional_data.get("top_sell")
    if not has_inst_data:
        print("  ❌ 無法取得三大法人數據（今日資料尚未發布），停止執行。")
        print("  💡 請等待台灣證交所發布後（通常下午 4 點後）再重新執行。")
        return EXIT_DATA_NOT_READY

    # ========================================
    # 1.5 Fetch Top Volume Stocks (成交股數前20名) + align trade dates
    #     Done before the universe / market data so every later fetch uses the
    #     final trade date (previously the universe came from pre-alignment lists).
    # ========================================
    volume_result = fetcher.get_top_volume_stocks(top_n=20)
    volume_data = unwrap_fetch_result(volume_result, default=[])
    volume_date = (volume_result.get("meta") or {}).get("data_date") if isinstance(volume_result, dict) else None
    institutional_date = institutional_data.get("data_date")
    if volume_data and institutional_date and volume_date and volume_date != institutional_date:
        print("\n  ⚠️  法人與成交量日期不一致，嘗試對齊到成交量交易日...")
        print(f"     三大法人資料日期：{institutional_date}")
        print(f"     成交量排行資料日期：{volume_date}")
        # target_date path uses multi-attempt readiness waits inside data_fetcher.
        aligned = unwrap_fetch_result(
            fetcher.get_institutional_data(top_n=10, target_date=volume_date),
            default={"top_buy": [], "top_sell": [], "data_date": None},
        )
        if aligned.get("top_buy") or aligned.get("top_sell"):
            institutional_data = aligned
            institutional_date = aligned.get("data_date")
            print(f"  ✅ 已對齊法人資料日期：{institutional_date}")
        if institutional_date != volume_date:
            # One more long wait cycle for late TWSE publishes (common ~16:10–16:25).
            import time as _time

            print("  [Info] 對齊尚未成功，額外等待 60 秒後再試一次法人就緒…")
            _time.sleep(60)
            aligned = unwrap_fetch_result(
                fetcher.get_institutional_data(top_n=10, target_date=volume_date),
                default={"top_buy": [], "top_sell": [], "data_date": None},
            )
            if aligned.get("top_buy") or aligned.get("top_sell"):
                institutional_data = aligned
                institutional_date = aligned.get("data_date")
                print(f"  ✅ 已對齊法人資料日期：{institutional_date}")
        if institutional_date != volume_date:
            print("\n  ❌ 仍無法對齊交易日，停止執行以避免跨日污染。")
            print("  💡 通常等 2～5 分鐘後重跑即可；證交所法人榜偶爾比成交量晚幾分鐘。")
            return EXIT_DATA_NOT_READY

    if institutional_date and institutional_date != run_date and stale_ok:
        # Explicit back-fill: label every artifact with the data's own session.
        # On a TWSE session day a bare ALLOW_STALE_SESSION=1 never relabels
        # (late T86 would overwrite the previous day's artifacts); the gate
        # then fails institutional_stale. Only =<data date> back-fills it.
        relabel, relabel_msg = stale_relabel_decision(run_date, institutional_date, target=stale_session_target())
        print(f"  ⚠️  {relabel_msg}")
        if relabel:
            run_date = institutional_date
        elif not is_twse_session(run_date):
            return EXIT_NOT_SESSION
    trade_date = institutional_date or run_date

    # ========================================
    # [優化 B] 讀取前次 structured_data，取得預測標的供後續驗收
    # 若前次產出距今 >1 交易日（中間沒跑 /1），不當作「昨日/上集驗收」
    # ========================================
    print("\n🔍 [優化 B] 讀取前次預測標的...")
    from modules.pipeline_support import resolve_onair_prediction_targets

    previous_predictions, yesterday_structured, validation_meta = find_previous_structured_data(
        REPORTS_DIR,
        as_of_date=run_date,
    )
    # Universe 仍可用前次標的做追蹤；內容驗收僅用「上一交易日有產出」的標的
    yesterday_predictions = resolve_onair_prediction_targets(previous_predictions, validation_meta)

    # 動態決定今日分析標的 (大盤 + 台積電 + 外資買賣超前三名 + 前次標的)
    # 在途預測（驗收窗未結束）只為驗收帳本補收盤價，不進今日分析。
    inflight_symbols = inflight_prediction_symbols(as_of_date=run_date)
    dynamic_symbols, dynamic_topics = build_dynamic_universe(
        BASE_SYMBOLS,
        BASE_TOPICS,
        institutional_data,
        previous_predictions or yesterday_predictions,
        inflight_symbols=inflight_symbols,
    )
    ledger_symbols = ledger_only_symbols(dynamic_symbols, inflight_symbols)

    # ========================================
    # 2. Fetch Market Data (with Technical Indicators), bars dated trade_date
    # ========================================
    print(f"\n📊 正在獲取動態股票數據與技術指標（平行處理，K 棒日期須為 {trade_date}）...")

    def fetch_one(sym):
        return unwrap_fetch_result(fetcher.get_stock_data(sym, expected_date=trade_date))

    def report_fetch(symbol, data, ledger):
        tag = "（帳本）" if ledger else ""
        if data:
            indicator_str = ""
            if data.get('ma5'):
                indicator_str += f" MA5={data['ma5']}"
            if data.get('ma20'):
                indicator_str += f" MA20={data['ma20']}"
            if data.get('rsi') is not None:
                indicator_str += f" RSI={data['rsi']}"
            print(f"  ✅ {symbol}{tag}: ${data['price']} ({pct_text(data)}){indicator_str} @ {data.get('bar_date')}")
        else:
            print(f"  ❌ {symbol}{tag}: 失敗")

    # market_data = today's analysis universe only; ledger_market_data (in-flight
    # names) is written to the analytics ledger and read by nothing else.
    market_data, ledger_market_data = fetch_market_universe(
        fetch_one,
        dynamic_symbols,
        ledger_symbols,
        max_workers=10,
        on_result=report_fetch,
    )
    # Keep failed analysis keys so prompts say 「數據無法取得」 instead of silently dropping them.
    for symbol in dynamic_symbols:
        market_data.setdefault(symbol, None)
    
    # ========================================
    # 2.5 Fetch Commodity Data (油金銀) + 海外／匯率 + 期貨/籌碼 + 成交值榜（皆為本機 ground truth）
    # ========================================
    print("\n🛢️  正在獲取國際商品行情...")
    commodity_data = unwrap_fetch_result(fetcher.get_commodity_data(), default={})
    print("\n🌏 正在獲取海外／匯率行情（台積電 ADR、費半、VIX、那指期、美元兌台幣）...")
    global_overnight = unwrap_fetch_result(fetcher.get_global_overnight(), default={}) or {}
    market_extras = {}
    try:
        from modules.market_extras import fetch_market_extras, format_extras_lines, missing_extras

        market_extras = fetch_market_extras(trade_date)
        for line in format_extras_lines(market_extras):
            print(f"  ✅ {line}")
        if missing_extras(market_extras):
            print(f"  ⚠️  未取得（不引用）：{'、'.join(missing_extras(market_extras))}")
    except Exception as extras_err:
        print(f"  ⚠️  期貨／籌碼補充資料略過：{extras_err}")
    value_ranking = unwrap_fetch_result(fetcher.get_top_value_stocks(trade_date), default={}) or {}

    quality_report = evaluate_data_quality(
        run_date=run_date,
        institutional_data=institutional_data,
        market_data=market_data,
        volume_data=volume_data,
        volume_date=volume_date,
        yesterday_predictions=yesterday_predictions,
        expected_symbols=dynamic_symbols,
        tls_events=insecure_events(),
    )
    if not apply_data_quality_gate(quality_report):
        return EXIT_QUALITY_GATE
    # A dated stale bar was kept only so the gate could see it; never narrate
    # or score it as this session's move.
    market_data, off_session = drop_off_session_bars(market_data, trade_date)
    if off_session:
        print(f"  ⚠️  K 棒不是 {trade_date}，改標為「數據無法取得」：{'、'.join(off_session[:8])}")

    # ========================================
    # 2.6 Fetch Macro Economic Events (日曆事件)
    # ========================================
    macro_events = [] if NEWS_MODE == "off" else unwrap_fetch_result(fetcher.get_macro_events(), default=[])

    analysis_bundle = build_analysis_bundle(
        fetcher=fetcher,
        institutional_data=institutional_data,
        market_data=market_data,
        commodity_data=commodity_data,
        volume_data=volume_data,
        macro_events=macro_events,
        yesterday_predictions=yesterday_predictions,
        validation_meta=validation_meta,
        dynamic_topics=dynamic_topics,
        news_mode=NEWS_MODE,
        supply_chain_event_radar=[],
        active_skills=active_skills,
        channel_story_log=CHANNEL_STORY_LOG,
        base_dir=os.path.dirname(os.path.abspath(__file__)),
        gemini_api_key=GEMINI_API_KEY,
        report_date=run_date,
        value_ranking=value_ranking,
    )

    validation_results = analysis_bundle["validation_results"]
    validation_summary = analysis_bundle["validation_summary"]
    validation_meta = analysis_bundle.get("validation_meta") or validation_meta
    supply_chain_event_radar = analysis_bundle["supply_chain_event_radar"]
    skill_context = analysis_bundle["skill_context"]
    unique_news = analysis_bundle["unique_news"]
    deep_dive_data = analysis_bundle["deep_dive_data"]
    editorial_frame = analysis_bundle["editorial_frame"]
    signal_summary = analysis_bundle["signal_summary"]
    tech_catalyst_events = []
    try:
        from modules.structured_facts import format_local_market_prompt_block

        local_market_block = format_local_market_prompt_block(
            institutional_data=institutional_data,
            global_overnight=global_overnight,
            market_extras=market_extras,
            value_ranking=value_ranking,
        )
        signal_summary = "\n\n".join(part for part in (signal_summary, local_market_block) if part)
    except Exception as block_err:
        print(f"  ⚠️  本機補充數據區塊略過：{block_err}")

    # ========================================
    # 4. Historical Comparison
    # ========================================
    print("\n📁 檢查歷史報告...")
    prev_report = find_previous_report(REPORTS_DIR)
    
    # ========================================
    # 5. Generate AI Report
    # ========================================
    print("\n🤖 正在使用 Gemini 生成 AI 報告（無固定等待，限流改由 retry 處理）...")
    report_content = analyzer.generate_report(
        market_data, 
        unique_news, 
        institutional_data=institutional_data,
        prev_report_path=prev_report,
        commodity_data=commodity_data,
        macro_events=macro_events,
        tech_catalyst_events=tech_catalyst_events,
        volume_data=volume_data,
        deep_dive_data=deep_dive_data,
        signal_summary=signal_summary,
        previous_prediction_targets=yesterday_predictions,
        previous_validation_summary=validation_summary,
        editorial_frame=editorial_frame,
        similar_sessions_summary=analysis_bundle.get("similar_sessions_summary") or "",
        last_output_date=(validation_meta or {}).get("source_date"),
        value_ranking=value_ranking,
        report_date=run_date,
    )
    if is_generation_error(report_content):
        print("\n❌ AI 報告生成失敗，停止流程以避免覆蓋正式報告。")
        print(f"   錯誤摘要: {report_content.splitlines()[0]}")
        print("   已抓取的市場資料未寫成正式日報；請稍後更換/恢復 Gemini 額度後重跑。")
        return EXIT_GEMINI_FAILED
    
    date_str = run_date
    persist_result = persist_daily_artifacts(
        gemini_api_key=GEMINI_API_KEY,
        report_content=report_content,
        reports_dir=REPORTS_DIR,
        date_str=date_str,
        validation_results=validation_results,
        validation_meta=validation_meta,
        editorial_frame=editorial_frame,
        supply_chain_event_radar=supply_chain_event_radar,
        market_data=market_data,
        ledger_market_data=ledger_market_data,
        institutional_data=institutional_data,
        volume_data=volume_data,
        commodity_data=commodity_data,
        skill_context=skill_context,
        project_theme=PROJECT_THEME,
        channel_story_log=CHANNEL_STORY_LOG,
        youtube_assets_dir=YOUTUBE_ASSETS_DIR,
        similar_sessions=analysis_bundle.get("similar_sessions"),
        local_extras={
            "global_overnight": global_overnight,
            "market_extras": market_extras,
            "value_ranking": value_ranking,
            "data_dates": {
                "institutional": institutional_data.get("data_date"),
                "volume": volume_date,
            },
            "news_inputs": {
                "searches": list(getattr(fetcher, "search_log", []) or []),
                "prompt_news": unique_news,
                "rejected_news": analysis_bundle.get("rejected_news") or [],
                "radar": supply_chain_event_radar,
                "macro_events": macro_events,
                "deep_dive": deep_dive_data,
            },
        },
    )
    if not persist_result.get("ok"):
        return EXIT_PERSIST_FAILED

    structured_data = persist_result["structured_data"]
    notebooklm_prompt_content = persist_result.get("notebooklm_prompt_content")
    filename = persist_result.get("filename", f"daily_report_V21_{date_str}.md")
    nl_filename = os.path.basename(persist_result["notebooklm_prompt_path"]) if persist_result.get("notebooklm_prompt_path") else None

    print("\n" + "="*50)
    print("  ✅ 所有任務完成！")
    print(f"  📄 日報: {filename}")
    if notebooklm_prompt_content:
        print(f"  🎙️  Podcast 腳本: {nl_filename}")
    else:
        print("  🎙️  Podcast 腳本: 未生成")
    print(f"  📺 頻道紀錄: {os.path.basename(CHANNEL_STORY_LOG)}")
    print("  🧠 Knowledge Wiki 已更新 (Append-Only)")
    print("="*50)
    return EXIT_OK

if __name__ == "__main__":
    sys.exit(normalize_exit_code(main()))
