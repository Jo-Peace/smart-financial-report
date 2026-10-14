"""
AI 個股研究員 MVP — FastAPI Backend
"""
import os
import re
import sys
import logging
import ipaddress
import threading
import concurrent.futures

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

# Add parent directory to path so we can import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from modules.data_fetcher import DataFetcher
from modules.fetch_result import is_fetch_result, unwrap_fetch_result
from modules.analyzer import MarketAnalyzer
from app.database import (
    init_db, get_cached_report, save_report, get_remaining_quota, use_quota, refund_quota,
    get_cache_stats, check_global_limit, get_global_remaining, get_recent_reports,
    get_top_hot_stocks, increment_views, log_generation, healthcheck, storage_is_persistent,
    storage_fallback_from, get_failed_attempts, record_failed_attempt,
    DAILY_FREE_QUOTA, DAILY_GLOBAL_LIMIT, DAILY_IP_FAILED_ATTEMPT_LIMIT,
)
from modules.twse_tape import pct_text
from app.research_data import (
    STOCK_NAMES,
    format_institutional_lines,
    listing_known,
    lookup_listing,
    lookup_stock_name,
    monthly_revenue,
    normalize_ticker,
    shares_to_lots,
    stock_profile,
    taipei_now,
)

logger = logging.getLogger("app.main_app")

# === Config ===
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


def _int_env(name, default):
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


# Web requests should fail fast: 1 retry (2 Gemini calls max) instead of the batch default.
WEB_GEMINI_MAX_RETRIES = max(0, _int_env("WEB_GEMINI_MAX_RETRIES", 1))
# get_news does at most one fallback search → 2 Tavily calls per report.
WEB_TAVILY_SEARCHES_PER_REPORT = max(1, _int_env("WEB_TAVILY_SEARCHES_PER_REPORT", 2))
# Number of trusted reverse proxies that append to X-Forwarded-For (Render: 1).
TRUSTED_PROXY_HOPS = max(0, _int_env("TRUSTED_PROXY_HOPS", 1))
# Optional platform header that already carries the real client IP (e.g. CF-Connecting-IP).
CLIENT_IP_HEADER = (os.getenv("CLIENT_IP_HEADER") or "").strip()
# LOG_CLIENT_IP_HEADERS=1 logs the raw forwarding headers of the first request once,
# so TRUSTED_PROXY_HOPS / CLIENT_IP_HEADER can be set from what the platform really sends.
LOG_CLIENT_IP_HEADERS = (os.getenv("LOG_CLIENT_IP_HEADERS") or "").strip().lower() in {"1", "true", "yes", "on"}
_FORWARDING_HEADERS = ("X-Forwarded-For", "X-Real-IP", "True-Client-IP", "CF-Connecting-IP", "Forwarded")
_ip_headers_logged = False
# Internal hops (RFC 1918, CGNAT, loopback, link-local, IPv6 ULA) are never a public
# client. Deliberately explicit: ipaddress.is_private also covers documentation ranges.
_INTERNAL_NETS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "127.0.0.0/8",
        "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10",
    )
)

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' https://cdn.jsdelivr.net",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data: https:",
        "connect-src 'self'",
        "frame-src https://www.youtube.com https://www.youtube-nocookie.com",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

GENERIC_FAILURE = "報告生成失敗，請稍後再試"
NO_CHARGE_NOTE = "（本次不扣額度）"

# === Init ===
app = FastAPI(title="AI 個股研究員", version="1.1.0")
init_db()

# Serve static files
static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# FastAPI's Swagger / ReDoc pages need inline scripts, so the strict CSP skips them.
_DOCS_PATHS = frozenset(
    p for p in (app.docs_url, app.redoc_url, app.openapi_url, app.swagger_ui_oauth2_redirect_url) if p
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path not in _DOCS_PATHS:
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


# === Models ===
class ResearchRequest(BaseModel):
    ticker: str


class ResearchError(Exception):
    """Expected generation failure with a user-safe message (details go to the log)."""

    def __init__(self, status_code: int, public_message: str, hint: str = ""):
        super().__init__(public_message)
        self.status_code = status_code
        self.public_message = public_message
        self.hint = hint


def get_client_ip(request: Request) -> str:
    """
    Extract the client IP without trusting client-controlled values.

    The left-most X-Forwarded-For entry is whatever the client sent, so it is
    never used. Each trusted proxy appends one hop on the right; the client IP
    is therefore the TRUSTED_PROXY_HOPS-th entry counted from the right. If that
    entry is an internal address (the platform added more internal hops than
    configured), the nearest public hop to its left is used instead, so a
    miscounted chain cannot collapse every visitor onto one proxy IP.
    """
    ip = _resolve_client_ip(request)
    if LOG_CLIENT_IP_HEADERS:
        _log_forwarding_headers_once(request, ip)
    return ip


def _resolve_client_ip(request: Request) -> str:
    if CLIENT_IP_HEADER:
        value = (request.headers.get(CLIENT_IP_HEADER) or "").strip()
        if value:
            return value
    forwarded = request.headers.get("X-Forwarded-For") or ""
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    if hops and TRUSTED_PROXY_HOPS > 0:
        index = max(0, len(hops) - TRUSTED_PROXY_HOPS)
        for candidate in range(index, -1, -1):
            if not _is_internal_ip(hops[candidate]):
                return hops[candidate]
        return hops[index]
    return request.client.host if request.client else "unknown"


def _is_internal_ip(value: str) -> bool:
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return False
    return any(addr in net for net in _INTERNAL_NETS if net.version == addr.version)


def _log_forwarding_headers_once(request: Request, resolved_ip: str) -> None:
    """One WARNING line with the raw proxy headers (verification aid for the deploy)."""
    global _ip_headers_logged
    if _ip_headers_logged:
        return
    _ip_headers_logged = True
    raw = {name: request.headers.get(name) for name in _FORWARDING_HEADERS if request.headers.get(name)}
    logger.warning(
        "client-ip headers: %s peer=%s resolved=%s (TRUSTED_PROXY_HOPS=%s, CLIENT_IP_HEADER=%s)",
        raw,
        request.client.host if request.client else None,
        resolved_ip,
        TRUSTED_PROXY_HOPS,
        CLIENT_IP_HEADER or "-",
    )


def gemini_calls_remaining() -> int | None:
    """Remaining Gemini calls under GEMINI_DAILY_CALL_LIMIT (None = no limit / unknown)."""
    try:
        from modules import ai_usage_guard

        limit = ai_usage_guard._daily_limit()
        if not limit:
            return None
        # Prefer the status-weighted usage the guard actually enforces (when available).
        usage_fn = getattr(ai_usage_guard, "get_today_gemini_usage", None) or ai_usage_guard.get_today_gemini_call_count
        # Each new report needs at least one call, so remaining calls cap remaining reports.
        return max(0, int(limit - float(usage_fn() or 0)))
    except Exception:
        logger.exception("Gemini usage guard unavailable")
        return None


def _global_remaining() -> int:
    remaining = get_global_remaining()
    gemini_remaining = gemini_calls_remaining()
    if gemini_remaining is not None:
        remaining = min(remaining, gemini_remaining)
    return max(0, remaining)


def _display_name(ticker: str) -> str:
    return lookup_stock_name(ticker, allow_fetch=False) or "未知"


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _iso_date(value) -> str | None:
    text = str(value or "").strip()
    return text[:10] if _ISO_DATE_RE.match(text) else None


def _price_bar_date(data: dict, result) -> str | None:
    """Trade date of the K-bar behind the price (payload or fetch meta ``bar_date``)."""
    meta = (result.get("meta") or {}) if is_fetch_result(result) else {}
    for value in (data.get("bar_date"), meta.get("bar_date"), data.get("date")):
        day = _iso_date(value)
        if day:
            return day
    return None


def _fetch_price(fetcher, ticker: str, market: str | None):
    symbols = [f"{ticker}.TWO", f"{ticker}.TW"] if market == "上櫃" else [f"{ticker}.TW", f"{ticker}.TWO"]
    for symbol in symbols:
        result = fetcher.get_stock_data(symbol)
        data = unwrap_fetch_result(result)
        if data:
            bar_date = _price_bar_date(data, result)
            if bar_date and data.get("bar_date") != bar_date:
                data = dict(data, bar_date=bar_date)
            return symbol, data
    return symbols[0], None


def _fmt(value, suffix="") -> str:
    if value is None or value == "":
        return "資料未提供"
    return f"{value}{suffix}"


def build_research_prompt(
    *,
    ticker,
    stock_name,
    market,
    profile,
    listing,
    yf_symbol,
    stock_data,
    institutional,
    institutional_date,
    revenue,
    news,
    date_str,
):
    """Build the research prompt from ground-truth blocks only (pure function, unit-tested)."""
    name_label = stock_name or "公司名稱未提供"
    # Date of the price actually quoted below (not the 12 h-cached exchange table date).
    price_date = _iso_date(stock_data.get("bar_date"))

    company_lines = [f"- 代號：{ticker}", f"- 名稱：{name_label}", f"- 市場別：{_fmt(market)}"]
    if profile:
        company_lines.append(f"- 產業定位：{_fmt(profile.get('sector'))}")
        company_lines.append(f"- 供應鏈角色：{_fmt(profile.get('supply_chain_role'))}")
        if profile.get("key_products"):
            company_lines.append(f"- 主要產品：{'、'.join(profile['key_products'])}")
        if profile.get("ai_connection"):
            company_lines.append(f"- AI 關聯：{profile['ai_connection']}")
    if revenue and revenue.get("industry"):
        company_lines.append(f"- 產業別（公開資訊觀測站）：{revenue['industry']}")

    volume_lots = shares_to_lots(stock_data.get("volume"))
    avg_lots = shares_to_lots(stock_data.get("avg_vol_5d"))
    if stock_data.get("pct_change") is not None:
        move = f"漲跌 {stock_data.get('change')} 元（{stock_data.get('pct_change')}%）"
    else:
        # 除權息 X day (item 6 payload): no reference price, so no move is printed.
        move = f"漲跌 {pct_text(stock_data, na='資料未提供')}"
    price_lines = [
        f"- {yf_symbol}：價格 {stock_data.get('price')} 元，{move}"
        + (f"；股價日期 {price_date}" if price_date else ""),
        f"- 成交量：{volume_lots:,} 張" if volume_lots is not None else "- 成交量：資料未提供",
        f"- 5 日均量：{avg_lots:,} 張" if avg_lots is not None else "- 5 日均量：資料未提供",
        f"- MA5：{_fmt(stock_data.get('ma5'))}；MA20：{_fmt(stock_data.get('ma20'))}；RSI：{_fmt(stock_data.get('rsi'))}",
    ]
    # The exchange table is a cross-check only when it is the same session as the quote.
    if listing and listing.get("close") is not None and price_date and listing.get("date") == price_date:
        price_lines.append(f"- 交易所收盤（同日核對）：{listing['close']} 元（{price_date}）")

    institutional_block = format_institutional_lines(institutional, institutional_date, price_date)

    if revenue and revenue.get("revenue_100m") is not None:
        revenue_block = "\n".join(
            [
                f"- 資料年月：{_fmt(revenue.get('year_month'))}（公開資訊觀測站月營收）",
                f"- 當月營收：{revenue['revenue_100m']:,} 億元",
                f"- 月增率：{_fmt(_round(revenue.get('mom_pct')), '%')}；年增率：{_fmt(_round(revenue.get('yoy_pct')), '%')}",
                f"- 累計營收年增率：{_fmt(_round(revenue.get('ytd_yoy_pct')), '%')}",
            ]
            + ([f"- 公司備註：{revenue['note']}"] if revenue.get("note") not in ("", "-", None) else [])
        )
        revenue_rule = "- 依據上方「月營收」數據解讀營收動能（月增、年增、累計年增）。"
    else:
        revenue_block = "- 月營收：資料未提供"
        revenue_rule = "- 本次未取得月營收數據：營收段落請寫「資料未提供」，不得自行推估。"

    news_lines = []
    for item in (news or [])[:8]:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        snippet = " ".join(str(item.get("content") or "").split())[:200]
        published = str(item.get("published_date") or "").strip()
        line = f"- {title}"
        if published:
            line += f"（{published[:10]}）"
        if snippet:
            line += f"\n  摘要：{snippet}"
        if item.get("url"):
            line += f"\n  來源：{item['url']}"
        news_lines.append(line)
    news_block = "\n".join(news_lines) if news_lines else "- 本次未取得相關新聞"

    company_rule = (
        "- 依據上方「公司基本資料」描述產業別、主營業務與護城河。"
        if profile
        else "- 本次沒有本機公司資料：只寫代號、名稱與市場別；主營業務若新聞摘要未提及，請寫「資料未提供」，不得自行補充。"
    )

    return f"""
    You are a professional financial analyst specializing in the Taiwan stock market.
    Create a research report for stock {ticker} ({name_label}) as of {date_str} (Asia/Taipei).

    You MUST output in Traditional Chinese (繁體中文) Markdown format.

    # 資料使用規則（最重要）
    - 只能使用下方提供的數據與新聞摘要。
    - 未提供的數據（例如 EPS、毛利率、目標價、未提供的營收數字）一律寫「資料未提供」，禁止自行推估或編造數字。
    - 引用新聞中的數字時，必須說明出自哪則新聞。
    - 單位已換算：成交量與法人買賣超都是「張」（1 張 = 1,000 股），不要再換算。

    # 公司基本資料
    {chr(10).join(company_lines)}

    # 股價數據
    {chr(10).join(price_lines)}

    # 籌碼數據
    {institutional_block}

    # 月營收
    {revenue_block}

    # 相關新聞（標題＋摘要）
    {news_block}

    # 報告要求（請嚴格按照以下結構生成內容，直接輸出 Markdown，不要閒話）

    ## 1. 🏢 公司概況與核心業務
    {company_rule}

    ## 2. 📈 技術面與籌碼面快照
    - 建立表格包含：價格、漲跌、漲跌幅、成交量（張）、MA5、MA20、RSI。
    - 均線與量價判斷（例如：多頭/空頭/糾結、量價配合度）。
    - 籌碼動態解讀（只用上方法人買賣超張數；若法人資料非當日，必須寫明資料日期）。

    ## 3. 📊 基本面與營收動能
    {revenue_rule}
    - EPS 與毛利率本次未提供：請寫「資料未提供」，除非新聞摘要中有明確數字並註明出處。

    ## 4. 🔍 盤面歸因分析（Why Behind the Move）
    - 列出 2~4 項近期股價波動的可能驅動力，必須對應到上方數據或新聞。
    - 使用「結果 ← 原因」的句型（例：「股價突破前高 ← 新聞指出法說會上修全年財測」）。

    ## 5. 📝 觀察結論與追蹤重點
    請將未來的觀察重點（Catalysts）按時間軸拆解：

    ### 短期（1-3 個月）
    - (列出 2-3 點短期觀察重點，如：月營收發佈、短期均線支撐等)

    ### 中期（3-6 個月）
    - (列出 2-3 點中期發展，僅限新聞或公司資料中有提到的事項)

    ### 長期（6-12 個月以上）
    - (列出 2-3 點長期趨勢題材，僅限上方資料支持的方向)

    > [!TIP] 核心觀察邏輯：請用 2-3 句話總結這檔股票目前最值得追蹤的重點，並給出目前股價位階的客觀評論（不構成投資建議）。

    > [!WARNING] 風險提示：請列出 2-3 項主要風險（如：特定技術尚在驗證、競爭對手降價、總體經濟影響等）。

    # 數字格式（重要！）
    在報告正文中提及關鍵數字時，必須在阿拉伯數字後加上中文括號標註（表格內的數字不需標註）：
    - 股價：1,915 元（一千九百一十五元）
    - 張數：12,634 張（一萬兩千六百三十四張）
    """


def _round(value, digits=2):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def generate_stock_research(ticker: str) -> dict:
    """
    Generate a research report for a single Taiwan stock.

    Returns {"content": markdown, "name": display name}. Raises ResearchError
    with a user-safe message on expected failures.
    """
    listing = lookup_listing(ticker)
    profile = stock_profile(ticker)
    stock_name = STOCK_NAMES.get(ticker) or profile.get("name") or (listing or {}).get("name") or ""
    market = (listing or {}).get("market")

    fetcher = DataFetcher(TAVILY_API_KEY, tavily_search_limit=WEB_TAVILY_SEARCHES_PER_REPORT)
    analyzer = MarketAnalyzer(GEMINI_API_KEY)

    # 1. Price first: it is cheap and proves the code is real before any Tavily spend.
    yf_symbol, stock_data = _fetch_price(fetcher, ticker, market)
    if not stock_data:
        raise ResearchError(
            404,
            f"無法取得 {ticker} 的股價資料",
            "可能原因：(1) 代號錯誤 (2) 雲端行情來源暫時被擋 (3) 新上市尚無足夠日線。請稍後再試" + NO_CHARGE_NOTE,
        )

    # 2. News / institutional / monthly revenue in parallel (all optional)
    search_query = f"{stock_name or ticker} {ticker} 台股 營收 展望 法人 {taipei_now().year}"
    listed_only = market != "上櫃"  # T86 and t187ap05_L only cover TWSE-listed stocks
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_news = executor.submit(fetcher.get_news, search_query, 7)
        future_inst = (
            executor.submit(fetcher.get_single_stock_institutional_data, ticker) if listed_only else None
        )
        future_rev = executor.submit(monthly_revenue, ticker) if listed_only else None

        news_data = unwrap_fetch_result(future_news.result(), default=[]) or []
        institutional, institutional_date = None, None
        if future_inst is not None:
            inst_result = future_inst.result()
            institutional = unwrap_fetch_result(inst_result, default=None)
            if is_fetch_result(inst_result):
                institutional_date = (inst_result.get("meta") or {}).get("data_date")
        revenue = None
        if future_rev is not None:
            try:
                revenue = future_rev.result()
            except Exception:
                logger.exception("monthly revenue lookup failed for %s", ticker)

    prompt = build_research_prompt(
        ticker=ticker,
        stock_name=stock_name,
        market=market,
        profile=profile,
        listing=listing,
        yf_symbol=yf_symbol,
        stock_data=stock_data,
        institutional=institutional,
        institutional_date=institutional_date,
        revenue=revenue,
        news=news_data,
        date_str=taipei_now().strftime("%Y-%m-%d"),
    )

    report = analyzer._call_gemini_with_retry(prompt, max_retries=WEB_GEMINI_MAX_RETRIES)
    if not report or not str(report).strip():
        raise ResearchError(502, GENERIC_FAILURE, NO_CHARGE_NOTE.strip("（）"))
    if str(report).startswith("Error generating"):
        logger.warning("Gemini generation failed for %s: %s", ticker, str(report)[:300])
        if "daily call limit" in str(report).lower():
            raise ResearchError(
                503,
                "今日 AI 分析額度已用完 🔒",
                "已生成的報告仍可免費查閱，明天再來產生新報告" + NO_CHARGE_NOTE,
            )
        raise ResearchError(502, GENERIC_FAILURE, "AI 服務暫時無法回應" + NO_CHARGE_NOTE)
    return {"content": report, "name": stock_name or ticker}


# === In-flight guard: one generation per ticker at a time ===
_inflight_lock = threading.Lock()
_inflight_tickers: set[str] = set()


def _claim_ticker(ticker: str) -> bool:
    with _inflight_lock:
        if ticker in _inflight_tickers:
            return False
        _inflight_tickers.add(ticker)
        return True


def _release_ticker(ticker: str) -> None:
    with _inflight_lock:
        _inflight_tickers.discard(ticker)


def _cached_response(ticker: str, cached: dict, ip: str):
    if "date" in cached:
        increment_views(ticker, cached["date"], viewer=ip)
    return {
        "ticker": ticker,
        "name": lookup_stock_name(ticker, allow_fetch=False) or ticker,
        "content": cached["content"],
        "cached": True,
        "remaining_quota": get_remaining_quota(ip),
        "message": f"📦 快取命中！此報告生成於 ({cached.get('date', '今日')})，直接觀看（不扣本次額度）",
    }


# === Routes ===
# Sync handlers on purpose: FastAPI runs them in its threadpool, so slow network /
# Gemini calls never block the event loop (home page, quota and cache hits stay fast).

@app.get("/", response_class=HTMLResponse)
def home():
    """Serve the frontend."""
    index_path = os.path.join(static_dir, "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/healthz")
def healthz():
    """Deploy health check: DB writable + required keys configured (no secrets returned)."""
    checks = {"db": False, "gemini_key": bool(GEMINI_API_KEY), "tavily_key": bool(TAVILY_API_KEY)}
    try:
        checks["db"] = bool(healthcheck())
    except Exception:
        logger.exception("DB health check failed")
    # A configured STOCK_RESEARCH_DB_PATH that fell back to the ephemeral app dir
    # (disk not mounted) fails the check, so the deploy does not go live silently.
    checks["configured_storage"] = storage_fallback_from() is None
    ok = all(checks.values())
    # Informational: without a configured disk the DB is ephemeral (quotas/cache reset on restart).
    info = {"persistent_storage": storage_is_persistent()}
    return JSONResponse(status_code=200 if ok else 503, content={"ok": ok, "checks": checks, "info": info})


@app.get("/api/quota")
def check_quota(request: Request):
    """Check remaining quota for the requesting IP."""
    ip = get_client_ip(request)
    return {
        "remaining": get_remaining_quota(ip),
        "total": DAILY_FREE_QUOTA,
        "global_remaining": _global_remaining(),
        "global_total": DAILY_GLOBAL_LIMIT,
    }


@app.get("/api/recent")
def recent_reports():
    """Get list of recently cached reports (within last 3 days) available for free reading."""
    recent = get_recent_reports(days=3, limit=12)
    for r in recent:
        r["name"] = _display_name(r["ticker"])
    return recent

@app.get("/api/hot_stocks")
def hot_stocks():
    """Most viewed cached reports this week (real view counts) + labelled editor picks."""
    hot = get_top_hot_stocks(limit=5)
    for r in hot:
        r["name"] = _display_name(r["ticker"])
    return hot


@app.post("/api/research")
def research(req: ResearchRequest, request: Request):
    """Generate or retrieve a stock research report."""
    raw_ticker = (req.ticker or "").strip()
    ip = get_client_ip(request)

    # Check if ticker contains Chinese characters
    if any(u'一' <= c <= u'鿿' for c in raw_ticker):
        return JSONResponse(
            status_code=400,
            content={"error": "⚠️ 請輸入股票「代號」（例如：2330 或 2543），目前暫不支援直接輸入中文名稱查詢喔！"}
        )

    # Validate ticker format before any quota / network use
    ticker = normalize_ticker(raw_ticker)
    if not ticker:
        return JSONResponse(
            status_code=400,
            content={"error": "請輸入有效的台股代號（4～6 位數字，例如：2330、00878）"}
        )

    # Check cache first (doesn't cost quota)
    cached = get_cached_report(ticker)
    if cached:
        return _cached_response(ticker, cached, ip)

    # Reject codes that are not listed on TWSE/TPEx (skipped when the code table is unreachable)
    if listing_known(ticker) is False:
        return JSONResponse(
            status_code=404,
            content={
                "error": f"查無代號 {ticker}",
                "message": "請確認是上市或上櫃股票代號" + NO_CHARGE_NOTE,
                "remaining_quota": get_remaining_quota(ip),
            },
        )

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

    gemini_remaining = gemini_calls_remaining()
    if gemini_remaining is not None and gemini_remaining <= 0:
        return JSONResponse(
            status_code=503,
            content={
                "error": "今日 AI 分析額度已用完 🔒",
                "message": "已生成的報告仍可免費查閱，明天再來產生新報告" + NO_CHARGE_NOTE,
                "remaining_quota": remaining,
            },
        )

    # Check global daily limit (protect Gemini / Tavily quota; failed attempts count too)
    if not check_global_limit():
        return JSONResponse(
            status_code=503,
            content={
                "error": "今日全站分析額度已達上限 🔒",
                "message": f"為保護服務品質，每日新報告生成上限為 {DAILY_GLOBAL_LIMIT} 份。您仍可查詢今日已生成的股票報告（不受限制）。",
                "remaining_quota": remaining
            }
        )

    # Failed generations are refunded but still spend the global attempt cap, so
    # one client may only burn a few of them per day.
    if get_failed_attempts(ip) >= DAILY_IP_FAILED_ATTEMPT_LIMIT:
        return JSONResponse(
            status_code=429,
            content={
                "error": "今日生成失敗次數已達上限",
                "message": (
                    f"同一來源今天已有 {DAILY_IP_FAILED_ATTEMPT_LIMIT} 次生成失敗，為保護全站額度暫停產生新報告；"
                    "已生成的報告仍可免費查閱，明天再試。" + NO_CHARGE_NOTE
                ),
                "remaining_quota": remaining,
            },
        )

    if not use_quota(ip):
        return JSONResponse(
            status_code=429,
            content={
                "error": "今日免費額度已用完 🙏",
                "message": "每天 00:00 重置額度，明天再來！",
                "remaining_quota": 0,
            },
        )

    if not _claim_ticker(ticker):
        refund_quota(ip)
        return JSONResponse(
            status_code=409,
            content={
                "error": f"{ticker} 的報告正在生成中",
                "message": "請約 1 分鐘後再查詢，屆時可直接讀取快取" + NO_CHARGE_NOTE,
                "remaining_quota": get_remaining_quota(ip),
            },
        )

    try:
        # Another request may have finished this ticker while we waited for the claim
        cached = get_cached_report(ticker)
        if cached:
            refund_quota(ip)
            return _cached_response(ticker, cached, ip)

        log_generation(ticker, "started")
        try:
            result = generate_stock_research(ticker)
        except ResearchError as exc:
            refund_quota(ip)
            record_failed_attempt(ip)
            log_generation(ticker, "failed")
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "error": exc.public_message,
                    "message": exc.hint,
                    "remaining_quota": get_remaining_quota(ip),
                },
            )
        except Exception:
            logger.exception("research generation crashed for %s", ticker)
            refund_quota(ip)
            record_failed_attempt(ip)
            log_generation(ticker, "failed")
            return JSONResponse(
                status_code=500,
                content={
                    "error": GENERIC_FAILURE,
                    "message": NO_CHARGE_NOTE.strip("（）"),
                    "remaining_quota": get_remaining_quota(ip),
                },
            )

        save_report(ticker, result["content"], viewer=ip)
        log_generation(ticker, "success")
        return {
            "ticker": ticker,
            "name": result.get("name") or ticker,
            "content": result["content"],
            "cached": False,
            "remaining_quota": get_remaining_quota(ip),
            "message": "✨ 全新生成！已消耗 1 次額度"
        }
    finally:
        _release_ticker(ticker)


@app.get("/api/stats")
def stats():
    """Get cache statistics."""
    return get_cache_stats()


# === Run ===
if __name__ == "__main__":
    import uvicorn
    print("🚀 AI 個股研究員 MVP 啟動中...")
    print("📡 開啟瀏覽器前往 http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
