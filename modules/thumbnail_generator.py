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


def _normalize_custom_style(style_key_or_custom):
    style = str(style_key_or_custom or "").strip()
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
    """Return a short index move label like '1783點' (unsigned) for template fill-in."""
    text = str(structured_data.get("index_action") or structured_data.get("main_theme") or "").strip()
    if not text:
        return ""
    match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*點", text)
    if match:
        # Keep magnitude only; direction is handled by _index_direction_word.
        value = abs(float(match.group(1)))
        if value >= 100:
            return f"{int(round(value))}點"
        return f"{value:g}點"
    return text[:12]


def _index_direction_word(structured_data):
    """Return 漲/跌/走 based on index_action text or signed point move."""
    text = str(structured_data.get("index_action") or structured_data.get("main_theme") or "").strip()
    if not text:
        return "走"
    if any(token in text for token in ("上漲", "大漲", "狂飆", "強彈", "翻紅", "收紅")):
        return "漲"
    if any(token in text for token in ("下跌", "大跌", "重挫", "崩", "血洗", "翻黑", "收黑")):
        return "跌"
    pct = re.search(r"\(([+-]?\d+(?:\.\d+)?)%\)", text)
    if pct:
        return "漲" if float(pct.group(1)) > 0 else ("跌" if float(pct.group(1)) < 0 else "走")
    match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*點", text)
    if match:
        value = float(match.group(1))
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


def _build_local_title_candidates(structured_data, date_label, fatigue_state):
    targets = _extract_stock_text(structured_data.get("prediction_targets"), limit=2)
    buys = _extract_stock_text(structured_data.get("safe_havens_bought"), limit=2)
    sells = _extract_stock_text(structured_data.get("heavyweights_dumped"), limit=1)
    validations = _extract_stock_text(structured_data.get("prev_day_picks_result"), limit=2)
    divergences = [str(item) for item in structured_data.get("price_volume_divergence") or []]
    frame = _editorial_frame_key(structured_data)
    index_move = _index_headline(structured_data)
    buy_amount = _first_amount_text(" ".join(str(item) for item in structured_data.get("safe_havens_bought") or []))
    sell_amount = _first_amount_text(" ".join(str(item) for item in structured_data.get("heavyweights_dumped") or []))
    main_buy = buys[0] if buys else (targets[0] if targets else "")
    main_sell = sells[0] if sells else ""
    main_target = targets[0] if targets else main_buy
    lead_buy_stock, lead_buy_amount = _lead_foreign_buy(structured_data)
    lead_sell_stock, lead_sell_amount = _lead_foreign_sell(structured_data)
    index_dir = _index_direction_word(structured_data)

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
        # Avoid hard-coding AMD openers; event names belong mid-title only when needed.
        candidates.append(
            f"外資狂砍{_stock_name_only(lead_sell_stock)}{lead_sell_amount}！利多竟成提款？"
        )

    if lead_buy_amount and lead_buy_stock and index_move:
        candidates.append(
            f"大盤{index_dir}{index_move}！外資卻買{_stock_name_only(lead_buy_stock)}{lead_buy_amount}？"
        )

    for item in divergences:
        if _is_off_board_accumulation(item):
            stock = _extract_stock_text_from_sentence(item) or main_target
            amount = _first_amount_text(item)
            candidates.append(f"外資狂砸{amount}買{_stock_name_only(stock)}，成交量卻沒進榜？")
            candidates.append(f"{amount}買{_stock_name_only(stock)}，為何沒進量榜？")
            break
        if "天量" in item or ("成交量" in item and ("賣超" in item or "下跌" in item or "派發" in item or "慘跌" in item)):
            stock = _extract_stock_text_from_sentence(item) or main_sell or main_buy
            sell_amt = _sell_amount_for_stock(stock, structured_data) or sell_amount
            volume_label = _volume_wanzhang_text(item) or "爆量"
            candidates.append(
                f"{_stock_name_only(stock)}{volume_label}成交王卻跌！外資賣{sell_amt}誰在跑？"
            )
            candidates.append(f"{_stock_name_only(stock)}爆量卻跌，法人方向一致嗎？")
            break

    if lead_sell_amount and lead_sell_stock and lead_buy_amount and lead_buy_stock:
        candidates.append(
            f"外資賣{lead_sell_stock}{lead_sell_amount}，卻反手{lead_buy_amount}買{_stock_name_only(lead_buy_stock)}？"
        )

    if lead_buy_amount and lead_buy_stock:
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

    if index_move and main_buy:
        if index_dir == "跌":
            candidates.append(f"{date_label} 台股跌{index_move}，{main_buy}為什麼還強？")
            candidates.append(f"{date_label} 台股｜{index_move}重挫，{main_buy}逆勢亮燈？")
        elif index_dir == "漲":
            candidates.append(f"{date_label} 台股漲{index_move}，{main_buy}還能追嗎？")
            candidates.append(f"{date_label} 台股｜狂飆{index_move}，{main_buy}是真行情？")
        else:
            candidates.append(f"{date_label} 台股走{index_move}，{main_buy}先看哪裡？")

    if buy_amount and main_buy:
        candidates.append(f"{date_label} 台股｜外資買{buy_amount}，{main_buy}先動？")
        candidates.append(f"{date_label} 台股｜{main_buy}外資買{buy_amount}，量價一致嗎？")

    if sell_amount and main_sell and main_buy:
        candidates.append(f"{date_label} 台股｜外資賣{sell_amount}買{buy_amount}，{main_buy}先看？")
        candidates.append(f"{date_label} 台股｜{main_sell}賣壓重，資金轉向{main_buy}？")

    if main_target:
        candidates.append(f"{date_label} 台股｜{main_target}明日先看哪個價位？")
        candidates.append(f"{date_label} 台股｜{main_target}是短線觀察還是假突破？")

    if frame in {"major_event_day", "重大事件日"}:
        events = structured_data.get("supply_chain_event_radar") or structured_data.get("macro_events") or []
        fresh = [
            row for row in (events or [])
            if isinstance(row, dict) and row.get("freshness") == "new" and int(row.get("impact_score") or 0) >= 60
        ]
        if fresh and main_target:
            candidates.append(f"{date_label} 台股｜{main_target}先看哪個條件？")

    candidates.append(f"{date_label} 台股盤後｜明天先盯這 2 個條件")

    return select_diverse_titles(candidates, num_titles=len(candidates), fatigue_state=fatigue_state)


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
        out = []
        for row in ranking:
            if row.get("base_role") == "direction_indicator":
                continue
            text = row.get("source_text") or f"{row.get('ticker', '')} {row.get('name', '')}".strip()
            if text:
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


def _named_stocks_from_structured(structured_data):
    """Name -> ticker from daily lists / prediction targets."""
    mapping = {}
    structured_data = structured_data or {}
    for item in structured_data.get("prediction_targets") or []:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("id") or item.get("ticker") or "").strip()
        name = str(item.get("name") or "").strip()
        if name:
            mapping[name] = ticker or mapping.get(name, "")
    for blob in list(structured_data.get("safe_havens_bought") or []) + list(
        structured_data.get("heavyweights_dumped") or []
    ):
        text = str(blob)
        ticker = ""
        t_match = re.search(r"\b(\d{4,6})\b", text)
        if t_match:
            ticker = t_match.group(1)
        name = re.sub(r"\([^)]*\)", "", text)
        name = re.sub(r"[+\-]?\d+(?:\.\d+)?\s*億", "", name)
        name = re.sub(r"\s+", " ", name).strip(" ：:，,")
        if name:
            mapping[name] = ticker or mapping.get(name, "")
    return mapping


def parse_title_hero(title, structured_data=None):
    """Pull the opening stock + amount so A/B overlays share one fact."""
    text = str(title or "")
    names = _named_stocks_from_structured(structured_data)
    ticker = ""
    t_match = re.search(r"\b(\d{4})\b", text)
    if t_match:
        ticker = t_match.group(1)
    amount = _first_amount_text(text)
    verb = ""
    head = text[:28]
    if any(token in head for token in ("砍", "提款", "賣")):
        verb = "砍"
    elif any(token in head for token in ("掃", "買", "狂掃", "砸", "密吸")):
        verb = "買"
    name = ""
    hits = []
    for candidate, sid in names.items():
        needle = re.sub(r"[\*＊]", "", str(candidate or ""))
        pos = text.find(candidate)
        if pos < 0 and needle:
            pos = text.find(needle)
        if pos >= 0:
            hits.append((pos, candidate, sid))
    hits.sort()
    if hits:
        name = hits[0][1]
        ticker = ticker or hits[0][2]
    if not name:
        name_match = re.search(
            r"(?:狂)?(?:砍|掃|買|賣超?|提款|砸|密吸)([\u4e00-\u9fff]{2,4})",
            text,
        )
        if name_match:
            name = name_match.group(1).strip()
    if name and not ticker:
        ticker = names.get(name) or _lookup_ticker_by_name(name) or ""
    if ticker and not name:
        for candidate, sid in names.items():
            if sid == ticker:
                name = candidate
                break
    return {
        "ticker": str(ticker or "").strip(),
        "name": str(name or "").strip(),
        "amount": amount,
        "verb": verb,
    }


def _control_overlay(hero):
    name = hero.get("name") or ""
    ticker = hero.get("ticker") or ""
    amount = hero.get("amount") or ""
    verb = hero.get("verb") or "買"
    primary = " ".join(part for part in (ticker, name) if part).strip() or name or ticker or "主角股"
    if amount:
        secondary = f"外資{verb}{amount}"
    else:
        secondary = "看這檔"
    return primary, secondary


def _fragment_overlay(hero):
    name = hero.get("name") or hero.get("ticker") or "這檔"
    ticker = hero.get("ticker") or ""
    amount = hero.get("amount") or ""
    verb = hero.get("verb") or ""
    if amount and verb == "砍":
        # 短句實驗：大字是金額動作；不要用已疲勞的「誰在收」。
        return f"砍{amount}", "誰在買?"
    if amount and verb == "買":
        label = f"{ticker} {name}".strip() if ticker else name
        return label, f"{verb}{amount}"
    if amount:
        return name, amount
    return name, "還紅?"


def align_ab_overlays(scenarios, titles, structured_data=None):
    """A = complete 股名+金額; B = fragment of the same hero. Same stock, same number."""
    titles = titles or []
    hero = parse_title_hero(titles[0] if titles else "", structured_data)
    if not hero.get("amount") and len(titles) > 1:
        extra = parse_title_hero(titles[1], structured_data)
        if extra.get("amount"):
            hero = extra
    if not hero.get("name") and not hero.get("ticker"):
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
        out.append(row)
    return out


def _buy_amount_for_stock(stock_text, structured_data):
    stock_name = _stock_name_only(stock_text)
    for item in structured_data.get("safe_havens_bought") or []:
        item_text = str(item)
        if stock_name and stock_name in item_text:
            return _first_amount_text(item_text)
    return _first_amount_text(" ".join(str(item) for item in structured_data.get("safe_havens_bought") or []))


def _sell_amount_for_stock(stock_text, structured_data):
    stock_name = _stock_name_only(stock_text)
    for item in structured_data.get("heavyweights_dumped") or []:
        item_text = str(item)
        if stock_name and stock_name in item_text:
            return _first_amount_text(item_text)
    return _first_amount_text(" ".join(str(item) for item in structured_data.get("heavyweights_dumped") or []))


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


def _collect_scenario_candidates(structured_data, titles):
    structured_data = structured_data or {}
    titles = titles or ["台股今日分析"]
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
    main_stocks = formal_targets or buy_focus
    main_name = main_stocks[0] if main_stocks else "主力"
    index_move = _index_headline(structured_data)
    candidates = []

    for item in divergences:
        if not _is_off_board_accumulation(item):
            continue
        amount = _first_amount_text(item)
        focus_stock = _extract_stock_text_from_sentence(item) or main_name
        candidates.append(
            {
                "scenario_name": "榜外吸籌",
                "family": "off_board",
                "priority": 92,
                "overlay_primary": _stock_overlay_primary(focus_stock),
                "overlay_secondary": f"外資買{amount}" if amount else "高買低量",
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

    combined = " ".join(divergences + [str(item) for item in structured_data.get("safe_havens_bought") or []] + [str(item) for item in structured_data.get("heavyweights_dumped") or []])
    if "成交量" in combined and ("賣超" in combined or "重挫" in combined or "-" in combined):
        buy_amount = _buy_amount_for_stock(main_name, structured_data)
        candidates.append(
            {
                "scenario_name": "天量出貨",
                "family": "volume_trap",
                "priority": 88,
                "overlay_primary": _stock_overlay_primary(main_name),
                "overlay_secondary": f"外資買{buy_amount}" if buy_amount else "爆量觀察",
                "main_stocks": main_stocks,
                "scene": (
                    f"{main_name} 成為畫面唯一主角，資金光束集中灌入主角票據；"
                    "背景保留高量換手與賣壓，但不要蓋過股名主角。"
                ),
                "emotion": "主角浮現、資金集中、警戒",
                "visual_metaphor": "主角股票被資金光束點亮，旁邊是退場賣壓形成的市場風暴",
                "avoid": ["不要單純畫暴跌恐慌", "不要公司 Logo", "不要塞滿數字"],
            }
        )

    if index_move and main_name:
        index_dir = _index_direction_word(structured_data)
        if index_dir == "跌":
            candidates.append(
                {
                    "scenario_name": "大跌守線",
                    "family": "anomaly",
                    "priority": 86,
                    "overlay_primary": f"跌{index_move}",
                    "overlay_secondary": f"{_stock_name_only(main_name)}還紅？",
                    "main_stocks": main_stocks,
                    "scene": (
                        f"大盤重挫 {index_move}，但 {main_name} 卻逆勢站穩，"
                        "畫面要呈現市場恐慌與個股韌性的反差。"
                    ),
                    "emotion": "恐慌中的例外、追查原因",
                    "visual_metaphor": "烏雲籠罩的大盤背景中，一檔股票仍被聚光燈照亮",
                    "avoid": ["不要過度恐嚇", "不要公司 Logo", "不要固定左右分割"],
                }
            )
        elif index_dir == "漲":
            candidates.append(
                {
                    "scenario_name": "強彈追蹤",
                    "family": "anomaly",
                    "priority": 86,
                    "overlay_primary": f"漲{index_move}",
                    "overlay_secondary": f"{_stock_name_only(main_name)}先看？",
                    "main_stocks": main_stocks,
                    "scene": (
                        f"大盤強彈 {index_move}，資金聚光燈先打在 {main_name}，"
                        "畫面要呈現反攻日與核心標的的同步升溫。"
                    ),
                    "emotion": "升溫、追查、等待確認",
                    "visual_metaphor": "綠色反攻能量從大盤湧向被點亮的個股票據",
                    "avoid": ["不要畫成慶祝狂歡", "不要公司 Logo", "不要固定左右分割"],
                }
            )

    if sell_risk and (buy_focus or formal_targets):
        buy_stock = (buy_focus or formal_targets)[0]
        sell_stock = sell_risk[0]
        buy_amount = _buy_amount_for_stock(buy_stock, structured_data)
        sell_amount = _sell_amount_for_stock(sell_stock, structured_data)
        candidates.append(
            {
                "scenario_name": "資金換手",
                "family": "rotation",
                "priority": 84,
                "overlay_primary": f"{_stock_name_only(sell_stock)}被砍" if sell_stock else "權值被砍",
                "overlay_secondary": f"{_stock_name_only(buy_stock)}接棒？" if buy_stock else "誰接棒？",
                "main_stocks": [buy_stock, sell_stock],
                "scene": (
                    f"{_stock_name_only(sell_stock)} 遭外資提款、"
                    f"{_stock_name_only(buy_stock)} 被資金點亮，形成明顯的換手對比。"
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
                        f"焦點落在 {_stock_name_only(buy_stock)} 是否成為新觀察核心。"
                    ),
                    "emotion": "對撞、追查、等待確認",
                    "visual_metaphor": "兩股法人能量在大盤戰場中央正面对撞",
                },
            }
        )
        candidates.append(
            {
                "scenario_name": "砍一邊買一邊",
                "family": "rotation",
                "priority": 78,
                "overlay_primary": f"砍{sell_amount} 買{buy_amount}".strip(),
                "overlay_secondary": "法人對決",
                "main_stocks": [buy_stock, sell_stock],
                "scene": (
                    "同一個金融戰場中，一邊是被賣出的標的被紅色能量推離，"
                    "另一邊是被買進的標的被資金護盾托起。"
                ),
                "emotion": "反轉、對比、等待確認",
                "visual_metaphor": "紅色撤退能量與金色買進護盾在同一畫面對撞",
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
                    "交易現場表面看似平靜，但桌面下方有巨大的資金機械正在運轉，"
                    "暗示買超與成交量之間的不尋常落差。"
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
        if event_sell and event_buy and event_sell != event_buy:
            event_primary = f"{event_sell}{event_sell_amt}" if event_sell_amt else f"{event_sell}被提款"
            event_secondary = f"{event_buy}反攻！"
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

    candidates.append(
        {
            "scenario_name": "主力點火",
            "family": "fallback",
            "priority": 60,
            "overlay_primary": f"{_stock_name_only(main_name)} 點火？",
            "overlay_secondary": "誰在買？",
            "main_stocks": main_stocks,
            "scene": f"{main_name} 的交易票據被資金光束點亮，市場正在檢查這是短線追價還是真正建倉。",
            "emotion": "等待確認、升溫、追查買盤",
            "visual_metaphor": "被點亮的交易票據與等待驗證的成交量儀表",
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
):
    """
    Generate thumbnail scenario briefs locally with formula-fatigue rotation.
    """
    structured_data = structured_data or {}
    titles = titles or ["台股今日分析"]
    fatigue_state = build_fatigue_state(channel_story=channel_story)
    candidates = _collect_scenario_candidates(structured_data, titles)
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

    selected = align_ab_overlays(selected[:count], titles, structured_data)
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
        "Put them in a high-contrast dark or bright negative-space panel occupying about 35% of the image. "
        "Do not hide stock names in small background details. Do not replace stock names with generic words. "
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
    )

    return (
        "YouTube thumbnail background image, landscape 16:9 format. "
        "Create a flexible cinematic scene, not a fixed template and not a repeated layout. "
        f"{composition_directive} "
        f"{text_requirement}"
        f"Visual world: {visual_desc} "
        f"Paired title idea: '{title}'. "
        f"Main stock focus for the human editor: {stock_focus}. "
        f"Core situation: {scene} "
        f"Emotional tone: {emotion}. "
        f"Visual metaphor: {visual_metaphor}. "
        f"{character_rules}"
        "Use expressive silhouettes, gestures, and staged power dynamics, but no real person likenesses. "
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
    composition_label = scenario_brief.get("composition_label") or "未指定（應有 A/B 差異）"
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

- Main stocks: {", ".join(scenario_brief.get("main_stocks", [])) or "依標題與報告主角"}
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
):
    """
    Generate thumbnail prompts + title pairings for A/B testing.
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
        channel_story=channel_story,
    )

    print(f"\n🎨 正在產生 {len(scenario_briefs)} 份情境式縮圖 Prompt（手動生圖用）...")

    for i, scenario in enumerate(scenario_briefs):
        # 同一 visual theme 下也強制 A/B 構圖不同（鏡頭、主體比例、角色大小）
        scenario = _stamp_ab_composition(scenario, i)
        style = styles[i % len(styles)]
        # 判斷是 preset key 還是自訂描述
        preset = STYLE_PRESETS.get(style)
        style_label = preset["name"] if preset else f"自訂風格 {i+1}"

        title = scenario.get("paired_title") or (titles[i % len(titles)] if titles else "台股今日分析")
        short_title = title[:20] + "..." if len(title) > 20 else title

        from modules.report_paths import YOUTUBE_ARTIFACTS

        filename = YOUTUBE_ARTIFACTS.get(f"thumbnail_{i+1}", f"yt_thumbnail_prompt_{i+1}.md")
        output_path = os.path.join(reports_dir, filename)

        print(
            f"\n  🖼️  縮圖 Prompt {i+1}/{len(scenario_briefs)}: {style_label} / "
            f"{scenario.get('scenario_name', '情境')} / {scenario.get('composition_label', '構圖')}"
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

    print(f"\n  💡 建議：選擇 1 個標題 + 1 份 Prompt 手動生圖後上傳，")
    print(f"     48 小時後根據 CTR 數據決定是否替換。")
    print(f"{'='*60}\n")
