#!/usr/bin/env python3
import os
import sys
import unittest
from datetime import date

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.market_calendar import (  # noqa: E402
    analyzer_calendar_block,
    calendar_one_liner,
    nearby_market_events,
    nth_weekday,
)
from modules.notebooklm_generator import build_compact_notebooklm_prompt  # noqa: E402
from modules.notebooklm_source_builder import build_notebooklm_source  # noqa: E402


class MarketCalendarTest(unittest.TestCase):
    def test_nth_weekday_september_2026(self):
        self.assertEqual(nth_weekday(2026, 9, 2, 3), date(2026, 9, 16))
        self.assertEqual(nth_weekday(2026, 9, 4, 3), date(2026, 9, 18))
        self.assertEqual(nth_weekday(2026, 9, 4, 1), date(2026, 9, 4))

    def test_sept15_is_settlement_eve_not_witching(self):
        rows = nearby_market_events("2026-09-15")
        kinds = {(row["date"], row["kind"]) for row in rows}
        self.assertIn(("2026-09-15", "tx_settlement_eve"), kinds)
        self.assertIn(("2026-09-16", "tx_settlement"), kinds)
        self.assertIn(("2026-09-16", "ex_div"), kinds)
        self.assertIn(("2026-09-16", "fomc_us"), kinds)
        self.assertIn(("2026-09-17", "fomc_tw"), kinds)
        self.assertIn(("2026-09-18", "quad_witching"), kinds)
        self.assertIn(("2026-09-11", "cpi"), kinds)
        eve = next(row for row in rows if row["kind"] == "tx_settlement_eve")
        self.assertEqual(eve["when"], "今天")
        self.assertIn("T-1", eve["text"])
        witch = next(row for row in rows if row["kind"] == "quad_witching")
        self.assertEqual(witch["when"], "尚未發生")
        self.assertIn("不是台指期結算日", witch["text"])
        block = analyzer_calendar_block("2026-09-15")
        self.assertIn("四巫日是美股季月第三個週五", block)
        self.assertIn("FOMC", block)
        self.assertIn("除息", block)

    def test_sept18_is_us_witching_not_tx_settlement(self):
        rows = nearby_market_events("2026-09-18")
        today = [row for row in rows if row["when"] == "今天"]
        kinds = {row["kind"] for row in today}
        self.assertIn("quad_witching", kinds)
        self.assertNotIn("tx_settlement", kinds)

    def test_first_friday_is_nfp_eve_for_twse(self):
        rows = nearby_market_events("2026-10-02")
        nfp = next(row for row in rows if row["kind"] == "nfp")
        self.assertEqual(nfp["date"], "2026-10-02")
        self.assertEqual(nfp["when"], "今天")
        self.assertIn("禁止把非農當已公布結果", nfp["text"])

    def test_prompt_and_source_get_calendar_line(self):
        sd = {"date": "2026-09-15", "index_action": "下跌 351點（-0.77%）"}
        prompt = build_compact_notebooklm_prompt(sd, date_str="2026-09-15")
        self.assertIn("日曆一句即可", prompt)
        self.assertIn("不要把未公布的 FOMC／非農講成結果", prompt)
        source = build_notebooklm_source(sd, date_str="2026-09-15")
        self.assertIn("日曆", source)
        self.assertTrue(calendar_one_liner("2026-09-15"))


if __name__ == "__main__":
    unittest.main()
