"""
Deterministic editorial framing for the daily stock video.

Goal: pick ONE main frame so packaging does not default every day to
"驗收日 / 資金輪動 / 異常警報". Previous-day validation is a credibility beat,
not an automatic title/main-story monopoly.
"""

from __future__ import annotations

import datetime
import os
import re
import sqlite3

from modules.tickers import bare_ticker


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "analytics.db")

FRAME_LABELS = {
    "validation_day": "驗收日",
    "major_event_day": "重大事件日",
    "anomaly_day": "異常日",
    "continuation_day": "續航日",
    "theme_day": "主題日",
    "quiet_setup_day": "整理日",
}

# How to use yesterday's validation in packaging / NotebookLM.
# lead  = open with it (rare; only when validation is the real story)
# brief = short mid-segment credibility check
# omit  = data still stored, but do not package around 驗收 wording
VALIDATION_PLACEMENTS = ("lead", "brief", "omit")

WARNING_TERMS = [
    "警訊", "退潮", "背離", "狂砍", "狂買", "接棒", "真空吸籌", "暗藏殺機", "誰接手",
    "榜外吸籌", "資金換手", "資金換車", "換倉", "背後真相", "沒進榜", "影子建倉", "核彈", "閃電換倉",
    "驗收",
]

# Soft threshold: |pct| at/above this can make validation "story-worthy".
VALIDATION_MOVE_PCT = 3.0
# Hard fatigue: if recent titles used 驗收 this many times, never lead with it.
VALIDATION_TITLE_FATIGUE_N = 2


def _normalize_ticker(value):
    return bare_ticker(value)


def _parse_date(value):
    if isinstance(value, datetime.date):
        return value
    return datetime.datetime.strptime(str(value), "%Y-%m-%d").date()


def _connect(db_path=DB_PATH):
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _float(value, default=None):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _prior_sessions(run_date, count):
    """The `count` TWSE sessions strictly before run_date, newest first."""
    from modules.twse_calendar import prev_session

    days = []
    cursor = _parse_date(run_date)
    for _ in range(max(0, int(count))):
        cursor = prev_session(cursor)
        days.append(cursor.isoformat())
    return days


def _recent_flow_summary(run_date, tickers, lookback_days=5, db_path=DB_PATH, today_sides=None):
    """
    Foreign top-list membership per ticker over the last `lookback_days` TWSE
    sessions including today.

    Prior sessions come from institutional_flows strictly before run_date;
    today's membership comes from `today_sides` ({ticker: {"buy", "sell"}},
    built from the signal pack). /1 records today's flows only after the frame
    is built, so reading today's rows from the DB would make a rerun or a
    replay see one more buy day than the live run did.
    """
    tickers = [ticker for ticker in tickers or [] if ticker]
    if not tickers:
        return {}
    by_ticker = {
        ticker: {"buy_days": 0, "sell_days": 0, "buy_amount": 0.0, "sell_amount": 0.0, "dates": []}
        for ticker in tickers
    }
    run_day = _parse_date(run_date).isoformat()
    sessions = _prior_sessions(run_day, max(0, int(lookback_days) - 1))

    rows = []
    conn = _connect(db_path) if sessions else None
    if conn:
        try:
            placeholders = ",".join(["?"] * len(tickers))
            day_marks = ",".join(["?"] * len(sessions))
            rows = conn.execute(
                f"""
                SELECT flow_date, ticker, side, est_amount
                FROM institutional_flows
                WHERE flow_date IN ({day_marks})
                  AND flow_date < ?
                  AND ticker IN ({placeholders})
                ORDER BY flow_date DESC
                """,
                [*sessions, run_day, *tickers],
            ).fetchall()
        finally:
            conn.close()

    seen = set()
    for row in rows:
        ticker = row["ticker"]
        side = row["side"]
        key = (ticker, side, row["flow_date"])
        if ticker not in by_ticker or side not in ("buy", "sell") or key in seen:
            continue
        seen.add(key)
        by_ticker[ticker][f"{side}_days"] += 1
        by_ticker[ticker][f"{side}_amount"] += float(row["est_amount"] or 0)
        if row["flow_date"] not in by_ticker[ticker]["dates"]:
            by_ticker[ticker]["dates"].append(row["flow_date"])

    for ticker, sides in (today_sides or {}).items():
        ticker = _normalize_ticker(ticker)
        if ticker not in by_ticker:
            continue
        for side in ("buy", "sell"):
            if side in (sides or ()):
                by_ticker[ticker][f"{side}_days"] += 1
        if sides and run_day not in by_ticker[ticker]["dates"]:
            by_ticker[ticker]["dates"].insert(0, run_day)
    return by_ticker


def _recent_title_terms(channel_story="", limit=7):
    rows = []
    for line in (channel_story or "").splitlines():
        if not line.startswith("| ") or "日期" in line or "---" in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 8:
            rows.append(cells[7])
    titles = rows[-limit:]
    counts = {term: sum(1 for title in titles if term in title) for term in WARNING_TERMS}
    repeated = [term for term, count in counts.items() if count >= 2]
    return {
        "recent_titles": titles,
        "repeated_terms": repeated,
        "counts": counts,
    }


def build_title_fatigue_report(channel_story="", limit=5, youtube_assets_dir=None):
    from modules.packaging_fatigue import build_title_fatigue_report as _build_packaging_fatigue_report

    return _build_packaging_fatigue_report(
        channel_story=channel_story,
        youtube_assets_dir=youtube_assets_dir,
    )


def _validation_story_score(validation_results):
    """
    Score how interesting previous-day validation is as the MAIN story.
    Routine single-name +1% hits should not monopolize the frame.
    """
    material = [row for row in (validation_results or []) if row.get("hit") is True or row.get("hit") is False]
    if not material:
        return {
            "material": [],
            "hits": 0,
            "misses": 0,
            "score": 0,
            "story_worthy": False,
            "highlights": [],
        }

    hits = sum(1 for row in material if row.get("hit") is True)
    misses = len(material) - hits
    score = 0
    highlights = []

    for row in material:
        pct = abs(_float(row.get("pct_change"), 0.0) or 0.0)
        name = f"{row.get('id', '')} {row.get('name', '')}".strip()
        if row.get("stop_loss_triggered") is True:
            score += 35
            highlights.append(f"{name} 觸發停損")
        if pct >= 7:
            score += 40
            highlights.append(f"{name} 波動 {pct:.1f}%")
        elif pct >= VALIDATION_MOVE_PCT:
            score += 22
            highlights.append(f"{name} 波動 {pct:.1f}%")
        elif pct >= 1.5:
            score += 8
        else:
            score += 3  # tiny move: credibility note only

        if row.get("hit") is False and pct >= VALIDATION_MOVE_PCT:
            score += 10  # clear miss is more newsworthy than tiny hit

    if hits >= 2:
        score += 15
    if misses >= 2:
        score += 12
    if hits and misses:
        score += 8  # mixed card is more interesting than all tiny hits

    # Story-worthy: clear move, stop-loss, multi-name card, or mixed results with some move.
    story_worthy = (
        score >= 28
        or any(abs(_float(r.get("pct_change"), 0) or 0) >= VALIDATION_MOVE_PCT for r in material)
        or any(r.get("stop_loss_triggered") is True for r in material)
        or len(material) >= 2
    )

    return {
        "material": material,
        "hits": hits,
        "misses": misses,
        "score": score,
        "story_worthy": story_worthy,
        "highlights": highlights[:4],
    }


# --- Major-event evidence (structured sources only; no headline keyword scan) ---

# Calendar kinds that are genuine cross-market catalysts for the TW session.
# US prints land after the TW close, so the TW session that digests them is
# the next session after the print date; fomc_tw is already the TW date.
MAJOR_CALENDAR_WEIGHTS = {"fomc_tw": 20, "cpi": 15, "nfp": 15, "ppi": 8}
NEXT_SESSION_DIGEST_KINDS = {"cpi", "nfp", "ppi"}
RADAR_MIN_IMPACT = 60
MAJOR_EVENT_MIN_INDEX_MOVE = 1.0
RADAR_MAX_TITLE_AGE_DAYS = 7
MAJOR_EVENT_MIN_SCORE = 60
STRONG_ANOMALY_SCORE = 55
CONTINUATION_MIN_BUY_DAYS = 3   # of the last CONTINUATION_LOOKBACK_SESSIONS TWSE sessions (incl. today)
CONTINUATION_LOOKBACK_SESSIONS = 5
CONTINUATION_MIN_WATCH = 35
FRAME_PRIORITY = ("anomaly_day", "major_event_day", "continuation_day", "theme_day", "quiet_setup_day")

# Social / forum / video hosts are not tradeable event sources on their own.
LOW_TIER_SOURCE_HOSTS = (
    "facebook.com", "fb.com", "instagram.com", "threads.net", "x.com", "twitter.com",
    "youtube.com", "youtu.be", "ptt.cc", "dcard.tw", "mobile01.com", "tiktok.com",
    "line.me", "medium.com", "vocus.cc",
)
_TITLE_DATE_RE = re.compile(r"(20\d{2})[/\-.年](\d{1,2})[/\-.月](\d{1,2})")


def _source_host(url):
    match = re.match(r"^[a-z]+://([^/]+)", str(url or "").strip().lower())
    host = match.group(1) if match else ""
    return host[4:] if host.startswith("www.") else host


def is_low_tier_source(url):
    host = _source_host(url)
    return bool(host) and any(host == h or host.endswith("." + h) for h in LOW_TIER_SOURCE_HOSTS)


def _title_date_age_days(text, run_date):
    """Days between a date written in the headline and run_date (None if no date)."""
    match = _TITLE_DATE_RE.search(str(text or ""))
    if not match:
        return None
    try:
        written = datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        return (_parse_date(run_date) - written).days
    except (ValueError, TypeError):
        return None


def _url_date_age_days(url, run_date):
    """
    Days since the oldest date written in the URL path (e.g. /2026-5-20-...,
    /2026/06/02/), None when the path carries no date. Publishers put the
    publication date there; the radar has no other age field.
    """
    text = str(url or "")
    path = re.sub(r"^[a-z]+://[^/]+", "", text.strip(), flags=re.I)
    ages = []
    for match in _TITLE_DATE_RE.finditer(path):
        try:
            written = datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            ages.append((_parse_date(run_date) - written).days)
        except (ValueError, TypeError):
            continue
    return max(ages) if ages else None


def eligible_radar_events(event_radar, run_date, min_impact=RADAR_MIN_IMPACT):
    """
    Radar rows usable as event evidence: freshness=new, impact >= min_impact,
    not a social/forum host, and no stale date written in the headline or in
    the URL path. Rows without a freshness tag (structured_data written before
    the radar tagged rows, or hand-built rows) are rejected as 'unknown':
    their age cannot be checked. Note freshness=new only means "first seen in
    local radar history", so the dated-URL / headline checks still apply.
    Returns (eligible_rows, rejected_notes).
    """
    eligible, rejected = [], []
    for row in event_radar or []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("event") or "")[:40]
        impact = _float(row.get("impact_score"), 0.0) or 0.0
        freshness = str(row.get("freshness") or "unknown")
        if freshness != "new":
            rejected.append(f"{title}：非新事件（{freshness}）")
            continue
        if impact < min_impact:
            continue
        if is_low_tier_source(row.get("url")):
            rejected.append(f"{title}：來源為社群／論壇（{_source_host(row.get('url'))}），未分級")
            continue
        age = _title_date_age_days(row.get("event"), run_date)
        if age is not None and age > RADAR_MAX_TITLE_AGE_DAYS:
            rejected.append(f"{title}：標題日期已過 {age} 天")
            continue
        url_age = _url_date_age_days(row.get("url"), run_date)
        if url_age is not None and url_age > RADAR_MAX_TITLE_AGE_DAYS:
            rejected.append(f"{title}：來源網址日期已過 {url_age} 天")
            continue
        eligible.append(row)
    eligible.sort(key=lambda r: (-(_float(r.get("impact_score"), 0.0) or 0.0), str(r.get("event") or "")))
    return eligible, rejected


def _calendar_catalysts(run_date, calendar_events):
    """Today's TW-session catalysts from the deterministic calendar: [(weight, text)]."""
    try:
        from modules.twse_calendar import next_session
    except Exception:  # pragma: no cover - calendar module always ships
        next_session = None
    today = _parse_date(run_date)
    hits = []
    for row in calendar_events or []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "")
        weight = MAJOR_CALENDAR_WEIGHTS.get(kind)
        if not weight:
            continue
        try:
            event_day = _parse_date(str(row.get("date"))[:10])
        except (ValueError, TypeError):
            continue
        digest_day = event_day
        if kind in NEXT_SESSION_DIGEST_KINDS and next_session is not None:
            digest_day = next_session(event_day)
        if digest_day != today:
            continue
        head = str(row.get("text") or kind).split("。")[0]
        hits.append((weight, f"{row.get('date')} {head}"))
    hits.sort(key=lambda item: (-item[0], item[1]))
    return hits


def _role_tokens(roles):
    tokens = set()
    for role in roles or []:
        for part in re.split(r"[/／、,，]", str(role)):
            part = part.strip()
            if len(part) >= 2:
                tokens.add(part)
    return tokens


def _radar_touches_focus(row, signal_pack):
    tokens = _role_tokens(row.get("impacted_roles"))
    if not tokens:
        return False
    for cand in (signal_pack or {}).get("top_candidates", [])[:8]:
        label = str(cand.get("industry_label") or "")
        if label and any(token in label for token in tokens):
            return True
    return False


def score_major_event(run_date, signal_pack=None, event_radar=None, calendar_events=None, index_pct=None):
    """
    Major-event candidate from structured evidence only.

    Needs (calendar catalyst or eligible radar event) AND a TW market link
    (|index pct| >= 1%). A radar event touching today's focus names adds
    weight. A holiday or a weekly macro-calendar headline alone never qualifies.
    """
    cal_hits = _calendar_catalysts(run_date, calendar_events)
    radar_rows, radar_rejected = eligible_radar_events(event_radar, run_date)
    top_radar = radar_rows[0] if radar_rows else None
    radar_touch = bool(top_radar and _radar_touches_focus(top_radar, signal_pack))
    radar_impact = _float((top_radar or {}).get("impact_score"), 0.0) or 0.0
    move = abs(_float(index_pct, 0.0) or 0.0) if index_pct is not None else None

    evidence = []
    score = 45.0
    if cal_hits:
        score += cal_hits[0][0]
        evidence.append("日曆：" + "；".join(text for _, text in cal_hits[:2]))
    if top_radar:
        score += max(0.0, min(15.0, (radar_impact - RADAR_MIN_IMPACT) / 40.0 * 15.0))
        evidence.append(f"事件雷達：{str(top_radar.get('event') or '')[:48]}（影響 {int(radar_impact)}／new）")
        if radar_touch:
            score += 5
            evidence.append("事件影響族群與今日焦點股重疊")
    if move is not None:
        if move >= 2.0:
            score += 20
        elif move >= 1.0:
            score += 12
        evidence.append(f"大盤 {_float(index_pct, 0.0):+.2f}%")

    has_evidence = bool(cal_hits or top_radar)
    # The TW tape must react; a strong radar headline on a flat day stays background.
    market_link = move is not None and move >= MAJOR_EVENT_MIN_INDEX_MOVE
    qualifies = has_evidence and market_link and score >= MAJOR_EVENT_MIN_SCORE
    if has_evidence and not market_link:
        evidence.append(
            f"有事件但台股盤面未反應（大盤未達 ±{MAJOR_EVENT_MIN_INDEX_MOVE:g}%），不升級為重大事件日"
        )
    return {
        "qualifies": qualifies,
        "score": round(min(88.0, score), 1) if qualifies else 0.0,
        "evidence": evidence,
        "rejected": radar_rejected[:3],
    }


def _is_structural_flow_name(ticker):
    """2330 / 2317 / 2454 and 00-prefixed ETFs: always near the top lists, not a story."""
    from modules.market_signal_scorer import EXCLUDED_WATCH_TICKERS

    ticker = _normalize_ticker(ticker)
    return ticker in EXCLUDED_WATCH_TICKERS or ticker.startswith("00")


def _pick_market_frame(
    run_date,
    signal_pack,
    event_context,
    db_path,
    *,
    event_radar=None,
    calendar_events=None,
    index_pct=None,
):
    """
    Score every non-validation frame and keep the strongest.

    `event_context` (free-text headlines) is accepted for backward
    compatibility but no longer decides the frame: undated headline keyword
    co-occurrence made almost every session a 重大事件日.
    """
    signal_pack = signal_pack or {}
    candidates = []

    strongest = signal_pack.get("strongest_anomaly") or {}
    anomaly_score = _float(strongest.get("score"), 0.0) or 0.0
    if strongest and anomaly_score >= STRONG_ANOMALY_SCORE:
        conf = int(min(88, round(anomaly_score)))
        candidates.append({
            "frame": "anomaly_day",
            "confidence": conf,
            "reasons": [f"{strongest.get('type')}：{strongest.get('text')}（背離強度 {anomaly_score:g}）"],
            "allowed_tones": ["反直覺", "背離", "誰在接手", "確認/反證"],
            "score": conf,
        })

    major = score_major_event(
        run_date,
        signal_pack=signal_pack,
        event_radar=event_radar,
        calendar_events=calendar_events,
        index_pct=index_pct,
    )
    if major["qualifies"]:
        conf = int(round(major["score"]))
        candidates.append({
            "frame": "major_event_day",
            "confidence": conf,
            "reasons": ["今日有可驗證的事件且台股盤面有反應：" + "；".join(major["evidence"])],
            "allowed_tones": ["事件影響", "風險傳導", "隔日風險", "確認條件"],
            "score": conf,
        })

    top = signal_pack.get("top_candidates", [])[:8]
    tickers = [_normalize_ticker(row.get("ticker")) for row in top if row.get("ticker")]
    today_sides = {}
    for row in top:
        sides = {side for side in ("buy", "sell") if row.get(f"foreign_{side}_rank")}
        if sides and row.get("ticker"):
            today_sides[_normalize_ticker(row.get("ticker"))] = sides
    try:
        flow_summary = _recent_flow_summary(
            run_date,
            tickers,
            lookback_days=CONTINUATION_LOOKBACK_SESSIONS,
            db_path=db_path,
            today_sides=today_sides,
        )
    except (sqlite3.Error, ValueError):
        flow_summary = {}
    best_cont = None
    for row in top:
        ticker = _normalize_ticker(row.get("ticker"))
        if _is_structural_flow_name(ticker):
            # Index weights / ETFs sit on the foreign top lists structurally;
            # repeat membership is not a flow-continuation story.
            continue
        flows = flow_summary.get(ticker, {})
        buy_days = int(flows.get("buy_days", 0) or 0)
        watch = _float(row.get("watch_score"), 0.0) or 0.0
        pct = _float(row.get("pct_change"), 0.0) or 0.0
        # Continuation = still a top foreign buy today, on most recent sessions,
        # and the tape agrees (not falling, direction score clears the bar).
        if (
            row.get("foreign_buy_rank")
            and buy_days >= CONTINUATION_MIN_BUY_DAYS
            and watch >= CONTINUATION_MIN_WATCH
            and pct >= 0
        ):
            cont_score = min(86.0, 40 + 6 * buy_days + min(10.0, watch / 6.0))
            key = (cont_score, ticker)
            if best_cont is None or key > best_cont[0]:
                best_cont = (key, row, flows)
    if best_cont:
        (cont_score, _), row, flows = best_cont
        conf = int(round(cont_score))
        candidates.append({
            "frame": "continuation_day",
            "confidence": conf,
            "reasons": [
                f"{row.get('ticker')} {row.get('name', '')} 近 {CONTINUATION_LOOKBACK_SESSIONS} 個交易日（含今日）"
                f"外資買超榜出現 {flows.get('buy_days')} 天，"
                f"且今日觀察分 {row.get('watch_score')}。"
            ],
            "allowed_tones": ["續買", "續量", "守住關鍵位", "等待確認"],
            "score": conf,
        })

    industry_counts = {}
    for row in top:
        label = (row.get("industry_label") or "").split("/")[0].strip()
        if label:
            industry_counts[label] = industry_counts.get(label, 0) + 1
    themes = sorted(((count, label) for label, count in industry_counts.items() if count >= 3), reverse=True)
    if themes:
        count, theme = themes[0]
        conf = int(min(80, 52 + 4 * count))
        candidates.append({
            "frame": "theme_day",
            "confidence": conf,
            "reasons": [f"{theme} 相關標的在前段候選中達 {count} 檔，適合做主題日。"],
            "allowed_tones": ["族群確認", "部分個股", "題材擴散", "分歧"],
            "score": conf,
        })

    candidates.append({
        "frame": "quiet_setup_day",
        "confidence": 58,
        "reasons": ["未偵測到足夠強的事件、異常或多日續航證據。"],
        "allowed_tones": ["整理", "觀察", "等待確認", "不要追強", "明日條件"],
        "score": 50,
    })

    priority = {frame: idx for idx, frame in enumerate(FRAME_PRIORITY)}
    candidates.sort(key=lambda c: (-float(c["score"]), priority.get(c["frame"], 99)))
    chosen = dict(candidates[0])
    chosen["frame_scores"] = {c["frame"]: c["score"] for c in candidates}
    chosen["event_evidence"] = major["evidence"]
    if major["rejected"]:
        chosen["event_rejected"] = major["rejected"]
    return chosen


def build_editorial_frame(
    run_date,
    signal_pack=None,
    validation_results=None,
    event_context="",
    channel_story="",
    db_path=DB_PATH,
    validation_meta=None,
    *,
    event_radar=None,
    calendar_events=None,
    index_pct=None,
):
    """
    Pick ONE main frame. Event evidence comes from structured sources:
    `calendar_events` (market_calendar rows; computed from run_date when
    omitted), `event_radar` (supply_chain_event_radar rows) and `index_pct`
    (^TWII % change). `event_context` is kept for old callers only.
    """
    signal_pack = signal_pack or {}
    validation_results = validation_results or []
    validation_meta = validation_meta or {}
    prohibited_tones = ["資金輪動"]

    title_stats = _recent_title_terms(channel_story)
    title_counts = title_stats.get("counts", {})
    validation_title_uses = int(title_counts.get("驗收", 0) or 0)
    validation_fatigued = validation_title_uses >= VALIDATION_TITLE_FATIGUE_N

    # Gap skip: previous /1 was not last trading session → never package as 上集驗收.
    onair_ok = validation_meta.get("onair_validation_eligible")
    if validation_meta and onair_ok is False:
        validation_results = []

    if calendar_events is None:
        try:
            from modules.market_calendar import nearby_market_events

            calendar_events = nearby_market_events(run_date, back_days=6, forward_days=0)
        except Exception:
            calendar_events = []

    v_info = _validation_story_score(validation_results)
    market = _pick_market_frame(
        run_date,
        signal_pack,
        event_context,
        db_path,
        event_radar=event_radar,
        calendar_events=calendar_events,
        index_pct=index_pct,
    )

    # Default: market/event owns the day; validation is a short credibility beat.
    frame = market["frame"]
    confidence = market["confidence"]
    reasons = list(market["reasons"])
    allowed_tones = list(market["allowed_tones"])
    validation_placement = "omit"

    if validation_meta and onair_ok is False:
        skip = validation_meta.get("skip_reason") or "前次產出間隔過長"
        reasons.append(f"驗收略過：{skip}")
        prohibited_tones.extend(["驗收", "驗收日", "上集驗收", "驗收現場", "上集點名", "昨日點名"])
        validation_placement = "omit"
    elif v_info["material"]:
        reasons.append(
            f"昨日驗收有 {v_info['hits']} 命中、{v_info['misses']} 失準"
            + ("；" + "；".join(v_info["highlights"]) if v_info["highlights"] else "")
            + "（預設只作短段可信度，不自動佔主軸）。"
        )
        validation_placement = "brief"

        # Only promote validation to MAIN frame when it is story-worthy,
        # not fatigued, and at least as strong as competing market frames.
        can_lead = (
            v_info["story_worthy"]
            and not validation_fatigued
            and v_info["score"] >= max(28, market["score"] - 5)
        )
        if can_lead:
            frame = "validation_day"
            confidence = min(86, 55 + v_info["score"] // 2)
            reasons.insert(
                0,
                f"驗收本身夠強（score={v_info['score']}）且近期標題未過度使用「驗收」，可當主軸。",
            )
            allowed_tones = ["驗收", "命中", "失準", "修正假說", "明日條件"]
            validation_placement = "lead"
        elif validation_fatigued:
            reasons.append(
                f"近 {len(title_stats.get('recent_titles') or [])} 則標題已出現「驗收」"
                f" {validation_title_uses} 次 → 禁用驗收當主鉤子，改 brief/omit。"
            )
            validation_placement = "brief"
            prohibited_tones.extend(["驗收日", "上集驗收", "驗收現場"])
        elif not v_info["story_worthy"]:
            reasons.append(
                "驗收波動偏小或僅例行命中，不值得當主軸；改以盤面主線帶節奏。"
            )
            validation_placement = "brief"

    if frame != "anomaly_day":
        prohibited_tones.extend(["背離警訊", "退潮警報", "暗藏殺機"])
    if frame != "continuation_day":
        prohibited_tones.extend(["資金續航", "主力還在", "接棒"])
    if frame == "quiet_setup_day":
        prohibited_tones.extend(["狂砍", "狂買", "噴出", "崩盤"])
    if frame != "validation_day":
        # Soft-block title formulas that re-center every day on 驗收.
        prohibited_tones.extend(["驗收上集點名", "上集點名驗收"])
    if validation_fatigued:
        prohibited_tones.append("驗收")

    fatigue_report = build_title_fatigue_report(channel_story)
    repeated_terms = title_stats.get("repeated_terms", [])
    for term in repeated_terms:
        if title_counts.get(term, 0) >= 3:
            prohibited_tones.append(term)

    return {
        "frame": frame,
        "label": FRAME_LABELS.get(frame, frame),
        "confidence": confidence,
        "reasons": reasons,
        "allowed_tones": allowed_tones,
        "prohibited_tones": sorted(set(prohibited_tones)),
        "title_fatigue_report": fatigue_report,
        # Downstream packaging / NotebookLM should read these:
        "validation_placement": validation_placement,
        "validation_story_score": v_info["score"],
        "validation_fatigued": validation_fatigued,
        "validation_hits": v_info["hits"],
        "validation_misses": v_info["misses"],
        # Audit trail: every candidate frame's score and the event evidence used.
        "frame_scores": market.get("frame_scores") or {},
        "event_evidence": market.get("event_evidence") or [],
        "event_rejected": market.get("event_rejected") or [],
    }


def format_editorial_frame_for_prompt(frame):
    if not frame:
        return "（尚未建立本地主軸分類）"
    placement = frame.get("validation_placement") or "brief"
    placement_guide = {
        "lead": "驗收可當開場主軸（今天少數情況）。",
        "brief": "驗收只做 20 秒可信度短段，不要當標題主鉤子、不要佔前半集。",
        "omit": "今天包裝不要強調驗收用語；資料仍可在內文一句帶過。",
    }.get(placement, "")

    lines = [
        "# 本地主軸分類（高優先，Gemini 不得自行升級語氣）",
        f"- 今日主軸：{frame.get('label')}（信心 {frame.get('confidence')}/100）",
        f"- 驗收使用方式：{placement} — {placement_guide}",
        f"- 可用語氣：{', '.join(frame.get('allowed_tones') or [])}",
        f"- 禁用/慎用語氣：{', '.join(frame.get('prohibited_tones') or [])}",
        "- 判斷依據：",
    ]
    for reason in frame.get("reasons") or ["無"]:
        lines.append(f"  - {reason}")
    if frame.get("title_fatigue_report"):
        lines.extend(["", frame["title_fatigue_report"]])
    return "\n".join(lines)
