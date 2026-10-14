"""
Deterministic market signal scoring for the daily Taiwan stock report.

This module intentionally does not call any AI API. It turns the raw market
inputs already collected by main.py into a compact, auditable signal pack for
Gemini to explain instead of inventing its own ranking from long raw lists.
"""

from __future__ import annotations

import json
import os

from modules.tickers import bare_ticker


EXCLUDED_WATCH_TICKERS = {"2330", "2317", "2454"}

# Taiwan daily price limit is ±10%; tick rounding lands the limit near 9.5%+.
LIMIT_MOVE_PCT = 9.5
# Anomaly scores are 0-100 strength of the contradiction itself (not the
# stock's cumulative attention). >=55 is treated as "strong" downstream
# (editorial_frame anomaly_day, pipeline_support news expansion).
STRONG_ANOMALY_SCORE = 55
# Flow anomalies on index-rebalance / ex-div sessions are partly mechanical:
# scale their strength down and say so in the text.
MECHANICAL_FLOW_DISCOUNT = 0.7
# 「法人買超但量縮」 needs the stock's own volume clearly below its 5-day mean.
SHRINK_VOL_RATIO = 0.85


def _normalize_ticker(value):
    return bare_ticker(value)


def _float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value, default=None):
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_etf(ticker):
    return str(ticker or "").startswith("00")


def _turnover_yi(price, volume):
    """Traded value in 億元 from price × shares; None when either is missing."""
    price = _float(price, None)
    volume = _float(volume, None)
    if not price or not volume or price <= 0 or volume <= 0:
        return None
    return price * volume / 1e8


def flow_intensity(est_amount, turnover_yi):
    """Signed foreign net amount ÷ same-day traded value (both 億元)."""
    amount = _float(est_amount, None)
    turnover = _float(turnover_yi, None)
    if amount is None or not turnover or turnover <= 0:
        return None
    return amount / turnover


def market_value_ranks(value_ranking=None, volume_data=None):
    """
    Market-wide 成交值 ranks (上市個股, ETFs excluded) from the same-day tape:
    ``value_ranking["stocks"]`` (get_top_value_stocks, top 20) plus the share
    board rows' ``value_rank`` (data_fetcher._attach_trade_value, same basis).
    Returns {ticker: {"rank", "trade_value_yi"}}; empty when the tape ranking
    is unavailable.
    """
    out = {}
    stocks = (value_ranking or {}).get("stocks") if isinstance(value_ranking, dict) else None
    for row in stocks or []:
        if not isinstance(row, dict):
            continue
        ticker = _normalize_ticker(row.get("id"))
        rank = _int(row.get("rank"))
        if not ticker or _is_etf(ticker) or not rank:
            continue
        out[ticker] = {"rank": rank, "trade_value_yi": _float(row.get("trade_value_yi"), None)}
    if not out:
        return out
    for row in volume_data or []:
        if not isinstance(row, dict):
            continue
        ticker = _normalize_ticker(row.get("id"))
        rank = _int(row.get("value_rank"))
        if not ticker or _is_etf(ticker) or not rank or ticker in out:
            continue
        if row.get("value_rank_basis") not in (None, "stocks_ex_etf"):
            continue
        out[ticker] = {"rank": rank, "trade_value_yi": _float(row.get("trade_value_yi"), None)}
    return out


def rank_volume_by_turnover(volume_data, market_data=None, value_ranking=None):
    """
    Traded-value rank (close × shares, 億元; ETFs and the index excluded).

    With ``value_ranking`` (the same-day tape's 成交值前20, also shown in the /1
    prompt as 「今日成交值前20名」) the rank is the market-wide one
    (``rank_basis`` = "market", from market_value_ranks): names outside the
    known market ranks get ``turnover_rank`` None, so one prompt never carries
    two different 成交值 ranks for the same stock.

    Without it, the rank falls back to the observation pool (``rank_basis`` =
    "pool"): the TWSE share-count board (MI_INDEX20) plus every `market_data`
    symbol (2330 and the foreign buy/sell leaders, yesterday's picks). The
    share board alone misses mega caps and high-priced names (2330 was on it 0
    of 101 local sessions), so ranking only the board made 1303 「成交值第 1」
    on days 2330 traded 2.5× more. The pool is not the whole market: callers
    must label it as a pool rank (turnover_rank_text), never as a market-wide
    成交值第 N 名.

    Returns {ticker: {"turnover_rank", "share_rank" (None when off the share
    board), "turnover_yi", "on_share_board", "rank_basis", "pct_change"}}.
    """
    pool = {}
    for item in volume_data or []:
        ticker = _normalize_ticker((item or {}).get("id"))
        if not ticker or _is_etf(ticker):
            continue
        turnover = _turnover_yi(item.get("close_price"), item.get("volume"))
        if turnover is None:
            continue
        pool[ticker] = {
            "turnover": turnover,
            "share_rank": _int(item.get("rank")),
            "on_share_board": True,
            "pct_change": _float(item.get("pct_change"), None),
        }
    for symbol, data in (market_data or {}).items():
        ticker = _normalize_ticker(symbol)
        if not ticker or ticker.startswith("^") or ticker == "TWII" or _is_etf(ticker) or ticker in pool:
            continue
        if not isinstance(data, dict):
            continue
        turnover = _turnover_yi(data.get("price"), data.get("volume"))
        if turnover is None:
            continue
        pool[ticker] = {
            "turnover": turnover,
            "share_rank": None,
            "on_share_board": False,
            "pct_change": _float(data.get("pct_change"), None),
        }
    market_ranks = market_value_ranks(value_ranking, volume_data)
    if market_ranks:
        out = {}
        for ticker in sorted(set(pool) | set(market_ranks)):
            info = pool.get(ticker) or {}
            known = market_ranks.get(ticker) or {}
            turnover = known.get("trade_value_yi")
            if turnover is None:
                turnover = info.get("turnover")
            out[ticker] = {
                "turnover_rank": known.get("rank"),
                "share_rank": info.get("share_rank"),
                "turnover_yi": round(turnover, 2) if turnover is not None else None,
                "on_share_board": bool(info.get("on_share_board")),
                "rank_basis": "market",
            }
            if info.get("pct_change") is not None:
                out[ticker]["pct_change"] = info["pct_change"]
        return out

    ranked = sorted(pool.items(), key=lambda kv: (-kv[1]["turnover"], kv[0]))
    out = {}
    for idx, (ticker, info) in enumerate(ranked, start=1):
        out[ticker] = {
            "turnover_rank": idx,
            "share_rank": info["share_rank"],
            "turnover_yi": round(info["turnover"], 2),
            "on_share_board": info["on_share_board"],
            "rank_basis": "pool",
        }
        if info["pct_change"] is not None:
            out[ticker]["pct_change"] = info["pct_change"]
    return out


# What the turnover rank covers; every rank string must carry this label.
TURNOVER_POOL_LABEL = "觀察池"
TURNOVER_POOL_NOTE = (
    "「觀察池成交值第 N 名」只在「成交股數前20＋今日追蹤標的（台積電、外資買賣超前段、昨日預測）」內"
    "依成交值排名，不是全市場排名；不得寫成「全市場成交值第 N 名」或「市場關注度最高」。"
)


# Market-wide basis: the same rank as the prompt's 「今日成交值前20名」 table.
TURNOVER_MARKET_LABEL = "個股"
TURNOVER_MARKET_NOTE = (
    "「個股成交值第 N 名」與「今日成交值前20名」同一排名（上市個股、不含 ETF，同日全日收盤表）；"
    "本機沒有給出名次的個股不寫成交值名次，也不得另用觀察池或其他名單自行排名。"
)


def turnover_rank_text(rank, turnover_yi=None, basis="pool"):
    """Honest label for a turnover rank (see rank_volume_by_turnover): pool or market-wide."""
    amount = _float(turnover_yi, None)
    note = f"（約 {amount:.0f} 億）" if amount else ""
    label = TURNOVER_MARKET_LABEL if basis == "market" else TURNOVER_POOL_LABEL
    return f"{label}成交值第 {rank} 名{note}"


def _value_scope_text(row):
    """「成交值居…前段」 wording that matches the rank basis of ``row``."""
    return "成交值居上市個股前 10" if (row or {}).get("turnover_rank_basis") == "market" else "成交值居觀察池前段"


def _has_value_focus(ticker, turnover_rank):
    """
    Top-10 pool traded value as a heat signal. Index weights (2330/2317/2454)
    top any value ranking every session, so their rank is structural, not heat.
    """
    return bool(turnover_rank and int(turnover_rank) <= 10 and ticker not in EXCLUDED_WATCH_TICKERS)


def confidence_grade(confidence):
    """Uncalibrated hand-tuned confidence → coarse grade for prompts."""
    value = _float(confidence, 0.0)
    if value >= 70:
        return "強"
    if value >= 50:
        return "中"
    return "弱"


def _mechanical_note(mechanical_flow, ticker=None):
    """Labels of calendar events that make this ticker's flow partly mechanical."""
    try:
        from modules.market_calendar import mechanical_flow_for_ticker

        rows = mechanical_flow_for_ticker(mechanical_flow, ticker)
    except Exception:
        rows = []
    labels = []
    for item in rows:
        label = str(item.get("label") or item.get("kind") or "")
        if label and label not in labels:
            labels.append(label)
    return "、".join(labels[:2])


def _stock_db_label(stock_db, ticker):
    info = stock_db.get(ticker, {}) if stock_db else {}
    sector = info.get("sector") or ""
    role = info.get("supply_chain_role") or ""
    if sector and role:
        return f"{sector} / {role}"
    return sector or role or ""


def load_stock_db(base_dir):
    db_path = os.path.join(base_dir, "data", "stock_db.json")
    try:
        with open(db_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


NO_SIGNAL_LOGIC = "訊號不足，暫無明確主力操作假說"


def _infer_operator_logic(row):
    """
    Infer a next-day operator hypothesis from observable evidence.

    The output is a hypothesis, not a fact. It is designed to be verified the
    next trading day by price, volume, and institutional follow-through.
    `confidence` is a hand-tuned heuristic (not calibrated against hit rates);
    prompts should show `confidence_grade` instead of the raw number.
    """
    ticker = row.get("ticker", "")
    name = row.get("name", "")
    label = f"{ticker} {name}".strip()
    pct = _float(row.get("pct_change", row.get("volume_pct_change")))
    vol_ratio_known = row.get("vol_ratio") is not None
    vol_ratio = _float(row.get("vol_ratio"), 1.0)
    price = _float(row.get("price"))
    ma20 = _float(row.get("ma20"))
    has_buy = bool(row.get("foreign_buy_rank"))
    has_sell = bool(row.get("foreign_sell_rank"))
    turnover_rank = row.get("turnover_rank")
    has_top_volume = _has_value_focus(ticker, turnover_rank)
    limit_up = bool(row.get("limit_up"))
    confidence = 35
    evidence = []

    basis = row.get("turnover_rank_basis") or "pool"
    scope = _value_scope_text(row)
    # Index weights' value rank is structural (see _has_value_focus): context only.
    if turnover_rank and ticker not in EXCLUDED_WATCH_TICKERS:
        confidence += max(0, 16 - int(turnover_rank))
        evidence.append(turnover_rank_text(turnover_rank, basis=basis))
    if vol_ratio_known and vol_ratio >= 2:
        confidence += 16
        evidence.append(f"量增比 {vol_ratio}x")
    elif vol_ratio_known and vol_ratio >= 1.5:
        confidence += 8
        evidence.append(f"量增比 {vol_ratio}x")
    if has_buy:
        confidence += max(0, 14 - int(row["foreign_buy_rank"]))
        evidence.append(f"外資買超第 {row['foreign_buy_rank']} 名")
    if has_sell:
        confidence += max(0, 12 - int(row["foreign_sell_rank"]))
        evidence.append(f"外資賣超第 {row['foreign_sell_rank']} 名")
    if price and ma20 and price >= ma20:
        confidence += 6
        evidence.append("收盤站上 MA20")
    if limit_up:
        evidence.append(f"漲停 {pct:+.2f}%")

    if limit_up and has_buy:
        if vol_ratio_known and vol_ratio < 1.0:
            logic = "漲停鎖量：買盤排隊未成交，量縮是鎖死不是沒人買，不屬低調吸籌"
            next_day_bias = "偏多但追價風險高"
            confirmation = "隔日開高續鎖，或打開後量增仍守住今日漲停價"
            invalidation = "隔日打開漲停後爆量收黑，代表鎖單鬆動"
        else:
            logic = "漲停且外資同步買超，屬已表態的攻擊盤，不是低調吸籌"
            next_day_bias = "偏多但高風險"
            confirmation = "隔日量縮守住今日漲停價附近"
            invalidation = "隔日爆量收黑、跌破今日開盤價"
        confidence += 8
    elif has_sell and pct >= 2 and has_top_volume:
        logic = "外資倒貨但市場強承接，疑似本土主力或短線資金接手"
        next_day_bias = "偏多但高風險"
        confirmation = "隔日量縮不破今日低點，或開高後仍守住 MA5/MA20"
        invalidation = "隔日爆量收黑、跌破今日低點，代表承接失敗轉為出貨"
        confidence += 10
    elif has_sell and pct <= -2:
        logic = "法人賣壓與價格同向，主力方向偏向撤退"
        next_day_bias = "偏空"
        confirmation = "隔日反彈無量且仍站不回 MA5"
        invalidation = "隔日放量收復 MA5，代表賣壓被承接"
        confidence += 8
    elif has_buy and has_top_volume and pct > 0:
        logic = f"外資買超且{scope}，主力有延續攻擊意圖"
        next_day_bias = "偏多"
        confirmation = "隔日續量或量縮守高，收盤不跌破 MA5"
        invalidation = "隔日開高走低且外資轉賣，代表追價失敗"
        confidence += 12
    elif has_buy and vol_ratio_known and vol_ratio < SHRINK_VOL_RATIO:
        logic = "外資先買但今日量低於近 5 日均量，可能是主力提前卡位"
        next_day_bias = "觀察偏多"
        confirmation = "隔日量增比升至 1.5x 以上且外資續買"
        invalidation = "隔日無量下跌或跌破 MA20，代表買超未擴散"
        confidence += 4
    elif has_buy and pct < 0:
        # A foreign buy leader that closes down is not 「調節」: the buy is real,
        # the price has not agreed yet.
        logic = "外資買超但股價走弱，買盤尚未獲得價格認同"
        next_day_bias = "中性"
        confirmation = "隔日外資續買且收盤站回今日開盤價之上"
        invalidation = "隔日外資轉賣或續跌破 MA20，代表買超未能撐住價格"
    elif has_top_volume and not has_buy and not has_sell and pct > 0:
        logic = f"{scope}，但三大法人尚未表態，偏短線換手盤"
        next_day_bias = "中性偏多"
        confirmation = "隔日法人買超跟上，且股價守住今日收盤附近"
        invalidation = "隔日爆量不漲或收黑，代表短線籌碼鬆動"
    elif has_top_volume and not has_buy and pct < 0:
        logic = f"{scope}但價格走弱，主力偏向調節或換手失敗"
        next_day_bias = "偏空"
        confirmation = "隔日反彈量縮、收盤仍弱於 MA5"
        invalidation = "隔日放量收復今日跌幅一半以上"
    else:
        logic = NO_SIGNAL_LOGIC
        next_day_bias = "中性"
        confirmation = "隔日出現法人買賣超或成交量放大後再判斷"
        invalidation = "無"
        confidence = min(confidence, 45)

    confidence = max(0, min(95, round(confidence)))
    return {
        "ticker": ticker,
        "name": name,
        "label": label,
        "logic": logic,
        "next_day_bias": next_day_bias,
        "confidence": confidence,
        "confidence_grade": confidence_grade(confidence),
        "confidence_calibrated": False,
        "evidence": evidence[:5],
        "confirmation": confirmation,
        "invalidation": invalidation,
    }


def _pick_operator_hypotheses(candidates, limit=5):
    """
    Top `limit` hypotheses in attention order, informative ones first: a
    「訊號不足」 fallback row only fills a slot no informative row wants, so an
    index weight on the sell list cannot push a real hypothesis out.
    """
    logic_rows = [row["operator_logic"] for row in candidates if row.get("operator_logic")]
    informative = [h for h in logic_rows if h.get("logic") != NO_SIGNAL_LOGIC]
    fallback = [h for h in logic_rows if h.get("logic") == NO_SIGNAL_LOGIC]
    return (informative + fallback)[:limit]


def _flow_strength(row, side):
    """
    0-30 strength of one side's foreign flow.

    Prefers turnover-normalized intensity (|foreign net| ÷ same-day traded
    value: 10% → 15, 20%+ → 30) so mega caps do not win on absolute 億元;
    falls back to the amount rank when the turnover is unknown.
    """
    intensity = row.get("foreign_intensity")
    rank = row.get(f"foreign_{side}_rank")
    if intensity is not None:
        share = abs(float(intensity))
        return min(30.0, share * 150.0), f"占成交值 {share * 100:.1f}%"
    if rank:
        return max(0.0, 30.0 - (int(rank) - 1) * 4.0), ""
    return 0.0, ""


def build_market_signal_pack(
    market_data,
    institutional_data=None,
    volume_data=None,
    stock_db=None,
    max_candidates=8,
    mechanical_flow=None,
    value_ranking=None,
):
    """
    Build a deterministic signal pack from today's raw market inputs.

    Scoring goals:
    - Detect the most YouTube-worthy anomalies: volume vs foreign flow conflict,
      broad market attention, and technical confirmation.
    - Produce stricter watch-list candidates while excluding mega caps that the
      prompt already forbids as "AI data picks".

    Units: the TWSE volume board ranks by shares, the foreign board by 億元.
    Volume is re-ranked by traded value: the market-wide 成交值 rank from
    ``value_ranking`` when given (same rank as the prompt's 成交值前20 table),
    else over share board ∪ market_data (ETFs excluded) labelled as a pool
    rank (觀察池), and
    "法人買超但量縮" requires the stock's own vol_ratio < 0.85 — being absent
    from the share-count top 20 is not evidence of low volume. Index weights'
    value rank is structural, so it never counts as heat (_has_value_focus).

    Row `score` is attention (both buy and sell add); `watch_score` is
    direction-aware. Anomaly `score` is the strength of the contradiction.
    `mechanical_flow` (market_calendar.mechanical_flow_events) down-weights
    flow anomalies on index-rebalance / settlement / ex-div sessions.
    """
    institutional_data = institutional_data or {}
    volume_data = volume_data or []
    stock_db = stock_db or {}
    mechanical_flow = [item for item in (mechanical_flow or []) if isinstance(item, dict)]

    rows = {}

    def ensure(ticker, name=""):
        ticker = _normalize_ticker(ticker)
        if not ticker:
            return None
        if ticker not in rows:
            rows[ticker] = {
                "ticker": ticker,
                "name": name or stock_db.get(ticker, {}).get("name", ""),
                "score": 0,
                "watch_score": 0,
                "signals": [],
                "risks": [],
            }
        elif name and not rows[ticker].get("name"):
            rows[ticker]["name"] = name
        return rows[ticker]

    for symbol, data in (market_data or {}).items():
        ticker = _normalize_ticker(symbol)
        if ticker in {"^TWII", "TWII"}:
            continue
        row = ensure(ticker)
        if not row or not data:
            continue
        row["price"] = data.get("price")
        row["pct_change"] = _float(data.get("pct_change"))
        vol_ratio = _float(data.get("vol_ratio"), None)
        if vol_ratio is not None:
            row["vol_ratio"] = vol_ratio
        row["ma5"] = data.get("ma5")
        row["ma20"] = data.get("ma20")
        row["rsi"] = data.get("rsi")
        turnover = _turnover_yi(data.get("price"), data.get("volume"))
        if turnover is not None:
            row["turnover_yi"] = round(turnover, 2)

        if vol_ratio is not None and vol_ratio >= 2:
            row["score"] += 24
            row["watch_score"] += 24
            row["signals"].append(f"量增比 {vol_ratio}x，屬爆量訊號")
        elif vol_ratio is not None and vol_ratio >= 1.5:
            row["score"] += 12
            row["watch_score"] += 12
            row["signals"].append(f"量增比 {vol_ratio}x，量能放大")

        if row["pct_change"] >= LIMIT_MOVE_PCT:
            row["limit_up"] = True
            row["signals"].append(f"漲停 {row['pct_change']:+.2f}%")
        elif row["pct_change"] <= -LIMIT_MOVE_PCT:
            row["limit_down"] = True
            row["risks"].append(f"跌停 {row['pct_change']:+.2f}%")

        price = _float(data.get("price"))
        ma20 = _float(data.get("ma20"))
        ma5 = _float(data.get("ma5"))
        if price and ma20:
            if price >= ma20:
                row["score"] += 8
                row["watch_score"] += 8
                row["signals"].append("收盤站上 MA20")
            else:
                row["risks"].append("收盤仍在 MA20 下方")
        if price and ma5 and price >= ma5:
            row["watch_score"] += 4

        rsi = _float(data.get("rsi"), None)
        if rsi is not None:
            if rsi >= 75:
                row["risks"].append(f"RSI {rsi} 偏熱，追價風險升高")
                row["watch_score"] -= 10
            elif 45 <= rsi <= 68:
                row["watch_score"] += 6

    for item in volume_data:
        row = ensure(item.get("id"), item.get("name", ""))
        if not row:
            continue
        row["share_volume_rank"] = _int(item.get("rank"))
        row["volume"] = item.get("volume")
        row["volume_pct_change"] = _float(item.get("pct_change"))
        if "pct_change" not in row:
            row["pct_change"] = row["volume_pct_change"]
            if row["pct_change"] >= LIMIT_MOVE_PCT:
                row["limit_up"] = True
                row["signals"].append(f"漲停 {row['pct_change']:+.2f}%")

    # Value rank over share board ∪ market_data (ETFs / index excluded); shares
    # alone are not comparable, so ETF rows and rows without a close get no credit.
    turnover_ranks = rank_volume_by_turnover(volume_data, market_data, value_ranking=value_ranking)
    rank_basis = next((info.get("rank_basis") for info in turnover_ranks.values()), None)
    for ticker, info in turnover_ranks.items():
        row = rows.get(ticker)
        if not row:
            continue
        if info.get("turnover_yi") is not None:
            row.setdefault("turnover_yi", info["turnover_yi"])
        rank = info.get("turnover_rank")
        if not rank:
            continue  # market basis: outside the known 成交值 ranks → no rank claim
        row["turnover_rank"] = rank
        row["turnover_rank_basis"] = info.get("rank_basis") or "pool"
        # Backward-compatible key; value-based rank with ETFs excluded.
        row["volume_rank"] = rank
        if ticker in EXCLUDED_WATCH_TICKERS:
            # Index weights top any value ranking every session: keep the rank
            # as context, never as attention / direction points (_has_value_focus).
            continue
        row["score"] += max(0, 24 - rank)
        row["watch_score"] += max(0, 14 - rank)
        if rank <= 10:
            row["signals"].append(turnover_rank_text(rank, info["turnover_yi"], basis=row["turnover_rank_basis"]))

    for side, key in (("buy", "top_buy"), ("sell", "top_sell")):
        for rank, item in enumerate(institutional_data.get(key, []) or [], start=1):
            row = ensure(item.get("id"), item.get("name", ""))
            if not row:
                continue
            est_amount = _float(item.get("est_amount"))
            foreign_net = _int(item.get("foreign_net"), 0)
            if side == "buy":
                row["foreign_buy_rank"] = rank
                row["foreign_est_amount"] = est_amount
                flow_points = max(0, 24 - rank * 2)
                row["score"] += flow_points
                row["watch_score"] += flow_points
                row["signals"].append(f"外資買超第 {rank} 名，估計 {est_amount:+.1f} 億")
                if foreign_net and foreign_net > 0:
                    row["foreign_net"] = foreign_net
            else:
                row["foreign_sell_rank"] = rank
                row["foreign_est_amount"] = est_amount
                row["score"] += max(0, 18 - rank)
                row["watch_score"] -= max(4, 14 - rank)
                row["signals"].append(f"外資賣超第 {rank} 名，估計 {est_amount:+.1f} 億")
                row["foreign_net"] = foreign_net
            intensity = flow_intensity(est_amount, row.get("turnover_yi"))
            if intensity is not None:
                row["foreign_intensity"] = round(intensity, 4)

    anomalies = []
    candidates = []

    def _add_anomaly(kind, row, text, strength, flow_based):
        strength = float(strength)
        anomaly = {"type": kind, "ticker": row["ticker"], "text": text}
        mech_note = _mechanical_note(mechanical_flow, row["ticker"]) if flow_based else ""
        if mech_note:
            strength *= MECHANICAL_FLOW_DISCOUNT
            anomaly["text"] = f"{text}（今日{mech_note}：外資流量可能含被動調整）"
            anomaly["mechanical_flow"] = True
        anomaly["score"] = round(max(0.0, min(100.0, strength)), 1)
        anomalies.append(anomaly)

    for ticker, row in rows.items():
        pct = _float(row.get("pct_change", row.get("volume_pct_change")))
        turnover_rank = row.get("turnover_rank")
        has_volume_focus = _has_value_focus(ticker, turnover_rank)
        has_buy = row.get("foreign_buy_rank")
        has_sell = row.get("foreign_sell_rank")
        vol_ratio = row.get("vol_ratio")
        label = f"{ticker} {row.get('name', '')}".strip()
        amount = _float(row.get("foreign_est_amount"))

        if has_sell and pct >= 2:
            flow_pts, flow_note = _flow_strength(row, "sell")
            strength = 20 + min(30.0, pct * 4) + flow_pts
            if vol_ratio is not None and vol_ratio >= 1.5:
                strength += 10
            note = f"，{flow_note}" if flow_note else ""
            text = f"{label}: 外資賣超第 {has_sell} 名（{amount:+.1f} 億{note}），但股價 {pct:+.2f}%"
            row["signals"].append("法人賣、市場買的籌碼背離")
            row["risks"].append("外資反向調節，需防隔日換手失敗")
            row["score"] += 22
            _add_anomaly("法人賣超但股價強", row, text, strength, True)

        if has_buy and row.get("limit_up"):
            if vol_ratio is not None and vol_ratio < 1.0:
                flow_pts, _ = _flow_strength(row, "buy")
                text = (
                    f"{label}: 漲停 {pct:+.2f}% 且量增比僅 {vol_ratio:.2f}x，"
                    f"外資買超第 {has_buy} 名（{amount:+.1f} 億）；量縮是鎖死，不是吸籌"
                )
                row["signals"].append("漲停鎖量")
                _add_anomaly("漲停鎖量", row, text, 30 + flow_pts * 0.5, True)
            else:
                row["signals"].append("漲停且外資同步買超（已表態，非低調吸籌）")
        elif has_buy and vol_ratio is not None and vol_ratio < SHRINK_VOL_RATIO:
            # Own volume vs its 5-day mean decides 量縮; a pool value rank says nothing about it.
            flow_pts, flow_note = _flow_strength(row, "buy")
            strength = 10 + flow_pts + min(30.0, (1.0 - float(vol_ratio)) * 60)
            note = f"，{flow_note}" if flow_note else ""
            text = (
                f"{label}: 外資買超第 {has_buy} 名（{amount:+.1f} 億{note}），"
                f"但量增比僅 {vol_ratio:.2f}x（低於近 5 日均量）"
            )
            row["signals"].append("法人買超但個股量縮，尚未擴散成市場共識")
            row["score"] += 10
            _add_anomaly("法人買超但量縮", row, text, strength, True)

        if has_volume_focus and not has_buy and not has_sell:
            rank_text = turnover_rank_text(turnover_rank, row.get("turnover_yi"), basis=row.get("turnover_rank_basis") or "pool")
            text = f"{label}: {rank_text}，但外資買賣超榜未同步"
            row["signals"].append(f"{_value_scope_text(row)}但法人方向不明")
            row["score"] += 10
            strength = 15 + (11 - int(turnover_rank)) * 2.5 + min(15.0, abs(pct) * 3)
            _add_anomaly("成交量熱但法人缺席", row, text, strength, False)

        row["operator_logic"] = _infer_operator_logic(row)

        label_text = _stock_db_label(stock_db, ticker)
        if label_text:
            row["industry_label"] = label_text
        row["score"] = round(row["score"], 1)
        row["watch_score"] = round(row["watch_score"], 1)
        row["attention_score"] = row["score"]
        row["direction_score"] = row["watch_score"]
        candidates.append(row)

    candidates = sorted(candidates, key=lambda x: (-x["score"], x["ticker"]))
    watch_candidates = [
        row for row in candidates
        if row["ticker"] not in EXCLUDED_WATCH_TICKERS
        and row["watch_score"] >= 28
        and not row.get("foreign_sell_rank")
    ]
    watch_candidates = sorted(watch_candidates, key=lambda x: (-x["watch_score"], x["ticker"]))

    anomalies = sorted(anomalies, key=lambda x: (-x["score"], x["ticker"], x["type"]))
    strongest_anomaly = anomalies[0] if anomalies else None

    return {
        "strongest_anomaly": strongest_anomaly,
        "anomalies": anomalies[:5],
        "top_candidates": candidates[:max_candidates],
        "operator_hypotheses": _pick_operator_hypotheses(candidates),
        "watch_candidates": watch_candidates[:3],
        "excluded_watch_tickers": sorted(EXCLUDED_WATCH_TICKERS),
        "mechanical_flow": mechanical_flow,
        "turnover_rank_basis": rank_basis,
    }


def format_signal_pack_for_prompt(signal_pack):
    """Render the deterministic signal pack into a compact prompt section."""
    if not signal_pack:
        return "（尚未建立本地量化訊號包）"

    lines = []
    strongest = signal_pack.get("strongest_anomaly")
    if strongest:
        lines.append(f"## 本地演算法判定的今日可用訊號")
        lines.append(
            f"- {strongest['type']}：{strongest['text']}"
            f"（背離強度 {strongest['score']}/100；≥{STRONG_ANOMALY_SCORE} 才算強訊號）"
        )
    else:
        lines.append("## 本地演算法判定的今日可用訊號")
        lines.append("- 未偵測到明確法人/成交量背離。今日應視為整理日或觀察日，不要硬包裝成警報。")

    lines.append("\n## 主軸使用提醒")
    lines.append("- 只有訊號同時具備具體數字與反直覺證據時，才可稱為異常日或警訊。")
    lines.append("- 若只是一般外資買賣或常態類股移動，請寫成具體買賣行為，不要升級成資金輪動、退潮或續航。")
    lines.append("- 若無強訊號，今日唯一結論應偏向等待確認與明日驗收條件。")
    lines.append("- 「成交量前20」是依成交股數排名，被 ETF 與低價股佔據；不得用「未進成交量前20」推論高價股沒有量或在吸籌。")
    if signal_pack.get("turnover_rank_basis") == "market":
        lines.append(f"- {TURNOVER_MARKET_NOTE}")
    else:
        lines.append(f"- {TURNOVER_POOL_NOTE}")
    lines.append("- 漲停股的量縮是鎖死，不是低調吸籌。")
    mech = signal_pack.get("mechanical_flow") or []
    if mech:
        labels = "、".join(str(item.get("label") or item.get("kind")) for item in mech[:3])
        lines.append(f"- 今日日曆：{labels}。外資大額買賣先寫機械性（被動調整／轉倉／除息缺口）可能，再寫主力意圖。")

    lines.append("\n## 異常訊號候選")
    anomalies = signal_pack.get("anomalies") or []
    if anomalies:
        for item in anomalies:
            lines.append(f"- {item['type']}｜{item['text']}")
    else:
        lines.append("- 無明確異常訊號。")

    lines.append("\n## 關注度前段班（關注度＝買賣超與量能都加分，不代表多空方向；方向看觀察分）")
    for row in signal_pack.get("top_candidates", [])[:8]:
        name = row.get("name") or ""
        label = f"｜{row['industry_label']}" if row.get("industry_label") else ""
        signals = "；".join(row.get("signals", [])[:3]) or "無主要訊號"
        risks = "；風險：" + "；".join(row.get("risks", [])[:2]) if row.get("risks") else ""
        lines.append(
            f"- {row['ticker']} {name}{label}：關注度 {row['score']}，方向觀察分 {row['watch_score']}。"
            f"{signals}{risks}"
        )

    lines.append(
        "\n## 主力操作假說與隔日方向（需驗收，不可當成事實；"
        "信心等級為本地規則、未經回測校準：弱<50／中50-69／強≥70）"
    )
    for item in signal_pack.get("operator_hypotheses", [])[:5]:
        evidence = "、".join(item.get("evidence", [])) or "證據不足"
        grade = item.get("confidence_grade") or confidence_grade(item.get("confidence"))
        lines.append(
            f"- {item['label']}：{item['logic']}；隔日偏向：{item['next_day_bias']}；"
            f"信心等級 {grade}（未校準）；證據：{evidence}；"
            f"確認：{item['confirmation']}；反證：{item['invalidation']}"
        )

    lines.append("\n## 明日觀察候選（已排除超級權值股）")
    watch = signal_pack.get("watch_candidates") or []
    if watch:
        for row in watch:
            name = row.get("name") or ""
            signals = "；".join(row.get("signals", [])[:3])
            lines.append(f"- {row['ticker']} {name}：觀察分 {row['watch_score']}。{signals}")
    else:
        lines.append("- 無標的達到本地觀察門檻；若 AI 報告仍要推薦，必須明確說明原因與風險。")

    return "\n".join(lines)
