"""
Canonical catalog of maintenance / YouTube / special / legacy tools.

`cli.py` and `compat/` redirects both read from this registry.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    id: str
    category: str
    script: str
    description: str
    legacy_names: tuple[str, ...] = ()
    has_main: bool = True


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        id="check-daily-workflow",
        category="maintenance",
        script="scripts/maintenance/check_daily_workflow.py",
        description="檢查 /1 + /2 產物是否齊全",
        legacy_names=("check_daily_workflow.py",),
    ),
    ToolSpec(
        id="check-complete",
        category="maintenance",
        script="scripts/maintenance/check_complete.py",
        description="快速確認法人資料是否已釋出",
        legacy_names=("check_complete.py",),
        has_main=False,
    ),
    ToolSpec(
        id="check-gemini-usage",
        category="maintenance",
        script="scripts/maintenance/check_gemini_usage.py",
        description="檢查今日 Gemini 用量",
        legacy_names=("check_gemini_usage.py",),
    ),
    ToolSpec(
        id="backfill-analytics",
        category="maintenance",
        script="scripts/maintenance/backfill_analytics.py",
        description="從 structured data 回填 analytics.db",
        legacy_names=("backfill_analytics.py",),
    ),
    ToolSpec(
        id="backfill-market-snapshots",
        category="maintenance",
        script="scripts/maintenance/backfill_market_snapshots.py",
        description="回填市場快照資料",
        legacy_names=("backfill_market_snapshots.py",),
    ),
    ToolSpec(
        id="seed-knowledge-wiki",
        category="maintenance",
        script="scripts/maintenance/seed_knowledge_wiki.py",
        description="用歷史日報灌入 Knowledge Wiki",
        legacy_names=("seed_knowledge_wiki.py",),
    ),
    ToolSpec(
        id="analytics-report",
        category="maintenance",
        script="scripts/maintenance/analytics_report.py",
        description="產生 analytics_summary.md",
        legacy_names=("analytics_report.py",),
    ),
    ToolSpec(
        id="dashboard-stock-watch",
        category="maintenance",
        script="scripts/maintenance/build_dashboard_stock_watch.py",
        description="產生 Dashboard 股票雷達摘要 JSON",
        legacy_names=(),
    ),
    ToolSpec(
        id="migrate-reports",
        category="maintenance",
        script="scripts/maintenance/migrate_report_layout.py",
        description="搬移 reports/ 到 daily/、youtube/、shared/ 結構",
        legacy_names=(),
    ),
    ToolSpec(
        id="consolidate-residuals",
        category="maintenance",
        script="scripts/maintenance/consolidate_report_residuals.py",
        description="整理殘留檔、同步 analytics/記憶複利、歸檔重複資料",
        legacy_names=(),
    ),
    ToolSpec(
        id="regen-notebooklm",
        category="maintenance",
        script="scripts/maintenance/regen_notebooklm.py",
        description="一鍵重產 NotebookLM source+prompt（本機；可 --refresh-frame）",
        legacy_names=(),
    ),
    ToolSpec(
        id="compare-institutional",
        category="maintenance",
        script="scripts/maintenance/compare_institutional_sources.py",
        description="同日對照 TWSE vs FinMind 外資買賣超 top 名單",
        legacy_names=(),
    ),
    ToolSpec(
        id="compounding-brief",
        category="maintenance",
        script="scripts/maintenance/compounding_brief.py",
        description="產生複利閉環簡報（可複製/禁止/命中率 watchlist）",
        legacy_names=(),
    ),


    ToolSpec(
        id="research-stock",
        category="maintenance",
        script="scripts/maintenance/research_stock.py",
        description="個股預測履歷卡（命中/trigger；服務預測準度）",
        legacy_names=(),
    ),
    ToolSpec(
        id="recompute-predictions",
        category="maintenance",
        script="scripts/maintenance/recompute_prediction_results.py",
        description="重算預測帳本（交易日窗＋同窗大盤超額；預設 dry-run，--apply 先備份）",
        legacy_names=(),
    ),
    ToolSpec(
        id="backtest-scores",
        category="maintenance",
        script="scripts/maintenance/backtest_scores.py",
        description="離線評分回測（IC／分組超額／信心校準，只讀 analytics.db）",
        legacy_names=(),
    ),
    ToolSpec(
        id="repair-learning-logs",
        category="maintenance",
        script="scripts/maintenance/repair_learning_logs.py",
        description="一次性修復學習紀錄（預設 dry-run，--apply 先備份）",
        legacy_names=(),
    ),
    ToolSpec(
        id="similar-sessions",
        category="maintenance",
        script="scripts/maintenance/similar_sessions.py",
        description="本機相似盤查詢（法人榜結構對照，0 Gemini）",
        legacy_names=(),
    ),
    ToolSpec(
        id="preflight-views",
        category="youtube",
        script="scripts/youtube/preflight_views.py",
        description="/2 前置：回顧近期觀看數（建議 --refresh）",
        legacy_names=(),
    ),
    ToolSpec(
        id="regen-assets",
        category="youtube",
        script="scripts/youtube/regen_assets.py",
        description="/2：YouTube 上架包 + 封面 prompt",
        legacy_names=("regen_assets.py",),
    ),
    ToolSpec(
        id="check-youtube-package",
        category="youtube",
        script="scripts/youtube/check_youtube_package.py",
        description="檢查 /2 YouTube package 與縮圖 prompt",
        legacy_names=("check_youtube_package.py",),
    ),
    ToolSpec(
        id="review-reminder",
        category="youtube",
        script="scripts/youtube/review_reminder.py",
        description="更新 YouTube 成效並寫入 review 提醒",
        legacy_names=("review_reminder.py",),
    ),
    ToolSpec(
        id="update-from-channel",
        category="youtube",
        script="scripts/youtube/update_youtube_from_channel.py",
        description="從頻道最近影片回寫 channel_story_log",
        legacy_names=("update_youtube_from_channel.py",),
    ),
    ToolSpec(
        id="update-learning",
        category="youtube",
        script="scripts/youtube/update_youtube_learning.py",
        description="從 channel_story_log 更新成效學習 wiki",
        legacy_names=("update_youtube_learning.py",),
    ),
    ToolSpec(
        id="record-metrics",
        category="youtube",
        script="scripts/youtube/record_youtube_metrics.py",
        description="記錄 YouTube 影片指標",
        legacy_names=("record_youtube_metrics.py",),
    ),
    # --- special: weekend / US-holiday scripts (output: reports/special_topics/YYYY-MM-DD_slug/) ---
    ToolSpec(
        id="weekend",
        category="special",
        script="weekend_special_main.py",
        description="週末特輯：美股一週＋本週台股本機資料＋下個交易日觀察（呼叫 Gemini）",
    ),
    ToolSpec(
        id="us-daily",
        category="special",
        script="main_us.py",
        description="美股盤後觀察日記（休市期間用；呼叫 Gemini）",
    ),
    ToolSpec(
        id="us-weekly",
        category="special",
        script="main_weekly.py",
        description="美股週報文案＋A/B 標題與封面 prompt（呼叫 Gemini）",
    ),
    ToolSpec(
        id="regen-prompt",
        category="legacy",
        script="scripts/legacy/regen_prompt.py",
        description="一次性重產 2026-04-01 NotebookLM prompt（硬編碼）",
        legacy_names=("regen_prompt.py",),
        has_main=False,
    ),
    ToolSpec(
        id="fix-srt",
        category="legacy",
        script="scripts/legacy/fix_srt.py",
        description="修正字幕檔（歷史工具）",
        legacy_names=("fix_srt.py",),
        has_main=False,
    ),
    ToolSpec(
        id="correct-srt",
        category="legacy",
        script="scripts/legacy/correct_srt.py",
        description="修正字幕檔（歷史工具）",
        legacy_names=("correct_srt.py",),
        has_main=False,
    ),
    ToolSpec(
        id="keep-alive",
        category="legacy",
        script="scripts/legacy/keep_alive.py",
        description="Render keep-alive ping（歷史工具）",
        legacy_names=("keep_alive.py",),
        has_main=False,
    ),
    ToolSpec(
        id="list-models",
        category="legacy",
        script="scripts/legacy/list_models.py",
        description="列出可用 Gemini 模型",
        legacy_names=("list_models.py",),
        has_main=False,
    ),
    ToolSpec(
        id="test-models",
        category="legacy",
        script="scripts/legacy/test_models.py",
        description="測試模型連線",
        legacy_names=("test_models.py",),
        has_main=False,
    ),
    ToolSpec(
        id="test-twse",
        category="legacy",
        script="scripts/legacy/test_twse.py",
        description="測試 TWSE 資料抓取",
        legacy_names=("test_twse.py",),
        has_main=False,
    ),
    ToolSpec(
        id="perfect-storm-special",
        category="legacy",
        script="scripts/legacy/perfect_storm_special.py",
        description="Perfect Storm 特輯（歷史工具）",
        legacy_names=("perfect_storm_special.py",),
        has_main=False,
    ),
)


def tools_by_category() -> dict[str, list[ToolSpec]]:
    grouped: dict[str, list[ToolSpec]] = {}
    for spec in TOOL_SPECS:
        grouped.setdefault(spec.category, []).append(spec)
    return grouped


def find_tool(category: str, tool_id: str) -> ToolSpec | None:
    for spec in TOOL_SPECS:
        if spec.category == category and spec.id == tool_id:
            return spec
    return None


def find_tool_by_legacy_name(filename: str) -> ToolSpec | None:
    for spec in TOOL_SPECS:
        if filename in spec.legacy_names:
            return spec
    return None
