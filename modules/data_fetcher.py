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

logger = logging.getLogger(__name__)

class DataFetcher:
    def __init__(self, tavily_api_key, tavily_search_limit=None):
        self.tavily_client = TavilyClient(api_key=tavily_api_key)
        self.tavily_search_count = 0
        self.tavily_search_limit = int(tavily_search_limit or os.getenv("TAVILY_DAILY_SEARCH_LIMIT", "20"))

    def _tavily_search(self, query, **kwargs):
        if self.tavily_search_count >= self.tavily_search_limit:
            print(f"  [Tavily Guard] 已達搜尋上限 {self.tavily_search_limit}，跳過: {query}")
            return {"results": []}
        
        from modules.api_retry import api_retry
        
        @api_retry
        def _do_search():
            return self.tavily_client.search(query, **kwargs)
            
        self.tavily_search_count += 1
        return _do_search()

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
        """TWSE GET with UA + SSL fallback (same pattern as volume ranking)."""
        import time

        last_err = None
        for attempt in range(3):
            try:
                try:
                    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
                except requests.exceptions.SSLError:
                    resp = requests.get(
                        url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout, verify=False
                    )
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                last_err = e
                if attempt < 2:
                    time.sleep(1.2)
        if last_err:
            raise last_err
        return None

    def _fetch_twse_daily_closes(self, stock_id, months_back=3):
        """
        Fetch listed-stock daily OHLCV from TWSE STOCK_DAY (fallback when Yahoo blocks cloud IPs).
        Returns list of dicts sorted by date ascending: {date, close, volume, change}.
        """
        stock_id = str(stock_id).strip()
        rows = []
        seen_dates = set()
        today = datetime.datetime.now()

        for m in range(months_back):
            # First day of target month
            year = today.year
            month = today.month - m
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
                    if dkey in seen_dates:
                        continue
                    close = float(str(row[6]).replace(",", "").strip())
                    volume = int(str(row[1]).replace(",", "").strip())
                    change_raw = str(row[7]).replace(",", "").strip()
                    # change may be "+60.00" / "X0.00" / " 60.00"
                    change = 0.0
                    if change_raw and change_raw[0] in "+-" and change_raw[1:].replace(".", "", 1).isdigit():
                        change = float(change_raw)
                    elif change_raw.replace(".", "", 1).isdigit():
                        change = float(change_raw)
                    seen_dates.add(dkey)
                    rows.append(
                        {
                            "date": dkey,
                            "close": close,
                            "volume": volume,
                            "change": change,
                        }
                    )
                except (ValueError, IndexError, TypeError):
                    continue

        rows.sort(key=lambda r: r["date"])
        return rows

    def _payload_from_close_series(self, symbol, closes, volumes, prev_close=None):
        """Build standard stock payload from close/volume arrays (oldest→newest)."""
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
        avg_vol_5d = int(np.mean(volumes[-5:])) if len(volumes) >= 5 else int(volumes[-1])
        vol_ratio = round(float(volumes[-1] / avg_vol_5d), 2) if avg_vol_5d > 0 else 1.0
        formatted_pct = round(float(pct_change), 3) if abs(pct_change) < 0.01 else round(float(pct_change), 2)
        return {
            "symbol": symbol,
            "price": round(float(current_close), 2),
            "change": round(float(change), 2),
            "pct_change": formatted_pct,
            "volume": int(volumes[-1]),
            "avg_vol_5d": avg_vol_5d,
            "vol_ratio": vol_ratio,
            "ma5": ma5,
            "ma20": ma20,
            "rsi": rsi,
        }

    def _get_stock_data_twse_fallback(self, symbol):
        """TWSE listed-stock fallback when Yahoo/yfinance returns empty (common on cloud IPs)."""
        stock_id = self._taiwan_stock_id(symbol)
        if not stock_id:
            return None
        # OTC (.TWO) is not on TWSE STOCK_DAY; skip
        if str(symbol).upper().endswith(".TWO"):
            return None
        rows = self._fetch_twse_daily_closes(stock_id, months_back=4)
        if len(rows) < 2:
            print(f"[Warning] TWSE 備援也無法取得 {stock_id} 日線")
            return None
        closes = [r["close"] for r in rows]
        volumes = [r["volume"] for r in rows]
        # Prefer TWSE signed change when present for last day
        last_change = rows[-1].get("change")
        prev_close = None
        if last_change is not None and rows[-1]["close"]:
            prev_close = rows[-1]["close"] - float(last_change)
            if prev_close <= 0:
                prev_close = None
        payload = self._payload_from_close_series(
            symbol if "." in str(symbol) else f"{stock_id}.TW",
            closes,
            volumes,
            prev_close=prev_close,
        )
        if not payload:
            return None
        print(f"  ✅ TWSE 備援成功: {stock_id} 收 {payload['price']} ({payload['pct_change']:+.2f}%)")
        return payload

    def get_stock_data(self, symbol):
        """
        Fetches stock data with technical indicators.
        Primary: yfinance. Fallback for Taiwan listed stocks: TWSE STOCK_DAY
        (Yahoo often blocks Render / datacenter IPs → empty history).
        """
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

            if hist is not None and not hist.empty and len(hist) >= 2:
                closes = hist["Close"].values
                # Avoid ticker.info on cloud — extra Yahoo call often 404/blocked and slows path
                prev_close = float(closes[-2])
                volumes = hist["Volume"].values
                payload = self._payload_from_close_series(symbol, closes, volumes, prev_close=prev_close)
                if payload:
                    return fetch_ok(payload, source="yfinance", meta={"symbol": symbol, "kind": "stock"})

            print(f"[Warning] yfinance 無資料，嘗試 TWSE 備援: {symbol}")
            twse_payload = self._get_stock_data_twse_fallback(symbol)
            if twse_payload:
                return fetch_ok(
                    twse_payload,
                    source="twse",
                    meta={"symbol": symbol, "kind": "stock", "fallback": "twse_stock_day"},
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
                twse_payload = self._get_stock_data_twse_fallback(symbol)
                if twse_payload:
                    return fetch_ok(
                        twse_payload,
                        source="twse",
                        meta={"symbol": symbol, "kind": "stock", "fallback": "twse_after_error"},
                    )
            except Exception:
                pass
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
                results[symbol] = {
                    "name": name,
                    "price": round(current, 2),
                    "change": round(change, 2),
                    "pct_change": round(pct, 2),
                }
                print(f"  ✅ {name}: ${results[symbol]['price']} ({results[symbol]['pct_change']:+.2f}%)")
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

    def get_top_volume_stocks(self, top_n=20):
        """
        Fetches top N stocks by trading volume from TWSE MI_INDEX20.
        This is the TRUE 'market vote' — what investors actually traded the most today.
        Falls back to previous trading day if today's data is not yet available.
        """
        import time
        print(f"\n🏆 正在獲取今日成交量前 {top_n} 名股票...")

        for days_back in range(0, 5):
            check_date = datetime.datetime.now() - datetime.timedelta(days=days_back)
            if check_date.weekday() >= 5:
                continue

            date_str = check_date.strftime("%Y%m%d")
            url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX20?date={date_str}&response=json"

            try:
                for attempt in range(3):
                    try:
                        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
                        break
                    except requests.exceptions.SSLError:
                        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30, verify=False)
                        break
                    except Exception:
                        if attempt < 2:
                            time.sleep(2)
                            continue
                        raise

                result = resp.json()
                if result.get("stat") == "OK" and "data" in result:
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
                            try:
                                dir_str = str(row[9]).strip().lower()
                                change_dir = "+" if "+" in dir_str or "red" in dir_str or "漲" in dir_str else ("-" if "-" in dir_str or "green" in dir_str or "跌" in dir_str else "")
                                change_val = float(str(row[10]).replace(",", "").strip())
                                signed_change = -change_val if change_dir == "-" else change_val
                                
                                prev_close = close_price - signed_change
                                if prev_close > 0:
                                    pct_change = round((signed_change / prev_close) * 100, 2)
                                else:
                                    pct_change = 0.0
                            except (ValueError, IndexError, ZeroDivisionError):
                                pct_change = 0.0

                            if stock_id and stock_name:
                                stocks.append({
                                    "rank": len(stocks) + 1,
                                    "id": stock_id,
                                    "name": stock_name,
                                    "volume": volume,
                                    "close_price": close_price,
                                    "pct_change": pct_change,
                                })
                        except (ValueError, IndexError):
                            continue

                    if stocks:
                        data_date = check_date.strftime("%Y-%m-%d")
                        print(f"  ✅ 成交量前 {len(stocks)} 名已獲取（資料日期：{data_date}）")
                        for s in stocks[:5]:
                            print(f"     {s['rank']}. {s['id']} {s['name']}: {s['volume']:,} 股 ({s['pct_change']:+.2f}%)")
                        return fetch_ok(
                            stocks[:top_n],
                            source="twse",
                            meta={"kind": "volume_ranking", "top_n": top_n, "data_date": data_date},
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
            meta={"kind": "volume_ranking", "top_n": top_n},
        )

    def get_macro_events(self, days=5):
        """
        Fetches upcoming global macro economic events (e.g., CPI, Fed meetings, Non-farm payrolls).
        """
        try:
            print("\n📅 正在獲取本週全球重要財經日曆與總體經濟事件...")
            query = "本週全球重要財經日曆 總經數據 發布時間表 (CPI, Fed, 聯準會, 非農, 財報)"
            response = self._tavily_search(query, search_depth="basic", max_results=3, days=days)
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

    def get_weekly_stock_data(self, symbol, trading_days=5):
        """
        Fetches a week's worth of daily stock data for weekly summary.
        Returns weekly change, daily closing series, intra-week high/low,
        plus current technical indicators (MA5, MA20, RSI).
        """
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="60d")

            if hist.empty or len(hist) < trading_days + 1:
                print(f"[Warning] 無法取得 {symbol} 的週數據")
                return fetch_error(
                    f"無法取得 {symbol} 的週數據",
                    code="EMPTY_DATA",
                    source="yfinance",
                    data=None,
                    meta={"symbol": symbol, "kind": "weekly_stock"},
                )

            # Last N trading days for the week
            week_data = hist.tail(trading_days)
            closes = hist['Close'].values
            week_closes = week_data['Close'].values
            week_highs = week_data['High'].values
            week_lows = week_data['Low'].values
            week_volumes = week_data['Volume'].values

            # The close before the week started (for weekly change calc)
            pre_week_close = hist['Close'].iloc[-(trading_days + 1)]

            week_open = week_closes[0]  # Monday close as proxy
            week_close = week_closes[-1]  # Friday close
            week_high = float(np.max(week_highs))
            week_low = float(np.min(week_lows))
            week_change = week_close - pre_week_close
            week_pct_change = (week_change / pre_week_close) * 100
            avg_volume = int(np.mean(week_volumes))

            # Technical indicators (current)
            ma5 = round(float(np.mean(closes[-5:])), 2) if len(closes) >= 5 else None
            ma20 = round(float(np.mean(closes[-20:])), 2) if len(closes) >= 20 else None
            rsi = self._calc_rsi(closes) if len(closes) >= 15 else None

            # Daily series for narration
            daily_series = []
            for idx, row in week_data.iterrows():
                date_str = idx.strftime("%m/%d")
                day_close = round(float(row['Close']), 2)
                day_change = round(float(row['Close'] - (hist['Close'].shift(1).loc[idx] if idx in hist.index else row['Open'])), 2)
                day_pct = round((day_change / (day_close - day_change)) * 100, 2) if (day_close - day_change) != 0 else 0
                daily_series.append({
                    "date": date_str,
                    "close": day_close,
                    "change": day_change,
                    "pct_change": day_pct,
                    "volume": int(row['Volume']),
                })

            payload = {
                "symbol": symbol,
                "week_close": round(float(week_close), 2),
                "week_change": round(float(week_change), 2),
                "week_pct_change": round(float(week_pct_change), 2),
                "week_high": round(week_high, 2),
                "week_low": round(week_low, 2),
                "avg_volume": avg_volume,
                "ma5": ma5,
                "ma20": ma20,
                "rsi": rsi,
                "daily_series": daily_series,
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

        date_str = check_date.strftime("%Y%m%d")
        url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALLBUT0999&response=json"
        for day_attempt in range(day_attempts):
            resp = None
            for attempt in range(3):
                try:
                    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
                    break
                except requests.exceptions.SSLError:
                    if attempt == 0:
                        print("  [Info] SSL 驗證失敗，嘗試跳過驗證...")
                    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30, verify=False)
                    break
                except requests.exceptions.ReadTimeout:
                    if attempt < 2:
                        print(f"  [Warning] TWSE API 讀取超時，重試第 {attempt + 1} 次...")
                        time.sleep(2)
                        continue
                    raise
            if resp is None:
                return None, None

            # Empty body / HTML / "no data yet" are common right after market close.
            text = (resp.text or "").strip()
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

    def get_institutional_data(self, top_n=10, target_date=None):
        """
        Fetches institutional investor buy/sell data from TWSE Open API.
        Dynamically returns the top N foreign investor net buy AND top N net sell stocks.
        Falls back up to 14 days to handle long holidays (e.g. Chinese New Year).

        If target_date (YYYY-MM-DD) is set, only fetch that session (no older fallback).
        """
        print("正在獲取三大法人買賣超數據...")

        data = None
        data_date_str = None

        try:
            if target_date:
                check_date = datetime.datetime.strptime(str(target_date), "%Y-%m-%d")
                data, data_date_str = self._fetch_institutional_payload(
                    check_date,
                    day_attempts=8,
                    label=f"{target_date}",
                )
            else:
                # Try today first, then fall back up to 14 days (covers CNY and other long holidays)
                for days_back in range(0, 15):
                    check_date = datetime.datetime.now() - datetime.timedelta(days=days_back)
                    if check_date.weekday() >= 5:
                        continue

                    # Today: more readiness waits (volume often arrives before T86).
                    attempts = 8 if days_back == 0 else 1
                    try:
                        data, data_date_str = self._fetch_institutional_payload(
                            check_date,
                            day_attempts=attempts,
                            label="今日" if days_back == 0 else "",
                        )
                        if data is not None:
                            if days_back > 0:
                                print(f"  [Info] 使用 {data_date_str} 的法人數據（最近有資料的交易日）")
                            break
                        if days_back == 0:
                            print("  [Info] 今日數據尚未發布（已用盡就緒重試），往前搜尋中...")
                    except Exception as e:
                        if days_back == 0:
                            print(f"  [Info] 取得今日法人數據發生錯誤 ({e})，往前搜尋中...")
                        continue

            if data is None:
                print("  [Warning] 無法取得三大法人數據（已搜尋近 14 天）")
                return fetch_error(
                    "無法取得三大法人數據（已搜尋近 14 天）",
                    code="EMPTY_DATA",
                    source="twse",
                    data={"top_buy": [], "top_sell": [], "data_date": None},
                    meta={"kind": "institutional", "top_n": top_n},
                )

            def parse_num(s):
                return int(s.replace(",", "").replace(" ", ""))

            all_stocks = []
            for row in data["data"]:
                try:
                    stock_id = row[0].strip()
                    stock_name = row[1].strip()
                    foreign_net = parse_num(row[4])
                    trust_net = parse_num(row[10])
                    total_net = parse_num(row[-1])

                    all_stocks.append({
                        "id": stock_id,
                        "name": stock_name,
                        "foreign_net": foreign_net,
                        "trust_net": trust_net,
                        "total_net": total_net,
                    })
                except (ValueError, IndexError):
                    continue

            # Fetch close prices for top movers to compute dollar amounts (parallel)
            print("  正在計算法人買賣超金額（並行抓取）...")
            sorted_by_shares = sorted(all_stocks, key=lambda x: abs(x["foreign_net"]), reverse=True)
            price_candidates = sorted_by_shares[:top_n * 4]

            def _lookup_price(stock):
                try:
                    hist = yf.Ticker(f"{stock['id']}.TW").history(period="5d")
                    if not hist.empty:
                        close_price = float(hist['Close'].iloc[-1])
                        stock['close_price'] = close_price
                        stock['est_amount'] = round(stock['foreign_net'] * close_price / 1e8, 2)
                    else:
                        stock['close_price'] = None
                        stock['est_amount'] = 0
                except Exception:
                    stock['close_price'] = None
                    stock['est_amount'] = 0

            with ThreadPoolExecutor(max_workers=10) as executor:
                list(executor.map(_lookup_price, price_candidates))

            # Fill 0 for stocks we didn't look up
            for stock in all_stocks:
                if 'est_amount' not in stock:
                    stock['est_amount'] = 0

            # Sort by estimated dollar amount
            sorted_by_amount = sorted(all_stocks, key=lambda x: x["est_amount"], reverse=True)
            top_buy = sorted_by_amount[:top_n]
            # For sells, sort ascending (most negative first)
            sorted_by_amount_sell = sorted(all_stocks, key=lambda x: x["est_amount"])
            top_sell = sorted_by_amount_sell[:top_n]

            print(f"  ✅ 外資買超前 {top_n} 名（依金額排序）:")
            for s in top_buy[:5]:
                amt_str = f"{s['est_amount']:+.1f}億" if s.get('est_amount') else ""
                print(f"     {s['id']} {s['name']}: {s['foreign_net']:+,} 股 ({amt_str})")
            print(f"  ✅ 外資賣超前 {top_n} 名（依金額排序）:")
            for s in top_sell[:5]:
                amt_str = f"{s['est_amount']:+.1f}億" if s.get('est_amount') else ""
                print(f"     {s['id']} {s['name']}: {s['foreign_net']:+,} 股 ({amt_str})")

            return fetch_ok(
                {"top_buy": top_buy, "top_sell": top_sell, "data_date": data_date_str},
                source="twse",
                meta={"kind": "institutional", "top_n": top_n},
            )

        except Exception as e:
            logger.exception("取得三大法人數據失敗")
            print(f"[Error] 取得三大法人數據失敗: {e}")
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
        try:
            for days_back in range(0, 15):
                check_date = datetime.datetime.now() - datetime.timedelta(days=days_back)
                if check_date.weekday() >= 5:
                    continue

                date_str = check_date.strftime("%Y%m%d")
                url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALLBUT0999&response=json"

                try:
                    import time
                    for attempt in range(3):
                        try:
                            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
                            break
                        except requests.exceptions.SSLError:
                            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15, verify=False)
                            break
                        except requests.exceptions.ReadTimeout:
                            if attempt < 2:
                                time.sleep(1)
                                continue
                            else:
                                raise

                    result = resp.json()
                    if result.get("stat") == "OK" and "data" in result:
                        
                        def parse_num(s):
                            return int(s.replace(",", "").replace(" ", ""))

                        for row in result["data"]:
                            try:
                                stock_id = row[0].strip()
                                if stock_id == ticker:
                                    foreign_net = parse_num(row[4])
                                    trust_net = parse_num(row[10])
                                    total_net = parse_num(row[-1])
                                    return fetch_ok(
                                        {"foreign_net": foreign_net, "trust_net": trust_net, "total_net": total_net},
                                        source="twse",
                                        meta={"kind": "single_institutional", "ticker": ticker},
                                    )
                            except (ValueError, IndexError):
                                continue
                                
                        # If reached here, TWSE data was fetched but ticker not found (e.g., it's OTC or no institutional action)
                        return fetch_error(
                            f"未找到 {ticker} 的法人資料",
                            code="NOT_FOUND",
                            source="twse",
                            data=None,
                            meta={"kind": "single_institutional", "ticker": ticker},
                        )
                except Exception:
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
                response = self._tavily_search(query, search_depth="basic", max_results=2, days=days)
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

    def get_supply_chain_catalyst_events(self, days=7, lite=True, cache_path=None):
        """
        Actively searches for supply-chain-level AI/semiconductor events.

        This catches structural news that may not immediately appear in today's
        volume or institutional rankings, e.g. AMD investing in Taiwan's AI
        ecosystem, CoWoS capacity deals, or AI server platform launches.
        """
        cache_path = cache_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data",
            "supply_chain_event_cache.json",
        )
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        try:
            if os.path.exists(cache_path):
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                if cached.get("date") == today:
                    print("\n🧭 使用今日已快取的主動消息面事件雷達")
                    return fetch_ok(
                        cached.get("events", []),
                        source="cache",
                        meta={"kind": "supply_chain_events", "days": days, "lite": lite},
                    )
        except Exception:
            pass

        print("\n🧭 正在搜尋低成本主動消息面事件雷達（AI供應鏈 / 台廠投資 / 先進封裝）...")
        full_queries = [
            "AMD Taiwan investment AI infrastructure advanced packaging supply chain",
            "AMD 台灣 投資 AI 基礎設施 先進封裝 供應鏈",
            "NVIDIA Taiwan AI server supply chain advanced packaging CoWoS investment",
            "Broadcom Marvell Taiwan AI ASIC supply chain CoWoS substrate",
            "OpenAI Microsoft AWS Taiwan AI server supply chain investment",
            "台灣 AI 伺服器 供應鏈 投資 先進封裝 CoWoS 基板 HBM",
        ]
        queries = full_queries[:2] if lite else full_queries

        events = []
        seen = set()
        for query in queries:
            try:
                response = self._tavily_search(
                    query,
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
                json.dump({"date": today, "events": events}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return fetch_ok(
            events,
            source="tavily",
            meta={"kind": "supply_chain_events", "days": days, "lite": lite},
        )

    def get_news(self, query, days=2):
        """
        Fetches news using Tavily with self-healing retry.
        """
        try:
            print(f"搜尋新聞: {query}")
            response = self._tavily_search(query, search_depth="advanced", max_results=5, days=days)
            
            if not response.get('results'):
                broad_query = f"{query.split(' ')[0]} stock news"
                print(f"  [Info] 無結果，嘗試更廣泛的關鍵詞: {broad_query}")
                response = self._tavily_search(broad_query, search_depth="advanced", max_results=5, days=days)
            
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
            "summary_for_ai": ""
        }

        # ── 1. 基本面：近4季毛利率 & 營收年增率 ──
        try:
            ticker = yf.Ticker(symbol)
            fin = ticker.quarterly_financials
            if fin is not None and not fin.empty:
                gross_profit_row = None
                revenue_row = None
                for idx in fin.index:
                    if "Gross Profit" in str(idx):
                        gross_profit_row = fin.loc[idx]
                    if "Total Revenue" in str(idx):
                        revenue_row = fin.loc[idx]

                if gross_profit_row is not None and revenue_row is not None:
                    quarters = list(fin.columns[:4])  # 最近4季
                    margins = []
                    revenues = []
                    for q in quarters:
                        try:
                            gp = float(gross_profit_row[q])
                            rev = float(revenue_row[q])
                            margins.append(round(gp / rev * 100, 1) if rev != 0 else None)
                            revenues.append(round(rev / 1e8, 1))  # 億 TWD (approx)
                        except Exception:
                            margins.append(None)
                            revenues.append(None)

                    result["fundamental"]["gross_margin_4q"] = margins
                    result["fundamental"]["revenue_4q_bn"] = revenues
                    result["fundamental"]["quarter_labels"] = [str(q)[:7] for q in quarters]

                    # 毛利率趨勢判斷
                    valid_margins = [m for m in margins if m is not None]
                    if len(valid_margins) >= 2:
                        trend = "改善" if valid_margins[0] > valid_margins[-1] else "走弱"
                        result["fundamental"]["margin_trend"] = trend
                        result["fundamental"]["latest_gross_margin"] = valid_margins[0]

                    # 營收年增率（近一季 vs 同期一年前）
                    if len(revenues) >= 4 and revenues[0] and revenues[3]:
                        yoy = round((revenues[0] - revenues[3]) / revenues[3] * 100, 1)
                        result["fundamental"]["revenue_yoy_pct"] = yoy

                    print(f"  ✅ 基本面：毛利率近4季 {margins}，最新 {valid_margins[0] if valid_margins else 'N/A'}%")
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
        except Exception:
            pass

        # ── 4. 近期訂單/法說/產業新聞（2-3 則精準搜尋）──
        try:
            queries = [
                f"{stock_name} {stock_id} 毛利率 訂單 展望 2026",
                f"{stock_name} {stock_id} 法說會 客戶 產能利用率",
                f"Taiwan {stock_id} {stock_name} revenue margin order 2026",
            ]
            news_items = []
            for q in queries:
                try:
                    resp = self._tavily_search(q, search_depth="basic", max_results=2, days=30)
                    for item in resp.get("results", []):
                        title = item.get("title", "")
                        snippet = item.get("content", "")[:200]
                        if title and title not in [n["title"] for n in news_items]:
                            news_items.append({"title": title, "snippet": snippet})
                except Exception:
                    logger.exception("深度押注新聞搜尋失敗")
                    continue
            result["recent_news"] = news_items[:5]
            print(f"  ✅ 近期新聞：找到 {len(result['recent_news'])} 則產業/訂單相關報導")
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
        if fund.get("revenue_yoy_pct") is not None:
            yoy = fund["revenue_yoy_pct"]
            lines.append(f"📈 營收年增率：{yoy:+.1f}%（{'成長' if yoy > 0 else '衰退'}）")

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
