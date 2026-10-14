#!/usr/bin/env python3
"""scripts/maintenance/recompute_prediction_results.py: dry-run safety and apply."""
import glob
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from scripts.maintenance.recompute_prediction_results import run  # noqa: E402

LEGACY_SCHEMA = """
CREATE TABLE predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, prediction_date TEXT NOT NULL,
    ticker TEXT NOT NULL, name TEXT, direction TEXT, trigger TEXT,
    stop_loss_price REAL, stop_loss_desc TEXT, raw_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(prediction_date, ticker, direction, trigger));
CREATE TABLE prediction_results (
    prediction_id INTEGER NOT NULL, horizon_days INTEGER NOT NULL,
    evaluated_date TEXT NOT NULL, return_pct REAL, index_return_pct REAL,
    excess_return_pct REAL, hit INTEGER, close_price REAL, result_desc TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (prediction_id, horizon_days),
    FOREIGN KEY (prediction_id) REFERENCES predictions(id));
CREATE TABLE market_snapshots (
    snapshot_date TEXT NOT NULL, ticker TEXT NOT NULL, price REAL, pct_change REAL,
    volume INTEGER, avg_vol_5d INTEGER, vol_ratio REAL, ma5 REAL, ma20 REAL, rsi REAL,
    raw_json TEXT NOT NULL, PRIMARY KEY (snapshot_date, ticker));
"""


def _md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


class RecomputeScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.db = os.path.join(root, "analytics.db")
        conn = sqlite3.connect(self.db)
        conn.executescript(LEGACY_SCHEMA)
        rows = [
            ("2026-10-07", "2303", "多", "a"),
            ("2026-10-07", "2303", "空", "b"),  # same-day rerun flipped direction
            ("2026-10-07", "2408", "多", "c"),  # dropped from the final JSON
        ]
        for day, ticker, direction, trigger in rows:
            conn.execute(
                "INSERT INTO predictions (prediction_date, ticker, direction, trigger, raw_json) VALUES (?,?,?,?,'{}')",
                (day, ticker, direction, trigger),
            )
        # Legacy calendar-day result: 1D scored on a later day with the daily pct_change.
        conn.execute(
            "INSERT INTO prediction_results (prediction_id, horizon_days, evaluated_date, return_pct, "
            "index_return_pct, excess_return_pct, hit) VALUES (1, 1, '2026-10-12', 5.0, 1.0, 4.0, 1)"
        )
        for day, px, idx in (("2026-10-07", 100, 20000), ("2026-10-08", 97, 20000)):
            conn.execute("INSERT INTO market_snapshots (snapshot_date, ticker, price, raw_json) VALUES (?, '2303', ?, '{}')", (day, px))
            conn.execute("INSERT INTO market_snapshots (snapshot_date, ticker, price, raw_json) VALUES (?, '^TWII', ?, '{}')", (day, idx))
        conn.commit()
        conn.close()

        self.reports = os.path.join(root, "reports")
        os.makedirs(os.path.join(self.reports, "daily", "2026-10-07"))
        with open(os.path.join(self.reports, "daily", "2026-10-07", "structured_data.json"), "w", encoding="utf-8") as f:
            json.dump({"prediction_targets": [{"id": "2303", "direction": "空", "trigger": "reworded"}]}, f)

        self.stock_db = os.path.join(root, "stock_db.json")
        dup = {"date": "2026-09-22", "direction": "多", "trigger": "t", "result": "實際 +9.90% → ✅ 命中"}
        with open(self.stock_db, "w", encoding="utf-8") as f:
            json.dump(
                {"2409": {"name": "友達", "tracking": {"prediction_count": 2, "hit_count": 2, "history": [dup, dict(dup)]}}},
                f,
                ensure_ascii=False,
            )

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_leaves_files_untouched(self):
        before_db, before_stock = _md5(self.db), _md5(self.stock_db)
        text, result = run(
            db_path=self.db, as_of="2026-10-12", reports_dir=self.reports, stock_db_path=self.stock_db
        )
        self.assertEqual(_md5(self.db), before_db)
        self.assertEqual(_md5(self.stock_db), before_stock)
        self.assertIn("DRY RUN", text)
        self.assertIn("2026-10-07 2408", text)
        self.assertEqual(result["issues"]["duplicate_ids"], [1])
        summary = {r["horizon_days"]: r for r in result["summary"]}
        self.assertEqual(summary[1]["samples"], 1)
        self.assertEqual(summary[1]["hit_rate"], 1.0)  # 空 call, -3% vs flat index
        self.assertIn("2409 友達：移除 1 筆", text)
        self.assertEqual(glob.glob(self.db + ".bak-*"), [])

    def test_daily_run_before_the_script_keeps_the_legacy_comparison(self):
        from modules.analytics_store import AnalyticsStore

        AnalyticsStore(self.db).record_daily_run("2026-10-12", {"index_action": ""})
        conn = sqlite3.connect(self.db)
        legacy = conn.execute(
            "SELECT evaluated_date, return_pct, hit, method FROM prediction_results WHERE prediction_id = 1"
        ).fetchall()
        conn.close()
        self.assertEqual(legacy, [("2026-10-12", 5.0, 1, None)])
        text, result = run(db_path=self.db, as_of="2026-10-12", reports_dir=self.reports, stock_db_path=None)
        self.assertIn("| 1D | 1 | 100.0% | +5.00% | +4.00% |", text)

    def test_apply_backs_up_dedupes_prunes_and_migrates_key(self):
        run(
            db_path=self.db,
            as_of="2026-10-12",
            reports_dir=self.reports,
            stock_db_path=self.stock_db,
            apply=True,
            prune_orphans=True,
            stock_db_dedupe=True,
        )
        self.assertEqual(len(glob.glob(self.db + ".bak-*")), 1)
        self.assertEqual(len(glob.glob(self.stock_db + ".bak-*")), 1)
        conn = sqlite3.connect(self.db)
        preds = conn.execute("SELECT id, ticker, direction FROM predictions").fetchall()
        results = conn.execute("SELECT prediction_id, horizon_days, method, hit FROM prediction_results").fetchall()
        schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='predictions'").fetchone()[0]
        conn.close()
        self.assertEqual(preds, [(2, "2303", "空")])
        self.assertEqual(results, [(2, 1, "sessions_v2", 1)])
        self.assertIn("UNIQUE(prediction_date, ticker)", schema)
        # The rebuild must not leave prediction_results pointing at a dropped table.
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA foreign_keys=ON")
        results_sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='prediction_results'").fetchone()[0]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("REFERENCES predictions(id)", results_sql)
        self.assertNotIn("legacy_key", results_sql)
        self.assertNotIn("predictions_new", tables | {results_sql})
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        conn.execute(
            "INSERT INTO prediction_results (prediction_id, horizon_days, evaluated_date) VALUES (2, 3, '2026-10-13')"
        )
        new_id = conn.execute(
            "INSERT INTO predictions (prediction_date, ticker, raw_json) VALUES ('2026-10-13', '2330', '{}')"
        ).lastrowid
        conn.commit()
        conn.close()
        self.assertGreater(new_id, 3)  # AUTOINCREMENT sequence survived the rebuild
        with open(self.stock_db, encoding="utf-8") as f:
            tracking = json.load(f)["2409"]["tracking"]
        self.assertEqual((tracking["prediction_count"], len(tracking["history"])), (1, 1))


class BackfillOnePathPerDateTest(unittest.TestCase):
    def test_final_daily_copy_wins_over_legacy_copies(self):
        from scripts.maintenance.backfill_analytics import _one_path_per_date

        root = os.path.join("r")
        daily = os.path.join(root, "daily", "2026-05-26", "structured_data.json")
        legacy = os.path.join(root, "daily_outputs", "2026-05-26", "structured_data_20260526.json")
        root_copy = os.path.join(root, "structured_data_20260526.json")
        other = os.path.join(root, "structured_data_20260527.json")
        self.assertEqual(_one_path_per_date([legacy, root_copy, daily, other]), [daily, other])


if __name__ == "__main__":
    unittest.main()
