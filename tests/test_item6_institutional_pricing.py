#!/usr/bin/env python3
"""法人買賣超億元 must use same-session closes; T86 parsed by column name."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.finmind_client import aggregate_institutional_rows  # noqa: E402
from modules.institutional_ranking import (  # noqa: E402
    T86SchemaError,
    apply_amount_estimates,
    find_trust_conflicts,
    latest_close_map,
    material_unpriced,
    parse_t86_rows,
    price_lookup_candidates,
    price_rows_same_session,
    pricing_coverage,
    rank_by_amount,
    reference_close_map,
)

T86_FIELDS = [
    "證券代號", "證券名稱", "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)",
    "外陸資買賣超股數(不含外資自營商)", "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數",
    "投信買進股數", "投信賣出股數", "投信買賣超股數", "自營商買賣超股數", "自營商買進股數(自行買賣)",
    "自營商賣出股數(自行買賣)", "自營商買賣超股數(自行買賣)", "自營商買進股數(避險)", "自營商賣出股數(避險)",
    "自營商買賣超股數(避險)", "三大法人買賣超股數",
]
T86_ROWS = [
    ["3443", "創意            ", "921,091", "496,098", "424,993", "0", "0", "0", "75,412", "193,000",
     "-117,588", "11,003", "18,518", "24,743", "-6,225", "64,122", "46,894", "17,228", "318,408"],
    ["2330", "台積電          ", "9,070,209", "18,493,035", "-9,422,826", "0", "0", "1,000", "723,078",
     "135,000", "588,078", "1,371,306", "42,010", "27,500", "14,510", "1,543,872", "187,076", "1,356,796",
     "-7,463,442"],
]


def _make_db(path, snapshots=(), closes=()):
    """snapshots: (snapshot_date, ticker, price[, bar_date]) — bar_date defaults to snapshot_date."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE market_snapshots (snapshot_date TEXT, ticker TEXT, price REAL, raw_json TEXT NOT NULL)")
    conn.execute("CREATE TABLE daily_closes (close_date TEXT, ticker TEXT, close_price REAL)")
    rows = []
    for snap in snapshots:
        day, ticker, price = snap[:3]
        bar_date = snap[3] if len(snap) > 3 else day
        rows.append((day, ticker, price, json.dumps({"price": price, "bar_date": bar_date})))
    conn.executemany("INSERT INTO market_snapshots VALUES (?, ?, ?, ?)", rows)
    conn.executemany("INSERT INTO daily_closes VALUES (?, ?, ?)", closes)
    conn.commit()
    conn.close()


class T86ParseTest(unittest.TestCase):
    def test_columns_by_name(self):
        rows = {r["id"]: r for r in parse_t86_rows({"fields": T86_FIELDS, "data": T86_ROWS})}
        self.assertEqual(rows["3443"]["name"], "創意")
        self.assertEqual(rows["3443"]["foreign_net"], 424_993)
        self.assertEqual(rows["3443"]["trust_net"], -117_588)
        self.assertEqual(rows["3443"]["dealer_net"], 11_003)
        self.assertEqual(rows["3443"]["total_net"], 318_408)
        self.assertEqual(rows["2330"]["foreign_dealer_net"], 1_000)
        self.assertEqual(rows["2330"]["foreign_total_net"], -9_421_826)

    def test_inserted_column_does_not_shift_numbers(self):
        fields = T86_FIELDS[:2] + ["新欄位"] + T86_FIELDS[2:]
        data = [row[:2] + ["999,999"] + row[2:] for row in T86_ROWS]
        rows = {r["id"]: r for r in parse_t86_rows({"fields": fields, "data": data})}
        self.assertEqual(rows["3443"]["foreign_net"], 424_993)
        self.assertEqual(rows["3443"]["trust_net"], -117_588)

    def test_missing_key_column_raises(self):
        fields = [f for f in T86_FIELDS if f != "投信買賣超股數"]
        with self.assertRaises(T86SchemaError):
            parse_t86_rows({"fields": fields, "data": []})


class SameSessionPricingTest(unittest.TestCase):
    """Regression for 2026-10-12: 創意 priced at the 09-18 snapshot (7150) instead of 8100."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "analytics.db")
        _make_db(
            self.db,
            snapshots=[
                ("2026-09-18", "3443", 7150.0),
                ("2026-08-20", "2603", 246.0),
                ("2026-10-08", "2330", 2550.0),
                ("2026-10-12", "2317", 249.0),
            ],
        )
        self.stocks = [
            {"id": "3443", "name": "創意", "foreign_net": 424_993, "trust_net": 0},
            {"id": "2603", "name": "長榮", "foreign_net": 12_029_425, "trust_net": 0},
            {"id": "2330", "name": "台積電", "foreign_net": -9_422_826, "trust_net": 0},
            {"id": "2317", "name": "鴻海", "foreign_net": 1_000_000, "trust_net": 0},
            {"id": "9999", "name": "冷門股", "foreign_net": 50_000_000, "trust_net": 0},
        ]

    def tearDown(self):
        self.tmp.cleanup()

    def test_latest_close_map_ignores_other_dates(self):
        self.assertEqual(latest_close_map(self.db, close_date="2026-10-12"), {"2317": 249.0})
        self.assertEqual(latest_close_map(self.db, close_date=None), {})

    def test_stale_snapshots_are_repriced_or_left_unpriced(self):
        same_day = {"3443": 8100.0, "2603": 255.5, "2330": 2540.0}
        calls = []

        def lookup(sid):
            calls.append(sid)
            return same_day.get(sid)

        rows, meta = price_rows_same_session(
            self.stocks, "2026-10-12", db_path=self.db, lookup_one=lookup, share_cap=10, extra_ids=()
        )
        by_id = {r["id"]: r for r in rows}
        self.assertAlmostEqual(by_id["3443"]["est_amount"], 34.42, places=2)
        self.assertAlmostEqual(by_id["2603"]["est_amount"], 30.74, places=2)
        self.assertAlmostEqual(by_id["2330"]["est_amount"], -239.34, places=2)
        self.assertEqual(by_id["2317"]["price_date"], "2026-10-12")
        self.assertNotIn("2317", calls)  # same-day local price reused
        self.assertEqual(by_id["9999"]["amount_status"], "unpriced")
        self.assertIsNone(by_id["9999"]["est_amount"])
        self.assertEqual(meta["unpriced"], 1)
        self.assertEqual(meta["coverage_top30"]["checked"], 5)
        self.assertEqual(meta["coverage_top30"]["priced"], 4)
        self.assertEqual(meta["material_unpriced"], [])  # 9999: no reference, not a high-price name

        top_buy, top_sell = rank_by_amount(rows, top_n=10)
        self.assertEqual([r["id"] for r in top_buy], ["3443", "2603", "2317"])
        self.assertEqual([r["id"] for r in top_sell], ["2330"])

    def test_snapshot_written_today_with_stale_bar_is_not_same_session(self):
        # analytics_store keys market_snapshots by run_date; a kept stale Yahoo
        # bar (bar_date 10-08) recorded on 10-12 must not price 10-12.
        db = os.path.join(self.tmp.name, "stale.db")
        _make_db(db, snapshots=[("2026-10-12", "2330", 2550.0, "2026-10-08"), ("2026-10-12", "2317", 249.0)])
        self.assertEqual(latest_close_map(db, close_date="2026-10-12"), {"2317": 249.0})

    def test_tape_rows_from_another_date_are_ignored(self):
        tape = [{"id": "3443", "close_price": 7150.0, "price_date": "2026-09-18"}]
        rows, meta = price_rows_same_session(self.stocks[:1], "2026-10-12", tape_rows=tape, extra_ids=())
        self.assertEqual(meta["tape_rows"], 0)
        self.assertEqual(rows[0]["amount_status"], "unpriced")

    def test_apply_rejects_stale_price_dates(self):
        rows = apply_amount_estimates(
            self.stocks[:1], {"3443": 7150.0}, price_dates={"3443": "2026-09-18"}, data_date="2026-10-12"
        )
        self.assertEqual(rows[0]["amount_status"], "stale_price")
        self.assertIsNone(rows[0]["est_amount"])

    def test_lookup_candidates_treat_stale_as_missing(self):
        missing = price_lookup_candidates(
            self.stocks,
            {"3443": 7150.0, "2317": 249.0},
            share_cap=10,
            extra_ids=(),
            price_dates={"3443": "2026-09-18", "2317": "2026-10-12"},
            data_date="2026-10-12",
        )
        self.assertIn("3443", missing)
        self.assertNotIn("2317", missing)

    def test_unpriced_rows_never_rank_or_conflict(self):
        rows = apply_amount_estimates(
            [{"id": "1", "name": "A", "foreign_net": 10_000_000, "trust_net": -20_000_000}], {}
        )
        self.assertEqual(rank_by_amount(rows), ([], []))
        self.assertEqual(find_trust_conflicts(rows), [])
        self.assertEqual(pricing_coverage(rows)["ratio"], 0.0)


class MaterialUnpricedTest(unittest.TestCase):
    """Reviewer repro for 2026-10-12: tape down, Yahoo missing 2330/3443 bars."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "analytics.db")
        # Previous-session closes only (reference, never used for est_amount).
        _make_db(self.db, closes=[("2026-10-08", "2330", 2550.0), ("2026-10-08", "3443", 8000.0)])
        # 30 share leaders priced on 10-12, then 台積電／創意 far down by |shares|.
        self.stocks = []
        for i in range(30):
            sign = 1 if i % 2 == 0 else -1
            self.stocks.append({"id": f"1{i:03d}", "name": f"S{i}", "foreign_net": sign * (60_000_000 - i * 1_000_000)})
        self.stocks.append({"id": "2330", "name": "台積電", "foreign_net": -9_422_826})
        self.stocks.append({"id": "3443", "name": "創意", "foreign_net": 424_993})
        self.same_day = {s["id"]: 20.0 for s in self.stocks[:30]}

    def tearDown(self):
        self.tmp.cleanup()

    def _price(self, refill=None):
        return price_rows_same_session(
            self.stocks,
            "2026-10-12",
            db_path=self.db,
            lookup_one=lambda sid: self.same_day.get(sid),
            refill_one=refill,
            top_n=10,
        )

    def test_share_coverage_alone_is_blind_to_unpriced_leaders(self):
        rows, meta = self._price()
        self.assertEqual(meta["coverage_top30"]["ratio"], 1.0)  # the old check passed
        material = {m["id"]: m for m in meta["material_unpriced"]}
        self.assertEqual(set(material), {"2330", "3443"})
        self.assertAlmostEqual(material["2330"]["reference_amount"], -240.28, places=2)
        top_buy, top_sell = rank_by_amount(rows, top_n=10)
        self.assertNotIn("2330", [r["id"] for r in top_sell])

    def test_refill_prices_leaders_for_exactly_that_session(self):
        calls = []

        def refill(sid):
            calls.append(sid)
            return {"2330": 2540.0, "3443": 8100.0}.get(sid)

        rows, meta = self._price(refill=refill)
        self.assertEqual(sorted(calls), ["2330", "3443"])
        self.assertEqual(meta["material_unpriced"], [])
        self.assertEqual(meta["refill_priced"], 2)
        top_buy, top_sell = rank_by_amount(rows, top_n=10)
        self.assertEqual(top_sell[0]["id"], "2330")
        self.assertAlmostEqual(top_sell[0]["est_amount"], -239.34, places=2)
        self.assertEqual(top_sell[0]["price_date"], "2026-10-12")

    def test_refill_errors_degrade_to_unpriced(self):
        def refill(_sid):
            raise RuntimeError("TWSE blocked")

        rows, meta = self._price(refill=refill)
        self.assertEqual({m["id"] for m in meta["material_unpriced"]}, {"2330", "3443"})

    def test_high_price_name_without_any_reference_is_material(self):
        rows = apply_amount_estimates([{"id": "2330", "name": "台積電", "foreign_net": -1_000}], {})
        out = material_unpriced(rows, [], [], top_n=10, must_price=("2330",), reference_prices={})
        self.assertEqual(out[0]["reason"], "high_price_no_reference")

    def test_small_reference_amount_is_not_material(self):
        rows = apply_amount_estimates(
            [{"id": "2881", "name": "富邦金", "foreign_net": 1_000}, {"id": "1", "name": "A", "foreign_net": 10_000_000}],
            {"1": 50.0},
        )
        top_buy, top_sell = rank_by_amount(rows, top_n=1)
        out = material_unpriced(rows, top_buy, top_sell, top_n=1, must_price=("2881",), reference_prices={"2881": 90.0})
        self.assertEqual(out, [])

    def test_reference_map_uses_latest_prior_close(self):
        db = os.path.join(self.tmp.name, "ref.db")
        _make_db(
            db,
            snapshots=[("2026-10-07", "2603", 240.0)],
            closes=[("2026-10-07", "2330", 2500.0), ("2026-10-08", "2330", 2550.0), ("2026-10-12", "2330", 2540.0)],
        )
        refs = reference_close_map(db, "2026-10-12")
        self.assertEqual(refs["2330"], 2550.0)  # strictly before the session
        self.assertEqual(refs["2603"], 240.0)
        self.assertEqual(reference_close_map(None, "2026-10-12"), {})


class FinMindAggregateTest(unittest.TestCase):
    def test_trust_and_dealers_are_parsed(self):
        rows = [
            {"stock_id": "2330", "name": "Foreign_Investor", "buy": 100, "sell": 40},
            {"stock_id": "2330", "name": "Foreign_Dealer_Self", "buy": 5, "sell": 0},
            {"stock_id": "2330", "name": "Investment_Trust", "buy": 0, "sell": 30},
            {"stock_id": "2330", "name": "Dealer_self", "buy": 10, "sell": 0},
            {"stock_id": "2330", "name": "Dealer_Hedging", "buy": 0, "sell": 4},
            {"stock_id": "6488", "name": "Investment_Trust", "buy": 9, "sell": 0},  # no foreign row
        ]
        out = aggregate_institutional_rows(rows)
        self.assertEqual(set(out), {"2330"})
        rec = out["2330"]
        self.assertEqual(rec["foreign_net"], 60)
        self.assertEqual(rec["foreign_dealer_net"], 5)
        self.assertEqual(rec["trust_net"], -30)
        self.assertEqual(rec["dealer_net"], 6)
        self.assertEqual(rec["total_net"], 41)



class InstitutionalFetchTest(unittest.TestCase):
    """get_institutional_data: response-date check, schema guard, no unpriced leaderboard."""

    def setUp(self):
        from unittest import mock

        from modules.data_fetcher import DataFetcher

        self.mock = mock
        self.fetcher = DataFetcher.__new__(DataFetcher)
        self.fetcher.search_log = []
        self.payload = {"stat": "OK", "date": "20261012", "fields": T86_FIELDS, "data": T86_ROWS}

    def _resp(self, payload):
        resp = self.mock.Mock()
        resp.text = "x"
        resp.json.return_value = payload
        resp.tls_unverified = False
        return resp

    def test_t86_for_another_date_is_rejected(self):
        import datetime

        wrong = dict(self.payload, date="20261008")
        with self.mock.patch("modules.twse_http.twse_get", return_value=self._resp(wrong)):
            data, day = self.fetcher._fetch_institutional_payload(datetime.datetime(2026, 10, 12))
        self.assertIsNone(data)
        self.assertIsNone(day)

    def test_priced_rows_rank_and_carry_scope(self):
        rows = apply_amount_estimates(
            parse_t86_rows(self.payload),
            {"3443": 8100.0, "2330": 2540.0},
            price_dates={"3443": "2026-10-12", "2330": "2026-10-12"},
            data_date="2026-10-12",
        )
        with self.mock.patch.object(self.fetcher, "_fetch_institutional_payload", return_value=(self.payload, "2026-10-12")), \
                self.mock.patch.object(self.fetcher, "_price_institutional_rows", return_value=(rows, {"priced": 2})):
            res = self.fetcher.get_institutional_data(top_n=5, target_date="2026-10-12")
        self.assertTrue(res["ok"])
        self.assertEqual(res["data"]["top_buy"][0]["id"], "3443")
        self.assertEqual(res["data"]["market_scope"], "TWSE_listed")

    def test_nothing_priced_is_an_error_not_a_zero_leaderboard(self):
        rows = apply_amount_estimates(parse_t86_rows(self.payload), {})
        with self.mock.patch.object(self.fetcher, "_fetch_institutional_payload", return_value=(self.payload, "2026-10-12")), \
                self.mock.patch.object(self.fetcher, "_price_institutional_rows", return_value=(rows, {"priced": 0})), \
                self.mock.patch("modules.finmind_client.finmind_fallback_enabled", return_value=False):
            res = self.fetcher.get_institutional_data(top_n=5, target_date="2026-10-12")
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "UNPRICED")
        self.assertEqual(res["data"]["top_buy"], [])

    def test_schema_change_is_an_error(self):
        broken = dict(self.payload, fields=[f for f in T86_FIELDS if f != "三大法人買賣超股數"])
        with self.mock.patch.object(self.fetcher, "_fetch_institutional_payload", return_value=(broken, "2026-10-12")):
            res = self.fetcher.get_institutional_data(top_n=5, target_date="2026-10-12")
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "DATA_FORMAT")


if __name__ == "__main__":
    unittest.main()
