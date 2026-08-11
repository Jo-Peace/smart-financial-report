"""
Data-driven horizon reliability notes for reports / prompts / dashboard.

Prefer describing what the sample actually shows (e.g. 20D hit rate higher than
1/3/5D) rather than hard-selling a fixed slogan. If another window leads, say so.
"""

from __future__ import annotations

from typing import Any

# Preferred reliability window when samples support it.
PRIMARY_CANDIDATE_DAYS = 20
SHORT_WINDOW_DAYS = (1, 3, 5)
MIN_SAMPLES_TO_CLAIM = 20


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
            try:
                hit = float(row["hitRatePct"]) / 100.0
            except (TypeError, ValueError):
                hit = None
        samples = row.get("samples")
        excess = row.get("avg_excess")
        if excess is None:
            excess = row.get("avgExcessPct")
        ret = row.get("avg_return")
        if ret is None:
            ret = row.get("avgReturnPct")
        out.append(
            {
                "horizon_days": _as_int(days),
                "samples": _as_int(samples),
                "hit_rate": _as_float(hit),
                "avg_return": _as_float(ret),
                "avg_excess": _as_float(excess),
            }
        )
    return [r for r in out if r["horizon_days"] > 0]


def _fmt_hit(rate: float | None) -> str:
    if rate is None:
        return "N/A"
    return f"{rate * 100:.1f}%"


def _fmt_excess(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2f}%"


def analyze_horizon_reliability(horizon_rows: list[dict] | None) -> dict[str, Any]:
    """
    Compare windows and decide which one the *current sample* supports as more reliable.
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

    primary_leads = False
    edge_pp = None  # percentage points over short avg
    if primary and primary.get("hit_rate") is not None and short_hit_avg is not None:
        edge_pp = (primary["hit_rate"] - short_hit_avg) * 100
        primary_leads = (
            primary["samples"] >= MIN_SAMPLES_TO_CLAIM
            and primary["hit_rate"] >= short_hit_avg + 0.03  # at least ~3pp
        )

    # Also true if 20D is simply the best hit-rate row with enough samples
    if (
        not primary_leads
        and primary
        and best
        and best["horizon_days"] == PRIMARY_CANDIDATE_DAYS
        and primary["samples"] >= MIN_SAMPLES_TO_CLAIM
    ):
        primary_leads = True
        if short_hit_avg is not None and primary.get("hit_rate") is not None:
            edge_pp = (primary["hit_rate"] - short_hit_avg) * 100

    return {
        "rows": rows,
        "by_days": by_days,
        "primary": primary,
        "short_rows": short,
        "short_hit_avg": short_hit_avg,
        "best": best,
        "primary_leads": primary_leads,
        "edge_pp": edge_pp,
        "primary_days": PRIMARY_CANDIDATE_DAYS,
    }


def natural_reliability_sentences(horizon_rows: list[dict] | None, *, max_sentences: int = 3) -> list[str]:
    """
    Short Chinese sentences that *naturally* state which window looks more reliable.
    Empty list if data is too thin to claim anything.
    """
    info = analyze_horizon_reliability(horizon_rows)
    primary = info["primary"]
    best = info["best"]
    short_avg = info["short_hit_avg"]
    sentences: list[str] = []

    if not info["rows"]:
        return sentences

    if info["primary_leads"] and primary and primary.get("hit_rate") is not None:
        edge = info["edge_pp"]
        edge_text = f"，約高 {edge:.0f} 個百分點" if edge is not None and edge > 0 else ""
        short_text = _fmt_hit(short_avg) if short_avg is not None else "短線窗"
        sentences.append(
            f"依目前本機回測樣本，{PRIMARY_CANDIDATE_DAYS}D 命中率 {_fmt_hit(primary['hit_rate'])}"
            f"（n={primary['samples']}）優於 1D/3D/5D 平均 {short_text}{edge_text}；"
            f"選標與標題主角宜以 {PRIMARY_CANDIDATE_DAYS}D 可信度為準，短線窗只當節奏參考。"
        )
        if primary.get("avg_excess") is not None and primary["avg_excess"] > 0:
            sentences.append(
                f"同一批樣本下 {PRIMARY_CANDIDATE_DAYS}D 平均超額 {_fmt_excess(primary['avg_excess'])}，"
                f"中期方向的可說服力通常高於隔日漲跌。"
            )
        sentences.append(
            "因此口播／標題若只用 1D 驗收論成敗，容易把噪音當成系統失準；"
            f"隔日結果可提一句，但成敗尺子請放在 {PRIMARY_CANDIDATE_DAYS}D。"
        )
    elif best and best.get("hit_rate") is not None:
        sentences.append(
            f"依目前樣本，命中率最高的是 {best['horizon_days']}D"
            f"（{_fmt_hit(best['hit_rate'])}，n={best['samples']}）；"
            "解讀時以該窗為主，其餘窗作對照即可。"
        )
        if primary and primary.get("hit_rate") is not None and best["horizon_days"] != PRIMARY_CANDIDATE_DAYS:
            sentences.append(
                f"{PRIMARY_CANDIDATE_DAYS}D 目前 {_fmt_hit(primary['hit_rate'])}"
                f"（n={primary['samples']}），尚未明顯領先短線窗；先並列觀察、勿硬稱 20D 一定較準。"
            )
    else:
        sentences.append("回測樣本尚不足以比較各時間窗可信度；先並列 1D/3D/5D/20D，勿單獨強調任一窗。")

    return sentences[:max_sentences]


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
    for row in info["rows"]:
        h = row["horizon_days"]
        role = "主指標" if (info["primary_leads"] and h == PRIMARY_CANDIDATE_DAYS) else (
            "短線參考" if h in SHORT_WINDOW_DAYS else "對照"
        )
        lines.append(
            f"- {h}D（{role}）：樣本 {row['samples']}；命中率 {_fmt_hit(row['hit_rate'])}；"
            f"平均報酬 {_fmt_excess(row['avg_return'])}；超額 {_fmt_excess(row['avg_excess'])}"
        )
    return lines or ["- 無"]


def reliability_one_liner(horizon_rows: list[dict] | None) -> str:
    """Single sentence for headers / preflight / dashboard hints."""
    sentences = natural_reliability_sentences(horizon_rows, max_sentences=1)
    return sentences[0] if sentences else ""
