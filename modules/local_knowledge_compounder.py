import datetime
import json
import os
import re


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

KNOWN_ROLE_OVERRIDES = {
    "2317": "direction_indicator",
    "2330": "direction_indicator",
    "0050": "capital_vehicle",
    "0052": "capital_vehicle",
    "00922": "capital_vehicle",
    "009816": "capital_vehicle",
    "2382": "elastic_attack",
    "2301": "elastic_attack",
    "2408": "elastic_attack",
    "3231": "elastic_attack",
    "3037": "elastic_attack",
    "2449": "theme_confirmation",
    "1303": "theme_confirmation",
    "1802": "elastic_attack",
}


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

    def update_from_structured_data(self, structured_data, report_content="", report_date=None):
        date_str = report_date or structured_data.get("date") or datetime.date.today().isoformat()
        os.makedirs(self.wiki_dir, exist_ok=True)

        memory_appended = self._append_local_market_memory(structured_data, date_str)
        roles_updated = self._update_stock_roles(structured_data, date_str)
        taxonomy_written = self._write_role_taxonomy()

        return {
            "date": date_str,
            "memory_appended": memory_appended,
            "roles_updated": roles_updated,
            "taxonomy_written": taxonomy_written,
        }

    def _append_local_market_memory(self, structured_data, date_str):
        entry = self._build_memory_entry(structured_data, date_str)
        if not entry:
            return False

        existing = self._read_text(self.local_memory_path)
        marker = f"## {date_str}"
        if marker in existing:
            return False

        if not existing.strip():
            self._write_text(
                self.local_memory_path,
                "# 本機市場記憶\n\n"
                "此檔由本機 structured_data deterministic append，保留可驗證欄位，避免只依賴 AI 自動整理。\n\n",
            )

        with open(self.local_memory_path, "a", encoding="utf-8") as f:
            f.write(entry)
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
            hit_count = sum(1 for row in validation if row.get("hit") is True)
            total_count = sum(1 for row in validation if row.get("hit") is not None)
            lines.append(f"- **昨日預測驗收**：{hit_count}/{total_count} 命中")
            for row in validation[:5]:
                status = "命中" if row.get("hit") is True else ("未成立" if row.get("hit") is False else "無法驗收")
                pct = row.get("pct_change")
                pct_text = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "無漲跌資料"
                lines.append(f"  - {row.get('id')} {row.get('name')}：{status}，{pct_text}")
        elif prev_results:
            lines.append(f"- **昨日預測驗收**：{self._join_limited(prev_results, limit=4)}")

        targets = structured_data.get("prediction_targets") or []
        if targets:
            target_lines = []
            for target in targets[:5]:
                target_lines.append(
                    f"{target.get('id')} {target.get('name')}（{target.get('direction', '觀察')}，"
                    f"{target.get('trigger', '無 trigger')}）"
                )
            lines.append(f"- **下次正式觀察標的**：{self._join_limited(target_lines, limit=5)}")

        divergences = structured_data.get("price_volume_divergence") or []
        if divergences:
            lines.append(f"- **量價/籌碼異常**：{self._join_limited(divergences, limit=3)}")

        lines.extend(["", ""])
        return "\n".join(lines)

    def _update_stock_roles(self, structured_data, date_str):
        stock_db = self._load_stock_db()
        if not stock_db:
            return 0

        daily_flow = self._build_daily_flow_lookup(structured_data)
        mentioned = self._collect_mentioned_stock_ids(structured_data)
        updated = 0

        for sid in sorted(mentioned):
            if sid not in stock_db:
                continue
            entry = stock_db[sid]
            base_role = self._classify_base_role(sid, entry)
            latest_state = daily_flow.get(sid, "mentioned")
            reason = self._build_role_reason(sid, entry, base_role, latest_state)
            market_role = {
                "base_role": base_role,
                "label": ROLE_LABELS[base_role],
                "title_guidance": ROLE_TITLE_GUIDANCE[base_role],
                "latest_flow_state": latest_state,
                "last_observed": date_str,
                "reason": reason,
            }
            if entry.get("market_role") != market_role:
                entry["market_role"] = market_role
                updated += 1

        if updated:
            self._save_stock_db(stock_db)
        return updated

    def _classify_base_role(self, sid, entry):
        if sid in KNOWN_ROLE_OVERRIDES:
            return KNOWN_ROLE_OVERRIDES[sid]

        name = entry.get("name", "")
        sector = entry.get("sector", "")
        role = entry.get("supply_chain_role", "")
        combined = f"{name} {sector} {role}"

        if sid.startswith("00"):
            return "capital_vehicle"
        if any(token in combined for token in ["金融", "銀行", "證券", "壽險", "保險"]):
            return "defensive_income"
        if any(token in combined for token in ["電信", "水泥", "鋼鐵", "物流", "租賃", "建材"]):
            return "indirect_infrastructure"
        if any(token in combined for token in ["台積電", "鴻海", "權值", "核心晶圓代工", "垂直整合龍頭"]):
            return "direction_indicator"
        if any(token in combined for token in ["記憶體", "DRAM", "載板", "PCB", "CCL", "散熱", "電源", "ODM", "系統整合"]):
            return "elastic_attack"
        if any(token in combined for token in ["封測", "測試", "材料", "基板", "BOM"]):
            return "theme_confirmation"
        return "theme_confirmation"

    def _build_role_reason(self, sid, entry, base_role, latest_state):
        tracking = entry.get("tracking") or {}
        hit_rate = tracking.get("hit_rate")
        prediction_count = tracking.get("prediction_count")
        parts = [f"{ROLE_LABELS[base_role]}；{latest_state}"]
        if prediction_count:
            parts.append(f"歷史驗收 {prediction_count} 次，命中率 {hit_rate}")
        supply_chain_role = entry.get("supply_chain_role")
        if supply_chain_role:
            parts.append(supply_chain_role)
        if sid == "2317":
            parts.append("權值與營收體量大，較適合作為資金方向確認")
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
        self._write_text(self.role_taxonomy_path, "\n".join(lines))
        return True

    def _load_stock_db(self):
        try:
            with open(self.stock_db_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_stock_db(self, stock_db):
        with open(self.stock_db_path, "w", encoding="utf-8") as f:
            json.dump(stock_db, f, ensure_ascii=False, indent=2)

    def _read_text(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    def _write_text(self, path, content):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _extract_sid(self, value):
        match = re.search(r"\b(\d{4,6})\b", str(value))
        return match.group(1) if match else ""

    def _join_limited(self, items, limit=5):
        items = [str(item) for item in items if str(item).strip()]
        return "；".join(items[:limit]) if items else "無"
