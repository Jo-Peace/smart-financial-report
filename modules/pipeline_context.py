from dataclasses import dataclass
import datetime
import os
import re

from modules.report_paths import resolve_shared_artifact, shared_dir
from modules.tz import now_tpe_naive


@dataclass(frozen=True)
class PipelineContext:
    base_dir: str
    reports_dir: str
    youtube_assets_dir: str
    channel_story_log: str
    tavily_api_key: str
    gemini_api_key: str
    youtube_api_key: str
    youtube_channel_id: str
    project_theme: str
    daily_visual_theme: str
    allow_early_run: bool
    news_mode: str
    tavily_daily_search_limit: int
    base_symbols: list[str]
    base_topics: list[str]
    run_date: str  # Asia/Taipei date at context build time
    now: datetime.datetime  # Asia/Taipei wall clock (naive, legacy-compatible)


def build_pipeline_context() -> PipelineContext:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    reports_dir = os.path.join(base_dir, "reports")
    shared = shared_dir(reports_dir)
    return PipelineContext(
        base_dir=base_dir,
        reports_dir=reports_dir,
        youtube_assets_dir=shared,
        channel_story_log=resolve_shared_artifact(reports_dir, "channel_story_log"),
        tavily_api_key=os.getenv("TAVILY_API_KEY"),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        youtube_api_key=os.getenv("YOUTUBE_API_KEY"),
        youtube_channel_id=os.getenv("YOUTUBE_CHANNEL_ID", "UCRWXWHHOs4iSDac1GHLsExw"),
        project_theme=os.getenv("PROJECT_THEME", ""),
        daily_visual_theme=os.getenv("DAILY_VISUAL_THEME", os.getenv("THUMBNAIL_STYLE", "")),
        allow_early_run=os.getenv("ALLOW_EARLY_RUN", os.getenv("FORCE_RUN_BEFORE_1610", "")).lower() in {"1", "true", "yes", "y"},
        news_mode=os.getenv("NEWS_MODE", "smart").strip().lower(),
        tavily_daily_search_limit=int(os.getenv("TAVILY_DAILY_SEARCH_LIMIT", "20")),
        base_symbols=["^TWII", "2330.TW"],
        base_topics=[
            "Taiwan Semiconductor",
            "台股 今日強勢族群 逆勢抗跌",
            "台股 今日成交量 法人買賣超 量價背離 最新分析",
        ],
        run_date=now_tpe_naive().strftime("%Y-%m-%d"),
        now=now_tpe_naive(),
    )


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "y"}


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def stale_session_target() -> str | None:
    """
    ``ALLOW_STALE_SESSION=YYYY-MM-DD``: the one past session an explicit
    back-fill may relabel run_date to. The preferred name wins over the alias.
    """
    for name in ("ALLOW_STALE_SESSION", "ALLOW_HOLIDAY_RUN"):
        value = os.getenv(name, "").strip()
        if value:
            return value if _ISO_DATE.fullmatch(value) else None
    return None


def allow_stale_session() -> bool:
    """Explicit opt-in to (re)generate the latest *past* session's report."""
    return _env_flag("ALLOW_STALE_SESSION") or _env_flag("ALLOW_HOLIDAY_RUN") or stale_session_target() is not None


def stale_relabel_decision(run_date, data_date, target: str | None = None) -> tuple[bool, str]:
    """
    (relabel, message) for ALLOW_STALE_SESSION when the T86 data date differs
    from run_date.

    - run_date is not a TWSE session (holiday / weekend): ``1`` relabels to
      the latest data date; a date value relabels only when it is that date.
    - run_date IS a session: T86 still on the previous session usually means
      today's data is not published yet, so ``1`` (e.g. left in .env) never
      relabels; the gate keeps ``institutional_stale`` (exit 3) instead of
      overwriting the previous day's published artifacts. Only an explicit
      ``ALLOW_STALE_SESSION=<data_date>`` back-fills that day.
    """
    from modules.twse_calendar import is_twse_session

    data_iso = str(data_date or "")[:10]
    if target and target != data_iso:
        return False, (
            f"ALLOW_STALE_SESSION={target}，但法人資料日是 {data_iso}：不改標產出日期"
            f"（要補跑 {data_iso} 請設 ALLOW_STALE_SESSION={data_iso}）。"
        )
    if not is_twse_session(run_date):
        return True, f"ALLOW_STALE_SESSION：{run_date} 休市，使用 {data_iso} 的盤後資料，產出日期改標為 {data_iso}。"
    if target:
        return True, (
            f"ALLOW_STALE_SESSION={target}：交易日 {run_date} 明確補跑 {data_iso}，"
            f"產出日期改標為 {data_iso}（會覆寫該日日報與帳本預測）。"
        )
    return False, (
        f"{run_date} 是交易日，法人資料仍停在 {data_iso}（今日資料多半尚未發布）：ALLOW_STALE_SESSION=1 "
        f"只用於休市日，不會把今天改標為 {data_iso}。要補跑 {data_iso} 請明確設 ALLOW_STALE_SESSION={data_iso}。"
    )


def session_guard(run_date, allow_stale: bool = False) -> tuple[bool, str]:
    """
    (ok, message). /1 only runs on TWSE sessions: on a holiday every TWSE
    endpoint silently falls back to the previous session, so the "today"
    report would be yesterday's tape under today's date.
    """
    from modules.twse_calendar import as_date, calendar_covers, closed_name, is_twse_session

    if is_twse_session(run_date):
        if not calendar_covers(run_date):
            year = getattr(as_date(run_date), "year", "?")
            return True, (
                f"休市日曆 data/twse_closed_days.json 沒有 {year} 年的資料，只能依週末判斷交易日；"
                f"若 {run_date} 其實是國定假日，T86 會等完重試後才以「今日資料尚未發布」停止。"
                f"請補上 {year} 年證交所休市日。"
            )
        return True, ""
    reason = closed_name(run_date) or "週末"
    if allow_stale:
        return True, f"{run_date} 休市（{reason}）；ALLOW_STALE_SESSION=1，改產出最近交易日的盤後並標示該資料日。"
    return False, f"{run_date} 不是台股交易日（{reason}），不執行 /1，避免把上一交易日的盤後寫成今天的日報。"
