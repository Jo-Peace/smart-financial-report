#!/usr/bin/env python3
import os
import sys
import unittest
from datetime import date

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.session_calendar import (  # noqa: E402
    analyzer_calendar_block,
    nearby_market_events,
    next_session,
    prev_session,
    session_lexicon,
    speech_rules_block,
)
from modules.notebooklm_generator import build_compact_notebooklm_prompt  # noqa: E402


class SessionCalendarTest(unittest.TestCase):
    def test_friday_next_is_monday(self):
        friday = date(2026, 9, 11)
        lex = session_lexicon(friday)
        self.assertEqual(next_session(friday), date(2026, 9, 14))
        self.assertEqual(lex["next_label"], "下週一")
        self.assertEqual(lex["next_open"], "下週一開盤")
        self.assertEqual(lex["prev_label"], "昨天")
        self.assertIn("明天開盤", lex["forbid"])
        block = speech_rules_block(friday)
        self.assertIn("下週一開盤", block)
        self.assertIn("禁止口播", block)
        self.assertIn("明天開盤", block)

    def test_monday_prev_is_friday(self):
        monday = date(2026, 9, 14)
        lex = session_lexicon(monday)
        self.assertEqual(prev_session(monday), date(2026, 9, 11))
        self.assertEqual(lex["prev_label"], "上週五")
        self.assertEqual(lex["prev_named"], "上週五點名")
        self.assertEqual(lex["next_label"], "明天")
        self.assertIn("昨天開盤", lex["forbid"])
        block = speech_rules_block(monday)
        self.assertIn("上週五", block)
        self.assertIn("昨天開盤", block)

    def test_wednesday_calendar_words_ok(self):
        wed = date(2026, 9, 9)
        lex = session_lexicon(wed)
        self.assertEqual(lex["next_label"], "明天")
        self.assertEqual(lex["prev_label"], "昨天")
        self.assertEqual(lex["forbid"], [])
        self.assertIn("明天／明天開盤", speech_rules_block(wed))

    def test_sept15_calendar_is_settlement_eve_not_settlement_day(self):
        rows = nearby_market_events("2026-09-15")
        dates = [row["date"] for row in rows]
        self.assertIn("2026-09-11", dates)
        self.assertIn("2026-09-15", dates)
        self.assertIn("2026-09-16", dates)
        today = next(row for row in rows if row["date"] == "2026-09-15")
        tomorrow = next(row for row in rows if row["date"] == "2026-09-16")
        self.assertEqual(today["when"], "今天")
        self.assertIn("T-1", today["text"])
        self.assertEqual(tomorrow["when"], "尚未發生")
        self.assertIn("除息", tomorrow["text"])
        block = analyzer_calendar_block("2026-09-15")
        self.assertIn("日曆約束", block)
        self.assertIn("禁止預測已公布的 CPI", block)
        self.assertIn("FOMC", block)

    def test_content_gap_thursday_does_not_call_episode_yesterday(self):
        lex = session_lexicon("2026-09-17", last_output_date="2026-09-15")
        self.assertTrue(lex["prev_is_yesterday"])
        self.assertTrue(lex["episode_gap"])
        self.assertEqual(lex["prev_named"], "上集點名")
        self.assertEqual(lex["prev_section"], "上集預測節奏")
        self.assertIn("昨天點名", lex["forbid"])
        self.assertNotIn("昨天開盤", lex["forbid"])
        block = speech_rules_block("2026-09-17", last_output_date="2026-09-15")
        self.assertIn("上集", block)
        self.assertIn("昨天有開盤但沒有影片", block)
        self.assertIn("昨天點名", block)
        prompt = build_compact_notebooklm_prompt(
            {
                "date": "2026-09-17",
                "validation_policy": {"source_date": "2026-09-15"},
            },
            date_str="2026-09-17",
        )
        self.assertIn("上集點名", prompt)
        self.assertIn("禁止口播", prompt)

    def test_mid_autumn_teachers_day_skips_friday_and_monday(self):
        thursday = date(2026, 9, 24)
        self.assertEqual(next_session(thursday), date(2026, 9, 29))
        lex = session_lexicon(thursday)
        self.assertEqual(lex["next_label"], "下週二")
        self.assertEqual(lex["next_open"], "下週二開盤")
        self.assertIn("明天開盤", lex["forbid"])
        self.assertIn("下週一開盤", lex["forbid"])
        self.assertIn("中秋節", lex["holidays_ahead"])
        self.assertIn("教師節", lex["holidays_ahead"])
        block = speech_rules_block(thursday)
        self.assertIn("下週二開盤", block)
        self.assertIn("中秋節", block)
        self.assertIn("教師節", block)
        prompt = build_compact_notebooklm_prompt({"date": "2026-09-24"}, date_str="2026-09-24")
        self.assertIn("下週二", prompt)
        self.assertNotIn("下一交易日請說「下週一」", prompt)

        tuesday = date(2026, 9, 29)
        self.assertEqual(prev_session(tuesday), date(2026, 9, 24))
        lex2 = session_lexicon(tuesday)
        self.assertEqual(lex2["prev_label"], "上週四")
        self.assertEqual(lex2["next_label"], "明天")
        self.assertIn("昨天開盤", lex2["forbid"])

    def test_director_card_friday_bans_tomorrow_open(self):
        prompt = build_compact_notebooklm_prompt({"date": "2026-09-11"}, date_str="2026-09-11")
        self.assertIn("下週一開盤", prompt)
        self.assertIn("禁止口播", prompt)
        self.assertIn("明天開盤", prompt)


if __name__ == "__main__":
    unittest.main()
