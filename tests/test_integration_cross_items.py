#!/usr/bin/env python3
"""Behaviour that only exists once several audit items are merged together."""
import os
import sys
import unittest
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.report_fact_check import enforce_local_tables, fact_check_report  # noqa: E402

REPORT = """## 〔成交值排名〕今日資金實際流向
| 排名 | 代號 | 名稱 | 成交值（億） | 漲跌幅 | 今日角色 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | 2330 | 台積電 | 999 | +9.99% | 權值撐盤 |
| 2 | 0050 | 元大台灣50 | 88 | +1.00% | ETF 申購 |

---

## 〔法人籌碼〕外資買賣超（資料日期：2026-10-12）
"""

VALUE_RANKING = {
    "data_date": "2026-10-12",
    "stocks": [
        {"rank": 1, "id": "2330", "name": "台積電", "trade_value_yi": 475.04, "pct_change": -0.39},
        {"rank": 2, "id": "2603", "name": "長榮", "trade_value_yi": 120.5, "pct_change": None, "ex_rights": True},
    ],
    "etfs": [{"rank": 1, "id": "0050", "name": "元大台灣50", "trade_value_yi": 88.2}],
}

VOLUME = [
    {"rank": 1, "id": "2409", "name": "友達", "volume": 309571757, "pct_change": -2.87},
    {"rank": 2, "id": "2330", "name": "台積電", "volume": 18000000, "pct_change": -0.39},
]


class ValueTableRebuildTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"REPORT_FACT_CHECK": "annotate", "REPORT_LOCAL_TABLES": "1"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_value_table_is_rebuilt_from_the_value_ranking(self):
        text, notes = enforce_local_tables(REPORT, volume_data=VOLUME, value_ranking=VALUE_RANKING)
        self.assertIn("| 1 | 2330 | 台積電 | 475.0 | -0.39% | 權值撐盤 |", text)
        self.assertIn("| 2 | 2603 | 長榮 | 120.5 | 除權息不比價 | — |", text)
        self.assertNotIn("999", text)
        self.assertIn("ETF（另列，不當個股主角）：0050 元大台灣50 88.2億", text)
        self.assertNotIn("| 2 | 0050 |", text)  # ETFs never take a stock rank
        self.assertTrue(any("成交值表" in n for n in notes), notes)

    def test_share_count_fallback_is_labelled(self):
        text, _notes = enforce_local_tables(REPORT, volume_data=VOLUME, value_ranking={"stocks": [], "etfs": []})
        self.assertIn("| 排名 | 代號 | 名稱 | 成交量（股，依股數） | 漲跌幅 | 今日角色 |", text)
        self.assertIn("| 1 | 2409 | 友達 | 309,571,757 |", text)
        self.assertIn("依股數，不是資金排名", text)

    def test_persist_hook_passes_the_value_ranking(self):
        text, summary = fact_check_report(REPORT, institutional_data={}, volume_data=VOLUME, value_ranking=VALUE_RANKING)
        self.assertIn("475.0", text)
        self.assertTrue(summary.get("tables"))


class DayAllRowDateTest(unittest.TestCase):
    def test_json_rows_from_another_session_are_dropped(self):
        import json

        from modules.twse_tape import parse_stock_day_all_text

        payload = {
            "stat": "OK",
            "date": "20261012",
            "fields": ["日期", "證券代號", "證券名稱", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"],
            "data": [
                ["1151012", "2603", "長榮", "30,000,000", "7,400,000,000", "246", "250", "240", "248.5"],
                ["1151009", "2618", "長榮航", "10,000,000", "400,000,000", "40", "41", "39", "40.0"],
            ],
        }
        tape = parse_stock_day_all_text(json.dumps(payload), expected_date="2026-10-12")
        self.assertEqual([r["id"] for r in tape["rows"]], ["2603"])
        self.assertEqual(tape["rows"][0]["price_date"], "2026-10-12")
        self.assertEqual(tape["rows"][0]["trade_value"], 7_400_000_000)


class WebPromptExRightsTest(unittest.TestCase):
    def test_ex_rights_day_prints_no_none_move(self):
        from app.main_app import build_research_prompt

        prompt = build_research_prompt(
            ticker="2543",
            stock_name="皇昌",
            market="上市",
            profile={},
            listing={"date": "2026-10-12", "close": 30.0},
            yf_symbol="2543.TW",
            stock_data={
                "price": 30.0,
                "change": None,
                "pct_change": None,
                "ex_rights": True,
                "volume": 3_456_000,
                "avg_vol_5d": 2_000_000,
                "bar_date": "2026-10-12",
            },
            institutional=None,
            institutional_date=None,
            revenue=None,
            news=[],
            date_str="2026-10-12",
        )
        line = next(l for l in prompt.splitlines() if "2543.TW：價格" in l)
        self.assertIn("除權息不比價", line)
        self.assertNotIn("None", line)


class SessionGuardExitCodeTest(unittest.TestCase):
    def test_holiday_stop_has_its_own_exit_code(self):
        from modules import exit_codes

        with open(os.path.join(BASE_DIR, "main.py"), encoding="utf-8") as f:
            source = f.read()
        self.assertIn("return EXIT_NOT_SESSION", source)
        self.assertNotIn("未知", exit_codes.describe(exit_codes.EXIT_NOT_SESSION))
        self.assertNotIn(exit_codes.EXIT_NOT_SESSION, exit_codes.RETRYABLE)

    def test_banner_reports_item_4_and_6_flags(self):
        from modules.runtime_flags import collect_flag_notices

        self.assertEqual(collect_flag_notices({}), [])
        text = " ".join(m for _l, m in collect_flag_notices({"ALLOW_HOLIDAY_RUN": "1", "MARKET_EXTRAS": "0", "INFLIGHT_PRICE_FETCH_MAX": "0"}))
        self.assertIn("ALLOW_STALE_SESSION", text)
        self.assertIn("MARKET_EXTRAS=0", text)
        self.assertIn("INFLIGHT_PRICE_FETCH_MAX=0", text)


if __name__ == "__main__":
    unittest.main()
