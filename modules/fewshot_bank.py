"""
Private few-shot example bank for the 「few-shot vs baseline」 title/cover experiment.

Variant A (baseline) = the normal package (unchanged). Variant B (few-shot) = the
same prompt plus 2-3 of the channel's own best performers as *style* examples.

Data quality note: youtube_video_metrics has no impressions / CTR (all 0/NULL;
Data API only), so the metric is lifetime views relative to the median of videos
published within ±30 days (controls for channel growth), recorded as
``views_rel_median``. Bank file is private (chmod 600, dir 700).
"""

import datetime
import json
import os
import random
import re
import sqlite3
import statistics

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BANK_PATH = os.path.join(BASE_DIR, "data", "fewshot_title_bank.json")
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "data", "analytics.db")
DEFAULT_REPORTS_DIR = os.path.join(BASE_DIR, "reports")
FEWSHOT_META_NAME = "fewshot_ab.json"
FEWSHOT_HYPOTHESIS_ID = "FS"

REGIMES = ("大跌", "大漲", "盤整", "個股新聞", "其他")
EXCLUDE_PATTERNS = ("星運", "taiwancasinoguide", "財神倍倍發")
PER_REGIME = 5
MIN_AGE_HOURS = 72
WINDOW_DAYS = 30


def is_excluded(*texts) -> bool:
    blob = " ".join(str(t or "") for t in texts).lower()
    return any(p.lower() in blob for p in EXCLUDE_PATTERNS)


# Old titles that break today's packaging rules are not style examples.
STYLE_REJECT_RE = re.compile(r"專題|\[專題\]|崩盤|吸籌|掃貨|鎖貨|斷頭|血洗|【驗收】")
_COVER_NOISE_RE = re.compile(r"[0-9０-９.+\-億%％!！?？/／\s]|外資|被提款|反攻|為何|卻|買|砍|摔|噴|誰接棒|KY|台股盤後")


def cover_matches_title(cover_text, title) -> bool:
    """The cover's stock words must appear in the title (old rows can carry another day's template)."""
    names = [n for n in (_COVER_NOISE_RE.sub(" ", line).strip() for line in str(cover_text or "").splitlines()) if len(n) >= 2]
    if not names:
        return bool(str(cover_text or "").strip())
    return any(any(part in title for part in n.split()) for n in names)


def classify_regime(index_pct=None, frame="") -> str:
    """大跌 ≤-1.5%、大漲 ≥+1.5%；其餘：重大事件日→個股新聞、|pct|<0.7→盤整、否則其他。"""
    frame = str(frame or "")
    if index_pct is not None:
        if index_pct <= -1.5:
            return "大跌"
        if index_pct >= 1.5:
            return "大漲"
    if frame in {"major_event_day", "重大事件日"}:
        return "個股新聞"
    if index_pct is not None and abs(index_pct) < 0.7:
        return "盤整"
    if frame in {"quiet_setup_day", "整理日"}:
        return "盤整"
    return "其他"


def regime_for_structured_data(structured_data) -> str:
    structured_data = structured_data or {}
    pct = None
    try:
        from modules.title_fact_gate import parse_index_action

        pct = parse_index_action(structured_data.get("index_action")).get("pct")
    except Exception:
        pct = None
    frame = structured_data.get("editorial_frame") or {}
    frame_key = (frame.get("frame") or frame.get("label") or "") if isinstance(frame, dict) else str(frame)
    return classify_regime(pct, frame_key)


def _cover_text_from_prompt(reports_dir, date_str):
    path = os.path.join(reports_dir, "youtube", date_str, "yt_thumbnail_prompt_1.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    primary = re.search(r"^- Primary:\s*(.+)$", text, flags=re.M)
    secondary = re.search(r"^- Secondary:\s*(.+)$", text, flags=re.M)
    return "\n".join(m.group(1).strip() for m in (primary, secondary) if m)


def build_bank(db_path=None, reports_dir=None, per_regime=PER_REGIME, now=None) -> dict:
    db_path = db_path or DEFAULT_DB_PATH
    reports_dir = reports_dir or DEFAULT_REPORTS_DIR
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        videos = conn.execute(
            "SELECT video_id, MIN(run_date), MAX(views), MAX(hours_since_publish), "
            "MAX(COALESCE(impressions,0)), MAX(COALESCE(ctr,0)) "
            "FROM youtube_video_metrics WHERE video_id IS NOT NULL AND video_id != '' GROUP BY video_id"
        ).fetchall()
        titles = {}
        for vid, title in conn.execute(
            "SELECT video_id, title FROM youtube_video_metrics WHERE title IS NOT NULL AND title != '' "
            "ORDER BY measured_at"
        ):
            titles[vid] = title
        covers = {}
        for run_date, variant, cover_text in conn.execute(
            "SELECT run_date, variant, cover_text FROM youtube_thumbnail_variants ORDER BY run_date, variant"
        ):
            covers.setdefault(run_date, {})[str(variant).lower()] = cover_text or ""
        pct = {
            d: p
            for d, p in conn.execute("SELECT snapshot_date, pct_change FROM market_snapshots WHERE ticker='^TWII'")
        }
        frames = {d: f for d, f in conn.execute("SELECT run_date, frame FROM daily_editorial_frames")}
    finally:
        conn.close()

    rows = []
    for vid, run_date, views, hours, impressions, ctr in videos:
        if not run_date or views is None:
            continue
        if hours is not None and hours < MIN_AGE_HOURS:
            continue
        title = titles.get(vid, "")
        cover = (covers.get(run_date) or {}).get("a") or _cover_text_from_prompt(reports_dir, run_date)
        if not title or is_excluded(title, cover) or STYLE_REJECT_RE.search(title):
            continue
        if cover and not cover_matches_title(cover, title):
            cover = ""
        rows.append(
            {
                "video_id": vid,
                "run_date": run_date,
                "views": int(views),
                "impressions": int(impressions or 0),
                "ctr": float(ctr or 0) or None,
                "title": title,
                "cover_text": cover,
                "regime": classify_regime(pct.get(run_date), frames.get(run_date, "")),
                "index_pct": pct.get(run_date),
            }
        )
    for row in rows:
        day = datetime.date.fromisoformat(row["run_date"][:10])
        peers = [
            r["views"]
            for r in rows
            if abs((datetime.date.fromisoformat(r["run_date"][:10]) - day).days) <= WINDOW_DAYS
        ]
        median = statistics.median(peers) if peers else 0
        row["peer_median_views"] = median
        row["views_rel_median"] = round(row["views"] / median, 3) if median else 0.0
    use_ctr = sum(1 for r in rows if r["ctr"]) >= 10
    metric = "ctr" if use_ctr else "views_rel_median"
    by_regime = {}
    for regime in REGIMES:
        pool = [
            r
            for r in rows
            if r["regime"] == regime and r["cover_text"] and (r[metric] or 0) > (0 if use_ctr else 1.0)
        ]
        pool.sort(key=lambda r: -(r[metric] or 0))
        by_regime[regime] = [
            {
                "video_id": r["video_id"],
                "run_date": r["run_date"],
                "regime": regime,
                "title": r["title"],
                "cover_text": r["cover_text"],
                "metric": metric,
                "metric_value": r[metric],
                "views": r["views"],
            }
            for r in pool[:per_regime]
        ]
    return {
        "version": 1,
        "built_at": (now or datetime.datetime.now()).isoformat(timespec="seconds"),
        "metric": metric,
        "metric_note": (
            "CTR" if use_ctr else "無 CTR/曝光資料（Data API only）：lifetime views ÷ ±30 天同期影片觀看中位數，只收 >1.0"
        ),
        "videos_considered": len(rows),
        "regimes": by_regime,
    }


def save_private_json(path, data):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return path


def load_bank(path=None) -> dict:
    try:
        with open(path or DEFAULT_BANK_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def sample_examples(bank, regime, k=3, rng=None, exclude_dates=()) -> list[dict]:
    """2-3 examples: same regime first (random order), topped up from other regimes."""
    rng = rng or random.Random()
    regimes = (bank or {}).get("regimes") or {}
    excluded = set(exclude_dates or ())

    def ok(e):
        return e.get("title") and e.get("run_date") not in excluded and not is_excluded(e.get("title"), e.get("cover_text"))

    same = [e for e in regimes.get(regime) or [] if ok(e)]
    rng.shuffle(same)
    picked = same[:k]
    if len(picked) < 2:
        others = [e for r, items in regimes.items() if r != regime for e in items or [] if ok(e)]
        rng.shuffle(others)
        picked += others[: k - len(picked)]
    return picked[:k]


def fewshot_block(examples) -> str:
    if not examples:
        return ""
    lines = [
        "===本頻道過去表現最好的包裝範例（few-shot；只學結構與語氣，嚴禁照抄內容）===",
        "以下是本頻道自己的高成效影片（同期觀看中位數倍數）。只學：句子結構、節奏、反差位置、問句缺口、封面大字與標題的搭配方式。",
        "嚴禁：沿用範例的股名、金額、漲跌、日期或事件；今天的標題與封面字只能用上方「今日核心數據」與「個股收盤與價格狀態」的事實，其餘所有規則照舊。",
    ]
    for i, e in enumerate(examples, 1):
        cover = " / ".join(x for x in str(e.get("cover_text") or "").splitlines() if x.strip())
        lines.append(f"範例{i}（{e.get('regime')}盤，{e.get('metric_value')}×）：標題「{e.get('title')}」；封面大字「{cover or '（無紀錄）'}」")
    return "\n".join(lines) + "\n"


def write_day_meta(reports_dir, date_str, meta) -> str:
    path = os.path.join(reports_dir, "youtube", date_str, FEWSHOT_META_NAME)
    return save_private_json(path, meta)


def read_day_meta(reports_dir, date_str) -> dict:
    path = os.path.join(reports_dir or DEFAULT_REPORTS_DIR, "youtube", date_str, FEWSHOT_META_NAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def summarize_fewshot(rows, metas) -> dict:
    """rows = ab_test_results; metas = {date: day meta}. few-shot wins vs baseline wins."""
    out = {"n": 0, "fewshot_wins": 0, "baseline_wins": 0, "no_difference": 0, "dates": []}
    for row in rows or []:
        if str(row.get("hypothesis_id") or "") != FEWSHOT_HYPOTHESIS_ID:
            continue
        meta = metas.get(row.get("run_date")) or {}
        fs_arm = str(meta.get("fewshot_arm") or "b").lower()
        winner = str(row.get("winner") or "").lower()
        out["n"] += 1
        out["dates"].append(row.get("run_date"))
        if winner not in {"a", "b"}:
            out["no_difference"] += 1
        elif winner == fs_arm:
            out["fewshot_wins"] += 1
        else:
            out["baseline_wins"] += 1
    return out
