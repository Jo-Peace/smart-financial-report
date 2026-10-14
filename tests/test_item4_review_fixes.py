#!/usr/bin/env python3
"""Item 4 review follow-ups: stop-loss judging, daily path vs legacy rows,
summary buckets, conditional triggers, calendar coverage and the remaining
「20D 尺子」 prompt consumers."""
import contextlib
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules import twse_calendar  # noqa: E402
from modules.analytics_store import (  # noqa: E402
    EVAL_METHOD,
    AnalyticsStore,
    is_conditional_trigger,
    nth_session_after,
)
from modules.horizon_reliability import DEFAULT_RULER_LINE, LEDGER_METHOD, wilson_interval  # noqa: E402
from modules.prediction_validator import classify_hit  # noqa: E402

# Affirmative claims only: the neutral lines themselves say 「勿把…當成敗尺子」.
CLAIM_PHRASES = ("這條尺子", "短線噪音比較大", "以 20D 可講的故事為準", "成敗以 20D 為主", "成敗尺子請放在", "以這條為主")


def _snap(store, day, ticker, price):
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        """
        INSERT INTO market_snapshots (snapshot_date, ticker, price, raw_json) VALUES (?, ?, ?, '{}')
        ON CONFLICT(snapshot_date, ticker) DO UPDATE SET price = excluded.price
        """,
        (str(day), ticker, price),
    )
    conn.commit()
    conn.close()


def _pred(store, day, ticker, direction="多", stop=None, trigger="外資買超第1名"):
    conn = sqlite3.connect(store.db_path)
    cur = conn.execute(
        "INSERT INTO predictions (prediction_date, ticker, direction, trigger, stop_loss_price, raw_json) "
        "VALUES (?, ?, ?, ?, ?, '{}')",
        (day, ticker, direction, trigger, stop),
    )
    conn.commit()
    pred_id = cur.lastrowid
    conn.close()
    return pred_id


def _rows(store, sql, params=()):
    conn = sqlite3.connect(store.db_path)
    conn.row_factory = sqlite3.Row
    out = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return out


class _StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AnalyticsStore(os.path.join(self.tmp.name, "analytics.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def _result(self, pred_id, horizon):
        rows = _rows(
            self.store,
            "SELECT * FROM prediction_results WHERE prediction_id = ? AND horizon_days = ?",
            (pred_id, horizon),
        )
        return rows[0] if rows else None


class StopLossJudgingTest(_StoreCase):
    def test_classify_hit_stopped_loss_is_a_miss_even_inside_the_band(self):
        inside = classify_hit("多", -3.5, -1.5, 20)
        self.assertEqual(inside["outcome"], "neutral")  # what the full-horizon band alone would say
        stopped = classify_hit("多", -3.5, -1.5, 2, stopped_out=True)
        self.assertEqual((stopped["outcome"], stopped["hit"], stopped["basis"]), ("miss", 0, "stop"))
        self.assertAlmostEqual(stopped["excess_pct"], -2.0)
        self.assertEqual(stopped["dead_band_pct"], 0.71)
        # Beating a falling index does not rescue a stopped-out loss.
        self.assertEqual(classify_hit("多", -3.5, -6.0, 2, stopped_out=True)["hit"], 0)
        self.assertEqual(classify_hit("空", 4.5, 0.0, 1, stopped_out=True)["hit"], 0)

    def test_stopped_20d_trade_is_judged_on_the_sessions_it_lasted(self):
        anchor = date(2026, 9, 1)
        pid = _pred(self.store, anchor.isoformat(), "2303", stop=97.0)
        s1, s2 = nth_session_after(anchor, 1), nth_session_after(anchor, 2)
        for day, px, idx in ((anchor, 100.0, 20000.0), (s1, 98.0, 19900.0), (s2, 96.5, 19700.0)):
            _snap(self.store, day, "2303", px)
            _snap(self.store, day, "^TWII", idx)
        self.store.recompute_prediction_results(nth_session_after(anchor, 20).isoformat())
        row = self._result(pid, 20)
        self.assertEqual(row["status"], "stopped")
        self.assertEqual(row["sessions_elapsed"], 2)
        self.assertAlmostEqual(row["excess_return_pct"], -2.0)
        self.assertEqual(row["dead_band_pct"], 0.71)
        self.assertEqual(row["hit"], 0)  # was neutral (hit=None) with the 20D band 2.24
        summary = {r["horizon_days"]: r for r in self.store.get_horizon_performance_summary()}
        self.assertEqual((summary[20]["samples"], summary[20]["hit_rate"], summary[20]["neutral"]), (1, 0.0, 0))

    def test_onair_validator_counts_a_stopped_loss_as_a_miss_like_the_ledger(self):
        from modules.prediction_validator import validate_prediction_targets

        # Prior close 100 → 96.5 (-3.5%) with TWII -3.2%: excess -0.3 sits inside the 1D band.
        market = {"^TWII": {"price": 19360, "pct_change": -3.2}, "2303.TW": {"price": 96.5, "pct_change": -3.5}}
        no_stop = validate_prediction_targets([{"id": "2303", "direction": "多"}], market)[0]
        self.assertEqual((no_stop["outcome"], no_stop["hit"]), ("neutral", None))
        stopped = validate_prediction_targets([{"id": "2303", "direction": "多", "stop_loss_price": 97}], market)[0]
        self.assertTrue(stopped["stop_loss_triggered"])
        self.assertEqual((stopped["outcome"], stopped["hit"], stopped["hit_basis"]), ("miss", False, "stop"))
        # A stop above the entry (wrong side) is ignored, as in the ledger.
        wrong_side = validate_prediction_targets([{"id": "2303", "direction": "多", "stop_loss_price": 101}], market)[0]
        self.assertEqual(wrong_side["outcome"], "neutral")
        short_market = {"^TWII": {"price": 20840, "pct_change": 4.2}, "6770.TW": {"price": 104.5, "pct_change": 4.5}}
        short = validate_prediction_targets([{"id": "6770", "direction": "偏空", "stop_loss_price": 104}], short_market)[0]
        self.assertTrue(short["stop_loss_triggered"])
        self.assertEqual((short["outcome"], short["hit"]), ("miss", False))


class DailyPathLegacyRowsTest(_StoreCase):
    def setUp(self):
        super().setUp()
        self.legacy = _pred(self.store, "2026-10-07", "2303")
        self.fresh = _pred(self.store, "2026-10-07", "2408")
        conn = sqlite3.connect(self.store.db_path)
        # Legacy calendar-day rows: a 1D scored on a later day and a 3D that is not due yet.
        conn.execute(
            "INSERT INTO prediction_results (prediction_id, horizon_days, evaluated_date, return_pct, hit) "
            "VALUES (?, 1, '2026-10-12', 5.0, 1)",
            (self.legacy,),
        )
        conn.execute(
            "INSERT INTO prediction_results (prediction_id, horizon_days, evaluated_date, return_pct, hit) "
            "VALUES (?, 3, '2026-10-12', 6.0, 1)",
            (self.legacy,),
        )
        conn.commit()
        conn.close()
        for ticker in ("2303", "2408"):
            _snap(self.store, "2026-10-07", ticker, 100.0)
        _snap(self.store, "2026-10-07", "^TWII", 20000.0)
        _snap(self.store, "2026-10-08", "^TWII", 20000.0)

    def test_daily_run_leaves_legacy_rows_and_grades_new_ones(self):
        market = {"2303.TW": {"price": 97.0}, "2408.TW": {"price": 103.0}}
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.store.record_daily_run("2026-10-08", {"index_action": ""}, market_data=market)
        legacy = _rows(
            self.store,
            "SELECT horizon_days, evaluated_date, return_pct, hit, method FROM prediction_results "
            "WHERE prediction_id = ? ORDER BY horizon_days",
            (self.legacy,),
        )
        self.assertEqual(
            legacy,
            [
                {"horizon_days": 1, "evaluated_date": "2026-10-12", "return_pct": 5.0, "hit": 1, "method": None},
                {"horizon_days": 3, "evaluated_date": "2026-10-12", "return_pct": 6.0, "hit": 1, "method": None},
            ],
        )
        fresh = self._result(self.fresh, 1)
        self.assertEqual((fresh["method"], fresh["hit"]), (EVAL_METHOD, 1))
        self.assertIn("2 筆舊制", out.getvalue())
        self.assertEqual(self.store.legacy_result_count(), 2)

        # A corrected close on a rerun still refreshes the sessions_v2 row.
        with contextlib.redirect_stdout(io.StringIO()):
            self.store.record_daily_run("2026-10-08", {"index_action": ""}, market_data={"2408.TW": {"price": 96.0}})
        self.assertEqual(self._result(self.fresh, 1)["hit"], 0)
        self.assertIsNone(self._result(self.legacy, 1)["method"])

    def test_explicit_regrade_converts_legacy_rows(self):
        _snap(self.store, "2026-10-08", "2303", 97.0)
        self.store.recompute_prediction_results("2026-10-08", regrade_legacy=True)
        row = self._result(self.legacy, 1)
        self.assertEqual((row["method"], row["evaluated_date"], row["hit"]), (EVAL_METHOD, "2026-10-08", 0))
        self.assertIsNone(self._result(self.legacy, 3))  # legacy 3D was not due under sessions
        self.assertEqual(self.store.legacy_result_count(), 0)

    def test_upsert_guard_never_overwrites_a_legacy_row(self):
        conn = sqlite3.connect(self.store.db_path)
        cur = conn.cursor()
        row = {
            "prediction_id": self.legacy,
            "horizon_days": 1,
            "evaluated_date": "2026-10-08",
            "hit": 0,
            "method": EVAL_METHOD,
        }
        self.assertEqual(AnalyticsStore._upsert_result(cur, row, overwrite_legacy=False), 0)
        conn.commit()
        conn.close()
        self.assertEqual(self._result(self.legacy, 1)["hit"], 1)


class SummaryBucketsTest(_StoreCase):
    def test_unjudged_non_directional_and_direction_adjusted_averages(self):
        short = _pred(self.store, "2026-10-07", "6770", direction="空")
        long_ = _pred(self.store, "2026-10-07", "2330")
        watch = _pred(self.store, "2026-10-07", "2603", direction="觀察")
        no_index = _pred(self.store, "2026-10-08", "3037")
        for ticker, base, exit_ in (("6770", 100.0, 96.0), ("2330", 100.0, 103.0), ("2603", 100.0, 110.0)):
            _snap(self.store, "2026-10-07", ticker, base)
            _snap(self.store, "2026-10-08", ticker, exit_)
        _snap(self.store, "2026-10-08", "3037", 100.0)
        _snap(self.store, "2026-10-12", "3037", 102.0)  # TWII 10-12 never stored
        _snap(self.store, "2026-10-07", "^TWII", 20000.0)
        _snap(self.store, "2026-10-08", "^TWII", 20000.0)
        self.store.recompute_prediction_results("2026-10-12")
        self.assertIsNone(self._result(no_index, 1)["excess_return_pct"])
        one = {r["horizon_days"]: r for r in self.store.get_horizon_performance_summary()}[1]
        self.assertEqual((one["samples"], one["hit_rate"]), (2, 1.0))
        self.assertEqual(one["unjudged"], 1)
        self.assertEqual(one["non_directional"], 1)
        self.assertEqual((one["due"], one["evaluated"], one["missing"]), (3, 2, 0))
        self.assertAlmostEqual(one["coverage"], 2 / 3)
        # Short -4% and long +3% are both +3.5 on average once direction-adjusted.
        self.assertAlmostEqual(one["avg_return"], 3.5)
        self.assertAlmostEqual(one["avg_excess"], 3.5)
        self.assertEqual((self._result(short, 1)["hit"], self._result(long_, 1)["hit"]), (1, 1))
        self.assertIsNone(self._result(watch, 1)["hit"])

    def test_conditional_entries_are_disclosed(self):
        from modules.horizon_reliability import format_horizon_summary_with_read, ledger_disclosure

        pid = _pred(self.store, "2026-10-07", "2327", trigger="開盤價需高於602元")
        _snap(self.store, "2026-10-07", "2327", 600.0)
        _snap(self.store, "2026-10-08", "2327", 630.0)
        _snap(self.store, "2026-10-07", "^TWII", 20000.0)
        _snap(self.store, "2026-10-08", "^TWII", 20000.0)
        self.store.recompute_prediction_results("2026-10-08")
        self.assertEqual(self._result(pid, 1)["entry_conditional"], 1)
        rows = self.store.get_horizon_performance_summary()
        self.assertEqual(rows[0]["conditional_entries"], 1)
        self.assertIn("條件式進場 1", ledger_disclosure(rows[0]))
        self.assertIn("條件式進場 1", "\n".join(format_horizon_summary_with_read(rows)))


class ConditionalTriggerPatternTest(unittest.TestCase):
    def test_need_words_only_match_entry_conditions(self):
        self.assertFalse(is_conditional_trigger("外資連買，籌碼需要時間消化"))
        self.assertFalse(is_conditional_trigger("AI 需求強勁、外資買超第一"))
        self.assertTrue(is_conditional_trigger("需站穩 600 元"))
        self.assertTrue(is_conditional_trigger("需量縮回測不破 98 元"))
        self.assertTrue(is_conditional_trigger("開盤價需高於602元"))


class CalendarCoverageTest(unittest.TestCase):
    def test_windows_past_the_listed_year_warn(self):
        end = twse_calendar.covered_through()
        self.assertIsNotNone(end)
        self.assertEqual(twse_calendar.coverage_warning(end), "")
        warning = twse_calendar.coverage_warning(date(end.year + 1, 1, 2))
        self.assertIn(str(end.year + 1), warning)
        with mock.patch.object(twse_calendar, "_load_closed", return_value={}):
            self.assertIn("沒有休市日", twse_calendar.coverage_warning(end))

    def test_one_coverage_rule_behind_every_helper(self):
        """calendar_covers is the rule; is_calendar_covered / coverage_warning follow it (gap years too)."""
        closed = {"2025-01-01": {"date": "2025-01-01", "name": "元旦"}, "2027-01-01": {"date": "2027-01-01", "name": "元旦"}}
        with mock.patch.object(twse_calendar, "_load_closed", return_value=closed):
            gap = date(2026, 6, 1)
            self.assertFalse(twse_calendar.calendar_covers(gap))
            self.assertEqual(twse_calendar.is_calendar_covered(gap), twse_calendar.calendar_covers(gap))
            self.assertIn("2026", twse_calendar.coverage_warning(gap))
            self.assertTrue(twse_calendar.calendar_covers(date(2027, 3, 1)))
            self.assertEqual(twse_calendar.coverage_warning(date(2027, 3, 1)), "")
            self.assertEqual(twse_calendar.covered_through(), date(2027, 12, 31))
            self.assertEqual(twse_calendar.calendar_coverage(), (date(2025, 1, 1), date(2027, 12, 31)))

    def test_atomic_io_and_taipei_clock_aliases_share_one_implementation(self):
        import json as _json

        from modules import atomic_io, special_context, tz

        with tempfile.TemporaryDirectory() as tmp:
            a, b = os.path.join(tmp, "a.json"), os.path.join(tmp, "b.json")
            atomic_io.atomic_write_json(a, {"k": "台積電"})
            atomic_io.write_json_atomic(b, {"k": "台積電"}, trailing_newline=True)
            with open(a, encoding="utf-8") as fa, open(b, encoding="utf-8") as fb:
                self.assertEqual(fa.read(), fb.read())
            with open(a, encoding="utf-8") as fa:
                self.assertEqual(_json.load(fa), {"k": "台積電"})
            with self.assertRaises(TypeError):
                atomic_io.write_json_atomic(a, {"bad": object()})
            with open(a, encoding="utf-8") as fa:
                self.assertIn("台積電", fa.read())  # a failed dump never touches the target
            atomic_io.write_text_atomic(b, "x")
            with open(b, encoding="utf-8") as fb:
                self.assertEqual(fb.read(), "x")
        self.assertIs(special_context.TAIPEI_TZ, tz.TPE)
        self.assertIs(special_context.taipei_now, tz.taipei_now)
        self.assertEqual(tz.taipei_now().utcoffset(), tz.now_tpe().utcoffset())

    def test_report_and_ledger_surface_the_warning(self):
        from scripts.maintenance.analytics_report import _calendar_warning

        end = twse_calendar.covered_through()
        self.assertEqual(_calendar_warning(date(end.year, 6, 1)), "")
        self.assertIn("休市日", _calendar_warning(date(end.year, 12, 20)))
        with tempfile.TemporaryDirectory() as tmp:
            store = AnalyticsStore(os.path.join(tmp, "analytics.db"))
            _pred(store, f"{end.year}-12-24", "2330")
            with contextlib.redirect_stdout(io.StringIO()) as out:
                store.recompute_prediction_results(f"{end.year}-12-28")
        self.assertIn("休市日", out.getvalue())


def _gated_structured(primary_leads):
    def row(days, hit, n):
        low, high = wilson_interval(hit, n)
        return {
            "horizon_days": days, "samples": n, "effective_samples": n, "hit_rate": hit,
            "hit_ci_low": low, "hit_ci_high": high, "baseline": 0.5, "avg_excess": 0.4,
            "method": LEDGER_METHOD,
        }

    twenty = row(20, 0.72, 160) if primary_leads else row(20, 0.56, 125)
    return {
        "date": "2026-10-12",
        "index_action": "上漲 10 點",
        "ai_data_picks": ["2603 長榮"],
        "horizon_reliability": {"one_liner": "20D 命中率 56% 優於短線窗", "primary_leads": True},
        "timeframe_history": {
            "horizon_performance": [row(1, 0.45, 200), row(3, 0.46, 200), row(5, 0.47, 200), twenty],
        },
    }


class PackagingPromptGateTest(unittest.TestCase):
    def _prompt(self, structured):
        from modules import youtube_content_generator as ycg

        captured = {}

        def fake_generate(api_key, prompt, **kwargs):
            captured["prompt"] = prompt
            return "###TITLE_A\nA\n###TITLE_B\nB\n###DESCRIPTION\nD\n###PINNED_COMMENT\nP\n###END"

        with mock.patch.object(ycg, "generate_text", side_effect=fake_generate), contextlib.redirect_stdout(
            io.StringIO()
        ):
            # Explicit brief: never read the generated reports/shared copy in a test.
            ycg.generate_youtube_package(
                "", structured, "今日報告", "2026-10-12", packaging_context={"compounding_brief": "（測試簡報）"}
            )
        return captured["prompt"]

    def test_failed_gate_has_no_ruler_or_noise_claim(self):
        prompt = self._prompt(_gated_structured(primary_leads=False))
        for phrase in CLAIM_PHRASES + ("20 個交易日這條",):
            self.assertNotIn(phrase, prompt)
        self.assertIn("樣本不足以判定", prompt)
        self.assertIn("未通過統計門檻", prompt)

    def test_legacy_rows_fall_back_to_the_neutral_line(self):
        structured = _gated_structured(primary_leads=True)
        for r in structured["timeframe_history"]["horizon_performance"]:
            r.pop("method")
        prompt = self._prompt(structured)
        self.assertIn(DEFAULT_RULER_LINE, prompt)
        self.assertNotIn("可參考 20D", prompt)

    def test_supported_gate_allows_20d_led_packaging(self):
        prompt = self._prompt(_gated_structured(primary_leads=True))
        self.assertIn("可參考 20D", prompt)
        self.assertNotIn("這條尺子", prompt)


class ReportPromptTimeScaleTest(unittest.TestCase):
    def test_daily_report_prompt_frames_time_scales_neutrally(self):
        from modules.analyzer import MarketAnalyzer

        with contextlib.redirect_stdout(io.StringIO()):
            analyzer = MarketAnalyzer("")
            with mock.patch.object(MarketAnalyzer, "_call_gemini_with_retry", side_effect=lambda p, *a, **k: p):
                prompt = analyzer.generate_report(
                    {"^TWII": {"price": 20000, "change": 100, "pct_change": 0.5, "volume": 1}}, []
                )
        self.assertIn("【時間尺度——寫作用語】", prompt)
        for phrase in CLAIM_PHRASES + ("「趨勢」尺子",):
            self.assertNotIn(phrase, prompt)


class LocalMemoryValidationLabelTest(unittest.TestCase):
    def test_neutral_rows_and_excess_pct_are_reported(self):
        from modules.local_knowledge_compounder import LocalKnowledgeCompounder

        with tempfile.TemporaryDirectory() as tmp:
            compounder = LocalKnowledgeCompounder(base_dir=tmp)
            entry = compounder._build_memory_entry(
                {
                    "validation_results": [
                        {"id": "2330", "name": "台積電", "direction": "多", "hit": None, "outcome": "neutral",
                         "pct_change": 1.2, "excess_pct": 0.2},
                        {"id": "6770", "name": "力積電", "direction": "空", "hit": True, "outcome": "hit",
                         "pct_change": -4.0, "excess_pct": -3.0},
                    ]
                },
                "2026-10-12",
            )
        self.assertIn("2330 台積電：與大盤同步（不計）", entry)
        self.assertNotIn("無法驗收", entry)
        self.assertIn("1/1 命中", entry)
        self.assertIn("平均超額報酬 +1.60%", entry)  # (+0.2 long, +3.0 short) / 2
        self.assertIn("超額 -3.00%", entry)


class LegacyLedgerWarningTest(unittest.TestCase):
    """Smoke test 13: legacy (method NULL) rows are reported, never migrated silently."""

    def test_warning_is_read_only_and_names_the_command(self):
        from modules.analytics_store import RECOMPUTE_COMMAND, ledger_legacy_status, legacy_ledger_warning

        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "analytics.db")
            self.assertEqual(ledger_legacy_status(db), {})  # no DB: nothing created
            self.assertFalse(os.path.exists(db))
            store = AnalyticsStore(db)
            conn = sqlite3.connect(db)
            conn.executemany(
                "INSERT INTO prediction_results (prediction_id, horizon_days, evaluated_date, hit, method)"
                " VALUES (?, ?, ?, ?, ?)",
                [(1, 1, "2026-05-02", 1, None), (1, 3, "2026-05-05", 0, None), (2, 1, "2026-10-13", 1, "sessions_v2")],
            )
            conn.commit()
            conn.close()
            before = os.path.getmtime(db)
            status = ledger_legacy_status(db)
            self.assertEqual(status, {"legacy": 2, "current": 1})
            self.assertEqual(os.path.getmtime(db), before)
            self.assertEqual(store.get_summary()["legacy_results"], 2)
            conn = sqlite3.connect(db)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM prediction_results WHERE method IS NULL").fetchone()[0], 2)
            conn.close()
        line = legacy_ledger_warning(2, 1)
        self.assertEqual(len(line.splitlines()), 1)
        self.assertIn("2 筆舊制", line)
        self.assertIn(RECOMPUTE_COMMAND, line)
        self.assertIn("--apply", line)
        self.assertIn("先備份", line)
        self.assertEqual(legacy_ledger_warning(0), "")

    def test_main_prints_the_warning_at_start_up(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"), encoding="utf-8") as f:
            source = f.read()
        head = source.split("def main():", 1)[1].split("cutoff = ", 1)[0]
        self.assertIn("ledger_legacy_status()", head)
        self.assertIn("legacy_ledger_warning", head)


if __name__ == "__main__":
    unittest.main()
