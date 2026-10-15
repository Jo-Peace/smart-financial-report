"""
YouTube Content Package Generator
Generates CTR-optimized titles, description, hashtags, and pinned comment
for Taiwan stock analysis YouTube videos.
"""

import os
import re
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
        return f"今日主線矛盾：{index_action}，外資卻先買 {buy}"
    if sell and index_action:
        return f"今日主線矛盾：{index_action}，外資先砍 {sell}"
    if index_action:
        return f"今日主線：{index_action}"
    return "今日主線：盤後最大可驗證矛盾"


# Chapter plans follow the NotebookLM program order (notebooklm_generator
# _program_order_block): (start share of the video, title).
_CHAPTER_PLANS = {
    "lead": [
        (0.00, "昨日預測驗收（說到做到）"),
        (0.20, "今日主軸與關鍵訊號"),
        (0.35, "成交量排名完整解析"),
        (0.55, "外資法人籌碼深挖"),
        (0.75, "明日觀察標的 + 停損設定"),
        (0.95, "操作建議"),
    ],
    "omit": [
        (0.00, None),
        (0.10, "今日主軸與關鍵訊號"),
        (0.20, "成交量 / 籌碼深挖"),
        (0.70, "明日觀察標的 + 失效條件"),
        (0.95, "操作建議"),
    ],
    "brief": [
        (0.00, None),
        (0.10, "今日主軸與關鍵訊號"),
        (0.20, "上集一句驗收（可信度）"),
        (0.30, "成交量 / 籌碼深挖"),
        (0.75, "明日觀察標的 + 失效條件"),
        (0.95, "操作建議"),
    ],
}
_MIN_CHAPTER_SEC = 10
_TIMESTAMP_LINE = re.compile(r"^\s*(?:\d{1,2}:)?\d{1,2}:\d{2}\s+\S")
# Viewer-facing (it is published with the description). The "no timestamps"
# instruction lives only in the youtube_package.md checklist (step 4.5).
_OUTLINE_HEADER = "📌 節目段落："
# Older packages / Gemini copies still carry the internal-instruction header.
_OUTLINE_HEADERS = frozenset({_OUTLINE_HEADER, "📌 節目段落（未提供影片長度，未加時間軸；上傳前可依實際長度補上）："})


def parse_video_duration(value) -> int | None:
    """「754」「12:34」「1:02:03」「12m34s」 → seconds; None when unknown."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    text = str(value).strip().lower()
    if not text:
        return None
    if text.isdigit():
        return int(text) or None
    if re.fullmatch(r"\d{1,2}(?::\d{1,2}){1,2}", text):
        total = 0
        for part in text.split(":"):
            total = total * 60 + int(part)
        return total or None
    m = re.fullmatch(r"(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?", text)
    if m and any(m.groups()):
        h, mi, sec = (int(g or 0) for g in m.groups())
        return h * 3600 + mi * 60 + sec or None
    return None


def resolve_video_duration(packaging_context=None, video_duration_sec=None) -> int | None:
    for candidate in (
        video_duration_sec,
        (packaging_context or {}).get("video_duration_sec"),
        os.getenv("YOUTUBE_VIDEO_DURATION"),
    ):
        seconds = parse_video_duration(candidate)
        if seconds:
            return seconds
    return None


def _format_timestamp(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, sec = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def build_chapter_markers(structured_data, packaging_context=None, video_duration_sec=None):
    """
    YouTube description chapters. Timestamps are derived from the real video
    length (``video_duration_sec`` / packaging_context / env
    ``YOUTUBE_VIDEO_DURATION``) and the program order shares; without a length
    the chapters are an untimed outline instead of fixed, usually-wrong times.
    """
    placement = _validation_placement(structured_data, packaging_context)
    hook = build_opening_hook(structured_data)
    # Keep chapter titles short for mobile description readability.
    hook_short = hook
    if len(hook_short) > 28:
        hook_short = hook_short[:25].rstrip() + "…"
    plan = _CHAPTER_PLANS.get(placement) or _CHAPTER_PLANS["brief"]
    titles = [(share, title or hook_short) for share, title in plan]
    duration = resolve_video_duration(packaging_context, video_duration_sec)
    if not duration:
        return [_OUTLINE_HEADER] + [f"・{title}" for _share, title in titles]
    markers = []
    last = None
    for share, title in titles:
        start = int(round(duration * share))
        if last is not None and (start - last < _MIN_CHAPTER_SEC or duration - start < _MIN_CHAPTER_SEC):
            continue
        markers.append(f"{_format_timestamp(start)} {title}")
        last = start
    if len(markers) < 3:
        # YouTube needs >= 3 chapters of >= 10s; too short a video gets the outline.
        return [_OUTLINE_HEADER] + [f"・{title}" for _share, title in titles]
    return markers


def _normalize_description_chapters(description, structured_data, packaging_context=None, video_duration_sec=None):
    """
    Replace whatever chapter block Gemini wrote with the local one, so the
    timestamps always come from the real video length (or are omitted).
    """
    text = (description or "").strip()
    if not text:
        return text
    markers = build_chapter_markers(structured_data, packaging_context, video_duration_sec)
    lines = text.splitlines()
    out = []
    inserted = False
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        is_block = _TIMESTAMP_LINE.match(stripped) or stripped in _OUTLINE_HEADERS
        if is_block:
            while index < len(lines):
                cur = lines[index].strip()
                if _TIMESTAMP_LINE.match(cur) or cur in _OUTLINE_HEADERS or (cur.startswith("・") and len(cur) <= 40):
                    index += 1
                    continue
                break
            if not inserted:
                out.extend(markers)
                inserted = True
            continue
        out.append(line)
        index += 1
    text2 = "\n".join(out)
    if not inserted:
        insert = "\n".join(markers)
        if "⚠️" in text2:
            text2 = text2.replace("⚠️", insert + "\n\n⚠️", 1)
        else:
            text2 = text2.rstrip() + "\n\n" + insert + "\n"
    return text2


def build_horizon_packaging_rule(structured_data):
    """
    Time-window rule for the /2 packaging prompt, gated on the session ledger.

    A preferred window (20D-led titles) is allowed only when
    horizon_reliability.primary_leads_from_structured() is True, i.e. the 20D
    excess-hit CI lower bound beats the 50% baseline and leads 1D/3D/5D.
    Otherwise the prompt gets the neutral line and an explicit ban on
    「20D 尺子」 / 「短線噪音」 framing. The analytics_summary and compounding
    brief in the same prompt carry raw rates (a stale generated brief may
    still hold the legacy 20D claim), so Gemini must not judge from them.
    """
    from modules.horizon_reliability import current_reliability_line, primary_leads_from_structured

    line = current_reliability_line(structured_data)
    common = (
        "隔日／3日結果最多當節奏鉤，不要用「上一交易日點名今天就該噴」當唯一勝敗。"
        "週五禁止標題寫「明天開盤」；週一禁止「昨天開盤」。"
    )
    if primary_leads_from_structured(structured_data):
        return (
            f"本機回測結論：{line}\n"
            "此結論已通過統計門檻（20D 信賴區間下界高於基準且領先短線窗）：標題主角與中期追蹤可參考 20D 可講的故事；"
            "口播若提及，只能照上句的數字與「目前樣本」語氣，不可說成保證。\n"
            f"{common}"
        )
    return (
        f"本機回測結論：{line}\n"
        "未通過統計門檻：不可在標題、描述、置頂留言或口播稱任一時間窗（包括 20D）較準、是成敗標準，"
        "也不可把隔日失準說成「只是短線雜訊」；不要依 Analytics 摘要或複利簡報裡的原始命中率自行判斷哪個窗較準。\n"
        f"{common}"
    )
# ---------------------------------------------------------------------------
# Official watch consistency (description / pinned comment vs prediction_targets)
# ---------------------------------------------------------------------------

_WATCH_SENTENCE = re.compile(r"[^。！？!?\n]*(?:主盯|正式追蹤|追蹤標的|觀察標的)[^。！？!?\n]*")


def _official_watch(structured_data):
    out = []
    for target in _as_list((structured_data or {}).get("prediction_targets")):
        if isinstance(target, dict):
            ticker = str(target.get("id") or "").strip()
            name = str(target.get("name") or "").strip()
            if ticker or name:
                out.append((ticker, name))
    return out


def _known_stock_names(structured_data):
    from modules.broadcast_facts import normalize_name, parse_ticker_name

    structured_data = structured_data or {}
    names = {}

    def _add(name, ticker):
        name = normalize_name(name)
        if len(name) < 2:
            return
        if not names.get(name):
            names[name] = str(ticker or "")

    for ticker, name in _official_watch(structured_data):
        _add(name, ticker)
    for ticker, row in ((structured_data.get("local_market_facts") or {}).get("stocks") or {}).items():
        _add(row.get("name"), ticker)
    for row in structured_data.get("focus_ranking") or []:
        if isinstance(row, dict):
            _add(row.get("name"), row.get("ticker"))
    for key in ("safe_havens_bought", "heavyweights_dumped", "price_volume_divergence", "ai_data_picks"):
        for raw in _as_list(structured_data.get(key)):
            ticker, name = parse_ticker_name(str(raw))
            _add(re.split(r"[\s(（：:]", name)[0] if name else "", ticker)
    return names


_WATCH_KEYWORDS = ("主盯", "正式追蹤")
_CLAUSE_BREAK = re.compile(r"[，,；;]")
# 「台積電只當對照」「僅供參考」: the stock before the marker is a comparison, not a watch.
_CONTRAST_MARKER = re.compile(r"(?:只|僅|則|就)?(?:當|作為|做為|列為|供)?(?:對照|參考|比較|對比)")
_CONTRAST_PAREN = re.compile(r"[（(][^（）()]*(?:對照|參考|比較|對比)[^（）()]*[)）]")
_LIST_JOINER = re.compile(r"[、與和及而但]")
_WATCH_TICKER = re.compile(r"(?<![\d.])(\d{4,6}[A-Z]?)(?![\d.]|\s*(?:元|點|%|％|億|萬|張|股|/))")


def _watch_clauses(sentence):
    """The clauses of a 主盯 sentence that name the watch set (comparison stocks cut off)."""
    sentence = _CONTRAST_PAREN.sub("", sentence)
    out = []
    for clause in _CLAUSE_BREAK.split(sentence):
        if not any(key in clause for key in _WATCH_KEYWORDS):
            continue
        key_pos = min(clause.find(key) for key in _WATCH_KEYWORDS if key in clause)
        marker = _CONTRAST_MARKER.search(clause, key_pos)
        if marker:
            joiners = [m.start() for m in _LIST_JOINER.finditer(clause, key_pos, marker.start())]
            clause = clause[: joiners[-1] if joiners else marker.start()]
        out.append(clause)
    return out


def check_main_watch_consistency(texts, structured_data):
    """
    Warnings when the description / pinned comment name a 「主盯」 set that is
    not exactly the official watch list (prediction_targets).

    Only the 主盯 clause counts (it ends at ，；, and a stock marked 對照／參考
    is dropped), and a bare number counts only when it is a known ticker, so
    「失效價 8100」 or 「台積電只當對照」 never read as an extra watch.
    """
    official = _official_watch(structured_data)
    official_keys = {ticker or name for ticker, name in official}
    names = _known_stock_names(structured_data)
    known_tickers = {ticker for ticker, _name in official if ticker} | {t for t in names.values() if t}
    known_tickers |= set(((structured_data or {}).get("local_market_facts") or {}).get("stocks") or {})
    warnings = []
    mentioned = set()
    saw_watch_sentence = False
    for text in _as_list(texts):
        for m in _WATCH_SENTENCE.finditer(str(text or "")):
            sentence = m.group(0)
            if not any(key in sentence for key in _WATCH_KEYWORDS):
                continue
            saw_watch_sentence = True
            for clause in _watch_clauses(sentence):
                for name in sorted(names, key=len, reverse=True):
                    if name in clause:
                        mentioned.add(names[name] or name)
                        clause = clause.replace(name, "")
                for ticker in _WATCH_TICKER.findall(clause):
                    if ticker in known_tickers:
                        mentioned.add(ticker)
    if not saw_watch_sentence:
        return warnings
    label = {ticker or name: f"{ticker} {name}".strip() for ticker, name in official}
    by_key = {(ticker or name): f"{ticker} {name}".strip() for name, ticker in names.items()}
    extra = sorted(by_key.get(k, k) for k in mentioned if k not in official_keys)
    missing = [k for k in official_keys if k not in mentioned]
    if not official:
        warnings.append("描述／置頂留言寫了「主盯」，但今日 prediction_targets 為空（沒有正式追蹤檔）")
        return warnings
    if extra:
        warnings.append(f"描述／置頂留言主盯了非正式追蹤股：{'、'.join(extra)}（正式追蹤：{'、'.join(label.values())}）")
    if missing:
        warnings.append(f"描述／置頂留言主盯漏了正式追蹤股：{'、'.join(label[k] for k in missing)}")
    return warnings


MAJOR_EVENT_MIN_SCORE = 60


def is_verified_major_event_day(structured_data, packaging_context=None):
    """
    True only when the local editorial frame is 重大事件日 *and* the event radar
    holds a fresh (freshness=new) event scoring >= 60. Only then may the title
    prompt carry the event-led structure.
    """
    structured_data = structured_data or {}
    frame = structured_data.get("editorial_frame")
    is_event = isinstance(frame, dict) and frame.get("frame") == "major_event_day"
    if not is_event:
        text = str((packaging_context or {}).get("editorial_frame") or "")
        is_event = "major_event_day" in text or "今日主軸：重大事件日" in text
    if not is_event:
        return False
    for event in _as_list(structured_data.get("supply_chain_event_radar")):
        if not isinstance(event, dict):
            continue
        try:
            score = int(float(event.get("impact_score") or 0))
        except (TypeError, ValueError):
            continue
        if (event.get("freshness") or "new") == "new" and score >= MAJOR_EVENT_MIN_SCORE:
            return True
    return False


def _generate_package_text(gemini_api_key, prompt, model_name):
    """
    youtube_package call with retry/backoff on 429 / 5xx / timeout (no fixed
    pre-call sleep). The fallback model waits for the last attempt (or a
    truncated answer) and a second MAX_TOKENS answer stops the retry. When the
    retries run out the real error is raised, not tenacity's RetryError.
    """
    from modules.api_retry import get_api_retry_decorator
    from modules.gemini_client import RetryPlan, unwrap_retry_error

    attempts = 3
    try:
        attempts = max(1, int(os.getenv("YOUTUBE_PACKAGE_MAX_ATTEMPTS") or "3"))
    except ValueError:
        attempts = 3
    plan = RetryPlan(attempts)

    @get_api_retry_decorator(max_attempts=attempts, min_wait=5, max_wait=40)
    def _call():
        use_fallback = plan.start()
        try:
            return generate_text(
                gemini_api_key,
                prompt,
                task_name="youtube_package",
                model_name=model_name,
                use_fallback=use_fallback,
            ).strip()
        except Exception as exc:
            plan.failed(exc)
            raise

    try:
        return _call()
    except Exception as exc:
        cause = unwrap_retry_error(exc)
        if cause is exc:
            raise
        raise cause from None


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

    from modules.narrative_sanitize import (
        sanitize_narrative_text,
        sanitize_packaging_text,
        sanitize_structured_data,
        strip_number_readings,
    )

    # Read-side sanitize: older structured_data / reports predate the regex families.
    structured_data = sanitize_structured_data(dict(structured_data or {}))
    report_excerpt = sanitize_narrative_text(strip_number_readings(report_content or ""))[:1000]
    official_watch = _official_watch(structured_data)
    if official_watch:
        official_watch_block = (
            "、".join(f"{t} {n}".strip() for t, n in official_watch)
            + "\n規則：描述或置頂留言若寫「主盯／正式追蹤」，只能點名上面這些，而且要全部點到；其他股票只能當對照。"
        )
    else:
        official_watch_block = "今日沒有正式追蹤檔：描述與置頂留言不要寫「主盯」，只講觀察條件。"

    ai_picks = structured_data.get("ai_data_picks", [])
    index_action = structured_data.get("index_action", "")
    safe_havens = structured_data.get("safe_havens_bought", [])
    heavyweights_dumped = structured_data.get("heavyweights_dumped", [])
    price_divergence = structured_data.get("price_volume_divergence", [])
    from modules.structured_facts import overnight_rule_text

    overnight_rule = overnight_rule_text(structured_data)
    try:
        from modules.title_fact_gate import format_price_state_block

        price_state_block = format_price_state_block(structured_data)
    except Exception:
        price_state_block = ""
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
    local_market_memory = sanitize_narrative_text(packaging_context.get("local_market_memory", ""))
    stock_role_taxonomy = packaging_context.get("stock_role_taxonomy", "")
    audience_profile = packaging_context.get("audience_profile", "")
    compounding_brief = packaging_context.get("compounding_brief", "")
    calendar_rules = packaging_context.get("calendar_rules", "")
    if not compounding_brief:
        try:
            brief_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "reports",
                "shared",
                "weekly_compounding_brief.md",
            )
            if os.path.exists(brief_path):
                with open(brief_path, "r", encoding="utf-8") as bf:
                    compounding_brief = bf.read()[:3500]
        except Exception:
            compounding_brief = ""
    placement = _validation_placement(structured_data, packaging_context)
    opening_hook = build_opening_hook(structured_data)
    chapter_markers = "\n".join(build_chapter_markers(structured_data, packaging_context))
    horizon_rule = build_horizon_packaging_rule(structured_data)
    if is_verified_major_event_day(structured_data, packaging_context):
        event_day_rule = (
            "今日本機主軸是重大事件日，且事件雷達有新的高分事件：標題A可用抽象結構"
            "「事件 → 台股數字 → 觀察條件」（事件名稱＋核心股已驗證金額或漲跌＋下一交易日要看的價位／條件）；"
            "事件與數字都必須來自上方資料。"
        )
    else:
        event_day_rule = (
            "今日不是已驗證的重大事件日（本機主軸不是重大事件日，或事件雷達沒有新的高分事件）："
            "標題A不要用時事／全球風險開場，維持「已驗證金額 + 核心股名 + 反直覺」。"
        )
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
海外／匯率／夜盤（本機）：{overnight_rule}
穩健策略：{conservative}
波段策略：{aggressive}

===個股收盤與價格狀態（本機；標題／封面寫金額、漲跌、收紅／收黑／平盤／漲停／噴／重挫／逆勢前必須對照，系統會逐字對帳）===
{price_state_block if price_state_block else '（無本機個股漲跌：標題不要寫漲跌或收紅／收黑）'}

===下一交易日正式追蹤（prediction_targets；本機帳本）===
{official_watch_block}

===本機正式驗收結果（可信度資料；是否當標題主鉤子看主軸分類 validation_placement）===
{validation_summary if validation_summary else '（無本機驗收資料）'}
規則：若 validation_placement 不是 lead，標題禁止用「驗收/上集點名/驗收現場」當主鉤子；最多內文一句帶過。

===近期頻道敘事與觀看數（用來避免重複、延續有效敘事）===
{channel_story if channel_story else '（無頻道敘事紀錄）'}

===近期 Analytics / YouTube 學習（有效樣本門檻 = 頻道中位數×2，見下方成效提煉；上架未滿 72 小時只作觀察）===
{analytics_summary if analytics_summary else '（無 Analytics 摘要）'}

===YouTube 成效提煉規則（高權重；由 channel_story_log 自動整理）===
{youtube_performance_patterns if youtube_performance_patterns else '（無 YouTube 成效提煉規則）'}

===本頻道觀眾偏好與包裝規則===
{audience_profile if audience_profile else '觀眾偏好：具體點名個股、外資買賣金額、量價矛盾、情境推演；驗收結果適度即可，不要每天當主鉤子。'}

===本地主軸分類（最高優先，不得自行升級成警報）===
{editorial_frame if editorial_frame else '（無本地主軸分類）'}

===主動消息面事件雷達（若有高分事件，標題可用事件當主軸）===
{event_radar if event_radar else '（無高分供應鏈事件）'}

===本機日曆約束（解讀用，不是預設標題開場）===
{calendar_rules if calendar_rules else '（無近窗日曆）'}

===近期標題疲勞檢查（避免連續使用同一套情緒公式）===
{title_fatigue_report if title_fatigue_report else '（無標題疲勞檢查）'}

===複利閉環簡報（自動；禁止/可複製/命中率 watchlist）===
{compounding_brief if compounding_brief else '（尚無 weekly_compounding_brief；請先跑 preflight-views 或 compounding-brief）'}
規則：若簡報有 BLOCK 開場，標題A禁止該 token 開場；優先金額+股名+矛盾+問句。

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
{report_excerpt}

===標題撰寫規則（最關鍵）===
只生成兩個標題：A 是上架標題，B 留給之後單獨的標題測試。A 與 B 必須同一核心股、同一筆已驗證金額或漲跌；B 只換句法，不是換題材。
- 標題A（上架標題；封面 A/B 都配這個）：完整句。前 20 字必須是「已驗證金額 + 核心股名 + 反直覺」。用「買／砍」，不要用「狂掃／狂砍」開場。不要用【驗收】、AMD、升息、非農、四巫當開場。
  結構依「標題結構輪替」的今日建議 A（見近期標題疲勞檢查）；骨架示意：「{{股名}}買{{N}}億！{{反差}}｜{short_date} AI帶你看股市」，不要照抄舊標題。
- 標題B（備用；之後單獨做標題測試，不與封面 A/B 同時進行）：同一檔、同一個數字，但換「標題結構輪替」建議 B 的結構家族（例如矛盾句「{{股名}}漲{{N}}%卻…」或短問句），句子要碎，不要把故事講完。禁止「外資○○億狂砍／狂掃」。禁止【驗收】、上集點名、VERIFIED、誰在收。

===複利閉環（自動簡報，高權重）===
若下方有「本週禁止／可複製」內容：標題A必須遵守禁止開場；優先模仿可複製樣本的金額+矛盾+問句。
BLOCK 開場日：禁止 AMD／驗收開場；系統會優先採用本機高樣本模板。

===時間窗可信度（本機回測；自然帶入，勿硬廣）===
{horizon_rule}

===頻道標題成效（動態計算；以上方「YouTube 成效提煉規則」與「標題結構輪替」為準）===
學習門檻：未達頻道中位數×2（同齡比較；上架未滿 72 小時不算）的影片只能視為觀察樣本，不可作為 A/B Test 勝負依據。
可複製的是「金額 + 個股 + 矛盾」的結構，不是開場詞，也不是任何一支舊片的原句。本段不提供寫死的「最高觀看樣本」。

判斷順序（依今日資料決定，不是模仿固定範例）：
1. 先判斷今天是哪一種內容日：驗收日、重大事件日、異常日、續航日、主題日、整理日。
2. {event_day_rule}
3. 若今日資料能呼應上集的預測，標題可使用「上集說過」結構；上一集若不是昨天，禁止用「昨天點名」。
4. 只有在法人與成交量/股價有明確反直覺數字時，才使用「背離、警訊、誰在賣、誰接手」語氣。
5. 只有連續 2-3 天有法人、量能、價格結構延續時，才使用「續航、接棒、外資續買 N 日」語氣（N 用本機連續買超天數）。
6. 若沒有強訊號，不要硬做警報標題；改用「今天不是追強日」「這幾檔等確認」「訊號普通但觀察條件很清楚」。
7. 若沒有時事槓桿，再給大盤表面現象：狂飆、重挫、翻紅、收黑；但不要每次都接「暗藏殺機」，除非數據真的支持。
8. 放入最有辨識度的大型股或核心標的，例如台積電，並附上真實外資買賣超金額。
9. 最後給觀眾一個想點進來的答案缺口：這檔、這兩檔、記憶體、AI伺服器、ETF 等具體題材。
10. 不要只寫「資金輪動」。若要表達資金移動，請寫成具體行為：外資砍台積電、投信買超、三大法人反手買、成交量爆出異常。
11. 連續劇標題要有明確驗收點：上集說了什麼、今天發生什麼、外資買賣多少、股價是否反著走。
12. 若今天接近長假、休市或連假後開盤：可在標題後半點出時間點，但必須搭配具體籌碼、期貨或權值股數字與觀察條件；不要用「崩盤預警」。

標題硬規則：
1. 標題A 28-60 字（含符號）；標題B 18-40 字，允許殘句，不要為了湊字把故事寫完。
2. 標題A 前 22 字必須出現核心股或已驗證金額之一。
3. 末尾固定加「｜AI帶你看股市」作為品牌識別
4. 數字必須來自今日真實數據，不得捏造
5. 繁體中文，禁止「穩了」「嗨了」「躺平」等中國大陸流行語
6. 可加 1-2 個 emoji，放最前面或最後面（末尾品牌標語前）
7. 不要使用「資金輪動」；標題要講具體事件，例如三大法人買賣超、成交量、量價背離。
8. 標題必須至少點名 1 檔具體股票；A 與 B 必須是同一檔核心股。
9. 只能寫可驗證的動作（「外資買超 N 億」「投信賣超 N 億」）並附實際金額；禁止「掃貨／鎖貨／吸籌／偷偷買」這類法人意圖推論（與日報規則一致）。
10. 若核心股是權值大股但彈性有限，標題應區分「看方向」與「看彈性」，不要把權值核心包裝成最大飆股。
11. 不要每天套同一種危機語氣；若日報主軸是整理日或觀察日，標題也必須承認訊號尚未確認。
12. 若「近期包裝疲勞檢查」列出禁用詞或禁用封面題型，標題A與標題B都不得再使用那些詞，也不得只把字換成同義詞（例如榜外吸籌→沒進榜、被砍→提款、接棒→換車）。
13. 標題A與標題B必須同一核心股、同一金額；差別只在句法。禁止 B 再寫「外資○○億狂砍／狂掃」。禁止 B 用【驗收】、上集點名、VERIFIED。
14. 若連續多日都在講法人換手，B 改問缺口，不要再寫完整換手句；「還紅？」只能用在本機漲跌 > 0 的股票，「沒進量榜？」只能用在本機確認未進成交股數榜的股票。
16. 金額、漲跌％、大盤點數、收紅／收黑／平盤／漲停／噴／重挫／逆勢等字，必須和上方「個股收盤與價格狀態」同一檔股票的本機數字一致（例如漲跌 0.00% 只能寫平盤／守平盤）；不一致的標題會被系統標成 FAIL。
15. 禁用恐慌字眼：斷頭、逃命、全逃、血洗、崩盤（系統會改寫成中性詞）；風險要寫成數字與觀察條件。

===描述撰寫規則===
第1行（≤55字）：今日主軸或關鍵訊號 + 核心數字 + 日期（包含「台股」）
第2行（≤55字）：AI觀察焦點 + 對散戶的實際意義
第3行：訂閱召喚 + 開啟通知提示

開場節奏（對齊影片前 30 秒，描述章節也要一致）：
{opening_rule}
- 0～8 秒：最大矛盾（股名 + 金額/漲跌 + 反差）；只能用今日真實數據
- 8～20 秒：一句可被影片回答的問句
- 20～40 秒：今日主線一句話（續航/事件/異常/整理…）
- 不用恐慌字眼（斷頭、逃命、全逃、血洗、崩盤），有數字也一樣；風險寫成數字與觀察條件
- 恐懼必須有出口：下一交易日看什麼、哪個價位失效（週五說下週一，不要說明天開盤）

空一行，加章節區塊（請完整照抄下列內容，不要自行加時間或改回「昨日預測驗收」開場，除非 placement=lead；系統會依實際影片長度覆寫）：
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
        output = _generate_package_text(gemini_api_key, prompt, model_name)

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
        # Titles are published too: same narrative + panic rewrite as the description.
        result["titles"] = [sanitize_packaging_text(t) for t in result["titles"] if t]
        # The guard needs the raw 9-column log rows (title in cell 7); the prompt's
        # channel_story is a compact 5-column projection that _parse_channel_story_titles
        # cannot read, which would silently disable opener / fatigue enforcement.
        channel_story = (packaging_context or {}).get("channel_story_rows") or (packaging_context or {}).get(
            "channel_story", ""
        )
        fatigue_state = build_fatigue_state(channel_story=channel_story)
        local_candidates = [
            sanitize_packaging_text(t)
            for t in generate_local_titles(
                structured_data,
                num_titles=4,
                date_label=short_date,
                channel_story=channel_story,
            )
        ]
        if result["titles"]:
            result["titles"] = guard_youtube_titles(
                result["titles"],
                fatigue_state=fatigue_state,
                local_candidates=local_candidates,
                short_date=short_date,
                date_str=date_str,
            )
        if result.get("description"):
            result["description"] = _normalize_description_chapters(
                sanitize_packaging_text(result["description"]),
                structured_data,
                packaging_context,
            )
        if result.get("pinned_comment"):
            result["pinned_comment"] = sanitize_packaging_text(result["pinned_comment"])
        warnings = check_main_watch_consistency(
            [result.get("description"), result.get("pinned_comment")],
            structured_data,
        )
        if warnings:
            result["consistency_warnings"] = warnings
            for warning in warnings:
                print(f"  ⚠️  [主盯一致性] {warning}")
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
    from modules.narrative_sanitize import sanitize_structured_data

    structured_data = sanitize_structured_data(dict(structured_data or {}))

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
    from modules.report_paths import resolve_shared_artifact
    import os as _os

    channel_story = ""
    try:
        story_path = resolve_shared_artifact(
            _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "reports"),
            "channel_story_log",
        )
        if story_path and _os.path.exists(story_path):
            with open(story_path, "r", encoding="utf-8") as _f:
                channel_story = _f.read()
    except Exception:
        channel_story = ""

    from modules.narrative_sanitize import sanitize_packaging_text

    fatigue_state = build_fatigue_state(channel_story=channel_story)
    local_candidates = [
        sanitize_packaging_text(t)
        for t in generate_local_titles(
            structured_data,
            num_titles=4,
            date_label=short_date,
            channel_story=channel_story,
        )
    ]
    fallback_titles = guard_youtube_titles(
        local_candidates,
        fatigue_state=fatigue_state,
        local_candidates=local_candidates,
        short_date=short_date,
        date_str=date_str,
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
            date_str=date_str,
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

    package = {
        "titles": [sanitize_packaging_text(title_a), sanitize_packaging_text(title_b)],
        "description": sanitize_packaging_text("\n".join(desc_lines)),
        "pinned_comment": sanitize_packaging_text(pinned_comment),
        "fallback": True,
        "fallback_reason": reason,
        "fallback_hook": hook,
    }
    warnings = check_main_watch_consistency([package["description"], pinned_comment], structured_data)
    if warnings:
        package["consistency_warnings"] = warnings
    return package


def _local_quote(structured_data, ticker):
    stocks = ((structured_data or {}).get("local_market_facts") or {}).get("stocks") or {}
    return stocks.get(str(ticker or "").strip()) or {}


def _fmt_price(value):
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


def _is_short(direction):
    """Short call (空 / 偏空 …): the one direction rule, prediction_validator.direction_sign."""
    from modules.prediction_validator import direction_sign

    return direction_sign(direction) < 0


def _is_long(direction):
    from modules.prediction_validator import direction_sign

    return direction_sign(direction) > 0


def past_trend_text(trend, windows=("5D", "10D")):
    """
    「；參考：過去 5 個交易日（from→to）已 +X.X%…（已發生的走勢，不是預測）」 from one
    point-in-time ``recent_trends`` row (timeframe_builder.load_point_in_time_trends).

    These are past moves ending today: never priced in 元 for a position
    entered at today's close, never multiplied by the call's side, never
    worded as a 驗證窗 result. Empty when no window has a number.
    """
    rows = ((trend or {}).get("trends") or {}) if isinstance(trend, dict) else {}
    bits = []
    for key in windows:
        row = rows.get(key) or {}
        pct = row.get("pct_change")
        if not isinstance(pct, (int, float)):
            continue
        sessions = str(key).rstrip("Dd")
        span = f"（{row['from_date']}→{row['to_date']}）" if row.get("from_date") and row.get("to_date") else ""
        bits.append(f"過去 {sessions} 個交易日{span}已 {pct:+.1f}%")
    if not bits:
        return ""
    return f"；參考：{'、'.join(bits)}（已發生的走勢，不是預測）"


def build_target_scenarios(
    target, structured_data=None, *, close=None, horizon="1~5日", heavyweight="", direction=None
):
    """
    Three distinct, price-anchored scenarios for one prediction target.

    多: bull = 收盤站上確認價（今日收盤或 MA20）且外資續買；base = 失效價與確認價之間；
    bear = 收盤跌破失效價 → 假說失效、停止追蹤.
    空 (the stop sits above the close): bear = 收盤跌破確認價（今日收盤或 MA20）且外資續賣
    → 空方節奏成立；bull = 收盤站上失效價 → 假說失效. ``direction`` defaults to
    ``target["direction"]``. Falls back to text-only scenarios (still distinct)
    when prices are unknown.
    """
    target = target if isinstance(target, dict) else {}
    structured_data = structured_data or {}
    short = _is_short(target.get("direction") if direction is None else direction)
    name = str(target.get("name") or target.get("id") or "今日主軸標的").strip()
    ticker = str(target.get("id") or "").strip()
    stop = target.get("stop_loss_price")
    stop = float(stop) if isinstance(stop, (int, float)) and stop > 0 else None
    local = _local_quote(structured_data, ticker)
    ma20 = local.get("ma20") if isinstance(local.get("ma20"), (int, float)) else None
    stop_desc = str(target.get("stop_loss_desc") or "").strip()
    heavy_note = f"；同時看 {heavyweight} 賣壓是否擴大。" if heavyweight else "。"
    out = {"alert": ""}
    if close and stop and short:
        stop_pct = (stop / close - 1) * 100
        confirm = ma20 if ma20 and ma20 < close else close
        confirm_note = f"（{(confirm / close - 1) * 100:+.1f}%）" if confirm != close else ""
        confirm_label = (
            f"跌破 MA20 {_fmt_price(confirm)}{confirm_note}" if confirm != close else f"收盤跌破今日收盤 {_fmt_price(close)}"
        )
        if stop <= close:
            out["alert"] = (
                f"今日收盤 {_fmt_price(close)} 已高於失效價 {_fmt_price(stop)}（高 {abs((close / stop - 1) * 100):.1f}%），"
                "失效條件已成立；只當觀察，不當下一交易日正式追蹤"
            )
            out["base"] = f"{name} 收盤仍高於 {_fmt_price(stop)}：空方假說維持失效，只觀察是否轉弱，不追空。"
            out["bull"] = f"{name} 續強、外資轉買：確認失效，停止追蹤並記錄失準原因。"
            out["bear"] = f"{name} {horizon} 內收盤重新跌回 {_fmt_price(stop)} 之下且外資續賣：假說才恢復，重新列入追蹤。"
            return out
        lower = f"MA20 {_fmt_price(confirm)}{confirm_note}" if confirm != close else f"今日收盤 {_fmt_price(close)}"
        out["base"] = (
            f"{name} 收盤落在{lower}與失效價 {_fmt_price(stop)}（{stop_pct:+.1f}%）之間："
            f"未破也未轉弱，{horizon} 內看量能與外資賣超是否延續。"
        )
        out["bull"] = (
            f"{name} 收盤站上失效價 {_fmt_price(stop)}（距今收盤 {stop_pct:+.1f}%）：空方假說失效，停止追蹤。"
        )
        out["bear"] = f"{name} {confirm_label}且外資續賣：空方節奏成立，改看約 20 交易日趨勢是否延續" + heavy_note
        return out
    if close and stop:
        stop_pct = (stop / close - 1) * 100
        confirm = ma20 if ma20 and ma20 > close else close
        confirm_label = f"站回 MA20 {_fmt_price(confirm)}" if confirm != close else f"收盤站上今日收盤 {_fmt_price(close)}"
        confirm_pct = (confirm / close - 1) * 100
        if stop >= close:
            out["alert"] = (
                f"今日收盤 {_fmt_price(close)} 已低於失效價 {_fmt_price(stop)}（低 {abs((close / stop - 1) * 100):.1f}%），"
                "失效條件已成立；只當觀察，不當下一交易日正式追蹤"
            )
            out["base"] = f"{name} 收盤仍低於 {_fmt_price(stop)}：假說維持失效，只觀察是否止跌，不加碼追蹤。"
            out["bull"] = f"{name} {horizon} 內收盤重新站回 {_fmt_price(stop)} 之上且外資續買：假說才恢復，重新列入追蹤。"
            out["bear"] = f"{name} 續跌、外資轉賣：確認失效，停止追蹤並記錄失準原因。"
            return out
        confirm_note = f"（{confirm_pct:+.1f}%）" if confirm != close else ""
        upper = f"MA20 {_fmt_price(confirm)}{confirm_note}" if confirm != close else f"今日收盤 {_fmt_price(close)}"
        out["base"] = (
            f"{name} 收盤落在失效價 {_fmt_price(stop)}（{stop_pct:+.1f}%）與{upper}之間："
            f"未破也未轉強，{horizon} 內看量能與外資是否延續。"
        )
        out["bull"] = f"{name} {confirm_label}{confirm_note}且外資續買：短線節奏成立，改看約 20 交易日趨勢是否延續。"
        out["bear"] = (
            f"{name} 收盤跌破失效價 {_fmt_price(stop)}（距今收盤 {stop_pct:+.1f}%）：假說失效，停止追蹤" + heavy_note
        )
        return out
    invalid = stop_desc or (f"{'收盤站上' if short else '收盤跌破'} {_fmt_price(stop)}" if stop else "失效條件")
    out["base"] = f"{name} 未觸發「{invalid}」也未放量轉{'弱' if short else '強'}：維持觀察，{horizon} 內看量能與外資是否延續。"
    if short:
        out["bull"] = f"{name} 觸發「{invalid}」：空方假說失效，停止追蹤。"
        out["bear"] = f"{name} 放量轉弱且外資續賣：空方節奏成立，改看約 20 交易日趨勢是否延續。"
    else:
        out["bull"] = f"{name} 放量轉強且外資續買：短線節奏成立，改看約 20 交易日趨勢是否延續。"
        out["bear"] = f"{name} 觸發「{invalid}」：假說失效，停止追蹤。"
    return out


def sand_table_scenarios_are_distinct(text):
    """False when 基準／樂觀 only differ by wording such as 先看／能否 (manual edits included)."""
    rows = {}
    for key in ("基準情境", "樂觀情境", "悲觀情境"):
        m = re.search(rf"{key}[：:]\s*(.+)", str(text or ""))
        if m:
            rows[key] = m.group(1)

    def _core(line):
        line = re.sub(r"先看|能否|是否|有沒有|守住|站穩|[，,。；;：:\s]", "", line or "")
        return line

    if len(rows) < 3:
        return True
    cores = [_core(rows[k]) for k in ("基準情境", "樂觀情境", "悲觀情境")]
    return len(set(cores)) == 3


def _build_sand_table_section(structured_data):
    structured_data = structured_data or {}
    targets = structured_data.get("prediction_targets") or []
    first_target = targets[0] if targets and isinstance(targets[0], dict) else {}
    safe_havens = _as_list(structured_data.get("safe_havens_bought"))
    heavyweights = _as_list(structured_data.get("heavyweights_dumped"))
    recent_trends = structured_data.get("recent_trends") or (
        (structured_data.get("timeframe_history") or {}).get("recent_trends") or {}
    )
    short_window = structured_data.get("short_term_validation_window") or "1D / 3D / 5D"
    long_window = structured_data.get("long_term_validation_window") or "20D"
    horizon = structured_data.get("short_term_horizon") or "1~5日"
    long_horizon = structured_data.get("long_term_horizon") or "2~6週 / 1~3個月"

    def _latest_price_for(ticker, fallback=None):
        local = _local_quote(structured_data, ticker)
        if local.get("close"):
            return float(local["close"])
        trend = recent_trends.get(str(ticker)) or {}
        latest_price = trend.get("latest_price")
        same_day = not trend.get("latest_date") or str(trend.get("latest_date")) == str(structured_data.get("date") or "")
        if isinstance(latest_price, (int, float)) and same_day:
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
        # A non-directional call (觀察 / 多空交錯) is not a position, and a long
        # idea whose stop is already above the close is not a position plan.
        if not _is_long(direction) and not _is_short(direction):
            return None
        if _is_long(direction) and float(stop_price) >= current_price:
            return None
        if _is_short(direction) and float(stop_price) <= current_price:
            return None
        # A short position gains when the price falls; its stop sits above the close.
        side = -1 if _is_short(direction) else 1
        shares = 1_000_000 / current_price
        stop_pct = side * (float(stop_price) / current_price - 1) * 100
        stop_pnl = 1_000_000 * stop_pct / 100
        stop_value = 1_000_000 + stop_pnl
        # Point-in-time recent_trends are moves that already happened (ending
        # today), never this position's validation-window outcome.
        trend_text = past_trend_text(recent_trends.get(str(ticker)))
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
        f"{'收盤站上' if _is_short(target_dir) else '收盤跌破'} {first_target.get('stop_loss_price')}"
        if first_target.get("stop_loss_price")
        else "若明日無法延續就視為失效"
    )

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
            short = _is_short(sim["direction"])
            sim_lines.append(
                f"  - {sim['ticker']} {sim['name']}：若 100 萬在 {sim['current_price']:.1f} 元"
                f"{'放空，約' if short else '進場，約可買'} {sim['shares']:.1f} 股；"
                f"若以停損價 {sim['stop_price']:.1f} 元{'回補' if short else '出場'}，帳面約剩 {sim['stop_value']:,.0f} 元，"
                f"損益約 {sim['stop_pnl']:+,.0f} 元（{sim['stop_pct']:+.2f}%）{sim['trend_text']}"
            )

    scenario = build_target_scenarios(
        first_target or {"name": target_name},
        structured_data,
        close=_latest_price_for(first_target.get("id"), first_target.get("price")) if first_target else None,
        horizon=horizon,
        heavyweight=heavyweights[0] if heavyweights else "",
    )

    lines = [
        "## 🎯 沙盤推演（預測部分）",
        "",
        "以下內容不是單純看漲跌，而是把今天的盤面拆成可驗證的三種情境（價位來自本機收盤與失效價）。",
        "",
        f"- 今日主觀察標的：{target_name}{f'（{target_dir}）' if target_dir else ''}",
        *([f"- ⚠️ {scenario['alert']}"] if scenario.get("alert") else []),
        f"- 基準情境：{scenario['base']}",
        f"- 樂觀情境：{scenario['bull']}",
        f"- 悲觀情境：{scenario['bear']}",
        f"- 觸發條件：{trigger}",
        f"- 失效條件：{invalidation}",
        f"- 驗收窗口：短線 {short_window}；中期 {long_window}",
        f"- 觀察重點：今天不是只看單日漲跌，而是看籌碼是否能把趨勢推進到下一個驗收窗。",
        *sim_lines,
        "",
    ]
    return "\n".join(lines)


def _cover_ab_block(titles, structured_data, date_str):
    """This week's single-variable cover test; /2 Step 3 refreshes it with the real covers."""
    from modules.cover_ab_test import current_hypothesis, package_section

    hero_label, number = "", ""
    try:
        from modules.title_fact_gate import choose_hero

        hero = choose_hero(list(titles or []), structured_data or {}) if structured_data else {}
        hero_label = hero.get("label") or ""
        number = (hero.get("amount") or "") if hero.get("verb") else ""
    except Exception:
        pass
    try:
        hypothesis = current_hypothesis(date_str)
    except Exception:
        hypothesis = None
    return package_section(hypothesis, date_str, hero_label=hero_label, number=number)


def save_youtube_package(result, reports_dir, date_str, structured_data=None):
    """Save the YouTube package to a markdown file and return the filepath."""
    from modules.report_paths import youtube_artifact_write_path

    filepath = youtube_artifact_write_path(reports_dir, date_str, "package")

    lines = [f"# YouTube 上架素材包 {date_str}\n"]

    from modules.cover_ab_test import TITLE_A_LABEL, TITLE_B_LABEL, TITLE_HEADING

    lines.append(TITLE_HEADING + "\n")
    labels = [TITLE_A_LABEL, TITLE_B_LABEL]
    for i, title in enumerate(result.get("titles", [])[:2]):
        label = labels[i] if i < len(labels) else f"標題 {i + 1}"
        lines.append(f"**{label}：**\n```\n{title}\n```\n")

    warnings = list(result.get("consistency_warnings") or [])
    if structured_data is not None and not warnings:
        warnings = check_main_watch_consistency(
            [result.get("description"), result.get("pinned_comment")], structured_data
        )
    if warnings:
        lines.append("> ⚠️ 主盯一致性檢查（上傳前修正描述／置頂留言）：")
        for warning in warnings:
            lines.append(f"> - {warning}")
        lines.append("")

    if structured_data is not None:
        try:
            from modules.title_fact_gate import check_titles, fact_gate_block, format_issue

            gate_issues = check_titles(result.get("titles", [])[:2], structured_data)
            block = fact_gate_block(gate_issues)
            if block:
                lines.append(block + "\n")
            if any(issue["level"] == "error" for issue in gate_issues):
                print("  ⛔⛔ 標題事實對帳 FAIL（已寫入 package，上傳前必須修正；系統不改寫 Gemini 標題）：")
            for issue in gate_issues:
                print(f"     {format_issue(issue)}")
        except Exception as gate_err:
            print(f"  ⚠️  標題事實對帳失敗，略過：{gate_err}")

    lines.append("---\n\n")
    lines.append(_build_sand_table_section(structured_data))

    lines.append("---\n\n## 📄 影片描述\n\n```")
    lines.append(result.get("description", "（未生成）"))
    lines.append("```\n")

    lines.append("---\n\n## 💬 置頂留言（上傳後10分鐘內貼上效果最好）\n\n```")
    lines.append(result.get("pinned_comment", "（未生成）"))
    lines.append("```\n")

    lines.append("---\n\n## 📌 上架流程提醒\n")
    lines.append("1. 用標題 A 上傳（封面 A/B 照下方「封面 A/B 測試」同時上傳）")
    lines.append("2. 在 Canva 打開縮圖背景，加上今日標題文字")
    lines.append("3. 上傳影片，貼上描述和縮圖")
    lines.append("4. 發布後立即貼置頂留言")
    if any(header in (result.get("description") or "") for header in _OUTLINE_HEADERS):
        lines.append(
            "4.5 章節沒有時間軸（未提供影片長度）：要 YouTube 章節請設 YOUTUBE_VIDEO_DURATION=秒數或 mm:ss 後重產，或手動補時間"
        )
    lines.append("")
    lines.append(_cover_ab_block(result.get("titles", [])[:2], structured_data, date_str) + "\n")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return filepath
