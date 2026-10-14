"""
Full-market TWSE closing tape with verified trade dates.

Why this exists (audit 2026-10-13):
- ``STOCK_DAY_ALL?response=json`` actually returns **CSV** (content-type
  text/csv), so ``resp.json()`` raised JSONDecodeError on every run and the
  pipeline silently priced 外資買賣超 with weeks-old snapshots.
- STOCK_DAY_ALL also **ignores the date parameter** (a holiday request returns
  the latest session), so its rows must be checked against the 日期 column.
- ``MI_INDEX?type=ALLBUT0999`` is date-parameterised JSON with a top-level
  ``date`` field and carries both the full close table (with 成交金額) and the
  發行量加權股價指數 row — one call covers prices, trade value and ^TWII.

Every parsed row carries ``price_date`` (YYYY-MM-DD). Nothing here guesses a
date: a payload whose date does not match the requested session is rejected.
"""

from __future__ import annotations

import csv
import datetime
import io
import logging
import re
import threading
import time

import requests

from modules.twse_http import twse_get

logger = logging.getLogger(__name__)

MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={ymd}&type=ALLBUT0999&response=json"
STOCK_DAY_ALL_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?date={ymd}&response=json"
FMTQIK_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={ymd}&response=json"

TAIEX_NAME = "發行量加權股價指數"

_CACHE_LOCK = threading.Lock()
_TAPE_CACHE: dict[str, dict] = {}
# Failed lookups are remembered briefly so the 4 callers in one /1 run
# (pricing, 成交值 enrichment, 成交值榜, ^TWII fallback) do not refetch.
_MISS_CACHE: dict[str, float] = {}
MISS_TTL_SECONDS = 90.0

_TAG_RE = re.compile(r"<[^>]+>")


# ── small parsers ────────────────────────────────────────────────────────────


def ymd_of(date_str) -> str:
    """'2026-10-12' / '20261012' / date → '20261012' ('' when unparsable)."""
    if isinstance(date_str, (datetime.date, datetime.datetime)):
        return date_str.strftime("%Y%m%d")
    text = str(date_str or "").strip().replace("-", "").replace("/", "")[:8]
    return text if len(text) == 8 and text.isdigit() else ""


def iso_of(ymd: str) -> str:
    ymd = ymd_of(ymd)
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}" if ymd else ""


def roc_to_iso(value) -> str | None:
    """'115/10/12' or '1151012' (ROC) → '2026-10-12'."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if "/" in text or "-" in text:
            parts = text.replace("-", "/").split("/")
            if len(parts) != 3:
                return None
            y, m, d = int(parts[0]) + 1911, int(parts[1]), int(parts[2])
        else:
            if not text.isdigit() or len(text) < 6:
                return None
            y, m, d = int(text[:-4]) + 1911, int(text[-4:-2]), int(text[-2:])
        return datetime.date(y, m, d).isoformat()
    except (ValueError, TypeError):
        return None


def strip_tags(value) -> str:
    return _TAG_RE.sub("", str(value or "")).strip()


def to_float(value) -> float | None:
    text = strip_tags(value).replace(",", "").replace(" ", "")
    if not text or text in {"--", "---", "—", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value) -> int | None:
    num = to_float(value)
    return int(num) if num is not None else None


def signed_change(sign_cell, diff_cell) -> tuple[float | None, bool]:
    """
    Combine MI_INDEX 漲跌(+/-) HTML sign + unsigned 漲跌價差.

    Returns (change, ex_rights). ``X`` (除權息／不比價) → (None, True) so callers
    never turn an ex-dividend day into a fake 0.00%.
    """
    sign = strip_tags(sign_cell).upper()
    diff = to_float(diff_cell)
    if "X" in sign:
        return None, True
    if diff is None:
        return None, False
    if "-" in sign:
        return -abs(diff), False
    if "+" in sign:
        return abs(diff), False
    return (0.0, False) if abs(diff) < 1e-12 else (None, False)


EX_RIGHTS_TEXT = "除權息不比價"


def pct_text(row, na: str = "N/A") -> str:
    """
    Display text for a payload / ranking row's pct_change: '+1.23%',
    「除權息不比價」 on an X day (pct_change is None by design), else ``na``.
    """
    row = row or {}
    pct = row.get("pct_change")
    if isinstance(pct, (int, float)):
        return f"{pct:+.2f}%"
    if row.get("ex_rights"):
        return EX_RIGHTS_TEXT
    return na


def _pct(close: float | None, change: float | None) -> float | None:
    if close is None or change is None:
        return None
    prev = close - change
    if prev <= 0:
        return None
    return round(change / prev * 100, 2)


# ── MI_INDEX ─────────────────────────────────────────────────────────────────


def _find_table(payload: dict, required: tuple[str, ...]) -> dict | None:
    for table in payload.get("tables") or []:
        fields = [str(f) for f in (table.get("fields") or [])]
        if all(name in fields for name in required):
            return table
    return None


def parse_mi_index_payload(payload: dict, expected_date: str | None = None) -> dict | None:
    """
    Parse MI_INDEX ALLBUT0999. Returns
    {date, rows:[{id,name,close_price,volume,trade_value,change,pct_change,
    ex_rights,price_date}], index:{close,change,pct_change,bar_date}|None}
    or None when stat != OK or the payload date differs from expected_date.
    """
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return None
    resp_ymd = ymd_of(payload.get("date") or (payload.get("params") or {}).get("date"))
    if not resp_ymd:
        return None
    if expected_date and ymd_of(expected_date) != resp_ymd:
        return None
    price_date = iso_of(resp_ymd)

    rows: list[dict] = []
    table = _find_table(payload, ("證券代號", "收盤價"))
    if table:
        fields = [str(f) for f in table.get("fields") or []]

        def idx(name, default=None):
            return fields.index(name) if name in fields else default

        i_id, i_name = idx("證券代號"), idx("證券名稱")
        i_vol, i_val = idx("成交股數"), idx("成交金額")
        i_close, i_sign, i_diff = idx("收盤價"), idx("漲跌(+/-)"), idx("漲跌價差")
        for raw in table.get("data") or []:
            try:
                sid = str(raw[i_id]).strip()
                name = str(raw[i_name]).strip() if i_name is not None else ""
            except (IndexError, TypeError):
                continue
            if not sid or not sid[0].isdigit():
                continue
            close = to_float(raw[i_close]) if i_close is not None and len(raw) > i_close else None
            if close is None or close <= 0:
                continue
            change, ex_rights = (None, False)
            if i_sign is not None and i_diff is not None and len(raw) > max(i_sign, i_diff):
                change, ex_rights = signed_change(raw[i_sign], raw[i_diff])
            rows.append(
                {
                    "id": sid,
                    "name": name,
                    "close_price": close,
                    "volume": to_int(raw[i_vol]) if i_vol is not None and len(raw) > i_vol else None,
                    "trade_value": to_int(raw[i_val]) if i_val is not None and len(raw) > i_val else None,
                    "change": change,
                    "pct_change": _pct(close, change),
                    "ex_rights": ex_rights,
                    "price_date": price_date,
                }
            )

    index_row = None
    itable = _find_table(payload, ("指數", "收盤指數"))
    if itable:
        fields = [str(f) for f in itable.get("fields") or []]
        i_close = fields.index("收盤指數")
        i_sign = fields.index("漲跌(+/-)") if "漲跌(+/-)" in fields else None
        i_pts = fields.index("漲跌點數") if "漲跌點數" in fields else None
        for raw in itable.get("data") or []:
            if not raw or str(raw[0]).strip() != TAIEX_NAME:
                continue
            close = to_float(raw[i_close])
            change = None
            if i_sign is not None and i_pts is not None:
                change, _ = signed_change(raw[i_sign], raw[i_pts])
            if close:
                index_row = {
                    "close": close,
                    "change": change,
                    "pct_change": _pct(close, change),
                    "bar_date": price_date,
                }
            break

    stats = _find_table(payload, ("成交統計", "成交股數(股)"))
    if index_row and stats:
        fields = [str(f) for f in stats.get("fields") or []]
        i_val, i_vol = fields.index("成交金額(元)") if "成交金額(元)" in fields else None, fields.index("成交股數(股)")
        for raw in stats.get("data") or []:
            if raw and str(raw[0]).strip().startswith("總計"):
                index_row["volume"] = to_int(raw[i_vol])
                index_row["trade_value"] = to_int(raw[i_val]) if i_val is not None else None
                break

    return {"date": price_date, "rows": rows, "index": index_row, "source": "twse_mi_index"}


# ── STOCK_DAY_ALL (CSV or JSON) ─────────────────────────────────────────────


def parse_stock_day_all_text(text: str, expected_date: str | None = None) -> dict | None:
    """
    STOCK_DAY_ALL body. TWSE serves CSV even for response=json; accept JSON too.
    Rows whose 日期 column differs from expected_date are dropped; returns None
    when nothing matches (e.g. the endpoint served the latest session instead).
    """
    body = (text or "").lstrip("﻿").strip()
    if not body:
        return None
    want_iso = iso_of(expected_date) if expected_date else ""
    if body.startswith("{"):
        import json

        try:
            payload = json.loads(body)
        except ValueError:
            return None
        resp_iso = iso_of(payload.get("date"))
        if not resp_iso or (want_iso and resp_iso != want_iso):
            return None
        from modules.institutional_ranking import parse_day_all_payload

        # A row's own 日期 (trade_date) wins; rows from another session are dropped.
        rows = [r for r in parse_day_all_payload(payload) if r.get("trade_date") in ("", None, resp_iso)]
        for row in rows:
            row["price_date"] = resp_iso
        return {"date": resp_iso, "rows": rows, "index": None, "source": "twse_stock_day_all"} if rows else None

    reader = csv.DictReader(io.StringIO(body))
    rows = []
    seen_date = None
    for rec in reader:
        rec = {str(k or "").strip(): v for k, v in rec.items()}
        price_date = roc_to_iso(rec.get("日期"))
        if not price_date:
            continue
        if want_iso and price_date != want_iso:
            continue
        seen_date = price_date
        sid = str(rec.get("證券代號") or "").strip()
        if not sid or not sid[0].isdigit():
            continue
        close = to_float(rec.get("收盤價"))
        if close is None or close <= 0:
            continue
        diff_raw = str(rec.get("漲跌價差") or "")
        change = to_float(diff_raw)  # already signed in the CSV; X / blank → None
        rows.append(
            {
                "id": sid,
                "name": str(rec.get("證券名稱") or "").strip(),
                "close_price": close,
                "volume": to_int(rec.get("成交股數")),
                "trade_value": to_int(rec.get("成交金額")),
                "change": change,
                "pct_change": _pct(close, change),
                "ex_rights": "X" in diff_raw.upper(),
                "price_date": price_date,
            }
        )
    if not rows:
        return None
    return {"date": seen_date, "rows": rows, "index": None, "source": "twse_stock_day_all"}


# ── fetch + cache ────────────────────────────────────────────────────────────


def _cache_get(ymd: str) -> dict | None:
    with _CACHE_LOCK:
        return _TAPE_CACHE.get(ymd)


def _cache_put(ymd: str, tape: dict) -> None:
    with _CACHE_LOCK:
        _TAPE_CACHE[ymd] = tape
        _MISS_CACHE.pop(ymd, None)


def _recent_miss(ymd: str) -> bool:
    with _CACHE_LOCK:
        stamp = _MISS_CACHE.get(ymd)
    return stamp is not None and (time.monotonic() - stamp) < MISS_TTL_SECONDS


def _mark_miss(ymd: str) -> None:
    with _CACHE_LOCK:
        _MISS_CACHE[ymd] = time.monotonic()


def clear_cache() -> None:
    with _CACHE_LOCK:
        _TAPE_CACHE.clear()
        _MISS_CACHE.clear()


def fetch_twse_tape(date_str, *, timeout: float = 20.0, use_cache: bool = True, quiet: bool = False) -> dict | None:
    """
    Verified full-market close tape for one session.

    MI_INDEX first (JSON, date-checked, includes 成交金額 + TAIEX); fall back to
    STOCK_DAY_ALL (CSV/JSON, rows filtered by 日期). Returns None on failure —
    never a different day's tape.
    """
    ymd = ymd_of(date_str)
    if not ymd:
        return None
    if use_cache:
        cached = _cache_get(ymd)
        if cached:
            return cached
        if _recent_miss(ymd):
            return None

    errors = []
    try:
        resp = twse_get(MI_INDEX_URL.format(ymd=ymd), timeout=timeout, retries=2)
        tape = parse_mi_index_payload(resp.json(), expected_date=ymd)
        if tape and tape.get("rows"):
            tape["tls_unverified"] = bool(getattr(resp, "tls_unverified", False))
            _cache_put(ymd, tape)
            return tape
        errors.append("MI_INDEX 無同日資料")
    except (requests.RequestException, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        errors.append(f"MI_INDEX {exc.__class__.__name__}")

    try:
        resp = twse_get(STOCK_DAY_ALL_URL.format(ymd=ymd), timeout=timeout, retries=2)
        resp.encoding = resp.encoding or "utf-8"
        tape = parse_stock_day_all_text(resp.text, expected_date=ymd)
        if tape and tape.get("rows"):
            tape["tls_unverified"] = bool(getattr(resp, "tls_unverified", False))
            _cache_put(ymd, tape)
            return tape
        errors.append("STOCK_DAY_ALL 日期不符或無資料")
    except (requests.RequestException, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        errors.append(f"STOCK_DAY_ALL {exc.__class__.__name__}")

    _mark_miss(ymd)
    if not quiet:
        print(f"  [Info] 全日收盤表未取到 {iso_of(ymd)}（{'；'.join(errors)}）")
    return None


# ── FMTQIK (TAIEX daily series) ─────────────────────────────────────────────


def parse_fmtqik_payload(payload: dict) -> list[dict]:
    """FMTQIK → [{date, close, change, volume, trade_value}] ascending."""
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return []
    fields = [str(f) for f in payload.get("fields") or []]

    def idx(name, default):
        return fields.index(name) if name in fields else default

    i_date, i_vol, i_val = idx("日期", 0), idx("成交股數", 1), idx("成交金額", 2)
    i_close, i_chg = idx("發行量加權股價指數", 4), idx("漲跌點數", 5)
    out = []
    for raw in payload.get("data") or []:
        try:
            day = roc_to_iso(raw[i_date])
            close = to_float(raw[i_close])
        except (IndexError, TypeError):
            continue
        if not day or not close:
            continue
        out.append(
            {
                "date": day,
                "close": close,
                "change": to_float(raw[i_chg]) if len(raw) > i_chg else None,
                "volume": to_int(raw[i_vol]) if len(raw) > i_vol else None,
                "trade_value": to_int(raw[i_val]) if len(raw) > i_val else None,
            }
        )
    out.sort(key=lambda r: r["date"])
    return out


def fetch_taiex_series(as_of=None, months_back: int = 3, timeout: float = 20.0) -> list[dict]:
    """Daily TAIEX closes (ascending) for the last few months; [] on failure."""
    from modules.tz import today_tpe

    anchor = as_of if isinstance(as_of, datetime.date) else None
    if anchor is None and as_of:
        try:
            anchor = datetime.datetime.strptime(str(as_of)[:10], "%Y-%m-%d").date()
        except ValueError:
            anchor = None
    anchor = anchor or today_tpe()
    seen = {}
    for m in range(months_back):
        year, month = anchor.year, anchor.month - m
        while month <= 0:
            month += 12
            year -= 1
        url = FMTQIK_URL.format(ymd=f"{year}{month:02d}01")
        try:
            resp = twse_get(url, timeout=timeout, retries=2)
            for row in parse_fmtqik_payload(resp.json()):
                if row["date"] <= anchor.isoformat():
                    seen[row["date"]] = row
        except (requests.RequestException, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            logger.warning("FMTQIK failed %s: %s", url, exc)
            continue
    return [seen[k] for k in sorted(seen)]
