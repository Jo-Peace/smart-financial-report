#!/usr/bin/env python3
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.similar_sessions import (  # noqa: E402
    card_from_structured,
    find_similar,
    format_for_prompt,
    parse_flow_item,
    parse_index_pct,
    score_pair,
)


STOCK_DB = {
    "2330": {"name": "台積電"},
    "2408": {"name": "南亞科"},
    "2344": {"name": "華邦電"},
    "3189": {"name": "景碩"},
    "3037": {"name": "欣興"},
    "6770": {"name": "力積電"},
}


def day(
    date,
    index,
    bought,
    sold,
    preds=None,
    next_date=None,
    next_results=None,
):
    card = card_from_structured(
        {
            "date": date,
            "index_action": index,
            "safe_havens_bought": bought,
            "heavyweights_dumped": sold,
            "prediction_targets": preds or [],
            "prev_day_picks_result": [],
        },
        stock_db=STOCK_DB,
    )
    card["next_date"] = next_date
    card["next_day_results"] = next_results or []
    return card


class ParseTests(unittest.TestCase):
    def test_index_pct_and_flow(self):
        self.assertEqual(parse_index_pct("上漲 820.2點（+1.78%）"), 1.78)
        self.assertEqual(parse_index_pct("下跌 46.3點（-0.10%）"), -0.10)
        row = parse_flow_item("南亞科 (-210.4億)", {"南亞科": "2408"})
        self.assertEqual(row["ticker"], "2408")
        self.assertEqual(row["amount"], -210.4)
        self.assertEqual(row["name"], "南亞科")


class ScoreTests(unittest.TestCase):
    def test_same_tape_ranks_above_next_day_reversal(self):
        query = day(
            "2026-09-01",
            "上漲 820.2點（+1.78%）",
            ["台積電 (+137.8億)", "景碩 (+64.5億)"],
            ["南亞科 (-210.4億)", "華邦電 (-102.1億)", "力積電 (-57.0億)"],
        )
        analog = day(
            "2026-07-15",
            "上漲 510點（+1.21%）",
            ["台積電 (+80.0億)", "景碩 (+20.0億)"],
            ["南亞科 (-90.0億)", "華邦電 (-40.0億)"],
            preds=[{"id": "3189", "name": "景碩", "direction": "多", "trigger": "載板補漲"}],
            next_date="2026-07-16",
            next_results=["3189 景碩 (-2.10%, 預測未成立)"],
        )
        reversal = day(
            "2026-08-31",
            "上漲 153.2點（+0.33%）",
            ["力積電 (+49.6億)", "南亞科 (+41.3億)", "華邦電 (+24.6億)"],
            ["台積電 (-110.2億)", "景碩 (-50.5億)"],
        )
        analog_score, analog_reasons = score_pair(query, analog)
        reversal_score, reversal_reasons = score_pair(query, reversal)
        self.assertGreater(analog_score, reversal_score)
        self.assertTrue(any("同賣" in r or "賣超冠軍" in r for r in analog_reasons))
        self.assertIn("較像隔日翻臉，不是同構盤", reversal_reasons)

    def test_find_similar_returns_analog_with_outcome(self):
        query = day(
            "2026-09-01",
            "上漲 820.2點（+1.78%）",
            ["台積電 (+137.8億)"],
            ["南亞科 (-210.4億)", "華邦電 (-102.1億)"],
        )
        analog = day(
            "2026-07-15",
            "上漲 510點（+1.21%）",
            ["台積電 (+80.0億)"],
            ["南亞科 (-90.0億)", "華邦電 (-40.0億)"],
            preds=[{"id": "2408", "name": "南亞科", "direction": "多", "trigger": "外資買超"}],
            next_date="2026-07-16",
            next_results=["2408 南亞科 (-4.60%, 預測未成立)"],
        )
        filler = day(
            "2026-06-01",
            "下跌 20點（-0.05%）",
            ["欣興 (+10.0億)"],
            ["力積電 (-8.0億)"],
        )
        matches = find_similar(query, [analog, filler], exclude_date="2026-09-01")
        self.assertEqual(matches[0]["date"], "2026-07-15")
        self.assertEqual(matches[0]["next_date"], "2026-07-16")
        self.assertTrue(matches[0]["next_day_results"])
        self.assertIn("2408", matches[0]["predictions"][0]["ticker"])

    def test_prompt_forbids_invention_when_empty(self):
        text = format_for_prompt([])
        self.assertIn("禁止編造", text)
        self.assertIn("本日無足夠相似歷史盤可對照", text)


class LiveCorpusSmoke(unittest.TestCase):
    def test_sept_first_does_not_treat_aug_31_as_best_analog(self):
        import json

        reports_dir = os.path.join(BASE_DIR, "reports")
        path = os.path.join(reports_dir, "daily", "2026-09-01", "structured_data.json")
        if not os.path.isfile(path):
            self.skipTest("no 2026-09-01 structured_data")
        from modules.similar_sessions import find_similar_for_structured

        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data["date"] = "2026-09-01"
        matches = find_similar_for_structured(data, reports_dir, exclude_date="2026-09-01")
        if matches:
            self.assertNotEqual(matches[0]["date"], "2026-08-31")


if __name__ == "__main__":
    unittest.main()
