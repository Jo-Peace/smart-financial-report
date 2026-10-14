import datetime
import json
import os
import re
import sqlite3


ROLE_LABELS = {
    "direction_indicator": "方向指標股",
    "elastic_attack": "彈性攻擊股",
    "theme_confirmation": "題材確認股",
    "capital_vehicle": "資金載體",
    "defensive_income": "防守收息股",
    "risk_warning": "風險提醒股",
    "indirect_infrastructure": "間接受惠基建股",
}

ROLE_TITLE_GUIDANCE = {
    "direction_indicator": "適合用來確認資金方向，不宜包裝成最大彈性飆股。",
    "elastic_attack": "適合放進標題或封面回答「誰先動、誰彈性大」。",
    "theme_confirmation": "適合用來驗證族群邏輯，標題應搭配更有彈性的同族群標的。",
    "capital_vehicle": "適合描述資金流向或 ETF 熱度，不應當成單一個股爆發故事。",
    "defensive_income": "適合做風控或防守配置，不宜包裝成攻擊主軸。",
    "risk_warning": "適合放在風險提醒、法人撤退或高檔套現段落。",
    "indirect_infrastructure": "適合放在專題延伸，不宜說成 AI 伺服器核心 BOM 個股。",
}

# Only structural, non-subjective overrides: the mega-cap index weights that
# role_scorer.DIRECTION_TICKERS already treats as direction indicators.
# Everything else is classified by local data / keyword rules below, and the
# evidence says which token (or none) decided it. ETFs go by their 00 prefix.
STRUCTURAL_ROLE_OVERRIDES = {
    "2317": ("direction_indicator", "structural:mega_cap_index_weight"),
    "2330": ("direction_indicator", "structural:mega_cap_index_weight"),
    "2454": ("direction_indicator", "structural:mega_cap_index_weight"),
}
# Backward-compatible name (role only).
KNOWN_ROLE_OVERRIDES = {sid: role for sid, (role, _src) in STRUCTURAL_ROLE_OVERRIDES.items()}

# Data-backed risk_warning: repeatedly in the foreign top-5 sell list over the
# last RISK_LOOKBACK_SESSIONS flow dates (local analytics.db, point-in-time).
RISK_LOOKBACK_SESSIONS = 10
RISK_SELL_TOP_N = 5
RISK_MIN_SELL_SESSIONS = 4

ROLE_RULES = (
    ("defensive_income", ["金融", "銀行", "證券", "壽險", "保險"]),
    ("indirect_infrastructure", ["電信", "水泥", "鋼鐵", "物流", "租賃", "建材"]),
    ("direction_indicator", ["台積電", "鴻海", "權值", "核心晶圓代工", "垂直整合龍頭"]),
    ("elastic_attack", ["記憶體", "DRAM", "載板", "PCB", "CCL", "散熱", "電源", "ODM", "系統整合"]),
    ("theme_confirmation", ["封測", "測試", "材料", "基板", "BOM"]),
)


class LocalKnowledgeCompounder:
    """
    Deterministic local memory updater.

    This layer only uses structured data already produced by the pipeline. It does not ask
    Gemini to interpret the market, so it can run even when AI quota is exhausted.
    """

    def __init__(self, base_dir=None):
        self.base_dir = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.reports_dir = os.path.join(self.base_dir, "reports")
        self.wiki_dir = os.path.join(self.base_dir, "data", "knowledge_wiki")
        self.stock_db_path = os.path.join(self.base_dir, "data", "stock_db.json")
        self.local_memory_path = os.path.join(self.wiki_dir, "local_market_memory.md")
        self.role_taxonomy_path = os.path.join(self.wiki_dir, "stock_role_taxonomy.md")

    def update_from_structured_data(self, structured_data, report_content="", report_date=None, replace_existing=False):
        """
        replace_existing=True (regen/resume reruns): rewrite that date's memory entry from the
        corrected structured_data instead of keeping the first run's version.
        """
        date_str = report_date or structured_data.get("date") or datetime.date.today().isoformat()
        os.makedirs(self.wiki_dir, exist_ok=True)

        memory_appended = self._append_local_market_memory(structured_data, date_str, replace=replace_existing)
        roles_updated = self._update_stock_roles(structured_data, date_str)
        taxonomy_written = self._write_role_taxonomy()

        return {
            "date": date_str,
            "memory_appended": memory_appended,
            "roles_updated": roles_updated,
            "taxonomy_written": taxonomy_written,
        }

    def _append_local_market_memory(self, structured_data, date_str, replace=False):
        from modules.knowledge_manager import upsert_dated_block

        entry = self._build_memory_entry(structured_data, date_str)
        if not entry:
            return False

        existing = self._read_text(self.local_memory_path)
        if not replace and re.search(rf"^## {re.escape(date_str)}\s*$", existing, re.MULTILINE):
            return False

        if not existing.strip():
            existing = (
                "# 本機市場記憶\n\n"
                "此檔由本機 structured_data deterministic append，只保留可驗證欄位（金額、排名、漲跌、驗收），"
                "Gemini 敘事（背離描述、trigger）留在當日 structured_data.json，不回饋 prompt。\n"
            )
        # Backfills / reruns of an older date are inserted in date order instead of at
        # the tail, because every reader treats the file tail as "most recent".
        updated, action = upsert_dated_block(existing, date_str, entry.strip(), replace=replace)
        if action in {"skipped", "unchanged"}:
            return False
        self._write_text(self.local_memory_path, updated)
        return True

    def _build_memory_entry(self, structured_data, date_str):
        lines = [f"## {date_str}", ""]
        index_action = structured_data.get("index_action")
        if index_action:
            lines.append(f"- **大盤定性**：{index_action}")

        buys = structured_data.get("safe_havens_bought") or []
        sells = structured_data.get("heavyweights_dumped") or []
        if buys:
            lines.append(f"- **外資/法人買超焦點**：{self._join_limited(buys)}")
        if sells:
            lines.append(f"- **外資/法人賣超焦點**：{self._join_limited(sells)}")

        validation = structured_data.get("validation_results") or []
        prev_results = structured_data.get("prev_day_picks_result") or []
        if validation:
            from modules.prediction_validator import direction_sign

            def _excess(row):
                # validate_prediction_targets emits excess_pct; older rows used excess_return_pct.
                value = row.get("excess_pct")
                return value if isinstance(value, (int, float)) else row.get("excess_return_pct")

            def _signed(row, value):
                # 預測損益 = direction-adjusted (a 空 call on a falling stock is a gain).
                sign = direction_sign(row.get("direction"))
                return value * sign if isinstance(value, (int, float)) and sign else None

            hit_count = sum(1 for row in validation if row.get("hit") is True)
            total_count = sum(1 for row in validation if row.get("hit") is not None)
            neutral_count = sum(1 for row in validation if row.get("outcome") == "neutral")
            valid_returns = [v for v in (_signed(row, row.get("pct_change")) for row in validation) if v is not None]
            avg_return = (sum(valid_returns) / len(valid_returns)) if valid_returns else None
            valid_excess = [v for v in (_signed(row, _excess(row)) for row in validation) if v is not None]
            avg_excess = (sum(valid_excess) / len(valid_excess)) if valid_excess else None
            summary_bits = [f"{hit_count}/{total_count} 命中"]
            if neutral_count:
                summary_bits.append(f"{neutral_count} 檔與大盤同步（不計）")
            if isinstance(avg_return, (int, float)):
                summary_bits.append(f"平均預測損益 {avg_return:+.2f}%")
            if isinstance(avg_excess, (int, float)):
                summary_bits.append(f"平均超額報酬 {avg_excess:+.2f}%")
            lines.append(f"- **昨日預測驗收**：{'；'.join(summary_bits)}")
            for row in validation[:5]:
                if row.get("hit") is True:
                    status = "命中"
                elif row.get("hit") is False:
                    status = "未成立"
                elif row.get("outcome") == "neutral":
                    status = "與大盤同步（不計）"
                else:
                    status = "無法驗收"
                pct = row.get("pct_change")
                excess = _excess(row)
                pct_text = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "無漲跌資料"
                excess_text = f"，超額 {excess:+.2f}%" if isinstance(excess, (int, float)) else ""
                lines.append(f"  - {row.get('id')} {row.get('name')}：{status}，漲跌 {pct_text}{excess_text}")
        elif prev_results:
            lines.append(f"- **昨日預測驗收**：{self._join_limited(prev_results, limit=4)}")

        targets = structured_data.get("prediction_targets") or []
        if targets:
            target_lines = []
            for target in targets[:5]:
                # Only verifiable fields: the Gemini trigger narrative must not feed back.
                stop = target.get("stop_loss_price")
                stop_text = f"，停損 {stop}" if stop not in (None, "") else ""
                target_lines.append(
                    f"{target.get('id')} {target.get('name')}（{target.get('direction', '觀察')}{stop_text}）"
                )
            lines.append(f"- **下次正式觀察標的**：{self._join_limited(target_lines, limit=5)}")

        trust_conflicts = structured_data.get("trust_conflicts") or []
        if trust_conflicts:
            lines.append(f"- **外資／投信對作**：{self._join_limited(trust_conflicts, limit=3)}")

        lines.extend(["", ""])
        return "\n".join(lines)

    def _update_stock_roles(self, structured_data, date_str):
        from modules.role_scorer import (
            enrich_index_with_names,
            merge_role_inputs,
            merge_scores_into_market_role,
            score_from_meta,
        )

        stock_db = self._load_stock_db()
        if not stock_db:
            return 0

        role_inputs = structured_data.get("role_score_inputs") or {}
        sell_pressure = self._recent_sell_pressure(date_str)
        daily_flow = self._build_daily_flow_lookup(structured_data)
        mentioned = self._collect_mentioned_stock_ids(structured_data)
        flow_meta = merge_role_inputs(enrich_index_with_names(structured_data, stock_db), role_inputs)
        # Include name-resolved tickers from buy/sell/divergence lists (not only digit IDs in text).
        for sid in flow_meta:
            mentioned.add(sid)
        updated = 0

        for sid in sorted(mentioned):
            # stock_db keys are plain tickers; also try case-insensitive match
            entry = stock_db.get(sid)
            if entry is None:
                for k, v in stock_db.items():
                    if str(k).upper() == str(sid).upper():
                        entry = v
                        sid = k
                        break
            if entry is None:
                continue
            last_observed = str((entry.get("market_role") or {}).get("last_observed") or "")
            if last_observed and last_observed > str(date_str):
                # Rerun/backfill of an older date must not roll the role back.
                continue
            meta = flow_meta.get(sid) or {}
            sell_sessions = int(sell_pressure.get(str(sid)) or 0)
            if meta.get("sell_rank") and int(meta["sell_rank"]) <= RISK_SELL_TOP_N:
                sell_sessions += 1  # today's list (not yet in analytics.db during /1)
            role_detail = self._classify_base_role_detail(
                sid,
                entry,
                sell_sessions=sell_sessions,
                bought_today=bool(meta.get("buy_rank")),
            )
            base_role = role_detail["role"]
            # Name-only 法人榜 rows are resolved in flow_meta; digit-id lookups keep
            # prediction / validation states that flow_meta does not carry.
            latest_state = daily_flow.get(sid) or meta.get("flow_state") or "mentioned"
            evidence = self._build_role_evidence(sid, entry, base_role, latest_state, role_detail=role_detail)
            reason = self._build_role_reason(sid, entry, base_role, latest_state, evidence)
            # Score with the role decided now (entry still holds yesterday's market_role).
            entry_for_score = dict(entry)
            entry_for_score["market_role"] = {**(entry.get("market_role") or {}), "base_role": base_role}
            scores = score_from_meta(
                sid,
                entry_for_score,
                meta,
                as_of=date_str,
                role_inputs=role_inputs,
                flow_state=meta.get("flow_state") or latest_state,
            )
            market_role = {
                "base_role": base_role,
                "label": ROLE_LABELS[base_role],
                "title_guidance": ROLE_TITLE_GUIDANCE[base_role],
                "role_source": role_detail["source"],
                "latest_flow_state": latest_state,
                "last_observed": date_str,
                "evidence": evidence,
                "reason": reason,
            }
            market_role = merge_scores_into_market_role(market_role, scores)
            # Keep human reason short + score snapshot
            market_role["reason"] = (
                f"{reason}；title_priority={scores.get('title_priority')} "
                f"(E{scores.get('elasticity')}/F{scores.get('foreign_buy_strength')}/"
                f"V{scores.get('volume_confirmation')}/C{scores.get('validation_credibility')})"
            )
            if entry.get("market_role") != market_role:
                entry["market_role"] = market_role
                updated += 1

        if updated:
            self._save_stock_db(stock_db)
        return updated

    def _classify_base_role(self, sid, entry):
        return self._classify_base_role_detail(sid, entry)["role"]

    def _classify_base_role_detail(self, sid, entry, sell_sessions=None, bought_today=False):
        """
        Role + where it came from: structural (index weight / ETF id),
        data (repeated foreign top-5 selling in local institutional_flows),
        rule (matched keyword in local DB text) or default (nothing matched).
        """
        if sid in STRUCTURAL_ROLE_OVERRIDES:
            role, source = STRUCTURAL_ROLE_OVERRIDES[sid]
            return {"role": role, "source": source, "matched": []}

        name = entry.get("name", "")
        sector = entry.get("sector", "")
        role = entry.get("supply_chain_role", "")
        combined = f"{name} {sector} {role}"

        if str(sid).startswith("00"):
            return {"role": "capital_vehicle", "source": "structural:etf_id_prefix", "matched": []}
        if sell_sessions and int(sell_sessions) >= RISK_MIN_SELL_SESSIONS and not bought_today:
            return {
                "role": "risk_warning",
                "source": "data:institutional_flows",
                "matched": [
                    f"foreign_sell_top{RISK_SELL_TOP_N}={int(sell_sessions)}/{RISK_LOOKBACK_SESSIONS}_sessions"
                ],
            }
        for rule_role, tokens in ROLE_RULES:
            matched = [token for token in tokens if token in combined]
            if matched:
                return {"role": rule_role, "source": "rule", "matched": matched}
        return {"role": "theme_confirmation", "source": "default", "matched": []}

    def _build_role_evidence(self, sid, entry, base_role, latest_state, role_detail=None):
        tracking = entry.get("tracking") or {}
        evidence = [
            f"sector={entry.get('sector', 'unknown')}",
            f"supply_chain_role={entry.get('supply_chain_role', 'unknown')}",
            f"latest_flow_state={latest_state}",
        ]
        if tracking.get("prediction_count"):
            evidence.append(
                f"tracking={tracking.get('prediction_count')} predictions, "
                f"{tracking.get('hit_count', 0)} hits, hit_rate={tracking.get('hit_rate')}"
            )
        if tracking.get("last_foreign_buy_date"):
            evidence.append(f"last_foreign_buy_date={tracking.get('last_foreign_buy_date')}")
        detail = role_detail or self._classify_base_role_detail(sid, entry)
        source = detail.get("source") or "default"
        if source.startswith("data:"):
            evidence.append(
                f"role_rule={base_role} from {source[5:]} ({'/'.join(detail.get('matched') or [])})"
            )
        elif source == "rule":
            evidence.append(
                f"role_rule={base_role} matched {'/'.join(detail.get('matched') or [])} in local DB text"
            )
        elif source == "default":
            evidence.append("role_rule=default theme_confirmation (no role marker matched in local DB text)")
        else:
            evidence.append(f"role_rule={base_role} via {source}")
        return evidence

    def _recent_sell_pressure(self, date_str, db_path=None):
        """
        {ticker: sessions in foreign top-N sell} over the prior flow dates
        strictly before date_str (today's list is added from structured_data,
        so /1 and a later regen agree).
        """
        db_path = db_path or os.path.join(self.base_dir, "data", "analytics.db")
        if not date_str or not os.path.exists(db_path):
            return {}
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            return {}
        try:
            dates = [
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT flow_date FROM institutional_flows WHERE flow_date < ? "
                    "ORDER BY flow_date DESC LIMIT ?",
                    (str(date_str), RISK_LOOKBACK_SESSIONS - 1),
                ).fetchall()
            ]
            if not dates:
                return {}
            placeholders = ",".join("?" for _ in dates)
            rows = conn.execute(
                f"SELECT ticker, COUNT(DISTINCT flow_date) FROM institutional_flows "
                f"WHERE side = 'sell' AND rank <= ? AND flow_date IN ({placeholders}) GROUP BY ticker",
                (RISK_SELL_TOP_N, *dates),
            ).fetchall()
        except sqlite3.Error:
            return {}
        finally:
            conn.close()
        return {str(ticker): int(count) for ticker, count in rows}

    def _build_role_reason(self, sid, entry, base_role, latest_state, evidence):
        tracking = entry.get("tracking") or {}
        hit_rate = tracking.get("hit_rate")
        prediction_count = tracking.get("prediction_count")
        parts = [f"{ROLE_LABELS[base_role]}；{latest_state}"]
        if prediction_count:
            parts.append(f"歷史驗收 {prediction_count} 次，命中率 {hit_rate}")
        supply_chain_role = entry.get("supply_chain_role")
        if supply_chain_role:
            parts.append(supply_chain_role)
        if evidence:
            parts.append(f"佐證: {evidence[0]}")
        return "；".join(parts)

    def _build_daily_flow_lookup(self, structured_data):
        flow = {}
        for item in structured_data.get("safe_havens_bought") or []:
            sid = self._extract_sid(item)
            if sid:
                flow[sid] = "foreign_buy_focus"
        for item in structured_data.get("heavyweights_dumped") or []:
            sid = self._extract_sid(item)
            if sid:
                flow[sid] = "foreign_sell_risk"
        for target in structured_data.get("prediction_targets") or []:
            sid = str(target.get("id", "")).strip()
            if sid:
                flow[sid] = "formal_prediction_target"
        for row in structured_data.get("validation_results") or []:
            sid = str(row.get("id", "")).strip()
            if sid and sid not in flow:
                flow[sid] = "validated_previous_target"
        for item in structured_data.get("prev_day_picks_result") or []:
            sid = self._extract_sid(item)
            if sid and sid not in flow:
                flow[sid] = "validated_previous_target"
        return flow

    def _collect_mentioned_stock_ids(self, structured_data):
        ids = set()

        for field in (
            "safe_havens_bought",
            "heavyweights_dumped",
            "ai_data_picks",
            "price_volume_divergence",
            "prev_day_picks_result",
        ):
            for item in structured_data.get(field) or []:
                ids.update(re.findall(r"\b\d{4,6}\b", str(item)))

        for stocks in (structured_data.get("sectors") or {}).values():
            for item in stocks:
                sid = self._extract_sid(item)
                if sid:
                    ids.add(sid)

        for field in ("prediction_targets", "validation_results"):
            for item in structured_data.get(field) or []:
                sid = str(item.get("id", "")).strip()
                if sid:
                    ids.add(sid)

        return ids

    def _write_role_taxonomy(self):
        lines = [
            "# 個股角色分類規則",
            "",
            "此檔由本機規則產生，提供報告、專題與 YouTube package 判斷標題包裝方式。",
            "",
        ]
        for role, label in ROLE_LABELS.items():
            lines.append(f"## {label} (`{role}`)")
            lines.append(f"- **標題用途**：{ROLE_TITLE_GUIDANCE[role]}")
            lines.append("")
        lines.extend(
            [
                "## 量化分數（`market_role.scores`）",
                "",
                "每日本機更新，供標題 / 封面 / NotebookLM 主角排序：",
                "",
                "- **elasticity**：本機快照的實現波動率與對加權指數 beta（樣本不足才退回角色先驗；方向股/ETF 會被壓低）",
                "- **foreign_buy_strength**：外資買超排名與金額；有當日成交值時再看外資淨額占成交值比重",
                "- **volume_confirmation**：個股量增比與成交值名次（ETF 排除）；不在股數前 20 名＝量能未知，不是量縮",
                "- **validation_credibility**：歷史 prediction 命中可信度",
                "- **title_priority**：綜合分（誰該當標題/封面/主角卡；近幾日已當主角會遞減）",
                "",
                "規則：方向指標股（如台積電）即使買超大，title_priority 仍應低於彈性攻擊股。",
                "角色來源寫在 `market_role.role_source`：structural（權值／ETF 代號）、data:institutional_flows（近 10 個交易日多次進外資賣超前 5 → 風險提醒股）、rule（本機文字命中的關鍵字）或 default（未命中任何標記）。",
                "",
            ]
        )
        self._write_text(self.role_taxonomy_path, "\n".join(lines))
        return True

    def _load_stock_db(self):
        try:
            with open(self.stock_db_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_stock_db(self, stock_db):
        from modules.atomic_io import atomic_write_json, backup_file_daily

        backup_file_daily(self.stock_db_path, backup_dir=os.path.join(os.path.dirname(self.stock_db_path), "backup"))
        atomic_write_json(self.stock_db_path, stock_db)

    def _read_text(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    def _write_text(self, path, content):
        from modules.atomic_io import atomic_write_text

        atomic_write_text(path, content)

    def _extract_sid(self, value):
        match = re.search(r"\b(\d{4,6})\b", str(value))
        return match.group(1) if match else ""

    def _join_limited(self, items, limit=5):
        items = [str(item) for item in items if str(item).strip()]
        return "；".join(items[:limit]) if items else "無"
