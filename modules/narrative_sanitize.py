"""Strip conspiracy / lock-chip language from reports, structured data, and prompts."""

from __future__ import annotations


# Longer phrases first so they win over shorter fragments.
NARRATIVE_REPLACEMENTS = (
    ("影子主力的避險碉堡", "法人避險買盤"),
    ("機構鎖籌碼意圖明顯", "成交量未進前20"),
    ("高端法人進行靜默入侵", "成交量未進前20的買超"),
    ("機構級鎖籌碼", "成交量未進前20的買超"),
    ("機構級暴力掃貨", "大額買超"),
    ("資金的地下大遷移", "資金轉向"),
    ("高濃度建倉", "買超金額高、量能未同步放大"),
    ("縮量建倉", "成交量未進前20的買超"),
    ("榜外低量買超", "成交量未進前20的買超"),
    ("靜默買進", "成交量未進前20的買超"),
    ("靜默建倉", "成交量未進前20的買超"),
    ("高階真空吸籌", "成交量未進前20的買超"),
    ("真空吸籌", "成交量未進前20的買超"),
    ("靜默入侵", "成交量未進前20的買超"),
    ("隱形掃貨", "成交量未進前20的買超"),
    ("影子建倉", "成交量未進前20的買超"),
    ("榜外買超", "成交量未進前20的買超"),
    ("影子資金", "法人買盤"),
    ("鎖籌碼", "成交量未進前20"),
    ("外資提款", "外資賣超"),
    ("精準轉入", "轉向買超"),
    ("確認主力", "顯示"),
    ("核彈級", "大幅"),
    ("血洗", "重挫"),
    ("崩盤", "重挫"),
    ("碉堡", "避險標的"),
    ("汰弱留強", "買賣超分化"),
)

_SKIP_KEYS = {"url", "source", "path", "id", "ticker", "date"}


def sanitize_narrative_text(value) -> str:
    text = str(value or "")
    if not text:
        return text
    for old, new in NARRATIVE_REPLACEMENTS:
        if old in text:
            text = text.replace(old, new)
    return text


def sanitize_value(value, key=""):
    key_l = str(key or "").lower()
    if key_l in _SKIP_KEYS or key_l.endswith("_url") or key_l.endswith("_path"):
        return value
    if isinstance(value, str):
        return sanitize_narrative_text(value)
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return {k: sanitize_value(v, key=k) for k, v in value.items()}
    return value


def sanitize_structured_data(structured_data):
    if not isinstance(structured_data, dict):
        return structured_data
    return sanitize_value(structured_data)
