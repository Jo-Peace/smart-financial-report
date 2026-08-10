from __future__ import annotations

import os
import re

from modules.analytics_store import AnalyticsStore
from modules.market_signal_scorer import load_stock_db


TICKER_PATTERN = re.compile(r"\b(\d{4})\b")


def _as_list(value):
    if not value:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def _extract_tickers_from_text(text):
    if not text:
        return []
    return TICKER_PATTERN.findall(str(text))


def _collect_tickers(structured_data):
    tickers = set()

    for row in _as_list(structured_data.get("validation_results") or structured_data.get("prev_day_picks_result")):
        if isinstance(row, dict):
            ticker = _clean_text(row.get("id"))
            if ticker:
                tickers.add(ticker)

    for row in _as_list(structured_data.get("prediction_targets")):
        if isinstance(row, dict):
            ticker = _clean_text(row.get("id"))
            if ticker:
                tickers.add(ticker)

    for bucket in (
        structured_data.get("heavyweights_dumped"),
        structured_data.get("safe_havens_bought"),
        structured_data.get("price_volume_divergence"),
        structured_data.get("ai_data_picks"),
        structured_data.get("commodities"),
    ):
        for item in _as_list(bucket):
            for ticker in _extract_tickers_from_text(item):
                tickers.add(ticker)

    for sector, stocks in (structured_data.get("sectors") or {}).items():
        for ticker in _extract_tickers_from_text(sector):
            tickers.add(ticker)
        for item in _as_list(stocks):
            for ticker in _extract_tickers_from_text(item):
                tickers.add(ticker)

    return sorted(tickers)


def _format_horizon_summary(horizon_rows):
    if not horizon_rows:
        return ["- 無"]
    lines = []
    for row in horizon_rows:
        horizon = row.get("horizon_days")
        samples = row.get("samples")
        hit_rate = row.get("hit_rate")
        avg_return = row.get("avg_return")
        avg_excess = row.get("avg_excess")
        hit_text = f"{float(hit_rate) * 100:.1f}%" if hit_rate is not None else "N/A"
        ret_text = f"{float(avg_return):+.2f}%" if avg_return is not None else "N/A"
        excess_text = f"{float(avg_excess):+.2f}%" if avg_excess is not None else "N/A"
        lines.append(f"- {horizon}D：樣本 {samples}；命中率 {hit_text}；平均報酬 {ret_text}；超額報酬 {excess_text}")
    return lines or ["- 無"]


def _format_recent_results(recent_results):
    if not recent_results:
        return ["- 無"]
    lines = []
    for row in recent_results[:5]:
        horizon = row.get("horizon_days")
        ticker = row.get("ticker", "")
        name = row.get("name", "")
        result_desc = row.get("result_desc") or ""
        ret = row.get("return_pct")
        hit = row.get("hit")
        ret_text = f"{float(ret):+.2f}%" if ret is not None else "N/A"
        hit_text = "命中" if hit == 1 else ("失準" if hit == 0 else "未判定")
        lines.append(f"- {ticker} {name}：{horizon}D {hit_text}；{ret_text}；{result_desc}")
    return lines


def _build_short_term_picks(structured_data):
    picks = []
    for target in _as_list(structured_data.get("prediction_targets"))[:2]:
        if not isinstance(target, dict):
            continue
        picks.append(
            {
                "ticker": _clean_text(target.get("id")),
                "name": _clean_text(target.get("name")),
                "direction": _clean_text(target.get("direction", "多")),
                "time_scale": "短線",
                "horizon": "1~5日",
                "validation_window": "1D / 3D / 5D",
                "reason": _clean_text(target.get("trigger")),
                "invalidation": _clean_text(target.get("stop_loss_desc") or target.get("stop_loss_price") or ""),
            }
        )

    if not picks:
        for pick in _as_list(structured_data.get("ai_data_picks"))[:1]:
            picks.append(
                {
                    "ticker": "",
                    "name": _clean_text(pick),
                    "direction": "觀察",
                    "time_scale": "短線",
                    "horizon": "1~5日",
                    "validation_window": "1D / 3D / 5D",
                    "reason": _clean_text(pick),
                    "invalidation": "若無量價延續則降級觀察",
                }
            )

    return picks


def _build_long_term_picks(structured_data, horizon_rows, recent_trends):
    picks = []
    event_radar = _as_list(structured_data.get("supply_chain_event_radar"))
    stock_db = load_stock_db(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    if event_radar:
        top_event = event_radar[0]
        if isinstance(top_event, dict):
            picks.append(
                {
                    "theme": _clean_text(top_event.get("event")),
                    "time_scale": "中長期",
                    "horizon": "2~6週 / 1~3個月",
                    "validation_window": "20D / 60D",
                    "thesis": f"事件雷達影響分數 {top_event.get('impact_score', 'N/A')}，代表供應鏈結構可能重估。",
                    "evidence": ", ".join(_clean_text(x) for x in _as_list(top_event.get("impacted_roles"))[:3]) or "無",
                }
            )

    if recent_trends:
        ranked_trends = []
        for ticker, info in recent_trends.items():
            trends = info.get("trends") or {}
            score = 0.0
            for window, trend in trends.items():
                score += abs(float(trend.get("pct_change", 0)))
            if "10D" not in trends and "20D" not in trends:
                continue
            ranked_trends.append((score, ticker, info))
        ranked_trends.sort(reverse=True)

        for _, ticker, info in ranked_trends[:2]:
            trend_parts = []
            for window, trend in (info.get("trends") or {}).items():
                trend_parts.append(f"{window} {float(trend.get('pct_change', 0)):+.2f}%")
            name = (stock_db.get(ticker, {}) or {}).get("name", "")
            theme = f"{ticker} {name}".strip()
            picks.append(
                {
                    "theme": theme,
                    "time_scale": "中期",
                    "horizon": "2~6週",
                    "validation_window": "20D",
                    "thesis": "、".join(trend_parts) or "近期走勢已連續累積，值得用 20D 以上追蹤。",
                    "evidence": "、".join(trend_parts) or "無",
                }
            )

    if not picks and recent_trends:
        for ticker, info in list(recent_trends.items())[:2]:
            trends = info.get("trends") or {}
            trend_texts = []
            for window, trend in trends.items():
                trend_texts.append(f"{window} {trend.get('pct_change', 0):+.2f}%")
            picks.append(
                {
                    "theme": ticker,
                    "time_scale": "中期",
                    "horizon": "2~6週",
                    "validation_window": "20D",
                    "thesis": "、".join(trend_texts) or "最近多日走勢可追蹤",
                    "evidence": "、".join(trend_texts) or "無",
                }
            )

    if not picks:
        picks.append(
            {
                "theme": "今日無明確長線主軸",
                "time_scale": "中期",
                "horizon": "2~6週 / 1~3個月",
                "validation_window": "20D / 60D",
                "thesis": "目前資料不足以建立穩定長線主軸，先等多日驗證再升級。",
                "evidence": "無",
            }
        )

    return picks


def enrich_structured_data_with_timeframes(structured_data):
    structured_data = structured_data or {}
    store = AnalyticsStore()
    horizon_rows = store.get_horizon_performance_summary()
    recent_results = store.get_recent_prediction_results(limit=8)
    tickers = _collect_tickers(structured_data)
    recent_trends = store.get_recent_market_trends(tickers, windows=(5, 10, 20))

    structured_data["short_term_horizon"] = "1~5日"
    structured_data["long_term_horizon"] = "2~6週 / 1~3個月"
    structured_data["short_term_validation_window"] = "1D / 3D / 5D"
    structured_data["long_term_validation_window"] = "20D / 60D"
    structured_data["short_term_picks"] = _build_short_term_picks(structured_data)
    structured_data["long_term_picks"] = _build_long_term_picks(structured_data, horizon_rows, recent_trends)
    structured_data["long_term_thesis"] = (
        "長線主軸應優先看供應鏈與族群是否有多日驗證延續，而不是單日爆量。"
    )
    structured_data["long_term_evidence"] = [
        "多日回測命中率與平均超額報酬，應作為長線可信度基準。",
        "事件雷達與族群同步性，應優先高於單日籌碼雜訊。",
    ]
    structured_data["timeframe_history"] = {
        "horizon_performance": horizon_rows,
        "recent_results": recent_results,
        "recent_trends": recent_trends,
        "tickers": tickers,
    }
    return structured_data


def format_timeframe_history_for_prompt(structured_data):
    history = structured_data.get("timeframe_history") or {}
    lines = []
    lines.extend(_format_horizon_summary(history.get("horizon_performance")))
    lines.extend(["", "最近驗收："])
    lines.extend(_format_recent_results(history.get("recent_results")))
    return "\n".join(line for line in lines if line is not None)
