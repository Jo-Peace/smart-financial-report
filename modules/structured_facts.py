"""
Deterministic market facts for structured_data.

Gemini still extracts narrative fields (sectors, strategies, prediction targets).
Numeric / leaderboard fields are built from TWSE / yfinance payloads already
fetched in /1, then force-merged so packaging cannot inherit hallucinated amounts.
"""

from __future__ import annotations

from modules.prediction_validator import format_validation_results_for_structured_data

# Fields owned by local market data (local always wins on merge).
LOCAL_OWNED_FIELDS = (
    "date",
    "index_action",
    "heavyweights_dumped",
    "safe_havens_bought",
    "commodities",
    "prev_day_picks_result",
)


def _normalize_ticker(value) -> str:
    return str(value or "").replace(".TW", "").replace(".TWO", "").strip().upper()


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
            parts.append(f"收${price:g}")
        if pct is not None:
            parts.append(f"{pct:+.2f}%")
        if parts:
            items.append(f"{short} ({', '.join(parts)})")
        else:
            items.append(short)
    return items


def build_local_structured_facts(
    *,
    date_str: str,
    market_data: dict | None = None,
    institutional_data: dict | None = None,
    volume_data: list | None = None,
    commodity_data: dict | None = None,
    validation_results: list | None = None,
    top_n: int = 5,
) -> dict:
    """
    Build the factual subset of structured_data from already-fetched market payloads.
    volume_data is accepted for future volume-rank annotations; not required today.
    """
    _ = volume_data  # reserved
    buys, sells = _build_institutional_lists(institutional_data, top_n=top_n)
    facts = {
        "date": date_str,
        "index_action": _build_index_action(market_data),
        "safe_havens_bought": buys,
        "heavyweights_dumped": sells,
        "commodities": _build_commodities(commodity_data),
        "prev_day_picks_result": format_validation_results_for_structured_data(validation_results),
    }
    return facts


def format_ground_truth_block(local_facts: dict | None) -> str:
    """Human-readable ground truth block injected into the extraction prompt."""
    local_facts = local_facts or {}
    lines = [
        "LOCAL MARKET GROUND TRUTH (TWSE / yfinance — do NOT invent different amounts or rankings):",
        f"- date: {local_facts.get('date') or 'N/A'}",
        f"- index_action: {local_facts.get('index_action') or 'N/A'}",
        f"- safe_havens_bought (外資買超前段): {local_facts.get('safe_havens_bought') or []}",
        f"- heavyweights_dumped (外資賣超前段): {local_facts.get('heavyweights_dumped') or []}",
        f"- commodities: {local_facts.get('commodities') or []}",
        f"- prev_day_picks_result (本機驗收): {local_facts.get('prev_day_picks_result') or []}",
        "Copy these factual fields as-is when present. Focus extraction on narrative fields:",
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
            continue
        merged[key] = value
    return merged


def summarize_local_facts(local_facts: dict | None) -> str:
    local_facts = local_facts or {}
    buys = local_facts.get("safe_havens_bought") or []
    sells = local_facts.get("heavyweights_dumped") or []
    commodities = local_facts.get("commodities") or []
    index_action = local_facts.get("index_action") or "N/A"
    return (
        f"大盤={index_action}；買超 {len(buys)} 檔；賣超 {len(sells)} 檔；"
        f"商品 {len(commodities)} 項；驗收 {len(local_facts.get('prev_day_picks_result') or [])} 筆"
    )
