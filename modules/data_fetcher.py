import yfinance as yf
from tavily import TavilyClient
import datetime
import requests
import numpy as np

class DataFetcher:
    def __init__(self, tavily_api_key):
        self.tavily_client = TavilyClient(api_key=tavily_api_key)

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
        """TWSE GET with UA + SSL fallback."""
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

    def _fetch_twse_daily_closes(self, stock_id, months_back=4):
        """
        Fetch listed-stock daily OHLCV from TWSE STOCK_DAY.
        Returns list of dicts sorted by date ascending: {date, close, volume, change}.
        """
        stock_id = str(stock_id).strip()
        rows = []
        seen_dates = set()
        today = datetime.datetime.now()

        for m in range(months_back):
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
                print(f"[Warning] TWSE STOCK_DAY failed for {stock_id} {date_str}: {e}")
                continue
            if not result or result.get("stat") != "OK":
                continue
            for row in result.get("data") or []:
                try:
                    # 日期, 成交股數, 成交金額, 開盤, 最高, 最低, 收盤, 漲跌價差, ...
                    roc_date = str(row[0]).strip()
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
                    change = 0.0
                    if change_raw and change_raw[0] in "+-" and change_raw[1:].replace(".", "", 1).isdigit():
                        change = float(change_raw)
                    elif change_raw.replace(".", "", 1).isdigit():
                        change = float(change_raw)
                    seen_dates.add(dkey)
                    rows.append({"date": dkey, "close": close, "volume": volume, "change": change})
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
        # OTC (.TWO) is not on TWSE STOCK_DAY
        if str(symbol).upper().endswith(".TWO"):
            return None
        rows = self._fetch_twse_daily_closes(stock_id, months_back=4)
        if len(rows) < 2:
            print(f"[Warning] TWSE 備援也無法取得 {stock_id} 日線")
            return None
        closes = [r["close"] for r in rows]
        volumes = [r["volume"] for r in rows]
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
        if payload:
            print(f"  ✅ TWSE 備援成功: {stock_id} 收 {payload['price']} ({payload['pct_change']:+.2f}%)")
        return payload

    def get_stock_data(self, symbol):
        """
        Fetches stock data with technical indicators.
        Primary: yfinance. Fallback for Taiwan listed stocks: TWSE STOCK_DAY
        (Yahoo often blocks Render / datacenter IPs → empty history).
        Returns dict or None (compatible with web app).
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
                    print(f"[Warning] yfinance download fallback failed for {symbol}: {dl_err}")

            if hist is not None and not hist.empty and len(hist) >= 2:
                closes = hist["Close"].values
                prev_close = float(closes[-2])
                volumes = hist["Volume"].values
                payload = self._payload_from_close_series(symbol, closes, volumes, prev_close=prev_close)
                if payload:
                    return payload

            print(f"[Warning] yfinance 無資料，嘗試 TWSE 備援: {symbol}")
            twse_payload = self._get_stock_data_twse_fallback(symbol)
            if twse_payload:
                return twse_payload

            print(f"[Warning] 無法取得 {symbol} 的數據")
            return None
        except Exception as e:
            print(f"[Error] 取得 {symbol} 數據失敗: {e}")
            try:
                twse_payload = self._get_stock_data_twse_fallback(symbol)
                if twse_payload:
                    return twse_payload
            except Exception:
                pass
            return None

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
                print(f"  ⚠️  {name}: 取得失敗 ({e})")
        return results

    def get_macro_events(self, days=5):
        """
        Fetches upcoming global macro economic events (e.g., CPI, Fed meetings, Non-farm payrolls).
        """
        try:
            print("\n📅 正在獲取本週全球重要財經日曆與總體經濟事件...")
            query = "本週全球重要財經日曆 總經數據 發布時間表 (CPI, Fed, 聯準會, 非農, 財報)"
            response = self.tavily_client.search(query, search_depth="basic", max_results=3, days=days)
            return response.get('results', [])
        except Exception as e:
            print(f"[Error] Tavily 總經事件搜尋失敗: {e}")
            return []

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
                return None

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

            return {
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
        except Exception as e:
            print(f"[Error] 取得 {symbol} 週數據失敗: {e}")
            return None

    def get_institutional_data(self, top_n=10):
        """
        Fetches institutional investor buy/sell data from TWSE Open API.
        Dynamically returns the top N foreign investor net buy AND top N net sell stocks.
        Falls back up to 14 days to handle long holidays (e.g. Chinese New Year).
        """
        print("正在獲取三大法人買賣超數據...")

        data = None
        data_date_str = None

        try:
            # Try today first, then fall back up to 14 days (covers CNY and other long holidays)
            for days_back in range(0, 15):
                check_date = datetime.datetime.now() - datetime.timedelta(days=days_back)
                # Skip weekends
                if check_date.weekday() >= 5:
                    continue

                date_str = check_date.strftime("%Y%m%d")
                url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALLBUT0999&response=json"

                try:
                    import time
                    for attempt in range(3): # Retry up to 3 times for timeout
                        try:
                            # Increase timeout to 30 seconds since TWSE is very slow post-market
                            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
                            break
                        except requests.exceptions.SSLError:
                            if days_back == 0 and attempt == 0:
                                print("  [Info] SSL 驗證失敗，嘗試跳過驗證...")
                            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30, verify=False)
                            break
                        except requests.exceptions.ReadTimeout:
                            if attempt < 2:
                                print(f"  [Warning] TWSE API 讀取超時，重試第 {attempt+1} 次...")
                                time.sleep(2)
                                continue
                            else:
                                raise
                        except Exception as e:
                            raise e

                    result = resp.json()
                    if result.get("stat") == "OK" and "data" in result:
                        data = result
                        data_date_str = check_date.strftime("%Y-%m-%d")
                        if days_back > 0:
                            print(f"  [Info] 使用 {data_date_str} 的法人數據（最近有資料的交易日）")
                        break
                    else:
                        if days_back == 0:
                            print(f"  [Info] 今日數據尚未發布，往前搜尋中...")
                        
                except Exception as e:
                    if days_back == 0:
                        print(f"  [Info] 取得今日法人數據發生錯誤 ({e})，往前搜尋中...")
                    continue

            if data is None:
                print("  [Warning] 無法取得三大法人數據（已搜尋近 14 天）")
                return {"top_buy": [], "top_sell": [], "data_date": None}

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

            # Fetch close prices for top movers to compute dollar amounts
            print("  正在計算法人買賣超金額...")
            # Get unique stock IDs that need price lookup (top candidates)
            sorted_by_shares = sorted(all_stocks, key=lambda x: abs(x["foreign_net"]), reverse=True)
            price_candidates = sorted_by_shares[:top_n * 4]  # Look up more than needed
            
            for stock in price_candidates:
                try:
                    tw_symbol = f"{stock['id']}.TW"
                    tk = yf.Ticker(tw_symbol)
                    hist = tk.history(period="5d")
                    if not hist.empty:
                        close_price = float(hist['Close'].iloc[-1])
                        stock['close_price'] = close_price
                        # est_amount in TWD (shares * price), convert to 億
                        stock['est_amount'] = round(stock['foreign_net'] * close_price / 1e8, 2)
                    else:
                        stock['close_price'] = None
                        stock['est_amount'] = 0
                except Exception:
                    stock['close_price'] = None
                    stock['est_amount'] = 0

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

            return {"top_buy": top_buy, "top_sell": top_sell, "data_date": data_date_str}

        except Exception as e:
            print(f"[Error] 取得三大法人數據失敗: {e}")
            return {"top_buy": [], "top_sell": [], "data_date": None}

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
                                    return {"foreign_net": foreign_net, "trust_net": trust_net, "total_net": total_net}
                            except (ValueError, IndexError):
                                continue
                                
                        # If reached here, TWSE data was fetched but ticker not found (e.g., it's OTC or no institutional action)
                        return None
                except Exception:
                    continue
            return None
        except Exception as e:
            print(f"[Error] 快速取得單一個股法人數據失敗: {e}")
            return None

    def get_news(self, query, days=2):
        """
        Fetches news using Tavily with self-healing retry.
        """
        try:
            print(f"搜尋新聞: {query}")
            response = self.tavily_client.search(query, search_depth="advanced", max_results=5, days=days)
            
            if not response.get('results'):
                broad_query = f"{query.split(' ')[0]} stock news"
                print(f"  [Info] 無結果，嘗試更廣泛的關鍵詞: {broad_query}")
                response = self.tavily_client.search(broad_query, search_depth="advanced", max_results=5, days=days)
            
            return response.get('results', [])
            
        except Exception as e:
            print(f"[Error] Tavily 搜尋失敗: {e}")
            return []

