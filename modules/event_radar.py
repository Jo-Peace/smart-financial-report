"""
Deterministic event radar for supply-chain catalysts.

This module turns raw news search results into an auditable event pack. It is
intentionally simple and local: no AI calls, no hidden scoring.
"""

from __future__ import annotations


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


def build_event_radar(events, min_score=45):
    ranked = []
    for event in events or []:
        score, matched = score_event(event)
        if score < min_score:
            continue
        ranked.append({
            "event": event.get("event") or event.get("title") or "",
            "url": event.get("url", ""),
            "snippet": event.get("snippet") or event.get("content", ""),
            "impact_score": score,
            "matched_factors": matched,
            "impacted_roles": impacted_roles(event),
        })
    return sorted(ranked, key=lambda row: row["impact_score"], reverse=True)


def format_event_radar_for_prompt(events):
    if not events:
        return "（未偵測到高分供應鏈事件）"

    lines = ["# 主動消息面事件雷達（高優先，不能只看當日榜單）"]
    for row in events[:6]:
        roles = "、".join(row.get("impacted_roles") or ["待判斷"])
        factors = "、".join(row.get("matched_factors") or [])
        lines.append(
            f"- {row['event']}（事件分數 {row['impact_score']}；影響角色：{roles}；因子：{factors}）"
        )
        if row.get("snippet"):
            lines.append(f"  摘要：{row['snippet'][:180]}")
        if row.get("url"):
            lines.append(f"  來源：{row['url']}")
    lines.append("- 使用規則：若事件分數高於 60，日報必須至少評估一次其對台股供應鏈的中期影響，即使相關個股今日未進成交量或法人榜。")
    return "\n".join(lines)
