"""
Optional same-session market extras for /1 (all public open data, no API key).

- TAIFEX 三大法人臺股期貨未平倉 (openapi MarketDataOfMajorInstitutionalTraders…)
- TAIFEX Put/Call ratio (openapi PutCallRatio)
- TWSE 信用交易統計 MI_MARGN (融資／融券餘額; usually published ~21:00, so at
  16:10 it is normally 「未取得」)
- TPEx 上櫃三大法人買賣超 + 上櫃收盤 (so 上櫃 names are not silently absent)

Rules:
- Every value carries the trade date it belongs to; a payload for another
  session is rejected (TAIFEX openapi only serves "the latest day").
- Any failure returns None for that item and never raises into /1.
- ``MARKET_EXTRAS=0`` disables all of it.
"""

from __future__ import annotations

import logging
import os

import requests

from modules.twse_http import twse_get
from modules.twse_tape import iso_of, roc_to_iso, to_float, to_int, ymd_of

logger = logging.getLogger(__name__)

TAIFEX_FUT_INST_URL = "https://openapi.taifex.com.tw/v1/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate"
TAIFEX_PCR_URL = "https://openapi.taifex.com.tw/v1/PutCallRatio"
TWSE_MARGIN_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={ymd}&selectType=MS&response=json"
TPEX_INSTI_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date={slash}&response=json"
TPEX_CLOSE_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={slash}&type=EW&response=json"

TX_CONTRACT = "臺股期貨"
FOREIGN_ITEM = "外資及陸資"


def extras_enabled() -> bool:
    flag = (os.getenv("MARKET_EXTRAS", "1") or "1").strip().lower()
    return flag not in {"0", "off", "false", "no"}


def _get_json(url: str, timeout: float):
    resp = twse_get(url, timeout=timeout, retries=2)
    resp.raise_for_status()
    return resp.json()


# ── TAIFEX ──────────────────────────────────────────────────────────────────


def parse_taifex_futures_oi(rows, expected_date: str) -> dict | None:
    """臺股期貨 外資／投信／自營商 未平倉淨口數 for exactly expected_date."""
    want = ymd_of(expected_date)
    if not want or not isinstance(rows, list):
        return None
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if ymd_of(row.get("Date")) != want or str(row.get("ContractCode") or "").strip() != TX_CONTRACT:
            continue
        item = str(row.get("Item") or "").strip()
        out[item] = {
            "oi_net": to_int(row.get("OpenInterest(Net)")),
            "trade_net": to_int(row.get("TradingVolume(Net)")),
            "oi_net_value_k": to_int(row.get("ContractValueofOpenInterest(Net)(Thousands)")),
        }
    foreign = out.get(FOREIGN_ITEM)
    if not foreign or foreign.get("oi_net") is None:
        return None
    return {
        "date": iso_of(want),
        "contract": TX_CONTRACT,
        "foreign_oi_net": foreign["oi_net"],
        "foreign_trade_net": foreign.get("trade_net"),
        "trust_oi_net": (out.get("投信") or {}).get("oi_net"),
        "dealer_oi_net": (out.get("自營商") or {}).get("oi_net"),
    }


def parse_taifex_pc_ratio(rows, expected_date: str) -> dict | None:
    want = ymd_of(expected_date)
    if not want or not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and ymd_of(row.get("Date")) == want:
            oi_ratio = to_float(row.get("PutCallOIRatio%"))
            vol_ratio = to_float(row.get("PutCallVolumeRatio%"))
            if oi_ratio is None and vol_ratio is None:
                return None
            return {"date": iso_of(want), "pc_oi_ratio_pct": oi_ratio, "pc_volume_ratio_pct": vol_ratio}
    return None


# ── TWSE margin ─────────────────────────────────────────────────────────────


def parse_twse_margin(payload, expected_date: str) -> dict | None:
    """MI_MARGN selectType=MS → 融資／融券 餘額與增減（張）, 融資金額（億）."""
    want = ymd_of(expected_date)
    if not isinstance(payload, dict) or payload.get("stat") != "OK" or ymd_of(payload.get("date")) != want:
        return None
    table = None
    for candidate in payload.get("tables") or []:
        fields = [str(f) for f in candidate.get("fields") or []]
        if "項目" in fields and "今日餘額" in fields and "前日餘額" in fields:
            table = candidate
            break
    if not table:
        return None
    fields = [str(f) for f in table["fields"]]
    i_item, i_today, i_prev = fields.index("項目"), fields.index("今日餘額"), fields.index("前日餘額")
    out = {"date": iso_of(want)}
    for raw in table.get("data") or []:
        try:
            item = str(raw[i_item])
            today, prev = to_int(raw[i_today]), to_int(raw[i_prev])
        except (IndexError, TypeError):
            continue
        if today is None or prev is None:
            continue
        if item.startswith("融資(交易單位)"):
            out["margin_balance_lots"], out["margin_change_lots"] = today, today - prev
        elif item.startswith("融券(交易單位)"):
            out["short_balance_lots"], out["short_change_lots"] = today, today - prev
        elif item.startswith("融資金額"):
            out["margin_amount_yi"] = round(today * 1000 / 1e8, 1)
            out["margin_amount_change_yi"] = round((today - prev) * 1000 / 1e8, 1)
    return out if len(out) > 1 else None


# ── TPEx ────────────────────────────────────────────────────────────────────


def _tpex_table(payload, expected_date: str) -> dict | None:
    want = ymd_of(expected_date)
    if not isinstance(payload, dict) or str(payload.get("stat") or "").lower() != "ok":
        return None
    if ymd_of(payload.get("date")) != want:
        return None
    for table in payload.get("tables") or []:
        if roc_to_iso(table.get("date")) == iso_of(want) and table.get("data"):
            return table
    return None


def parse_tpex_close(payload, expected_date: str) -> dict[str, float]:
    table = _tpex_table(payload, expected_date)
    if not table:
        return {}
    fields = [str(f).strip() for f in table.get("fields") or []]
    if "代號" not in fields or "收盤" not in fields:
        return {}
    i_id, i_close = fields.index("代號"), fields.index("收盤")
    out = {}
    for raw in table["data"]:
        try:
            close = to_float(raw[i_close])
            sid = str(raw[i_id]).strip()
        except (IndexError, TypeError):
            continue
        if sid and close and close > 0:
            out[sid] = close
    return out


def parse_tpex_institutional(payload, expected_date: str) -> list[dict]:
    """
    上櫃三大法人 daily table. Column names repeat per investor group, so the
    layout is validated (24 columns, 代號 first, 三大法人合計 last) before using
    positions: 4 = 外資及陸資(不含外資自營商)買賣超, 13 = 投信, 22 = 自營商合計.
    """
    table = _tpex_table(payload, expected_date)
    if not table:
        return []
    fields = [str(f).strip() for f in table.get("fields") or []]
    if len(fields) != 24 or fields[0] != "代號" or not fields[-1].startswith("三大法人"):
        logger.warning("TPEx institutional layout changed: %s", fields[:4])
        return []
    rows = []
    for raw in table["data"]:
        if len(raw) < 24:
            continue
        sid = str(raw[0]).strip()
        foreign = to_int(raw[4])
        if not sid or foreign is None:
            continue
        rows.append(
            {
                "id": sid,
                "name": str(raw[1]).strip(),
                "foreign_net": foreign,
                "trust_net": to_int(raw[13]) or 0,
                "dealer_net": to_int(raw[22]),
                "total_net": to_int(raw[23]) or 0,
                "market": "TPEx",
            }
        )
    return rows


def rank_tpex(rows: list[dict], closes: dict[str, float], date_iso: str, top_n: int = 5) -> dict:
    from modules.institutional_ranking import apply_amount_estimates, rank_by_amount

    priced = apply_amount_estimates(
        rows, closes, price_dates={sid: date_iso for sid in closes}, data_date=date_iso
    )
    top_buy, top_sell = rank_by_amount(priced, top_n=top_n)
    return {"data_date": date_iso, "market_scope": "TPEx", "top_buy": top_buy, "top_sell": top_sell}


# ── orchestration ───────────────────────────────────────────────────────────


def fetch_market_extras(data_date: str, timeout: float = 15.0) -> dict:
    """{taifex_oi, pc_ratio, margin, tpex} — each None when unavailable for data_date."""
    out = {"date": str(data_date or "")[:10], "taifex_oi": None, "pc_ratio": None, "margin": None, "tpex": None}
    if not extras_enabled() or not ymd_of(data_date):
        return out
    ymd = ymd_of(data_date)
    slash = f"{ymd[:4]}%2F{ymd[4:6]}%2F{ymd[6:]}"
    jobs = {
        "taifex_oi": lambda: parse_taifex_futures_oi(_get_json(TAIFEX_FUT_INST_URL, timeout), data_date),
        "pc_ratio": lambda: parse_taifex_pc_ratio(_get_json(TAIFEX_PCR_URL, timeout), data_date),
        "margin": lambda: parse_twse_margin(_get_json(TWSE_MARGIN_URL.format(ymd=ymd), timeout), data_date),
    }
    for key, job in jobs.items():
        try:
            out[key] = job()
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            logger.warning("market extra %s failed: %s", key, exc)
        except Exception:  # never break /1
            logger.exception("market extra %s failed", key)
    try:
        rows = parse_tpex_institutional(_get_json(TPEX_INSTI_URL.format(slash=slash), timeout), data_date)
        if rows:
            closes = parse_tpex_close(_get_json(TPEX_CLOSE_URL.format(slash=slash), timeout), data_date)
            out["tpex"] = rank_tpex(rows, closes, iso_of(ymd))
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        logger.warning("TPEx institutional failed: %s", exc)
    except Exception:
        logger.exception("TPEx institutional failed")
    return out


def format_extras_lines(extras: dict | None) -> list[str]:
    """Human-readable, dated facts. Missing items are listed as 未取得 (do not cite)."""
    extras = extras or {}
    lines = []
    oi = extras.get("taifex_oi")
    if oi:
        lines.append(f"台指期外資未平倉淨口數 {oi['foreign_oi_net']:+,} 口（{oi['date']}）")
    pcr = extras.get("pc_ratio")
    if pcr and pcr.get("pc_oi_ratio_pct") is not None:
        lines.append(f"選擇權 Put/Call 未平倉比 {pcr['pc_oi_ratio_pct']:.2f}%（{pcr['date']}）")
    margin = extras.get("margin")
    if margin and margin.get("margin_change_lots") is not None:
        text = f"上市融資餘額增減 {margin['margin_change_lots']:+,} 張"
        if margin.get("margin_amount_change_yi") is not None:
            text += f"（金額 {margin['margin_amount_change_yi']:+.1f} 億）"
        lines.append(f"{text}（{margin['date']}）")
    tpex = extras.get("tpex")
    if tpex:
        for side, label in (("top_buy", "上櫃外資買超"), ("top_sell", "上櫃外資賣超")):
            names = [
                f"{r.get('name')}({r['est_amount']:+.1f}億)" for r in (tpex.get(side) or [])[:3] if r.get("est_amount") is not None
            ]
            if names:
                lines.append(f"{label}：{'、'.join(names)}（{tpex['data_date']}）")
    return lines


def missing_extras(extras: dict | None) -> list[str]:
    labels = {"taifex_oi": "期貨未平倉", "pc_ratio": "P/C ratio", "margin": "融資融券", "tpex": "上櫃法人"}
    extras = extras or {}
    return [label for key, label in labels.items() if not extras.get(key)]


# ── monthly revenue (上市, MOPS via TWSE open data) ─────────────────────────

TWSE_MONTHLY_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
_REVENUE_CACHE: dict[str, list] = {}


def _roc_month_to_iso(value) -> str | None:
    text = str(value or "").strip()
    if len(text) < 4 or not text.isdigit():
        return None
    year, month = int(text[:-2]) + 1911, int(text[-2:])
    if not 1 <= month <= 12:
        return None
    return f"{year:04d}-{month:02d}"


def parse_monthly_revenue(rows, stock_id: str) -> dict | None:
    """t187ap05_L rows → {month, revenue_k, yoy_pct, mom_pct, cum_yoy_pct, note} for one company."""
    sid = str(stock_id or "").strip()
    for row in rows or []:
        if not isinstance(row, dict) or str(row.get("公司代號") or "").strip() != sid:
            continue
        month = _roc_month_to_iso(row.get("資料年月"))
        yoy = to_float(row.get("營業收入-去年同月增減(%)"))
        if not month or yoy is None:
            return None
        return {
            "month": month,
            "revenue_k": to_int(row.get("營業收入-當月營收")),
            "yoy_pct": round(yoy, 1),
            "mom_pct": None if to_float(row.get("營業收入-上月比較增減(%)")) is None
            else round(to_float(row.get("營業收入-上月比較增減(%)")), 1),
            "cum_yoy_pct": None if to_float(row.get("累計營業收入-前期比較增減(%)")) is None
            else round(to_float(row.get("累計營業收入-前期比較增減(%)")), 1),
            "note": str(row.get("備註") or "").strip(" -") or None,
            "source": "twse_t187ap05_L",
        }
    return None


def fetch_monthly_revenue(stock_id: str, timeout: float = 20.0) -> dict | None:
    """Latest monthly revenue YoY for a listed company; None on any failure."""
    try:
        rows = _REVENUE_CACHE.get("rows")
        if rows is None:
            rows = _get_json(TWSE_MONTHLY_REVENUE_URL, timeout)
            if not isinstance(rows, list):
                return None
            _REVENUE_CACHE["rows"] = rows
        return parse_monthly_revenue(rows, stock_id)
    except (requests.RequestException, ValueError, TypeError) as exc:
        logger.warning("monthly revenue fetch failed: %s", exc)
        return None
