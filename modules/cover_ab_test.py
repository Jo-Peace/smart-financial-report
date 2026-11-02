"""
Single-variable cover A/B tests (data/ab_test_plan.json).

/2 used to change the title syntax AND the cover composition between A and B and
told the operator 「先用 A，48 小時後再判斷是否換 B」: no result could be attributed,
and channel views are confounded by topic and video age. Now:

- one hypothesis per ISO week (rotation in the plan; ``AB_HYPOTHESIS`` overrides),
  e.g. H1 style 漫畫場景 vs 戰情室數據大字牌, H2 text 陳述句 vs 問句,
  H3 characters 有雙角色 vs 無角色, H4 number size / position;
- covers A and B share the hero, the verified number, the title pairing (title A),
  the scene and the composition — only the declared variable differs (H1 style tests
  the scene itself, so H1 keeps the composition only), and both prompts carry a
  ``- A/B variable: …`` line; on a war-room day (the 財經戰情室 fallback) H1's two
  arms would be near-identical, so the next non-style hypothesis is tested instead;
- both covers go up together in YouTube Studio 「測試與比較」 (Test & compare);
  the winner YouTube reports is recorded with ``cli.py youtube record-ab-result``
  (analytics.db ``ab_test_results``);
- learning only recommends promoting an arm after ≥ 3 decided tests with a
  majority (``LEVER_DEFAULTS`` is changed by hand), and an A/B result never creates
  a ban.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PLAN_PATH = os.path.join(BASE_DIR, "data", "ab_test_plan.json")
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "data", "analytics.db")
ENV_VAR = "AB_HYPOTHESIS"
DEFAULT_METRIC = "watch_time_share"
MIN_DECIDED_TESTS = 3
WINNERS = ("a", "b", "none")

# Lever defaults: every variable that is not under test keeps this value on both covers.
# A promoted arm (summarize_results) is a recommendation: change these by hand.
LEVER_DEFAULTS = {"style": "theme_scene", "text": "statement", "characters": "duo", "number_layout": "standard"}
# Theme presets that already are the war room: H1 (scene vs war-room data board) has no contrast on them.
WAR_ROOM_STYLES = frozenset({"war_room", "war_room_flow", "war_room_board"})

BLOCK_START = "<!-- cover-ab-test:start -->"
BLOCK_END = "<!-- cover-ab-test:end -->"
TITLE_HEADING = "## 📝 A/B 標題選項（測試與比較：標題＋封面字一起測，A 配封面 A、B 配封面 B）"
TITLE_A_LABEL = "標題 A（原方法；配封面 A）"
TITLE_B_LABEL = "標題 B（測試組；配封面 B）"
UPLOAD_STEP = "1. 照下方「今日 A/B 測試」在「測試與比較」同時上傳 A（標題 A＋封面 A）與 B（標題 B＋封面 B）"
CHECKLIST_ROW = "| 封面 A/B 結果（Studio 測試與比較 → record-ab-result） |  |  |"

# Daily package A/B (2026-10-30 decision): YouTube 「測試與比較」 tests title + cover headline
# together. FS = A 原方法 vs B few-shot; PK = FEWSHOT_AB=off fallback (A 原標題 A vs B 原標題 B),
# each cover with its own headline text. The weekly single-variable rotation is retired
# (plan hypotheses are disabled; AB_HYPOTHESIS=H1..H4 still forces one by hand).
PACKAGE_VARIABLES = frozenset({"fewshot", "title_package"})
PACKAGE_HYPOTHESES = (
    {
        "id": "FS",
        "variable": "fewshot",
        "label": "few-shot 標題＋封面字",
        "arms": {
            "a": {"mode": "baseline", "label": "原方法標題＋原封面字"},
            "b": {"mode": "fewshot", "label": "few-shot 標題＋自己的封面字"},
        },
    },
    {
        "id": "PK",
        "variable": "title_package",
        "label": "標題＋封面字（few-shot 關閉時）",
        "arms": {
            "a": {"mode": "title_a", "label": "標題 A＋完整封面字"},
            "b": {"mode": "title_b", "label": "標題 B＋自己的封面字"},
        },
    },
)


def is_package_test(hypothesis) -> bool:
    return bool(hypothesis) and hypothesis.get("variable") in PACKAGE_VARIABLES


def package_hypothesis(fewshot=True) -> dict:
    return dict(PACKAGE_HYPOTHESES[0 if fewshot else 1])


def fewshot_enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("FEWSHOT_AB", "on")).strip().lower() not in {"off", "0", "false", "no"}


_FALLBACK_PLAN = {
    "version": 1,
    "rotation": {"start_week_monday": "2026-10-12", "weeks_per_hypothesis": 1},
    "decision": {"metric": DEFAULT_METRIC, "min_decided_tests": MIN_DECIDED_TESTS},
    "hypotheses": [],
}


# ---------------------------------------------------------------------------
# plan / current hypothesis
# ---------------------------------------------------------------------------


def load_plan(path=None) -> dict:
    try:
        with open(path or DEFAULT_PLAN_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("hypotheses"), list):
            return data
    except (OSError, ValueError):
        pass
    return dict(_FALLBACK_PLAN)


def enabled_hypotheses(plan) -> list[dict]:
    return [
        h
        for h in (plan or {}).get("hypotheses") or []
        if isinstance(h, dict) and h.get("id") and h.get("variable") and h.get("enabled", True)
    ]


def find_hypothesis(plan, key) -> dict | None:
    """By id (「H2」) or variable (「text」), case-insensitive; disabled ones included."""
    key = str(key or "").strip().lower()
    if not key:
        return None
    for h in list((plan or {}).get("hypotheses") or []) + [dict(h) for h in PACKAGE_HYPOTHESES]:
        if isinstance(h, dict) and key in {str(h.get("id", "")).lower(), str(h.get("variable", "")).lower()}:
            return h
    return None


def _as_date(value) -> datetime.date:
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def iso_week_label(day) -> str:
    year, week, _ = _as_date(day).isocalendar()
    return f"{year}-W{week:02d}"


def current_hypothesis(day, plan=None, override=None, env=None) -> dict | None:
    """
    The test for ``day``: ``override`` (id / variable) > the ``AB_HYPOTHESIS`` env
    var > the daily package test (FS, or PK when FEWSHOT_AB=off). The weekly
    single-variable rotation is retired. Returns a copy with ``iso_week`` and
    ``source`` ("param" | "env" | "daily").
    """
    plan = plan if plan is not None else load_plan()
    env = os.environ if env is None else env
    day = _as_date(day)
    for source, key in (("param", override), ("env", env.get(ENV_VAR, ""))):
        if not key:
            continue
        found = find_hypothesis(plan, key)
        if found:
            return dict(found, iso_week=iso_week_label(day), source=source)
        print(f"  ⚠️  {ENV_VAR if source == 'env' else 'A/B 假設'}「{key}」不存在，改用每日標題＋封面字測試")
    return dict(package_hypothesis(fewshot_enabled(env)), iso_week=iso_week_label(day), source="daily")


def hypothesis_for_style(hypothesis, base_style, plan=None) -> dict | None:
    """
    H1 compares the day's comic scene (arm A) with the war-room data board (arm B).
    When the day's theme already is the war room (「財經戰情室」 fallback or preset),
    both covers would be war-room pictures and the test day would be wasted, so the
    next enabled hypothesis with another variable is tested instead (plan order,
    deterministic). The covers declare the substitute, so record-ab-result credits it.
    """
    if not hypothesis or hypothesis.get("variable") != "style" or base_style not in WAR_ROOM_STYLES:
        return hypothesis
    plan = plan if plan is not None else load_plan()
    # weekly rotation retired (all disabled): substitute from the whole plan order
    enabled = enabled_hypotheses(plan) or [
        h for h in plan.get("hypotheses") or [] if isinstance(h, dict) and h.get("id") and h.get("variable")
    ]
    ids = [h["id"] for h in enabled]
    start = ids.index(hypothesis.get("id")) + 1 if hypothesis.get("id") in ids else 0
    for candidate in enabled[start:] + enabled[:start]:
        if candidate.get("variable") != "style":
            return dict(
                candidate,
                iso_week=hypothesis.get("iso_week") or "",
                source="substitute",
                replaces=hypothesis.get("id"),
            )
    return hypothesis


def arm(hypothesis, letter) -> dict:
    return ((hypothesis or {}).get("arms") or {}).get(str(letter).lower()) or {}


def levers_for(hypothesis, letter) -> dict:
    """Lever values for one cover: the defaults, with only the tested variable set by the arm."""
    levers = dict(LEVER_DEFAULTS)
    if hypothesis and hypothesis.get("variable") in levers:
        mode = arm(hypothesis, letter).get("mode")
        if mode:
            levers[hypothesis["variable"]] = mode
    return levers


def hypothesis_title(hypothesis) -> str:
    if not hypothesis:
        return "未宣告"
    return f"{hypothesis.get('id')} {hypothesis.get('label') or hypothesis.get('variable')}"


def variable_line(hypothesis, letter) -> str:
    """「style｜H1 畫風｜本張 A：漫畫場景（當日視覺主題）｜對照 B：戰情室數據大字牌」."""
    if not hypothesis:
        return "未宣告（data/ab_test_plan.json 沒有啟用中的假設）"
    letter = str(letter).lower()
    other = "b" if letter == "a" else "a"
    return (
        f"{hypothesis.get('variable')}｜{hypothesis_title(hypothesis)}｜"
        f"本張 {letter.upper()}：{arm(hypothesis, letter).get('label', '')}｜"
        f"對照 {other.upper()}：{arm(hypothesis, other).get('label', '')}"
    )


_VARIABLE_LINE_RE = re.compile(r"^- A/B variable:\s*([A-Za-z_]+)｜(H\d+|FS|PK)[^｜]*｜本張\s*([AB])", re.MULTILINE)


def parse_variable_line(content) -> dict | None:
    """{"variable", "hypothesis_id", "arm"} from a thumbnail prompt's ``- A/B variable:`` line."""
    match = _VARIABLE_LINE_RE.search(str(content or ""))
    if not match:
        return None
    return {"variable": match.group(1), "hypothesis_id": match.group(2), "arm": match.group(3).lower()}


def constants_line(hero_label="", number="", title_label="標題 A", variable="") -> str:
    """What both covers share. Under H1 (style) the scene itself is the variable, so only the composition is shared."""
    bits = []
    if hero_label:
        bits.append(f"主角 {hero_label}")
    if number:
        bits.append(f"數字 {number}")
    bits.append(f"配對{title_label}")
    bits.append("同構圖" if variable == "style" else "同一場景與構圖")
    return "、".join(bits)


# ---------------------------------------------------------------------------
# YouTube package guidance
# ---------------------------------------------------------------------------


def _package_test_section(
    hypothesis, date_str, title_a="", title_b="", cover_a="", cover_b="", hero_label="", number=""
) -> str:
    def cover(text):
        return " / ".join(x for x in str(text or "").splitlines() if x.strip()) or "（見封面 Prompt）"

    fs = hypothesis.get("id") == "FS"
    b_name = "few-shot" if fs else "標題 B"
    return "\n".join(
        [
            BLOCK_START,
            "## 🧪 今日 A/B 測試（YouTube Studio「測試與比較」：標題＋封面）",
            "",
            f"- 測試：{hypothesis_title(hypothesis)}｜A {arm(hypothesis, 'a').get('label', '')} vs B {arm(hypothesis, 'b').get('label', '')}",
            f"- A（原方法）：標題 `{title_a or '見標題 A'}`＋封面 A（大字 {cover(cover_a)}）",
            f"- B（{b_name}）：標題 `{title_b or '見標題 B'}`＋封面 B（大字 {cover(cover_b)}）",
            f"- 兩組同一視覺主題與構圖；封面 A {constants_line(hero_label, number)}（B 依 B 標題自己的主角與大字）",
            "",
            "1. 在 YouTube Studio「測試與比較」建立「標題＋縮圖」測試：A 組 = 標題 A＋封面 A，B 組 = 標題 B＋封面 B（兩張封面都要生圖，B 封面用 B 的大字）。",
            "2. 不要先上 A、過幾天再手動換 B（頻道觀看數會混入題材與影片天數的差異）。",
            "3. YouTube 回報勝出（或沒有明顯差異）後記錄（預設就是今日這組測試）：",
            f"   `python3 cli.py youtube record-ab-result --date {date_str} --winner a|b|none "
            "--value-a <A 觀看時間占比> --value-b <B 觀看時間占比>`",
            "4. 累積 few-shot vs 原方法：`python3 scripts/youtube/fewshot_ab_summary.py`（≥3 次有勝負且過半才考慮改預設；不產生禁令）。",
            BLOCK_END,
        ]
    )


def package_section(hypothesis, date_str, hero_label="", number="", **arms) -> str:
    if is_package_test(hypothesis):
        return _package_test_section(hypothesis, date_str, hero_label=hero_label, number=number, **arms)
    plan_rule = (
        f"同一假設累積 ≥{MIN_DECIDED_TESTS} 次有勝負、且多數同一邊才建議升級為預設（手動調整 LEVER_DEFAULTS）；"
        "A/B 結果不產生任何禁令。"
    )
    if hypothesis:
        week = hypothesis.get("iso_week") or iso_week_label(date_str)
        if hypothesis.get("replaces"):
            week += f"；今日主題就是財經戰情室，{hypothesis['replaces']} 兩組會幾乎一樣，改測此假設"
        head = (
            f"- 手動指定假設：{hypothesis_title(hypothesis)}（{week}）｜"
            f"A {arm(hypothesis, 'a').get('label', '')} vs B {arm(hypothesis, 'b').get('label', '')}"
        )
        only = (
            f"- 唯一變數：{hypothesis.get('label') or hypothesis.get('variable')}；"
            f"封面 A/B {constants_line(hero_label, number, variable=hypothesis.get('variable', ''))}"
        )
    else:
        head = "- 假設：未宣告"
        only = f"- 封面 A/B {constants_line(hero_label, number)}"
    return "\n".join(
        [
            BLOCK_START,
            "## 🧪 封面 A/B 測試（YouTube Studio「測試與比較」）",
            "",
            head,
            only,
            "",
            "1. 上架用標題 A（AB_HYPOTHESIS 手動指定的單一變數封面測試）。",
            "2. 在 YouTube Studio「測試與比較」（Test & compare）一次上傳封面 A 與封面 B，讓 YouTube 分流測試；"
            "不要先上 A、過幾天再手動換 B（頻道觀看數會混入題材與影片天數的差異）。",
            "3. YouTube 回報勝出（或顯示沒有明顯差異）後記錄結果：",
            f"   `python3 cli.py youtube record-ab-result --date {date_str} --winner a|b|none "
            "--value-a <A 觀看時間占比> --value-b <B 觀看時間占比>`",
            f"4. {plan_rule}",
            BLOCK_END,
        ]
    )


_OLD_GUIDANCE_LINES = (
    re.compile(r"^5\. 觀看數未達頻道中位數×2[^\n]*\n?", re.MULTILINE),
    re.compile(r"^6\. 48小時觀看低於頻道中位數[^\n]*\n?", re.MULTILINE),
)


def refresh_package_ab_guidance(package_text, section) -> str:
    """
    Idempotent: title heading / labels, upload checklist and the 48-hour table use
    the single-variable cover test; the marker block is replaced (or inserted after
    the upload checklist).
    """
    text = str(package_text or "")
    text = re.sub(r"^## 📝 A/B 標題選項（[^\n]*", TITLE_HEADING, text, count=1, flags=re.MULTILINE)
    text = re.sub(r"\*\*標題 A（[^*\n]*）：\*\*", f"**{TITLE_A_LABEL}：**", text, count=1)
    text = re.sub(r"\*\*標題 B（[^*\n]*）：\*\*", f"**{TITLE_B_LABEL}：**", text, count=1)
    text = text.replace("1. 先用標題 A 上傳", UPLOAD_STEP)
    text = text.replace("1. 用標題 A 上傳（封面 A/B 照下方「封面 A/B 測試」同時上傳）", UPLOAD_STEP)
    for pattern in _OLD_GUIDANCE_LINES:
        text = pattern.sub("", text)
    text = re.sub(r"^\| 是否更換 B 組 \|[^\n]*", CHECKLIST_ROW, text, flags=re.MULTILINE)
    text = re.sub(
        r"不判斷 A/B 勝負，只記錄觀察。",
        "不判斷標題好壞，只記錄觀察；封面 A/B 以 Studio「測試與比較」結果為準。",
        text,
    )
    block_re = re.compile(re.escape(BLOCK_START) + r"[\s\S]*?" + re.escape(BLOCK_END))
    if block_re.search(text):
        return block_re.sub(lambda _m: section, text, count=1)
    for anchor in ("\n---\n\n## 🎬 今日影片製作計畫", "\n### A/B 完整封面 Prompt", "\n## 48 小時驗收欄位"):
        index = text.find(anchor)
        if index >= 0:
            return text[:index].rstrip() + "\n\n" + section + "\n" + text[index:]
    return text.rstrip() + "\n\n" + section + "\n"


# ---------------------------------------------------------------------------
# results / learning
# ---------------------------------------------------------------------------


def load_results(db_path=None) -> list[dict]:
    """ab_test_results rows (read-only; [] when the DB or table does not exist yet)."""
    path = db_path or DEFAULT_DB_PATH
    if not os.path.exists(path):
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM ab_test_results ORDER BY run_date").fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []


def summarize_results(rows, plan=None, min_decided=None) -> list[dict]:
    """
    Per hypothesis: n, decided (winner a/b), wins per arm, no-difference count and
    the promoted arm — only after ``min_decided`` decided tests with a strict
    majority. Hypotheses of the plan without results are listed with n=0.
    """
    plan = plan if plan is not None else load_plan()
    if min_decided is None:
        min_decided = int(((plan or {}).get("decision") or {}).get("min_decided_tests") or MIN_DECIDED_TESTS)
    order = [h["id"] for h in (plan or {}).get("hypotheses") or [] if isinstance(h, dict) and h.get("id")]
    groups: dict[str, list[dict]] = {}
    for row in rows or []:
        hid = str(row.get("hypothesis_id") or "").strip() or "未宣告"
        groups.setdefault(hid, []).append(row)
    summary = []
    for hid in order + sorted(k for k in groups if k not in order):
        items = groups.get(hid, [])
        hypothesis = find_hypothesis(plan, hid) or {}
        wins_a = sum(1 for r in items if str(r.get("winner")).lower() == "a")
        wins_b = sum(1 for r in items if str(r.get("winner")).lower() == "b")
        decided = wins_a + wins_b
        promoted = None
        if decided >= min_decided:
            if wins_a * 2 > decided:
                promoted = "a"
            elif wins_b * 2 > decided:
                promoted = "b"
        last = items[-1] if items else {}
        summary.append(
            {
                "hypothesis_id": hid,
                "variable": hypothesis.get("variable") or last.get("variable") or "",
                "label": hypothesis.get("label") or last.get("variable") or "",
                "arm_a": arm(hypothesis, "a").get("label") or last.get("arm_a") or "A",
                "arm_b": arm(hypothesis, "b").get("label") or last.get("arm_b") or "B",
                "n": len(items),
                "decided": decided,
                "wins_a": wins_a,
                "wins_b": wins_b,
                "no_difference": len(items) - decided,
                "promoted": promoted,
                "min_decided": min_decided,
            }
        )
    return summary


def verdict(row) -> str:
    if not row["n"]:
        return "尚無紀錄"
    if row["promoted"]:
        winner = row["arm_a"] if row["promoted"] == "a" else row["arm_b"]
        return f"✅ 可升級為預設（需手動調整 cover_ab_test.LEVER_DEFAULTS）：{row['promoted'].upper()} {winner}"
    if row["decided"] < row["min_decided"]:
        return f"樣本不足（有勝負 {row['decided']}/{row['min_decided']} 次），先不下結論"
    return "沒有多數（平手），繼續測或換下一個假設"


def format_learning_lines(summary) -> list[str]:
    lines = []
    for row in summary or []:
        lines.append(
            f"- {row['hypothesis_id']} {row['label']}（A {row['arm_a']} vs B {row['arm_b']}）："
            f"n={row['n']}，有勝負 {row['decided']}（A {row['wins_a']}／B {row['wins_b']}，無明顯差異 {row['no_difference']}）"
            f"→ {verdict(row)}"
        )
    if not lines:
        lines.append("- （data/ab_test_plan.json 沒有假設，也還沒有紀錄）")
    lines.append(
        f"- 規則：只看 YouTube「測試與比較」的結果；同一假設 ≥{MIN_DECIDED_TESTS} 次有勝負且過半同一邊才建議升級預設（手動改，不會自動套用）；"
        "A/B 結果只用來升級預設，不產生任何禁令（禁令只來自 /2 主題的「禁止…」子句）。"
    )
    return lines


def learning_section(db_path=None, plan=None) -> list[str]:
    try:
        rows = load_results(db_path)
    except Exception:
        rows = []
    return format_learning_lines(summarize_results(rows, plan=plan))
