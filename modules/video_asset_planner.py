"""
Deterministic YouTube / NotebookLM production plan generator.

This module does not call any AI API. It standardizes the daily human-facing
production package so the channel can keep titles, thumbnails, NotebookLM video
overview, and upload checks aligned without manually rewriting prompts.
"""

from __future__ import annotations

import os
import re


def _as_list(value):
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def _clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _short(value, limit=34):
    value = _clean_text(value)
    return value if len(value) <= limit else value[:limit].rstrip() + "..."


def _compact_label(value, limit=8):
    value = _stock_label(value)
    value = re.sub(r"[^\w\u4e00-\u9fff]", "", value)
    return value if len(value) <= limit else value[:limit]


def _stock_label(text):
    """
    Extract a short stock/topic label from strings like:
    "台積電 (-489.8億)" or "旺宏 (外資大買)".
    """
    text = _clean_text(text)
    if not text:
        return ""
    text = re.sub(r"^[0-9A-Za-z.]+\s*", "", text)
    text = re.split(r"[（(]", text)[0]
    text = re.split(r"[:：,，|｜]", text)[0]
    return text.strip()


def _pick_titles(titles):
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
        curiosity = titles[0] if titles[0] != data_title else (titles[1] if len(titles) > 1 else data_title)

    return {"a": data_title, "b": curiosity}


def _cover_copy(structured_data):
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


def _theme_phrase(visual_theme):
    visual_theme = _clean_text(visual_theme)
    if _is_michael_jackson_theme(visual_theme):
        visual_theme = (
            "80s pop concert stage style inspired by moonwalk-era performance: "
            "white glove, fedora silhouette, sparkling black jacket, spotlight dance floor, "
            "rhythmic pose, no real celebrity likeness."
        )
    visual_theme = visual_theme.rstrip(".。")
    if visual_theme:
        return f"Daily visual theme: {visual_theme}."
    return "Daily visual theme: AI Taiwan stock market analysis, data-driven market battle."


def _display_theme(visual_theme):
    visual_theme = _clean_text(visual_theme)
    if _is_michael_jackson_theme(visual_theme):
        return "麥克傑克森舞台風格（80s pop dance stage / 白手套 / 聚光燈 / 月球漫步感；避免真人肖像）"
    return visual_theme if visual_theme else "待設定（可在 /2 指定，無需重跑 Gemini）"


def _is_michael_jackson_theme(visual_theme):
    visual_theme = _clean_text(visual_theme).lower()
    return any(keyword in visual_theme for keyword in ["麥克傑克森", "michael jackson", "mj"])


def _date_index(date_label):
    try:
        return int(str(date_label).split("/")[-1])
    except Exception:
        return 0


def _composition_a(date_label):
    templates = [
        """Composition: low-angle cinematic hero shot on a crowded trading floor. "博士" stands center-front holding a glowing tablet; "韭菜" is behind him, half-hiding from falling red candlesticks. A giant stock scoreboard curves overhead like a stadium screen. Strong foreground depth, not symmetrical split-screen.""",
        """Composition: isometric overhead view of a stock-market game board. Red and green candlestick towers form city blocks. "博士" and "韭菜" stand on different tiles, looking up at a huge institutional money arrow moving across the board. Make it feel like a 3D animated strategy scene.""",
        """Composition: movie-poster close-up. "韭菜" fills the foreground with an exaggerated shocked face and phone reflection in his eyes; "博士" appears smaller in the background pointing to a giant chart wall. Use strong depth-of-field and diagonal motion, not left-right symmetry.""",
        """Composition: conveyor-belt money transfer scene inside a stock exchange warehouse. Green money crates labeled only with abstract arrows move toward the bought target, while red crates fall off a side chute. "博士" supervises from a control platform; "韭菜" runs beside the belt in panic.""",
        """Composition: giant balance scale in the center of a financial plaza. One side sinks under red sell orders, the other side rises with green buy orders. "博士" stands on the scale base reading data; "韭菜" clings to one side of the scale. Wide cinematic 3D scene.""",
    ]
    return templates[_date_index(date_label) % len(templates)]


def _composition_b(date_label):
    templates = [
        """Composition: vertical split-depth scene, not a simple left-right split. Foreground shows "韭菜" staring at a phone; behind him, a transparent x-ray-like stock dashboard reveals institutional buy/sell flows. "博士" appears as a small figure on an upper balcony pointing at the hidden signal.""",
        """Composition: elevator shaft metaphor. A green-lit elevator carrying the bought target shoots upward, while a red elevator carrying the sold target drops downward. "博士" watches from a glass control room; "韭菜" presses buttons nervously. Dynamic diagonal perspective.""",
        """Composition: stock-market airport runway. A green candlestick airplane takes off toward the bought target, while a red plane aborts takeoff for the sold target. "博士" acts like an air-traffic controller; "韭菜" looks through binoculars from the runway edge.""",
        """Composition: backstage theater reveal. The front stage shows a roaring market rally, but the curtain is pulled open to reveal institutional hands moving red and green chart props behind the scenes. "博士" pulls the curtain; "韭菜" gasps in the front row.""",
        """Composition: financial weather-room scene. A huge radar map shows a green capital storm moving into the bought target and a red pressure zone over the sold target. "博士" presents like a market meteorologist; "韭菜" holds an umbrella made of stock charts.""",
    ]
    return templates[(_date_index(date_label) + 2) % len(templates)]


def _prompt_a(copy, date_label, visual_theme):
    line1, line2 = copy["a_title_lines"]
    badge1, badge2 = copy["a_badges"]
    return f"""Create a complete 16:9 YouTube finance thumbnail with readable Traditional Chinese text, anime-comic style, high contrast, high CTR.

{_theme_phrase(visual_theme)}

Scene: a dramatic Taiwan stock-market moment using the daily visual theme. Use original designs only, no copied IP, no real company logos.

{_composition_a(date_label)}

Characters:
- "博士": original Taiwanese finance professor and data analyst, glasses, lab coat + trader jacket, calm and sharp.
- "韭菜": original retail investor in a hoodie, shocked and excited, holding a phone with stock charts.
- The scene must clearly show institutional selling versus the counterattack target through red falling candlesticks and green rising candlesticks.

IMPORTANT text layout:
- Add huge bold Traditional Chinese title text across the bottom 30%:
  "{line1}"
  "{line2}"
- Add small date label in the upper-left corner:
  "{date_label} 台股盤後"
- Add two small badge labels near the title:
  "{badge1}"
  "{badge2}"

Typography: text must be crisp, large, readable at YouTube thumbnail size, bold white/yellow text with black stroke or dark shadow, no misspellings, no distorted Chinese characters.

No watermark, no real company logos, no extra characters. Characters crisp; action elements can have motion blur."""


def _prompt_b(copy, date_label, visual_theme):
    line1, line2 = copy["b_title_lines"]
    badge1, badge2 = copy["b_badges"]
    return f"""Create a complete 16:9 YouTube finance thumbnail with readable Traditional Chinese text, anime-comic finance analysis style, high contrast, suspenseful, high CTR.

{_theme_phrase(visual_theme)}

Scene: a suspenseful Taiwan stock-market analysis moment using the daily visual theme. It must be a totally different camera angle, layout, character scale, and background from A.

{_composition_b(date_label)}

Characters:
- "博士": original Taiwanese finance professor and data analyst, glasses, lab coat + trader jacket, pointing at the hidden institutional signal.
- "韭菜": original retail investor in a hoodie, shocked and curious, holding a phone.
- The selling target should be represented by red falling candlesticks, while the secretly bought targets emerge under green light.

IMPORTANT text layout:
- Add huge bold Traditional Chinese title text across the bottom 30%:
  "{line1}"
  "{line2}"
- Add small date label in the upper-left corner:
  "{date_label} 台股盤後"
- Add two small badge labels near the title:
  "{badge1}"
  "{badge2}"

Typography: text must be crisp, large, readable at YouTube thumbnail size, bold white/yellow text with black stroke or dark shadow, no misspellings, no distorted Chinese characters.

No watermark, no real company logos, no extra characters. Characters crisp; chart signals and background motion can be dramatic but not cluttered."""


def build_video_production_plan(structured_data, report_content, youtube_package, date_str, visual_theme=""):
    """Return a Markdown production plan string."""
    date_label = _date_label(date_str)
    titles = _pick_titles(youtube_package.get("titles", []))
    copy = _cover_copy(structured_data or {})
    description = youtube_package.get("description", "（未生成）")
    pinned_comment = youtube_package.get("pinned_comment", "（未生成）")
    ai_picks = _as_list((structured_data or {}).get("ai_data_picks"))
    divergences = _as_list((structured_data or {}).get("price_volume_divergence"))
    prev_results = _as_list((structured_data or {}).get("prev_day_picks_result"))

    lines = [
        f"# Video Production Plan {date_str}",
        "",
        "## 今日主線",
        "",
        f"- 大盤摘要：{(structured_data or {}).get('index_action', 'N/A')}",
        f"- AI 觀察焦點：{', '.join(ai_picks) if ai_picks else '無明確標的'}",
        f"- 籌碼 / 量價背離：{', '.join(divergences) if divergences else '無明顯背離'}",
        f"- 昨日預測驗收：{', '.join(prev_results) if prev_results else '無可驗收標的'}",
        f"- 今日視覺主題：{visual_theme if visual_theme else 'AI Taiwan stock market analysis, data-driven market battle'}",
        "",
        "## 已驗證包裝公式",
        "",
        "- 目前較有效：大盤表面很強/很弱 + 核心股外資具體金額 + 隱藏買盤標的。",
        "- 標題要講具體行為：外資砍誰、投信買誰、三大法人反手買誰、哪個題材爆量。",
        "- 避免只說「資金輪動」；若需要表達資金移動，改寫成可驗證的籌碼動作。",
        "- 參考句型：台股狂飆卻暗藏殺機？台積電大漲外資竟狂砍XX億！主力反手鎖定「這檔」記憶體股",
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
        "- [ ] 先用 A 組上架",
        "- [ ] 確認封面中文字清楚可讀",
        "- [ ] NotebookLM Video Overview 第一張投影片有封面頁",
        "- [ ] 發布後立即貼置頂留言",
        "- [ ] 48 小時後記錄 CTR",
        "- [ ] CTR < 5% 時測 B 組封面或 B 組標題",
        "",
        "## 48 小時驗收欄位",
        "",
        "| 項目 | 數值 | 判斷 |",
        "|------|------|------|",
        "| CTR |  |  |",
        "| 平均觀看時長 |  |  |",
        "| 前 30 秒留存 |  |  |",
        "| 留言數 |  |  |",
        "| 是否更換 B 組 |  |  |",
        "",
    ]
    return "\n".join(lines)


def build_youtube_package_addendum(structured_data, report_content, youtube_package, date_str, visual_theme=""):
    """Return the production addendum appended to the daily YouTube package."""
    date_label = _date_label(date_str)
    titles = _pick_titles(youtube_package.get("titles", []))
    copy = _cover_copy(structured_data or {})
    ai_picks = _as_list((structured_data or {}).get("ai_data_picks"))
    divergences = _as_list((structured_data or {}).get("price_volume_divergence"))
    prev_results = _as_list((structured_data or {}).get("prev_day_picks_result"))
    theme_label = _display_theme(visual_theme)

    lines = [
        "---",
        "",
        "## 🎬 今日影片製作計畫",
        "",
        "### 今日主線",
        "",
        f"- 大盤摘要：{(structured_data or {}).get('index_action', 'N/A')}",
        f"- AI 觀察焦點：{', '.join(ai_picks) if ai_picks else '無明確標的'}",
        f"- 籌碼 / 量價背離：{', '.join(divergences) if divergences else '無明顯背離'}",
        f"- 昨日預測驗收：{', '.join(prev_results) if prev_results else '無可驗收標的'}",
        f"- 今日視覺主題：{theme_label}",
        "",
        "### 已驗證包裝公式",
        "",
        "- 目前較有效：時事情緒鉤子 + 大盤矛盾 + 核心股外資具體金額 + 隱藏買盤標的。",
        "- 標題要講具體行為：外資砍誰、投信買誰、三大法人反手買誰、哪個題材爆量。",
        "- 避免只說「資金輪動」；若要表達資金移動，改寫成可驗證的主力買賣動作。",
        "- 觀看數低於 900 不判斷 A/B 勝負，只記錄觀察。",
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
        "### NotebookLM Video Overview 封面視覺統一指令",
        "",
        "```text",
        "請將影片第一張投影片設計成封面頁，不要直接進入內容。",
        f"封面主標題請使用：「{copy['a_title_lines'][0]}」",
        f"封面副標題請使用：「{copy['a_title_lines'][1]}」",
        f"左上角小字日期請使用：「{date_label} 台股盤後」",
        f"封面視覺主題請與 YouTube 封面一致：{theme_label}。",
        "請保留博士與韭菜兩個角色感，使用高對比深色背景、白色/黃色大字、紅綠 K 線元素。",
        "請不要自行改寫主標題與副標題。",
        "```",
        "",
        "### 48 小時驗收欄位",
        "",
        "| 項目 | 數值 | 判斷 |",
        "|------|------|------|",
        "| 觀看數 |  | 低於 900 不判斷 A/B |",
        "| CTR |  |  |",
        "| 平均觀看時長 |  |  |",
        "| 前 30 秒留存 |  |  |",
        "| 留言數 |  |  |",
        "| 是否更換 B 組 |  |  |",
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
    date_label = _date_label(date_str)
    source_titles = _as_list(youtube_package.get("titles", []))
    titles = _pick_titles(source_titles)
    copy = _cover_copy(structured_data or {})
    ai_picks = _as_list((structured_data or {}).get("ai_data_picks"))
    divergences = _as_list((structured_data or {}).get("price_volume_divergence"))
    title_variants = [
        {
            "variant": "A",
            "title_type": "source_a",
            "title": source_titles[0] if len(source_titles) >= 1 else titles["a"],
            "selected": True,
        },
        {
            "variant": "B",
            "title_type": "source_b",
            "title": source_titles[1] if len(source_titles) >= 2 else titles["b"],
            "selected": False,
        },
    ]

    return {
        "run_date": date_str,
        "main_topic": (ai_picks[0] if ai_picks else (divergences[0] if divergences else "")),
        "visual_theme": visual_theme,
        "title_variants": title_variants,
        "thumbnail_variants": [
            {
                "variant": "A",
                "cover_text": copy["a_title_lines"],
                "badges": copy["a_badges"],
                "prompt": _prompt_a(copy, date_label, visual_theme),
                "selected": True,
            },
            {
                "variant": "B",
                "cover_text": copy["b_title_lines"],
                "badges": copy["b_badges"],
                "prompt": _prompt_b(copy, date_label, visual_theme),
                "selected": False,
            },
        ],
    }


def save_video_production_plan(plan_content, output_dir, date_str):
    os.makedirs(output_dir, exist_ok=True)
    compact_date = date_str.replace("-", "")
    filepath = os.path.join(output_dir, f"video_production_plan_{compact_date}.md")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(plan_content)
    return filepath


def _date_label(date_str):
    try:
        year, month, day = date_str.split("-")
        return f"{int(month)}/{int(day)}"
    except Exception:
        return date_str
