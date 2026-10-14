#!/usr/bin/env python3
"""Prediction ledger measurement: session horizons, same-window index, stop path,
missing-not-skip, recompute on rerun, (date, ticker) key, trend windows."""
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.analytics_store import (  # noqa: E402
    EVAL_METHOD,
    AnalyticsStore,
    anchor_session,
    is_conditional_trigger,
    nth_session_after,
    nth_session_before,
)


def _snap(store, day, ticker, price, pct=0.0):
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        """
        INSERT INTO market_snapshots (snapshot_date, ticker, price, pct_change, raw_json)
        VALUES (?, ?, ?, ?, '{}')
        ON CONFLICT(snapshot_date, ticker) DO UPDATE SET price = excluded.price,
            pct_change = excluded.pct_change
        """,
        (day, ticker, price, pct),
    )
    conn.commit()
    conn.close()


def _pred(store, day, ticker, direction="多", stop=None, trigger="外資買超第1名"):
    conn = sqlite3.connect(store.db_path)
    cur = conn.execute(
        """
        INSERT INTO predictions (prediction_date, ticker, name, direction, trigger,
                                 stop_loss_price, stop_loss_desc, raw_json)
        VALUES (?, ?, '', ?, ?, ?, '', '{}')
        """,
        (day, ticker, direction, trigger, stop),
    )
    conn.commit()
    pred_id = cur.lastrowid
    conn.close()
    return pred_id


def _result(store, pred_id, horizon):
    conn = sqlite3.connect(store.db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM prediction_results WHERE prediction_id = ? AND horizon_days = ?",
        (pred_id, horizon),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


class LedgerTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AnalyticsStore(os.path.join(self.tmp.name, "analytics.db"))

    def tearDown(self):
        self.tmp.cleanup()


class SessionCalendarHorizonTest(LedgerTestBase):
    def test_horizon_counts_twse_sessions_across_national_day(self):
        # 2026-10-09 (Fri) is closed, 10-10/10-11 weekend.
        anchor = date(2026, 10, 7)
        self.assertEqual(nth_session_after(anchor, 1), date(2026, 10, 8))
        self.assertEqual(nth_session_after(anchor, 3), date(2026, 10, 13))
        self.assertEqual(nth_session_after(anchor, 5), date(2026, 10, 15))
        self.assertEqual(nth_session_before(date(2026, 10, 12), 1), date(2026, 10, 8))
        # A weekend prediction is anchored on the previous session's close.
        self.assertEqual(anchor_session("2026-10-11"), date(2026, 10, 8))

    def test_missing_target_close_is_missing_not_a_later_substitute(self):
        pid = _pred(self.store, "2026-10-07", "2303")
        _snap(self.store, "2026-10-07", "2303", 100.0)
        _snap(self.store, "2026-10-12", "2303", 105.0)  # 10-08 never fetched
        _snap(self.store, "2026-10-07", "^TWII", 20000.0)
        _snap(self.store, "2026-10-12", "^TWII", 20100.0)
        self.store.recompute_prediction_results("2026-10-12")
        one = _result(self.store, pid, 1)
        self.assertEqual(one["status"], "missing")
        self.assertEqual(one["target_date"], "2026-10-08")
        self.assertIsNone(one["return_pct"])
        self.assertIsNone(one["hit"])
        # 3D target (10-13) is not due on 10-12: no row at all.
        self.assertIsNone(_result(self.store, pid, 3))

    def test_missing_base_never_falls_back_to_daily_pct_change(self):
        pid = _pred(self.store, "2026-10-07", "2313")
        _snap(self.store, "2026-10-08", "2313", 110.0, pct=9.98)
        _snap(self.store, "2026-10-07", "^TWII", 20000.0)
        _snap(self.store, "2026-10-08", "^TWII", 20000.0)
        self.store.recompute_prediction_results("2026-10-08")
        one = _result(self.store, pid, 1)
        self.assertEqual(one["status"], "missing")
        self.assertIsNone(one["return_pct"])

    def test_due_horizons_on_holiday_rerun_are_not_duplicated_values(self):
        # Old behaviour scored 1D and 3D on the same later day with one price.
        pid = _pred(self.store, "2026-10-07", "3037")
        for day, px, idx in (
            ("2026-10-07", 100.0, 20000.0),
            ("2026-10-08", 101.0, 20000.0),
            ("2026-10-12", 99.0, 20000.0),
            ("2026-10-13", 104.0, 20000.0),
        ):
            _snap(self.store, day, "3037", px)
            _snap(self.store, day, "^TWII", idx)
        self.store.recompute_prediction_results("2026-10-13")
        one, three = _result(self.store, pid, 1), _result(self.store, pid, 3)
        self.assertEqual(one["evaluated_date"], "2026-10-08")
        self.assertEqual(three["evaluated_date"], "2026-10-13")
        self.assertAlmostEqual(one["return_pct"], 1.0)
        self.assertAlmostEqual(three["return_pct"], 4.0)
        self.assertEqual(three["sessions_elapsed"], 3)
        self.assertEqual(three["method"], EVAL_METHOD)


class ExcessAndStopTest(LedgerTestBase):
    def _prices(self, ticker, rows):
        for day, px in rows:
            _snap(self.store, day, ticker, px)

    def test_index_return_uses_same_window_not_one_day_pct_change(self):
        pid = _pred(self.store, "2026-10-07", "2408")
        self._prices("2408", [("2026-10-07", 100.0), ("2026-10-08", 101.0), ("2026-10-12", 102.0), ("2026-10-13", 103.0)])
        # TWII +2% over the window, but its 10-13 daily pct_change is -0.5%.
        _snap(self.store, "2026-10-07", "^TWII", 20000.0, pct=0.3)
        _snap(self.store, "2026-10-08", "^TWII", 20100.0, pct=0.5)
        _snap(self.store, "2026-10-12", "^TWII", 20500.0, pct=2.0)
        _snap(self.store, "2026-10-13", "^TWII", 20400.0, pct=-0.5)
        self.store.recompute_prediction_results("2026-10-13")
        three = _result(self.store, pid, 3)
        self.assertAlmostEqual(three["index_return_pct"], 2.0)
        self.assertAlmostEqual(three["excess_return_pct"], 1.0)
        self.assertEqual(three["hit"], 1)  # +1.0pp > 3D dead band 0.87pp
        self.assertEqual(three["raw_hit"], 1)

    def test_excess_inside_dead_band_is_neutral_and_not_counted(self):
        pid = _pred(self.store, "2026-10-07", "2330")
        self._prices("2330", [("2026-10-07", 100.0), ("2026-10-08", 102.2)])
        _snap(self.store, "2026-10-07", "^TWII", 20000.0)
        _snap(self.store, "2026-10-08", "^TWII", 20400.0)  # +2.0%
        self.store.recompute_prediction_results("2026-10-08")
        one = _result(self.store, pid, 1)
        self.assertIsNone(one["hit"])
        self.assertEqual(one["raw_hit"], 1)
        summary = {r["horizon_days"]: r for r in self.store.get_horizon_performance_summary()}
        self.assertEqual(summary[1]["samples"], 0)
        self.assertEqual(summary[1]["neutral"], 1)
        self.assertEqual(summary[1]["evaluated"], 1)

    def test_stop_loss_breached_on_path_settles_at_that_close(self):
        pid = _pred(self.store, "2026-10-07", "2303", stop=97.0)
        self._prices("2303", [("2026-10-07", 100.0), ("2026-10-08", 96.0), ("2026-10-12", 105.0), ("2026-10-13", 110.0)])
        for day in ("2026-10-07", "2026-10-08", "2026-10-12", "2026-10-13"):
            _snap(self.store, day, "^TWII", 20000.0)
        self.store.recompute_prediction_results("2026-10-13")
        three = _result(self.store, pid, 3)
        self.assertEqual(three["status"], "stopped")
        self.assertEqual(three["evaluated_date"], "2026-10-08")
        self.assertEqual(three["target_date"], "2026-10-13")
        self.assertAlmostEqual(three["return_pct"], -4.0)
        self.assertEqual(three["hit"], 0)
        self.assertIn("停損", three["result_desc"])

    def test_short_stop_and_wrong_side_stop(self):
        short = _pred(self.store, "2026-10-07", "6770", direction="空", stop=104.0)
        wrong = _pred(self.store, "2026-10-07", "2344", stop=105.0)  # above a long entry
        self._prices("6770", [("2026-10-07", 100.0), ("2026-10-08", 104.5)])
        self._prices("2344", [("2026-10-07", 100.0), ("2026-10-08", 101.0)])
        _snap(self.store, "2026-10-07", "^TWII", 20000.0)
        _snap(self.store, "2026-10-08", "^TWII", 20000.0)
        self.store.recompute_prediction_results("2026-10-08")
        self.assertEqual(_result(self.store, short, 1)["status"], "stopped")
        self.assertEqual(_result(self.store, short, 1)["hit"], 0)
        self.assertEqual(_result(self.store, wrong, 1)["status"], "ok")

    def test_rerun_with_corrected_price_recomputes_frozen_result(self):
        pid = _pred(self.store, "2026-10-07", "2603")
        self._prices("2603", [("2026-10-07", 100.0), ("2026-10-08", 100.0)])  # stale bar
        _snap(self.store, "2026-10-07", "^TWII", 20000.0)
        _snap(self.store, "2026-10-08", "^TWII", 20000.0)
        self.store.recompute_prediction_results("2026-10-08")
        self.assertIsNone(_result(self.store, pid, 1)["hit"])
        _snap(self.store, "2026-10-08", "2603", 103.0)  # corrected close
        self.store.recompute_prediction_results("2026-10-08")
        row = _result(self.store, pid, 1)
        self.assertAlmostEqual(row["return_pct"], 3.0)
        self.assertEqual(row["hit"], 1)
        conn = sqlite3.connect(self.store.db_path)
        count = conn.execute("SELECT COUNT(*) FROM prediction_results").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_daily_closes_fill_gaps_for_listed_stocks(self):
        pid = _pred(self.store, "2026-10-07", "2382")
        self.store.record_daily_closes("2026-10-07", [{"id": "2382", "close_price": 300}])
        self.store.record_daily_closes("2026-10-08", [{"id": "2382", "close_price": 306}])
        _snap(self.store, "2026-10-07", "^TWII", 20000.0)
        _snap(self.store, "2026-10-08", "^TWII", 20000.0)
        self.store.recompute_prediction_results("2026-10-08")
        self.assertAlmostEqual(_result(self.store, pid, 1)["return_pct"], 2.0)

    def test_conditional_trigger_is_flagged(self):
        self.assertTrue(is_conditional_trigger("開盤價需高於602元"))
        self.assertTrue(is_conditional_trigger("若明日量縮且收盤不跌破今日低點 485 元"))
        self.assertFalse(is_conditional_trigger("外資買超第一且剛站上 MA20，AI 需求強勁"))


class PredictionKeyTest(LedgerTestBase):
    def _sd(self, targets):
        return {"index_action": "", "prediction_targets": targets}

    def test_same_day_rerun_rewording_updates_instead_of_duplicating(self):
        self.store.record_daily_run("2026-10-07", self._sd([{"id": "2408", "direction": "多", "trigger": "量增 1.5 倍"}]))
        self.store.record_daily_run("2026-10-07", self._sd([{"id": "2408", "direction": "多", "trigger": "量增 2.2 倍"}]))
        conn = sqlite3.connect(self.store.db_path)
        rows = conn.execute("SELECT ticker, trigger FROM predictions").fetchall()
        conn.close()
        self.assertEqual(rows, [("2408", "量增 2.2 倍")])

    def test_dropped_target_is_removed_with_its_results(self):
        self.store.record_daily_run(
            "2026-10-07",
            self._sd([{"id": "2408", "direction": "多"}, {"id": "3231", "direction": "多"}]),
        )
        self.store.record_daily_run("2026-10-07", self._sd([{"id": "3231", "direction": "空"}]))
        conn = sqlite3.connect(self.store.db_path)
        rows = conn.execute("SELECT ticker, direction FROM predictions").fetchall()
        conn.close()
        self.assertEqual(rows, [("3231", "空")])

    def test_empty_target_list_keeps_existing_rows(self):
        self.store.record_daily_run("2026-10-07", self._sd([{"id": "2408", "direction": "多"}]))
        self.store.record_daily_run("2026-10-07", self._sd([]))
        conn = sqlite3.connect(self.store.db_path)
        count = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_legacy_schema_duplicates_are_deduped_on_read(self):
        path = os.path.join(self.tmp.name, "legacy.db")
        conn = sqlite3.connect(path)
        conn.execute(
            """
            CREATE TABLE predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, prediction_date TEXT NOT NULL,
                ticker TEXT NOT NULL, name TEXT, direction TEXT, trigger TEXT,
                stop_loss_price REAL, stop_loss_desc TEXT, raw_json TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(prediction_date, ticker, direction, trigger))
            """
        )
        conn.execute(
            """
            CREATE TABLE prediction_results (
                prediction_id INTEGER NOT NULL, horizon_days INTEGER NOT NULL,
                evaluated_date TEXT NOT NULL, return_pct REAL, index_return_pct REAL,
                excess_return_pct REAL, hit INTEGER, close_price REAL, result_desc TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (prediction_id, horizon_days))
            """
        )
        conn.execute("INSERT INTO predictions (prediction_date, ticker, direction, trigger, raw_json) VALUES ('2026-10-07','2303','多','a','{}')")
        conn.execute("INSERT INTO predictions (prediction_date, ticker, direction, trigger, raw_json) VALUES ('2026-10-07','2303','空','b','{}')")
        # Legacy calendar-day row that is not due under session counting.
        conn.execute("INSERT INTO prediction_results (prediction_id, horizon_days, evaluated_date, return_pct, hit) VALUES (1, 3, '2026-10-12', 1.0, 1)")
        conn.commit()
        conn.close()

        store = AnalyticsStore(path)  # migrates columns; index skipped (duplicates)
        self.assertTrue(store.predictions_unique_key_is_legacy())
        _snap(store, "2026-10-07", "2303", 100.0)
        _snap(store, "2026-10-08", "2303", 98.0)
        _snap(store, "2026-10-07", "^TWII", 20000.0)
        _snap(store, "2026-10-08", "^TWII", 20000.0)
        store.recompute_prediction_results("2026-10-12")
        conn = sqlite3.connect(path)
        rows = conn.execute("SELECT prediction_id, horizon_days, hit, method FROM prediction_results").fetchall()
        conn.close()
        # Only the newest (空) prediction is scored; the legacy 3D row is gone.
        self.assertEqual(rows, [(2, 1, 1, EVAL_METHOD)])
        summary = store.get_horizon_performance_summary()
        self.assertEqual([r["horizon_days"] for r in summary], [1])
        self.assertEqual(summary[0]["samples"], 1)


class RecentTrendsTest(LedgerTestBase):
    def test_windows_are_sessions_and_sparse_windows_are_dropped(self):
        # 2603: 10-08 latest; 5 sessions before 10-08 is 10-01; no 10D base stored.
        _snap(self.store, "2026-05-20", "2603", 50.0)  # old sparse snapshot
        _snap(self.store, "2026-10-01", "2603", 80.0)
        _snap(self.store, "2026-10-08", "2603", 88.0)
        trends = self.store.get_recent_market_trends(["2603"], windows=(5, 10, 20), as_of="2026-10-12")
        info = trends["2603"]
        self.assertEqual(sorted(info["trends"]), ["5D"])
        self.assertEqual(info["trends"]["5D"]["from_date"], "2026-10-01")
        self.assertAlmostEqual(info["trends"]["5D"]["pct_change"], 10.0)
        self.assertEqual(info["as_of"], "2026-10-12")

    def test_stale_ticker_and_look_ahead_are_excluded(self):
        _snap(self.store, "2026-08-04", "1303", 90.0)
        _snap(self.store, "2026-08-11", "1303", 100.0)
        self.assertEqual(self.store.get_recent_market_trends(["1303"], windows=(5,), as_of="2026-09-24"), {})
        # Snapshot after as_of must not be read (backfill / resume --date).
        _snap(self.store, "2026-09-17", "2409", 10.0)
        _snap(self.store, "2026-09-24", "2409", 11.0)
        _snap(self.store, "2026-10-01", "2409", 30.0)
        trends = self.store.get_recent_market_trends(["2409"], windows=(5,), as_of="2026-09-24")
        self.assertEqual(trends["2409"]["latest_date"], "2026-09-24")
        self.assertAlmostEqual(trends["2409"]["trends"]["5D"]["pct_change"], 10.0)

    def test_latest_overrides_supply_todays_close(self):
        _snap(self.store, "2026-10-02", "2330", 100.0)  # 5 sessions before 10-12 (10-09 closed)
        trends = self.store.get_recent_market_trends(
            ["2330"], windows=(5,), as_of="2026-10-12", latest_overrides={"2330.TW": {"price": 104.0, "pct_change": 1.0}}
        )
        self.assertEqual(trends["2330"]["latest_date"], "2026-10-12")
        self.assertAlmostEqual(trends["2330"]["trends"]["5D"]["pct_change"], 4.0)


class InflightTickersTest(LedgerTestBase):
    def test_open_windows_prioritise_due_today_and_skip_tape_covered(self):
        _pred(self.store, "2026-09-01", "1111")  # window closed long ago
        _pred(self.store, "2026-10-07", "2222")  # 3D target 10-13 (due today)
        _pred(self.store, "2026-10-12", "3333")  # 1D target 10-13 (due today)
        _pred(self.store, "2026-10-01", "4444")  # open window, nothing due today
        _pred(self.store, "2026-10-08", "5555")  # on the TWSE tape today
        self.store.record_daily_closes("2026-10-13", [{"id": "5555", "close_price": 10}])
        out = self.store.get_inflight_prediction_tickers(as_of="2026-10-13")
        self.assertEqual(out[:2], ["3333", "2222"])
        self.assertIn("4444", out)
        self.assertNotIn("1111", out)
        self.assertNotIn("5555", out)
        self.assertEqual(len(self.store.get_inflight_prediction_tickers(as_of="2026-10-13", limit=1)), 1)


if __name__ == "__main__":
    unittest.main()
