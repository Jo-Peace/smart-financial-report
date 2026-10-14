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

# Title *opener* hooks: consecutive days with the same lead token cause browse fatigue.
# Measured as a trailing streak on the most recent titles (newest last in story log parse).
OPENER_HOOK_PATTERNS = [
    ("AMD", re.compile(r"^[\s🚀🚨📉📈]*AMD", re.I)),
    ("驗收開場", re.compile(r"^[\s🚀🚨]*【?驗收")),
    ("驗收上集", re.compile(r"^[\s🚀🚨]*驗收上集")),
    ("外資金額開場", re.compile(r"^[\s🚀🚨📉📈]*外資\s*[\d.]+億狂[掃砍]")),
]

# Soft limit: 2 consecutive same openers = warn; 3+ = block as fatigued opener.
OPENER_WARN_STREAK = 2
OPENER_BLOCK_STREAK = 3
OPENER_BLOCK_STREAK_BY_LABEL = {
    "外資金額開場": 2,  # 連兩支「外資Ｎ億狂掃／狂砍」就換鉤
}

SHADOW_TITLE_RE = re.compile(r"影子建倉|影子資金|隱形掃貨")
WHO_RECEIVES_RE = re.compile(r"誰在收[？?]")
UNPUBLISHED_RESULT_OPENERS = [
    re.compile(r"^[\s🚀🚨📉📈]*升息"),
    re.compile(r"^[\s🚀🚨📉📈]*降息"),
    re.compile(r"^[\s🚀🚨📉📈]*非農"),
    re.compile(r"^[\s🚀🚨📉📈]*聯準會.{0,12}(升息|降息|按兵|利率決議已)"),
]

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
            title = cells[7]
            if title in {"-", "待更新", ""}:
                continue
            titles.append(title)
    return titles[-limit:]


def _opener_hook_label(title: str) -> str | None:
    text = str(title or "").strip()
    if not text:
        return None
    for label, pattern in OPENER_HOOK_PATTERNS:
        if pattern.search(text):
            return label
    return None


def analyze_title_opener_streaks(titles: list[str], lookback: int = 8) -> dict:
    """
    titles: chronological oldest→newest (as returned by _parse_channel_story_titles).
    Returns trailing streak for known opener hooks on the newest titles.
    """
    window = list(titles or [])[-lookback:]
    newest_first = list(reversed(window))
    if not newest_first:
        return {
            "trailing_label": None,
            "trailing_streak": 0,
            "warn": False,
            "block": False,
            "recent_openers": [],
            "advice": "",
        }

    recent_openers = []
    for title in newest_first[:lookback]:
        recent_openers.append({"title": title, "opener": _opener_hook_label(title)})

    trailing_label = recent_openers[0]["opener"]
    streak = 0
    if trailing_label:
        for row in recent_openers:
            if row["opener"] == trailing_label:
                streak += 1
            else:
                break

    block_n = OPENER_BLOCK_STREAK_BY_LABEL.get(trailing_label, OPENER_BLOCK_STREAK)
    warn_n = max(1, block_n - 1)
    warn = bool(trailing_label and streak >= warn_n)
    block = bool(trailing_label and streak >= block_n)
    advice = ""
    if block:
        advice = (
            f"標題開場「{trailing_label}」已連續 {streak} 支（≥{OPENER_BLOCK_STREAK}）："
            "今日禁止再用同一開場；前 20 字改放已驗證金額＋股名＋矛盾。"
        )
    elif warn:
        advice = (
            f"標題開場「{trailing_label}」已連續 {streak} 支（≥{OPENER_WARN_STREAK}）："
            "建議今日換鉤；事件可放描述中後段，不要當第一鉤。"
        )
    return {
        "trailing_label": trailing_label,
        "trailing_streak": streak,
        "warn": warn,
        "block": block,
        "recent_openers": recent_openers,
        "advice": advice,
    }


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
    # Need a slightly longer title window for opener streaks.
    recent_titles = _parse_channel_story_titles(channel_story, limit=max(title_limit, 8))
    recent_prompts = _parse_recent_thumbnail_prompts(youtube_assets_dir, limit=prompt_limit)
    opener_streak = analyze_title_opener_streaks(recent_titles, lookback=8)

    title_counts = Counter()
    for title in recent_titles:
        for term in TITLE_FORMULA_TERMS:
            if term in title:
                title_counts[term] += 1
        # Count opener brand tokens even if mid-title (soft), but streak is authoritative.
        if "AMD" in title:
            title_counts["AMD"] += 1

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

    # Promote blocked opener token into fatigued terms for soft rewrite helpers.
    if opener_streak.get("block") and opener_streak.get("trailing_label") == "AMD":
        if "AMD" not in fatigued_title_terms:
            fatigued_title_terms = sorted(set(fatigued_title_terms) | {"AMD"})

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
        "opener_streak": opener_streak,
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

    opener = state.get("opener_streak") or {}
    if opener.get("advice"):
        lines.append(f"- 開場鉤子：{opener['advice']}")
    elif opener.get("trailing_label"):
        lines.append(
            f"- 開場鉤子：最近 1 支以「{opener['trailing_label']}」開場"
            f"（連續 {opener.get('trailing_streak', 1)}）；可延續但勿連打同一公式。"
        )

    if not any(
        [
            state["fatigued_title_terms"],
            state["fatigued_scenarios"],
            state["fatigued_overlay_patterns"],
            state["fatigued_families"],
            opener.get("warn"),
            opener.get("block"),
        ]
    ):
        lines.append("- 未偵測到明顯疲勞；仍需依今日主軸選不同題型，不要預設套換手公式。")

    lines.extend(
        [
            "- 若資料支持換手，請改問：為什麼逆勢、量價哪裡矛盾、明日失效條件是什麼。",
            "- 連續 3 天內不得重複「榜外吸籌 + 資金換手」A/B 配對。",
            "- 同一開場關鍵字（如 AMD）連續 ≥3 支：禁止再開場；事件可放描述中後段。",
            "- 「外資Ｎ億狂掃／狂砍」連兩支即換鉤；B 組不要連用「誰在收？」。",
            "- 禁止標題出現影子建倉／把未公布的升息、非農當結果開場。",
            "- 標題前 20 字優先：已驗證金額 + 核心股名 + 反直覺矛盾（非品牌事件名）。",
        ]
    )
    return "\n".join(lines)


def format_opener_streak_for_preflight(channel_story: str = "", log_path: str = "") -> list[str]:
    """Lines for /2 preflight console output."""
    text = channel_story
    if not text and log_path and os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            text = ""
    titles = _parse_channel_story_titles(text, limit=10)
    streak = analyze_title_opener_streaks(titles, lookback=8)
    lines = ["  ## 標題開場疲勞（連續鉤子）"]
    if not titles:
        lines.append("  - 尚無可用標題列可檢查")
        return lines
    label = streak.get("trailing_label") or "（無已知開場 token）"
    lines.append(f"  - 目前 trailing 開場：{label} × {streak.get('trailing_streak', 0)}")
    if streak.get("advice"):
        level = "BLOCK" if streak.get("block") else "WARN"
        lines.append(f"  - [{level}] {streak['advice']}")
    else:
        lines.append("  - 開場未達連續疲勞門檻；仍避免無必要的品牌事件連打。")
    # show last 4 openers
    for row in (streak.get("recent_openers") or [])[:4]:
        op = row.get("opener") or "其他"
        lines.append(f"    · {op}: {(row.get('title') or '')[:52]}")
    return lines


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


def enforce_opener_fatigue_on_title(title, fatigue_state=None):
    """
    When trailing opener is WARN/BLOCK, strip the repeated lead token so /2
    does not ship another AMD… / 驗收… cold-open.
    """
    text = str(title or "").strip()
    if not text:
        return text
    opener = (fatigue_state or {}).get("opener_streak") or {}
    if not (opener.get("warn") or opener.get("block")):
        return text
    label = opener.get("trailing_label")
    if label == "AMD":
        # Drop "AMD…！" / "AMD砸3200億利多！" style leads in one pass.
        text = re.sub(
            r"^[\s🚀🚨📉📈🔥]*AMD[^｜\n]{0,40}?(?:[！!？?]|\s)+",
            "",
            text,
            count=1,
            flags=re.I,
        )
        text = re.sub(r"^[\s🚀🚨📉📈🔥]*AMD\s*", "", text, count=1, flags=re.I)
    elif label in {"驗收開場", "驗收上集"}:
        text = re.sub(r"^[\s🚀🚨]*【?驗收】?\s*", "", text)
        text = re.sub(r"^驗收上集點名：\s*", "", text)
        text = re.sub(r"^驗收上集\s*", "", text)
    return text.strip(" ！!？?，,|｜")


def sanitize_title(title, fatigue_state):
    # Opener strip first (whole lead token+phrase), then soft term replace, then CTR rules.
    text = enforce_opener_fatigue_on_title(title, fatigue_state)
    text = _soft_replace_fatigued_terms(text, fatigue_state)
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


def _still_uses_blocked_opener(title, fatigue_state=None):
    """True if title still cold-opens with a blocked or warned-dead opener."""
    opener = (fatigue_state or {}).get("opener_streak") or {}
    label = opener.get("trailing_label")
    if not label:
        return False
    if _opener_hook_label(title) != label:
        return False
    if opener.get("block"):
        return True
    # 外資Ｎ億狂掃／狂砍：連一支都不要再接同一開場。
    if opener.get("warn") and label == "外資金額開場":
        return True
    return False


def _recent_used_who_receives(fatigue_state=None) -> bool:
    titles = (fatigue_state or {}).get("recent_titles") or []
    return any(WHO_RECEIVES_RE.search(str(title or "")) for title in titles[-4:])


def _unpublished_result_kinds(date_str="") -> set[str]:
    if not date_str:
        return set()
    try:
        from modules.market_calendar import nearby_market_events
    except Exception:
        return set()
    kinds = set()
    for row in nearby_market_events(date_str):
        if row.get("when") in {"今天", "尚未發生"} and row.get("kind") in {
            "nfp",
            "fomc_us",
            "fomc_tw",
        }:
            kinds.add(row["kind"])
    return kinds


def title_is_unusable(title, fatigue_state=None, date_str="") -> bool:
    """Hard rejects that local templates should replace."""
    text = str(title or "").strip()
    if not text:
        return True
    if SHADOW_TITLE_RE.search(text):
        return True
    if _recent_used_who_receives(fatigue_state) and WHO_RECEIVES_RE.search(text):
        return True
    unpublished = _unpublished_result_kinds(date_str)
    if unpublished and any(pattern.search(text) for pattern in UNPUBLISHED_RESULT_OPENERS):
        return True
    return False


def guard_youtube_titles(titles, fatigue_state=None, local_candidates=None, short_date="", date_str=""):
    """
    Post-process Gemini/fallback titles: soft-replace fatigued terms, keep two distinct angles.

    When opener streak is BLOCK: prefer sanitized local high-sample templates first.
    Gemini titles often re-introduce the same cold-open formula even after soft strip;
    local templates are amount+stock+contradiction by construction.

    Otherwise: Gemini first; fall back to local_candidates only when a line is empty/unusable.
    """
    fatigue_state = fatigue_state or build_fatigue_state()
    locals_pool = list(local_candidates or [])
    opener = fatigue_state.get("opener_streak") or {}
    prefer_local = bool(opener.get("block"))

    def _prep(raw):
        line = sanitize_title(raw, fatigue_state)
        if not line or len(line) < 12:
            return ""
        if _still_uses_blocked_opener(line, fatigue_state):
            return ""
        if title_is_unusable(line, fatigue_state=fatigue_state, date_str=date_str):
            return ""
        return _ensure_brand_suffix(line, short_date=short_date)

    guarded = []
    seen = set()

    def _push(raw):
        line = _prep(raw)
        if not line or line in seen:
            return False
        seen.add(line)
        guarded.append(line)
        return True

    if prefer_local:
        # BLOCK day: local high-sample templates first, Gemini fills gaps only.
        for raw in locals_pool:
            _push(raw)
            if len(guarded) >= 4:
                break
        for raw in titles or []:
            if len(guarded) >= 4:
                break
            _push(raw)
    else:
        for raw in titles or []:
            if _push(raw):
                continue
            # empty / still-blocked after sanitize → try one local
            while locals_pool:
                if _push(locals_pool.pop(0)):
                    break
        for extra in locals_pool:
            _push(extra)

    return select_diverse_titles(guarded, num_titles=2, fatigue_state={})