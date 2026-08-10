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
    if value is None:
        return ""
    return str(value).replace(".TW", "").replace(".TWO", "").strip().upper()


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


def _recent_flow_summary(run_date, tickers, lookback_days=5, db_path=DB_PATH):
    conn = _connect(db_path)
    if not conn or not tickers:
        return {}

    start = (_parse_date(run_date) - datetime.timedelta(days=lookback_days * 2)).isoformat()
    placeholders = ",".join(["?"] * len(tickers))
    rows = conn.execute(
        f"""
        SELECT flow_date, ticker, name, side, foreign_net, est_amount
        FROM institutional_flows
        WHERE flow_date BETWEEN ? AND ?
          AND ticker IN ({placeholders})
        ORDER BY flow_date DESC
        """,
        [start, run_date, *tickers],
    ).fetchall()
    conn.close()

    by_ticker = {ticker: {"buy_days": 0, "sell_days": 0, "buy_amount": 0.0, "sell_amount": 0.0, "dates": []} for ticker in tickers}
    seen_dates = {ticker: set() for ticker in tickers}
    for row in rows:
        ticker = row["ticker"]
        if ticker not in by_ticker:
            continue
        if len(seen_dates[ticker]) >= lookback_days and row["flow_date"] not in seen_dates[ticker]:
            continue
        seen_dates[ticker].add(row["flow_date"])
        if row["side"] == "buy":
            by_ticker[ticker]["buy_days"] += 1
            by_ticker[ticker]["buy_amount"] += float(row["est_amount"] or 0)
        elif row["side"] == "sell":
            by_ticker[ticker]["sell_days"] += 1
            by_ticker[ticker]["sell_amount"] += float(row["est_amount"] or 0)
        by_ticker[ticker]["dates"].append(row["flow_date"])
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


def _pick_market_frame(run_date, signal_pack, event_context, db_path):
    """Pick the best non-validation frame from market/event evidence."""
    event_keywords = [
        "fed", "fomc", "adr", "nvidia", "輝達", "台指期", "匯率", "關稅",
        "地緣", "戰爭", "財報", "法說", "長假", "休市", "vix", "美股",
    ]
    event_text = (event_context or "").lower()
    urgency_keywords = [
        "暴跌", "大跌", "血洗", "崩", "飆", "急升", "急貶", "衝擊",
        "今晚", "明天", "今日", "開盤", "休市", "公布", "決議", "異常",
        "crash", "plunge", "spike", "shock", "today", "tomorrow",
    ]
    has_event = any(keyword in event_text for keyword in event_keywords)
    has_urgency = any(keyword in event_text for keyword in urgency_keywords)
    if has_event and has_urgency:
        return {
            "frame": "major_event_day",
            "confidence": 76,
            "reasons": ["今日有重大事件或跨市場線索，應由事件帶出台股影響。"],
            "allowed_tones": ["事件影響", "風險傳導", "隔日風險", "確認條件"],
            "score": 76,
        }

    strongest = signal_pack.get("strongest_anomaly") or {}
    if strongest and float(strongest.get("score") or 0) >= 55:
        conf = min(88, int(float(strongest.get("score") or 55)))
        return {
            "frame": "anomaly_day",
            "confidence": conf,
            "reasons": [f"{strongest.get('type')}：{strongest.get('text')}"],
            "allowed_tones": ["反直覺", "背離", "誰在接手", "確認/反證"],
            "score": conf,
        }

    tickers = [
        _normalize_ticker(row.get("ticker"))
        for row in signal_pack.get("top_candidates", [])[:8]
        if row.get("ticker")
    ]
    flow_summary = _recent_flow_summary(run_date, tickers, db_path=db_path)
    continuation = []
    for row in signal_pack.get("top_candidates", [])[:8]:
        ticker = _normalize_ticker(row.get("ticker"))
        flows = flow_summary.get(ticker, {})
        if flows.get("buy_days", 0) >= 2 and row.get("watch_score", 0) >= 28:
            continuation.append((row, flows))

    if continuation:
        row, flows = continuation[0]
        return {
            "frame": "continuation_day",
            "confidence": 72,
            "reasons": [
                f"{row.get('ticker')} {row.get('name', '')} 近幾日外資買超出現 {flows.get('buy_days')} 天，"
                f"且今日觀察分 {row.get('watch_score')}。"
            ],
            "allowed_tones": ["續買", "續量", "守住關鍵位", "等待確認"],
            "score": 72,
        }

    industry_counts = {}
    for row in signal_pack.get("top_candidates", [])[:8]:
        label = (row.get("industry_label") or "").split("/")[0].strip()
        if label:
            industry_counts[label] = industry_counts.get(label, 0) + 1
    theme = next((label for label, count in industry_counts.items() if count >= 3), "")
    if theme:
        return {
            "frame": "theme_day",
            "confidence": 64,
            "reasons": [f"{theme} 相關標的在前段候選中達 3 檔以上，適合做主題日。"],
            "allowed_tones": ["族群確認", "部分個股", "題材擴散", "分歧"],
            "score": 64,
        }

    return {
        "frame": "quiet_setup_day",
        "confidence": 58,
        "reasons": ["未偵測到足夠強的事件、異常或多日續航證據。"],
        "allowed_tones": ["整理", "觀察", "等待確認", "不要追強", "明日條件"],
        "score": 50,
    }


def build_editorial_frame(
    run_date,
    signal_pack=None,
    validation_results=None,
    event_context="",
    channel_story="",
    db_path=DB_PATH,
    validation_meta=None,
):
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

    v_info = _validation_story_score(validation_results)
    market = _pick_market_frame(run_date, signal_pack, event_context, db_path)

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
