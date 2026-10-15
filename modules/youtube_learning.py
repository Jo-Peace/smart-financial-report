"""
YouTube packaging learning from ``reports/shared/channel_story_log.md``.

Design rules (item 9 audit):
- rows are de-duplicated by video_id (and by date for rows without a video yet), so a
  rerun of /1 or a mis-matched video ID never counts one video twice
- videos younger than ``MATURE_HOURS`` are "still collecting views": they are listed
  separately and never used as a low (avoid) sample or as a ban source; the age is the
  age when the views were MEASURED (latest snapshot / last refresh), not "now", so a
  stale log never makes a 20-hour count look mature
- when per-refresh snapshots exist in analytics.db, videos are compared at the same
  age (views@48h); snapshots only happen on the /1 refresh (trading days), so a Friday
  upload first measured on Monday (~70h) uses its nearest snapshot within 40–80h
  instead of dropping out; otherwise mature cumulative views are used and labelled
- thresholds are relative to the channel's own median, not an absolute 900 views
- "lessons" are only emitted when a feature separates the high and low groups (lift,
  each group ≥ a quartile; the actual share is printed), never because a keyword
  merely appears in the top group
- title structure IDs (skeleton families) let /2 rotate structures on purpose
"""

import datetime
import json
import os
import re
import statistics

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_STOCK_DB = os.path.join(BASE_DIR, "data", "stock_db.json")

MATURE_HOURS = 72
SAME_AGE_HOURS = 48
ASSUMED_PUBLISH_HOUR = 18  # daily videos go up after the 16:10 /1 run (local time)
MIN_SAME_AGE_SAMPLES = 8
# views@48h fallback when no snapshot pair brackets 48h (Friday / pre-holiday uploads)
SAME_AGE_FALLBACK_WINDOW = (40.0, 80.0)
LIFT_MIN_GROUP = 5
LIFT_MIN_DIFF = 0.20
RELATIVE_WIN_MULTIPLIER = 2.0

_PLACEHOLDER_VIEWS = {"", "-", "待更新", "N/A"}
_PLACEHOLDER_IDS = {"", "-"}
_BRAND_SUFFIX_RE = re.compile(r"[｜|]\s*(?:\d{1,2}/\d{1,2}\s*)?AI帶你看股市.*$")


# ─────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────
def _parse_views(value):
    text = str(value or "").strip()
    if text in _PLACEHOLDER_VIEWS:
        return None
    try:
        return int(text.replace(",", ""))
    except ValueError:
        return None


def _cells(line):
    return [part.strip() for part in line.strip().strip("|").split("|")]


def _read_text(log_path=None, text=None):
    if text is not None:
        return text
    if not log_path or not os.path.exists(log_path):
        return ""
    with open(log_path, "r", encoding="utf-8") as f:
        return f.read()


def parse_channel_story_rows(log_path=None, text=None, include_unviewed=True):
    """
    Main-table rows (9+ columns, date first), de-duplicated, oldest → newest.

    - same video_id on several rows → keep the latest-dated row
    - rows without a video_id → one per date, dropped when that date already has a video row
    """
    content = _read_text(log_path, text)
    raw = []
    for order, line in enumerate(content.splitlines()):
        if not line.startswith("| 20"):
            continue
        parts = _cells(line)
        if len(parts) < 9:
            continue
        raw.append(
            {
                "order": order,
                "date": parts[0],
                "target": parts[1],
                "prediction": parts[2],
                "validation": parts[3],
                "visual_style": parts[4],
                "story_direction": parts[5],
                "video_id": "" if parts[6] in _PLACEHOLDER_IDS else parts[6],
                "title": "" if parts[7] in {"-", "待更新"} else parts[7],
                "views": _parse_views(parts[8]),
            }
        )

    by_video = {}
    for row in raw:
        if not row["video_id"]:
            continue
        current = by_video.get(row["video_id"])
        if current is None or (row["date"], row["order"]) >= (current["date"], current["order"]):
            by_video[row["video_id"]] = row
    dated_with_video = {row["date"] for row in by_video.values()}
    by_date = {}
    for row in raw:
        if row["video_id"] or row["date"] in dated_with_video:
            continue
        by_date[row["date"]] = row  # last row of that date wins

    rows = sorted(list(by_video.values()) + list(by_date.values()), key=lambda r: (r["date"], r["order"]))
    if not include_unviewed:
        rows = [row for row in rows if row["views"] is not None]
    return rows


def _parse_channel_story_rows(log_path):
    """Backward-compatible: de-duplicated rows that have a view count."""
    return parse_channel_story_rows(log_path, include_unviewed=False)


def parse_offsite_dates(log_path=None, text=None):
    """
    {date: label} from the hand-maintained「站外導流變數」table (5-column rows) and from
    an optional 站外= marker anywhere in a main-table row. Off-site seeded videos are
    excluded from title learning (their views are not a packaging result).
    """
    content = _read_text(log_path, text)
    marks = {}
    in_offsite = False
    for line in content.splitlines():
        if "站外導流" in line and line.startswith("#"):
            in_offsite = True
            continue
        if line.startswith("#"):
            in_offsite = False
        if not line.startswith("| 20"):
            continue
        parts = _cells(line)
        if in_offsite and len(parts) < 9 and len(parts) >= 2:
            label = parts[1].replace("站外=", "").strip()
            if label and label not in {"無", "-"}:
                marks[parts[0]] = label
            continue
        for cell in parts:
            match = re.search(r"站外=([^｜|，,；;\s]+)", cell)
            if match and match.group(1) not in {"無", "-"}:
                marks[parts[0]] = match.group(1)
    return marks


# ─────────────────────────────────────────
# Age & same-age comparison
# ─────────────────────────────────────────
def _parse_ts(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)  # host local time
    return parsed


def estimate_published_at(row, published_at=None):
    parsed = _parse_ts(published_at)
    if parsed:
        return parsed
    try:
        day = datetime.date.fromisoformat(str(row.get("date"))[:10])
    except ValueError:
        return None
    return datetime.datetime.combine(day, datetime.time(ASSUMED_PUBLISH_HOUR, 0))


def age_hours(row, now=None, published_at=None):
    published = estimate_published_at(row, published_at)
    if not published:
        return None
    now = now or datetime.datetime.now()
    return max(0.0, (now - published).total_seconds() / 3600.0)


def views_at_age(series, target_hours, fallback_window=None):
    """
    Linear interpolation of views at ``target_hours`` from [(hours_since_publish, views), ...].
    None when the series does not bracket the target (no extrapolation), unless
    ``fallback_window=(lo, hi)`` is given: then the snapshot nearest to the target inside
    [lo, hi] is used (snapshots only happen on trading-day refreshes, so a Friday upload is
    first measured on Monday at ~70h and would otherwise never get a views@48h).
    """
    points = sorted((float(h), float(v)) for h, v in (series or []) if h is not None and v is not None)
    if not points:
        return None
    for h, v in points:
        if abs(h - target_hours) < 1e-6:
            return v
    before = [p for p in points if p[0] < target_hours]
    after = [p for p in points if p[0] > target_hours]
    if before and after:
        (h0, v0), (h1, v1) = before[-1], after[0]
        return v0 + (v1 - v0) * (target_hours - h0) / (h1 - h0)
    if fallback_window:
        lo, hi = fallback_window
        near = [p for p in points if lo <= p[0] <= hi]
        if near:
            return min(near, key=lambda p: abs(p[0] - target_hours))[1]
    return None


def load_metric_series(video_ids, db_path=None):
    """
    {video_id: {"published_at": str|None, "series": [(hours, views), ...]}} from
    analytics.db, opened read-only (theme resolution, wiki and brief summaries never
    write the DB); {} on any error.
    """
    ids = [vid for vid in (video_ids or []) if vid]
    if not ids:
        return {}
    try:
        from modules.analytics_store import read_youtube_metric_series

        return read_youtube_metric_series(ids, db_path=db_path)
    except Exception:
        return {}


def last_refresh_at(metric_series):
    """Latest snapshot time across all videos (≈ the log's last /1 refresh), or None."""
    stamps = [_parse_ts((info or {}).get("last_measured_at")) for info in (metric_series or {}).values()]
    stamps = [s for s in stamps if s]
    return max(stamps) if stamps else None


def annotate_rows(rows, now=None, metric_series=None, offsite=None, measured_at=None):
    """
    Attach age_hours / mature / views_same_age / offsite to each row (copies).

    ``age_hours`` is the video's age when its views were measured: the latest snapshot's
    hours_since_publish when one exists, else the age at the last refresh (``measured_at``,
    default: the newest snapshot of any video), else the age now (legacy rows).
    """
    metric_series = metric_series or {}
    offsite = offsite or {}
    if measured_at is None:
        measured_at = last_refresh_at(metric_series)
    out = []
    for row in rows:
        info = metric_series.get(row.get("video_id")) or {}
        series = [p for p in info.get("series") or [] if p and p[0] is not None]
        item = dict(row)
        if series:
            item["age_hours"] = max(0.0, max(float(h) for h, _ in series))
        else:
            item["age_hours"] = age_hours(row, now=measured_at or now, published_at=info.get("published_at"))
        item["mature"] = item["age_hours"] is not None and item["age_hours"] >= MATURE_HOURS
        exact = views_at_age(series, SAME_AGE_HOURS)
        item["views_same_age"] = exact if exact is not None else views_at_age(
            series, SAME_AGE_HOURS, fallback_window=SAME_AGE_FALLBACK_WINDOW
        )
        item["views_same_age_approx"] = exact is None and item["views_same_age"] is not None
        item["offsite"] = offsite.get(row.get("date"), "")
        out.append(item)
    return out


def comparable_sample(rows):
    """
    (rows_with_metric, basis): same-age views@48h when enough snapshots exist, else
    cumulative views of mature videos. Off-site seeded videos are excluded.
    """
    candidates = [r for r in rows if r.get("views") is not None and not r.get("offsite")]
    same_age = [r for r in candidates if r.get("views_same_age") is not None]
    if len(same_age) >= MIN_SAME_AGE_SAMPLES:
        approx = sum(1 for r in same_age if r.get("views_same_age_approx"))
        lo, hi = SAME_AGE_FALLBACK_WINDOW
        note = f"；{approx} 支無 48h 前後快照，取 {lo:.0f}–{hi:.0f}h 最近一筆" if approx else ""
        return [dict(r, metric=r["views_same_age"]) for r in same_age], f"views@{SAME_AGE_HOURS}h（同齡快照{note}）"
    mature = [r for r in candidates if r.get("mature")]
    return [dict(r, metric=r["views"]) for r in mature], f"累積觀看（僅上架 ≥{MATURE_HOURS} 小時；尚無足夠同齡快照）"


def _quantile(values, q):
    values = sorted(values)
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    pos = (len(values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def channel_baseline(sample):
    metrics = [r["metric"] for r in sample if r.get("metric") is not None]
    if not metrics:
        return {"n": 0, "median": None, "q25": None, "q75": None, "win_threshold": None}
    median = statistics.median(metrics)
    return {
        "n": len(metrics),
        "median": median,
        "q25": _quantile(metrics, 0.25),
        "q75": _quantile(metrics, 0.75),
        "win_threshold": median * RELATIVE_WIN_MULTIPLIER,
    }


def learning_window(log_path=None, text=None, recent_limit=30, now=None, db_path=None, metric_series=None):
    """Shared entry point: deduped recent rows annotated with age / same-age views + baseline."""
    rows = parse_channel_story_rows(log_path, text=text, include_unviewed=True)
    rows = [r for r in rows if r.get("video_id") or r.get("views") is not None]
    recent = rows[-recent_limit:]
    if metric_series is None:
        metric_series = load_metric_series([r.get("video_id") for r in recent], db_path=db_path)
    annotated = annotate_rows(recent, now=now, metric_series=metric_series, offsite=parse_offsite_dates(log_path, text))
    sample, basis = comparable_sample(annotated)
    return {
        "rows": annotated,
        "sample": sample,
        "basis": basis,
        "baseline": channel_baseline(sample),
        "young": [r for r in annotated if r.get("views") is not None and not r.get("mature")],
    }


def relative_win_threshold(log_path=None, text=None, **kwargs):
    """Channel-relative 'valid winner' views threshold (median × 2) or None without data."""
    try:
        return learning_window(log_path, text=text, **kwargs)["baseline"].get("win_threshold")
    except Exception:
        return None


# ─────────────────────────────────────────
# Title structure IDs
# ─────────────────────────────────────────
STRUCTURE_LABELS = {
    "amount_lead": "股名+金額開場（{S}買{N}億！…）",
    "foreign_amount_lead": "外資金額開場（外資{N}億…）",
    "move_lead": "漲跌/點數開場（{S}漲{N}%…／大盤{N}點…）",
    "contradiction_lead": "矛盾句開場（…卻／竟…）",
    "question_lead": "問句開場（…？）",
    "event_lead": "事件開場（Fed／關稅／ADR…→台股）",
    "validation_lead": "驗收開場（上集／驗收…）",
    "other": "其他",
}
_EVENT_WORDS = r"(?:Fed|FOMC|聯準會|升息|降息|非農|CPI|關稅|ADR|輝達|NVIDIA|AMD|川普|除息|法說|財報|連假|休市)"
_STOCK_LEAD_RE = re.compile(r"^[\s🚀🚨📉📈🔥]*([一-鿿A-Za-z\-＊*]{2,8}?)(買|賣|砍|少|倒|抽)\s*\d+(?:\.\d+)?億")
_FOREIGN_LEAD_RE = re.compile(r"^[\s🚀🚨📉📈🔥]*外資\s*\d+(?:\.\d+)?億")
_MOVE_LEAD_RE = re.compile(r"^[\s🚀🚨📉📈🔥]*(?:[一-鿿]{0,6})(?:漲|跌|噴|摔|重挫|大漲|狂飆|暴跌)\s*\d+(?:\.\d+)?(?:%|點)")


def strip_brand(title):
    return _BRAND_SUFFIX_RE.sub("", str(title or "")).strip()


def title_structure_id(title, stock_names=None):
    """
    {"family": ..., "signature": ...}. ``signature`` replaces stock names with {S} and
    numbers with {N}, so two titles with the same skeleton share a signature.
    """
    text = strip_brand(title)
    if not text:
        return {"family": "other", "signature": ""}
    sig = text
    for name in sorted({str(n) for n in (stock_names or []) if len(str(n)) >= 2}, key=len, reverse=True):
        sig = sig.replace(name, "{S}")
    lead = _STOCK_LEAD_RE.match(text)
    if lead and "{S}" not in sig[: len(lead.group(1)) + 3]:
        sig = sig.replace(lead.group(1), "{S}", 1)
    sig = re.sub(r"\d+(?:\.\d+)?", "{N}", sig)
    head = re.split(r"[！!？?，,]", text, maxsplit=1)[0]
    if re.search(r"驗收|上集", head):
        family = "validation_lead"
    elif _FOREIGN_LEAD_RE.match(text):
        family = "foreign_amount_lead"
    elif lead:
        family = "amount_lead"
    elif re.match(rf"^[\s🚀🚨📉📈🔥]*{_EVENT_WORDS}", text):
        family = "event_lead"
    elif _MOVE_LEAD_RE.match(text):
        family = "move_lead"
    elif re.search(r"卻|竟|反而", head):
        family = "contradiction_lead"
    elif re.search(r"[？?]", text[: max(len(head) + 1, 1)]):
        family = "question_lead"
    else:
        family = "other"
    return {"family": family, "signature": sig[:40]}


def load_stock_names(stock_db_path=DEFAULT_STOCK_DB):
    try:
        with open(stock_db_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return sorted({str((v or {}).get("name") or "").strip() for v in data.values() if (v or {}).get("name")})
    except Exception:
        return []


def structure_stats(sample, stock_names=None):
    """{family: {"n", "median", "best"}} over the comparable sample."""
    groups = {}
    for row in sample:
        family = title_structure_id(row.get("title"), stock_names)["family"]
        groups.setdefault(family, []).append(row)
    stats = {}
    for family, items in groups.items():
        values = [r["metric"] for r in items]
        best = max(items, key=lambda r: r["metric"])
        stats[family] = {"n": len(items), "median": statistics.median(values), "best": best}
    return stats


# A must keep the hard rule (stock + verified amount in the first 20 chars), so A is the
# control family; B rotates among structures that can carry the same stock + number.
CONTROL_FAMILY = "amount_lead"
B_ROTATION_FAMILIES = ("contradiction_lead", "question_lead", "move_lead")


def structure_rotation_lines(window=None, log_path=None, text=None, stock_names=None, recent_titles=6):
    """
    Positive rotation (backlog #5). Families whose median ≥ channel median (n≥2) are the
    whitelist; A stays on the control family (hard title rule), B rotates to the best
    whitelisted / least-tested B family so the A/B test measures structure, not wording.
    """
    window = window or learning_window(log_path, text=text)
    stock_names = stock_names if stock_names is not None else load_stock_names()
    sample, baseline = window["sample"], window["baseline"]
    lines = ["# 標題結構輪替（本機計算）"]
    if not sample or baseline.get("median") is None:
        lines.append("- 樣本不足，無法計算結構成效；A 用股名+金額開場，B 換一種結構家族累積樣本。")
        return lines
    stats = structure_stats(sample, stock_names)
    median = baseline["median"]
    ranked = sorted(stats.items(), key=lambda kv: -kv[1]["median"])
    whitelist = [fam for fam, st in ranked if st["n"] >= 2 and st["median"] >= median]
    recent = [title_structure_id(r.get("title"), stock_names)["family"] for r in window["rows"] if r.get("title")][-recent_titles:]
    streak_family = recent[-1] if recent else None
    streak = 0
    for fam in reversed(recent):
        if fam != streak_family:
            break
        streak += 1
    lines.append(f"- 基準：{window['basis']}；頻道中位數 {median:.0f}（n={baseline['n']}）")
    for fam, st in ranked:
        tag = "白名單" if fam in whitelist else ("樣本少" if st["n"] < 2 else "低於中位")
        lines.append(
            f"- {STRUCTURE_LABELS.get(fam, fam)}：n={st['n']}，中位 {st['median']:.0f}（{tag}）"
            f"；最佳 {st['best']['date']} {strip_brand(st['best']['title'])[:30]}"
        )
    recent_b = set(recent[-3:])

    def _b_rank(fam):
        st = stats.get(fam)
        tested = st is not None and st["n"] >= 2
        group = 0 if fam in whitelist else (1 if not tested else 2)  # proven ≥ median, then untested
        return (
            group,
            1 if fam in recent_b else 0,  # prefer a family not used in the last 3 titles
            -(st["median"]) if group == 0 else B_ROTATION_FAMILIES.index(fam),  # n<2 medians are noise
        )

    pick_b = sorted(B_ROTATION_FAMILIES, key=_b_rank)[0]
    if streak_family and streak >= 3:
        lines.append(f"- 最近 {streak} 支連用「{STRUCTURE_LABELS.get(streak_family, streak_family)}」：B 組必須換結構家族。")
    lines.append(f"- 今日 A（對照，硬規則）：{STRUCTURE_LABELS[CONTROL_FAMILY]}")
    lines.append(f"- 今日 B（實驗）：{STRUCTURE_LABELS[pick_b]}——同一檔、同一數字，只換結構")
    lines.append("- 白名單結構不得被列入禁令；禁令只能封鎖低於中位且非前四分位的結構。")
    return lines


# ─────────────────────────────────────────
# Lift-based lessons
# ─────────────────────────────────────────
FEATURES = (
    ("股名+金額開場", lambda t, names: bool(_STOCK_LEAD_RE.match(t))),
    ("含金額（億）", lambda t, names: bool(re.search(r"\d+(?:\.\d+)?億", t))),
    ("問句", lambda t, names: bool(re.search(r"[？?]", t))),
    ("反差詞（卻／竟／仍）", lambda t, names: bool(re.search(r"卻|竟|仍|反而", t))),
    ("漲跌幅／點數", lambda t, names: bool(re.search(r"\d+(?:\.\d+)?(?:%|點)", t))),
    ("驗收／上集", lambda t, names: bool(re.search(r"驗收|上集", t))),
    ("事件詞", lambda t, names: bool(re.search(_EVENT_WORDS, t))),
    ("恐慌詞", lambda t, names: bool(re.search(r"崩盤|血洗|斷頭|逃命|全逃|暴跌", t))),
    ("點名 2 檔以上個股", lambda t, names: sum(1 for n in (names or []) if n and n in t) >= 2),
)


def lift_lessons(sample, stock_names=None, min_group=LIFT_MIN_GROUP, min_diff=LIFT_MIN_DIFF):
    """
    Compare feature rates between the high and low groups of the comparable sample.

    Each group is the top / bottom quartile, but never fewer than ``min_group`` videos, so
    with 10–19 comparable videos the groups are wider than a quartile; the label prints
    the real share (e.g. 前 5 支＝前 33%) instead of claiming 25%.
    Returns (lines, detail). Features present (or absent) in ≥90% of both groups are dropped.
    """
    ranked = sorted([r for r in sample if r.get("title")], key=lambda r: r["metric"])
    n = len(ranked)
    k = max(min_group, n // 4)
    if n < 2 * min_group:
        return [f"- 樣本不足（可比較 {n} 支，需 ≥{2 * min_group}）：不輸出可執行學習，只列原始樣本。"], []
    low, top = ranked[:k], ranked[-k:]
    groups = f"前 {k} 支 vs 後 {k} 支，各占 {k / n:.0%}，n={n}"
    detail, lines = [], []
    for label, fn in FEATURES:
        rate_top = sum(1 for r in top if fn(strip_brand(r["title"]), stock_names)) / len(top)
        rate_low = sum(1 for r in low if fn(strip_brand(r["title"]), stock_names)) / len(low)
        if (rate_top >= 0.9 and rate_low >= 0.9) or (rate_top <= 0.1 and rate_low <= 0.1):
            continue
        diff = rate_top - rate_low
        detail.append({"feature": label, "top": rate_top, "low": rate_low, "diff": diff})
        if abs(diff) >= min_diff:
            direction = "高觀看組較常見" if diff > 0 else "低觀看組較常見"
            lines.append(
                f"- 「{label}」{direction}：高觀看組 {rate_top:.0%} vs 低觀看組 {rate_low:.0%}"
                f"（差 {diff * 100:+.0f} pp；{groups}）"
            )
    if not lines:
        lines.append(f"- 高低兩組（{groups}）的標題特徵無顯著差異（差距 < {min_diff * 100:.0f} pp）；不要把任何單一關鍵字當成勝利公式。")
    return lines, detail


# ─────────────────────────────────────────
# Wiki output
# ─────────────────────────────────────────
def _fmt_row(row):
    metric = row.get("metric", row.get("views"))
    age = row.get("age_hours")
    age_text = f"{age / 24:.0f}天" if age is not None and age >= 48 else (f"{age:.0f}h" if age is not None else "?")
    views_text = f"{int(round(metric)):,}" if metric is not None else "-"
    return f"- {row['date']}｜{views_text} views（上架 {age_text}）｜{strip_brand(row.get('title')) or '-'}"


def format_compact_channel_story(log_path=None, text=None, n_rows=15, window=None, metric_series=None):
    """Compact projection for the packaging prompt: date / title / views@age / 主角 / 站外 (no visual column)."""
    if window is None:
        # include the newest rows that have no video yet (today's /1 row) — context, not learning
        recent = parse_channel_story_rows(log_path, text=text, include_unviewed=True)[-n_rows:]
        if metric_series is None:
            metric_series = load_metric_series([r.get("video_id") for r in recent])
        rows = annotate_rows(recent, metric_series=metric_series, offsite=parse_offsite_dates(log_path, text))
    else:
        rows = window["rows"][-n_rows:]
    lines = ["| 日期 | 使用標題 | 觀看（上架時長） | 核心標的 | 站外 |", "|---|---|---|---|---|"]
    for row in rows:
        age = row.get("age_hours")
        age_text = f"{age / 24:.0f}天" if age is not None and age >= 48 else (f"{age:.0f}h" if age is not None else "?")
        views = f"{row['views']:,}" if row.get("views") is not None else "待更新"
        # names only: the parenthetical Gemini narrative (鎖貨／吸籌…) never reaches the prompt
        hero = re.sub(r"\s*[（(][^）)]*[）)]", "", str(row.get("target") or "")).strip(" 、,，") or "-"
        lines.append(
            f"| {row['date']} | {strip_brand(row.get('title')) or '-'} | {views}（{age_text}） | "
            f"{hero[:24]} | {row.get('offsite') or '無'} |"
        )
    return "\n".join(lines) + "\n"


# Generated /2 rules (rewritten on every regeneration).
GENERATED_PACKAGING_RULES = (
    "標題不要只回報今日數字，要提出一個可被影片回答的市場問題。",
    "恐懼標題必須提供出口：明天看哪個驗收點、失效條件是什麼；禁止血洗／斷頭／逃命這類空泛恐嚇。",
    "若沒有強訊號，承認訊號普通，改用等待確認與明日觀察條件，不硬做警報。",
    "有站外導流（FB 留言等）的影片不列入標題學習。",
)
# Rules the generator itself wrote before 090535f and then deliberately dropped.
# A legacy file still lists them under 「## /2 包裝規則」; they are not hand-written
# and must not be frozen into the manual block on migration. An operator who
# wants one back adds it between the manual-rules markers.
RETIRED_GENERATED_PACKAGING_RULES = (
    "標題不要只回報今日數字，要提出一個可被影片回答的市場問題。",
    "優先使用「具體資金動作 + 觀眾關心的風險 + 明確推測問題」。",
    "封面主字優先做可被影片回答的問題牌，但需輪替題型：逆勢原因、量價矛盾、驗收現場、事件衝擊、明日失效價位；"
    "不要連續使用『榜外吸籌 / 被砍 / 接棒』組合。",
    "若近 5 天已出現相同 scenario 或封面大字，今日必須換另一種家族（例如驗收、逆勢、事件、天量陷阱）。",
    "數據牌可以當副標，不要讓封面只剩股票代號與買超金額。",
    "恐懼標題必須提供出口：誰可能受惠、明天看哪個驗收點、失效條件是什麼。",
    "若沒有強訊號，承認訊號普通，改用等待確認與明日觀察條件，不硬做警報。",
)
# Hand-written rules live between these markers and survive every regeneration.
MANUAL_RULES_HEADING = "## 人工包裝規則（手寫，重產時原樣保留）"
MANUAL_RULES_START = "<!-- manual-rules:start -->"
MANUAL_RULES_END = "<!-- manual-rules:end -->"
_MANUAL_RULES_HINT = "（在下方兩個標記之間增修手寫規則；重產這份檔案時原樣保留，標記外的內容會被覆寫。）"
_LEGACY_RULES_HEADING = "## /2 包裝規則"
_EMPTY_MANUAL_LINE = "- （尚無手寫規則）"


def _clean_manual_body(body) -> str:
    lines = [line for line in str(body or "").split("\n") if line.strip() not in {_EMPTY_MANUAL_LINE, _MANUAL_RULES_HINT}]
    return "\n".join(lines).strip("\n")


def manual_rules_with_notes(text) -> tuple[str, list[str]]:
    """
    (hand-written block, operator notes) of an existing youtube_performance_patterns.md.

    - Both markers: everything between them, verbatim.
    - One marker only (the other deleted by accident): the block is still
      recovered — after START (up to the next heading) or between the manual
      heading and END — with a warning note, never the legacy fallback.
    - Legacy file (no markers): the bullets of 「## /2 包裝規則」 that are not
      a current or retired generated rule (same first 10 characters); the
      notes list what was migrated so the operator can confirm it.
    """
    text = str(text or "")
    has_start, has_end = MANUAL_RULES_START in text, MANUAL_RULES_END in text
    if has_start and has_end:
        body = text.split(MANUAL_RULES_START, 1)[1].split(MANUAL_RULES_END, 1)[0]
        return _clean_manual_body(body), []
    if has_start or has_end:
        if has_start:
            body = re.split(r"\n## ", text.split(MANUAL_RULES_START, 1)[1], maxsplit=1)[0]
            missing = MANUAL_RULES_END
        else:
            before = text.split(MANUAL_RULES_END, 1)[0]
            if MANUAL_RULES_HEADING in before:
                body = before.split(MANUAL_RULES_HEADING, 1)[1]
            else:
                body = re.split(r"\n## [^\n]*\n", before)[-1]
            missing = MANUAL_RULES_START
        body = _clean_manual_body(body)
        return body, [
            f"youtube_performance_patterns.md 少了 {missing}：已保留另一個標記旁的手寫規則（{len(body.splitlines())} 行），"
            "重產後兩個標記都會補回，請確認內容。"
        ]
    if _LEGACY_RULES_HEADING not in text:
        return "", []
    section = text.split(_LEGACY_RULES_HEADING, 1)[1]
    section = re.split(r"\n## ", section, maxsplit=1)[0]
    generated_heads = {rule[:10] for rule in GENERATED_PACKAGING_RULES + RETIRED_GENERATED_PACKAGING_RULES}
    kept = []
    for line in section.splitlines():
        bullet = line.strip()
        if not bullet.startswith("- "):
            continue
        rule = bullet[2:].strip()
        if rule and rule[:10] not in generated_heads and bullet not in kept:
            kept.append(bullet)
    notes = []
    if kept:
        notes.append(
            "舊版「/2 包裝規則」中非系統產生的規則已移入人工規則區（請確認是否保留）：" + "；".join(b[2:] for b in kept)
        )
    return "\n".join(kept), notes


def extract_manual_rules(text) -> str:
    """The hand-written block of an existing youtube_performance_patterns.md (see manual_rules_with_notes)."""
    return manual_rules_with_notes(text)[0]


def manual_rules_section(body) -> list[str]:
    body = str(body or "").strip("\n")
    return [
        MANUAL_RULES_HEADING,
        "",
        _MANUAL_RULES_HINT,
        MANUAL_RULES_START,
        body if body else _EMPTY_MANUAL_LINE,
        MANUAL_RULES_END,
        "",
    ]


def cover_ab_lines(ab_results=None) -> list[str]:
    """Per-hypothesis cover A/B tallies (YouTube 測試與比較; promotion needs ≥3 decided tests)."""
    from modules.cover_ab_test import format_learning_lines, load_results, summarize_results

    try:
        rows = load_results() if ab_results is None else ab_results
        return format_learning_lines(summarize_results(rows))
    except Exception as exc:
        return [f"- （封面 A/B 結果讀取失敗：{exc}）"]


def build_youtube_performance_summary(
    log_path,
    recent_limit=30,
    top_n=8,
    low_n=6,
    now=None,
    metric_series=None,
    stock_names=None,
    manual_rules=None,
    ab_results=None,
):
    window = learning_window(log_path, recent_limit=recent_limit, now=now, metric_series=metric_series)
    if not window["rows"]:
        return "\n".join(
            ["# YouTube Performance Patterns", "", "尚無可分析的觀看數資料。", ""] + manual_rules_section(manual_rules)
        )
    stock_names = stock_names if stock_names is not None else load_stock_names()
    sample, baseline, basis = window["sample"], window["baseline"], window["basis"]
    ranked = sorted(sample, key=lambda r: r["metric"], reverse=True)
    top_rows = ranked[:top_n]
    low_rows = sorted(sample, key=lambda r: r["metric"])[:low_n]
    lessons, _ = lift_lessons(sample, stock_names)
    now = now or datetime.datetime.now()

    lines = [
        "# YouTube Performance Patterns",
        "",
        f"更新時間：{now.strftime('%Y-%m-%d %H:%M')}",
        "",
        "用途：`/2` 生成 YouTube 標題、封面前的成效學習摘要，全部由 `channel_story_log.md`（依影片 ID 去重）"
        "與 analytics.db 觀看快照本機計算。",
        "",
        "## 近期基準",
        "",
        f"- 分析樣本：最近 {len(window['rows'])} 支影片，其中 {len(sample)} 支可比較（{basis}）",
    ]
    if baseline.get("median") is not None:
        lines.extend(
            [
                f"- 中位數：{baseline['median']:.0f}；前四分位門檻 {baseline['q75']:.0f}；後四分位 {baseline['q25']:.0f}",
                f"- 有效勝出門檻：≥ 頻道中位數 × {RELATIVE_WIN_MULTIPLIER:g} = {baseline['win_threshold']:.0f} views"
                "（取代舊的固定 900；未達只作觀察，不判標題好壞；封面 A/B 只看 Studio「測試與比較」）",
            ]
        )
    lines.append(f"- 上架未滿 {MATURE_HOURS} 小時的影片：只列觀察，不列入低樣本、不得當禁令依據")
    lines.extend(["", "## 高觀看樣本（前段）", ""])
    lines.extend(_fmt_row(r) for r in top_rows)
    lines.extend(["", "## 低觀看樣本（僅成熟影片）", ""])
    lines.extend(_fmt_row(r) for r in low_rows)
    if window["young"]:
        lines.extend(["", f"## 觀察中（上架 < {MATURE_HOURS} 小時）", ""])
        lines.extend(_fmt_row(r) for r in window["young"])
    lines.extend(["", "## 標題特徵對照（高觀看組 vs 低觀看組）", ""])
    lines.extend(lessons)
    lines.extend([""])
    lines.extend(structure_rotation_lines(window=window, stock_names=stock_names))
    lines.extend(["", "## 封面 A/B 測試（單一變數，YouTube 測試與比較）", ""])
    lines.extend(cover_ab_lines(ab_results))
    lines.extend(["", _LEGACY_RULES_HEADING, ""])
    lines.extend(f"- {rule}" for rule in GENERATED_PACKAGING_RULES)
    lines.append("")
    # Last, so the tail that /1 and /2 read (_read_tail) always carries it.
    lines.extend(manual_rules_section(manual_rules))
    return "\n".join(lines)


def update_youtube_performance_wiki(log_path, output_path):
    """Regenerate the wiki; the hand-written rules block of the existing file is kept."""
    from modules.atomic_io import atomic_write_text

    existing = ""
    try:
        with open(output_path, encoding="utf-8") as f:
            existing = f.read()
    except OSError:
        existing = ""
    manual, notes = manual_rules_with_notes(existing)
    for note in notes:
        print(f"  [Warning] {note}")
    content = build_youtube_performance_summary(log_path, manual_rules=manual)
    atomic_write_text(output_path, content)
    return output_path
