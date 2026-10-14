"""
Data-driven horizon reliability notes for reports / prompts / dashboard.

Describe what the sample actually shows, and only *claim* that a window is
more reliable when the statistics support it:

- hit rates come from the analytics ledger (TWSE-session windows, excess
  return vs the same-window TWII move, dead band → neutral not counted);
- each window is compared with a naive baseline (50% for excess hits: a
  random pick beats the index about half the time);
- a window "beats baseline" only when the lower bound of its 95% Wilson
  interval is above that baseline (cluster-adjusted effective n when the
  ledger provides it, because overlapping windows are not independent);
- "20D 較可信" is said only when 20D beats baseline *and* leads 1D/3D/5D.
  Otherwise the fixed sentence is 「樣本不足以判定」.
"""

from __future__ import annotations

import math
from typing import Any

# Ledger method tag written by analytics_store (TWSE-session windows, same-window
# index, stop path). Rows without it are legacy calendar-day results and must
# not be republished as claims.
LEDGER_METHOD = "sessions_v2"

# Preferred reliability window when samples support it.
PRIMARY_CANDIDATE_DAYS = 20
SHORT_WINDOW_DAYS = (1, 3, 5)
MIN_SAMPLES_TO_CLAIM = 20
DEFAULT_BASELINE = 0.5
MIN_LEAD_OVER_SHORT = 0.03  # 3pp over the short-window average
Z_95 = 1.96

INSUFFICIENT_SENTENCE = (
    "依本機回測（交易日窗、扣同窗大盤），各時間窗超額命中率的 95% 信賴區間都未高於 50% 基準，"
    "樣本不足以判定哪個時間窗較準；先並列 1D/3D/5D/20D，勿稱任一窗較準，也勿把命中率當成敗尺子。"
)
EMPTY_SENTENCE = "回測樣本尚不足以比較各時間窗可信度；先並列 1D/3D/5D/20D，勿單獨強調任一窗。"
# Neutral time-scale line for hosts when no current-method ledger stats exist.
DEFAULT_RULER_LINE = (
    "隔日～5D＝短線節奏、約 20 交易日＝中期趨勢；"
    "本機回測尚未證實哪個時間窗較準，勿把任一窗講成成敗尺子。"
)


def wilson_interval(rate: float | None, n: int | float | None, z: float = Z_95) -> tuple[float | None, float | None]:
    """Wilson score interval for a proportion. Returns (low, high) or (None, None)."""
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return None, None
    if rate is None or n <= 0:
        return None, None
    p = min(1.0, max(0.0, float(rate)))
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denom
    half = (z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_to_fraction(value: Any) -> float | None:
    value = _as_float(value)
    if value is None:
        return None
    return value / 100.0


def _normalize_rows(horizon_rows: list[dict] | None) -> list[dict]:
    out: list[dict] = []
    for row in horizon_rows or []:
        if not isinstance(row, dict):
            continue
        days = row.get("horizon_days")
        if days is None:
            days = row.get("horizonDays")
        hit = row.get("hit_rate")
        if hit is None and row.get("hitRatePct") is not None:
            hit = _pct_to_fraction(row.get("hitRatePct"))
        samples = _as_int(row.get("samples"))
        excess = row.get("avg_excess")
        if excess is None:
            excess = row.get("avgExcessPct")
        ret = row.get("avg_return")
        if ret is None:
            ret = row.get("avgReturnPct")
        baseline = _as_float(row.get("baseline"))
        if baseline is None:
            baseline = DEFAULT_BASELINE
        effective = _as_int(row.get("effective_samples"), samples) or samples
        ci_low = _as_float(row.get("hit_ci_low"))
        ci_high = _as_float(row.get("hit_ci_high"))
        hit_f = _as_float(hit)
        if ci_low is None and hit_f is not None and samples > 0:
            ci_low, ci_high = wilson_interval(hit_f, min(samples, effective))
        out.append(
            {
                "horizon_days": _as_int(days),
                "samples": samples,
                "effective_samples": min(samples, effective) if samples else 0,
                "hit_rate": hit_f,
                "hit_ci_low": ci_low,
                "hit_ci_high": ci_high,
                "baseline": baseline,
                "avg_return": _as_float(ret),
                "avg_excess": _as_float(excess),
                "raw_hit_rate": _as_float(row.get("raw_hit_rate")),
                "index_up_rate": _as_float(row.get("index_up_rate")),
                "coverage": _as_float(row.get("coverage")),
                "due": _as_int(row.get("due")),
                "evaluated": _as_int(row.get("evaluated"), samples),
                "missing": _as_int(row.get("missing")),
                "neutral": _as_int(row.get("neutral")),
                "stopped": _as_int(row.get("stopped")),
                "unjudged": _as_int(row.get("unjudged")),
                "conditional_entries": _as_int(row.get("conditional_entries")),
            }
        )
    return [r for r in out if r["horizon_days"] > 0]


def beats_baseline(row: dict | None) -> bool:
    """True only when n is large enough and the CI lower bound clears the baseline."""
    if not row or row.get("hit_rate") is None:
        return False
    if (row.get("samples") or 0) < MIN_SAMPLES_TO_CLAIM:
        return False
    low = row.get("hit_ci_low")
    baseline = row.get("baseline")
    if low is None or baseline is None:
        return False
    return low > baseline


def _fmt_hit(rate: float | None) -> str:
    if rate is None:
        return "N/A"
    return f"{rate * 100:.1f}%"


def _fmt_excess(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2f}%"


def _fmt_ci(row: dict) -> str:
    low, high = row.get("hit_ci_low"), row.get("hit_ci_high")
    if low is None or high is None:
        return "CI N/A"
    return f"95% CI {low * 100:.0f}–{high * 100:.0f}%"


def _baseline_relation(row: dict) -> str:
    low, high, baseline = row.get("hit_ci_low"), row.get("hit_ci_high"), row.get("baseline")
    if low is None or high is None or baseline is None:
        return "信賴區間無法計算"
    if high < baseline:
        return f"整段區間低於 {baseline * 100:.0f}% 基準（目前樣本沒有贏過大盤）"
    if low > baseline and (row.get("samples") or 0) < MIN_SAMPLES_TO_CLAIM:
        return f"樣本未達 n≥{MIN_SAMPLES_TO_CLAIM}，暫不下結論"
    return f"區間涵蓋 {baseline * 100:.0f}% 基準"


def analyze_horizon_reliability(horizon_rows: list[dict] | None) -> dict[str, Any]:
    """
    Compare windows and decide which one (if any) the *current sample* supports.
    """
    rows = _normalize_rows(horizon_rows)
    by_days = {r["horizon_days"]: r for r in rows}
    short = [by_days[d] for d in SHORT_WINDOW_DAYS if d in by_days]
    primary = by_days.get(PRIMARY_CANDIDATE_DAYS)

    ranked = sorted(
        [r for r in rows if r.get("hit_rate") is not None],
        key=lambda r: (r["hit_rate"], r.get("avg_excess") or -999, r.get("samples") or 0),
        reverse=True,
    )
    best = ranked[0] if ranked else None

    short_hit_avg = None
    if short:
        hits = [r["hit_rate"] for r in short if r["hit_rate"] is not None]
        if hits:
            short_hit_avg = sum(hits) / len(hits)

    edge_pp = None  # percentage points over short avg
    if primary and primary.get("hit_rate") is not None and short_hit_avg is not None:
        edge_pp = (primary["hit_rate"] - short_hit_avg) * 100

    supported = [r for r in rows if beats_baseline(r)]
    primary_leads = bool(
        primary
        and beats_baseline(primary)
        and (short_hit_avg is None or primary["hit_rate"] >= short_hit_avg + MIN_LEAD_OVER_SHORT)
    )
    if not rows or not ranked:
        verdict = "empty"
    elif primary_leads:
        verdict = "primary_leads"
    elif supported:
        verdict = "other_supported"
    else:
        verdict = "insufficient"

    return {
        "rows": rows,
        "by_days": by_days,
        "primary": primary,
        "short_rows": short,
        "short_hit_avg": short_hit_avg,
        "best": best,
        "supported": supported,
        "primary_leads": primary_leads,
        "verdict": verdict,
        "edge_pp": edge_pp,
        "primary_days": PRIMARY_CANDIDATE_DAYS,
    }


def natural_reliability_sentences(horizon_rows: list[dict] | None, *, max_sentences: int = 3) -> list[str]:
    """
    Short Chinese sentences describing which window (if any) the sample supports.
    A window is only called more reliable when its CI lower bound beats baseline.
    """
    info = analyze_horizon_reliability(horizon_rows)
    primary = info["primary"]
    short_avg = info["short_hit_avg"]
    verdict = info["verdict"]
    sentences: list[str] = []

    if not info["rows"]:
        return sentences

    if verdict == "primary_leads" and primary:
        short_text = _fmt_hit(short_avg) if short_avg is not None else "短線窗"
        sentences.append(
            f"依本機回測（交易日窗、扣同窗大盤），{PRIMARY_CANDIDATE_DAYS}D 超額命中率 "
            f"{_fmt_hit(primary['hit_rate'])}（{_fmt_ci(primary)}，n={primary['samples']}）"
            f"高於 {primary['baseline'] * 100:.0f}% 基準，也高於 1D/3D/5D 平均 {short_text}；"
            f"選標與標題主角可參考 {PRIMARY_CANDIDATE_DAYS}D，短線窗只當節奏參考。"
        )
        if primary.get("avg_excess") is not None:
            sentences.append(
                f"同一批樣本 {PRIMARY_CANDIDATE_DAYS}D 平均超額 {_fmt_excess(primary['avg_excess'])}"
                "（收盤對收盤、非可成交報酬）。"
            )
        sentences.append("這是目前樣本的統計結果，不是保證；樣本或盤勢改變時結論可能翻轉。")
    elif verdict == "other_supported":
        row = sorted(info["supported"], key=lambda r: r["hit_ci_low"], reverse=True)[0]
        sentences.append(
            f"依本機回測，{row['horizon_days']}D 超額命中率 {_fmt_hit(row['hit_rate'])}"
            f"（{_fmt_ci(row)}，n={row['samples']}）高於 {row['baseline'] * 100:.0f}% 基準；"
            f"{PRIMARY_CANDIDATE_DAYS}D 尚未被樣本證實較準，勿硬稱 20D 一定較準。"
        )
    elif verdict == "insufficient":
        sentences.append(INSUFFICIENT_SENTENCE)
        if primary and primary.get("hit_rate") is not None:
            sentences.append(
                f"參考：{PRIMARY_CANDIDATE_DAYS}D 目前超額命中率 {_fmt_hit(primary['hit_rate'])}"
                f"（{_fmt_ci(primary)}，n={primary['samples']}），{_baseline_relation(primary)}。"
            )
    else:
        sentences.append(EMPTY_SENTENCE)

    return sentences[:max_sentences]


def ledger_disclosure(row: dict | None) -> str:
    """
    How a window's sample was built, for published stats: stop-loss exits
    (counted as misses), neutral rows (not counted), conditional entries whose
    condition was not verified (counted as filled), unjudged (no same-window
    TWII) and missing prices. Zero buckets are omitted; "" when all are zero.
    """
    if not row:
        return ""
    parts = []
    for key, label in (
        ("stopped", "停損出場 {n}（計失準）"),
        ("neutral", "持平不計 {n}"),
        ("conditional_entries", "條件式進場 {n}（條件未驗證，仍計入）"),
        ("unjudged", "缺同窗大盤未判 {n}"),
        ("missing", "缺價 {n}"),
    ):
        n = _as_int(row.get(key))
        if n > 0:
            parts.append(label.format(n=n))
    return "、".join(parts)


def format_reliability_brief(horizon_rows: list[dict] | None) -> str:
    """One multi-line block for prompts / briefs."""
    lines = natural_reliability_sentences(horizon_rows)
    return "\n".join(f"- {s}" for s in lines) if lines else "- （尚無時間窗回測）"


def format_horizon_summary_with_read(horizon_rows: list[dict] | None) -> list[str]:
    """Bullet list for each window + a leading natural conclusion."""
    info = analyze_horizon_reliability(horizon_rows)
    lines: list[str] = []
    for s in natural_reliability_sentences(horizon_rows, max_sentences=2):
        lines.append(f"- {s}")
    if lines:
        lines.append("")
    supported_days = {r["horizon_days"] for r in info["supported"]}
    for row in info["rows"]:
        h = row["horizon_days"]
        if info["primary_leads"] and h == PRIMARY_CANDIDATE_DAYS:
            role = "主指標"
        elif h in supported_days:
            role = "高於基準"
        elif h in SHORT_WINDOW_DAYS:
            role = "短線參考"
        else:
            role = "對照"
        coverage = row.get("coverage")
        coverage_text = f"；覆蓋 {coverage * 100:.0f}%" if coverage is not None else ""
        disclosure = ledger_disclosure(row)
        disclosure_text = f"；{disclosure}" if disclosure else ""
        lines.append(
            f"- {h}D（{role}）：超額命中率 {_fmt_hit(row['hit_rate'])}（{_fmt_ci(row)}，n={row['samples']}）；"
            f"方向調整平均報酬 {_fmt_excess(row['avg_return'])}；超額 {_fmt_excess(row['avg_excess'])}"
            f"{coverage_text}{disclosure_text}"
        )
    return lines or ["- 無"]


def reliability_one_liner(horizon_rows: list[dict] | None) -> str:
    """Single sentence for headers / preflight / dashboard hints."""
    sentences = natural_reliability_sentences(horizon_rows, max_sentences=1)
    return sentences[0] if sentences else ""


def rows_are_current(horizon_rows: list[dict] | None) -> bool:
    """True when every row comes from the current ledger method (not legacy)."""
    rows = [r for r in (horizon_rows or []) if isinstance(r, dict)]
    return bool(rows) and all(r.get("method") == LEDGER_METHOD for r in rows)


def current_horizon_rows(structured_data: dict | None) -> list[dict]:
    """horizon_performance rows of a structured_data, or [] when they are legacy."""
    history = (structured_data or {}).get("timeframe_history") or {}
    rows = history.get("horizon_performance") or []
    return list(rows) if rows_are_current(rows) else []


def current_reliability_line(structured_data: dict | None) -> str:
    """
    Publishable one-liner for a structured_data. It is recomputed from
    current-method rows, so an old stored one_liner (e.g. a calendar-day
    「20D 優於短線窗」claim in an archived JSON) is never republished.
    """
    rows = current_horizon_rows(structured_data)
    if rows:
        line = reliability_one_liner(rows)
        if line:
            return line
    return DEFAULT_RULER_LINE


def primary_leads_from_structured(structured_data: dict | None) -> bool:
    rows = current_horizon_rows(structured_data)
    return bool(rows) and bool(analyze_horizon_reliability(rows).get("primary_leads"))
