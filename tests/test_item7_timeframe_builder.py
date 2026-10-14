#!/usr/bin/env python3
"""Item 7: mid-term picks need signed, fresh, point-in-time local evidence."""
import os
import sqlite3
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.notebooklm_generator import _pick_max_abs_return  # noqa: E402
from modules.notebooklm_source_builder import _flag_extreme_returns  # noqa: E402
from modules.analytics_store import AnalyticsStore  # noqa: E402
from modules.timeframe_builder import (  # noqa: E402
    _build_long_term_picks,
    _collect_tickers,
    _long_term_summary,
    enrich_structured_data_with_timeframes,
    load_point_in_time_trends,
)

STOCK_DB = {
    "2408": {"name": "南亞科"},
    "3037": {"name": "欣興"},
    "3231": {"name": "緯創"},
    "3443": {"name": "創意"},
    "2603": {"name": "長榮"},
    "00685L": {"name": "群益臺灣加權正2"},
    "4152": {"name": "台微體"},
}

# TWSE sessions in Sep / Oct 2026 (09/25, 09/28 and 10/09 are holidays).
SESSIONS = [
    "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-14",
    "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21", "2026-09-22",
    "2026-09-23", "2026-09-24", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02",
    "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-12",
]


def _trend(**windows):
    return {
        "latest_date": "2026-10-12",
        "trends": {w: {"pct_change": v, "from_date": "x", "to_date": "2026-10-12", "sessions": int(w[:-1])}
                   for w, v in windows.items()},
    }


class CollectTickersTest(unittest.TestCase):
    def test_pool_resolves_names_and_ignores_prices(self):
        sd = {
            "safe_havens_bought": ["南亞科 (+111.9億)"],
            "heavyweights_dumped": ["00685L 群益臺灣加權正2 (-5.0億)"],
            "commodities": ["黃金 (收$4152.2, +1.76%)"],
            "price_volume_divergence": ["2026/07/31 創意 法說會"],
            "prev_day_picks_result": ["3231 緯創 (+0.53%, 預測成立)"],
            "focus_ranking": [{"ticker": "3037"}],
        }
        tickers = _collect_tickers(sd, STOCK_DB)
        self.assertIn("2408", tickers)
        self.assertIn("00685L", tickers)
        self.assertIn("3231", tickers)
        self.assertIn("3037", tickers)
        self.assertIn("3443", tickers)
        self.assertNotIn("4152", tickers)  # gold price, not 台微體
        self.assertNotIn("2026", tickers)
        self.assertEqual(tickers, sorted(tickers))


class PointInTimeTrendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "analytics.db")
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE market_snapshots (snapshot_date TEXT, ticker TEXT, price REAL, pct_change REAL)")
        rows = []
        for i, day in enumerate(SESSIONS):
            rows.append((day, "2408", 100.0 + i, 1.0))
        # sparse ticker: two snapshots 3 months apart
        rows += [("2026-07-01", "2603", 200.0, 0.0), ("2026-10-12", "2603", 250.0, 9.9)]
        # stale ticker: last snapshot weeks before as_of
        rows += [(d, "3443", 3000.0, 0.0) for d in SESSIONS[:5]]
        rows.append(("2026-10-20", "2408", 999.0, 50.0))  # after as_of: must be ignored
        conn.executemany("INSERT INTO market_snapshots VALUES (?,?,?,?)", rows)
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_windows_are_sessions_and_point_in_time(self):
        trends = load_point_in_time_trends(["3443", "2603", "2408"], "2026-10-12", db_path=self.db)
        self.assertEqual(list(trends), ["2408", "2603"])  # 3443 stale, sorted order
        t = trends["2408"]
        self.assertEqual(t["latest_date"], "2026-10-12")
        self.assertEqual(t["trends"]["20D"]["from_date"], SESSIONS[-21])
        self.assertEqual(t["trends"]["20D"]["sessions"], 20)
        self.assertAlmostEqual(t["trends"]["5D"]["pct_change"], (122 - 117) / 117 * 100, places=6)
        self.assertEqual(trends["2603"]["trends"], {})  # a 3-month gap is not a 5D window

    def test_today_quote_stands_in_for_unwritten_snapshot(self):
        # 3037 has snapshots up to 10-08 only; /1 knows today's close from market_data.
        conn = sqlite3.connect(self.db)
        conn.executemany(
            "INSERT INTO market_snapshots VALUES (?,?,?,?)",
            [(day, "3037", 50.0, 0.0) for day in SESSIONS[:-1]],
        )
        conn.commit()
        conn.close()
        trends = load_point_in_time_trends(
            ["3037"], "2026-10-12", db_path=self.db, today_quotes={"3037": (55.0, 10.0)}
        )
        self.assertEqual(trends["3037"]["latest_date"], "2026-10-12")
        self.assertAlmostEqual(trends["3037"]["trends"]["5D"]["pct_change"], 10.0)

    def test_missing_db_is_empty(self):
        self.assertEqual(load_point_in_time_trends(["2408"], "2026-10-12", db_path="/nonexistent.db"), {})


class LongTermPickTest(unittest.TestCase):
    def _picks(self, trends, sd=None):
        sd = dict(sd or {"date": "2026-10-12"})
        return _build_long_term_picks(sd, [], trends, stock_db=STOCK_DB, run_date="2026-10-12")

    def test_no_evidence_means_explicit_no_axis(self):
        picks = self._picks({"3443": {"latest_date": "2026-09-18", "trends": {}}, "2603": _trend(**{"5D": 15.8})})
        self.assertEqual(len(picks), 1)
        self.assertTrue(picks[0]["placeholder"])
        self.assertNotEqual(picks[0]["evidence"], "無")

    def test_direction_sign_and_disagreement(self):
        picks = self._picks({
            "2408": _trend(**{"10D": -16.8, "20D": -23.0}),
            "3037": _trend(**{"10D": 12.0, "20D": -4.0}),  # disagree → no trend
        })
        self.assertEqual([p["ticker"] for p in picks], ["2408"])
        self.assertEqual(picks[0]["direction"], "空")
        self.assertEqual(picks[0]["as_of"], "2026-10-12")
        self.assertEqual(picks[0]["trends"], {"10D": -16.8, "20D": -23.0})
        self.assertIn("中期偏空", picks[0]["thesis"])

    def test_short_term_picks_excluded_and_extremes_downweighted(self):
        sd = {"date": "2026-10-12", "prediction_targets": [{"id": "3037", "name": "欣興"}]}
        picks = self._picks({
            "3037": _trend(**{"10D": 20.0, "20D": 25.0}),
            "3231": _trend(**{"10D": 96.8, "20D": 61.2}),
            "2408": _trend(**{"10D": 12.0, "20D": 18.0}),
        }, sd)
        self.assertNotIn("3037", [p["ticker"] for p in picks])
        self.assertEqual(picks[0]["ticker"], "2408")
        extreme = next(p for p in picks if p["ticker"] == "3231")
        self.assertTrue(extreme["extreme"])

    def test_no_mid_term_direction_is_not_a_pick(self):
        # 2026-10-12 2454: big 5D/10D moves but a flat 20D anchor → no direction, no slot.
        flat = {"2454": _trend(**{"5D": -6.57, "10D": -10.71, "20D": 0.0})}
        picks = self._picks(flat)
        self.assertEqual(len(picks), 1)
        self.assertTrue(picks[0]["placeholder"])
        self.assertFalse(any("方向未明" in str(p.get("thesis")) for p in picks))
        mixed = dict(flat, **{"2408": _trend(**{"10D": 12.0, "20D": 18.0})})
        self.assertEqual([p["ticker"] for p in self._picks(mixed)], ["2408"])

    def test_ties_break_by_ticker(self):
        trends = {"3231": _trend(**{"20D": 10.0}), "2408": _trend(**{"20D": 10.0}), "3037": _trend(**{"20D": 10.0})}
        self.assertEqual([p["ticker"] for p in self._picks(trends)], ["2408", "3037"])

    def test_radar_branch_is_gated_and_unverified(self):
        sd = {"date": "2026-10-12", "supply_chain_event_radar": [
            {"event": "臉書貼文", "url": "https://www.facebook.com/p/1", "impact_score": 100,
             "freshness": "new", "impacted_roles": ["先進封裝"]},
        ]}
        self.assertTrue(self._picks({}, sd)[0].get("placeholder"))
        sd["supply_chain_event_radar"].append(
            {"event": "AMD 投資台灣", "url": "https://news.cnyes.com/news/id/1", "impact_score": 90,
             "freshness": "new", "impacted_roles": ["先進封裝 / CoWoS", "基板 / 載板"]}
        )
        pick = self._picks({}, sd)[0]
        self.assertEqual(pick["direction"], "待驗證")
        self.assertIn("待驗證事件", pick["thesis"])
        self.assertNotIn("重估", pick["thesis"])
        self.assertIn("cnyes", pick["evidence"])

    def test_summary_comes_from_picks(self):
        thesis, evidence = _long_term_summary(self._picks({"2408": _trend(**{"10D": 12.0, "20D": 18.0})}))
        self.assertIn("2408", thesis)
        self.assertTrue(evidence)
        thesis, evidence = _long_term_summary(self._picks({}))
        self.assertIn("無明確", thesis)
        self.assertEqual(evidence, [])


class EnrichPointInTimeTest(unittest.TestCase):
    def test_reliability_ignores_results_evaluated_after_as_of(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "analytics.db")
            store = AnalyticsStore(db)
            conn = sqlite3.connect(db)
            for pid, (pred_day, eval_day, hit) in enumerate(
                [("2026-10-01", "2026-10-02", 1), ("2026-10-05", "2026-10-06", 0),
                 ("2026-10-08", "2026-10-12", 1), ("2026-10-12", "2026-10-13", 1)],
                start=1,
            ):
                conn.execute(
                    "INSERT INTO predictions (id, prediction_date, ticker, name, direction, trigger, raw_json)"
                    " VALUES (?, ?, '3037', '欣興', '多', ?, '{}')",
                    (pid, pred_day, f"t{pid}"),
                )
                # Item 4 ledger: summaries read sessions_v2 rows only and grade
                # on the same-window excess (hit already decided here).
                conn.execute(
                    "INSERT INTO prediction_results (prediction_id, horizon_days, evaluated_date, return_pct,"
                    " index_return_pct, excess_return_pct, hit, raw_hit, status, method)"
                    " VALUES (?, 1, ?, 1.0, 0.0, 1.0, ?, 1, 'ok', 'sessions_v2')",
                    (pid, eval_day, hit),
                )
            conn.commit()
            conn.close()
            self.assertEqual(store.get_horizon_performance_summary()[0]["samples"], 4)  # no as_of: all rows
            sd = {"date": "2026-10-08", "sectors": ["載板：欣興"]}  # list-shaped sectors must not crash
            out = enrich_structured_data_with_timeframes(sd, db_path=db, stock_db={}, as_of="2026-10-08")
            history = out["timeframe_history"]
            self.assertEqual(history["horizon_performance"][0]["samples"], 2)
            self.assertEqual(
                sorted(r["evaluated_date"] for r in history["recent_results"]), ["2026-10-02", "2026-10-06"]
            )


class ExtremeReturnTextTest(unittest.TestCase):
    def test_director_card_reads_every_value_without_decimal_bug(self):
        self.assertLess(_pick_max_abs_return({"thesis": "5D +1.45%"}), 40)
        self.assertLess(_pick_max_abs_return({"thesis": "-0.41%"}), 40)
        self.assertGreaterEqual(_pick_max_abs_return({"thesis": "5D +3.07%、10D -0.23%、20D +54.61%"}), 40)
        self.assertEqual(_pick_max_abs_return({"trends": {"10D": 96.81, "20D": 61.16}}), 96.81)

    def test_source_flags_20d(self):
        self.assertIn("極端值", _flag_extreme_returns("20D +61.16%"))
        self.assertNotIn("極端值", _flag_extreme_returns("20D +45.00%"))
        self.assertIn("極端值", _flag_extreme_returns("10D +96.81%"))


if __name__ == "__main__":
    unittest.main()
