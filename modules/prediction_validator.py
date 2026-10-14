"""
Deterministic prediction validation for daily report continuity.

Gemini can explain why a prediction worked or failed, but the official
validation list should come from local market data so every previous
prediction target is checked exactly once.

This module also owns the single hit rule shared by the on-air validator,
``analytics_store`` (1/3/5/20 session ledger) and ``stock_db`` tracking:

- returns are compared with the *same-window* TWII return (excess return);
- a call is a hit only when its direction-adjusted excess clears a dead band,
  a miss only when it falls below minus the dead band, otherwise ``neutral``
  (not counted in hit-rate denominators);
- ``raw_hit`` keeps the plain direction-only sign for baseline comparisons.
"""

from __future__ import annotations

import math

from modules.tickers import bare_ticker

# Dead band (percentage points of excess return) for a 1-session window.
# Wider windows scale with sqrt(sessions): 1D 0.50 / 3D 0.87 / 5D 1.12 / 20D 2.24.
DEAD_BAND_1D_PCT = 0.5


def _normalize_ticker(value):
    # Suffix-only (".TWO" before ".TW"), otherwise "6488.TWO" becomes "6488O".
    return bare_ticker(value)


def _float(value, default=None):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _market_row(market_data, ticker):
    ticker = _normalize_ticker(ticker)
    keys = [ticker, f"{ticker}.TW", f"{ticker}.TWO"]
    for key in keys:
        if key in market_data and market_data[key]:
            return market_data[key]
    return None


def _volume_row(volume_data, ticker):
    ticker = _normalize_ticker(ticker)
    for row in volume_data or []:
        if _normalize_ticker(row.get("id")) == ticker:
            return row
    return None


def _institutional_row(institutional_data, ticker):
    ticker = _normalize_ticker(ticker)
    for side, rows in (("buy", institutional_data.get("top_buy", [])), ("sell", institutional_data.get("top_sell", []))):
        for rank, row in enumerate(rows or [], start=1):
            if _normalize_ticker(row.get("id")) == ticker:
                return {
                    "side": side,
                    "rank": rank,
                    "foreign_net": row.get("foreign_net"),
                    "est_amount": row.get("est_amount"),
                }
    return None


def direction_sign(direction):
    """
    +1 long (多 / 偏多 / empty default), -1 short (空 / 偏空),
    0 non-directional (觀察 / 中性 / mixed) — not a call, never scored.
    """
    text = str(direction or "").strip()
    if not text:
        return 1
    long_word, short_word = "多" in text, "空" in text
    if short_word and not long_word:
        return -1
    if long_word and not short_word:
        return 1
    return 0


def dead_band_pct(horizon_sessions=1):
    try:
        sessions = max(1, int(horizon_sessions or 1))
    except (TypeError, ValueError):
        sessions = 1
    return round(DEAD_BAND_1D_PCT * math.sqrt(sessions), 2)


def classify_hit(
    direction,
    return_pct,
    index_return_pct=None,
    horizon_sessions=1,
    *,
    allow_raw_fallback=False,
    stopped_out=False,
):
    """
    Single hit rule (see module docstring).

    horizon_sessions sizes the dead band: pass the sessions the trade actually
    lasted (a stop-loss exit on session 2 of a 20D window uses the 2-session
    band). stopped_out=True marks a stop-loss exit: when the direction-adjusted
    raw return is <= 0 the call is a miss whatever the index did (the viewer
    following the rule exited at a loss), so stopped losers never fall into
    the neutral band and out of the denominator.

    Returns dict(outcome, hit, raw_hit, excess_pct, dead_band_pct, basis):
      outcome: hit | miss | neutral | unknown
      hit: 1 / 0 / None (None = neutral or not judgeable; never in denominators)
      raw_hit: 1 / 0 / None  (direction-adjusted raw return > 0)
      basis: excess | raw | stop (raw only when allow_raw_fallback and index
             missing; stop when a stopped-out loss decided the miss)
    """
    band = dead_band_pct(horizon_sessions)
    ret = _float(return_pct)
    idx = _float(index_return_pct)
    out = {
        "outcome": "unknown",
        "hit": None,
        "raw_hit": None,
        "excess_pct": None,
        "dead_band_pct": band,
        "basis": "excess",
    }
    sign = direction_sign(direction)
    if ret is None or sign == 0:
        return out
    out["raw_hit"] = 1 if sign * ret > 0 else 0
    if idx is not None:
        out["excess_pct"] = ret - idx
    if stopped_out and out["raw_hit"] == 0:
        out["outcome"], out["hit"], out["basis"] = "miss", 0, "stop"
        return out
    if idx is not None:
        signed = sign * out["excess_pct"]
    elif allow_raw_fallback:
        out["basis"] = "raw"
        signed = sign * ret
    else:
        return out
    if signed > band:
        out["outcome"], out["hit"] = "hit", 1
    elif signed < -band:
        out["outcome"], out["hit"] = "miss", 0
    else:
        out["outcome"] = "neutral"
    return out


def _index_pct_change(market_data, trade_date=None):
    for key in ("^TWII", "TWII"):
        row = (market_data or {}).get(key)
        if row:
            bar_date = str(row.get("bar_date") or "")[:10]
            if trade_date and bar_date and bar_date != trade_date:
                # A stale index bar is not this session's benchmark.
                return None
            return _float(row.get("pct_change"))
    return None


def _stop_loss_triggered(direction, price, stop_loss_price):
    if price is None or stop_loss_price is None:
        return None
    if direction_sign(direction) < 0:
        return price >= stop_loss_price
    return price <= stop_loss_price


def _stop_on_losing_side(direction, stop_loss_price, price, pct_change):
    """
    Same validity rule as the ledger (analytics_store._valid_stop): a stop
    counts only on the losing side of the entry (prior close, derived from
    today's close and pct_change); a stop on the wrong side would 'stop out'
    instantly and is ignored for the hit rule.
    """
    if stop_loss_price is None or stop_loss_price <= 0 or price is None or pct_change is None:
        return False
    if pct_change <= -100:
        return False
    base = price / (1 + pct_change / 100)
    if direction_sign(direction) < 0:
        return stop_loss_price > base
    return stop_loss_price < base


def _result_label(hit, outcome=None):
    if outcome == "neutral":
        return "與大盤同步（不計成敗）"
    if hit is None:
        return "無法驗收"
    return "預測成立" if hit else "預測未成立"


def validate_prediction_targets(previous_targets, market_data, institutional_data=None, volume_data=None):
    """Return official validation rows for prior structured prediction targets."""
    institutional_data = institutional_data or {}
    trade_date = str(institutional_data.get("data_date") or "")[:10]
    index_pct = _index_pct_change(market_data, trade_date=trade_date)
    results = []

    for target in previous_targets or []:
        ticker = _normalize_ticker(target.get("id"))
        if not ticker:
            continue

        market = _market_row(market_data or {}, ticker)
        stale_bar = None
        if market and trade_date:
            bar_date = str(market.get("bar_date") or "")[:10]
            if bar_date and bar_date != trade_date:
                # Previous session's move must not be scored as today's 驗收.
                stale_bar = bar_date
                market = None
        volume = _volume_row(volume_data or [], ticker)
        inst = _institutional_row(institutional_data, ticker)

        price = _float(market.get("price") if market else None)
        pct_change = _float(market.get("pct_change") if market else None)
        direction = target.get("direction", "多")
        stop_loss_price = _float(target.get("stop_loss_price"))
        stop_loss_hit = _stop_loss_triggered(direction, price, stop_loss_price)
        # Same rule as the ledger: a close through a valid stop is a stop-loss
        # exit, and a stopped loser is a miss, never neutral (classify_hit).
        stopped_out = stop_loss_hit is True and _stop_on_losing_side(direction, stop_loss_price, price, pct_change)
        verdict = classify_hit(
            direction, pct_change, index_pct, 1, allow_raw_fallback=True, stopped_out=stopped_out
        )
        hit = None if verdict["hit"] is None else bool(verdict["hit"])

        evidence = []
        if price is not None and pct_change is not None:
            line = f"收盤 {price:g}，漲跌 {pct_change:+.2f}%"
            if verdict["excess_pct"] is not None:
                line += f"（大盤 {index_pct:+.2f}%，超額 {verdict['excess_pct']:+.2f}%）"
            elif verdict["basis"] == "raw":
                line += "（大盤資料缺，暫以絕對漲跌判定）"
            evidence.append(line)
        elif price is not None and market and market.get("ex_rights"):
            # 除權息 X: no comparable previous close, so the move is not scored.
            evidence.append(f"收盤 {price:g}，除權息不比價（漲跌幅不驗收）")
        elif stale_bar:
            evidence.append(f"今日行情資料未取得（K 棒停在 {stale_bar}）")
        elif market is None:
            evidence.append("今日行情資料未取得")

        if volume:
            evidence.append(f"成交量排名第 {volume.get('rank')} 名")

        if inst:
            side_label = "買超" if inst["side"] == "buy" else "賣超"
            amount = inst.get("est_amount")
            amount_text = f"{amount:+.1f} 億" if amount is not None else "金額未估"
            evidence.append(f"外資{side_label}第 {inst['rank']} 名，{amount_text}")

        if stop_loss_hit is True:
            evidence.append(f"觸發停損：{target.get('stop_loss_desc') or stop_loss_price}")
        elif stop_loss_hit is False and stop_loss_price is not None:
            evidence.append(f"未觸發停損：{target.get('stop_loss_desc') or stop_loss_price}")

        results.append(
            {
                "id": ticker,
                "name": target.get("name", ""),
                "direction": direction,
                "trigger": target.get("trigger", ""),
                "stop_loss_price": stop_loss_price,
                "stop_loss_desc": target.get("stop_loss_desc", ""),
                "price": price,
                "pct_change": pct_change,
                "hit": hit,
                "raw_hit": None if verdict["raw_hit"] is None else bool(verdict["raw_hit"]),
                "outcome": verdict["outcome"],
                "index_pct_change": index_pct,
                "excess_pct": verdict["excess_pct"],
                "hit_basis": verdict["basis"],
                "result": _result_label(hit, verdict["outcome"]),
                "stop_loss_triggered": stop_loss_hit,
                "volume_rank": volume.get("rank") if volume else None,
                "institutional_side": inst.get("side") if inst else None,
                "institutional_rank": inst.get("rank") if inst else None,
                "institutional_est_amount": inst.get("est_amount") if inst else None,
                "evidence": evidence,
            }
        )

    return results


def format_validation_results_for_prompt(results):
    if not results:
        return "（無前次正式 prediction_targets 可驗收）"

    lines = ["# 本機正式驗收結果（Gemini 必須逐一引用，不得省略）"]
    for row in results:
        status = "✅" if row.get("hit") is True else ("❌" if row.get("hit") is False else "⚠️")
        evidence = "；".join(row.get("evidence") or [])
        lines.append(
            f"- {status} {row.get('id')} {row.get('name')}：{row.get('result')}；"
            f"方向={row.get('direction')}；{evidence}"
        )
    return "\n".join(lines)


def format_validation_results_for_structured_data(results):
    rows = []
    for row in results or []:
        pct = row.get("pct_change")
        pct_text = f"{pct:+.2f}%" if pct is not None else "無行情資料"
        volume = f"，成交量排名第{row['volume_rank']}" if row.get("volume_rank") else ""
        rows.append(f"{row.get('id')} {row.get('name')} ({pct_text}, {row.get('result')}{volume})")
    return rows
