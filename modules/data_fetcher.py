import yfinance as yf
from tavily import TavilyClient
import datetime
import requests
import numpy as np
from concurrent.futures import ThreadPoolExecutor

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

    def get_stock_data(self, symbol):
        """
        Fetches stock data with technical indicators using yfinance.
        Returns price, change, volume, MA5, MA20, RSI.
        """
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="60d")
            
            if hist.empty or len(hist) < 2:
                print(f"[Warning] 無法取得 {symbol} 的數據")
                return None
                
            closes = hist['Close'].values
            current_close = closes[-1]
            
            # Robust previous close strategy to prevent gaps (e.g., missing days on yfinance)
            info_prev_close = ticker.info.get('previousClose')
            if info_prev_close and info_prev_close > 0:
                prev_close = float(info_prev_close)
            else:
                prev_close = closes[-2]
                
            change = current_close - prev_close
            pct_change = (change / prev_close) * 100
            
            # Technical Indicators
            ma5 = round(float(np.mean(closes[-5:])), 2) if len(closes) >= 5 else None
            ma20 = round(float(np.mean(closes[-20:])), 2) if len(closes) >= 20 else None
            rsi = self._calc_rsi(closes) if len(closes) >= 15 else None
            
            # Volume Profile Analysis
            volumes = hist['Volume'].values
            avg_vol_5d = int(np.mean(volumes[-5:])) if len(volumes) >= 5 else int(volumes[-1])
            vol_ratio = round(float(volumes[-1] / avg_vol_5d), 2) if avg_vol_5d > 0 else 1.0
            
            # 若為大盤或極小變動，保留較高精確度避免出現 0.00%
            formatted_pct = round(float(pct_change), 3) if abs(pct_change) < 0.01 else round(float(pct_change), 2)
            
            return {
                "symbol": symbol,
                "price": round(float(current_close), 2),
                "change": round(float(change), 2),
                "pct_change": formatted_pct,
                "volume": int(hist['Volume'].iloc[-1]),
                "avg_vol_5d": avg_vol_5d,
                "vol_ratio": vol_ratio,
                "ma5": ma5,
                "ma20": ma20,
                "rsi": rsi,
            }
        except Exception as e:
            print(f"[Error] 取得 {symbol} 數據失敗: {e}")
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
                            # TWSE MI_INDEX20 field order: 排名, 代號, 名稱, 成交股數, 成交金額, 開盤, 最高, 最低, 收盤, 漲跌, 漲跌幅
                            stock_id = str(row[1]).strip()
                            stock_name = str(row[2]).strip()
                            volume = int(str(row[3]).replace(",", "").strip())
                            try:
                                close_price = float(str(row[9]).replace(",", "").strip())
                            except (ValueError, IndexError):
                                close_price = 0.0
                            try:
                                dir_str = str(row[10]).strip()
                                change_dir = "+" if "+" in dir_str or "漲" in dir_str else ("-" if "-" in dir_str or "跌" in dir_str else "")
                                change_val = float(str(row[11]).replace(",", "").strip())
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
                        return stocks[:top_n]

            except Exception as e:
                print(f"  [Warning] 成交量排名獲取失敗: {e}")
                continue

        print("  [Warning] 無法獲取成交量前20名數據，分析將以外資買超為主")
        return []

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
                response = self.tavily_client.search(query, search_depth="basic", max_results=2, days=days)
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
                print(f"  [Warning] 催化劑事件搜尋失敗: {e}")
                continue
        
        print(f"  ✅ 找到 {len(events)} 個重大催化劑事件")
        return events

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
            print(f"  ⚠️  成交量趨勢分析失敗: {e}")

        # ── 3. 連續外資買超天數（估算） ──
        try:
            if institutional_data:
                top_buy_ids = {s["id"] for s in institutional_data.get("top_buy", [])}
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
                    resp = self.tavily_client.search(q, search_depth="basic", max_results=2, days=30)
                    for item in resp.get("results", []):
                        title = item.get("title", "")
                        snippet = item.get("content", "")[:200]
                        if title and title not in [n["title"] for n in news_items]:
                            news_items.append({"title": title, "snippet": snippet})
                except Exception:
                    continue
            result["recent_news"] = news_items[:5]
            print(f"  ✅ 近期新聞：找到 {len(result['recent_news'])} 則產業/訂單相關報導")
        except Exception as e:
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
        return result

