import os
import json
import time
import google.generativeai as genai

class StockDatabaseUpdater:
    def __init__(self, api_key):
        genai.configure(api_key=api_key)
        # 預設使用 Flash，因為做分類標籤速度快且成本低
        model_name = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
        self.model = genai.GenerativeModel(model_name)
        
        self.db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "stock_db.json")
        self.stock_db = self._load_stock_db()

    def _load_stock_db(self):
        try:
            with open(self.db_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[StockDB Updater] 無法載入資料庫，建立新空字典: {e}")
            return {}
            
    def _save_stock_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with open(self.db_path, "w", encoding="utf-8") as f:
            json.dump(self.stock_db, f, ensure_ascii=False, indent=2)

    def update_new_stocks(self, stock_list):
        """
        stock_list: list of dicts [{'id': '2330', 'name': '台積電'}, ...]
        Checks if they exist in DB. If not, batch queries Gemini and updates DB.
        """
        missing_stocks = []
        for stock in stock_list:
            if not stock.get('id'):
                continue
            # Remove '.TW' if present
            sid = stock['id'].replace('.TW', '').replace('.TWO', '')
            if sid not in self.stock_db and sid.isdigit():
                # Avoid duplicates in missing list
                if not any(s['id'] == sid for s in missing_stocks):
                    missing_stocks.append({"id": sid, "name": stock.get('name', '未知')})
                    
        if not missing_stocks:
            print("  [Auto-DB] 本次列入清單的股票皆已存在於知識庫中。")
            return
            
        print(f"  [Auto-DB] 發現 {len(missing_stocks)} 檔未知新股票，啟動 AI 身家調查: {[s['id'] + ' ' + s['name'] for s in missing_stocks]}")
        
        new_data = self._query_gemini_for_stocks(missing_stocks)
        if new_data:
            # Merge and save
            self.stock_db.update(new_data)
            self._save_stock_db()
            print(f"  [Auto-DB] 成功為 {len(new_data)} 檔股票建檔並寫入資料庫！")
        else:
            print("  [Auto-DB] AI 調查失敗，本次未新增資料。")

    def update_tracking_stats(self, yesterday_predictions, todays_market_data, todays_institutional_data=None):
        """
        [優化 D] 根據昨日預測與今日實際表現，更新 stock_db 中的動態績效 tracking 欄位。

        Args:
            yesterday_predictions: list of dicts from yesterday's structured_data["prediction_targets"]
                e.g. [{"id": "2303", "name": "聯電", "direction": "多", "stop_loss_price": 68.0, ...}]
            todays_market_data: dict from data_fetcher, keyed by "XXXX.TW"
                e.g. {"2303.TW": {"price": 76.0, "pct_change": 4.1, ...}}
            todays_institutional_data: optional, the institutional data dict with top_buy/top_sell lists
        """
        if not yesterday_predictions:
            return

        today_str = str(__import__('datetime').date.today())
        needs_save = False

        # Build lookup: id -> pct_change from today's market data
        market_lookup = {}
        for symbol, data in todays_market_data.items():
            sid = symbol.replace('.TW', '').replace('.TWO', '')
            if data and 'pct_change' in data:
                market_lookup[sid] = data['pct_change']

        # Build set of today's foreign buy ids
        fii_buy_ids = set()
        if todays_institutional_data:
            for s in todays_institutional_data.get('top_buy', []):
                fii_buy_ids.add(str(s['id']))

        print(f"  [Auto-DB] 正在更新 {len(yesterday_predictions)} 檔昨日預測標的的績效追蹤...")

        for pred in yesterday_predictions:
            sid = str(pred.get('id', '')).replace('.TW', '')
            if not sid:
                continue

            # Ensure stock exists in DB (may be a known stock not yet in DB)
            if sid not in self.stock_db:
                continue

            entry = self.stock_db[sid]
            tracking = entry.get('tracking', {
                'prediction_count': 0,
                'hit_count': 0,
                'hit_rate': '0.0%',
                'last_recommended': None,
                'consecutive_foreign_buy_days': 0,
                'last_foreign_buy_date': None,
                'history': []
            })

            # Determine actual result
            actual_pct = market_lookup.get(sid)
            direction = pred.get('direction', '多')
            stop_loss_price = pred.get('stop_loss_price')

            hit = None
            result_desc = "無法驗收（今日無數據）"

            if actual_pct is not None:
                if direction == '多':
                    hit = actual_pct > 0
                elif direction == '空':
                    hit = actual_pct < 0
                result_desc = f"實際 {actual_pct:+.2f}% → {'✅ 命中' if hit else '❌ 失準'}"

            # Update counters
            tracking['prediction_count'] = tracking.get('prediction_count', 0) + 1
            if hit is True:
                tracking['hit_count'] = tracking.get('hit_count', 0) + 1
            tracking['last_recommended'] = today_str

            # Hit rate
            if tracking['prediction_count'] > 0:
                rate = tracking['hit_count'] / tracking['prediction_count'] * 100
                tracking['hit_rate'] = f"{rate:.1f}%"

            # Append to history (keep last 20 entries)
            history_entry = {
                'date': today_str,
                'direction': direction,
                'trigger': pred.get('trigger', ''),
                'stop_loss': pred.get('stop_loss_desc', ''),
                'result': result_desc
            }
            history = tracking.get('history', [])
            history.append(history_entry)
            tracking['history'] = history[-20:]  # keep last 20

            # Update foreign buy streak
            if sid in fii_buy_ids:
                last_fii_date = tracking.get('last_foreign_buy_date')
                if last_fii_date == str(__import__('datetime').date.today() - __import__('datetime').timedelta(days=1)):
                    tracking['consecutive_foreign_buy_days'] = tracking.get('consecutive_foreign_buy_days', 0) + 1
                else:
                    tracking['consecutive_foreign_buy_days'] = 1
                tracking['last_foreign_buy_date'] = today_str
            else:
                # Check if streak should be reset
                last_fii_date = tracking.get('last_foreign_buy_date')
                if last_fii_date and last_fii_date != today_str:
                    tracking['consecutive_foreign_buy_days'] = 0

            entry['tracking'] = tracking
            self.stock_db[sid] = entry
            needs_save = True
            print(f"    [Tracking] {sid} {entry.get('name', '')}: {result_desc} (累計命中率: {tracking['hit_rate']})")

        if needs_save:
            self._save_stock_db()
            print(f"  [Auto-DB] 績效追蹤更新完成並寫入資料庫。")

    def _query_gemini_for_stocks(self, missing_stocks):
        list_str = "\n".join([f"- {s['id']} {s['name']}" for s in missing_stocks])
        
        prompt = f"""
你是一名資深的台股科技產業鏈研究員。
請負責為以下初次進榜的台股建立「純粹基於供應鏈角色」的基本面檔案：

{list_str}

📋 嚴格分類準則：
1. 這個公司的產品是否進入 AI 伺服器 BOM 表（物料清單）？
   - 有進去：不管它原本叫玻璃、塑化、還是紡織，都必須歸類在「AI 相關族群」，而非傳產！
   - 沒進去：依照其真實終端應用（如車用、消費電子、傳統產業）分類。
2. "not_classify_as" 欄位非常重要，必須列出該公司「常被外界誤解的傳產名號」，例如台玻是 AI 玻纖布，不能被稱為「玻璃窯業」。

回傳格式必須是單純的 JSON，且為以下結構 (最外層就是物件，KEY為股票代號)：
{{
  "股票代號": {{
    "name": "公司名稱",
    "sector": "族群名稱 (例如: AI PCB材料族群)",
    "supply_chain_role": "供應鏈精確角色 (例如: AI伺服器高頻基板輔材)",
    "key_products": ["產品A", "產品B"],
    "ai_connection": "與AI的具體關聯性 (說明它哪裡切入AI)",
    "not_classify_as": ["絕對不能被叫做的舊稱1", "傳產"]
  }}
}}

請確保輸出是合法且可解析的 JSON 字串，不要有 Markdown ```json 的包裝，直接以 {{ 開頭。
"""
        wait_times = [5, 10, 20]
        for attempt in range(3):
            try:
                response = self.model.generate_content(prompt)
                text = response.text.strip()
                if text.startswith("```json"):
                    text = text[7:]
                if text.startswith("```"):
                    text = text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
                
                parsed_json = json.loads(text)
                return parsed_json
            except Exception as e:
                print(f"  [Auto-DB Retry] 解析 JSON 失敗重試中: {e}")
                time.sleep(wait_times[attempt])
                
        return None
