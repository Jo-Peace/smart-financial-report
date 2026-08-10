from dataclasses import dataclass
import datetime
import os

from modules.report_paths import resolve_shared_artifact, shared_dir


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
    run_date: str
    now: datetime.datetime


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
        run_date=datetime.datetime.now().strftime("%Y-%m-%d"),
        now=datetime.datetime.now(),
    )
