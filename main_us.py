"""
US market observation report for Taiwan investors (manual / holiday script).

Run: python3 cli.py special us-daily
Output: reports/special_topics/YYYY-MM-DD_us_market_observation/
"""
import os
import logging
from dotenv import load_dotenv
from modules.data_fetcher import DataFetcher
from modules.analyzer import MarketAnalyzer, is_report_error
from modules.atomic_io import write_text_atomic
from modules.exit_codes import EXIT_CONFIG, EXIT_GEMINI_FAILED, EXIT_OK
from modules.fetch_result import unwrap_fetch_result
from modules.narrative_sanitize import sanitize_narrative_text
from modules.report_paths import special_topic_file
from modules.special_context import (
    NO_INVENTION_RULE,
    NO_PREDICTION_RULE,
    US_OBSERVATION_SLUG,
    fetch_adr_premium,
    find_recent_us_reports,
    format_adr_block,
    format_session_context,
    session_context,
    taipei_now,
    us_observation_title,
    us_reaction_note,
    us_topics,
)

# Load environment variables from .env
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# US market targets watched while Taiwan is closed (or after the TWSE close)
US_SYMBOLS = [
    "^GSPC",    # S&P 500
    "^IXIC",    # NASDAQ
    "^SOX",     # 費城半導體指數
    "TSM",      # 台積電 ADR
    "NVDA",     # NVIDIA
    "AMD",      # AMD
    "AVGO",     # Broadcom
]

# Reports directory
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


def find_previous_us_report(date_str=None, reports_dir=REPORTS_DIR):
    """Most recent US observation report dated before date_str (7-day window)."""
    date_str = date_str or taipei_now().date().isoformat()
    reports = find_recent_us_reports(reports_dir, date_str, window_days=7)
    if reports:
        day, latest = reports[-1]
        print(f"  找到前次美股報告: {day} {os.path.basename(latest)}")
        return latest
    return None


def generate_us_prompt(date_str, data_summary, news_summary, hist_section="", *, session_ctx=None, adr_block=""):
    """
    US market observation prompt with a Taiwan investor perspective.

    Calendar-aware: which Taiwan session reacts to the US moves comes from
    us_reaction_note (TWSE calendar + the 13:30 close when session_ctx was
    built with ``now``); dates the calendar does not cover are never named.
    """
    session_ctx = session_ctx or session_context(date_str)
    title = us_observation_title(session_ctx)
    reminder = us_reaction_note(session_ctx)
    return f"""
    You are a professional financial analyst helping Taiwan investors track the US market.
    Create a US market OBSERVATION report for {date_str} in Traditional Chinese (繁體中文) Markdown format.

    # 台股行事曆
    - {format_session_context(session_ctx)}
    The goal is to help Taiwan investors TRACK daily changes — NOT to make predictions.

    # 美股數據（含技術指標）
    {data_summary}

    # 台積電 ADR 換算（本機計算）
    {adr_block or format_adr_block(None)}

    # 相關新聞
    {news_summary}
    {hist_section}

    # 資料使用規則
    - {NO_INVENTION_RULE}
    - {NO_PREDICTION_RULE}

    # 報告要求
    1. **美股快照**: 建立 Markdown 表格，欄位：代碼, 名稱, 價格, 漲跌, 漲跌幅, 成交量, MA5, MA20, RSI。
    2. **重點觀察 — 台積電 ADR (TSM)**: 分析 TSM 最新表現與技術指標；ADR 相對台股的溢價／折價只能引用上方「ADR 換算」區塊，區塊寫資料不足時不得提及。
    3. **半導體族群**: 分析費半指數、NVIDIA、AMD、Broadcom 的走勢，點出值得留意的訊號。
    4. **總經觀察**: 分析 S&P 500 與 NASDAQ 的走勢、Fed 利率方向對資金流向的影響（只用新聞中出現的事件）。
    5. **觀察筆記（重要語氣指引！）**:
       - 用「目前觀察到...」「若此趨勢持續...」「值得留意的是...」等語句
       - **嚴禁使用**「預期將...」「必定...」「建議買入/賣出」等斷言式用語
       - 明確提醒：「{reminder}」
       - 列出「目前對台股有利的訊號」和「目前對台股不利的訊號」兩組，讓觀眾自行判斷
    {"6. **與前次比較**: 用表格對比本次與前次報告的數據變化，標註趨勢方向。" if hist_section else ""}
    7. **語氣**: 像寫「觀察日記」而非「投資報告」。專業但謙遜，承認不確定性。
    8. **格式**: 乾淨的 Markdown。
    9. **數字格式（重要！）**: 在報告正文（非表格）中提及關鍵數字時，在阿拉伯數字後加上中文括號標註，以確保語音朗讀正確。範例：
       - 指數：5,800 點（五千八百點）
       - 股價：185.50 美元（一百八十五點五美元）
       - 表格內的數字不需要加中文標註。

    請生成完整報告。標題使用：「{title}」。
    """


def main():
    now = taipei_now()
    date_str = now.strftime("%Y-%m-%d")
    session_ctx = session_context(date_str, now=now)
    print(f"{'='*50}")
    print(f"  {us_observation_title(session_ctx)}")
    print(f"  日期: {now.strftime('%Y-%m-%d %H:%M')}（台北時間）")
    print(f"  {format_session_context(session_ctx)}")
    print(f"{'='*50}\n")

    if not TAVILY_API_KEY or not GEMINI_API_KEY:
        print("[Error] 請在 .env 檔案中設定 TAVILY_API_KEY 和 GEMINI_API_KEY")
        return EXIT_CONFIG

    fetcher = DataFetcher(TAVILY_API_KEY)
    analyzer = MarketAnalyzer(GEMINI_API_KEY)

    # ========================================
    # 1. Fetch US Market Data
    # ========================================
    market_data = {}
    print("📊 正在獲取美股數據與技術指標...")
    for symbol in US_SYMBOLS:
        data = unwrap_fetch_result(fetcher.get_stock_data(symbol))
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

    adr = fetch_adr_premium(fetcher, (market_data.get("TSM") or {}).get("price"))
    adr_block = format_adr_block(adr, session_ctx)
    if adr:
        print(f"  ✅ ADR 相對台股 {adr['premium_pct']:+.2f}%（本機計算）")
    else:
        print("  ⚠️  ADR 換算資料不足，報告不提溢價")

    # ========================================
    # 2. Fetch News
    # ========================================
    news_data = []
    print("\n📰 正在獲取美股相關新聞...")
    for topic in us_topics(date_str):
        query = f"{topic} market news today"
        results = unwrap_fetch_result(fetcher.get_news(query), default=[])
        if results:
            news_data.extend(results)
            print(f"  ✅ {topic}: 找到 {len(results)} 篇文章")
        else:
            print(f"  ⚠️  {topic}: 未找到文章")

    # Deduplicate
    unique_news = list({n['url']: n for n in news_data}.values())
    print(f"\n  📋 獨特新聞文章總數: {len(unique_news)}")

    # ========================================
    # 3. Historical Comparison (previous report within 7 days, never today's own)
    # ========================================
    print("\n📁 檢查歷史報告...")
    prev_report = find_previous_us_report(date_str)
    hist_section = ""
    if prev_report:
        try:
            with open(prev_report, "r", encoding="utf-8") as f:
                hist_section = f"\n# 前次報告（供比較用）\n{f.read()[:2000]}\n"
        except Exception:
            pass

    # ========================================
    # 4. Prepare Data & Generate Report
    # ========================================
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

    prompt = generate_us_prompt(
        date_str, data_summary, news_summary, hist_section, session_ctx=session_ctx, adr_block=adr_block
    )

    print("\n🤖 正在使用 Gemini 生成美股觀察報告...")
    report_content = analyzer._call_gemini_with_retry(prompt)
    # Same check as analyzer.abort_on_generation_error (error string / empty answer).
    if is_report_error(report_content):
        print(f"\n❌ 報告生成失敗，未寫檔: {str(report_content)[:200]}")
        return EXIT_GEMINI_FAILED

    # ========================================
    # 5. Save Report (special_topics layout, sanitized wording)
    # ========================================
    filepath = special_topic_file(REPORTS_DIR, date_str, US_OBSERVATION_SLUG, "report", ensure=True)
    write_text_atomic(filepath, sanitize_narrative_text(report_content))

    print(f"\n✅ 美股觀察報告已儲存至: {filepath}")
    print("完成!")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
