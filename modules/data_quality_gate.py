"""
Deterministic pre-flight checks before /1 spends Gemini or Tavily quota.

No AI calls — only inspects TWSE-derived payloads already fetched in main.py.
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass, field

from modules.tickers import bare_ticker


@dataclass
class GateIssue:
    level: str  # "fail" | "warn"
    code: str
    message: str


@dataclass
class DataQualityReport:
    run_date: str
    issues: list[GateIssue] = field(default_factory=list)

    @property
    def failures(self) -> list[GateIssue]:
        return [issue for issue in self.issues if issue.level == "fail"]

    @property
    def warnings(self) -> list[GateIssue]:
        return [issue for issue in self.issues if issue.level == "warn"]


def _gate_mode() -> str:
    return os.getenv("DATA_QUALITY_GATE", os.getenv("DATA_QUALITY_GATE_MODE", "strict")).strip().lower()


def _force_continue() -> bool:
    return os.getenv("ALLOW_DATA_QUALITY_FAIL", "").strip().lower() in {"1", "true", "yes", "y"}


def _parse_date(value: str | None) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _is_weekend(day: datetime.date) -> bool:
    return day.weekday() >= 5


def _calendar_covers(day: datetime.date) -> bool:
    try:
        from modules.twse_calendar import calendar_covers

        return bool(calendar_covers(day))
    except Exception:
        return False


def _is_session(day: datetime.date) -> bool:
    """TWSE session per the holiday calendar; falls back to weekday-only."""
    try:
        from modules.twse_calendar import is_twse_session

        return bool(is_twse_session(day))
    except Exception:
        return not _is_weekend(day)


INDEX_SYMBOLS = ("^TWII", "TWII", "IX0001")
KEY_SYMBOLS = ("2330.TW",)
# Share leaders (top 30 by |foreign shares|) that must carry a same-session price.
PRICING_COVERAGE_MIN = 0.95


def _zero_pct_ratio(rows: list[dict], key: str = "pct_change") -> float:
    if not rows:
        return 0.0
    zeros = sum(1 for row in rows if not row.get("ex_rights") and _float(row.get(key), 0.0) == 0.0)
    return zeros / len(rows)


def _float(value, default=None):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_ticker(value) -> str:
    return bare_ticker(value)


def _market_row(market_data: dict, ticker: str) -> dict | None:
    ticker = _normalize_ticker(ticker)
    for key in (ticker, f"{ticker}.TW", f"{ticker}.TWO"):
        row = (market_data or {}).get(key)
        if row:
            return row
    return None


def evaluate_data_quality(
    *,
    run_date: str,
    institutional_data: dict | None,
    market_data: dict | None,
    volume_data: list | None,
    volume_date: str | None = None,
    yesterday_predictions: list | None = None,
    expected_symbols: list | None = None,
    tls_events: list | None = None,
) -> DataQualityReport:
    """
    ``tls_events``: TWSE requests that only succeeded without certificate
    verification this run (``modules.twse_http.insecure_events()``).
    """
    report = DataQualityReport(run_date=run_date)
    institutional_data = institutional_data or {}
    market_data = market_data or {}
    volume_data = volume_data or []
    expected_symbols = expected_symbols or list(market_data.keys())

    inst_date = _parse_date(institutional_data.get("data_date"))
    vol_date = _parse_date(volume_date)
    run_day = _parse_date(run_date)

    top_buy = institutional_data.get("top_buy") or []
    top_sell = institutional_data.get("top_sell") or []
    if not top_buy and not top_sell:
        report.issues.append(
            GateIssue(
                "fail",
                "institutional_missing",
                "三大法人買賣超為空，今日盤後資料可能尚未發布。",
            )
        )

    if not volume_data:
        report.issues.append(
            GateIssue(
                "warn",
                "volume_missing",
                "成交量前 20 名為空，量價分析與驗收證據會變弱。",
            )
        )
    else:
        missing_price = sum(1 for row in volume_data if _float(row.get("close_price"), 0.0) <= 0)
        if missing_price / len(volume_data) >= 0.6:
            report.issues.append(
                GateIssue(
                    "fail",
                    "volume_price_missing",
                    f"成交量排行有 {missing_price}/{len(volume_data)} 檔缺少收盤價，資料解析可能異常。",
                )
            )
        elif missing_price / len(volume_data) >= 0.3:
            report.issues.append(
                GateIssue(
                    "warn",
                    "volume_price_sparse",
                    f"成交量排行有 {missing_price}/{len(volume_data)} 檔缺少收盤價。",
                )
            )

        zero_ratio = _zero_pct_ratio(volume_data)
        if zero_ratio >= 0.75:
            report.issues.append(
                GateIssue(
                    "fail",
                    "volume_pct_flat",
                    f"成交量排行有 {zero_ratio:.0%} 檔漲跌幅為 0.00%，疑似異常資料。",
                )
            )
        elif zero_ratio >= 0.5:
            report.issues.append(
                GateIssue(
                    "warn",
                    "volume_pct_suspect",
                    f"成交量排行有 {zero_ratio:.0%} 檔漲跌幅為 0.00%，請留意。",
                )
            )

    if expected_symbols:
        ok_symbols = 0
        flat_symbols = 0
        for symbol in expected_symbols:
            row = _market_row(market_data, symbol)
            if row and _float(row.get("price")) is not None:
                ok_symbols += 1
                # 除權息 X rows have pct_change None on purpose — not a flat tape.
                if not row.get("ex_rights") and _float(row.get("pct_change"), 0.0) == 0.0:
                    flat_symbols += 1
        if ok_symbols == 0:
            report.issues.append(
                GateIssue(
                    "fail",
                    "market_data_missing",
                    "動態標的行情全部缺失，無法生成可信日報。",
                )
            )
        elif ok_symbols < max(1, len(expected_symbols) // 2):
            report.issues.append(
                GateIssue(
                    "warn",
                    "market_data_partial",
                    f"動態標的僅 {ok_symbols}/{len(expected_symbols)} 檔有行情。",
                )
            )
        if ok_symbols and flat_symbols / ok_symbols >= 0.8:
            report.issues.append(
                GateIssue(
                    "warn",
                    "market_pct_flat",
                    f"行情標的有 {flat_symbols}/{ok_symbols} 檔漲跌幅為 0.00%。",
                )
            )

    if inst_date and vol_date and inst_date != vol_date:
        report.issues.append(
            GateIssue(
                "fail",
                "trade_date_mismatch",
                f"法人資料日 {inst_date} 與成交量日 {vol_date} 不一致。",
            )
        )

    if run_day and inst_date and inst_date < run_day:
        if _is_session(run_day):
            # Publishing the previous session's tape under today's date is wrong data.
            hint = (
                ""
                if _calendar_covers(run_day)
                else f"注意：休市日曆沒有 {run_day.year} 年資料，今天可能其實是休市日，請先補 data/twse_closed_days.json。"
            )
            report.issues.append(
                GateIssue(
                    "fail",
                    "institutional_stale",
                    f"執行日 {run_day} 是交易日，但法人資料日仍是 {inst_date}（今日資料尚未發布）。"
                    f"要補跑 {inst_date} 請明確設定 ALLOW_STALE_SESSION={inst_date}（產出會改標為該日並覆寫該日產出；"
                    "交易日上 ALLOW_STALE_SESSION=1 不會改標）。" + hint,
                )
            )
        else:
            report.issues.append(
                GateIssue(
                    "warn",
                    "non_session_run",
                    f"執行日 {run_day} 不是台股交易日，資料為 {inst_date} 的盤後。",
                )
            )

    if run_day and not _calendar_covers(run_day):
        report.issues.append(
            GateIssue(
                "warn",
                "calendar_year_missing",
                f"休市日曆沒有 {run_day.year} 年資料，交易日判斷只看週末；請補上證交所公告的休市日。",
            )
        )

    _check_bar_dates(report, market_data, expected_symbols, inst_date or vol_date)
    _check_institutional_pricing(report, institutional_data, inst_date)

    unverified_hosts = sorted({str(e.get("host") or "") for e in (tls_events or []) if e})
    if institutional_data.get("tls_unverified") and not unverified_hosts:
        unverified_hosts = ["www.twse.com.tw"]
    if unverified_hosts:
        report.issues.append(
            GateIssue(
                "warn",
                "tls_unverified",
                f"部分 TWSE 資料是在未驗證憑證的連線下取得：{'、'.join(h for h in unverified_hosts if h)}。",
            )
        )

    targets = yesterday_predictions or []
    if targets:
        checkable = 0
        for target in targets:
            ticker = _normalize_ticker(target.get("id"))
            market = _market_row(market_data, ticker)
            if market and _float(market.get("pct_change")) is not None:
                checkable += 1
                continue
            for row in volume_data:
                if _normalize_ticker(row.get("id")) == ticker and _float(row.get("pct_change")) is not None:
                    checkable += 1
                    break
        if checkable == 0:
            report.issues.append(
                GateIssue(
                    "warn",
                    "validation_blind",
                    f"前次有 {len(targets)} 檔 prediction_targets，但今日行情皆無法驗收漲跌幅。",
                )
            )
        elif checkable < len(targets):
            report.issues.append(
                GateIssue(
                    "warn",
                    "validation_partial",
                    f"前次 prediction_targets 僅 {checkable}/{len(targets)} 檔能驗收漲跌幅。",
                )
            )

    return report


def _check_bar_dates(report: DataQualityReport, market_data: dict, expected_symbols: list, trade_date) -> None:
    """Every market payload carries bar_date; it must be the reported session."""
    index_row = None
    for sym in INDEX_SYMBOLS:
        index_row = (market_data or {}).get(sym) or index_row
    wants_index = any(str(s).upper() in INDEX_SYMBOLS for s in expected_symbols or [])
    if wants_index and not (index_row and _float(index_row.get("price")) is not None):
        report.issues.append(
            GateIssue(
                "fail",
                "index_missing",
                "加權指數（^TWII）Yahoo 與 TWSE 備援都取不到；大盤漲跌點數不能交給模型自行填寫。",
            )
        )
    key_missing = set()
    for sym in KEY_SYMBOLS:
        if sym in (expected_symbols or []) and not _market_row(market_data, sym):
            key_missing.add(sym)
            report.issues.append(
                GateIssue(
                    "fail",
                    "key_symbol_missing",
                    f"{sym} 行情缺失（Yahoo 與 TWSE 備援都取不到）；台積電漲跌與驗收不能交給模型自行填寫。",
                )
            )

    if not trade_date:
        return
    want = trade_date.isoformat()
    for sym in KEY_SYMBOLS:
        row = _market_row(market_data, sym)
        if sym in key_missing or not row:
            continue
        bar_date = str(row.get("bar_date") or "")[:10]
        if bar_date != want:
            report.issues.append(
                GateIssue(
                    "fail",
                    "key_bar_date_mismatch",
                    f"{sym} K 棒日期 {bar_date or '未知'} 不是交易日 {want}；前一交易日的漲跌會被當成今日數字與驗收證據。",
                )
            )
        elif row.get("ex_rights") and _float(row.get("pct_change")) is None:
            # Same-session close is known; only the comparable move is not.
            report.issues.append(
                GateIssue(
                    "warn",
                    "key_symbol_ex_rights",
                    f"{sym} {want} 為除權息（X，不比價）：只有收盤價，日報寫「除權息不比價」，不寫漲跌幅、不驗收。",
                )
            )
    key_norm = {_normalize_ticker(sym) for sym in KEY_SYMBOLS}
    mismatched = []
    for symbol, row in (market_data or {}).items():
        if not row:
            continue
        bar_date = str(row.get("bar_date") or "")[:10]
        if not bar_date or bar_date == want:
            continue
        if _normalize_ticker(symbol) in key_norm:
            continue  # already a fail above
        if str(symbol).upper() in INDEX_SYMBOLS:
            report.issues.append(
                GateIssue(
                    "fail",
                    "index_bar_date_mismatch",
                    f"加權指數 K 棒日期 {bar_date} 不是交易日 {want}，大盤漲跌會是別天的數字。",
                )
            )
        else:
            mismatched.append(f"{symbol}@{bar_date}")
    if mismatched:
        report.issues.append(
            GateIssue(
                "warn",
                "bar_date_mismatch",
                f"{len(mismatched)} 檔行情 K 棒不是 {want}：{'、'.join(mismatched[:6])}"
                "（main.py 會改標為「數據無法取得」，不進分析與驗收）。",
            )
        )


def drop_off_session_bars(market_data: dict | None, trade_date) -> tuple[dict, list[str]]:
    """
    Copy of ``market_data`` with every payload whose ``bar_date`` is not
    ``trade_date`` replaced by None, plus the dropped symbols.

    get_stock_data deliberately keeps a dated stale Yahoo bar so the gate can
    see the mismatch; after the gate it must not reach the prompt (narrated as
    today's move) or validate_prediction_targets (scored as today's 驗收).
    Payloads without bar_date are kept (legacy callers).
    """
    want = trade_date.isoformat() if isinstance(trade_date, datetime.date) else str(trade_date or "")[:10]
    out = dict(market_data or {})
    dropped = []
    if not want:
        return out, dropped
    for symbol, row in list(out.items()):
        if not row:
            continue
        bar_date = str(row.get("bar_date") or "")[:10]
        if bar_date and bar_date != want:
            out[symbol] = None
            dropped.append(f"{symbol}@{bar_date}")
    return out, dropped


def _check_institutional_pricing(report: DataQualityReport, institutional_data: dict, inst_date) -> None:
    """億元 amounts must come from same-session closes."""
    institutional_data = institutional_data or {}
    want = inst_date.isoformat() if inst_date else ""
    bad = []
    for row in (institutional_data.get("top_buy") or []) + (institutional_data.get("top_sell") or []):
        if _float((row or {}).get("est_amount")) is None:
            continue
        price_date = str(row.get("price_date") or "")[:10]
        if want and price_date and price_date != want:
            bad.append(f"{row.get('id')} {row.get('name') or ''}@{price_date}".strip())
    if bad:
        report.issues.append(
            GateIssue(
                "fail",
                "institutional_price_stale",
                f"法人買賣超金額用了非交易日 {want} 的股價：{'、'.join(bad[:5])}。",
            )
        )
    pricing = institutional_data.get("pricing") or {}
    coverage = pricing.get("coverage_top30") or {}
    ratio = _float(coverage.get("ratio"))
    if ratio is not None and coverage.get("checked") and ratio < PRICING_COVERAGE_MIN:
        names = "、".join(
            f"{u.get('id')} {u.get('name') or ''}".strip() for u in (coverage.get("unpriced") or [])[:5]
        )
        report.issues.append(
            GateIssue(
                "fail",
                "institutional_pricing_sparse",
                f"外資張數前 {coverage.get('checked')} 名只有 {coverage.get('priced')} 檔有同日股價"
                f"（低於 {PRICING_COVERAGE_MIN:.0%}；未列入金額榜：{names}）。",
            )
        )
    material = [m for m in (pricing.get("material_unpriced") or []) if m]
    if material:
        # High-price leaders (台積電 / 創意 …) sit far outside the top 30 by
        # shares, so the ratio above cannot see them silently dropping off.
        names = "、".join(
            f"{m.get('id')} {m.get('name') or ''}".strip()
            + (f"（參考約 {m['reference_amount']:+.1f}億）" if isinstance(m.get("reference_amount"), (int, float)) else "")
            for m in material[:5]
        )
        report.issues.append(
            GateIssue(
                "fail",
                "institutional_leader_unpriced",
                f"可能進入外資買賣超金額榜的個股沒有同日股價，榜單會漏掉它們：{names}。",
            )
        )
    if pricing.get("basis") == "shares":
        report.issues.append(
            GateIssue("warn", "institutional_amount_unpriced", "法人榜無同日股價，只能依張數排序，不可引用億元金額。")
        )


def format_gate_report(report: DataQualityReport) -> str:
    lines = ["", "🛡️  資料品質閘門（不消耗 AI 額度）", ""]
    if not report.issues:
        lines.append("  ✅ 未發現異常，可進入 Gemini 分析與日報產出。")
        return "\n".join(lines)

    for issue in report.failures:
        lines.append(f"  ❌ [{issue.code}] {issue.message}")
    for issue in report.warnings:
        lines.append(f"  ⚠️  [{issue.code}] {issue.message}")
    return "\n".join(lines)


def apply_data_quality_gate(report: DataQualityReport, *, mode: str | None = None) -> bool:
    """
    Print gate results and return True when /1 should continue.

    strict: failures stop /1 unless ALLOW_DATA_QUALITY_FAIL=1
    warn:   only print issues, always continue
    off:    skip
    """
    mode = (mode or _gate_mode() or "strict").lower()
    if mode in {"off", "0", "false", "no"}:
        return True

    print(format_gate_report(report))

    if report.failures and mode == "strict" and not _force_continue():
        print("\n  ⛔ 資料品質閘門：停止 /1，避免用異常資料消耗 Gemini 並污染正式產出。")
        print("  💡 若仍要強制執行，請設定 ALLOW_DATA_QUALITY_FAIL=1")
        return False

    if report.failures and _force_continue():
        print("\n  ⚠️  已設定 ALLOW_DATA_QUALITY_FAIL=1，忽略 fail 並繼續 /1。")

    if report.warnings:
        print("  ℹ️  有警告但允許繼續；請在日報中謹慎引用量價結論。")

    if not report.failures or mode == "warn" or _force_continue():
        if not report.issues:
            pass
        elif not report.failures:
            print("  ✅ 無阻擋級問題，繼續 /1。\n")
        return True

    return False

def _latest_session_on_or_before(day: datetime.date) -> datetime.date:
    cursor = day
    for _ in range(15):
        if _is_session(cursor):
            return cursor
        cursor -= datetime.timedelta(days=1)
    return day


def evaluate_single_stock_quality(
    *,
    run_date: str,
    price_date: str | None,
    institutional: dict | None = None,
    institutional_date: str | None = None,
    institutional_expected: bool = True,
    revenue: dict | None = None,
    revenue_expected: bool = True,
    news_count: int = 0,
    after_close: bool = True,
) -> DataQualityReport:
    """Per-ticker variant of ``evaluate_data_quality`` for the web research report.

    Never blocks generation: every issue is a ``warn`` shown to the reader and
    passed to the model as a "do not invent" instruction.
    """
    report = DataQualityReport(run_date=run_date)
    run_day = _parse_date(run_date)
    price_day = _parse_date(price_date)
    if run_day is not None:
        # Before today's close the latest complete bar is the previous session.
        ref = run_day if after_close else run_day - datetime.timedelta(days=1)
        expected = _latest_session_on_or_before(ref)
    else:
        expected = None

    if price_day is None:
        report.issues.append(GateIssue("warn", "price_date_unknown", "股價資料日期不明，無法確認是否為最新交易日"))
    elif expected and price_day < expected:
        report.issues.append(
            GateIssue("warn", "price_stale", f"股價為 {price_day.isoformat()} 資料，非最新交易日 {expected.isoformat()}")
        )

    if institutional_expected:
        inst_day = _parse_date(institutional_date)
        if not institutional:
            report.issues.append(GateIssue("warn", "institutional_missing", "本次未取得三大法人買賣超資料"))
        elif inst_day and price_day and inst_day != price_day:
            report.issues.append(
                GateIssue("warn", "institutional_date_mismatch", f"法人資料日期 {inst_day.isoformat()} 與股價日期 {price_day.isoformat()} 不同")
            )
    if revenue_expected and not (revenue and revenue.get("revenue_100m") is not None):
        report.issues.append(GateIssue("warn", "revenue_missing", "本次未取得月營收資料"))
    if not news_count:
        report.issues.append(GateIssue("warn", "news_missing", "本次未取得相關新聞"))
    return report
