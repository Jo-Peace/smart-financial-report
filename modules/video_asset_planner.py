"""
Deterministic YouTube production-plan helpers.

This module keeps the /2 packaging layer fully local and rule-based.
It intentionally avoids any AI calls.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _as_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _short(value: Any, limit: int = 34) -> str:
    value = _clean_text(value)
    return value if len(value) <= limit else value[:limit].rstrip() + "..."


def _stock_label(text: Any) -> str:
    text = _clean_text(text)
    if not text:
        return ""
    text = re.sub(r"^[0-9A-Za-z.]+\s*", "", text)
    text = re.split(r"[（(]", text)[0]
    text = re.split(r"[:：,，|｜]", text)[0]
    return text.strip()


def _compact_label(value: Any, limit: int = 8) -> str:
    value = _stock_label(value)
    value = re.sub(r"[^\w\u4e00-\u9fff]", "", value)
    return value if len(value) <= limit else value[:limit]


def _pick_titles(titles: Any) -> Dict[str, str]:
    titles = [t for t in _as_list(titles) if t]
    if not titles:
        return {
            "a": "今日台股主力買賣邏輯曝光｜AI帶你看股市",
            "b": "外資賣誰、偷偷買誰？｜AI帶你看股市",
        }
    if len(titles) >= 2:
        return {"a": titles[0], "b": titles[1]}

    data_title = max(titles, key=lambda t: sum(ch.isdigit() for ch in t))
    curiosity = next((t for t in titles if "?" in t or "？" in t), None)
    if not curiosity:
        curiosity = titles[0]
    if curiosity == data_title and len(titles) > 1:
        curiosity = titles[1]
    return {"a": data_title, "b": curiosity}


def _cover_copy(structured_data: Dict[str, Any]) -> Dict[str, List[str]]:
    dumped = _compact_label((_as_list(structured_data.get("heavyweights_dumped")) or [""])[0])
    picks = [_compact_label(x) for x in _as_list(structured_data.get("ai_data_picks"))]
    picks = [p for p in picks if p and p != "今日無安全推薦標的"]

    if dumped and picks:
        a_line1 = f"{dumped}被提款"
        a_line2 = f"{picks[0]}反攻！"
        b_line1 = f"外資賣{dumped}"
        b_line2 = "偷偷買誰？"
    elif picks:
        a_line1 = f"{picks[0]}爆量"
        a_line2 = "主力進場？"
        b_line1 = "誰被偷偷買？"
        b_line2 = f"{picks[0]}現蹤"
    else:
        a_line1 = "主力方向曝光"
        a_line2 = "明天驗收！"
        b_line1 = "外資賣誰？"
        b_line2 = "主力買誰？"

    if picks:
        pick_pair = "＋".join(picks[:2])
    else:
        pick_pair = "主力買超"

    if dumped and picks:
        a_badges = ["外資大賣", pick_pair]
    elif picks:
        a_badges = ["爆量異動", pick_pair]
    else:
        a_badges = ["三大法人", "成交量異常"]

    return {
        "a_title_lines": [a_line1, a_line2],
        "a_badges": a_badges,
        "b_title_lines": [b_line1, b_line2],
        "b_badges": [pick_pair, "主力買賣邏輯"],
    }


def _latest_price_for(structured_data: Dict[str, Any], ticker: str, fallback: Optional[float] = None) -> Optional[float]:
    trend = (structured_data.get("recent_trends") or {}).get(str(ticker)) or {}
    latest_price = trend.get("latest_price")
    if isinstance(latest_price, (int, float)):
        return float(latest_price)
    if isinstance(fallback, (int, float)):
        return float(fallback)
    return None


def _simulate_100w_lines(structured_data: Dict[str, Any], youtube_package: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    targets = _as_list(structured_data.get("prediction_targets"))
    target_rows = structured_data.get("prediction_targets") or []
    if not isinstance(target_rows, list):
        return lines

    for target in target_rows[:2]:
        if not isinstance(target, dict):
            continue
        ticker = str(target.get("id", "")).strip()
        name = target.get("name") or ticker
        direction = target.get("direction") or "多"
        current_price = _latest_price_for(structured_data, ticker, target.get("price"))
        stop_price = target.get("stop_loss_price")
        if not current_price or not isinstance(stop_price, (int, float)):
            continue
        shares = 1_000_000 / current_price
        stop_value = shares * float(stop_price)
        stop_pnl = stop_value - 1_000_000
        stop_pct = (float(stop_price) / current_price - 1) * 100
        from modules.youtube_content_generator import past_trend_text

        # Past N-session moves, labelled as history (never a 驗證窗 result in 元).
        trend_text = past_trend_text((structured_data.get("recent_trends") or {}).get(ticker))
        lines.append(
            f"{ticker} {name}（{direction}）：100萬約買 {shares:.1f} 股；"
            f"若以停損價 {stop_price:.1f} 元出場，帳面約剩 {stop_value:,.0f} 元，"
            f"損益約 {stop_pnl:+,.0f} 元（{stop_pct:+.2f}%）{trend_text}"
        )
    return lines


def _date_label(date_str: str) -> str:
    try:
        year, month, day = date_str.split("-")
        return f"{int(month)}/{int(day)}"
    except Exception:
        return date_str


def _display_theme(visual_theme: str) -> str:
    visual_theme = _clean_text(visual_theme)
    if _is_michael_jackson_theme(visual_theme):
        return "麥克傑克森舞台風格（80s pop dance stage / 白手套 / 聚光燈 / 月球漫步感；避免真人肖像）"
    if _is_ninja_turtle_theme(visual_theme):
        return "忍者龜靈感的原創下水道武術英雄漫畫風（綠色黏液光、城市地下基地、眼罩色彩元素；避免直接複製 IP 角色）"
    if _is_jojo_fifa_theme(visual_theme):
        return "JOJO x FIFA 混搭風（誇張漫畫定格姿勢 + 足球賽事戰術場；避免官方角色與商標）"
    return visual_theme if visual_theme else "待設定（可在 /2 指定，無需重跑 Gemini）"


def _theme_phrase(visual_theme: str) -> str:
    visual_theme = _clean_text(visual_theme).rstrip(".。")
    if visual_theme:
        return f"Daily visual theme: {visual_theme}."
    return "Daily visual theme: AI Taiwan stock market analysis, data-driven market battle."


def _theme_scene_notes(visual_theme: str) -> str:
    if _is_ninja_turtle_theme(visual_theme):
        return """Scene notes for Ninja Turtle–inspired style:
- Use a sewer / underground base atmosphere with neon green accents.
- Keep characters original and not visually identical to any copyrighted franchise.
- Add skateboard rails, pipe tunnels, comic impact lines, and pizza-box shaped chart props in the background.
- The image must feel like a sewer martial-arts hero team finance parody while remaining original and not copying actual TMNT characters.
"""
    if _is_michael_jackson_theme(visual_theme):
        return """Scene notes for Michael Jackson–inspired stage style:
- Use a concert stage, spotlight beams, glossy floor reflections, and dance-pose energy.
- Keep characters original and not visually identical to any real person.
- Add moonwalk-like movement lines and dramatic floor lighting.
- The image must feel like a pop performance finance spectacle while remaining original.
"""
    if _is_jojo_fifa_theme(visual_theme):
        return """Scene notes for JOJO x FIFA crossover style:
- Combine exaggerated manga freeze-frame poses with stadium floodlights, tactical boards, and scoreboard energy.
- Use bold ink outlines, speed lines, and dramatic color blocking like a shonen battle panel.
- Translate capital flow into football passing lanes, counterattacks, and defensive pressing.
- Keep all characters original; no JoJo characters, no FIFA/World Cup logos, no real team crests or player likenesses.
"""
    return ""


def _is_michael_jackson_theme(visual_theme: str) -> bool:
    visual_theme = _clean_text(visual_theme).lower()
    return any(keyword in visual_theme for keyword in ["麥克傑克森", "michael jackson", "mj"])


def _is_ninja_turtle_theme(visual_theme: str) -> bool:
    visual_theme = _clean_text(visual_theme).lower()
    return any(keyword in visual_theme for keyword in ["忍者龜", "ninja turtle", "tmnt"])


def _is_jojo_theme(visual_theme: str) -> bool:
    visual_theme = _clean_text(visual_theme).lower()
    return any(keyword in visual_theme for keyword in ["jojo", "ジョジョ", "jo jo"])


def _is_fifa_theme(visual_theme: str) -> bool:
    visual_theme = _clean_text(visual_theme).lower()
    return any(keyword in visual_theme for keyword in ["fifa", "足球", "football", "世界盃", "世界杯", "世足"])


def _is_jojo_fifa_theme(visual_theme: str) -> bool:
    return _is_jojo_theme(visual_theme) and _is_fifa_theme(visual_theme)


# Taiwan market color convention (NOT US/Western):
# 漲 / buy pressure / bullish = RED (紅)
# 跌 / sell pressure / bearish = GREEN (綠)
_TW_COLOR_NOTE = (
    "IMPORTANT Taiwan market colors: rising/buy/bullish = RED; falling/sell/bearish = GREEN. "
    "Never use US-style green-up/red-down."
)


def _composition_a(date_label: str) -> str:
    templates = [
        f"""Composition: a dramatic Taiwan stock-market control room with two giant monitors. One monitor shows green sell pressure hammering the market, the other shows red institutional buy signals locking onto the target stock. "博士" stands at a central console analyzing the data. "韭菜" stands at the edge of the room with a phone, reacting to the move. Cinematic wide shot, clear depth layers, finance-thriller atmosphere. {_TW_COLOR_NOTE}""",
        f"""Composition: a conveyor-belt money transfer scene inside a stock exchange warehouse. Red money crates labeled only with abstract arrows move toward the bought target, while green crates fall off a side chute. "博士" supervises from a control platform; "韭菜" runs beside the belt in panic. Cinematic industrial finance scene, wide shot. {_TW_COLOR_NOTE}""",
        f"""Composition: a giant balance scale in the center of a financial plaza. One side sinks under green sell orders, the other side rises with red buy orders. "博士" stands on the scale base reading data; "韭菜" clings to one side of the scale. Wide cinematic 3D scene. {_TW_COLOR_NOTE}""",
    ]
    return templates[(_date_index(date_label)) % len(templates)]


def _composition_b(date_label: str) -> str:
    templates = [
        f"""Composition: backstage theater reveal. The front stage shows a roaring market rally, but the curtain is pulled open to reveal institutional hands moving red (up) and green (down) chart props behind the scenes. "博士" pulls the curtain; "韭菜" gasps in the front row. Cinematic finance stage, different angle from A. {_TW_COLOR_NOTE}""",
        f"""Composition: financial weather-room scene. A huge radar map shows a red capital storm moving into the bought target and a green pressure zone over the sold/crashing market. "博士" presents like a market meteorologist; "韭菜" holds an umbrella made of stock charts. Cinematic wide shot, suspense tone. {_TW_COLOR_NOTE}""",
    ]
    return templates[(_date_index(date_label) + 2) % len(templates)]


def _date_index(date_label: str) -> int:
    try:
        return int(str(date_label).split("/")[-1])
    except Exception:
        return 0


def _prompt_a(copy: Dict[str, List[str]], date_label: str, visual_theme: str) -> str:
    line1, line2 = copy["a_title_lines"]
    badge1, badge2 = copy["a_badges"]
    return f"""Create a complete 16:9 YouTube finance thumbnail with readable Traditional Chinese text, anime-comic style, high contrast, high CTR.

{_theme_phrase(visual_theme)}

Scene: a dramatic Taiwan stock-market moment using the daily visual theme. Use original designs only, no copied IP, no real company logos.

{_theme_scene_notes(visual_theme)}

{_composition_a(date_label)}

Characters:
- "博士": original Taiwanese finance professor and data analyst, glasses, lab coat + trader jacket, calm and sharp.
- "韭菜": original retail investor in a hoodie, shocked and excited, holding a phone with stock charts.

IMPORTANT text layout:
- Add huge bold Traditional Chinese title text across the bottom 30%:
  "{line1}"
  "{line2}"
- Add two small badge labels near the title:
  "{badge1}"
  "{badge2}"

Typography: text must be crisp, large, readable at YouTube thumbnail size, bold white/yellow text with black stroke or dark shadow, no misspellings, no distorted Chinese characters.

No watermark, no real company logos, no extra characters. Characters crisp; action elements can have motion blur."""


def _prompt_b(copy: Dict[str, List[str]], date_label: str, visual_theme: str) -> str:
    line1, line2 = copy["b_title_lines"]
    badge1, badge2 = copy["b_badges"]
    return f"""Create a complete 16:9 YouTube finance thumbnail with readable Traditional Chinese text, anime-comic finance analysis style, high contrast, suspenseful, high CTR.

{_theme_phrase(visual_theme)}

Scene: a suspenseful Taiwan stock-market analysis moment using the daily visual theme. It must be a totally different camera angle, layout, character scale, and background from A.

{_theme_scene_notes(visual_theme)}

{_composition_b(date_label)}

Characters:
- "博士": original Taiwanese finance professor and data analyst, glasses, lab coat + trader jacket, pointing at the hidden institutional signal.
- "韭菜": original retail investor in a hoodie, shocked and curious, holding a phone.

IMPORTANT text layout:
- Add huge bold Traditional Chinese title text across the bottom 30%:
  "{line1}"
  "{line2}"
- Add two small badge labels near the title:
  "{badge1}"
  "{badge2}"

Typography: text must be crisp, large, readable at YouTube thumbnail size, bold white/yellow text with black stroke or dark shadow, no misspellings, no distorted Chinese characters.

No watermark, no real company logos, no extra characters. Characters crisp; chart signals and background motion can be dramatic but not cluttered."""


def _performance_window(log_path: Optional[str] = None):
    try:
        from modules.report_paths import resolve_shared_artifact
        from modules.youtube_learning import learning_window

        log_path = log_path or resolve_shared_artifact(os.path.join(BASE_DIR, "reports"), "channel_story_log")
        return learning_window(log_path)
    except Exception:
        return None


def _packaging_formula_lines(window: Optional[Dict[str, Any]] = None) -> List[str]:
    """
    Data-computed replacement for the old hard-coded「已驗證包裝公式」: best structure
    families and the channel-relative winner threshold from channel_story_log (+ snapshots).
    """
    window = window if window is not None else _performance_window()
    if not window or not window.get("sample"):
        return [
            "- 成效樣本不足：本日不宣稱任何「已驗證公式」，A/B 各用不同結構家族累積樣本。",
            "- 標題要講具體行為：外資砍誰、投信買誰、三大法人反手買誰、哪個題材爆量。",
        ]
    from modules.youtube_learning import STRUCTURE_LABELS, load_stock_names, strip_brand, structure_stats

    baseline = window.get("baseline") or {}
    stats = structure_stats(window["sample"], load_stock_names())
    ranked = sorted(stats.items(), key=lambda kv: -kv[1]["median"])
    lines = [f"- 基準：{window.get('basis')}；頻道中位數 {baseline.get('median', 0):.0f} views（n={baseline.get('n', 0)}）"]
    for family, st in ranked[:3]:
        lines.append(
            f"- {STRUCTURE_LABELS.get(family, family)}：n={st['n']}，中位 {st['median']:.0f}；"
            f"代表 {st['best']['date']}「{strip_brand(st['best']['title'])[:28]}」"
        )
    threshold = baseline.get("win_threshold")
    if threshold:
        lines.append(
            f"- 未達頻道中位數 ×2（約 {threshold:.0f} views）不判斷標題好壞，只記錄觀察；封面 A/B 以 Studio「測試與比較」結果為準。"
        )
    lines.append("- 避免只說「資金輪動」；若要表達資金移動，改寫成可驗證的主力買賣動作。")
    return lines


def _ab_checklist_lines(window: Optional[Dict[str, Any]] = None) -> List[str]:
    window = window if window is not None else _performance_window()
    median = ((window or {}).get("baseline") or {}).get("median")
    median_text = f"頻道中位數（約 {median:.0f} views）" if median else "頻道中位數"
    return [
        "- [ ] 用標題 A 上架；封面 A/B 在 YouTube Studio「測試與比較」同時上傳（只差本週 A/B 變數）",
        "- [ ] 確認封面中文字清楚可讀",
        "- [ ] NotebookLM Video Overview 第一張投影片有封面頁",
        "- [ ] 發布後立即貼置頂留言",
        "- [ ] YouTube 回報封面勝出後執行 `python3 cli.py youtube record-ab-result`；觀看數快照由 /1 自動記錄",
        f"- [ ] 觀看低於 {median_text} 只記錄觀察；標題 B 留給之後單獨的標題測試",
    ]


def _build_100w_section(structured_data: Dict[str, Any], youtube_package: Dict[str, Any]) -> List[str]:
    lines = _simulate_100w_lines(structured_data, youtube_package)
    if not lines:
        return []
    return ["- 100 萬模擬倉位："] + [f"  - {line}" for line in lines]


def build_video_production_plan(structured_data, report_content, youtube_package, date_str, visual_theme="", performance_window=None):
    """Return a Markdown production plan string."""
    performance_window = performance_window if performance_window is not None else _performance_window()
    structured_data = structured_data or {}
    youtube_package = youtube_package or {}
    date_label = _date_label(date_str)
    titles = _pick_titles(youtube_package.get("titles", []))
    copy = _cover_copy(structured_data)
    description = youtube_package.get("description", "（未生成）")
    pinned_comment = youtube_package.get("pinned_comment", "（未生成）")
    ai_picks = _as_list(structured_data.get("ai_data_picks"))
    divergences = _as_list(structured_data.get("price_volume_divergence"))
    prev_results = _as_list(structured_data.get("prev_day_picks_result"))

    lines = [
        f"# Video Production Plan {date_str}",
        "",
        "## 今日主線",
        "",
        f"- 大盤摘要：{structured_data.get('index_action', 'N/A')}",
        f"- AI 觀察焦點：{', '.join(ai_picks) if ai_picks else '無明確標的'}",
        f"- 籌碼 / 量價背離：{', '.join(divergences) if divergences else '無明顯背離'}",
        f"- 昨日預測驗收 / 預測損益：{', '.join(prev_results) if prev_results else '無可驗收標的'}",
        f"- 今日視覺主題：{visual_theme if visual_theme else 'AI Taiwan stock market analysis, data-driven market battle'}",
        "",
        "### 包裝成效參考（本機計算，非寫死公式）",
        "",
        *_packaging_formula_lines(performance_window),
        "",
        "### 100 萬模擬損益",
        "",
        * _build_100w_section(structured_data, youtube_package),
        "",
        "## A/B Test 上架組合",
        "",
        "### A 組：數據衝擊型",
        "",
        "**影片標題**",
        "",
        "```text",
        titles["a"],
        "```",
        "",
        "**封面大字**",
        "",
        "```text",
        "\n".join(copy["a_title_lines"]),
        "```",
        "",
        "**封面小標**",
        "",
        "```text",
        "\n".join(copy["a_badges"]),
        "```",
        "",
        "**封面產出 Prompt**",
        "",
        "```text",
        _prompt_a(copy, date_label, visual_theme),
        "```",
        "",
        "### B 組：好奇心缺口型",
        "",
        "**影片標題**",
        "",
        "```text",
        titles["b"],
        "```",
        "",
        "**封面大字**",
        "",
        "```text",
        "\n".join(copy["b_title_lines"]),
        "```",
        "",
        "**封面小標**",
        "",
        "```text",
        "\n".join(copy["b_badges"]),
        "```",
        "",
        "**封面產出 Prompt**",
        "",
        "```text",
        _prompt_b(copy, date_label, visual_theme),
        "```",
        "",
        "## NotebookLM Video Overview 封面視覺統一指令",
        "",
        "```text",
        "請將影片第一張投影片設計成封面頁，不要直接進入內容。",
        f"封面主標題請使用：「{copy['a_title_lines'][0]}」",
        f"封面副標題請使用：「{copy['a_title_lines'][1]}」",
        f"左上角小字日期請使用：「{date_label} 台股盤後」",
        f"封面視覺主題請與 YouTube 封面一致：{visual_theme if visual_theme else 'AI帶你看股市、博士與韭菜、紅綠 K 線、主力買賣邏輯'}。",
        "請保留博士與韭菜兩個角色感，使用高對比深色背景、白色/黃色大字、紅綠 K 線元素。",
        "請不要自行改寫主標題與副標題。",
        "```",
        "",
        "## YouTube 描述",
        "",
        "```text",
        description,
        "```",
        "",
        "## 置頂留言",
        "",
        "```text",
        pinned_comment,
        "```",
        "",
        "## 上架 Checklist",
        "",
        *_ab_checklist_lines(performance_window),
        "",
        "## 48 小時驗收欄位",
        "",
        "| 項目 | 數值 | 判斷 |",
        "|------|------|------|",
        "| CTR |  |  |",
        "| 平均觀看時長 |  |  |",
        "| 前 30 秒留存 |  |  |",
        "| 留言數 |  |  |",
        "| 封面 A/B 結果（Studio 測試與比較 → record-ab-result） |  |  |",
        "",
    ]
    return "\n".join(lines)


def build_youtube_package_addendum(structured_data, report_content, youtube_package, date_str, visual_theme="", performance_window=None):
    """Return the production addendum appended to the daily YouTube package."""
    performance_window = performance_window if performance_window is not None else _performance_window()
    structured_data = structured_data or {}
    youtube_package = youtube_package or {}
    date_label = _date_label(date_str)
    titles = _pick_titles(youtube_package.get("titles", []))
    copy = _cover_copy(structured_data)
    ai_picks = _as_list(structured_data.get("ai_data_picks"))
    divergences = _as_list(structured_data.get("price_volume_divergence"))
    prev_results = _as_list(structured_data.get("prev_day_picks_result"))
    theme_label = _display_theme(visual_theme)

    lines = [
        "---",
        "",
        "## 🎬 今日影片製作計畫",
        "",
        "### 今日主線",
        "",
        f"- 大盤摘要：{structured_data.get('index_action', 'N/A')}",
        f"- AI 觀察焦點：{', '.join(ai_picks) if ai_picks else '無明確標的'}",
        f"- 籌碼 / 量價背離：{', '.join(divergences) if divergences else '無明顯背離'}",
        f"- 昨日預測驗收 / 預測損益：{', '.join(prev_results) if prev_results else '無可驗收標的'}",
        f"- 今日視覺主題：{theme_label}",
        "",
        "### 包裝成效參考（本機計算，非寫死公式）",
        "",
        *_packaging_formula_lines(performance_window),
        "",
        "### 100 萬模擬損益",
        "",
        * _build_100w_section(structured_data, youtube_package),
        "",
        "### A 組封面設定（數據衝擊型）",
        "",
        "**對應標題**",
        "",
        "```text",
        titles["a"],
        "```",
        "",
        "**封面大字**",
        "",
        "```text",
        "\n".join(copy["a_title_lines"]),
        "```",
        "",
        "**封面小標 / Badge**",
        "",
        "```text",
        "\n".join(copy["a_badges"]),
        "```",
        "",
        "**封面產出 Prompt**",
        "",
        "```text",
        _prompt_a(copy, date_label, visual_theme),
        "```",
        "",
        "### B 組封面設定（好奇心缺口型）",
        "",
        "**對應標題**",
        "",
        "```text",
        titles["b"],
        "```",
        "",
        "**封面大字**",
        "",
        "```text",
        "\n".join(copy["b_title_lines"]),
        "```",
        "",
        "**封面小標 / Badge**",
        "",
        "```text",
        "\n".join(copy["b_badges"]),
        "```",
        "",
        "**封面產出 Prompt**",
        "",
        "```text",
        _prompt_b(copy, date_label, visual_theme),
        "```",
        "",
        "## NotebookLM Video Overview 封面視覺統一指令",
        "",
        "```text",
        "請將影片第一張投影片設計成封面頁，不要直接進入內容。",
        f"封面主標題請使用：「{copy['a_title_lines'][0]}」",
        f"封面副標題請使用：「{copy['a_title_lines'][1]}」",
        f"左上角小字日期請使用：「{date_label} 台股盤後」",
        f"封面視覺主題請與 YouTube 封面一致：{visual_theme if visual_theme else 'AI帶你看股市、博士與韭菜、紅綠 K 線、主力買賣邏輯'}。",
        "請保留博士與韭菜兩個角色感，使用高對比深色背景、白色/黃色大字、紅綠 K 線元素。",
        "請不要自行改寫主標題與副標題。",
        "```",
        "",
        "## 置頂留言",
        "",
        "```text",
        youtube_package.get("pinned_comment", "（未生成）"),
        "```",
        "",
        "## 48 小時驗收欄位",
        "",
        "| 項目 | 數值 | 判斷 |",
        "|------|------|------|",
        "| CTR |  |  |",
        "| 平均觀看時長 |  |  |",
        "| 前 30 秒留存 |  |  |",
        "| 留言數 |  |  |",
        "| 封面 A/B 結果（Studio 測試與比較 → record-ab-result） |  |  |",
        "",
    ]
    return "\n".join(lines)


def append_youtube_package_addendum(package_path, addendum):
    """Append production details to the daily YouTube package."""
    with open(package_path, "a", encoding="utf-8") as f:
        f.write("\n\n")
        f.write(addendum.rstrip())
        f.write("\n")
    return package_path


def build_video_asset_record(structured_data, youtube_package, date_str, visual_theme=""):
    """Return structured title/thumbnail variants for the analytics database."""
    structured_data = structured_data or {}
    youtube_package = youtube_package or {}
    date_label = _date_label(date_str)
    titles = _pick_titles(youtube_package.get("titles", []))
    copy = _cover_copy(structured_data)
    theme_label = _display_theme(visual_theme)

    # Nothing is "selected" at planning time: the live title is matched to A/B when /1
    # refreshes YouTube stats (youtube_analytics.match_title_variant).
    title_variants = [
        {"variant": "a", "title_type": "data", "title": titles["a"], "selected": False, "planned_first": True},
        {"variant": "b", "title_type": "curiosity", "title": titles["b"], "selected": False},
    ]
    thumbnail_variants = [
        {
            "variant": "a",
            "cover_text": copy["a_title_lines"],
            "badges": copy["a_badges"],
            "prompt": _prompt_a(copy, date_label, visual_theme),
            "selected": True,
        },
        {
            "variant": "b",
            "cover_text": copy["b_title_lines"],
            "badges": copy["b_badges"],
            "prompt": _prompt_b(copy, date_label, visual_theme),
            "selected": False,
        },
    ]
    return {
        "run_date": date_str,
        "main_topic": _short(structured_data.get("index_action") or structured_data.get("main_theme") or "今日台股主軸"),
        "visual_theme": theme_label,
        "title_variants": title_variants,
        "thumbnail_variants": thumbnail_variants,
    }


def save_video_production_plan(plan_content, output_dir, date_str):
    os.makedirs(output_dir, exist_ok=True)
    compact_date = str(date_str).replace("-", "")
    filepath = os.path.join(output_dir, f"video_production_plan_{compact_date}.md")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(plan_content)
    return filepath
