"""
Deterministic prediction validation for daily report continuity.

Gemini can explain why a prediction worked or failed, but the official
validation list should come from local market data so every previous
prediction target is checked exactly once.
"""

from __future__ import annotations


def _normalize_ticker(value):
    if value is None:
        return ""
    return str(value).replace(".TW", "").replace(".TWO", "").strip().upper()


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


def _hit(direction, pct_change):
    if pct_change is None:
        return None
    direction = str(direction or "多").strip()
    if direction == "空":
        return pct_change < 0
    return pct_change > 0


def _stop_loss_triggered(direction, price, stop_loss_price):
    if price is None or stop_loss_price is None:
        return None
    direction = str(direction or "多").strip()
    if direction == "空":
        return price >= stop_loss_price
    return price <= stop_loss_price


def _result_label(hit):
    if hit is None:
        return "無法驗收"
    return "預測成立" if hit else "預測未成立"


def validate_prediction_targets(previous_targets, market_data, institutional_data=None, volume_data=None):
    """Return official validation rows for prior structured prediction targets."""
    institutional_data = institutional_data or {}
    results = []

    for target in previous_targets or []:
        ticker = _normalize_ticker(target.get("id"))
        if not ticker:
            continue

        market = _market_row(market_data or {}, ticker)
        volume = _volume_row(volume_data or [], ticker)
        inst = _institutional_row(institutional_data, ticker)

        price = _float(market.get("price") if market else None)
        pct_change = _float(market.get("pct_change") if market else None)
        direction = target.get("direction", "多")
        hit = _hit(direction, pct_change)
        stop_loss_price = _float(target.get("stop_loss_price"))
        stop_loss_hit = _stop_loss_triggered(direction, price, stop_loss_price)

        evidence = []
        if price is not None and pct_change is not None:
            evidence.append(f"收盤 {price:g}，漲跌 {pct_change:+.2f}%")
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
                "result": _result_label(hit),
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
