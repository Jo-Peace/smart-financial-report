"""
AI 個股研究員 MVP — FastAPI Backend
"""
import os
import sys
import datetime

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

# Add parent directory to path so we can import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from modules.data_fetcher import DataFetcher
from modules.analyzer import MarketAnalyzer
from app.database import init_db, get_cached_report, save_report, get_remaining_quota, use_quota, get_cache_stats, check_global_limit

# === Config ===
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# === Init ===
app = FastAPI(title="AI 個股研究員", version="1.0.0")
init_db()

# Serve static files
static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# === Models ===
class ResearchRequest(BaseModel):
    ticker: str


# === Stock name mapping (common Taiwan stocks) ===
STOCK_NAMES = {
    "1301": "台塑", "1303": "南亞", "2002": "中鋼", "2207": "和泰車",
    "2301": "光寶科", "2303": "聯電", "2308": "台達電", "2312": "金寶",
    "2317": "鴻海", "2327": "國巨", "2330": "台積電", "2337": "旺宏",
    "2345": "智邦", "2353": "宏碁", "2354": "鴻準", "2356": "英業達",
    "2357": "華碩", "2376": "技嘉", "2377": "微星", "2379": "瑞昱",
    "2382": "廣達", "2395": "研華", "2409": "友達", "2412": "中華電",
    "2454": "聯發科", "2474": "可成", "2492": "華新科", "2498": "宏達電",
    "2603": "長榮", "2609": "陽明", "2615": "萬海", "2880": "華南金",
    "2881": "富邦金", "2882": "國泰金", "2883": "凱基金", "2884": "玉山金",
    "2885": "元大金", "2886": "兆豐金", "2887": "台新金", "2890": "永豐金",
    "2891": "中信金", "2892": "第一金", "3008": "大立光", "3034": "聯詠",
    "3037": "欣興", "3105": "穩懋", "3189": "景碩", "3231": "緯創",
    "3260": "威剛", "3293": "鈊象", "3443": "創意", "3481": "群創",
    "3661": "世芯KY", "3711": "日月光", "4904": "遠傳", "4938": "和碩",
    "5347": "世界", "5871": "中租KY", "6239": "力成", "6415": "矽力KY",
    "6505": "台塑化", "6669": "緯穎", "8046": "南電", "8454": "富邦媒",
}


def get_client_ip(request: Request) -> str:
    """Extract client IP from request."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host


def generate_stock_research(ticker: str) -> str:
    """
    Generate a deep research report for a single stock.
    Reuses existing data_fetcher and analyzer modules.
    """
    stock_name = STOCK_NAMES.get(ticker, ticker)

    fetcher = DataFetcher(TAVILY_API_KEY)
    analyzer = MarketAnalyzer(GEMINI_API_KEY)

    # 1. Get stock data (Try .TW first, fallback to .TWO for OTC stocks)
    stock_data = None
    if ticker.isdigit():
        yf_symbol = f"{ticker}.TW"
        stock_data = fetcher.get_stock_data(yf_symbol)
        
        if not stock_data:
            yf_symbol = f"{ticker}.TWO"
            stock_data = fetcher.get_stock_data(yf_symbol)
    else:
        yf_symbol = ticker
        stock_data = fetcher.get_stock_data(yf_symbol)

    # 2. Get relevant news
    search_query = f"{stock_name} {ticker} 台股 營收 展望 法人 2026"
    news_data = fetcher.get_news(search_query, days=7)

    # 3. Get institutional data
    institutional_data = fetcher.get_institutional_data(top_n=10)

    # 4. Build market data dict
    market_data = {yf_symbol: stock_data}

    # 5. Generate report with custom prompt
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")

    data_summary = ""
    if stock_data:
        data_summary = (
            f"- {yf_symbol} ({stock_name}): 價格 {stock_data['price']}, "
            f"漲跌 {stock_data['change']} ({stock_data['pct_change']}%), "
            f"成交量 {stock_data['volume']}"
        )
        if stock_data.get('ma5'):
            data_summary += f", MA5={stock_data['ma5']}"
        if stock_data.get('ma20'):
            data_summary += f", MA20={stock_data['ma20']}"
        if stock_data.get('rsi') is not None:
            data_summary += f", RSI={stock_data['rsi']}"

    news_summary = ""
    if news_data:
        for item in news_data[:10]:
            news_summary += f"- {item['title']} ({item['url']})\n"

    # Institutional data for this specific stock
    inst_info = ""
    if institutional_data and institutional_data.get("top_buy"):
        for s in institutional_data["top_buy"]:
            if s["id"] == ticker:
                inst_info = f"\n此股三大法人動態：外資 {s['foreign_net']:+,}, 投信 {s['trust_net']:+,}, 合計 {s['total_net']:+,}\n"
                break
    if not inst_info and institutional_data and institutional_data.get("top_sell"):
        for s in institutional_data["top_sell"]:
            if s["id"] == ticker:
                inst_info = f"\n此股三大法人動態：外資 {s['foreign_net']:+,}, 投信 {s['trust_net']:+,}, 合計 {s['total_net']:+,}\n"
                break

    prompt = f"""
    You are a professional financial analyst specializing in Taiwan stock market.
    Create a comprehensive deep research report for stock {ticker} ({stock_name}) as of {date_str}.
    Write in Traditional Chinese (繁體中文) Markdown format.

    # 股票數據
    {data_summary}
    {inst_info}

    # 相關新聞
    {news_summary}

    # 報告要求（請嚴格按照以下結構）

    ## 1. 公司基本資料
    - 公司名稱、代碼、產業別
    - 主營業務概述

    ## 2. 股價技術面快照
    - 建立表格：價格、漲跌、漲跌幅、MA5、MA20、RSI
    - 均線排列判斷（多頭/空頭/糾結）
    - RSI 超買(>70)/超賣(<30) 判斷

    ## 3. 營收與獲利分析
    - 根據新聞中找到的數據，整理近期營收表現
    - EPS 趨勢（若有法人預估數據請列出）
    - 毛利率趨勢觀察

    ## 4. 成長引擎分析
    - 根據新聞，分析公司未來 1~2 年的主要成長動能
    - 至少列出 2~3 個成長引擎，附上具體數據

    ## 5. 🔍 盤面歸因分析（Why Behind the Move）
    - 用「結果 ← 原因」格式，分析近期股價表現的驅動力
    - 宏觀因素、產業催化劑、籌碼因素各至少 1 項

    ## 6. 風險評估
    - 列出 3~5 項主要風險
    - 短期風險 vs 長期風險

    ## 7. 投資結論
    - 綜合評價（偏多/中性/偏空）
    - 建議觀察重點

    # 數字格式（重要！）
    在報告正文中提及關鍵數字時，必須在阿拉伯數字後加上中文括號標註：
    - 股價：1,915 元（一千九百一十五元）
    - 張數：12,634 張（一萬兩千六百三十四張）
    - 表格內的數字不需要加中文標註。

    請生成完整、專業的深度研究報告。
    """

    report = analyzer._call_gemini_with_retry(prompt)
    return report


# === Routes ===

@app.get("/", response_class=HTMLResponse)
async def home():
    """Serve the frontend."""
    index_path = os.path.join(static_dir, "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/api/quota")
async def check_quota(request: Request):
    """Check remaining quota for the requesting IP."""
    ip = get_client_ip(request)
    remaining = get_remaining_quota(ip)
    return {"remaining": remaining, "total": 3}


@app.post("/api/research")
async def research(req: ResearchRequest, request: Request):
    """Generate or retrieve a stock research report."""
    ticker = req.ticker.strip().upper()
    ip = get_client_ip(request)

    # Validate ticker
    if not ticker or len(ticker) < 2:
        return JSONResponse(
            status_code=400,
            content={"error": "請輸入有效的股票代號（例如：2330）"}
        )

    # Check cache first (doesn't cost quota)
    cached = get_cached_report(ticker)
    if cached:
        remaining = get_remaining_quota(ip)
        stock_name = STOCK_NAMES.get(ticker, ticker)
        return {
            "ticker": ticker,
            "name": stock_name,
            "content": cached["content"],
            "cached": True,
            "remaining_quota": remaining,
            "message": f"📦 快取命中！此報告今日稍早已生成（不扣額度）"
        }

    # Check quota (only for new reports)
    remaining = get_remaining_quota(ip)
    if remaining <= 0:
        return JSONResponse(
            status_code=429,
            content={
                "error": "今日免費額度已用完 🙏",
                "message": "每天 00:00 重置額度，明天再來！或查詢今日已有人查過的股票（不扣額度）。",
                "remaining_quota": 0
            }
        )

    # Check global daily limit (protect Gemini API quota)
    if not check_global_limit():
        return JSONResponse(
            status_code=503,
            content={
                "error": "今日全站分析額度已達上限 🔒",
                "message": "為保護服務品質，每日新報告生成上限為 20 份。您仍可查詢今日已生成的股票報告（不受限制）。",
                "remaining_quota": remaining
            }
        )

    # Use quota and generate
    use_quota(ip)
    remaining = get_remaining_quota(ip)

    try:
        stock_name = STOCK_NAMES.get(ticker, ticker)
        content = generate_stock_research(ticker)

        # Check if Gemini returned an error string instead of a valid report
        if content and content.startswith("Error generating"):
            return JSONResponse(
                status_code=500,
                content={"error": content, "remaining_quota": remaining}
            )

        # Save to cache
        save_report(ticker, content)

        return {
            "ticker": ticker,
            "name": stock_name,
            "content": content,
            "cached": False,
            "remaining_quota": remaining,
            "message": f"✨ 全新生成！已消耗 1 次額度"
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"報告生成失敗：{str(e)}", "remaining_quota": remaining}
        )


@app.get("/api/stats")
async def stats():
    """Get cache statistics."""
    return get_cache_stats()


# === Run ===
if __name__ == "__main__":
    import uvicorn
    print("🚀 AI 個股研究員 MVP 啟動中...")
    print("📡 開啟瀏覽器前往 http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
