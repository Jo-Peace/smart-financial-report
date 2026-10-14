"""
Weekend special: US week recap + this week's Taiwan facts + next-session watch list.

Run: python3 cli.py special weekend
Output: reports/special_topics/YYYY-MM-DD_weekend_special/
Taiwan numbers come only from reports/daily/*/structured_data.json and ^TWII;
the prompt is neutral (modules/special_context.build_weekend_special_prompt).
"""
import os
import logging
from dotenv import load_dotenv
from modules.data_fetcher import DataFetcher
from modules.fetch_result import unwrap_fetch_result
from modules.analyzer import MarketAnalyzer, is_report_error
from modules.atomic_io import write_json_atomic, write_text_atomic
from modules.exit_codes import EXIT_CONFIG, EXIT_GEMINI_FAILED, EXIT_OK, EXIT_PERSIST_FAILED
from modules.notebooklm_generator import generate_weekend_special_prompt
from modules.data_extractor import extract_weekend_structured_data
from modules.narrative_sanitize import sanitize_narrative_text, sanitize_structured_data
from modules.project_skill_registry import format_skill_context, format_skill_summary, select_skills
from modules.report_paths import special_topic_file
from modules.special_context import (
    WEEKEND_SLUG,
    build_weekend_special_prompt,
    collect_taiwan_week_facts,
    fetch_adr_premium,
    format_adr_block,
    format_session_context,
    format_taiwan_week_block,
    session_context,
    taipei_now,
    this_week_index_summary,
)

# Load environment variables
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Weekend Special Tickers
US_SYMBOLS = ["^GSPC", "^IXIC", "^VIX", "NVDA", "TSM"]
# Neutral queries: the news picks the story, not the query wording.
US_TOPICS = [
    "US stock market weekly recap",
    "Nasdaq S&P 500 tech stocks week",
    "global macro geopolitics stock market this week",
    "gold crude oil prices this week",
    "Taiwan stock market week ahead",
]

REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


def _save_structured_failure(date_str, now, structured_data) -> str:
    """Failure record next to the report (never the special_structured_data_* artifact)."""
    if isinstance(structured_data, dict):
        record = {
            "error": str(structured_data.get("error") or "weekend structured data extraction failed"),
            "raw_error": str(structured_data.get("raw_error") or "")[:500],
        }
    else:
        record = {"error": "weekend structured data is not a JSON object", "raw_error": type(structured_data).__name__}
    record.update({"date": date_str, "stage": "extract_weekend_structured_data"})
    folder = os.path.dirname(special_topic_file(REPORTS_DIR, date_str, WEEKEND_SLUG, "report", ensure=True))
    path = os.path.join(folder, f"structured_data_failed_{now.strftime('%Y%m%d_%H%M%S')}_{WEEKEND_SLUG}.json")
    write_json_atomic(path, record)
    return path


def main():
    now = taipei_now()
    date_str = now.strftime('%Y-%m-%d')
    session_ctx = session_context(date_str, now=now)
    print(f"{'='*50}")
    print(f"  週末特輯：美股一週回顧與台股下個交易日觀察")
    print(f"  日期: {now.strftime('%Y-%m-%d %H:%M')}（台北時間）")
    print(f"  {format_session_context(session_ctx)}")
    print(f"{'='*50}\n")

    if not TAVILY_API_KEY or not GEMINI_API_KEY:
        print("[Error] 請在 .env 檔案中設定 TAVILY_API_KEY 和 GEMINI_API_KEY")
        return EXIT_CONFIG

    try:
        active_skills = select_skills(
            [
                "weekly_special",
                "macro",
                "commodity",
                "taiwan_stock",
                "market_analysis",
            ],
            base_dir=os.path.dirname(os.path.abspath(__file__)),
        )
        print(f"\n🧩 Project skills loaded: {format_skill_summary(active_skills)}")
    except Exception as e:
        print(f"\n⚠️  Project skills registry unavailable: {e}")
        active_skills = []
    skill_context = format_skill_context(active_skills)

    fetcher = DataFetcher(TAVILY_API_KEY)
    analyzer = MarketAnalyzer(GEMINI_API_KEY)

    # 1. Fetch US Market Data (Weekly Summary)
    market_data = {}
    print("\n📊 正在獲取美股與波動指標一週數據 (S&P500, Nasdaq, VIX, NVDA, TSM)...")
    for symbol in US_SYMBOLS:
        data = unwrap_fetch_result(fetcher.get_weekly_stock_data(symbol), default={})
        if data:
            market_data[symbol] = data
            print(f"  ✅ {symbol}: 週收盤 ${data['week_close']} (本週漲跌 {data['week_pct_change']:+.2f}%)")
        else:
            print(f"  ❌ {symbol}: 數據獲取失敗")

    # 2. Taiwan ground truth: local daily structured_data + ^TWII weekly
    print("\n🇹🇼 正在彙整本週台股本機資料（daily/*/structured_data.json + ^TWII）...")
    taiwan_facts = collect_taiwan_week_facts(REPORTS_DIR, date_str)
    # Only the bars dated in this week (holiday weeks have fewer than 5 sessions).
    twii = this_week_index_summary(
        unwrap_fetch_result(fetcher.get_weekly_stock_data("^TWII"), default={}) or {}, date_str
    )
    taiwan_block = format_taiwan_week_block(taiwan_facts, twii)
    if twii:
        print(f"  📌 ^TWII 本週 {twii['sessions']} 個交易日，週漲跌 {twii['week_pct_change']:+.2f}%")
    print(f"  📌 本機日報 {len(taiwan_facts['days'])} 天；法人資料：{'有' if taiwan_facts['has_institutional'] else '無'}")

    adr = fetch_adr_premium(fetcher, (market_data.get("TSM") or {}).get("week_close"))
    adr_block = format_adr_block(adr, session_ctx)

    # 3. Fetch Commodities
    print("\n🛢️  正在獲取國際商品行情 (黃金、原油、白銀)...")
    commodity_data = unwrap_fetch_result(fetcher.get_commodity_data(), default={})

    # 4. Fetch Macro Events
    macro_events = unwrap_fetch_result(fetcher.get_macro_events(), default=[])

    # 5. Fetch News (neutral queries)
    news_data = []
    print("\n📰 正在獲取本週國際總經與市場新聞...")
    for topic in US_TOPICS:
        results = unwrap_fetch_result(fetcher.get_news(f"{topic} latest news"), default=[])
        if results:
            news_data.extend(results)
            print(f"  ✅ {topic[:25]}... : 找到 {len(results)} 篇")

    unique_news = list({n['url']: n for n in news_data}.values())
    print(f"\n  📋 獨特新聞文章總數: {len(unique_news)}")

    # 6. Generate Weekend Report (in Traditional Chinese)
    print("\n🤖 正在使用 Gemini 生成週末特輯分析報告 (繁體中文)...")
    prompt = build_weekend_special_prompt(
        date_str=date_str,
        session_ctx=session_ctx,
        us_market_data=market_data,
        taiwan_block=taiwan_block,
        adr_block=adr_block,
        commodity_data=commodity_data,
        macro_events=macro_events,
        news=unique_news,
    )
    report_content = analyzer._call_gemini_with_retry(prompt)
    # Same check as analyzer.abort_on_generation_error (error string / empty answer).
    if is_report_error(report_content):
        print(f"\n❌ 週末報告生成失敗，未寫檔: {str(report_content)[:200]}")
        return EXIT_GEMINI_FAILED
    report_content = sanitize_narrative_text(report_content)

    report_filepath = special_topic_file(REPORTS_DIR, date_str, WEEKEND_SLUG, "report", ensure=True)
    write_text_atomic(report_filepath, report_content)
    print(f"  ✅ 週末報告已儲存: {report_filepath}")

    # 7. Extract Structured JSON (sanitized; Taiwan facts attached for the director card)
    print("\n📦 正在萃取週末結構化數據 (建構腳本藍圖)...")
    structured_data = extract_weekend_structured_data(GEMINI_API_KEY, report_content)
    if not isinstance(structured_data, dict) or structured_data.get("error"):
        failed_path = _save_structured_failure(date_str, now, structured_data)
        print("\n❌ 週末結構化數據萃取失敗：不寫 special_structured_data，也不產生 NotebookLM 導演卡。")
        print(f"   報告已保留: {report_filepath}")
        print(f"   錯誤紀錄: {os.path.basename(failed_path)}")
        return EXIT_PERSIST_FAILED
    structured_data = sanitize_structured_data(structured_data)
    structured_data["date"] = date_str
    structured_data["taiwan_week"] = taiwan_facts
    structured_data["session_context"] = session_ctx
    structured_data["adr_premium"] = adr

    json_filepath = special_topic_file(REPORTS_DIR, date_str, WEEKEND_SLUG, "structured_data", ensure=True)
    write_json_atomic(json_filepath, structured_data)
    print(f"  ✅ 結構化數據已儲存: {os.path.basename(json_filepath)}")

    # 8. Generate NotebookLM Prompt for the Weekend
    print("\n🎧 正在生成 NotebookLM 週末特輯 Podcast 腳本指令...")
    podcast_prompt = generate_weekend_special_prompt(
        GEMINI_API_KEY,
        structured_data,
        date_str,
        skill_context=skill_context,
    )
    if podcast_prompt is None:
        print("  ⏭  NotebookLM prompt 已關閉（NOTEBOOKLM_PROMPT_MODE=off），不寫檔")
    else:
        nl_filepath = special_topic_file(REPORTS_DIR, date_str, WEEKEND_SLUG, "notebooklm_prompt", ensure=True)
        write_text_atomic(nl_filepath, podcast_prompt)
        print(f"\n✅ 週末 NotebookLM 腳本指令已儲存至: {nl_filepath}")
    print("週末特輯生成完畢！")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
