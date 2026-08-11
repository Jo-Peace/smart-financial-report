"""
Build a *facts-only* NotebookLM source document from structured_data.

Design rules for content quality:
- Source = verifiable numbers, lists, conditions (what NotebookLM may quote).
- No host lines, no fixed script, no template market commentary.
- No production/director instructions (those belong in notebooklm_prompt).
"""

from __future__ import annotations

import json
import os
import re


def _as_list(value):
    if not value:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _clean_source_text(value):
    text = str(value)
    replacements = {
        "影子主力的避險碉堡": "法人避險買盤",
        "高階真空吸籌": "低量買超",
        "真空吸籌": "榜外買超",
        "資金的地下大遷移": "資金轉向",
        "血洗": "重挫",
        "崩盤": "重挫",
        "碉堡": "避險標的",
        "外資提款": "外資賣超",
        "精準轉入": "轉向買超",
        "汰弱留強": "買賣超分化",
        "影子建倉": "榜外低量買超",
        "核彈級": "大幅",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _line_items(items, limit=None):
    values = [_clean_source_text(item).strip() for item in _as_list(items) if str(item).strip()]
    if limit is not None:
        values = values[:limit]
    if not values:
        return "- 無"
    return "\n".join(f"- {item}" for item in values)


def _validation_placement(structured_data):
    frame = structured_data.get("editorial_frame") or {}
    if isinstance(frame, dict) and frame.get("validation_placement"):
        return str(frame.get("validation_placement")).strip().lower()
    return "brief"


def _editorial_label(structured_data):
    frame = structured_data.get("editorial_frame") or {}
    if isinstance(frame, dict):
        return _clean_source_text(frame.get("label") or frame.get("frame") or "")
    return ""


def _prediction_lines(targets, limit=3):
    targets = _as_list(targets)[:limit]
    if not targets:
        return "- 無"
    lines = []
    for target in targets:
        if isinstance(target, dict):
            sid = target.get("id", "")
            name = target.get("name", "")
            direction = target.get("direction", "")
            trigger = target.get("trigger", "")
            stop_desc = target.get("stop_loss_desc", "")
            stop_price = target.get("stop_loss_price", None)
            stop = stop_desc if stop_price in (None, "") else f"{stop_desc}（{stop_price}）"
            # Prefer explicit fields when present; otherwise keep trigger/stop readable.
            rhythm = target.get("rhythm_condition") or target.get("next_day_condition") or ""
            trend = (
                target.get("trend_condition")
                or target.get("horizon_20d_condition")
                or target.get("thesis")
                or ""
            )
            lines.append(
                f"- {sid} {name}：方向 {direction}；"
                f"為何點名：{_clean_source_text(trigger)}；"
                f"隔日節奏：{_clean_source_text(rhythm) or '（見觸發理由中的短線條件）'}；"
                f"趨勢成立（≈20 交易日）：{_clean_source_text(trend) or '（中期假說見觸發理由；成敗以趨勢窗為主）'}；"
                f"失效條件：{_clean_source_text(stop)}"
            )
        else:
            lines.append(f"- {_clean_source_text(target)}")
    return "\n".join(lines)


def _validation_lines(rows, structured_data=None):
    """Short-horizon rhythm only — not the system scoreboard."""
    rows = _as_list(rows)
    if not rows:
        policy = (structured_data or {}).get("validation_policy") or {}
        if policy.get("onair_validation_eligible") is False:
            reason = policy.get("skip_reason") or "前次產出間隔過長"
            return f"- 略過上集短線對照：{_clean_source_text(reason)}"
        return "- 無"
    lines = [
        "- （解讀）以下為隔日／近端節奏對照，不是中期趨勢成敗；勿用單一 1D 論定系統準不準。"
    ]
    for row in rows:
        if isinstance(row, dict):
            sid = row.get("id", "")
            name = row.get("name", "")
            result = row.get("result", "")
            pct = row.get("pct_change", "")
            if isinstance(pct, (int, float)):
                pct_text = f"{pct:+.2f}%"
            else:
                pct_text = f"{pct}%" if pct not in (None, "") else "無漲跌"
            evidence = "；".join(_clean_source_text(x) for x in _as_list(row.get("evidence")))
            extra = f"；證據：{evidence}" if evidence else ""
            lines.append(
                f"- {sid} {name}：短線節奏 {result}；漲跌 {pct_text}{extra}"
            )
        else:
            lines.append(f"- {_clean_source_text(row)}")
    return "\n".join(lines)


def _sector_lines(sectors, limit=5):
    if not sectors or not isinstance(sectors, dict):
        return "- 無"
    lines = []
    for i, (sector, stocks) in enumerate(sectors.items()):
        if i >= limit:
            break
        stock_text = "、".join(_clean_source_text(x) for x in _as_list(stocks)[:3]) or "無"
        lines.append(f"- {_clean_source_text(sector)}：{stock_text}")
    return "\n".join(lines) if lines else "- 無"


def _event_fingerprint(title: str) -> str:
    text = re.sub(r"\s+", " ", str(title or "").lower())
    text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text)
    tokens = [t for t in text.split() if len(t) > 1]
    # Prefer entity-ish tokens for near-duplicate news (same AMD Taiwan package story).
    key_tokens = []
    for t in tokens:
        if t in {"amd", "nvidia", "taiwan", "ai", "packaging", "investment", "台灣", "投資", "封裝"}:
            key_tokens.append(t)
    if len(key_tokens) >= 2:
        return " ".join(sorted(set(key_tokens))[:6])
    return " ".join(tokens[:8])


def _dedupe_events(events, limit=2):
    """Keep highest-score unique event stories; drop near-duplicate headlines."""
    scored = []
    for event in _as_list(events):
        if isinstance(event, dict):
            title = str(event.get("event") or "").strip()
            score = event.get("impact_score")
            try:
                score_n = float(score) if score not in (None, "") else 0.0
            except (TypeError, ValueError):
                score_n = 0.0
            scored.append((score_n, title, event))
        else:
            scored.append((0.0, str(event), event))
    scored.sort(key=lambda x: x[0], reverse=True)

    out = []
    seen = set()
    for _score, title, event in scored:
        fp = _event_fingerprint(title)
        if not fp or fp in seen:
            continue
        # Also block if an existing title shares a long common substring.
        if any(fp[:12] and fp[:12] in prev for prev in seen):
            continue
        seen.add(fp)
        out.append(event)
        if len(out) >= limit:
            break
    return out


def _event_lines(events, limit=2):
    events = _dedupe_events(events, limit=limit)
    if not events:
        return "- 無"
    lines = []
    for event in events:
        if isinstance(event, dict):
            title = _clean_source_text(event.get("event", ""))
            score = event.get("impact_score", "")
            roles = "、".join(_clean_source_text(x) for x in _as_list(event.get("impacted_roles"))[:3]) or "無"
            # Shorten English headline noise for TTS/source clarity.
            if len(title) > 80:
                title = title[:77] + "…"
            lines.append(f"- {title}；影響分數 {score}；可能影響：{roles}")
        else:
            lines.append(f"- {_clean_source_text(event)}")
    return "\n".join(lines)


def _flag_extreme_returns(text: str) -> str:
    """Annotate extreme short-window % so hosts don't treat outliers as normal trend."""
    cleaned = _clean_source_text(text)

    def _repl(match):
        window = match.group(1)
        sign = match.group(2) or ""
        num = float(match.group(3))
        signed = num if sign != "-" else -num
        if abs(signed) >= 40 and window.upper() in {"5D", "10D", "1D", "3D"}:
            return f"{window} {sign}{num:.2f}%（極端值，僅作背景勿當主線）"
        return match.group(0)

    return re.sub(r"\b(\d+D)\s*([+-]?)(\d+(?:\.\d+)?)%", _repl, cleaned, flags=re.I)


def _short_term_pick_lines(structured_data):
    picks = _as_list(structured_data.get("short_term_picks"))
    if not picks:
        # Fall back to prediction_targets so source still has a forward card.
        body = _prediction_lines(structured_data.get("prediction_targets"), limit=2)
        if body == "- 無":
            return body
        return f"- （時間尺度）1～5 日＝節奏參考，不是趨勢成敗主尺子。\n{body}"
    lines = ["- （時間尺度）1～5 日＝短線節奏參考，不是趨勢成敗主尺子。"]
    for pick in picks[:2]:
        if isinstance(pick, dict):
            ticker = pick.get("ticker", "")
            name = pick.get("name", "")
            direction = pick.get("direction", "")
            horizon = pick.get("horizon", "")
            window = pick.get("validation_window", "")
            reason = _clean_source_text(pick.get("reason", ""))
            invalidation = _clean_source_text(pick.get("invalidation", ""))
            lines.append(
                f"- {ticker} {name}：方向 {direction}；時間窗 {horizon}；驗證窗 {window}；"
                f"理由：{reason}；失效條件：{invalidation}"
            )
        else:
            lines.append(f"- {_clean_source_text(pick)}")
    return "\n".join(lines)


def _long_term_pick_lines(structured_data):
    picks = _as_list(structured_data.get("long_term_picks"))
    if not picks:
        return "- 無"
    lines = [
        "- （時間尺度）中期／約 20 交易日＝「趨勢」窗；假說成敗優先看此層，不是隔日漲跌。"
    ]
    for pick in picks[:2]:
        if isinstance(pick, dict):
            theme = _flag_extreme_returns(pick.get("theme", ""))
            horizon = _clean_source_text(pick.get("horizon", ""))
            window = _clean_source_text(pick.get("validation_window", ""))
            thesis = _flag_extreme_returns(pick.get("thesis", ""))
            evidence = _flag_extreme_returns(pick.get("evidence", ""))
            lines.append(
                f"- {theme}：時間窗 {horizon}；驗證窗 {window}；主軸：{thesis}；證據：{evidence}"
            )
        else:
            lines.append(f"- {_flag_extreme_returns(pick)}")
    return "\n".join(lines)


def _parse_amount(text: str) -> str:
    m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*億", str(text or ""))
    return f"{m.group(1)}億" if m else ""


def _parse_name_id(text: str) -> tuple[str, str]:
    """Best-effort (id, name) from free text like '2303 聯電 (+37.6億)' or '聯電 (+37.6億)'."""
    text = _clean_source_text(text).strip()
    # TW tickers: 4–6 digits, optional letter suffix (0050, 00685L).
    m = re.match(r"^(\d{4,6}[A-Za-z]?)\s*([^\s(（+−\-]*)", text)
    if m:
        return m.group(1), (m.group(2) or "").strip()
    # strip amount paren for name
    name = re.sub(r"\s*[\(（].*?[\)）]\s*", "", text).strip()
    return "", name


def _lookup_target_invalidation(structured_data, sid: str, name: str) -> str:
    for target in _as_list(structured_data.get("prediction_targets")):
        if not isinstance(target, dict):
            continue
        tid = str(target.get("id") or "").strip()
        tname = str(target.get("name") or "").strip()
        if (sid and tid == sid) or (name and tname and name in tname):
            stop = target.get("stop_loss_desc") or target.get("stop_loss_price") or ""
            return _clean_source_text(stop)
    for pick in _as_list(structured_data.get("short_term_picks")):
        if not isinstance(pick, dict):
            continue
        tid = str(pick.get("ticker") or pick.get("id") or "").strip()
        tname = str(pick.get("name") or "").strip()
        if (sid and tid == sid) or (name and tname and name in tname):
            return _clean_source_text(pick.get("invalidation") or "")
    return ""


def _build_protagonist_cards(structured_data, limit=3) -> str:
    """
    2–3 focus stock cards ranked by quantitative title_priority (role_scorer).
    Falls back to list order if ranking unavailable.
    """
    cards = []
    ranking = structured_data.get("focus_ranking")
    if not ranking:
        try:
            from modules.role_scorer import rank_focus_stocks

            ranking = rank_focus_stocks(structured_data, limit=limit)
        except Exception:
            ranking = []

    if ranking:
        for i, row in enumerate(ranking[:limit], start=1):
            sid = row.get("ticker") or ""
            name = row.get("name") or ""
            label = f"{sid} {name}".strip() or row.get("source_text") or f"標的{i}"
            text = _clean_source_text(row.get("source_text") or label)
            amount = _parse_amount(text)
            inv = _lookup_target_invalidation(structured_data, sid, name)
            role_label = row.get("role_label") or row.get("base_role") or "觀察"
            lines = [
                f"### 主角 {i}：{label}",
                f"- 角色分類：{role_label}（`{row.get('base_role', '')}`）",
                (
                    f"- 量化分：title_priority={row.get('title_priority')}｜"
                    f"彈性={row.get('elasticity')}｜外資={row.get('foreign_buy_strength')}｜"
                    f"量能={row.get('volume_confirmation')}｜驗收可信={row.get('validation_credibility')}"
                ),
            ]
            if amount:
                lines.append(f"- 法人金額線索：{amount}")
            lines.append(f"- 原始證據：{text}")
            if inv:
                lines.append(f"- 失效條件：{inv}")
            else:
                lines.append("- 失效條件：資料源未指定（勿自行發明）")
            # Packaging guidance: direction stocks are context, not hero
            if row.get("base_role") == "direction_indicator":
                lines.append("- 包裝提示：方向指標股，適合作對照/大盤錨，不宜當「誰先噴」主角")
            elif row.get("base_role") == "elastic_attack":
                lines.append("- 包裝提示：彈性攻擊股，適合標題/封面主角")
            cards.append("\n".join(lines))
        return "\n\n".join(cards)

    # Fallback: previous list-order cards
    seen = set()
    fallback = []

    def _add(role: str, raw: str, note: str = ""):
        if len(fallback) >= limit:
            return
        text = _clean_source_text(raw).strip()
        if not text:
            return
        sid, name = _parse_name_id(text)
        key = sid or name or text
        if key in seen:
            return
        seen.add(key)
        amount = _parse_amount(text)
        inv = _lookup_target_invalidation(structured_data, sid, name)
        label = f"{sid} {name}".strip() if sid or name else text
        lines = [
            f"### 主角 {len(fallback) + 1}：{label}",
            f"- 角色：{role}",
        ]
        if amount:
            lines.append(f"- 法人金額線索：{amount}")
        lines.append(f"- 原始證據：{text}")
        if note:
            lines.append(f"- 備註：{_clean_source_text(note)}")
        if inv:
            lines.append(f"- 失效條件：{inv}")
        else:
            lines.append("- 失效條件：資料源未指定（勿自行發明）")
        fallback.append("\n".join(lines))

    for raw in _as_list(structured_data.get("safe_havens_bought"))[:2]:
        _add("買超/強勢觀察", raw)
    for raw in _as_list(structured_data.get("price_volume_divergence"))[:2]:
        _add("量價/籌碼矛盾", raw)
    for raw in _as_list(structured_data.get("heavyweights_dumped"))[:1]:
        _add("賣超/弱勢對照", raw)

    return "\n\n".join(fallback) if fallback else "- 無足夠資料組成主角卡"


def _focus_stock_lines(structured_data):
    """Legacy one-liners; prefer protagonist cards in source body."""
    items = []
    for key in ("safe_havens_bought", "heavyweights_dumped", "price_volume_divergence", "ai_data_picks"):
        for raw in _as_list(structured_data.get(key))[:2]:
            text = _clean_source_text(raw).strip()
            if text and text not in items:
                items.append(text)
        if len(items) >= 3:
            break
    if not items:
        return "- 無"
    return "\n".join(f"- {item}" for item in items[:3])


def _timeframe_brief_lines(structured_data):
    """Keep multi-day stats short — bulk tables dilute the daily story."""
    history = structured_data.get("timeframe_history") or {}
    recent_results = _as_list(history.get("recent_results"))
    lines = []
    # Natural, data-driven reliability note (often: 20D leads short windows).
    reliability = structured_data.get("horizon_reliability") or {}
    one_liner = reliability.get("one_liner") or history.get("reliability_note") or ""
    if not one_liner:
        try:
            from modules.horizon_reliability import reliability_one_liner

            one_liner = reliability_one_liner(history.get("horizon_performance"))
        except Exception:
            one_liner = ""
    if one_liner:
        lines.append(f"- {one_liner}")
    # Short windows only as rhythm context, not the main scoreboard.
    for row in _as_list(history.get("horizon_performance")):
        if str(row.get("horizon_days")) in {"1", "1D"} or row.get("horizon_days") == 1:
            hit_rate = row.get("hit_rate")
            hit_text = f"{float(hit_rate) * 100:.0f}%" if hit_rate is not None else "N/A"
            lines.append(
                f"- 近端 1D 命中率 {hit_text}（樣本 {row.get('samples', 'N/A')}；僅節奏參考）"
            )
            break
    for row in _as_list(history.get("horizon_performance")):
        if int(row.get("horizon_days") or 0) == 20:
            hit_rate = row.get("hit_rate")
            hit_text = f"{float(hit_rate) * 100:.0f}%" if hit_rate is not None else "N/A"
            excess = row.get("avg_excess")
            excess_text = f"{float(excess):+.1f}%" if excess is not None else "N/A"
            lines.append(
                f"- 20D 命中率 {hit_text}、超額 {excess_text}（樣本 {row.get('samples', 'N/A')}；可信度主指標）"
            )
            break
    if recent_results:
        lines.append("- 與今日相關的最近驗收：")
        seen = set()
        for row in recent_results[:3]:
            if not isinstance(row, dict):
                continue
            key = (row.get("ticker"), row.get("horizon_days"), row.get("result_desc"))
            if key in seen:
                continue
            seen.add(key)
            hit = row.get("hit")
            hit_text = "命中" if hit == 1 else ("失準" if hit == 0 else "未判定")
            lines.append(
                f"  - {row.get('ticker', '')} {row.get('name', '')}："
                f"{row.get('horizon_days')}D {hit_text}；{_clean_source_text(row.get('result_desc') or '')}"
            )
    return "\n".join(lines) if lines else "- 無（本節可略）"


def build_notebooklm_source(structured_data, date_str=None):
    """
    Facts-only NotebookLM source. No dramatic narration, no host lines,
    no production directions (put those in notebooklm_prompt).
    """
    structured_data = structured_data or {}
    date_str = date_str or structured_data.get("date", "")
    label = _editorial_label(structured_data) or "未分類"
    placement = _validation_placement(structured_data)
    confidence = ""
    frame = structured_data.get("editorial_frame") or {}
    if isinstance(frame, dict) and frame.get("confidence") not in (None, ""):
        confidence = f"{frame.get('confidence')}/100"

    reliability = structured_data.get("horizon_reliability") or {}
    ruler_line = reliability.get("one_liner") or (
        structured_data.get("timeframe_history") or {}
    ).get("reliability_note") or ""
    if not ruler_line:
        try:
            from modules.horizon_reliability import reliability_one_liner

            ruler_line = reliability_one_liner(
                (structured_data.get("timeframe_history") or {}).get("horizon_performance")
            )
        except Exception:
            ruler_line = ""
    ruler_bullet = (
        f"- 時間尺度：{_clean_source_text(ruler_line)}"
        if ruler_line
        else "- 時間尺度：隔日～5D＝節奏；約 20 交易日＝趨勢成敗主尺子（目前樣本優先）"
    )

    return f"""# NotebookLM 乾淨資料源：{date_str} 台股盤後

用途：僅供 NotebookLM 引用的可驗證資料。不含主持人口吻、不含導演指令、不含模板解讀。
解讀權重：短線節奏可提；假說／選標成敗以中期趨勢窗（≈20 交易日）為主。

## 1. 市場總覽

- 日期：{structured_data.get("date", date_str)}
- 大盤：{_clean_source_text(structured_data.get("index_action", "N/A"))}
- 主軸標籤：{label}{f'（信心 {confidence}）' if confidence else ''}
- 短線對照在節目中的位置：{placement}（lead / brief / omit；實際說法見主持人 prompt）
{ruler_bullet}

## 2. 昨日預測節奏（短線事實；非趨勢期末考）

{_validation_lines(
    structured_data.get("validation_results") or structured_data.get("prev_day_picks_result"),
    structured_data=structured_data,
)}

## 3. 法人買超（前段）

{_line_items(structured_data.get("safe_havens_bought"), limit=5)}

## 4. 法人賣超（前段）

{_line_items(structured_data.get("heavyweights_dumped"), limit=5)}

## 5. 量價 / 籌碼矛盾

{_line_items(structured_data.get("price_volume_divergence"), limit=3)}

## 6. 今日主角股卡片（最多 3 檔，主持人優先講這幾張）

{_build_protagonist_cards(structured_data, limit=3)}

## 7. 短線節奏卡（1～5 日；僅參考）

{_short_term_pick_lines(structured_data)}

## 8. 明日／追蹤標的（節奏 + 趨勢兩層）

{_prediction_lines(structured_data.get("prediction_targets"), limit=2)}

## 9. 族群與個股對應

{_sector_lines(structured_data.get("sectors"), limit=5)}

## 10. 商品行情

{_line_items(structured_data.get("commodities"), limit=4)}

## 11. 外部事件（已去重，最多 2 則）

{_event_lines(structured_data.get("supply_chain_event_radar"), limit=2)}

## 12. 策略摘要（一句即可）

- 穩健：{_clean_source_text(structured_data.get("conservative_strategy", "N/A"))}
- 波段：{_clean_source_text(structured_data.get("aggressive_strategy", "N/A"))}

## 13. 中期趨勢背景（≈20 交易日；可略讀；極端報酬已標註）

{_long_term_pick_lines(structured_data)}

## 14. 多日統計摘要（趨勢窗優先；可略讀）

{_timeframe_brief_lines(structured_data)}
"""


def save_notebooklm_source(structured_data, reports_dir, date_str=None):
    from modules.report_paths import daily_artifact_write_path

    date_str = date_str or structured_data.get("date", "")
    filepath = daily_artifact_write_path(reports_dir, date_str, "notebooklm_source")
    content = build_notebooklm_source(structured_data, date_str=date_str)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath


def load_structured_data(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
