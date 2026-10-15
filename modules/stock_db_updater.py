import datetime
import os
import json
import time
from modules.atomic_io import atomic_write_json, backup_file_daily
from modules.gemini_client import default_model_name, generate_text
from modules.tickers import bare_ticker

# Fields a Gemini draft must carry before it may enter stock_db.json.
DRAFT_REQUIRED_FIELDS = ("name", "sector", "supply_chain_role")
DRAFT_SOURCE = "gemini_draft"


def _clean_sid(value):
    return bare_ticker(value)


def _nullable_text(value):
    text = str(value or "").strip()
    if not text or text.lower() in {"null", "none", "n/a"} or text in {"無", "無明確依據", "無公開依據"}:
        return None
    return text


def is_reviewed_entry(info) -> bool:
    """Only entries a human explicitly marked ``reviewed: true`` count as reviewed."""
    return isinstance(info, dict) and info.get("reviewed") is True


def build_draft_entries(new_data, requested_stocks, existing_db, today=None):
    """
    Validate a Gemini stock_db answer before merging.

    - only ids that were actually requested this run are accepted
    - an id that already exists in stock_db is never overwritten
    - malformed entries are rejected
    - accepted entries are tagged ``source=gemini_draft, reviewed=False`` and
      ``ai_connection`` is dropped unless Gemini cited evidence (``ai_evidence``)

    Returns (accepted: dict, rejected: list[(sid, reason)]).
    """
    today = today or datetime.date.today().isoformat()
    requested = {}
    for stock in requested_stocks or []:
        sid = _clean_sid(stock.get("id"))
        if sid:
            requested[sid] = str(stock.get("name") or "").strip()
    existing_db = existing_db or {}

    accepted, rejected = {}, []
    if not isinstance(new_data, dict):
        return accepted, [("*", "Gemini 回傳不是 JSON 物件")]

    for raw_sid, entry in new_data.items():
        sid = _clean_sid(raw_sid)
        if sid not in requested:
            rejected.append((sid, "非本次查詢的股票"))
            continue
        if sid in existing_db:
            rejected.append((sid, "既有條目不覆寫"))
            continue
        if not isinstance(entry, dict) or any(not str(entry.get(k) or "").strip() for k in DRAFT_REQUIRED_FIELDS if k != "name"):
            rejected.append((sid, "欄位不完整"))
            continue
        ai_evidence = _nullable_text(entry.get("ai_evidence"))
        ai_connection = _nullable_text(entry.get("ai_connection")) if ai_evidence else None
        products = entry.get("key_products") or []
        if not isinstance(products, list):
            products = [str(products)]
        accepted[sid] = {
            "name": str(entry.get("name") or requested.get(sid) or "").strip() or requested.get(sid) or sid,
            "twse_industry": _nullable_text(entry.get("twse_industry")),
            "sector": str(entry.get("sector")).strip(),
            "supply_chain_role": str(entry.get("supply_chain_role")).strip(),
            "key_products": [str(p).strip() for p in products if str(p).strip()][:6],
            "ai_connection": ai_connection,
            "ai_evidence": ai_evidence,
            "not_classify_as": [],
            "source": DRAFT_SOURCE,
            "reviewed": False,
            "created_at": today,
        }
    return accepted, rejected


def format_stock_db_for_prompt(stock_db, active_tickers) -> str:
    """
    Render stock_db entries for today's active tickers as *reference* context.

    Unreviewed entries (every Gemini draft and any legacy entry without
    ``reviewed: true``) are labelled 未審核 and never produce 「禁止分類為」 rules.
    """
    if not stock_db:
        return "（目前資料庫為空，請依常理判斷）"
    active = {_clean_sid(t) for t in (active_tickers or []) if _clean_sid(t)}
    lines = []
    for sid in sorted(active):
        info = stock_db.get(sid)
        if not isinstance(info, dict):
            continue
        name = info.get("name", "")
        sector = info.get("sector", "")
        role = info.get("supply_chain_role", "")
        industry = info.get("twse_industry")
        if is_reviewed_entry(info):
            lines.append(f"- {sid} {name}：分類「{sector}」（已審核）")
            if role:
                lines.append(f"  └→ 角色：{role}")
            not_class = "、".join(str(x) for x in (info.get("not_classify_as") or []) if str(x).strip())
            if not_class:
                lines.append(f"  └→ 避免稱為：{not_class}")
        else:
            label = f"本業「{industry}」；" if industry else ""
            lines.append(f"- {sid} {name}：{label}參考分類（未審核）「{sector}」")
            if role:
                lines.append(f"  └→ 角色描述（未審核，不可據此改寫本業）：{role}")
            if info.get("ai_connection") and info.get("ai_evidence"):
                lines.append(f"  └→ AI 關聯（未審核，引用時需附依據）：{info.get('ai_evidence')}")
    if not lines:
        return "（今日主力個股尚無特殊之自訂分類，請依常理判斷）"
    return "\n".join(lines) + "\n"


class StockDatabaseUpdater:
    # Class defaults so tools / tests that build the updater without __init__
    # (StockDatabaseUpdater.__new__ + db_path / stock_db) can still save.
    load_error = None
    backup_dir = None

    def __init__(self, api_key, db_path=None):
        self.api_key = api_key
        # 預設使用 Flash，因為做分類標籤速度快且成本低
        self.model_name = default_model_name()

        self.db_path = db_path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "stock_db.json")
        self.backup_dir = os.path.join(os.path.dirname(self.db_path), "backup")
        self.load_error = None
        self.stock_db = self._load_stock_db()

    def _load_stock_db(self):
        if not os.path.exists(self.db_path):
            return {}
        try:
            with open(self.db_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError(f"stock_db 頂層不是物件（{type(data).__name__}）")
            return data
        except Exception as e:
            # Never return {} silently: a later save would wipe the whole DB.
            self.load_error = str(e)
            print(f"[StockDB Updater] ⚠️ 資料庫無法解析，本次只讀不寫（避免覆蓋既有 DB）: {e}")
            return {}

    def _save_stock_db(self):
        if self.load_error:
            print(f"  [Auto-DB] ⚠️ 載入失敗（{self.load_error}），拒絕寫入 stock_db.json 以免覆蓋。")
            return False
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        backup_dir = self.backup_dir or os.path.join(os.path.dirname(self.db_path), "backup")
        backup_file_daily(self.db_path, backup_dir=backup_dir)
        atomic_write_json(self.db_path, self.stock_db)
        return True

    def update_new_stocks(self, stock_list):
        """
        stock_list: list of dicts [{'id': '2330', 'name': '台積電'}, ...]
        Checks if they exist in DB. If not, batch queries Gemini and adds
        *unreviewed drafts* for the requested ids only (existing entries are never overwritten).
        """
        if self.load_error:
            print("  [Auto-DB] stock_db 載入失敗，略過新股建檔。")
            return
        missing_stocks = []
        for stock in stock_list:
            if not stock.get('id'):
                continue
            sid = _clean_sid(stock['id'])
            if sid not in self.stock_db and sid.isdigit():
                # Avoid duplicates in missing list
                if not any(s['id'] == sid for s in missing_stocks):
                    missing_stocks.append({"id": sid, "name": stock.get('name', '未知')})

        if not missing_stocks:
            print("  [Auto-DB] 本次列入清單的股票皆已存在於知識庫中。")
            return

        print(f"  [Auto-DB] 發現 {len(missing_stocks)} 檔未知新股票，建立待審核草稿: {[s['id'] + ' ' + s['name'] for s in missing_stocks]}")

        new_data = self._query_gemini_for_stocks(missing_stocks)
        if not new_data:
            print("  [Auto-DB] AI 調查失敗，本次未新增資料。")
            return
        accepted, rejected = build_draft_entries(new_data, missing_stocks, self.stock_db)
        for sid, reason in rejected:
            print(f"  [Auto-DB] 略過 {sid}：{reason}")
        if not accepted:
            print("  [Auto-DB] 沒有可寫入的新條目。")
            return
        self.stock_db.update(accepted)
        if self._save_stock_db():
            print(f"  [Auto-DB] 已為 {len(accepted)} 檔股票建立草稿（reviewed=false，待人工審核）。")

    def _refresh_foreign_streaks(self, institutional_data, today_str, streaks=None):
        """
        Store each listed stock's 外資連續買超天數 counted in TWSE sessions
        (modules.foreign_streak: today's list + institutional_flows before the
        data date). Keyed by the data date, never date.today(), so Mondays,
        post-holiday sessions and same-day reruns keep the real count.
        """
        if not institutional_data:
            return False
        from modules.foreign_streak import compute_foreign_buy_streaks

        if streaks is None:
            streaks = compute_foreign_buy_streaks(institutional_data, today_str)
        changed = False
        for sid, info in (streaks or {}).items():
            if sid not in self.stock_db:
                continue
            entry = self.stock_db[sid]
            tracking = entry.get("tracking") or _empty_tracking()
            days = int(info.get("days") or 0)
            data_date = str(info.get("data_date") or today_str)[:10]
            before = (
                tracking.get("consecutive_foreign_buy_days"),
                tracking.get("last_foreign_buy_date"),
                tracking.get("foreign_buy_streak_date"),
                tracking.get("foreign_buy_streak_complete"),
            )
            tracking["consecutive_foreign_buy_days"] = days
            # The date the count is valid for (readers check freshness against it).
            tracking["last_foreign_buy_date"] = data_date if days > 0 else tracking.get("last_foreign_buy_date")
            tracking["foreign_buy_streak_date"] = data_date
            tracking["foreign_buy_streak_complete"] = bool(info.get("complete"))
            after = (
                tracking.get("consecutive_foreign_buy_days"),
                tracking.get("last_foreign_buy_date"),
                tracking.get("foreign_buy_streak_date"),
                tracking.get("foreign_buy_streak_complete"),
            )
            changed = changed or before != after
            entry["tracking"] = tracking
            self.stock_db[sid] = entry
        return changed

    def refresh_foreign_streaks(self, institutional_data, as_of_date=None, streaks=None):
        """
        Every /1 (independent of yesterday_predictions): refresh the session-based
        streaks and save when anything changed. Idempotent on reruns.
        """
        if self.load_error or not institutional_data:
            return False
        today_str = str(as_of_date or (institutional_data or {}).get("data_date") or "")[:10]
        if self._refresh_foreign_streaks(institutional_data, today_str, streaks=streaks):
            self._save_stock_db()
            print("  [Auto-DB] 外資連續買超天數已依交易日曆重算並寫入。")
            return True
        return False

    def update_tracking_stats(
        self,
        yesterday_predictions,
        todays_market_data,
        todays_institutional_data=None,
        *,
        as_of_date=None,
        prediction_date=None,
        validation_results=None,
    ):
        """
        [優化 D] 根據前次預測與今日實際表現，更新 stock_db 中的動態績效 tracking 欄位。

        Idempotent: one history entry per (prediction_date, sid) — or, when the
        prediction date is unknown, per (validation date, sid, direction, trigger).
        A same-day rerun replaces that entry and its counter contribution instead
        of counting it again.

        Verdicts are the on-air 驗收 rows (prediction_validator
        .validate_prediction_targets → classify_hit for 1 session: excess vs
        ^TWII outside the dead band, a close through a valid stop is a miss,
        a stale bar is not judged). Neutral / unjudgeable / raw-fallback results
        are kept in history but not counted in prediction_count (the hit-rate
        denominator), as in the ledger.

        Args:
            yesterday_predictions: list of dicts from the previous structured_data["prediction_targets"]
            todays_market_data: dict from data_fetcher, keyed by "XXXX.TW" (incl. "^TWII")
            todays_institutional_data: optional, the institutional data dict with top_buy/top_sell lists
            as_of_date: validation (run) date, default today
            prediction_date: date of the structured_data the predictions came from
            validation_results: today's validate_prediction_targets rows (rebuilt when omitted)
        """
        today_str = str(as_of_date or __import__('datetime').date.today())[:10]
        prediction_date = str(prediction_date)[:10] if prediction_date else None
        needs_save = False
        # 外資連續買超天數 is no longer refreshed here: refresh_foreign_streaks runs on
        # every /1 (pipeline_analysis), independent of yesterday_predictions.
        if not yesterday_predictions:
            return

        # The on-air 驗收 rows (validate_prediction_targets) are the verdicts:
        # pipeline_analysis passes the very rows it printed; otherwise they are
        # rebuilt here with the same rule (stale bar / stop-loss / index excess).
        if validation_results is None:
            from modules.prediction_validator import validate_prediction_targets

            validation_results = validate_prediction_targets(
                yesterday_predictions, todays_market_data or {}, todays_institutional_data or {}
            )
        verdicts = {}
        for row in validation_results or []:
            sid = bare_ticker((row or {}).get('id', ''))
            if sid:
                verdicts.setdefault(sid, row)

        print(f"  [Auto-DB] 正在更新 {len(yesterday_predictions)} 檔前次預測標的的績效追蹤...")

        for pred in yesterday_predictions:
            sid = bare_ticker(pred.get('id', ''))
            if not sid:
                continue

            # Ensure stock exists in DB (may be a known stock not yet in DB)
            if sid not in self.stock_db:
                continue

            entry = self.stock_db[sid]
            tracking = entry.get('tracking') or _empty_tracking()
            row = verdicts.get(sid) or {
                'id': sid,
                'direction': pred.get('direction', '多'),
                'trigger': pred.get('trigger', ''),
                'stop_loss_desc': pred.get('stop_loss_desc', ''),
            }
            history_entry = tracking_entry_from_validation(today_str, prediction_date, row)
            replaced = apply_tracking_entry(tracking, history_entry)
            tracking['last_recommended'] = today_str
            entry['tracking'] = tracking
            self.stock_db[sid] = entry
            needs_save = True
            tag = "（同日重跑：覆寫，不重複計數）" if replaced else ""
            print(
                f"    [Tracking] {sid} {entry.get('name', '')}: {history_entry['result']}"
                f" (累計命中率: {tracking['hit_rate']}，n={tracking['prediction_count']}){tag}"
            )

        if needs_save:
            self._save_stock_db()
            print(f"  [Auto-DB] 績效追蹤更新完成並寫入資料庫。")

    def _query_gemini_for_stocks(self, missing_stocks):
        list_str = "\n".join([f"- {s['id']} {s['name']}" for s in missing_stocks])
        
        prompt = f"""
你是一名台股產業研究助理。請為以下初次進榜的台股建立「草稿」產業檔案（之後由人工審核）：

{list_str}

📋 分類準則：
1. twse_industry 與 sector 以證交所／櫃買中心官方產業別為準（例如：半導體業、電子零組件業、塑膠工業、航運業、光電業、水泥工業）。不要因為題材熱門就把本業改名。
2. supply_chain_role：一句話描述主要產品在供應鏈的位置，以本業為主。
3. ai_connection：只有在有公開可查的依據（法說會、年報、營收比重、主要客戶）時才填寫，並在 ai_evidence 寫出依據類型與出處；沒有明確依據時兩者都填 null，不得因「可能用在 AI 伺服器」就改分類。
4. 只回傳上面列出的股票代號，不要新增其他股票。

回傳格式必須是單純的 JSON，且為以下結構 (最外層就是物件，KEY為股票代號)：
{{
  "股票代號": {{
    "name": "公司名稱",
    "twse_industry": "官方產業別",
    "sector": "族群名稱（以本業為主）",
    "supply_chain_role": "供應鏈角色（本業優先）",
    "key_products": ["產品A", "產品B"],
    "ai_connection": null,
    "ai_evidence": null
  }}
}}

請確保輸出是合法且可解析的 JSON 字串，不要有 Markdown ```json 的包裝，直接以 {{ 開頭。
"""
        from modules.ai_usage_guard import GeminiUsageLimitError
        from modules.api_retry import is_retryable_exception

        # Backoff only for a retryable API error (429 / 5xx / timeout); a bad
        # JSON answer is retried at once, and a budget refusal or a blocked /
        # non-retryable answer stops here (no sleeping on a call that cannot succeed).
        wait_times = [5, 10, 20]
        for attempt in range(len(wait_times)):
            try:
                text = generate_text(
                    self.api_key,
                    prompt,
                    task_name="stock_db_classification",
                    model_name=self.model_name,
                    response_mime_type="application/json",
                ).strip()
                if text.startswith("```json"):
                    text = text[7:]
                if text.startswith("```"):
                    text = text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
                
                parsed_json = json.loads(text)
                return parsed_json
            except GeminiUsageLimitError as e:
                print(f"  [Auto-DB] 略過分類（今日 Gemini 額度保留給日報／包裝）: {e}")
                return None
            except Exception as e:
                last_attempt = attempt == len(wait_times) - 1
                if isinstance(e, json.JSONDecodeError):
                    if not last_attempt:
                        print(f"  [Auto-DB Retry] 解析 JSON 失敗重試中: {e}")
                    continue
                if not is_retryable_exception(e):
                    print(f"  [Auto-DB] 分類失敗（不重試）: {e}")
                    return None
                if not last_attempt:
                    print(f"  [Auto-DB Retry] Gemini 暫時失敗，{wait_times[attempt]} 秒後重試: {e}")
                    time.sleep(wait_times[attempt])

        print("  [Auto-DB] 分類重試用盡，略過本次新股分類")
        return None


TRACKING_HISTORY_LIMIT = 20


def _empty_tracking():
    return {
        'prediction_count': 0,
        'hit_count': 0,
        'hit_rate': '0.0%',
        'last_recommended': None,
        'consecutive_foreign_buy_days': 0,
        'last_foreign_buy_date': None,
        'history': [],
    }


def tracking_entry_from_validation(date_str, prediction_date, row):
    """
    One stock_db history row from an on-air validation row
    (prediction_validator.validate_prediction_targets): the same classify_hit
    verdict — 1-session excess vs ^TWII outside the dead band, a close through
    a valid stop is a miss, a stale bar is not judged — so stock_db tracking
    never grades a call differently from what was said on air.

    Counting follows the ledger: only an index-relative hit / miss enters
    prediction_count. A raw-return fallback (index missing), neutral,
    non-directional or price-missing row is kept in history with counted=False.
    """
    from modules.prediction_validator import _float, direction_sign

    row = row or {}
    direction = row.get('direction', '多')
    actual_pct = _float(row.get('pct_change'))
    index_pct = _float(row.get('index_pct_change'))
    excess = _float(row.get('excess_pct'))
    outcome = row.get('outcome')
    basis = row.get('hit_basis') or 'excess'
    directional = direction_sign(direction) != 0
    hit = row.get('hit') if directional and basis in ('excess', 'stop') else None
    if actual_pct is None:
        result_desc = "無法驗收（今日無數據）"
    elif not directional:
        result_desc = f"實際 {actual_pct:+.2f}% → ⚠️ 無法判定（方向非多/空，不計）"
    elif basis == 'stop' and hit is not None:
        result_desc = f"實際 {actual_pct:+.2f}% → ❌ 失準（收盤觸發停損）"
    elif index_pct is None or excess is None:
        result_desc = f"實際 {actual_pct:+.2f}% → ⚠️ 無法判定（大盤資料缺，不計）"
    elif outcome == 'neutral':
        result_desc = (
            f"實際 {actual_pct:+.2f}%，大盤 {index_pct:+.2f}%，超額 {excess:+.2f}%"
            " → ➖ 與大盤持平（不計）"
        )
    elif hit is None:
        result_desc = f"實際 {actual_pct:+.2f}% → ⚠️ 無法判定（不計）"
    else:
        result_desc = (
            f"實際 {actual_pct:+.2f}%，大盤 {index_pct:+.2f}%，超額 {excess:+.2f}%"
            f" → {'✅ 命中' if hit else '❌ 失準'}"
        )
    return {
        'date': date_str,
        'prediction_date': prediction_date,
        'direction': direction,
        'trigger': row.get('trigger', ''),
        'stop_loss': row.get('stop_loss_desc', ''),
        'result': result_desc,
        'hit': None if hit is None else bool(hit),
        'counted': hit is not None,
        'basis': 'stop_1d' if basis == 'stop' and hit is not None else 'excess_1d',
    }


def build_tracking_entry(
    date_str, prediction_date, direction, trigger, stop_desc, actual_pct, index_pct, stop_loss_price=None, price=None
):
    """
    One stock_db history row from bare numbers. Thin wrapper: the verdict comes
    from prediction_validator.validate_prediction_targets (the single hit rule)
    via tracking_entry_from_validation.
    """
    from modules.prediction_validator import _float, validate_prediction_targets

    target = {
        'id': 'TRACK',
        'direction': direction,
        'trigger': trigger,
        'stop_loss_desc': stop_desc,
        'stop_loss_price': stop_loss_price,
    }
    market = {}
    if _float(actual_pct) is not None:
        market['TRACK'] = {'pct_change': _float(actual_pct), 'price': _float(price)}
    if _float(index_pct) is not None:
        market['^TWII'] = {'pct_change': _float(index_pct)}
    rows = validate_prediction_targets([target], market)
    return tracking_entry_from_validation(date_str, prediction_date, rows[0] if rows else dict(target))


def _entry_contribution(entry):
    """(counted, hit) of a history entry; legacy entries were always counted."""
    if 'counted' in entry:
        return bool(entry.get('counted')), bool(entry.get('hit'))
    return True, '✅' in str(entry.get('result') or '')


def _same_tracking_key(a, b):
    if a.get('prediction_date') and b.get('prediction_date'):
        return a['prediction_date'] == b['prediction_date']
    return (
        a.get('date') == b.get('date')
        and str(a.get('direction') or '') == str(b.get('direction') or '')
        and str(a.get('trigger') or '') == str(b.get('trigger') or '')
    )


def _refresh_hit_rate(tracking):
    count = max(0, int(tracking.get('prediction_count') or 0))
    hits = min(count, max(0, int(tracking.get('hit_count') or 0)))
    tracking['prediction_count'] = count
    tracking['hit_count'] = hits
    tracking['hit_rate'] = f"{(hits / count * 100) if count else 0.0:.1f}%"


def apply_tracking_entry(tracking, entry):
    """Insert or replace entry (idempotent). Returns True when it replaced one."""
    history = list(tracking.get('history') or [])
    replaced = False
    for idx, old in enumerate(history):
        if _same_tracking_key(old, entry):
            counted, hit = _entry_contribution(old)
            if counted:
                tracking['prediction_count'] = int(tracking.get('prediction_count') or 0) - 1
                if hit:
                    tracking['hit_count'] = int(tracking.get('hit_count') or 0) - 1
            history[idx] = entry
            replaced = True
            break
    if not replaced:
        history.append(entry)
    if entry.get('counted'):
        tracking['prediction_count'] = int(tracking.get('prediction_count') or 0) + 1
        if entry.get('hit'):
            tracking['hit_count'] = int(tracking.get('hit_count') or 0) + 1
    tracking['history'] = history[-TRACKING_HISTORY_LIMIT:]
    _refresh_hit_rate(tracking)
    return replaced


def dedupe_tracking_history(tracking):
    """
    Remove repeated same-key history rows left by old same-day reruns and take
    their contribution back out of prediction_count / hit_count.
    The newest (last) occurrence wins, matching apply_tracking_entry where a
    rerun replaces the earlier, often pre-close, result.
    Returns the number of removed rows (0 = unchanged).
    """
    history = list((tracking or {}).get('history') or [])
    kept, removed = [], 0
    for entry in reversed(history):
        if any(_same_tracking_key(later, entry) for later in kept):
            counted, hit = _entry_contribution(entry)
            if counted:
                tracking['prediction_count'] = int(tracking.get('prediction_count') or 0) - 1
                if hit:
                    tracking['hit_count'] = int(tracking.get('hit_count') or 0) - 1
            removed += 1
            continue
        kept.append(entry)
    if removed:
        tracking['history'] = list(reversed(kept))
        _refresh_hit_rate(tracking)
    return removed
