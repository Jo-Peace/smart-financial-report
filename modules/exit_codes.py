"""
Process exit codes for /1 (main.py), shared with run_daily.sh and cli.py.

0 means the daily artifacts were written. Every controlled stop gets its own
non-zero code so schedulers and agents can tell "retry later" from "broken".
"""

EXIT_OK = 0
EXIT_UNHANDLED = 1  # Python's default for an uncaught exception (traceback in the log)
EXIT_DATA_NOT_READY = 2  # TWSE institutional data missing / not aligned yet: retry in a few minutes
EXIT_QUALITY_GATE = 3  # data quality gate stopped the run
EXIT_GEMINI_FAILED = 4  # report generation failed (quota / API error)
EXIT_PERSIST_FAILED = 5  # structured_data extraction failed; daily_report.md kept
EXIT_CONFIG = 6  # missing API keys / configuration
EXIT_TOO_EARLY = 7  # before the 16:10 data cut-off
EXIT_NOT_SESSION = 8  # run_date is not a TWSE session (holiday / weekend); ALLOW_STALE_SESSION=1 back-fills

DESCRIPTIONS = {
    EXIT_OK: "成功",
    EXIT_UNHANDLED: "未預期例外（請看 log 的 traceback）",
    EXIT_DATA_NOT_READY: "資料未就緒（法人資料未發布或日期未對齊），稍後重跑",
    EXIT_QUALITY_GATE: "資料品質閘門擋下",
    EXIT_GEMINI_FAILED: "Gemini 日報生成失敗",
    EXIT_PERSIST_FAILED: "structured_data 萃取失敗（日報已保留，可用 resume 補跑）",
    EXIT_CONFIG: "設定錯誤（缺 API key）",
    EXIT_TOO_EARLY: "16:10 前不執行",
    EXIT_NOT_SESSION: "今日非台股交易日，未執行（補跑最近交易日請設 ALLOW_STALE_SESSION=1）",
}

RETRYABLE = frozenset({EXIT_DATA_NOT_READY, EXIT_TOO_EARLY})


def describe(code) -> str:
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "未知"
    return DESCRIPTIONS.get(code, f"未知退出碼 {code}")


def normalize(result) -> int:
    """Map main()'s return value to an exit code (None = legacy success path)."""
    if result is None:
        return EXIT_OK
    try:
        return int(result)
    except (TypeError, ValueError):
        return EXIT_UNHANDLED
