"""
Deterministic event radar for supply-chain catalysts.

This module turns raw news search results into an auditable event pack. It is
intentionally simple and local: no AI calls, no hidden scoring.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta


HIGH_IMPACT_TERMS = {
    "investment": ["投資", "investment", "invest", "擴大", "expand", "capacity", "產能"],
    "advanced_packaging": ["先進封裝", "advanced packaging", "cowos", "2.5d", "3d封裝", "efb"],
    "ai_server": ["ai server", "ai伺服器", "rack-scale", "機架", "helios", "mi450", "gpu", "epyc"],
    "taiwan": ["台灣", "taiwan", "台廠", "供應鏈", "ecosystem"],
    "mega_buyer": ["amd", "nvidia", "輝達", "broadcom", "marvell", "openai", "microsoft", "aws", "google"],
}

ROLE_KEYWORDS = {
    "先進封裝 / CoWoS / 2.5D": ["先進封裝", "advanced packaging", "cowos", "2.5d", "3d封裝", "efb"],
    "AI 伺服器 / 機架系統": ["ai server", "ai伺服器", "rack-scale", "機架", "helios"],
    "GPU / CPU 平台": ["gpu", "mi450", "epyc", "venice", "instinct"],
    "基板 / 載板": ["substrate", "基板", "載板", "abf"],
    "HBM / 記憶體": ["hbm", "memory", "記憶體"],
    "晶圓代工 / 先進製程": ["2nm", "2奈米", "tsmc", "台積電", "foundry"],
}


def _text(event):
    return " ".join(
        str(event.get(key, ""))
        for key in ("event", "title", "snippet", "content")
    ).lower()


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def event_key(event) -> str:
    url = str((event or {}).get("url") or "").strip().rstrip("/").lower()
    if url:
        return "url:" + url
    title = re.sub(
        r"\s+",
        " ",
        str((event or {}).get("event") or (event or {}).get("title") or "").strip().lower(),
    )
    if title:
        return "title:" + title[:160]
    return ""


def default_history_path() -> str:
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data",
        "supply_chain_event_history.json",
    )


def load_event_history(path=None) -> dict:
    path = path or default_history_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(payload, dict) and isinstance(payload.get("events"), dict):
        return payload["events"]
    if isinstance(payload, dict):
        return {k: v for k, v in payload.items() if isinstance(v, dict)}
    return {}


def save_event_history(history: dict, path=None, today=None) -> None:
    path = path or default_history_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cutoff = (_as_date(today) or date.today()) - timedelta(days=21)
    pruned = {}
    for key, row in (history or {}).items():
        last = _as_date((row or {}).get("last_date"))
        if last is not None and last < cutoff:
            continue
        pruned[key] = row
    if len(pruned) > 80:
        ranked = sorted(
            pruned.items(),
            key=lambda item: str((item[1] or {}).get("last_date") or ""),
            reverse=True,
        )
        pruned = dict(ranked[:80])
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"events": pruned}, f, ensure_ascii=False, indent=2)


def prior_days_seen(event, history, today) -> int:
    key = event_key(event)
    if not key:
        return 0
    row = (history or {}).get(key) or {}
    count = int(row.get("count") or 0)
    last = _as_date(row.get("last_date"))
    first = _as_date(row.get("first_date"))
    today_d = _as_date(today) or date.today()
    if count <= 0:
        return 0
    if last is None:
        return count
    if last < today_d:
        return count
    if first is not None and first < today_d:
        return max(0, count - 1)
    return 0


def freshness_label(prior_days: int) -> str:
    if prior_days <= 0:
        return "new"
    if prior_days == 1:
        return "repeat"
    return "stale"


def decay_impact_score(raw_score: int, prior_days: int) -> int:
    """Repeat/stale events cannot alone open the expanded-news gate (>=60)."""
    score = int(raw_score or 0)
    if prior_days <= 0:
        return min(100, score)
    if prior_days == 1:
        return min(59, score)
    return min(40, score)


def record_event_history(history: dict, events, today) -> dict:
    today_s = (_as_date(today) or date.today()).isoformat()
    out = dict(history or {})
    for event in events or []:
        key = event_key(event)
        if not key:
            continue
        row = dict(out.get(key) or {})
        last = str(row.get("last_date") or "")
        count = int(row.get("count") or 0)
        if last != today_s:
            count += 1
        out[key] = {
            "first_date": row.get("first_date") or today_s,
            "last_date": today_s,
            "count": count,
            "title": str(event.get("event") or event.get("title") or "")[:160],
        }
    return out


def score_event(event):
    text = _text(event)
    score = 0
    matched = []
    for group, terms in HIGH_IMPACT_TERMS.items():
        if any(term.lower() in text for term in terms):
            score += 20
            matched.append(group)

    if any(term in text for term in ["billion", "億美元", "百億", "$10", "us$10", "100億"]):
        score += 15
        matched.append("large_amount")

    if any(term in text for term in ["2026", "2027", "next-generation", "下一代"]):
        score += 8
        matched.append("forward_demand")

    return min(100, score), matched


def impacted_roles(event):
    text = _text(event)
    roles = []
    for role, terms in ROLE_KEYWORDS.items():
        if any(term.lower() in text for term in terms):
            roles.append(role)
    return roles


def build_event_radar(events, min_score=45, history=None, today=None):
    ranked = []
    today_d = _as_date(today) or date.today()
    for event in events or []:
        raw_score, matched = score_event(event)
        prior_days = prior_days_seen(event, history, today_d)
        freshness = freshness_label(prior_days)
        score = decay_impact_score(raw_score, prior_days)
        if score < min_score and freshness == "stale":
            # Keep a short stale note so the model can treat it as background,
            # but do not let it rank above fresh tape.
            if raw_score < 60:
                continue
        elif score < min_score:
            continue
        ranked.append({
            "event": event.get("event") or event.get("title") or "",
            "url": event.get("url", ""),
            "snippet": event.get("snippet") or event.get("content", ""),
            "impact_score": score,
            "raw_score": raw_score,
            "freshness": freshness,
            "prior_days": prior_days,
            "matched_factors": matched,
            "impacted_roles": impacted_roles(event),
        })
    return sorted(
        ranked,
        key=lambda row: (
            0 if row.get("freshness") == "new" else 1 if row.get("freshness") == "repeat" else 2,
            -int(row.get("impact_score") or 0),
        ),
    )


def format_event_radar_for_prompt(events):
    if not events:
        return "（未偵測到高分供應鏈事件）"

    lines = ["# 主動消息面事件雷達（背景層，不能蓋過當日法人／量榜）"]
    for row in events[:6]:
        roles = "、".join(row.get("impacted_roles") or ["待判斷"])
        factors = "、".join(row.get("matched_factors") or [])
        fresh = row.get("freshness") or "new"
        lines.append(
            f"- {row['event']}（事件分數 {row['impact_score']}；新舊={fresh}；"
            f"影響角色：{roles}；因子：{factors}）"
        )
        if row.get("snippet"):
            lines.append(f"  摘要：{row['snippet'][:180]}")
        if row.get("url"):
            lines.append(f"  來源：{row['url']}")
    lines.append(
        "- 使用規則：只有 freshness=new 且分數≥60 才評估中期供應鏈影響。"
        "repeat／stale 只當背景一句，禁止當今日開場主因，"
        "禁止用舊利多解釋今日最大買超／賣超，除非事件正文點名該股。"
    )
    return "\n".join(lines)
