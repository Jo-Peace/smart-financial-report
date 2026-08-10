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


def _build_mainline(structured_data: dict) -> str:
    """One-sentence daily spine for the hosts."""
    frame = _editorial_label(structured_data) or "盤後主線"
    index_action = str(structured_data.get("index_action") or "").strip()
    buy = _stock_short(_first_str(structured_data.get("safe_havens_bought")))
    sell = _stock_short(_first_str(structured_data.get("heavyweights_dumped")))
    div = _first_str(structured_data.get("price_volume_divergence"))
    target = _first_str(structured_data.get("prediction_targets") or structured_data.get("ai_data_picks"))

    bits = [f"框架「{frame}」"]
    if index_action:
        bits.append(f"大盤{index_action}")
    if buy and sell:
        bits.append(f"對照 {buy} 買超 vs {sell} 賣超")
    elif buy:
        bits.append(f"買超焦點 {buy}")
    elif sell:
        bits.append(f"賣超壓力 {sell}")
    if div:
        short_div = div if len(div) <= 36 else div[:33] + "…"
        bits.append(f"矛盾點：{short_div}")
    if target:
        bits.append(f"收尾盯 {_stock_short(target)}")
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

    # Prefer quantitative focus_ranking (title_priority) over raw buy-list order
    ranking = structured_data.get("focus_ranking") or []
    if not ranking:
        try:
            from modules.role_scorer import rank_focus_stocks

            ranking = rank_focus_stocks(structured_data, limit=3)
        except Exception:
            ranking = []
    if ranking:
        lines.append("主角排序（依 title_priority，優先講這些）：")
        for i, row in enumerate(ranking[:3], start=1):
            lines.append(
                f"  {i}. {row.get('ticker', '')} {row.get('name', '')} "
                f"— {row.get('role_label') or row.get('base_role')}；"
                f"P{row.get('title_priority')}/E{row.get('elasticity')}/F{row.get('foreign_buy_strength')}/"
                f"V{row.get('volume_confirmation')}；證據：{row.get('source_text', '')}"
            )
            if row.get("base_role") == "direction_indicator":
                lines.append("     （方向股：當對照，不要講成最大彈性飆股）")
    else:
        buy = _first_str(structured_data.get("safe_havens_bought"))
        sell = _first_str(structured_data.get("heavyweights_dumped"))
        if buy:
            lines.append(f"買超證據：{buy}")
        if sell:
            lines.append(f"賣超證據：{sell}")
        for div in _as_list(structured_data.get("price_volume_divergence"))[:2]:
            lines.append(f"矛盾：{div}")

    targets = _as_list(structured_data.get("prediction_targets"))[:1]
    for t in targets:
        if isinstance(t, dict):
            stop = t.get("stop_loss_desc") or t.get("stop_loss_price") or ""
            lines.append(
                f"明日條件：{t.get('id', '')} {t.get('name', '')}（{t.get('direction', '觀察')}）；失效：{stop}"
            )
        else:
            lines.append(f"明日條件：{t}")
    return lines or ["依資料源挑 2～3 個最有數字的證據即可"]


def _must_not_lines(structured_data: dict, placement: str) -> list[str]:
    lines = [
        "不要唸完整法人榜或完整族群清單",
        "不要說「資金輪動」空話；要講誰買誰賣與金額",
        "不要把短線標的講成長線必漲",
        "不要捏造資料源沒有的數字",
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
    mainline = _build_mainline(structured_data)
    div = _first_str(structured_data.get("price_volume_divergence"))
    buy = _first_str(structured_data.get("safe_havens_bought"))
    sell = _first_str(structured_data.get("heavyweights_dumped"))
    index_action = str(structured_data.get("index_action") or "").strip()

    if div:
        hook_line = div
    elif buy and sell:
        hook_line = f"買超 {buy} 對上 賣超 {sell}"
    elif buy:
        hook_line = f"{index_action or '今日盤面'}，外資卻先鎖 {buy}"
    else:
        hook_line = mainline

    if placement == "lead":
        return f"""## 開場鉤子（lead：可先驗收，但仍要聳動）

- 0～8 秒：先丟一個可驗證數字（建議：{_first_str(structured_data.get("prev_day_picks_result") or structured_data.get("validation_results")) or hook_line}）
- 8～20 秒：接「上集說了什麼 → 今天數字怎麼走」
- 20～40 秒：轉入今日主線：{mainline}
- 禁止空泛恐嚇；每個聳動句子後面都要有資料源數字
"""

    mid_note = (
        "中段才用 1 句帶過昨日結果"
        if placement == "brief"
        else "昨日結果最多一句，不要獨立段"
    )
    return f"""## 開場鉤子（{placement}：前 30 秒先矛盾，不要先驗收）

- 0～8 秒：最大矛盾（股名 + 金額/漲跌 + 反差）。建議素材：{hook_line}
- 8～20 秒：阿明用一句散戶問句接住（例如「這是續航還是假突破？」「為什麼量這麼冷？」）
- 20～40 秒：博士定調今日主線：{mainline}
- {mid_note}
- 禁止用「先跟大家驗收上集」當開場儀式
- 禁止沒有數字的「崩盤／逃命／毀屍」空話；恐懼必須接失效條件或明日觀察
"""


def _program_order_block(placement: str) -> str:
    if placement == "lead":
        return """## 節目順序（驗收 lead）

1. 開場鉤子：可驗證數字 → 昨日預測結果 — 約 20%
2. 今日一句主線 — 約 15%
3. 2～3 個證據股（法人/量價）— 約 40%
4. 明日條件與失效 — 約 20%
5. 收尾：不叫買賣 — 約 5%
"""
    if placement == "omit":
        return """## 節目順序（略過驗收主鉤子）

1. 開場鉤子：今日最大矛盾 + 問句 — 約 20%
2. 2～3 個證據股 — 約 50%
3. 明日條件與失效 — 約 25%
4. 收尾 — 約 5%
（資料源若有昨日結果，最多一句，不開獨立段）
"""
    return """## 節目順序（驗收 brief，預設）

1. 開場鉤子：今日最大矛盾 + 問句 — 約 20%
2. 中段 1～2 句帶過昨日預測結果 — 約 10%
3. 2～3 個證據股（法人/量價）— 約 45%
4. 明日條件與失效 — 約 20%
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
    skill_section = _skill_block(skill_context)
    order_block = _program_order_block(placement)
    opening_block = _opening_hook_block(structured_data, placement)
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

清楚、可信、可追蹤；聽完要記得：今天主線是什麼、誰買誰賣、明天盯什麼、什麼條件失效。
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
