"""
Track recent title/thumbnail formula usage and steer /2 packaging away from repeats.
"""

from __future__ import annotations

import os
import re
from collections import Counter

from modules.report_paths import iter_thumbnail_prompt_paths


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
YOUTUBE_ASSETS_DIR = REPORTS_DIR

TITLE_FORMULA_TERMS = [
    "榜外吸籌",
    "資金換手",
    "資金換車",
    "換倉",
    "接棒",
    "被砍",
    "背後真相",
    "沒進榜",
    "影子建倉",
    "影子",
    "核彈",
    "閃電換倉",
    "誰接棒",
    "誰在接",
    "驗收",
    "暗藏殺機",
    "資金輪動",
]

SCENARIO_FAMILIES = {
    "off_board": ["榜外吸籌", "高買低量", "沒進榜"],
    "rotation": ["資金換手", "砍一邊買一邊", "法人對決"],
    "volume_trap": ["天量出貨", "天量陷阱", "高量分歧"],
    "validation": ["驗收現場", "上集驗收"],
    "anomaly": ["量價異常", "逆勢買進", "大跌守線"],
    "event": ["事件衝擊", "消息引爆"],
    "fallback": ["主力點火", "資金轉向", "危機反轉"],
}

OVERLAY_FATIGUE_PATTERNS = [
    "榜外吸籌",
    "被砍",
    "接棒",
    "誰接棒",
    "資金換手",
    "資金換車",
]

# Soft replacements keep titles runnable and distinct — never strip to empty.
TERM_SOFT_REPLACEMENTS = {
    "榜外吸籌": "量榜缺席",
    "沒進榜": "量能榜外",
    "資金換手": "籌碼換邊",
    "資金換車": "籌碼換邊",
    "閃電換倉": "盤中換邊",
    "換倉": "換邊",
    "接棒": "誰在買",
    "誰接棒": "誰在買",
    "誰在接": "誰在買",
    "被砍": "遭賣超",
    "核彈": "巨量",
    "影子建倉": "法人布局",
    "影子": "法人",
    "背後真相": "數據矛盾",
    "暗藏殺機": "風險點",
    "資金輪動": "籌碼移動",
}

BRAND_SUFFIX = "｜AI帶你看股市"

COMPOUND_REPLACEMENTS = [
    ("能接棒嗎", "誰在買"),
    ("能接棒", "誰在承接"),
    ("誰接棒", "誰在買"),
    ("資金換手", "籌碼換邊"),
    ("驗收上集點名", "上集點名"),
    ("驗收現場", "上集點名"),
    ("驗收上集", "上集點名"),
    ("上集驗收", "上集點名"),
]


def _parse_channel_story_titles(channel_story="", limit=7):
    titles = []
    for line in (channel_story or "").splitlines():
        if not line.startswith("| ") or "日期" in line or "---" in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 8 and cells[7]:
            titles.append(cells[7])
    return titles[-limit:]


def _parse_recent_thumbnail_prompts(youtube_assets_dir=YOUTUBE_ASSETS_DIR, limit=14):
    reports_dir = youtube_assets_dir if youtube_assets_dir and os.path.basename(youtube_assets_dir) == "reports" else REPORTS_DIR
    files = iter_thumbnail_prompt_paths(reports_dir, limit=limit)
    rows = []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        scenario = ""
        primary = ""
        secondary = ""
        for line in text.splitlines():
            if line.startswith("Scenario:"):
                scenario = line.split(":", 1)[1].strip()
            elif line.startswith("- Primary:"):
                primary = line.split(":", 1)[1].strip()
            elif line.startswith("- Secondary:"):
                secondary = line.split(":", 1)[1].strip()
        if scenario:
            rows.append(
                {
                    "path": path,
                    "scenario": scenario,
                    "primary": primary,
                    "secondary": secondary,
                }
            )
    return rows


def _family_for_scenario(scenario_name):
    for family, names in SCENARIO_FAMILIES.items():
        if scenario_name in names:
            return family
    return "other"


def build_fatigue_state(channel_story="", youtube_assets_dir=YOUTUBE_ASSETS_DIR, title_limit=7, prompt_limit=10):
    youtube_assets_dir = youtube_assets_dir or YOUTUBE_ASSETS_DIR
    recent_titles = _parse_channel_story_titles(channel_story, limit=title_limit)
    recent_prompts = _parse_recent_thumbnail_prompts(youtube_assets_dir, limit=prompt_limit)

    title_counts = Counter()
    for title in recent_titles:
        for term in TITLE_FORMULA_TERMS:
            if term in title:
                title_counts[term] += 1

    scenario_counts = Counter(row["scenario"] for row in recent_prompts)
    overlay_counts = Counter()
    for row in recent_prompts:
        blob = f"{row['primary']} {row['secondary']}"
        for pattern in OVERLAY_FATIGUE_PATTERNS:
            if pattern in blob:
                overlay_counts[pattern] += 1

    family_counts = Counter(_family_for_scenario(row["scenario"]) for row in recent_prompts)

    # Include「驗收」in fatigue — it was exempt and became a daily monopoly hook.
    fatigued_title_terms = sorted(
        term for term, count in title_counts.items() if count >= 2
    )
    fatigued_scenarios = sorted(
        name for name, count in scenario_counts.items() if count >= 2
    )
    fatigued_overlay_patterns = sorted(
        pattern for pattern, count in overlay_counts.items() if count >= 2
    )
    fatigued_families = sorted(
        family for family, count in family_counts.items() if count >= 2
    )

    return {
        "recent_titles": recent_titles,
        "recent_prompts": recent_prompts,
        "title_counts": dict(title_counts),
        "scenario_counts": dict(scenario_counts),
        "overlay_counts": dict(overlay_counts),
        "family_counts": dict(family_counts),
        "fatigued_title_terms": fatigued_title_terms,
        "fatigued_scenarios": fatigued_scenarios,
        "fatigued_overlay_patterns": fatigued_overlay_patterns,
        "fatigued_families": fatigued_families,
    }


def is_title_term_fatigued(term, fatigue_state):
    return term in (fatigue_state or {}).get("fatigued_title_terms", [])


def is_scenario_fatigued(scenario_name, fatigue_state):
    return scenario_name in (fatigue_state or {}).get("fatigued_scenarios", [])


def is_family_fatigued(family, fatigue_state):
    return family in (fatigue_state or {}).get("fatigued_families", [])


def is_overlay_fatigued(text, fatigue_state):
    blob = str(text or "")
    for pattern in (fatigue_state or {}).get("fatigued_overlay_patterns", []):
        if pattern in blob:
            return True
    return False


def build_title_fatigue_report(channel_story="", youtube_assets_dir=YOUTUBE_ASSETS_DIR):
    youtube_assets_dir = youtube_assets_dir or YOUTUBE_ASSETS_DIR
    state = build_fatigue_state(channel_story=channel_story, youtube_assets_dir=youtube_assets_dir)
    lines = ["# 近期包裝疲勞檢查"]

    if state["recent_titles"]:
        lines.append(f"- 檢查最近 {len(state['recent_titles'])} 支影片標題。")
    else:
        lines.append("- 尚無足夠近期標題可檢查。")

    if state["recent_prompts"]:
        lines.append(f"- 檢查最近 {len(state['recent_prompts'])} 份縮圖 Prompt。")

    if state["fatigued_title_terms"]:
        lines.append(f"- 標題避免再用的詞：{', '.join(state['fatigued_title_terms'])}。")
    if state["fatigued_scenarios"]:
        lines.append(f"- 封面避免再用的 scenario：{', '.join(state['fatigued_scenarios'])}。")
    if state["fatigued_overlay_patterns"]:
        lines.append(f"- 封面大字避免再用：{', '.join(state['fatigued_overlay_patterns'])}。")
    if state["fatigued_families"]:
        lines.append(f"- 封面題型家族已連續出現：{', '.join(state['fatigued_families'])}；今日必須換另一種問法。")

    if not any(
        [
            state["fatigued_title_terms"],
            state["fatigued_scenarios"],
            state["fatigued_overlay_patterns"],
            state["fatigued_families"],
        ]
    ):
        lines.append("- 未偵測到明顯疲勞；仍需依今日主軸選不同題型，不要預設套換手公式。")

    lines.extend(
        [
            "- 若資料支持換手，請改問：為什麼逆勢、量價哪裡矛盾、明日失效條件是什麼。",
            "- 連續 3 天內不得重複「榜外吸籌 + 資金換手」A/B 配對。",
        ]
    )
    return "\n".join(lines)


def pick_non_fatigued(candidates, fatigue_state, key="scenario_name", family_key="family", limit=2):
    selected = []
    used_families = set()

    for candidate in candidates:
        scenario = candidate.get(key, "")
        family = candidate.get(family_key) or _family_for_scenario(scenario)
        if family in used_families:
            continue
        if is_scenario_fatigued(scenario, fatigue_state) and candidate.get("required"):
            continue
        if is_family_fatigued(family, fatigue_state) and not candidate.get("force"):
            continue
        overlay = f"{candidate.get('overlay_primary', '')} {candidate.get('overlay_secondary', '')}"
        if is_overlay_fatigued(overlay, fatigue_state) and candidate.get("has_variant"):
            variant = candidate.get("variant")
            if variant:
                candidate = {**candidate, **variant}
                scenario = candidate.get(key, scenario)
                family = candidate.get(family_key) or _family_for_scenario(scenario)
        if is_scenario_fatigued(scenario, fatigue_state):
            continue
        selected.append(candidate)
        used_families.add(family)
        if len(selected) >= limit:
            break

    if len(selected) < limit:
        for candidate in candidates:
            if candidate in selected:
                continue
            family = candidate.get(family_key) or _family_for_scenario(candidate.get(key, ""))
            if family in used_families:
                continue
            if candidate.get("has_variant"):
                variant = candidate.get("variant")
                if variant:
                    candidate = {**candidate, **variant}
            selected.append(candidate)
            used_families.add(family)
            if len(selected) >= limit:
                break

    return selected[:limit]


def title_contains_fatigued_terms(title, fatigue_state):
    return any(term in str(title or "") for term in (fatigue_state or {}).get("fatigued_title_terms", []))


def _soft_replace_fatigued_terms(text, fatigue_state):
    result = str(text or "")
    fatigued = set((fatigue_state or {}).get("fatigued_title_terms", []))
    for phrase, replacement in sorted(COMPOUND_REPLACEMENTS, key=lambda item: len(item[0]), reverse=True):
        if any(token in phrase for token in fatigued) and phrase in result:
            result = result.replace(phrase, replacement)
    terms = sorted(fatigued, key=len, reverse=True)
    replacements = dict(TERM_SOFT_REPLACEMENTS)
    # Bare「驗收」→「點名」avoids「上集點名上集」double-substitution noise.
    replacements.setdefault("驗收", "點名")
    for term in terms:
        replacement = replacements.get(term, "")
        if replacement and term in result:
            result = result.replace(term, replacement)
        elif term in result:
            result = result.replace(term, "")
    result = re.sub(r"(上集點名){2,}", "上集點名", result)
    result = re.sub(r"點名點名", "點名", result)
    return re.sub(r"\s{2,}", " ", result).strip()


def enforce_packaging_title_rules(title):
    """Apply 2026-07-07 CTR title rules without dropping the title entirely."""
    text = str(title or "").strip()
    if not text:
        return text

    text = re.sub(r"^【驗收】\s*", "", text)
    text = re.sub(r"^驗收上集點名：\s*", "", text)
    text = re.sub(r"^\d{1,2}/\d{1,2}\s+驗收上集點名：\s*", "", text)

    if text and not re.search(r"\d", text[:20]):
        moved = re.search(r"(\d+(?:\.\d+)?億|\d+萬張|\d+點)", text)
        if moved:
            number = moved.group(1)
            rest = text.replace(number, "", 1).strip(" ，、？?！!｜|")
            text = f"{number}！{rest}" if rest else number

    return text.strip()


def sanitize_title(title, fatigue_state):
    text = _soft_replace_fatigued_terms(title, fatigue_state)
    text = enforce_packaging_title_rules(text)
    return text or str(title or "")


def _title_signature(title):
    """Rough fingerprint for diversity — avoid picking two near-identical lines."""
    text = str(title or "")
    text = re.sub(r"\d{1,2}/\d{1,2}", "", text)
    text = re.sub(r"\d+(\.\d+)?億", "X億", text)
    text = re.sub(r"\s+", "", text)
    for token in ("台股", "盤後", "驗收", "外資", "AI帶你看股市"):
        text = text.replace(token, "")
    return text[:28]


def titles_too_similar(left, right):
    if not left or not right:
        return False
    sig_a = _title_signature(left)
    sig_b = _title_signature(right)
    if sig_a == sig_b:
        return True
    shorter, longer = (sig_a, sig_b) if len(sig_a) <= len(sig_b) else (sig_b, sig_a)
    return len(shorter) >= 10 and shorter in longer


def select_diverse_titles(candidates, num_titles=2, fatigue_state=None):
    """Pick varied titles; fatigue only soft-replaces, never drops all candidates."""
    fatigue_state = fatigue_state or {}
    pool = []
    seen = set()
    for raw in candidates or []:
        title = sanitize_title(raw, fatigue_state) if fatigue_state else str(raw or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        pool.append(title)

    if not pool:
        return []

    selected = [pool[0]]
    for candidate in pool[1:]:
        if any(titles_too_similar(candidate, picked) for picked in selected):
            continue
        selected.append(candidate)
        if len(selected) >= num_titles:
            break

    idx = 1
    while len(selected) < num_titles and idx < len(pool):
        if pool[idx] not in selected:
            selected.append(pool[idx])
        idx += 1

    return selected[:num_titles]


def _ensure_brand_suffix(title, short_date=""):
    text = str(title or "").strip()
    if "AI帶你看股市" in text:
        return text
    if short_date and short_date in text:
        return f"{text}｜AI帶你看股市"
    if short_date:
        return f"{text}｜{short_date} AI帶你看股市"
    return f"{text}{BRAND_SUFFIX}"


def guard_youtube_titles(titles, fatigue_state=None, local_candidates=None, short_date=""):
    """
    Post-process Gemini/fallback titles: soft-replace fatigued terms, keep two distinct angles.
    Falls back to local_candidates only when a line is empty or still unusable.
    """
    fatigue_state = fatigue_state or build_fatigue_state()
    locals_pool = list(local_candidates or [])
    guarded = []

    for idx, raw in enumerate(titles or []):
        line = sanitize_title(raw, fatigue_state)
        if not line or len(line) < 12:
            line = locals_pool.pop(0) if locals_pool else line
        if line:
            line = _ensure_brand_suffix(line, short_date=short_date)
            guarded.append(line)

    for extra in locals_pool:
        candidate = _ensure_brand_suffix(sanitize_title(extra, fatigue_state), short_date=short_date)
        if candidate and candidate not in guarded:
            guarded.append(candidate)

    return select_diverse_titles(guarded, num_titles=2, fatigue_state={})