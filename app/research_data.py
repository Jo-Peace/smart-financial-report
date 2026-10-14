"""
Ground-truth helpers for the AI 個股研究員 web app.

Everything here is fail-safe: a missing network table or a broken local file
degrades to "資料未提供" instead of crashing the request or letting the model
guess. Remote tables are TWSE / TPEx public open data and are cached in-process.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import re
import threading
import time

import requests

logger = logging.getLogger(__name__)

from modules import tz as _tz  # the one Taipei clock
from modules.tz import TAIPEI_TZ  # noqa: F401
TICKER_RE = re.compile(r"^\d{4,6}[A-Z]?$")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOCK_DB_PATH = os.path.join(BASE_DIR, "data", "stock_db.json")

TWSE_LISTING_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_LISTING_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
TWSE_MONTHLY_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"

REMOTE_TTL_SEC = 12 * 3600
REMOTE_RETRY_AFTER_SEC = 300
HTTP_TIMEOUT_SEC = 10

# Common Taiwan stocks (kept for offline name lookup when open data is unreachable).
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


def taipei_now() -> datetime.datetime:
    """Asia/Taipei wall clock (modules.tz, the one implementation)."""
    return _tz.now_tpe()


def taipei_today_str() -> str:
    return taipei_now().strftime("%Y-%m-%d")


def normalize_ticker(raw) -> str | None:
    """Return a canonical Taiwan stock code (e.g. ``2330``, ``00878``, ``2883B``) or None."""
    text = str(raw or "").strip().upper()
    for suffix in (".TWO", ".TW"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text if TICKER_RE.match(text) else None


def roc_to_iso(value) -> str | None:
    """Convert ROC dates like ``1151012`` (or ``11509`` year-month) to ISO strings."""
    text = re.sub(r"\D", "", str(value or ""))
    try:
        if len(text) == 7:
            return datetime.date(int(text[:3]) + 1911, int(text[3:5]), int(text[5:7])).isoformat()
        if len(text) == 5:
            return f"{int(text[:3]) + 1911:04d}-{int(text[3:5]):02d}"
    except ValueError:
        return None
    return None


def _to_float(value) -> float | None:
    try:
        text = str(value).replace(",", "").replace("+", "").strip()
        if text in {"", "-", "--", "None"}:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def shares_to_lots(shares) -> int | None:
    """TWSE/yfinance volumes and T86 net buys are in shares (股); 1 張 = 1,000 股."""
    value = _to_float(shares)
    if value is None:
        return None
    return int(round(value / 1000.0))


def _fetch_json(url: str):
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=HTTP_TIMEOUT_SEC)
    resp.raise_for_status()
    return resp.json()


LISTING_MARKETS = frozenset({"上市", "上櫃"})
# Cold-cache callers wait for the single in-flight load (TWSE + TPEx, 10 s each).
LOAD_WAIT_TIMEOUT_SEC = 2 * HTTP_TIMEOUT_SEC + 5


class ListingTable(dict):
    """Code -> listing row, plus the markets whose source actually loaded."""

    def __init__(self, *args, markets=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.markets = frozenset(markets)

    @property
    def complete(self) -> bool:
        return LISTING_MARKETS <= self.markets


_LOAD_FAILED = object()


class _CachedTable:
    """
    In-process TTL cache around a loader; keeps serving stale data on failure.

    A result whose ``complete`` attribute is False (e.g. only one of TWSE /
    TPEx answered) is kept only for ``retry_after_sec`` and never replaces an
    older complete table. One thread runs the loader at a time: the others get
    the current data at once or, on a cold cache, wait for that single load
    (bounded by ``wait_timeout_sec``) instead of queueing behind a lock.
    """

    def __init__(
        self,
        loader,
        ttl_sec=REMOTE_TTL_SEC,
        retry_after_sec=REMOTE_RETRY_AFTER_SEC,
        wait_timeout_sec=LOAD_WAIT_TIMEOUT_SEC,
    ):
        self._loader = loader
        self._ttl = ttl_sec
        self._retry_after = retry_after_sec
        self._wait_timeout = wait_timeout_sec
        self._cond = threading.Condition()
        self._data = None
        self._fresh_until = 0.0
        self._failed_at = 0.0
        self._loading = False

    def peek(self):
        return self._data

    def get(self):
        with self._cond:
            now = time.monotonic()
            if self._data is not None and now < self._fresh_until:
                return self._data
            if self._failed_at and now - self._failed_at < self._retry_after:
                return self._data
            if self._loading:
                if self._data is None:
                    self._cond.wait_for(lambda: not self._loading, timeout=self._wait_timeout)
                return self._data
            self._loading = True
        result = _LOAD_FAILED
        try:
            result = self._loader()
        except Exception as exc:  # network / format failure must not break requests
            logger.warning("open data table load failed: %s", exc)
        finally:
            with self._cond:
                self._loading = False
                self._store(result, time.monotonic())
                self._cond.notify_all()
        return self._data

    def _store(self, result, now):
        if result is _LOAD_FAILED or not result:
            self._failed_at = now
            return
        if getattr(result, "complete", True):
            self._data = result
            self._fresh_until = now + self._ttl
            self._failed_at = 0.0
            return
        # Partial load: keep an older complete table; otherwise serve the partial
        # one briefly and retry soon instead of trusting it for the full TTL.
        if self._data is None or not getattr(self._data, "complete", True):
            self._data = result
            self._fresh_until = now + self._retry_after
        self._failed_at = now

    def reset(self, data=None):
        with self._cond:
            self._data = data
            self._fresh_until = time.monotonic() + self._ttl if data is not None else 0.0
            self._failed_at = 0.0


def _listing_rows(url, code_key, name_key, close_key, market):
    rows = {}
    for row in _fetch_json(url) or []:
        code = str(row.get(code_key) or "").strip().upper()
        if code:
            rows[code] = {
                "code": code,
                "name": str(row.get(name_key) or "").strip(),
                "market": market,
                "date": roc_to_iso(row.get("Date")),
                "close": _to_float(row.get(close_key)),
            }
    return rows


def _load_listing_table():
    """TWSE + TPEx code table; ``markets`` records which source fully loaded."""
    table = {}
    markets = set()
    sources = (
        (TWSE_LISTING_URL, "Code", "Name", "ClosingPrice", "上市"),
        (TPEX_LISTING_URL, "SecuritiesCompanyCode", "CompanyName", "Close", "上櫃"),
    )
    for url, code_key, name_key, close_key, market in sources:
        try:
            rows = _listing_rows(url, code_key, name_key, close_key, market)
        except Exception as exc:
            logger.warning("%s listing table unavailable: %s", market, exc)
            continue
        if rows:
            markets.add(market)
        for code, row in rows.items():
            table.setdefault(code, row)  # a code listed on both keeps the TWSE row
    return ListingTable(table, markets=markets)


def _load_monthly_revenue_table():
    table = {}
    for row in _fetch_json(TWSE_MONTHLY_REVENUE_URL) or []:
        code = str(row.get("公司代號") or "").strip().upper()
        if not code:
            continue
        revenue_k = _to_float(row.get("營業收入-當月營收"))
        table[code] = {
            "code": code,
            "name": str(row.get("公司名稱") or "").strip(),
            "industry": str(row.get("產業別") or "").strip(),
            "year_month": roc_to_iso(row.get("資料年月")),
            # 公開資訊觀測站月營收單位為「千元」→ 換算成億元
            "revenue_100m": round(revenue_k / 100000.0, 2) if revenue_k is not None else None,
            "mom_pct": _to_float(row.get("營業收入-上月比較增減(%)")),
            "yoy_pct": _to_float(row.get("營業收入-去年同月增減(%)")),
            "ytd_yoy_pct": _to_float(row.get("累計營業收入-前期比較增減(%)")),
            "note": str(row.get("備註") or "").strip(),
        }
    return table


LISTING_TABLE = _CachedTable(_load_listing_table)
REVENUE_TABLE = _CachedTable(_load_monthly_revenue_table)

_STOCK_DB_CACHE = {"mtime": None, "data": {}}


def load_stock_db() -> dict:
    try:
        mtime = os.path.getmtime(STOCK_DB_PATH)
    except OSError:
        return {}
    if _STOCK_DB_CACHE["mtime"] != mtime:
        try:
            with open(STOCK_DB_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            _STOCK_DB_CACHE.update({"mtime": mtime, "data": data if isinstance(data, dict) else {}})
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("stock_db.json unreadable: %s", exc)
            _STOCK_DB_CACHE.update({"mtime": mtime, "data": {}})
    return _STOCK_DB_CACHE["data"]


def lookup_listing(ticker, *, allow_fetch=True) -> dict | None:
    table = LISTING_TABLE.get() if allow_fetch else LISTING_TABLE.peek()
    if not table:
        return None
    return table.get(str(ticker or "").upper())


def listing_known(ticker) -> bool | None:
    """
    True when the code is in the TWSE/TPEx table, False only when BOTH sources
    loaded and neither lists it, None when that cannot be decided (table
    unavailable, or one market's source failed and the code may be there).
    """
    table = LISTING_TABLE.get()
    if not table:
        return None
    if str(ticker or "").upper() in table:
        return True
    return False if getattr(table, "complete", True) else None


def lookup_stock_name(ticker, *, allow_fetch=False) -> str | None:
    code = str(ticker or "").upper()
    if code in STOCK_NAMES:
        return STOCK_NAMES[code]
    entry = load_stock_db().get(code)
    if isinstance(entry, dict) and entry.get("name"):
        return str(entry["name"])
    listing = lookup_listing(code, allow_fetch=allow_fetch)
    if listing and listing.get("name"):
        return listing["name"]
    return None


def stock_profile(ticker) -> dict:
    """Local company profile from data/stock_db.json (empty dict when unknown)."""
    entry = load_stock_db().get(str(ticker or "").upper())
    if not isinstance(entry, dict):
        return {}
    products = entry.get("key_products") or []
    if not isinstance(products, list):
        products = [str(products)]
    return {
        "name": entry.get("name") or "",
        "sector": entry.get("sector") or "",
        "supply_chain_role": entry.get("supply_chain_role") or "",
        "key_products": [str(p) for p in products if p],
        "ai_connection": entry.get("ai_connection") or "",
    }


def monthly_revenue(ticker) -> dict | None:
    table = REVENUE_TABLE.get()
    if not table:
        return None
    return table.get(str(ticker or "").upper())


def format_institutional_lines(institutional: dict | None, data_date: str | None, price_date: str | None) -> str:
    """Render T86 net buys (shares) as 張 with an explicit data date."""
    if not institutional:
        return "- 三大法人：資料未提供（上櫃股或證交所尚未公布）"
    parts = []
    for key, label in (("foreign_net", "外資"), ("trust_net", "投信"), ("total_net", "三大法人合計")):
        lots = shares_to_lots(institutional.get(key))
        if lots is None:
            parts.append(f"{label} 資料未提供")
        else:
            parts.append(f"{label} {lots:+,} 張")
    line = f"- 三大法人買賣超（單位：張）：{'、'.join(parts)}"
    date_note = f"法人資料日期：{data_date}" if data_date else "法人資料日期：未標示（證交所最近一次公布）"
    if data_date and price_date and data_date != price_date:
        date_note += f"（與股價日期 {price_date} 不同，引用時必須註明「非當日」）"
    return f"{line}\n- {date_note}"
