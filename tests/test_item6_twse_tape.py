#!/usr/bin/env python3
"""Full-market tape parsing (MI_INDEX JSON / STOCK_DAY_ALL CSV) with verified dates."""
import os
import sys
import unittest
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules import twse_tape  # noqa: E402
from modules.twse_tape import (  # noqa: E402
    fetch_twse_tape,
    parse_fmtqik_payload,
    parse_mi_index_payload,
    parse_stock_day_all_text,
    roc_to_iso,
    signed_change,
)

# Trimmed from a recorded MI_INDEX?date=20261012&type=ALLBUT0999 response.
MI_INDEX_20261012 = {
    "stat": "OK",
    "date": "20261012",
    "params": {"date": "20261012", "type": "ALLBUT0999", "response": "json"},
    "tables": [
        {
            "title": "115年10月12日 價格指數(臺灣證券交易所)",
            "fields": ["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"],
            "data": [
                ["寶島股價指數", "54,682.15", "<p style ='color:green'>-</p>", "1.31", "--", ""],
                ["發行量加權股價指數", "49,301.96", "<p style ='color:green'>-</p>", "11.48", "-0.02", ""],
            ],
        },
        {
            "title": "115年10月12日 大盤統計資訊",
            "fields": ["成交統計", "成交金額(元)", "成交股數(股)", "成交筆數"],
            "data": [
                ["1.一般股票", "821,995,173,102", "4,579,738,373", "3,661,277"],
                ["總計(1~15)", "894,521,928,358", "10,868,236,326", "4,759,498"],
            ],
        },
        {
            "title": "115年10月12日 每日收盤行情(全部(不含權證、牛熊證、可展延牛熊證))",
            "fields": [
                "證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價",
                "收盤價", "漲跌(+/-)", "漲跌價差", "最後揭示買價", "最後揭示買量", "最後揭示賣價",
                "最後揭示賣量", "本益比",
            ],
            "data": [
                ["2330", "台積電", "23,555,768", "125,197", "59,600,136,896", "2,510.00", "2,550.00", "2,510.00",
                 "2,540.00", "<p style= color:green>-</p>", "10.00", "2,535.00", "1,391", "2,540.00", "17", "29.44"],
                ["2603", "長榮", "39,634,258", "30,672", "10,010,293,530", "242.00", "255.50", "241.50",
                 "255.50", "<p style= color:red>+</p>", "23.00", "255.50", "68,315", "--", "0", "10.13"],
                ["3443", "創意", "1,784,400", "12,973", "14,316,359,475", "8,290.00", "8,385.00", "7,820.00",
                 "8,100.00", "<p style= color:green>-</p>", "260.00", "8,080.00", "2", "8,100.00", "3", "207.64"],
                ["2323", "中環", "11,211,677", "3,293", "123,400,223", "11.50", "11.60", "10.70",
                 "10.90", "<p>X</p>", "0.00", "10.90", "127", "10.95", "34", "3.60"],
                ["1101", "台泥", "5,000,000", "2,000", "120,000,000", "24.00", "24.10", "23.90",
                 "24.00", "<p> </p>", "0.00", "24.00", "1", "24.05", "1", "20.00"],
                ["9999", "停牌股", "0", "0", "0", "--", "--", "--", "--", "<p> </p>", "0.00", "--", "0", "--", "0", "0.00"],
            ],
        },
    ],
}

# STOCK_DAY_ALL?response=json actually serves CSV and ignores ?date=.
STOCK_DAY_ALL_CSV_20261013 = (
    "﻿日期,證券代號,證券名稱,成交股數,成交金額,開盤價,最高價,最低價,收盤價,漲跌價差,成交筆數\n"
    '"1151013","2330","台積電","40700261","101680302000","2500.00","2510.00","2490.00","2490.00","-50.0000","329146"\n'
    '"1151013","2603","長榮","20000000","5000000000","250.00","252.00","248.00","250.00","-5.5000","12000"\n'
    '"1151013","2323","中環","100000","1000000","10.90","11.00","10.80","10.95","X0.00","50"\n'
    '"1151013","00625K","富邦上証+R","","","","","","","0.0000",""\n'
)


class SignedChangeTest(unittest.TestCase):
    def test_html_signs(self):
        self.assertEqual(signed_change("<p style= color:green>-</p>", "10.00"), (-10.0, False))
        self.assertEqual(signed_change("<p style= color:red>+</p>", "23.00"), (23.0, False))
        self.assertEqual(signed_change("<p> </p>", "0.00"), (0.0, False))

    def test_ex_rights_is_never_zero(self):
        change, ex_rights = signed_change("<p>X</p>", "0.00")
        self.assertIsNone(change)
        self.assertTrue(ex_rights)

    def test_roc_dates(self):
        self.assertEqual(roc_to_iso("1151012"), "2026-10-12")
        self.assertEqual(roc_to_iso("115/10/12"), "2026-10-12")
        self.assertIsNone(roc_to_iso("bad"))


class MiIndexParseTest(unittest.TestCase):
    def test_rows_carry_price_date_trade_value_and_taiex(self):
        tape = parse_mi_index_payload(MI_INDEX_20261012, expected_date="2026-10-12")
        self.assertIsNotNone(tape)
        self.assertEqual(tape["date"], "2026-10-12")
        rows = {r["id"]: r for r in tape["rows"]}
        self.assertEqual(rows["3443"]["close_price"], 8100.0)
        self.assertEqual(rows["2603"]["close_price"], 255.5)
        self.assertEqual(rows["2330"]["change"], -10.0)
        self.assertEqual(rows["2330"]["trade_value"], 59_600_136_896)
        self.assertTrue(all(r["price_date"] == "2026-10-12" for r in tape["rows"]))
        self.assertNotIn("9999", rows)  # no close → skipped
        self.assertIsNone(rows["2323"]["change"])
        self.assertIsNone(rows["2323"]["pct_change"])
        self.assertTrue(rows["2323"]["ex_rights"])
        self.assertEqual(rows["1101"]["pct_change"], 0.0)

        index = tape["index"]
        self.assertEqual(index["close"], 49301.96)
        self.assertEqual(index["change"], -11.48)
        self.assertEqual(index["pct_change"], -0.02)
        self.assertEqual(index["bar_date"], "2026-10-12")
        self.assertEqual(index["volume"], 10_868_236_326)

    def test_other_session_is_rejected(self):
        self.assertIsNone(parse_mi_index_payload(MI_INDEX_20261012, expected_date="2026-10-13"))
        self.assertIsNone(parse_mi_index_payload({"stat": "很抱歉，沒有符合條件的資料!"}, "2026-10-09"))


class StockDayAllCsvTest(unittest.TestCase):
    def test_csv_rows_filtered_by_date(self):
        tape = parse_stock_day_all_text(STOCK_DAY_ALL_CSV_20261013, expected_date="2026-10-13")
        rows = {r["id"]: r for r in tape["rows"]}
        self.assertEqual(rows["2330"]["close_price"], 2490.0)
        self.assertEqual(rows["2330"]["change"], -50.0)
        self.assertEqual(rows["2330"]["trade_value"], 101_680_302_000)
        self.assertIsNone(rows["2323"]["change"])
        self.assertTrue(rows["2323"]["ex_rights"])
        self.assertNotIn("00625K", rows)

    def test_endpoint_ignoring_date_param_is_rejected(self):
        # Asking for 2026-10-12 but TWSE served the 10-13 session.
        self.assertIsNone(parse_stock_day_all_text(STOCK_DAY_ALL_CSV_20261013, expected_date="2026-10-12"))


class FetchTapeTest(unittest.TestCase):
    def setUp(self):
        twse_tape.clear_cache()

    def tearDown(self):
        twse_tape.clear_cache()

    @staticmethod
    def _resp(json_payload=None, text=None):
        resp = mock.Mock()
        resp.tls_unverified = False
        resp.encoding = "utf-8"
        if json_payload is not None:
            resp.json.return_value = json_payload
        else:
            resp.json.side_effect = ValueError("Expecting value")
        resp.text = text or ""
        return resp

    def test_mi_index_first_and_cached(self):
        with mock.patch.object(twse_tape, "twse_get", return_value=self._resp(MI_INDEX_20261012)) as get:
            tape = fetch_twse_tape("2026-10-12")
            again = fetch_twse_tape("2026-10-12")
        self.assertEqual(tape["source"], "twse_mi_index")
        self.assertIs(tape, again)
        self.assertEqual(get.call_count, 1)

    def test_falls_back_to_csv_and_checks_date(self):
        responses = [self._resp({"stat": "沒有符合條件的資料"}), self._resp(text=STOCK_DAY_ALL_CSV_20261013)]
        with mock.patch.object(twse_tape, "twse_get", side_effect=responses):
            tape = fetch_twse_tape("2026-10-13", quiet=True)
        self.assertEqual(tape["source"], "twse_stock_day_all")
        self.assertEqual(tape["date"], "2026-10-13")

    def test_holiday_returns_none_and_is_negative_cached(self):
        responses = [self._resp({"stat": "沒有符合條件的資料"}), self._resp(text=STOCK_DAY_ALL_CSV_20261013)]
        with mock.patch.object(twse_tape, "twse_get", side_effect=responses) as get:
            self.assertIsNone(fetch_twse_tape("2026-10-09", quiet=True))
            self.assertIsNone(fetch_twse_tape("2026-10-09", quiet=True))
        self.assertEqual(get.call_count, 2)  # second call served by the miss cache


class FmtqikTest(unittest.TestCase):
    def test_parse(self):
        payload = {
            "stat": "OK",
            "fields": ["日期", "成交股數", "成交金額", "成交筆數", "發行量加權股價指數", "漲跌點數"],
            "data": [
                ["115/10/12", "10,868,236,326", "894,521,928,358", "4,759,498", "49,301.96", "-11.48"],
                ["115/10/08", "10,437,288,727", "924,509,097,873", "5,075,757", "49,313.44", "-492.93"],
            ],
        }
        rows = parse_fmtqik_payload(payload)
        self.assertEqual([r["date"] for r in rows], ["2026-10-08", "2026-10-12"])
        self.assertEqual(rows[-1]["close"], 49301.96)
        self.assertEqual(rows[-1]["change"], -11.48)


if __name__ == "__main__":
    unittest.main()
