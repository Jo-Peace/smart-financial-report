#!/usr/bin/env python3
"""Item 7: offline score backtest (IC / terciles / calibration) on a synthetic local DB."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.local_knowledge_compounder import LocalKnowledgeCompounder  # noqa: E402
from scripts.maintenance.backtest_scores import (  # noqa: E402
    _strip_lookahead,
    format_report,
    forward_return,
    point_in_time_roles,
    replay,
    session_value_inputs,
    spearman,
    tercile_table,
)

SESSIONS = [
    "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07", "2026-09-08",
    "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15", "2026-09-16",
]
TICKERS = ["3037", "2408", "2344", "3231", "4958", "2303"]


def _build_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE market_snapshots (snapshot_date TEXT, ticker TEXT, price REAL, pct_change REAL, raw_json TEXT);
        CREATE TABLE institutional_flows (flow_date TEXT, ticker TEXT, name TEXT, side TEXT, rank INTEGER,
            est_amount REAL, raw_json TEXT);
        CREATE TABLE volume_rankings (ranking_date TEXT, ticker TEXT, rank INTEGER, close_price REAL, raw_json TEXT);
        """
    )
    index = 20000.0
    prices = {t: 100.0 + 10 * i for i, t in enumerate(TICKERS)}
    for d_i, day in enumerate(SESSIONS):
        index *= 1.001
        conn.execute(
            "INSERT INTO market_snapshots VALUES (?,?,?,?,?)",
            (day, "^TWII", index, 0.1, json.dumps({"price": index, "pct_change": 0.1})),
        )
        for rank, ticker in enumerate(TICKERS, start=1):
            # higher buy rank → stronger next-day drift, so foreign strength has a positive IC
            drift = (len(TICKERS) - rank) * 0.4 - 1.0
            prices[ticker] *= 1 + drift / 100
            payload = {"price": prices[ticker], "pct_change": drift, "volume": 1_000_000, "vol_ratio": 1.1}
            conn.execute(
                "INSERT INTO market_snapshots VALUES (?,?,?,?,?)",
                (day, ticker, prices[ticker], drift, json.dumps(payload)),
            )
            item = {"id": ticker, "name": f"N{ticker}", "est_amount": 20.0 - rank, "foreign_net": 1000}
            conn.execute(
                "INSERT INTO institutional_flows VALUES (?,?,?,?,?,?,?)",
                (day, ticker, item["name"], "buy", rank, item["est_amount"], json.dumps(item)),
            )
            vol = {"rank": rank, "id": ticker, "name": item["name"], "volume": 5_000_000,
                   "close_price": prices[ticker], "pct_change": drift}
            conn.execute(
                "INSERT INTO volume_rankings VALUES (?,?,?,?,?)",
                (day, ticker, rank, prices[ticker], json.dumps(vol)),
            )
    conn.commit()
    conn.close()


class StatsTest(unittest.TestCase):
    def test_spearman_and_terciles(self):
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)
        self.assertIsNone(spearman([1, 1, 1], [1, 2, 3]))
        table = tercile_table([(i, float(i)) for i in range(9)])
        self.assertEqual([row["bucket"] for row in table], ["低", "中", "高"])
        self.assertLess(table[0]["mean_excess"], table[2]["mean_excess"])

    def test_forward_return_uses_sessions_and_index(self):
        panel = {
            "2026-09-11": {"3037": 100.0, "^TWII": 20000.0},
            "2026-09-14": {"3037": 103.0, "^TWII": 20200.0},  # next TWSE session after Friday
        }
        fwd = forward_return(panel, "2026-09-11", "3037", 1)
        self.assertAlmostEqual(fwd["raw"], 3.0)
        self.assertAlmostEqual(fwd["excess"], 2.0)
        self.assertIsNone(forward_return(panel, "2026-09-11", "2408", 1))


class ReplayTest(unittest.TestCase):
    def test_replay_runs_offline_on_local_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "analytics.db")
            _build_db(db)
            stock_db = os.path.join(tmp, "stock_db.json")
            with open(stock_db, "w", encoding="utf-8") as f:
                json.dump({t: {"name": f"N{t}"} for t in TICKERS}, f)
            result = replay(db_path=db, stock_db_path=stock_db, horizons=(1,))
            self.assertEqual(result["sessions_replayed"], len(SESSIONS))
            fb = result["scores"]["foreign_buy_strength"]["1"]
            self.assertGreater(fb["observations"], 0)
            self.assertGreater(fb["mean_ic"], 0)
            self.assertIn("1", result["confidence_calibration"])
            self.assertTrue(result["roles_point_in_time"])
            cov = result["coverage"]["1"]
            self.assertGreater(cov["scored_names"], 0)
            self.assertLessEqual(cov["with_forward_close"], cov["scored_names"])
            self.assertGreaterEqual(cov["low_coverage_days"], 1)  # last session has no T+1 close
            report = format_report(result)
            self.assertIn("日均 IC", report)
            self.assertIn("覆蓋率", report)


class ValueRankBasisTest(unittest.TestCase):
    """The backtest replays the 成交值 rank basis live /1 used (market-wide when the tape exists)."""

    def _db_with_tape(self, tmp, tape_days):
        db = os.path.join(tmp, "analytics.db")
        _build_db(db)
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE daily_closes (close_date TEXT, ticker TEXT, name TEXT, close_price REAL,"
            " volume INTEGER, source TEXT)"
        )
        for day in tape_days:
            rows = [("2330", "台積電", 1000.0, 50_000_000), ("0050", "元大台灣50", 200.0, 900_000_000)]
            rows += [(t, f"N{t}", 100.0 + i, 1_000_000 * (i + 1)) for i, t in enumerate(TICKERS)]
            rows += [(f"9{i:03d}", f"小{i}", 10.0, 1000) for i in range(30)]
            for ticker, name, close, volume in rows:
                conn.execute(
                    "INSERT INTO daily_closes VALUES (?,?,?,?,?,?)", (day, ticker, name, close, volume, "twse_day_all")
                )
        conn.commit()
        conn.close()
        return db

    def test_session_value_inputs_rebuild_the_market_ranking(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db_with_tape(tmp, [SESSIONS[0]])
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            try:
                board = [{"rank": 1, "id": "3037", "volume": 5_000_000, "close_price": 100.0,
                          "value_rank": 99, "value_rank_basis": "stocks_ex_etf"}]
                volume, ranking, basis = session_value_inputs(conn, SESSIONS[0], board)
                self.assertEqual(basis, "market")
                ids = [row["id"] for row in ranking["stocks"]]
                self.assertEqual(len(ids), 20)  # same size as the /1 成交值前20
                self.assertEqual(ids[0], "2330")
                self.assertNotIn("0050", ids)  # ETFs ranked separately, as live
                self.assertEqual(ranking["etfs"][0]["id"], "0050")
                # Board rows carry the rank of the same rebuilt tape (close × shares), not the recorded 99.
                self.assertEqual(volume[0]["value_rank"], ids.index("3037") + 1)
                self.assertEqual(board[0]["value_rank"], 99)  # input untouched
                # No recorded tape that day → pool basis, rows unchanged.
                same, none, pool = session_value_inputs(conn, SESSIONS[1], board)
                self.assertEqual((none, pool), (None, "pool"))
                self.assertIs(same, board)
            finally:
                conn.close()

    def test_replay_reports_the_rank_basis_per_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db_with_tape(tmp, SESSIONS[:4])
            stock_db = os.path.join(tmp, "stock_db.json")
            with open(stock_db, "w", encoding="utf-8") as f:
                json.dump({t: {"name": f"N{t}"} for t in TICKERS}, f)
            result = replay(db_path=db, stock_db_path=stock_db, horizons=(1,))
            self.assertEqual(result["turnover_rank_basis_days"], {"market": 4, "pool": len(SESSIONS) - 4})
            self.assertIn("成交值名次基準：全市場 4 日", format_report(result))


class PointInTimeRoleTest(unittest.TestCase):
    def test_roles_use_only_flows_up_to_the_previous_session(self):
        days = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07"]
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "analytics.db")
            conn = sqlite3.connect(db)
            conn.execute(
                "CREATE TABLE institutional_flows (flow_date TEXT, ticker TEXT, name TEXT, side TEXT, rank INTEGER,"
                " est_amount REAL, raw_json TEXT)"
            )
            for day in days:
                conn.execute(
                    "INSERT INTO institutional_flows VALUES (?, '3037', 'N3037', 'sell', 1, -10.0, '{}')", (day,)
                )
            conn.commit()
            conn.close()
            stock_db = {"3037": {"name": "N3037", "market_role": {"base_role": "elastic_attack"},
                                 "tracking": {"prediction_count": 9, "hit_rate": 0.9}}}
            base = _strip_lookahead(stock_db)
            self.assertNotIn("market_role", base["3037"])
            self.assertNotIn("tracking", base["3037"])
            comp = LocalKnowledgeCompounder(base_dir=tmp)
            sold = {"top_buy": [], "top_sell": [{"id": "3037"}]}
            first = point_in_time_roles(base, comp, db, None, None)
            self.assertEqual(first["3037"]["market_role"]["base_role"], "theme_confirmation")
            # Role for 09-04 is decided on 09-03: 2 prior sell sessions + 09-03 = 3 < 4.
            early = point_in_time_roles(base, comp, db, "2026-09-03", sold)
            self.assertNotEqual(early["3037"]["market_role"]["base_role"], "risk_warning")
            # Role for 09-07 is decided on 09-04: 3 prior + 09-04 = 4 → data-backed risk_warning.
            later = point_in_time_roles(base, comp, db, "2026-09-04", sold)
            self.assertEqual(later["3037"]["market_role"]["base_role"], "risk_warning")
            self.assertEqual(stock_db["3037"]["market_role"]["base_role"], "elastic_attack")  # input untouched


if __name__ == "__main__":
    unittest.main()
