# maintenance

放資料回填、健康檢查、知識庫灌入等維護腳本。

典型用途：

- `analytics_report.py`
- `backfill_analytics.py`
- `backfill_market_snapshots.py`
- `check_daily_workflow.py`
- `check_complete.py`
- `check_gemini_usage.py`
- `seed_knowledge_wiki.py`
- `backtest_scores.py`（離線評分回測：IC／分組超額報酬／信心等級校準，只讀 data/analytics.db；`--frames N` 重播最近 N 日主軸框架，任一框架佔比應 ≤50%）
