"""
Structured packaging bans (finding 82/91): ``data/packaging_bans.json``.

Operators used to append 「禁止…」 clauses to the free-text ``--visual-theme``; those
clauses then leaked into the channel log, wiki, image prompts and NotebookLM cover
instructions, never expired, and even banned the channel's best-performing structures.

Now:
- ``split_visual_theme`` classifies clause by clause: 「禁止／不要／不得／勿用…」 bans and
  explicit title / narration rules (標題、開場、口播、昨天、明天開盤…) leave the scene; every
  positive visual or cover instruction (incl. A/B cover-text layout notes) stays in the
  scene (≤150 chars) for the thumbnail and NotebookLM cover prompts
- bans carry ``created`` / ``expires`` (TTL in TWSE sessions, counted from the FIRST time
  a pattern was seen — repeating yesterday's list does not extend it)
- positive title / narration rules are day-specific operator instructions, not bans:
  they are active on the session they were pasted (re-pasting refreshes them) and the
  latest paste replaces earlier ones
- title-affecting bans are capped (newest first); visual-object and visual-rule bans
  are only a "recently used / avoided" list for the operator and never reach any prompt
  (the thumbnail prompt keeps its own fixed IP / logo guard rails)
- a structure / hero ban is auto-exempt when its source video is in the channel's top
  quartile, or younger than the maturity window (not enough data to judge)
- active bans are injected once, into the /2 title prompt only
"""

from __future__ import annotations

import datetime
import json
import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BANS_PATH = os.path.join(BASE_DIR, "data", "packaging_bans.json")

TTL_SESSIONS = 5
# Per-cluster caps (newest first); title-affecting clusters sum to MAX_TITLE_BANS.
CLUSTER_CAPS = {"title_structure": 4, "stock_hero": 2, "title_rule": 2, "visual_object": 12, "visual_rule": 8}
MAX_TITLE_BANS = sum(CLUSTER_CAPS[t] for t in ("title_structure", "stock_hero", "title_rule"))
MAX_VISUAL_OBJECTS = CLUSTER_CAPS["visual_object"]
PRUNE_AFTER_DAYS = 30
SCENE_MAX_CHARS = 150
TITLE_TYPES = ("title_structure", "stock_hero", "title_rule")

BAN_PREFIXES = ("禁止", "不要", "不得", "勿用")
# A ban clause (optionally after a conjunction: 「但不要使用 Marvel…」).
_BAN_CLAUSE_RE = re.compile(r"^(?:但|且|並|另外|也)?\s*(?=" + "|".join(BAN_PREFIXES) + ")")
# A negative instruction in mid-clause (「南亞科不要當主角」) never stays in an image prompt either.
_BAN_WORD_RE = re.compile("|".join(BAN_PREFIXES))
# Positive title / narration rules (day-specific): they belong to the title prompt, not the scene.
_TITLE_RULE_MARKERS = re.compile(
    r"標題|開場|口播|旁白|主持人|交易日說|可以說|說成|講成|今天週|今天是週|昨天|前天|上週|下週|明天開盤|上集"
)
# A piece without its own marker that continues the previous rule (「禁止AMD開場，改用台積電」).
_CONTINUATION_RE = re.compile(r"^(?:改用|改成|改為|改寫|而是|換成|換用)")
# Ban clauses about the picture / cover (never title rules, never in a prompt).
_TITLE_BAN_WORDS = re.compile(r"標題|開場|口播|旁白|主角|同構|句型|字眼|用語|說|講")
_VISUAL_BAN_WORDS = re.compile(r"封面|縮圖|畫面|構圖|圖中|背景|角色|Logo|logo|LOGO|IP|漫畫|配色|紅跌|綠漲|紅漲|綠跌|Marvel|迪士尼")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_SOURCE_DATE_RE = re.compile(r"[（(]\s*(\d{1,2})/(\d{1,2})([^）)]*)[）)]")
_VIEWS_IN_REASON_RE = re.compile(r"(\d[\d,]*)\s*views", re.I)


def _today():
    return datetime.date.today()


def _as_date(value):
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _add_sessions(day, n):
    try:
        from modules.twse_calendar import next_session

        cursor = day
        for _ in range(n):
            cursor = next_session(cursor)
        return cursor
    except Exception:
        return day + datetime.timedelta(days=int(n * 7 / 5) + 1)


# ─────────────────────────────────────────
# Visual theme splitting
# ─────────────────────────────────────────
_TITLE_PHRASE_RE = re.compile(r"誰|被砍|開場|標題|狂掃|狂砍|吸籌|建倉|量榜")


_SEGMENT_SEPS = "。\n；;"
_PIECE_SEPS = "，,"


def _tokens(text):
    """[(piece, sep)] split on sentence / clause / comma separators outside （）; sep is "" at the end."""
    tokens, buf, depth = [], [], 0
    for ch in str(text or ""):
        if ch in "（(":
            depth += 1
        elif ch in "）)" and depth:
            depth -= 1
        if depth == 0 and ch in _SEGMENT_SEPS + _PIECE_SEPS:
            tokens.append(("".join(buf), ch))
            buf = []
            continue
        buf.append(ch)
    tokens.append(("".join(buf), ""))
    return tokens


def _segments(text):
    """[[(piece, sep), ...], ...]: one list per sentence / ；-clause, pieces split on commas."""
    segments, current = [], []
    for piece, sep in _tokens(text):
        current.append((piece, sep))
        if sep in _SEGMENT_SEPS and sep:
            segments.append(current)
            current = []
    if current:
        segments.append(current)
    return segments


def _piece_kind(piece):
    """'ban' | 'rule' (positive title / narration rule) | 'scene'."""
    text = piece.strip()
    if _BAN_CLAUSE_RE.match(text) or _BAN_WORD_RE.search(text):
        return "ban"
    if _TITLE_RULE_MARKERS.search(text):
        return "rule"
    return "scene"


def _cut_at_boundary(text, max_chars):
    """Hard limit without cutting a word: last comma / 、 / space before ``max_chars``."""
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    cut = max(head.rfind(sep) for sep in ("，", ",", "、", " ", "／"))
    if cut >= max_chars // 2:
        head = head[:cut]
    return head.rstrip(" ，,、／")


def split_visual_theme(theme, max_chars=SCENE_MAX_CHARS):
    """
    (scene, rule_clauses), classified per comma piece.

    - ban pieces (「禁止／不要／不得／勿用…」, also mid-clause) and positive title / narration rules
      go to ``rule_clauses`` (a rule's continuation 「…，改用X」 stays with it; consecutive
      positive rule pieces stay one clause)
    - everything else — scene, style, cover / visual directions, A/B cover-text layout
      notes — stays in the scene, with its original punctuation, cut at a sentence
      boundary to ``max_chars`` (a single over-long sentence is cut at a comma / word)
    """
    cjk = bool(_CJK_RE.search(str(theme or "")))
    scene_segments, rules = [], []
    for segment in _segments(theme):
        groups = []  # [kind, [(piece, sep), ...]]
        for piece, sep in segment:
            text = piece.strip()
            if not text:
                continue
            kind = _piece_kind(text)
            prev = groups[-1][0] if groups else None
            if kind == "scene" and prev in {"ban", "rule"} and _CONTINUATION_RE.match(text):
                groups[-1][1].append((piece, sep))  # 「…，改用X」 continues that clause
                continue
            if kind == prev and kind != "ban":  # every ban is its own clause
                groups[-1][1].append((piece, sep))
                continue
            groups.append([kind, [(piece, sep)]])
        scene_bits = []
        for kind, pieces in groups:
            if kind == "scene":
                scene_bits.append(pieces)
                continue
            clause = "".join(p + (s if i < len(pieces) - 1 else "") for i, (p, s) in enumerate(pieces)).strip(" ，,")
            if kind == "ban":
                clause = re.sub(r"^(?:但|且|並|另外|也)\s*", "", clause)
            if clause:
                rules.append(clause)
        if not scene_bits:
            continue
        terminal = segment[-1][1]
        end = {"\n": "。" if cjk else ". ", "": ""}.get(terminal, terminal)
        parts = []
        for pieces in scene_bits:
            parts.append("".join(p + s for p, s in pieces[:-1]) + pieces[-1][0])
        joiner = "，" if cjk else ", "
        text = joiner.join(part.strip() for part in parts if part.strip())
        if text:
            scene_segments.append(text + end)
    scene, used = "", 0
    for seg in scene_segments:
        if used + len(seg) > max_chars:
            if not scene:
                scene = _cut_at_boundary(seg, max_chars)
            break
        scene += seg
        used += len(seg)
    scene = scene.strip()
    if cjk:
        scene = re.sub(r"[；;，,、]$", "", scene)
        if scene and not scene.endswith("。"):
            scene += "。"
    else:
        scene = scene.rstrip(" ;,")
    return scene, rules


def scene_only(theme, max_chars=SCENE_MAX_CHARS):
    return split_visual_theme(theme, max_chars=max_chars)[0]


def without_ban_clauses(theme) -> str:
    """
    ``theme`` with only its 「禁止／不要…」 pieces removed: scene AND positive rule pieces
    stay, uncut. Used to see which scene a logged theme drew — ``scene_only`` drops a
    scene piece that merely mentions 「昨天／上週」 as a title rule (「群創昨天的紅色印版…」).
    """
    kept = []
    for segment in _segments(theme):
        pieces = [piece.strip() for piece, _sep in segment if piece.strip() and _piece_kind(piece) != "ban"]
        if pieces:
            kept.append("，".join(pieces))
    return "。".join(kept)


# 「A兩行股名+金額，B短句大字」-style cover-text notes: the old two-variable A/B
# instruction. The cover text of each arm is now set by the week's A/B hypothesis
# (data/ab_test_plan.json), so the note is dropped from every /2 output (image
# prompts, channel log, package, NotebookLM / director-card cover lines).
_COVER_TEXT_NOTE_RE = re.compile(
    r"[；;，,。]?\s*A\s*(?:圖|組)?\s*(?:兩行|第一行)[^；;。]*?[，,、]\s*B\s*(?:圖|組)?\s*(?:短句|破碎字|殘句|大字|第一行)[^；;。]*"
)


def strip_cover_text_note(theme) -> str:
    return _COVER_TEXT_NOTE_RE.sub("", str(theme or "")).strip(" ；;，,")


# ─────────────────────────────────────────
# Clause parsing
# ─────────────────────────────────────────
def _source_date(text, created):
    match = _SOURCE_DATE_RE.search(text)
    if not match:
        return None, ""
    month, day, note = int(match.group(1)), int(match.group(2)), match.group(3)
    year = created.year if (month, day) <= (created.month, created.day) else created.year - 1
    try:
        return datetime.date(year, month, day), note
    except ValueError:
        return None, note


def parse_ban_clauses(clauses, created):
    """Rule clauses → [{type, pattern, raw, source_date, note}] (no ids / TTL yet)."""
    created = _as_date(created) or _today()
    items = []
    for clause in clauses or []:
        clause = re.sub(r"^(?:但|且|並|另外|也)\s*(?=" + "|".join(BAN_PREFIXES) + ")", "", str(clause).strip())
        if not clause:
            continue
        if not clause.startswith(BAN_PREFIXES):
            if _BAN_WORD_RE.search(clause) and not _TITLE_BAN_WORDS.search(clause) and _VISUAL_BAN_WORDS.search(clause):
                items.append({"type": "visual_rule", "pattern": clause, "raw": clause})  # 「封面不要放Logo」
                continue
            # Positive / day-specific instruction (「上一交易日說上週四」「南亞科不要當主角」):
            # today's title prompt only, verbatim.
            items.append({"type": "title_rule", "pattern": clause, "raw": clause, "positive": True})
            continue
        body = re.sub("^(" + "|".join(BAN_PREFIXES) + ")", "", clause).strip()
        source, note = _source_date(body, created)
        pattern = _SOURCE_DATE_RE.sub("", body).strip()
        hero = re.match(r"^(.+?)當標題主角$", pattern)
        if hero:
            for name in re.split(r"[、，,／/]", hero.group(1)):
                if name.strip():
                    items.append({"type": "stock_hero", "pattern": name.strip(), "raw": clause})
            continue
        if "同構" in pattern or source:
            items.append(
                {
                    "type": "title_structure",
                    "pattern": pattern.replace("同構", "").strip(),
                    "raw": clause,
                    "source_date": source.isoformat() if source else None,
                    "note": note.strip(),
                }
            )
            continue
        objects = [p.strip() for p in re.split(r"[／/]", pattern) if p.strip()]
        if len(objects) >= 3 and not _TITLE_PHRASE_RE.search(pattern) and not re.search(r"寫|說|用", pattern):
            for obj in objects:
                items.append({"type": "visual_object", "pattern": obj, "raw": clause})
            continue
        if not _TITLE_BAN_WORDS.search(pattern) and _VISUAL_BAN_WORDS.search(pattern):
            # 「不要使用 Marvel 角色」「禁止真實公司Logo」: picture rules, not title rules.
            items.append({"type": "visual_rule", "pattern": pattern, "raw": clause})
            continue
        items.append({"type": "title_rule", "pattern": pattern, "raw": clause})
    return items


# ─────────────────────────────────────────
# Registry
# ─────────────────────────────────────────
def load_bans(path=DEFAULT_BANS_PATH):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("bans"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "ttl_sessions": TTL_SESSIONS, "max_title_bans": MAX_TITLE_BANS, "bans": []}


def save_bans(state, path=DEFAULT_BANS_PATH):
    from modules.atomic_io import atomic_write_json

    state["updated_at"] = datetime.datetime.now().replace(microsecond=0).isoformat(sep=" ")
    atomic_write_json(path, state)
    return path


def _key(item):
    return f"{item['type']}:{item['pattern']}"


def _source_row(window, source_date):
    if not window or not source_date:
        return None
    rows = [r for r in window.get("rows") or [] if r.get("date") == source_date]
    return rows[-1] if rows else None


def _hero_rows(window, name):
    from modules.youtube_learning import strip_brand

    return [r for r in (window or {}).get("sample") or [] if strip_brand(r.get("title")).startswith(name)]


def evaluate_exemption(ban, window):
    """Return an exemption reason (str) or "" — the ban stays active."""
    if not window:
        return ""
    baseline = window.get("baseline") or {}
    # top quartile, or ≥ 2× channel median (whichever is lower) proves the structure works
    cutoffs = [v for v in (baseline.get("q75"), baseline.get("win_threshold")) if v is not None]
    q75 = min(cutoffs) if cutoffs else None
    if ban["type"] == "title_structure":
        row = _source_row(window, ban.get("source_date"))
        if row is None:
            return ""
        if not row.get("mature"):
            return "來源影片上架未滿 72 小時，樣本不足，不得當禁令依據"
        metric = next((r["metric"] for r in window.get("sample") or [] if r.get("video_id") == row.get("video_id")), None)
        if metric is not None and q75 is not None and metric >= q75:
            return f"來源影片 {int(metric):,} views 達前四分位／中位數×2（≥{q75:.0f}），結構不得被禁"
    elif ban["type"] == "stock_hero" and q75 is not None:
        best = max((r["metric"] for r in _hero_rows(window, ban["pattern"])), default=None)
        if best is not None and best >= q75:
            return f"近期以「{ban['pattern']}」當主角的影片有 {int(best):,} views（≥{q75:.0f}，前段樣本），不得禁用"
    return ""


def register_bans(clauses, created, path=DEFAULT_BANS_PATH, window=None, source_text=""):
    """
    Merge today's rule clauses into the registry and refresh status flags.

    - a ban already in the registry keeps its original ``created`` / ``expires``
      (re-pasting yesterday's list never extends it)
    - positive operator instructions are day-specific: the latest paste replaces the
      previous set, and a re-pasted instruction is refreshed to expire after this session
    Returns the saved state.
    """
    created = _as_date(created) or _today()
    state = load_bans(path)
    items = parse_ban_clauses(clauses, created)
    pasted_positive = {_key(i) for i in items if i.get("positive")}
    state["bans"] = [b for b in state["bans"] if not b.get("positive") or _key(b) in pasted_positive]
    by_key = {_key(b): b for b in state["bans"]}
    for item in items:
        key = _key(item)
        if key in by_key:
            by_key[key]["last_seen"] = created.isoformat()
            if item.get("positive"):
                by_key[key]["expires"] = _add_sessions(created, 1).isoformat()
            continue
        entry = dict(item)
        entry["created"] = created.isoformat()
        # A structure ban protects against repeating a structure that was just used, so its
        # clock starts at the source video's date (an old 9/24 ban re-pasted on 10/12 is
        # already expired); other bans start when first seen.
        start = _as_date(item.get("source_date")) or created
        ttl = 1 if item.get("positive") else TTL_SESSIONS
        entry["expires"] = _add_sessions(min(start, created), ttl).isoformat()
        entry["last_seen"] = created.isoformat()
        if source_text:
            entry["source_text"] = str(source_text)[:80]
        state["bans"].append(entry)
        by_key[key] = entry
    refresh_status(state, today=created, window=window)
    save_bans(state, path)
    return state


def refresh_status(state, today=None, window=None):
    today = _as_date(today) or _today()
    keep = []
    for ban in state.get("bans") or []:
        expires = _as_date(ban.get("expires"))
        if expires and (today - expires).days > PRUNE_AFTER_DAYS:
            continue  # pruned; may be re-added later as a brand-new ban
        if ban.get("positive"):
            # today's operator instruction: only on the session it was (last) pasted
            ban["status"] = "active" if ban.get("last_seen") == today.isoformat() else "expired"
            keep.append(ban)
            continue
        reason = evaluate_exemption(ban, window) if window else ban.get("exempt_reason", "")
        ban["exempt_reason"] = reason
        if expires and today >= expires:
            ban["status"] = "expired"
        elif reason:
            ban["status"] = "exempt"
        else:
            ban["status"] = "active"
        keep.append(ban)
    # per-cluster caps, newest first (positive day notes are not bans and are not capped)
    for ban_type, cap in CLUSTER_CAPS.items():
        active = [b for b in keep if b["type"] == ban_type and b["status"] == "active" and not b.get("positive")]
        active.sort(key=lambda b: (b.get("source_date") or b.get("created") or "", keep.index(b)), reverse=True)
        for ban in active[cap:]:
            ban["status"] = "capped"
    state["bans"] = keep
    return state


def active_bans(state, types=TITLE_TYPES):
    return [b for b in (state or {}).get("bans") or [] if b.get("type") in types and b.get("status") == "active"]


_PLACEHOLDER_RE = re.compile(r"(?<![A-Za-z])[A-Z](?![A-Za-z])")  # 「買A、B才是量第一」 → A / B are slots
_FRAGMENT_SPLIT_RE = re.compile(r"[、，,\s]+")
# parse_ban_clauses only strips 禁止／不要／不得／勿用, so the stored pattern can
# still start with the verb of the instruction (「不要用AMD開場」→「用AMD開場」,
# 「不要再用被砍同構」→「再用被砍」) or end with what the words are (「勿用「漲停」
# 字眼」→「「漲停」字眼」). Neither part is ever in a title.
_BAN_LEAD_VERB_RE = re.compile(r"^(?:再|又|還)?(?:使用|採用|沿用|用|寫成|寫|說成|說|講成|講|讓|拿|出現)\s*")
_BAN_TAIL_NOUN_RE = re.compile(r"\s*(?:的)?(?:字眼|用語|句型|說法|寫法|講法|詞彙)$")


def _strip_ban_wording(text):
    """The banned words themselves: the instruction's leading verb / trailing noun removed (>= 2 chars kept)."""
    text = str(text or "").strip()
    for regex in (_BAN_LEAD_VERB_RE, _BAN_TAIL_NOUN_RE):
        stripped = regex.sub("", text, count=1).strip()
        if len(stripped) >= 2:
            text = stripped
    return text


def _ban_alternatives(pattern):
    """
    [(fragments, opener)] for one ban pattern: ／-separated alternatives, each a
    list of literal fragments (placeholders, quotes and the 「，改用…」 tail
    dropped; >= 2 chars) that must all appear in a title. ``opener`` marks a
    「X開場」 ban (「AMD／狂掃開場」 covers both): X has to be in the title's
    opening clause.
    """
    pieces = [p for p in re.split(r"[，,]", str(pattern or "")) if p.strip()]
    kept = []
    for piece in pieces:
        if kept and _CONTINUATION_RE.match(piece.strip()):
            break  # 「AMD開場，改用台積電」: the replacement is advice, not the ban
        kept.append(piece)
    text = "，".join(kept).replace("同構", "")
    text = re.sub(r"[「」『』“”\"']", "", text)
    text = text.strip()
    opener_all = text.endswith("開場")
    if opener_all:
        text = text[: -len("開場")]
    out = []
    for alt in re.split(r"[／/]", text):
        alt = alt.strip()
        opener = opener_all or alt.endswith("開場")
        if alt.endswith("開場"):
            alt = alt[: -len("開場")]
        alt = _strip_ban_wording(alt)
        alt = _PLACEHOLDER_RE.sub(" ", alt)
        fragments = [f for f in _FRAGMENT_SPLIT_RE.split(alt) if len(f) >= 2]
        if fragments:
            out.append((fragments, opener))
    return out


def title_ban_hits(title, state):
    """
    Active title bans (title_structure / stock_hero / non-positive title_rule)
    that ``title`` would break. Used to keep banned titles out of positive
    「可複製」 examples; exempt / expired / capped bans and day-specific
    operator instructions never match.
    """
    from modules.youtube_learning import strip_brand

    head = strip_brand(title or "")
    opening = re.split(r"[！!？?，,｜|]", head, maxsplit=1)[0]
    hits = []
    for ban in active_bans(state):
        if ban.get("positive"):
            continue
        pattern = str(ban.get("pattern") or "").strip()
        if not pattern:
            continue
        if ban.get("type") == "stock_hero":
            matched = head.startswith(_strip_ban_wording(pattern))  # 「不要讓台積電當標題主角」
        else:
            matched = False
            for fragments, opener in _ban_alternatives(pattern):
                if opener:
                    matched = all(f in opening for f in fragments)
                else:
                    matched = all(f in head for f in fragments)
                if matched:
                    break
        if matched:
            hits.append(ban)
    return hits


def format_bans_for_title_prompt(state):
    """The only place bans enter a prompt: the /2 title prompt, once."""
    labels = {"title_structure": "標題結構", "stock_hero": "主角股", "title_rule": "標題規則"}
    active = active_bans(state)
    exempt = [b for b in (state or {}).get("bans") or [] if b.get("status") == "exempt" and b.get("type") in TITLE_TYPES]
    lines = [f"# 包裝禁令（結構化；TTL {TTL_SESSIONS} 個交易日、上限 {MAX_TITLE_BANS} 條）"]
    if not active:
        lines.append("- 目前沒有有效禁令。")
    for ban in active:
        src = f"；來源 {ban['source_date'][5:]}" if ban.get("source_date") else ""
        if ban.get("positive"):
            lines.append(f"- [今日操作者指示] {ban['pattern']}（僅本交易日）")
            continue
        label = labels.get(ban["type"], ban["type"])
        lines.append(f"- [{label}] 禁止：{ban['pattern']}（到期 {ban.get('expires', '')[5:]}{src}）")
    for ban in exempt[:4]:
        lines.append(f"- 已豁免（可使用）：{ban['pattern']}——{ban.get('exempt_reason')}")
    return "\n".join(lines)


def recent_visual_objects(state):
    return [b["pattern"] for b in (state or {}).get("bans") or [] if b.get("type") == "visual_object" and b.get("status") == "active"]


def recent_visual_rules(state):
    """Operator-only list of picture / cover bans (never injected into an image prompt)."""
    return [b["pattern"] for b in (state or {}).get("bans") or [] if b.get("type") == "visual_rule" and b.get("status") == "active"]


def apply_visual_theme(raw_theme, date_str, path=DEFAULT_BANS_PATH, window=None):
    """
    /2 entry point: split ``--visual-theme``, register its rule clauses, return
    (scene_for_prompts, state). Never raises — on failure returns the scene only.
    """
    scene, clauses = split_visual_theme(raw_theme)
    state = None
    try:
        if clauses:
            state = register_bans(clauses, date_str, path=path, window=window, source_text=scene)
        else:
            state = load_bans(path)
            if str(raw_theme or "").strip() and any(b.get("positive") for b in state["bans"]):
                # a new theme without instructions replaces an earlier paste's instructions
                state = register_bans([], date_str, path=path, window=window)
            else:
                state = refresh_status(state, today=date_str, window=window)
    except Exception as exc:
        print(f"  ⚠️  packaging_bans 更新失敗，略過：{exc}")
    return scene, state
