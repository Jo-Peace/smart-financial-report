"""
Quantitative market_role scores for title / NotebookLM / packaging ranking.

Scores are deterministic (no Gemini). They refine categorical base_role with:
- elasticity (realized daily volatility + beta vs ^TWII over the last 20
  consecutive sessions from the market_data payload; sparse local snapshots
  at capped trust otherwise; the categorical prior is only a fallback /
  shrinkage target)
- foreign_buy_strength (rank + amount, normalized by same-day traded value
  when the turnover is known)
- volume_confirmation (own vol_ratio; traded-value rank — market-wide when
  the tape is known, else the observation pool, each with its own rank map —
  only when vol_ratio is unknown; "off the share-count board" is *not*
  evidence of low volume)
- validation_credibility (local tracking hit rate)

title_priority is a composite for 「誰該當標題/封面/主角卡」.
direction_indicator (台積電等) can score high on foreign flow but stay low on title_priority.

Inputs that are not in structured_data itself (volume board, vol_ratio,
price statistics, recent protagonists, mechanical-flow calendar flags) travel
in structured_data["role_score_inputs"], built once by build_role_score_inputs
during /1 so regen / backtests reproduce the same ranking.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

from modules.tickers import bare_ticker


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(BASE_DIR, "data", "stock_db.json")
DEFAULT_ANALYTICS_DB = os.path.join(BASE_DIR, "data", "analytics.db")

# base_role → default elasticity prior (fallback when local price history is thin)
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

# Realized-volatility elasticity. Preferred source: the 20 consecutive
# sessions ending at the previous close that get_stock_data keeps in the
# market_data payload (realized_vol_20d_pct / returns_20d, basis
# "consecutive_20d"). Fallback: local market_snapshots pct_change values
# (point-in-time: snapshot_date < as_of), which only exist on days the name
# was in the dynamic universe — sparse and biased toward big-move sessions,
# so their trust is capped.
PRICE_STATS_MIN_SAMPLES = 8
PRICE_STATS_FULL_TRUST = 20
PRICE_STATS_LOOKBACK_DAYS = 120
PRICE_STATS_MAX_SAMPLES = 40
SPARSE_SNAPSHOT_MAX_TRUST = 0.5
CONSECUTIVE_BASIS = "consecutive_20d"

# title_priority shaping
ELASTIC_TITLE_BONUS = 6.0          # additive (was ×1.12)
FLOW_X_ELASTIC_BONUS = 4.0         # additive (was +8)
PREDICTION_TARGET_BONUS = 5.0
AI_PICK_BONUS = 3.0
RECENT_LEAD_SESSIONS = 5
RECENT_LEAD_DECAY = 4.0            # per recent session as #1 protagonist
RECENT_LEAD_DECAY_MAX = 12.0
MECHANICAL_FLOW_SHRINK = 0.7       # foreign score pulled toward 50 on rebalance/settlement days


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
    return bare_ticker(value)


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


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Elasticity
# ---------------------------------------------------------------------------


def _elasticity_from_stats(stats: dict | None) -> tuple[float | None, float, list[str]]:
    """Return (data_score, trust_weight, evidence) from realized price statistics."""
    stats = stats or {}
    n = int(stats.get("n") or 0)
    sigma = _float(stats.get("vol_daily_pct"))
    if n < PRICE_STATS_MIN_SAMPLES or sigma is None:
        return None, 0.0, []
    consecutive = stats.get("basis") == CONSECUTIVE_BASIS
    # σ 1%→20, 2.5%→44, 4%→68, 6%+→100.
    vol_score = max(0.0, min(100.0, 20 + (sigma - 1.0) * 16))
    if consecutive:
        evidence = [f"realized_vol_20d={sigma:.2f}%/day n={n} (consecutive sessions)"]
    else:
        evidence = [f"realized_vol={sigma:.2f}%/day n={n} (sparse snapshot days)"]
    beta = _float(stats.get("beta"))
    beta_n = int(stats.get("beta_n") or 0)
    if beta is not None and beta_n >= PRICE_STATS_MIN_SAMPLES:
        # β 0.5→30, 1.0→50, 1.5→70, 2.0→90
        beta_score = max(0.0, min(100.0, 30 + (beta - 0.5) * 40))
        data_score = 0.5 * vol_score + 0.5 * beta_score
        evidence.append(f"beta_vs_TWII={beta:.2f} n={beta_n}")
    else:
        data_score = vol_score
    trust = min(1.0, n / float(PRICE_STATS_FULL_TRUST))
    if not consecutive:
        trust = min(trust, SPARSE_SNAPSHOT_MAX_TRUST)
    return data_score, trust, evidence


def score_elasticity(
    sid: str,
    entry: dict | None,
    base_role: str | None = None,
    price_stats: dict | None = None,
) -> tuple[int, list[str]]:
    """
    Elasticity = realized volatility / beta (20 consecutive sessions when the
    market_data payload has them, else sparse local snapshots at capped
    trust), shrunk toward the categorical prior when the sample is small.
    With no usable price history the categorical prior (plus sector keyword
    adjustment) is used and the evidence says why.
    """
    entry = entry or {}
    base_role = base_role or _base_role_of(entry, sid)
    prior = float(ELASTICITY_PRIOR.get(base_role, 50))
    evidence = []

    sector = f"{entry.get('sector', '')} {entry.get('supply_chain_role', '')} {entry.get('name', '')}"
    prior_adj = 0.0
    prior_notes = []
    if any(t in sector for t in ("記憶體", "DRAM", "HBM", "載板", "PCB", "散熱", "ODM", "面板")):
        prior_adj += 8
        prior_notes.append("elastic_sector_boost")
    if any(t in sector for t in ("金融", "銀行", "壽險", "電信")):
        prior_adj -= 10
        prior_notes.append("defensive_sector_penalty")

    data_score, trust, data_evidence = _elasticity_from_stats(price_stats)
    if data_score is None:
        score = prior + prior_adj
        evidence.append(f"prior={base_role}:{int(prior)}")
        n = int((price_stats or {}).get("n") or 0)
        if n:
            evidence.append(f"basis=category_prior(insufficient_local_samples n={n}<{PRICE_STATS_MIN_SAMPLES})")
        else:
            evidence.append("basis=category_prior(no_local_price_history)")
        evidence.extend(prior_notes)
    else:
        score = trust * data_score + (1.0 - trust) * (prior + prior_adj)
        evidence.extend(data_evidence)
        evidence.append(f"basis=realized trust={trust:.2f} prior={base_role}:{int(prior)}")
        if trust < 1.0 and prior_notes:
            evidence.extend(prior_notes)

    sid_n = normalize_ticker(sid)
    if sid_n in DIRECTION_TICKERS:
        score = min(score, 25)
        evidence.append("mega_cap_direction_cap")
    if sid_n.startswith("00"):
        score = min(score, 20)
        evidence.append("etf_or_vehicle_cap")

    return _clamp(score), evidence


# ---------------------------------------------------------------------------
# Foreign flow
# ---------------------------------------------------------------------------


def _intensity_score(intensity: float) -> float:
    """|foreign net| / traded value → 0-100 (0%→20, 5%→45, 10%→70, 15%+→95)."""
    return max(0.0, min(100.0, 20 + abs(float(intensity)) * 100 * 5))


def score_foreign_buy_strength(
    *,
    buy_rank: int | None = None,
    sell_rank: int | None = None,
    amount_yi: float | None = None,
    flow_state: str | None = None,
    intensity: float | None = None,
    mechanical_flow: bool = False,
) -> tuple[int, list[str]]:
    """
    Buy-side strength. Rank/amount alone favour mega caps, so when the same-day
    traded value is known the score blends in flow intensity
    (= foreign net 億 ÷ traded value 億). Sell flow still subtracts.
    """
    score = 15.0
    evidence = []
    rank_score = None
    if buy_rank:
        rank_score = max(10.0, 90 - (buy_rank - 1) * 8)  # rank1=90, rank5=58, rank10=18
        score = max(score, rank_score)
        evidence.append(f"buy_rank={buy_rank}")
    if amount_yi is not None and amount_yi > 0:
        # soft, capped: 10億→+2, 50億→+10
        bonus = min(10.0, amount_yi * 0.2)
        score += bonus
        evidence.append(f"buy_amount={amount_yi:+.1f}億")
    if intensity is not None and (buy_rank or (amount_yi or 0) > 0) and float(intensity) > 0:
        intensity_score = _intensity_score(intensity)
        score = 0.5 * score + 0.5 * intensity_score
        evidence.append(f"flow_intensity={float(intensity) * 100:.1f}%_of_turnover")
    if sell_rank:
        score -= max(0, 40 - (sell_rank - 1) * 3)
        evidence.append(f"sell_rank={sell_rank}")
    if amount_yi is not None and amount_yi < 0:
        score -= min(35, abs(amount_yi) * 0.5)
        evidence.append(f"sell_amount={amount_yi:.1f}億")
        if intensity is not None and float(intensity) < 0:
            evidence.append(f"flow_intensity={float(intensity) * 100:.1f}%_of_turnover")
    if flow_state == "foreign_buy_focus" and buy_rank is None:
        score = max(score, 55)
        evidence.append("flow=foreign_buy_focus")
    if flow_state == "foreign_sell_risk":
        score = min(score, 35)
        evidence.append("flow=foreign_sell_risk")
    if mechanical_flow:
        score = 50 + (score - 50) * MECHANICAL_FLOW_SHRINK
        evidence.append("mechanical_flow_day_shrink")
    return _clamp(score), evidence


# ---------------------------------------------------------------------------
# Volume confirmation
# ---------------------------------------------------------------------------


def volume_flags(
    *,
    buy_rank: int | None = None,
    vol_ratio: float | None = None,
    volume_rank: int | None = None,
    in_divergence: bool = False,
    limit_up: bool = False,
) -> list[str]:
    """Descriptive flags kept *outside* the confirmation score."""
    flags = []
    if limit_up:
        flags.append("limit_up_lock" if vol_ratio is not None and vol_ratio < 1.0 else "limit_up")
    elif buy_rank and vol_ratio is not None and vol_ratio < 1.0:
        flags.append("shadow_low_volume_buy")
    if buy_rank and vol_ratio is None and not volume_rank:
        flags.append("volume_unknown_buy")
    if in_divergence:
        flags.append("in_divergence_list")
    return flags


def _turnover_rank_score(rank: int, basis: str | None = None) -> float:
    """
    Rank-only volume proxy. ``basis`` = rank_volume_by_turnover's rank_basis.

    - "pool" (or unknown): rank inside the ~20-40 name observation pool;
      rank1=95, rank10=59, rank 17+ = 30.
    - "market": rank among all ~1,000 listed stocks. A known market rank only
      comes from the 成交值前20 or a share-count top-20 board row (both
      market-wide top lists) and is always >= the pool rank, so the pool
      decay would call a #2-by-shares, 100 億 name thin. Market top 20 is
      confirmed (rank1=95 … rank20=66.5); beyond it the score decays gently to
      neutral 50 and never reads as thin volume (< 50).
    """
    rank = int(rank)
    if basis == "market":
        if rank <= 20:
            return 95 - (rank - 1) * 1.5
        return max(50.0, 66.5 - (rank - 20) * 0.5)  # rank31=61, rank53+=50
    return max(30.0, 95 - (rank - 1) * 4)  # rank1=95, rank10=59


def score_volume_confirmation(
    *,
    volume_rank: int | None = None,
    vol_ratio: float | None = None,
    in_divergence: bool = False,
    divergence_text: str = "",
    buy_rank: int | None = None,
    limit_up: bool = False,
    volume_rank_basis: str | None = None,
) -> tuple[int, list[str]]:
    """
    How much today's volume confirms the move.

    - vol_ratio = today's volume ÷ 5-day average (from market_data) — the
      stock's own confirmation, so when it is known it decides the score
    - volume_rank = traded-value rank among non-ETF names: market-wide when
      the tape's value_ranking is known, else in the observation pool
      (share-count board ∪ market_data; see rank_volume_by_turnover);
      a cross-sectional proxy used only when vol_ratio is unknown (board-only
      names), so a big-cap whose own volume shrank is not "confirmed" by size;
      ``volume_rank_basis`` ("market" / "pool") picks the rank → score map
      (_turnover_rank_score)
    - neither known → neutral 50 ("volume_unknown"); being absent from the
      share-count board is not low volume, so it must not read as thin
      (broadcast_facts treats < 50 as thin volume).
    Divergence / shadow-buy membership is a flag (see volume_flags), not score.
    """
    evidence = []
    candidates = []
    if vol_ratio is not None:
        vr = float(vol_ratio)
        if vr >= 3:
            v = 95.0
        elif vr >= 2:
            v = 80 + (vr - 2) * 15
        elif vr >= 1.3:
            v = 65 + (vr - 1.3) / 0.7 * 15
        elif vr >= 1.0:
            v = 50 + (vr - 1.0) / 0.3 * 15
        elif vr >= 0.7:
            v = 35 + (vr - 0.7) / 0.3 * 15
        else:
            v = 25.0
        candidates.append(v)
        evidence.append(f"vol_ratio={vr}x")
        if volume_rank:
            evidence.append(f"turnover_rank={volume_rank}(context_only)")
    elif volume_rank:
        candidates.append(_turnover_rank_score(volume_rank, volume_rank_basis))
        basis = f"({volume_rank_basis})" if volume_rank_basis else ""
        evidence.append(f"turnover_rank={volume_rank}{basis}")
    if candidates:
        score = max(candidates)
    else:
        score = 50.0
        evidence.append("volume_unknown")
    if limit_up and vol_ratio is not None and vol_ratio < 1.0:
        # Locked limit-up: thin volume is unfilled demand, not weak confirmation.
        score = max(score, 60.0)
        evidence.append("limit_up_lock")
    for flag in volume_flags(
        buy_rank=buy_rank,
        vol_ratio=vol_ratio,
        volume_rank=volume_rank,
        in_divergence=in_divergence,
        limit_up=limit_up,
    ):
        evidence.append(f"flag={flag}")
    return _clamp(score), evidence


# ---------------------------------------------------------------------------
# Validation credibility + title priority
# ---------------------------------------------------------------------------


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


# Closed-loop: hit-rate already feeds validation_credibility (weight 0.18), so the
# title multiplier is a guard against chronic misses, not a second boost.
HIT_RATE_MIN_SAMPLES = 6
# mult = HIT_RATE_MULT_FLOOR + HIT_RATE_MULT_SPAN * rate  (rate 0.5 → 1.00)
HIT_RATE_MULT_FLOOR = 0.80
HIT_RATE_MULT_SPAN = 0.40
HIT_RATE_MULT_MIN = 0.80
HIT_RATE_MULT_MAX = 1.04


def _hit_rate_multiplier(entry: dict | None, min_samples: int = HIT_RATE_MIN_SAMPLES) -> tuple[float | None, str]:
    entry = entry or {}
    tracking = entry.get("tracking") or {}
    n = int(tracking.get("prediction_count") or 0)
    if n < min_samples:
        return None, f"hit_rate_track=skip samples={n}<{min_samples}"
    rate = _hit_rate_fraction(tracking)
    if rate is None:
        return None, f"hit_rate_track=skip samples={n} rate=unknown"
    mult = HIT_RATE_MULT_FLOOR + HIT_RATE_MULT_SPAN * float(rate)
    mult = max(HIT_RATE_MULT_MIN, min(HIT_RATE_MULT_MAX, mult))
    return mult, f"hit_rate_track samples={n} rate={rate:.0%} mult={mult:.2f}"


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
    - high hit-rate: at most a tiny lift (hit-rate is already in validation_credibility)
    """
    mult, note = _hit_rate_multiplier(entry, min_samples=min_samples)
    if mult is None:
        return int(title_priority), [note]
    adjusted = _clamp(float(title_priority) * mult)
    return adjusted, [f"{note} title_priority {int(title_priority)}→{adjusted}"]


def _title_score_raw(
    *,
    base_role: str,
    elasticity: int,
    foreign_buy: int,
    volume: int,
    validation: int,
    sid: str = "",
) -> tuple[float, list[str]]:
    evidence = []
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
        raw += ELASTIC_TITLE_BONUS
        evidence.append(f"elastic_title_bonus+{ELASTIC_TITLE_BONUS:g}")
    elif base_role == "defensive_income":
        raw *= 0.75
        evidence.append("defensive_title_penalty")

    # Strong buy + measured elasticity = hero
    if foreign_buy >= 70 and elasticity >= 65:
        raw += FLOW_X_ELASTIC_BONUS
        evidence.append(f"flow_x_elastic_bonus+{FLOW_X_ELASTIC_BONUS:g}")
    return raw, evidence


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
    Boosts are additive and bounded so the composite does not saturate at 100.
    """
    raw, evidence = _title_score_raw(
        base_role=base_role,
        elasticity=elasticity,
        foreign_buy=foreign_buy,
        volume=volume,
        validation=validation,
        sid=sid,
    )
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
    price_stats: dict | None = None,
    turnover_yi: float | None = None,
    limit_up: bool = False,
    mechanical_flow: bool = False,
    volume_rank_basis: str | None = None,
) -> dict[str, Any]:
    entry = entry or {}
    base_role = _base_role_of(entry, sid)
    elast, e1 = score_elasticity(sid, entry, base_role, price_stats=price_stats)
    intensity = None
    if amount_yi is not None and turnover_yi:
        try:
            intensity = float(amount_yi) / float(turnover_yi)
        except (TypeError, ValueError, ZeroDivisionError):
            intensity = None
    foreign, e2 = score_foreign_buy_strength(
        buy_rank=buy_rank,
        sell_rank=sell_rank,
        amount_yi=amount_yi,
        flow_state=flow_state,
        intensity=intensity,
        mechanical_flow=mechanical_flow,
    )
    volume, e3 = score_volume_confirmation(
        volume_rank=volume_rank,
        vol_ratio=vol_ratio,
        in_divergence=in_divergence,
        divergence_text=divergence_text,
        buy_rank=buy_rank,
        limit_up=limit_up,
        volume_rank_basis=volume_rank_basis,
    )
    valid, e4 = score_validation_credibility(entry)
    raw, e5 = _title_score_raw(
        base_role=base_role,
        elasticity=elast,
        foreign_buy=foreign,
        volume=volume,
        validation=valid,
        sid=sid,
    )
    mult, note = _hit_rate_multiplier(entry)
    if mult is not None:
        before = raw
        raw *= mult
        note = f"{note} title_score {before:.1f}→{raw:.1f}"
    e5 = list(e5) + [note]
    return {
        "ticker": normalize_ticker(sid),
        "base_role": base_role,
        "elasticity": elast,
        "foreign_buy_strength": foreign,
        "volume_confirmation": volume,
        "validation_credibility": valid,
        "title_priority": _clamp(raw),
        "title_score": round(raw, 2),
        "volume_flags": volume_flags(
            buy_rank=buy_rank,
            vol_ratio=vol_ratio,
            volume_rank=volume_rank,
            in_divergence=in_divergence,
            limit_up=limit_up,
        ),
        "as_of": as_of,
        "score_evidence": {
            "elasticity": e1,
            "foreign_buy_strength": e2,
            "volume_confirmation": e3,
            "validation_credibility": e4,
            "title_priority": e5,
        },
    }


# ---------------------------------------------------------------------------
# Index today's candidates
# ---------------------------------------------------------------------------


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


def merge_role_inputs(indexed: dict[str, dict], role_inputs: dict | None) -> dict[str, dict]:
    """Attach volume / vol_ratio / turnover / price stats from role_score_inputs."""
    role_inputs = role_inputs or {}
    volume = role_inputs.get("volume") or {}
    market = role_inputs.get("market") or {}
    stats = role_inputs.get("price_stats") or {}
    for sid, meta in indexed.items():
        v = volume.get(sid) or {}
        m = market.get(sid) or {}
        if v.get("turnover_rank"):
            meta["volume_rank"] = int(v["turnover_rank"])
            if v.get("rank_basis"):
                meta["volume_rank_basis"] = v["rank_basis"]
        if m.get("vol_ratio") is not None:
            meta["vol_ratio"] = _float(m.get("vol_ratio"))
        turnover = m.get("turnover_yi") if m.get("turnover_yi") is not None else v.get("turnover_yi")
        if turnover is not None:
            meta["turnover_yi"] = _float(turnover)
        pct = _float(m.get("pct_change"))
        if pct is None:
            pct = _float(v.get("pct_change"))
        if pct is not None:
            meta["pct_change"] = pct
            meta["limit_up"] = pct >= 9.5
        if stats.get(sid):
            meta["price_stats"] = stats[sid]
    return indexed


def _mechanical_flow_for(role_inputs: dict | None, sid: str) -> bool:
    """True when a same-session calendar event makes this ticker's flow partly mechanical."""
    rows = (role_inputs or {}).get("mechanical_flow") or []
    if not rows:
        return False
    # Older payloads stored bare kinds; treat index rebalances as market-wide.
    rows = [row if isinstance(row, dict) else {"kind": str(row)} for row in rows]
    try:
        from modules.market_calendar import mechanical_flow_for_ticker

        return bool(mechanical_flow_for_ticker(rows, sid))
    except Exception:
        return False


def score_from_meta(sid: str, entry: dict | None, meta: dict | None, *, as_of=None, role_inputs=None, flow_state=None) -> dict:
    """score_ticker driven by an indexed meta row (shared by ranking and stock_db roles)."""
    meta = meta or {}
    return score_ticker(
        sid,
        entry,
        buy_rank=meta.get("buy_rank"),
        sell_rank=meta.get("sell_rank"),
        amount_yi=meta.get("amount_yi"),
        volume_rank=meta.get("volume_rank"),
        vol_ratio=meta.get("vol_ratio"),
        in_divergence=bool(meta.get("in_divergence")),
        divergence_text=meta.get("divergence_text") or "",
        flow_state=flow_state or meta.get("flow_state"),
        as_of=as_of,
        price_stats=meta.get("price_stats"),
        turnover_yi=meta.get("turnover_yi"),
        limit_up=bool(meta.get("limit_up")),
        mechanical_flow=_mechanical_flow_for(role_inputs, sid),
        volume_rank_basis=meta.get("volume_rank_basis"),
    )


def _lookup_entry(stock_db: dict, sid: str) -> dict:
    entry = stock_db.get(sid) or stock_db.get(sid.lstrip("0") if sid.isdigit() else sid) or {}
    if not entry:
        for k, v in stock_db.items():
            if normalize_ticker(k) == sid:
                return v or {}
    return entry


def _entry_with_scored_role(entry: dict | None, base_role: str | None) -> dict:
    """
    stock_db entry with market_role.base_role pinned to the role /1 scored with
    (role_score_inputs["base_roles"]). After /1 the compounder writes today's
    roles into stock_db, so a later regen would otherwise rank with different
    roles than the published /1 ranking. Other fields (tracking, sector) are kept.
    """
    entry = entry or {}
    if not base_role or (entry.get("market_role") or {}).get("base_role") == base_role:
        return entry
    market_role = dict(entry.get("market_role") or {})
    market_role["base_role"] = base_role
    market_role.pop("label", None)  # label belongs to the newer role; fall back to base_role
    pinned = dict(entry)
    pinned["market_role"] = market_role
    return pinned


def ranking_is_reproducible(structured_data: dict | None) -> bool:
    """True when role_score_inputs pins the roles /1 scored with (see _entry_with_scored_role)."""
    inputs = (structured_data or {}).get("role_score_inputs") or {}
    return bool(isinstance(inputs, dict) and inputs.get("base_roles"))


def rank_focus_stocks(
    structured_data: dict | None,
    stock_db: dict | None = None,
    *,
    limit: int = 3,
    as_of: str | None = None,
    role_inputs: dict | None = None,
) -> list[dict]:
    """
    Rank today's candidates by title_priority for NotebookLM / packaging.
    Returns list of score dicts with name/label extras.

    Sorting uses the unclamped title_score; names that led the ranking on
    recent sessions decay so the same protagonist does not repeat by inertia.
    """
    structured_data = structured_data or {}
    stock_db = stock_db if stock_db is not None else load_stock_db()
    as_of = as_of or structured_data.get("date")
    if role_inputs is None:
        role_inputs = structured_data.get("role_score_inputs") or {}
    indexed = merge_role_inputs(enrich_index_with_names(structured_data, stock_db), role_inputs)
    recent_leads = [normalize_ticker(t) for t in (role_inputs.get("recent_leads") or []) if t]

    base_roles = role_inputs.get("base_roles") or {}

    ranked = []
    for sid, meta in indexed.items():
        entry = _entry_with_scored_role(_lookup_entry(stock_db, sid), base_roles.get(sid))
        scores = score_from_meta(sid, entry, meta, as_of=as_of, role_inputs=role_inputs)
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
        raw = float(scores.get("title_score") or 0.0)
        title_ev = scores.setdefault("score_evidence", {}).setdefault("title_priority", [])
        # small boost if formal prediction target
        if meta.get("is_prediction_target"):
            raw += PREDICTION_TARGET_BONUS
            title_ev.append("prediction_target_boost")
        if meta.get("ai_pick"):
            raw += AI_PICK_BONUS
            title_ev.append("ai_pick_boost")
        lead_days = sum(1 for t in recent_leads[:RECENT_LEAD_SESSIONS] if t == sid)
        if lead_days:
            decay = min(RECENT_LEAD_DECAY_MAX, RECENT_LEAD_DECAY * lead_days)
            raw -= decay
            title_ev.append(f"recent_lead_decay lead_days={lead_days}/{RECENT_LEAD_SESSIONS} -{decay:g}")
        scores["title_score"] = round(raw, 2)
        scores["title_priority"] = _clamp(raw)
        ranked.append(scores)

    ranked.sort(
        key=lambda r: (
            -float(r.get("title_score") or 0.0),
            -int(r.get("foreign_buy_strength") or 0),
            -int(r.get("elasticity") or 0),
            str(r.get("ticker") or ""),
        )
    )
    return ranked[:limit]


def attach_role_scores_to_structured(
    structured_data: dict,
    stock_db: dict | None = None,
    *,
    limit: int = 5,
    role_inputs: dict | None = None,
) -> dict:
    """Mutate-copy structured_data with focus_ranking + top score snapshot."""
    structured_data = dict(structured_data or {})
    stock_db = stock_db if stock_db is not None else load_stock_db()
    if role_inputs is not None:
        structured_data["role_score_inputs"] = role_inputs
    ranking = rank_focus_stocks(
        structured_data,
        stock_db,
        limit=limit,
        as_of=structured_data.get("date"),
    )
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
            "title_score": r.get("title_score"),
            "volume_flags": r.get("volume_flags") or [],
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


# ---------------------------------------------------------------------------
# role_score_inputs (built once during /1, persisted in structured_data)
# ---------------------------------------------------------------------------


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def load_price_stats(
    tickers,
    as_of,
    *,
    db_path: str | None = None,
    lookback_days: int = PRICE_STATS_LOOKBACK_DAYS,
    max_samples: int = PRICE_STATS_MAX_SAMPLES,
) -> dict[str, dict]:
    """
    Realized daily volatility and beta vs ^TWII from local market_snapshots.

    Point-in-time: only sessions strictly before as_of (today's snapshot is
    written after scoring during /1, so /1, regen and backtests agree).
    Returns {} when the DB is missing or unreadable (callers fall back to the
    categorical prior).
    """
    as_of_d = _as_date(as_of)
    db_path = db_path or DEFAULT_ANALYTICS_DB
    tickers = sorted({normalize_ticker(t) for t in (tickers or []) if t})
    if not tickers or as_of_d is None or not os.path.exists(db_path):
        return {}
    start = (as_of_d - timedelta(days=lookback_days)).isoformat()
    end = as_of_d.isoformat()
    out: dict[str, dict] = {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    try:
        index_rows = conn.execute(
            """
            SELECT snapshot_date, pct_change FROM market_snapshots
            WHERE ticker IN ('^TWII', 'TWII') AND snapshot_date >= ? AND snapshot_date < ?
            """,
            (start, end),
        ).fetchall()
        index_by_date = {d: float(p) for d, p in index_rows if p is not None}
        for ticker in tickers:
            rows = conn.execute(
                """
                SELECT snapshot_date, pct_change FROM market_snapshots
                WHERE ticker = ? AND snapshot_date >= ? AND snapshot_date < ?
                ORDER BY snapshot_date DESC
                LIMIT ?
                """,
                (ticker, start, end, max_samples),
            ).fetchall()
            samples = [(d, max(-10.5, min(10.5, float(p)))) for d, p in rows if p is not None]
            if len(samples) < 2:
                continue
            sigma = _stdev([p for _, p in samples])
            pairs = [(p, index_by_date[d]) for d, p in samples if d in index_by_date]
            beta = _beta(pairs)
            out[ticker] = {
                "vol_daily_pct": round(sigma, 3) if sigma is not None else None,
                "beta": round(beta, 3) if beta is not None else None,
                "n": len(samples),
                "beta_n": len(pairs),
                "from": samples[-1][0],
                "to": samples[0][0],
            }
    except sqlite3.Error:
        return out
    finally:
        conn.close()
    return out


def _beta(pairs: list[tuple[float, float]]) -> float | None:
    """OLS slope of stock % on index % over (stock, index) pairs."""
    if len(pairs) < PRICE_STATS_MIN_SAMPLES:
        return None
    xs = [b for _, b in pairs]
    ys = [a for a, _ in pairs]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    var_x = sum((x - mx) ** 2 for x in xs)
    if var_x <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var_x


def consecutive_price_stats(market_data: dict | None, tickers=None) -> dict[str, dict]:
    """
    Realized σ / β from the consecutive-session returns that get_stock_data
    keeps in each market_data payload (data_fetcher.realized_vol_fields:
    20 sessions ending at the previous close). β is computed on dates shared
    with ^TWII's own returns_20d. Payloads without the fields (older
    snapshots, failed fetches) are skipped, so callers fall back to
    load_price_stats.
    """
    market_data = market_data or {}
    wanted = {normalize_ticker(t) for t in tickers} if tickers is not None else None
    index_returns = {}
    for key in ("^TWII", "TWII"):
        data = market_data.get(key)
        if isinstance(data, dict) and isinstance(data.get("returns_20d"), dict):
            index_returns = data["returns_20d"]
            break
    out: dict[str, dict] = {}
    for symbol, data in market_data.items():
        sid = normalize_ticker(symbol)
        if not sid or sid in {"^TWII", "TWII"} or not isinstance(data, dict):
            continue
        if wanted is not None and sid not in wanted:
            continue
        sigma = _float(data.get("realized_vol_20d_pct"))
        n = int(_float(data.get("realized_vol_20d_n"), 0) or 0)
        if sigma is None or n < PRICE_STATS_MIN_SAMPLES:
            continue
        own = data.get("returns_20d") if isinstance(data.get("returns_20d"), dict) else {}
        pairs = []
        for day, pct in own.items():
            idx = _float(index_returns.get(day))
            val = _float(pct)
            if idx is not None and val is not None:
                pairs.append((val, idx))
        beta = _beta(pairs)
        days = sorted(own)
        out[sid] = {
            "vol_daily_pct": round(sigma, 3),
            "beta": round(beta, 3) if beta is not None else None,
            "n": n,
            "beta_n": len(pairs),
            "basis": CONSECUTIVE_BASIS,
            "from": days[0] if days else None,
            "to": days[-1] if days else None,
        }
    return out


def load_recent_leads(reports_dir: str | None, before_date, sessions: int = RECENT_LEAD_SESSIONS) -> list[str]:
    """focus_ranking[0] tickers of the most recent sessions strictly before before_date (newest first)."""
    before = _as_date(before_date)
    if not reports_dir or before is None or not os.path.isdir(reports_dir):
        return []
    try:
        from modules.report_paths import date_from_structured_path, iter_structured_data_paths

        paths = iter_structured_data_paths(reports_dir)
    except Exception:
        return []
    dated = []
    for path in paths:
        day = date_from_structured_path(path)
        parsed = _as_date(day)
        if parsed is None or parsed >= before:
            continue
        dated.append((day, path))
    # Newest first; prefer reports/daily/ copies over legacy duplicates for a date.
    dated.sort(key=lambda item: (item[0], "/daily/" in item[1].replace("\\", "/")), reverse=True)
    leads: list[str] = []
    seen_days: set[str] = set()
    for day, path in dated:
        if day in seen_days:
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        ranking = (data or {}).get("focus_ranking") or []
        if ranking and isinstance(ranking[0], dict) and ranking[0].get("ticker"):
            seen_days.add(day)
            leads.append(normalize_ticker(ranking[0]["ticker"]))
            if len(leads) >= sessions:
                break
    return leads


def build_role_score_inputs(
    structured_data: dict | None,
    stock_db: dict | None = None,
    *,
    market_data: dict | None = None,
    volume_data: list | None = None,
    analytics_db_path: str | None = None,
    reports_dir: str | None = None,
    calendar_events: list | None = None,
    value_ranking: dict | None = None,
) -> dict:
    """
    Compact, JSON-safe inputs for role scoring that are not in structured_data
    (traded-value rank: market-wide from ``value_ranking`` when given, else over
    share board ∪ market_data; own vol_ratio, realized price stats, recent
    protagonists, mechanical-flow calendar flag). Every source is optional.
    """
    structured_data = structured_data or {}
    stock_db = stock_db if stock_db is not None else load_stock_db()
    as_of = structured_data.get("date")
    inputs: dict[str, Any] = {"as_of": as_of}

    try:
        from modules.market_signal_scorer import rank_volume_by_turnover

        # Same basis as the signal pack: the market-wide 成交值 rank when the
        # tape ranking is known, else the observation pool (share board ∪
        # market_data) so off-board mega caps / flow leaders are ranked too.
        inputs["volume"] = rank_volume_by_turnover(volume_data or [], market_data or {}, value_ranking=value_ranking)
    except Exception:
        inputs["volume"] = {}

    market = {}
    for symbol, data in (market_data or {}).items():
        sid = normalize_ticker(symbol)
        if not sid or sid in {"^TWII", "TWII"} or not isinstance(data, dict):
            continue
        row = {}
        vr = _float(data.get("vol_ratio"))
        if vr is not None:
            row["vol_ratio"] = vr
        pct = _float(data.get("pct_change"))
        if pct is not None:
            row["pct_change"] = pct
        price = _float(data.get("price"))
        volume = _float(data.get("volume"))
        if price and volume and price > 0 and volume > 0:
            row["turnover_yi"] = round(price * volume / 1e8, 2)
        if row:
            market[sid] = row
    inputs["market"] = market

    tickers = sorted(enrich_index_with_names(structured_data, stock_db).keys())
    # The role each candidate is scored with today, so regen (after the
    # compounder rewrote stock_db roles) reproduces the /1 ranking.
    inputs["base_roles"] = {sid: _base_role_of(_lookup_entry(stock_db, sid), sid) for sid in tickers}
    try:
        price_stats = load_price_stats(tickers, as_of, db_path=analytics_db_path)
    except Exception:
        price_stats = {}
    try:
        # Consecutive 20-session stats win over sparse snapshot days.
        price_stats.update(consecutive_price_stats(market_data, tickers))
    except Exception:
        pass
    inputs["price_stats"] = price_stats

    try:
        inputs["recent_leads"] = load_recent_leads(reports_dir, as_of)
    except Exception:
        inputs["recent_leads"] = []

    mechanical = []
    try:
        from modules.market_calendar import mechanical_flow_events

        events = calendar_events if calendar_events is not None else structured_data.get("calendar_events")
        mechanical = [
            {"kind": row["kind"], "scope": row.get("scope"), "tickers": list(row.get("tickers") or [])}
            for row in mechanical_flow_events(as_of, events)
        ]
    except Exception:
        mechanical = []
    inputs["mechanical_flow"] = mechanical
    return inputs
