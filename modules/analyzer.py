import os
import datetime
import time
import json
from modules.gemini_client import default_model_name, generate_text

class MarketAnalyzer:
    def __init__(self, api_key):
        self.api_key = api_key
        # 讀取環境變數，本地端預設可設為 gemini-pro-latest，網站端若未設定則預設使用 gemini-flash-latest
        self.model_name = default_model_name()
        self.stock_db = self._load_stock_db()

    def _load_stock_db(self):
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "stock_db.json")
        try:
            with open(db_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Warning] Could not load stock_db.json: {e}")
            return {}


    def _call_gemini_with_retry(self, prompt, max_retries=3):
        """
        Calls Gemini with exponential backoff retry using tenacity.
        """
        from modules.api_retry import get_api_retry_decorator
        
        @get_api_retry_decorator(max_attempts=max_retries + 1, min_wait=5, max_wait=60)
        def _do_call():
            return generate_text(
                self.api_key,
                prompt,
                task_name="market_report",
                model_name=self.model_name,
            )
            
        try:
            return _do_call()
        except Exception as e:
            return f"Error generating report: {e}"

    def generate_report(self, market_data, news_data, institutional_data=None, prev_report_path=None, commodity_data=None, macro_events=None, tech_catalyst_events=None, volume_data=None, deep_dive_data=None, signal_summary=None, previous_prediction_targets=None, previous_validation_summary=None, editorial_frame=None, similar_sessions_summary=None, last_output_date=None):
        """
        Generates a Markdown report using Gemini with enhanced data.
        """
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        from modules.session_calendar import (
            analyzer_calendar_block,
            analyzer_session_block,
            session_lexicon,
        )

        _lex = session_lexicon(date_str, last_output_date=last_output_date)
        _session_rules = analyzer_session_block(date_str, last_output_date=last_output_date)
        _calendar_rules = analyzer_calendar_block(date_str)
        _prev_section = _lex.get("prev_section") or "昨日預測節奏"
        _prev_named = _lex.get("prev_named") or "昨天點名"
        _next_watch = _lex.get("next_watch") or "明天先看"
        _next_observe = _lex.get("next_observe") or "明日觀察"
        editorial_frame = editorial_frame or {}
        validation_placement = str(editorial_frame.get("validation_placement") or "brief").strip().lower()
        frame_label = str(editorial_frame.get("label") or editorial_frame.get("frame") or "").strip()
        # 尺子分工：隔日=節奏；約 20 交易日=趨勢成敗（本機回測較穩）。口語可說「趨勢」。
        horizon_ruler_block = """================================================================
【時間尺度——寫作與成敗尺子】
================================================================
- 隔日／近幾日（1D～5D）：只當「節奏／天氣」。可寫命中或失準，但禁止用它單獨論定「系統準不準」或整篇升級成史詩驗收日。
- 中期（約 20 個交易日／2～4 週）：「趨勢」尺子。點名標的是否延續、假說是否成立，以這條為主。
- 口語可用「趨勢」指中期窗；不要暗示在做技術分析趨勢線神單。
- 若文中提到本機多日回測：用「目前樣本顯示…」的語氣，勿寫成永遠真理。
"""
        if validation_placement == "lead":
            validation_step_block = f"""================================================================
STEP 0【{_prev_section}】今日可當開場（placement=lead）——仍是短線節奏，不是趨勢期末考
================================================================
優先使用「本機正式驗收結果」逐一寫入每一檔，不得自行刪減、替換或把非正式提醒混成正式標的；只有本機驗收結果為空時，才從「昨日正式 prediction_targets」或前日報告補抓：
- 節奏命中：寫「✅ {_prev_named} XXX，今日實際 +X%（短線節奏符合）。」
- 節奏失準：寫「❌ {_prev_named} XXX，今日實際 -X%（短線節奏偏離）。可能原因：[具體]；趨勢假說是否仍在，要看後續 2～4 週／約 20 交易日。」
- 無可驗收：寫「本日無昨日預測標的可做短線節奏對照。」
此段建立連續感後，必須立刻銜接今日盤面主線；不要把隔日漲跌寫成最終判決。
"""
            validation_output_block = f"""## 〔0〕{_prev_section}（短線）
（今日 placement=lead：可放開頭，逐一寫清數字；標明這是節奏不是趨勢期末考；寫完立刻進今日主軸。）

---
"""
        elif validation_placement == "omit":
            validation_step_block = f"""================================================================
STEP 0【{_prev_section}】今日不要當主鉤子（placement=omit）
================================================================
若有本機驗收結果：最多用 1～2 句寫在「量價對比」或「主軸」段內，並標成短線節奏；不要獨立成長段開場。
標題/主軸標籤禁止用「驗收日」「驗收現場」「上集點名驗收」當主鉤子。
若無驗收資料：完全跳過。
"""
            validation_output_block = f"""## 〔0〕{_prev_section}（短句即可，可併入主軸）
（placement=omit：不要長段驗收開場；有數字就一句帶過，屬短線節奏。）

---
"""
        else:
            validation_step_block = f"""================================================================
STEP 0【{_prev_section}】短段（placement=brief，預設）——天氣，不是趨勢判決
================================================================
不要用長段「先驗收再分析」當每日固定開場。
優先使用「本機正式驗收結果」：
- 有結果：用 2～4 句寫清短線命中/失準與數字；可加半句「趨勢要看後面約 20 個交易日」。
- 無可驗收：寫「本日無昨日預測標的可做短線節奏對照。」一句即可。
禁止把微小漲跌包裝成「史詩級驗收日」或「系統失效」。整篇主軸必須以本地主軸分類為準。
"""
            validation_output_block = f"""## 〔0〕{_prev_section}（短段）
（placement=brief：2～4 句寫清數字即可；屬短線節奏，不要佔前半篇。）

---
"""
        frame_hint = frame_label or "依本地主軸分類 / 數據判斷"
        editorial_rule = f"""
================================================================
【本地主軸分類——最高優先，不得自行升級】
================================================================
- 今日主軸標籤：{frame_hint}
- 驗收使用方式：{validation_placement}（lead=可開場但仍是節奏；brief=短段；omit=勿當主鉤子）
- 若上方 signal_summary 已給主軸分類，正文「今日主軸」必須使用該標籤，不要改回「驗收日」除非 placement=lead。
- 禁用/慎用語氣以 signal_summary 的 prohibited 為準。
{horizon_ruler_block}
"""
        
        # === Stock Data Summary ===
        data_summary = ""
        for symbol, data in market_data.items():
            if data:
                line = f"- {symbol}: 價格 {data['price']}, 漲跌 {data['change']} ({data['pct_change']}%), 成交量 {data['volume']} (5日均量 {data.get('avg_vol_5d', 'N/A')}, 量增比 {data.get('vol_ratio', 'N/A')}x)"
                if data.get('ma5'):
                    line += f", MA5={data['ma5']}"
                if data.get('ma20'):
                    line += f", MA20={data['ma20']}"
                if data.get('rsi') is not None:
                    line += f", RSI={data['rsi']}"
                data_summary += line + "\n"
            else:
                data_summary += f"- {symbol}: 數據無法取得\n"
                
        # === News Summary ===
        news_summary = ""
        for item in news_data:
            news_summary += f"- {item['title']} ({item['url']})\n"
            
        # === Institutional Data Summary (with dollar amounts) ===
        inst_summary = ""
        if institutional_data and (institutional_data.get("top_buy") or institutional_data.get("top_sell")):
            data_date = institutional_data.get("data_date", "未知")
            inst_summary = f"\n# 三大法人買賣超（依金額排序 - 資料日期：{data_date}）\n"
            inst_summary += "\n## 外資買超前10名（依估計金額排序）\n"
            for s in institutional_data.get("top_buy", []):
                amt = s.get('est_amount', 0)
                amt_str = f", 估計金額 {amt:+.1f}億" if amt else ""
                inst_summary += f"- {s['id']} {s['name']}: 外資 {s['foreign_net']:+,}股, 投信 {s['trust_net']:+,}股, 合計 {s['total_net']:+,}股{amt_str}\n"
            inst_summary += "\n## 外資賣超前10名（依估計金額排序）\n"
            for s in institutional_data.get("top_sell", []):
                amt = s.get('est_amount', 0)
                amt_str = f", 估計金額 {amt:+.1f}億" if amt else ""
                inst_summary += f"- {s['id']} {s['name']}: 外資 {s['foreign_net']:+,}股, 投信 {s['trust_net']:+,}股, 合計 {s['total_net']:+,}股{amt_str}\n"
        
        # === Commodity Data ===
        commodity_summary = ""
        if commodity_data:
            commodity_summary = "\n# 國際商品行情\n"
            for sym, cdata in commodity_data.items():
                commodity_summary += f"- {cdata['name']}: ${cdata['price']} ({cdata['pct_change']:+.2f}%)\n"
        
        # === Macro Events ===
        macro_summary = ""
        if macro_events:
            macro_summary = "\n# 近期全球重要總經事件與財曆 (Macro Calendar)\n"
            for item in macro_events:
                macro_summary += f"- {item['title']} ({item['url']})\n"
                
        # === Tech Catalyst Events ===
        catalyst_summary = ""
        if tech_catalyst_events:
            catalyst_summary = "\n# 重大科技/總經催化劍事件（趁近 14 天）\n"
            for ev in tech_catalyst_events:
                catalyst_summary += f"- {ev['event']}\n  └→ {ev['snippet']}\n"

        # === Volume Ranking Data (今日成交量排名 - 真實市場投票) ===
        volume_summary = ""
        if volume_data:
            volume_summary = "\n# 📊 今日成交量前20名（真實市場行動，分析必須以此為主軸）\n"
            volume_summary += "⚠️ 警示：這才是今日市場『最有共識』的實際行動。外資買超金額≠成交量。\n"
            for s in volume_data[:20]:
                volume_summary += f"- 第{s['rank']}名: {s['id']} {s['name']} | 成交量 {s['volume']:,} 股 | 收盤 {s.get('close_price', 'N/A')} | 漲跌 {s.get('pct_change', 0):+.2f}%\n"

        previous_predictions_summary = ""
        if previous_prediction_targets:
            previous_predictions_summary = "\n# 昨日正式 prediction_targets（必須逐一驗收，不得省略）\n"
            for target in previous_prediction_targets:
                previous_predictions_summary += (
                    f"- {target.get('id', '')} {target.get('name', '')}: "
                    f"方向={target.get('direction', '未指定')}；"
                    f"觸發理由={target.get('trigger', '未指定')}；"
                    f"停損={target.get('stop_loss_desc') or target.get('stop_loss_price') or '未指定'}\n"
                )

        # === Deep Dive Data (今日焦點股深度押注資料) ===
        deep_dive_summary = ""
        if deep_dive_data and deep_dive_data.get("summary_for_ai"):
            deep_dive_summary = f"\n# 🔬 今日深度押注數據包（焦點股：{deep_dive_data['stock_id']} {deep_dive_data['stock_name']}\n"
            deep_dive_summary += deep_dive_data["summary_for_ai"] + "\n"
            deep_dive_summary += "🚨 此數據包是系統自動抓取的真實基本面資料。請基於此作出具體時間預測。\n"

        # === Historical Comparison ===
        hist_section = ""
        if prev_report_path:
            try:
                with open(prev_report_path, "r", encoding="utf-8") as f:
                    prev_content = f.read()
                hist_section = f"\n# 前日報告（供昨日預測驗收用）\n{prev_content[:3000]}\n"
            except Exception:
                hist_section = ""
            
        # === Dynamic Stock Database (Lazy Loading) ===
        # Extract active tickers from today's data to avoid bloating the prompt
        active_tickers = set()
        
        # from market_data keys e.g. "2330.TW"
        for symbol in market_data.keys():
            active_tickers.add(symbol.replace('.TW', '').replace('.TWO', ''))
            
        # from volume_data
        if volume_data:
            for s in volume_data:
                active_tickers.add(str(s['id']))
                
        # from institutional_data
        if institutional_data:
            for s in institutional_data.get('top_buy', []):
                active_tickers.add(str(s['id']))
            for s in institutional_data.get('top_sell', []):
                active_tickers.add(str(s['id']))
                
        stock_db_str = ""
        if self.stock_db:
            appended_count = 0
            for sid, info in self.stock_db.items():
                if sid in active_tickers:
                    stock_db_str += f"- {sid} {info.get('name', '')}：分類為「{info.get('sector', '')}」\n"
                    stock_db_str += f"  └→ 角色：{info.get('supply_chain_role', '')}\n"
                    not_class = "、".join(info.get('not_classify_as', []))
                    if not_class:
                        stock_db_str += f"  🚫 絕對禁止分類為：{not_class}\n"
                    appended_count += 1
            if appended_count == 0:
                stock_db_str = "（今日主力個股尚無特殊之自訂分類，請依常理判斷）"
        else:
            stock_db_str = "（目前資料庫為空，請依常理判斷）"
            
        # === Second Brain: Historical Knowledge Wiki (size-capped) ===
        wiki_str = ""
        wiki_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "knowledge_wiki")
        MAX_WIKI_CHARS = 3500

        if os.path.exists(wiki_dir):
            # 優先讀取 weekly_digest（壓縮版，最節省 token）
            digest_path = os.path.join(wiki_dir, "weekly_digest.md")
            if os.path.exists(digest_path):
                try:
                    with open(digest_path, "r", encoding="utf-8") as f:
                        digest_content = f.read().strip()
                    if digest_content:
                        wiki_str += f"\n=== weekly_digest.md（本週精華）===\n{digest_content[:1500]}\n"
                except Exception:
                    pass

            # 再從各 wiki 檔取最近 20 行（補充當週最新動態）
            for filename in ["capital_rotation.md", "macro_events.md", "trading_strategies.md"]:
                filepath = os.path.join(wiki_dir, filename)
                if not os.path.exists(filepath):
                    continue
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    recent = "".join(lines[-20:]).strip()
                    if recent:
                        wiki_str += f"\n=== {filename}（最近20行）===\n{recent}\n"
                except Exception as e:
                    print(f"  [Warning] 無法讀取 Wiki 檔案 {filename}: {e}")

            # 硬上限：超過就截斷，防止 prompt 爆炸
            if len(wiki_str) > MAX_WIKI_CHARS:
                wiki_str = wiki_str[:MAX_WIKI_CHARS] + "\n...（已截斷，完整歷史請查閱 Wiki 檔案）"

        if not wiki_str.strip():
            wiki_str = "（尚未累積任何歷史知識）"

        prompt = f"""
你是一個台股財金節目的「首席偵探型分析師」。
你的工作不是寫報告。是找到今天「最讓人意外的數據現象」，然後用說故事的方式把它說得讓人無法跳過。

日期：{date_str}（繁體中文，數字需加括號標注中文讀音）

================================================================
【原始數據輸入——這些是事實，禁止在事實之外捏造】
================================================================

## 今日成交量前20名（市場真實行動，最重要的數據）
{volume_summary if volume_summary else '（成交量數據未能取得，改以外資買超數據為輔）'}

## 昨日正式預測標的（最高優先驗收清單）
{previous_predictions_summary if previous_predictions_summary else '（無 structured_data prediction_targets，才可從前日報告文字補抓）'}

## 本機正式驗收結果（最高優先，必須照此逐一寫入昨日預測驗收）
{previous_validation_summary if previous_validation_summary else '（尚無本機驗收結果）'}

## 本地量化訊號包（由程式計算，非 AI 推論）
{signal_summary if signal_summary else '（尚未建立本地量化訊號包）'}

## 本機相似盤（結構化比對，不是印象、不是 Wiki 尾巴）
{similar_sessions_summary if similar_sessions_summary else '（未提供相似盤；禁止編造歷史日期）'}

使用規則：
- 「主力操作假說」只能寫成假說，不能寫成已確認事實。
- 報告必須同時寫清兩層條件：
  (1) 隔日節奏：續量/量縮、守住哪條均線、是否跌破今日低點、法人是否轉買/轉賣；
  (2) 趨勢成立（約 20 交易日／2～4 週）：方向是否延續、籌碼/量價是否仍支持假說。
- 如果本地訊號包的信心分數低於 50/100，請降低語氣，寫「觀察」而不是「預測」。

## 個股技術指標（含均線與量增比）
{data_summary}

## 三大法人數據
{inst_summary}

## 國際商品
{commodity_summary}

## 新聞資訊
{news_summary}

## 催化劑事件
{catalyst_summary}

## 前日報告（用於昨日預測驗收）
{hist_section}

## 🔬 今日深度押注數據（由系統自動抓取，包含近4季毛利率、營收年増率、近期訂單消息）
{deep_dive_summary if deep_dive_summary else '(未指定深度押注數據，該段展開請依揚現有數據判斷)'}

================================================================
【我們的內部私有財經歷史記憶庫 (The Second Brain)】
================================================================
這些是我們在過去追蹤到的市場敘事、大事件、法人買賣與成交量結構。
你在分析今日數據時，必須以此為「既有認知基礎」。
- 如果今天的數據「延續」了記憶庫裡的趨勢，請在報告中點出這個連貫性（例如「正如我們此前的觀察...」）。
- 如果今天的數據「打破或反轉」了過去的敘事，這就是今日「最強異常」的絕佳題材！
{wiki_str}
{editorial_rule}
{validation_step_block}
================================================================
STEP 0.5【相似盤對照】本機已比對的歷史日，不是回憶
================================================================
上方「本機相似盤」若列出 1～2 個日期：必須寫「〔相似盤對照〕」短段（每個歷史日 2～4 句），引用那天的買賣超與隔日節奏命中/失敗。
選今日 prediction_targets 時，若相似盤顯示同一類押注隔日常失敗，要降權或寫明為何今天仍押。
若寫「未找到足夠相似盤」：正文只寫「本日無足夠相似歷史盤可對照。」一句。禁止編造沒列到的日期，禁止用「史上第一次」迴避對照。
相似盤是輔助，不能蓋過今日成交量與法人榜的主線。

================================================================
STEP 1【找出今日的最強異常 / 主線】整集的靈魂，只選一個現象
================================================================
優先使用「本地量化訊號包」與「本地主軸分類」。除非原始數據明顯反駁，否則不要自行改選主題。
從以下類型找出最反直覺、最有衝突感的數據現象（若主軸是整理日，就誠實寫訊號普通）：
A) 量價矛盾：大成交量但股價小漲（分配訊號）？縮量但大漲（假突破）？
B) 法人 vs 市場矛盾：外資大買的股票，成交量排名卻在20名以外？
C) 族群內部分歧：多支同族群個股同向，但關鍵個股逆勢？（需3支以上才能說「族群」）
D) 技術位攻防：MA20、月線有無被突破或失守？
E) 昨日預測 vs 今日現實的反差（僅在 placement=lead 或反差極大時當主線）

選出最值得追問的ONE個。所有後續分析圍繞它展開。

================================================================
【深度押注】STEP 內嵌——每日一支的大膽預測
================================================================
基於上方「今日深度押注數據」，對今日外資買超金額第一名的標的，產出一段「深度押注」專屬導言。

請同時整合：
- 毛利率趨勢（近4季毛利率是改善還是走弱？）
- 營收年增率（賺錢能力有沒有在提升？）
- 法人籌碼特徵（對比歷史上外資在低位大買的模式）
- 成交量趨勢（放量還是縮量？）
- 近期訂單/法說消息（新聞中有沒有潛在展望）

必須應含的區塊格式：
```
【今日深度押注：XXXX ×× 】
- 基本面：毛利率 X%（趨勢：改善/走弱），營收年增 X%
- 法人籌碼：今日外資買超 XX 億（全市場第 X 名）
- 成交量：[放量/縮量/量平]（今日量 Xx 倍 20日均量）
- 隔日節奏：若 [具體短線條件]，先觀察 [隔日反應]
- 趨勢假說（約 20 交易日／2～4 週）：若 [中期條件]，預期將 [回到MA20上方/突破XX元/維持強勢]
- 信心度：★★★（依據強弱：1-5顆星）
- 失效條件：收盤跌破 XX 元或 [趨勢失效條件]，假說降級
```

應該大膽不應該模糊。不要說「可能漲」；趨勢層要有可驗證條件與時間尺度。若基本面資料不足，誠實說明，不要編造。

================================================================
STEP 2【今日勾魂開場句——讓人停下來的問題】
================================================================
用最強異常設計一個帶衝突感的問題，例如：
「外資今天買進台積電 358 億，但成交量前三名沒有台積電。那錢去哪了？」
「大盤漲了X點，但成交量冠軍是一支下跌的股票。誰在逆勢操作？」
「昨天我們說 XXX 會漲，但它今天跌了。判斷哪裡出了問題？」

必須：(1) 基於真實數據 (2) 有內在衝突或懸念 (3) 讓人想知道答案

================================================================
【台股供應鏈族群知識庫——必須熟記，不得用產品名稱亂分類】
================================================================
以下是台股供應鏈的正確分類字典，當分析到這些股票時，必須強制使用這裡的「供應鏈角色」：

{stock_db_str}

📌 正確的族群分類原則：
「這家公司的產品，有沒有進入AI伺服器的BOM表（物料清單）？」
→ 有 = AI供應鏈族群；沒有 = 才是傳產

================================================================
STEP 3【偵探式展開——成交量帶路，法人對比，新聞輔助】
================================================================
順序：
1. 成交量前10名完整列表（這才是市場真正的投票結果）
2. 外資買賣超對比（機構的官方說法）
3. 兩者一致？→ 趨勢確認。兩者矛盾？→ 這才是最值得挖掘的地方
4. 新聞事件作補充（只是「可能原因之一」，不是確定答案）
5. 族群分析（只在有3支以上個股同向時，才能下「族群性」結論）
6. 【強制應用上方知識庫】：分析個股所屬族群時，先比對知識庫，用「供應鏈角色」分類，而非產品外觀

================================================================
🚫 五條鐵律，違反即失敗：
================================================================


1. 不能用單一個股代表整個族群
2. 黃金漲不等於 Fed 避險；油跌不等於需求崩潰，要加「可能原因之一」
3. 數據無法解釋的現象，直接寫「目前無法判斷，需持續觀察」，禁止捏造
4. 詞彙每週各限一次：「史詩級修復」「板塊重新定價」「機構級建倉」「主升段點火」
5. 股價剛從MA20下站上→用「跌深強彈」「底部成型」；已站穩且持續創高→才能用「主升段確認」
6. 禁用空泛套話：正文不得出現「資金輪動」四字；「資金脈絡」不得作為段落標題。請改為抓出「三大法人與成交量之間的主力買賣邏輯」，每次談買賣方向，都必須具體寫出「誰買、誰賣、金額/成交量、哪個榜單支持、隔日節奏條件、趨勢延續條件」。
7. 禁止每天硬套警報語氣：不是每天都有「最強異常」「退潮警報」「資金續航」。若今日沒有足夠數據支持，必須誠實寫成「整理日 / 觀察日 / 訊號普通」，不要把常態買賣包裝成重大警訊。
8. 禁止用「隔日沒漲＝預測失敗、系統失效」當全文結論；短線失準最多修正節奏，趨勢假說要另寫仍有效或已失效。
9. 禁止把「買超但未進成交量前20」寫成鎖籌碼、靜默入侵、影子建倉、機構級掃貨。只能寫可驗證事實：金額、是否進量榜、連續買超天數。連續買超未滿 3 日、或當日已賣超，不得推論意圖。
10. 交易日用語：週五禁止寫「明天開盤／明天台股」；週一禁止寫「昨天開盤」。用「下一交易日／上一交易日」或下面的指定口語。

{_session_rules}
{_calendar_rules}

================================================================
STEP 3.5【每日主軸選擇——先判斷今天是哪一種日子】
================================================================
在寫正文前先選一個主軸，整篇只能圍繞這個主軸，不要每天固定套同一種故事。

主軸選擇：
- **最高優先**使用本地主軸分類標籤：{frame_hint}（勿因「有驗收資料」就改成驗收日）。
- 僅當本地標籤就是驗收日且 placement=lead 時，才以驗收當開場主軸。
- 否則優先：重大事件日 → 異常日 → 續航日 → 主題日 → 整理日。
- 例行小幅命中/失準只做短段，不升級整篇為驗收日。

可用詞彙必須符合主軸：
- 驗收日（僅 lead）：短線節奏命中/失準、必要時一句「趨勢仍待 2～4 週驗證」；勿連日重複「驗收現場」。
- 異常日：背離、反直覺、換手、誰接手，但必須有數字。
- 續航日：續買、續量、守住關鍵位（偏趨勢延續語境）。
- 整理日：觀察、等待確認、訊號普通、不要追強。
- 「資金輪動」不得作為主軸；它是市場常態，只能改寫成具體買賣行為。

================================================================
STEP 4【今日唯一重要結論——觀眾明天還記得的那句話】
================================================================
整集只允許一個核心結論。必須具體、有可驗證的方向；可同時點到「{_next_watch}什麼」與「中期趨勢假說」，但不要把隔日當唯一勝敗。
例如：「今天的關鍵不在台積電漲多少，而在成交量前3名是面板和衛星股，
但法人偏偏在賣這些——短線先看量能否續得住，趨勢上要看這兩週籌碼是否真的換邊。」

================================================================
STEP 5【{_next_observe}焦點——節奏 + 趨勢兩層，嚴格門檻】
================================================================
優先從「本地量化訊號包」的明日觀察候選中挑選；若候選為空，原則上輸出「今日數據無安全明牌，現金為王，靜待訊號。」
推薦1支（最多2支）值得追蹤標的，需符合至少一項：
- 外資連續多日買超（不只今日單日）
- 成交量異常放大且爆量突破（vol_ratio > 2x）
- 跌深有籌碼保護（外資買超 + 技術支撐）

每檔必須用條列寫清（缺一不可）：
- 為何點名（籌碼／量價／事件，要有數字）
- 隔日節奏條件（{_next_watch}什麼）
- 趨勢成立條件（約 20 交易日／2～4 週怎樣算假說成立；口語可稱「趨勢」）
- 失效條件（何時降級觀察）

禁止推薦台積電（2330）、鴻海（2317）、聯發科（2454）等超級權值股。
若無符合標的：直接說「今日數據無安全明牌，現金為王，靜待訊號。」
若有推薦：給出停損點（例如：「收盤跌破 MA20 出場」）。

================================================================
STEP 6【技術位與商品——簡短補充，不是主角】
================================================================
商品每行一句，必須加「可能反映了...，需後續驗證」。
技術位：哪支股票今天剛突破或跌破 MA20？為什麼重要？

================================================================
【輸出格式——Markdown，繁體中文，正文數字加中文讀音括號，表格內不需要】
================================================================

{validation_output_block}
## 〔日曆約束〕（解讀用，不是標題鉤子）
（3～6 句：結算／除息／已公布指數／尚未公布的 FOMC。用本機日曆事實，不要發明日期。）

---

## 〔今日主軸〕[必須標明本地主軸：{frame_hint}]
（1-2段，150字以內；不要因有驗收就改寫成驗收日，除非 placement=lead）

---

## 〔相似盤對照〕
（有本機相似盤：每個歷史日 2～4 句，含當時預測與隔日節奏。無資料：寫「本日無足夠相似歷史盤可對照。」）

---

## 〔成交量排名〕今日最真實的市場投票
| 排名 | 代號 | 名稱 | 成交量 | 漲跌幅 | 今日角色 |
（前10名，最後欄說明它在今日故事中的意義）

---

## 〔法人籌碼〕外資買賣超（資料日期：XXXX-XX-XX）
（前10買超 + 前10賣超，標注估計金額）

---

## 〔量價對比〕法人說的 vs 市場做的
（成交量排名 vs 外資買超的一致或矛盾——這是今集最有料的段落）

---

## 〔主力買賣邏輯〕三大法人 vs 成交量證據鏈
（用具體榜單與數字說明：外資買超/賣超、成交量排名、技術位。必須包含「主力操作假說」：隔日節奏條件 + 趨勢延續條件（約 20 交易日／2～4 週）+ 反證/失效。不要寫抽象口號。）

---

## 〔技術位快報〕誰剛突破、誰剛失守 MA20
（每項最多一行，商品加「可能反映了...，需驗證」）

---

## 〔今日唯一重要結論〕
（100字以內；可含短線節奏，但勿把隔日當唯一勝敗）

---

## 〔{_next_observe}焦點〕節奏 + 趨勢
（每檔：為何點名｜隔日節奏｜趨勢成立（≈20 交易日，可稱趨勢）｜失效條件；或「今日無安全明牌，現金為王。」）

---

## 〔操作建議〕
- **穩健型**：...
- **波段型**：...
- **防禦股驗證**：找跌幅 < -0.5% 且 vol_ratio < 1.2x 的個股。找不到就說「今日無防禦角落，現金為王。」

---

語氣：像一個真正在查案的人。好奇、謹慎、偶爾有「我看懂了！」的興奮感。
不知道的地方直接說不知道——這比假裝知道更有說服力，也更讓人信任。
        """


        return self._call_gemini_with_retry(prompt)


    def generate_weekend_special_report(self, market_data, news_data, commodity_data=None, macro_events=None):
        """
        Generates a Weekend Special Markdown report focusing on the US Market and Geopolitical events.
        Crucially, this report is generated entirely in TRADITIONAL CHINESE.
        """
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        
        # === US Stock Data Summary (Weekly View) ===
        data_summary = ""
        for symbol, data in market_data.items():
            if data:
                line = f"- {symbol}: 本週收盤 ${data.get('week_close', data.get('price'))}, 本週漲跌 {data.get('week_change', 'N/A')} ({data.get('week_pct_change', 'N/A')}%), 本週高點 ${data.get('week_high', 'N/A')}, 本週低點 ${data.get('week_low', 'N/A')}"
                if 'daily_series' in data and data['daily_series']:
                    friday = data['daily_series'][-1]
                    line += f"  [週五單日表現: 價格 ${friday['close']}, 跌幅 {friday['pct_change']}%]"
                data_summary += line + "\n"
            else:
                data_summary += f"- {symbol}: Data unavailable\n"
                
        # === News Summary ===
        news_summary = ""
        for item in news_data:
            news_summary += f"- {item['title']} ({item['url']})\n"
        
        # === Commodity Data ===
        commodity_summary = ""
        if commodity_data:
            commodity_summary = "\n# Global Commodities & Safe Havens\n"
            for sym, cdata in commodity_data.items():
                commodity_summary += f"- {cdata['name']}: ${cdata['price']} ({cdata['pct_change']:+.2f}%)\n"
        
        # === Macro Events ===
        macro_summary = ""
        if macro_events:
            macro_summary = "\n# Macro Events & Geopolitics\n"
            for item in macro_events:
                macro_summary += f"- {item['title']} ({item['url']})\n"
                
        prompt = f"""
        You are an elite Wall Street macro analyst and geopolitical strategist.
        Create a "Weekend Special" financial report for {date_str}.
        CRITICAL: The entire report MUST BE WRITTEN IN TRADITIONAL CHINESE (繁體中文).
        
        # US Indices & Key Stock Data (Weekly Performance + Friday Action)
        {data_summary}
        
        # Global News (Geopolitics & Market Drops)
        {news_summary}
        {commodity_summary}
        # Macro & Geopolitical Events
        {macro_summary}
        
        # Report Requirements (Elite Macro Perspective)
        
        1. **一週總結與週五血洗 (The Weekly Story & Friday Bloodbath)**: 開篇以總體角度回顧本「整週」美股的情境變化（參考 S&P 500 ^GSPC 與 Nasdaq ^IXIC 的全週漲跌幅），並將焦點強力收束在地緣政治爆發導致的「週五大拋售」。分析恐慌指數 VIX 的飆升。不要只寫週五，要寫出「一週總結+週五恐慌拋售」的層次感。
        
        2. **戰火陰霾 (The War Narrative & Safe Havens)**: 將股市下跌與提供的地緣政治/戰爭新聞強烈連結。
           - 點出避險資產（黃金、原油、白銀）的異動，資金是否正在逃命？
           - 帶入歷史借鑑。這跟 1991 波灣戰爭或 2022 烏俄戰爭有何異同？提及「買在砲聲隆隆時」的華爾街概念。
           
        3. **科技股重災區 (Tech Damage)**: 針對輝達 (NVDA) 與台積電 ADR (TSM) 的全週跌幅與週五重摔做深度點評。為何最賺錢的公司最先被提款？（解釋流動性變現與去槓桿的邏輯）。
        
        4. **台股週一生存指南 (Monday Survival Guide)**: 
           - 基於美股大逃殺與戰火敘事，預判下週一台股開盤的「心理面與結構面」衝擊。
           - 哪些台股族群是絕對的「重災區」（如高估值 AI 硬體）？
           - 哪些又是資金可能的「避風港」（如防禦型、內需、原物料）？
           
        5. **週末實戰策略 (Weekend Strategy Checklist)**: 結合以上分析，給出兩套具體的週末沉澱與下週應對策略：
           - **防禦姿態 (The Defensive Posture)**: 資金該往哪裡躲？
           - **禿鷹戰法 (The Vulture Strategy)**: 觀察哪些明確指標，才知道恐慌殺盤已竭盡，可以開始大口咬進優質錯殺股？
           
        Please generate the comprehensive Traditional Chinese Markdown report now.
        """
        
        return self._call_gemini_with_retry(prompt)
