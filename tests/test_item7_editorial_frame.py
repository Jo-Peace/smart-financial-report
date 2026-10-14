#!/usr/bin/env python3
"""Item 7: editorial_frame picks the best-scoring frame from structured evidence."""
import os
import sqlite3
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.editorial_frame import (  # noqa: E402
    build_editorial_frame,
    eligible_radar_events,
    is_low_tier_source,
    score_major_event,
)
from modules.event_radar import carry_forward_radar  # noqa: E402
from modules.timeframe_builder import _radar_pick  # noqa: E402

NO_DB = "/nonexistent/analytics.db"
HEADLINES = "本週全球重要財經日曆 Fed 財報 今日公布\n美股 明天 開盤 NVIDIA 法說"
FOMC_TW = [{"date": "2026-09-17", "kind": "fomc_tw", "priority": 3, "text": "FOMC 已公布：升 1 碼。屬已知數"}]


def _frame(run_date="2026-09-17", **kw):
    kw.setdefault("db_path", NO_DB)
    kw.setdefault("calendar_events", [])
    return build_editorial_frame(run_date, kw.pop("signal_pack", {}), [], kw.pop("event_context", ""), "", **kw)


class MajorEventTest(unittest.TestCase):
    def test_headline_keywords_alone_never_make_major_event(self):
        frame = _frame(event_context=HEADLINES, index_pct=-1.8)
        self.assertNotEqual(frame["frame"], "major_event_day")
        self.assertEqual(frame["frame"], "quiet_setup_day")

    def test_calendar_catalyst_needs_market_reaction(self):
        flat = _frame(calendar_events=FOMC_TW, index_pct=0.2)
        self.assertNotEqual(flat["frame"], "major_event_day")
        self.assertTrue(any("未反應" in e for e in flat["event_evidence"]))
        moved = _frame(calendar_events=FOMC_TW, index_pct=-1.5)
        self.assertEqual(moved["frame"], "major_event_day")
        self.assertIn("FOMC", "".join(moved["reasons"]))
        bigger = _frame(calendar_events=FOMC_TW, index_pct=-2.5)
        self.assertGreater(bigger["confidence"], moved["confidence"])  # not a constant 76

    def test_us_print_is_digested_next_tw_session(self):
        cpi = [{"date": "2026-09-11", "kind": "cpi", "priority": 2, "text": "美國 CPI 已公布。"}]
        self.assertFalse(score_major_event("2026-09-11", calendar_events=cpi, index_pct=1.5)["qualifies"])
        self.assertTrue(score_major_event("2026-09-14", calendar_events=cpi, index_pct=1.5)["qualifies"])

    def test_holiday_alone_never_qualifies(self):
        closed = [{"date": "2026-10-09", "kind": "twse_closed", "priority": 3, "text": "台股休市（國慶日）。"}]
        self.assertFalse(score_major_event("2026-10-12", calendar_events=closed, index_pct=2.0)["qualifies"])

    def test_radar_gate_rejects_social_and_stale_dated(self):
        rows = [
            {"event": "伯恩斯坦拆解 NVIDIA AI", "url": "https://www.facebook.com/x/posts/1",
             "impact_score": 80, "freshness": "new"},
            {"event": "2026/07/31 創意 法說會整理", "url": "https://news.example.com/a",
             "impact_score": 75, "freshness": "new"},
            {"event": "舊聞", "url": "https://news.example.com/b", "impact_score": 90, "freshness": "stale"},
            {"event": "AMD 投資台灣", "url": "https://news.cnyes.com/news/id/1", "impact_score": 70,
             "freshness": "new", "impacted_roles": ["先進封裝 / CoWoS"]},
        ]
        eligible, rejected = eligible_radar_events(rows, "2026-10-12")
        self.assertEqual([r["event"] for r in eligible], ["AMD 投資台灣"])
        self.assertEqual(len(rejected), 3)
        self.assertTrue(is_low_tier_source("https://m.facebook.com/abc"))
        self.assertFalse(is_low_tier_source("https://news.cnyes.com/news/id/1"))

    def test_radar_gate_rejects_dated_url_and_untagged_rows(self):
        rows = [
            # Months-old press release: the headline has no date, the URL does.
            {"event": "AMD 擴大台灣先進封裝投資", "impact_score": 100, "freshness": "new",
             "url": "https://www.amd.com/en/newsroom/press-releases/2026-5-20-amd-expands-taiwan.html"},
            {"event": "Evertiq 報導 AI 伺服器擴產", "impact_score": 95, "freshness": "new",
             "url": "https://evertiq.com/news/2026/06/02/ai-server"},
            # Pre-tagging structured_data (no freshness field): age unknown.
            {"event": "輝達 GPU 新平台", "impact_score": 90, "url": "https://news.cnyes.com/news/id/2"},
            {"event": "台廠 AI 伺服器 投資", "impact_score": 80, "freshness": "new",
             "url": "https://news.example.com/2026-10-11/ai"},
        ]
        eligible, rejected = eligible_radar_events(rows, "2026-10-12")
        self.assertEqual([r["event"] for r in eligible], ["台廠 AI 伺服器 投資"])
        self.assertTrue(any("網址日期" in note for note in rejected))
        self.assertTrue(any("unknown" in note for note in rejected))

    def test_resumed_day_cannot_use_previous_session_radar(self):
        """/1 resume copies yesterday's radar; with a 1.5% index move it must not become 重大事件日."""
        prev_radar = [
            {"event": "AMD 投資台灣先進封裝", "url": "https://news.cnyes.com/news/id/1", "impact_score": 90,
             "raw_score": 90, "freshness": "new", "prior_days": 0,
             "impacted_roles": ["先進封裝 / CoWoS", "基板 / 載板"]},
        ]
        fresh = _frame("2026-10-12", event_radar=prev_radar, index_pct=1.5)
        self.assertEqual(fresh["frame"], "major_event_day")  # same rows as today's news would qualify
        carried = carry_forward_radar(prev_radar, from_date="2026-10-08")
        self.assertEqual(carried[0]["freshness"], "repeat")
        self.assertLess(carried[0]["impact_score"], 60)
        self.assertEqual(prev_radar[0]["freshness"], "new")  # input not mutated
        resumed = _frame("2026-10-12", event_radar=carried, index_pct=1.5)
        self.assertNotEqual(resumed["frame"], "major_event_day")
        self.assertIsNone(_radar_pick({"supply_chain_event_radar": carried}, "2026-10-12"))
        self.assertEqual(carry_forward_radar(carried)[0]["freshness"], "stale")


class ScoredSelectionTest(unittest.TestCase):
    def test_strongest_candidate_wins_and_scores_are_recorded(self):
        pack = {"strongest_anomaly": {"type": "法人賣超但股價強", "ticker": "2327", "text": "x", "score": 84}}
        frame = _frame(calendar_events=FOMC_TW, index_pct=-1.2, signal_pack=pack)
        scores = frame["frame_scores"]
        self.assertIn("anomaly_day", scores)
        self.assertIn("major_event_day", scores)
        self.assertEqual(frame["frame"], max(scores, key=lambda k: (scores[k], k == "anomaly_day")))
        weak = _frame(calendar_events=FOMC_TW, index_pct=-2.8,
                      signal_pack={"strongest_anomaly": dict(pack["strongest_anomaly"], score=56)})
        self.assertEqual(weak["frame"], "major_event_day")

    def _flow_db(self, tmp, days):
        db = os.path.join(tmp, "analytics.db")
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE institutional_flows (flow_date TEXT, ticker TEXT, name TEXT, side TEXT,"
            " foreign_net INTEGER, est_amount REAL)"
        )
        for day in days:
            conn.execute("INSERT INTO institutional_flows VALUES (?, '3037', '欣興', 'buy', 1, 10.0)", (day,))
        conn.commit()
        conn.close()
        return db

    def test_continuation_needs_repeat_top_buy_today(self):
        # /1 state: only prior sessions are in the DB (today's flows are recorded after the frame).
        with tempfile.TemporaryDirectory() as tmp:
            db = self._flow_db(tmp, ("2026-10-06", "2026-10-07"))
            row = {"ticker": "3037", "name": "欣興", "watch_score": 48, "pct_change": 1.2}
            not_today = _frame("2026-10-08", db_path=db, signal_pack={"top_candidates": [row]})
            self.assertNotEqual(not_today["frame"], "continuation_day")
            row["foreign_buy_rank"] = 2
            today = _frame("2026-10-08", db_path=db, signal_pack={"top_candidates": [row]})
            self.assertEqual(today["frame"], "continuation_day")  # 2 prior + today = 3 of 5
            self.assertIn("含今日", "".join(today["reasons"]))
            row["pct_change"] = -2.0
            falling = _frame("2026-10-08", db_path=db, signal_pack={"top_candidates": [row]})
            self.assertNotEqual(falling["frame"], "continuation_day")

    def test_continuation_is_point_in_time(self):
        """A rerun / replay that already holds today's rows must pick the same frame as /1."""
        row = {"ticker": "3037", "name": "欣興", "watch_score": 48, "pct_change": 1.2, "foreign_buy_rank": 2}
        with tempfile.TemporaryDirectory() as tmp:
            live = self._flow_db(tmp, ("2026-10-06", "2026-10-07"))
            live_frame = _frame("2026-10-08", db_path=live, signal_pack={"top_candidates": [dict(row)]})
        with tempfile.TemporaryDirectory() as tmp:
            rerun = self._flow_db(tmp, ("2026-10-06", "2026-10-07", "2026-10-08"))
            rerun_frame = _frame("2026-10-08", db_path=rerun, signal_pack={"top_candidates": [dict(row)]})
        self.assertEqual(live_frame["frame"], "continuation_day")
        self.assertEqual(live_frame["frame"], rerun_frame["frame"])
        self.assertEqual(live_frame["frame_scores"], rerun_frame["frame_scores"])
        self.assertEqual(live_frame["reasons"], rerun_frame["reasons"])

    def test_index_weights_never_anchor_continuation(self):
        """2330 / 2317 / 2454 and ETFs sit on the foreign lists structurally."""
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "analytics.db")
            conn = sqlite3.connect(db)
            conn.execute(
                "CREATE TABLE institutional_flows (flow_date TEXT, ticker TEXT, name TEXT, side TEXT,"
                " foreign_net INTEGER, est_amount REAL)"
            )
            for day in ("2026-10-06", "2026-10-07"):
                for sid in ("2330", "0050"):
                    conn.execute("INSERT INTO institutional_flows VALUES (?, ?, '', 'buy', 1, 50.0)", (day, sid))
            conn.commit()
            conn.close()
            rows = [
                {"ticker": "2330", "name": "台積電", "watch_score": 60, "pct_change": 1.0, "foreign_buy_rank": 1},
                {"ticker": "0050", "name": "元大台灣50", "watch_score": 60, "pct_change": 1.0, "foreign_buy_rank": 2},
            ]
            frame = _frame("2026-10-08", db_path=db, signal_pack={"top_candidates": rows})
        self.assertNotEqual(frame["frame"], "continuation_day")
        self.assertNotIn("continuation_day", frame["frame_scores"])

    def test_continuation_counts_twse_sessions_not_ticker_appearances(self):
        """Buy rows older than the last 4 prior sessions do not count, however few rows exist."""
        row = {"ticker": "3037", "name": "欣興", "watch_score": 48, "pct_change": 1.2, "foreign_buy_rank": 2}
        with tempfile.TemporaryDirectory() as tmp:
            # 10-08 prior sessions: 10-07, 10-06, 10-05, 10-02. 09-30 / 10-01 are outside the window.
            db = self._flow_db(tmp, ("2026-09-30", "2026-10-01", "2026-10-07"))
            frame = _frame("2026-10-08", db_path=db, signal_pack={"top_candidates": [row]})
        self.assertNotEqual(frame["frame"], "continuation_day")


if __name__ == "__main__":
    unittest.main()
