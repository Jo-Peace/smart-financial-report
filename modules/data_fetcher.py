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
            prev_close = closes[-2]
            change = current_close - prev_close
            pct_change = (change / prev_close) * 100
            
            # Technical Indicators
            ma5 = round(float(np.mean(closes[-5:])), 2) if len(closes) >= 5 else None
            ma20 = round(float(np.mean(closes[-20:])), 2) if len(closes) >= 20 else None
            rsi = self._calc_rsi(closes) if len(closes) >= 15 else None
            
            # 若為大盤或極小變動，保留較高精確度避免出現 0.00%
            formatted_pct = round(float(pct_change), 3) if abs(pct_change) < 0.01 else round(float(pct_change), 2)
            
            return {
                "symbol": symbol,
                "price": round(float(current_close), 2),
                "change": round(float(change), 2),
                "pct_change": formatted_pct,
                "volume": int(hist['Volume'].iloc[-1]),
                "ma5": ma5,
                "ma20": ma20,
                "rsi": rsi,
            }
        except Exception as e:
            print(f"[Error] 取得 {symbol} 數據失敗: {e}")
            return None

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
                    try:
                        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
                    except requests.exceptions.SSLError:
                        if days_back == 0:
                            print("  [Info] SSL 驗證失敗，嘗試跳過驗證...")
                        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15, verify=False)

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
                except Exception:
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

            # Sort by foreign investor net buy/sell
            sorted_by_foreign = sorted(all_stocks, key=lambda x: x["foreign_net"], reverse=True)
            top_buy = sorted_by_foreign[:top_n]
            top_sell = sorted_by_foreign[-top_n:][::-1]  # Reverse so most sold is first

            print(f"  ✅ 外資買超前 {top_n} 名:")
            for s in top_buy[:5]:
                print(f"     {s['id']} {s['name']}: 外資 {s['foreign_net']:+,}")
            print(f"  ✅ 外資賣超前 {top_n} 名:")
            for s in top_sell[:5]:
                print(f"     {s['id']} {s['name']}: 外資 {s['foreign_net']:+,}")

            return {"top_buy": top_buy, "top_sell": top_sell, "data_date": data_date_str}

        except Exception as e:
            print(f"[Error] 取得三大法人數據失敗: {e}")
            return {"top_buy": [], "top_sell": [], "data_date": None}

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
