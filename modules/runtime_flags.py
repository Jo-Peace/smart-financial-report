"""
Visibility for environment flags that silently change /1 behaviour.

The flags themselves are still read where they are used (pipeline_context,
data_quality_gate, finmind_client, ...). This module only reports which
non-default / risky values are active at start-up, so a stale `.env` entry such
as DATA_QUALITY_GATE=off cannot quietly disable a safety check for weeks.
See docs/env_flags.md for the full table.
"""

from __future__ import annotations

import os
import re

# Truthy sets copied from each reader, so the banner never reports a flag the
# reader ignores (e.g. ALLOW_EARLY_RUN=on is NOT honoured by pipeline_context).
_READER_TRUE = {"1", "true", "yes", "y"}  # data_quality_gate, pipeline_context, monthly_consolidate
_FINMIND_TRUE = {"1", "true", "yes", "on"}  # finmind_client.finmind_fallback_enabled
_OFF = {"off", "0", "false", "no"}  # data_quality_gate.apply_data_quality_gate "off" modes
_GATE_MODES = {"strict", "warn"} | _OFF
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")  # pipeline_context.stale_session_target

# (legacy alias, preferred name)
DEPRECATED_ALIASES = (
    ("THUMBNAIL_STYLE", "DAILY_VISUAL_THEME"),
    ("FORCE_RUN_BEFORE_1610", "ALLOW_EARLY_RUN"),
    ("DATA_QUALITY_GATE_MODE", "DATA_QUALITY_GATE"),
    ("FINMIND_API_TOKEN", "FINMIND_TOKEN"),
    ("ALLOW_HOLIDAY_RUN", "ALLOW_STALE_SESSION"),
)


def _get(env, name, default=""):
    value = env.get(name)
    return default if value is None else str(value).strip()


def _first(env, *names, default=""):
    """Same precedence as ``os.getenv(a, os.getenv(b, default))`` used by the readers."""
    for name in names:
        value = env.get(name)
        if value is not None:
            return str(value).strip()
    return default


def _report_safety_notices(env) -> list[tuple[str, str]]:
    """Item 8 post-generation checks (report_fact_check / analyzer): warn when relaxed."""
    notices: list[tuple[str, str]] = []
    # report_fact_check._mode: annotate (default) | warn | off; any other value
    # still checks but never annotates (only "annotate" annotates).
    fact = _get(env, "REPORT_FACT_CHECK", "annotate").lower() or "annotate"
    if fact == "off":
        notices.append(("warn", "REPORT_FACT_CHECK=off：日報事實核對已關閉，錯誤名次／金額／漲跌會直接發布"))
    elif fact == "warn":
        notices.append(("warn", "REPORT_FACT_CHECK=warn：日報事實核對只記錄，不在日報內標註更正"))
    elif fact != "annotate":
        notices.append(
            ("warn", f"REPORT_FACT_CHECK={fact} 不是有效值（annotate / warn / off）：目前只核對、不標註，請修正拼字")
        )
    # report_fact_check._local_tables_enabled: 0 / false / off / no disable it.
    if _get(env, "REPORT_LOCAL_TABLES", "1").lower() in {"0", "false", "off", "no"}:
        notices.append(("warn", "REPORT_LOCAL_TABLES=0：法人／成交值表不再以本機資料重建，Gemini 抄錯的表格會發布"))
    # analyzer._daily_report_problems: strict (default) | warn | off; others act as strict.
    section = _get(env, "REPORT_SECTION_CHECK", "strict").lower() or "strict"
    if section == "off":
        notices.append(("warn", "REPORT_SECTION_CHECK=off：日報缺少核心章節也會發布"))
    elif section == "warn":
        notices.append(("warn", "REPORT_SECTION_CHECK=warn：日報缺少核心章節只提示、不重試"))
    elif section != "strict":
        notices.append(("info", f"REPORT_SECTION_CHECK={section} 不是有效值（strict / warn / off）：目前視同 strict"))
    return notices


def _gemini_budget_notices(env) -> list[tuple[str, str]]:
    """Item 8 Gemini fallback / budget knobs (gemini_client, ai_usage_guard)."""
    notices: list[tuple[str, str]] = []
    fallback = env.get("GEMINI_FALLBACK_MODELS")
    if fallback is not None:
        value = str(fallback).strip()
        # gemini_client._DISABLED_VALUES
        if value.lower() in {"", "none", "off", "0", "false"}:
            notices.append(("warn", f"GEMINI_FALLBACK_MODELS={value or '(空)'}：備援模型已停用，主模型 503／下架時日報直接失敗"))
        else:
            notices.append(("info", f"GEMINI_FALLBACK_MODELS={value}（預設 gemini-2.5-flash → gemini-2.5-flash-lite）"))
    after = _get(env, "GEMINI_FALLBACK_AFTER_ATTEMPTS")
    if after:
        notices.append(("info", f"GEMINI_FALLBACK_AFTER_ATTEMPTS={after}（預設：最後一次重試才改用備援模型）"))
    reserved = _get(env, "GEMINI_RESERVED_CALLS")
    if reserved == "0":
        notices.append(("warn", "GEMINI_RESERVED_CALLS=0：非優先任務可用光每日額度，日報／包裝可能沒有 Gemini 額度"))
    elif reserved and reserved != "3":
        notices.append(("info", f"GEMINI_RESERVED_CALLS={reserved}（預設 3）"))
    weight = _get(env, "GEMINI_ERROR_CALL_WEIGHT")
    if weight and weight != "0.25":
        notices.append(("info", f"GEMINI_ERROR_CALL_WEIGHT={weight}（預設 0.25；失敗呼叫計入每日額度的權重）"))
    for name in ("GEMINI_LIGHT_THINKING_LEVEL", "GEMINI_LIGHT_THINKING_BUDGET"):
        if _get(env, name):
            notices.append(("info", f"{name}={_get(env, name)}（輕量任務的 thinking 設定）"))
    attempts = _get(env, "YOUTUBE_PACKAGE_MAX_ATTEMPTS")
    if attempts and attempts != "3":
        notices.append(("info", f"YOUTUBE_PACKAGE_MAX_ATTEMPTS={attempts}（預設 3）"))
    return notices


def collect_flag_notices(env=None) -> list[tuple[str, str]]:
    """Return [(level, message)] for active risky / non-default flags. level: warn|info."""
    env = os.environ if env is None else env
    notices: list[tuple[str, str]] = []

    gate = _first(env, "DATA_QUALITY_GATE", "DATA_QUALITY_GATE_MODE", default="strict").lower() or "strict"
    if gate in _OFF:
        notices.append(("warn", f"資料品質閘門已關閉（DATA_QUALITY_GATE={gate}）：異常 TWSE 資料不會被擋下"))
    elif gate == "warn":
        notices.append(("warn", "資料品質閘門為 warn 模式：fail 只提示、不停止 /1"))
    elif gate not in _GATE_MODES:
        # apply_data_quality_gate still stops on failures for an unknown mode,
        # but without its usual stop message: surface the probable typo.
        notices.append(
            ("warn", f"DATA_QUALITY_GATE={gate} 不是有效值（strict / warn / off）：目前視同 strict，請修正拼字")
        )

    allow_fail = _get(env, "ALLOW_DATA_QUALITY_FAIL").lower()
    if allow_fail in _READER_TRUE:
        notices.append(("warn", "ALLOW_DATA_QUALITY_FAIL=1：閘門 fail 也會繼續 /1"))
    elif allow_fail and allow_fail not in _OFF:
        notices.append(("info", f"ALLOW_DATA_QUALITY_FAIL={allow_fail} 不會生效（只接受 1 / true / yes / y）"))

    early = _first(env, "ALLOW_EARLY_RUN", "FORCE_RUN_BEFORE_1610").lower()
    if early in _READER_TRUE:
        notices.append(("warn", "提早執行已啟用（ALLOW_EARLY_RUN）：16:10 前也會跑 /1，資料可能未齊"))
    elif early and early not in _OFF:
        notices.append(("info", f"ALLOW_EARLY_RUN={early} 不會生效（只接受 1 / true / yes / y）"))

    # pipeline_context.allow_stale_session honours either name on its own;
    # stale_session_target reads a YYYY-MM-DD value (preferred name first).
    stale_target = next((_get(env, n) for n in ("ALLOW_STALE_SESSION", "ALLOW_HOLIDAY_RUN") if _get(env, n)), "")
    if _ISO_DATE.fullmatch(stale_target):
        notices.append(
            (
                "warn",
                f"ALLOW_STALE_SESSION={stale_target}：明確補跑 {stale_target}，產出日期改標為該日並覆寫該日產出"
                "（法人資料日不是這天就不改標；用完請移除）",
            )
        )
    elif any(_get(env, name).lower() in _READER_TRUE for name in ("ALLOW_STALE_SESSION", "ALLOW_HOLIDAY_RUN")):
        notices.append(
            (
                "warn",
                "ALLOW_STALE_SESSION=1：休市日會改產出最近交易日的盤後，產出日期改標為該資料日"
                "（交易日不改標；補跑前一交易日請設 =YYYY-MM-DD）",
            )
        )

    if _get(env, "TWSE_ALLOW_INSECURE_SSL", "1").lower() in _OFF:
        notices.append(("info", "TWSE_ALLOW_INSECURE_SSL=0：證交所憑證失敗時不使用 verify=False 備援"))

    if _get(env, "MARKET_EXTRAS", "1").lower() in _OFF:
        notices.append(("info", "MARKET_EXTRAS=0：略過期貨／融資券／上櫃補充資料"))

    concurrency = _get(env, "TWSE_MAX_CONCURRENCY")
    if concurrency and concurrency != "3":
        notices.append(("info", f"TWSE_MAX_CONCURRENCY={concurrency}（預設 3）"))

    inflight = _get(env, "INFLIGHT_PRICE_FETCH_MAX")
    if inflight and inflight != "15":
        notices.append(("info", f"INFLIGHT_PRICE_FETCH_MAX={inflight}（預設 15；0 = 在途預測不補抓價）"))

    limit = _get(env, "GEMINI_DAILY_CALL_LIMIT")
    if limit == "0":
        notices.append(("warn", "GEMINI_DAILY_CALL_LIMIT=0：Gemini 每日呼叫上限已停用"))

    notices.extend(_report_safety_notices(env))
    notices.extend(_gemini_budget_notices(env))

    if _get(env, "FINMIND_FALLBACK").lower() in _FINMIND_TRUE:
        has_token = bool(_get(env, "FINMIND_TOKEN") or _get(env, "FINMIND_API_TOKEN"))
        notices.append(("info", f"FinMind 備援已啟用（token {'已設定' if has_token else '未設定，備援會略過'}）"))

    news_mode = _get(env, "NEWS_MODE", "smart").lower() or "smart"
    if news_mode != "smart":
        notices.append(("info", f"NEWS_MODE={news_mode}"))

    wiki_mode = _get(env, "KNOWLEDGE_WIKI_MODE", "local").lower() or "local"
    if wiki_mode != "local":
        notices.append(("info", f"KNOWLEDGE_WIKI_MODE={wiki_mode}" + ("（會消耗 Gemini）" if wiki_mode == "ai" else "")))

    prompt_mode = _get(env, "NOTEBOOKLM_PROMPT_MODE", "local").lower() or "local"
    if prompt_mode != "local":
        notices.append(("info", f"NOTEBOOKLM_PROMPT_MODE={prompt_mode}"))

    gap = _get(env, "VALIDATION_MAX_TRADING_GAP")
    if gap and gap != "1":
        notices.append(("info", f"VALIDATION_MAX_TRADING_GAP={gap}（預設 1）"))

    if _get(env, "SKIP_MONTHLY_CONSOLIDATE").lower() in _READER_TRUE:
        notices.append(("info", "SKIP_MONTHLY_CONSOLIDATE=1：略過月初整理"))

    for legacy, preferred in DEPRECATED_ALIASES:
        if _get(env, legacy) and not _get(env, preferred):
            notices.append(("info", f"{legacy} 是舊名稱，請改用 {preferred}"))

    return notices


def print_active_flags(prefix: str = "  ", env=None) -> list[tuple[str, str]]:
    notices = collect_flag_notices(env)
    if not notices:
        print(f"{prefix}🚩 環境旗標：全部為預設值")
        return notices
    print(f"{prefix}🚩 生效中的非預設環境旗標：")
    for level, message in notices:
        icon = "⚠️ " if level == "warn" else "ℹ️ "
        print(f"{prefix}  {icon}{message}")
    return notices
