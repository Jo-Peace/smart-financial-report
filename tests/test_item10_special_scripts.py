#!/usr/bin/env python3
"""US / weekly / weekend special scripts: calendar-aware prompts, ADR ground truth, layout."""
import contextlib
import datetime
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

import main_us  # noqa: E402
import main_weekly  # noqa: E402
import weekend_special_main  # noqa: E402
from modules import special_context as sc  # noqa: E402
from modules.fetch_result import fetch_ok  # noqa: E402
from modules.notebooklm_generator import build_weekend_notebooklm_prompt  # noqa: E402
from scripts.tool_registry import find_tool  # noqa: E402

TODAY = sc.taipei_now().strftime("%Y-%m-%d")
TODAY_MD = sc.taipei_now().strftime("%m/%d")
TPE = sc.TAIPEI_TZ


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class SessionContextTest(unittest.TestCase):
    def test_trading_day(self):
        ctx = sc.session_context("2026-10-13")
        self.assertTrue(ctx["tw_open_today"])
        self.assertFalse(ctx["is_holiday_break"])
        self.assertEqual(ctx["next_session"], "2026-10-14")
        self.assertNotIn("春節", sc.weekly_title(ctx) + sc.us_observation_title(ctx))

    def test_mid_autumn_long_weekend(self):
        ctx = sc.session_context("2026-09-27")
        self.assertEqual(ctx["closure_label"], "中秋節、教師節")
        self.assertEqual(ctx["last_session"], "2026-09-24")
        self.assertEqual(ctx["next_session"], "2026-09-29")
        self.assertIn("下週二", sc.weekly_title(ctx))
        self.assertNotIn("春節", sc.format_session_context(ctx))

    def test_lunar_new_year_and_plain_weekend(self):
        self.assertEqual(sc.session_context("2026-02-15")["closure_label"], "春節")
        weekend = sc.session_context("2026-10-18")  # ordinary Sunday
        self.assertFalse(weekend["is_holiday_break"])
        self.assertIn("週末休市", sc.format_session_context(weekend))
        self.assertIn("本週美股回顧", sc.weekly_title(weekend))

    def test_before_close_trading_morning(self):
        ctx = sc.session_context("2026-10-13", now=datetime.datetime(2026, 10, 13, 8, 30, tzinfo=TPE))
        self.assertTrue(ctx["tw_before_close"])
        self.assertEqual(ctx["last_session"], "2026-10-12")
        self.assertIn("今天", sc.us_reaction_note(ctx))
        after = sc.session_context("2026-10-13", now=datetime.datetime(2026, 10, 13, 14, 0, tzinfo=TPE))
        self.assertTrue(after["tw_after_close"])
        self.assertEqual(after["last_session"], "2026-10-13")
        self.assertIn("下一個交易日 2026-10-14", sc.us_reaction_note(after))

    def test_uncovered_year_names_no_dates(self):
        for day in ("2027-01-04", "2027-01-05", "2027-01-09"):
            ctx = sc.session_context(day, now=datetime.datetime.fromisoformat(day + "T09:00:00+08:00"))
            self.assertFalse(ctx["calendar_known"], day)
            text = sc.format_session_context(ctx) + sc.us_reaction_note(ctx) + sc.weekly_title(ctx)
            self.assertNotIn("None", text, day)
            self.assertNotIn("休市期間", text, day)

    def test_topics_follow_the_year(self):
        self.assertIn("Federal Reserve interest rate 2027", sc.us_topics("2027-01-05"))
        self.assertIn("Federal Reserve interest rate 2027", main_weekly.weekly_topics("2027-01-05"))


class AdrPremiumTest(unittest.TestCase):
    def test_math(self):
        adr = sc.compute_adr_premium(300, 32.0, 1800)
        self.assertEqual(adr["implied_twd"], 1920.0)
        self.assertEqual(adr["premium_pct"], 6.67)
        self.assertLess(sc.compute_adr_premium(250, 30, 1600)["premium_pct"], 0)

    def test_missing_inputs_forbid_premium_talk(self):
        for args in [(None, 32, 1800), (300, 0, 1800), (300, 32, "n/a")]:
            self.assertIsNone(sc.compute_adr_premium(*args))
        self.assertIn("不得提及", sc.format_adr_block(None))

    def test_adr_block_labels_the_2330_price_by_session(self):
        adr = sc.compute_adr_premium(300, 32.0, 1800, tw_date="2026-10-13")
        morning = sc.session_context("2026-10-13", now=datetime.datetime(2026, 10, 13, 10, 0, tzinfo=TPE))
        block = sc.format_adr_block(adr, morning)
        self.assertIn("盤中報價", block)
        self.assertNotIn("收盤：", block)
        self.assertIn("ADR 相對台股溢價 +6.67%", block)

        undated = sc.compute_adr_premium(300, 32.0, 1800)
        early = sc.session_context("2026-10-13", now=datetime.datetime(2026, 10, 13, 7, 0, tzinfo=TPE))
        self.assertIn("2026-10-12 的收盤價", sc.format_adr_block(undated, early))

        evening = sc.session_context("2026-10-13", now=datetime.datetime(2026, 10, 13, 20, 0, tzinfo=TPE))
        self.assertIn("收盤：1800.0 元（交易日 2026-10-13）", sc.format_adr_block(adr, evening))

        unknown = sc.session_context("2027-01-04", now=datetime.datetime(2027, 1, 4, 20, 0, tzinfo=TPE))
        block = sc.format_adr_block(undated, unknown)
        self.assertNotIn("None", block)
        self.assertNotIn("最後交易日", block)
        # Uncovered weekday before 13:30: a bar dated today is intraday, an undated one may be.
        unknown_am = sc.session_context("2027-01-04", now=datetime.datetime(2027, 1, 4, 10, 0, tzinfo=TPE))
        dated_today = sc.compute_adr_premium(300, 32.0, 1800, tw_date="2027-01-04")
        self.assertIn("盤中報價", sc.format_adr_block(dated_today, unknown_am))
        self.assertIn("可能是盤中價或前一交易日收盤價", sc.format_adr_block(undated, unknown_am))

    def test_fetch_uses_2330_and_usd_twd(self):
        fetcher = mock.Mock()
        fetcher.get_stock_data.side_effect = lambda symbol: fetch_ok({"price": {"2330.TW": 1800, "TWD=X": 32.0}[symbol]})
        self.assertEqual(sc.fetch_adr_premium(fetcher, 300)["premium_pct"], 6.67)
        fetcher.get_stock_data.side_effect = lambda symbol: fetch_ok(
            {"price": {"2330.TW": 1800, "TWD=X": 32.0}[symbol]}, meta={"bar_date": "2026-10-13"}
        )
        self.assertEqual(sc.fetch_adr_premium(fetcher, 300)["tw_date"], "2026-10-13")
        fetcher.get_stock_data.side_effect = RuntimeError("down")
        self.assertIsNone(sc.fetch_adr_premium(fetcher, 300))


class UsReportWindowTest(unittest.TestCase):
    def test_window_excludes_today_old_and_keeps_both_layouts(self):
        with tempfile.TemporaryDirectory() as reports:
            _write(os.path.join(reports, "us_market_report_2026-09-25.md"), "legacy")
            _write(
                os.path.join(reports, "special_topics", "2026-09-26_us_market_observation", "special_report_2026-09-26_us_market_observation.md"),
                "new",
            )
            _write(
                os.path.join(reports, "special_topics", "2026-09-28_us_market_observation", "special_report_2026-09-28_us_market_observation.md"),
                "same day",
            )
            _write(os.path.join(reports, "archive", "02_US_Reports", "us_market_report_2026-02-12.md"), "old season")
            found = sc.find_recent_us_reports(reports, "2026-09-28", window_days=7)
            self.assertEqual([day for day, _ in found], ["2026-09-25", "2026-09-26"])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(main_us.find_previous_us_report("2026-09-28", reports).endswith("2026-09-26_us_market_observation.md"))
                context = main_weekly.load_existing_daily_reports("2026-09-28", reports)
            self.assertIn("2026-09-25 的每日觀察", context)
            self.assertNotIn("old season", context)
            self.assertNotIn("same day", context)


class TaiwanWeekFactsTest(unittest.TestCase):
    def test_aggregates_local_daily_lists(self):
        with tempfile.TemporaryDirectory() as reports:
            for day, buys, sells in [
                ("2026-10-07", ["欣興 (+23.7億)"], ["台積電 (-30.1億)"]),
                ("2026-10-08", ["欣興 (+23.9億)", "緯創 (+28.9億)"], ["台積電 (-318.0億)"]),
                ("2026-09-20", ["舊資料 (+99.0億)"], []),
            ]:
                _write(
                    os.path.join(reports, "daily", day, "structured_data.json"),
                    json.dumps({"date": day, "index_action": "下跌 1點", "safe_havens_bought": buys, "heavyweights_dumped": sells}, ensure_ascii=False),
                )
            facts = sc.collect_taiwan_week_facts(reports, "2026-10-11")
            self.assertEqual([r["date"] for r in facts["days"]], ["2026-10-07", "2026-10-08"])
            self.assertTrue(facts["has_institutional"])
            self.assertEqual(facts["leaders_buy"][0], {"name": "欣興", "amount_100m": 47.6, "days": 2})
            self.assertEqual(facts["leaders_sell"][0]["name"], "台積電")
            block = sc.format_taiwan_week_block(facts, {"week_close": 23000.5, "week_change": -120.3, "week_pct_change": -0.52})
            self.assertIn("^TWII", block)
            self.assertIn("不是完整週累計", block)
            self.assertNotIn("舊資料", block)

    def test_twii_week_uses_only_this_weeks_sessions(self):
        # Week of 2026-10-05: 10-09 closed, so tail(5) also holds last Friday 10-02.
        weekly = {
            "week_close": 23100.0,
            "week_change": 999.0,  # tail(5)-based figure that must not be used
            "daily_series": [
                {"date": "10/02", "close": 22800.0, "change": 50.0},
                {"date": "10/05", "close": 22900.0, "change": 100.0},
                {"date": "10/06", "close": 23000.0, "change": 100.0},
                {"date": "10/07", "close": 22950.0, "change": -50.0},
                {"date": "10/08", "close": 23100.0, "change": 150.0},
            ],
        }
        summary = sc.this_week_index_summary(weekly, "2026-10-11")
        self.assertEqual(summary["sessions"], 4)
        self.assertEqual(summary["first_date"], "2026-10-05")
        self.assertEqual(summary["week_change"], 300.0)  # vs the 10-02 close 22800
        self.assertEqual(summary["week_pct_change"], 1.32)
        block = sc.format_taiwan_week_block({}, summary)
        self.assertIn("本週 4 個交易日", block)
        self.assertNotIn("999", block)
        # A fully closed week has no ^TWII week line at all.
        self.assertIsNone(sc.this_week_index_summary({"daily_series": weekly["daily_series"][:1]}, "2026-10-11"))
        # New Year wrap: 12/31 belongs to the previous year.
        wrap = sc.this_week_index_summary(
            {"daily_series": [{"date": "12/30", "close": 10.0, "change": 1.0}, {"date": "12/31", "close": 11.0, "change": 1.0}]},
            "2027-01-02",
        )
        self.assertEqual(wrap["first_date"], "2026-12-30")

    def test_no_local_data_forbids_taiwan_numbers(self):
        with tempfile.TemporaryDirectory() as reports:
            block = sc.format_taiwan_week_block(sc.collect_taiwan_week_facts(reports, "2026-10-11"))
        self.assertIn("不得提及台股指數點位", block)


class PromptWordingTest(unittest.TestCase):
    def test_weekly_prompt_is_calendar_driven_and_forbids_predictions(self):
        ctx = sc.session_context("2026-10-18")
        prompt = main_weekly.generate_weekly_prompt("2026-10-18", "DATA", "NEWS", "", session_ctx=ctx)
        for banned in ["Lunar New Year", "春節", "(Sunday)", "封關一週", "2026\""]:
            self.assertNotIn(banned, prompt)
        self.assertIn(sc.NO_PREDICTION_RULE, prompt)
        self.assertIn("不得提及 ADR 溢價", prompt)

    def test_us_prompt_trading_day(self):
        ctx = sc.session_context("2026-10-13")
        prompt = main_us.generate_us_prompt("2026-10-13", "DATA", "NEWS", session_ctx=ctx)
        self.assertNotIn("春節", prompt)
        self.assertIn("台股今日正常交易", prompt)
        self.assertIn("美股盤後觀察日記", prompt)

    def test_us_prompt_trading_morning_reacts_today(self):
        ctx = sc.session_context("2026-10-13", now=datetime.datetime(2026, 10, 13, 8, 0, tzinfo=TPE))
        adr = sc.format_adr_block(sc.compute_adr_premium(300, 32.0, 1800), ctx)
        prompt = main_us.generate_us_prompt("2026-10-13", "DATA", "NEWS", session_ctx=ctx, adr_block=adr)
        self.assertIn("反映在今天（2026-10-13）的台股盤中", prompt)
        self.assertNotIn("美股盤後變化會在下一個交易日反映", prompt)
        self.assertNotIn("最新收盤：1800.0 元，最後交易日 2026-10-13", prompt)
        self.assertIn("2026-10-12 的收盤價", prompt)  # before 13:30 the last completed session is yesterday
        self.assertNotIn("None", prompt)
        self.assertNotIn("休市期間", prompt)

    def test_us_prompt_uncovered_calendar_never_says_closed_or_none(self):
        for day in ("2027-01-04", "2027-01-05"):
            ctx = sc.session_context(day, now=datetime.datetime.fromisoformat(day + "T20:00:00+08:00"))
            adr = sc.format_adr_block(sc.compute_adr_premium(300, 32.0, 1800), ctx)
            prompt = main_us.generate_us_prompt(day, "DATA", "NEWS", session_ctx=ctx, adr_block=adr)
            weekly = main_weekly.generate_weekly_prompt(day, "DATA", "NEWS", "", session_ctx=ctx, adr_block=adr)
            for text in (prompt, weekly):
                self.assertNotIn("None", text, day)
                self.assertNotIn("休市期間", text, day)
            self.assertIn("以證交所公告為準", prompt)

    def test_us_prompt_holiday_names_the_reacting_session(self):
        ctx = sc.session_context("2026-09-27", now=datetime.datetime(2026, 9, 27, 9, 0, tzinfo=TPE))
        prompt = main_us.generate_us_prompt("2026-09-27", "DATA", "NEWS", session_ctx=ctx)
        self.assertIn("因中秋節、教師節休市", prompt)
        self.assertIn("下一個交易日 2026-09-29", prompt)

    def test_weekend_prompt_is_neutral_and_grounded(self):
        prompt = sc.build_weekend_special_prompt(
            date_str="2026-10-18",
            session_ctx=sc.session_context("2026-10-18"),
            us_market_data={"^GSPC": {"week_close": 6000, "week_change": 10, "week_pct_change": 0.2, "week_high": 1, "week_low": 1}},
            taiwan_block="- 台股本週資料不足：本報告不得提及台股指數點位、法人買賣超或個股籌碼。",
            adr_block=sc.format_adr_block(None),
        )
        self.assertIn("台股本週資料不足", prompt)
        self.assertIn(sc.NO_PREDICTION_RULE, prompt)
        for loaded in ["Friday Bloodbath", "週五血洗", "大逃殺 (", "戰火陰霾", "禿鷹戰法"]:
            self.assertNotIn(loaded, prompt)
        self.assertEqual(len(weekend_special_main.US_TOPICS), 5)
        self.assertFalse(any("panic" in t or "plunge" in t or "war" in t for t in weekend_special_main.US_TOPICS))

    def test_weekend_director_card_chips_line_needs_local_data(self):
        without = build_weekend_notebooklm_prompt({"us_market_hook": "x"}, "2026-10-18")
        self.assertIn("不談法人買賣超", without)
        self.assertNotIn("法人 vs 成交量", without)
        with_data = build_weekend_notebooklm_prompt({"taiwan_week": {"has_institutional": True}}, "2026-10-18")
        self.assertIn("法人 vs 成交量", with_data)


class _FakeFetcher:
    def __init__(self, *args, **kwargs):
        pass

    def get_weekly_stock_data(self, symbol, trading_days=5):
        return fetch_ok(
            {
                "week_close": 300.0 if symbol == "TSM" else 6000.0,
                "week_change": 1.0,
                "week_pct_change": 0.5,
                "week_high": 1.0,
                "week_low": 1.0,
                "avg_volume": 1000,
                "ma5": 1.0,
                "ma20": 1.0,
                "rsi": 50.0,
                "daily_series": [{"date": TODAY_MD, "close": 6000.0, "change": 30.0, "pct_change": 0.5, "volume": 10}],
            }
        )

    def get_stock_data(self, symbol):
        prices = {"2330.TW": 1800.0, "TWD=X": 32.0}
        return fetch_ok({"price": prices.get(symbol, 300.0), "change": 1.0, "pct_change": 0.3, "volume": 10})

    def get_news(self, query, days=2):
        return fetch_ok([{"title": f"news {query}", "url": f"https://example.com/{abs(hash(query))}"}])

    def get_commodity_data(self):
        return fetch_ok({"GC=F": {"name": "黃金", "price": 2000.0, "pct_change": 0.5}})

    def get_macro_events(self):
        return fetch_ok([])


class _FakeAnalyzer:
    reply = "# 報告\n美股週五血洗，崩盤"

    def __init__(self, *args, **kwargs):
        pass

    def _call_gemini_with_retry(self, prompt, max_retries=3):
        _FakeAnalyzer.last_prompt = prompt
        return self.reply


class ScriptMainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reports = self.tmp.name
        self.out = io.StringIO()

    def tearDown(self):
        self.tmp.cleanup()

    def _common(self, module):
        return [
            mock.patch.object(module, "REPORTS_DIR", self.reports),
            mock.patch.object(module, "GEMINI_API_KEY", "k"),
            mock.patch.object(module, "TAVILY_API_KEY", "t"),
            mock.patch.object(module, "DataFetcher", _FakeFetcher),
            mock.patch.object(module, "MarketAnalyzer", _FakeAnalyzer),
        ]

    def test_weekly_calls_thumbnails_with_valid_signature_into_topic_folder(self):
        patches = self._common(main_weekly) + [
            mock.patch.object(main_weekly, "generate_ab_test_thumbnails", autospec=True, return_value={"titles": []}),
            mock.patch.object(main_weekly, "print_ab_test_summary"),
        ]
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(self.out):
            mocks = [stack.enter_context(p) for p in patches]
            rc = main_weekly.main()
        self.assertEqual(rc, 0)
        thumbs = mocks[5]
        kwargs = thumbs.call_args.kwargs
        self.assertEqual(kwargs["num_titles"], 2)
        self.assertIsNone(kwargs["openai_api_key"])
        self.assertEqual(kwargs["run_date"], TODAY)
        self.assertTrue(kwargs["reports_dir"].endswith(f"{TODAY}_us_weekly_recap"))
        report = os.path.join(kwargs["reports_dir"], f"special_report_{TODAY}_us_weekly_recap.md")
        with open(report, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("血洗", text)  # sanitized before saving
        self.assertIn("ADR 相對台股溢價 +6.67%", _FakeAnalyzer.last_prompt)
        self.assertFalse(os.path.exists(os.path.join(self.reports, f"weekly_us_report_{TODAY}.md")))

    def test_us_daily_writes_topic_folder_and_returns_codes(self):
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(self.out):
            for p in self._common(main_us):
                stack.enter_context(p)
            self.assertEqual(main_us.main(), 0)
        path = os.path.join(self.reports, "special_topics", f"{TODAY}_us_market_observation", f"special_report_{TODAY}_us_market_observation.md")
        self.assertTrue(os.path.exists(path))
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(self.out):
            stack.enter_context(mock.patch.object(main_us, "GEMINI_API_KEY", None))
            self.assertEqual(main_us.main(), 6)

    def test_weekend_with_prompt_mode_off_and_sanitized_structured_data(self):
        patches = self._common(weekend_special_main) + [
            mock.patch.object(
                weekend_special_main,
                "extract_weekend_structured_data",
                return_value={"us_market_hook": "美股週五全面血洗", "geopolitical_impact": "x"},
            ),
            mock.patch.dict(os.environ, {"NOTEBOOKLM_PROMPT_MODE": "off"}),
        ]
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(self.out):
            for p in patches:
                stack.enter_context(p)
            rc = weekend_special_main.main()
        self.assertEqual(rc, 0)
        folder = os.path.join(self.reports, "special_topics", f"{TODAY}_weekend_special")
        files = sorted(os.listdir(folder))
        ymd = TODAY.replace("-", "")
        self.assertEqual(
            files,
            [f"special_report_{TODAY}_weekend_special.md", f"special_structured_data_{ymd}_weekend_special.json"],
        )
        with open(os.path.join(folder, files[1]), encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotIn("血洗", data["us_market_hook"])
        self.assertIn("taiwan_week", data)
        # ^TWII came back but no local daily structured_data: chips are explicitly off-limits.
        self.assertIn("本週沒有本機法人資料", _FakeAnalyzer.last_prompt)

    def test_us_daily_morning_run_uses_the_taipei_clock(self):
        morning = datetime.datetime(2026, 10, 13, 8, 15, tzinfo=TPE)
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(self.out):
            for p in self._common(main_us):
                stack.enter_context(p)
            stack.enter_context(mock.patch.object(main_us, "taipei_now", return_value=morning))
            self.assertEqual(main_us.main(), 0)
        prompt = _FakeAnalyzer.last_prompt
        self.assertIn("反映在今天（2026-10-13）的台股盤中", prompt)
        self.assertIn("13:30 尚未收盤", prompt)
        self.assertNotIn("美股盤後變化會在下一個交易日反映", prompt)
        self.assertTrue(os.path.isdir(os.path.join(self.reports, "special_topics", "2026-10-13_us_market_observation")))

    def test_weekend_structured_failure_exits_5_without_director_card(self):
        patches = self._common(weekend_special_main) + [
            mock.patch.object(
                weekend_special_main,
                "extract_weekend_structured_data",
                return_value={"error": "Failed to extract weekend structured data.", "raw_error": "429 RESOURCE_EXHAUSTED"},
            ),
        ]
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(self.out):
            for p in patches:
                stack.enter_context(p)
            prompt_gen = stack.enter_context(mock.patch.object(weekend_special_main, "generate_weekend_special_prompt"))
            rc = weekend_special_main.main()
        self.assertEqual(rc, 5)
        prompt_gen.assert_not_called()
        folder = os.path.join(self.reports, "special_topics", f"{TODAY}_weekend_special")
        files = sorted(os.listdir(folder))
        self.assertIn(f"special_report_{TODAY}_weekend_special.md", files)
        self.assertFalse([f for f in files if f.startswith("special_structured_data_") or f.startswith("notebooklm_")])
        failed = [f for f in files if f.startswith("structured_data_failed_")]
        self.assertEqual(len(failed), 1)
        with open(os.path.join(folder, failed[0]), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["stage"], "extract_weekend_structured_data")

    def test_gemini_failure_writes_nothing(self):
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(self.out):
            for p in self._common(weekend_special_main):
                stack.enter_context(p)
            stack.enter_context(mock.patch.object(_FakeAnalyzer, "reply", "Error generating report: quota"))
            self.assertEqual(weekend_special_main.main(), 4)
        self.assertFalse(os.path.exists(os.path.join(self.reports, "special_topics")))


class CliRegistrationTest(unittest.TestCase):
    def test_special_tools_registered(self):
        for tool_id, script in [("weekend", "weekend_special_main.py"), ("us-daily", "main_us.py"), ("us-weekly", "main_weekly.py")]:
            spec = find_tool("special", tool_id)
            self.assertIsNotNone(spec, tool_id)
            self.assertEqual(spec.script, script)
            self.assertTrue(os.path.exists(os.path.join(BASE_DIR, script)))


if __name__ == "__main__":
    unittest.main()
