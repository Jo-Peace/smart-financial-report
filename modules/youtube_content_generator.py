"""
YouTube Content Package Generator
Generates CTR-optimized titles, description, hashtags, and pinned comment
for Taiwan stock analysis YouTube videos.
"""

import os
import datetime
from modules.gemini_client import default_model_name, generate_text
from modules.packaging_fatigue import build_fatigue_state, guard_youtube_titles
from modules.thumbnail_generator import generate_local_titles


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


def _validation_placement(structured_data, packaging_context=None):
    """Resolve validation_placement from structured_data / packaging text."""
    frame = (structured_data or {}).get("editorial_frame") or {}
    if isinstance(frame, dict):
        placement = str(frame.get("validation_placement") or "").strip().lower()
        if placement in {"lead", "brief", "omit"}:
            return placement
    # packaging_context.editorial_frame may be a pre-rendered markdown string
    text = ""
    if packaging_context:
        text = str(packaging_context.get("editorial_frame") or "")
    if "validation_placement=lead" in text or "驗收使用：lead" in text or "placement=lead" in text:
        return "lead"
    if "validation_placement=omit" in text or "驗收使用：omit" in text or "placement=omit" in text:
        return "omit"
    return "brief"


def _first_text(value):
    items = _as_list(value)
    if not items:
        return ""
    first = items[0]
    if isinstance(first, dict):
        ticker = str(first.get("id") or first.get("ticker") or "").strip()
        name = str(first.get("name") or "").strip()
        if ticker and name:
            return f"{ticker} {name}"
        return ticker or name or str(first)
    return str(first).strip()


def build_opening_hook(structured_data):
    """
    Build a data-backed sensational opening line for TOC / host cues.
    Prefer price-volume contradiction, then big buy/sell vs market surface.
    """
    structured_data = structured_data or {}
    div = _first_text(structured_data.get("price_volume_divergence"))
    buy = _first_text(structured_data.get("safe_havens_bought"))
    sell = _first_text(structured_data.get("heavyweights_dumped"))
    index_action = str(structured_data.get("index_action") or "").strip()

    if div:
        hook = div
        if len(hook) > 42:
            hook = hook[:39].rstrip() + "…"
        return f"今日主線矛盾：{hook}"
    if buy and sell:
        return f"今日主線矛盾：買超 {buy} vs 賣超 {sell}"
    if buy and index_action:
        return f"今日主線矛盾：{index_action}，外資卻先鎖 {buy}"
    if sell and index_action:
        return f"今日主線矛盾：{index_action}，外資先砍 {sell}"
    if index_action:
        return f"今日主線：{index_action}"
    return "今日主線：盤後最大可驗證矛盾"


def build_chapter_markers(structured_data, packaging_context=None):
    """YouTube description chapter list; opening depends on validation_placement."""
    placement = _validation_placement(structured_data, packaging_context)
    hook = build_opening_hook(structured_data)
    # Keep chapter titles short for mobile description readability.
    hook_short = hook
    if len(hook_short) > 28:
        hook_short = hook_short[:25].rstrip() + "…"

    if placement == "lead":
        return [
            "00:00 昨日預測驗收（說到做到）",
            "01:30 今日主軸與關鍵訊號",
            "04:00 成交量排名完整解析",
            "07:00 外資法人籌碼深挖",
            "10:00 明日觀察標的 + 停損設定",
            "12:00 操作建議",
        ]
    if placement == "omit":
        return [
            f"00:00 {hook_short}",
            "01:20 今日主軸與關鍵訊號",
            "04:00 成交量 / 籌碼深挖",
            "08:00 明日觀察標的 + 失效條件",
            "11:00 操作建議",
        ]
    # brief (default): lead with contradiction; validation is a mid beat only
    return [
        f"00:00 {hook_short}",
        "01:20 今日主軸與關鍵訊號",
        "03:30 上集一句驗收（可信度）",
        "04:30 成交量 / 籌碼深挖",
        "08:00 明日觀察標的 + 失效條件",
        "11:00 操作建議",
    ]


def _normalize_description_chapters(description, structured_data, packaging_context=None):
    """Rewrite description TOC so brief/omit never opens with 驗收 ritual."""
    text = (description or "").strip()
    if not text:
        return text
    markers = build_chapter_markers(structured_data, packaging_context)
    placement = _validation_placement(structured_data, packaging_context)

    # Replace a leading 00:00 line if it still starts with validation ritual.
    lines = text.splitlines()
    rewritten = []
    replaced_zero = False
    for line in lines:
        stripped = line.strip()
        if (
            not replaced_zero
            and stripped.startswith("00:00")
            and placement != "lead"
            and any(token in stripped for token in ("昨日預測", "預測驗收", "上集驗收", "驗收（說到做到）"))
        ):
            rewritten.append(markers[0])
            replaced_zero = True
            continue
        rewritten.append(line)

    text2 = "\n".join(rewritten)
    # If Gemini omitted chapters entirely, append the local TOC before disclaimer.
    if "00:00" not in text2:
        insert = "\n".join(markers)
        if "⚠️" in text2:
            text2 = text2.replace("⚠️", insert + "\n\n⚠️", 1)
        else:
            text2 = text2.rstrip() + "\n\n" + insert + "\n"
    return text2


def generate_youtube_package(gemini_api_key, structured_data, report_content, date_str, packaging_context=None):
    """
    Generates a complete YouTube upload package using Gemini.

    Returns dict with:
        titles         - list of 2 title variants (A/B)
        description    - full SEO-optimized description
        pinned_comment - pinned comment template
    """
    model_name = default_model_name()

    date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    short_date = f"{date_obj.month}/{date_obj.day}"  # e.g. "4/23"

    ai_picks = structured_data.get("ai_data_picks", [])
    index_action = structured_data.get("index_action", "")
    safe_havens = structured_data.get("safe_havens_bought", [])
    heavyweights_dumped = structured_data.get("heavyweights_dumped", [])
    price_divergence = structured_data.get("price_volume_divergence", [])
    conservative = structured_data.get("conservative_strategy", "")
    aggressive = structured_data.get("aggressive_strategy", "")
    packaging_context = packaging_context or {}
    validation_summary = packaging_context.get("validation_summary", "")
    analytics_summary = packaging_context.get("analytics_summary", "")
    youtube_performance_patterns = packaging_context.get("youtube_performance_patterns", "")
    channel_story = packaging_context.get("channel_story", "")
    title_packaging_rules = packaging_context.get("title_packaging_rules", "")
    thumbnail_testing_rules = packaging_context.get("thumbnail_testing_rules", "")
    packaging_playbook = packaging_context.get("packaging_playbook", "")
    skill_context = packaging_context.get("skill_context", "")
    editorial_frame = packaging_context.get("editorial_frame", "")
    event_radar = packaging_context.get("event_radar", "")
    title_fatigue_report = packaging_context.get("title_fatigue_report", "")
    local_market_memory = packaging_context.get("local_market_memory", "")
    stock_role_taxonomy = packaging_context.get("stock_role_taxonomy", "")
    audience_profile = packaging_context.get("audience_profile", "")
    placement = _validation_placement(structured_data, packaging_context)
    opening_hook = build_opening_hook(structured_data)
    chapter_markers = "\n".join(build_chapter_markers(structured_data, packaging_context))
    if placement == "lead":
        opening_rule = (
            "今日 validation_placement=lead：章節 00:00 可用「昨日預測驗收」，"
            "但開場 15 秒仍要先丟一個可驗證數字，再接驗收。"
        )
    elif placement == "omit":
        opening_rule = (
            "今日 validation_placement=omit：禁止章節或口播以「昨日預測驗收/上集點名」開場；"
            f"00:00 必須是今日主線矛盾。建議開場句素材：{opening_hook}"
        )
    else:
        opening_rule = (
            "今日 validation_placement=brief：禁止 00:00 用「昨日預測驗收」當開場儀式；"
            f"00:00 必須先丟今日最大矛盾（建議素材：{opening_hook}）；"
            "驗收最多放在 03:30 左右用 1 句帶過。"
        )

    prompt = f"""你是一位專業的 YouTube 頻道成長策略師，深度熟悉台灣股市投資者的行為心理與 YouTube 演算法。

===今日核心數據（必須用這些數據，不得捏造）===
大盤狀況：{index_action}
AI觀察焦點：{', '.join(ai_picks) if ai_picks else '無明確標的'}
法人買超或強勢板塊：{', '.join(safe_havens) if safe_havens else '分散'}
法人賣超或弱勢標的：{', '.join(heavyweights_dumped) if heavyweights_dumped else '無明顯'}
量價背離現象：{', '.join(price_divergence) if price_divergence else '無明顯背離'}
穩健策略：{conservative}
波段策略：{aggressive}

===本機正式驗收結果（可信度資料；是否當標題主鉤子看主軸分類 validation_placement）===
{validation_summary if validation_summary else '（無本機驗收資料）'}
規則：若 validation_placement 不是 lead，標題禁止用「驗收/上集點名/驗收現場」當主鉤子；最多內文一句帶過。

===近期頻道敘事與觀看數（用來避免重複、延續有效敘事）===
{channel_story if channel_story else '（無頻道敘事紀錄）'}

===近期 Analytics / YouTube 學習（900 views 以上才是有效樣本）===
{analytics_summary if analytics_summary else '（無 Analytics 摘要）'}

===YouTube 成效提煉規則（高權重；由 channel_story_log 自動整理）===
{youtube_performance_patterns if youtube_performance_patterns else '（無 YouTube 成效提煉規則）'}

===本頻道觀眾偏好與包裝規則===
{audience_profile if audience_profile else '觀眾偏好：具體點名個股、外資買賣金額、量價矛盾、情境推演；驗收結果適度即可，不要每天當主鉤子。'}

===本地主軸分類（最高優先，不得自行升級成警報）===
{editorial_frame if editorial_frame else '（無本地主軸分類）'}

===主動消息面事件雷達（若有高分事件，標題可用事件當主軸）===
{event_radar if event_radar else '（無高分供應鏈事件）'}

===近期標題疲勞檢查（避免連續使用同一套情緒公式）===
{title_fatigue_report if title_fatigue_report else '（無標題疲勞檢查）'}

{title_packaging_rules if title_packaging_rules else ''}

===YouTube 包裝複利 Playbook（高權重，需延續有效縮圖/標題版型）===
{packaging_playbook if packaging_playbook else '（無包裝 playbook）'}

===本地技能指引===
{skill_context if skill_context else '（無本地技能指引）'}

===縮圖 A/B 測試規則（需讓標題服務縮圖，避免每天推倒版型）===
{thumbnail_testing_rules if thumbnail_testing_rules else '（無縮圖測試規則）'}

===本機市場記憶與個股角色分類（用來判斷誰適合當標題主角）===
{local_market_memory if local_market_memory else '（無本機市場記憶）'}

{stock_role_taxonomy if stock_role_taxonomy else '（無個股角色分類規則）'}

===今日報告開場段落（用來抓取核心鉤子）===
{report_content[:1000]}

===標題撰寫規則（最關鍵）===
只生成兩個標題，方便累積乾淨的 A/B Test：
- 標題A：主推高勝率公式。優先檢查今日是否有重大「時事槓桿」；若有，標題A必須從時事切入，再連到台股風險/機會。若沒有時事槓桿，再檢查是否可呼應上集或昨日預測；最後才使用「大盤反差 + 核心股外資金額 + 隱藏買盤標的」。
  範例格式：「上集說XX是地雷，但外資今天砸了XX億進去！這是接刀還是提前卡位？｜{short_date} AI帶你看股市」
- 標題B：探索替換版。使用不同角度的好奇心缺口或數據衝擊，作為 48 小時後可替換版本。
  範例格式：「XX億！外資狂砍權值股，卻反手鎖定這兩檔記憶體｜{short_date} AI帶你看股市」

===已驗證較有效的頻道標題公式（高權重參考）===
學習門檻：觀看數低於 900 的影片只能視為觀察樣本，不可作為 A/B Test 勝負依據。只有 900 觀看以上的案例，才視為可強化的標題/封面公式。

近期表現較好的標題使用了這種結構：
「台股大漲/大跌卻暗藏殺機？台積電大漲但外資狂砍XX億！主力反手鎖定『這檔』記憶體股｜AI帶你看股市」

另一個表現較好的標題使用了「上集驗收 / 連續劇」結構：
「上集說XX是地雷，但外資今天砸了XX億進去！股價卻繼續跌——這是接刀還是提前卡位？」

另一個高觀看樣本使用了「節假日後風險預警」結構：
「228連假後台股崩盤預警!?」

目前最高觀看樣本使用了「地緣政治 / 美股血洗 / 台股風險傳導」結構：
「戰火引爆美股血洗！台積電 ADR 暴跌 9.5% 週一台股開盤面臨斷頭危機？資金全逃去哪了！」

請優先模仿這個邏輯，而不是照抄文字：
1. 先判斷今天是哪一種內容日：驗收日、重大事件日、異常日、續航日、主題日、整理日。
2. 若有重大時事槓桿：戰爭、美股暴跌、台積電 ADR、輝達、VIX、Fed、匯率、關稅、長假、重大財報、台指期夜盤，標題A順序為：時事事件 → 台股會怎樣 → 核心標的/數字 → 散戶該怕什麼或看什麼。
3. 若今日資料能呼應昨日或上集的預測，標題可使用「上集說過 / 昨天點名 / 真的驗收」結構，讓觀眾感覺這不是單集資訊，而是連續追蹤。
4. 只有在法人與成交量/股價有明確反直覺數字時，才使用「背離、警訊、誰在賣、誰接手」語氣。
5. 只有連續 2-3 天有法人、量能、價格結構延續時，才使用「續航、接棒、主力還在」語氣。
6. 若沒有強訊號，不要硬做警報標題；改用「今天不是追強日」「這幾檔等確認」「訊號普通但觀察條件很清楚」。
7. 若沒有時事槓桿，再給大盤表面現象：狂飆、重挫、翻紅、收黑；但不要每次都接「暗藏殺機」，除非數據真的支持。
8. 放入最有辨識度的大型股或核心標的，例如台積電，並附上真實外資買賣超金額。
9. 最後給觀眾一個想點進來的答案缺口：這檔、這兩檔、記憶體、AI伺服器、ETF 等具體題材。
10. 不要只寫「資金輪動」。若要表達資金移動，請寫成具體行為：外資砍台積電、投信買超、三大法人反手買、成交量爆出異常。
11. 連續劇標題要有明確驗收點：上集說了什麼、今天發生什麼、外資買賣多少、股價是否反著走。
12. 若今天接近長假、休市、重大總經事件或連假後開盤，標題A可使用「節假日後風險預警」結構，但必須搭配具體籌碼、期貨、美股或權值股風險證據。
13. 若美股、VIX、台積電 ADR、輝達、油金、地緣政治出現重大異常，標題A可使用「全球風險傳導到台股」結構：全球觸發事件 + 台灣核心資產跌幅 + 下一交易日風險 + 投資人該追問的問題。

標題硬規則：
1. 每個標題 35-60 字（含符號）；若今日數據很強，可以略長，但前半段必須完整有鉤子
2. 前 22 字是黃金區——必須出現大盤反差、核心股、或外資金額之一
3. 末尾固定加「｜AI帶你看股市」作為品牌識別
4. 數字必須來自今日真實數據，不得捏造
5. 繁體中文，禁止「穩了」「嗨了」「躺平」等中國大陸流行語
6. 可加 1-2 個 emoji，放最前面或最後面（末尾品牌標語前）
7. 不要使用「資金輪動」；標題要講具體事件，例如三大法人買賣超、成交量、量價背離。
8. 標題必須至少點名 1 檔具體股票或 1 個具體族群；若標題點名族群，描述第一行或第二行必須補上個股。
9. 若使用「外資已買 / 偷偷買 / 掃貨」等語氣，必須在標題或描述中放入實際金額或正式資料依據。
10. 若核心股是權值大股但彈性有限，標題應區分「看方向」與「看彈性」，不要把權值核心包裝成最大飆股。
11. 不要每天套同一種危機語氣；若日報主軸是整理日或觀察日，標題也必須承認訊號尚未確認。
12. 若「近期包裝疲勞檢查」列出禁用詞或禁用封面題型，標題A與標題B都不得再使用那些詞，也不得只把字換成同義詞（例如榜外吸籌→沒進榜、被砍→提款、接棒→換車）。
13. 標題A與標題B必須使用不同題型：不可兩個都寫「外資砍X卻買Y」；至少一個要改成驗收、逆勢原因、事件衝擊、明日失效條件或量價矛盾。
14. 若連續多日都在講法人換手，今天改問：為什麼逆勢、量價哪裡不一致、哪個價位失效，而不是再問「誰接棒」。

===描述撰寫規則===
第1行（≤55字）：今日主軸或關鍵訊號 + 核心數字 + 日期（包含「台股」）
第2行（≤55字）：AI觀察焦點 + 對散戶的實際意義
第3行：訂閱召喚 + 開啟通知提示

開場節奏（對齊影片前 30 秒，描述章節也要一致）：
{opening_rule}
- 0～8 秒：最大矛盾（股名 + 金額/漲跌 + 反差）；只能用今日真實數據
- 8～20 秒：一句可被影片回答的問句
- 20～40 秒：今日主線一句話（續航/事件/異常/整理…）
- 禁止空泛恐嚇（「崩盤」「逃命」）若沒有對應數字
- 恐懼必須有出口：明天看什麼、哪個價位失效

空一行，加章節標記（請完整照抄下列時間軸，不要自行改回「昨日預測驗收」開場，除非 placement=lead）：
{chapter_markers}

空一行，加免責聲明（繁體中文，一行）：
⚠️ 本節目所有內容僅供參考，不構成任何投資建議，請務必自行判斷風險。

空一行，加 hashtag（6-8個，最相關的放前面）：
#台股 #台股分析 #AI選股 等

===置頂留言規則===
60-90 字，格式：
1. 先呼應開場矛盾拋出開放性問題（不要先問「上集有沒有驗收」）
2. 邀請觀眾分享自己的持股或看法
3. 說明留言區的用途（博士和阿明會看）
4. 加 2-3 個相關 emoji

===輸出格式（嚴格遵守，不得添加任何說明文字或markdown標記）===

###TITLE_A
（標題A）
###TITLE_B
（標題B）
###DESCRIPTION
（完整描述，包含所有段落和hashtag）
###PINNED_COMMENT
（置頂留言）
###END
"""

    try:
        output = generate_text(
            gemini_api_key,
            prompt,
            task_name="youtube_package",
            model_name=model_name,
        ).strip()

        sections = {"TITLE_A": [], "TITLE_B": [], "DESCRIPTION": [], "PINNED_COMMENT": []}
        current = None

        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith("###END"):
                if current and sections.get(current) is not None:
                    pass
                break
            if stripped.startswith("###"):
                key = stripped.replace("###", "").strip()
                current = key if key in sections else None
                continue
            if current is not None:
                sections[current].append(line)

        result = {
            "titles": [
                "\n".join(sections["TITLE_A"]).strip(),
                "\n".join(sections["TITLE_B"]).strip(),
            ],
            "description": "\n".join(sections["DESCRIPTION"]).strip(),
            "pinned_comment": "\n".join(sections["PINNED_COMMENT"]).strip(),
        }
        result["titles"] = [t for t in result["titles"] if t]
        channel_story = (packaging_context or {}).get("channel_story", "")
        fatigue_state = build_fatigue_state(channel_story=channel_story)
        local_candidates = generate_local_titles(
            structured_data,
            num_titles=4,
            date_label=short_date,
            channel_story=channel_story,
        )
        if result["titles"]:
            result["titles"] = guard_youtube_titles(
                result["titles"],
                fatigue_state=fatigue_state,
                local_candidates=local_candidates,
                short_date=short_date,
            )
        if result.get("description"):
            result["description"] = _normalize_description_chapters(
                result["description"],
                structured_data,
                packaging_context,
            )
        return result

    except Exception as e:
        print(f"  [Error] YouTube 內容生成失敗: {e}")
        return {"titles": [], "description": "", "pinned_comment": ""}


def _stock_label(value):
    if isinstance(value, dict):
        ticker = str(value.get("id") or value.get("ticker") or "").strip()
        name = str(value.get("name") or "").strip()
        if ticker and name:
            return f"{ticker} {name}"
        return ticker or name
    return str(value).strip()


def _first_non_empty(*values, default="今日主軸"):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and value:
            first = _stock_label(value[0])
            if first:
                return first
    return default


def build_fallback_youtube_package(structured_data, report_content, date_str, reason="Gemini 生成失敗"):
    """Build a conservative local YouTube package when Gemini is unavailable.

    This is intentionally plain. Its job is to keep /2 usable during 429/rate-limit
    periods without inventing numbers or over-packaging weak signals.
    """
    date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    short_date = f"{date_obj.month}/{date_obj.day}"
    structured_data = structured_data or {}

    index_action = _first_non_empty(structured_data.get("index_action"), default="台股盤後")
    targets = structured_data.get("prediction_targets") or []
    ai_picks = _as_list(structured_data.get("ai_data_picks"))
    safe_havens = _as_list(structured_data.get("safe_havens_bought"))
    heavyweights = _as_list(structured_data.get("heavyweights_dumped"))
    divergence = _as_list(structured_data.get("price_volume_divergence"))
    validation = _as_list(structured_data.get("prev_day_picks_result"))

    main_target = _stock_label(targets[0]) if targets else _first_non_empty(ai_picks, safe_havens, default="今日觀察標的")
    counter_target = _first_non_empty(heavyweights, divergence, default="弱勢標的")
    hook = validation[0] if validation else index_action
    if len(hook) > 32:
        hook = hook[:32].rstrip() + "..."

    from modules.packaging_fatigue import build_fatigue_state, guard_youtube_titles

    fatigue_state = build_fatigue_state()
    local_candidates = generate_local_titles(
        structured_data,
        num_titles=4,
        date_label=short_date,
    )
    fallback_titles = guard_youtube_titles(
        local_candidates,
        fatigue_state=fatigue_state,
        local_candidates=local_candidates,
        short_date=short_date,
    )
    if len(fallback_titles) < 2:
        fallback_titles.extend(
            [
                f"{short_date} 台股盤後｜{main_target}明日先看哪個條件？｜{short_date} AI帶你看股市",
                f"{short_date} 台股｜{index_action}，{main_target}對照{counter_target}｜{short_date} AI帶你看股市",
            ]
        )
        fallback_titles = guard_youtube_titles(
            fallback_titles,
            fatigue_state=fatigue_state,
            local_candidates=local_candidates,
            short_date=short_date,
        )
    title_a = fallback_titles[0] if fallback_titles else f"{main_target}明日觀察條件？｜{short_date} AI帶你看股市"
    title_b = fallback_titles[1] if len(fallback_titles) > 1 else title_a

    chapters = build_chapter_markers(structured_data)
    opening_hook = build_opening_hook(structured_data)
    desc_lines = [
        f"{short_date} 台股盤後：{index_action}",
        f"今日先看 {main_target}，再對照 {counter_target} 的籌碼與量價變化。",
        "訂閱 AI帶你看股市，開啟通知一起做 1D / 3D / 5D 驗收。",
        "",
        *chapters,
        "",
        "⚠️ 本節目所有內容僅供參考，不構成任何投資建議，請務必自行判斷風險。",
        "",
        "#台股 #台股分析 #AI選股 #法人籌碼 #成交量 #投資理財",
        "",
        f"Fallback note: {reason}，本段由本機資料保守產生，未額外呼叫 Gemini。",
    ]
    pinned_comment = (
        f"{opening_hook} 你會先追 {main_target}，還是先避開 {counter_target}？"
        "留言告訴博士和阿明你的觀察，我們會用後續數據一起驗收。"
    )

    return {
        "titles": [title_a, title_b],
        "description": "\n".join(desc_lines),
        "pinned_comment": pinned_comment,
        "fallback": True,
        "fallback_reason": reason,
        "fallback_hook": hook,
    }


def _build_sand_table_section(structured_data):
    structured_data = structured_data or {}
    targets = structured_data.get("prediction_targets") or []
    first_target = targets[0] if targets and isinstance(targets[0], dict) else {}
    safe_havens = _as_list(structured_data.get("safe_havens_bought"))
    heavyweights = _as_list(structured_data.get("heavyweights_dumped"))
    recent_trends = structured_data.get("recent_trends") or {}
    short_window = structured_data.get("short_term_validation_window") or "1D / 3D / 5D"
    long_window = structured_data.get("long_term_validation_window") or "20D / 60D"
    horizon = structured_data.get("short_term_horizon") or "1~5日"
    long_horizon = structured_data.get("long_term_horizon") or "2~6週 / 1~3個月"

    def _latest_price_for(ticker, fallback=None):
        trend = recent_trends.get(str(ticker)) or {}
        latest_price = trend.get("latest_price")
        if isinstance(latest_price, (int, float)):
            return float(latest_price)
        if isinstance(fallback, (int, float)):
            return float(fallback)
        return None

    def _simulate_100w(target):
        if not isinstance(target, dict):
            return None
        ticker = str(target.get("id", "")).strip()
        name = target.get("name") or ticker
        direction = target.get("direction") or "多"
        current_price = _latest_price_for(ticker, target.get("price"))
        stop_price = target.get("stop_loss_price")
        if not current_price or not isinstance(stop_price, (int, float)):
            return None
        shares = 1_000_000 / current_price
        stop_value = shares * float(stop_price)
        stop_pnl = stop_value - 1_000_000
        stop_pct = (float(stop_price) / current_price - 1) * 100
        trend = recent_trends.get(str(ticker)) or {}
        trend_5d = ((trend.get("trends") or {}).get("5D") or {}).get("pct_change")
        trend_10d = ((trend.get("trends") or {}).get("10D") or {}).get("pct_change")
        trend_bits = []
        if isinstance(trend_5d, (int, float)):
            trend_bits.append(f"若按 5D 驗證窗，約 {1_000_000 * trend_5d / 100:+,.0f} 元")
        if isinstance(trend_10d, (int, float)):
            trend_bits.append(f"10D 約 {1_000_000 * trend_10d / 100:+,.0f} 元")
        trend_text = f"；{'；'.join(trend_bits)}" if trend_bits else ""
        return {
            "name": name,
            "ticker": ticker,
            "direction": direction,
            "current_price": current_price,
            "shares": shares,
            "stop_price": float(stop_price),
            "stop_value": stop_value,
            "stop_pnl": stop_pnl,
            "stop_pct": stop_pct,
            "trend_text": trend_text,
        }

    target_name = first_target.get("name") or _stock_label(targets[0]) if targets else ""
    target_dir = first_target.get("direction") or ""
    trigger = first_target.get("trigger") or "以今天的法人買賣超、量價結構與均線位置作為觸發"
    invalidation = first_target.get("stop_loss_desc") or (
        f"收盤跌破 {first_target.get('stop_loss_price')}" if first_target.get("stop_loss_price") else "若明日無法延續就視為失效"
    )

    safe_haven_line = safe_havens[0] if safe_havens else "金融或防禦族群"
    heavyweight_line = heavyweights[0] if heavyweights else "今日主要權值"

    if not target_name and safe_havens:
        target_name = _stock_label(safe_havens[0])
    if not target_name:
        target_name = "今日主軸標的"

    sim_lines = []
    sim_rows = []
    for target in targets[:2]:
        sim = _simulate_100w(target)
        if sim:
            sim_rows.append(sim)
    if sim_rows:
        sim_lines.append("- 100 萬模擬倉位：")
        for sim in sim_rows:
            sim_lines.append(
                f"  - {sim['ticker']} {sim['name']}：若 100 萬在 {sim['current_price']:.1f} 元進場，約可買 {sim['shares']:.1f} 股；"
                f"若以停損價 {sim['stop_price']:.1f} 元出場，帳面約剩 {sim['stop_value']:,.0f} 元，"
                f"損益約 {sim['stop_pnl']:+,.0f} 元（{sim['stop_pct']:+.2f}%）{sim['trend_text']}"
            )

    lines = [
        "## 🎯 沙盤推演（預測部分）",
        "",
        "以下內容不是單純看漲跌，而是把今天的盤面拆成可驗證的三種情境。",
        "",
        f"- 今日主觀察標的：{target_name}{f'（{target_dir}）' if target_dir else ''}",
        f"- 基準情境：{target_name} 依照今天的法人與量價訊號，先走 {horizon} 內的驗證路徑。",
        f"- 樂觀情境：{safe_haven_line} 延續承接力，資金若從弱勢權值轉向防禦/金融，盤面可先穩住。",
        f"- 悲觀情境：{heavyweight_line} 若持續被提款，且今日強弱對比沒有修復，修正會延長。",
        f"- 觸發條件：{trigger}",
        f"- 失效條件：{invalidation}",
        f"- 驗收窗口：短線 {short_window}；中期 {long_window}",
        f"- 觀察重點：今天不是只看單日漲跌，而是看籌碼是否能把趨勢推進到下一個驗收窗。",
        *sim_lines,
        "",
    ]
    return "\n".join(lines)


def save_youtube_package(result, reports_dir, date_str, structured_data=None):
    """Save the YouTube package to a markdown file and return the filepath."""
    from modules.report_paths import youtube_artifact_write_path

    filepath = youtube_artifact_write_path(reports_dir, date_str, "package")

    lines = [f"# YouTube 上架素材包 {date_str}\n"]

    lines.append("## 📝 A/B 標題選項（先用 A，48 小時後再判斷是否換 B）\n")
    labels = ["A（主推高勝率公式）", "B（探索替換版）"]
    for i, title in enumerate(result.get("titles", [])[:2]):
        label = labels[i] if i < len(labels) else str(i + 1)
        lines.append(f"**標題 {label}：**\n```\n{title}\n```\n")

    lines.append("---\n\n")
    lines.append(_build_sand_table_section(structured_data))

    lines.append("---\n\n## 📄 影片描述\n\n```")
    lines.append(result.get("description", "（未生成）"))
    lines.append("```\n")

    lines.append("---\n\n## 💬 置頂留言（上傳後10分鐘內貼上效果最好）\n\n```")
    lines.append(result.get("pinned_comment", "（未生成）"))
    lines.append("```\n")

    lines.append("---\n\n## 📌 上架流程提醒\n")
    lines.append("1. 先用標題 A 上傳")
    lines.append("2. 在 Canva 打開縮圖背景，加上今日標題文字")
    lines.append("3. 上傳影片，貼上描述和縮圖")
    lines.append("4. 發布後立即貼置頂留言")
    lines.append("5. 觀看數低於 900 不判斷 A/B 勝負，只記錄觀察")
    lines.append("6. 48小時後若 views >= 900 且 CTR < 5%，再測 B 組\n")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return filepath
