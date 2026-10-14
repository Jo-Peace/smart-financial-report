"""
Weekly US market recap script for Taiwan investors (manual / holiday script).

Run: python3 cli.py special us-weekly
Output: reports/special_topics/YYYY-MM-DD_us_weekly_recap/
"""
import os
import logging
from dotenv import load_dotenv
from modules.data_fetcher import DataFetcher
from modules.analyzer import MarketAnalyzer, is_report_error
from modules.atomic_io import write_text_atomic
from modules.exit_codes import EXIT_CONFIG, EXIT_GEMINI_FAILED, EXIT_OK
from modules.thumbnail_generator import generate_ab_test_thumbnails, print_ab_test_summary
from modules.fetch_result import unwrap_fetch_result
from modules.narrative_sanitize import sanitize_narrative_text
from modules.report_paths import special_topic_dir, special_topic_file
from modules.special_context import (
    NO_INVENTION_RULE,
    NO_PREDICTION_RULE,
    US_WEEKLY_SLUG,
    fetch_adr_premium,
    find_recent_us_reports,
    format_adr_block,
    format_session_context,
    session_context,
    taipei_now,
    weekly_title,
)

# Load environment variables from .env
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# US market targets for the weekly recap
US_SYMBOLS = [
    "^GSPC",    # S&P 500
    "^IXIC",    # NASDAQ
    "^SOX",     # 費城半導體指數
    "TSM",      # 台積電 ADR
    "NVDA",     # NVIDIA
    "AMD",      # AMD
    "AVGO",     # Broadcom
]

def weekly_topics(date_str):
    year = str(date_str)[:4]
    return [
        "NVIDIA AI chip demand",
        "TSM TSMC ADR stock",
        "US semiconductor industry outlook",
        f"Federal Reserve interest rate {year}",
        "US stock market weekly recap",
        "S&P 500 NASDAQ weekly performance",
    ]

# Reports directory
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


def load_existing_daily_reports(date_str=None, reports_dir=REPORTS_DIR, window_days=7):
    """US observation reports from the last 7 days (before today) as context."""
    date_str = date_str or taipei_now().date().isoformat()
    combined = ""
    for day, report_path in find_recent_us_reports(reports_dir, date_str, window_days=window_days):
        try:
            with open(report_path, "r", encoding="utf-8") as f:
                content = f.read()
            combined += f"\n--- {day} 的每日觀察 ---\n{content[:1500]}\n"
            print(f"  📄 已載入: {os.path.basename(report_path)}")
        except Exception:
            pass
    return combined


def generate_weekly_prompt(date_str, weekly_data_summary, news_summary, daily_reports_context, *, session_ctx=None, adr_block=""):
    """
    Custom prompt for weekly US market video script.
    Designed for NotebookLM podcast/video generation — narrative style.
    Calendar facts (weekday, closure reason, next session) come from twse_calendar.
    """
    session_ctx = session_ctx or session_context(date_str)
    next_label = session_ctx["next_label"]
    period = f"{session_ctx['closure_label']}休市期間" if session_ctx["is_holiday_break"] else "本週"
    return f"""
    You are a professional financial content creator helping produce a Taiwan investor YouTube video script.
    The video is a recap of the US stock market this week, for Taiwan investors before the next TWSE session.

    # === 台股行事曆（以此為準） ===
    - {format_session_context(session_ctx)}

    Please write the script in **Traditional Chinese (繁體中文)**.

    # === 本週美股數據（含每日走勢） ===
    {weekly_data_summary}

    # === 台積電 ADR 換算（本機計算） ===
    {adr_block or format_adr_block(None)}

    # === 本週相關新聞 ===
    {news_summary}

    # === {period}每日觀察報告（參考用） ===
    {daily_reports_context if daily_reports_context else "（無每日報告資料）"}

    # === 資料使用規則 ===
    - {NO_INVENTION_RULE}
    - {NO_PREDICTION_RULE}
    - 每日觀察報告只是參考：其中若有溢價、跳空等預測式說法，不得沿用。

    # === 文案撰寫要求 ===

    ## 風格與語氣
    - **敘事型文案**：像是跟朋友聊天分享這週美股發生什麼事，不是念表格
    - 語氣：專業但親切，像 YouTuber 講給觀眾聽的投資觀察
    - 節奏感：每段有「吸引注意的開場 → 事實與數據 → 小結論」的結構
    - **禁止**使用命令式語氣（如「建議買入」「應該賣出」），改用觀察式語句

    ## 文案結構（請嚴格遵守這個順序）

    ### 1. 開場 Hook（2-3 句）
    - 一句話勾起觀眾好奇心，例如「{period}，美股到底發生了什麼事？」
    - 用一句話概括這週美股的大方向（漲/跌/震盪）

    ### 2. 指數總覽：這週美股怎麼走？
    - 描述 S&P 500、NASDAQ 的「一週故事線」：週一怎樣，週中有什麼轉折，週五收怎樣
    - 用百分比和點數說明週漲跌幅
    - 提到關鍵事件驅動（Fed 發言、財報、經濟數據等）

    ### 3. 📢 節目口播贊助（約影片 1 分鐘處）
    - 在大致這時候（講完大盤後），插入一段自然的「口播廣告（ad-read）」。
    - 範例語氣：「在進入重點個股之前，插播一下！如果你覺得今天這個客製化的 AI 財經報告對你有幫助，這套系統現在開放免費測試中！**免費試用連結已經放在影片下方的說明欄**，趕快點擊連結去生成你手中持股的專屬分析報告吧！記得分享給一起投資的朋友喔～好，我們回到美股...」
    - 聲明提醒：「另外也提醒，這個網站功能還在 Beta 測試階段，結果僅供學習參考，不構成投資建議喔！」

    ### 4. 半導體重點：費半、台積電 ADR、NVIDIA
    - **費城半導體指數**：一週表現、趨勢方向
    - **台積電 ADR (TSM)**：這是觀眾最在意的！詳細描述每日走勢變化；與台股最後收盤的溢價／折價只能引用「台積電 ADR 換算」區塊，資料不足時不得提及
    - **NVIDIA**：財報前的市場情緒、股價走勢
    - **AMD、Broadcom**：簡述表現

    ### 5. 技術面觀察
    - 用白話描述 MA5/MA20 的相對位置（例如「短線均線仍然站在長線之上，趨勢還沒有轉弱」）
    - RSI 是否接近超買或超賣
    - 不要只丟數字，要用故事方式解讀「這代表什麼」

    ### 6. 台股{next_label}開盤前觀察
    - 🟢 目前對台股有利的訊號（列 3-5 點）
    - 🔴 目前對台股不利的訊號（列 3-5 點）
    - 整體偏多/偏空的觀察結論（注意：是「觀察」不是「預測」）
    - 提醒觀眾：「美股收盤到台股{next_label}開盤之間還有時間差，任何消息都可能改變方向」

    ### 7. 收尾
    - 一句話總結
    - 提醒投資人做好風險控管

    ## 數字格式（重要！）
    在文案中提及關鍵數字時，**必須**在阿拉伯數字後加上中文括號標註，確保 TTS 語音朗讀正確：
    - 指數：6,832 點（六千八百三十二點）
    - 股價：368.10 美元（三百六十八點一美元）
    - 百分比：2.5%（百分之二點五）
    - 不需要每個數字都標，挑「口語中會念出來的關鍵數字」標註即可

    ## 格式
    - 使用 Markdown 格式
    - 每個段落之間用 `---` 分隔
    - 段落標題用 `##` 或 `###`
    - 重要觀點用 **粗體** 標記

    請生成完整的影片文案。標題使用：「{weekly_title(session_ctx)}」
    """


def main():
    now = taipei_now()
    date_str = now.strftime("%Y-%m-%d")
    session_ctx = session_context(date_str, now=now)
    print(f"{'='*60}")
    print(f"  {weekly_title(session_ctx)}")
    print(f"  日期: {now.strftime('%Y-%m-%d %H:%M')}（台北時間）")
    print(f"  {format_session_context(session_ctx)}")
    print(f"  模式: 週報型文案（NotebookLM 影片用）")
    print(f"{'='*60}\n")

    if not TAVILY_API_KEY or not GEMINI_API_KEY:
        print("[Error] 請在 .env 檔案中設定 TAVILY_API_KEY 和 GEMINI_API_KEY")
        return EXIT_CONFIG

    fetcher = DataFetcher(TAVILY_API_KEY)
    analyzer = MarketAnalyzer(GEMINI_API_KEY)

    # ========================================
    # 1. Fetch Weekly US Market Data
    # ========================================
    weekly_data = {}
    print("📊 正在獲取一週美股數據（每日收盤序列）...")
    for symbol in US_SYMBOLS:
        data = unwrap_fetch_result(fetcher.get_weekly_stock_data(symbol, trading_days=5))
        if data:
            weekly_data[symbol] = data
            print(f"  ✅ {symbol}: 週收 ${data['week_close']} "
                  f"(週漲跌 {data['week_pct_change']:+.2f}%) "
                  f"高:{data['week_high']} 低:{data['week_low']} "
                  f"RSI={data['rsi']}")
        else:
            print(f"  ❌ {symbol}: 失敗")

    adr = fetch_adr_premium(fetcher, (weekly_data.get("TSM") or {}).get("week_close"))
    adr_block = format_adr_block(adr, session_ctx)
    if adr:
        print(f"  ✅ ADR 相對台股 {adr['premium_pct']:+.2f}%（本機計算）")
    else:
        print("  ⚠️  ADR 換算資料不足，週報不提溢價")

    # ========================================
    # 2. Fetch Week's News (expanded range)
    # ========================================
    news_data = []
    print("\n📰 正在獲取本週美股相關新聞...")
    for topic in weekly_topics(date_str):
        query = f"{topic} market news this week"
        results = unwrap_fetch_result(fetcher.get_news(query, days=7), default=[])
        if results:
            news_data.extend(results)
            print(f"  ✅ {topic}: 找到 {len(results)} 篇文章")
        else:
            print(f"  ⚠️  {topic}: 未找到文章")

    # Deduplicate
    unique_news = list({n['url']: n for n in news_data}.values())
    print(f"\n  📋 獨特新聞文章總數: {len(unique_news)}")

    # ========================================
    # 3. Load Existing Daily Reports
    # ========================================
    print("\n📁 載入近 7 日美股觀察報告...")
    daily_reports_context = load_existing_daily_reports(date_str)

    # ========================================
    # 4. Prepare Data Summary
    # ========================================

    # Build weekly data summary with daily series
    weekly_data_summary = ""
    for symbol, data in weekly_data.items():
        weekly_data_summary += f"\n## {symbol}\n"
        weekly_data_summary += (
            f"- 週收盤: {data['week_close']}, "
            f"週漲跌: {data['week_change']} ({data['week_pct_change']}%)\n"
            f"- 週內最高: {data['week_high']}, 週內最低: {data['week_low']}\n"
            f"- 均量: {data['avg_volume']:,}\n"
            f"- 技術指標: MA5={data['ma5']}, MA20={data['ma20']}, RSI={data['rsi']}\n"
        )
        weekly_data_summary += "- 每日走勢:\n"
        for day in data['daily_series']:
            weekly_data_summary += (
                f"  - {day['date']}: 收 {day['close']} "
                f"({day['pct_change']:+.2f}%) 量 {day['volume']:,}\n"
            )

    # News summary
    news_summary = ""
    for item in unique_news:
        title = item.get('title', 'No title')
        url = item.get('url', '')
        news_summary += f"- {title} ({url})\n"

    # ========================================
    # 5. Generate Video Script via Gemini
    # ========================================
    prompt = generate_weekly_prompt(
        date_str,
        weekly_data_summary,
        news_summary,
        daily_reports_context,
        session_ctx=session_ctx,
        adr_block=adr_block,
    )

    print("\n🤖 正在使用 Gemini 生成週報型影片文案...")
    report_content = analyzer._call_gemini_with_retry(prompt)
    # Same check as analyzer.abort_on_generation_error (error string / empty answer).
    if is_report_error(report_content):
        print(f"\n❌ 週報生成失敗，未寫檔: {str(report_content)[:200]}")
        return EXIT_GEMINI_FAILED
    report_content = sanitize_narrative_text(report_content)

    # ========================================
    # 6. Save Report (special_topics layout)
    # ========================================
    filepath = special_topic_file(REPORTS_DIR, date_str, US_WEEKLY_SLUG, "report", ensure=True)
    filename = os.path.basename(filepath)
    write_text_atomic(filepath, report_content)

    print(f"\n✅ 週報文案已儲存至: {filepath}")

    # ========================================
    # 7. Generate YouTube Thumbnails (A/B Test) into the same topic folder
    # ========================================
    print("\n🎬 開始生成 YouTube 縮圖與標題（A/B Test）...")
    try:
        ab_results = generate_ab_test_thumbnails(
            gemini_api_key=GEMINI_API_KEY,
            openai_api_key=None,
            report_content=report_content,
            reports_dir=special_topic_dir(REPORTS_DIR, date_str, US_WEEKLY_SLUG),
            num_titles=2,
            run_date=date_str,
        )
        print_ab_test_summary(ab_results)
    except Exception as exc:
        print(f"  ⚠️  A/B 標題與縮圖 prompt 產生失敗（週報已保存）: {exc}")

    print("✅ 全部完成! 你可以：")
    print(f"   1. 將 {filename} 匯入 NotebookLM 生成影片")
    print(f"   2. 從上方 A/B Test 素材中挑選標題和縮圖")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
