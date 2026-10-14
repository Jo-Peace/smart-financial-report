#!/usr/bin/env python3
"""Item 7 / finding 17: replayed sessions must not collapse onto one editorial frame."""
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
from scripts.maintenance.backtest_scores import (  # noqa: E402
    ANALYTICS_DB,
    MAX_FRAME_SHARE,
    REPORTS_DIR,
    STOCK_DB,
    replay_frames,
)

LOCAL_REPLAY_SESSIONS = 30
LOCAL_MIN_SESSIONS = 20


def _quote(price, pct, volume, vol_ratio=1.0):
    return {
        "price": price, "pct_change": pct, "volume": volume, "vol_ratio": vol_ratio,
        "ma5": price * 0.99, "ma20": price * 0.95, "rsi": 55,
    }


# 12 TWSE sessions, each with a deliberate shape. Calendar facts are real:
# 09-17 is the FOMC TW session; 09-16 (2330 ex-div) and 09-18 (FTSE) carry no anomaly.
QUIET = {"index": 0.3}
FIXTURE = [
    ("2026-09-08", dict(QUIET, buy=[("1101", "台泥", 3.0, 0.4)])),
    ("2026-09-09", dict(QUIET, sell=[("2609", "陽明", -30.0, 5.0)])),
    ("2026-09-10", dict(QUIET, buy=[("1216", "統一", 3.0, 0.3)])),
    ("2026-09-11", dict(QUIET, buy=[("3037", "欣興", 20.0, 1.0)], sell=[("2609", "陽明", -30.0, 5.5)])),
    ("2026-09-14", dict(QUIET, buy=[("3037", "欣興", 20.0, 1.0)])),
    ("2026-09-15", dict(QUIET, buy=[("3037", "欣興", 20.0, 1.2)])),   # 3 of 5 → continuation
    ("2026-09-16", dict(QUIET, buy=[("3037", "欣興", 18.0, 0.8)])),   # 4 of 5 → continuation
    ("2026-09-17", {"index": -1.6, "buy": [("1301", "台塑", 2.0, 0.2)]}),  # FOMC + tape reaction
    ("2026-09-18", dict(QUIET, buy=[("2002", "中鋼", 3.0, 0.5)])),
    ("2026-09-21", dict(QUIET, sell=[("2615", "萬海", -30.0, 5.0)])),
    ("2026-09-22", dict(QUIET, buy=[("2105", "正新", 3.0, 0.4)])),
    ("2026-09-23", dict(QUIET, sell=[("2618", "長榮航", -30.0, 6.0)])),
]


def _build_fixture_db(path):
    AnalyticsStore(path)  # creates the schema
    conn = sqlite3.connect(path)
    try:
        for day, spec in FIXTURE:
            snapshots = {"^TWII": {"price": 22000.0, "pct_change": spec["index"], "volume": 0}}
            for side in ("buy", "sell"):
                for rank, (sid, name, amount, pct) in enumerate(spec.get(side) or [], start=1):
                    snapshots[sid] = _quote(100.0, pct, 10_000_000, 1.6 if side == "sell" else 1.0)
                    item = {"id": sid, "name": name, "est_amount": amount, "foreign_net": int(amount * 1e6)}
                    conn.execute(
                        "INSERT INTO institutional_flows (flow_date, ticker, name, side, rank, foreign_net,"
                        " est_amount, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (day, sid, name, side, rank, item["foreign_net"], amount, json.dumps(item)),
                    )
            for sid, quote in snapshots.items():
                conn.execute(
                    "INSERT INTO market_snapshots (snapshot_date, ticker, price, pct_change, volume, raw_json)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (day, sid, quote["price"], quote["pct_change"], quote["volume"], json.dumps(quote)),
                )
        conn.commit()
    finally:
        conn.close()


class FrameReplayTest(unittest.TestCase):
    def test_fixture_sessions_spread_across_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "analytics.db")
            _build_fixture_db(db)
            result = replay_frames(
                db_path=db, stock_db_path=os.path.join(tmp, "none.json"), reports_dir=None, last_n=len(FIXTURE)
            )
        self.assertEqual(result["sessions"], len(FIXTURE))
        by_day = {row["date"]: row["frame"] for row in result["per_day"]}
        self.assertEqual(by_day["2026-09-09"], "anomaly_day")
        self.assertEqual(by_day["2026-09-15"], "continuation_day")
        self.assertEqual(by_day["2026-09-17"], "major_event_day")
        self.assertEqual(by_day["2026-09-08"], "quiet_setup_day")
        self.assertGreaterEqual(len(result["counts"]), 4)
        self.assertLessEqual(result["max_share"], MAX_FRAME_SHARE, result["counts"])

    @unittest.skipUnless(os.path.exists(ANALYTICS_DB), "no local data/analytics.db")
    def test_recent_local_sessions_do_not_collapse_onto_one_frame(self):
        result = replay_frames(
            db_path=ANALYTICS_DB, stock_db_path=STOCK_DB, reports_dir=REPORTS_DIR, last_n=LOCAL_REPLAY_SESSIONS
        )
        if result["sessions"] < LOCAL_MIN_SESSIONS:
            self.skipTest(f"only {result['sessions']} local sessions")
        self.assertLessEqual(result["max_share"], MAX_FRAME_SHARE, result["counts"])


if __name__ == "__main__":
    unittest.main()
