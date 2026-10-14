"""Find analog trading days from local structured_data.

No embeddings. Query today's tape (index + foreign buy/sell leaders) against
historical daily cards, then attach that day's prediction and next-day rhythm.

Used by /1 before Gemini so the report can cite real analog dates instead of
the last 3500 chars of wiki.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from modules.report_paths import date_from_structured_path, iter_structured_data_paths

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOCK_DB_PATH = os.path.join(BASE_DIR, "data", "stock_db.json")
DEFAULT_REPORTS_DIR = os.path.join(BASE_DIR, "reports")

INDEX_PCT_RE = re.compile(r"（([+-]?\d+(?:\.\d+)?)%）")
AMOUNT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*億")
TICKER_RE = re.compile(r"\b(\d{4,6}[A-Z]?)\b", re.I)
TOP_N = 5
TOP_K = 2
MIN_SCORE = 20


def _norm_ticker(value) -> str:
    return str(value or "").replace(".TW", "").replace(".TWO", "").strip().upper()


def _clean(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def load_stock_maps(stock_db: dict | None = None) -> tuple[dict[str, str], dict[str, str]]:
    if stock_db is None:
        stock_db = {}
        if os.path.isfile(STOCK_DB_PATH):
            try:
                with open(STOCK_DB_PATH, encoding="utf-8") as fh:
                    loaded = json.loads(fh.read())
            except (OSError, json.JSONDecodeError):
                loaded = {}
            if isinstance(loaded, dict):
                stock_db = loaded
    name_to_ticker: dict[str, str] = {}
    ticker_to_name: dict[str, str] = {}
    for ticker, info in stock_db.items():
        sid = _norm_ticker(ticker)
        name = _clean((info or {}).get("name") if isinstance(info, dict) else "")
        if sid:
            ticker_to_name[sid] = name or ticker_to_name.get(sid, "")
        if name:
            name_to_ticker[name] = sid
    return name_to_ticker, ticker_to_name


def parse_index_pct(text) -> float | None:
    blob = _clean(text)
    if not blob:
        return None
    match = INDEX_PCT_RE.search(blob)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", blob)
    if not match:
        return None
    try:
        pct = float(match.group(1))
    except ValueError:
        return None
    if "跌" in blob and pct > 0:
        return -pct
    return pct


def index_dir(pct: float | None) -> str | None:
    if pct is None:
        return None
    if pct > 0.08:
        return "up"
    if pct < -0.08:
        return "down"
    return "flat"


def parse_flow_item(text, name_to_ticker: dict[str, str] | None = None) -> dict[str, Any]:
    raw = _clean(text)
    name_to_ticker = name_to_ticker or {}
    amount = None
    amt_match = AMOUNT_RE.search(raw)
    if amt_match:
        try:
            amount = float(amt_match.group(1))
        except ValueError:
            amount = None
    ticker = ""
    for match in TICKER_RE.finditer(raw):
        token = _norm_ticker(match.group(1))
        if token.startswith("00") and len(token) >= 4:
            ticker = token
            break
        if len(token) == 4:
            ticker = token
            break
        ticker = token
    label = raw
    if amt_match:
        label = raw[: amt_match.start()]
    label = re.sub(r"[（(].*$", " ", label)
    label = re.sub(r"[：:].*$", "", label)
    name = _clean(label).strip(" -·,，()（）")
    if not ticker and name in name_to_ticker:
        ticker = name_to_ticker[name]
    if not name and ticker:
        name = ticker
    return {
        "ticker": ticker,
        "name": name,
        "amount": amount,
        "raw": raw,
    }


def _flow_list(items, name_to_ticker, limit=TOP_N) -> list[dict[str, Any]]:
    out = []
    for item in items or []:
        if isinstance(item, dict):
            ticker = _norm_ticker(item.get("id") or item.get("ticker"))
            name = _clean(item.get("name") or "")
            amount = item.get("est_amount")
            try:
                amount = float(amount) if amount not in (None, "") else None
            except (TypeError, ValueError):
                amount = None
            if item.get("raw"):
                raw = _clean(item.get("raw"))
            elif amount is not None:
                raw = _clean(f"{name} ({amount:+.1f}億)")
            else:
                raw = name
            parsed = {
                "ticker": ticker,
                "name": name or ticker,
                "amount": amount,
                "raw": raw,
            }
        else:
            parsed = parse_flow_item(item, name_to_ticker)
        if not parsed.get("ticker") and not parsed.get("name"):
            continue
        out.append(parsed)
        if len(out) >= limit:
            break
    return out


def _prediction_rows(items) -> list[dict[str, str]]:
    rows = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        ticker = _norm_ticker(item.get("id") or item.get("ticker"))
        name = _clean(item.get("name") or "")
        if not ticker and not name:
            continue
        rows.append(
            {
                "ticker": ticker,
                "name": name,
                "direction": _clean(item.get("direction") or ""),
                "trigger": _clean(item.get("trigger") or item.get("reason") or "")[:120],
            }
        )
    return rows[:4]


def _result_lines(items) -> list[str]:
    lines = []
    for item in items or []:
        if isinstance(item, dict):
            ticker = _clean(item.get("id") or item.get("ticker") or "")
            name = _clean(item.get("name") or "")
            result = _clean(item.get("result") or "")
            pct = item.get("pct_change")
            if isinstance(pct, (int, float)):
                pct_text = f"{pct:+.2f}%"
            else:
                pct_text = _clean(pct)
            bit = " ".join(x for x in (ticker, name) if x)
            extra = " ".join(x for x in (result, pct_text) if x)
            line = f"{bit}：{extra}".strip("：")
        else:
            line = _clean(item)
        if line:
            lines.append(line[:160])
        if len(lines) >= 4:
            break
    return lines


def _keys(rows: list[dict[str, Any]]) -> set[str]:
    keys = set()
    for row in rows:
        ticker = _norm_ticker(row.get("ticker"))
        if ticker:
            keys.add(ticker)
        elif row.get("name"):
            keys.add("n:" + str(row["name"]))
    return keys


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def card_from_structured(data: dict | None, stock_db: dict | None = None) -> dict[str, Any] | None:
    data = data or {}
    date_str = str(data.get("date") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        return None
    name_to_ticker, ticker_to_name = load_stock_maps(stock_db)
    bought = _flow_list(data.get("safe_havens_bought"), name_to_ticker)
    sold = _flow_list(data.get("heavyweights_dumped"), name_to_ticker)
    for row in bought + sold:
        if row.get("ticker") and not row.get("name"):
            row["name"] = ticker_to_name.get(row["ticker"], row["ticker"])
    index_action = _clean(data.get("index_action") or "")
    pct = parse_index_pct(index_action)
    frame = data.get("editorial_frame") or {}
    frame_label = ""
    if isinstance(frame, dict):
        frame_label = _clean(frame.get("label") or frame.get("frame") or "")
    return {
        "date": date_str,
        "index_action": index_action,
        "index_pct": pct,
        "index_dir": index_dir(pct),
        "bought": bought,
        "sold": sold,
        "predictions": _prediction_rows(data.get("prediction_targets")),
        "prev_day_picks_result": _result_lines(data.get("prev_day_picks_result")),
        "frame_label": frame_label,
        "next_date": None,
        "next_day_results": [],
    }


def card_from_live(
    date_str: str,
    market_data: dict | None = None,
    institutional_data: dict | None = None,
    editorial_frame: dict | None = None,
    stock_db: dict | None = None,
) -> dict[str, Any] | None:
    from modules.structured_facts import build_local_structured_facts

    facts = build_local_structured_facts(
        date_str=date_str,
        market_data=market_data,
        institutional_data=institutional_data,
    )
    if editorial_frame:
        facts["editorial_frame"] = editorial_frame
    return card_from_structured(facts, stock_db=stock_db)


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.loads(fh.read())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_history(reports_dir: str | None = None, stock_db: dict | None = None) -> list[dict[str, Any]]:
    reports_dir = reports_dir or DEFAULT_REPORTS_DIR
    by_date: dict[str, dict[str, Any]] = {}
    paths = sorted(
        iter_structured_data_paths(reports_dir),
        key=lambda path: (
            date_from_structured_path(path) or "",
            0 if "/daily/" in path.replace("\\", "/") else 1,
            path,
        ),
    )
    for path in paths:
        date_str = date_from_structured_path(path)
        if not date_str or date_str in by_date:
            continue
        data = _read_json(path)
        if not data:
            continue
        if not data.get("date"):
            data["date"] = date_str
        card = card_from_structured(data, stock_db=stock_db)
        if card:
            by_date[date_str] = card
    dates = sorted(by_date)
    for i, date_str in enumerate(dates):
        if i + 1 >= len(dates):
            continue
        nxt = by_date[dates[i + 1]]
        by_date[date_str]["next_date"] = nxt["date"]
        by_date[date_str]["next_day_results"] = list(nxt.get("prev_day_picks_result") or [])
    return [by_date[d] for d in dates]


def score_pair(query: dict, hist: dict) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    q_dir = query.get("index_dir")
    h_dir = hist.get("index_dir")
    if q_dir and h_dir and q_dir == h_dir and q_dir != "flat":
        score += 22
        reasons.append("大盤同向")
    elif q_dir and h_dir and q_dir != h_dir:
        score -= 8

    q_pct = abs(query.get("index_pct") or 0)
    h_pct = abs(hist.get("index_pct") or 0)
    if q_pct >= 1.0 and h_pct >= 1.0:
        score += 8
        reasons.append("都是大波動日")
    elif q_pct < 0.35 and h_pct < 0.35:
        score += 4

    q_buy, q_sell = _keys(query.get("bought") or []), _keys(query.get("sold") or [])
    h_buy, h_sell = _keys(hist.get("bought") or []), _keys(hist.get("sold") or [])
    jb, js = _jaccard(q_buy, h_buy), _jaccard(q_sell, h_sell)
    score += round(jb * 38)
    score += round(js * 38)
    same_buy = sorted(x for x in (q_buy & h_buy) if not str(x).startswith("n:"))
    same_sell = sorted(x for x in (q_sell & h_sell) if not str(x).startswith("n:"))
    if same_buy:
        reasons.append("同買 " + "、".join(same_buy[:4]))
    if same_sell:
        reasons.append("同賣 " + "、".join(same_sell[:4]))

    q_buy_lead = (query.get("bought") or [{}])[0].get("ticker") if query.get("bought") else ""
    h_buy_lead = (hist.get("bought") or [{}])[0].get("ticker") if hist.get("bought") else ""
    q_sell_lead = (query.get("sold") or [{}])[0].get("ticker") if query.get("sold") else ""
    h_sell_lead = (hist.get("sold") or [{}])[0].get("ticker") if hist.get("sold") else ""
    if q_buy_lead and q_buy_lead == h_buy_lead:
        score += 12
        reasons.append("買超冠軍相同")
    if q_sell_lead and q_sell_lead == h_sell_lead:
        score += 12
        reasons.append("賣超冠軍相同")

    reversal = _jaccard(q_sell, h_buy) + _jaccard(q_buy, h_sell)
    similar_flow = jb + js
    if reversal > similar_flow + 0.12:
        score = round(score * 0.4)
        reasons.append("較像隔日翻臉，不是同構盤")

    q_pred = {row.get("ticker") for row in (query.get("predictions") or []) if row.get("ticker")}
    h_pred = {row.get("ticker") for row in (hist.get("predictions") or []) if row.get("ticker")}
    if q_pred and h_pred and q_pred & h_pred:
        score += 6
        reasons.append("預測標的重疊")

    return int(score), reasons


def _flow_preview(rows: list[dict[str, Any]], limit=3) -> str:
    bits = []
    for row in rows[:limit]:
        name = row.get("name") or row.get("ticker") or ""
        ticker = row.get("ticker") or ""
        label = f"{ticker} {name}".strip() if ticker and name and ticker not in name else (name or ticker)
        amount = row.get("amount")
        if amount is not None:
            label = f"{label} ({amount:+.1f}億)"
        bits.append(label)
    return "、".join(bits) if bits else "（無）"


def _match_payload(hist: dict, score: int, reasons: list[str]) -> dict[str, Any]:
    preds = []
    for row in hist.get("predictions") or []:
        preds.append(
            {
                "ticker": row.get("ticker") or "",
                "name": row.get("name") or "",
                "direction": row.get("direction") or "",
                "trigger": row.get("trigger") or "",
            }
        )
    return {
        "date": hist.get("date"),
        "score": score,
        "reasons": reasons[:5],
        "index_action": hist.get("index_action") or "",
        "index_pct": hist.get("index_pct"),
        "bought": _flow_preview(hist.get("bought") or []),
        "sold": _flow_preview(hist.get("sold") or []),
        "predictions": preds,
        "next_date": hist.get("next_date"),
        "next_day_results": list(hist.get("next_day_results") or [])[:4],
        "frame_label": hist.get("frame_label") or "",
    }


def find_similar(
    query: dict | None,
    history: list[dict[str, Any]] | None = None,
    *,
    exclude_date: str | None = None,
    top_k: int = TOP_K,
    min_score: int = MIN_SCORE,
) -> list[dict[str, Any]]:
    if not query:
        return []
    exclude = {exclude_date or "", query.get("date") or ""}
    ranked = []
    for hist in history or []:
        if hist.get("date") in exclude:
            continue
        score, reasons = score_pair(query, hist)
        if score < min_score:
            continue
        ranked.append((score, hist.get("date") or "", reasons, hist))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [_match_payload(hist, score, reasons) for score, _date, reasons, hist in ranked[:top_k]]


def find_similar_for_structured(
    data: dict | None,
    reports_dir: str | None = None,
    *,
    exclude_date: str | None = None,
    stock_db: dict | None = None,
    history: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    query = card_from_structured(data, stock_db=stock_db)
    if history is None:
        history = load_history(reports_dir, stock_db=stock_db)
    return find_similar(query, history, exclude_date=exclude_date or (data or {}).get("date"))


def find_similar_for_live(
    date_str: str,
    *,
    market_data: dict | None = None,
    institutional_data: dict | None = None,
    editorial_frame: dict | None = None,
    reports_dir: str | None = None,
    stock_db: dict | None = None,
) -> list[dict[str, Any]]:
    query = card_from_live(
        date_str,
        market_data=market_data,
        institutional_data=institutional_data,
        editorial_frame=editorial_frame,
        stock_db=stock_db,
    )
    history = load_history(reports_dir, stock_db=stock_db)
    return find_similar(query, history, exclude_date=date_str)


def format_for_prompt(matches: list[dict[str, Any]] | None) -> str:
    header = (
        "# 本機相似盤（結構化比對，0 Gemini）\n"
        "規則：只能引用下列日期與數字。沒有列到的歷史日禁止寫進報告，也禁止說「史上第一次」來迴避對照。\n"
        "選今日 prediction_targets 時：若相似盤顯示同一類押注隔日常失敗，要降權或寫明為何今天仍押。"
    )
    if not matches:
        return header + "\n（未找到足夠相似的歷史盤。禁止編造日期。寫「本日無足夠相似歷史盤可對照。」一句即可。）"
    blocks = [header]
    for row in matches:
        pred_bits = []
        for pred in row.get("predictions") or []:
            bit = " ".join(x for x in (pred.get("ticker"), pred.get("name"), pred.get("direction")) if x)
            trigger = pred.get("trigger") or ""
            pred_bits.append(f"{bit}｜{trigger}" if trigger else bit)
        results = row.get("next_day_results") or []
        next_date = row.get("next_date") or "尚無下一交易日"
        result_text = "；".join(results) if results else "尚無隔日節奏結果"
        reason_text = "、".join(row.get("reasons") or []) or "結構相近"
        blocks.append(
            f"## {row.get('date')}（分數 {row.get('score')}；{reason_text}）\n"
            f"- 大盤：{row.get('index_action') or '（無）'}\n"
            f"- 買超：{row.get('bought') or '（無）'}\n"
            f"- 賣超：{row.get('sold') or '（無）'}\n"
            f"- 當時預測：{'；'.join(pred_bits) if pred_bits else '（當日無正式預測）'}\n"
            f"- 隔日節奏（{next_date}）：{result_text}"
        )
    return "\n\n".join(blocks)


def attach_similar_sessions(structured_data: dict | None, reports_dir: str | None = None) -> dict:
    structured_data = structured_data if isinstance(structured_data, dict) else {}
    try:
        matches = find_similar_for_structured(structured_data, reports_dir)
    except Exception:
        matches = []
    structured_data["similar_sessions"] = matches
    return structured_data
