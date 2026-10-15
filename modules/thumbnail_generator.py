"""
YouTube Thumbnail Prompt Generator with A/B Testing Support.
Uses Gemini for title generation and writes image prompts for manual thumbnail
generation. The actual image is produced manually outside this script.
"""
import os
import datetime
import json
import re
from modules.gemini_client import default_model_name, generate_text
from modules.title_fact_gate import (
    build_fact_index,
    check_cover,
    check_text,
    choose_hero,
    format_amount,
    format_issue,
    format_pct_abs,
    has_errors,
    index_move_word,
    parse_index_action,
    stock_move_word,
)
from modules.packaging_fatigue import (
    build_fatigue_state,
    is_overlay_fatigued,
    is_title_term_fatigued,
    pick_non_fatigued,
    sanitize_title,
    select_diverse_titles,
    title_contains_fatigued_terms,
)


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
    "war_room": {
        "name": "財經戰情室",
        "prompt": (
            "Taiwan stock market financial war room command center: transparent holographic data wall, "
            "large capital flow arrows in Taiwan market colors (RED = up/buy, GREEN = down/sell) crossing a "
            "Taiwan trading floor, institutional buy/sell beams highlighting one core stock, candlestick "
            "charts with sharp numeric contrast, dark navy and black background with gold and electric-blue "
            "UI accents. Dr. Zhang as calm data commander pointing at the anomaly panel; A-Ming as leek-green "
            "retail investor shocked at phone charts. High-contrast two-line Traditional Chinese headline "
            "space, dramatic but readable, no sports or anime scenery, no franchise IP, no company logos. "
            "Never use US green-up/red-down colors."
        ),
    },
    "war_room_flow": {
        "name": "資金流戰情圖",
        "prompt": (
            "Financial intelligence center with a top-down capital flow map: glowing buy/sell routes moving "
            "between stock nodes, one hero stock lit by a massive foreign-institutional RED buy beam, sell "
            "pressure shown as retreating GREEN streams, volume anomaly as pulsing radar rings. Clean "
            "data-dashboard aesthetic, not an Excel screenshot, not cluttered text. Dr. Zhang traces the flow "
            "path on a glass command table; A-Ming watches the hero stock light up on his phone. Dark "
            "background, gold numbers, Taiwan market colors (red=up, green=down), strong numeric contrast, "
            "original characters only. Never use US green-up/red-down colors."
        ),
    },
}

# Cover A/B H1 arm B (data/ab_test_plan.json): the verified number itself is the picture.
STYLE_PRESETS["war_room_board"] = {
    "name": "戰情室數據大字牌",
    "prompt": (
        "Taiwan stock market war-room DATA BOARD: one giant illuminated number plaque is the main subject, "
        "mounted on a dark navy command-center wall; thin capital-flow lines, small candlestick panels and "
        "radar grids are background texture only. No metaphor scene and no story props — the verified number "
        "and the stock name are the visual; the recurring characters (if any) stand beside the board. "
        "Taiwan market colors (RED = up/buy, GREEN = down/sell), gold and "
        "electric-blue accents, original design only, no franchise IP, no company logos. "
        "Never use US green-up/red-down colors."
    ),
}

DEFAULT_THUMBNAIL_STYLES = ["war_room", "war_room_flow"]
DEFAULT_VISUAL_THEME_LABEL = "財經戰情室"


def _is_jojo_theme(text):
    text = str(text or "").lower()
    return any(keyword in text for keyword in ["jojo", "ジョジョ", "ジョジョの奇妙", "jo jo"])


def _is_fifa_theme(text):
    text = str(text or "").lower()
    return any(keyword in text for keyword in ["fifa", "足球", "football", "世界盃", "世界杯", "世足"])


def _is_jojo_fifa_theme(text):
    return _is_jojo_theme(text) and _is_fifa_theme(text)


def _is_sports_anime_theme(text):
    normalized = str(text or "").lower()
    keywords = [
        "fifa", "足球", "football", "世界盃", "世界杯", "世足",
        "jojo", "ジョジョ", "忍者龜", "ninja turtle", "tmnt",
        "甄嬛", "漫威", "marvel", "七龍珠", "dragon ball",
        "傳說對決", "aov", "moba", "布袋戲", "武俠", "宮廷",
        "超級英雄", "superhero", "動漫", "anime", "manga",
        "格鬥", "電競", "nba", "籃球", "棒球",
    ]
    return any(keyword in normalized for keyword in keywords)


def _resolve_thumbnail_styles(styles=None):
    if styles is None:
        env_style = os.getenv("THUMBNAIL_STYLE", "").strip()
        if env_style:
            styles = [env_style]
        else:
            styles = list(DEFAULT_THUMBNAIL_STYLES)

    resolved = []
    for style in styles:
        key = str(style or "").strip()
        if not key:
            continue
        if key in STYLE_PRESETS:
            resolved.append(key)
            continue
        if key.rstrip("。") == DEFAULT_VISUAL_THEME_LABEL:
            # The documented /2 default (「財經戰情室」) uses the two war-room presets.
            resolved.extend(DEFAULT_THUMBNAIL_STYLES)
            continue
        preset_key = next((k for k, v in STYLE_PRESETS.items() if v.get("name") == key.rstrip("。")), None)
        if preset_key:
            resolved.append(preset_key)
            continue
        if _is_sports_anime_theme(key):
            # Explicit experiment markers or env override keep the requested theme.
            allow_experiment = (
                "實驗" in key
                or os.getenv("ALLOW_SPORTS_ANIME_THEME", "").strip().lower()
                in {"1", "true", "yes", "y", "on"}
            )
            if allow_experiment:
                print(
                    "  ⚠️  視覺主題屬體育/動漫類，但本次標記為實驗，"
                    "允許通過（勿當日常預設；記得記 站外/實驗 log）。"
                )
                resolved.append(key)
                continue
            print(
                "  ⚠️  視覺主題屬體育/動漫類；2026-07-07 起預設暫停實驗，"
                f"改用 {DEFAULT_VISUAL_THEME_LABEL}。"
            )
            resolved.extend(DEFAULT_THUMBNAIL_STYLES)
            break
        resolved.append(key)

    if not resolved:
        resolved = list(DEFAULT_THUMBNAIL_STYLES)
    return resolved[:2] if len(resolved) > 2 else resolved


# 「A兩行股名+金額，B短句大字」-style cover-text notes inside a theme: the cover text of
# each A/B arm is set by the A/B hypothesis, so the note never reaches an image prompt.
from modules.packaging_bans import strip_cover_text_note  # noqa: E402


def _normalize_custom_style(style_key_or_custom):
    # Image prompts only get the positive scene: 「禁止…」/title-rule clauses (and the stock
    # names / scene nouns inside them) are stripped; they live in data/packaging_bans.json.
    from modules.packaging_bans import scene_only

    style = strip_cover_text_note(scene_only(str(style_key_or_custom or "").strip()).rstrip("。")).rstrip("。")
    normalized = style.lower()
    if _is_jojo_fifa_theme(normalized):
        return (
            "Original Japanese manga battle-sports crossover aesthetic combining exaggerated shonen pose drama "
            "with world-class football tournament energy: bold ink outlines, dramatic freeze-frame tension, "
            "speed-line impact bursts, stylized heroic silhouettes, high-contrast color blocking, "
            "stadium floodlights, tactical chalkboard overlays, glowing scoreboards, capital flow visualized as "
            "passing lanes and counterattacks, sell pressure as defensive pressing, breakout stocks as strikers "
            "breaking through the line. Dr. Zhang as a calm tactical coach; A-Ming as a nervous fan-player with "
            "leek-green accents. No JoJo's Bizarre Adventure characters, no FIFA/World Cup official logos, "
            "no real national team crests, no real player likenesses."
        )
    if _is_jojo_theme(normalized):
        return (
            "Original Japanese manga battle-drama aesthetic with bold ink outlines, dramatic freeze-frame poses, "
            "speed-line impact bursts, stylized heroic silhouettes, high-contrast color blocking, and comic "
            "onomatopoeia energy. Translate stock-market clashes into exaggerated stand-battle tension without "
            "copying any copyrighted manga characters, names, logos, or direct likenesses."
        )
    if any(keyword in normalized for keyword in ["fifa", "足球", "football", "世界盃", "世界杯", "世足"]):
        return (
            "Original world-class football tournament cinematic aesthetic: stadium floodlights, tactical boards, "
            "scoreboards, passing lanes and counterattack motion, sell pressure as defensive pressing, "
            "strong stocks as strikers breaking through the line. Dr. Zhang as calm tactical coach; "
            "A-Ming as nervous fan-player with leek-green accents. "
            "No FIFA/World Cup official logos, no real national team crests, no real player likenesses."
        )
    if any(keyword in normalized for keyword in ["忍者龜", "ninja turtle", "tmnt"]):
        return (
            "Teenage Mutant Ninja Turtles-inspired ORIGINAL sewer-ninja finance parody aesthetic: "
            "underground sewer dojo trading floor, round manhole covers used as glowing stock dashboards, "
            "neon green ooze light, brick tunnel walls, metal pipes, skateboard-graffiti texture, "
            "pizza-box shaped chart props, colored mask accents, shell-like armor silhouettes, "
            "red and green candlestick arrows exploding like comic action effects. "
            "No actual TMNT characters, no names, no franchise logos, no direct likenesses."
        )
    if any(keyword in normalized for keyword in ["七龍珠", "dragon ball", "dragonball", "熱血戰鬥動漫", "變身光柱"]):
        return (
            "MUST look like classic 90s Japanese shonen battle anime at first glance (Dragon Ball Z era energy, "
            "NOT cyberpunk, NOT holographic finance HUD, NOT war-room map, NOT dark sci-fi UI). "
            "Signature look: rocky barren arena with cracked ground and floating debris; huge spiky golden-orange "
            "and blue-white power auras wrapping fighters; vertical transformation light pillars shooting into a "
            "stormy sky; thick black cel-shaded outlines; radial speed lines; concentric shockwave rings; dust "
            "and energy sparks; freeze-frame impact pose; saturated high-contrast colors. "
            "Stock ideas appear as glowing power orbs / aura shields / energy pillars labeled with stock names, "
            "NOT as digital maps or stock dashboards. Candlesticks may appear as distant energy trails only. "
            "TAIWAN COLORS: bullish/up/buy = RED auras and RED candles; bearish/down/sell = GREEN pressure beams "
            "(never US green-up/red-down). "
            "Dr. Zhang = original mentor fighter with black round glasses, short neat hair (NOT spiky anime hero "
            "hair), dark training gi or battle jacket, calm stance with tablet as energy tablet prop. "
            "A-Ming = original leek-haired rookie (small leek leaf on head), green scarf, shocked face, phone. "
            "Characters should feel large and dynamic, not tiny UI decorations. "
            "STRICT ORIGINAL: no Goku, Vegeta, Frieza, Shenron, dragon balls, orange gi, blue spiky hair hero, "
            "Saiyan armor, signature named techniques, logos, or direct likenesses."
        )
    if any(
        keyword in normalized
        for keyword in ["城市英雄", "蛛網", "spider-adjacent", "web-slinger", "城市資金網"]
    ):
        return (
            "ORIGINAL urban comic-book superhero finance aesthetic (cultural comic energy only — NOT Spider-Man, "
            "NOT Marvel, NOT Sony). Signature look at first glance: night city skyline with glowing skyscrapers, "
            "capital flows drawn as luminous web-like energy strands across towers (abstract data webs, not "
            "character webs), bold action speed lines, freeze-frame impact, thick black comic outlines, "
            "high-contrast red/blue accent lighting that is NOT a red-and-blue superhero suit. "
            "Stock ideas = glowing city nodes / tower beacons / web-beam hubs labeled with stock names. "
            "TAIWAN COLORS: up/buy = RED; down/sell = GREEN (never US green-up/red-down). "
            "Dr. Zhang = calm intel commander with black round glasses, neat short hair, dark tactical jacket + tablet. "
            "A-Ming = leek-haired rookie hero (small leek leaf), green accents, shocked expression, phone. "
            "STRICT: no spider mask, no red-blue bodysuit, no spider emblem, no movie logos, no franchise characters "
            "or poses copied from Spider-Man films."
        )
    if any(keyword in normalized for keyword in ["甄嬛傳", "甄嬛传", "後宮", "后宫", "宮鬥", "宫斗"]):
        return (
            "Original imperial palace intrigue drama aesthetic inspired by Qing dynasty court politics: "
            "ornate palace corridors, silk curtains, lacquered red columns, golden throne-room lighting, "
            "jade hairpin and embroidered robe textures, secret memorial scrolls, candlelit strategy tables, "
            "subtle power struggle atmosphere, elegant but dangerous court tension. Include original fictional "
            "channel mascots reimagined in ancient palace attire: a wise professor strategist in scholar-official "
            "robes reading market memorials, and a small leek investor character dressed like a nervous palace "
            "attendant holding trade slips. Add cautious ministers and shadowy faction messengers watching the "
            "capital-flow signal table if useful. "
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


def generate_titles(api_key_or_client, report_content, num_titles=2, date_label=None):
    """
    Use Gemini to generate multiple YouTube title variations for A/B testing.
    Accepts either an API key string or a google.genai Client (legacy callers).
    """
    if isinstance(api_key_or_client, str):
        api_key = api_key_or_client
    else:
        api_key = os.getenv("GEMINI_API_KEY") or ""

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

    text = generate_text(
        api_key,
        prompt,
        task_name="thumbnail_title_generation",
        model_name=default_model_name(),
    )
    titles = [line.strip() for line in text.strip().split("\n") if line.strip()]
    return titles[:num_titles]


def _editorial_frame_key(structured_data):
    frame = structured_data.get("editorial_frame") or {}
    if isinstance(frame, dict):
        return frame.get("frame") or frame.get("label") or ""
    return ""


def _index_headline(structured_data):
    """
    Short unsigned index move like '1783點' from the local index_action. Free
    text without a point number yields "" (never a text[:12] fragment, finding 157).
    """
    points = parse_index_action(structured_data.get("index_action")).get("points")
    if points is None:
        return ""
    value = abs(points)
    if value >= 100:
        return f"{int(round(value))}點"
    return f"{value:g}點"


def _index_pct(structured_data):
    return parse_index_action(structured_data.get("index_action")).get("pct")


def _index_direction_word(structured_data):
    """漲/跌/走 from the local index_action (full- or half-width brackets)."""
    parsed = parse_index_action(structured_data.get("index_action"))
    pct = parsed.get("pct")
    points = parsed.get("points")
    value = pct if pct is not None else points
    if value is None:
        return "走"
    if value > 0:
        return "漲"
    if value < 0:
        return "跌"
    return "走"


def _lead_foreign_buy(structured_data):
    items = structured_data.get("safe_havens_bought") or []
    if not items:
        return "", ""
    top = str(items[0])
    stock = _extract_stock_text([top], limit=1)
    stock_text = stock[0] if stock else ""
    amount = _first_amount_text(top)
    return stock_text, amount


def _lead_foreign_sell(structured_data):
    items = structured_data.get("heavyweights_dumped") or []
    if not items:
        return "", ""
    top = str(items[0])
    stock = _extract_stock_text([top], limit=1)
    stock_text = stock[0] if stock else ""
    amount = _first_amount_text(top)
    return stock_text, amount


def _stock_fact(facts, stock_text):
    """Facts row for a 「1303 南亞」 / 「南亞」 label by exact ticker or name (南亞 ≠ 南亞科)."""
    text = str(stock_text or "").strip()
    match = re.match(r"^(\d{4,6}[A-Z]?)\s*(.*)$", text)
    ticker, name = (match.group(1), match.group(2)) if match else ("", text.split("(")[0].strip())
    key = facts.lookup(ticker, name)
    return key, (facts.fact(key) if key else None)


def _build_local_title_candidates(structured_data, date_label, fatigue_state):
    """
    Local fallback titles (BLOCK opener days / Gemini failure). Every number and
    direction word comes from the stock's own local facts; a candidate that still
    contradicts them is dropped by the fact gate (findings 45, 46, 120, 122, 157).
    """
    facts = build_fact_index(structured_data)
    targets = _extract_stock_text(structured_data.get("prediction_targets"), limit=2)
    buys = _extract_stock_text(structured_data.get("safe_havens_bought"), limit=2)
    sells = _extract_stock_text(structured_data.get("heavyweights_dumped"), limit=1)
    validations = _extract_stock_text(structured_data.get("prev_day_picks_result"), limit=2)
    divergences = [str(item) for item in structured_data.get("price_volume_divergence") or []]
    frame = _editorial_frame_key(structured_data)
    index_move = _index_headline(structured_data)
    index_pct = _index_pct(structured_data)
    main_buy = buys[0] if buys else (targets[0] if targets else "")
    main_sell = sells[0] if sells else ""
    main_target = targets[0] if targets else main_buy
    lead_buy_stock, lead_buy_amount = _lead_foreign_buy(structured_data)
    lead_sell_stock, lead_sell_amount = _lead_foreign_sell(structured_data)
    buy_amount = lead_buy_amount
    sell_amount = lead_sell_amount
    index_dir = _index_direction_word(structured_data)
    index_word = index_move_word(index_pct)
    _main_key, main_row = _stock_fact(facts, main_buy)
    main_pct = (main_row or {}).get("pct_change")

    candidates = []

    if lead_sell_amount and lead_sell_stock:
        if index_move and index_dir in {"漲", "跌"}:
            candidates.append(
                f"外資狂砍{_stock_name_only(lead_sell_stock)}{lead_sell_amount}！大盤{index_dir}{index_move}誰在接？"
            )
        else:
            candidates.append(
                f"外資狂砍{_stock_name_only(lead_sell_stock)}{lead_sell_amount}！資金往哪裡跑？"
            )
        # 「利多竟成提款」 only when the radar has a fresh event (finding 46).
        if _has_fresh_event(structured_data):
            candidates.append(
                f"外資狂砍{_stock_name_only(lead_sell_stock)}{lead_sell_amount}！利多竟成提款？"
            )

    if lead_buy_amount and lead_buy_stock and index_move and index_dir == "跌":
        candidates.append(
            f"大盤{index_dir}{index_move}！外資卻買{_stock_name_only(lead_buy_stock)}{lead_buy_amount}？"
        )

    for item in divergences:
        key, row = _stock_fact(facts, _extract_stock_text_from_sentence(item))
        if not row:
            continue
        name = row.get("name") or _stock_name_only(_extract_stock_text_from_sentence(item))
        amount = row.get("foreign_amount")
        if _is_off_board_accumulation(item):
            # Only a stock foreigners really bought and that really missed the volume board.
            if amount is not None and amount > 0 and row.get("off_rank") is True:
                candidates.append(f"外資狂砸{format_amount(amount)}買{name}，成交量卻沒進榜？")
                candidates.append(f"{format_amount(amount)}買{name}，為何沒進量榜？")
            break
        if re.search(r"跌出|未進|沒進", item):
            continue
        if "天量" in item or ("成交量" in item and re.search(r"賣超|下跌|派發|慘跌", item)):
            pct = row.get("pct_change")
            rank = row.get("volume_rank")
            if amount is not None and amount < 0 and pct is not None and pct < 0 and rank and rank <= 3:
                volume_label = _volume_wanzhang_text(item) or "爆量"
                candidates.append(f"{name}{volume_label}成交王卻跌！外資賣{format_amount(amount)}誰在跑？")
                candidates.append(f"{name}爆量卻跌，法人方向一致嗎？")
            break

    if lead_sell_amount and lead_sell_stock and lead_buy_amount and lead_buy_stock:
        candidates.append(
            f"外資賣{lead_sell_stock}{lead_sell_amount}，卻反手{lead_buy_amount}買{_stock_name_only(lead_buy_stock)}？"
        )

    if lead_buy_amount and lead_buy_stock:
        _lead_key, lead_row = _stock_fact(facts, lead_buy_stock)
        if (lead_row or {}).get("off_rank") is True:
            candidates.append(f"外資狂砸{lead_buy_amount}買{_stock_name_only(lead_buy_stock)}，成交量卻沒進榜？")
        candidates.append(f"外資買超第一{_stock_name_only(lead_buy_stock)}{lead_buy_amount}，量價一致嗎？")

    if lead_sell_amount and lead_sell_stock:
        candidates.append(f"外資狂砍{_stock_name_only(lead_sell_stock)}{lead_sell_amount}，誰在接？")
        candidates.append(f"{lead_sell_amount}賣壓來了，資金轉向哪裡？")

    # Only when editorial frame explicitly leads with validation (not every day).
    ef = structured_data.get("editorial_frame") or {}
    validation_placement = str(ef.get("validation_placement") or "").lower()
    if validations and (
        validation_placement == "lead" or frame in {"validation_day", "驗收日"}
    ) and validation_placement != "omit":
        validation_stock = validations[0]
        if lead_buy_amount and lead_buy_stock:
            candidates.append(
                f"外資狂砸{lead_buy_amount}買{_stock_name_only(lead_buy_stock)}，昨天點名{validation_stock}怎麼走？"
            )
        else:
            candidates.append(f"昨天點名{validation_stock}真的動了？今天法人數據怎麼說？")

    if index_move and main_buy and index_word:
        # Wording by the actual index percent: -0.02% is not 重挫, +0.22% is not 狂飆.
        if index_dir == "跌":
            if main_pct is not None and main_pct > 0:
                candidates.append(f"{date_label} 台股{index_word}{index_move}，{main_buy}為什麼還強？")
                candidates.append(f"{date_label} 台股｜{index_word}{index_move}，{main_buy}逆勢亮燈？")
            else:
                candidates.append(f"{date_label} 台股{index_word}{index_move}，{main_buy}先看哪裡？")
        elif index_dir == "漲":
            candidates.append(f"{date_label} 台股{index_word}{index_move}，{main_buy}還能追嗎？")
            candidates.append(f"{date_label} 台股｜{index_word}{index_move}，{main_buy}是真行情？")
        else:
            candidates.append(f"{date_label} 台股平盤，{main_buy}先看哪裡？")

    if buy_amount and main_buy:
        candidates.append(f"{date_label} 台股｜外資買{buy_amount}，{main_buy}先動？")
        candidates.append(f"{date_label} 台股｜{main_buy}外資買{buy_amount}，量價一致嗎？")

    if sell_amount and main_sell and main_buy:
        candidates.append(f"{date_label} 台股｜外資賣{main_sell}{sell_amount}買{main_buy}{buy_amount}，先看誰？")
        candidates.append(f"{date_label} 台股｜{main_sell}賣壓重，資金轉向{main_buy}？")

    if main_target:
        candidates.append(f"{date_label} 台股｜{main_target}明日先看哪個價位？")
        candidates.append(f"{date_label} 台股｜{main_target}是短線觀察還是假突破？")

    if frame in {"major_event_day", "重大事件日"}:
        if _has_fresh_event(structured_data) and main_target:
            candidates.append(f"{date_label} 台股｜{main_target}先看哪個條件？")

    candidates.append(f"{date_label} 台股盤後｜明天先盯這 2 個條件")

    kept = []
    for candidate in candidates:
        issues = check_text(candidate, where="本機候選標題", facts=facts)
        if has_errors(issues):
            print(f"  ✂️  [事實對帳] 捨棄本機候選標題：{candidate}（{format_issue(next(i for i in issues if i['level'] == 'error'))}）")
            continue
        kept.append(candidate)
    return select_diverse_titles(kept, num_titles=len(kept), fatigue_state=fatigue_state)


def _has_fresh_event(structured_data):
    events = structured_data.get("supply_chain_event_radar") or structured_data.get("macro_events") or []
    return any(
        isinstance(row, dict) and row.get("freshness") == "new" and int(row.get("impact_score") or 0) >= 60
        for row in events or []
    )


def generate_local_titles(structured_data=None, num_titles=2, date_label=None, channel_story=""):
    structured_data = structured_data or {}
    date_label = date_label or datetime.datetime.now().strftime("%m/%d")
    fatigue_state = build_fatigue_state(channel_story=channel_story)
    candidates = _build_local_title_candidates(structured_data, date_label, fatigue_state)
    picked = select_diverse_titles(candidates, num_titles=num_titles, fatigue_state=fatigue_state)
    return picked or candidates[:num_titles]


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


def _normalize_stock_name(name):
    return re.sub(r"[\*＊]", "", str(name or "").strip())


def _lookup_ticker_by_name(name):
    name = _normalize_stock_name(name)
    if not name:
        return ""
    for sid, entry in _load_stock_db().items():
        db_name = _normalize_stock_name(entry.get("name"))
        if db_name == name:
            return sid
    return ""


def _load_stock_db():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stock_db_path = os.path.join(base_dir, "data", "stock_db.json")
    try:
        with open(stock_db_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _extract_stock_text_from_sentence(text):
    text = str(text)
    match = re.search(r"\b(\d{4,6})\s+([\w\u4e00-\u9fff\*\-]+)", text)
    if match:
        return f"{match.group(1)} {_normalize_stock_name(match.group(2))}".strip()
    match = re.search(r"^([\w\u4e00-\u9fff\*\-]+)\s*[（(]", text.strip())
    if match:
        name = _normalize_stock_name(match.group(1))
        ticker = _lookup_ticker_by_name(name)
        if ticker:
            return f"{ticker} {name}"
        return name
    match = re.search(r"^([\w\u4e00-\u9fff\*\-]+)[：:]", text.strip())
    if match:
        name = _normalize_stock_name(match.group(1))
        ticker = _lookup_ticker_by_name(name)
        if ticker:
            return f"{ticker} {name}"
        return name
    return ""


def _is_off_board_accumulation(text):
    text = str(text)
    return any(key in text for key in [
        "未進成交量前",
        "未入成交量前",
        "成交量未進",
        "成交量未入",
        "未進前20",
        "未入前20",
        "未進成交量榜",
        "未入成交量榜",
        "沒進成交量",
        "榜單外",
        "查無此人",
    ])


def _prefer_thumbnail_main_stocks(items, market_roles, limit=2, structured_data=None):
    """Prefer high title_priority / elastic_attack; deprioritize direction indicators."""
    ranking = (structured_data or {}).get("focus_ranking") or []
    if ranking:
        # Labels come from ticker + name, never source_text (「國巨* (+36.6億)」 or a
        # whole divergence sentence, finding 156); a 「被資金點亮」 buy focus must be
        # a stock foreigners bought (local facts), not a sell-side / divergence row.
        facts = build_fact_index(structured_data)
        out = []
        for row in ranking:
            if row.get("base_role") == "direction_indicator":
                continue
            key = facts.lookup(row.get("ticker"), row.get("name"))
            fact = facts.fact(key) if key else None
            amount = (fact or {}).get("foreign_amount")
            if fact is not None and amount is not None and amount <= 0:
                continue
            text = facts.label(key) if key else f"{row.get('ticker', '')} {row.get('name', '')}".strip()
            if text and text not in out:
                out.append(text)
            if len(out) >= limit:
                return out[:limit]
        if out:
            return out[:limit]

    stocks = _extract_stock_text(items, limit=8)
    if not stocks:
        return []
    elastic = []
    mid = []
    direction = []
    for stock in stocks:
        sid = _extract_sid(stock)
        role = market_roles.get(sid, "")
        if role == "elastic_attack":
            elastic.append(stock)
        elif role == "direction_indicator":
            direction.append(stock)
        else:
            mid.append(stock)
    return (elastic + mid + direction)[:limit]


def _first_amount_text(text):
    match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*億", str(text))
    if not match:
        return ""
    amount = match.group(1).lstrip("+-")
    return f"{amount}億"


def _volume_wanzhang_text(text):
    match = re.search(r"(\d+)\s*萬張", str(text))
    if match:
        return f"{match.group(1)}萬張"
    return ""


def _stock_name_only(stock_text):
    text = str(stock_text or "").strip()
    return re.sub(r"^\d{4,6}\s+", "", text).strip() or text


def _stock_overlay_primary(stock_text, fallback="主角股"):
    text = str(stock_text or "").strip()
    return text or fallback


def parse_title_hero(title, structured_data=None):
    """
    The stock a title opens with, with ITS local facts (findings 43/44/121/159):
    exact name / ticker match (南亞 ≠ 南亞科), verb from the sign of its own
    foreign amount, amount from its own facts — never keywords in the title,
    never a default 「買」, never the first 「億」 in the title or a list.
    """
    facts = build_fact_index(structured_data or {})
    hero = choose_hero([title], facts=facts)
    if not hero:
        return {"ticker": "", "name": "", "amount": "", "verb": ""}
    return hero


def _hero_label(hero):
    return hero.get("label") or " ".join(p for p in (hero.get("ticker"), hero.get("name")) if p).strip()


def _control_overlay(hero):
    """A: 「1303 南亞」 / 「外資買55.3億」 — all from the hero's own facts."""
    name = hero.get("name") or ""
    primary = _hero_label(hero) or name or "主角股"
    verb = hero.get("verb") or ""
    amount = hero.get("amount") or ""
    pct = hero.get("pct_change")
    if verb and amount:
        secondary = f"外資{verb}{amount}"
    elif pct is not None:
        word = stock_move_word(pct)
        secondary = f"{word}！" if word in {"漲停", "跌停", "平盤"} else f"{word}{format_pct_abs(pct)}"
    else:
        secondary = "看這檔"
    return primary, secondary


def _fragment_overlay(hero):
    """B: same hero, same number, fragment syntax: 「買55.3億」 / 「噴9.4%！」."""
    name = hero.get("name") or hero.get("ticker") or "這檔"
    verb = hero.get("verb") or ""
    amount = hero.get("amount") or ""
    pct = hero.get("pct_change")
    word = stock_move_word(pct) if pct is not None else ""
    if verb and amount:
        if not word:
            return f"{verb}{amount}", f"{name}?"
        against = (verb == "買" and pct < 0) or (verb == "砍" and pct > 0)
        if word == "平盤":
            return f"{verb}{amount}", ("卻守平盤?" if verb == "砍" else "卻平盤?")
        if word in {"漲停", "跌停"}:
            return f"{verb}{amount}", (f"卻{word}?" if against else f"{word}！")
        text = f"{word}{format_pct_abs(pct)}"
        return f"{verb}{amount}", (f"卻{text}?" if against else f"{text}！")
    if word:
        return name, (f"{word}！" if word in {"漲停", "跌停", "平盤"} else f"{word}{format_pct_abs(pct)}")
    return name, "看這檔?"


def _question_overlay(hero, fallback_primary="", fallback_secondary=""):
    """A/B text arm B: same stock, same number, as a question (「1303 南亞」/「外資為何買55.3億？」)."""
    primary = _hero_label(hero) or hero.get("name") or fallback_primary or "主角股"
    verb = hero.get("verb") or ""
    amount = hero.get("amount") or ""
    pct = hero.get("pct_change")
    if verb and amount:
        return primary, f"外資為何{verb}{amount}？"
    if pct is not None:
        word = stock_move_word(pct)
        move = word if word in {"漲停", "跌停", "平盤"} else f"{word}{format_pct_abs(pct)}"
        return primary, f"為何{move}？"
    secondary = re.sub(r"[?？!！。]+$", "", str(fallback_secondary or "").strip()) or "看這檔"
    return (fallback_primary or primary), f"{secondary}？"


def _hero_scene(hero):
    label = _hero_label(hero) or "主角股"
    verb = hero.get("verb")
    amount = hero.get("amount")
    pct = hero.get("pct_change")
    bits = []
    if verb == "買" and amount:
        bits.append(f"{label} 被外資 {amount} 的紅色資金光束點亮")
    elif verb == "砍" and amount:
        bits.append(f"{label} 的票據被外資抽走 {amount}，綠色賣壓往外流")
    else:
        bits.append(f"{label} 站在聚光燈中央")
    if pct is not None:
        word = stock_move_word(pct)
        if word in {"漲停", "跌停", "平盤"}:
            bits.append(f"股價{word}")
        else:
            bits.append(f"股價{word}{format_pct_abs(pct)}")
    return "，".join(bits) + f"；{hero.get('name') or label} 是畫面唯一主角，其他股票只當背景。"


def _same_stock(label, hero):
    text = str(label or "")
    ticker = hero.get("ticker") or ""
    name = hero.get("name") or ""
    if ticker and re.search(rf"(?<!\d){re.escape(ticker)}(?!\d)", text):
        return True
    bare = re.sub(r"^\d{4,6}[A-Z]?\s*", "", text).split("(")[0].strip().strip("*＊")
    return bool(name) and bare == name.strip("*＊")


_FLOW_WORD = {"買": "買超", "砍": "賣超"}
_BUY_FLOW_WORDS = re.compile(r"買超|買進|買盤|吸籌|掃貨|建倉|紅色資金")
_SELL_FLOW_WORDS = re.compile(r"賣超|賣壓|提款|出貨|倒貨|抽走|抽離|撤出")


def _scene_contradicts_hero(scene, hero):
    """A scene that only speaks of the opposite flow (台積電 -168億 under 「買超」)."""
    verb = hero.get("verb")
    text = str(scene or "")
    if verb == "砍":
        return bool(_BUY_FLOW_WORDS.search(text)) and not _SELL_FLOW_WORDS.search(text)
    if verb == "買":
        return bool(_SELL_FLOW_WORDS.search(text)) and not _BUY_FLOW_WORDS.search(text)
    return False


def _anchor_scenario_to_hero(row, hero):
    """Scene / main_stocks follow the chosen hero (finding 48)."""
    label = _hero_label(hero)
    if not label:
        return row
    others = [stock for stock in (row.get("main_stocks") or []) if stock and not _same_stock(stock, hero)]
    others = [re.sub(r"\s*[（(][^)）]*[)）]", "", str(stock)).strip() for stock in others]
    row["main_stocks"] = [label] + [stock for stock in others if stock][:1]
    name = hero.get("name") or label
    scene = str(row.get("scene") or "")
    if name not in scene or _scene_contradicts_hero(scene, hero):
        row["scene"] = _hero_scene(hero)
    metaphor = str(row.get("visual_metaphor") or "")
    if metaphor and name not in metaphor and re.search(r"[\u4e00-\u9fff]{2,}\*?\s*(?:票據|\()", metaphor):
        row["visual_metaphor"] = (
            "主角票據被紅色買盤光束點亮，背景是退場賣壓形成的市場風暴"
            if hero.get("verb") == "買"
            else "主角票據被綠色賣壓抽離，背景仍有零星承接光點"
        )
    return row


def align_ab_overlays(scenarios, titles, structured_data=None, hero=None):
    """A = complete 股名+金額; B = fragment of the same hero. Same stock, same number."""
    titles = titles or []
    if hero is None:
        hero = choose_hero(titles, structured_data or {})
    if not hero or not (hero.get("name") or hero.get("ticker")):
        return scenarios
    out = []
    for index, scenario in enumerate(scenarios or []):
        row = dict(scenario)
        if index == 0:
            primary, secondary = _control_overlay(hero)
        else:
            primary, secondary = _fragment_overlay(hero)
            avoid = list(row.get("avoid") or [])
            avoid.extend(
                [
                    "不要驗收章",
                    "不要 VERIFIED",
                    "不要上集點名當封面大字",
                    "不要考卷／核取章版型",
                    "不要把故事寫成完整句當封面字",
                ]
            )
            row["avoid"] = avoid
            if row.get("family") == "validation" or row.get("scenario_name") == "驗收現場":
                row["scene"] = (
                    f"{hero.get('name') or hero.get('ticker') or '主角股'} 的數字要一眼可讀；"
                    "場景當背景，不要做成核對台或驗收儀式。"
                )
        row["overlay_primary"] = primary
        row["overlay_secondary"] = secondary
        row["hero"] = hero
        out.append(_anchor_scenario_to_hero(row, hero))
    return out


def _buy_amount_for_stock(stock_text, structured_data):
    """That stock's own foreign buy amount (exact ticker/name); "" when unknown or not a buy."""
    _key, row = _stock_fact(build_fact_index(structured_data), stock_text)
    amount = (row or {}).get("foreign_amount")
    return format_amount(amount) if amount is not None and amount > 0 else ""


def _sell_amount_for_stock(stock_text, structured_data):
    """That stock's own foreign sell amount (exact ticker/name); "" when unknown or not a sell."""
    _key, row = _stock_fact(build_fact_index(structured_data), stock_text)
    amount = (row or {}).get("foreign_amount")
    return format_amount(amount) if amount is not None and amount < 0 else ""


def _scenario_brief(copy, titles, main_stocks, paired_index=0):
    return {
        "scenario_name": copy["scenario_name"],
        "family": copy.get("family", "other"),
        "paired_title": titles[min(paired_index, len(titles) - 1)] if titles else "台股今日分析",
        "main_stocks": copy.get("main_stocks") or main_stocks,
        "overlay_primary": copy["overlay_primary"],
        "overlay_secondary": copy["overlay_secondary"],
        "scene": copy["scene"],
        "emotion": copy["emotion"],
        "visual_metaphor": copy["visual_metaphor"],
        "avoid": copy["avoid"],
        "priority": copy.get("priority", 50),
        "has_variant": bool(copy.get("variant")),
        "variant": copy.get("variant"),
    }


def _top_listed(facts, structured_data, key_name, exclude_key=None):
    """(key, label, amount_text) of the first leaderboard stock that is not the hero."""
    for raw in structured_data.get(key_name) or []:
        labels = _extract_stock_text([raw], limit=1)
        key, row = _stock_fact(facts, labels[0] if labels else "")
        if not row or key == exclude_key:
            continue
        return key, facts.label(key), format_amount(row.get("foreign_amount"))
    return None, "", ""


def _scenario_counterpart(facts, structured_data, titles, hero, key_name):
    """
    (key, label, amount_text) of the 資金換手 counterpart: the opposite-side stock title A
    names with a local amount (the same pick as the /2 auto theme, so the theme and the
    cover's Core situation tell one story), else the leaderboard's first non-hero stock.
    """
    if hero and hero.get("foreign_amount"):
        try:
            from modules.visual_scene import title_a_counterpart

            named = title_a_counterpart(facts, titles, hero)
        except Exception:
            named = None
        if named:
            key, _row, amount = named
            return key, facts.label(key), amount
    return _top_listed(facts, structured_data, key_name, exclude_key=(hero or {}).get("key"))


def _collect_scenario_candidates(structured_data, titles, hero=None):
    """
    Scenario cards built around the chosen hero (finding 48): main_name, scene and
    main_stocks start from the hero, every amount is the stock's own local fact
    (finding 45), and each family only appears when its facts hold — 榜外吸籌 needs
    a real off-board buy, 天量出貨 a top-3 volume sell, 大盤 scenes |pct| ≥ 1%
    with wording by size (findings 120/122).
    """
    structured_data = structured_data or {}
    titles = titles or ["台股今日分析"]
    facts = build_fact_index(structured_data)
    hero = hero or {}
    hero_key = hero.get("key")
    hero_label = _hero_label(hero) if hero else ""
    hero_name = hero.get("name") or hero_label
    market_roles = _load_market_roles()
    formal_targets = _extract_stock_text(structured_data.get("prediction_targets"), limit=2)
    buy_focus = _prefer_thumbnail_main_stocks(
        structured_data.get("safe_havens_bought"),
        market_roles,
        limit=2,
        structured_data=structured_data,
    )
    sell_risk = _extract_stock_text(structured_data.get("heavyweights_dumped"), limit=2)
    validations = _extract_stock_text(structured_data.get("prev_day_picks_result"), limit=2)
    divergences = [str(item) for item in structured_data.get("price_volume_divergence") or []]
    base_stocks = formal_targets or buy_focus
    if hero_label:
        main_stocks = [hero_label] + [stock for stock in base_stocks if not _same_stock(stock, hero)][:1]
    else:
        main_stocks = list(base_stocks)
    main_name = main_stocks[0] if main_stocks else "主力"
    main_short = _stock_name_only(main_name)
    _main_key, main_row = _stock_fact(facts, main_name)
    main_row = main_row or {}
    main_amount = main_row.get("foreign_amount")
    main_pct = main_row.get("pct_change")
    main_verb = "買" if (main_amount or 0) > 0 else ("砍" if (main_amount or 0) < 0 else "")
    index_move = _index_headline(structured_data)
    index_pct = _index_pct(structured_data)
    candidates = []

    for item in divergences:
        if not _is_off_board_accumulation(item):
            continue
        focus_stock = _extract_stock_text_from_sentence(item) or main_name
        key, row = _stock_fact(facts, focus_stock)
        amount = (row or {}).get("foreign_amount")
        if not row or amount is None or amount <= 0 or row.get("off_rank") is not True:
            break  # 「賣超」 or a stock that was on the volume board is not 榜外吸籌
        focus_stock = facts.label(key)
        candidates.append(
            {
                "scenario_name": "榜外吸籌",
                "family": "off_board",
                "priority": 92,
                "overlay_primary": _stock_overlay_primary(focus_stock),
                "overlay_secondary": f"外資買{format_amount(amount)}",
                "main_stocks": [focus_stock] + [stock for stock in main_stocks if stock != focus_stock],
                "scene": (
                    f"成交量榜像一座透明排行榜，{focus_stock} 沒站在最亮的位置，"
                    "但巨大的資金光束正從榜單外流入，形成明顯的量價矛盾。"
                ),
                "emotion": "疑惑、追查、反直覺",
                "visual_metaphor": "公開榜單與榜單外的資金通道形成對比",
                "avoid": ["不要做成制式排行榜截圖", "不要公司 Logo", "不要固定左右分割", "不要用『榜外吸籌』當封面大字"],
                "has_variant": True,
                "variant": {
                    "scenario_name": "高買低量",
                    "family": "off_board",
                    "overlay_primary": _stock_overlay_primary(focus_stock),
                    "overlay_secondary": "沒進量榜？",
                    "scene": (
                        f"{focus_stock} 的外資買超金額很大，但成交量排名卻很冷，"
                        "畫面要呈現『金額很大、市場卻還沒跟上』的矛盾。"
                    ),
                    "emotion": "追查、等待確認",
                    "visual_metaphor": "巨大的買超數字懸在冷清的成交量儀表上方",
                },
            }
        )
        break

    rank = main_row.get("volume_rank")
    if main_verb == "砍" and rank and rank <= 3 and main_pct is not None and main_pct < 0:
        candidates.append(
            {
                "scenario_name": "天量出貨",
                "family": "volume_trap",
                "priority": 88,
                "overlay_primary": _stock_overlay_primary(main_name),
                "overlay_secondary": f"外資砍{format_amount(main_amount)}",
                "main_stocks": main_stocks,
                "scene": (
                    f"{main_name} 成交量第 {rank} 名卻收跌 {format_pct_abs(main_pct)}，"
                    "綠色賣壓從主角票據往外抽；背景是高量換手，但不要蓋過股名主角。"
                ),
                "emotion": "主角浮現、量價背離、警戒",
                "visual_metaphor": "爆量的成交儀表旁，主角票據被綠色賣壓抽離",
                "avoid": ["不要單純畫暴跌恐慌", "不要公司 Logo", "不要塞滿數字"],
            }
        )

    index_word = index_move_word(index_pct)
    if index_move and main_name and index_pct is not None and abs(index_pct) >= 1.0 and main_pct is not None:
        if index_pct < 0 and main_pct > 0:
            candidates.append(
                {
                    "scenario_name": "大跌守線",
                    "family": "anomaly",
                    "priority": 86,
                    "overlay_primary": f"{index_word}{index_move}",
                    "overlay_secondary": f"{main_short}還紅？",
                    "main_stocks": main_stocks,
                    "scene": (
                        f"大盤{index_word} {index_move}，但 {main_name} 卻逆勢收漲 {format_pct_abs(main_pct)}，"
                        "畫面要呈現市場恐慌與個股韌性的反差。"
                    ),
                    "emotion": "恐慌中的例外、追查原因",
                    "visual_metaphor": "綠色烏雲籠罩的大盤背景中，一檔紅色股票仍被聚光燈照亮",
                    "avoid": ["不要過度恐嚇", "不要公司 Logo", "不要固定左右分割"],
                }
            )
        elif index_pct > 0 and main_pct > 0:
            candidates.append(
                {
                    "scenario_name": "強彈追蹤",
                    "family": "anomaly",
                    "priority": 86,
                    "overlay_primary": f"{index_word}{index_move}",
                    "overlay_secondary": f"{main_short}先看？",
                    "main_stocks": main_stocks,
                    "scene": (
                        f"大盤{index_word} {index_move}，資金聚光燈先打在 {main_name}，"
                        "畫面要呈現反攻日與核心標的的同步升溫。"
                    ),
                    "emotion": "升溫、追查、等待確認",
                    "visual_metaphor": "紅色反攻能量從大盤湧向被點亮的個股票據",
                    "avoid": ["不要畫成慶祝狂歡", "不要公司 Logo", "不要固定左右分割"],
                }
            )

    if sell_risk and (buy_focus or formal_targets or hero_label):
        # The hero's amount is worded as in the title when the title states it (同股同數字).
        hero_amount = hero.get("amount") or format_amount(main_amount)
        if hero_label and main_verb == "砍":
            sell_key, sell_stock, sell_amount = hero_key, hero_label, hero_amount
            buy_key, buy_stock, buy_amount = _scenario_counterpart(facts, structured_data, titles, hero, "safe_havens_bought")
        elif hero_label and main_verb == "買":
            buy_key, buy_stock, buy_amount = hero_key, hero_label, hero_amount
            sell_key, sell_stock, sell_amount = _scenario_counterpart(facts, structured_data, titles, hero, "heavyweights_dumped")
        else:
            buy_stock = (buy_focus or formal_targets)[0] if (buy_focus or formal_targets) else ""
            sell_stock = sell_risk[0]
            buy_amount = _buy_amount_for_stock(buy_stock, structured_data)
            sell_amount = _sell_amount_for_stock(sell_stock, structured_data)
        if buy_stock and sell_stock:
            candidates.append(
                {
                    "scenario_name": "資金換手",
                    "family": "rotation",
                    "priority": 84,
                    "overlay_primary": f"{_stock_name_only(sell_stock)}被砍",
                    "overlay_secondary": f"{_stock_name_only(buy_stock)}接棒？",
                    "main_stocks": [buy_stock, sell_stock] if main_verb != "砍" else [sell_stock, buy_stock],
                    "scene": (
                        f"{_stock_name_only(sell_stock)} 遭外資提款{(' ' + sell_amount) if sell_amount else ''}、"
                        f"{_stock_name_only(buy_stock)} 被外資{('買進 ' + buy_amount) if buy_amount else '資金'}點亮，形成明顯的換手對比。"
                    ),
                    "emotion": "反轉、警戒、資金轉向",
                    "visual_metaphor": f"{_stock_name_only(sell_stock)} 票據裂開，資金流向 {_stock_name_only(buy_stock)} 的發光票據",
                    "avoid": ["不要做成固定紅綠對半", "不要真實公司 Logo", "不要用『被砍/接棒』當唯一問法"],
                    "has_variant": True,
                    "variant": {
                        "scenario_name": "法人對決",
                        "family": "rotation",
                        "overlay_primary": f"賣{sell_amount}" if sell_amount else "賣壓重",
                        "overlay_secondary": f"買{buy_amount}" if buy_amount else "買盤現身",
                        "scene": (
                            f"同一交易日出現賣 {sell_amount or '壓'} 與買 {buy_amount or '盤'} 的對撞，"
                            f"焦點落在 {_stock_name_only(main_name)} 是否成為新觀察核心。"
                        ),
                        "emotion": "對撞、追查、等待確認",
                        "visual_metaphor": "兩股法人能量在大盤戰場中央正面對撞",
                    },
                }
            )
            if sell_amount and buy_amount:
                candidates.append(
                    {
                        "scenario_name": "砍一邊買一邊",
                        "family": "rotation",
                        "priority": 78,
                        "overlay_primary": f"砍{sell_amount} 買{buy_amount}",
                        "overlay_secondary": "法人對決",
                        "main_stocks": [buy_stock, sell_stock] if main_verb != "砍" else [sell_stock, buy_stock],
                        "scene": (
                            f"同一個金融戰場中，{_stock_name_only(sell_stock)} 被綠色賣壓推離，"
                            f"{_stock_name_only(buy_stock)} 被紅色買盤護盾托起。"
                        ),
                        "emotion": "反轉、對比、等待確認",
                        "visual_metaphor": "綠色撤退能量與紅色買進護盾在同一畫面對撞",
                        "avoid": ["不要抽象說資金輪動", "不要真實 Logo", "不要固定紅綠對半"],
                    }
                )

    # High-priority「驗收現場」only when placement=lead; otherwise skip so /2 doesn't daily-repeat it.
    ef_sc = structured_data.get("editorial_frame") or {}
    v_place = str(ef_sc.get("validation_placement") or "").lower()
    if validations and v_place == "lead":
        candidates.append(
            {
                "scenario_name": "驗收現場",
                "family": "validation",
                "priority": 90,
                "overlay_primary": "上集點名",
                "overlay_secondary": validations[0] if validations else "昨日點名",
                "main_stocks": validations,
                "scene": (
                    "昨日被點名的股票像站上核對台，市場數據正在被逐項核對；"
                    "命中的標的被聚光燈照亮，未成立的標的留在較暗的背景裡。"
                ),
                "emotion": "可信度、反差、連續追蹤",
                "visual_metaphor": "交易票據、成交量柱、檢核印章與聚光燈構成財經核對現場",
                "avoid": ["不要像考卷版型", "不要塞超過兩檔主角", "不要每天重複驗收儀式感"],
            }
        )

    if divergences:
        candidates.append(
            {
                "scenario_name": "量價異常",
                "family": "anomaly",
                "priority": 76,
                "overlay_primary": "量價異常",
                "overlay_secondary": "誰在撐盤？",
                "main_stocks": main_stocks,
                "scene": (
                    f"交易現場表面看似平靜，但 {main_name} 桌面下方有巨大的資金機械正在運轉，"
                    f"暗示{_FLOW_WORD.get(main_verb, '法人資金')}與成交量之間的不尋常落差。"
                ),
                "emotion": "懸疑、暗藏訊號、即將揭露",
                "visual_metaphor": "安靜檯面與地下發光管線形成對比",
                "avoid": ["不要畫成制式技術分析圖", "不要滿版數字", "不要固定版型"],
            }
        )

    events = structured_data.get("supply_chain_event_radar") or []
    if events:
        first_event = events[0]
        if isinstance(first_event, dict):
            event_title = str(
                first_event.get("event")
                or first_event.get("title")
                or first_event.get("headline")
                or ""
            ).strip()
        else:
            event_title = str(first_event).strip()
        event_title = event_title[:36] if event_title else "重大供應鏈事件"
        event_sell = _stock_name_only(sell_risk[0]) if sell_risk else ""
        event_buy = _stock_name_only((buy_focus or formal_targets or [main_name])[0])
        event_sell_amt = _sell_amount_for_stock(sell_risk[0], structured_data) if sell_risk else ""
        event_buy_amt = _buy_amount_for_stock(event_buy, structured_data)
        if event_sell and event_buy and event_sell != event_buy:
            event_primary = f"{event_sell}{event_sell_amt}" if event_sell_amt else f"{event_sell}被提款"
            event_secondary = f"{event_buy}買{event_buy_amt}" if event_buy_amt else f"{event_buy}先看？"
        else:
            event_primary = "事件來了"
            event_secondary = event_buy or "台股先看"
        candidates.append(
            {
                "scenario_name": "事件衝擊",
                "family": "event",
                "priority": 82,
                "overlay_primary": event_primary,
                "overlay_secondary": event_secondary,
                "main_stocks": main_stocks,
                "scene": f"供應鏈事件「{event_title}」正在改寫盤面排序，{main_name} 成為第一個要被檢查的標的。",
                "emotion": "事件驅動、重新定價",
                "visual_metaphor": "新聞衝擊波掃過交易戰場，少數節點被重新點亮",
                "avoid": ["不要使用真實品牌 Logo", "不要照搬新聞截圖"],
            }
        )

    if main_verb == "砍":
        fallback_primary = f"{main_short} 退場？"
        fallback_scene = f"{main_name} 的交易票據被綠色賣壓抽走，市場正在檢查這是短線調節還是趨勢轉弱。"
        fallback_metaphor = "被抽離的交易票據與等待驗證的成交量儀表"
    else:
        fallback_primary = f"{main_short} 點火？"
        fallback_scene = f"{main_name} 的交易票據被資金光束點亮，市場正在檢查這是短線追價還是真正建倉。"
        fallback_metaphor = "被點亮的交易票據與等待驗證的成交量儀表"
    candidates.append(
        {
            "scenario_name": "主力點火",
            "family": "fallback",
            "priority": 60,
            "overlay_primary": fallback_primary,
            "overlay_secondary": f"外資{main_verb}{format_amount(main_amount)}" if main_verb else "看量能",
            "main_stocks": main_stocks,
            "scene": fallback_scene,
            "emotion": "等待確認、升溫、追查買盤",
            "visual_metaphor": fallback_metaphor,
            "avoid": ["不要把方向指標股畫成最大飆股", "不要公司 Logo", "不要固定左右分割"],
        }
    )

    deduped = []
    seen = set()
    for candidate in sorted(candidates, key=lambda row: -row.get("priority", 0)):
        key = (candidate.get("family"), candidate.get("scenario_name"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(_scenario_brief(candidate, titles, candidate.get("main_stocks") or main_stocks))
    return deduped


def generate_scenario_briefs(
    report_content,
    titles,
    visual_style,
    structured_data=None,
    count=2,
    date_label=None,
    channel_story="",
    hero=None,
    run_date=None,
):
    """
    Generate thumbnail scenario briefs locally with formula-fatigue rotation
    (``run_date``'s own earlier prompts are ignored, so a same-day rerun keeps its scenario).
    The hero is chosen first (from the titles + local facts) and every card is
    built around it.
    """
    structured_data = structured_data or {}
    titles = titles or ["台股今日分析"]
    if hero is None:
        hero = choose_hero(titles, structured_data)
    fatigue_state = build_fatigue_state(channel_story=channel_story, exclude_date=run_date)
    candidates = _collect_scenario_candidates(structured_data, titles, hero=hero)
    selected = pick_non_fatigued(candidates, fatigue_state, limit=count)

    if len(selected) < count:
        for candidate in candidates:
            if candidate in selected:
                continue
            selected.append(candidate)
            if len(selected) >= count:
                break

    for index, scenario in enumerate(selected):
        scenario["paired_title"] = titles[min(index, len(titles) - 1)] if titles else "台股今日分析"

    selected = align_ab_overlays(selected[:count], titles, structured_data, hero=hero)
    return selected[:count]


# A/B 必須明顯不同構圖（同一視覺主題下也不能偷懶複製同一鏡頭）
AB_COMPOSITION_VARIANTS = [
    {
        "id": "A_wide_map",
        "label": "A 廣角地圖",
        "directive": (
            "COMPOSITION VARIANT A (mandatory, must look different from B): "
            "Camera = ultra-wide bird's-eye or high 3/4 top-down. "
            "Subject scale = market map / capital-flow diagram fills ~55-65% of frame. "
            "Headline plaque sits bottom-left or lower third. "
            "Characters are SMALL in a corner (supporting only). "
            "Depth layers = map → path beams → tiny characters. "
            "Do NOT use medium-close character duel, and do NOT center two large opposing stock props."
        ),
    },
    {
        "id": "B_close_conflict",
        "label": "B 殘句中景",
        "directive": (
            "COMPOSITION VARIANT B (mandatory, must look different from A): "
            "Camera = medium-close, characters MEDIUM size with readable faces. "
            "Headline plaque occupies about 40% and is the first thing you read; "
            "overlay is a fragment (short question or ticker+status), not a full sentence. "
            "Daily visual theme stays in the background only. "
            "Do NOT use verification stamps, VERIFIED badges, 上集點名, exam-sheet layouts, "
            "or an ultra-wide bird's-eye capital-flow map as the main subject."
        ),
    },
]


# Single-variable cover A/B (data/ab_test_plan.json): both covers use this composition;
# only the declared A/B variable may change between them.
AB_SHARED_COMPOSITION = {
    "id": "shared",
    "label": "A/B 共用構圖（只差 A/B 變數）",
    "directive": (
        "COMPOSITION (identical for cover A and cover B — only the declared A/B variable may differ): "
        "camera = 3/4 medium-wide view at eye level; the hero stock prop or plaque is large at center-right; "
        "supporting elements stay in the background; keep a clean negative-space area for the headline. "
        "Do not invent a different camera angle or layout for this cover."
    ),
}

_NUMBER_LAYOUTS = {
    "standard": "Put them in a high-contrast dark or bright negative-space panel occupying about 35% of the image. ",
    "big": (
        "NUMBER LAYOUT (A/B variable): the number line is the largest element — a headline plaque about 45% of "
        "the image in the lower-left, the number glyphs about one third of the image height. "
    ),
    "small": (
        "NUMBER LAYOUT (A/B variable): put both headline lines in a compact badge in the upper-right corner "
        "(about 15% of the image); the scene and the hero prop fill the rest of the frame. "
    ),
}

# Composition-variant avoid notes from the old two-layout A/B; meaningless once both covers share one layout.
_OLD_LAYOUT_AVOIDS = {"不要特寫雙人對峙當主構圖", "不要中景角色決鬥構圖", "不要俯視全圖資金地圖當主構圖", "不要與 A 組相同鏡頭與主體比例"}


def cover_number(primary, secondary):
    """The verified number a cover prints: first 「N億」, else first 「N%」 ("" when none)."""
    text = f"{primary or ''} {secondary or ''}"
    match = re.search(r"\d+(?:\.\d+)?\s*億", text) or re.search(r"\d+(?:\.\d+)?\s*[%％]", text)
    return re.sub(r"\s+", "", match.group(0)) if match else ""


def build_ab_cover_briefs(base, hypothesis, hero, base_style, paired_title):
    """
    Covers A and B from ONE scenario brief: same hero, same verified number, same
    title pairing (title A), same scene and composition; only the hypothesis
    variable (style / text / characters / number_layout) differs.
    """
    from modules.cover_ab_test import constants_line, levers_for, variable_line

    hero = hero or {}
    base = dict(base or {})
    if hero.get("name") or hero.get("ticker"):
        control = _control_overlay(hero)
    else:
        control = (base.get("overlay_primary") or paired_title, base.get("overlay_secondary") or "")
    number = cover_number(*control)
    briefs = []
    for letter in ("a", "b"):
        levers = levers_for(hypothesis, letter)
        brief = dict(base)
        brief["avoid"] = [bit for bit in (base.get("avoid") or []) if bit not in _OLD_LAYOUT_AVOIDS]
        brief["main_stocks"] = list(base.get("main_stocks") or [])
        brief["paired_title"] = paired_title
        brief["hero"] = hero
        if levers["text"] == "question":
            brief["overlay_primary"], brief["overlay_secondary"] = _question_overlay(hero, *control)
        else:
            brief["overlay_primary"], brief["overlay_secondary"] = control
        brief["style_key"] = "war_room_board" if levers["style"] == "war_room_board" else base_style
        brief["character_mode"] = levers["characters"]
        brief["number_layout"] = levers["number_layout"]
        brief["composition_id"] = AB_SHARED_COMPOSITION["id"]
        brief["composition_label"] = AB_SHARED_COMPOSITION["label"]
        brief["composition_directive"] = AB_SHARED_COMPOSITION["directive"]
        brief["ab_arm"] = letter
        brief["ab_hypothesis"] = hypothesis
        brief["ab_levers"] = levers
        brief["ab_variable_line"] = variable_line(hypothesis, letter)
        brief["ab_constants_line"] = constants_line(
            _hero_label(hero), number, variable=(hypothesis or {}).get("variable", "")
        )
        brief["ab_number"] = number
        if levers["characters"] == "none":
            brief["avoid"] = brief["avoid"] + ["不要任何人物或吉祥物"]
        briefs.append(brief)
    return briefs


def _stamp_ab_composition(scenario_brief, index):
    """Inject a hard A/B composition directive so two thumbnails cannot share one layout."""
    scenario_brief = dict(scenario_brief or {})
    variant = AB_COMPOSITION_VARIANTS[index % len(AB_COMPOSITION_VARIANTS)]
    scenario_brief["composition_id"] = variant["id"]
    scenario_brief["composition_label"] = variant["label"]
    scenario_brief["composition_directive"] = variant["directive"]
    avoid = list(scenario_brief.get("avoid") or [])
    if index % 2 == 0:
        avoid.append("不要特寫雙人對峙當主構圖")
        avoid.append("不要中景角色決鬥構圖")
    else:
        avoid.append("不要俯視全圖資金地圖當主構圖")
        avoid.append("不要與 A 組相同鏡頭與主體比例")
    scenario_brief["avoid"] = avoid
    return scenario_brief


def _build_image_prompt(style_key_or_custom, title, scenario_brief=None, date_label_text=""):
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
    overlay_primary = scenario_brief.get("overlay_primary", title)
    overlay_secondary = scenario_brief.get("overlay_secondary", "")
    overlay_text = overlay_primary if not overlay_secondary else f"{overlay_primary} / {overlay_secondary}"
    composition_directive = scenario_brief.get("composition_directive") or (
        "Create a flexible cinematic scene with a unique camera angle; do not reuse a generic centered template."
    )
    # Taiwan market colors: 漲/買=紅, 跌/賣=綠 (opposite of US convention).
    tw_color_rule = (
        "TAIWAN MARKET COLOR RULE (mandatory): rising prices / buy pressure / bullish = RED; "
        "falling prices / sell pressure / bearish / market crash = GREEN. "
        "Never use US-style green-up/red-down. "
    )
    text_requirement = (
        "IMPORTANT TEXT REQUIREMENT: the thumbnail must contain one large clean text block in the foreground. "
        f"The first line must be exactly: 「{overlay_primary}」. "
        + (f"The second line must be exactly: 「{overlay_secondary}」. " if overlay_secondary else "")
        + "These headline lines are the most important visible elements in the image. "
        + _NUMBER_LAYOUTS.get(scenario_brief.get("number_layout") or "standard", _NUMBER_LAYOUTS["standard"])
        + "Do not hide stock names in small background details. Do not replace stock names with generic words. "
        + tw_color_rule
    )
    character_rules = (
        "Recurring character rules: "
        "Dr. Zhang is a Taiwanese male data strategist around age 40, black round glasses, calm detective-like posture, "
        "holding a tablet or data screen, wearing a dark suit or today's themed commander outfit. "
        "A-Ming is the channel's fixed leek retail-investor character: Taiwanese male around age 30, friendly but nervous, "
        "with a small leek-leaf hair tuft or leek-green head accessory that remains visible in every theme, "
        "wearing a loose T-shirt or today's themed rookie outfit with clear leek-green accents, exaggerated facial expression, "
        "holding a phone with stock charts. The outfit may adapt to the visual world, but the leek-green color and leek-leaf silhouette must remain recognizable. "
        "Use expressive silhouettes, gestures, and staged power dynamics, but no real person likenesses. "
    )
    if style_key_or_custom == "war_room_board":
        # H1 arm B: the scenario's story is context for the board, never a staged scene
        # (the arm A scene / metaphor would otherwise leak into the data-board cover).
        situation_line = f"Story behind the number (only as small background data panels, never a staged scene): {scene} "
        metaphor_line = (
            "Visual metaphor: none on this cover — the giant number plaque replaces the metaphor scene; "
            "do not draw story props. "
        )
    else:
        situation_line = f"Core situation: {scene} "
        metaphor_line = f"Visual metaphor: {visual_metaphor}. "
    if scenario_brief.get("character_mode") == "none":
        character_rules = (
            "NO CHARACTERS (A/B variable): no people, no mascots, no Dr. Zhang and no A-Ming anywhere in the image, "
            "even if the visual world mentions them; a pure data / object composition. "
        )

    return (
        "YouTube thumbnail background image, landscape 16:9 format. "
        "Create a flexible cinematic scene, not a fixed template and not a repeated layout. "
        f"{composition_directive} "
        f"{text_requirement}"
        f"Visual world: {visual_desc} "
        f"Paired title idea: '{title}'. "
        f"Main stock focus for the human editor: {stock_focus}. "
        f"{situation_line}"
        f"Emotional tone: {emotion}. "
        f"{metaphor_line}"
        f"{character_rules}"
        "Include a large, highly readable Traditional Chinese thumbnail headline in the image. "
        f"Render exactly this headline text: '{overlay_text}'. "
        f"Also add a small date label in the upper-left corner: '{date_label_text}'. "
        "Use bold Traditional Chinese display typography with strong contrast and clean spacing, integrated on a "
        "palace plaque, scroll, shadow area, or dramatic negative space. "
        "No extra generated text beyond the headline and date label, no random letters, no random numbers, no Simplified Chinese, no company logos, "
        "no real person likenesses in the image. "
        f"Avoid: {', '.join(avoid)}. "
        "Ultra high quality, dramatic, readable at small sizes, YouTube finance thumbnail energy."
    )


def _hero_fact_line(hero):
    """「1303 南亞（外資 +55.3億，漲跌 +9.40%）」 — what the cover numbers were checked against."""
    label = _hero_label(hero) if hero else ""
    if not label:
        return "未指定（封面字未綁定單一主角）"
    bits = []
    if hero.get("foreign_amount") is not None:
        bits.append(f"外資 {hero['foreign_amount']:+.1f}億")
    if hero.get("pct_change") is not None:
        bits.append(f"漲跌 {hero['pct_change']:+.2f}%")
    return f"{label}（{'，'.join(bits)}）" if bits else label


def _gate_cover(scenario, index, facts, hero):
    """
    Cover fact gate before a prompt is saved: a contradiction is rebuilt from the
    hero's facts (A = control overlay, B = fragment) and logged.
    """
    where = f"封面{'AB'[index] if index < 2 else index + 1}"
    hero_key = (hero or {}).get("key")
    issues = check_cover(
        scenario.get("overlay_primary"), scenario.get("overlay_secondary"), hero_key=hero_key, where=where, facts=facts
    )
    if not has_errors(issues):
        return scenario, issues
    for issue in issues:
        if issue["level"] == "error":
            print(f"  ⛔ [封面事實對帳] {format_issue(issue)}")
    fixed = dict(scenario)
    text_mode = (scenario.get("ab_levers") or {}).get("text")
    if hero and text_mode:
        # single-variable A/B: rebuild in the arm's own text mode, so a fix never adds a second variable
        primary, secondary = _question_overlay(hero) if text_mode == "question" else _control_overlay(hero)
    elif hero:
        primary, secondary = _control_overlay(hero) if index == 0 else _fragment_overlay(hero)
    else:
        primary, secondary = (scenario.get("overlay_primary") or "台股盤後"), "看這檔"
        primary = re.sub(r"[+\-−]?\d+(?:\.\d+)?\s*(?:億|%|％|點)", "", str(primary)).strip() or "台股盤後"
    fixed["overlay_primary"], fixed["overlay_secondary"] = primary, secondary
    print(f"  🔧 [封面事實對帳] {where} 已依本機數據重建：{primary} / {secondary}")
    return fixed, issues


def save_thumbnail_prompt(style_key_or_custom, title, date_str, output_path, scenario_brief=None):
    """
    Save a single YouTube thumbnail prompt for manual image generation.
    style_key_or_custom: a STYLE_PRESETS key (e.g. "dc_comics") OR a free-form style description.
    """
    date_label_text = f"{date_str.lstrip('0').replace('/0', '/')} 台股盤後"
    image_prompt = _build_image_prompt(
        style_key_or_custom,
        title,
        scenario_brief=scenario_brief,
        date_label_text=date_label_text,
    )
    scenario_brief = scenario_brief or {}
    composition_label = scenario_brief.get("composition_label") or "未指定"
    ab_lines = ""
    if scenario_brief.get("ab_variable_line"):
        ab_lines = (
            f"- A/B variable: {scenario_brief['ab_variable_line']}\n"
            f"- A/B held constant: {scenario_brief.get('ab_constants_line') or '主角、數字、配對標題、場景、構圖'}\n"
        )
    content = f"""# YouTube Thumbnail Prompt

Date: {date_str}
Title pairing: {title}
Scenario: {scenario_brief.get("scenario_name", "未命名情境")}
Composition: {composition_label}

## Thumbnail Headline Text

- Primary: {scenario_brief.get("overlay_primary", title)}
- Secondary: {scenario_brief.get("overlay_secondary", "外資暗流 / 影子主力")}
- Small date label: {date_label_text}
- Text mode: Ask ChatGPT image generation to render the exact Traditional Chinese text above. If the text is imperfect, regenerate or add it manually.

## Scenario Brief

- Hero (fact source): {_hero_fact_line(scenario_brief.get("hero") or {})}
{ab_lines}- Main stocks: {", ".join(scenario_brief.get("main_stocks", [])) or "依標題與報告主角"}
- Scene: {scenario_brief.get("scene", "")}
- Emotion: {scenario_brief.get("emotion", "")}
- Visual metaphor: {scenario_brief.get("visual_metaphor", "")}
- Composition: {composition_label}
- Avoid: {", ".join(scenario_brief.get("avoid", [])) or "固定版型、真實 Logo、圖中文字"}

## Image Prompt

{image_prompt}
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    return image_prompt


def generate_ab_test_thumbnails(
    gemini_api_key,
    openai_api_key,
    report_content,
    reports_dir,
    styles=None,
    num_titles=2,
    structured_data=None,
    run_date=None,
    channel_story="",
    preferred_titles=None,
    ab_hypothesis=None,
):
    """
    Generate thumbnail prompts + title pairings for A/B testing.

    Covers A and B are a single-variable test (data/ab_test_plan.json): one scenario,
    one hero, one verified number and title A on both; only this week's hypothesis
    variable differs (``ab_hypothesis`` = id / variable / dict overrides the rotation
    and the AB_HYPOTHESIS env var).
    With structured_data, this is fully local and does not call Gemini.

    styles 可以是：
      - None              → 讀 THUMBNAIL_STYLE 環境變數；若沒設定則用預設兩種 preset
      - ["dc_comics"]     → 指定 STYLE_PRESETS 中的 key
      - ["午夜霓虹，紫金配色"] → 直接用作自訂 prompt（每天換風格就改這裡）

    preferred_titles:
      - 若 /2 已有 Gemini YouTube package 標題，優先沿用，避免本機 fallback 覆蓋事實正確的主標題。

    Returns:
        dict with 'titles' and 'thumbnails' lists
    """
    if run_date:
        parsed_date = datetime.datetime.strptime(run_date, "%Y-%m-%d")
    else:
        parsed_date = datetime.datetime.now()
    date_str = parsed_date.strftime("%m/%d")
    date_full = parsed_date.strftime("%Y-%m-%d")

    styles = _resolve_thumbnail_styles(styles)
    if styles == DEFAULT_THUMBNAIL_STYLES:
        print(f"\n  🎨 使用預設風格: {DEFAULT_VISUAL_THEME_LABEL}（2026-07-07 CTR 規則）")
    elif styles:
        print(f"\n  🎨 使用視覺風格: {styles[0][:60]}...")

    results = {"titles": [], "thumbnails": []}

    # === Step 1: Generate title pairings ===
    cleaned_preferred = [
        str(title).strip()
        for title in (preferred_titles or [])
        if str(title or "").strip()
    ]
    if cleaned_preferred:
        print("\n🎯 沿用 YouTube package 標題作為封面配對（不呼叫 Gemini）...")
        titles = cleaned_preferred[:num_titles]
    elif structured_data:
        print("\n🎯 正在用本機 structured_data 生成封面標題配對（不呼叫 Gemini）...")
        titles = generate_local_titles(
            structured_data,
            num_titles=num_titles,
            date_label=date_str,
            channel_story=channel_story,
        )
    else:
        print("\n🎯 無 structured_data，回退 Gemini 生成 YouTube 標題變體...")
        titles = generate_titles(gemini_api_key, report_content, num_titles, date_label=date_str)
    results["titles"] = titles
    for i, title in enumerate(titles):
        print(f"  📝 標題 {i+1}: {title}")

    # === Step 2: ONE scenario brief → covers A/B that differ only in this week's A/B variable ===
    primary_style = styles[0] if styles else "dc_comics"
    paired_title = titles[0] if titles else "台股今日分析"
    hypothesis = None
    try:
        from modules.cover_ab_test import current_hypothesis, hypothesis_for_style

        hypothesis = ab_hypothesis if isinstance(ab_hypothesis, dict) else current_hypothesis(date_full, override=ab_hypothesis)
        hypothesis = hypothesis_for_style(hypothesis, primary_style)
    except Exception as exc:
        print(f"  ⚠️  A/B 假設讀取失敗，A/B 封面只差「未宣告」：{exc}")
    if hypothesis:
        if hypothesis.get("replaces"):
            print(
                f"\n  ℹ️  今日主題就是財經戰情室：{hypothesis['replaces']}（漫畫場景 vs 戰情室數據大字牌）兩組會幾乎一樣，"
                f"今天改測 {hypothesis.get('id')}"
            )
        print(
            f"\n🧪 本週封面 A/B 假設：{hypothesis.get('id')} {hypothesis.get('label')}"
            f"（{hypothesis.get('iso_week')}，來源 {hypothesis.get('source')}）"
        )
    print("\n🎬 正在生成情境式封面 brief（A/B 共用同一情境、同主角、同數字、同配標題 A）...")
    facts = build_fact_index(structured_data or {})
    hero = choose_hero(titles, facts=facts) if structured_data else {}
    if hero:
        print(f"  🎯 封面主角（本機數據）：{_hero_fact_line(hero)}")
    results["hero"] = hero
    results["cover_gate"] = []
    base_briefs = generate_scenario_briefs(
        report_content,
        titles,
        primary_style,
        structured_data=structured_data,
        count=1,
        date_label=date_str,
        channel_story=channel_story,
        hero=hero,
        run_date=date_full,
    )
    base = base_briefs[0] if base_briefs else {"scenario_name": "主力點火", "avoid": []}
    scenario_briefs = build_ab_cover_briefs(base, hypothesis, hero, primary_style, paired_title)
    results["ab_test"] = {
        "hypothesis": hypothesis,
        "hero_label": _hero_label(hero) if hero else "",
        "number": scenario_briefs[0].get("ab_number", ""),
    }

    print(f"\n🎨 正在產生 {len(scenario_briefs)} 份情境式縮圖 Prompt（手動生圖用）...")

    for i, scenario in enumerate(scenario_briefs):
        if structured_data:
            scenario, cover_issues = _gate_cover(scenario, i, facts, hero)
            results["cover_gate"].extend(cover_issues)
        style = scenario.get("style_key") or primary_style
        preset = STYLE_PRESETS.get(style)
        style_label = preset["name"] if preset else "當日視覺主題"

        title = scenario.get("paired_title") or paired_title
        short_title = title[:20] + "..." if len(title) > 20 else title

        from modules.report_paths import YOUTUBE_ARTIFACTS

        filename = YOUTUBE_ARTIFACTS.get(f"thumbnail_{i+1}", f"yt_thumbnail_prompt_{i+1}.md")
        output_path = os.path.join(reports_dir, filename)

        print(
            f"\n  🖼️  縮圖 Prompt {i+1}/{len(scenario_briefs)}: {style_label} / "
            f"{scenario.get('scenario_name', '情境')} / A/B 變數 {scenario.get('ab_variable_line', '未宣告')}"
        )
        print(f"     主題方向: {short_title}")

        prompt = save_thumbnail_prompt(style, title, date_str, output_path, scenario_brief=scenario)
        print(f"     ✅ Prompt 已儲存: {filename}")
        results["thumbnails"].append({
            "style": style_label,
            "scenario": scenario.get("scenario_name", ""),
            "composition": scenario.get("composition_label", ""),
            "title": title,
            "path": output_path,
            "prompt": prompt,
            "arm": scenario.get("ab_arm", ""),
            "ab_variable": scenario.get("ab_variable_line", ""),
            "hypothesis_id": (hypothesis or {}).get("id", ""),
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
        comp = thumb.get("composition") or "構圖未標"
        print(
            f"     {chr(65+i)}. [{thumb['style']} / {thumb.get('scenario', '情境')} / {comp}] "
            f"{os.path.basename(thumb['path'])}"
        )
        print(f"        搭配標題: {thumb['title'][:30]}...")

    hypothesis = (results.get("ab_test") or {}).get("hypothesis") or {}
    if hypothesis:
        print(f"\n  🧪 A/B 變數：{hypothesis.get('id')} {hypothesis.get('label')}（其餘全部相同）")
    print("\n  💡 建議：用標題 A 上架；兩份封面 Prompt 手動生圖後，在 YouTube Studio「測試與比較」同時上傳，")
    print("     等 YouTube 回報勝出後執行 python3 cli.py youtube record-ab-result 記錄。標題 B 留給之後單獨的標題測試。")
    print(f"{'='*60}\n")
