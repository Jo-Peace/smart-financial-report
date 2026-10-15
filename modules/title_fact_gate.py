"""
/2 title & cover fact gate (findings 43–48, 120–122, 156–159).

Published numbers kept going out wrong because /2 trusted text: the cover
verb came from keywords in the first 28 title characters (default 「買」), the
amount was the first 「億」 in the title or in a whole leaderboard list, stock
names were substring-matched (南亞 took 南亞科's amount), and nothing compared
a Gemini title with the local numbers (10/14 B: 「台積電砍 168 億卻收紅？」 while
台積電 closed 2490 → 2490, 0.00%).

This module is the single local fact source for /2:

- ``FactIndex``: per-stock facts from structured_data (``local_market_facts`` →
  ``broadcast_facts.heroes`` → ``validation_results`` → ``role_score_inputs`` →
  the local leaderboard strings), exact-name / ticker lookup and
  longest-name-first mention finding (南亞 ≠ 南亞科, 長榮 ≠ 長榮航);
- ``choose_hero``: the cover hero is the first stock with local facts named in
  title A (then B); verb and amount always come from its own facts;
- ``check_text`` / ``check_titles`` / ``check_cover``: extract every claim tied
  to a stock or the index — amounts (X億), percents, index points, flow verbs
  (買／砍／賣／狂掃…) and price-state words (收紅／收黑／平盤／守平盤／漲停／
  跌停／噴／重挫／逆勢…) — and reconcile them with the facts. Contradictions are
  ``error`` (check-youtube-package FAILs), unverifiable or exaggerated wording
  is ``warn``.

No Gemini, no network.
"""

from __future__ import annotations

import json
import math
import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(BASE_DIR, "data", "stock_db.json")

INDEX_KEY = "INDEX"
INDEX_WORDS = ("加權指數", "加權", "大盤", "台股", "指數")
# Non-stock subjects: claims after them are not about the previous stock
# (「大盤漲820點記憶體卻崩」, 「群創卻偷買這兩檔」).
OTHER_KEY = "OTHER"
OTHER_SUBJECTS = (
    "這兩檔", "這幾檔", "這檔", "兩檔", "這類", "電子權值股", "電子權值", "權值股", "權值", "記憶體", "族群", "類股", "半導體",
    "面板股", "面板", "被動元件", "航運股", "航運", "金融股", "封測", "載板", "伺服器", "散熱", "矽光子",
    "塑化", "傳產", "電子股", "AI股", "ETF",
)

GATE_BLOCK_START = "<!-- title-fact-gate:start -->"
GATE_BLOCK_END = "<!-- title-fact-gate:end -->"


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


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


def normalize_name(name) -> str:
    """「國巨*」「臻鼎-KY」「 南亞 」→「國巨」「臻鼎」「南亞」."""
    text = re.sub(r"\s+", "", str(name or ""))
    text = re.sub(r"[-－]?(KY|ＫＹ)$", "", text, flags=re.IGNORECASE)
    return text.strip("*＊")


def bare_ticker(value) -> str:
    from modules.tickers import bare_ticker as _bare

    return _bare(value)


def format_amount(amount) -> str:
    """55.33 → 「55.3億」 (magnitude; the verb carries the direction)."""
    value = _float(amount)
    if value is None:
        return ""
    return f"{abs(value):.1f}億"


def format_pct_abs(pct) -> str:
    value = _float(pct)
    if value is None:
        return ""
    return f"{abs(value):.1f}%"


def flow_verb(amount) -> str:
    value = _float(amount)
    if value is None or value == 0:
        return ""
    return "買" if value > 0 else "砍"


def stock_move_word(pct) -> str:
    """Price word a cover may print for a stock's own close-to-close change."""
    value = _float(pct)
    if value is None:
        return ""
    if value >= 9.5:
        return "漲停"
    if value <= -9.5:
        return "跌停"
    if abs(value) < 0.005:
        return "平盤"
    if value >= 3:
        return "噴"
    if value > 0:
        return "漲"
    if value <= -3:
        return "摔"
    return "跌"


def index_move_word(pct) -> str:
    """Index wording by the actual percent (finding 122): 0.59% is not 強彈."""
    value = _float(pct)
    if value is None:
        return ""
    size = abs(value)
    if size < 0.005:
        return "平盤"
    if value > 0:
        return "狂飆" if size >= 2.5 else ("大漲" if size >= 1.5 else ("上漲" if size >= 0.3 else "小漲"))
    return "重挫" if size >= 2.5 else ("大跌" if size >= 1.5 else ("下跌" if size >= 0.3 else "小跌"))


def parse_index_action(text) -> dict:
    """「上漲 284.5點（+0.59%）」 → {"points": 284.5, "pct": 0.59} (signed)."""
    blob = str(text or "")
    out = {"points": None, "pct": None}
    sign = 0
    if re.search(r"上漲|大漲|收紅|狂飆|強彈", blob):
        sign = 1
    elif re.search(r"下跌|大跌|重挫|收黑|崩", blob):
        sign = -1
    pct = re.search(r"[（(]\s*([+\-−]?\d+(?:\.\d+)?)\s*%\s*[)）]", blob)
    if pct:
        out["pct"] = float(pct.group(1).replace("−", "-"))
        if sign == 0:
            sign = 1 if out["pct"] > 0 else (-1 if out["pct"] < 0 else 0)
    points = re.search(r"([+\-−]?\d+(?:\.\d+)?)\s*點", blob)
    if points:
        value = float(points.group(1).replace("−", "-"))
        out["points"] = abs(value) * (sign or (1 if value > 0 else (-1 if value < 0 else 0)))
    if out["pct"] is None and "平盤" in blob:
        out["pct"] = 0.0
    return out


def strip_brand(title) -> str:
    """Drop the 「｜10/14 AI帶你看股市」 suffix and emoji noise before claim parsing."""
    text = str(title or "")
    text = re.sub(r"[｜|]\s*(?:\d{1,2}/\d{1,2}\s*)?AI\s*帶你看股市.*$", "", text)
    text = re.sub(r"AI\s*帶你看股市", "", text)
    return text.strip()


def _load_stock_db(path=None) -> dict:
    try:
        with open(path or DEFAULT_DB, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# Fact ledger
# ---------------------------------------------------------------------------

# stock_db names that are also everyday words; only matched when the stock is in today's facts.
_COMMON_WORD_NAMES = {"全新", "正文", "大同", "統一", "永光", "東元", "至上", "長興", "全友", "和益", "金寶", "正新", "華新", "新纖", "恩德"}


class FactIndex:
    """Per-stock local facts + exact-name mention finder."""

    def __init__(self, structured_data=None, stock_db=None):
        self.structured_data = structured_data or {}
        self.stocks: dict[str, dict] = {}
        self.names: dict[str, str] = {}
        self.extra_names: dict[str, str | None] = {}
        self.index = {"points": None, "pct": None}
        self._build(stock_db)

    # -- building ----------------------------------------------------------

    def _slot(self, ticker="", name="") -> dict | None:
        ticker = bare_ticker(ticker)
        norm = normalize_name(name)
        key = ticker or (self.names.get(norm) if norm else None) or (f"name:{norm}" if norm else "")
        if not key:
            return None
        if ticker and norm and f"name:{norm}" in self.stocks and key != f"name:{norm}":
            # a name-only row learned its ticker: merge
            old = self.stocks.pop(f"name:{norm}")
            row = self.stocks.setdefault(key, {"key": key, "ticker": ticker, "name": name})
            for k, v in old.items():
                if k not in {"key", "ticker"}:
                    row.setdefault(k, v)
            self.names[norm] = key
        row = self.stocks.setdefault(key, {"key": key, "ticker": ticker, "name": str(name or "").strip()})
        if ticker and not row.get("ticker"):
            row["ticker"] = ticker
        if name and not row.get("name"):
            row["name"] = str(name).strip()
        if norm and norm not in self.names:
            self.names[norm] = key
        return row

    @staticmethod
    def _fill(row, field, value):
        if value is not None and row.get(field) is None:
            row[field] = value

    def _build(self, stock_db):
        sd = self.structured_data
        lmf = sd.get("local_market_facts") if isinstance(sd.get("local_market_facts"), dict) else {}
        volume_complete = bool(lmf.get("volume_complete"))
        for raw in (lmf.get("stocks") or {}).values():
            if not isinstance(raw, dict):
                continue
            row = self._slot(raw.get("ticker"), raw.get("name"))
            if row is None:
                continue
            self._fill(row, "foreign_amount", _float(raw.get("foreign_amount")))
            self._fill(row, "pct_change", _float(raw.get("pct_change")))
            self._fill(row, "close", _float(raw.get("close")))
            self._fill(row, "volume_rank", _int(raw.get("volume_rank")))
            self._fill(row, "buy_rank", _int(raw.get("buy_rank")))
            self._fill(row, "sell_rank", _int(raw.get("sell_rank")))
            self._fill(row, "foreign_streak_days", _int(raw.get("foreign_streak_days")))
            if row.get("foreign_streak_days") is not None and row.get("foreign_streak_complete") is None:
                # Missing flag → a lower bound (「至少 N」), never an exact count.
                row["foreign_streak_complete"] = bool(raw.get("foreign_streak_complete", False))
            price = _float(raw.get("est_price")) or _float(raw.get("close"))
            trust = _int(raw.get("trust_net"))
            if price and trust is not None:
                self._fill(row, "trust_amount", round(trust * price / 1e8, 2))
            if row.get("volume_rank"):
                row["off_rank"] = False
            elif volume_complete and raw.get("listing") == "twse":
                row["off_rank"] = True
        facts = sd.get("broadcast_facts") if isinstance(sd.get("broadcast_facts"), dict) else {}
        for hero in facts.get("heroes") or []:
            if not isinstance(hero, dict):
                continue
            row = self._slot(hero.get("ticker"), hero.get("name"))
            if row is None:
                continue
            self._fill(row, "foreign_amount", _float(hero.get("foreign_amount")))
            self._fill(row, "pct_change", _float(hero.get("pct_change")))
            self._fill(row, "close", _float(hero.get("close")))
            self._fill(row, "volume_rank", _int(hero.get("volume_rank")))
            if row.get("off_rank") is None and hero.get("off_rank") is not None and (
                hero.get("number_source") or {}
            ).get("volume_rank") == "local":
                row["off_rank"] = bool(hero.get("off_rank"))
        for result in sd.get("validation_results") or []:
            if not isinstance(result, dict):
                continue
            row = self._slot(result.get("id"), result.get("name"))
            if row is None:
                continue
            self._fill(row, "pct_change", _float(result.get("pct_change")))
            self._fill(row, "close", _float(result.get("price")))
            self._fill(row, "volume_rank", _int(result.get("volume_rank")))
            amount = _float(result.get("institutional_est_amount"))
            if amount is not None:
                side = result.get("institutional_side")
                if side == "sell":
                    amount = -abs(amount)
                elif side == "buy":
                    amount = abs(amount)
                self._fill(row, "foreign_amount", amount)
        role_inputs = sd.get("role_score_inputs") if isinstance(sd.get("role_score_inputs"), dict) else {}
        for part in ("volume", "market"):
            table = role_inputs.get(part) if isinstance(role_inputs.get(part), dict) else {}
            for ticker, info in table.items():
                row = self.stocks.get(bare_ticker(ticker))
                if row is not None and isinstance(info, dict):
                    self._fill(row, "pct_change", _float(info.get("pct_change")))
        for key, sign in (("safe_havens_bought", 1), ("heavyweights_dumped", -1)):
            for raw in sd.get(key) or []:
                text = str(raw or "").strip()
                m = re.match(r"^(?:(\d{4,6}[A-Z]?)\s+)?([^\s(（]+)\s*[（(]\s*([+\-−]?\d+(?:\.\d+)?)\s*億", text)
                if not m:
                    continue
                row = self._slot(m.group(1) or "", m.group(2))
                if row is None:
                    continue
                value = float(m.group(3).replace("−", "-"))
                if not m.group(3).startswith(("+", "-", "−")):
                    value = abs(value) * sign
                self._fill(row, "foreign_amount", value)
        for target in sd.get("prediction_targets") or []:
            if isinstance(target, dict):
                self._slot(target.get("id") or target.get("ticker"), target.get("name"))
        # ticker-less rows: learn the ticker from stock_db by exact name
        db = stock_db if stock_db is not None else _load_stock_db()
        by_name = {}
        for sid, entry in (db or {}).items():
            if isinstance(entry, dict) and entry.get("name"):
                by_name.setdefault(normalize_name(entry["name"]), bare_ticker(sid))
        for key in [k for k in self.stocks if k.startswith("name:")]:
            norm = key[5:]
            sid = by_name.get(norm)
            if sid and sid not in self.stocks:
                row = self.stocks.pop(key)
                row["key"] = sid
                row["ticker"] = sid
                self.stocks[sid] = row
                self.names[norm] = sid
        # short forms used in titles: 日月光投控 → 日月光, 富邦金控 → 富邦金
        for norm, key in list(self.names.items()):
            alias = re.sub(r"(投控|控股)$", "", norm)
            alias = re.sub(r"金控$", "金", alias)
            if alias != norm and len(alias) >= 2 and alias not in self.names and alias not in by_name:
                self.names[alias] = key
        # Other stock_db names are known stocks without today's numbers: they stop
        # a claim from attaching to the wrong stock (南亞科 vs 南亞) and are reported
        # as 「無本機數據」 instead of silently passing.
        for norm, sid in by_name.items():
            if norm in self.names or len(norm) < 2 or norm in _COMMON_WORD_NAMES:
                continue
            self.extra_names[norm] = sid if sid in self.stocks else None
        self.index = parse_index_action(sd.get("index_action"))
        if self.index.get("pct") is None:
            for result in sd.get("validation_results") or []:
                pct = _float((result or {}).get("index_pct_change"))
                if pct is not None:
                    self.index["pct"] = pct
                    break

    # -- lookups -----------------------------------------------------------

    def fact(self, key) -> dict | None:
        if key == INDEX_KEY:
            return dict(self.index, key=INDEX_KEY, name="大盤")
        return self.stocks.get(key or "")

    def lookup(self, ticker="", name="") -> str | None:
        ticker = bare_ticker(ticker)
        if ticker and ticker in self.stocks:
            return ticker
        norm = normalize_name(name)
        if norm and norm in self.names:
            return self.names[norm]
        return None

    def label(self, key) -> str:
        row = self.fact(key) or {}
        if key == INDEX_KEY:
            return "大盤"
        return " ".join(part for part in (row.get("ticker") or "", row.get("name") or "") if part).strip() or str(key)

    def name(self, key) -> str:
        row = self.fact(key) or {}
        return row.get("name") or row.get("ticker") or ""

    def amount_owners(self, value, decimals, exclude=None) -> list[str]:
        return [
            key
            for key, row in self.stocks.items()
            if key != exclude
            and row.get("foreign_amount") is not None
            and _number_matches(value, decimals, row["foreign_amount"])
        ]

    def find_mentions(self, text) -> list[list]:
        """[[start, end, key]] — longest name first, so 南亞科 never yields 南亞."""
        text = str(text or "")
        taken = bytearray(len(text))
        spans = []
        candidates = [(n, k) for n, k in self.names.items()] + [(n, k) for n, k in self.extra_names.items()]
        for name, key in sorted(candidates, key=lambda item: -len(item[0])):
            if not name:
                continue
            for m in re.finditer(re.escape(name), text):
                start, end = m.span()
                if any(taken[start:end]):
                    continue
                suffix = re.match(r"(?:[\*＊]|[-－]?KY)", text[end:])
                if suffix:
                    end += suffix.end()
                for i in range(start, end):
                    taken[i] = 1
                spans.append([start, end, key])
        for m in re.finditer(r"(?<![\d./])(\d{4,6}[A-Z]?)(?![\d.])", text):
            start, end = m.span()
            if any(taken[start:end]) or m.group(1) not in self.stocks:
                continue
            if re.match(r"\s*(?:億|點|%|％|萬|張|元|股|年|月|日|倍)", text[end:]):
                continue  # 「3160 億」「1236點」 are numbers, not tickers (finding 159)
            for i in range(start, end):
                taken[i] = 1
            spans.append([start, end, m.group(1)])
        for words, key in ((INDEX_WORDS, INDEX_KEY), (OTHER_SUBJECTS, OTHER_KEY)):
            for word in sorted(words, key=len, reverse=True):
                for m in re.finditer(re.escape(word), text):
                    start, end = m.span()
                    if any(taken[start:end]):
                        continue
                    for i in range(start, end):
                        taken[i] = 1
                    spans.append([start, end, key])
        spans.sort()
        merged = []
        for span in spans:
            if merged and merged[-1][2] == span[2] and text[merged[-1][1]:span[0]].strip() == "":
                merged[-1][1] = span[1]
                continue
            merged.append(span)
        return merged


def build_fact_index(structured_data, stock_db=None) -> FactIndex:
    return FactIndex(structured_data, stock_db=stock_db)


# ---------------------------------------------------------------------------
# Claim tokens
# ---------------------------------------------------------------------------

_STATE_WORDS = {
    # word: (direction, strength)
    "漲停": ("up", "limit"),
    "跌停": ("down", "limit"),
    "守住平盤": ("hold", ""),
    "守平盤": ("hold", ""),
    "守平": ("hold", ""),
    "平盤": ("flat", ""),
    "收平": ("flat", ""),
    "持平": ("flat", ""),
    "收紅": ("up", ""),
    "翻紅": ("up", ""),
    "還紅": ("up", ""),
    "紅盤": ("up", ""),
    "長紅": ("up", "strong"),
    "收黑": ("down", ""),
    "翻黑": ("down", ""),
    "收綠": ("down", ""),
    "翻綠": ("down", ""),
    "長黑": ("down", "strong"),
    "狂飆": ("up", "extreme"),
    "飆漲": ("up", "extreme"),
    "暴漲": ("up", "extreme"),
    "噴出": ("up", "strong"),
    "大漲": ("up", "strong"),
    "強彈": ("up", "strong"),
    "飆": ("up", "strong"),
    "噴": ("up", "strong"),
    "上漲": ("up", ""),
    "收漲": ("up", ""),
    "收高": ("up", ""),
    "走高": ("up", ""),
    "反彈": ("up", ""),
    "還強": ("up", ""),
    "亮燈": ("up", ""),
    "漲": ("up", ""),
    "暴跌": ("down", "extreme"),
    "崩跌": ("down", "extreme"),
    "崩": ("down", "extreme"),
    "重挫": ("down", "strong"),
    "重摔": ("down", "strong"),
    "大跌": ("down", "strong"),
    "摔": ("down", "strong"),
    "下跌": ("down", ""),
    "收跌": ("down", ""),
    "收低": ("down", ""),
    "走低": ("down", ""),
    "回檔": ("down", ""),
    "跌": ("down", ""),
}

# A bare 買／賣 is a flow claim only when it is the verb: 買點／買氣／買盤／買不買／
# 該買嗎 and 賣壓／賣點 are market wording, not 「外資買了這檔」.
_BUY = (
    r"狂掃|掃貨|狂買|大買|加碼|回補|敲進|搶進|買超|買進|買回"
    r"|買(?!點|氣|盤|方|家|單|不買|嗎|得起|不起|不到)|(?:掃|砸|吸)(?=\s*\d)|吸納"
)
_SELL = (
    r"狂砍|大砍|賣超|賣出|提款|倒貨|拋售|狂拋|清倉|出貨|減碼|調節|砍"
    r"|賣(?!壓|點|盤|方|家|單|不賣|嗎|相)|(?:少|倒|抽|撤|拋)(?=\s*\d)|撤資|撤出"
)
_STATE = "|".join(sorted((re.escape(w) for w in _STATE_WORDS), key=len, reverse=True))

_TOKEN_RE = re.compile(
    rf"(?P<amount>(?P<amount_q>不到|未達|低於|不足|逾|超過|超|近|約|逼近|將近)?\s*(?P<amount_v>[+\-−]?\d+(?:\.\d+)?)\s*億(?!\s*(?:股|張|天量|量)))"
    rf"|(?P<pct>(?P<pct_q>不到|未達|低於|不足|逾|超過|超|近|約|逼近|將近)?\s*(?P<pct_v>[+\-−]?\d+(?:\.\d+)?)\s*[%％])"
    rf"|(?P<points>(?P<points_q>不到|未達|低於|不足|逾|超過|超|近|約|逼近|將近)?\s*(?P<points_v>\d+(?:\.\d+)?)\s*點)"
    rf"|(?P<voloff>沒進量榜|未進量榜|沒進榜|未進榜|量榜外|榜外|未進前\s*20|沒進前\s*20)"
    rf"|(?P<volking>爆量成交王|成交量冠軍|成交量第一|量第一|量王)"
    rf"|(?P<volrank>(?:成交量|量)第\s*(?P<volrank_v>\d+))"
    rf"|(?P<context>成交值|成交額|成交金額|營收|市值|利多|投資|訂單|資本支出|股利|現金|美元|合計|共計|加總|合共|一共|聯手|[A-Za-z]{{2,}})"
    rf"|(?P<subject>三大法人|外資|投信|自營商|自營|法人|主力|內資|散戶)"
    rf"|(?P<contra>逆勢)"
    rf"|(?P<state>{_STATE})"
    rf"|(?P<buy>{_BUY})"
    rf"|(?P<sell>{_SELL})"
)

# Questions and advice (「該買嗎」「可以買」「值得追」「能不能漲」) are not claims.
_MODAL_BEFORE = re.compile(r"(?:會|要|能|將|恐|若|想|該|可以|可|值得|敢|適合|可望|有望|明天|明日|下週|隔日)\s*$")
_NEGATION_BEFORE = re.compile(r"(?:不|沒|未|抗|止|別|防)\s*$")
# 「漲不動」「跌不下去」 deny the move; 「漲不停」「跌不休」 still claim it.
_STATE_STOP_AFTER = re.compile(r"^(?:破|幅|深|價|跌|漲|勢|不(?!停|休|止))")
_FILLER = re.compile(r"^[\s外資投信自營商狂大再又卻反手也還就竟連都要被遭偷狠]*$")
_RUN_GAP = re.compile(r"^[\s、和與及跟]*$")
_OTHER_OBJECT_AFTER = re.compile(r"^\s*(?:這|那|哪|兩檔|它|誰|其他|另|「|『)")
# 「卻反手砍電子權值」: a group noun right after the verb is the object of the flow,
# so the claim is about that group, not the stock carried over from the clause before.
_GROUP_OBJECT_AFTER = re.compile(
    r"^\s*[\u4e00-\u9fff]{0,3}?(?:" + "|".join(sorted(map(re.escape, OTHER_SUBJECTS), key=len, reverse=True)) + r")"
)
_PAST_BEFORE = re.compile(r"(?:昨天|昨日|昨|前天|上週|上集|之前)\s*$")
_GAP_TO_NEXT = re.compile(rf"^(?:[\s外資投信自營商狂大再又卻反手也還就竟連都要被遭偷狠]|{_BUY}|{_SELL})*$")
_CLAUSE_RE = re.compile(r"[^！!？?，,。；;｜|\n/]+")
# A subject-less clause continues the previous stock only when it clearly starts
# with the predicate (「股價跌3.1%」「逆勢噴3.5%」); one that starts with 外資／投信
# may have a different object, so its contradictions are only warnings.
_CARRY_STRONG = re.compile(r"^(?:股價|成交量|量能|量|卻|還|仍|竟|又|再|也|逆勢|漲|跌|噴|摔|收|翻|重挫|大漲|大跌|狂飆|[+\-−]?\d)")
_CARRY_WEAK = re.compile(r"^(?:外資|投信|自營商|自營|法人|三大法人)")
_FLOW_KINDS = {"buy", "sell", "amount", "subject", "contra"}


def _decimals(text) -> int:
    text = str(text or "")
    return len(text.split(".", 1)[1]) if "." in text else 0


def _number_matches(value, decimals, actual, qualifier="") -> bool:
    actual = abs(float(actual))
    value = abs(float(value))
    if qualifier in {"不到", "未達", "低於", "不足"}:
        return actual < value
    if qualifier in {"逾", "超過", "超"}:
        return actual > value
    if qualifier in {"近", "約", "逼近", "將近"}:
        return value * 0.85 <= actual <= value * 1.05
    if decimals <= 0:
        if value >= 100 and value % 10 == 0:
            # 「500點」「100億」: a rounded headline number (±2%)
            return abs(value - actual) <= max(1.0, value * 0.02)
        # An integer is the truncated or the rounded value, never rounded the wrong
        # way up: 55.33億 → 55 (not 56); +9.40% → 9% (「10%」 reads as limit-up,
        # which the 漲停 check already rejects below 9.5%).
        return math.floor(actual + 1e-9) <= value <= _round_half_up(actual)
    return abs(value - actual) <= 0.5 * 10 ** (-decimals) + 0.011


def _round_half_up(value) -> int:
    return int(math.floor(float(value) + 0.5 + 1e-9))


def _tokens(text, mentions):
    """Claim tokens of one clause; positions inside a stock mention are skipped."""
    blocked = bytearray(len(text))
    for start, end, _key in mentions:
        for i in range(start, end):
            blocked[i] = 1
    out = []
    for m in _TOKEN_RE.finditer(text):
        start, end = m.span()
        if any(blocked[start:end]):
            continue
        kind = m.lastgroup
        if kind in {"amount_q", "amount_v", "pct_q", "pct_v", "points_q", "points_v", "volrank_v"}:
            kind = next(k for k in ("amount", "pct", "points", "volrank") if m.group(k))
        token = {"kind": kind, "start": start, "end": end, "text": m.group(0).strip()}
        if kind in {"amount", "pct", "points"}:
            raw = m.group(f"{kind}_v")
            token["value"] = float(raw.replace("−", "-"))
            token["decimals"] = _decimals(raw.lstrip("+-−"))
            token["signed"] = raw.startswith(("+", "-", "−"))
            token["qualifier"] = m.group(f"{kind}_q") or ""
        elif kind == "volrank":
            token["value"] = int(m.group("volrank_v"))
        elif kind == "state":
            word = m.group(0)
            before = text[max(0, start - 3):start]
            if _PAST_BEFORE.search(before):
                continue
            if _STATE_STOP_AFTER.match(text[end:end + 2]) and word not in {"漲停", "跌停"}:
                continue
            if _NEGATION_BEFORE.search(before) or _MODAL_BEFORE.search(before):
                continue
            direction, strength = _STATE_WORDS[word]
            token.update(word=word, direction=direction, strength=strength)
        elif kind in {"buy", "sell"}:
            before = text[max(0, start - 2):start]
            if before.endswith(("誰在", "誰")):
                continue
            if _MODAL_BEFORE.search(text[max(0, start - 3):start]):
                continue
            if _NEGATION_BEFORE.search(text[max(0, start - 1):start]):
                continue  # 「買不買」「不賣」「沒砍」 deny the flow, they do not claim it
            if _OTHER_OBJECT_AFTER.match(text[end:]) or _GROUP_OBJECT_AFTER.match(text[end:]):
                continue  # 「卻偷買這兩檔」「反手砍電子權值」: the object is not the previous stock
            if _PAST_BEFORE.search(text[max(0, start - 3):start]):
                continue  # 「昨砍今買」: yesterday's flow is not today's claim
        out.append(token)
    return out


def _owner(token, mentions, text):
    prev = None
    nxt = None
    for start, end, key in mentions:
        if end <= token["start"]:
            prev = (start, end, key)
        elif start >= token["end"] and nxt is None:
            nxt = (start, end, key)
    if prev is None and nxt is None:
        return None
    if prev is None:
        return nxt[2]
    if nxt is None:
        return prev[2]
    gap = text[token["end"]:nxt[0]]
    if token["kind"] in _FLOW_KINDS and (_FILLER.match(gap) or _GAP_TO_NEXT.match(gap)):
        return nxt[2]
    if token["kind"] in _FLOW_KINDS and re.search(r"[、和與及]", text[prev[1]:token["start"]]):
        return nxt[2]  # 「買鴻海、84.7億買華邦電」: a new list item
    return prev[2]


def extract_claims(text, facts: FactIndex, default_key=None) -> list[dict]:
    """
    [{key, clause, tokens}] — one entry per (clause, stock or index) with every
    claim token the clause ties to it. A clause without any mention belongs to
    ``default_key`` (the cover hero) or, in a title, to the stock named in the
    previous clause (「創意買30億！股價跌3.1%」); a subject-less clause with
    「N點」 is about the index.
    """
    body = strip_brand(text)
    body = re.sub(r"\s*(?:vs|VS|Vs)\.?\s*|對比|對上", "｜", body)
    claims = []
    carry = default_key
    for clause_match in _CLAUSE_RE.finditer(body):
        clause = clause_match.group(0)
        if not clause.strip():
            continue
        mentions = facts.find_mentions(clause)
        tokens = _tokens(clause, mentions)
        clause_default = None
        carried = ""
        if not mentions:
            if any(t["kind"] == "points" for t in tokens):
                clause_default = INDEX_KEY
            elif default_key is not None:
                clause_default = default_key
            elif carry is not None:
                head = clause.strip()
                if _CARRY_STRONG.match(head):
                    clause_default, carried = carry, "strong"
                elif _CARRY_WEAK.match(head):
                    clause_default, carried = carry, "weak"
        stock_keys = [m[2] for m in mentions if m[2] not in {INDEX_KEY, OTHER_KEY, None}]
        if mentions:
            # the last named subject carries into the next subject-less clause
            carry = mentions[-1][2] if mentions[-1][2] not in {None} else carry
            if default_key is not None and not stock_keys:
                carry = default_key
        if not tokens:
            continue
        # runs of adjacent stocks (「力積電、南亞科」): an amount may be their total
        runs = []
        for mention in mentions:
            if runs and _RUN_GAP.match(clause[runs[-1][-1][1]:mention[0]]) and mention[2] not in {INDEX_KEY, OTHER_KEY}:
                runs[-1].append(mention)
            else:
                runs.append([mention])
        run_of = {}
        for run in runs:
            keys = [m[2] for m in run if m[2] not in {INDEX_KEY, OTHER_KEY, None}]
            for m in run:
                run_of[m[2]] = keys
        grouped: dict = {}
        order = []
        for token in tokens:
            key = _owner(token, mentions, clause) if mentions else clause_default
            if key is None or key == OTHER_KEY:
                continue
            if len(run_of.get(key) or []) > 1:
                token["group"] = run_of[key]
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(token)
        for key in order:
            claims.append({"key": key, "clause": clause.strip(), "tokens": grouped[key], "carried": carried})
    return claims


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def _issue(level, where, facts, key, clause, claim, fact, message):
    return {
        "level": level,
        "where": where,
        "stock": facts.label(key) if key else "",
        "clause": clause,
        "claim": claim,
        "fact": fact,
        "message": message,
    }


def _pct_fact(row) -> str:
    pct = row.get("pct_change")
    close = row.get("close")
    bits = []
    if close:
        bits.append(f"收盤 {close:g}")
    if pct is not None:
        bits.append(f"漲跌 {pct:+.2f}%")
    return "、".join(bits) or "無本機漲跌"


def _amount_fact(row) -> str:
    amount = row.get("foreign_amount")
    if amount is None:
        return "無本機外資金額"
    return f"外資 {amount:+.1f}億"


def _state_problem(word, direction, strength, pct, *, is_index=False):
    """(level, reason) or None."""
    if direction == "hold":
        return None if pct >= -0.005 else ("error", "實際收跌，不是守住平盤")
    if direction == "flat":
        limit = 0.3 if is_index else 0.05
        return None if abs(pct) < limit else ("error", "實際不是平盤")
    if direction == "up" and pct <= 0.0049:
        return ("error", "實際沒有上漲" if abs(pct) < 0.005 else "實際下跌")
    if direction == "down" and pct >= -0.0049:
        return ("error", "實際沒有下跌" if abs(pct) < 0.005 else "實際上漲")
    size = abs(pct)
    if strength == "limit":
        return None if size >= 9.5 else ("error", "實際未達漲跌停")
    if is_index:
        need = {"extreme": 2.5, "strong": 1.5}.get(strength)
        if need is None:
            return None
        if size < 1.0:
            return ("error", f"大盤只{'漲' if pct > 0 else '跌'} {size:.2f}%，用「{word}」誇大")
        if size < need:
            return ("warn", f"大盤 {pct:+.2f}% 用「{word}」偏誇大（≥{need}% 才用）")
        return None
    if strength in {"strong", "extreme"} and size < 2.0:
        return ("warn", f"漲跌只有 {pct:+.2f}%，用「{word}」偏誇大")
    return None


def _is_flow_amount_token(token, tokens) -> bool:
    """Context markers (成交值 / 營收 / AMD …) just before an amount mean it is not a flow amount."""
    for other in tokens:
        if other["kind"] == "context" and other["end"] <= token["start"] and token["start"] - other["end"] <= 8:
            return False
    return True


def _subject_before(token, tokens) -> str:
    subject = ""
    for other in tokens:
        if other["kind"] == "subject" and other["end"] <= token["start"]:
            subject = other["text"]
    return subject


def _check_claim(claim, facts: FactIndex, where) -> list[dict]:
    key = claim["key"]
    clause = claim["clause"]
    tokens = claim["tokens"]
    issues = []
    is_index = key == INDEX_KEY
    row = facts.fact(key)
    if row is None:
        if any(t["kind"] in {"amount", "pct", "state", "buy", "sell"} for t in tokens):
            issues.append(_issue("warn", where, facts, key, clause, clause, "本機無此股數據", "無本機數據可對帳"))
        return issues
    name = facts.name(key) if not is_index else "大盤"

    def _is_flow_amount(token):
        return _is_flow_amount_token(token, tokens)

    def _subject_for(token):
        return _subject_before(token, tokens)

    def _verb_for(token):
        before = [t for t in tokens if t["kind"] in {"buy", "sell"} and t["end"] <= token["start"]]
        after = [t for t in tokens if t["kind"] in {"buy", "sell"} and t["start"] >= token["end"]]
        if before:
            return before[-1]
        if after and after[0]["start"] - token["end"] <= 3:
            return after[0]
        return None

    pct = row.get("pct") if is_index else row.get("pct_change")

    for token in tokens:
        kind = token["kind"]
        if kind == "amount" and not is_index:
            if not _is_flow_amount(token):
                continue
            subject = _subject_for(token)
            verb = _verb_for(token)
            if subject in {"自營商", "自營", "主力", "內資", "散戶", "三大法人", "法人"}:
                continue
            actual = row.get("trust_amount") if subject == "投信" else row.get("foreign_amount")
            who = "投信" if subject == "投信" else "外資"
            if actual is None:
                issues.append(
                    _issue("warn", where, facts, key, clause, token["text"], f"無本機{who}金額", f"{name} 的 {token['text']} 無本機{who}金額可對帳")
                )
                continue
            group = token.get("group") or []
            group_total = None
            if len(group) > 1 and who == "外資":
                amounts = [(facts.fact(k) or {}).get("foreign_amount") for k in group]
                if all(a is not None for a in amounts) and (all(a > 0 for a in amounts) or all(a < 0 for a in amounts)):
                    group_total = sum(amounts)
            if group_total is not None and _number_matches(token["value"], token["decimals"], group_total, token["qualifier"]):
                actual = group_total
            elif not _number_matches(token["value"], token["decimals"], actual, token["qualifier"]):
                owners = facts.amount_owners(token["value"], token["decimals"], exclude=key) if who == "外資" else []
                hint = f"；{token['text']} 是 {'、'.join(facts.label(o) for o in owners)} 的金額" if owners else ""
                issues.append(
                    _issue(
                        "error",
                        where,
                        facts,
                        key,
                        clause,
                        token["text"],
                        f"{who} {actual:+.1f}億",
                        f"{name} 的{who}金額是 {format_amount(actual)}，不是 {token['text']}{hint}",
                    )
                )
            sign = 1 if (verb and verb["kind"] == "buy") else (-1 if verb else 0)
            if token["signed"]:
                sign = -1 if token["value"] < 0 else 1
            if sign and actual and (sign > 0) != (actual > 0):
                issues.append(
                    _issue(
                        "error",
                        where,
                        facts,
                        key,
                        clause,
                        (verb or {}).get("text") or token["text"],
                        f"{who} {actual:+.1f}億",
                        f"{name} 實際是{who}{'買超' if actual > 0 else '賣超'} {format_amount(actual)}，不能寫成「{(verb or {}).get('text') or token['text']}」",
                    )
                )
        elif kind in {"buy", "sell"} and not is_index:
            # verb without its own amount still states a direction
            if any(t["kind"] == "amount" and _verb_for(t) is token for t in tokens):
                continue
            subject = _subject_for(token)
            if subject not in {"", "外資"}:
                continue
            actual = row.get("foreign_amount")
            if actual is None:
                continue
            if (kind == "buy") != (actual > 0):
                issues.append(
                    _issue(
                        "error",
                        where,
                        facts,
                        key,
                        clause,
                        token["text"],
                        _amount_fact(row),
                        f"{name} 實際是外資{'買超' if actual > 0 else '賣超'} {format_amount(actual)}，不能寫「{token['text']}」",
                    )
                )
        elif kind == "state":
            if pct is None:
                issues.append(
                    _issue("warn", where, facts, key, clause, token["word"], "無本機漲跌", f"{name} 無本機漲跌，無法核對「{token['word']}」")
                )
                continue
            problem = _state_problem(token["word"], token["direction"], token["strength"], pct, is_index=is_index)
            if problem:
                level, reason = problem
                fact_text = f"大盤 {pct:+.2f}%" if is_index else _pct_fact(row)
                issues.append(
                    _issue(level, where, facts, key, clause, token["word"], fact_text, f"{name}「{token['word']}」與本機數據不符：{fact_text}（{reason}）")
                )
        elif kind == "pct":
            if pct is None:
                continue
            before = clause[max(0, token["start"] - 6):token["start"]]
            if re.search(r"(毛利|營收|年增|月增|殖利率|持股|比重|佔|機率|命中|勝率|成長|\d\s*[日週月季年]|近\s*\d)", before):
                continue
            if not _number_matches(token["value"], token["decimals"], pct, token["qualifier"]):
                fact_text = f"大盤 {pct:+.2f}%" if is_index else _pct_fact(row)
                issues.append(
                    _issue("error", where, facts, key, clause, token["text"], fact_text, f"{name} 漲跌是 {pct:+.2f}%，不是 {token['text']}")
                )
                continue
            sign = 0
            if token["signed"]:
                sign = -1 if token["value"] < 0 else 1
            else:
                states = [t for t in tokens if t["kind"] == "state" and t["end"] <= token["start"] and token["start"] - t["end"] <= 2]
                if states:
                    sign = {"up": 1, "down": -1}.get(states[-1]["direction"], 0)
            if sign and abs(pct) >= 0.005 and (sign > 0) != (pct > 0):
                issues.append(
                    _issue("error", where, facts, key, clause, token["text"], f"{pct:+.2f}%", f"{name} 漲跌方向錯：實際 {pct:+.2f}%")
                )
        elif kind == "points" and is_index:
            points = row.get("points")
            if points is None:
                continue
            if not _number_matches(token["value"], token["decimals"], points, token["qualifier"]):
                issues.append(
                    _issue("error", where, facts, key, clause, token["text"], f"大盤 {points:+.1f}點", f"大盤是 {points:+.1f} 點，不是 {token['text']}")
                )
        elif kind == "voloff" and not is_index:
            if row.get("off_rank") is False and row.get("volume_rank"):
                issues.append(
                    _issue("error", where, facts, key, clause, token["text"], f"成交量第 {row['volume_rank']} 名", f"{name} 成交量第 {row['volume_rank']} 名，不是「{token['text']}」")
                )
            elif row.get("off_rank") is None:
                issues.append(_issue("warn", where, facts, key, clause, token["text"], "量榜資料不完整", f"{name}「{token['text']}」無本機量榜可對帳"))
        elif kind in {"volking", "volrank"} and not is_index:
            want = 1 if kind == "volking" else token.get("value")
            rank = row.get("volume_rank")
            if rank and want and rank != want:
                issues.append(
                    _issue("error", where, facts, key, clause, token["text"], f"成交量第 {rank} 名", f"{name} 成交量第 {rank} 名，不是「{token['text']}」")
                )
            elif not rank and row.get("off_rank"):
                issues.append(
                    _issue("error", where, facts, key, clause, token["text"], "未進成交量榜", f"{name} 未進成交量榜，不是「{token['text']}」")
                )

    # 逆勢: the stock moved against the index
    if not is_index and any(t["kind"] == "contra" for t in tokens):
        index_pct = facts.index.get("pct")
        has_price_word = any(t["kind"] == "state" for t in tokens)
        if has_price_word and pct is not None and index_pct is not None:
            if abs(index_pct) < 0.005 or abs(pct) < 0.005 or (pct > 0) == (index_pct > 0):
                issues.append(
                    _issue(
                        "error",
                        where,
                        facts,
                        key,
                        clause,
                        "逆勢",
                        f"{name} {pct:+.2f}%／大盤 {index_pct:+.2f}%",
                        f"{name}{pct:+.2f}% 與大盤 {index_pct:+.2f}% 同向，不是「逆勢」",
                    )
                )
    return issues


def check_text(text, structured_data=None, *, where="", default_key=None, facts=None) -> list[dict]:
    facts = facts or build_fact_index(structured_data)
    issues = []
    for claim in extract_claims(text, facts, default_key=default_key):
        found = _check_claim(claim, facts, where)
        if claim.get("carried") == "weak":
            for issue in found:
                if issue["level"] == "error":
                    issue["level"] = "warn"
                    issue["message"] = f"（主詞沿用上一句，請人工確認）{issue['message']}"
        issues.extend(found)
    return issues


def check_titles(titles, structured_data=None, facts=None) -> list[dict]:
    facts = facts or build_fact_index(structured_data)
    issues = []
    for index, title in enumerate([t for t in (titles or []) if str(t or "").strip()][:2]):
        issues.extend(check_text(title, where=f"標題{'AB'[index]}", facts=facts))
    return issues


def check_cover(primary, secondary, structured_data=None, *, hero_key=None, where="封面", facts=None) -> list[dict]:
    """Cover lines are read together (「1303 南亞」+「外資買55.3億」); lines without a stock belong to the hero."""
    facts = facts or build_fact_index(structured_data)
    text = f"{primary or ''} {secondary or ''}".strip()
    return check_text(text, where=where, default_key=hero_key, facts=facts)


def has_errors(issues) -> bool:
    return any((issue or {}).get("level") == "error" for issue in issues or [])


def format_issue(issue) -> str:
    level = "FAIL" if issue.get("level") == "error" else "WARN"
    where = issue.get("where") or ""
    return f"[{level}] {where}：「{issue.get('clause')}」→ {issue.get('message')}"


# ---------------------------------------------------------------------------
# Hero (cover) selection
# ---------------------------------------------------------------------------


def hero_from_key(facts: FactIndex, key) -> dict:
    row = facts.fact(key) or {}
    if not row or key == INDEX_KEY:
        return {}
    amount = row.get("foreign_amount")
    return {
        "key": key,
        "ticker": row.get("ticker") or "",
        "name": row.get("name") or "",
        "label": facts.label(key),
        "foreign_amount": amount,
        "pct_change": row.get("pct_change"),
        "volume_rank": row.get("volume_rank"),
        "off_rank": row.get("off_rank"),
        "verb": flow_verb(amount),
        "amount": format_amount(amount) if amount not in (None, 0) else "",
    }


def _has_numbers(row) -> bool:
    return bool(row) and (row.get("foreign_amount") is not None or row.get("pct_change") is not None)


def title_stock_keys(title, facts: FactIndex) -> list[str]:
    keys = []
    for _start, _end, key in facts.find_mentions(strip_brand(title)):
        if key and key != INDEX_KEY and key not in keys:
            keys.append(key)
    return keys


def title_amount_for(title, key, facts: FactIndex) -> str:
    """
    The title's own wording of ``key``'s foreign amount (「168億」) when it
    agrees with the local fact, so the cover repeats the title's number (rule 13
    「同股同數字」) instead of a re-formatted 168.1億. "" when the title states
    no matching amount for that stock.
    """
    row = facts.fact(key) or {}
    actual = row.get("foreign_amount")
    if actual in (None, 0):
        return ""
    for claim in extract_claims(title, facts):
        if claim["key"] != key:
            continue
        tokens = claim["tokens"]
        for token in tokens:
            if token["kind"] != "amount" or token.get("qualifier") or token.get("group"):
                continue
            if not _is_flow_amount_token(token, tokens) or _subject_before(token, tokens) not in {"", "外資"}:
                continue
            if _number_matches(token["value"], token["decimals"], actual):
                return f"{abs(token['value']):.{token['decimals']}f}億"
    return ""


def choose_hero(titles, structured_data=None, facts=None) -> dict:
    """
    The cover hero: the first stock with local numbers named in title A, then B;
    otherwise the first official watch / buy-list stock. Never a stock picked
    by keyword or by the first 「億」 in a list. Verb and amount are the hero's
    own facts; when the title states that amount correctly, the cover uses the
    title's wording of it (「砍 168 億」 → 「外資砍168億」, not 168.1億).
    """
    facts = facts or build_fact_index(structured_data)
    for title in [t for t in (titles or []) if str(t or "").strip()][:2]:
        for key in title_stock_keys(title, facts):
            if _has_numbers(facts.fact(key)):
                hero = hero_from_key(facts, key)
                worded = title_amount_for(title, key, facts) if hero.get("amount") else ""
                if worded:
                    hero["amount"] = worded
                return hero
    sd = facts.structured_data
    for target in sd.get("prediction_targets") or []:
        if isinstance(target, dict):
            key = facts.lookup(target.get("id"), target.get("name"))
            if key and _has_numbers(facts.fact(key)):
                return hero_from_key(facts, key)
    for raw in sd.get("safe_havens_bought") or []:
        m = re.match(r"^(?:(\d{4,6}[A-Z]?)\s+)?([^\s(（]+)", str(raw or "").strip())
        if m:
            key = facts.lookup(m.group(1) or "", m.group(2))
            if key and _has_numbers(facts.fact(key)):
                return hero_from_key(facts, key)
    return {}


# ---------------------------------------------------------------------------
# Prompt block (Gemini sees the same facts the gate checks)
# ---------------------------------------------------------------------------


def _allowed_words(pct) -> str:
    if pct is None:
        return "無本機漲跌：標題不要寫漲跌或收紅／收黑"
    if pct >= 9.5:
        return "漲停：可寫漲停／收紅"
    if pct <= -9.5:
        return "跌停：可寫跌停／收黑"
    if abs(pct) < 0.005:
        return "平盤：只能寫平盤／守平盤，不可寫收紅／收黑／漲／跌"
    if pct > 0:
        extra = "；未達 2% 不要寫噴／大漲" if pct < 2 else ""
        return f"收漲：可寫收紅／漲，不可寫收黑／跌／漲停{extra}"
    extra = "；跌幅未達 2% 不要寫重挫／摔" if pct > -2 else ""
    return f"收跌：可寫收黑／跌，不可寫收紅／漲／跌停{extra}"


def format_price_state_block(structured_data, limit=12) -> str:
    """Per-stock 外資金額 + 收盤漲跌 + allowed price words, for the packaging prompt."""
    facts = build_fact_index(structured_data)
    keys = []
    for hero in ((structured_data or {}).get("broadcast_facts") or {}).get("heroes") or []:
        key = facts.lookup((hero or {}).get("ticker"), (hero or {}).get("name"))
        if key and key not in keys:
            keys.append(key)
    for list_key in ("safe_havens_bought", "heavyweights_dumped"):
        for raw in (structured_data or {}).get(list_key) or []:
            m = re.match(r"^(?:(\d{4,6}[A-Z]?)\s+)?([^\s(（]+)", str(raw or "").strip())
            key = facts.lookup(m.group(1) or "", m.group(2)) if m else None
            if key and key not in keys:
                keys.append(key)
    lines = []
    index_pct = facts.index.get("pct")
    if index_pct is not None:
        word = index_move_word(index_pct)
        lines.append(
            f"- 大盤：{index_pct:+.2f}%（{word}）；重挫／大跌需 ≤ -1.5%，狂飆需 ≥ +2.5%，大漲／強彈需 ≥ +1.5%"
        )
    for key in keys[:limit]:
        row = facts.fact(key) or {}
        bits = [facts.label(key)]
        if row.get("foreign_amount") is not None:
            bits.append(f"外資 {row['foreign_amount']:+.1f}億（{'買超' if row['foreign_amount'] > 0 else '賣超'}）")
        if row.get("close"):
            bits.append(f"收盤 {row['close']:g}")
        if row.get("pct_change") is not None:
            bits.append(f"漲跌 {row['pct_change']:+.2f}%")
        if row.get("volume_rank"):
            bits.append(f"成交量第 {row['volume_rank']} 名")
        elif row.get("off_rank") is True:
            bits.append("未進成交股數榜")
        if row.get("foreign_streak_days"):
            from modules.foreign_streak import format_streak_text

            streak = format_streak_text(row["foreign_streak_days"], bool(row.get("foreign_streak_complete")))
            if streak:
                bits.append(f"外資連續買超 {streak}")
        lines.append(f"- {'；'.join(bits)} → {_allowed_words(row.get('pct_change'))}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Package block
# ---------------------------------------------------------------------------


def fact_gate_block(issues) -> str:
    errors = [i for i in issues or [] if i.get("level") == "error"]
    warns = [i for i in issues or [] if i.get("level") != "error"]
    if not errors and not warns:
        return ""
    lines = [GATE_BLOCK_START]
    if errors:
        lines.append("> ⛔ 標題／封面事實對帳 FAIL：以下字句與本機數據矛盾，上傳前必須修正（系統不會自動改寫 Gemini 標題）")
    else:
        lines.append("> ⚠️ 標題／封面事實對帳提醒（無法核對或用語偏誇大）")
    for issue in errors + warns:
        lines.append(f"> - {format_issue(issue)}")
    lines.append(GATE_BLOCK_END)
    return "\n".join(lines)


def apply_fact_gate_block(package_text, issues) -> str:
    """Replace (or remove) the gate block right after the A/B title section."""
    text = str(package_text or "")
    text = re.sub(
        rf"\n*{re.escape(GATE_BLOCK_START)}[\s\S]*?{re.escape(GATE_BLOCK_END)}\n*",
        "\n\n",
        text,
    )
    block = fact_gate_block(issues)
    if not block:
        return text
    anchor = re.search(r"\*\*標題 B[^*]*\*\*\s*```[\s\S]*?```", text)
    if not anchor:
        anchor = re.search(r"\*\*標題 A[^*]*\*\*\s*```[\s\S]*?```", text)
    if anchor:
        return text[: anchor.end()] + "\n\n" + block + "\n\n" + text[anchor.end():].lstrip("\n")
    return block + "\n\n" + text


def titles_from_package(package_text) -> list[str]:
    return [
        title.strip()
        for title in re.findall(r"\*\*標題 [AB][^*]*\*\*\s*```\s*(.*?)\s*```", str(package_text or ""), flags=re.DOTALL)
        if title.strip()
    ]
