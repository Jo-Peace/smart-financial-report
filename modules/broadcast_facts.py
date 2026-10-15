"""
Local broadcast ledger between structured_data and NotebookLM.

Source quotes these numbers. The director prompt only says *how* to speak them.
No Gemini. Internal scores (P/E/F/V) stay off-air.

Number sourcing (each hero card records ``number_source``):
- ``local``: TWSE / yfinance payloads fetched in /1 (``institutional_data``,
  ``volume_data``, ``market_data``), joined by ticker. persist stores a compact
  copy as ``structured_data["local_market_facts"]`` so every rebuild path
  (NotebookLM source / prompt / regen scripts) sees the same numbers.
- ``local_list``: the force-merged local leaderboard strings
  (``safe_havens_bought`` / ``heavyweights_dumped``), used when an older
  structured_data has no ``local_market_facts``.
- ``llm_text``: numbers regex-parsed from Gemini prose. They are kept under
  ``llm_numbers`` for debugging only and never reach ``say_line`` or the card
  fields that NotebookLM quotes.

Official tracking (下一交易日主盯) comes straight from ``prediction_targets``;
the focus ranking only fills the remaining hero slots.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(BASE_DIR, "data", "stock_db.json")

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


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


def _float(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value):
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_ticker(value) -> str:
    return str(value or "").replace(".TWO", "").replace(".TW", "").strip().upper()


def normalize_name(value) -> str:
    """「臻鼎-KY」「臻鼎*」→「臻鼎」 for name joins."""
    name = _clean(value)
    name = re.sub(r"[-－]?(KY|ＫＹ)$", "", name, flags=re.IGNORECASE)
    return name.strip(" *＊")


def cn_numeral_to_int(text) -> int | None:
    """「一」「十二」「二十」→ int (1..99). Arabic digits pass through."""
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    if any(ch not in _CN_DIGITS and ch != "十" for ch in raw):
        return None
    if "十" in raw:
        head, _, tail = raw.partition("十")
        tens = _CN_DIGITS.get(head, 1) if head else 1
        ones = _CN_DIGITS.get(tail, 0) if tail else 0
        return tens * 10 + ones
    if len(raw) == 1:
        return _CN_DIGITS.get(raw)
    return None


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


_VOLUME_RANK_RE = re.compile(
    r"(?:成交量|量能|量)[^，,。；;、\n]{0,12}?第\s*([0-9]+|[一二三四五六七八九十兩]+)\s*(?:名)?"
)


def parse_volume_rank(text) -> int | None:
    """Volume-rank claim such as 「成交量第 4 名」「成交量全台第一」「量居第 12 名」.

    Only matches when the rank is tied to 成交量／量, so 「外資買超第2名」 is not
    mistaken for a volume rank.
    """
    blob = str(text or "")
    m = _VOLUME_RANK_RE.search(blob)
    if m:
        return cn_numeral_to_int(m.group(1))
    if re.search(r"成交量(?:全台|全市場)?冠軍|量能冠軍", blob):
        return 1
    return None


def is_off_rank(text: str) -> bool:
    blob = str(text or "")
    return bool(re.search(r"(未進|沒進|未進前|沒進前).{0,6}(20|量榜|榜)", blob))


# Absence from the share-count top 20 (ETF / low-price dominated) is not a
# volume fact; say it only with this caveat, and prefer the stock's own ratio.
OFF_SHARE_BOARD_LABEL = "未進股數量榜（不代表量縮）"


def own_vol_ratio(structured_data: dict, ticker: str) -> float | None:
    """Today's volume ÷ 5-day mean from role_score_inputs (market_data), if known."""
    market = ((structured_data or {}).get("role_score_inputs") or {}).get("market") or {}
    row = market.get(str(ticker or "").strip()) if isinstance(market, dict) else None
    try:
        value = (row or {}).get("vol_ratio")
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError, AttributeError):
        return None


def volume_say_bit(volume_rank, vol_ratio, off_rank) -> str:
    """
    Speakable volume fact: the share-board rank; for an off-board name its own
    vol ratio when known, else the caveated absence (never 「成交量未進前 20」
    alone, which reads as thin volume).
    """
    if volume_rank:
        return f"成交量第 {volume_rank} 名"
    if off_rank:
        return f"量增比 {vol_ratio:.1f}x" if vol_ratio is not None else OFF_SHARE_BOARD_LABEL
    return ""


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


# ---------------------------------------------------------------------------
# Local market facts (TWSE / yfinance payloads already fetched in /1)
# ---------------------------------------------------------------------------


def build_local_market_facts(
    institutional_data: dict | None = None,
    volume_data: list | None = None,
    market_data: dict | None = None,
) -> dict:
    """
    Compact per-ticker ledger from raw payloads. Never raises; missing inputs
    simply leave fields empty. ``volume_complete`` tells callers whether
    「成交量未進前 N」 can be stated as a fact.

    ``close`` (spoken on hero cards, used for invalidation distance) is ranked:
    same-day TWSE volume rank > market_data quote > the institutional rows'
    ``close_price`` only when its ``price_date`` (stamped by
    ``institutional_ranking.apply_amount_estimates``) equals ``data_date``.
    That price only estimates the amount and may be a stale snapshot, so
    otherwise it is kept as ``est_price`` and ``close`` stays unset.

    ``listing`` = "twse" marks tickers known to be 上市 (TWSE T86 rows and the
    TWSE volume list), "tpex" a ``.TWO`` quote. Only a known 上市 ticker may be
    told 「成交量未進前 N」: the volume list does not cover 上櫃 stocks.
    """
    institutional_data = institutional_data if isinstance(institutional_data, dict) else {}
    volume_rows = [row for row in _as_list(volume_data) if isinstance(row, dict)]
    market_data = market_data if isinstance(market_data, dict) else {}
    data_date = str(institutional_data.get("data_date") or "")[:10]
    # T86 lists 上市 stocks only; the FinMind fallback may mix in 上櫃 rows.
    inst_is_twse = str(institutional_data.get("source") or "twse").strip().lower() == "twse"
    stocks: dict[str, dict] = {}
    inst_close: dict[str, float] = {}

    def _slot(ticker, name=""):
        key = normalize_ticker(ticker)
        if not key:
            return None
        row = stocks.setdefault(key, {"ticker": key, "name": ""})
        if name and not row.get("name"):
            row["name"] = _clean(name)
        return row

    for side, rows in (("buy", institutional_data.get("top_buy")), ("sell", institutional_data.get("top_sell"))):
        for index, item in enumerate(_as_list(rows), start=1):
            if not isinstance(item, dict):
                continue
            row = _slot(item.get("id"), item.get("name"))
            if row is None:
                continue
            amount = _float(item.get("est_amount"))
            if amount is not None and amount != 0:
                row["foreign_amount"] = round(amount, 2)
            for key in ("foreign_net", "trust_net", "total_net"):
                value = _int(item.get(key))
                if value is not None:
                    row[key] = value
            row[f"{side}_rank"] = index
            if inst_is_twse:
                row["listing"] = "twse"
            # Session-based 外資連續買超 (modules.foreign_streak, stamped in /1).
            streak = _int(item.get("foreign_buy_streak"))
            if streak is not None:
                row["foreign_streak_days"] = streak
                row["foreign_streak_complete"] = bool(item.get("foreign_buy_streak_complete", True))
            close = _float(item.get("close_price"))
            if close and "est_price" not in row:
                row["est_price"] = close
                price_date = str(item.get("price_date") or item.get("close_date") or "")[:10]
                if price_date:
                    row["est_price_date"] = price_date
                if data_date and price_date == data_date:
                    inst_close[row["ticker"]] = close

    for index, item in enumerate(volume_rows, start=1):
        row = _slot(item.get("id"), item.get("name"))
        if row is None:
            continue
        row["volume_rank"] = _int(item.get("rank")) or index
        row["listing"] = "twse"  # TWSE MI_INDEX20
        volume = _int(item.get("volume"))
        if volume is not None:
            row["volume"] = volume
        close = _float(item.get("close_price"))
        if close:
            row["close"] = close
            row["close_source"] = "twse_volume_rank"
        pct = _float(item.get("pct_change"))
        # data_fetcher writes 0.0 when it cannot parse the change; let market_data fill it.
        if pct is not None and pct != 0:
            row["pct_change"] = pct
            row["pct_source"] = "twse_volume_rank"

    for symbol, quote in market_data.items():
        if not isinstance(quote, dict):
            continue
        key = normalize_ticker(quote.get("symbol") or symbol)
        if not re.match(r"^\d{4,6}[A-Z]?$", key):
            continue
        row = stocks.setdefault(key, {"ticker": key, "name": ""})
        if str(quote.get("symbol") or symbol).upper().endswith(".TWO"):
            row.setdefault("listing", "tpex")
        price = _float(quote.get("price"))
        if price and not row.get("close"):
            row["close"] = price
            row["close_source"] = "market_data"
        pct = _float(quote.get("pct_change"))
        if pct is not None and row.get("pct_change") is None:
            row["pct_change"] = pct
            row["pct_source"] = "market_data"
        for key_in, key_out in (("ma20", "ma20"), ("ma5", "ma5"), ("vol_ratio", "vol_ratio")):
            value = _float(quote.get(key_in))
            if value is not None:
                row[key_out] = value

    for ticker, close in inst_close.items():
        row = stocks.get(ticker)
        if row is not None and not row.get("close"):
            row["close"] = close
            row["close_source"] = "institutional_price_map"

    return {
        "data_date": institutional_data.get("data_date"),
        "inst_top_n": max(len(_as_list(institutional_data.get("top_buy"))), len(_as_list(institutional_data.get("top_sell")))),
        "volume_top_n": len(volume_rows),
        "volume_complete": len(volume_rows) >= 20,
        "stocks": stocks,
    }


_TICKER_ONLY = re.compile(r"^\d{4,6}[A-Z]?$")


def _local_facts_from(structured_data: dict, local_facts: dict | None) -> dict | None:
    if isinstance(local_facts, dict) and local_facts.get("stocks"):
        return local_facts
    stored = (structured_data or {}).get("local_market_facts")
    if isinstance(stored, dict) and stored.get("stocks"):
        return stored
    return None


def lookup_local_stock(local_facts: dict | None, ticker: str = "", name: str = "") -> dict | None:
    if not isinstance(local_facts, dict):
        return None
    stocks = local_facts.get("stocks") or {}
    key = normalize_ticker(ticker)
    if key and key in stocks:
        return stocks[key]
    wanted = normalize_name(name)
    if wanted:
        for row in stocks.values():
            if normalize_name(row.get("name")) == wanted:
                return row
    return None


def _known_names(structured_data: dict, local_facts: dict | None) -> set[str]:
    names = set()
    for row in ((local_facts or {}).get("stocks") or {}).values():
        if row.get("name"):
            names.add(normalize_name(row["name"]))
    for key in ("safe_havens_bought", "heavyweights_dumped"):
        for raw in _as_list(structured_data.get(key)):
            names.add(normalize_name(parse_ticker_name(_clean(raw))[1]))
    for target in _as_list(structured_data.get("prediction_targets")):
        if isinstance(target, dict) and target.get("name"):
            names.add(normalize_name(target["name"]))
    names.discard("")
    return names


def mentions_stock(blob: str, ticker: str = "", name: str = "", known_names: set[str] | None = None) -> bool:
    """Ticker or name mention that is not part of a longer stock name (南亞 ≠ 南亞科)."""
    blob = str(blob or "")
    ticker = normalize_ticker(ticker)
    if ticker and re.search(rf"(?<!\d){re.escape(ticker)}(?!\d)", blob):
        return True
    short = normalize_name(name)
    if not short:
        return False
    longer = [n for n in (known_names or set()) if n != short and n.startswith(short)]
    for m in re.finditer(re.escape(short), blob):
        tail = blob[m.start():]
        if any(tail.startswith(n) for n in longer):
            continue
        return True
    return False


def _list_amount(structured_data: dict, ticker: str, name: str, known: set[str]) -> tuple[float | None, bool]:
    """Amount from the force-merged local leaderboard strings; (amount, in_sell_list)."""
    amount = None
    in_sell = False
    for key in ("safe_havens_bought", "heavyweights_dumped"):
        for raw in _as_list(structured_data.get(key)):
            blob = _clean(raw)
            if not blob:
                continue
            row_ticker, row_name = parse_ticker_name(blob)
            hit = (ticker and row_ticker == ticker) or (
                name and normalize_name(row_name) == normalize_name(name)
            ) or mentions_stock(blob, ticker, name, known)
            if not hit:
                continue
            if key == "heavyweights_dumped":
                in_sell = True
            if amount is None:
                amount = parse_amount_yi(blob)
    return amount, in_sell


# ---------------------------------------------------------------------------
# Admission / invalidation
# ---------------------------------------------------------------------------


def _format_price(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _target_for(structured_data: dict, ticker: str, name: str) -> dict | None:
    for target in _as_list(structured_data.get("prediction_targets")):
        if not isinstance(target, dict):
            continue
        tid = normalize_ticker(target.get("id"))
        tname = normalize_name(target.get("name"))
        if (ticker and tid == ticker) or (name and tname and normalize_name(name) == tname):
            return target
    return None


def is_short_direction(direction) -> bool:
    """Short call (空 / 偏空 …) under the one direction rule, prediction_validator.direction_sign."""
    from modules.prediction_validator import direction_sign

    return direction_sign(direction) < 0


def stop_breached(direction, stop: float | None, close: float | None) -> bool:
    """多: close at/below the stop; 空: close at/above the stop (the stop sits above a short)."""
    from modules.prediction_validator import direction_sign

    if not stop or not close or not str(direction or "").strip():
        return False  # unknown direction (caller passed none): never claim a breach
    sign = direction_sign(direction)
    if sign < 0:
        return close >= stop
    if sign > 0:
        return close <= stop
    return False  # non-directional (觀察 / 多空交錯): no stop side


def format_invalidation(desc, price, close: float | None = None, direction: str = "") -> str:
    """「描述（價格；距今收盤 ±x%）」. The price is never dropped when a desc exists."""
    desc = _clean(desc)
    price_f = _float(price)
    bits = []
    if price_f:
        shown = _format_price(price_f)
        if shown not in desc.replace(",", ""):
            bits.append(f"{shown} 元")
        if close:
            bits.append(f"距今收盤 {(price_f / close - 1) * 100:+.1f}%")
            if stop_breached(direction, price_f, close):
                bits.append("今日收盤已觸及失效價，假說待重新確認")
    if not desc and price_f:
        verb = "收盤站上" if is_short_direction(direction) else "收盤跌破"
        desc = f"{verb} {_format_price(price_f)} 元"
        bits = [b for b in bits if not b.endswith(" 元")]
    if bits:
        return f"{desc}（{'；'.join(bits)}）" if desc else "；".join(bits)
    return desc


def _lookup_invalidation(structured_data: dict, ticker: str, name: str, close: float | None = None) -> str:
    target = _target_for(structured_data, ticker, name)
    if target is not None:
        text = format_invalidation(
            target.get("stop_loss_desc"), target.get("stop_loss_price"), close, target.get("direction") or ""
        )
        if text:
            return text
    for pick in _as_list(structured_data.get("short_term_picks")):
        if not isinstance(pick, dict):
            continue
        tid = normalize_ticker(pick.get("ticker") or pick.get("id"))
        tname = normalize_name(pick.get("name"))
        if (ticker and tid == ticker) or (name and tname and normalize_name(name) == tname):
            return _clean(pick.get("invalidation") or "")
    return ""


def _official_tickers(structured_data: dict) -> set[str]:
    out = set()
    for target in _as_list(structured_data.get("prediction_targets")):
        if isinstance(target, dict):
            tid = normalize_ticker(target.get("id"))
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
    off_rank: bool | None = None,
) -> tuple[str, str, bool]:
    """
    Presentation gate, not a rewrite of prediction_targets.

    official  = 已進預測帳本 → 一律「下一交易日主盯」；單日矛盾只加 caution
    talk_only = 只能當矛盾／背景，不能講成已確認趨勢
    context   = 對照股
    """
    off_rank = is_off_rank(evidence) if off_rank is None else bool(off_rank)
    flip = is_one_day_flip(evidence)
    thin_volume = volume_confirmation is not None and volume_confirmation < 50
    weak_streak = streak_days is not None and streak_days < 3
    in_book = ticker in official_ids

    # The prediction ledger wins: a stock in prediction_targets is never demoted,
    # otherwise the director card, description and next-day validation disagree.
    if in_book and (off_rank or flip):
        return "official", "已進預測帳本，但含單日矛盾，口播必須標成節奏而非趨勢", True
    if in_book and thin_volume and (weak_streak or streak_days in (None, 0)):
        return "official", "已進預測帳本，但量能／連續買超未確認，口播標成節奏而非趨勢", True
    if in_book:
        return "official", "已進預測帳本", False
    if off_rank and thin_volume and (weak_streak or streak_days in (None, 0)):
        return "talk_only", "單日榜外且量能／連續買超未確認，只可當矛盾不可當正式追蹤", True
    if off_rank or flip:
        return "talk_only", "單日矛盾／翻臉，未進正式預測", True
    return "context", "對照股，不是下一交易日主盯", False


def _hero_seed_rows(structured_data: dict, limit: int = 3) -> list[dict[str, Any]]:
    """
    Every prediction_target first (in ledger order), then focus_ranking by
    title_priority to fill the remaining slots. A stock is deduplicated by
    both its ticker and its name, so a target with a ticker and a ranking row
    without one (or the reverse) never yields two cards.
    """
    ranking = [row for row in (structured_data.get("focus_ranking") or []) if isinstance(row, dict)]
    seeds: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _keys(ticker, name) -> set[str]:
        return {key for key in (normalize_ticker(ticker), normalize_name(name)) if key}

    by_key = {}
    for row in ranking:
        for key in _keys(row.get("ticker"), row.get("name")):
            by_key.setdefault(key, row)

    for target in _as_list(structured_data.get("prediction_targets")):
        if not isinstance(target, dict):
            continue
        ticker = normalize_ticker(target.get("id"))
        name = _clean(target.get("name"))
        keys = _keys(ticker, name)
        if not keys or keys & seen:
            continue
        base = next((by_key[key] for key in (ticker, normalize_name(name)) if key in by_key), {})
        seen.update(keys | _keys(base.get("ticker"), base.get("name")))
        row = dict(base)
        row.setdefault("ticker", ticker)
        row.setdefault("name", name)
        if not row.get("ticker"):
            row["ticker"] = ticker
        if not row.get("name"):
            row["name"] = name
        if not row.get("source_text"):
            row["source_text"] = _clean(target.get("trigger"))
        seeds.append(row)

    if ranking:
        for row in ranking:
            if len(seeds) >= limit:
                break
            keys = _keys(row.get("ticker"), row.get("name"))
            if not keys or keys & seen:
                continue
            seen.update(keys)
            seeds.append(row)
        return seeds

    for raw in (
        _as_list(structured_data.get("safe_havens_bought"))[:2]
        + _as_list(structured_data.get("price_volume_divergence"))[:2]
        + _as_list(structured_data.get("heavyweights_dumped"))[:1]
    ):
        if len(seeds) >= limit:
            break
        text = _clean(raw)
        ticker, name = parse_ticker_name(text)
        keys = _keys(ticker, name) or {text}
        if not text or keys & seen:
            continue
        seen.update(keys)
        seeds.append({"ticker": ticker, "name": name, "source_text": text})
    return seeds


# ---------------------------------------------------------------------------
# Hero cards
# ---------------------------------------------------------------------------


def _streak_for(structured_data, local_facts, local, stock_db, ticker):
    """
    (days | None, complete) for 外資連續買超. The session-based count stamped in
    local_market_facts wins; a stock_db count is used only when it was written
    for this data date (an older count would be a stale streak). Unknown → None.
    """
    if local and local.get("foreign_streak_days") is not None:
        days = _int(local.get("foreign_streak_days"))
        return days, bool(local.get("foreign_streak_complete", True))
    from modules.foreign_streak import streak_is_fresh

    tracking = _tracking(stock_db, ticker)
    as_of = (local_facts or {}).get("data_date") or (structured_data or {}).get("date")
    if tracking and streak_is_fresh(tracking, as_of):
        days = _int(tracking.get("consecutive_foreign_buy_days"))
        return days, bool(tracking.get("foreign_buy_streak_complete", False))
    return None, True


def build_hero_cards(
    structured_data: dict,
    stock_db: dict | None = None,
    limit: int = 3,
    local_facts: dict | None = None,
) -> list[dict[str, Any]]:
    structured_data = structured_data or {}
    stock_db = stock_db if stock_db is not None else _load_stock_db()
    local_facts = _local_facts_from(structured_data, local_facts)
    official_ids = _official_tickers(structured_data)
    known = _known_names(structured_data, local_facts)
    volume_complete = bool((local_facts or {}).get("volume_complete"))
    volume_top_n = int((local_facts or {}).get("volume_top_n") or 20)
    cards = []
    for row in _hero_seed_rows(structured_data, limit=limit):
        ticker = normalize_ticker(row.get("ticker"))
        name = _clean(row.get("name"))
        local = lookup_local_stock(local_facts, ticker, name)
        if local:
            ticker = ticker or local.get("ticker") or ""
            name = name or local.get("name") or ""

        evidence_bits = []
        source_text = _clean(row.get("source_text") or "")
        if source_text:
            evidence_bits.append(source_text)
        for div in _as_list(structured_data.get("price_volume_divergence")):
            blob = _clean(div)
            if blob and blob not in evidence_bits and mentions_stock(blob, ticker, name, known):
                evidence_bits.append(blob)
        evidence = "；".join(evidence_bits)

        list_amount, in_sell_list = _list_amount(structured_data, ticker, name, known)
        llm_numbers = {
            "foreign_amount": parse_amount_yi(evidence),
            "pct_change": parse_pct(evidence),
            "volume_rank": parse_volume_rank(evidence),
            "off_rank": is_off_rank(evidence),
        }
        number_source: dict[str, str] = {}

        amount = None
        if local and local.get("foreign_amount") is not None:
            amount = float(local["foreign_amount"])
            number_source["foreign_amount"] = "local"
        elif list_amount is not None:
            amount = list_amount
            number_source["foreign_amount"] = "local_list"
        elif llm_numbers["foreign_amount"] is not None:
            number_source["foreign_amount"] = "llm_text"
        if local and local.get("sell_rank"):
            in_sell_list = True

        pct = None
        if local and local.get("pct_change") is not None:
            pct = float(local["pct_change"])
            number_source["pct_change"] = "local"
        elif llm_numbers["pct_change"] is not None:
            number_source["pct_change"] = "llm_text"

        vol_rank = None
        off_rank = False
        if local and local.get("volume_rank"):
            vol_rank = int(local["volume_rank"])
            number_source["volume_rank"] = "local"
        elif (
            local_facts is not None
            and volume_complete
            and _TICKER_ONLY.match(ticker or "")
            and (local or {}).get("listing") == "twse"
        ):
            # The full TWSE top-N list is known: absence of a known 上市 ticker
            # is a verified fact. A name-only seed (「PCB族群」) or a ticker that
            # may be 上櫃 (not covered by the list) stays unknown.
            off_rank = True
            number_source["volume_rank"] = "local"
        elif llm_numbers["volume_rank"] or llm_numbers["off_rank"]:
            number_source["volume_rank"] = "llm_text"
        # Legacy rows (no local ledger) keep the old text-based off-rank signal
        # for the admission gate only; it is not spoken as a fact.
        admission_off_rank = off_rank if local_facts is not None else bool(llm_numbers["off_rank"])

        close = _float((local or {}).get("close"))
        ma20 = _float((local or {}).get("ma20"))
        vol_ratio = _float((local or {}).get("vol_ratio"))

        streak_n, streak_complete = _streak_for(structured_data, local_facts, local, stock_db, ticker)
        sold_today = in_sell_list or (amount is not None and amount < 0)
        # A sell day cannot keep a leftover buy streak from an older prediction row.
        if sold_today:
            streak_n = 0
            streak_complete = True
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
            off_rank=admission_off_rank,
        )
        say_bits = [f"{ticker} {name}".strip() or "焦點股"]
        if amount is not None:
            say_bits.append(f"外資約 {format_amount_yi(amount)}")
        if close:
            say_bits.append(f"收盤 {_format_price(close)}")
        if pct is not None:
            say_bits.append(f"漲跌 {format_pct(pct)}")
        if vol_ratio is None:
            vol_ratio = own_vol_ratio(structured_data, ticker)
        # A verified absence from the share-count board is spoken only with its
        # caveat or the stock's own vol ratio (never 「成交量未進前 20」 alone).
        vol_bit = volume_say_bit(vol_rank, vol_ratio, off_rank)
        if vol_bit:
            say_bits.append(vol_bit)
        verified = sum(
            1
            for value in (amount, pct, vol_rank or (True if off_rank else None), close)
            if value is not None
        )
        if admission != "official" and verified == 0:
            # No locally verified number: a context card would only repeat LLM prose.
            continue
        card = {
            "ticker": ticker,
            "name": name,
            "role_label": row.get("role_label") or row.get("base_role") or "",
            "foreign_amount": amount,
            "pct_change": pct,
            "volume_rank": vol_rank,
            "off_rank": off_rank,
            "close": close,
            "ma20": ma20,
            "vol_ratio": vol_ratio,
            "one_day_flip": is_one_day_flip(evidence),
            "sold_today": sold_today,
            "volume_confirmation": vol_confirm_n,
            "foreign_streak_days": streak_n,
            "foreign_streak_complete": streak_complete,
            "admission": admission,
            "admission_reason": reason,
            "caution": caution,
            "invalidation": _lookup_invalidation(structured_data, ticker, name, close=close),
            "evidence": evidence,
            "number_source": number_source,
            "llm_numbers": {k: v for k, v in llm_numbers.items() if v not in (None, False)},
            "say_line": "，".join(say_bits),
        }
        if ma20 and close:
            card["ma20_gap_pct"] = round((close / ma20 - 1) * 100, 2)
        cards.append(card)
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
            who = h.get("name") or h.get("ticker")
            if h.get("off_rank"):
                if h.get("vol_ratio") is not None:
                    extra.append(f"{who}量增比{h['vol_ratio']:.1f}x")
                else:
                    extra.append(f"{who}{OFF_SHARE_BOARD_LABEL}")
            elif h.get("volume_rank"):
                extra.append(f"{who}量第{h.get('volume_rank')}")
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
    """
    Time-scale / reliability line for hosts. Recomputed from current-method
    ledger rows (TWSE sessions, same-window excess, CI vs baseline); a stored
    legacy one_liner such as 「20D 優於短線窗」 is never republished, and no
    window is called the 成敗尺子 unless the statistics support it.
    """
    from modules.horizon_reliability import current_reliability_line

    return _clean(current_reliability_line(structured_data))


def _official_watch(structured_data: dict, heroes: list[dict]) -> list[dict]:
    """
    One card per prediction_target, in ledger order. Hero seeds always include
    every prediction_target (never capped by the focus-ranking top 3), so this
    is a filter, not a second ranking.
    """
    by_ticker = {h.get("ticker"): h for h in heroes if h.get("admission") == "official"}
    out = []
    for target in _as_list(structured_data.get("prediction_targets")):
        if not isinstance(target, dict):
            continue
        card = by_ticker.pop(normalize_ticker(target.get("id")), None)
        if card:
            out.append(card)
    return out


def build_broadcast_facts(
    structured_data: dict,
    stock_db: dict | None = None,
    local_facts: dict | None = None,
) -> dict[str, Any]:
    structured_data = structured_data or {}
    stock_db = stock_db if stock_db is not None else _load_stock_db()
    local_facts = _local_facts_from(structured_data, local_facts)
    # Read-side sanitize: older structured_data (written before the regex
    # families existed) must not leak intent language into hooks / cards.
    from modules.narrative_sanitize import sanitize_structured_data

    structured_data = sanitize_structured_data(
        {k: v for k, v in structured_data.items() if k not in {"broadcast_facts", "local_market_facts"}}
    )
    heroes = build_hero_cards(structured_data, stock_db=stock_db, limit=3, local_facts=local_facts)
    opening = build_opening(structured_data, heroes)
    official = _official_watch(structured_data, heroes)
    talk_only = [h for h in heroes if h.get("admission") == "talk_only"]
    return {
        "opening": opening,
        "heroes": heroes,
        "official_watch": official,
        "talk_only": talk_only,
        "ruler_line": _ruler_line(structured_data),
        "number_source": "local" if local_facts else "local_list",
    }


def attach_broadcast_facts(
    structured_data: dict,
    stock_db: dict | None = None,
    *,
    institutional_data: dict | None = None,
    volume_data: list | None = None,
    market_data: dict | None = None,
) -> dict:
    """
    Rebuild ``structured_data["broadcast_facts"]``. When raw payloads are passed
    (persist), a compact ``local_market_facts`` ledger is stored first so later
    rebuilds without payloads quote the same local numbers.
    """
    structured_data = structured_data or {}
    if institutional_data or volume_data or market_data:
        try:
            ledger = build_local_market_facts(institutional_data, volume_data, market_data)
            if ledger.get("stocks"):
                structured_data["local_market_facts"] = ledger
        except Exception as exc:  # pragma: no cover - defensive, data shapes vary
            print(f"  ⚠️  本機行情帳本建立失敗，改用本機榜單字串: {exc}")
    structured_data["broadcast_facts"] = build_broadcast_facts(structured_data, stock_db=stock_db)
    return structured_data


def ensure_broadcast_facts(structured_data: dict, stock_db: dict | None = None) -> dict:
    facts = structured_data.get("broadcast_facts") if isinstance(structured_data, dict) else None
    if isinstance(facts, dict) and facts.get("heroes"):
        return facts
    return build_broadcast_facts(structured_data or {}, stock_db=stock_db)


def official_watch_labels(structured_data: dict) -> list[str]:
    """「2603 長榮」 labels for every official tracking stock (prediction_targets order)."""
    out = []
    for target in _as_list((structured_data or {}).get("prediction_targets")):
        if not isinstance(target, dict):
            continue
        label = f"{normalize_ticker(target.get('id'))} {_clean(target.get('name'))}".strip()
        if label and label not in out:
            out.append(label)
    return out
