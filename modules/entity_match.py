"""
Stock-entity matching for news / radar text (同名異公司 safe).

A bare substring test lets 長榮(2603) match 長榮航(2618), 南亞(1303) match
南亞科 / 東南亞, and 創意(3443) match the ordinary word 「創意」. Matching rules:

1. The stock code (not embedded in a longer number) always counts.
2. A known alias of that code counts (創意電子 → 3443).
3. Otherwise the name counts only after removing longer names that contain it
   (other listed names + known phrases), and never for names that are common
   words (those need the code or an alias).
"""

from __future__ import annotations

import json
import os
import re
import threading

ALIASES = {
    "3443": ("創意電子",),
    "2603": ("長榮海運",),
    "2330": ("台積",),
    "2317": ("鴻海精密",),
    "2409": ("友達光電",),
    "3481": ("群創光電",),
}

HOMONYM_PHRASES = {
    "長榮": ("長榮航", "長榮鋼", "長榮航太", "長榮國際", "長榮大學"),
    "南亞": ("南亞科", "東南亞", "南亞洲"),
    "台塑": ("台塑化",),
    "中華": ("中華電", "中華化", "中華民國", "中華經濟"),
    "華邦": ("華邦電",),
    "台泥": ("台泥綠能",),
}

# Names that are everyday words: require the code or an alias.
COMMON_WORD_NAMES = {"創意", "統一", "中華", "大同", "精英", "台灣", "全新", "力積"}

_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "stock_db.json")
_NAMES_LOCK = threading.Lock()
_NAMES_CACHE: dict[str, str] | None = None


def _known_names() -> dict[str, str]:
    """{name: id} from data/stock_db.json (empty when unavailable)."""
    global _NAMES_CACHE
    with _NAMES_LOCK:
        if _NAMES_CACHE is not None:
            return _NAMES_CACHE
        out: dict[str, str] = {}
        try:
            with open(_DB_PATH, "r", encoding="utf-8") as f:
                payload = json.load(f)
            for sid, info in (payload or {}).items():
                name = str((info or {}).get("name") or "").strip() if isinstance(info, dict) else ""
                if name:
                    out[name] = str(sid)
        except (OSError, ValueError):
            out = {}
        _NAMES_CACHE = out
        return out


def as_entity(item) -> dict:
    if isinstance(item, dict):
        return {"id": str(item.get("id") or "").strip(), "name": str(item.get("name") or "").strip()}
    text = str(item or "").strip()
    if text.isdigit():
        return {"id": text, "name": ""}
    return {"id": "", "name": text}


def homonyms_for(name: str, sid: str = "", known_names: dict[str, str] | None = None) -> list[str]:
    """Longer strings that contain ``name`` but refer to something else."""
    if not name:
        return []
    names = known_names if known_names is not None else _known_names()
    own_aliases = set(ALIASES.get(sid, ()))
    longer = [n for n, other_id in names.items() if name in n and n != name and other_id != sid]
    longer.extend(HOMONYM_PHRASES.get(name, ()))
    longer = [n for n in longer if n not in own_aliases]
    return sorted(set(longer), key=len, reverse=True)


def mentions_entity(text: str, item, known_names: dict[str, str] | None = None) -> bool:
    entity = as_entity(item)
    blob = str(text or "")
    sid, name = entity["id"], entity["name"]
    if sid and re.search(rf"(?<![0-9A-Za-z]){re.escape(sid)}(?![0-9A-Za-z])", blob):
        return True
    if any(alias in blob for alias in ALIASES.get(sid, ())):
        return True
    if not name or name in COMMON_WORD_NAMES:
        return False
    cleaned = blob
    for longer in homonyms_for(name, sid, known_names):
        cleaned = cleaned.replace(longer, "　")
    return name in cleaned


def mentions_any(text: str, items, known_names: dict[str, str] | None = None):
    """First entity from ``items`` mentioned in ``text`` (or None)."""
    for item in items or []:
        if mentions_entity(text, item, known_names):
            return item
    return None


def entity_query(item, suffix: str = "") -> str:
    """'長榮 2603 suffix' — code next to the name so search engines disambiguate."""
    entity = as_entity(item)
    parts = [p for p in (entity["name"], entity["id"], suffix.strip()) if p]
    return " ".join(parts)


_TAPE_TOPIC_RE = re.compile(r"^Taiwan stock (?P<id>[0-9A-Z]{4,6}) (?P<name>\S+) 外資(?P<side>買超|賣超) 原因$")


def topic_entity(topic: str, focus_entities=None) -> dict | None:
    """
    The stock a news topic is about: a tape topic 「Taiwan stock 2603 長榮 外資買超
    原因」, or a focus entity whose code is a token of the topic (radar queries
    are built by entity_query, 「長榮 2603 訂單 法說 營運」). None for generic topics.
    """
    text = str(topic or "").strip()
    match = _TAPE_TOPIC_RE.match(text)
    if match:
        return {"id": match.group("id"), "name": match.group("name")}
    tokens = set(text.split())
    for item in focus_entities or []:
        entity = as_entity(item)
        if entity["id"] and entity["id"] in tokens:
            return entity
    return None


def screen_homonym_news(items, entity, known_names: dict[str, str] | None = None) -> tuple[list, list]:
    """
    Split search results for ``entity`` into (kept, rejected).

    Rejected = the stock's name appears only as part of another company /
    phrase or as a common word, with no code or alias (a 長榮航 2618 article
    returned for 長榮 2603, 「創意」 the word for 創意 3443). Those must not reach
    the prompt as news about the stock. Results that do not name the stock at
    all are kept (market wraps, English coverage without the Chinese name).
    Each rejected item is a copy with ``reason="homonym"`` and ``entity``.
    """
    items = list(items or [])
    entity = as_entity(entity) if entity else None
    if not entity or not entity["name"]:
        return items, []
    kept, rejected = [], []
    for item in items:
        if not isinstance(item, dict):
            kept.append(item)
            continue
        blob = " ".join(
            str(item.get(key) or "") for key in ("title", "content", "snippet")
        )
        if entity["name"] in blob and not mentions_entity(blob, entity, known_names):
            rejected.append({**item, "reason": "homonym", "entity": dict(entity)})
        else:
            kept.append(item)
    return kept, rejected


def news_query_for_topic(topic: str) -> str:
    """
    Tavily query for a news topic. Tape topics become Chinese entity queries
    (「長榮 2603 外資買超」) instead of 「Taiwan stock … 原因 market news today」:
    right after the close there is no 「今天為什麼買超」 article yet, and the
    mixed-language phrasing pulled generic or old pages.
    """
    text = str(topic or "").strip()
    match = _TAPE_TOPIC_RE.match(text)
    if match:
        return f"{match.group('name')} {match.group('id')} 外資{match.group('side')} 股價 新聞"
    if text and text.isascii():
        return f"{text} stock news"
    return text
