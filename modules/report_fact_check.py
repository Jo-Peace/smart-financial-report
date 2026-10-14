"""
Post-generation fact check for the Gemini-written daily_report.md.

Gemini copies the leaderboards and numbers by hand; nothing used to compare
them with the TWSE / yfinance payloads fetched in /1. This module runs in
persist, *before* the report is written and before structured extraction:

1. ``enforce_local_tables``: the 〔成交值排名〕 table (from ``value_ranking``,
   the same-day tape ranked by 成交金額; share-count rows marked 依股數 when
   no value ranking exists), a legacy 〔成交量排名〕 table and the 〔法人籌碼〕
   lists are rebuilt from local data (full top 10, local numbers). Gemini's
   「今日角色」 / per-row notes are kept.
2. ``check_report_facts``: regex claims in the prose — 「第 N 名」, 「最主要的
   提款機／買超第一／成交量冠軍」, 「成交值第 N 名」, 「外資 ±X 億」, 「漲／跌 X%」
   — are compared with the same local ledger
   (``broadcast_facts.build_local_market_facts`` plus the market-wide 成交值
   ranks from ``value_ranking``, see ``attach_value_ranks``).
3. ``annotate_report``: severe mismatches (wrong rank, wrong sign, a
   superlative that the local ranking contradicts) get an inline
   「（本機核對：…）」 correction instead of being published as fact.

The summary goes to ``structured_data["fact_check"]``. Environment:
``REPORT_FACT_CHECK`` = annotate (default) | warn | off;
``REPORT_LOCAL_TABLES`` = 1 (default) | 0.
No Gemini, no network. Every function degrades to "no change" on bad input.
"""

from __future__ import annotations

import os
import re
import json
from typing import Any

from modules.broadcast_facts import (
    build_local_market_facts,
    cn_numeral_to_int,
    format_amount_yi,
    normalize_name,
    normalize_ticker,
)

REQUIRED_SECTION_KEYS = ("今日主軸", "法人籌碼", "觀察焦點")

AMOUNT_TOLERANCE_YI = 0.6
AMOUNT_TOLERANCE_RATIO = 0.05
PCT_TOLERANCE = 0.06

_RANK_NUM = r"([0-9]{1,2}|[一二三四五六七八九十兩]{1,3})"
_SELL_WORDS = r"(?:賣|砍|提款|拋|倒貨|調節|出脫|失血)"
_BUY_WORDS = r"(?:買|掃|加碼|承接|吃|塞進|搬上)"
_CLAUSE_SPLIT = re.compile(r"[，,；;。！!？?：:]")

_RANK_PATTERNS = (
    ("buy_rank", re.compile(r"買超(?:金額)?(?:全台|全市場|市場)?(?:的)?第\s*" + _RANK_NUM + r"\s*名?")),
    ("sell_rank", re.compile(r"賣超(?:金額)?(?:全台|全市場|市場)?(?:的)?第\s*" + _RANK_NUM + r"\s*名?")),
    (
        "volume_rank",
        re.compile(r"(?:成交量|量榜|量能)(?:排名)?(?:全台|全市場)?(?:居|排)?第\s*" + _RANK_NUM + r"\s*名?"),
    ),
    # Market-wide 成交值 rank (value_ranking); a 「觀察池成交值第 N 名」 pool rank
    # or an ETF-only rank is a different list and is not checked here.
    (
        "value_rank",
        re.compile(
            r"(?<!觀察池)(?<!ETF)(?<!ETF\s)成交(?:值|金額)(?:排名)?(?:全台|全市場|上市)?(?:個股)?(?:居|排)?第\s*"
            + _RANK_NUM
            + r"\s*名?"
        ),
    ),
)
_SUPERLATIVE_PATTERNS = (
    ("sell_rank", re.compile(r"最(?:主要|大)的?(?:提款機|賣壓來源|賣超|提款|出貨|失血)|賣超(?:冠軍|居首|王|最多)")),
    ("buy_rank", re.compile(r"最(?:主要|大)的?(?:買超|買盤|金主|加碼)|買超(?:冠軍|居首|王|最多)")),
    ("volume_rank", re.compile(r"成交量(?:全台|全市場)?(?:冠軍|最大)|量能冠軍")),
    ("value_rank", re.compile(r"(?<!觀察池)(?<!ETF)(?<!ETF\s)成交(?:值|金額)(?:全台|全市場|上市)?(?:冠軍|最大|最高|居首)")),
)
_GENERIC_FIRST = re.compile(r"(?:全台|全市場|市場)第(?:一|1)(?!\d)")
# 「外資大買 2409 友達 81 億」: a ticker token may sit between 外資 and the amount.
_AMOUNT_RE = re.compile(
    r"外資(?:[^，,。；;\n\d]|(?<![\d.])\d{4,6}[A-Z]?(?![\d.]|\s*億)){0,16}?([+-]?\d+(?:\.\d+)?)\s*億"
)
# 「漲跌／漲跌幅」 are neutral: the sign comes from the number itself.
_PCT_RE = re.compile(
    r"(漲跌幅|漲跌|上漲|下跌|大漲|大跌|重挫|收漲|收跌|漲幅|跌幅|漲|跌|摔)\s*(?:了|約|高達|達|逾|近)?\s*([+-]?\d+(?:\.\d+)?)\s*%"
)
_NEGATIVE_WORDS = {"下跌", "大跌", "重挫", "收跌", "跌幅", "跌", "摔"}
# The last time marker before a claim decides which session it talks about.
_OTHER_DAY = r"明日|明天|下週|下一(?:個)?交易日|未來|若|如果|一旦|假如|昨|前日|前一(?:個)?(?:交易)?日|上週|上一(?:個)?交易日|隔日|隔天|次日|當時|當日|當天|過去|歷史|此前|先前|之前|曾|上次|上集|那天|那次|\d{4}-\d{2}-\d{2}|(?<!\d)\d{1,2}/\d{1,2}(?!\d)"
_TODAY = r"今日|今天|本日|今(?=[買賣漲跌收量])"
_TIME_MARKER = re.compile(f"(?P<other>{_OTHER_DAY})|(?P<today>{_TODAY})")
_AGGREGATE = re.compile(r"合計|累計|共計|加總|總共|兩檔|三檔|分別|連續|近\s*\d+\s*(?:日|天|週)|本週|這週|月來|年來")
# The local buy / sell lists are *foreign* single-day ranks. The last investor
# group named before a rank claim decides whom it is about; 「投信買超第一」 or
# 「三大法人合計買超第一」 cannot be checked against them.
_INVESTOR_GROUP = re.compile(r"外資(?:及陸資)?|投信|自營商|三大法人|(?<!外資)法人")
_LOWER_BOUND = re.compile(r"(?:逾|超過|破|以上|上看)\s*$")
_APPROX = re.compile(r"(?:近|約|將近|接近)\s*$")
_SKIP_SECTIONS = ("相似盤", "日曆約束")
_TICKER_RE = re.compile(r"(?<![0-9A-Za-z])(\d{4,6}[A-Z]?)(?![0-9])")


def _mode() -> str:
    return (os.getenv("REPORT_FACT_CHECK") or "annotate").strip().lower()


def _local_tables_enabled() -> bool:
    return (os.getenv("REPORT_LOCAL_TABLES") or "1").strip().lower() not in {"0", "false", "off", "no"}


# ---------------------------------------------------------------------------
# Section completeness (used by analyzer before the report is accepted)
# ---------------------------------------------------------------------------


def missing_required_sections(report: str, required=REQUIRED_SECTION_KEYS) -> list[str]:
    """Core 〔…〕 sections missing from a daily report (truncation / format drift)."""
    text = str(report or "")
    missing = []
    for key in required:
        if not re.search(r"〔[^〕\n]*" + re.escape(key) + r"[^〕\n]*〕", text):
            missing.append(key)
    return missing


# ---------------------------------------------------------------------------
# Mentions
# ---------------------------------------------------------------------------


def _stock_db_names() -> set[str]:
    """Known names (stock_db.json) so 「南亞」 is not matched inside 「南亞科」. Optional."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "stock_db.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {normalize_name(v.get("name")) for v in data.values() if isinstance(v, dict) and v.get("name")}
    except Exception:
        return set()


def _name_index(ledger: dict) -> list[tuple[str, str]]:
    names = []
    for ticker, row in (ledger.get("stocks") or {}).items():
        for raw in {row.get("name") or "", normalize_name(row.get("name"))}:
            raw = str(raw).strip()
            if len(raw) >= 2:
                names.append((raw, ticker))
    names.sort(key=lambda item: -len(item[0]))
    return names


def _mentions(
    segment: str,
    ledger: dict,
    names: list[tuple[str, str]],
    known_names: set[str] | None = None,
) -> list[tuple[int, str]]:
    """[(position, ticker)] for every stock mention, longest name wins on overlap."""
    stocks = ledger.get("stocks") or {}
    known_names = known_names or set()
    taken: list[tuple[int, int]] = []
    found: list[tuple[int, str]] = []

    def _free(start, end):
        return all(end <= s or start >= e for s, e in taken)

    for m in _TICKER_RE.finditer(segment):
        ticker = normalize_ticker(m.group(1))
        # Any 4-6 digit code blocks the span, even when it is not in the ledger
        # (「2408 南亞科」 must not become a mention of 1303 南亞).
        taken.append((m.start(), m.end()))
        if ticker in stocks:
            found.append((m.start(), ticker))
    for name, ticker in names:
        longer = [n for n in known_names if len(n) > len(name) and n.startswith(name)]
        for m in re.finditer(re.escape(name), segment):
            if not _free(m.start(), m.end()):
                continue
            if any(segment.startswith(n, m.start()) for n in longer):
                continue
            prev_code = re.search(r"(\d{4,6}[A-Z]?)\s*\**\s*$", segment[: m.start()])
            if prev_code and normalize_ticker(prev_code.group(1)) != ticker:
                continue
            taken.append((m.start(), m.end()))
            found.append((m.start(), ticker))
    found.sort()
    return found


def _clause_bounds(segment: str, pos: int) -> tuple[int, int]:
    clause_start = 0
    clause_end = len(segment)
    for m in _CLAUSE_SPLIT.finditer(segment):
        if m.start() < pos:
            clause_start = m.end()
        elif m.start() >= pos:
            clause_end = m.start()
            break
    return clause_start, clause_end


def _talks_about_today(segment: str, pos: int, report_date: str = "") -> bool:
    """False when the nearest time marker before ``pos`` points at another session."""
    clause_start, _ = _clause_bounds(segment, pos)
    for start in (clause_start, 0):
        last = None
        for m in _TIME_MARKER.finditer(segment, start, pos):
            last = m
        if last is not None:
            if last.group("today"):
                return True
            token = last.group("other") or ""
            if report_date and token == report_date:
                return True
            return False
    return True


def _quoted_term(segment: str, start: int, end: int) -> bool:
    """「冷門買超第一」 style quoted generic terms are not claims about a stock."""
    left = segment.rfind("「", 0, start)
    right = segment.find("」", end)
    if left < 0 or right < 0:
        return False
    if segment.find("」", left, start) >= 0:
        return False
    return (right - left) <= 12


def _candidates(segment: str, pos: int, mentions: list[tuple[int, str]], allow_segment: bool = False) -> list[str]:
    """Stocks the claim may refer to: same clause (nearest before first), else the table row's stock."""
    clause_start, clause_end = _clause_bounds(segment, pos)
    in_clause = [(p, t) for p, t in mentions if clause_start <= p < clause_end]
    before = [t for p, t in sorted(in_clause, key=lambda x: -x[0]) if p <= pos]
    after = [t for p, t in in_clause if p > pos]
    out = []
    for t in before + after:
        if t not in out:
            out.append(t)
    if not out and allow_segment and mentions:
        # Table rows: a cell without its own stock talks about the row's stock.
        out.append(mentions[0][1])
    return out


def _subject(segment: str, pos: int, mentions: list[tuple[int, str]], allow_segment: bool = False) -> str | None:
    found = _candidates(segment, pos, mentions, allow_segment=allow_segment)
    return found[0] if found else None


def _label(ledger: dict, ticker: str) -> str:
    row = (ledger.get("stocks") or {}).get(ticker) or {}
    return f"{ticker} {row.get('name') or ''}".strip()


def _leader(ledger: dict, field: str) -> dict | None:
    for row in (ledger.get("stocks") or {}).values():
        if row.get(field) == 1:
            return row
    return None


def _rank_list_size(ledger: dict, field: str) -> int:
    if field == "volume_rank":
        return int(ledger.get("volume_top_n") or 0)
    if field == "value_rank":
        return int(ledger.get("value_top_n") or 0)
    return int(ledger.get("inst_top_n") or 0)


def _field_text(field: str) -> str:
    return {"buy_rank": "外資買超", "sell_rank": "外資賣超", "volume_rank": "成交量", "value_rank": "個股成交值"}[field]


def attach_value_ranks(ledger: dict, value_ranking: dict | None, volume_data: list | None = None) -> dict:
    """
    Add the market-wide 成交值 rank (``value_rank`` / ``trade_value_yi``) from
    the same-day tape's ``value_ranking`` (and the share board rows' value_rank,
    same basis) to the local ledger, so a prose 「成交值第 N 名」 is checked
    against the same list as the prompt's 成交值前20 table. No-op without it.
    """
    try:
        from modules.market_signal_scorer import market_value_ranks

        ranks = market_value_ranks(value_ranking, volume_data)
    except Exception:
        ranks = {}
    if not ranks or not isinstance(ledger, dict):
        return ledger
    stocks = ledger.setdefault("stocks", {})
    names = {
        normalize_ticker(row.get("id")): row.get("name")
        for row in ((value_ranking or {}).get("stocks") or []) + list(volume_data or [])
        if isinstance(row, dict)
    }
    for ticker, info in ranks.items():
        row = stocks.setdefault(ticker, {"ticker": ticker, "name": str(names.get(ticker) or "").strip()})
        if not row.get("name") and names.get(ticker):
            row["name"] = str(names[ticker]).strip()
        row["value_rank"] = info["rank"]
        if info.get("trade_value_yi") is not None:
            row["trade_value_yi"] = info["trade_value_yi"]
    ledger["value_top_n"] = len([r for r in (value_ranking or {}).get("stocks") or [] if isinstance(r, dict)])
    return ledger


def _leader_text(ledger: dict, field: str) -> str:
    leader = _leader(ledger, field)
    if not leader:
        return ""
    label = f"{leader.get('ticker')} {leader.get('name') or ''}".strip()
    if field in {"buy_rank", "sell_rank"} and leader.get("foreign_amount") is not None:
        return f"{label} {format_amount_yi(leader['foreign_amount'])}"
    if field == "value_rank" and leader.get("trade_value_yi") is not None:
        return f"{label} 約 {float(leader['trade_value_yi']):.0f} 億"
    return label


# ---------------------------------------------------------------------------
# Claim checks
# ---------------------------------------------------------------------------


def _issue(kind, severity, ticker, claim, local, snippet, phrase, correction="") -> dict[str, Any]:
    return {
        "type": kind,
        "severity": severity,
        "ticker": ticker or "",
        "claim": claim,
        "local": local,
        "snippet": snippet[:160],
        "phrase": phrase,
        "correction": correction,
    }


def _check_rank_claim(ledger, field, ticker, claimed, segment, phrase):
    if field == "value_rank" and str(ticker or "").startswith("00"):
        return None  # ETFs have their own 成交值 list; the stock ranking excludes them
    row = (ledger.get("stocks") or {}).get(ticker) or {}
    actual = row.get(field)
    size = _rank_list_size(ledger, field)
    label = _label(ledger, ticker)
    if actual == claimed:
        return None
    if actual is None:
        if not size or claimed > size:
            return None  # outside the locally known list: unverifiable
        correction = f"{label} 不在本機{_field_text(field)}前 {size} 名"
        leader = _leader_text(ledger, field)
        if leader and claimed == 1:
            correction += f"；第 1 名是 {leader}"
        return _issue("rank", "fail", ticker, f"{_field_text(field)}第{claimed}名", None, segment, phrase, correction)
    correction = f"{label} 為{_field_text(field)}第 {actual} 名"
    if claimed == 1 or actual != 1:
        leader = _leader_text(ledger, field)
        if leader and actual != 1:
            correction += f"；第 1 名是 {leader}"
    return _issue("rank", "fail", ticker, f"{_field_text(field)}第{claimed}名", actual, segment, phrase, correction)


# Field keywords for a bare 「全市場／全台第一」: the keyword *nearest* the
# superlative decides which list it claims (「創意成交值放大、外資買盤全市場第一」
# is a foreign-buy claim, 「外資買超的長榮，成交值全市場第一」 a 成交值 claim).
# A 觀察池 / ETF 成交值 is a different list: the claim is not checked.
_GENERIC_FIELD_WORDS = re.compile(
    r"(?P<pool>(?:觀察池|ETF\s?)成交(?:值|金額))"
    r"|(?P<value>成交(?:值|金額))"
    r"|(?P<volume>成交量|量能|量榜)"
    rf"|(?P<sell>{_SELL_WORDS})"
    rf"|(?P<buy>{_BUY_WORDS}|(?<!成交)金額)"
)
_GENERIC_FIELD_BY_GROUP = {"pool": None, "value": "value_rank", "volume": "volume_rank", "sell": "sell_rank", "buy": "buy_rank"}


def _generic_first_field(segment: str, pos: int) -> str | None:
    last = None
    for m in _GENERIC_FIELD_WORDS.finditer(segment, max(0, pos - 24), pos):
        last = m
    if last is None:
        return None
    return _GENERIC_FIELD_BY_GROUP[last.lastgroup]


def _single_day_foreign_scope(segment: str, m: re.Match, field: str) -> bool:
    """
    True when a rank / superlative claim can be compared with the local lists:
    no multi-day qualifier in its clause, and (for buy / sell ranks) no other
    investor group than 外資 named last before it.
    """
    clause_start, clause_end = _clause_bounds(segment, m.start())
    if _AGGREGATE.search(segment[clause_start:min(clause_end, m.end() + 6)]):
        return False
    if field not in {"buy_rank", "sell_rank"}:
        return True
    groups = _INVESTOR_GROUP.findall(segment, clause_start, m.start())
    return not groups or groups[-1].startswith("外資")


def _claimed_sign(segment: str, start: int, end: int) -> int:
    around = segment[max(0, start - 10):min(len(segment), end + 6)]
    if re.search(_SELL_WORDS, around) and not re.search(_BUY_WORDS, around):
        return -1
    if re.search(_BUY_WORDS, around) and not re.search(_SELL_WORDS, around):
        return 1
    return 0


def _pct_tolerance(raw: str) -> float:
    """「5%」 may be a rounded 5.59%; 「4.85%」 must match to rounding."""
    decimals = len(raw.split(".", 1)[1]) if "." in raw else 0
    if decimals == 0:
        return 1.0
    if decimals == 1:
        return 0.1
    return PCT_TOLERANCE


def _iter_segments(report: str):
    """(line_no, line, section_header) for every non-empty, non-separator line."""
    section = ""
    for line_no, line in enumerate(str(report or "").splitlines()):
        if re.match(r"^\s*#{1,4}\s", line):
            section = line
        if not line.strip() or re.match(r"^\s*\|?\s*:?-{3,}", line):
            continue
        yield line_no, line, section


def check_report_facts(
    report: str,
    *,
    institutional_data: dict | None = None,
    volume_data: list | None = None,
    market_data: dict | None = None,
    ledger: dict | None = None,
    known_names: set[str] | None = None,
    value_ranking: dict | None = None,
) -> dict[str, Any]:
    """Compare prose claims about *today* with local data. Returns {status, checked, issues, ...}."""
    if not ledger:
        ledger = build_local_market_facts(institutional_data, volume_data, market_data)
        attach_value_ranks(ledger, value_ranking, volume_data)
    result: dict[str, Any] = {"status": "skipped", "checked": 0, "issues": []}
    if not (ledger.get("stocks")):
        result["reason"] = "no local market data"
        return result
    report_date = str(ledger.get("data_date") or "")
    names = _name_index(ledger)
    if known_names is None:
        known_names = _stock_db_names()
    known_names = set(known_names) | {normalize_name(n) for n, _t in names}
    issues: list[dict] = []
    checked = 0
    stocks = ledger.get("stocks") or {}

    for _line_no, segment, section in _iter_segments(report):
        if any(key in section for key in _SKIP_SECTIONS):
            continue
        mentions = _mentions(segment, ledger, names, known_names)
        if not mentions:
            continue
        # Table rows and 「- **2603 長榮**：…」 label lines: a clause without its own
        # stock talks about the row / label stock.
        colon = segment.find("：")
        is_table_row = segment.lstrip().startswith("|") or bool(
            re.match(r"^\s*(?:[-*]|\d+\.)\s", segment) and colon > 0 and mentions[0][0] < colon <= 40
        )
        spans_used: list[tuple[int, int]] = []

        def _rank_claim(field, m, claimed):
            nonlocal checked
            if not claimed or _quoted_term(segment, m.start(), m.end()):
                return
            if not _talks_about_today(segment, m.start(), report_date):
                return
            if not _single_day_foreign_scope(segment, m, field):
                return  # 本週／近 5 日／連續 N 日 or 投信／自營商 ranks: not in the local lists
            candidates = _candidates(segment, m.start(), mentions, allow_segment=is_table_row)
            if not candidates:
                return
            checked += 1
            spans_used.append((m.start(), m.end()))
            if any((stocks.get(t) or {}).get(field) == claimed for t in candidates):
                return
            issue = _check_rank_claim(ledger, field, candidates[0], claimed, segment, m.group(0))
            if issue:
                issues.append(issue)
            return issue

        for field, pattern in _RANK_PATTERNS:
            for m in pattern.finditer(segment):
                _rank_claim(field, m, cn_numeral_to_int(m.group(1)))

        superlatives = [(field, m) for field, pattern in _SUPERLATIVE_PATTERNS for m in pattern.finditer(segment)]
        for m in _GENERIC_FIRST.finditer(segment):
            field = _generic_first_field(segment, m.start())
            if field:
                superlatives.append((field, m))
        for field, m in superlatives:
            if any(s0 <= m.start() < e0 for s0, e0 in spans_used):
                continue
            issue = _rank_claim(field, m, 1)
            if issue:
                issue["type"] = "superlative"

        for m in _AMOUNT_RE.finditer(segment):
            pos = m.start(1)
            if not _talks_about_today(segment, pos, report_date):
                continue
            clause_start, _ = _clause_bounds(segment, pos)
            if _AGGREGATE.search(segment[clause_start:m.end() + 4]):
                continue
            candidates = _candidates(segment, pos, mentions, allow_segment=segment.lstrip().startswith("|"))
            if len(candidates) != 1:
                continue  # no subject, or one amount shared by several stocks
            ticker = candidates[0]
            local = (stocks.get(ticker) or {}).get("foreign_amount")
            if local is None:
                continue
            try:
                claimed = float(m.group(1))
            except ValueError:
                continue
            checked += 1
            sign = -1 if m.group(1).startswith("-") else (1 if m.group(1).startswith("+") else _claimed_sign(segment, m.start(), m.end()))
            label = _label(ledger, ticker)
            if sign and (sign > 0) != (local > 0):
                issues.append(
                    _issue(
                        "amount_sign",
                        "fail",
                        ticker,
                        f"外資{'買' if sign > 0 else '賣'}超 {abs(claimed):g} 億",
                        local,
                        segment,
                        m.group(0),
                        f"{label} 本機外資 {format_amount_yi(local)}",
                    )
                )
                continue
            head = segment[clause_start:pos]
            if _LOWER_BOUND.search(head):
                ok = abs(local) >= abs(claimed) * 0.98
            else:
                ratio = 0.10 if _APPROX.search(head) else AMOUNT_TOLERANCE_RATIO
                floor = AMOUNT_TOLERANCE_YI if "." in m.group(1) else 1.0
                ok = abs(abs(claimed) - abs(local)) <= max(floor, abs(local) * ratio)
            if not ok:
                issues.append(
                    _issue(
                        "amount",
                        "warn",
                        ticker,
                        f"外資 {abs(claimed):g} 億",
                        local,
                        segment,
                        m.group(0),
                        f"{label} 本機外資 {format_amount_yi(local)}",
                    )
                )

        for m in _PCT_RE.finditer(segment):
            if not _talks_about_today(segment, m.start(), report_date):
                continue
            ticker = _subject(segment, m.start(), mentions, allow_segment=is_table_row)
            local = (stocks.get(ticker) or {}).get("pct_change") if ticker else None
            if local is None or local == 0:
                continue  # 0.0 doubles as the fetcher's "could not parse" value
            try:
                value = float(m.group(2))
            except ValueError:
                continue
            word = m.group(1)
            if m.group(2).startswith("-") or (word in _NEGATIVE_WORDS and not m.group(2).startswith("+")):
                claimed = -abs(value)
            else:
                claimed = abs(value)
            checked += 1
            label = _label(ledger, ticker)
            if claimed and local and (claimed > 0) != (local > 0):
                issues.append(
                    _issue("pct_sign", "fail", ticker, f"{word} {value:g}%", local, segment, m.group(0), f"{label} 本機漲跌 {local:+.2f}%")
                )
            elif abs(claimed - local) > _pct_tolerance(m.group(2)):
                issues.append(
                    _issue("pct", "warn", ticker, f"{word} {value:g}%", local, segment, m.group(0), f"{label} 本機漲跌 {local:+.2f}%")
                )

    # De-duplicate identical findings (same stock / phrase / correction).
    unique = []
    seen = set()
    for issue in issues:
        key = (issue["type"], issue["ticker"], issue["phrase"], issue["correction"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    result["issues"] = unique
    result["checked"] = checked
    if any(i["severity"] == "fail" for i in unique):
        result["status"] = "fail"
    elif unique:
        result["status"] = "warn"
    else:
        result["status"] = "pass"
    return result


def annotate_report(report: str, issues: list[dict]) -> tuple[str, int]:
    """Insert 「（本機核對：…）」 right after each failing claim phrase. Returns (text, count)."""
    lines = str(report or "").splitlines()
    count = 0
    for issue in issues or []:
        if issue.get("severity") != "fail" or not issue.get("phrase") or not issue.get("correction"):
            continue
        note = f"（本機核對：{issue['correction']}）"
        phrase = issue["phrase"]
        ticker = issue.get("ticker") or ""
        name = ""
        m = re.match(r"^\S+\s+(.+?)\s+(?:為|不在)", issue.get("correction") or "")
        if m:
            name = normalize_name(m.group(1))
        done = False
        for index, line in enumerate(lines):
            if phrase not in line or note in line:
                continue
            if ticker not in line and (not name or name not in line):
                continue
            pos = line.index(phrase) + len(phrase)
            lines[index] = line[:pos] + note + line[pos:]
            count += 1
            done = True
            break
        issue["resolution"] = "annotated" if done else "not_in_final_text"
    text = "\n".join(lines)
    if str(report or "").endswith("\n"):
        text += "\n"
    return text, count


# ---------------------------------------------------------------------------
# Local tables
# ---------------------------------------------------------------------------


def _section_bounds(lines: list[str], key: str) -> tuple[int, int] | None:
    start = None
    for index, line in enumerate(lines):
        if re.match(r"^\s*#{1,4}\s*〔[^〕]*" + re.escape(key), line):
            start = index
            break
    if start is None:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped == "---" or re.match(r"^\s*#{1,4}\s", lines[index]):
            end = index
            break
    return start, end


def _table_rows(body: list[str]) -> tuple[int, int, list[list[str]]]:
    first = last = -1
    rows = []
    for index, line in enumerate(body):
        if line.strip().startswith("|"):
            if first < 0:
                first = index
            last = index
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            rows.append(cells)
        elif first >= 0:
            break
    return first, last, rows


def _row_ticker(cells: list[str]) -> str:
    for cell in cells[:3]:
        m = _TICKER_RE.search(cell.replace("*", ""))
        if m:
            return normalize_ticker(m.group(1))
    return ""


def _fmt_pct(value) -> str:
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_int(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "—"


def _replace_volume_table(lines: list[str], volume_data: list, top_n: int, notes: list[str]) -> list[str]:
    bounds = _section_bounds(lines, "成交量排名")
    all_rows = [r for r in (volume_data or []) if isinstance(r, dict)]
    rows = all_rows[:top_n]
    if not bounds or not rows:
        return lines
    start, end = bounds
    body = lines[start + 1:end]
    first, last, table = _table_rows(body)
    roles = {}
    llm_rows = []
    for cells in table[2:] if len(table) >= 2 else []:
        ticker = _row_ticker(cells)
        if ticker:
            roles[ticker] = cells[-1] if len(cells) >= 6 else ""
            llm_rows.append(ticker)
    new_table = [
        "| 排名 | 代號 | 名稱 | 成交量（股） | 漲跌幅 | 今日角色 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    shown = {normalize_ticker(r.get("id")) for r in rows}
    # Rows the AI chose outside the top N keep their commentary, with the real rank.
    extra = [
        r
        for r in all_rows[top_n:]
        if normalize_ticker(r.get("id")) in roles and normalize_ticker(r.get("id")) not in shown
    ]
    for index, row in enumerate(rows + extra, start=1):
        ticker = normalize_ticker(row.get("id"))
        role = roles.get(ticker) or "—"
        new_table.append(
            f"| {row.get('rank') or index} | {ticker} | {row.get('name') or ''} | "
            f"{_fmt_int(row.get('volume'))} | {_fmt_pct(row.get('pct_change'))} | {role} |"
        )
    new_table.append("")
    new_table.append(f"（排名、成交量、漲跌幅由本機 TWSE 成交量前 {len(all_rows)} 名覆寫；「今日角色」為 AI 註記。）")
    if first >= 0:
        new_body = body[:first] + new_table + body[last + 1:]
    else:
        new_body = [""] + new_table + body
    expected = [normalize_ticker(r.get("id")) for r in rows + extra]
    if llm_rows != expected:
        notes.append(
            f"成交量表：AI 只列 {len(llm_rows)} 列（{', '.join(llm_rows) or '無'}），已改為本機前 {len(rows)} 名"
        )
    return lines[: start + 1] + new_body + lines[end:]


def _fmt_yi(value) -> str:
    try:
        return f"{float(value):,.1f}"
    except (TypeError, ValueError):
        return "—"


def _replace_value_table(
    lines: list[str], value_ranking: dict | None, volume_data: list, top_n: int, notes: list[str]
) -> list[str]:
    """
    Rebuild the 〔成交值排名〕 table (daily report template since item 6) from
    the same-day value ranking: listed stocks by 成交金額, ETFs on one extra
    line. Without a value ranking the share-count board is used and the table
    says 依股數, as the template asks. Gemini's 「今日角色」 column is kept.
    """
    bounds = _section_bounds(lines, "成交值排名")
    if not bounds:
        return lines
    value_ranking = value_ranking if isinstance(value_ranking, dict) else {}
    stocks = [r for r in value_ranking.get("stocks") or [] if isinstance(r, dict) and r.get("trade_value_yi") is not None]
    etfs = [r for r in value_ranking.get("etfs") or [] if isinstance(r, dict) and r.get("trade_value_yi") is not None]
    by_value = bool(stocks)
    all_rows = stocks if by_value else [r for r in (volume_data or []) if isinstance(r, dict)]
    rows = all_rows[:top_n]
    if not rows:
        return lines
    start, end = bounds
    body = lines[start + 1:end]
    first, last, table = _table_rows(body)
    roles = {}
    llm_rows = []
    for cells in table[2:] if len(table) >= 2 else []:
        ticker = _row_ticker(cells)
        if ticker:
            roles[ticker] = cells[-1] if len(cells) >= 6 else ""
            llm_rows.append(ticker)
    unit = "成交值（億）" if by_value else "成交量（股，依股數）"
    new_table = [
        f"| 排名 | 代號 | 名稱 | {unit} | 漲跌幅 | 今日角色 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    shown = {normalize_ticker(r.get("id")) for r in rows}
    extra = [
        r
        for r in all_rows[top_n:]
        if normalize_ticker(r.get("id")) in roles and normalize_ticker(r.get("id")) not in shown
    ]
    for index, row in enumerate(rows + extra, start=1):
        ticker = normalize_ticker(row.get("id"))
        role = roles.get(ticker) or "—"
        amount = _fmt_yi(row.get("trade_value_yi")) if by_value else _fmt_int(row.get("volume"))
        pct = "除權息不比價" if row.get("ex_rights") and row.get("pct_change") is None else _fmt_pct(row.get("pct_change"))
        new_table.append(f"| {row.get('rank') or index} | {ticker} | {row.get('name') or ''} | {amount} | {pct} | {role} |")
    new_table.append("")
    if by_value:
        if etfs:
            new_table.append(
                "ETF（另列，不當個股主角）："
                + "、".join(f"{r.get('id')} {r.get('name') or ''} {_fmt_yi(r.get('trade_value_yi'))}億".strip() for r in etfs[:5])
            )
        new_table.append(
            f"（排名、成交值、漲跌幅由本機 TWSE 同日全日收盤表覆寫（上市個股，ETF 另列）；「今日角色」為 AI 註記。）"
        )
    else:
        new_table.append(
            f"（成交值排行未取得，改列本機 TWSE 成交股數前 {len(all_rows)} 名（依股數，不是資金排名）；「今日角色」為 AI 註記。）"
        )
    if first >= 0:
        new_body = body[:first] + new_table + body[last + 1:]
    else:
        new_body = [""] + new_table + body
    expected = [normalize_ticker(r.get("id")) for r in rows + extra]
    if llm_rows != expected:
        label = "成交值表" if by_value else "成交值表（依股數）"
        notes.append(f"{label}：AI 列 {len(llm_rows)} 列（{', '.join(llm_rows) or '無'}），已改為本機前 {len(rows)} 名")
    return lines[: start + 1] + new_body + lines[end:]


def _parse_inst_notes(body: list[str]) -> tuple[dict[tuple[str, str], str], list[str], list[tuple[str, str]]]:
    """({(side, ticker): note}, paragraph lines to keep, [(side, ticker)] as AI listed them)."""
    notes: dict[tuple[str, str], str] = {}
    keep: list[str] = []
    listed: list[tuple[str, str]] = []
    side = ""
    for line in body:
        stripped = line.strip()
        if not stripped:
            continue
        if re.search(r"買超", stripped) and not re.match(r"^\s*(?:\d+\.|[-*])\s", stripped) and len(stripped) < 40:
            side = "buy"
            continue
        if re.search(r"賣超", stripped) and not re.match(r"^\s*(?:\d+\.|[-*])\s", stripped) and len(stripped) < 40:
            side = "sell"
            continue
        if stripped.startswith("|"):
            continue  # an AI-written table is superseded by the local lists
        if re.match(r"^\s*(?:\d+\.|[-*])\s", stripped):
            m = _TICKER_RE.search(stripped.replace("*", ""))
            if m and side:
                ticker = normalize_ticker(m.group(1))
                listed.append((side, ticker))
                note = re.search(r"[（(]([^（）()]{1,60})[)）]\s*$", stripped)
                if note and not re.fullmatch(r"[0-9.,+\-\s億元%]+", note.group(1)):
                    notes[(side, ticker)] = note.group(1).strip()
            continue
        keep.append(line)
    return notes, keep, listed


def _replace_institutional_section(lines: list[str], institutional_data: dict, top_n: int, notes_out: list[str]) -> list[str]:
    bounds = _section_bounds(lines, "法人籌碼")
    buys = [r for r in (institutional_data.get("top_buy") or []) if isinstance(r, dict)][:top_n]
    sells = [r for r in (institutional_data.get("top_sell") or []) if isinstance(r, dict)][:top_n]
    if not bounds or not (buys or sells):
        return lines
    start, end = bounds
    header = lines[start]
    data_date = institutional_data.get("data_date")
    if data_date:
        if re.search(r"資料日期：[^）)]*", header):
            header = re.sub(r"資料日期：[^）)]*", f"資料日期：{data_date}", header)
        else:
            header = header.rstrip() + f"（資料日期：{data_date}）"
    body = lines[start + 1:end]
    row_notes, keep, listed = _parse_inst_notes(body)

    def _block(title, side, rows):
        out = [f"**{title}（本機 TWSE 估計金額＝外資股數×收盤）：**"]
        for index, row in enumerate(rows, start=1):
            ticker = normalize_ticker(row.get("id"))
            amount = row.get("est_amount")
            try:
                amount_text = f"約 {float(amount):+.1f} 億元" if amount not in (None, "", 0) else "金額未估"
            except (TypeError, ValueError):
                amount_text = "金額未估"
            note = row_notes.get((side, ticker))
            out.append(f"{index}. **{ticker} {row.get('name') or ''}**：{amount_text}{f'（{note}）' if note else ''}")
        return out

    new_body = [""]
    if buys:
        new_body += _block(f"外資買超前 {len(buys)} 名", "buy", buys) + [""]
    if sells:
        new_body += _block(f"外資賣超前 {len(sells)} 名", "sell", sells) + [""]
    if keep:
        new_body += keep + [""]
    expected = [("buy", normalize_ticker(r.get("id"))) for r in buys] + [
        ("sell", normalize_ticker(r.get("id"))) for r in sells
    ]
    if listed != expected:
        notes_out.append(
            f"法人籌碼：AI 列 {len(listed)} 檔，已改為本機買超前 {len(buys)}＋賣超前 {len(sells)}"
        )
    return lines[:start] + [header] + new_body + lines[end:]


def enforce_local_tables(
    report: str,
    *,
    institutional_data: dict | None = None,
    volume_data: list | None = None,
    top_n: int = 10,
    value_ranking: dict | None = None,
) -> tuple[str, list[str]]:
    """Rebuild the leaderboards from local data. Returns (report, notes)."""
    notes: list[str] = []
    text = str(report or "")
    if not text or not _local_tables_enabled():
        return text, notes
    try:
        lines = text.splitlines()
        lines = _replace_value_table(lines, value_ranking, volume_data or [], top_n, notes)
        lines = _replace_volume_table(lines, volume_data or [], top_n, notes)
        lines = _replace_institutional_section(lines, institutional_data or {}, top_n, notes)
        out = "\n".join(lines)
        if text.endswith("\n"):
            out += "\n"
        return out, notes
    except Exception as exc:  # never block persist on a formatting surprise
        return text, [f"本機榜單覆寫略過：{exc}"]


def fact_check_report(
    report: str,
    *,
    institutional_data: dict | None = None,
    volume_data: list | None = None,
    market_data: dict | None = None,
    value_ranking: dict | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    Full persist hook: local tables → claim check → inline corrections.
    Returns (report_to_write, summary_for_structured_data).
    """
    mode = _mode()
    text = str(report or "")
    if mode == "off" or not text:
        return text, {"status": "off" if mode == "off" else "skipped", "checked": 0, "issues": []}
    try:
        ledger = build_local_market_facts(institutional_data, volume_data, market_data)
        # 「成交值第 N 名」 claims are checked against the market-wide value ranking.
        attach_value_ranks(ledger, value_ranking, volume_data)
        # Check the *original* prose first so wrong table rows are reported too.
        summary = check_report_facts(text, ledger=ledger)
        text, table_notes = enforce_local_tables(
            text,
            institutional_data=institutional_data,
            volume_data=volume_data,
            value_ranking=value_ranking,
        )
        summary["tables"] = table_notes
        annotated = 0
        if mode == "annotate" and summary.get("issues"):
            text, annotated = annotate_report(text, summary["issues"])
        summary["annotated"] = annotated
        summary["mode"] = mode
        return text, summary
    except Exception as exc:
        return str(report or ""), {"status": "error", "checked": 0, "issues": [], "error": str(exc)[:300]}


def format_fact_check_lines(summary: dict | None, limit: int = 6) -> list[str]:
    summary = summary or {}
    lines = []
    status = summary.get("status")
    issues = summary.get("issues") or []
    lines.append(
        f"日報事實核對：{status}（核對 {summary.get('checked', 0)} 處，"
        f"不符 {len(issues)}，已標註 {summary.get('annotated', 0)}）"
    )
    for note in summary.get("tables") or []:
        lines.append(f"  - {note}")
    for issue in issues[:limit]:
        lines.append(f"  - [{issue.get('severity')}] {issue.get('claim')} → {issue.get('correction')}")
    return lines
