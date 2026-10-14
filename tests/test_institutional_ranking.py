#!/usr/bin/env python3
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.institutional_ranking import (  # noqa: E402
    apply_amount_estimates,
    find_trust_conflicts,
    parse_day_all_payload,
    price_lookup_candidates,
    rank_by_amount,
)


class InstitutionalRankingTest(unittest.TestCase):
    def test_high_price_beats_share_count(self):
        stocks = [
            {"id": "3481", "name": "群創", "foreign_net": 80_000_000, "trust_net": 0},
            {"id": "2454", "name": "聯發科", "foreign_net": 10_000_000, "trust_net": 0},
        ]
        priced = apply_amount_estimates(
            stocks,
            {"3481": 20.0, "2454": 1600.0},
        )
        top_buy, _ = rank_by_amount(priced, top_n=2)
        self.assertEqual(top_buy[0]["id"], "2454")
        self.assertAlmostEqual(top_buy[0]["est_amount"], 160.0)
        self.assertAlmostEqual(top_buy[1]["est_amount"], 16.0)

    def test_price_candidates_include_high_price_not_just_share_leaders(self):
        stocks = [{"id": str(i), "name": str(i), "foreign_net": 1_000_000 - i} for i in range(90)]
        stocks.append({"id": "2454", "name": "聯發科", "foreign_net": 500})
        missing = price_lookup_candidates(stocks, price_map={}, share_cap=10)
        self.assertIn("2454", missing)
        self.assertIn("0", missing)
        self.assertNotIn("50", missing)

    def test_trust_conflict(self):
        stocks = apply_amount_estimates(
            [
                {
                    "id": "2344",
                    "name": "華邦電",
                    "foreign_net": 10_000_000,
                    "trust_net": -20_000_000,
                }
            ],
            {"2344": 180.0},
        )
        conflicts = find_trust_conflicts(stocks, min_abs_yi=5.0)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["note"], "外資買、投信賣")

    def test_parse_day_all_payload(self):
        payload = {
            "stat": "OK",
            "fields": ["證券代號", "證券名稱", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"],
            "data": [
                ["2330", "台積電", "20,000,000", "47600000000", "2380", "2400", "2370", "2,380.00"],
                ["2454", "聯發科", "1,000,000", "1600000000", "1600", "1610", "1580", "1,600"],
                ["0000", "加權指數", "--", "--", "--", "--", "--", "--"],
            ],
        }
        rows = parse_day_all_payload(payload)
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id["2330"]["close_price"], 2380.0)
        self.assertEqual(by_id["2454"]["close_price"], 1600.0)
        self.assertNotIn("0000", by_id)


if __name__ == "__main__":
    unittest.main()
