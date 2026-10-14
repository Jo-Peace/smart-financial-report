#!/usr/bin/env python3
"""Single hit rule (excess + dead band), baseline/Wilson CI claim gate,
stock_db tracking idempotency."""
import os
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.horizon_reliability import (  # noqa: E402
    DEFAULT_RULER_LINE,
    LEDGER_METHOD,
    analyze_horizon_reliability,
    current_reliability_line,
    natural_reliability_sentences,
    wilson_interval,
)
from modules.prediction_validator import classify_hit, dead_band_pct, validate_prediction_targets  # noqa: E402
from modules.stock_db_updater import (  # noqa: E402
    StockDatabaseUpdater,
    apply_tracking_entry,
    build_tracking_entry,
    dedupe_tracking_history,
)


class HitRuleTest(unittest.TestCase):
    def test_dead_band_scales_with_sessions(self):
        self.assertEqual(dead_band_pct(1), 0.5)
        self.assertEqual(dead_band_pct(20), 2.24)

    def test_excess_decides_and_raw_hit_is_kept(self):
        rising_tide = classify_hit("多", 3.0, 5.0, 5)
        self.assertEqual(rising_tide["outcome"], "miss")
        self.assertEqual(rising_tide["raw_hit"], 1)
        short = classify_hit("空", -2.0, 0.0, 1)
        self.assertEqual(short["hit"], 1)
        flat = classify_hit("多", 0.0, 0.0, 1)
        self.assertEqual(flat["outcome"], "neutral")
        self.assertIsNone(flat["hit"])
        self.assertEqual(classify_hit("多", 1.0, None, 1)["outcome"], "unknown")
        self.assertEqual(classify_hit("偏空", -2.0, 0.0, 1)["hit"], 1)
        watch = classify_hit("觀察", 5.0, 0.0, 1)
        self.assertEqual(watch["outcome"], "unknown")
        self.assertIsNone(watch["raw_hit"])
        self.assertEqual(classify_hit("多", 1.0, None, 1, allow_raw_fallback=True)["basis"], "raw")

    def test_onair_validation_uses_index_excess(self):
        market = {"^TWII": {"price": 20000, "pct_change": 2.0}, "2303.TW": {"price": 50, "pct_change": 1.0}}
        rows = validate_prediction_targets([{"id": "2303", "name": "聯電", "direction": "多"}], market)
        self.assertIs(rows[0]["hit"], False)
        self.assertIs(rows[0]["raw_hit"], True)
        self.assertIn("超額", rows[0]["evidence"][0])

    def test_otc_suffix_is_normalised(self):
        market = {"^TWII": {"price": 1, "pct_change": 0.0}, "6488.TWO": {"price": 50, "pct_change": 3.0}}
        rows = validate_prediction_targets([{"id": "6488.TWO", "direction": "多"}], market)
        self.assertEqual(rows[0]["id"], "6488")
        self.assertIs(rows[0]["hit"], True)


def _row(days, hit, n, eff=None, excess=0.5, method=LEDGER_METHOD):
    low, high = wilson_interval(hit, eff or n)
    return {
        "horizon_days": days,
        "samples": n,
        "effective_samples": eff or n,
        "hit_rate": hit,
        "hit_ci_low": low,
        "hit_ci_high": high,
        "baseline": 0.5,
        "avg_return": 1.0,
        "avg_excess": excess,
        "method": method,
    }


class ReliabilityGateTest(unittest.TestCase):
    def test_wilson_interval_known_value(self):
        low, high = wilson_interval(0.56, 125)
        self.assertAlmostEqual(low, 0.4724, places=3)
        self.assertAlmostEqual(high, 0.6442, places=3)

    def test_published_56pct_claim_is_not_supported(self):
        # The 2026-10-12 numbers: 20D 56% (n=125) vs short-window avg 46.8%.
        rows = [_row(1, 0.42, 181), _row(3, 0.484, 159), _row(5, 0.50, 146), _row(20, 0.56, 125)]
        info = analyze_horizon_reliability(rows)
        self.assertFalse(info["primary_leads"])
        self.assertEqual(info["verdict"], "insufficient")
        text = " ".join(natural_reliability_sentences(rows))
        self.assertIn("樣本不足以判定", text)
        self.assertNotIn("優於", text)
        self.assertNotIn("可信度為準", text)

    def test_claim_only_when_ci_lower_bound_beats_baseline(self):
        rows = [_row(1, 0.45, 200), _row(3, 0.47, 200), _row(5, 0.48, 200), _row(20, 0.70, 150, eff=120)]
        info = analyze_horizon_reliability(rows)
        self.assertTrue(info["primary_leads"])
        self.assertIn("高於 50% 基準", natural_reliability_sentences(rows)[0])

    def test_cluster_adjusted_n_can_remove_the_claim(self):
        # Same 62% hit rate, but only 25 independent clusters → CI covers 50%.
        rows = [_row(1, 0.45, 200), _row(20, 0.62, 150, eff=25)]
        self.assertFalse(analyze_horizon_reliability(rows)["primary_leads"])

    def test_below_baseline_reference_is_not_called_covering(self):
        rows = [_row(1, 0.47, 134, eff=110), _row(20, 0.235, 68, eff=40)]
        text = " ".join(natural_reliability_sentences(rows))
        self.assertIn("低於 50% 基準", text)
        self.assertNotIn("區間涵蓋", text)

    def test_legacy_stored_one_liner_is_never_republished(self):
        legacy = {
            "horizon_reliability": {"one_liner": "20D 命中率 56.0%（n=125）優於 1D/3D/5D；成敗尺子請放在 20D"},
            "timeframe_history": {"horizon_performance": [{"horizon_days": 20, "samples": 125, "hit_rate": 0.56}]},
        }
        self.assertEqual(current_reliability_line(legacy), DEFAULT_RULER_LINE)
        current = {"timeframe_history": {"horizon_performance": [_row(1, 0.45, 50), _row(20, 0.5, 40)]}}
        self.assertIn("樣本不足以判定", current_reliability_line(current))


class TrackingIdempotencyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.updater = StockDatabaseUpdater.__new__(StockDatabaseUpdater)
        self.updater.db_path = os.path.join(self.tmp.name, "stock_db.json")
        self.updater.stock_db = {"2409": {"name": "友達"}}

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, pct, index_pct=0.0):
        market = {"^TWII": {"pct_change": index_pct}, "2409.TW": {"pct_change": pct}}
        preds = [{"id": "2409", "direction": "多", "trigger": "外資買超第2名"}]
        self.updater.update_tracking_stats(
            preds, market, None, as_of_date="2026-09-22", prediction_date="2026-09-21"
        )
        return self.updater.stock_db["2409"]["tracking"]

    def test_same_day_rerun_does_not_double_count(self):
        self._run(9.9)
        self._run(9.9)
        tracking = self._run(9.9)
        self.assertEqual(tracking["prediction_count"], 1)
        self.assertEqual(tracking["hit_count"], 1)
        self.assertEqual(len(tracking["history"]), 1)
        self.assertEqual(tracking["hit_rate"], "100.0%")

    def test_rerun_with_new_price_replaces_contribution(self):
        self._run(3.0)
        tracking = self._run(-3.0)
        self.assertEqual((tracking["prediction_count"], tracking["hit_count"]), (1, 0))

    def test_neutral_and_missing_are_not_in_denominator(self):
        tracking = self._run(1.1, index_pct=1.0)  # excess 0.1 < 0.5 dead band
        self.assertEqual(tracking["prediction_count"], 0)
        self.assertIn("與大盤持平", tracking["history"][0]["result"])
        entry = build_tracking_entry("2026-09-22", None, "觀察", "", "", 5.0, 0.0)
        self.assertFalse(entry["counted"])
        self.assertNotIn("命中", entry["result"])

    def test_tracking_uses_the_on_air_verdict(self):
        """stock_db tracking grades a call exactly like the on-air 驗收 row (one rule)."""
        market = {
            "^TWII": {"pct_change": -2.0, "price": 22000.0, "bar_date": "2026-09-22"},
            # Long call, close 95 through the 96 stop: +0.5% excess but a stopped loser.
            "2409.TW": {"pct_change": -1.5, "price": 95.0, "bar_date": "2026-09-22"},
            # Stale bar (yesterday's move): not judged on air, not counted either.
            "2603.TW": {"pct_change": 4.0, "price": 200.0, "bar_date": "2026-09-19"},
        }
        self.updater.stock_db["2603"] = {"name": "長榮"}
        preds = [
            {"id": "2409", "direction": "多", "trigger": "t", "stop_loss_price": 96.0, "stop_loss_desc": "跌破 96"},
            {"id": "2603", "direction": "多", "trigger": "u"},
        ]
        inst = {"data_date": "2026-09-22", "top_buy": [], "top_sell": []}
        on_air = {r["id"]: r for r in validate_prediction_targets(preds, market, inst)}
        self.assertIs(on_air["2409"]["hit"], False)
        self.assertEqual(on_air["2409"]["hit_basis"], "stop")
        self.assertIsNone(on_air["2603"]["hit"])
        self.updater.update_tracking_stats(preds, market, inst, as_of_date="2026-09-22", prediction_date="2026-09-21")
        stopped = self.updater.stock_db["2409"]["tracking"]
        self.assertEqual((stopped["prediction_count"], stopped["hit_count"]), (1, 0))
        self.assertIn("停損", stopped["history"][-1]["result"])
        stale = self.updater.stock_db["2603"]["tracking"]
        self.assertEqual(stale["prediction_count"], 0)
        # The bare-number wrapper goes through the same validator.
        entry = build_tracking_entry("2026-09-22", None, "多", "t", "跌破 96", -1.5, -2.0, stop_loss_price=96.0, price=95.0)
        self.assertEqual((entry["counted"], entry["hit"], entry["basis"]), (True, False, "stop_1d"))
        # Index missing: on air falls back to the raw move, the tally does not count it.
        raw = build_tracking_entry("2026-09-22", None, "多", "t", "", 3.0, None)
        self.assertFalse(raw["counted"])
        self.assertIn("大盤資料缺", raw["result"])

    def test_dedupe_legacy_same_day_history(self):
        tracking = {
            "prediction_count": 7,
            "hit_count": 3,
            "history": [
                {"date": "2026-09-22", "direction": "多", "trigger": "t", "result": "實際 +9.90% → ✅ 命中"},
                {"date": "2026-09-22", "direction": "多", "trigger": "t", "result": "實際 +9.90% → ✅ 命中"},
                {"date": "2026-09-22", "direction": "多", "trigger": "t", "result": "實際 +9.90% → ✅ 命中"},
                {"date": "2026-09-23", "direction": "多", "trigger": "u", "result": "實際 -1.00% → ❌ 失準"},
            ],
        }
        self.assertEqual(dedupe_tracking_history(tracking), 2)
        self.assertEqual((tracking["prediction_count"], tracking["hit_count"]), (5, 1))
        self.assertEqual(tracking["hit_rate"], "20.0%")
        self.assertEqual([h["date"] for h in tracking["history"]], ["2026-09-22", "2026-09-23"])
        # Re-applying the same legacy-keyed day is a replace, not an add.
        entry = build_tracking_entry("2026-09-23", None, "多", "u", "", -1.0, 0.0)
        self.assertTrue(apply_tracking_entry(tracking, entry))
        self.assertEqual(tracking["prediction_count"], 5)

    def test_dedupe_keeps_the_latest_rerun_like_apply(self):
        # 2313 on 2026-05-15: a pre-close run (-3.26%) then the final rerun (-4.07%).
        early = {"date": "2026-05-15", "direction": "多", "trigger": "t", "result": "實際 -3.26% → ❌ 失準"}
        final = {"date": "2026-05-15", "direction": "多", "trigger": "t", "result": "實際 -4.07% → ❌ 失準"}
        other = {"date": "2026-05-16", "direction": "多", "trigger": "u", "result": "實際 +2.00% → ✅ 命中"}
        tracking = {"prediction_count": 3, "hit_count": 1, "history": [early, final, other]}
        self.assertEqual(dedupe_tracking_history(tracking), 1)
        self.assertEqual(tracking["history"], [final, other])
        self.assertEqual((tracking["prediction_count"], tracking["hit_count"]), (2, 1))
        applied = {"prediction_count": 0, "hit_count": 0, "history": []}
        apply_tracking_entry(applied, dict(early, counted=True, hit=False))
        apply_tracking_entry(applied, dict(final, counted=True, hit=False))
        self.assertEqual([h["result"] for h in applied["history"]], [final["result"]])


if __name__ == "__main__":
    unittest.main()
