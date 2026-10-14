"""
Deterministic market facts for structured_data.

Gemini still extracts narrative fields (sectors, strategies, prediction targets).
Numeric / leaderboard fields are built from TWSE / yfinance payloads already
fetched in /1, then force-merged so packaging cannot inherit hallucinated amounts.
"""

from __future__ import annotations

from modules.prediction_validator import format_validation_results_for_structured_data
from modules.tickers import bare_ticker

# Fields owned by local market data (local always wins on merge).
LOCAL_OWNED_FIELDS = (
    "date",
    "index_action",
    "heavyweights_dumped",
    "safe_havens_bought",
    "trust_conflicts",
    "commodities",
    "prev_day_picks_result",
    "global_overnight",
    "market_extras",
    "top_value_stocks",
    "data_scope",
)

# Local-owned fields that must never fall back to the AI's value when the local
# fetch failed: an explicit marker is written instead.
INDEX_MISSING_TEXT = "大盤資料未取得（不得引用點數）"
STRICT_LOCAL_FIELDS = {
    "index_action": INDEX_MISSING_TEXT,
    "global_overnight": [],
    "market_extras": [],
    "top_value_stocks": [],
}


def _normalize_ticker(value) -> str:
    return bare_ticker(value)


def _float(value, default=None):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _market_row(market_data: dict | None, *candidates: str) -> dict | None:
    market_data = market_data or {}
    for raw in candidates:
        for key in (raw, f"{raw}.TW", f"{raw}.TWO", _normalize_ticker(raw)):
            row = market_data.get(key)
            if row:
                return row
    # Loose match on normalized symbol field
    wanted = {_normalize_ticker(c) for c in candidates}
    for key, row in market_data.items():
        if not row:
            continue
        if _normalize_ticker(key) in wanted or _normalize_ticker(row.get("symbol")) in wanted:
            return row
    return None


def _format_amount_item(row: dict) -> str:
    name = str(row.get("name") or row.get("id") or "").strip()
    ticker = str(row.get("id") or "").strip()
    label = name or ticker
    # Prefer "聯電" style; keep ticker when name missing.
    if name and ticker and ticker not in name and name.startswith("00"):
        label = f"{ticker} {name}"
    amount = _float(row.get("est_amount"))
    if amount is None:
        return label
    return f"{label} ({amount:+.1f}億)"


def _build_index_action(market_data: dict | None) -> str | None:
    row = _market_row(market_data, "^TWII", "TWII", "IX0001")
    if not row:
        return None
    change = _float(row.get("change"))
    pct = _float(row.get("pct_change"))
    if change is None and pct is None:
        return None
    if change is None:
        direction = "上漲" if pct > 0 else ("下跌" if pct < 0 else "平盤")
        return f"{direction}（{pct:+.2f}%）"
    if abs(change) < 1e-9:
        direction = "平盤"
    elif change > 0:
        direction = "上漲"
    else:
        direction = "下跌"
    points = abs(change)
    # Index points: keep one decimal when fractional, else integer-like display.
    point_text = f"{points:.1f}".rstrip("0").rstrip(".")
    text = f"{direction} {point_text}點"
    if pct is not None:
        text += f"（{pct:+.2f}%）"
    return text


def _build_institutional_lists(institutional_data: dict | None, top_n: int = 5) -> tuple[list[str], list[str]]:
    institutional_data = institutional_data or {}
    buys = [
        _format_amount_item(row)
        for row in (institutional_data.get("top_buy") or [])[:top_n]
        if row
    ]
    sells = [
        _format_amount_item(row)
        for row in (institutional_data.get("top_sell") or [])[:top_n]
        if row
    ]
    return buys, sells


def _fmt_quote(price: float) -> str:
    """
    Quote text without losing digits: 2 decimals at/above 10, 4 below (the
    precision get_global_overnight / get_commodity_data round to), trailing
    zeros trimmed. ``{:g}`` kept only 6 significant digits, so NQ=F 25012.75
    was published as 25012.8.
    """
    text = f"{price:.2f}" if abs(price) >= 10 else f"{price:.4f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _build_commodities(commodity_data: dict | None) -> list[str]:
    items = []
    for _symbol, row in (commodity_data or {}).items():
        if not row:
            continue
        name = str(row.get("name") or _symbol).split("(")[0].strip()
        # Prefer short Chinese label: "黃金 (Gold)" -> "黃金"
        short = name.split()[0] if name else str(_symbol)
        price = _float(row.get("price"))
        pct = _float(row.get("pct_change"))
        parts = []
        if price is not None:
            # Futures at 16:10 Taipei are a live Asian-session quote, not a settlement.
            label = "報價" if row.get("quote_kind") == "latest_quote" else "收"
            parts.append(f"{label}${_fmt_quote(price)}")
        if pct is not None:
            basis = "1日" if row.get("change_basis") == "1d" else ""
            parts.append(f"{basis}{pct:+.2f}%")
        if parts:
            items.append(f"{short} ({', '.join(parts)})")
        else:
            items.append(short)
    return items


def _build_global_overnight(global_overnight: dict | None) -> list[str]:
    """['台積電ADR TSM 2026-10-12 收$310.5 (-1.20%)', ...] — only locally fetched quotes."""
    items = []
    for _symbol, row in (global_overnight or {}).items():
        if not row:
            continue
        price = _float(row.get("price"))
        pct = _float(row.get("pct_change"))
        if price is None:
            continue
        label = row.get("label") or _symbol
        bar_date = row.get("bar_date") or "日期不明"
        kind = "收" if row.get("quote_kind") == "close" else "報價"
        text = f"{label} {bar_date} {kind}{_fmt_quote(price)}"
        if pct is not None:
            text += f" ({pct:+.2f}%)"
        items.append(text)
    return items


def _build_top_value(value_ranking: dict | None, top_n: int = 10) -> list[str]:
    rows = (value_ranking or {}).get("stocks") or []
    out = []
    for row in rows[:top_n]:
        value = _float(row.get("trade_value_yi"))
        if value is None:
            continue
        out.append(f"{row.get('name') or row.get('id')} 成交值{value:.0f}億")
    return out


def _data_scope(institutional_data: dict | None) -> dict:
    institutional_data = institutional_data or {}
    scope = institutional_data.get("market_scope") or "TWSE_listed"
    label = {"TWSE_listed": "上市", "TWSE_TPEx": "上市＋上櫃"}.get(scope, str(scope))
    pricing = institutional_data.get("pricing") or {}
    return {
        "institutional_scope": scope,
        "institutional_scope_label": label,
        "institutional_data_date": institutional_data.get("data_date"),
        "amount_price_date": pricing.get("price_date"),
        "amount_basis": "同日收盤價 × 外資買賣超股數（估算）" if pricing.get("basis") != "shares" else "無同日股價，僅張數",
    }


def build_local_structured_facts(
    *,
    date_str: str,
    market_data: dict | None = None,
    institutional_data: dict | None = None,
    volume_data: list | None = None,
    commodity_data: dict | None = None,
    validation_results: list | None = None,
    top_n: int = 5,
    global_overnight: dict | None = None,
    market_extras: list | None = None,
    value_ranking: dict | None = None,
) -> dict:
    """
    Build the factual subset of structured_data from already-fetched market payloads.
    volume_data is accepted for future volume-rank annotations; not required today.

    ``global_overnight`` / ``market_extras`` / ``value_ranking`` are optional
    locally fetched facts; when absent the fields are empty lists, which means
    「未取得」— prompts must not cite such numbers.
    """
    _ = volume_data  # reserved
    buys, sells = _build_institutional_lists(institutional_data, top_n=top_n)
    from modules.institutional_ranking import format_conflict_line

    conflicts = [
        format_conflict_line(row)
        for row in (institutional_data or {}).get("trust_conflicts") or []
        if row
    ]
    facts = {
        "date": date_str,
        "index_action": _build_index_action(market_data),
        "safe_havens_bought": buys,
        "heavyweights_dumped": sells,
        "trust_conflicts": conflicts,
        "commodities": _build_commodities(commodity_data),
        "prev_day_picks_result": format_validation_results_for_structured_data(validation_results),
        "global_overnight": _build_global_overnight(global_overnight),
        "market_extras": [str(x) for x in (market_extras or []) if x],
        "top_value_stocks": _build_top_value(value_ranking),
        "data_scope": _data_scope(institutional_data),
    }
    return facts


def overnight_rule_text(local_facts: dict | None) -> str:
    """One-line citation rule for ADR / VIX / 匯率 / 費半 / 夜盤 numbers."""
    overnight = (local_facts or {}).get("global_overnight") or []
    if overnight:
        return (
            "海外／匯率數字只能引用本機數據：" + "；".join(overnight)
            + "。台指期夜盤、其他海外數字未提供者一律不得寫數字。"
        )
    return "本機未取得台積電 ADR、費半、VIX、匯率、台指期夜盤數據：標題、說明、口播都不得寫這些數字。"


def format_local_market_prompt_block(
    *,
    institutional_data: dict | None = None,
    global_overnight: dict | None = None,
    market_extras: dict | None = None,
    value_ranking: dict | None = None,
) -> str:
    """
    Daily-report prompt block with locally fetched, dated numbers. Anything not
    listed here (ADR, VIX, 匯率, 夜盤, 籌碼) must not be written as a number.
    """
    from modules.market_extras import format_extras_lines, missing_extras

    scope = _data_scope(institutional_data)
    lines = ["# 本機盤後補充數據（唯一可引用來源；未列出的海外／期貨／籌碼數字一律不得寫）"]
    lines.append(
        f"- 法人買賣超範圍：{scope['institutional_scope_label']}；金額＝{scope['amount_basis']}；"
        f"股價日 {scope['amount_price_date'] or scope['institutional_data_date'] or '未知'}。"
        "請寫「上市外資買超第 X 名」，不要寫「全市場」。"
    )
    value_items = _build_top_value(value_ranking, top_n=10)
    if value_items:
        lines.append(f"- 成交值前10（上市個股，不含 ETF）：{'、'.join(value_items)}")
        etfs = [
            f"{r.get('name') or r.get('id')} {r['trade_value_yi']:.0f}億"
            for r in ((value_ranking or {}).get("etfs") or [])[:5]
            if r.get("trade_value_yi") is not None
        ]
        if etfs:
            lines.append(f"- ETF 成交值前段（另列，不當個股主角）：{'、'.join(etfs)}")
    else:
        lines.append("- 成交值排行：未取得（只能用成交股數榜，且不得把「未進股數榜」解讀為沒跟量）")
    overnight = _build_global_overnight(global_overnight)
    lines.append(f"- 海外／匯率（含 K 棒日期）：{'；'.join(overnight) if overnight else '未取得'}")
    extra_lines = format_extras_lines(market_extras)
    if extra_lines:
        lines.extend(f"- {line}" for line in extra_lines)
    missing = missing_extras(market_extras)
    if missing:
        lines.append(f"- 未取得（不得寫數字）：{'、'.join(missing)}、台指期夜盤")
    else:
        lines.append("- 未取得（不得寫數字）：台指期夜盤")
    lines.append(f"- 規則：{overnight_rule_text({'global_overnight': overnight})}")
    return "\n".join(lines)


def format_ground_truth_block(local_facts: dict | None) -> str:
    """Human-readable ground truth block injected into the extraction prompt."""
    local_facts = local_facts or {}
    lines = [
        "LOCAL MARKET GROUND TRUTH (TWSE / yfinance — do NOT invent different amounts or rankings):",
        f"- date: {local_facts.get('date') or 'N/A'}",
        f"- index_action: {local_facts.get('index_action') or 'N/A'}",
        f"- safe_havens_bought (外資買超前段): {local_facts.get('safe_havens_bought') or []}",
        f"- heavyweights_dumped (外資賣超前段): {local_facts.get('heavyweights_dumped') or []}",
        f"- trust_conflicts (土洋對作，只當備查不可講成主線): {local_facts.get('trust_conflicts') or []}",
        f"- commodities: {local_facts.get('commodities') or []}",
        f"- prev_day_picks_result (本機驗收): {local_facts.get('prev_day_picks_result') or []}",
        f"- global_overnight (本機海外/匯率，含 K 棒日期): {local_facts.get('global_overnight') or '未取得'}",
        f"- market_extras (本機期貨/籌碼): {local_facts.get('market_extras') or '未取得'}",
        f"- top_value_stocks (成交值前段): {local_facts.get('top_value_stocks') or []}",
        f"- data_scope: {local_facts.get('data_scope') or {}}",
        f"- RULE: {overnight_rule_text(local_facts)}",
        "Local fields are force-merged after extraction; do not output them. Focus extraction on narrative fields:",
        "sectors, ai_data_picks, prediction_targets, strategies, price_volume_divergence.",
    ]
    return "\n".join(lines)


def merge_structured_with_local_facts(ai_data: dict | None, local_facts: dict | None) -> dict:
    """
    Overlay local-owned fields onto a successful AI extraction.
    AI failure dicts are returned unchanged so pipeline_persist can hard-stop.
    """
    local_facts = local_facts or {}

    if not isinstance(ai_data, dict):
        return {"error": "Failed to extract structured data.", "raw_error": "non-dict response"}
    if ai_data.get("error"):
        return ai_data

    merged = dict(ai_data)
    for key in LOCAL_OWNED_FIELDS:
        value = local_facts.get(key)
        if value in (None, "", []):
            if key in STRICT_LOCAL_FIELDS and local_facts:
                # Local fetch failed: never keep an AI-written number for this field.
                merged[key] = STRICT_LOCAL_FIELDS[key]
            continue
        merged[key] = value
    return merged


def summarize_local_facts(local_facts: dict | None) -> str:
    local_facts = local_facts or {}
    buys = local_facts.get("safe_havens_bought") or []
    sells = local_facts.get("heavyweights_dumped") or []
    commodities = local_facts.get("commodities") or []
    index_action = local_facts.get("index_action") or "未取得"
    return (
        f"大盤={index_action}；買超 {len(buys)} 檔；賣超 {len(sells)} 檔；"
        f"商品 {len(commodities)} 項；海外 {len(local_facts.get('global_overnight') or [])} 項；"
        f"驗收 {len(local_facts.get('prev_day_picks_result') or [])} 筆"
    )
