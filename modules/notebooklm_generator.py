"""
NotebookLM *director* prompts (how to speak).

Daily/weekend prompts are local templates. They intentionally do NOT re-dump full
leaderboards (those live in notebooklm_source). Prompt owns: hosts, mainline,
must-cover / must-not, segment order, TTS hygiene.
"""

from __future__ import annotations

import datetime
import os
import re


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [value]


def _first_str(items, fallback=""):
    for item in _as_list(items):
        if isinstance(item, dict):
            label = " ".join(
                str(item.get(k) or "").strip()
                for k in ("id", "ticker", "name", "theme")
                if item.get(k)
            ).strip()
            if label:
                return label
            continue
        text = str(item).strip()
        if text:
            return text
    return fallback


def _stock_short(text: str) -> str:
    text = str(text or "").strip()
    # "聯電 (+37.6億)" -> "聯電"
    text = re.sub(r"\s*[\(（].*?[\)）]\s*", "", text).strip()
    return text or "焦點股"


def _prompt_mode() -> str:
    raw = os.getenv("NOTEBOOKLM_PROMPT_MODE", "local").strip().lower()
    if raw in {"off", "0", "false", "no", "skip", "none"}:
        return "off"
    return "local"


def _editorial_label(structured_data: dict) -> str:
    frame = structured_data.get("editorial_frame") or {}
    if isinstance(frame, dict):
        return str(frame.get("label") or frame.get("frame") or "").strip()
    return ""


def _validation_placement(structured_data: dict) -> str:
    frame = structured_data.get("editorial_frame") or {}
    if isinstance(frame, dict) and frame.get("validation_placement"):
        return str(frame.get("validation_placement")).strip().lower()
    return "brief"


def _skill_block(skill_context: str, max_chars: int = 500) -> str:
    text = (skill_context or "").strip()
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return f"\n## 本地技能提示（語氣/重點；不得改寫數字）\n\n{text}\n"


def _broadcast(structured_data: dict) -> dict:
    from modules.broadcast_facts import attach_broadcast_facts, ensure_broadcast_facts

    structured_data = attach_broadcast_facts(structured_data or {})
    return ensure_broadcast_facts(structured_data)


def _build_mainline(structured_data: dict) -> str:
    """One-sentence daily spine for the hosts — full numbers, no truncation."""
    facts = _broadcast(structured_data)
    opening = facts.get("opening") or {}
    frame = _editorial_label(structured_data) or "盤後主線"
    bits = [f"框架「{frame}」"]
    index_action = opening.get("index_action") or str(structured_data.get("index_action") or "").strip()
    if index_action:
        bits.append(f"大盤{index_action}")
    hook = (opening.get("hook_line") or "").strip()
    if hook:
        bits.append(hook)
    official = facts.get("official_watch") or []
    if official:
        first = official[0]
        bits.append(f"收尾盯 {first.get('ticker', '')} {first.get('name', '')}".strip())
    return "；".join(bits)


def _must_cover_lines(structured_data: dict, placement: str) -> list[str]:
    lines = []
    if placement == "lead":
        val = _first_str(structured_data.get("prev_day_picks_result") or structured_data.get("validation_results"))
        if val:
            lines.append(f"開場驗收：{val}")
    elif placement == "brief":
        val = _first_str(structured_data.get("prev_day_picks_result") or structured_data.get("validation_results"))
        if val:
            lines.append(f"中段一句驗收即可：{val}")

    facts = _broadcast(structured_data)
    heroes = facts.get("heroes") or []
    if heroes:
        lines.append("主角卡（只唸「可唸事實」與失效；不要唸內部分數）：")
        for i, card in enumerate(heroes[:3], start=1):
            level = {
                "official": "正式追蹤",
                "talk_only": "僅可口述",
                "context": "對照",
            }.get(card.get("admission"), "觀察")
            caution = "；單日矛盾，不成趨勢結論" if card.get("caution") else ""
            stop = f"；失效：{card['invalidation']}" if card.get("invalidation") else ""
            lines.append(
                f"  {i}. [{level}] {card.get('say_line') or (card.get('ticker', '') + ' ' + card.get('name', ''))}{stop}{caution}"
            )
    else:
        buy = _first_str(structured_data.get("safe_havens_bought"))
        sell = _first_str(structured_data.get("heavyweights_dumped"))
        if buy:
            lines.append(f"買超證據：{buy}")
        if sell:
            lines.append(f"賣超證據：{sell}")

    official = facts.get("official_watch") or []
    from modules.session_calendar import last_output_from_structured, session_lexicon

    lex = session_lexicon(
        structured_data.get("date"),
        last_output_date=last_output_from_structured(structured_data),
    )
    if official:
        first = official[0]
        stop = first.get("invalidation") or ""
        next_main = lex.get("next_main") or "明日主盯"
        lines.append(
            f"{next_main}：{first.get('ticker', '')} {first.get('name', '')}；失效：{stop or '見資料源主角卡'}"
        )
    else:
        next_main = lex.get("next_main") or "明日主盯"
        lines.append(f"{next_main}：今日沒有過門檻的正式追蹤檔；只講矛盾與確認條件")
    from modules.market_calendar import calendar_one_liner

    cal = calendar_one_liner(structured_data.get("date"))
    if cal:
        lines.append(f"日曆一句即可：{cal}。不要當開場主鉤子，除非資料源開場矛盾就是它。")
    return lines or ["依資料源挑 2～3 個最有數字的證據即可"]


def _must_not_lines(structured_data: dict, placement: str) -> list[str]:
    lines = [
        "不要唸完整法人榜或完整族群清單",
        "不要說「資金輪動」空話；要講誰買誰賣與金額",
        "不要說鎖籌碼／靜默入侵／影子建倉；量沒進前20只准講「成交量未進前20」",
        "土洋對作最多一句備查，不要升級成主線陰謀",
        "不要把短線標的講成長線必漲",
        "不要捏造資料源沒有的數字",
        "不要唸 title_priority / P100 / 彈性分數等內部量化分",
        "不要把未公布的 FOMC／非農講成結果；四巫日不是台指期結算",
        "上一集若不是上一交易日，禁止說昨天點名／昨天那集／昨天的影片；改說上集",
        "標成「僅可口述」的檔，不可講成下一交易日正式追蹤",
        "極端報酬（例如單週 +40% 以上）若資料源已標註異常，只可當背景不可當主線",
    ]
    if placement != "lead":
        lines.append("不要用長段「先驗收再分析」當開場儀式")
    if placement == "omit":
        lines.append("標題式口吻不要出現「驗收日／驗收現場」")
    # If long-term looks extreme, call it out.
    for pick in _as_list(structured_data.get("long_term_picks"))[:2]:
        blob = str(pick)
        if re.search(r"\+?\d{2,}(?:\.\d+)?%", blob):
            m = re.search(r"([+-]?\d{2,}(?:\.\d+)?)%", blob)
            if m and abs(float(m.group(1))) >= 40:
                lines.append("長線背景若含極端漲幅，禁止當「必漲主線」朗讀")
                break
    return lines


def _opening_hook_block(structured_data: dict, placement: str) -> str:
    """Director notes for a data-backed sensational cold open."""
    from modules.session_calendar import last_output_from_structured, session_lexicon

    facts = _broadcast(structured_data)
    opening = facts.get("opening") or {}
    mainline = _build_mainline(structured_data)
    hook_line = (opening.get("hook_line") or "").strip() or mainline
    lex = session_lexicon(
        structured_data.get("date"),
        last_output_date=last_output_from_structured(structured_data),
    )
    prev_result = lex.get("prev_result") or "昨日結果"
    next_observe = lex.get("next_observe") or "明日觀察"

    if placement == "lead":
        return f"""## 開場鉤子（lead：可先驗收，但仍要聳動）

- 0～8 秒：先丟一個可驗證數字（建議：{_first_str(structured_data.get("prev_day_picks_result") or structured_data.get("validation_results")) or hook_line}）
- 8～20 秒：接「上集說了什麼 → 今天數字怎麼走」
- 20～40 秒：轉入今日主線：{mainline}
- 禁止空泛恐嚇；每個聳動句子後面都要有資料源數字
"""

    mid_note = (
        f"中段才用 1 句帶過{prev_result}"
        if placement == "brief"
        else f"{prev_result}最多一句，不要獨立段"
    )
    return f"""## 開場鉤子（{placement}：前 30 秒先矛盾，不要先驗收）

- 0～8 秒：最大矛盾（股名 + 金額/漲跌 + 反差）。建議素材：{hook_line}
- 8～20 秒：阿明用一句散戶問句接住（例如「這是續航還是假突破？」「為什麼量這麼冷？」）
- 20～40 秒：博士定調今日主線：{mainline}
- {mid_note}
- 禁止用「先跟大家驗收上集」當開場儀式
- 禁止沒有數字的「崩盤／逃命／毀屍」空話；恐懼必須接失效條件或{next_observe}
"""


def _program_order_block(placement: str, date_str: str | None = None, last_output_date=None) -> str:
    from modules.session_calendar import session_lexicon

    lex = session_lexicon(date_str, last_output_date=last_output_date)
    next_section = lex.get("next_section") or "明日條件"
    prev_result = lex.get("prev_result") or "昨日結果"
    if placement == "lead":
        return f"""## 節目順序（驗收 lead）

1. 開場鉤子：可驗證數字 → {prev_result} — 約 20%
2. 今日一句主線 — 約 15%
3. 2～3 個證據股（法人/量價）— 約 40%
4. {next_section}與失效 — 約 20%
5. 收尾：不叫買賣 — 約 5%
"""
    if placement == "omit":
        return f"""## 節目順序（略過驗收主鉤子）

1. 開場鉤子：今日最大矛盾 + 問句 — 約 20%
2. 2～3 個證據股 — 約 50%
3. {next_section}與失效 — 約 25%
4. 收尾 — 約 5%
（資料源若有{prev_result}，最多一句，不開獨立段）
"""
    return f"""## 節目順序（驗收 brief，預設）

1. 開場鉤子：今日最大矛盾 + 問句 — 約 20%
2. 中段 1～2 句帶過{prev_result} — 約 10%
3. 2～3 個證據股（法人/量價）— 約 45%
4. {next_section}與失效 — 約 20%
5. 收尾 — 約 5%
"""


def _short_date_label(date_str: str) -> str:
    try:
        dt = datetime.datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
        return f"{dt.month}/{dt.day} 台股盤後"
    except ValueError:
        return f"{date_str} 台股盤後"


def _cover_alignment_block(
    date_str: str,
    *,
    visual_theme: str = "",
    title_a: str = "",
    title_b: str = "",
) -> str:
    """
    NotebookLM Video Overview first-slide alignment.
    Role names match channel pack: 張博士 / 阿明（封面可用博士與散戶角色感）。
    """
    if not any([visual_theme, title_a, title_b]):
        return ""
    main = title_a or "（請使用當日 YouTube 標題 A）"
    sub = title_b or "（可空；勿自行發明聳動副標）"
    theme = visual_theme or "（沿用當日 /2 視覺主題；未指定則深色高對比財經風）"
    return f"""
## 封面投影片對齊（Video Overview 第一張）

請將影片第一張投影片設計成封面頁，不要直接進入內容。
- 封面主標題請使用：「{main}」
- 封面副標題請使用：「{sub}」
- 左上角小字日期請使用：「{_short_date_label(date_str)}」
- 封面視覺主題請與 YouTube 封面一致：{theme}
- 請保留「張博士 + 阿明/散戶」角色感，高對比深色背景、白/黃大字、紅綠 K 線元素。
- 請不要自行改寫主標題與副標題。
（NotebookLM 未必精準還原封面；以資料正確為先，視覺對齊為輔。）
"""


def build_compact_notebooklm_prompt(
    structured_data,
    date_str=None,
    current_theme="",
    skill_context="",
    visual_theme="",
    title_a="",
    title_b="",
):
    """Director card for NotebookLM hosts — not a second copy of the source table."""
    structured_data = structured_data or {}
    if not date_str:
        date_str = structured_data.get("date") or datetime.datetime.now().strftime("%Y-%m-%d")

    placement = _validation_placement(structured_data)
    frame = _editorial_label(structured_data) or "依資料源"
    mainline = _build_mainline(structured_data)
    must_cover = "\n".join(f"- {x}" for x in _must_cover_lines(structured_data, placement))
    must_not = "\n".join(f"- {x}" for x in _must_not_lines(structured_data, placement))
    theme = visual_theme or current_theme or ""
    theme_line = f"- 外部視覺/主題（若有）：{theme}" if theme else "- 外部主題：無"
    skill_section = (
        _skill_block(skill_context)
        if os.getenv("NOTEBOOKLM_SKILL_HINTS", "").lower() in {"1", "true", "yes"}
        else ""
    )
    from modules.session_calendar import (
        last_output_from_structured,
        session_lexicon,
        speech_rules_block,
    )

    last_output = last_output_from_structured(structured_data)
    order_block = _program_order_block(placement, date_str, last_output_date=last_output)
    opening_block = _opening_hook_block(structured_data, placement)
    lex = session_lexicon(date_str, last_output_date=last_output)
    session_block = speech_rules_block(date_str, last_output_date=last_output)
    next_watch = lex.get("next_watch") or "明天先看"
    cover_block = _cover_alignment_block(
        date_str,
        visual_theme=theme,
        title_a=title_a,
        title_b=title_b,
    )

    return f"""# NotebookLM 主持人導演卡：{date_str} 台股盤後

請**只根據已上傳的「NotebookLM 乾淨資料源」**生成繁體中文音頻/影片摘要。
本檔是導演卡（怎麼講），不是第二份數據表；**所有數字必須來自資料源**，此卡若與資料源衝突，以資料源為準。

## 主持人

- A：數據張博士 — 冷靜、只講有數字的證據。
- B：阿明 — 代表散戶追問，反應自然，不要固定驚嘆句。
- 不要加第三位主持人或長篇人設。
{cover_block}
## 今日唯一主線（請整集繞它轉）

{mainline}

- 編輯框架：{frame}
- 驗收使用：{placement}（lead=可開場；brief=中段一句；omit=不要當主鉤子）
{theme_line}

## 必講

{must_cover}

## 禁止

{must_not}

{session_block}
{opening_block}
{order_block}
## 口播注意

- 股票代號可用中文讀法輔助（如 2303 → 二三零三），但不要發明公司故事。
- 大金額講「X 點 Y 億」，不要快速掃完整榜單。
- 英文新聞標題只講重點（誰、做了什麼），不要整句朗讀。
- 優先講資料源「主角股卡片」裡的 2～3 檔，不要平均分配全榜。
- 前 30 秒必須先兌現標題承諾的矛盾，再展開分析。
- 不要寫成逐字劇本；給可臨場發揮的段落重點即可。
{skill_section}
## 輸出期望

清楚、可信、可追蹤；聽完要記得：今天主線是什麼、誰買誰賣、{next_watch}什麼、什麼條件失效。
開場要聳動但有數字；中後段要可驗收、可追蹤。
"""


def build_weekend_notebooklm_prompt(structured_data, date_str=None, skill_context=""):
    """Weekend director card (local, no Gemini)."""
    structured_data = structured_data or {}
    if not date_str:
        date_str = structured_data.get("date") or datetime.datetime.now().strftime("%Y-%m-%d")

    us_hook = structured_data.get("us_market_hook") or structured_data.get("index_action") or "見資料源"
    geo = structured_data.get("geopolitical_impact") or "見資料源"
    defense = structured_data.get("taiwan_monday_defense") or structured_data.get("conservative_strategy") or "見資料源"
    vulture = structured_data.get("taiwan_monday_vulture") or structured_data.get("aggressive_strategy") or "見資料源"
    skill_section = _skill_block(skill_context)

    return f"""# 週末特別版導演卡：{date_str}

請根據已上傳的週末資料源，生成繁體中文週末覆盤摘要。本檔是導演卡；數字以資料源為準。

## 主持人

- A：數據張博士
- B：阿明

## 本週唯一主線

- 美股/盤面 hook：{us_hook}
- 宏觀/地緣：{geo}
- 週一防守：{defense}
- 週一積極：{vulture}

## 節目順序

1. 本週最大故事 1 條（不要逐日流水帳）
2. 法人 vs 成交量的一致或矛盾（2～3 檔）
3. 下週只盯 1 個變數 + 失效條件
4. 穩健 / 波段各一句；不叫買賣

## 禁止

- 不要「資金輪動」空話
- 不要平均分配每天新聞
- 不要捏造數字
{skill_section}
"""


def generate_notebooklm_prompt(
    api_key,
    structured_data,
    date_str=None,
    current_theme="",
    skill_context="",
    visual_theme="",
    title_a="",
    title_b="",
):
    """Daily NotebookLM prompt. ``api_key`` unused in local mode (compat)."""
    if _prompt_mode() == "off":
        return None
    return build_compact_notebooklm_prompt(
        structured_data,
        date_str=date_str,
        current_theme=current_theme,
        skill_context=skill_context,
        visual_theme=visual_theme,
        title_a=title_a,
        title_b=title_b,
    )


def generate_weekend_special_prompt(api_key, structured_data, date_str=None, skill_context=""):
    """Weekend NotebookLM prompt. ``api_key`` unused in local mode (compat)."""
    if _prompt_mode() == "off":
        return None
    return build_weekend_notebooklm_prompt(
        structured_data,
        date_str=date_str,
        skill_context=skill_context,
    )
