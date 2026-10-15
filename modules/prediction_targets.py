"""
Local filter for Gemini-extracted ``prediction_targets`` (finding 18/79).

The extractor is asked to read 〔明日觀察焦點〕, but nothing enforced it: Gemini
merged stocks from 【今日深度押注】 (including the banned mega-caps 2330 /
2317 / 2454), returned three targets, and the next day those rows were
published as 「上集驗收」 and written to the prediction ledger.

Rules (analyzer STEP 5):
1. keep only stocks named in the report's 〔…觀察焦點〕 section
   (〔明日觀察焦點〕 / 〔下週一觀察焦點〕 …), matched by ticker or exact name
   (南亞 ≠ 南亞科); the section saying 「無安全明牌」 means no targets unless it
   still carries the per-stock 為何點名／失效條件 structure (a number such as
   「23000 點」 or a stock named as 「已過熱不追」 is not a pick);
2. drop ``EXCLUDED_WATCH_TICKERS`` (超級權值股);
3. at most 2, in the order the section lists them.

Every dropped row is logged and recorded in ``prediction_targets_filter``.
"""

from __future__ import annotations

import re

from modules.tickers import bare_ticker

MAX_PREDICTION_TARGETS = 2

_SECTION_HEAD = re.compile(r"^(#{1,4})\s*〔[^〕\n]*觀察焦點[^〕\n]*〕.*$", re.MULTILINE)
# 「23000 點」「2026年」「2490元」「3160億」 are numbers, not tickers. A unit after a
# space is only 點／%: 「3711 日月光」「2885 元大金」「2393 億光」 are tickers + names.
_TICKER = re.compile(
    r"(?<![\d.])(\d{4,6}[A-Z]?)(?![\d.%％])(?!\s*(?:點|%|％))(?!(?:元|億|萬|張|年|月|日|倍|股|名|天|次|週|檔))"
)
_NO_PICK = re.compile(r"無安全明牌|現金為王|今日無.{0,4}(推薦|觀察)標的")
# The analyzer's per-pick structure (STEP 5): 為何點名／隔日節奏／趨勢成立／失效條件.
_PICK_STRUCTURE = re.compile(r"為何點名|失效條件")


def excluded_tickers():
    from modules.market_signal_scorer import EXCLUDED_WATCH_TICKERS

    return set(EXCLUDED_WATCH_TICKERS)


def focus_sections(report_content: str) -> list[str]:
    """Body text of every 〔…觀察焦點…〕 section (heading included)."""
    text = str(report_content or "")
    out = []
    for head in _SECTION_HEAD.finditer(text):
        start = head.start()
        rest = text[head.end():]
        # Ends at the next heading of the same or a higher level, or a rule line.
        level = len(head.group(1))
        nxt = re.search(rf"^(?:#{{1,{level}}}\s|---\s*$)", rest, flags=re.MULTILINE)
        end = head.end() + (nxt.start() if nxt else len(rest))
        out.append(text[start:end])
    return out


def _norm_name(name) -> str:
    text = re.sub(r"\s+", "", str(name or ""))
    text = re.sub(r"[-－]?(KY|ＫＹ)$", "", text, flags=re.IGNORECASE)
    return text.strip("*＊")


def _name_position(section: str, name: str, longer: list[str]) -> int:
    """First index of ``name`` that is not the start of a longer known name."""
    if not name:
        return -1
    for m in re.finditer(re.escape(name), section):
        tail = section[m.start():]
        if any(tail.startswith(other) for other in longer):
            continue
        return m.start()
    return -1


def known_stock_names(db_path=None) -> list[str]:
    """Normalized stock_db names (to tell 南亞 from 南亞科 when no ticker is written)."""
    import json
    import os

    path = db_path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "stock_db.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            db = json.load(f)
    except (OSError, ValueError):
        return []
    return [_norm_name(entry.get("name")) for entry in db.values() if isinstance(entry, dict) and entry.get("name")]


def filter_prediction_targets(
    targets, report_content, *, excluded=None, max_targets=MAX_PREDICTION_TARGETS, known_names=None
):
    """
    Returns (kept_targets, audit) where audit = {"section_found", "kept", "dropped": [{id, name, reason}]}.
    """
    excluded = excluded_tickers() if excluded is None else set(excluded)
    rows = [t for t in (targets or []) if isinstance(t, dict)]
    sections = focus_sections(report_content)
    section = "\n".join(sections)
    audit = {"section_found": bool(sections), "kept": [], "dropped": []}

    names = [_norm_name(t.get("name")) for t in rows] + [n for n in (known_names or []) if n]
    ticker_pos = {}
    for m in _TICKER.finditer(section):
        ticker_pos.setdefault(m.group(1), m.start())
    # 「無安全明牌」 clears the targets unless the section still lists a real pick.
    no_pick = bool(sections) and bool(_NO_PICK.search(section)) and not _PICK_STRUCTURE.search(section)

    ranked = []
    for row in rows:
        ticker = bare_ticker(row.get("id") or row.get("ticker"))
        name = _norm_name(row.get("name"))
        label = f"{ticker} {row.get('name') or ''}".strip()
        if ticker and ticker in excluded:
            audit["dropped"].append({"id": ticker, "name": row.get("name"), "reason": "超級權值股不列正式追蹤（EXCLUDED_WATCH_TICKERS）"})
            continue
        if sections:
            if no_pick:
                audit["dropped"].append({"id": ticker, "name": row.get("name"), "reason": "觀察焦點段寫「無安全明牌」"})
                continue
            longer = [n for n in names if n and n != name and name and n.startswith(name)]
            pos = ticker_pos.get(ticker, -1) if ticker else -1
            if pos < 0:
                pos = _name_position(section, name, longer)
            if pos < 0:
                audit["dropped"].append({"id": ticker, "name": row.get("name"), "reason": "不在〔觀察焦點〕段（例如只出現在深度押注）"})
                continue
        else:
            pos = len(ranked)
        ranked.append((pos, len(ranked), row, label))

    ranked.sort(key=lambda item: (item[0], item[1]))
    kept = []
    for _pos, _idx, row, label in ranked:
        if len(kept) >= max_targets:
            audit["dropped"].append(
                {"id": bare_ticker(row.get("id")), "name": row.get("name"), "reason": f"超過上限 {max_targets} 檔"}
            )
            continue
        kept.append(row)
        audit["kept"].append(label)
    return kept, audit


def apply_prediction_target_filter(structured_data, report_content):
    """In-place filter on a successful extraction; prints every override."""
    if not isinstance(structured_data, dict) or structured_data.get("error"):
        return structured_data
    original = structured_data.get("prediction_targets") or []
    kept, audit = filter_prediction_targets(original, report_content, known_names=known_stock_names())
    structured_data["prediction_targets"] = kept
    structured_data["prediction_targets_filter"] = audit
    if not audit["section_found"]:
        print("  ⚠️  [正式追蹤] 日報找不到〔觀察焦點〕段，只套用權值股排除與上限 2 檔")
    for row in audit["dropped"]:
        print(f"  ✂️  [正式追蹤] 移除 {row.get('id') or ''} {row.get('name') or ''}：{row['reason']}")
    if audit["dropped"]:
        print(f"  ✅ [正式追蹤] 保留：{'、'.join(audit['kept']) or '無'}")
    return structured_data
