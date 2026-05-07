"""
YouTube Thumbnail Prompt Generator with A/B Testing Support.
Uses Gemini for title generation and writes image prompts for manual thumbnail
generation. The actual image is produced manually outside this script.
"""
import os
import datetime
import json
import re
from google import genai


# === Visual Style Presets ===
STYLE_PRESETS = {
    "dc_comics": {
        "name": "DC 美漫風",
        "prompt": (
            "DC American comic book art style with bold black ink outlines, halftone dot shading, "
            "dramatic cinematic lighting. A powerful charging bull with golden horns surrounded by "
            "explosive energy and flying stock chart papers. Wall Street skyscrapers and NYSE building "
            "in dark navy blue background with lightning bolts. "
            "Color scheme: deep navy blue, metallic gold, crimson red, emerald green. "
            "High contrast, dramatic shadows, comic book panel borders. "
            "Think 'The Dark Knight meets Wall Street'."
        ),
    },
    "cyberpunk": {
        "name": "賽博龐克科技風",
        "prompt": (
            "Cyberpunk futuristic style with neon glow effects, holographic stock charts floating "
            "in the air, dark city skyline with glowing circuit board patterns. A digital bull made of "
            "blue neon light and data streams charging through a futuristic trading floor. "
            "Color scheme: deep black, electric blue (#00d4ff), hot pink/magenta (#ff00ff), "
            "neon green accents. Glitch effects, scan lines, holographic UI elements. "
            "Blade Runner meets Wall Street aesthetic."
        ),
    },
    "epic_cinematic": {
        "name": "史詩電影風",
        "prompt": (
            "Epic cinematic movie poster style with dramatic volumetric lighting and lens flare. "
            "A golden bull statue on top of a mountain of gold coins, with stock market charts rising "
            "like aurora borealis in the sky behind it. Storm clouds parting to reveal golden sunlight. "
            "Color scheme: deep black, warm gold, amber, with cool blue shadows. "
            "Ultra dramatic lighting like a Marvel movie poster. Premium, luxurious, powerful."
        ),
    },
    "chen_uen_ink": {
        "name": "鄭問水墨風",
        "prompt": (
            "Chen Uen (鄭問) Taiwanese ink wash comic art style. Bold black Chinese ink brush strokes "
            "with dramatic ink splatter and wash effects. A majestic war horse (馬) charging forward "
            "through splashing ink and gold paint, reminiscent of classical Chinese warrior paintings. "
            "Stock market candlestick charts rendered as ink brush strokes rising behind the horse. "
            "Traditional Chinese seal stamps (印章) as accent elements. "
            "Color palette: black ink, vermillion red (朱紅), burnished gold leaf, rice paper white. "
            "Dramatic composition with explosive energy, combining Eastern calligraphic art with "
            "modern financial imagery. Premium, powerful, uniquely Taiwanese aesthetic."
        ),
    },
}


def _normalize_custom_style(style_key_or_custom):
    style = str(style_key_or_custom or "").strip()
    normalized = style.lower()
    if any(keyword in normalized for keyword in ["忍者龜", "ninja turtle", "tmnt"]):
        return (
            "Teenage Mutant Ninja Turtles-inspired ORIGINAL sewer-ninja finance parody aesthetic: "
            "underground sewer dojo trading floor, round manhole covers used as glowing stock dashboards, "
            "neon green ooze light, brick tunnel walls, metal pipes, skateboard-graffiti texture, "
            "pizza-box shaped chart props, colored mask accents, shell-like armor silhouettes, "
            "red and green candlestick arrows exploding like comic action effects. "
            "No actual TMNT characters, no names, no franchise logos, no direct likenesses."
        )
    if any(keyword in normalized for keyword in ["甄嬛傳", "甄嬛传", "後宮", "后宫", "宮鬥", "宫斗"]):
        return (
            "Original imperial palace intrigue drama aesthetic inspired by Qing dynasty court politics: "
            "ornate palace corridors, silk curtains, lacquered red columns, golden throne-room lighting, "
            "jade hairpin and embroidered robe textures, secret memorial scrolls, candlelit strategy tables, "
            "subtle power struggle atmosphere, elegant but dangerous court tension. Include original fictional "
            "palace-court figures such as a powerful empress strategist silhouette, cautious ministers, "
            "and shadowy faction messengers watching the capital-flow signal table. "
            "Translate stock-market capital flows into palace-faction power shifts: glowing trade slips, "
            "court edict scrolls as market signals, red and green candlestick patterns reflected on polished jade. "
            "No actual Zhen Huan characters, no actor likenesses, no drama title, no franchise logos, "
            "no direct costume copying; create an original historical palace-finance visual world."
        )
    if style:
        return (
            f"Daily user-selected visual theme: {style}. Interpret it as an original, non-infringing, "
            "cinematic finance-thumbnail visual world. Do not copy protected characters, names, logos, "
            "or direct likenesses from any franchise."
        )
    return style


def generate_titles(client, report_content, num_titles=2, date_label=None):
    """
    Use Gemini to generate multiple YouTube title variations for A/B testing.
    """
    prompt = f"""
    你是一位專業的 YouTube 財經頻道標題撰寫專家。
    根據以下影片文案內容，生成 {num_titles} 個不同風格的 YouTube 標題。

    今日日期：{date_label or datetime.datetime.now().strftime("%m/%d")}

    文案摘要：
    {report_content[:1500]}

    要求：
    1. 只輸出 A/B 兩個標題，不要輸出第三個
    2. 每個標題要有不同的「鉤子」策略（好奇心、緊迫感、數據驅動等）
    3. 標題長度控制在 25-35 個中文字以內
    4. 必須包含今日日期「{date_label or datetime.datetime.now().strftime("%m/%d")}」，禁止自行推算或使用其他日期
    5. 使用 emoji 增加點擊率
    6. 針對台灣投資人

    請直接輸出標題，每行一個，不要編號，不要其他說明文字。
    """

    model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    response = client.models.generate_content(
        model=model_name,
        contents=[prompt],
    )
    
    titles = [line.strip() for line in response.text.strip().split("\n") if line.strip()]
    return titles[:num_titles]


def generate_local_titles(structured_data=None, num_titles=2, date_label=None):
    structured_data = structured_data or {}
    date_label = date_label or datetime.datetime.now().strftime("%m/%d")
    targets = _extract_stock_text(structured_data.get("prediction_targets"), limit=2)
    buys = _extract_stock_text(structured_data.get("safe_havens_bought"), limit=2)
    sells = _extract_stock_text(structured_data.get("heavyweights_dumped"), limit=1)
    validations = _extract_stock_text(structured_data.get("prev_day_picks_result"), limit=2)

    titles = []
    if targets:
        titles.append(f"{date_label} 台股｜{targets[0]}被外資偷點火？")
    if validations:
        titles.append(f"{date_label} 驗收上集點名：{validations[0]}真的動了？")
    if buys and sells:
        titles.append(f"{date_label} 外資換手：買{buys[0]}、砍{sells[0]}")
    if buys:
        titles.append(f"{date_label} 台股資金轉向：{buys[0]}先亮燈")

    fallback = f"{date_label} 台股盤後｜外資資金正在換手"
    titles.append(fallback)

    deduped = []
    for title in titles:
        if title not in deduped:
            deduped.append(title)
    return deduped[:num_titles]


def _read_tail(path, max_chars=3000):
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return content[-max_chars:]
    except Exception:
        return ""


def _load_market_roles():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stock_db_path = os.path.join(base_dir, "data", "stock_db.json")
    try:
        with open(stock_db_path, "r", encoding="utf-8") as f:
            stock_db = json.load(f)
    except Exception:
        return {}
    return {
        sid: (entry.get("market_role") or {}).get("base_role", "")
        for sid, entry in stock_db.items()
        if entry.get("market_role")
    }


def _extract_sid(text):
    match = re.search(r"\b(\d{4,6})\b", str(text))
    return match.group(1) if match else ""


def _extract_stock_text(items, limit=2):
    values = []
    for item in items or []:
        if isinstance(item, dict):
            sid = item.get("id", "")
            name = item.get("name", "")
            text = f"{sid} {name}".strip()
        else:
            text = str(item).split("(")[0].strip()
        if text and text not in values:
            values.append(text)
    return values[:limit]


def _prefer_thumbnail_main_stocks(items, market_roles, limit=2):
    stocks = _extract_stock_text(items, limit=8)
    if not stocks:
        return []
    elastic = []
    others = []
    for stock in stocks:
        sid = _extract_sid(stock)
        role = market_roles.get(sid, "")
        if role == "elastic_attack":
            elastic.append(stock)
        else:
            others.append(stock)
    return (elastic + others)[:limit]


def generate_scenario_briefs(report_content, titles, visual_style, structured_data=None, count=2, date_label=None):
    """
    Generate thumbnail scenario briefs locally.

    This intentionally does not call Gemini/OpenAI. It encodes the channel's thumbnail
    strategy in deterministic rules so the prompt is controlled by the local pipeline.
    """
    structured_data = structured_data or {}
    titles = titles or ["台股今日分析"]
    market_roles = _load_market_roles()
    formal_targets = _extract_stock_text(structured_data.get("prediction_targets"), limit=2)
    buy_focus = _prefer_thumbnail_main_stocks(structured_data.get("safe_havens_bought"), market_roles, limit=2)
    sell_risk = _extract_stock_text(structured_data.get("heavyweights_dumped"), limit=2)
    validations = _extract_stock_text(structured_data.get("prev_day_picks_result"), limit=2)
    divergences = structured_data.get("price_volume_divergence") or []

    scenarios = []

    if formal_targets or buy_focus:
        main = formal_targets or buy_focus
        scenarios.append({
            "scenario_name": "暗流點火",
            "paired_title": titles[0],
            "main_stocks": main,
            "overlay_primary": f"{main[0]} 偷點火？",
            "overlay_secondary": "外資暗流",
            "scene": (
                "市場像一個壓低聲音的資金現場，表面沒有全面爆量，"
                f"但資金正在悄悄集中到 {', '.join(main)}，像一個即將被市場發現的訊號正在升溫。"
            ),
            "emotion": "爆發前、偷買、壓抑後的轉強",
            "visual_metaphor": "外資資金像光流或電流，從陰影處流向少數被點亮的交易票據",
            "avoid": ["不要把方向指標股畫成最大飆股", "不要公司 Logo", "不要圖中文字", "不要固定左右分割"],
        })

    if validations:
        scenarios.append({
            "scenario_name": "驗收現場",
            "paired_title": titles[min(1, len(titles) - 1)],
            "main_stocks": validations,
            "overlay_primary": "上集驗收",
            "overlay_secondary": validations[0] if validations else "昨日點名",
            "scene": (
                "昨日被點名的股票像站上驗收台，市場數據正在被逐項核對；"
                "命中的標的被聚光燈照亮，未成立的標的留在較暗的背景裡。"
            ),
            "emotion": "驗收、反差、可信度建立",
            "visual_metaphor": "交易票據、成交量柱、檢核印章與聚光燈構成一個財經驗收現場",
            "avoid": ["不要像考卷版型", "不要在圖中生成中文字", "不要塞超過兩檔主角"],
        })

    if sell_risk and (buy_focus or formal_targets):
        main = (buy_focus or formal_targets)[:1] + sell_risk[:1]
        scenarios.append({
            "scenario_name": "資金換手",
            "paired_title": titles[0],
            "main_stocks": main,
            "overlay_primary": "資金換手",
            "overlay_secondary": "誰被砍？誰接棒？",
            "scene": (
                "同一個市場裡，一邊是被外資撤出的過熱票據正在降溫，"
                "另一邊是新資金流入的標的開始發亮，形成清楚的換手反轉。"
            ),
            "emotion": "反轉、警戒、資金轉向",
            "visual_metaphor": "冷色退潮與暖色聚焦同時出現，但構圖保持自然像電影劇照",
            "avoid": ["不要做成固定紅綠對半", "不要真實公司 Logo", "不要過度恐嚇"],
        })

    if divergences:
        scenarios.append({
            "scenario_name": "量價異常",
            "paired_title": titles[min(1, len(titles) - 1)],
            "main_stocks": formal_targets or buy_focus,
            "overlay_primary": "量價異常",
            "overlay_secondary": "誰在撐盤？",
            "scene": (
                "交易現場表面看似平靜，但桌面下方有巨大的資金機械正在運轉，"
                "暗示買超與成交量之間的不尋常落差。"
            ),
            "emotion": "懸疑、暗藏訊號、即將揭露",
            "visual_metaphor": "安靜檯面與地下發光管線形成對比，象徵隱形吸籌",
            "avoid": ["不要畫成制式技術分析圖", "不要滿版數字", "不要固定版型"],
        })

    fallback_title = titles[0] if titles else "台股今日分析"
    if not scenarios:
        scenarios.append({
            "scenario_name": "資金轉向",
            "paired_title": fallback_title,
            "main_stocks": [],
            "overlay_primary": "資金轉向",
            "overlay_secondary": "外資在換手",
            "scene": "台股交易室像一場正在改變方向的資金戰情，一邊是退場的過熱標的，一邊是剛被點亮的核心觀察股。",
            "emotion": "緊張、反轉、爆發前",
            "visual_metaphor": "資金像光流從暗處流向新的交易票據",
            "avoid": ["不要固定版型", "不要真實 Logo", "不要在圖中放文字"],
        })

    while len(scenarios) < count:
        scenarios.append({
            "scenario_name": "危機反轉",
            "paired_title": titles[len(scenarios) % len(titles)],
            "main_stocks": formal_targets or buy_focus or validations,
            "overlay_primary": "危機反轉",
            "overlay_secondary": "誰先亮燈？",
            "scene": "市場原本被不確定性壓住，但某個清楚訊號讓資金開始重新定價，畫面像危機解除前一秒。",
            "emotion": "不確定性、反轉、等待確認",
            "visual_metaphor": "陰影中的市場地圖逐步亮起少數關鍵節點",
            "avoid": ["不要固定構圖", "不要簡體字", "不要公司 Logo"],
        })

    return scenarios[:count]


def _build_image_prompt(style_key_or_custom, title, scenario_brief=None):
    """
    Returns the final image prompt string.
    - If style_key_or_custom matches a STYLE_PRESETS key → use that preset.
    - Otherwise treat it as a free-form custom style description (daily override).
    """
    preset = STYLE_PRESETS.get(style_key_or_custom)
    visual_desc = preset["prompt"] if preset else _normalize_custom_style(style_key_or_custom)
    scenario_brief = scenario_brief or {}
    scene = scenario_brief.get("scene") or f"Create a dramatic financial scene for the theme: {title}."
    emotion = scenario_brief.get("emotion") or "dramatic, high tension, clear market reversal energy"
    visual_metaphor = scenario_brief.get("visual_metaphor") or "capital flow and stock market uncertainty shown through cinematic visual symbols"
    main_stocks = scenario_brief.get("main_stocks") or []
    avoid = scenario_brief.get("avoid") or []
    stock_focus = ", ".join(main_stocks) if main_stocks else "the key stock ideas from the paired title"

    return (
        "YouTube thumbnail background image, landscape 16:9 format. "
        "Create a flexible cinematic scene, not a fixed template and not a repeated layout. "
        f"Visual world: {visual_desc} "
        f"Paired title idea: '{title}'. "
        f"Main stock focus for the human editor: {stock_focus}. "
        f"Core situation: {scene} "
        f"Emotional tone: {emotion}. "
        f"Visual metaphor: {visual_metaphor}. "
        "Include original fictional human figures when the visual world benefits from character tension; "
        "use expressive silhouettes, gestures, and staged power dynamics, but no real person likenesses. "
        "Leave a dramatic empty plaque, scroll, palace wall, shadow area, or clean negative space for later "
        "human-added Chinese headline text, but do not force a bottom banner or a fixed left-right composition. "
        "No generated text, no random letters, no random numbers, no company logos, no real person likenesses in the image. "
        f"Avoid: {', '.join(avoid)}. "
        "Ultra high quality, dramatic, readable at small sizes, YouTube finance thumbnail energy."
    )


def save_thumbnail_prompt(style_key_or_custom, title, date_str, output_path, scenario_brief=None):
    """
    Save a single YouTube thumbnail prompt for manual image generation.
    style_key_or_custom: a STYLE_PRESETS key (e.g. "dc_comics") OR a free-form style description.
    """
    image_prompt = _build_image_prompt(style_key_or_custom, title, scenario_brief=scenario_brief)
    scenario_brief = scenario_brief or {}
    content = f"""# YouTube Thumbnail Prompt

Date: {date_str}
Title pairing: {title}
Scenario: {scenario_brief.get("scenario_name", "未命名情境")}

## Manual Overlay Text Suggestions

- Primary: {scenario_brief.get("overlay_primary", title)}
- Secondary: {scenario_brief.get("overlay_secondary", "外資暗流 / 影子主力")}

## Scenario Brief

- Main stocks: {", ".join(scenario_brief.get("main_stocks", [])) or "依標題與報告主角"}
- Scene: {scenario_brief.get("scene", "")}
- Emotion: {scenario_brief.get("emotion", "")}
- Visual metaphor: {scenario_brief.get("visual_metaphor", "")}
- Avoid: {", ".join(scenario_brief.get("avoid", [])) or "固定版型、真實 Logo、圖中文字"}

## Image Prompt

{image_prompt}
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    return image_prompt


def generate_ab_test_thumbnails(gemini_api_key, openai_api_key, report_content, reports_dir,
                                 styles=None, num_titles=2, structured_data=None, run_date=None):
    """
    Generate thumbnail prompts + title pairings for A/B testing.
    With structured_data, this is fully local and does not call Gemini.

    styles 可以是：
      - None              → 讀 THUMBNAIL_STYLE 環境變數；若沒設定則用預設兩種 preset
      - ["dc_comics"]     → 指定 STYLE_PRESETS 中的 key
      - ["午夜霓虹，紫金配色"] → 直接用作自訂 prompt（每天換風格就改這裡）

    Returns:
        dict with 'titles' and 'thumbnails' lists
    """
    if run_date:
        parsed_date = datetime.datetime.strptime(run_date, "%Y-%m-%d")
    else:
        parsed_date = datetime.datetime.now()
    date_str = parsed_date.strftime("%m/%d")
    date_full = parsed_date.strftime("%Y-%m-%d")

    # 優先順序：函數參數 → THUMBNAIL_STYLE 環境變數 → 預設兩種 preset
    if styles is None:
        env_style = os.getenv("THUMBNAIL_STYLE", "").strip()
        if env_style:
            styles = [env_style]
            print(f"\n  🎨 使用今日自訂風格: {env_style[:60]}...")
        else:
            styles = ["dc_comics", "cyberpunk"]

    results = {"titles": [], "thumbnails": []}

    # === Step 1: Generate title pairings ===
    if structured_data:
        print("\n🎯 正在用本機 structured_data 生成封面標題配對（不呼叫 Gemini）...")
        titles = generate_local_titles(structured_data, num_titles=num_titles, date_label=date_str)
    else:
        print("\n🎯 無 structured_data，回退 Gemini 生成 YouTube 標題變體...")
        gemini_client = genai.Client(api_key=gemini_api_key)
        titles = generate_titles(gemini_client, report_content, num_titles, date_label=date_str)
    results["titles"] = titles
    for i, title in enumerate(titles):
        print(f"  📝 標題 {i+1}: {title}")

    # === Step 2: Generate scenario briefs, then save prompts for manual image generation ===
    prompt_count = max(num_titles, len(styles), 2)
    primary_style = styles[0] if styles else "dc_comics"
    print(f"\n🎬 正在生成 {prompt_count} 個情境式封面 brief（避免固定版型）...")
    scenario_briefs = generate_scenario_briefs(
        report_content,
        titles,
        primary_style,
        structured_data=structured_data,
        count=prompt_count,
        date_label=date_str,
    )

    print(f"\n🎨 正在產生 {len(scenario_briefs)} 份情境式縮圖 Prompt（手動生圖用）...")

    for i, scenario in enumerate(scenario_briefs):
        style = styles[i % len(styles)]
        # 判斷是 preset key 還是自訂描述
        preset = STYLE_PRESETS.get(style)
        style_label = preset["name"] if preset else f"自訂風格 {i+1}"

        title = scenario.get("paired_title") or (titles[i % len(titles)] if titles else "台股今日分析")
        short_title = title[:20] + "..." if len(title) > 20 else title

        filename = f"yt_thumbnail_prompt_{date_full}_{i+1}.md"
        output_path = os.path.join(reports_dir, filename)

        print(f"\n  🖼️  縮圖 Prompt {i+1}/{len(scenario_briefs)}: {style_label} / {scenario.get('scenario_name', '情境')}")
        print(f"     主題方向: {short_title}")

        prompt = save_thumbnail_prompt(style, title, date_str, output_path, scenario_brief=scenario)
        print(f"     ✅ Prompt 已儲存: {filename}")
        results["thumbnails"].append({
            "style": style_label,
            "scenario": scenario.get("scenario_name", ""),
            "title": title,
            "path": output_path,
            "prompt": prompt,
        })

    return results


def print_ab_test_summary(results):
    """Print a formatted summary of A/B test options."""
    print(f"\n{'='*60}")
    print(f"  📊 A/B Test 素材總覽")
    print(f"{'='*60}")

    print("\n  📝 標題選項：")
    for i, title in enumerate(results["titles"][:2]):
        print(f"     {chr(65+i)}. {title}")

    print(f"\n  🖼️  縮圖 Prompt 選項：")
    for i, thumb in enumerate(results["thumbnails"]):
        print(f"     {chr(65+i)}. [{thumb['style']} / {thumb.get('scenario', '情境')}] {os.path.basename(thumb['path'])}")
        print(f"        搭配標題: {thumb['title'][:30]}...")

    print(f"\n  💡 建議：選擇 1 個標題 + 1 份 Prompt 手動生圖後上傳，")
    print(f"     48 小時後根據 CTR 數據決定是否替換。")
    print(f"{'='*60}\n")
