"""Strip conspiracy / lock-chip / intent language from reports, structured data, and prompts.

Two layers:
1. ``NARRATIVE_REPLACEMENTS``: exact phrases with a hand-written rewrite.
2. ``_FAMILY_RULES``: regex families, so variants such as 鎖貨／結構性鎖貨／
   悄悄鎖貨／低調掃貨／靜默吸籌／安靜吸籌／隱形多頭／影子卡位 are caught too.

Replacements are deliberately *neutral* (「買超」「賣超」). They never inject a
fact the sanitizer cannot verify (for example 「成交量未進前20」 is never added;
a source sentence that already says 未進成交量前 20 keeps its own wording, and
「榜外…」 becomes a plain 買超). Unhedged intent claims
such as 「法人在利用…」「主力正在…」 get a 「可能」.

``find_narrative_residue`` is the write-time assertion: it lists whatever still
looks like intent language after sanitizing, so callers can warn with the
original sentence.
"""

from __future__ import annotations

import re


# Longer phrases first so they win over shorter fragments.
# Replacements must not assert anything about volume: absence from the
# share-count top 20 (ETF / low-price dominated) is not low volume, and a
# locked limit-up is not a volume fact either. "Quiet buying" phrases become
# a neutral 買超 (法人買超 when the phrase names the actor). Whether the stock
# was on the board, and its own volume ratio, come from broadcast_facts
# (OFF_SHARE_BOARD_LABEL / 量增比), not from here. Nothing below may match
# broadcast_facts.is_off_rank.
NEUTRAL_BUY = "法人買超"

NARRATIVE_REPLACEMENTS = (
    ("影子主力的避險碉堡", "法人避險買盤"),
    ("機構鎖籌碼意圖明顯", NEUTRAL_BUY),
    ("高端法人進行靜默入侵", NEUTRAL_BUY),
    ("機構級鎖籌碼", NEUTRAL_BUY),
    ("機構級暴力掃貨", "大額買超"),
    ("資金的地下大遷移", "資金轉向"),
    ("高濃度建倉", "買超金額高"),
    ("縮量建倉", "買超"),
    # 「榜外」 claims are dropped, not restated: whether a name was off the
    # share-count board is a local-ledger fact (broadcast_facts.volume_say_bit).
    ("榜外低量買超", "買超"),
    ("榜外吸籌", "買超"),
    ("靜默買進", "買超"),
    ("靜默建倉", "買超"),
    ("高階真空吸籌", "買超"),
    ("真空吸籌", "買超"),
    ("靜默入侵", "買超"),
    ("隱形掃貨", "買超"),
    ("影子建倉", "買超"),
    ("榜外買超", "買超"),
    ("影子資金", "法人買盤"),
    ("籌碼鎖貨", "外資買超"),
    ("鎖籌碼", "買超"),
    ("外資提款", "外資賣超"),
    ("精準轉入", "轉向買超"),
    ("確認主力", "顯示"),
    ("核彈級", "大幅"),
    ("血洗", "重挫"),
    ("崩盤", "重挫"),
    ("碉堡", "避險標的"),
    ("汰弱留強", "買賣超分化"),
)

# Stealth modifiers combine with any buying verb; force modifiers only with
# lock / sweep / absorb verbs (so 「瘋狂撿便宜」 stays untouched).
_STEALTH = r"(?:靜悄悄|悄悄|偷偷|默默|暗中|低調|安靜|靜默|隱形|影子|秘密|神秘)"
_FORCE = r"(?:結構性|籌碼性|機構級|高效率|強力|暴力|瘋狂|精準)"
# 鎖碼／鎖貨／掃貨／吸貨 also sit inside ordinary words, which shipping news
# (長榮／陽明) uses a lot: 封鎖碼頭、封鎖貨輪、連鎖貨運、打掃貨架、陸客掃貨、
# 鎖貨源. Guard the bare verbs against those neighbours.
_NOT_CHIP_NOUN = r"(?![頭輪櫃運架物源品款車船機])"
_LOCK_CORE = (
    r"(?<![封連閉上])鎖(?:籌碼|籌|(?:碼|貨)" + _NOT_CHIP_NOUN + r")"
    r"|(?<![打清客眾者])掃(?:籌碼|籌|貨" + _NOT_CHIP_NOUN + r")"
    r"|吸(?:籌碼|籌|貨" + _NOT_CHIP_NOUN + r")"
)
_LOCK_VERBS = r"(?:" + _LOCK_CORE + r"|吃貨|建倉)"
# A bare 「買」 must not eat the noun it starts (「神秘買家」 → 「買超家」).
_STEALTH_VERBS = (
    r"(?:" + _LOCK_CORE + r"|吃貨|建倉|吸納|卡位|佈局|布局|"
    r"買超|買進|敲進|進貨|入侵|埋伏|撿便宜|狂購|買(?![家方盤氣單賣]))"
)
_SHADOW_NOUNS = r"(多頭|名單|資金|主力|買盤|部隊|大戶|大象|巨頭|武士|買超股|權值股|避風港)"
_KEEP_NOUN = {"買超股", "權值股", "避風港"}


def _shadow_noun(match):
    noun = match.group(1)
    if noun == "多頭":
        return "多方訊號"
    if noun == "名單":
        return "觀察名單"
    if noun in _KEEP_NOUN:
        return noun
    return "法人買盤"


_SELL_ACTORS = r"(法人|外資|投信|自營商|主力|大戶|機構)"

_FAMILY_RULES = (
    # 隱形多頭／影子名單／影子買盤／影子大象 ... (before verbs so 「隱形買盤」 is a noun)
    (re.compile(r"(?:影子|隱形|靜默|神秘)(?:的)?" + _SHADOW_NOUNS), _shadow_noun),
    # 神秘買家／低調買盤／偷偷的買方: the buyer is a noun, keep it neutral.
    (re.compile(_STEALTH + r"(?:的)?(?:買家|買方|買盤)"), "買盤"),
    # 結構性鎖貨／悄悄鎖貨／低調掃貨／靜默吸籌／安靜吸籌／影子卡位 ...
    (re.compile(_STEALTH + r"(?:的|地)?" + _STEALTH_VERBS), "買超"),
    (re.compile(_FORCE + r"(?:的|地)?" + _LOCK_VERBS), "買超"),
    # 偷偷搬家／影子搬家／安靜搬貨: direction unknown, keep it neutral.
    (re.compile(_STEALTH + r"(?:的|地)?(?:搬家|搬貨|搬走|移動)"), "資金移動"),
    # 集體靜默換手／悄悄調節: drop the stealth modifier, keep the observable verb.
    (re.compile(_STEALTH + r"(?:的|地)?(換手|換股|調節|轉倉|撤出|撤退)"), r"\1"),
    (re.compile(_STEALTH + r"(?:的|地)?(?:出貨|倒貨|拋售)"), "賣超"),
    # bare intent verbs that are never a verifiable fact
    (re.compile(_LOCK_CORE), "買超"),
    # 「外資倒貨」 states who sold → 賣超. Without that subject (「外資卻趁跌倒貨時
    # 瘋狂撿便宜」: someone else dumped) only the intent word goes: 倒貨 → 賣壓.
    (re.compile(_SELL_ACTORS + r"((?:持續|大舉|連續|趁機|趁高|又|再|開始|狂)?)倒貨"), r"\1\2賣超"),
    (re.compile(r"倒貨"), "賣壓"),
)

# Panic framing for *published packaging* (titles / description / pinned comment).
# Not applied to the daily report: 「融資斷頭」 can be a factual market term there.
PANIC_REPLACEMENTS = (
    ("面臨斷頭危機", "面臨融資賣壓"),
    ("斷頭危機", "融資賣壓"),
    ("斷頭潮", "融資賣壓"),
    ("斷頭", "賣壓"),
    ("資金全逃", "資金撤出"),
    ("全逃", "撤出"),
    ("逃命潮", "減碼潮"),
    ("逃命", "減碼"),
    ("血洗", "重挫"),
    ("崩盤", "重挫"),
)
PANIC_TERMS = ("斷頭", "逃命", "全逃", "血洗", "崩盤")

_ACTORS = r"(?:主力|法人|外資|投信|大戶|大資金|機構)"
_HEDGE_RULE = re.compile(
    r"(?<!可能)(?<!可能是)(?<!或許)(" + _ACTORS + r")(正在|在)(?=利用|進行|佈局|布局|拉抬|洗盤|調節|換股|撤退|撤出|卡位)"
)

_RESIDUE_RE = re.compile(
    _LOCK_CORE + r"|倒貨|靜默(?!期)|影子|隱形掃|真空吸|"
    r"(?:悄悄|偷偷|默默|低調|安靜)(?:的|地)?(?:買|鎖|掃|吸|搬|佈|布|建倉)"
)

# Chinese-numeral reading in parentheses right after a number, e.g.
# 「240.3 (二百四十點三) 億」「2026-05-20 (二千零二十六點零五點二零)」「MA20 (二三十八點七八)」.
_READING_RE = re.compile(
    r"(?<=[0-9%])\s*[（(]\s*([零〇一二兩三四五六七八九十百千萬億點負正趴分之]{1,40})\s*[)）]"
)
_WEEKDAY_MARKS = set("一二三四五六日")

_SKIP_KEYS = {
    "url",
    "source",
    "path",
    "id",
    "ticker",
    "date",
    # Packaging ban lists quote the banned words on purpose.
    "title_fatigue_report",
    "prohibited_tones",
    "allowed_tones",
    # Diagnostics that quote the original (pre-rewrite) wording.
    "fact_check",
    "sanitize_hits",
}


def _record(hits, term):
    if hits is not None and term:
        hits.append(term)


def sanitize_narrative_text(value, hits=None) -> str:
    """Rewrite intent / conspiracy wording. ``hits`` (list) collects matched terms."""
    text = str(value or "")
    if not text:
        return text
    for old, new in NARRATIVE_REPLACEMENTS:
        if old in text:
            if hits is not None:
                hits.extend([old] * text.count(old))
            text = text.replace(old, new)
    for pattern, repl in _FAMILY_RULES:
        if hits is not None:
            for match in pattern.finditer(text):
                _record(hits, match.group(0))
        text = pattern.sub(repl, text)
    if hits is not None:
        for match in _HEDGE_RULE.finditer(text):
            _record(hits, f"{match.group(1)}{match.group(2)}（斷言→可能）")
    text = _HEDGE_RULE.sub(lambda m: f"{m.group(1)}可能{m.group(2)}", text)
    # Clean doubled neutral words produced by nested rewrites (e.g. 「買超買超」).
    text = re.sub(r"(買超)(?:\s*\1)+", r"\1", text)
    return text


def sanitize_packaging_text(value, hits=None) -> str:
    """Narrative rewrite plus panic framing (斷頭／逃命／全逃／血洗／崩盤) for YouTube packaging."""
    text = sanitize_narrative_text(value, hits=hits)
    if not text:
        return text
    for old, new in PANIC_REPLACEMENTS:
        if old in text:
            if hits is not None:
                hits.extend([old] * text.count(old))
            text = text.replace(old, new)
    text = re.sub(r"(賣壓)(?:\s*\1)+", r"\1", text)
    # 「融資斷頭潮」 → 「融資融資賣壓」 → 「融資賣壓」
    text = re.sub(r"(融資)(?:\s*\1)+", r"\1", text)
    return text


def strip_number_readings(value) -> str:
    """Drop LLM-written Chinese readings after numbers (often wrong, ~15% of report chars)."""
    text = str(value or "")
    if not text:
        return text

    def _repl(match):
        reading = match.group(1)
        if len(reading) == 1 and reading in _WEEKDAY_MARKS:
            # Keep weekday marks such as 「10/12 (一)」「2026-10-12（一）」.
            head = text[max(0, match.start() - 12):match.start()]
            if re.search(r"\d{1,4}[/-]\d{1,2}(?:[/-]\d{1,2})?\s*$", head):
                return match.group(0)
        return ""

    return _READING_RE.sub(_repl, text)


def find_narrative_residue(value, context_chars: int = 18) -> list[dict]:
    """Intent-language left in ``value`` (run *after* sanitizing) with the surrounding snippet."""
    text = str(value or "")
    out = []
    for match in _RESIDUE_RE.finditer(text):
        start = max(0, match.start() - context_chars)
        end = min(len(text), match.end() + context_chars)
        out.append(
            {
                "term": match.group(0),
                "snippet": text[start:end].replace("\n", " ").strip(),
            }
        )
    return out


def sanitize_value(value, key="", hits=None):
    key_l = str(key or "").lower()
    if key_l in _SKIP_KEYS or key_l.endswith("_url") or key_l.endswith("_path"):
        return value
    if isinstance(value, str):
        return sanitize_narrative_text(value, hits=hits)
    if isinstance(value, list):
        return [sanitize_value(item, hits=hits) for item in value]
    if isinstance(value, dict):
        return {k: sanitize_value(v, key=k, hits=hits) for k, v in value.items()}
    return value


def sanitize_structured_data(structured_data, hits=None):
    if not isinstance(structured_data, dict):
        return structured_data
    return sanitize_value(structured_data, hits=hits)


def summarize_hits(hits) -> dict:
    """Compact {term: count} summary for structured_data.sanitize_hits."""
    summary: dict[str, int] = {}
    for term in hits or []:
        summary[term] = summary.get(term, 0) + 1
    return dict(sorted(summary.items(), key=lambda kv: (-kv[1], kv[0])))
