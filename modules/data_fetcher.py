import yfinance as yf
from tavily import TavilyClient
import datetime
import requests
import numpy as np
from concurrent.futures import ThreadPoolExecutor
import json
import os
import logging

from modules.fetch_result import fetch_error, fetch_error_from_exception, fetch_ok
from modules.twse_calendar import is_twse_session
from modules.twse_tape import pct_text, strip_tags
from modules.tz import now_tpe_naive

logger = logging.getLogger(__name__)

# Realized volatility for role_scorer elasticity (consecutive sessions).
REALIZED_VOL_SESSIONS = 20
REALIZED_VOL_MIN_RETURNS = 8
DAILY_RETURN_CLIP_PCT = 10.5


def realized_vol_fields(closes, dates=None, sessions=REALIZED_VOL_SESSIONS):
    """
    Daily % returns of the last `sessions` consecutive sessions ending at the
    previous close, and their sample stdev.

    The latest session is excluded on purpose: /1 scores before today's
    snapshot exists, and regen / backtests read this stored payload, so all
    of them see the same window. `dates` (same order as closes) adds
    {date: pct} so beta vs ^TWII can be computed on matching sessions.
    Returns {} when fewer than REALIZED_VOL_MIN_RETURNS returns exist.
    """
    try:
        values = [float(c) for c in closes]
    except (TypeError, ValueError):
        return {}
    day_keys = [str(d)[:10] for d in dates] if dates is not None and len(dates) == len(values) else None
    prior = values[:-1]
    prior_days = day_keys[:-1] if day_keys else None
    start = max(0, len(prior) - (sessions + 1))
    returns = []
    by_date = {}
    for i in range(start + 1, len(prior)):
        prev, cur = prior[i - 1], prior[i]
        if not (np.isfinite(prev) and np.isfinite(cur)) or prev <= 0 or cur <= 0:
            continue
        pct = max(-DAILY_RETURN_CLIP_PCT, min(DAILY_RETURN_CLIP_PCT, (cur / prev - 1.0) * 100.0))
        returns.append(pct)
        if prior_days:
            by_date[prior_days[i]] = round(pct, 3)
    if len(returns) < REALIZED_VOL_MIN_RETURNS:
        return {}
    fields = {
        "realized_vol_20d_pct": round(float(np.std(returns, ddof=1)), 3),
        "realized_vol_20d_n": len(returns),
    }
    if by_date:
        fields["returns_20d"] = by_date
    return fields
_WEEKDAY_ZH = "一二三四五六日"


def focus_label(item) -> str:
    """'長榮' / {'id': '2603', 'name': '長榮'} → '長榮(2603)' (id disambiguates homonyms)."""
    if isinstance(item, dict):
        name = str(item.get("name") or "").strip()
        sid = str(item.get("id") or "").strip()
        if name and sid and sid != name:
            return f"{name}({sid})"
        return name or sid
    return str(item or "").strip()


def supply_chain_radar_queries(lite=True, focus_names=None):
    """Build radar search queries. Lite must not be an AMD-only pair.

    focus_names may be plain names or {"id","name"} entities; entities are
    searched as 「名稱(代號)」 so 長榮(2603) does not pull 長榮航(2618) news.
    """
    generic = [
        "台灣 AI 伺服器 供應鏈 投資 先進封裝 CoWoS 基板 HBM 液冷",
        "Taiwan semiconductor supply chain CoWoS HBM liquid cooling investment 2026",
    ]
    mega = [
        "NVIDIA Taiwan AI server supply chain advanced packaging CoWoS investment",
        "AMD Taiwan investment AI infrastructure advanced packaging supply chain",
        "Broadcom Marvell Taiwan AI ASIC supply chain CoWoS substrate",
        "OpenAI Microsoft AWS Taiwan AI server supply chain investment",
    ]
    names = [focus_label(n) for n in (focus_names or []) if focus_label(n)]
    focus_q = []
    if names:
        focus_q.append(f"{' '.join(names[:2])} 台股 法說 訂單 產能 供應鏈")

    if lite:
        queries = [generic[0]]
        queries.append(focus_q[0] if focus_q else generic[1])
        return queries
    return generic + focus_q + mega


class DataFetcher:
    # T86: short waits after a connect failure (network blip) before giving up;
    # extended by twse_http.connect_recovery_wait while the host circuit is open.
    T86_CONNECT_RECOVERY_WAITS_SEC = (5.0, 10.0)

    def __init__(self, tavily_api_key, tavily_search_limit=None):
        self.tavily_client = TavilyClient(api_key=tavily_api_key)
        self.tavily_search_count = 0
        self.tavily_search_limit = int(tavily_search_limit or os.getenv("TAVILY_DAILY_SEARCH_LIMIT", "20"))
        self.search_log = []  # audit trail of Tavily inputs (see pipeline_persist.write_news_inputs)

    @staticmethod
    def _recency_kwargs(kwargs):
        """
        Tavily only honours ``days`` for topic="news"; on the default general
        topic it is silently ignored (old analysis pages leak in). Map it to
        ``time_range``, which applies to every topic.
        """
        kwargs = dict(kwargs)
        days = kwargs.get("days")
        if days and kwargs.get("topic") != "news" and not kwargs.get("time_range"):
            kwargs.pop("days", None)
            days = int(days)
            if days <= 1:
                kwargs["time_range"] = "day"
            elif days <= 7:
                kwargs["time_range"] = "week"
            elif days <= 31:
                kwargs["time_range"] = "month"
            else:
                kwargs["time_range"] = "year"
        return kwargs

    def _log_search(self, stage, query, response, kwargs):
        """Keep an audit trail of every Tavily input (archived as news_inputs.json)."""
        try:
            results = []
            for item in (response or {}).get("results") or []:
                url = str(item.get("url") or "")
                results.append(
                    {
                        "title": str(item.get("title") or "")[:200],
                        "url": url,
                        "domain": url.split("/")[2] if url.count("/") >= 2 else "",
                        "published_date": item.get("published_date"),
                        "snippet": str(item.get("content") or "")[:200],
                    }
                )
            self.search_log.append(
                {
                    "stage": stage,
                    "query": query,
                    "params": {k: v for k, v in kwargs.items() if k in {"search_depth", "max_results", "time_range", "days", "topic"}},
                    "results": results,
                }
            )
        except Exception:
            logger.debug("search log append failed", exc_info=True)

    def _tavily_search(self, query, stage="search", **kwargs):
        if self.tavily_search_count >= self.tavily_search_limit:
            print(f"  [Tavily Guard] 已達搜尋上限 {self.tavily_search_limit}，跳過: {query}")
            self._log_search(stage, query, {"results": []}, {"skipped": "limit"})
            return {"results": []}
        
        from modules.api_retry import api_retry

        kwargs = self._recency_kwargs(kwargs)

        @api_retry
        def _do_search():
            return self.tavily_client.search(query, **kwargs)
            
        self.tavily_search_count += 1
        response = _do_search()
        self._log_search(stage, query, response, kwargs)
        return response

    def _calc_rsi(self, closes, period=14):
        """Calculate RSI (Relative Strength Index)."""
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        if len(gains) < period:
            return None
            
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])
        
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 2)

    def _taiwan_stock_id(self, symbol):
        """Extract bare Taiwan ticker id from 2330 / 2330.TW / 2330.TWO."""
        text = str(symbol or "").strip().upper()
        if not text:
            return None
        for suffix in (".TW", ".TWO"):
            if text.endswith(suffix):
                text = text[: -len(suffix)]
                break
        return text if text.isdigit() else None

    def _twse_http_get_json(self, url, timeout=30):
        """Throttled TWSE GET + JSON (shared semaphore, verified TLS first)."""
        from modules.twse_http import twse_get_json

        payload, _insecure = twse_get_json(url, timeout=timeout, retries=3)
        return payload

    def _fetch_twse_daily_closes(self, stock_id, months_back=3, as_of=None):
        """
        Fetch listed-stock daily OHLCV from TWSE STOCK_DAY (fallback when Yahoo blocks cloud IPs).
        Returns list of dicts sorted by date ascending: {date, close, volume, change, ex_rights}.

        ``change`` is None when TWSE marks the day 「X」(除權息／不比價) or the
        cell is unparsable — never a fake 0.0. Rows after ``as_of`` are dropped.
        """
        stock_id = str(stock_id).strip()
        rows = []
        seen_dates = set()
        anchor = self._as_date(as_of) or now_tpe_naive().date()

        for m in range(months_back):
            # First day of target month
            year = anchor.year
            month = anchor.month - m
            while month <= 0:
                month += 12
                year -= 1
            date_str = f"{year}{month:02d}01"
            url = (
                "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
                f"?date={date_str}&stockNo={stock_id}&response=json"
            )
            try:
                result = self._twse_http_get_json(url)
            except Exception as e:
                logger.warning("TWSE STOCK_DAY failed for %s %s: %s", stock_id, date_str, e)
                continue
            if not result or result.get("stat") != "OK":
                continue
            for row in result.get("data") or []:
                try:
                    # 日期, 成交股數, 成交金額, 開盤, 最高, 最低, 收盤, 漲跌價差, 成交筆數
                    roc_date = str(row[0]).strip()  # e.g. 115/07/21
                    parts = roc_date.replace("-", "/").split("/")
                    if len(parts) != 3:
                        continue
                    y = int(parts[0]) + 1911
                    dkey = f"{y:04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
                    if dkey in seen_dates or dkey > anchor.isoformat():
                        continue
                    close = float(str(row[6]).replace(",", "").strip())
                    volume = int(str(row[1]).replace(",", "").strip())
                    change, ex_rights = self._parse_twse_change(row[7])
                    seen_dates.add(dkey)
                    rows.append(
                        {
                            "date": dkey,
                            "close": close,
                            "volume": volume,
                            "change": change,
                            "ex_rights": ex_rights,
                        }
                    )
                except (ValueError, IndexError, TypeError):
                    continue

        rows.sort(key=lambda r: r["date"])
        return rows

    @staticmethod
    def _parse_twse_change(raw):
        """STOCK_DAY 漲跌價差: '+60.00' / '-1.5' / ' 0.00' / 'X0.00' → (change|None, ex_rights)."""
        text = str(raw or "").replace(",", "").strip()
        if not text:
            return None, False
        if text[0].upper() == "X":
            return None, True
        try:
            return float(text), False
        except ValueError:
            return None, False

    @staticmethod
    def _as_date(value):
        if value is None or value == "":
            return None
        if isinstance(value, datetime.datetime):
            return value.date()
        if isinstance(value, datetime.date):
            return value
        try:
            return datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

    def _payload_from_close_series(self, symbol, closes, volumes, prev_close=None, bar_date=None, dates=None):
        """Build standard stock payload from close/volume arrays (oldest→newest).

        ``dates`` (same order as closes, optional) feeds realized_vol_fields so
        the payload carries returns_20d keyed by session for beta vs ^TWII;
        when ``bar_date`` is not given the last date is used.

        Volume ratio: today's volume / mean of the previous 5 sessions
        (today excluded). Including today in the denominator capped the ratio
        at 5 and made the 「≥2x 爆量」 threshold actually need ~2.67x.
        """
        closes = np.asarray(closes, dtype=float)
        volumes = np.asarray(volumes, dtype=float)
        if len(closes) < 2:
            return None
        current_close = float(closes[-1])
        if prev_close is None or prev_close <= 0:
            prev_close = float(closes[-2])
        change = current_close - float(prev_close)
        pct_change = (change / float(prev_close)) * 100 if prev_close else 0.0
        ma5 = round(float(np.mean(closes[-5:])), 2) if len(closes) >= 5 else None
        ma20 = round(float(np.mean(closes[-20:])), 2) if len(closes) >= 20 else None
        rsi = self._calc_rsi(closes) if len(closes) >= 15 else None
        if rsi is not None:
            rsi = float(rsi)
        prior = volumes[-6:-1] if len(volumes) >= 2 else volumes[-1:]
        avg_vol_5d = int(np.mean(prior)) if len(prior) else int(volumes[-1])
        vol_ratio = round(float(volumes[-1] / avg_vol_5d), 2) if avg_vol_5d > 0 else 1.0
        formatted_pct = round(float(pct_change), 3) if abs(pct_change) < 0.01 else round(float(pct_change), 2)
        payload = {
            "symbol": symbol,
            "price": round(float(current_close), 2),
            "change": round(float(change), 2),
            "pct_change": formatted_pct,
            "volume": int(volumes[-1]),
            "avg_vol_5d": avg_vol_5d,
            "vol_ratio": vol_ratio,
            "vol_ratio_basis": "today_vs_prev5_avg",
            "ma5": ma5,
            "ma20": ma20,
            "rsi": rsi,
            "bar_date": str(bar_date)[:10] if bar_date else None,
        }
        if not payload["bar_date"] and dates is not None and len(dates):
            payload["bar_date"] = str(dates[-1])[:10]
        payload.update(realized_vol_fields(closes, dates))
        return payload

    @staticmethod
    def _mark_ex_rights(payload):
        """
        除權息 X day: TWSE prints 「X」 instead of 漲跌價差 and has no reference
        price in STOCK_DAY / MI_INDEX, so close − previous close would book the
        dividend as a loss. Keep the dated close (the session is known) but
        null change / pct_change; prompts say 「除權息不比價」 and the validator
        does not score it.
        """
        if payload:
            payload.update(change=None, pct_change=None, ex_rights=True, pct_basis="ex_rights_no_reference")
        return payload

    def _get_taiex_twse_fallback(self, expected_date=None):
        """^TWII from TWSE FMTQIK (daily series) + MI_INDEX (same-day index row)."""
        from modules.twse_tape import fetch_taiex_series, fetch_twse_tape

        want = self._as_date(expected_date)
        series = fetch_taiex_series(as_of=want, months_back=3)
        if want:
            series = [r for r in series if r["date"] <= want.isoformat()]
            if not series or series[-1]["date"] != want.isoformat():
                tape = fetch_twse_tape(want.isoformat(), quiet=True)
                index_row = (tape or {}).get("index")
                if index_row and index_row.get("close"):
                    series.append(
                        {
                            "date": want.isoformat(),
                            "close": index_row["close"],
                            "change": index_row.get("change"),
                            "volume": index_row.get("volume") or 0,
                        }
                    )
        if len(series) < 2:
            # FMTQIK failed but the date-checked MI_INDEX row exists: its 漲跌點數
            # gives the previous close, which is all pct_change needs.
            only = series[-1] if series else None
            if want and only and only["date"] == want.isoformat() and only.get("change") is not None:
                prev_close = float(only["close"]) - float(only["change"])
                if prev_close > 0:
                    payload = self._payload_from_close_series(
                        "^TWII", [prev_close, only["close"]], [0, 0], prev_close=prev_close, bar_date=only["date"]
                    )
                    if payload:
                        payload.update(
                            volume=only.get("volume"),
                            avg_vol_5d=None,
                            vol_ratio=None,
                            vol_ratio_basis="unavailable_single_bar",
                        )
                        print(
                            f"  ✅ TWSE 加權指數（MI_INDEX 指數列）: {payload['price']} "
                            f"({payload['pct_change']:+.2f}%) @ {only['date']}"
                        )
                        return payload
            print("[Warning] TWSE 加權指數備援也無法取得日線")
            return None
        last = series[-1]
        prev_close = None
        if last.get("change") is not None:
            prev_close = last["close"] - float(last["change"])
        payload = self._payload_from_close_series(
            "^TWII",
            [r["close"] for r in series],
            [r.get("volume") or 0 for r in series],
            prev_close=prev_close if prev_close and prev_close > 0 else None,
            bar_date=last["date"],
            dates=[r["date"] for r in series],
        )
        if payload:
            print(f"  ✅ TWSE 加權指數備援成功: {payload['price']} ({payload['pct_change']:+.2f}%) @ {last['date']}")
        return payload

    def _get_stock_data_twse_fallback(self, symbol, expected_date=None):
        """TWSE listed-stock fallback when Yahoo/yfinance returns empty (common on cloud IPs)."""
        if str(symbol or "").strip().upper() in {"^TWII", "TWII", "IX0001"}:
            return self._get_taiex_twse_fallback(expected_date)
        stock_id = self._taiwan_stock_id(symbol)
        if not stock_id:
            return None
        # OTC (.TWO) is not on TWSE STOCK_DAY; skip
        if str(symbol).upper().endswith(".TWO"):
            return None
        rows = self._fetch_twse_daily_closes(stock_id, months_back=3, as_of=expected_date)
        if len(rows) < 2:
            print(f"[Warning] TWSE 備援也無法取得 {stock_id} 日線")
            return None
        last = rows[-1]
        closes = [r["close"] for r in rows]
        volumes = [r["volume"] for r in rows]
        prev_close = None
        if last.get("change") is not None and last["close"]:
            prev_close = last["close"] - float(last["change"])
            if prev_close <= 0:
                prev_close = None
        payload = self._payload_from_close_series(
            symbol if "." in str(symbol) else f"{stock_id}.TW",
            closes,
            volumes,
            prev_close=prev_close,
            bar_date=last["date"],
            dates=[r["date"] for r in rows],
        )
        if not payload:
            return None
        if last.get("ex_rights"):
            # 「X」不比價: no reference price here; keep the dated close only.
            print(f"  [Info] {stock_id} {last['date']} 為除權息／不比價（X）：TWSE 備援只輸出收盤 {payload['price']}，不輸出漲跌幅")
            return self._mark_ex_rights(payload)
        print(f"  ✅ TWSE 備援成功: {stock_id} 收 {payload['price']} ({payload['pct_change']:+.2f}%) @ {last['date']}")
        return payload

    def _tape_bar(self, symbol, want_iso):
        """
        Same-session close/change for ``symbol`` from the date-checked MI_INDEX
        tape (cached once per session by twse_tape). None for 上櫃 (.TWO), a
        missing row, or when the tape for ``want_iso`` is unavailable.
        """
        from modules.twse_tape import fetch_twse_tape

        try:
            tape = fetch_twse_tape(want_iso, quiet=True)
        except Exception:
            logger.exception("tape lookup failed for %s", symbol)
            return None
        if not tape:
            return None
        text = str(symbol or "").strip().upper()
        if text in {"^TWII", "TWII", "IX0001"}:
            row = tape.get("index") or {}
            if not row.get("close"):
                return None
            return {"close": row["close"], "change": row.get("change"), "volume": row.get("volume"), "ex_rights": False}
        if text.endswith(".TWO"):
            return None
        sid = self._taiwan_stock_id(symbol)
        for row in tape.get("rows") or []:
            if row.get("id") == sid:
                return {
                    "close": row.get("close_price"),
                    "change": row.get("change"),
                    "volume": row.get("volume"),
                    "ex_rights": bool(row.get("ex_rights")),
                }
        return None

    def _patch_last_bar_from_tape(self, symbol, hist, bar_dates, want_iso):
        """
        Yahoo history ends on the previous session: append the reported
        session's close from the cached tape instead of 3 STOCK_DAY / FMTQIK
        requests per symbol. None when there is a gap or the tape has no row.
        On a 除權息 X day the dated close is kept with change / pct_change None
        (see _mark_ex_rights) so the session is not reported as missing.
        """
        from modules.twse_calendar import prev_session

        if hist is None or not bar_dates or not want_iso:
            return None
        if bar_dates[-1] != prev_session(want_iso).isoformat():
            return None
        bar = self._tape_bar(symbol, want_iso)
        if not bar or not bar.get("close"):
            return None
        ex_rights = bool(bar.get("ex_rights"))
        if not ex_rights and bar.get("change") is None:
            return None
        close = float(bar["close"])
        prev_close = None
        if not ex_rights:
            prev_close = close - float(bar["change"])
            if prev_close <= 0:
                return None
        closes = list(hist["Close"].values) + [close]
        volumes = list(hist["Volume"].values) + [bar.get("volume") or 0]
        payload = self._payload_from_close_series(
            symbol, closes, volumes, prev_close=prev_close, bar_date=want_iso, dates=list(bar_dates) + [want_iso]
        )
        if not payload:
            return None
        if str(symbol).strip().upper() in {"^TWII", "TWII", "IX0001"}:
            # TWSE 成交股數 and Yahoo's ^TWII volume are not the same unit/scope.
            payload.update(volume=bar.get("volume"), avg_vol_5d=None, vol_ratio=None, vol_ratio_basis="unavailable_mixed_sources")
        if ex_rights:
            print(f"  ✅ {symbol} 以 TWSE 全日收盤表補上 {want_iso} K 棒: {payload['price']}（除權息不比價，不輸出漲跌幅）")
            return self._mark_ex_rights(payload)
        print(f"  ✅ {symbol} 以 TWSE 全日收盤表補上 {want_iso} K 棒: {payload['price']} ({payload['pct_change']:+.2f}%)")
        return payload

    @staticmethod
    def _hist_bar_dates(hist):
        out = []
        for idx in hist.index:
            try:
                out.append(idx.date().isoformat())
            except AttributeError:
                out.append(str(idx)[:10])
        return out

    def get_stock_data(self, symbol, expected_date=None):
        """
        Fetches stock data with technical indicators.
        Primary: yfinance. Fallback for Taiwan listed stocks: TWSE STOCK_DAY;
        ^TWII falls back to TWSE FMTQIK / MI_INDEX.

        Every payload carries ``bar_date`` (the K-bar's trade date). When
        ``expected_date`` (YYYY-MM-DD, the TWSE session being reported) is set:
        newer Yahoo bars are trimmed to that session, and an older/missing
        Yahoo bar triggers the TWSE fallback for exactly that session.
        """
        want = self._as_date(expected_date)
        want_iso = want.isoformat() if want else None
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="60d")

            # Secondary Yahoo path: download() sometimes works when history() is empty
            if hist is None or hist.empty or len(hist) < 2:
                try:
                    dl = yf.download(
                        symbol,
                        period="60d",
                        progress=False,
                        auto_adjust=True,
                        threads=False,
                    )
                    if dl is not None and not dl.empty:
                        hist = dl
                        if getattr(hist.columns, "nlevels", 1) > 1:
                            hist.columns = hist.columns.get_level_values(0)
                except Exception as dl_err:
                    logger.warning("yfinance download fallback failed for %s: %s", symbol, dl_err)

            yf_payload = None
            bar_dates = []
            if hist is not None and not hist.empty and len(hist) >= 2:
                bar_dates = self._hist_bar_dates(hist)
                if want_iso and bar_dates and bar_dates[-1] > want_iso:
                    keep = [d <= want_iso for d in bar_dates]
                    hist = hist[keep]
                    bar_dates = [d for d in bar_dates if d <= want_iso]
                if hist is not None and len(hist) >= 2:
                    closes = hist["Close"].values
                    # Avoid ticker.info on cloud — extra Yahoo call often 404/blocked and slows path
                    prev_close = float(closes[-2])
                    volumes = hist["Volume"].values
                    yf_payload = self._payload_from_close_series(
                        symbol, closes, volumes, prev_close=prev_close, bar_date=bar_dates[-1], dates=bar_dates
                    )
            if yf_payload and (not want_iso or yf_payload.get("bar_date") == want_iso):
                return fetch_ok(
                    yf_payload,
                    source="yfinance",
                    meta={"symbol": symbol, "kind": "stock", "bar_date": yf_payload.get("bar_date")},
                )

            if yf_payload:
                print(f"[Warning] {symbol} Yahoo K 棒日期 {yf_payload.get('bar_date')} ≠ {want_iso}，改試 TWSE 同日資料")
                patched = self._patch_last_bar_from_tape(symbol, hist, bar_dates, want_iso)
                if patched:
                    return fetch_ok(
                        patched,
                        source="yfinance+twse_tape",
                        meta={"symbol": symbol, "kind": "stock", "fallback": "twse_tape_patch", "bar_date": want_iso},
                    )
            else:
                print(f"[Warning] yfinance 無資料，嘗試 TWSE 備援: {symbol}")
            twse_payload = self._get_stock_data_twse_fallback(symbol, expected_date=want_iso)
            if twse_payload and (not want_iso or twse_payload.get("bar_date") == want_iso or not yf_payload):
                return fetch_ok(
                    twse_payload,
                    source="twse",
                    meta={
                        "symbol": symbol,
                        "kind": "stock",
                        "fallback": "twse_stock_day",
                        "bar_date": twse_payload.get("bar_date"),
                    },
                )
            if yf_payload:
                # Keep the dated Yahoo bar; data_quality_gate flags the mismatch.
                return fetch_ok(
                    yf_payload,
                    source="yfinance",
                    meta={"symbol": symbol, "kind": "stock", "bar_date": yf_payload.get("bar_date"), "bar_date_mismatch": True},
                )

            print(f"[Warning] 無法取得 {symbol} 的數據")
            return fetch_error(
                f"無法取得 {symbol} 的數據",
                code="EMPTY_DATA",
                source="yfinance+twse",
                data=None,
                meta={"symbol": symbol, "kind": "stock"},
            )
        except Exception as e:
            logger.exception("取得 %s 數據失敗", symbol)
            print(f"[Error] 取得 {symbol} 數據失敗: {e}")
            # Last chance: TWSE even after yfinance exception
            try:
                twse_payload = self._get_stock_data_twse_fallback(symbol, expected_date=want_iso)
                if twse_payload:
                    # Same contract as the non-exception path: the payload keeps
                    # its own bar_date (a previous-session STOCK_DAY row stays
                    # dated so the gate and drop_off_session_bars can see it).
                    bar_date = twse_payload.get("bar_date")
                    meta = {
                        "symbol": symbol,
                        "kind": "stock",
                        "fallback": "twse_after_error",
                        "bar_date": bar_date,
                    }
                    if want_iso and bar_date != want_iso:
                        meta["bar_date_mismatch"] = True
                    return fetch_ok(twse_payload, source="twse", meta=meta)
            except Exception:
                logger.exception("TWSE fallback after yfinance error failed for %s", symbol)
            return fetch_error_from_exception(
                e,
                source="yfinance",
                data=None,
                meta={"symbol": symbol, "kind": "stock"},
                default_message=f"取得 {symbol} 數據失敗: {e}",
            )

    def get_commodity_data(self):
        """
        Fetches Gold, Oil, Silver prices via yfinance.
        Returns a dict of commodity data.

        Futures trade nearly 24h: at 16:10 Taipei the last bar is a live Asian
        session quote, not a settlement. Each row carries ``bar_date`` and
        ``quote_kind="latest_quote"`` so downstream text says 報價, not 收盤.
        """
        commodities = {
            "GC=F": "黃金 (Gold)",
            "CL=F": "原油 (Crude Oil)",
            "SI=F": "白銀 (Silver)",
        }
        results = {}
        for symbol, name in commodities.items():
            try:
                ticker = yf.Ticker(symbol)
                # Just fetch the last 2 days to get the exact 1-day change
                hist = ticker.history(period="2d")
                if hist.empty or len(hist) < 2:
                    continue
                current = float(hist['Close'].iloc[-1])
                prev = float(hist['Close'].iloc[-2])
                change = current - prev
                pct = (change / prev) * 100
                bar_dates = self._hist_bar_dates(hist)
                results[symbol] = {
                    "name": name,
                    "price": round(current, 2),
                    "change": round(change, 2),
                    "pct_change": round(pct, 2),
                    "bar_date": bar_dates[-1] if bar_dates else None,
                    "change_basis": "1d",
                    "quote_kind": "latest_quote",
                }
                print(f"  ✅ {name}: ${results[symbol]['price']} ({results[symbol]['pct_change']:+.2f}%，1日，報價 @ {results[symbol]['bar_date']})")
            except Exception as e:
                logger.exception("商品行情抓取失敗: %s", name)
                print(f"  ⚠️  {name}: 取得失敗 ({e})")
        if results:
            return fetch_ok(results, source="yfinance", meta={"kind": "commodity"})
        return fetch_error(
            "無法取得商品行情數據",
            code="EMPTY_DATA",
            source="yfinance",
            data={},
            meta={"kind": "commodity"},
        )

    GLOBAL_OVERNIGHT_SYMBOLS = {
        "TSM": ("台積電ADR", "us_cash"),
        "^SOX": ("費半", "us_cash"),
        "^VIX": ("VIX", "us_cash"),
        "NQ=F": ("那斯達克期貨", "24h"),
        "TWD=X": ("美元兌台幣", "24h"),
    }

    def get_global_overnight(self, symbols=None):
        """
        Local ground truth for 台積電 ADR / 費半 / VIX / 那斯達克期貨 / 美元兌台幣.

        Each row: {label, price, change, pct_change, bar_date, quote_kind}.
        ``quote_kind="close"`` only for a finished US cash session (bar date
        before today's New York date); otherwise ``latest_quote``. Symbols that
        fail are simply absent — prompts must then not cite them.
        """
        try:
            from zoneinfo import ZoneInfo

            ny_today = datetime.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
        except Exception:
            ny_today = None
        wanted = symbols or self.GLOBAL_OVERNIGHT_SYMBOLS
        results = {}
        for symbol in wanted:
            label, session = self.GLOBAL_OVERNIGHT_SYMBOLS.get(symbol, (symbol, "us_cash"))
            try:
                hist = yf.Ticker(symbol).history(period="5d")
                if hist is None or hist.empty or len(hist) < 2:
                    continue
                current = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2])
                if not current or not prev:
                    continue
                bar_dates = self._hist_bar_dates(hist)
                bar_date = bar_dates[-1] if bar_dates else None
                finished = session == "us_cash" and bar_date and ny_today and bar_date < ny_today
                results[symbol] = {
                    "label": label,
                    "price": round(current, 2 if current >= 10 else 4),
                    "change": round(current - prev, 4),
                    "pct_change": round((current - prev) / prev * 100, 2),
                    "bar_date": bar_date,
                    "quote_kind": "close" if finished else "latest_quote",
                }
            except Exception as e:
                logger.warning("海外行情抓取失敗 %s: %s", symbol, e)
        if results:
            preview = "、".join(f"{r['label']} {r['pct_change']:+.2f}%@{r['bar_date']}" for r in results.values())
            print(f"  ✅ 海外／匯率（本機）：{preview}")
            return fetch_ok(results, source="yfinance", meta={"kind": "global_overnight"})
        return fetch_error(
            "無法取得海外／匯率行情",
            code="EMPTY_DATA",
            source="yfinance",
            data={},
            meta={"kind": "global_overnight"},
        )

    def _session_dates_back(self, max_days, start=None):
        """Recent TWSE session dates (newest first) using the holiday calendar + Asia/Taipei clock."""
        base = start or now_tpe_naive()
        out = []
        for days_back in range(0, max_days):
            check_date = base - datetime.timedelta(days=days_back)
            if is_twse_session(check_date):
                out.append(check_date)
        return out

    def get_top_volume_stocks(self, top_n=20):
        """
        Fetches top N stocks by trading volume (成交股數) from TWSE MI_INDEX20.
        Falls back to previous trading day if today's data is not yet available.

        Note: this ranks by *shares*, which favours ETFs / low-priced names.
        Each row is enriched with 成交金額 (``trade_value`` / ``trade_value_yi``)
        and ``value_rank`` from the same-day tape when available; use
        ``get_top_value_stocks`` for the 成交值 ranking.
        """
        from modules.twse_http import twse_get

        print(f"\n🏆 正在獲取今日成交股數前 {top_n} 名股票（MI_INDEX20，依股數）...")

        for check_date in self._session_dates_back(7):
            date_str = check_date.strftime("%Y%m%d")
            url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX20?date={date_str}&response=json"

            try:
                resp = twse_get(url, timeout=30, retries=3, backoff=2)
                result = resp.json()
                if result.get("stat") == "OK" and "data" in result:
                    resp_date = str(result.get("date") or "").strip()
                    if resp_date and resp_date != date_str:
                        print(f"  [Warning] MI_INDEX20 回應日期 {resp_date} ≠ 請求日 {date_str}，略過")
                        continue
                    stocks = []
                    for row in result["data"]:
                        try:
                            # TWSE MI_INDEX20 field order:
                            # 排名, 代號, 名稱, 成交股數, 成交筆數, 開盤, 最高, 最低, 收盤, 漲跌(+/-), 漲跌價差, 買價, 賣價
                            stock_id = str(row[1]).strip()
                            stock_name = str(row[2]).strip()
                            volume = int(str(row[3]).replace(",", "").strip())
                            try:
                                close_price = float(str(row[8]).replace(",", "").strip())
                            except (ValueError, IndexError):
                                close_price = 0.0
                            ex_rights = False
                            try:
                                # 「X」(除權息／不比價) has no comparable 漲跌價差; never
                                # read it as an unsigned (= positive) move.
                                ex_rights = "X" in strip_tags(row[9]).upper()
                                dir_str = str(row[9]).strip().lower()
                                change_dir = "+" if "+" in dir_str or "red" in dir_str or "漲" in dir_str else ("-" if "-" in dir_str or "green" in dir_str or "跌" in dir_str else "")
                                change_val = float(str(row[10]).replace(",", "").strip())
                                signed_change = -change_val if change_dir == "-" else change_val
                                
                                prev_close = close_price - signed_change
                                if ex_rights:
                                    pct_change = None
                                elif prev_close > 0:
                                    pct_change = round((signed_change / prev_close) * 100, 2)
                                else:
                                    pct_change = 0.0
                            except (ValueError, IndexError, ZeroDivisionError):
                                pct_change = None if ex_rights else 0.0

                            if stock_id and stock_name:
                                row_out = {
                                    "rank": len(stocks) + 1,
                                    "id": stock_id,
                                    "name": stock_name,
                                    "volume": volume,
                                    "close_price": close_price,
                                    "pct_change": pct_change,
                                }
                                if ex_rights:
                                    row_out["ex_rights"] = True
                                stocks.append(row_out)
                        except (ValueError, IndexError):
                            continue

                    if stocks:
                        data_date = check_date.strftime("%Y-%m-%d")
                        self._attach_trade_value(stocks, data_date)
                        print(f"  ✅ 成交股數前 {len(stocks)} 名已獲取（資料日期：{data_date}）")
                        for s in stocks[:5]:
                            value = f"，成交值 {s['trade_value_yi']:.1f}億" if s.get("trade_value_yi") is not None else ""
                            print(f"     {s['rank']}. {s['id']} {s['name']}: {s['volume']:,} 股{value} ({pct_text(s)})")
                        return fetch_ok(
                            stocks[:top_n],
                            source="twse",
                            meta={
                                "kind": "volume_ranking",
                                "top_n": top_n,
                                "data_date": data_date,
                                "ranking_basis": "shares",
                                "tls_unverified": bool(getattr(resp, "tls_unverified", False)),
                            },
                        )

            except Exception as e:
                logger.exception("成交量排名獲取失敗")
                print(f"  [Warning] 成交量排名獲取失敗: {e}")
                continue

        print("  [Warning] 無法獲取成交量前20名數據，分析將以外資買超為主")
        return fetch_error(
            "無法獲取成交量前20名數據",
            code="EMPTY_DATA",
            source="twse",
            data=[],
            meta={"kind": "volume_ranking", "top_n": top_n, "ranking_basis": "shares"},
        )

    @staticmethod
    def _is_etf_code(stock_id):
        """ETF / ETN codes start with 00 (0050, 00632R …)."""
        return str(stock_id or "").startswith("00")

    @classmethod
    def _rank_tape_by_value(cls, tape_rows):
        """
        Tape rows with 成交金額 split into (stocks, etfs), each sorted by
        trade_value descending. Single source for 成交值名次 so the 成交股數
        table's 「成交值第N名」 and the 成交值前20 table never disagree at the
        top-20 cutoff (ETFs used to shift every stock's rank in one of them).
        """
        rows = [r for r in tape_rows or [] if r.get("trade_value")]
        rows.sort(key=lambda r: r["trade_value"], reverse=True)
        stocks = [r for r in rows if not cls._is_etf_code(r.get("id"))]
        etfs = [r for r in rows if cls._is_etf_code(r.get("id"))]
        return stocks, etfs

    @classmethod
    def _attach_trade_value(cls, stocks, data_date):
        """
        Add 成交金額 + 成交值名次 from the verified same-day tape (best effort).

        ``value_rank`` is the rank among listed stocks excluding ETFs — the same
        basis as get_top_value_stocks; ETF rows get ``value_rank_etf`` instead.
        """
        try:
            from modules.twse_tape import fetch_twse_tape

            tape = fetch_twse_tape(data_date, quiet=True)
        except Exception:
            logger.exception("trade value enrichment failed")
            return
        if not tape:
            return
        ranked_stocks, ranked_etfs = cls._rank_tape_by_value(tape.get("rows"))
        stock_rank = {r["id"]: i for i, r in enumerate(ranked_stocks, start=1)}
        etf_rank = {r["id"]: i for i, r in enumerate(ranked_etfs, start=1)}
        by_id = {r["id"]: r for r in ranked_stocks + ranked_etfs}
        for stock in stocks:
            row = by_id.get(str(stock.get("id") or ""))
            if not row:
                continue
            stock["trade_value"] = row["trade_value"]
            stock["trade_value_yi"] = round(row["trade_value"] / 1e8, 2)
            stock["value_rank"] = stock_rank.get(row["id"])
            stock["value_rank_basis"] = "stocks_ex_etf"
            if row["id"] in etf_rank:
                stock["value_rank_etf"] = etf_rank[row["id"]]

    def get_top_value_stocks(self, data_date, top_n=20, etf_n=5):
        """
        成交值（成交金額）前 N 名 from the verified same-day tape.

        ETFs / ETNs (codes starting with 00) are listed separately so the main
        ranking reflects where money actually went. Empty lists on failure.
        """
        from modules.twse_tape import fetch_twse_tape

        tape = fetch_twse_tape(data_date, quiet=True) if data_date else None
        if not tape:
            return fetch_error(
                "成交值排行無同日全日收盤表",
                code="EMPTY_DATA",
                source="twse",
                data={"stocks": [], "etfs": [], "data_date": data_date},
                meta={"kind": "value_ranking"},
            )
        ranked_stocks, ranked_etfs = self._rank_tape_by_value(tape.get("rows"))

        def _top(rows, n):
            out = []
            for i, row in enumerate(rows[: max(0, int(n))], start=1):
                out.append({**row, "rank": i, "trade_value_yi": round(row["trade_value"] / 1e8, 2)})
            return out

        stocks, etfs = _top(ranked_stocks, top_n), _top(ranked_etfs, etf_n)
        return fetch_ok(
            {"stocks": stocks, "etfs": etfs, "data_date": tape.get("date")},
            source=tape.get("source") or "twse",
            meta={"kind": "value_ranking", "data_date": tape.get("date"), "top_n": top_n},
        )

    def get_macro_events(self, days=5):
        """
        Fetches upcoming global macro economic events (e.g., CPI, Fed meetings, Non-farm payrolls).
        """
        try:
            print("\n📅 正在獲取本週全球重要財經日曆與總體經濟事件...")
            query = "本週全球重要財經日曆 總經數據 發布時間表 (CPI, Fed, 聯準會, 非農, 財報)"
            response = self._tavily_search(query, stage="macro", search_depth="basic", max_results=3, days=days)
            results = response.get('results', [])
            if results:
                return fetch_ok(results, source="tavily", meta={"kind": "macro_events", "days": days})
            return fetch_error(
                "本週全球重要財經日曆與總體經濟事件無結果",
                code="EMPTY_DATA",
                source="tavily",
                data=[],
                meta={"kind": "macro_events", "days": days},
            )
        except Exception as e:
            logger.exception("Tavily 總經事件搜尋失敗")
            print(f"[Error] Tavily 總經事件搜尋失敗: {e}")
            return fetch_error_from_exception(
                e,
                source="tavily",
                data=[],
                meta={"kind": "macro_events", "days": days},
                default_message=f"Tavily 總經事件搜尋失敗: {e}",
            )

    @staticmethod
    def select_week_positions(bar_dates, today=None, trading_days=5, calendar_week=True, session_checker=None):
        """
        Positions (into bar_dates, ascending ISO strings) of the bars that form
        "the week" plus a description dict.

        calendar_week=True: the latest *finished* ISO week — the week of the last
        bar if it ended on Friday, today is already past that week's Friday
        (weekend after a holiday-shortened week), or ``session_checker`` (a
        ``day -> bool`` exchange calendar, e.g. is_twse_session) says no session
        remains in that week; else the week before. A Tuesday run therefore
        reports last Mon–Fri, not a rolling 5 bars that straddle two weeks.
        calendar_week=False keeps the legacy rolling ``trading_days`` window.
        """
        days = [datetime.datetime.strptime(d[:10], "%Y-%m-%d").date() for d in bar_dates]
        if not days:
            return [], {}
        if not calendar_week:
            pos = list(range(max(0, len(days) - trading_days), len(days)))
            return pos, {"week_basis": f"rolling_{trading_days}_bars", "week_complete": None}
        today = today or now_tpe_naive().date()
        weeks = [d.isocalendar()[:2] for d in days]
        last = days[-1]
        complete = last.weekday() >= 4
        if not complete:
            week_friday = last + datetime.timedelta(days=4 - last.weekday())
            # Friday holiday (TW 10/9, US Good Friday …): a weekend run must
            # still report the short week that just ended, not the one before.
            complete = today > week_friday
            if not complete and session_checker is not None and today > last:
                remaining = [last + datetime.timedelta(days=n) for n in range(1, (week_friday - last).days + 1)]
                try:
                    complete = not any(session_checker(d) for d in remaining)
                except Exception:
                    complete = False
        target = weeks[-1]
        if not complete:
            earlier = [w for w in weeks if w < weeks[-1]]
            if earlier:
                target = earlier[-1]
                complete = True
        pos = [i for i, w in enumerate(weeks) if w == target]
        return pos, {"week_basis": "calendar_week", "week_complete": complete, "iso_week": f"{target[0]}-W{target[1]:02d}"}

    def get_weekly_stock_data(self, symbol, trading_days=5, as_of=None, calendar_week=True):
        """
        Fetches a week's worth of daily stock data for weekly summary.
        Returns weekly change, daily closing series (each bar dated with its
        real weekday), intra-week high/low, plus current technical indicators.

        The week is a calendar (ISO) week by default; ``week_start``/``week_end``
        and ``daily_series[*].weekday`` say exactly which sessions are covered.
        """
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="60d")

            if hist.empty or len(hist) < 2:
                print(f"[Warning] 無法取得 {symbol} 的週數據")
                return fetch_error(
                    f"無法取得 {symbol} 的週數據",
                    code="EMPTY_DATA",
                    source="yfinance",
                    data=None,
                    meta={"symbol": symbol, "kind": "weekly_stock"},
                )

            bar_dates = self._hist_bar_dates(hist)
            is_tw = str(symbol).upper().endswith((".TW", ".TWO")) or str(symbol).upper() == "^TWII"
            positions, week_meta = self.select_week_positions(
                bar_dates,
                today=self._as_date(as_of),
                trading_days=trading_days,
                calendar_week=calendar_week,
                session_checker=is_twse_session if is_tw else None,
            )
            if not positions or positions[0] < 1:
                print(f"[Warning] {symbol} 週數據不足（缺少週前收盤）")
                return fetch_error(
                    f"{symbol} 週數據不足",
                    code="EMPTY_DATA",
                    source="yfinance",
                    data=None,
                    meta={"symbol": symbol, "kind": "weekly_stock"},
                )

            all_closes = hist["Close"].values
            closes = all_closes[: positions[-1] + 1]
            week_data = hist.iloc[positions[0]: positions[-1] + 1]
            week_closes = week_data["Close"].values
            pre_week_close = float(all_closes[positions[0] - 1])
            week_close = float(week_closes[-1])
            week_high = float(np.max(week_data["High"].values))
            week_low = float(np.min(week_data["Low"].values))
            week_change = week_close - pre_week_close
            week_pct_change = (week_change / pre_week_close) * 100 if pre_week_close else 0.0
            avg_volume = int(np.mean(week_data["Volume"].values))

            # Technical indicators as of the week's last bar
            ma5 = round(float(np.mean(closes[-5:])), 2) if len(closes) >= 5 else None
            ma20 = round(float(np.mean(closes[-20:])), 2) if len(closes) >= 20 else None
            rsi = self._calc_rsi(closes) if len(closes) >= 15 else None

            daily_series = []
            for pos in positions:
                day = datetime.datetime.strptime(bar_dates[pos], "%Y-%m-%d").date()
                day_close = round(float(all_closes[pos]), 2)
                prev_close = float(all_closes[pos - 1])
                day_change = round(day_close - prev_close, 2)
                day_pct = round(day_change / prev_close * 100, 2) if prev_close else 0
                daily_series.append({
                    "date": f"{day:%m/%d}(週{_WEEKDAY_ZH[day.weekday()]})",
                    "iso_date": day.isoformat(),
                    "weekday": f"週{_WEEKDAY_ZH[day.weekday()]}",
                    "close": day_close,
                    "change": day_change,
                    "pct_change": day_pct,
                    "volume": int(hist["Volume"].iloc[pos]),
                })

            payload = {
                "symbol": symbol,
                "week_close": round(week_close, 2),
                "week_change": round(float(week_change), 2),
                "week_pct_change": round(float(week_pct_change), 2),
                "week_high": round(week_high, 2),
                "week_low": round(week_low, 2),
                "avg_volume": avg_volume,
                "ma5": ma5,
                "ma20": ma20,
                "rsi": rsi,
                "daily_series": daily_series,
                "week_start": daily_series[0]["iso_date"],
                "week_end": daily_series[-1]["iso_date"],
                "trading_days_in_week": len(daily_series),
                "pre_week_close": round(pre_week_close, 2),
                **week_meta,
            }
            return fetch_ok(payload, source="yfinance", meta={"symbol": symbol, "kind": "weekly_stock"})
        except Exception as e:
            logger.exception("取得 %s 週數據失敗", symbol)
            print(f"[Error] 取得 {symbol} 週數據失敗: {e}")
            return fetch_error_from_exception(
                e,
                source="yfinance",
                data=None,
                meta={"symbol": symbol, "kind": "weekly_stock"},
                default_message=f"取得 {symbol} 週數據失敗: {e}",
            )

    def _fetch_institutional_payload(self, check_date, day_attempts=1, label=""):
        import time
        import json as _json

        from modules.twse_http import connect_recovery_wait, is_connect_failure, twse_get

        date_str = check_date.strftime("%Y%m%d")
        url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALLBUT0999&response=json"
        recovery_waits = list(self.T86_CONNECT_RECOVERY_WAITS_SEC)
        day_attempt = -1
        while day_attempt < day_attempts - 1:
            day_attempt += 1
            try:
                resp = twse_get(url, timeout=30, retries=3, backoff=2)
            except requests.exceptions.RequestException as exc:
                if is_connect_failure(exc):
                    # DNS / refused / connect timeout: the network is down, not
                    # "T86 not published yet". A short outage gets a couple of
                    # short recovery waits (they do not use up the readiness
                    # attempts); a persistent one raises within ~30 s instead of
                    # burning the 15s…105s readiness waits.
                    if not recovery_waits:
                        raise
                    wait_secs = connect_recovery_wait(url, recovery_waits.pop(0))
                    print(f"  [Warning] TWSE T86 連線失敗（{exc.__class__.__name__}，網路中斷？），{wait_secs:.0f} 秒後再試一次...")
                    time.sleep(wait_secs)
                    day_attempt -= 1
                    continue
                if day_attempt >= day_attempts - 1:
                    raise
                print(f"  [Warning] TWSE T86 連線失敗（{exc.__class__.__name__}），稍後重試...")
                resp = None
            self._last_tls_unverified = bool(getattr(resp, "tls_unverified", False)) if resp is not None else False

            # Empty body / HTML / "no data yet" are common right after market close.
            text = (resp.text or "").strip() if resp is not None else ""
            result = None
            if text:
                try:
                    result = resp.json()
                except ValueError:
                    try:
                        result = _json.loads(text)
                    except Exception:
                        result = None

            if isinstance(result, dict) and result.get("stat") == "OK" and result.get("data"):
                resp_date = str(result.get("date") or "").strip()
                if resp_date and resp_date != date_str:
                    print(f"  [Warning] T86 回應日期 {resp_date} ≠ 請求日 {date_str}，不採用")
                    return None, None
                return result, check_date.strftime("%Y-%m-%d")

            if day_attempt < day_attempts - 1:
                # 15s, 30s, 45s… — TWSE institutional table often lags volume by minutes.
                wait_secs = 15 * (day_attempt + 1)
                hint = label or check_date.strftime("%Y-%m-%d")
                stat = (result or {}).get("stat") if isinstance(result, dict) else "empty/non-json"
                print(
                    f"  [Info] {hint} 法人數據尚未就緒（{stat}），"
                    f"{wait_secs} 秒後重試 ({day_attempt + 1}/{day_attempts - 1})..."
                )
                time.sleep(wait_secs)
        return None, None

    def _yf_same_day_close(self, sid, data_date):
        """yfinance close for exactly ``data_date`` (.TW then .TWO); None if the bar is another day."""
        from modules.institutional_ranking import yf_same_day_close

        return yf_same_day_close(sid, data_date)

    def _twse_same_day_close(self, sid, data_date):
        """TWSE STOCK_DAY close for exactly ``data_date`` (None when that row is missing)."""
        want = str(data_date or "")[:10]
        if not want:
            return None
        rows = self._fetch_twse_daily_closes(sid, months_back=1, as_of=want)
        if rows and rows[-1].get("date") == want and rows[-1].get("close"):
            return float(rows[-1]["close"])
        return None

    def _price_institutional_rows(self, all_stocks, data_date_str, top_n=10):
        """
        Same-session pricing for the 億元 ranking.

        Order: verified full-market tape (MI_INDEX / STOCK_DAY_ALL, date-checked)
        → local daily_closes / market_snapshots for exactly that date → yfinance
        bars dated that day → TWSE STOCK_DAY for that session, only for names
        that could still reach the top-``top_n`` board. Stale prices are never
        reused. Returns (priced_rows, pricing_meta).
        """
        from modules.institutional_ranking import fetch_twse_day_all, price_rows_same_session

        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "analytics.db")
        try:
            tape = fetch_twse_day_all(data_date_str or "")
        except Exception:  # pricing must degrade, never abort the institutional fetch
            logger.exception("full-market tape fetch failed")
            tape = []
        tape = [r for r in tape if str(r.get("price_date") or "")[:10] == data_date_str]
        if tape:
            try:
                from modules.analytics_store import AnalyticsStore

                saved = AnalyticsStore(db_path).record_daily_closes(data_date_str, tape)
                print(f"  ✅ 全日收盤 {saved} 檔已入庫（{data_date_str}，只供估價／回測，不進口播）")
            except Exception:
                logger.exception("daily_closes persist failed")
                print(f"  ✅ 全日收盤 {len(tape)} 檔已取到（入庫略過）")

        priced_rows, meta = price_rows_same_session(
            all_stocks,
            data_date_str,
            tape_rows=tape,
            db_path=db_path,
            lookup_one=lambda sid: self._yf_same_day_close(sid, data_date_str),
            top_n=top_n,
            refill_one=lambda sid: self._twse_same_day_close(sid, data_date_str),
        )
        if meta.get("lookup_requested"):
            print(
                f"  補價 {meta['lookup_requested']} 檔（張數前段＋高價股，只收 {data_date_str} 當日 K 棒）"
                f"→ 成功 {meta.get('lookup_priced', 0)} 檔"
            )
        meta["yfinance_priced"] = meta.get("lookup_priced", 0)
        coverage = meta.get("coverage_top30") or {}
        if coverage.get("ratio", 1.0) < 1.0:
            names = "、".join(f"{u['id']} {u['name']}" for u in coverage.get("unpriced", [])[:5])
            print(
                f"  ⚠️  張數前 {coverage.get('checked')} 名有 {coverage.get('checked', 0) - coverage.get('priced', 0)} 檔"
                f"無同日股價，未列入金額榜：{names}"
            )
        if meta.get("refill_requested"):
            print(
                f"  TWSE 個股日成交補價 {meta['refill_requested']} 檔（可能上榜的未定價股）"
                f"→ 成功 {meta.get('refill_priced', 0)} 檔"
            )
        material = meta.get("material_unpriced") or []
        if material:
            names = "、".join(f"{u['id']} {u.get('name') or ''}".strip() for u in material[:5])
            print(f"  ❌ 可能上榜的個股仍無同日股價（金額榜不完整）：{names}")
        return priced_rows, meta

    def get_institutional_data(self, top_n=10, target_date=None):
        """
        Fetches institutional investor buy/sell data from TWSE (上市, T86 ALLBUT0999).
        Dynamically returns the top N foreign investor net buy AND top N net sell stocks.
        Falls back across TWSE sessions (holiday calendar) up to 14 days.

        If target_date (YYYY-MM-DD) is set, only fetch that session (no older fallback).
        """
        print("正在獲取三大法人買賣超數據...")

        data = None
        data_date_str = None
        network_down = False
        self._last_tls_unverified = False

        try:
            if target_date:
                check_date = datetime.datetime.strptime(str(target_date), "%Y-%m-%d")
                data, data_date_str = self._fetch_institutional_payload(
                    check_date,
                    day_attempts=8,
                    label=f"{target_date}",
                )
            else:
                # Try today first (if it is a session), then earlier sessions.
                today_iso = now_tpe_naive().date().isoformat()
                for check_date in self._session_dates_back(15):
                    is_today = check_date.date().isoformat() == today_iso
                    # Today: more readiness waits (volume often arrives before T86).
                    attempts = 8 if is_today else 1
                    try:
                        data, data_date_str = self._fetch_institutional_payload(
                            check_date,
                            day_attempts=attempts,
                            label="今日" if is_today else "",
                        )
                        if data is not None:
                            if not is_today:
                                print(f"  [Info] 使用 {data_date_str} 的法人數據（最近有資料的交易日）")
                            break
                        if is_today:
                            print("  [Info] 今日數據尚未發布（已用盡就緒重試），往前搜尋中...")
                    except Exception as e:
                        from modules.twse_http import is_connect_failure

                        if is_connect_failure(e):
                            # Network down: every older session fails the same way.
                            print(f"  [Warning] TWSE 連線失敗（{e.__class__.__name__}），停止往前搜尋法人數據")
                            network_down = True
                            break
                        if is_today:
                            print(f"  [Info] 取得今日法人數據發生錯誤 ({e})，往前搜尋中...")
                        continue

            if data is None:
                # Optional same-day FinMind backup (never invent older-day mix with today's volume).
                from modules.finmind_client import fetch_foreign_institutional_top, finmind_fallback_enabled

                if finmind_fallback_enabled():
                    # target_date path: only that session. Open path: only calendar today.
                    fallback_date = (
                        str(target_date)[:10]
                        if target_date
                        else now_tpe_naive().strftime("%Y-%m-%d")
                    )
                    fm = fetch_foreign_institutional_top(fallback_date, top_n=top_n)
                    if (
                        fm
                        and (fm.get("top_buy") or fm.get("top_sell"))
                        and fm.get("data_date") == fallback_date
                    ):
                        print(f"  ✅ 使用 FinMind 備援法人資料（{fm.get('data_date')}）")
                        return fetch_ok(
                            {
                                "top_buy": fm.get("top_buy") or [],
                                "top_sell": fm.get("top_sell") or [],
                                "trust_conflicts": fm.get("trust_conflicts") or [],
                                "data_date": fm.get("data_date"),
                                "source": "finmind",
                                "market_scope": fm.get("market_scope") or "TWSE_TPEx",
                                "pricing": fm.get("pricing") or {},
                            },
                            source="finmind",
                            meta={"kind": "institutional", "top_n": top_n, "fallback": True},
                        )

                if network_down:
                    print("  [Warning] 無法取得三大法人數據（TWSE 連線失敗，網路可能中斷）")
                    return fetch_error(
                        "無法取得三大法人數據（TWSE 連線失敗，網路可能中斷）",
                        code="NETWORK",
                        source="twse",
                        data={"top_buy": [], "top_sell": [], "data_date": None},
                        meta={"kind": "institutional", "top_n": top_n},
                    )
                print("  [Warning] 無法取得三大法人數據（已搜尋近 14 天）")
                return fetch_error(
                    "無法取得三大法人數據（已搜尋近 14 天）",
                    code="EMPTY_DATA",
                    source="twse",
                    data={"top_buy": [], "top_sell": [], "data_date": None},
                    meta={"kind": "institutional", "top_n": top_n},
                )

            from modules.institutional_ranking import (
                MARKET_SCOPE,
                T86SchemaError,
                find_trust_conflicts,
                parse_t86_rows,
                rank_by_amount,
            )

            try:
                all_stocks = parse_t86_rows(data)
            except T86SchemaError as schema_err:
                print(f"  [Error] {schema_err}；停止使用此份法人資料以免數字錯位。")
                return fetch_error(
                    str(schema_err),
                    code="DATA_FORMAT",
                    source="twse",
                    data={"top_buy": [], "top_sell": [], "data_date": None},
                    meta={"kind": "institutional", "top_n": top_n, "fields": data.get("fields")},
                )

            # Amount rank needs same-session prices beyond the top share-count names.
            print("  正在計算法人買賣超金額（同日全日收盤表優先，舊價一律不用）...")
            all_stocks, pricing = self._price_institutional_rows(all_stocks, data_date_str, top_n=top_n)
            top_buy, top_sell = rank_by_amount(all_stocks, top_n=top_n)
            if all_stocks and not top_buy and not top_sell:
                print(
                    "  ❌ T86 已取得，但沒有任何同日股價可估算金額（全日收盤表與 yfinance 都失敗）；"
                    "不使用舊價估算。"
                )
                return fetch_error(
                    f"{data_date_str} 法人資料無同日股價可估算金額",
                    code="UNPRICED",
                    source="twse",
                    data={"top_buy": [], "top_sell": [], "data_date": data_date_str, "pricing": pricing},
                    meta={"kind": "institutional", "top_n": top_n},
                )
            trust_conflicts = find_trust_conflicts(all_stocks)

            print(f"  ✅ 外資買超前 {top_n} 名（上市，依同日收盤估算金額）:")
            for s in top_buy[:5]:
                amt_str = f"{s['est_amount']:+.1f}億" if s.get('est_amount') else ""
                print(f"     {s['id']} {s['name']}: {s['foreign_net']:+,} 股 ({amt_str})")
            print(f"  ✅ 外資賣超前 {top_n} 名（上市，依同日收盤估算金額）:")
            for s in top_sell[:5]:
                amt_str = f"{s['est_amount']:+.1f}億" if s.get('est_amount') else ""
                print(f"     {s['id']} {s['name']}: {s['foreign_net']:+,} 股 ({amt_str})")
            if trust_conflicts:
                print("  ⚠ 土洋對作（外資／投信反向，備查）:")
                for row in trust_conflicts[:3]:
                    print(f"     {row['id']} {row['name']}: {row['note']} 外資{row['foreign_amount']:+.1f}億 投信{row['trust_amount']:+.1f}億")

            return fetch_ok(
                {
                    "top_buy": top_buy,
                    "top_sell": top_sell,
                    "trust_conflicts": trust_conflicts,
                    "data_date": data_date_str,
                    "source": "twse",
                    "market_scope": MARKET_SCOPE,
                    "pricing": pricing,
                },
                source="twse",
                meta={
                    "kind": "institutional",
                    "top_n": top_n,
                    "priced": pricing.get("priced"),
                    "tls_unverified": bool(getattr(self, "_last_tls_unverified", False)),
                },
            )

        except Exception as e:
            logger.exception("取得三大法人數據失敗")
            print(f"[Error] 取得三大法人數據失敗: {e}")
            # Last-chance FinMind if explicitly enabled and a target date is known.
            try:
                from modules.finmind_client import fetch_foreign_institutional_top, finmind_fallback_enabled

                if finmind_fallback_enabled() and target_date:
                    fm = fetch_foreign_institutional_top(str(target_date)[:10], top_n=top_n)
                    if fm and (fm.get("top_buy") or fm.get("top_sell")):
                        print(f"  ✅ 例外後使用 FinMind 備援（{fm.get('data_date')}）")
                        return fetch_ok(
                            {
                                "top_buy": fm.get("top_buy") or [],
                                "top_sell": fm.get("top_sell") or [],
                                "trust_conflicts": fm.get("trust_conflicts") or [],
                                "data_date": fm.get("data_date"),
                                "source": "finmind",
                                "market_scope": fm.get("market_scope") or "TWSE_TPEx",
                                "pricing": fm.get("pricing") or {},
                            },
                            source="finmind",
                            meta={"kind": "institutional", "top_n": top_n, "fallback": True},
                        )
            except Exception:
                logger.exception("FinMind fallback also failed")
            return fetch_error_from_exception(
                e,
                source="twse",
                data={"top_buy": [], "top_sell": [], "data_date": None},
                meta={"kind": "institutional", "top_n": top_n},
                default_message=f"取得三大法人數據失敗: {e}",
            )

    def get_single_stock_institutional_data(self, ticker):
        """
        Fetches TWSE institutional data and ONLY parses the requested ticker.
        This is extremely fast because it skips hitting Yahoo Finance 40 times.
        """
        from modules.institutional_ranking import T86SchemaError, parse_t86_rows
        from modules.twse_http import twse_get

        try:
            for check_date in self._session_dates_back(15):
                date_str = check_date.strftime("%Y%m%d")
                url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALLBUT0999&response=json"

                try:
                    resp = twse_get(url, timeout=15, retries=3, backoff=1)
                    result = resp.json()
                    if result.get("stat") == "OK" and "data" in result:
                        try:
                            rows = parse_t86_rows(result)
                        except T86SchemaError as schema_err:
                            return fetch_error(
                                str(schema_err),
                                code="DATA_FORMAT",
                                source="twse",
                                data=None,
                                meta={"kind": "single_institutional", "ticker": ticker},
                            )
                        for row in rows:
                            if row["id"] == ticker:
                                payload = {k: row.get(k) for k in (
                                    "foreign_net", "trust_net", "total_net",
                                    "foreign_dealer_net", "foreign_total_net", "dealer_net",
                                )}
                                payload["data_date"] = check_date.strftime("%Y-%m-%d")
                                return fetch_ok(
                                    payload,
                                    source="twse",
                                    meta={
                                        "kind": "single_institutional",
                                        "ticker": ticker,
                                        "data_date": payload["data_date"],
                                        "tls_unverified": bool(getattr(resp, "tls_unverified", False)),
                                    },
                                )

                        # If reached here, TWSE data was fetched but ticker not found (e.g., it's OTC or no institutional action)
                        return fetch_error(
                            f"未找到 {ticker} 的法人資料",
                            code="NOT_FOUND",
                            source="twse",
                            data=None,
                            meta={"kind": "single_institutional", "ticker": ticker},
                        )
                except Exception:
                    logger.debug("single-stock T86 attempt failed for %s", date_str, exc_info=True)
                    continue
            return fetch_error(
                f"無法取得 {ticker} 的法人資料",
                code="EMPTY_DATA",
                source="twse",
                data=None,
                meta={"kind": "single_institutional", "ticker": ticker},
            )
        except Exception as e:
            logger.exception("快速取得單一個股法人數據失敗")
            print(f"[Error] 快速取得單一個股法人數據失敗: {e}")
            return fetch_error_from_exception(
                e,
                source="twse",
                data=None,
                meta={"kind": "single_institutional", "ticker": ticker},
                default_message=f"快速取得單一個股法人數據失敗: {e}",
            )

    def get_tech_catalyst_events(self, days=14):
        """
        Actively searches for major upcoming tech and macro catalyst events using Tavily.
        Targets: NVIDIA GTC, Apple WWDC, Fed FOMC, TSMC Investor Day, major earnings.
        Returns a list of catalyst dicts with event name, date hint, and supply chain relevance.
        """
        print("\n🔭 正在搜尋近期重大科技催化劑事件（NVIDIA GTC / Fed / 財報季等）...")
        queries = [
            "NVIDIA GTC 2026 conference date GPU AI announcement",
            "Apple WWDC Microsoft Build 2026 date",
            "Fed FOMC meeting 2026 interest rate decision date",
            "TSMC 台積電 法說會 investor conference 2026",
            "輝達 NVIDIA 財報 earnings date 2026",
        ]
        
        events = []
        seen_titles = set()
        for query in queries:
            try:
                response = self._tavily_search(query, stage="catalyst", search_depth="basic", max_results=2, days=days)
                for item in response.get("results", []):
                    title = item.get("title", "")
                    if title and title not in seen_titles:
                        seen_titles.add(title)
                        events.append({
                            "event": title,
                            "url": item.get("url", ""),
                            "snippet": item.get("content", "")[:200],
                        })
            except Exception as e:
                logger.exception("重大科技催化劑事件搜尋失敗")
                print(f"  [Warning] 催化劑事件搜尋失敗: {e}")
                continue
        
        print(f"  ✅ 找到 {len(events)} 個重大催化劑事件")
        return fetch_ok(events, source="tavily", meta={"kind": "tech_catalyst_events", "days": days})

    def get_supply_chain_catalyst_events(self, days=7, lite=True, cache_path=None, focus_names=None):
        """
        Actively searches for supply-chain-level AI/semiconductor events.

        Catches structural news that may not appear on today's institutional
        rankings. Lite mode uses a generic Taiwan-supply-chain query plus today's
        protagonist names — not an AMD-only pair.
        """
        cache_path = cache_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data",
            "supply_chain_event_cache.json",
        )
        today = now_tpe_naive().strftime("%Y-%m-%d")
        focus_entities = [n for n in (focus_names or []) if focus_label(n)]
        focus_names = [focus_label(n) for n in focus_entities]
        try:
            if os.path.exists(cache_path):
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                if (
                    cached.get("date") == today
                    and cached.get("lite") == bool(lite)
                    and list(cached.get("focus_names") or []) == focus_names
                ):
                    print("\n🧭 使用今日已快取的主動消息面事件雷達")
                    return fetch_ok(
                        cached.get("events", []),
                        source="cache",
                        meta={"kind": "supply_chain_events", "days": days, "lite": lite},
                    )
        except Exception as cache_err:
            logger.warning("supply chain radar cache read failed (re-searching): %s", cache_err)

        print("\n🧭 正在搜尋低成本主動消息面事件雷達（AI供應鏈 / 台廠投資 / 先進封裝）...")
        queries = supply_chain_radar_queries(lite=lite, focus_names=focus_entities)

        events = []
        seen = set()
        for query in queries:
            try:
                response = self._tavily_search(
                    query,
                    stage="radar",
                    search_depth="basic" if lite else "advanced",
                    max_results=2 if lite else 3,
                    days=days,
                )
                for item in response.get("results", []):
                    title = item.get("title", "")
                    url = item.get("url", "")
                    key = url or title
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    events.append({
                        "event": title,
                        "url": url,
                        "snippet": item.get("content", "")[:300],
                        "query": query,
                    })
            except Exception as e:
                logger.exception("供應鏈事件搜尋失敗")
                print(f"  [Warning] 供應鏈事件搜尋失敗: {e}")
                continue

        print(f"  ✅ 找到 {len(events)} 個供應鏈事件候選")
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "date": today,
                        "lite": bool(lite),
                        "focus_names": focus_names,
                        "events": events,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as cache_err:
            logger.warning("supply chain radar cache write failed: %s", cache_err)
        return fetch_ok(
            events,
            source="tavily",
            meta={"kind": "supply_chain_events", "days": days, "lite": lite},
        )

    @staticmethod
    def _broad_news_query(query):
        """Fallback keeps the entity (「長榮 2603」), never collapses to 「Taiwan stock news」."""
        tokens = [t for t in str(query or "").split() if t]
        if len(tokens) >= 2 and tokens[1].isdigit():
            return " ".join(tokens[:2])
        if len(tokens) >= 3:
            return " ".join(tokens[:2])
        return None

    def get_news(self, query, days=2):
        """
        Fetches news using Tavily with self-healing retry.

        Uses topic="news" so ``days`` is honoured (on Tavily's general topic it
        is ignored and old analysis pages leak in).
        """
        try:
            print(f"搜尋新聞: {query}")
            response = self._tavily_search(
                query, stage="news", topic="news", search_depth="advanced", max_results=5, days=days
            )

            broad_query = self._broad_news_query(query)
            if not response.get('results') and broad_query and broad_query != query:
                print(f"  [Info] 無結果，改用實體關鍵詞: {broad_query}")
                response = self._tavily_search(
                    broad_query, stage="news_fallback", topic="news", search_depth="advanced", max_results=5, days=days
                )
            
            results = response.get('results', [])
            if results:
                return fetch_ok(results, source="tavily", meta={"kind": "news", "query": query, "days": days})
            return fetch_error(
                f"搜尋新聞無結果: {query}",
                code="EMPTY_DATA",
                source="tavily",
                data=[],
                meta={"kind": "news", "query": query, "days": days},
            )
            
        except Exception as e:
            logger.exception("Tavily 搜尋失敗")
            print(f"[Error] Tavily 搜尋失敗: {e}")
            return fetch_error_from_exception(
                e,
                source="tavily",
                data=[],
                meta={"kind": "news", "query": query, "days": days},
                default_message=f"Tavily 搜尋失敗: {e}",
            )

    @staticmethod
    def quarterly_fundamentals(fin):
        """
        yfinance quarterly_financials (columns newest → oldest) → margins + revenue growth.

        Revenue YoY compares the latest quarter with the *same quarter a year
        earlier* (column 4, checked to be 365±45 days apart). The previous code
        compared column 0 with column 3 — three quarters back — and labelled it
        年增率. Without a year-ago column, only an explicitly labelled QoQ is given.
        """
        out = {}
        gross_profit_row = None
        revenue_row = None
        for idx in fin.index:
            if "Gross Profit" in str(idx):
                gross_profit_row = fin.loc[idx]
            if "Total Revenue" in str(idx):
                revenue_row = fin.loc[idx]
        if revenue_row is None:
            return out

        def _num(row, col):
            try:
                value = float(row[col])
            except (TypeError, ValueError, KeyError):
                return None
            return None if value != value else value  # NaN → None

        def _day(col):
            try:
                return col.date() if hasattr(col, "date") else datetime.datetime.strptime(str(col)[:10], "%Y-%m-%d").date()
            except (TypeError, ValueError):
                return None

        cols = list(fin.columns[:5])
        quarters = cols[:4]
        if gross_profit_row is not None:
            margins, revenues = [], []
            for q in quarters:
                gp, rev = _num(gross_profit_row, q), _num(revenue_row, q)
                margins.append(round(gp / rev * 100, 1) if gp is not None and rev else None)
                revenues.append(round(rev / 1e8, 1) if rev is not None else None)
            out["gross_margin_4q"] = margins
            out["revenue_4q_bn"] = revenues
            out["quarter_labels"] = [str(q)[:7] for q in quarters]
            valid_margins = [m for m in margins if m is not None]
            if len(valid_margins) >= 2:
                out["margin_trend"] = "改善" if valid_margins[0] > valid_margins[-1] else "走弱"
                out["latest_gross_margin"] = valid_margins[0]

        rev0 = _num(revenue_row, cols[0]) if cols else None
        if len(cols) >= 5:
            rev4 = _num(revenue_row, cols[4])
            d0, d4 = _day(cols[0]), _day(cols[4])
            if rev0 and rev4 and d0 and d4 and 320 <= (d0 - d4).days <= 410:
                out["revenue_yoy_pct"] = round((rev0 - rev4) / rev4 * 100, 1)
                out["revenue_yoy_basis"] = f"{str(cols[0])[:7]} vs {str(cols[4])[:7]}"
        if "revenue_yoy_pct" not in out and len(cols) >= 2:
            rev1 = _num(revenue_row, cols[1])
            if rev0 and rev1:
                out["revenue_qoq_pct"] = round((rev0 - rev1) / rev1 * 100, 1)
                out["revenue_qoq_basis"] = f"{str(cols[0])[:7]} vs {str(cols[1])[:7]}"
        return out

    def get_deep_dive_data(self, stock_id, stock_name, institutional_data=None):
        """
        [深度押注] 每日針對「今日焦點股」抓取完整基本面 + 技術面 + 訂單消息，
        供 AI 做出有時間預測的大膽判斷。

        Args:
            stock_id: 股票代號，e.g. "2344"
            stock_name: 股票名稱，e.g. "華邦電"
            institutional_data: 今日法人數據 dict（用來計算連續買超天數）

        Returns:
            dict with keys:
              - fundamental: 近4季毛利率、營收年增率
              - volume_trend: 近20日成交量趨勢分析
              - consecutive_fii_buy: 連續外資買超天數估算
              - recent_news: 近期訂單/法說/毛利相關新聞摘要 (list)
              - summary_for_ai: 整合成文字段落，直接餵給 AI
        """
        print(f"\n🔬 [深度押注] 正在深挖 {stock_id} {stock_name} 的完整資料...")
        symbol = f"{stock_id}.TW"
        result = {
            "stock_id": stock_id,
            "stock_name": stock_name,
            "fundamental": {},
            "volume_trend": {},
            "consecutive_fii_buy": None,
            "recent_news": [],
            "rejected_news": [],
            "summary_for_ai": ""
        }

        # ── 1. 基本面：近4季毛利率 & 營收年增率 ──
        try:
            from modules.market_extras import fetch_monthly_revenue

            monthly = fetch_monthly_revenue(stock_id)
            if monthly:
                result["fundamental"]["monthly_revenue"] = monthly
                print(f"  ✅ 月營收：{monthly['month']} 年增 {monthly['yoy_pct']:+.1f}%（MOPS／證交所開放資料）")
        except Exception as e:
            logger.warning("月營收抓取失敗: %s", e)
        try:
            ticker = yf.Ticker(symbol)
            fin = ticker.quarterly_financials
            if fin is not None and not fin.empty:
                result["fundamental"].update(self.quarterly_fundamentals(fin))
                margins = result["fundamental"].get("gross_margin_4q") or []
                valid = [m for m in margins if m is not None]
                print(f"  ✅ 基本面：毛利率近4季 {margins}，最新 {valid[0] if valid else 'N/A'}%")
        except Exception as e:
            logger.exception("深度押注基本面抓取失敗")
            print(f"  ⚠️  基本面抓取失敗: {e}")

        # ── 2. 技術面：近20日成交量趨勢 ──
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="60d")
            if not hist.empty and len(hist) >= 20:
                volumes = hist['Volume'].values
                closes = hist['Close'].values
                vol_20 = volumes[-20:]
                vol_5 = volumes[-5:]
                avg_20d = int(np.mean(vol_20))
                avg_5d = int(np.mean(vol_5))
                today_vol = int(volumes[-1])
                vol_trend = "放量" if avg_5d > avg_20d * 1.3 else ("縮量" if avg_5d < avg_20d * 0.7 else "量平")

                # 連續上漲/下跌天數
                direction_streak = 1
                for i in range(-2, -min(11, len(closes)), -1):
                    if closes[i] > closes[i-1]:
                        if closes[-1] >= closes[-2]:
                            direction_streak += 1
                        else:
                            break
                    elif closes[i] < closes[i-1]:
                        if closes[-1] <= closes[-2]:
                            direction_streak += 1
                        else:
                            break
                    else:
                        break

                result["volume_trend"] = {
                    "today_vol": today_vol,
                    "avg_5d_vol": avg_5d,
                    "avg_20d_vol": avg_20d,
                    "vol_vs_20d_avg": round(today_vol / avg_20d, 2) if avg_20d > 0 else None,
                    "trend": vol_trend,
                    "direction_streak_days": direction_streak,
                }
                print(f"  ✅ 成交量趨勢：{vol_trend}（今日 {today_vol:,} 股，20日均 {avg_20d:,} 股）")
        except Exception as e:
            logger.exception("深度押注成交量趨勢分析失敗")
            print(f"  ⚠️  成交量趨勢分析失敗: {e}")

        # ── 3. 連續外資買超天數（估算） ──
        try:
            if institutional_data:
                inst_payload = institutional_data.get("data") if isinstance(institutional_data, dict) and "data" in institutional_data else institutional_data
                top_buy_ids = {s["id"] for s in (inst_payload or {}).get("top_buy", [])}
                if stock_id in top_buy_ids:
                    result["consecutive_fii_buy"] = "今日買超（連續天數需對照歷史紀錄）"
                else:
                    result["consecutive_fii_buy"] = "今日未進外資買超榜"
        except Exception as fii_err:
            logger.warning("deep dive foreign-buy flag skipped: %s", fii_err)

        # ── 4. 近期訂單/法說/產業新聞（2-3 則精準搜尋）──
        try:
            queries = [
                f"{stock_name} {stock_id} 毛利率 訂單 展望 2026",
                f"{stock_name} {stock_id} 法說會 客戶 產能利用率",
                f"Taiwan {stock_id} {stock_name} revenue margin order 2026",
            ]
            from modules.entity_match import screen_homonym_news

            entity = {"id": str(stock_id or ""), "name": str(stock_name or "")}
            news_items = []
            for q in queries:
                try:
                    resp = self._tavily_search(q, stage="deep_dive", search_depth="basic", max_results=2, days=30)
                    # 同名異公司: a 長榮航 article must not enter 長榮's 深度數據包.
                    kept, dropped = screen_homonym_news(resp.get("results", []), entity)
                    for item in dropped:
                        result["rejected_news"].append(
                            {
                                "title": item.get("title", ""),
                                "snippet": str(item.get("content") or "")[:200],
                                "url": item.get("url", ""),
                                "reason": item.get("reason"),
                                "query": q,
                            }
                        )
                    for item in kept:
                        title = item.get("title", "")
                        snippet = item.get("content", "")[:200]
                        if title and title not in [n["title"] for n in news_items]:
                            news_items.append({"title": title, "snippet": snippet, "url": item.get("url", "")})
                except Exception:
                    logger.exception("深度押注新聞搜尋失敗")
                    continue
            result["recent_news"] = news_items[:5]
            print(f"  ✅ 近期新聞：找到 {len(result['recent_news'])} 則產業/訂單相關報導")
            if result["rejected_news"]:
                print(f"  🚫 排除 {len(result['rejected_news'])} 則同名異公司報導（非 {stock_name} {stock_id}）")
        except Exception as e:
            logger.exception("深度押注新聞搜尋失敗")
            print(f"  ⚠️  新聞搜尋失敗: {e}")

        # ── 5. 組裝給 AI 的文字摘要 ──
        lines = [f"【{stock_id} {stock_name} 深度數據包】"]

        fund = result.get("fundamental", {})
        if fund.get("latest_gross_margin"):
            lines.append(f"📊 最新毛利率：{fund['latest_gross_margin']}%（趨勢：{fund.get('margin_trend', '未知')}）")
            if "gross_margin_4q" in fund:
                labels = fund.get("quarter_labels", [])
                margins = fund["gross_margin_4q"]
                pairs = [f"{l}:{m}%" for l, m in zip(labels, margins) if m is not None]
                lines.append(f"   近4季毛利率：{' → '.join(pairs)}")
        monthly = fund.get("monthly_revenue") or {}
        if monthly.get("yoy_pct") is not None:
            yoy = monthly["yoy_pct"]
            lines.append(
                f"📈 {monthly['month']} 月營收年增率：{yoy:+.1f}%（vs 去年同月，{'成長' if yoy > 0 else '衰退'}；"
                f"MOPS 月營收）"
            )
        if fund.get("revenue_yoy_pct") is not None:
            yoy = fund["revenue_yoy_pct"]
            lines.append(
                f"📈 季營收年增率（{fund.get('revenue_yoy_basis')}，近一季 vs 去年同季）：{yoy:+.1f}%"
            )
        elif fund.get("revenue_qoq_pct") is not None:
            lines.append(
                f"📈 季營收季增率（{fund.get('revenue_qoq_basis')}，近一季 vs 前一季，非年增）：{fund['revenue_qoq_pct']:+.1f}%"
            )

        vt = result.get("volume_trend", {})
        if vt.get("trend"):
            lines.append(f"📉 成交量趨勢：{vt['trend']}（今日量 {vt.get('vol_vs_20d_avg', 'N/A')}x 20日均量）")

        if result.get("consecutive_fii_buy"):
            lines.append(f"🏦 外資動態：{result['consecutive_fii_buy']}")

        if result["recent_news"]:
            lines.append("📰 近期產業新聞摘要：")
            for n in result["recent_news"][:3]:
                lines.append(f"   - {n['title']}：{n['snippet'][:80]}...")

        result["summary_for_ai"] = "\n".join(lines)
        print(f"  ✅ 深度數據包組裝完成，共 {len(lines)} 行資訊")
        return fetch_ok(result, source="mixed", meta={"kind": "deep_dive", "stock_id": stock_id, "stock_name": stock_name})
