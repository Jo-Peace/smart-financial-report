import os
import time
import json
from modules.gemini_client import default_model_name, generate_text
from modules.tz import today_tpe_str
from modules.twse_tape import EX_RIGHTS_TEXT, pct_text
from modules.tickers import bare_ticker


def is_report_error(content) -> bool:
    """True for the "Error generating report: ..." string (or an empty answer)."""
    return not isinstance(content, str) or not content.strip() or content.strip().startswith("Error generating")


def abort_on_generation_error(content, label="報告"):
    """
    Entry scripts (weekly / US / weekend special) call this before writing:
    a Gemini failure must exit non-zero instead of being saved as a report
    and fed into further Gemini extraction.
    """
    if not is_report_error(content):
        return
    first = content.strip().splitlines()[0] if isinstance(content, str) and content.strip() else "（空白回應）"
    print(f"\n❌ {label}生成失敗，停止流程以避免把錯誤字串寫成正式報告。")
    print(f"   錯誤摘要: {first[:300]}")
    raise SystemExit(1)


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


    def _call_gemini_with_retry(self, prompt, max_retries=3, validate=None):
        """
        Calls Gemini with exponential backoff retry using tenacity.

        ``validate(text)`` may return a list of problems (e.g. missing report
        sections). A non-empty list raises a retryable GeminiIncompleteResponse,
        so a truncated / malformed report is retried and, if it never completes,
        returned as "Error generating report: ..." (callers stop before persist).
        """
        from modules.api_retry import get_api_retry_decorator
        from modules.gemini_client import GeminiIncompleteResponse, RetryPlan, unwrap_retry_error

        # One plan per report: the fallback model waits for the last attempt
        # (or a truncated answer), and a second MAX_TOKENS answer stops the retry.
        plan = RetryPlan(max_retries + 1)

        @get_api_retry_decorator(max_attempts=max_retries + 1, min_wait=5, max_wait=60)
        def _do_call():
            use_fallback = plan.start()
            try:
                text = generate_text(
                    self.api_key,
                    prompt,
                    task_name="market_report",
                    model_name=self.model_name,
                    use_fallback=use_fallback,
                )
            except Exception as exc:
                plan.failed(exc)
                raise
            problems = validate(text) if validate else []
            if problems:
                raise GeminiIncompleteResponse(
                    f"report incomplete: {'; '.join(problems)} (chars={len(text or '')})",
                    reason="MISSING_SECTIONS",
                    retryable=True,
                )
            return text

        try:
            return _do_call()
        except Exception as e:
            # tenacity wraps the final failure in RetryError; report the real cause.
            return f"Error generating report: {unwrap_retry_error(e)}"

    @staticmethod
    def _daily_report_problems(text, next_observe="明日觀察"):
        """Missing core sections → problems list (env REPORT_SECTION_CHECK=warn|off relaxes it)."""
        from modules.report_fact_check import missing_required_sections

        mode = (os.getenv("REPORT_SECTION_CHECK") or "strict").strip().lower()
        if mode == "off":
            return []
        missing = missing_required_sections(text)
        if not missing:
            return []
        labels = [f"〔{next_observe}焦點〕" if key == "觀察焦點" else f"〔{key}〕" for key in missing]
        if mode == "warn":
            print(f"  ⚠️  日報缺少章節：{'、'.join(labels)}（REPORT_SECTION_CHECK=warn，仍繼續）")
            return []
        return [f"缺少章節 {'、'.join(labels)}（可設 REPORT_SECTION_CHECK=warn 暫時放行）"]

    def generate_report(self, market_data, news_data, institutional_data=None, prev_report_path=None, commodity_data=None, macro_events=None, tech_catalyst_events=None, volume_data=None, deep_dive_data=None, signal_summary=None, previous_prediction_targets=None, previous_validation_summary=None, editorial_frame=None, similar_sessions_summary=None, last_output_date=None, value_ranking=None, report_date=None):
        """
        Generates a Markdown report using Gemini with enhanced data.

        ``report_date`` (YYYY-MM-DD) is the session the report is filed under
        (main.py passes run_date, which a back-fill relabels to the data
        session); it drives the 日期 header and 昨日／明天 wording. Defaults to
        today in Asia/Taipei. ``value_ranking`` (get_top_value_stocks) is the
        primary 量 axis; ``volume_data`` (MI_INDEX20, shares) is a side table.
        """
        date_str = str(report_date)[:10] if report_date else today_tpe_str()
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
        # 時間尺度分工（中性用語）：1D～5D＝短線節奏；約 20 交易日＝中期脈絡。
        # 哪個窗「較準」只能由 horizon_reliability 的 CI 門檻決定（見
        # docs/optimization_backlog.md Standing Decisions），日報 prompt 不預設任何窗為成敗標準。
        horizon_ruler_block = """================================================================
【時間尺度——寫作用語】
================================================================
- 隔日／近幾日（1D～5D）：短線節奏／天氣。可寫命中或失準，但禁止用它單獨論定「系統準不準」或整篇升級成史詩驗收日。
- 中期（約 20 個交易日／2～4 週）：中期脈絡。可寫點名標的的中期走勢與假說後續要看的條件，但不要把任一時間窗（包括 20 日）寫成判定成敗的標準或「較準的窗」。
- 口語可用「趨勢」指中期窗；不要暗示在做技術分析趨勢線神單。
- 若文中提到本機多日回測：用「目前樣本顯示…」的語氣，勿寫成永遠真理；本機回測尚未以統計證實哪個時間窗較準，也不要把隔日失準說成「只是雜訊」。
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
        def _na(value):
            return "N/A" if value is None else value

        for symbol, data in market_data.items():
            if data:
                bar = f" @ {data['bar_date']}" if data.get('bar_date') else ""
                if data.get('pct_change') is not None:
                    move = f"漲跌 {data['change']} ({data['pct_change']}%)"
                elif data.get('ex_rights'):
                    move = f"漲跌 {EX_RIGHTS_TEXT}（除權息日收盤不能和前一日收盤直接相比，禁止寫漲跌幅）"
                else:
                    move = "漲跌 N/A"
                line = f"- {symbol}{bar}: 價格 {data['price']}, {move}, 成交量 {_na(data.get('volume'))} (5日均量 {_na(data.get('avg_vol_5d'))}, 量增比 {_na(data.get('vol_ratio'))}{'x' if data.get('vol_ratio') is not None else ''})"
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
            scope_label = "上市＋上櫃（FinMind 備援）" if institutional_data.get("market_scope") == "TWSE_TPEx" else "上市（TWSE，不含上櫃）"
            inst_summary = f"\n# 三大法人買賣超｜{scope_label}｜依同日收盤估算金額排序 - 資料日期：{data_date}\n"
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

        # === 成交值排行（主軸：資金實際流向，上市個股；ETF 另列）===
        value_summary = ""
        value_stocks = [r for r in ((value_ranking or {}).get("stocks") or []) if r.get("trade_value_yi") is not None]
        if value_stocks:
            value_date = (value_ranking or {}).get("data_date") or "未知"
            value_summary = f"\n# 💰 今日成交值前20名（依成交金額排序，上市個股，不含 ETF）- 資料日期：{value_date}\n"
            for s in value_stocks[:20]:
                pct_str = pct_text(s)
                value_summary += (
                    f"- 第{s.get('rank')}名: {s['id']} {s.get('name', '')} | 成交值 {s['trade_value_yi']:.1f}億"
                    f" | 收盤 {s.get('close_price', 'N/A')} | 漲跌 {pct_str}\n"
                )
            etfs = [r for r in ((value_ranking or {}).get("etfs") or []) if r.get("trade_value_yi") is not None]
            if etfs:
                value_summary += "ETF 成交值前段（另列，不當個股主角）：" + "、".join(
                    f"{r.get('name') or r.get('id')} {r['trade_value_yi']:.0f}億" for r in etfs[:5]
                ) + "\n"

        # === 成交股數排行（副表：MI_INDEX20，股數排序）===
        volume_summary = ""
        if volume_data:
            volume_summary = "\n# 📊 今日成交股數前20名（副表：依「股數」排序，上市；ETF／低價股易上榜）\n"
            volume_summary += "⚠️ 股數排名不是資金焦點，也不是市場共識：高價權值股不在此榜不代表「市場沒跟量」。資金焦點以上方成交值（億元）排行為準。\n"
            for s in volume_data[:20]:
                value = ""
                if s.get('trade_value_yi') is not None:
                    if s.get('value_rank'):
                        value_rank = f"個股成交值第{s['value_rank']}名，不含 ETF，與上方成交值榜同一排名"
                    elif s.get('value_rank_etf'):
                        value_rank = f"ETF，ETF 成交值第{s['value_rank_etf']}名，不列入個股成交值排名"
                    else:
                        value_rank = "成交值名次未知"
                    value = f" | 成交值 {s['trade_value_yi']:.1f}億（{value_rank}）"
                volume_summary += f"- 第{s['rank']}名: {s['id']} {s['name']} | 成交量 {s['volume']:,} 股{value} | 收盤 {s.get('close_price', 'N/A')} | 漲跌 {pct_text(s)}\n"

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
                from modules.narrative_sanitize import sanitize_narrative_text, strip_number_readings

                with open(prev_report_path, "r", encoding="utf-8") as f:
                    prev_content = f.read()
                # Older reports carry LLM number readings / intent wording; strip them
                # so the 3000-char window holds verifiable content.
                prev_content = sanitize_narrative_text(strip_number_readings(prev_content))
                hist_section = f"\n# 前日報告（供昨日預測驗收用）\n{prev_content[:3000]}\n"
            except Exception:
                hist_section = ""
            
        # === Dynamic Stock Database (Lazy Loading) ===
        # Extract active tickers from today's data to avoid bloating the prompt
        active_tickers = set()
        
        # from market_data keys e.g. "2330.TW"
        for symbol in market_data.keys():
            active_tickers.add(bare_ticker(symbol))
            
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
                
        # Unreviewed (Gemini draft / legacy) entries are reference-only; see stock_db_updater.
        from modules.stock_db_updater import format_stock_db_for_prompt

        stock_db_str = format_stock_db_for_prompt(self.stock_db, active_tickers)
            
        # === Second Brain: Historical Knowledge Wiki (per-file budget, whole entries, fresh digest only) ===
        from modules.knowledge_manager import build_wiki_prompt_context

        wiki_str = build_wiki_prompt_context(report_date=date_str, max_chars=3500)

        prompt = f"""
你是一個台股財金節目的「首席偵探型分析師」。
你的工作不是寫報告。是找到今天「最讓人意外的數據現象」，然後用說故事的方式把它說得讓人無法跳過。

日期：{date_str}（繁體中文；數字一律用阿拉伯數字，不要加中文讀音括號）

================================================================
【原始數據輸入——這些是事實，禁止在事實之外捏造】
================================================================

## 今日成交值前20名（資金實際流向——量的主軸）
{value_summary if value_summary else '（成交值排行未能取得：只能參考下方成交股數副表，且不得把「未進股數榜」解讀為沒跟量或市場不認同）'}

## 今日成交股數前20名（副表，只看股數；不是資金焦點）
{volume_summary if volume_summary else '（成交股數排行未能取得）'}

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
- 如果本地訊號包的信心等級為「弱」（等級皆未經回測校準），請降低語氣，寫「觀察」而不是「預測」；「中」也只能寫成待確認假說。

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
這些是過去幾個交易日的本機紀錄（法人買賣金額、大盤漲跌、驗收結果）。
請把它們當成「待驗證假說」，不是既有事實：
- 只有今日數據（金額、排名、漲跌）重新支持時，才可以點出延續性，並寫出今日支持它的數字。
- 若今日數據不支持或反轉，直接寫「假說未獲今日數據支持」，這可以是今日的題材。
- 不得沿用任何意圖推論（鎖貨、吸籌、掃貨、主力布局等）；記憶庫只提供數字與日期。
{wiki_str}
{editorial_rule}
{validation_step_block}
================================================================
STEP 0.5【相似盤對照】本機已比對的歷史日，不是回憶
================================================================
上方「本機相似盤」若列出日期（最多 5 個）：必須寫「〔相似盤對照〕」短段，先引用「彙總」的隔日預測成立／失準與隔日大盤，再挑 1～2 個最相近的日期各寫 1～2 句（當天買賣超與隔日節奏命中/失敗）。
選今日 prediction_targets 時，只有至少 3 個相似盤、且彙總顯示隔日預測多數失準，才可據此降權或寫明為何今天仍押；1～2 個個案或標示「樣本不足」時只能當背景對照，不可據此降權。
若寫「未找到足夠相似盤」：正文只寫「本日無足夠相似歷史盤可對照。」一句。禁止編造沒列到的日期，禁止用「史上第一次」迴避對照。
相似盤是輔助，不能蓋過今日成交值與法人榜的主線。

================================================================
STEP 1【找出今日的最強異常 / 主線】整集的靈魂，只選一個現象
================================================================
優先使用「本地量化訊號包」與「本地主軸分類」。除非原始數據明顯反駁，否則不要自行改選主題。
從以下類型找出最反直覺、最有衝突感的數據現象（若主軸是整理日，就誠實寫訊號普通）：
A) 量價矛盾：大成交量但股價小漲（分配訊號）？縮量但大漲（假突破）？
B) 法人 vs 市場矛盾：外資大買的股票，成交值排名卻在20名以外？（看成交值，不看成交股數）
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
- 法人籌碼：今日外資買超 XX 億（上市第 X 名）
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
「外資今天賣超台積電 200 億，但台積電仍是成交值第一名。接手的是誰？」
「大盤漲了X點，但成交值冠軍是一支下跌的股票。誰在逆勢操作？」
「昨天我們說 XXX 會漲，但它今天跌了。判斷哪裡出了問題？」

必須：(1) 基於真實數據 (2) 有內在衝突或懸念 (3) 讓人想知道答案

================================================================
【台股供應鏈族群知識庫——必須熟記，不得用產品名稱亂分類】
================================================================
以下是本機 stock_db 的產業參考。標示「未審核」的條目是 AI 草稿，只能當參考：不得據此把公司寫成 AI 供應鏈，也不得改稱其本業。

{stock_db_str}

📌 族群分類原則：
以本業（證交所產業別）為主；只有在資料源或「已審核」條目有營收比重／法說會等依據時，才可加註「AI 相關」。
航運、塑化、面板、水泥等本業不得因題材被改寫成 AI 族群。

================================================================
STEP 3【偵探式展開——成交值帶路，法人對比，新聞輔助】
================================================================
順序：
1. 成交值前10名（資金實際流向；ETF 另列）——成交股數榜只當副表，用來看散戶／低價股熱度
2. 外資買賣超對比（機構的官方說法）
3. 兩者一致？→ 趨勢確認。兩者矛盾？→ 這才是最值得挖掘的地方
4. 新聞事件作補充（只是「可能原因之一」，不是確定答案）
5. 族群分析（只在有3支以上個股同向時，才能下「族群性」結論）
6. 【參考上方知識庫】：已審核條目可沿用其分類；未審核條目只作參考，族群判斷以本業為準

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
9. 禁止把「買超但未進成交量前20」寫成鎖籌碼、靜默入侵、影子建倉、機構級掃貨。只能寫可驗證事實：金額、成交值名次、連續買超天數。成交股數榜以股數排序，高價股天生上不了榜，「未進股數榜」不是任何訊號。連續買超未滿 3 日、或當日已賣超，不得推論意圖。
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
例如：「今天的關鍵不在台積電漲多少，而在成交值前3名有兩檔是面板和衛星股，
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
【輸出格式——Markdown，繁體中文，數字一律用阿拉伯數字，不要加中文讀音括號】
================================================================

{validation_output_block}
## 〔日曆約束〕（解讀用，不是標題鉤子）
（3～6 句：結算／除息／已公布指數／尚未公布的 FOMC。用本機日曆事實，不要發明日期。）

---

## 〔今日主軸〕[必須標明本地主軸：{frame_hint}]
（1-2段，150字以內；不要因有驗收就改寫成驗收日，除非 placement=lead）

---

## 〔相似盤對照〕
（有本機相似盤：先寫彙總（成立／失準、隔日大盤），再挑 1～2 個最相近日期各 1～2 句，含當時預測與隔日節奏。無資料：寫「本日無足夠相似歷史盤可對照。」）

---

## 〔成交值排名〕今日資金實際流向
| 排名 | 代號 | 名稱 | 成交值（億） | 漲跌幅 | 今日角色 |
（成交值前10名，上市個股；ETF 另列一行即可；最後欄說明它在今日故事中的意義。成交值未取得時改列成交股數榜，並在表頭標明「依股數」。排名／成交值／漲跌幅會由本機 TWSE 資料覆寫，請把力氣放在「今日角色」。）

---

## 〔法人籌碼〕外資買賣超（資料日期：XXXX-XX-XX）
（前10買超 + 前10賣超，每檔一行「N. **代號 名稱**：約 ±X 億元（一句註記）」；名次與金額會由本機資料覆寫，「最主要／第一」這類說法必須和名次一致。）

---

## 〔量價對比〕法人說的 vs 市場做的
（成交值排名 vs 外資買超的一致或矛盾——這是今集最有料的段落；不要拿成交股數名次和億元金額硬比）

---

## 〔主力買賣邏輯〕三大法人 vs 成交值證據鏈
（用具體榜單與數字說明：外資買超/賣超、成交值排名、技術位。必須包含「主力操作假說」：隔日節奏條件 + 趨勢延續條件（約 20 交易日／2～4 週）+ 反證/失效。不要寫抽象口號。）

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


        return self._call_gemini_with_retry(
            prompt,
            validate=lambda text: self._daily_report_problems(text, next_observe=_next_observe),
        )


    def generate_weekend_special_report(self, market_data, news_data, commodity_data=None, macro_events=None):
        """
        Generates a Weekend Special Markdown report focusing on the US Market and Geopolitical events.
        Crucially, this report is generated entirely in TRADITIONAL CHINESE.
        """
        date_str = today_tpe_str()

        # === US Stock Data Summary (Weekly View) ===
        data_summary = ""
        for symbol, data in market_data.items():
            if data:
                week_span = f"{data.get('week_start')}~{data.get('week_end')}" if data.get('week_start') else "本週"
                line = f"- {symbol}（{week_span}）: 週收盤 ${data.get('week_close', data.get('price'))}, 週漲跌 {data.get('week_change', 'N/A')} ({data.get('week_pct_change', 'N/A')}%), 週高點 ${data.get('week_high', 'N/A')}, 週低點 ${data.get('week_low', 'N/A')}"
                if 'daily_series' in data and data['daily_series']:
                    last_bar = data['daily_series'][-1]
                    last_label = last_bar.get('date') or last_bar.get('iso_date') or '最後交易日'
                    line += f"  [最後交易日 {last_label} 單日: 價格 ${last_bar['close']}, 漲跌幅 {last_bar['pct_change']:+}%]"
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
            commodity_summary = "\n# Global Commodities & Safe Havens（以下為「1日」漲跌，不是週漲跌）\n"
            for sym, cdata in commodity_data.items():
                commodity_summary += f"- {cdata['name']}: ${cdata['price']} (1日 {cdata['pct_change']:+.2f}%，報價日 {cdata.get('bar_date') or '不明'})\n"
        
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
