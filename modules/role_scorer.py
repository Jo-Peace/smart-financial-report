"""
Quantitative market_role scores for title / NotebookLM / packaging ranking.

Scores are deterministic (no Gemini). They refine categorical base_role with:
- elasticity (who can move first / high beta packaging fit)
- foreign_buy_strength (rank + amount)
- volume_confirmation (量榜 / 量增 / 影子低量買超)
- validation_credibility (local tracking hit rate)

title_priority is a composite for 「誰該當標題/封面/主角卡」.
direction_indicator (台積電等) can score high on foreign flow but stay low on title_priority.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(BASE_DIR, "data", "stock_db.json")

# base_role → default elasticity prior
ELASTICITY_PRIOR = {
    "elastic_attack": 78,
    "theme_confirmation": 58,
    "risk_warning": 48,
    "indirect_infrastructure": 45,
    "defensive_income": 28,
    "direction_indicator": 22,
    "capital_vehicle": 18,
}

DIRECTION_TICKERS = {"2330", "2317", "2454"}


def _float(value, default=None):
    try:
        if value in (None, ""):
            return default
        if isinstance(value, str) and value.endswith("%"):
            return float(value[:-1])
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(n, lo=0, hi=100):
    return max(lo, min(hi, int(round(n))))


def normalize_ticker(value) -> str:
    return str(value or "").replace(".TW", "").replace(".TWO", "").strip().upper()


def extract_sid(value) -> str:
    m = re.search(r"\b(\d{4,6}[A-Za-z]?)\b", str(value or ""))
    return m.group(1) if m else ""


def parse_amount_yi(text) -> float | None:
    m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*億", str(text or ""))
    return float(m.group(1)) if m else None


def load_stock_db(path: str | None = None) -> dict:
    path = path or DEFAULT_DB
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _base_role_of(entry: dict | None, sid: str = "") -> str:
    entry = entry or {}
    role = (entry.get("market_role") or {}).get("base_role")
    if role:
        return str(role)
    if sid in DIRECTION_TICKERS or normalize_ticker(sid) in DIRECTION_TICKERS:
        return "direction_indicator"
    if str(sid).startswith("00"):
        return "capital_vehicle"
    return "theme_confirmation"


def _hit_rate_fraction(tracking: dict) -> float | None:
    if not tracking:
        return None
    raw = tracking.get("hit_rate")
    if isinstance(raw, (int, float)):
        return float(raw) if raw <= 1 else float(raw) / 100.0
    if isinstance(raw, str) and raw.strip():
        s = raw.strip().replace("%", "")
        try:
            v = float(s)
            return v / 100.0 if v > 1 else v
        except ValueError:
            return None
    hits = tracking.get("hit_count")
    n = tracking.get("prediction_count")
    if hits is not None and n:
        try:
            return float(hits) / float(n)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    return None


def score_elasticity(sid: str, entry: dict | None, base_role: str | None = None) -> tuple[int, list[str]]:
    entry = entry or {}
    base_role = base_role or _base_role_of(entry, sid)
    score = float(ELASTICITY_PRIOR.get(base_role, 50))
    evidence = [f"prior={base_role}:{int(score)}"]

    sid_n = normalize_ticker(sid)
    if sid_n in DIRECTION_TICKERS:
        score = min(score, 25)
        evidence.append("mega_cap_direction_cap")
    if sid_n.startswith("00"):
        score = min(score, 20)
        evidence.append("etf_or_vehicle_cap")

    sector = f"{entry.get('sector', '')} {entry.get('supply_chain_role', '')} {entry.get('name', '')}"
    if any(t in sector for t in ("記憶體", "DRAM", "HBM", "載板", "PCB", "散熱", "ODM", "面板")):
        score += 8
        evidence.append("elastic_sector_boost")
    if any(t in sector for t in ("金融", "銀行", "壽險", "電信")):
        score -= 10
        evidence.append("defensive_sector_penalty")

    return _clamp(score), evidence


def score_foreign_buy_strength(
    *,
    buy_rank: int | None = None,
    sell_rank: int | None = None,
    amount_yi: float | None = None,
    flow_state: str | None = None,
) -> tuple[int, list[str]]:
    score = 15.0
    evidence = []
    if buy_rank:
        score = max(score, 100 - (buy_rank - 1) * 8)  # rank1=100, rank5=68, rank10=28
        evidence.append(f"buy_rank={buy_rank}")
    if amount_yi is not None and amount_yi > 0:
        # 10億→+8, 30億→+18, 50億→+25 (soft)
        bonus = min(30, amount_yi * 0.6)
        score += bonus
        evidence.append(f"buy_amount={amount_yi:+.1f}億")
    if sell_rank:
        score -= max(0, 40 - (sell_rank - 1) * 3)
        evidence.append(f"sell_rank={sell_rank}")
    if amount_yi is not None and amount_yi < 0:
        score -= min(35, abs(amount_yi) * 0.5)
        evidence.append(f"sell_amount={amount_yi:.1f}億")
    if flow_state == "foreign_buy_focus" and buy_rank is None:
        score = max(score, 55)
        evidence.append("flow=foreign_buy_focus")
    if flow_state == "foreign_sell_risk":
        score = min(score, 35)
        evidence.append("flow=foreign_sell_risk")
    return _clamp(score), evidence


def score_volume_confirmation(
    *,
    volume_rank: int | None = None,
    vol_ratio: float | None = None,
    in_divergence: bool = False,
    divergence_text: str = "",
    buy_rank: int | None = None,
) -> tuple[int, list[str]]:
    score = 25.0
    evidence = []
    text = divergence_text or ""

    if volume_rank:
        score = max(score, 95 - (volume_rank - 1) * 6)  # rank1≈95, rank10≈41
        evidence.append(f"volume_rank={volume_rank}")
    if vol_ratio is not None:
        if vol_ratio >= 2:
            score += 20
            evidence.append(f"vol_ratio={vol_ratio}x")
        elif vol_ratio >= 1.3:
            score += 10
            evidence.append(f"vol_ratio={vol_ratio}x")
        elif vol_ratio <= 0.5 and buy_rank:
            # shadow accumulation: low volume + foreign buy
            score = max(score, 70)
            evidence.append("shadow_low_volume_buy")
    if in_divergence or any(k in text for k in ("量縮", "沒進", "榜外", "影子", "量不出", "未進")):
        if buy_rank or "買" in text:
            score = max(score, 72)
            evidence.append("divergence_shadow_or_offboard")
        else:
            score = max(score, 60)
            evidence.append("divergence_present")
    if any(k in text for k in ("天量", "爆量")):
        score = max(score, 75)
        evidence.append("extreme_volume_language")
    return _clamp(score), evidence


def score_validation_credibility(entry: dict | None) -> tuple[int, list[str]]:
    entry = entry or {}
    tracking = entry.get("tracking") or {}
    evidence = []
    n = int(tracking.get("prediction_count") or 0)
    if n <= 0:
        return 40, ["no_tracking_history"]  # neutral prior
    rate = _hit_rate_fraction(tracking)
    if rate is None:
        return 45, [f"predictions={n}, hit_rate=unknown"]
    # sample-size adjusted: 1 sample stays near 50, 5+ trusts rate more
    trust = min(1.0, n / 5.0)
    score = 50 * (1 - trust) + (rate * 100) * trust
    evidence.append(f"predictions={n}, hit_rate={rate:.0%}, trust={trust:.2f}")
    return _clamp(score), evidence


# Closed-loop: only trust hit-rate after enough local predictions.
HIT_RATE_MIN_SAMPLES = 3
# mult = HIT_RATE_MULT_FLOOR + HIT_RATE_MULT_SPAN * rate  (rate 0.5 → ~1.0)
HIT_RATE_MULT_FLOOR = 0.72
HIT_RATE_MULT_SPAN = 0.56  # rate 1.0 → 1.28, then clamped
HIT_RATE_MULT_MIN = 0.70
HIT_RATE_MULT_MAX = 1.18


def apply_hit_rate_title_discount(
    title_priority: int,
    entry: dict | None,
    *,
    min_samples: int = HIT_RATE_MIN_SAMPLES,
) -> tuple[int, list[str]]:
    """
    Scale title_priority by historical prediction hit-rate from stock_db tracking.

    - samples < min_samples: no change (avoid punishing new names)
    - low hit-rate chronic misses: down-rank as title/cover lead
    - high hit-rate with enough samples: mild boost
    """
    entry = entry or {}
    tracking = entry.get("tracking") or {}
    n = int(tracking.get("prediction_count") or 0)
    if n < min_samples:
        return int(title_priority), [f"hit_rate_track=skip samples={n}<{min_samples}"]

    rate = _hit_rate_fraction(tracking)
    if rate is None:
        return int(title_priority), [f"hit_rate_track=skip samples={n} rate=unknown"]

    mult = HIT_RATE_MULT_FLOOR + HIT_RATE_MULT_SPAN * float(rate)
    mult = max(HIT_RATE_MULT_MIN, min(HIT_RATE_MULT_MAX, mult))
    adjusted = _clamp(float(title_priority) * mult)
    return adjusted, [
        f"hit_rate_track samples={n} rate={rate:.0%} mult={mult:.2f} "
        f"title_priority {int(title_priority)}→{adjusted}"
    ]


def compute_title_priority(
    *,
    base_role: str,
    elasticity: int,
    foreign_buy: int,
    volume: int,
    validation: int,
    sid: str = "",
) -> tuple[int, list[str]]:
    """
    Who should star on title / cover / NotebookLM cards.
    Direction indicators and ETFs are down-ranked as lead stars.
    """
    evidence = []
    # Weighted: elasticity + flow still lead; validation (hit-rate based) denser than before.
    raw = elasticity * 0.32 + foreign_buy * 0.28 + volume * 0.22 + validation * 0.18
    evidence.append(f"weighted={raw:.1f}")

    sid_n = normalize_ticker(sid)
    if base_role == "direction_indicator" or sid_n in DIRECTION_TICKERS:
        raw *= 0.45
        evidence.append("direction_indicator_title_penalty")
    elif base_role == "capital_vehicle" or sid_n.startswith("00"):
        raw *= 0.40
        evidence.append("vehicle_title_penalty")
    elif base_role == "elastic_attack":
        raw *= 1.12
        evidence.append("elastic_title_boost")
    elif base_role == "defensive_income":
        raw *= 0.75
        evidence.append("defensive_title_penalty")

    # Strong buy + elastic = hero
    if foreign_buy >= 70 and elasticity >= 60:
        raw += 8
        evidence.append("flow_x_elastic_boost")

    return _clamp(raw), evidence


def score_ticker(
    sid: str,
    entry: dict | None = None,
    *,
    buy_rank: int | None = None,
    sell_rank: int | None = None,
    amount_yi: float | None = None,
    volume_rank: int | None = None,
    vol_ratio: float | None = None,
    in_divergence: bool = False,
    divergence_text: str = "",
    flow_state: str | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    entry = entry or {}
    base_role = _base_role_of(entry, sid)
    elast, e1 = score_elasticity(sid, entry, base_role)
    foreign, e2 = score_foreign_buy_strength(
        buy_rank=buy_rank,
        sell_rank=sell_rank,
        amount_yi=amount_yi,
        flow_state=flow_state,
    )
    volume, e3 = score_volume_confirmation(
        volume_rank=volume_rank,
        vol_ratio=vol_ratio,
        in_divergence=in_divergence,
        divergence_text=divergence_text,
        buy_rank=buy_rank,
    )
    valid, e4 = score_validation_credibility(entry)
    title, e5 = compute_title_priority(
        base_role=base_role,
        elasticity=elast,
        foreign_buy=foreign,
        volume=volume,
        validation=valid,
        sid=sid,
    )
    title, e6 = apply_hit_rate_title_discount(title, entry)
    e5 = list(e5) + list(e6)
    return {
        "ticker": normalize_ticker(sid),
        "base_role": base_role,
        "elasticity": elast,
        "foreign_buy_strength": foreign,
        "volume_confirmation": volume,
        "validation_credibility": valid,
        "title_priority": title,
        "as_of": as_of,
        "score_evidence": {
            "elasticity": e1,
            "foreign_buy_strength": e2,
            "volume_confirmation": e3,
            "validation_credibility": e4,
            "title_priority": e5,
        },
    }


def _index_lists(structured_data: dict) -> dict[str, dict]:
    """Map sid -> {buy_rank, sell_rank, amount, divergence_text, flow_state}."""
    out: dict[str, dict] = {}

    def _touch(sid: str) -> dict:
        sid = normalize_ticker(sid)
        if not sid:
            return {}
        row = out.setdefault(sid, {})
        return row

    for i, item in enumerate(structured_data.get("safe_havens_bought") or [], start=1):
        sid = extract_sid(item) or ""
        # also try name-only later via stock_db — skip if no sid
        if not sid:
            # fuzzy: keep raw for name match in rank_focus
            continue
        row = _touch(sid)
        row["buy_rank"] = i
        amt = parse_amount_yi(item)
        if amt is not None:
            row["amount_yi"] = amt
        row["flow_state"] = "foreign_buy_focus"
        row["buy_text"] = str(item)

    for i, item in enumerate(structured_data.get("heavyweights_dumped") or [], start=1):
        sid = extract_sid(item)
        if not sid:
            continue
        row = _touch(sid)
        row["sell_rank"] = i
        amt = parse_amount_yi(item)
        if amt is not None:
            row["amount_yi"] = amt
        row["flow_state"] = "foreign_sell_risk"
        row["sell_text"] = str(item)

    for item in structured_data.get("price_volume_divergence") or []:
        sid = extract_sid(item)
        if not sid:
            continue
        row = _touch(sid)
        row["in_divergence"] = True
        row["divergence_text"] = str(item)

    for item in structured_data.get("ai_data_picks") or []:
        sid = extract_sid(item)
        if not sid:
            continue
        row = _touch(sid)
        row["ai_pick"] = True
        row["ai_text"] = str(item)

    for target in structured_data.get("prediction_targets") or []:
        if not isinstance(target, dict):
            continue
        sid = extract_sid(target.get("id"))
        if not sid:
            continue
        row = _touch(sid)
        row["is_prediction_target"] = True
        row["name"] = target.get("name")
        row["trigger"] = target.get("trigger")

    return out


def _name_to_sid(stock_db: dict) -> dict[str, str]:
    mapping = {}
    for sid, entry in (stock_db or {}).items():
        name = str((entry or {}).get("name") or "").strip()
        if name:
            mapping[name] = normalize_ticker(sid)
    return mapping


def _resolve_sid_from_text(text: str, name_map: dict[str, str]) -> str:
    sid = extract_sid(text)
    if sid:
        return normalize_ticker(sid)
    cleaned = re.sub(r"\s*[\(（].*?[\)）]\s*", "", str(text)).strip()
    # longest name match
    for name, sid in sorted(name_map.items(), key=lambda x: len(x[0]), reverse=True):
        if name and name in cleaned:
            return sid
    return ""


def enrich_index_with_names(structured_data: dict, stock_db: dict) -> dict[str, dict]:
    """Like _index_lists but also resolve name-only rows via stock_db."""
    indexed = _index_lists(structured_data)
    name_map = _name_to_sid(stock_db)

    def _merge_text_list(field: str, kind: str):
        for i, item in enumerate(structured_data.get(field) or [], start=1):
            sid = _resolve_sid_from_text(str(item), name_map)
            if not sid:
                continue
            row = indexed.setdefault(sid, {})
            if kind == "buy":
                row.setdefault("buy_rank", i)
                row["flow_state"] = "foreign_buy_focus"
                amt = parse_amount_yi(item)
                if amt is not None:
                    row["amount_yi"] = amt
                row["buy_text"] = str(item)
            elif kind == "sell":
                row.setdefault("sell_rank", i)
                row["flow_state"] = "foreign_sell_risk"
                amt = parse_amount_yi(item)
                if amt is not None:
                    row["amount_yi"] = amt
                row["sell_text"] = str(item)
            elif kind == "div":
                row["in_divergence"] = True
                row["divergence_text"] = str(item)
            elif kind == "ai":
                row["ai_pick"] = True

    _merge_text_list("safe_havens_bought", "buy")
    _merge_text_list("heavyweights_dumped", "sell")
    _merge_text_list("price_volume_divergence", "div")
    _merge_text_list("ai_data_picks", "ai")
    return indexed


def rank_focus_stocks(
    structured_data: dict | None,
    stock_db: dict | None = None,
    *,
    limit: int = 3,
    as_of: str | None = None,
) -> list[dict]:
    """
    Rank today's candidates by title_priority for NotebookLM / packaging.
    Returns list of score dicts with name/label extras.
    """
    structured_data = structured_data or {}
    stock_db = stock_db if stock_db is not None else load_stock_db()
    as_of = as_of or structured_data.get("date")
    indexed = enrich_index_with_names(structured_data, stock_db)

    ranked = []
    for sid, meta in indexed.items():
        entry = stock_db.get(sid) or stock_db.get(sid.lstrip("0") if sid.isdigit() else sid) or {}
        # stock_db keys are usually without leading issues; try raw
        if not entry:
            for k, v in stock_db.items():
                if normalize_ticker(k) == sid:
                    entry = v
                    break
        scores = score_ticker(
            sid,
            entry,
            buy_rank=meta.get("buy_rank"),
            sell_rank=meta.get("sell_rank"),
            amount_yi=meta.get("amount_yi"),
            volume_rank=meta.get("volume_rank"),
            vol_ratio=meta.get("vol_ratio"),
            in_divergence=bool(meta.get("in_divergence")),
            divergence_text=meta.get("divergence_text") or "",
            flow_state=meta.get("flow_state"),
            as_of=as_of,
        )
        name = (
            meta.get("name")
            or (entry or {}).get("name")
            or ""
        )
        scores["name"] = name
        scores["label"] = f"{sid} {name}".strip()
        scores["source_text"] = (
            meta.get("buy_text")
            or meta.get("divergence_text")
            or meta.get("sell_text")
            or meta.get("ai_text")
            or scores["label"]
        )
        scores["role_label"] = (entry.get("market_role") or {}).get("label") or scores["base_role"]
        # small boost if formal prediction target
        if meta.get("is_prediction_target"):
            scores["title_priority"] = _clamp(scores["title_priority"] + 5)
            scores.setdefault("score_evidence", {}).setdefault("title_priority", []).append("prediction_target_boost")
        if meta.get("ai_pick"):
            scores["title_priority"] = _clamp(scores["title_priority"] + 3)
        ranked.append(scores)

    ranked.sort(key=lambda r: (r.get("title_priority", 0), r.get("elasticity", 0), r.get("foreign_buy_strength", 0)), reverse=True)
    return ranked[:limit]


def attach_role_scores_to_structured(
    structured_data: dict,
    stock_db: dict | None = None,
    *,
    limit: int = 5,
) -> dict:
    """Mutate-copy structured_data with focus_ranking + top score snapshot."""
    structured_data = dict(structured_data or {})
    stock_db = stock_db if stock_db is not None else load_stock_db()
    ranking = rank_focus_stocks(structured_data, stock_db, limit=limit, as_of=structured_data.get("date"))
    structured_data["focus_ranking"] = [
        {
            "ticker": r["ticker"],
            "name": r.get("name"),
            "base_role": r.get("base_role"),
            "role_label": r.get("role_label"),
            "elasticity": r.get("elasticity"),
            "foreign_buy_strength": r.get("foreign_buy_strength"),
            "volume_confirmation": r.get("volume_confirmation"),
            "validation_credibility": r.get("validation_credibility"),
            "title_priority": r.get("title_priority"),
            "source_text": r.get("source_text"),
        }
        for r in ranking
    ]
    return structured_data


def merge_scores_into_market_role(market_role: dict | None, scores: dict | None) -> dict:
    """Attach quantitative scores onto a market_role dict for stock_db persistence."""
    market_role = dict(market_role or {})
    scores = scores or {}
    market_role["scores"] = {
        "elasticity": scores.get("elasticity"),
        "foreign_buy_strength": scores.get("foreign_buy_strength"),
        "volume_confirmation": scores.get("volume_confirmation"),
        "validation_credibility": scores.get("validation_credibility"),
        "title_priority": scores.get("title_priority"),
        "as_of": scores.get("as_of"),
    }
    if scores.get("score_evidence"):
        market_role["score_evidence"] = scores["score_evidence"]
    return market_role
