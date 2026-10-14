"""
Local broadcast ledger between structured_data and NotebookLM.

Source quotes these numbers. The director prompt only says *how* to speak them.
No Gemini. Internal scores (P/E/F/V) stay off-air.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(BASE_DIR, "data", "stock_db.json")


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _clean(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def parse_amount_yi(text) -> float | None:
    blob = str(text or "")
    m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*億", blob)
    if not m:
        return None
    try:
        amount = float(m.group(1))
    except ValueError:
        return None
    if amount > 0:
        head = blob[max(0, m.start() - 16):m.start()]
        if re.search(r"(大砍|賣超|提款|出貨|拋售|狂砍|遭提款)", head) and not re.search(
            r"(買超|狂掃|掃貨|大買)", head
        ):
            amount = -amount
    return amount


def format_amount_yi(amount: float | None) -> str:
    if amount is None:
        return ""
    return f"{amount:+.1f}億"


def parse_pct(text) -> float | None:
    blob = str(text or "")
    m = re.search(r"(跌|摔|漲)?\s*([+-]?)(\d+(?:\.\d+)?)\s*%", blob)
    if not m:
        return None
    try:
        amount = float(m.group(3))
    except ValueError:
        return None
    if m.group(2) == "-" or m.group(1) in {"跌", "摔"}:
        return -abs(amount)
    if m.group(2) == "+" or m.group(1) == "漲":
        return abs(amount)
    return amount


def format_pct(pct: float | None) -> str:
    if pct is None:
        return ""
    return f"{pct:+.2f}%"


def parse_volume_rank(text) -> int | None:
    m = re.search(r"(?:成交量)?(?:排名)?第\s*(\d+)\s*名", str(text or ""))
    if m:
        return int(m.group(1))
    return None


def is_off_rank(text: str) -> bool:
    blob = str(text or "")
    return bool(re.search(r"(未進|沒進|未進前|沒進前).{0,6}(20|量榜|榜)", blob))


def is_one_day_flip(text: str) -> bool:
    blob = str(text or "")
    return bool(re.search(r"昨.{0,8}(買|賣).{0,12}今.{0,8}(買|賣|砍|出)", blob))


def parse_ticker_name(text: str) -> tuple[str, str]:
    text = _clean(text)
    m = re.match(r"^(\d{4,6}[A-Za-z]?)\s*([^\s(（+−\-]*)", text)
    if m:
        return m.group(1), (m.group(2) or "").strip()
    name = re.sub(r"\s*[\(（].*?[\)）]\s*", "", text).strip()
    return "", name


def _load_stock_db(path: str | None = None) -> dict:
    db_path = path or DEFAULT_DB
    try:
        with open(db_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _tracking(stock_db: dict, ticker: str) -> dict:
    row = stock_db.get(str(ticker or "").strip()) or {}
    tracking = row.get("tracking") or {}
    return tracking if isinstance(tracking, dict) else {}


def _lookup_invalidation(structured_data: dict, ticker: str, name: str) -> str:
    for target in _as_list(structured_data.get("prediction_targets")):
        if not isinstance(target, dict):
            continue
        tid = str(target.get("id") or "").strip()
        tname = str(target.get("name") or "").strip()
        if (ticker and tid == ticker) or (name and tname and name in tname):
            return _clean(target.get("stop_loss_desc") or target.get("stop_loss_price") or "")
    for pick in _as_list(structured_data.get("short_term_picks")):
        if not isinstance(pick, dict):
            continue
        tid = str(pick.get("ticker") or pick.get("id") or "").strip()
        tname = str(pick.get("name") or "").strip()
        if (ticker and tid == ticker) or (name and tname and name in tname):
            return _clean(pick.get("invalidation") or "")
    return ""


def _official_tickers(structured_data: dict) -> set[str]:
    out = set()
    for target in _as_list(structured_data.get("prediction_targets")):
        if isinstance(target, dict):
            tid = str(target.get("id") or "").strip()
            if tid:
                out.add(tid)
    return out


def _classify_admission(
    *,
    ticker: str,
    evidence: str,
    volume_confirmation: int | None,
    official_ids: set[str],
    streak_days: int | None,
) -> tuple[str, str, bool]:
    """
    Presentation gate, not a rewrite of prediction_targets.

    official  = 可當「下一交易日盯這檔」
    talk_only = 只能當矛盾／背景，不能講成已確認趨勢
    caution   = 正式追蹤但仍要講單日風險
    """
    off_rank = is_off_rank(evidence)
    flip = is_one_day_flip(evidence)
    thin_volume = volume_confirmation is not None and volume_confirmation < 50
    weak_streak = streak_days is not None and streak_days < 3
    in_book = ticker in official_ids

    if off_rank and thin_volume and (weak_streak or streak_days in (None, 0)):
        return "talk_only", "單日榜外且量能／連續買超未確認，只可當矛盾不可當正式追蹤", True
    if in_book and (off_rank or flip):
        return "official", "已進預測帳本，但含單日矛盾，口播必須標成節奏而非趨勢", True
    if in_book:
        return "official", "已進預測帳本", False
    if off_rank or flip:
        return "talk_only", "單日矛盾／翻臉，未進正式預測", True
    return "context", "對照股，不是下一交易日主盯", False


def _hero_seed_rows(structured_data: dict, limit: int = 3) -> list[dict[str, Any]]:
    ranking = structured_data.get("focus_ranking") or []
    if ranking:
        return [row for row in ranking[:limit] if isinstance(row, dict)]

    seeds = []
    seen = set()
    for raw in (
        _as_list(structured_data.get("safe_havens_bought"))[:2]
        + _as_list(structured_data.get("price_volume_divergence"))[:2]
        + _as_list(structured_data.get("heavyweights_dumped"))[:1]
    ):
        text = _clean(raw)
        ticker, name = parse_ticker_name(text)
        key = ticker or name or text
        if not key or key in seen:
            continue
        seen.add(key)
        seeds.append({"ticker": ticker, "name": name, "source_text": text})
        if len(seeds) >= limit:
            break
    return seeds


def build_hero_cards(structured_data: dict, stock_db: dict | None = None, limit: int = 3) -> list[dict[str, Any]]:
    stock_db = stock_db if stock_db is not None else _load_stock_db()
    official_ids = _official_tickers(structured_data)
    cards = []
    for row in _hero_seed_rows(structured_data, limit=limit):
        ticker = str(row.get("ticker") or "").strip()
        name = str(row.get("name") or "").strip()
        evidence = _clean(row.get("source_text") or "")
        extra_bits = []
        for div in _as_list(structured_data.get("price_volume_divergence")):
            blob = _clean(div)
            if not blob:
                continue
            if (ticker and ticker in blob) or (name and name in blob):
                extra_bits.append(blob)
        if extra_bits:
            evidence = "；".join([evidence] + extra_bits) if evidence else "；".join(extra_bits)
        amount = parse_amount_yi(evidence)
        in_sell_list = False
        for dumped in _as_list(structured_data.get("heavyweights_dumped")):
            blob = _clean(dumped)
            if not blob:
                continue
            if (ticker and ticker in blob) or (name and name in blob):
                in_sell_list = True
                if amount is None:
                    amount = parse_amount_yi(blob)
        pct = parse_pct(evidence)
        vol_rank = parse_volume_rank(evidence)
        tracking = _tracking(stock_db, ticker)
        streak = tracking.get("consecutive_foreign_buy_days")
        try:
            streak_n = int(streak) if streak not in (None, "") else None
        except (TypeError, ValueError):
            streak_n = None
        # A sell day cannot keep a leftover buy streak from an older prediction row.
        if in_sell_list or (amount is not None and amount < 0):
            streak_n = 0
        vol_confirm = row.get("volume_confirmation")
        try:
            vol_confirm_n = int(vol_confirm) if vol_confirm not in (None, "") else None
        except (TypeError, ValueError):
            vol_confirm_n = None
        admission, reason, caution = _classify_admission(
            ticker=ticker,
            evidence=evidence,
            volume_confirmation=vol_confirm_n,
            official_ids=official_ids,
            streak_days=streak_n,
        )
        say_bits = [f"{ticker} {name}".strip() or "焦點股"]
        if amount is not None:
            say_bits.append(f"外資約 {format_amount_yi(amount)}")
        if pct is not None:
            say_bits.append(f"漲跌 {format_pct(pct)}")
        if vol_rank:
            say_bits.append(f"成交量第 {vol_rank} 名")
        elif is_off_rank(evidence):
            say_bits.append("成交量未進前 20")
        cards.append(
            {
                "ticker": ticker,
                "name": name,
                "role_label": row.get("role_label") or row.get("base_role") or "",
                "foreign_amount": amount,
                "pct_change": pct,
                "volume_rank": vol_rank,
                "off_rank": is_off_rank(evidence),
                "one_day_flip": is_one_day_flip(evidence),
                "sold_today": in_sell_list or (amount is not None and amount < 0),
                "volume_confirmation": vol_confirm_n,
                "foreign_streak_days": streak_n,
                "admission": admission,
                "admission_reason": reason,
                "caution": caution,
                "invalidation": _lookup_invalidation(structured_data, ticker, name),
                "evidence": evidence,
                "say_line": "，".join(say_bits),
            }
        )
    return cards


def _contrast_line(structured_data: dict, heroes: list[dict]) -> str:
    buy = _clean((_as_list(structured_data.get("safe_havens_bought")) or [""])[0])
    sell = _clean((_as_list(structured_data.get("heavyweights_dumped")) or [""])[0])
    buy_amt = parse_amount_yi(buy)
    sell_amt = parse_amount_yi(sell)
    buy_name = parse_ticker_name(buy)[1] or buy
    sell_name = parse_ticker_name(sell)[1] or sell
    if buy and sell:
        left = f"{buy_name} {format_amount_yi(buy_amt)}".strip() if buy_amt is not None else buy_name
        right = f"{sell_name} {format_amount_yi(sell_amt)}".strip() if sell_amt is not None else sell_name
        return f"{left} vs {right}"
    if heroes:
        return heroes[0].get("say_line") or ""
    return ""


def build_opening(structured_data: dict, heroes: list[dict]) -> dict[str, str]:
    contrast = _contrast_line(structured_data, heroes)
    divs = [_clean(x) for x in _as_list(structured_data.get("price_volume_divergence")) if _clean(x)]
    hero_says = [h.get("say_line") for h in heroes[:2] if h.get("say_line")]
    same_side = False
    if len(heroes) >= 2:
        a, b = heroes[0].get("foreign_amount"), heroes[1].get("foreign_amount")
        if a is not None and b is not None and a * b > 0:
            same_side = True
    if contrast and (same_side or len(hero_says) < 2):
        hook = contrast
        extra = []
        for h in heroes[:2]:
            if h.get("off_rank"):
                extra.append(f"{h.get('name') or h.get('ticker')}成交量未進前20")
            elif h.get("volume_rank"):
                extra.append(f"{h.get('name') or h.get('ticker')}量第{h.get('volume_rank')}")
        if extra:
            hook = f"{hook}；" + "，".join(extra)
    elif len(hero_says) >= 2:
        hook = f"{hero_says[0]}；對照 {hero_says[1]}"
    elif divs:
        hook = divs[0]
        if len(divs) > 1 and parse_amount_yi(divs[1]) is not None:
            hook = f"{divs[0]}；對照 {divs[1]}"
    else:
        hook = contrast or _clean(structured_data.get("index_action"))
    return {
        "hook_line": hook,
        "contrast_line": _contrast_line(structured_data, heroes),
        "index_action": _clean(structured_data.get("index_action")),
    }


def _ruler_line(structured_data: dict) -> str:
    reliability = structured_data.get("horizon_reliability") or {}
    line = reliability.get("one_liner") or (structured_data.get("timeframe_history") or {}).get("reliability_note") or ""
    if line:
        return _clean(line)
    return "隔日～5D＝節奏參考；約 20 交易日＝趨勢成敗主尺子。"


def build_broadcast_facts(structured_data: dict, stock_db: dict | None = None) -> dict[str, Any]:
    structured_data = structured_data or {}
    heroes = build_hero_cards(structured_data, stock_db=stock_db, limit=3)
    opening = build_opening(structured_data, heroes)
    official = [h for h in heroes if h.get("admission") == "official"]
    talk_only = [h for h in heroes if h.get("admission") == "talk_only"]
    return {
        "opening": opening,
        "heroes": heroes,
        "official_watch": official,
        "talk_only": talk_only,
        "ruler_line": _ruler_line(structured_data),
    }


def attach_broadcast_facts(structured_data: dict, stock_db: dict | None = None) -> dict:
    structured_data = structured_data or {}
    structured_data["broadcast_facts"] = build_broadcast_facts(structured_data, stock_db=stock_db)
    return structured_data


def ensure_broadcast_facts(structured_data: dict, stock_db: dict | None = None) -> dict:
    facts = structured_data.get("broadcast_facts") if isinstance(structured_data, dict) else None
    if isinstance(facts, dict) and facts.get("heroes"):
        return facts
    return build_broadcast_facts(structured_data or {}, stock_db=stock_db)
