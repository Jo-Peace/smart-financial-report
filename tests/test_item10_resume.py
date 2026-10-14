#!/usr/bin/env python3
"""Resume path: no fabricated 驗收, same local facts + post-processing as /1."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.resume_inputs import load_resume_inputs, resume_inputs_path, save_resume_inputs  # noqa: E402
from scripts.maintenance import resume_daily_persist as resume  # noqa: E402

GEMINI_LEFTOVER = {
    "date": "2026-10-13",
    "prediction_targets": [{"id": "2330", "name": "台積電"}],
    "prev_day_picks_result": "昨日驗收：凱基金 收盤 30.7 預測成立",
    "validation_results": [{"id": "2883", "hit": True, "result": "預測成立"}],
}


class ResumeInputsTest(unittest.TestCase):
    def test_roundtrip_and_date_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_resume_inputs(
                tmp,
                "2026-10-13",
                local_facts={"date": "2026-10-13", "safe_havens_bought": ["2330 台積電"]},
                validation_results=[{"id": "2330", "hit": False}],
                validation_meta={"onair_validation_eligible": True},
            )
            self.assertTrue(os.path.exists(resume_inputs_path(tmp, "2026-10-13")))
            loaded = load_resume_inputs(tmp, "2026-10-13")
            self.assertEqual(loaded["local_facts"]["safe_havens_bought"], ["2330 台積電"])
            self.assertEqual(loaded["validation_results"][0]["id"], "2330")
            self.assertIsNone(load_resume_inputs(tmp, "2026-10-14"))


class ResumeStructuredDataTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reports = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self):
        captured = {}

        def fake_extract(api_key, report_content, local_facts=None):
            captured["local_facts"] = local_facts
            return json.loads(json.dumps(GEMINI_LEFTOVER))

        from modules import timeframe_builder
        from modules.analytics_store import AnalyticsStore

        # finalize_structured_data enriches timeframes (item 7): keep the ledger
        # read on a temp DB, never the real data/analytics.db.
        tmp_db = os.path.join(self.reports, "analytics.db")
        with mock.patch.object(resume, "extract_structured_data", side_effect=fake_extract), mock.patch.object(
            timeframe_builder, "AnalyticsStore", side_effect=lambda *a, **k: AnalyticsStore(tmp_db)
        ), mock.patch.object(timeframe_builder, "DEFAULT_ANALYTICS_DB", tmp_db), contextlib.redirect_stdout(
            io.StringIO()
        ):
            data, error = resume.build_resumed_structured_data("k", "# 日報", self.reports, "2026-10-13")
        self.assertIsNone(error)
        return data, captured

    def test_hardcoded_validation_is_gone(self):
        self.assertFalse(hasattr(resume, "_build_validation_from_report"))

    def test_without_saved_inputs_no_validation_is_claimed(self):
        prev = os.path.join(self.reports, "daily", "2026-10-12", "structured_data.json")
        os.makedirs(os.path.dirname(prev))
        with open(prev, "w", encoding="utf-8") as f:
            json.dump({"date": "2026-10-12", "prediction_targets": [{"id": "2883", "name": "凱基金"}]}, f)
        data, captured = self._run()
        self.assertEqual(captured["local_facts"], {})
        self.assertEqual(data["prev_day_picks_result"], [])
        self.assertEqual(data["validation_results"], [])
        self.assertFalse(data["validation_policy"]["onair_validation_eligible"])
        self.assertIn("resume_inputs.json", data["validation_policy"]["skip_reason"])
        self.assertNotIn("預測成立", json.dumps(data, ensure_ascii=False))

    def test_saved_inputs_drive_extraction_and_validation(self):
        validation = [
            {
                "id": "2330",
                "name": "台積電",
                "direction": "多",
                "price": 1000.0,
                "pct_change": -1.2,
                "hit": False,
                "result": "預測失準",
                "stop_loss_triggered": False,
                "evidence": ["收盤 1000，漲跌 -1.20%"],
            }
        ]
        save_resume_inputs(
            self.reports,
            "2026-10-13",
            local_facts={"date": "2026-10-13", "safe_havens_bought": ["2330 台積電 (+30.0億)"]},
            validation_results=validation,
            validation_meta={"source_date": "2026-10-12", "trading_days_gap": 1, "onair_validation_eligible": True},
            editorial_frame={"label": "test"},
        )
        data, captured = self._run()
        self.assertEqual(captured["local_facts"]["safe_havens_bought"], ["2330 台積電 (+30.0億)"])
        self.assertEqual(data["validation_results"], validation)
        self.assertNotIn("凱基金", json.dumps(data["prev_day_picks_result"], ensure_ascii=False))
        self.assertEqual(data["editorial_frame"], {"label": "test"})
        self.assertTrue(data["validation_policy"]["onair_validation_eligible"])

    def test_saved_payloads_feed_role_inputs_and_hero_ledger(self):
        """Schema 2: the resumed turnover rank / vol_ratio / local quotes match /1's inputs."""
        market = {
            "^TWII": {"symbol": "^TWII", "price": 49301.96, "change": -11.48, "pct_change": -0.02, "bar_date": "2026-10-13"},
            "2330.TW": {
                "symbol": "2330.TW",
                "price": 1000.0,
                "change": -12.0,
                "pct_change": -1.2,
                "volume": 30_000_000,
                "avg_vol_5d": 20_000_000,
                "vol_ratio": 1.5,
                "ma5": 1005.0,
                "ma20": 990.0,
                "bar_date": "2026-10-13",
            },
            "2603.TW": None,  # failed fetch stays a 「數據無法取得」 key
        }
        volume = [
            {"rank": 1, "id": "2409", "name": "友達", "volume": 120_000_000, "close_price": 20.3, "pct_change": 1.5},
        ]
        institutional = {
            "data_date": "2026-10-13",
            "source": "twse",
            "top_buy": [{"id": "2330", "name": "台積電", "foreign_net": 5_000_000, "est_amount": 50.0}],
            "top_sell": [],
        }
        save_resume_inputs(
            self.reports,
            "2026-10-13",
            local_facts={"date": "2026-10-13"},
            market_data=market,
            institutional_data=institutional,
            volume_data=volume,
            similar_sessions=[],
        )
        saved = load_resume_inputs(self.reports, "2026-10-13")
        self.assertEqual(saved["schema_version"], 2)
        self.assertIsNone(saved["payloads"]["market_data"]["2603.TW"])
        data, _ = self._run()
        inputs = data["role_score_inputs"]
        self.assertIn("2330", inputs["volume"])  # turnover rank over share board ∪ market_data
        self.assertIn("2409", inputs["volume"])
        self.assertEqual(inputs["market"]["2330"]["vol_ratio"], 1.5)
        self.assertEqual(inputs["market"]["2330"]["turnover_yi"], 300.0)
        ledger = data["local_market_facts"]["stocks"]
        self.assertEqual(ledger["2330"]["close"], 1000.0)
        self.assertEqual(data["similar_sessions"], [])

    def test_value_ranking_round_trips_so_resume_uses_the_same_value_rank(self):
        from modules.resume_inputs import saved_payloads

        value_ranking = {"data_date": "2026-10-13", "stocks": [{"rank": 3, "id": "2409", "trade_value_yi": 24.4}], "etfs": []}
        save_resume_inputs(
            self.reports,
            "2026-10-13",
            local_facts={"date": "2026-10-13"},
            volume_data=[{"rank": 1, "id": "2409", "name": "友達", "volume": 120_000_000, "close_price": 20.3}],
            value_ranking=value_ranking,
        )
        payloads = saved_payloads(load_resume_inputs(self.reports, "2026-10-13"))
        self.assertEqual(payloads["value_ranking"], value_ranking)
        data, _ = self._run()
        self.assertEqual(data["role_score_inputs"]["volume"]["2409"]["turnover_rank"], 3)
        self.assertEqual(data["role_score_inputs"]["volume"]["2409"]["rank_basis"], "market")

    def test_schema_1_file_without_payloads_still_resumes(self):
        from modules.resume_inputs import saved_payloads

        self.assertEqual(saved_payloads({"schema_version": 1, "date": "2026-10-13"}), {})
        self.assertEqual(saved_payloads(None), {})


if __name__ == "__main__":
    unittest.main()
