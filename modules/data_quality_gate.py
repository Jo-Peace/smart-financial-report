"""
Deterministic pre-flight checks before /1 spends Gemini or Tavily quota.

No AI calls — only inspects TWSE-derived payloads already fetched in main.py.
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass, field


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


def _zero_pct_ratio(rows: list[dict], key: str = "pct_change") -> float:
    if not rows:
        return 0.0
    zeros = sum(1 for row in rows if _float(row.get(key), 0.0) == 0.0)
    return zeros / len(rows)


def _float(value, default=None):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_ticker(value) -> str:
    return str(value or "").replace(".TW", "").replace(".TWO", "").strip().upper()


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
) -> DataQualityReport:
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
                if _float(row.get("pct_change"), 0.0) == 0.0:
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

    if run_day and inst_date and not _is_weekend(run_day):
        if inst_date < run_day:
            report.issues.append(
                GateIssue(
                    "warn",
                    "institutional_stale",
                    f"法人資料日 {inst_date} 早於執行日 {run_day}，可能尚未更新到今日。",
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