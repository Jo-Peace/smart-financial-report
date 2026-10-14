#!/usr/bin/env python3
"""Published 「20D 較準／成敗尺子」 claims only appear when the ledger supports them."""
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.broadcast_facts import build_broadcast_facts  # noqa: E402
from modules.horizon_reliability import DEFAULT_RULER_LINE, LEDGER_METHOD, wilson_interval  # noqa: E402
from modules.notebooklm_source_builder import build_notebooklm_source  # noqa: E402

LEGACY_CLAIM = "依目前本機回測樣本，20D 命中率 56.0%（n=125）優於 1D/3D/5D 平均 46.8%；選標與標題主角宜以 20D 可信度為準"


def _current_row(days, hit, n):
    low, high = wilson_interval(hit, n)
    return {
        "horizon_days": days,
        "samples": n,
        "effective_samples": n,
        "hit_rate": hit,
        "hit_ci_low": low,
        "hit_ci_high": high,
        "baseline": 0.5,
        "avg_return": 1.0,
        "avg_excess": 0.3,
        "method": LEDGER_METHOD,
    }


def _structured(rows, one_liner=LEGACY_CLAIM):
    return {
        "date": "2026-10-12",
        "index_action": "上漲 10 點",
        "prediction_targets": [{"id": "2603", "name": "長榮", "direction": "多", "trigger": "外資買超第2名"}],
        "horizon_reliability": {"one_liner": one_liner, "primary_leads": True},
        "long_term_picks": [
            {"theme": "2603 長榮", "horizon": "2~6週", "validation_window": "20D", "thesis": "5D +15.76%", "evidence": "5D +15.76%"}
        ],
        "timeframe_history": {
            "horizon_performance": rows,
            "recent_trends": {"2603": {"latest_date": "2026-10-12", "trends": {}}},
            "recent_results": [],
        },
    }


class PublishedClaimsTest(unittest.TestCase):
    def test_legacy_structured_data_does_not_republish_ruler_claim(self):
        legacy_rows = [{"horizon_days": 20, "samples": 125, "hit_rate": 0.56, "avg_excess": 6.78}]
        sd = _structured(legacy_rows)
        facts = build_broadcast_facts(sd, stock_db={})
        self.assertEqual(facts["ruler_line"], DEFAULT_RULER_LINE)
        source = build_notebooklm_source(sd, date_str="2026-10-12")
        self.assertNotIn("成敗尺子：", source)
        self.assertNotIn("優於 1D/3D/5D", source)
        self.assertNotIn("可信度主指標", source)
        self.assertNotIn("+6.8%", source)
        self.assertIn("時間窗可信度：", source)

    def test_current_rows_without_support_say_insufficient(self):
        rows = [_current_row(1, 0.42, 181), _current_row(20, 0.56, 125)]
        source = build_notebooklm_source(_structured(rows), date_str="2026-10-12")
        self.assertIn("樣本不足以判定", source)
        self.assertIn("20D 超額命中率 56.0%", source)
        self.assertIn("對照，未證實較準", source)
        self.assertNotIn("主指標", source)

    def test_supported_primary_window_is_labelled(self):
        rows = [_current_row(1, 0.45, 200), _current_row(3, 0.46, 200), _current_row(5, 0.47, 200), _current_row(20, 0.72, 160)]
        source = build_notebooklm_source(_structured(rows), date_str="2026-10-12")
        self.assertIn("主指標", source)
        self.assertIn("高於 50% 基準", source)

    def test_long_term_picks_are_marked_unvalidated_with_data_date(self):
        source = build_notebooklm_source(_structured([]), date_str="2026-10-12")
        self.assertIn("未進驗收帳本", source)
        self.assertIn("驗收：未進帳本", source)
        self.assertIn("資料截至 2026-10-12", source)
        self.assertNotIn("假說成敗優先看此層", source)
        self.assertNotIn("驗證窗 20D", source)

    def test_compounding_brief_has_no_unconditional_20d_ruler(self):
        from modules import compounding_loop

        class _FakeStore:
            def get_horizon_performance_summary(self):
                return [_current_row(1, 0.42, 181), _current_row(20, 0.56, 125)]

        with tempfile.TemporaryDirectory() as tmp, mock.patch("modules.analytics_store.AnalyticsStore", _FakeStore):
            text = compounding_loop.build_weekly_compounding_brief(
                log_path=os.path.join(tmp, "none.md"),
                stock_db_path=os.path.join(tmp, "stock_db.json"),
            )
        self.assertNotIn("成敗以 20D 為主", text)
        self.assertIn("樣本不足以判定", text)


class AnalyticsReportTest(unittest.TestCase):
    def test_report_uses_session_ledger_and_ci_read_as(self):
        from modules.analytics_store import AnalyticsStore
        from scripts.maintenance.analytics_report import build_report

        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "analytics.db")
            store = AnalyticsStore(db)
            conn = sqlite3.connect(db)
            conn.execute(
                "INSERT INTO predictions (prediction_date, ticker, direction, trigger, raw_json) "
                "VALUES ('2026-10-07', '2303', '多', 't', '{}')"
            )
            for day, px, idx in (("2026-10-07", 100, 20000), ("2026-10-08", 103, 20000)):
                conn.execute(
                    "INSERT INTO market_snapshots (snapshot_date, ticker, price, raw_json) VALUES (?, '2303', ?, '{}')",
                    (day, px),
                )
                conn.execute(
                    "INSERT INTO market_snapshots (snapshot_date, ticker, price, raw_json) VALUES (?, '^TWII', ?, '{}')",
                    (day, idx),
                )
            conn.commit()
            conn.close()
            store.recompute_prediction_results("2026-10-08")
            text = build_report(limit=5, db_path=db)
        self.assertIn("TWSE sessions", text)
        self.assertIn("| 1D | 1 |", text)
        self.assertIn("樣本不足以判定", text)
        self.assertNotIn("| 主指標 |", text)


class DashboardStockWatchTest(unittest.TestCase):
    def test_primary_label_requires_supported_claim(self):
        import scripts.maintenance.build_dashboard_stock_watch as dash
        from modules.analytics_store import AnalyticsStore

        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "analytics.db")
            AnalyticsStore(db)
            with mock.patch.object(dash, "DB_PATH", dash.Path(db)):
                payload = dash._build_analytics(limit=5)
        self.assertTrue(payload["available"])
        self.assertFalse(payload["interpretation"]["primaryLeads"])
        self.assertNotIn("主要依據", payload["interpretation"]["primary"])
        self.assertEqual(dash._horizon_role(20)["role"], "candidate")
        self.assertEqual(dash._horizon_role(20, primary_leads=True)["role"], "primary")


if __name__ == "__main__":
    unittest.main()
