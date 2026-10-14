#!/usr/bin/env python3
"""Stock payloads: bar_date, TWSE fallbacks (^TWII, 除權息 X), volume ratio basis."""
import datetime
import os
import sys
import unittest
from unittest import mock

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules import data_fetcher as df_mod  # noqa: E402
from modules.data_fetcher import DataFetcher  # noqa: E402


def _fetcher():
    fetcher = DataFetcher.__new__(DataFetcher)  # skip TavilyClient (no API key in tests)
    fetcher.tavily_search_count = 0
    fetcher.tavily_search_limit = 0
    fetcher.search_log = []
    return fetcher


def _hist(dates, closes, volumes=None):
    idx = pd.DatetimeIndex(pd.to_datetime(dates)).tz_localize("Asia/Taipei")
    volumes = volumes or [1000] * len(closes)
    return pd.DataFrame({"Close": closes, "Volume": volumes, "Open": closes, "High": closes, "Low": closes}, index=idx)


class ChangeParseTest(unittest.TestCase):
    def test_ex_rights_x_is_none_not_zero(self):
        self.assertEqual(DataFetcher._parse_twse_change("X0.00"), (None, True))
        self.assertEqual(DataFetcher._parse_twse_change("+60.00"), (60.0, False))
        self.assertEqual(DataFetcher._parse_twse_change("-1.5"), (-1.5, False))
        self.assertEqual(DataFetcher._parse_twse_change(" 0.00"), (0.0, False))
        self.assertEqual(DataFetcher._parse_twse_change(""), (None, False))

    def test_twse_fallback_keeps_ex_rights_close_without_a_move(self):
        fetcher = _fetcher()
        rows = [
            {"date": "2026-10-08", "close": 100.0, "volume": 10, "change": 1.0, "ex_rights": False},
            {"date": "2026-10-12", "close": 97.0, "volume": 10, "change": None, "ex_rights": True},
        ]
        with mock.patch.object(fetcher, "_fetch_twse_daily_closes", return_value=rows):
            payload = fetcher._get_stock_data_twse_fallback("2330.TW", expected_date="2026-10-12")
        # The session's close is known; only the comparable move (no reference price) is not.
        self.assertEqual(payload["bar_date"], "2026-10-12")
        self.assertEqual(payload["price"], 97.0)
        self.assertIsNone(payload["pct_change"])  # never the -3% ex-dividend gap
        self.assertIsNone(payload["change"])
        self.assertTrue(payload["ex_rights"])

    def test_mi_index20_x_row_is_not_read_as_a_rise(self):
        from modules.twse_tape import pct_text

        fetcher = _fetcher()
        resp = mock.Mock()
        resp.tls_unverified = False
        resp.json.return_value = {
            "stat": "OK",
            "date": "20261012",
            "data": [
                ["1", "2409", "友達", "120,000,000", "1", "20.00", "20.50", "19.90", "20.30", "<p style= color:red>+</p>", "0.30", "", ""],
                ["2", "2323", "中環", "90,000,000", "1", "11.50", "11.60", "10.70", "10.90", "<p>X</p>", "0.60", "", ""],
            ],
        }
        with mock.patch("modules.twse_http.twse_get", return_value=resp), \
                mock.patch("modules.twse_tape.fetch_twse_tape", return_value=None), \
                mock.patch.object(fetcher, "_session_dates_back", return_value=[datetime.datetime(2026, 10, 12)]):
            res = fetcher.get_top_volume_stocks(top_n=20)
        rows = {r["id"]: r for r in res["data"]}
        self.assertEqual(rows["2409"]["pct_change"], 1.5)
        self.assertIsNone(rows["2323"]["pct_change"])
        self.assertTrue(rows["2323"]["ex_rights"])
        self.assertEqual(pct_text(rows["2323"]), "除權息不比價")
        self.assertEqual(pct_text(rows["2409"]), "+1.50%")


class VolumeRatioTest(unittest.TestCase):
    def test_denominator_excludes_today(self):
        payload = _fetcher()._payload_from_close_series(
            "X", [10, 10, 10, 10, 10, 11], [100, 100, 100, 100, 100, 200], bar_date="2026-10-12"
        )
        self.assertEqual(payload["avg_vol_5d"], 100)
        self.assertEqual(payload["vol_ratio"], 2.0)  # old formula: 200/116 = 1.71
        self.assertEqual(payload["bar_date"], "2026-10-12")


class BarDateTest(unittest.TestCase):
    """get_stock_data never touches the network here: the MI_INDEX tape is patched per test."""

    def setUp(self):
        tape = mock.patch("modules.twse_tape.fetch_twse_tape", return_value=None)
        self.fetch_tape = tape.start()
        self.addCleanup(tape.stop)

    def test_yahoo_bar_dated_and_trimmed_to_session(self):
        fetcher = _fetcher()
        hist = _hist(["2026-10-07", "2026-10-08", "2026-10-12", "2026-10-13"], [100, 101, 102, 103])
        ticker = mock.Mock()
        ticker.history.return_value = hist
        with mock.patch.object(df_mod.yf, "Ticker", return_value=ticker):
            res = fetcher.get_stock_data("2330.TW", expected_date="2026-10-12")
        self.assertTrue(res["ok"])
        self.assertEqual(res["data"]["bar_date"], "2026-10-12")
        self.assertEqual(res["data"]["price"], 102)

    def test_stale_yahoo_bar_uses_twse_same_session(self):
        fetcher = _fetcher()
        hist = _hist(["2026-10-07", "2026-10-08"], [100, 101])
        ticker = mock.Mock()
        ticker.history.return_value = hist
        twse_payload = {"symbol": "2330.TW", "price": 2540.0, "pct_change": -0.39, "bar_date": "2026-10-12"}
        with mock.patch.object(df_mod.yf, "Ticker", return_value=ticker), \
                mock.patch.object(fetcher, "_get_stock_data_twse_fallback", return_value=twse_payload):
            res = fetcher.get_stock_data("2330.TW", expected_date="2026-10-12")
        self.assertEqual(res["source"], "twse")
        self.assertEqual(res["data"]["bar_date"], "2026-10-12")

    def test_stale_bar_kept_but_flagged_when_no_fallback(self):
        fetcher = _fetcher()
        hist = _hist(["2026-10-07", "2026-10-08"], [100, 101])
        ticker = mock.Mock()
        ticker.history.return_value = hist
        with mock.patch.object(df_mod.yf, "Ticker", return_value=ticker), \
                mock.patch.object(fetcher, "_get_stock_data_twse_fallback", return_value=None):
            res = fetcher.get_stock_data("2330.TW", expected_date="2026-10-12")
        self.assertEqual(res["data"]["bar_date"], "2026-10-08")
        self.assertTrue(res["meta"]["bar_date_mismatch"])

    def test_yfinance_exception_twse_fallback_keeps_bar_date(self):
        """yf.Ticker raising (e.g. YFRateLimitError on a 429) must not strip bar_date."""
        fetcher = _fetcher()
        twse_payload = {"symbol": "2330.TW", "price": 2540.0, "pct_change": -0.39, "bar_date": "2026-10-12"}
        with mock.patch.object(df_mod.yf, "Ticker", side_effect=RuntimeError("Too Many Requests")), \
                mock.patch.object(fetcher, "_get_stock_data_twse_fallback", return_value=dict(twse_payload)), \
                mock.patch("builtins.print"), mock.patch.object(df_mod.logger, "exception"):
            res = fetcher.get_stock_data("2330.TW", expected_date="2026-10-12")
        self.assertTrue(res["ok"])
        self.assertEqual(res["meta"]["fallback"], "twse_after_error")
        self.assertEqual(res["data"]["bar_date"], "2026-10-12")
        self.assertEqual(res["meta"]["bar_date"], "2026-10-12")
        self.assertNotIn("bar_date_mismatch", res["meta"])

    def test_yfinance_exception_stale_twse_row_keeps_its_own_date(self):
        """Today's STOCK_DAY row not yet published: the previous session stays dated, never relabelled."""
        fetcher = _fetcher()
        stale = {"symbol": "2317.TW", "price": 200.0, "pct_change": 1.0, "bar_date": "2026-10-08"}
        with mock.patch.object(df_mod.yf, "Ticker", side_effect=RuntimeError("Too Many Requests")), \
                mock.patch.object(fetcher, "_get_stock_data_twse_fallback", return_value=dict(stale)), \
                mock.patch("builtins.print"), mock.patch.object(df_mod.logger, "exception"):
            res = fetcher.get_stock_data("2317.TW", expected_date="2026-10-12")
        self.assertEqual(res["data"]["bar_date"], "2026-10-08")
        self.assertEqual(res["meta"]["bar_date"], "2026-10-08")
        self.assertTrue(res["meta"]["bar_date_mismatch"])
        from modules.data_quality_gate import drop_off_session_bars

        kept, dropped = drop_off_session_bars({"2317.TW": res["data"]}, datetime.date(2026, 10, 12))
        self.assertIsNone(kept["2317.TW"])  # never narrated / scored as today's move
        self.assertEqual(dropped, ["2317.TW@2026-10-08"])


# 2026-10-12 MI_INDEX rows (date-checked by twse_tape); 10-09 is the 國慶 holiday,
# so 10-08 is the previous session.
TAPE_1012 = {
    "date": "2026-10-12",
    "rows": [
        {"id": "2330", "name": "台積電", "close_price": 2540.0, "change": -10.0, "volume": 30_000_000, "ex_rights": False},
        {"id": "2881", "name": "富邦金", "close_price": 88.0, "change": None, "volume": 9_000_000, "ex_rights": True},
    ],
    "index": {"close": 49301.96, "change": -11.48, "volume": 7_000_000_000, "bar_date": "2026-10-12"},
}


class TapePatchTest(unittest.TestCase):
    """Yahoo one session behind → append the reported session from the cached tape."""

    def setUp(self):
        tape = mock.patch("modules.twse_tape.fetch_twse_tape", return_value=TAPE_1012)
        self.fetch_tape = tape.start()
        self.addCleanup(tape.stop)
        fallback = mock.patch.object(DataFetcher, "_get_stock_data_twse_fallback", return_value=None)
        self.fallback = fallback.start()
        self.addCleanup(fallback.stop)

    def _run(self, symbol, dates, closes, volumes=None):
        ticker = mock.Mock()
        ticker.history.return_value = _hist(dates, closes, volumes)
        with mock.patch.object(df_mod.yf, "Ticker", return_value=ticker):
            return _fetcher().get_stock_data(symbol, expected_date="2026-10-12")

    def test_previous_session_bar_gets_tape_close(self):
        res = self._run("2330.TW", ["2026-10-06", "2026-10-07", "2026-10-08"], [2500.0, 2530.0, 2550.0])
        self.assertTrue(res["ok"])
        self.assertEqual(res["source"], "yfinance+twse_tape")
        self.assertEqual(res["meta"]["fallback"], "twse_tape_patch")
        data = res["data"]
        self.assertEqual(data["bar_date"], "2026-10-12")
        self.assertEqual(data["price"], 2540.0)
        self.assertEqual(data["change"], -10.0)  # TWSE 漲跌價差, not Yahoo's (possibly adjusted) close
        self.assertEqual(data["pct_change"], -0.39)
        self.assertEqual(data["volume"], 30_000_000)
        self.fetch_tape.assert_called_with("2026-10-12", quiet=True)
        self.fallback.assert_not_called()

    def test_gap_of_more_than_one_session_is_not_patched(self):
        res = self._run("2330.TW", ["2026-10-06", "2026-10-07"], [2500.0, 2530.0])
        self.fallback.assert_called_once()
        self.assertEqual(res["source"], "yfinance")
        self.assertEqual(res["data"]["bar_date"], "2026-10-07")
        self.assertTrue(res["meta"]["bar_date_mismatch"])

    def test_missing_tape_row_or_otc_symbol_is_not_patched(self):
        fetcher = _fetcher()
        self.assertIsNone(fetcher._tape_bar("9999.TW", "2026-10-12"))
        self.assertIsNone(fetcher._tape_bar("6488.TWO", "2026-10-12"))
        self.fetch_tape.return_value = None
        self.assertIsNone(fetcher._tape_bar("2330.TW", "2026-10-12"))

    def test_ex_rights_day_keeps_dated_close_without_a_move(self):
        res = self._run("2881.TW", ["2026-10-07", "2026-10-08"], [92.0, 93.5])
        self.assertEqual(res["source"], "yfinance+twse_tape")
        data = res["data"]
        self.assertEqual(data["bar_date"], "2026-10-12")
        self.assertEqual(data["price"], 88.0)
        self.assertIsNone(data["pct_change"])  # 88 vs 93.5 is the dividend, not a -5.9% day
        self.assertIsNone(data["change"])
        self.assertTrue(data["ex_rights"])
        self.fallback.assert_not_called()

    def test_index_patch_drops_mixed_source_volume_ratio(self):
        res = self._run("^TWII", ["2026-10-07", "2026-10-08"], [49806.37, 49313.44], [5_000_000, 6_000_000])
        self.assertEqual(res["source"], "yfinance+twse_tape")
        data = res["data"]
        self.assertEqual(data["price"], 49301.96)
        self.assertEqual(data["change"], -11.48)
        self.assertEqual(data["pct_change"], -0.02)
        self.assertEqual(data["volume"], 7_000_000_000)
        self.assertIsNone(data["avg_vol_5d"])
        self.assertIsNone(data["vol_ratio"])
        self.assertEqual(data["vol_ratio_basis"], "unavailable_mixed_sources")


class ValueRankBasisTest(unittest.TestCase):
    """成交股數 table's 成交值名次 and the 成交值前20 table share one (ex-ETF) ordering."""

    TAPE = {
        "date": "2026-10-12",
        "source": "twse_mi_index",
        "rows": [
            {"id": "2330", "name": "台積電", "trade_value": 59_600_000_000, "close_price": 2540.0, "pct_change": -0.39},
            {"id": "0050", "name": "元大台灣50", "trade_value": 30_000_000_000, "close_price": 200.0, "pct_change": 0.1},
            {"id": "3443", "name": "創意", "trade_value": 14_300_000_000, "close_price": 8100.0, "pct_change": -3.11},
            {"id": "00632R", "name": "元大台灣50反1", "trade_value": 12_000_000_000, "close_price": 3.0, "pct_change": 0.3},
            {"id": "2409", "name": "友達", "trade_value": 2_400_000_000, "close_price": 20.3, "pct_change": 1.5},
            {"id": "9999", "name": "無成交", "trade_value": 0, "close_price": 10.0, "pct_change": 0.0},
        ],
        "index": None,
    }

    def test_volume_table_ranks_match_value_table(self):
        fetcher = _fetcher()
        volume_rows = [{"id": "00632R", "name": "元大台灣50反1"}, {"id": "2409", "name": "友達"}, {"id": "3443", "name": "創意"}]
        with mock.patch("modules.twse_tape.fetch_twse_tape", return_value=self.TAPE):
            fetcher._attach_trade_value(volume_rows, "2026-10-12")
            value = fetcher.get_top_value_stocks("2026-10-12", top_n=20, etf_n=5)["data"]
        value_rank = {r["id"]: r["rank"] for r in value["stocks"]}
        self.assertEqual(value_rank, {"2330": 1, "3443": 2, "2409": 3})
        self.assertEqual([r["id"] for r in value["etfs"]], ["0050", "00632R"])
        by_id = {r["id"]: r for r in volume_rows}
        self.assertEqual(by_id["2409"]["value_rank"], value_rank["2409"])  # was 5 with ETFs counted
        self.assertEqual(by_id["3443"]["value_rank"], value_rank["3443"])
        self.assertIsNone(by_id["00632R"]["value_rank"])
        self.assertEqual(by_id["00632R"]["value_rank_etf"], 2)
        self.assertEqual(by_id["2409"]["trade_value_yi"], 24.0)
        self.assertNotIn("rank", self.TAPE["rows"][0])  # cached tape rows are not mutated


class PromptFormattingTest(unittest.TestCase):
    """generate_report renders ex-rights rows instead of crashing or printing None%."""

    @staticmethod
    def _prompt(**kw):
        from modules.analyzer import MarketAnalyzer

        analyzer = MarketAnalyzer.__new__(MarketAnalyzer)  # no Gemini client / stock_db
        analyzer.model_name = "test"
        analyzer.stock_db = {}
        with mock.patch.object(MarketAnalyzer, "_call_gemini_with_retry", side_effect=lambda prompt, **_: prompt):
            return analyzer.generate_report(kw.pop("market_data"), [], report_date="2026-10-12", **kw)

    def test_ex_rights_rows_say_no_comparison(self):
        prompt = self._prompt(
            market_data={
                "2330.TW": {"price": 2540.0, "change": None, "pct_change": None, "ex_rights": True,
                            "bar_date": "2026-10-12", "volume": 1, "avg_vol_5d": None, "vol_ratio": None},
            },
            volume_data=[{"rank": 1, "id": "2323", "name": "中環", "volume": 90_000_000, "close_price": 10.9,
                          "pct_change": None, "ex_rights": True},
                         {"rank": 2, "id": "00632R", "name": "元大台灣50反1", "volume": 80_000_000, "close_price": 3.0,
                          "pct_change": 0.3, "trade_value_yi": 120.0, "value_rank": None, "value_rank_etf": 2},
                         {"rank": 3, "id": "2409", "name": "友達", "volume": 70_000_000, "close_price": 20.3,
                          "pct_change": 1.5, "trade_value_yi": 24.0, "value_rank": 20}],
            value_ranking={"data_date": "2026-10-12", "etfs": [], "stocks": [
                {"rank": 1, "id": "2330", "name": "台積電", "trade_value_yi": 596.0, "close_price": 2540.0,
                 "pct_change": None, "ex_rights": True}]},
        )
        lines = {line for line in prompt.splitlines() if "2330" in line or "2323" in line}
        market = next(line for line in lines if line.startswith("- 2330.TW @ 2026-10-12"))
        self.assertIn("漲跌 除權息不比價", market)
        self.assertNotIn("None", market)
        self.assertNotIn("N/Ax", market)
        self.assertTrue(any("2323 中環" in line and "漲跌 除權息不比價" in line for line in lines))
        self.assertTrue(any("2330 台積電 | 成交值 596.0億" in line and "除權息不比價" in line for line in lines))
        volume = [line for line in prompt.splitlines() if line.startswith("- 第") and ("00632R" in line or "2409" in line)]
        self.assertTrue(any("ETF 成交值第2名，不列入個股成交值排名" in line for line in volume))
        self.assertTrue(any("個股成交值第20名，不含 ETF" in line for line in volume))
        self.assertNotIn("第None名", prompt)


class TaiexFallbackTest(unittest.TestCase):
    def test_twii_uses_fmtqik_and_mi_index(self):
        fetcher = _fetcher()
        series = [
            {"date": "2026-10-07", "close": 49806.37, "change": -16.18, "volume": 1},
            {"date": "2026-10-08", "close": 49313.44, "change": -492.93, "volume": 1},
        ]
        tape = {"index": {"close": 49301.96, "change": -11.48, "volume": 1, "bar_date": "2026-10-12"}}
        with mock.patch("modules.twse_tape.fetch_taiex_series", return_value=series), \
                mock.patch("modules.twse_tape.fetch_twse_tape", return_value=tape):
            payload = fetcher._get_stock_data_twse_fallback("^TWII", expected_date="2026-10-12")
        self.assertEqual(payload["symbol"], "^TWII")
        self.assertEqual(payload["bar_date"], "2026-10-12")
        self.assertEqual(payload["price"], 49301.96)
        self.assertEqual(payload["change"], -11.48)
        self.assertEqual(payload["pct_change"], -0.02)


if __name__ == "__main__":
    unittest.main()
