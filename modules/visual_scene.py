"""
Automatic daily /2 visual theme (local, deterministic, no LLM).

Before 10/14 an operator typed a daily metaphor scene for /2 (「水庫／洩洪閘漫畫
（漲紅跌綠）：台積電綠色閘門…」). With no theme, /2 now builds one from local facts:

1. ``classify_archetypes``: today's story type from structured_data only — index
   move, the cover hero's own flow / price (``title_fact_gate``), the leaderboards,
   limit-ups and 土洋 conflicts (換手, 權值提款, 單股暴衝, 量價背離, 漲停鎖量,
   大盤強彈, 大跌守線, 土洋對作, 盤整觀望).
2. ``data/visual_scene_library.json``: everyday, non-IP, non-sports scenes with the
   archetypes they fit and buy / sell / divergence metaphor templates.
3. Candidates fit the archetype, were not used in the last ``RECENT_SESSIONS`` TWSE
   sessions (channel_story_log 視覺風格: the scene label before 「：」, or the
   ban-free text of a row without one), and are not banned — an active
   ``visual_object`` ban in data/packaging_bans.json, or a scene named in the
   「禁止…／…」 list of a theme logged in the last ``RECENT_SESSIONS`` sessions (the
   operator re-pastes that list daily; it lapses 5 sessions after the last paste).
   Scenes unused for ``FRESH_SESSIONS`` sessions are preferred. The pick is seeded by
   the date: same date and inputs → same theme.
4. The theme names the SAME hero / amount / percent the cover gate prints and, when
   title A names an opposite-side stock with a local amount, that counterpart (else
   the opposite leaderboard's leader). Colour marks the foreign flow (紅色＝買超、
   綠色＝賣超, as in 漲紅跌綠); the price move is in words (噴／跌／守平盤). It is
   re-checked with ``title_fact_gate`` and kept ≤ 150 chars, in the house format
   「<label>漫畫（漲紅跌綠）：<metaphor>；<cast>」. No A/B cover-text note: each
   cover's text is set by the week's A/B hypothesis (modules/cover_ab_test.py).

Gemini never chooses the scene (Standing Decision).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LIBRARY_PATH = os.path.join(BASE_DIR, "data", "visual_scene_library.json")

FALLBACK_THEME = "財經戰情室"
RECENT_SESSIONS = 5
FRESH_SESSIONS = 20
MAX_CHARS = 150
FORMAT_INFIX = "漫畫（漲紅跌綠）："

ARCHETYPE_ORDER = (
    "大跌守線",
    "大盤強彈",
    "量價背離",
    "換手",
    "權值提款",
    "漲停鎖量",
    "單股暴衝",
    "土洋對作",
    "盤整觀望",
)

# thresholds (億 / %)
INDEX_BIG_MOVE = 1.5
ROTATION_SELL = 80.0
ROTATION_BUY = 20.0
WITHDRAWAL_SELL = 150.0
WITHDRAWAL_WEAK_BUY = 30.0
SURGE_PCT = 5.0
LIMIT_PCT = 9.5
DIVERGENCE_PCT = 0.5
FLAT_BIG_FLOW = 100.0
TRUST_CONFLICT = 5.0
COUNTERPART_MIN = 20.0  # the opposite-side leader only joins the scene from 20億

_UNSET_THEMES = {"", "-", "未指定", "待 /2 設定", "待/2設定", "待設定"}


def load_library(path=None) -> dict:
    with open(path or DEFAULT_LIBRARY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    scenes = [s for s in data.get("scenes") or [] if isinstance(s, dict) and s.get("id") and s.get("label")]
    data["scenes"] = scenes
    return data


# ---------------------------------------------------------------------------
# facts
# ---------------------------------------------------------------------------


def _leader(facts, structured_data, list_key, exclude_key=None):
    """(key, row) of the first leaderboard stock with a local amount, skipping ``exclude_key``."""
    for raw in structured_data.get(list_key) or []:
        m = re.match(r"^(?:(\d{4,6}[A-Z]?)\s+)?([^\s(（]+)", str(raw or "").strip())
        if not m:
            continue
        key = facts.lookup(m.group(1) or "", m.group(2))
        row = facts.fact(key) if key else None
        if not row or key == exclude_key or row.get("foreign_amount") is None:
            continue
        return key, row
    return None, None


def _limit_ups(facts, structured_data):
    count = 0
    for raw in structured_data.get("safe_havens_bought") or []:
        m = re.match(r"^(?:(\d{4,6}[A-Z]?)\s+)?([^\s(（]+)", str(raw or "").strip())
        key = facts.lookup(m.group(1) or "", m.group(2)) if m else None
        pct = (facts.fact(key) or {}).get("pct_change") if key else None
        if pct is not None and pct >= LIMIT_PCT:
            count += 1
    return count


def classify_archetypes(structured_data, facts=None, hero=None) -> list[dict]:
    """
    Matched archetypes in ``ARCHETYPE_ORDER`` priority, each {"archetype", "reason"}.
    The last entry is always 盤整觀望 when nothing else holds.
    """
    from modules.title_fact_gate import build_fact_index

    structured_data = structured_data or {}
    facts = facts or build_fact_index(structured_data)
    hero = hero or {}
    index_pct = facts.index.get("pct")
    hero_amount = hero.get("foreign_amount")
    hero_pct = hero.get("pct_change")
    hero_key = hero.get("key")
    hero_name = hero.get("name") or hero.get("label") or ""
    _sell_key, sell_row = _leader(facts, structured_data, "heavyweights_dumped")
    _buy_key, buy_row = _leader(facts, structured_data, "safe_havens_bought")
    top_sell = abs(sell_row["foreign_amount"]) if sell_row and sell_row["foreign_amount"] < 0 else 0.0
    top_buy = buy_row["foreign_amount"] if buy_row and buy_row["foreign_amount"] > 0 else 0.0
    found = {}

    if index_pct is not None and index_pct <= -INDEX_BIG_MOVE:
        found["大跌守線"] = f"大盤 {index_pct:+.2f}%"
    if index_pct is not None and index_pct >= INDEX_BIG_MOVE:
        found["大盤強彈"] = f"大盤 {index_pct:+.2f}%"
    if hero_amount and hero_pct is not None:
        against = (hero_amount > 0 and hero_pct <= -DIVERGENCE_PCT) or (hero_amount < 0 and hero_pct >= DIVERGENCE_PCT)
        flat_big = abs(hero_pct) < DIVERGENCE_PCT and abs(hero_amount) >= FLAT_BIG_FLOW
        if against or flat_big:
            found["量價背離"] = f"{hero_name} 外資 {hero_amount:+.1f}億、股價 {hero_pct:+.2f}%"
    if sell_row and buy_row and _sell_key != _buy_key and top_sell >= ROTATION_SELL and top_buy >= ROTATION_BUY:
        found["換手"] = f"{sell_row.get('name')} 賣超 {top_sell:.1f}億、{buy_row.get('name')} 買超 {top_buy:.1f}億"
    if sell_row and top_sell >= WITHDRAWAL_SELL and (top_buy < WITHDRAWAL_WEAK_BUY or (index_pct is not None and index_pct < 0)):
        found["權值提款"] = f"{sell_row.get('name')} 賣超 {top_sell:.1f}億"
    limit_ups = _limit_ups(facts, structured_data)
    if (hero_pct is not None and hero_pct >= LIMIT_PCT) or limit_ups >= 2:
        found["漲停鎖量"] = (
            f"{hero_name} 漲停" if hero_pct is not None and hero_pct >= LIMIT_PCT else f"買超榜 {limit_ups} 檔漲停"
        )
    if hero_amount and hero_amount > 0 and hero_pct is not None and hero_pct >= SURGE_PCT:
        found["單股暴衝"] = f"{hero_name} 買超 {hero_amount:.1f}億、{hero_pct:+.2f}%"
    trust = (facts.fact(hero_key) or {}).get("trust_amount") if hero_key else None
    if hero_amount and trust is not None and abs(trust) >= TRUST_CONFLICT and (trust > 0) != (hero_amount > 0):
        found["土洋對作"] = f"{hero_name} 外資 {hero_amount:+.1f}億、投信 {trust:+.1f}億"
    out = [{"archetype": name, "reason": found[name]} for name in ARCHETYPE_ORDER if name in found]
    if not out:
        out.append({"archetype": "盤整觀望", "reason": f"大盤 {index_pct:+.2f}%" if index_pct is not None else "無明顯異常"})
    return out


# ---------------------------------------------------------------------------
# recency / bans
# ---------------------------------------------------------------------------


def _story_rows(log_path):
    try:
        from modules.pipeline_support import _is_story_row, _table_cells

        with open(log_path, "r", encoding="utf-8") as f:
            return [_table_cells(line) for line in f if _is_story_row(line)]
    except OSError:
        return []


def previous_sessions(date_str, n) -> list[str]:
    """The ``n`` TWSE sessions before ``date_str`` (newest first)."""
    try:
        from modules.twse_calendar import prev_session

        day = datetime.date.fromisoformat(str(date_str)[:10])
        out = []
        for _ in range(n):
            day = prev_session(day)
            out.append(day.isoformat())
        return out
    except Exception:
        return []


# 「印刷廠／隔夜改版漫畫（漲紅跌綠）：…」 → the label before the first 「：」.
_LABEL_RE = re.compile(r"^\s*([^：:；;。，,\n]{2,40}?)\s*[：:]")


def theme_scene_head(cell) -> str:
    """
    The scene a logged theme used. In the house format that is the label before 「：」,
    read from the raw cell: ``scene_only`` would drop a first clause that merely
    mentions 「昨天／上週」 (10/01 「群創昨天的紅色印版…」, 09/23 「…從昨天高點俯衝…」)
    and with it the label. Without a label, the cell minus its 「禁止…」 pieces
    (lists of older scenes never count as uses).
    """
    from modules.packaging_bans import _BAN_WORD_RE, without_ban_clauses

    text = str(cell or "").strip()
    match = _LABEL_RE.match(text)
    if match and not _BAN_WORD_RE.search(match.group(1)):
        return match.group(1).strip()
    return without_ban_clauses(text).strip()


def _session_cells(log_path, date_str, sessions) -> list[tuple[str, str]]:
    """[(date, raw 視覺風格 cell)] of the logged themes in the last ``sessions`` TWSE sessions before ``date_str``."""
    rows = [cells for cells in _story_rows(log_path) if len(cells) > 4 and cells[0] < str(date_str)]
    days = previous_sessions(date_str, sessions)
    if not days:  # calendar unavailable: the latest distinct logged dates
        days = sorted({cells[0] for cells in rows}, reverse=True)[:sessions]
    wanted = set(days)
    return [(cells[0], cells[4]) for cells in rows if cells[0] in wanted and cells[4].strip() not in _UNSET_THEMES]


def recent_theme_cells(log_path, date_str, sessions=RECENT_SESSIONS) -> list[tuple[str, str]]:
    """[(date, scene head)] for the last ``sessions`` TWSE sessions before ``date_str``."""
    out = []
    for day, cell in _session_cells(log_path, date_str, sessions):
        head = theme_scene_head(cell)
        if head:
            out.append((day, head))
    return out


def logged_scene_bans(log_path, date_str, sessions=RECENT_SESSIONS) -> list[str]:
    """
    Scene words the operator banned in the 「禁止A／B／C…」 list of a theme logged in
    the last ``sessions`` TWSE sessions (10/13: 「禁止實驗室滴定管／…／印刷廠／…／雲霄
    飛車／超商退貨／…」). Read on every run, so the bans hold even while
    data/packaging_bans.json is empty and lapse ``sessions`` sessions after the last
    paste; nothing is written.
    """
    from modules.packaging_bans import parse_ban_clauses, split_visual_theme

    patterns = []
    for day, cell in _session_cells(log_path, date_str, sessions):
        try:
            _scene, clauses = split_visual_theme(cell)
            items = parse_ban_clauses(clauses, datetime.date.fromisoformat(day[:10]))
        except ValueError:
            continue
        for item in items:
            pattern = str(item.get("pattern") or "").strip()
            if item.get("type") == "visual_object" and len(pattern) >= 2 and pattern not in patterns:
                patterns.append(pattern)
    return patterns


def scene_keywords(scene) -> list[str]:
    words = [str(w).strip() for w in scene.get("match") or [] if str(w).strip()]
    words.extend(part.strip() for part in re.split(r"[／/]", str(scene.get("label") or "")) if len(part.strip()) >= 2)
    return list(dict.fromkeys(words))


def scene_used_in(scene, cells) -> list[str]:
    """Dates whose logged theme uses this scene (any keyword or label part)."""
    words = scene_keywords(scene)
    return [day for day, cell in cells if any(word in cell for word in words)]


def scene_ban(scene, bans_state, logged_bans=()) -> str:
    """
    The ban pattern that covers this scene ("" when none): an active ``visual_object``
    ban in packaging_bans, or a scene word from a recently logged 「禁止…」 list.
    """
    try:
        from modules.packaging_bans import recent_visual_objects

        patterns = list(recent_visual_objects(bans_state or {}))
    except Exception:
        patterns = []
    patterns.extend(logged_bans or ())
    words = scene_keywords(scene)
    for pattern in patterns:
        pattern = str(pattern or "").strip()
        if len(pattern) < 2:
            continue
        if any(pattern in word or word in pattern for word in words):
            return pattern
    return ""


def _seeded_index(date_str, n, salt="visual-scene") -> int:
    digest = hashlib.sha256(f"{date_str}|{salt}".encode("utf-8")).hexdigest()
    return int(digest, 16) % n if n else 0


# ---------------------------------------------------------------------------
# composing
# ---------------------------------------------------------------------------


def pct_phrase(pct) -> str:
    """「噴9.4%」「跌3.1%」「鎖漲停」「守平盤」 — the same words the cover gate accepts."""
    from modules.title_fact_gate import format_pct_abs, stock_move_word

    if pct is None:
        return ""
    word = stock_move_word(pct)
    if word in {"漲停", "跌停"}:
        return f"鎖{word}"
    if word == "平盤":
        return "守平盤"
    return f"{word}{format_pct_abs(pct)}"


def _display_name(name) -> str:
    from modules.title_fact_gate import normalize_name

    return normalize_name(name) or str(name or "").strip()


_SLOT_RE = re.compile(r"\{([a-z_]+)\}")
_OPTIONAL_RE = re.compile(r"\[([^\[\]]*)\]")


def _fill(template, **slots) -> str:
    """Fill {slots}; an optional 「[…]」 segment is dropped when any slot inside it is empty."""

    def _value(name):
        return str(slots.get(name) or "")

    def _optional(match):
        body = match.group(1)
        if any(not _value(name) for name in _SLOT_RE.findall(body)):
            return ""
        return body

    text = _OPTIONAL_RE.sub(_optional, str(template or ""))
    return _SLOT_RE.sub(lambda m: _value(m.group(1)), text).strip()


def _clause(scene, side, name, amount_text, pct_text, foreign_amount):
    """
    One stock clause from the scene's buy / sell / divergence template. The colour
    marks the foreign flow (紅色 買超／綠色 賣超); the price move stays in words
    (``pct_phrase``), so a divergence day reads 「紅色…買超55.3億…卻跌3.1%」.
    """
    templates = scene.get("templates") or {}
    flow_color, flow_word = ("紅色", "買超") if foreign_amount > 0 else ("綠色", "賣超")
    template = templates.get(side)
    if not template:
        return ""
    return _fill(
        template,
        hero=name,
        other=name,
        amount=amount_text,
        pct=pct_text,
        flow_color=flow_color,
        flow_word=flow_word,
    )


def _side_for(amount, pct) -> str:
    if amount > 0:
        return "buy" if pct is None or pct > 0 else "divergence"
    return "sell" if pct is None or pct < 0 else "divergence"


def _is_counterpart(row, hero) -> bool:
    amount = (row or {}).get("foreign_amount")
    return amount not in (None, 0) and (amount > 0) != (hero["foreign_amount"] > 0) and abs(amount) >= COUNTERPART_MIN


def title_a_counterpart(facts, titles, hero):
    """
    (key, row, amount text) of the first opposite-side stock title A names with a local
    amount ≥ ``COUNTERPART_MIN`` — both covers pair with title A, so the scene tells
    title A's story (「南亞買55.3億…南亞科卻遭砍110億」 → 南亞科, not 台積電). The amount
    keeps title A's wording when it agrees with the fact (「110億」). None otherwise.
    """
    from modules.title_fact_gate import format_amount, title_amount_for, title_stock_keys

    title_a = next((str(t) for t in titles or [] if str(t or "").strip()), "")
    if not title_a:
        return None
    for key in title_stock_keys(title_a, facts):
        row = facts.fact(key) or {}
        if key == hero.get("key") or not _is_counterpart(row, hero):
            continue
        return key, row, title_amount_for(title_a, key, facts) or format_amount(row["foreign_amount"])
    return None


def build_context(structured_data, titles=None, facts=None, hero=None) -> dict:
    """
    Hero (= cover hero), counterpart and archetypes from local facts only. The
    counterpart is the opposite-side stock title A names (with a local amount), else
    the opposite leaderboard's leader.
    """
    from modules.title_fact_gate import build_fact_index, choose_hero, format_amount

    structured_data = structured_data or {}
    facts = facts or build_fact_index(structured_data)
    hero = hero if hero is not None else choose_hero(list(titles or []), facts=facts)
    ctx = {"facts": facts, "hero": hero or {}, "archetypes": classify_archetypes(structured_data, facts, hero or {})}
    if not hero or hero.get("foreign_amount") in (None, 0) or not hero.get("amount"):
        return ctx
    named = title_a_counterpart(facts, titles, hero)
    if named:
        other_key, other_row, other_amount = named
        source = "title_a"
    else:
        other_list = "heavyweights_dumped" if hero["foreign_amount"] > 0 else "safe_havens_bought"
        other_key, other_row = _leader(facts, structured_data, other_list, exclude_key=hero.get("key"))
        if not _is_counterpart(other_row, hero):
            return ctx
        other_amount = format_amount(other_row["foreign_amount"])
        source = "leaderboard"
    ctx["other"] = {
        "key": other_key,
        "name": _display_name(other_row.get("name") or other_key),
        "foreign_amount": other_row["foreign_amount"],
        "amount": other_amount,
        "source": source,
    }
    return ctx


def compose_metaphor(scene, ctx, archetype, with_other=True) -> str:
    hero = ctx.get("hero") or {}
    amount = hero.get("foreign_amount")
    if not amount or not hero.get("amount"):
        return ""
    name = _display_name(hero.get("name") or hero.get("ticker"))
    pct = hero.get("pct_change")
    hero_clause = _clause(scene, _side_for(amount, pct), name, hero["amount"], pct_phrase(pct), amount)
    if not hero_clause:
        return ""
    other = ctx.get("other") if with_other else None
    if not other:
        return hero_clause
    other_side = "buy" if other["foreign_amount"] > 0 else "sell"
    other_clause = _clause(scene, other_side, other["name"], other["amount"], "", other["foreign_amount"])
    if not other_clause:
        return hero_clause
    if archetype == "換手" and other_side == "sell":
        return f"{other_clause}，{hero_clause}"  # the money leaves the heavyweight first
    return f"{hero_clause}，{other_clause}"


def format_theme(label, metaphor, cast="") -> str:
    """「<label>漫畫（漲紅跌綠）：<metaphor>[；<cast>]」 — no A/B cover-text note (the hypothesis sets it)."""
    return f"{label}{FORMAT_INFIX}{metaphor}；{cast}" if cast else f"{label}{FORMAT_INFIX}{metaphor}"


def theme_is_clean(theme) -> bool:
    """The theme survives the /2 scene split unchanged (no rule / ban clause, no cut)."""
    from modules.packaging_bans import scene_only

    return len(theme) <= MAX_CHARS and scene_only(theme).rstrip("。") == theme


def _facts_ok(metaphor, facts) -> bool:
    from modules.title_fact_gate import check_text, has_errors

    return not has_errors(check_text(metaphor, where="自動視覺主題", facts=facts))


def _compose_checked(scene, ctx, archetype):
    """Longest theme that is ≤ 150 chars, split-clean and fact-checked (counterpart / cast optional)."""
    for with_other in (True, False):
        metaphor = compose_metaphor(scene, ctx, archetype, with_other=with_other)
        if not metaphor or not _facts_ok(metaphor, ctx["facts"]):
            continue
        for cast in (scene.get("cast") or "", ""):
            theme = format_theme(scene["label"], metaphor, cast)
            if theme_is_clean(theme):
                return theme, with_other and bool(ctx.get("other")), bool(cast)
    return "", False, False


def auto_visual_theme(
    date_str,
    structured_data,
    titles=None,
    log_path=None,
    bans_state=None,
    library_path=None,
    hero=None,
) -> dict | None:
    """
    {theme, scene_id, label, archetype, archetypes, hero, other, excluded_recent,
    excluded_banned, logged_bans, candidates, recent_cells} — or None when no auto theme can be
    built (no hero with a local amount, no library); the caller falls back to
    財經戰情室.
    """
    try:
        library = load_library(library_path)
    except (OSError, ValueError) as exc:
        print(f"  ⚠️  視覺場景庫讀取失敗，略過自動主題：{exc}")
        return None
    scenes = sorted(library.get("scenes") or [], key=lambda s: s["id"])
    if not scenes:
        return None
    ctx = build_context(structured_data, titles=titles, hero=hero)
    hero_row = ctx.get("hero") or {}
    if not hero_row.get("amount") or not hero_row.get("foreign_amount"):
        return None

    if log_path is None:
        from modules.report_paths import resolve_shared_artifact

        log_path = resolve_shared_artifact(os.path.join(BASE_DIR, "reports"), "channel_story_log")
    recent = recent_theme_cells(log_path, date_str, RECENT_SESSIONS)
    fresh_window = recent_theme_cells(log_path, date_str, FRESH_SESSIONS)
    logged_bans = logged_scene_bans(log_path, date_str, RECENT_SESSIONS)
    excluded_recent, excluded_banned, allowed = {}, {}, []
    for scene in scenes:
        used = scene_used_in(scene, recent)
        if used:
            excluded_recent[scene["id"]] = used
            continue
        ban = scene_ban(scene, bans_state, logged_bans)
        if ban:
            excluded_banned[scene["id"]] = ban
            continue
        allowed.append(scene)

    archetypes = [row["archetype"] for row in ctx["archetypes"]]
    tiers = [[s for s in allowed if name in (s.get("archetypes") or [])] for name in archetypes]
    tiers.append(list(allowed))  # any allowed scene beats the generic fallback
    for tier_index, tier in enumerate(tiers):
        if not tier:
            continue
        archetype = archetypes[tier_index] if tier_index < len(archetypes) else archetypes[0]
        fresh = [s for s in tier if not scene_used_in(s, fresh_window)]
        pool = fresh or tier
        start = _seeded_index(date_str, len(pool))
        for offset in range(len(pool)):
            scene = pool[(start + offset) % len(pool)]
            theme, used_other, used_cast = _compose_checked(scene, ctx, archetype)
            if not theme:
                continue
            return {
                "theme": theme,
                "scene_id": scene["id"],
                "label": scene["label"],
                "archetype": archetype,
                "archetypes": ctx["archetypes"],
                "hero": {k: hero_row.get(k) for k in ("key", "label", "name", "amount", "foreign_amount", "pct_change")},
                "other": ctx.get("other") if used_other else None,
                "cast": used_cast,
                "candidates": len(pool),
                "excluded_recent": excluded_recent,
                "excluded_banned": excluded_banned,
                "logged_bans": logged_bans,
                "recent_cells": recent,
            }
    return None


def describe(result) -> str:
    """One log line: which scene, why, and what was excluded."""
    if not result:
        return ""
    hero = result.get("hero") or {}
    bits = [
        f"場景 {result['label']}（{result['scene_id']}）",
        f"archetype {result['archetype']}",
        f"主角 {hero.get('label') or hero.get('name')} {hero.get('amount')}",
        f"候選 {result.get('candidates')} 個",
    ]
    if result.get("excluded_recent"):
        bits.append(f"排除近 {RECENT_SESSIONS} 個交易日用過 {len(result['excluded_recent'])} 個")
    if result.get("excluded_banned"):
        bits.append(f"排除禁用 {len(result['excluded_banned'])} 個")
    return "；".join(bits)


# ---------------------------------------------------------------------------
# provenance sidecar (reports/youtube/<date>/visual_theme.json)
# ---------------------------------------------------------------------------

SIDECAR_NAME = "visual_theme.json"


def sidecar_path(reports_dir, date_str) -> str:
    from modules.report_paths import youtube_dir

    return os.path.join(youtube_dir(reports_dir, date_str), SIDECAR_NAME)


def read_sidecar(reports_dir, date_str) -> dict:
    try:
        with open(sidecar_path(reports_dir, date_str), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_sidecar(reports_dir, date_str, payload) -> str:
    from modules.atomic_io import atomic_write_json
    from modules.report_paths import ensure_youtube_dir

    ensure_youtube_dir(reports_dir, date_str)
    path = sidecar_path(reports_dir, date_str)
    data = {k: v for k, v in (payload or {}).items() if k != "recent_cells"}
    data["date"] = str(date_str)
    data["written_at"] = datetime.datetime.now().replace(microsecond=0).isoformat(sep=" ")
    atomic_write_json(path, data)
    return path
