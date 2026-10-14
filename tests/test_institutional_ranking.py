#!/usr/bin/env python3
import os
import sqlite3
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.institutional_ranking import (  # noqa: E402
    apply_amount_estimates,
    find_trust_conflicts,
    latest_close_map,
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
        self.assertEqual(by_id["2330"]["trade_date"], "")  # payload does not say which session

    def test_day_all_rows_carry_their_own_session_date(self):
        # STOCK_DAY_ALL answers with the latest session whatever date was asked.
        payload = {
            "stat": "OK",
            "date": "20261013",
            "fields": ["日期", "證券代號", "證券名稱", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"],
            "data": [["1151013", "2603", "長榮", "30,000,000", "1", "246", "250", "240", "248.5"]],
        }
        row = parse_day_all_payload(payload)[0]
        self.assertEqual(row["close_price"], 248.5)
        self.assertEqual(row["trade_date"], "2026-10-13")
        payload["fields"] = payload["fields"][1:]
        payload["data"] = [payload["data"][0][1:]]
        self.assertEqual(parse_day_all_payload(payload)[0]["trade_date"], "2026-10-13")

    def test_price_dates_are_stamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "a.db")
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE daily_closes (close_date TEXT, ticker TEXT, close_price REAL)")
            conn.execute("CREATE TABLE market_snapshots (ticker TEXT, price REAL, snapshot_date TEXT)")
            conn.execute("INSERT INTO daily_closes VALUES ('2026-10-12', '2603', 246.0)")
            conn.execute("INSERT INTO market_snapshots VALUES ('2610', 31.0, '2026-10-08')")
            conn.commit()
            conn.close()
            dates = {}
            prices = latest_close_map(db, close_date="2026-10-12", dates_out=dates)
            self.assertEqual(latest_close_map(db, close_date="2026-10-12"), prices)  # old call shape
        # Item 6: only same-session closes; the 2026-10-08 snapshot is not reused.
        self.assertEqual(prices, {"2603": 246.0})
        self.assertEqual(dates, {"2603": "2026-10-12"})
        stocks = [{"id": "2603", "foreign_net": 1_000_000}, {"id": "2610", "foreign_net": 1_000_000}, {"id": "2606", "foreign_net": 1}]
        rows = apply_amount_estimates(stocks, {**prices, "2610": 31.0, "2606": 50.0}, {**dates, "2610": "2026-10-08"})
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id["2603"]["price_date"], "2026-10-12")
        self.assertEqual(by_id["2610"]["price_date"], "2026-10-08")
        self.assertNotIn("price_date", by_id["2606"])
        self.assertEqual(by_id["2603"]["est_amount"], 2.46)  # shares × close / 1e8
        # With the data date, another session's price is rejected, not stamped.
        rows = apply_amount_estimates(
            stocks, {**prices, "2610": 31.0}, {**dates, "2610": "2026-10-08"}, data_date="2026-10-12"
        )
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id["2610"]["amount_status"], "stale_price")
        self.assertIsNone(by_id["2610"]["est_amount"])


if __name__ == "__main__":
    unittest.main()
