"""
Deterministic market signal scoring for the daily Taiwan stock report.

This module intentionally does not call any AI API. It turns the raw market
inputs already collected by main.py into a compact, auditable signal pack for
Gemini to explain instead of inventing its own ranking from long raw lists.
"""

from __future__ import annotations

import json
import os


EXCLUDED_WATCH_TICKERS = {"2330", "2317", "2454"}


def _normalize_ticker(value):
    if value is None:
        return ""
    return str(value).replace(".TW", "").replace(".TWO", "").strip().upper()


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


def _infer_operator_logic(row):
    """
    Infer a next-day operator hypothesis from observable evidence.

    The output is a hypothesis, not a fact. It is designed to be verified the
    next trading day by price, volume, and institutional follow-through.
    """
    ticker = row.get("ticker", "")
    name = row.get("name", "")
    label = f"{ticker} {name}".strip()
    pct = _float(row.get("pct_change", row.get("volume_pct_change")))
    vol_ratio = _float(row.get("vol_ratio"), 1.0)
    price = _float(row.get("price"))
    ma5 = _float(row.get("ma5"))
    ma20 = _float(row.get("ma20"))
    has_buy = bool(row.get("foreign_buy_rank"))
    has_sell = bool(row.get("foreign_sell_rank"))
    has_top_volume = bool(row.get("volume_rank") and row["volume_rank"] <= 10)
    confidence = 35
    evidence = []

    if row.get("volume_rank"):
        confidence += max(0, 16 - int(row["volume_rank"]))
        evidence.append(f"成交量第 {row['volume_rank']} 名")
    if vol_ratio >= 2:
        confidence += 16
        evidence.append(f"量增比 {vol_ratio}x")
    elif vol_ratio >= 1.5:
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

    if has_sell and pct >= 2 and has_top_volume:
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
        logic = "外資買超與市場成交量共振，主力有延續攻擊意圖"
        next_day_bias = "偏多"
        confirmation = "隔日續量或量縮守高，收盤不跌破 MA5"
        invalidation = "隔日開高走低且外資轉賣，代表追價失敗"
        confidence += 12
    elif has_buy and not row.get("volume_rank"):
        logic = "外資先買但市場尚未跟量，可能是主力提前卡位"
        next_day_bias = "觀察偏多"
        confirmation = "隔日成交量進入前20或量增比升至 1.5x 以上"
        invalidation = "隔日無量下跌或跌破 MA20，代表買超未擴散"
        confidence += 4
    elif has_top_volume and not has_buy and not has_sell and pct > 0:
        logic = "市場成交量先熱，但三大法人尚未表態，偏短線換手盤"
        next_day_bias = "中性偏多"
        confirmation = "隔日法人買超跟上，且股價守住今日收盤附近"
        invalidation = "隔日爆量不漲或收黑，代表短線籌碼鬆動"
    elif has_top_volume and pct < 0:
        logic = "成交量放大但價格走弱，主力偏向調節或換手失敗"
        next_day_bias = "偏空"
        confirmation = "隔日反彈量縮、收盤仍弱於 MA5"
        invalidation = "隔日放量收復今日跌幅一半以上"
    else:
        logic = "訊號不足，暫無明確主力操作假說"
        next_day_bias = "中性"
        confirmation = "隔日出現法人買賣超或成交量放大後再判斷"
        invalidation = "無"
        confidence = min(confidence, 45)

    return {
        "ticker": ticker,
        "name": name,
        "label": label,
        "logic": logic,
        "next_day_bias": next_day_bias,
        "confidence": max(0, min(95, round(confidence))),
        "evidence": evidence[:5],
        "confirmation": confirmation,
        "invalidation": invalidation,
    }


def build_market_signal_pack(
    market_data,
    institutional_data=None,
    volume_data=None,
    stock_db=None,
    max_candidates=8,
):
    """
    Build a deterministic signal pack from today's raw market inputs.

    Scoring goals:
    - Detect the most YouTube-worthy anomalies: volume vs foreign flow conflict,
      broad market attention, and technical confirmation.
    - Produce stricter watch-list candidates while excluding mega caps that the
      prompt already forbids as "AI data picks".
    """
    institutional_data = institutional_data or {}
    volume_data = volume_data or []
    stock_db = stock_db or {}

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
        row["vol_ratio"] = _float(data.get("vol_ratio"), 1.0)
        row["ma5"] = data.get("ma5")
        row["ma20"] = data.get("ma20")
        row["rsi"] = data.get("rsi")

        if row["vol_ratio"] >= 2:
            row["score"] += 24
            row["watch_score"] += 24
            row["signals"].append(f"量增比 {row['vol_ratio']}x，屬爆量訊號")
        elif row["vol_ratio"] >= 1.5:
            row["score"] += 12
            row["watch_score"] += 12
            row["signals"].append(f"量增比 {row['vol_ratio']}x，量能放大")

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
        rank = _int(item.get("rank"))
        row["volume_rank"] = rank
        row["volume"] = item.get("volume")
        row["volume_pct_change"] = _float(item.get("pct_change"))
        if rank:
            volume_points = max(0, 24 - rank)
            row["score"] += volume_points
            row["watch_score"] += max(0, 14 - rank)
            if rank <= 5:
                row["signals"].append(f"成交量第 {rank} 名，市場關注度極高")
            elif rank <= 10:
                row["signals"].append(f"成交量第 {rank} 名，進入市場前十焦點")

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

    anomalies = []
    candidates = []
    for ticker, row in rows.items():
        pct = _float(row.get("pct_change", row.get("volume_pct_change")))
        has_volume_focus = row.get("volume_rank") and row["volume_rank"] <= 10
        has_buy = row.get("foreign_buy_rank")
        has_sell = row.get("foreign_sell_rank")

        if has_sell and pct >= 2:
            text = f"{ticker} {row.get('name', '')}: 外資賣超第 {row['foreign_sell_rank']} 名，但股價/成交量漲幅 {pct:+.2f}%"
            row["signals"].append("法人賣、市場買的籌碼背離")
            row["risks"].append("外資反向調節，需防隔日換手失敗")
            row["score"] += 22
            anomalies.append({"type": "法人賣超但股價強", "ticker": ticker, "text": text, "score": row["score"]})

        if has_buy and not row.get("volume_rank"):
            text = f"{ticker} {row.get('name', '')}: 外資買超第 {row['foreign_buy_rank']} 名，但未進成交量前20"
            row["signals"].append("法人買超尚未擴散成市場共識")
            row["score"] += 10
            anomalies.append({"type": "法人買超但市場未放量", "ticker": ticker, "text": text, "score": row["score"]})

        if has_volume_focus and not has_buy and not has_sell:
            text = f"{ticker} {row.get('name', '')}: 成交量第 {row['volume_rank']} 名，但外資買賣超榜未同步"
            row["signals"].append("市場熱度高但法人方向不明")
            row["score"] += 10
            anomalies.append({"type": "成交量熱但法人缺席", "ticker": ticker, "text": text, "score": row["score"]})

        row["operator_logic"] = _infer_operator_logic(row)

        label = _stock_db_label(stock_db, ticker)
        if label:
            row["industry_label"] = label
        row["score"] = round(row["score"], 1)
        row["watch_score"] = round(row["watch_score"], 1)
        candidates.append(row)

    candidates = sorted(candidates, key=lambda x: x["score"], reverse=True)
    watch_candidates = [
        row for row in candidates
        if row["ticker"] not in EXCLUDED_WATCH_TICKERS
        and row["watch_score"] >= 28
        and not row.get("foreign_sell_rank")
    ]
    watch_candidates = sorted(watch_candidates, key=lambda x: x["watch_score"], reverse=True)

    anomalies = sorted(anomalies, key=lambda x: x["score"], reverse=True)
    strongest_anomaly = anomalies[0] if anomalies else None

    return {
        "strongest_anomaly": strongest_anomaly,
        "anomalies": anomalies[:5],
        "top_candidates": candidates[:max_candidates],
        "operator_hypotheses": [row["operator_logic"] for row in candidates[:5]],
        "watch_candidates": watch_candidates[:3],
        "excluded_watch_tickers": sorted(EXCLUDED_WATCH_TICKERS),
    }


def format_signal_pack_for_prompt(signal_pack):
    """Render the deterministic signal pack into a compact prompt section."""
    if not signal_pack:
        return "（尚未建立本地量化訊號包）"

    lines = []
    strongest = signal_pack.get("strongest_anomaly")
    if strongest:
        lines.append(f"## 本地演算法判定的今日最強異常")
        lines.append(f"- {strongest['type']}：{strongest['text']}（異常分數 {strongest['score']}）")
    else:
        lines.append("## 本地演算法判定的今日最強異常")
        lines.append("- 未偵測到明確法人/成交量背離，請以成交量與技術位共振為主。")

    lines.append("\n## 異常訊號候選")
    anomalies = signal_pack.get("anomalies") or []
    if anomalies:
        for item in anomalies:
            lines.append(f"- {item['type']}｜{item['text']}")
    else:
        lines.append("- 無明確異常訊號。")

    lines.append("\n## 綜合分數前段班")
    for row in signal_pack.get("top_candidates", [])[:8]:
        name = row.get("name") or ""
        label = f"｜{row['industry_label']}" if row.get("industry_label") else ""
        signals = "；".join(row.get("signals", [])[:3]) or "無主要訊號"
        risks = "；風險：" + "；".join(row.get("risks", [])[:2]) if row.get("risks") else ""
        lines.append(f"- {row['ticker']} {name}{label}：總分 {row['score']}，觀察分 {row['watch_score']}。{signals}{risks}")

    lines.append("\n## 主力操作假說與隔日方向（需驗收，不可當成事實）")
    for item in signal_pack.get("operator_hypotheses", [])[:5]:
        evidence = "、".join(item.get("evidence", [])) or "證據不足"
        lines.append(
            f"- {item['label']}：{item['logic']}；隔日偏向：{item['next_day_bias']}；"
            f"信心 {item['confidence']}/100；證據：{evidence}；"
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
