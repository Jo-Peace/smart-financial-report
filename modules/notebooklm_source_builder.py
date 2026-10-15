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
    from modules.narrative_sanitize import sanitize_narrative_text

    return sanitize_narrative_text(value)


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
                f"趨勢成立（≈20 交易日）：{_clean_source_text(trend) or '（中期假說見觸發理由；尚未經本機回測證實）'}；"
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


def _similar_session_lines(rows, limit=2):
    rows = _as_list(rows)[:limit]
    if not rows:
        return "- 無足夠相似歷史盤可對照"
    lines = []
    for row in rows:
        if not isinstance(row, dict):
            lines.append(f"- {_clean_source_text(row)}")
            continue
        date = row.get("date") or "未知日"
        score = row.get("score", "")
        index_action = _clean_source_text(row.get("index_action") or "")
        bought = _clean_source_text(row.get("bought") or "無")
        sold = _clean_source_text(row.get("sold") or "無")
        reasons = "、".join(_clean_source_text(x) for x in _as_list(row.get("reasons"))[:3])
        preds = []
        for pred in _as_list(row.get("predictions"))[:2]:
            if isinstance(pred, dict):
                preds.append(
                    f"{pred.get('ticker', '')} {pred.get('name', '')} {pred.get('direction', '')}".strip()
                )
            elif pred:
                preds.append(_clean_source_text(pred))
        pred_text = "；".join(p for p in preds if p) or "無正式預測"
        results = "；".join(_clean_source_text(x) for x in _as_list(row.get("next_day_results"))[:3]) or "尚無隔日節奏"
        next_date = row.get("next_date") or "無"
        lines.append(
            f"- {date}（分數 {score}）：大盤 {index_action or 'N/A'}；"
            f"買超 {bought}；賣超 {sold}；"
            f"對照原因 {reasons or '結構相近'}；"
            f"當時預測 {pred_text}；隔日（{next_date}）{results}"
        )
    return "\n".join(lines)


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
            fresh = event.get("freshness") or "new"
            tag = "舊事件背景" if fresh == "stale" else ("連日重複" if fresh == "repeat" else "新事件")
            lines.append(f"- {title}；影響分數 {score}；新舊={tag}；可能影響：{roles}")
        else:
            lines.append(f"- {_clean_source_text(event)}")
    return "\n".join(lines)


# |return| at/above which a window is flagged as an outlier (20D gets more room).
EXTREME_RETURN_LIMITS = {"1D": 40.0, "3D": 40.0, "5D": 40.0, "10D": 40.0, "20D": 50.0}


def _flag_extreme_returns(text: str) -> str:
    """Annotate extreme short-window % so hosts don't treat outliers as normal trend."""
    cleaned = _clean_source_text(text)

    def _repl(match):
        window = match.group(1)
        sign = match.group(2) or ""
        num = float(match.group(3))
        signed = num if sign != "-" else -num
        limit = EXTREME_RETURN_LIMITS.get(window.upper())
        if limit is not None and abs(signed) >= limit:
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
        return f"- （時間尺度）1～5 日＝短線節奏參考。\n{body}"
    lines = ["- （時間尺度）1～5 日＝短線節奏參考。"]
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


def _trend_as_of(structured_data, theme):
    """'資料截至 YYYY-MM-DD' for a long-term pick whose ticker has trend data."""
    match = re.search(r"\b(\d{4,6}[A-Z]?)\b", str(theme or ""))
    if not match:
        return ""
    trends = ((structured_data.get("timeframe_history") or {}).get("recent_trends") or {}).get(match.group(1)) or {}
    latest = trends.get("latest_date")
    return f"資料截至 {latest}" if latest else ""


def _long_term_pick_lines(structured_data):
    picks = _as_list(structured_data.get("long_term_picks"))
    if not picks:
        return "- 無"
    lines = [
        "- （時間尺度）中期背景：約 20 交易日的走勢脈絡，只供參考；這些標的未進驗收帳本，"
        "沒有命中率紀錄，勿拿本機命中率替它背書。"
    ]
    for pick in picks[:2]:
        if isinstance(pick, dict):
            theme = _flag_extreme_returns(pick.get("theme", ""))
            horizon = _clean_source_text(pick.get("horizon", ""))
            thesis = _flag_extreme_returns(pick.get("thesis", ""))
            evidence = _flag_extreme_returns(pick.get("evidence", ""))
            direction = _clean_source_text(pick.get("direction", ""))
            as_of = _clean_source_text(pick.get("as_of", ""))
            if pick.get("placeholder"):
                as_of_text = ""
            elif as_of:
                as_of_text = f"資料截至 {as_of}；"
            else:
                # Older picks carry no as_of: fall back to the trend data date.
                fallback = _trend_as_of(structured_data, pick.get("theme", ""))
                as_of_text = f"{fallback}；" if fallback else ""
            meta = (f"方向 {direction}；" if direction else "") + as_of_text
            lines.append(
                f"- {theme}：{meta}時間窗 {horizon}；驗收：未進帳本；主軸：{thesis}；證據：{evidence}"
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


def _admission_label(level: str) -> str:
    return {
        "official": "正式追蹤（可當下一交易日主盯）",
        "talk_only": "僅可口述（矛盾／背景，不可講成已確認趨勢）",
        "context": "對照股（不要當主盯）",
    }.get(level or "", "觀察")


def _render_broadcast_cards(facts: dict) -> str:
    cards = facts.get("heroes") or []
    if not cards:
        return "- 無足夠資料組成主角卡"
    blocks = []
    for i, card in enumerate(cards[:3], start=1):
        label = f"{card.get('ticker', '')} {card.get('name', '')}".strip() or f"標的{i}"
        from modules.broadcast_facts import format_amount_yi, format_pct, volume_say_bit

        amount = format_amount_yi(card.get("foreign_amount")) if card.get("foreign_amount") is not None else ""
        pct = format_pct(card.get("pct_change")) if card.get("pct_change") is not None else ""
        vol = volume_say_bit(card.get("volume_rank"), card.get("vol_ratio"), card.get("off_rank"))
        streak = card.get("foreign_streak_days")
        if card.get("sold_today") or (card.get("foreign_amount") is not None and card.get("foreign_amount") < 0):
            streak_text = "今日賣超（連續買超中斷，勿唸舊天數）"
        elif streak not in (None, 0):
            from modules.foreign_streak import format_streak_text

            streak_text = format_streak_text(streak, card.get("foreign_streak_complete", True)) or f"{streak} 個交易日"
        else:
            streak_text = "資料不足（勿自行發明連續天數）"
        lines = [
            f"### 主角 {i}：{label}",
            f"- 入場層級：{_admission_label(card.get('admission'))}",
            f"- 層級理由：{card.get('admission_reason') or '無'}",
            f"- 可唸事實：{card.get('say_line') or label}",
        ]
        if amount:
            lines.append(f"- 外資金額：{amount}")
        if card.get("close"):
            close_text = f"- 收盤：{card['close']:g}"
            if card.get("ma20"):
                gap = card.get("ma20_gap_pct")
                close_text += f"（MA20 {card['ma20']:g}" + (f"，距 MA20 {gap:+.1f}%）" if gap is not None else "）")
            lines.append(close_text)
        if pct:
            lines.append(f"- 漲跌：{pct}")
        if vol:
            lines.append(f"- 量能：{vol}")
        lines.append(f"- 外資連續買超：{streak_text}")
        if card.get("invalidation"):
            lines.append(f"- 失效條件：{card['invalidation']}")
        else:
            lines.append("- 失效條件：資料源未指定（勿自行發明）")
        if card.get("caution"):
            lines.append("- 口播限制：這是單日矛盾／節奏，不要講成隔日必漲；中期是否成立要看後續多日資料")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


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
    """
    Keep multi-day stats short — bulk tables dilute the daily story.

    Only current-method ledger rows are shown (TWSE-session windows, excess vs
    the same-window TWII, CI vs 50% baseline); legacy calendar-day stats in an
    archived structured_data are not republished.
    """
    from modules.horizon_reliability import (
        _fmt_ci,
        _fmt_hit,
        analyze_horizon_reliability,
        current_horizon_rows,
        current_reliability_line,
        ledger_disclosure,
    )

    history = structured_data.get("timeframe_history") or {}
    rows = current_horizon_rows(structured_data)
    lines = [f"- {_clean_source_text(current_reliability_line(structured_data))}"]
    if rows:
        info = analyze_horizon_reliability(rows)
        for days in (1, 20):
            row = info["by_days"].get(days)
            if not row or row.get("hit_rate") is None:
                continue
            if days == 20 and info.get("primary_leads"):
                role = "主指標"
            elif days == 1:
                role = "僅節奏參考"
            else:
                role = "對照，未證實較準"
            excess = row.get("avg_excess")
            excess_text = f"、平均超額 {float(excess):+.1f}%" if excess is not None else ""
            disclosure = ledger_disclosure(row)
            disclosure_text = f"；樣本組成：{disclosure}" if disclosure else ""
            lines.append(
                f"- {days}D 超額命中率 {_fmt_hit(row['hit_rate'])}（{_fmt_ci(row)}，n={row.get('samples', 'N/A')}；"
                f"基準 50%{excess_text}；{role}{disclosure_text}）"
            )
    recent_results = [
        row for row in _as_list(history.get("recent_results")) if isinstance(row, dict) and row.get("status")
    ]
    if rows and recent_results:
        lines.append("- 與今日相關的最近驗收（收盤對收盤）：")
        seen = set()
        for row in recent_results[:3]:
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

    from modules.broadcast_facts import attach_broadcast_facts, ensure_broadcast_facts

    structured_data = attach_broadcast_facts(structured_data)
    facts = ensure_broadcast_facts(structured_data)
    opening = facts.get("opening") or {}
    ruler_line = facts.get("ruler_line") or ""
    if not ruler_line:
        from modules.horizon_reliability import DEFAULT_RULER_LINE

        ruler_line = DEFAULT_RULER_LINE
    ruler_bullet = f"- 時間窗可信度：{_clean_source_text(ruler_line)}"
    from modules.market_calendar import calendar_one_liner, event_line_text, nearby_market_events

    calendar_events = structured_data.get("calendar_events") or nearby_market_events(date_str)
    calendar_line = calendar_one_liner(date_str)
    calendar_bullet = f"- 日曆：{_clean_source_text(calendar_line)}" if calendar_line else ""
    calendar_backup = "\n".join(
        # Estimated windows keep the （估計，非事實） label (event_line_text).
        f"- {row.get('date')}（{row.get('when')}）：{_clean_source_text(event_line_text(row))}"
        for row in (calendar_events or [])[:8]
        if isinstance(row, dict)
    ) or "- （近窗無日曆事件）"
    hook = _clean_source_text(opening.get("hook_line") or "")
    contrast = _clean_source_text(opening.get("contrast_line") or "")
    from modules.session_calendar import last_output_from_structured, session_lexicon

    lex = session_lexicon(
        date_str or structured_data.get("date"),
        last_output_date=last_output_from_structured(structured_data),
    )
    prev_section = lex.get("prev_section") or "昨日預測節奏"
    next_section = lex.get("next_section") or "明日條件"
    next_label = lex.get("next_label") or "明天"

    return f"""# NotebookLM 乾淨資料源：{date_str} 台股盤後

用途：僅供 NotebookLM 引用的可驗證資料。不含主持人口吻、不含導演指令。
數字以本節「必引」與「主角卡」為準；內部量化分（P/E/F/V）不是口播內容。
解讀權重：短線節奏可提；哪個時間窗較準只依本節「時間窗可信度」一行（未經統計證實前，勿稱任一窗較準或當成敗尺子）。

## 1. 今日必引數字（開場先用這段，不要自行重算）

- 日期：{structured_data.get("date", date_str)}
- 大盤：{_clean_source_text(structured_data.get("index_action", "N/A"))}
- 主軸標籤：{label}{f'（信心 {confidence}）' if confidence else ''}
- 開場矛盾：{hook or "見主角卡"}
- 買賣對照：{contrast or "見主角卡"}
- 短線對照位置：{placement}（實際講法見主持人 prompt）
{ruler_bullet}
{calendar_bullet}

## 2. 主角股卡片（最多 3 檔；優先講「正式追蹤」）

{_render_broadcast_cards(facts)}

## 3. {prev_section}（短線事實；非趨勢期末考）

{_validation_lines(
    structured_data.get("validation_results") or structured_data.get("prev_day_picks_result"),
    structured_data=structured_data,
)}

## 4. {next_section}

- 正式追蹤：見主角卡標成「正式追蹤」的檔；失效條件必須原句唸出。
- 僅可口述：見主角卡標成「僅可口述」的檔；可以當矛盾，不可以當「{next_label}追這檔」。
{_prediction_lines(structured_data.get("prediction_targets"), limit=2)}

## 5. 備查（不要整段朗讀）

### 法人買超前段
{_line_items(structured_data.get("safe_havens_bought"), limit=5)}

### 法人賣超前段
{_line_items(structured_data.get("heavyweights_dumped"), limit=5)}

### 土洋對作（備查；不要當開場主線）
{_line_items(structured_data.get("trust_conflicts"), limit=5)}

### 量價 / 籌碼矛盾原文
{_line_items(structured_data.get("price_volume_divergence"), limit=3)}

### 相似歷史盤（本機結構化比對，不是模型記憶）
{_similar_session_lines(structured_data.get("similar_sessions"), limit=2)}

### 商品
{_line_items(structured_data.get("commodities"), limit=4)}

### 日曆約束（解讀用，不要整段朗讀）
{calendar_backup}

### 外部事件（已去重，最多 2 則）
{_event_lines(structured_data.get("supply_chain_event_radar"), limit=2)}

### 策略一句
- 穩健：{_clean_source_text(structured_data.get("conservative_strategy", "N/A"))}
- 波段：{_clean_source_text(structured_data.get("aggressive_strategy", "N/A"))}

### 中期趨勢背景（未進驗收帳本；可略讀；極端報酬已標註）
{_long_term_pick_lines(structured_data)}

### 多日統計（交易日窗、扣同窗大盤；可略讀）
{_timeframe_brief_lines(structured_data)}
"""


def _assert_no_narrative_residue(content):
    """Write-time check: intent language left after sanitizing is rewritten and reported."""
    from modules.narrative_sanitize import find_narrative_residue, sanitize_narrative_text

    residue = find_narrative_residue(content)
    if not residue:
        return content
    print(f"  ⚠️  NotebookLM 資料源仍有 {len(residue)} 處意圖用語，寫檔前再改寫：")
    for row in residue[:5]:
        print(f"     - {row['term']}：…{row['snippet']}…")
    cleaned = sanitize_narrative_text(content)
    left = find_narrative_residue(cleaned)
    if left:
        print(f"  ⚠️  改寫後仍殘留 {len(left)} 處（請檢查 narrative_sanitize 規則）：")
        for row in left[:5]:
            print(f"     - {row['term']}：…{row['snippet']}…")
    return cleaned


def save_notebooklm_source(structured_data, reports_dir, date_str=None):
    from modules.report_paths import daily_artifact_write_path

    date_str = date_str or structured_data.get("date", "")
    filepath = daily_artifact_write_path(reports_dir, date_str, "notebooklm_source")
    content = build_notebooklm_source(structured_data, date_str=date_str)
    content = _assert_no_narrative_residue(content)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath


def load_structured_data(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
