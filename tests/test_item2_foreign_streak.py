#!/usr/bin/env python3
"""Item 2: 外資連續買超天數 counted in TWSE sessions (findings 15/39/63/79)."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.analytics_store import AnalyticsStore  # noqa: E402
from modules.broadcast_facts import build_broadcast_facts, build_local_market_facts  # noqa: E402
from modules.foreign_streak import (  # noqa: E402
    annotate_foreign_streaks,
    compute_foreign_buy_streaks,
    format_streak_text,
)
from modules.notebooklm_source_builder import build_notebooklm_source  # noqa: E402
from modules.stock_db_updater import StockDatabaseUpdater  # noqa: E402

# Real 2026-10 sequence: 3037 欣興 bought 10-02, 10-05..10-08, 10-12 (10-09 國慶補假),
# 1303 南亞 sold 10-12, bought 10-13 and 10-14.
FLOWS = {
    "2026-10-01": {"buy": ["2330"], "sell": ["1303"]},
    "2026-10-02": {"buy": ["3037", "1303"], "sell": ["2330"]},
    "2026-10-05": {"buy": ["3037"], "sell": ["2330"]},
    "2026-10-06": {"buy": ["3037", "1303"], "sell": ["2330"]},
    "2026-10-07": {"buy": ["3037", "1303"], "sell": ["2330"]},
    "2026-10-08": {"buy": ["3037"], "sell": ["1303"]},
    "2026-10-12": {"buy": ["3037"], "sell": ["1303"]},
    "2026-10-13": {"buy": ["1303"], "sell": ["3037"]},
}


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _payload(day, buy, sell=()):
    return {
        "data_date": day,
        "top_buy": [{"id": t, "name": t, "foreign_net": 1000, "trust_net": 0, "total_net": 1000, "est_amount": 10.0} for t in buy],
        "top_sell": [{"id": t, "name": t, "foreign_net": -1000, "trust_net": 0, "total_net": -1000, "est_amount": -10.0} for t in sell],
    }


class ForeignStreakTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "analytics.db")
        store = AnalyticsStore(db_path=self.db)
        conn = store._connect()
        cur = conn.cursor()
        for day, sides in FLOWS.items():
            store._record_institutional_flows(cur, day, _payload(day, sides["buy"], sides["sell"]))
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_counts_trading_sessions_across_weekend_and_holiday(self):
        streaks = compute_foreign_buy_streaks(_payload("2026-10-12", ["3037"], ["1303"]), db_path=self.db)
        self.assertEqual(streaks["3037"]["days"], 6)
        self.assertEqual(streaks["3037"]["since"], "2026-10-02")
        self.assertTrue(streaks["3037"]["complete"])
        self.assertEqual(streaks["1303"]["days"], 0)

    def test_report_date_not_today_and_rerun_is_idempotent(self):
        today = _payload("2026-10-14", ["1303"], ["2330"])
        first = compute_foreign_buy_streaks(today, db_path=self.db)
        self.assertEqual(first["1303"]["days"], 2)
        # /1 records today's flows after the streak was computed; a rerun must not count today twice.
        store = AnalyticsStore(db_path=self.db)
        conn = store._connect()
        store._record_institutional_flows(conn.cursor(), "2026-10-14", today)
        conn.commit()
        conn.close()
        again = compute_foreign_buy_streaks(today, db_path=self.db)
        self.assertEqual(again["1303"]["days"], 2)

    def test_missing_session_is_a_lower_bound_not_a_reset(self):
        streaks = compute_foreign_buy_streaks(_payload("2026-10-02", ["3037"]), db_path=self.db)
        # 10-01 has data but no 3037 buy → a complete count of 1
        self.assertEqual((streaks["3037"]["days"], streaks["3037"]["complete"]), (1, True))
        gap = compute_foreign_buy_streaks(_payload("2026-10-01", ["2330"]), db_path=self.db)
        self.assertEqual(gap["2330"]["days"], 1)
        self.assertFalse(gap["2330"]["complete"])  # 09-30 not stored → unknown before
        self.assertIn("至少 1", format_streak_text(1, False))

    def test_rerun_replaces_stale_list_rows(self):
        store = AnalyticsStore(db_path=self.db)
        conn = store._connect()
        store._record_institutional_flows(conn.cursor(), "2026-10-13", _payload("2026-10-13", ["2313"], ["3037"]))
        conn.commit()
        rows = conn.execute(
            "SELECT ticker FROM institutional_flows WHERE flow_date='2026-10-13' AND side='buy'"
        ).fetchall()
        conn.close()
        self.assertEqual([r[0] for r in rows], ["2313"])

    def test_stock_db_refresh_every_run_without_predictions(self):
        db_file = os.path.join(self.tmp.name, "stock_db.json")
        with open(db_file, "w", encoding="utf-8") as f:
            json.dump({"3037": {"name": "欣興"}, "1303": {"name": "南亞"}}, f)
        updater = StockDatabaseUpdater.__new__(StockDatabaseUpdater)
        updater.db_path = db_file
        updater.backup_dir = os.path.join(self.tmp.name, "backup")
        updater.load_error = None
        updater.stock_db = _read_json(db_file)
        payload = _payload("2026-10-12", ["3037"], ["1303"])
        streaks = annotate_foreign_streaks(payload, "2026-10-12", db_path=self.db)
        self.assertTrue(updater.refresh_foreign_streaks(payload, as_of_date="2026-10-12", streaks=streaks))
        saved = _read_json(db_file)
        self.assertEqual(saved["3037"]["tracking"]["consecutive_foreign_buy_days"], 6)
        self.assertEqual(saved["3037"]["tracking"]["last_foreign_buy_date"], "2026-10-12")
        self.assertEqual(saved["1303"]["tracking"]["consecutive_foreign_buy_days"], 0)
        # same-day rerun: nothing changes
        self.assertFalse(updater.refresh_foreign_streaks(payload, as_of_date="2026-10-12", streaks=streaks))
        self.assertEqual(payload["top_buy"][0]["foreign_buy_streak"], 6)

    def test_streak_reaches_cards_and_source(self):
        payload = _payload("2026-10-12", ["3037"], ["1303"])
        annotate_foreign_streaks(payload, "2026-10-12", db_path=self.db)
        payload["top_buy"][0]["name"] = "欣興"
        sd = {
            "date": "2026-10-12",
            "safe_havens_bought": ["欣興 (+15.5億)"],
            "heavyweights_dumped": ["南亞 (-26.5億)"],
            "prediction_targets": [{"id": "3037", "name": "欣興", "direction": "多"}],
            "local_market_facts": build_local_market_facts(payload, [], {}),
        }
        # stock_db holds the old calendar-yesterday value; the session count wins.
        facts = build_broadcast_facts(sd, stock_db={"3037": {"tracking": {"consecutive_foreign_buy_days": 1, "last_foreign_buy_date": "2026-10-12"}}})
        card = next(h for h in facts["heroes"] if h["ticker"] == "3037")
        self.assertEqual(card["foreign_streak_days"], 6)
        sd["broadcast_facts"] = facts
        self.assertIn("外資連續買超：6 個交易日", build_notebooklm_source(sd, date_str="2026-10-12"))

    def test_analyzer_prompt_carries_session_streak(self):
        from unittest import mock

        from modules.analyzer import MarketAnalyzer

        payload = _payload("2026-10-12", ["3037"], ["1303"])
        annotate_foreign_streaks(payload, "2026-10-12", db_path=self.db)
        analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
        analyzer.model_name = "test"
        analyzer.stock_db = {}
        with mock.patch.object(MarketAnalyzer, "_call_gemini_with_retry", side_effect=lambda prompt, **_: prompt):
            prompt = analyzer.generate_report({}, [], institutional_data=payload, report_date="2026-10-12")
        self.assertIn("外資連續買超 6 個交易日", prompt)

    def test_stale_stock_db_streak_is_not_read(self):
        sd = {
            "date": "2026-10-14",
            "safe_havens_bought": ["欣興 (+15.5億)"],
            "prediction_targets": [{"id": "3037", "name": "欣興", "direction": "多"}],
        }
        facts = build_broadcast_facts(
            sd, stock_db={"3037": {"tracking": {"consecutive_foreign_buy_days": 4, "last_foreign_buy_date": "2026-10-08"}}}
        )
        card = next(h for h in facts["heroes"] if h["ticker"] == "3037")
        self.assertIsNone(card["foreign_streak_days"])

    def test_legacy_same_day_stock_db_count_is_not_a_session_count(self):
        # Old structured_data (no local streak) + a stock_db row written by the old
        # calendar-yesterday counter for the same date: 「1 日」 is not trusted.
        sd = {
            "date": "2026-10-14",
            "safe_havens_bought": ["南亞 (+55.3億)"],
            "prediction_targets": [{"id": "1303", "name": "南亞", "direction": "多"}],
        }
        legacy = {"1303": {"tracking": {"consecutive_foreign_buy_days": 1, "last_foreign_buy_date": "2026-10-14"}}}
        card = next(h for h in build_broadcast_facts(sd, stock_db=legacy)["heroes"] if h["ticker"] == "1303")
        self.assertIsNone(card["foreign_streak_days"])
        sd["broadcast_facts"] = build_broadcast_facts(sd, stock_db=legacy)
        source = build_notebooklm_source(sd, date_str="2026-10-14")
        self.assertIn("外資連續買超：資料不足", source)
        self.assertNotIn("外資連續買超：1 個交易日", source)
        # a count the session counter stamped for this date is read
        fresh = {
            "1303": {
                "tracking": {
                    "consecutive_foreign_buy_days": 2,
                    "last_foreign_buy_date": "2026-10-14",
                    "foreign_buy_streak_date": "2026-10-14",
                    "foreign_buy_streak_complete": True,
                }
            }
        }
        card = next(h for h in build_broadcast_facts(sd, stock_db=fresh)["heroes"] if h["ticker"] == "1303")
        self.assertEqual((card["foreign_streak_days"], card["foreign_streak_complete"]), (2, True))

    def test_stale_rank_collision_rows_do_not_extend_streaks(self):
        # Pre-fix reruns left 2313 華通 at buy #1 next to 3037 欣興 #1 on 10-13 (it
        # dropped out of the later run); the latest run's list is the larger amounts.
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO institutional_flows (flow_date, ticker, name, side, rank, foreign_net, trust_net, total_net, est_amount, raw_json) "
            "VALUES ('2026-10-13', '2313', '華通', 'buy', 1, 500, 0, 500, 5.0, '{}')"
        )
        conn.commit()
        conn.close()
        streaks = compute_foreign_buy_streaks(_payload("2026-10-14", ["2313", "1303"]), db_path=self.db)
        self.assertEqual(streaks["2313"]["days"], 1)
        self.assertEqual(streaks["1303"]["days"], 2)
        # opening the store removes the stale row once, and only that row
        AnalyticsStore(db_path=self.db)
        conn = sqlite3.connect(self.db)
        rows = conn.execute(
            "SELECT ticker FROM institutional_flows WHERE flow_date='2026-10-13' AND side='buy' ORDER BY rank"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM institutional_flows").fetchone()[0]
        conn.close()
        self.assertEqual([r[0] for r in rows], ["1303"])
        AnalyticsStore(db_path=self.db)
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM institutional_flows").fetchone()[0], total)
        conn.close()

    def test_refresh_stamps_streak_date_on_legacy_rows(self):
        db_file = os.path.join(self.tmp.name, "stock_db.json")
        with open(db_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "1303": {
                        "name": "南亞",
                        "tracking": {
                            "consecutive_foreign_buy_days": 0,
                            "last_foreign_buy_date": "2026-10-08",
                            "foreign_buy_streak_complete": True,
                        },
                    }
                },
                f,
            )
        updater = StockDatabaseUpdater.__new__(StockDatabaseUpdater)
        updater.db_path = db_file
        updater.backup_dir = os.path.join(self.tmp.name, "backup")
        updater.load_error = None
        updater.stock_db = _read_json(db_file)
        payload = _payload("2026-10-12", ["3037"], ["1303"])
        streaks = annotate_foreign_streaks(payload, "2026-10-12", db_path=self.db)
        # a sell day leaves days/last date unchanged, but the count's date must still be saved
        self.assertTrue(updater.refresh_foreign_streaks(payload, as_of_date="2026-10-12", streaks=streaks))
        self.assertEqual(_read_json(db_file)["1303"]["tracking"]["foreign_buy_streak_date"], "2026-10-12")


if __name__ == "__main__":
    unittest.main()
