#!/usr/bin/env python3
"""Ground-truth helpers + prompt builder for the web research report."""
import logging
import os
import sys
import tempfile
import unittest
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault("STOCK_RESEARCH_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="item10_rd_"), "rd.db"))

from app import research_data as rd  # noqa: E402
from app.main_app import build_research_prompt  # noqa: E402


class TickerAndUnitsTest(unittest.TestCase):
    def test_normalize_ticker(self):
        self.assertEqual(rd.normalize_ticker(" 2330 "), "2330")
        self.assertEqual(rd.normalize_ticker("2330.tw"), "2330")
        self.assertEqual(rd.normalize_ticker("6488.TWO"), "6488")
        self.assertEqual(rd.normalize_ticker("00878"), "00878")
        self.assertEqual(rd.normalize_ticker("2881a"), "2881A")
        for bad in ["AAPL", "12", "1234567", "23 30", "<b>", "", None]:
            self.assertIsNone(rd.normalize_ticker(bad), bad)

    def test_shares_to_lots(self):
        self.assertEqual(rd.shares_to_lots(5_000_000), 5000)
        self.assertEqual(rd.shares_to_lots("-1,234,567"), -1235)
        self.assertIsNone(rd.shares_to_lots(None))
        self.assertIsNone(rd.shares_to_lots("--"))

    def test_roc_dates(self):
        self.assertEqual(rd.roc_to_iso("1151012"), "2026-10-12")
        self.assertEqual(rd.roc_to_iso("11509"), "2026-09")
        self.assertIsNone(rd.roc_to_iso("abc"))

    def test_institutional_lines_label_units_and_stale_date(self):
        text = rd.format_institutional_lines(
            {"foreign_net": 5_000_000, "trust_net": -200_000, "total_net": 4_800_000},
            "2026-10-08",
            "2026-10-12",
        )
        self.assertIn("外資 +5,000 張", text)
        self.assertIn("投信 -200 張", text)
        self.assertIn("法人資料日期：2026-10-08", text)
        self.assertIn("非當日", text)
        self.assertIn("資料未提供", rd.format_institutional_lines(None, None, None))


class OpenDataTablesTest(unittest.TestCase):
    def setUp(self):
        logger = logging.getLogger("app.research_data")
        previous = logger.level
        logger.setLevel(logging.CRITICAL)
        self.addCleanup(logger.setLevel, previous)

    def test_monthly_revenue_converts_thousand_ntd_to_100m(self):
        payload = [
            {
                "資料年月": "11509",
                "公司代號": "1108",
                "公司名稱": "幸福",
                "產業別": "水泥工業",
                "營業收入-當月營收": "301975",
                "營業收入-上月比較增減(%)": "4.08",
                "營業收入-去年同月增減(%)": "-13.85",
                "累計營業收入-前期比較增減(%)": "-18.09",
                "備註": "-",
            }
        ]
        with mock.patch.object(rd, "_fetch_json", return_value=payload):
            table = rd._load_monthly_revenue_table()
        row = table["1108"]
        self.assertEqual(row["revenue_100m"], 3.02)
        self.assertEqual(row["year_month"], "2026-09")
        self.assertEqual(row["industry"], "水泥工業")

    def test_listing_table_merges_twse_and_tpex(self):
        def fake_fetch(url):
            if url == rd.TWSE_LISTING_URL:
                return [{"Date": "1151012", "Code": "2330", "Name": "台積電", "ClosingPrice": "1,000.00"}]
            return [{"Date": "1151012", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶", "Close": "450.5"}]

        with mock.patch.object(rd, "_fetch_json", side_effect=fake_fetch):
            table = rd._load_listing_table()
        self.assertEqual(table["2330"]["market"], "上市")
        self.assertEqual(table["2330"]["close"], 1000.0)
        self.assertEqual(table["6488"]["market"], "上櫃")
        self.assertEqual(table["6488"]["date"], "2026-10-12")

    def test_listing_partial_failure_is_unknown_not_missing(self):
        """One market's source down: its codes are 'unknown' (None), never 404 「查無代號」."""
        twse_row = [{"Date": "1151012", "Code": "2330", "Name": "台積電", "ClosingPrice": "1,000.00"}]
        tpex_row = [{"Date": "1151012", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶", "Close": "450.5"}]
        for failing_url, ok_code, unknown_code in (
            (rd.TWSE_LISTING_URL, "6488", "2330"),
            (rd.TPEX_LISTING_URL, "2330", "5347"),
        ):
            def fake_fetch(url, failing_url=failing_url):
                if url == failing_url:
                    raise TimeoutError("timed out")
                return twse_row if url == rd.TWSE_LISTING_URL else tpex_row

            cache = rd._CachedTable(rd._load_listing_table)
            with mock.patch.object(rd, "_fetch_json", side_effect=fake_fetch), mock.patch.object(
                rd, "LISTING_TABLE", cache
            ):
                self.assertFalse(cache.get().complete)
                self.assertTrue(rd.listing_known(ok_code))
                self.assertIsNone(rd.listing_known(unknown_code), failing_url)

    def test_complete_listing_rejects_unknown_codes(self):
        def fake_fetch(url):
            if url == rd.TWSE_LISTING_URL:
                return [{"Date": "1151012", "Code": "2330", "Name": "台積電", "ClosingPrice": "1,000.00"}]
            return [{"Date": "1151012", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶", "Close": "450.5"}]

        cache = rd._CachedTable(rd._load_listing_table)
        with mock.patch.object(rd, "_fetch_json", side_effect=fake_fetch), mock.patch.object(rd, "LISTING_TABLE", cache):
            self.assertTrue(rd.listing_known("2330"))
            self.assertFalse(rd.listing_known("9999"))

    def test_partial_table_is_retried_soon_and_never_replaces_complete_one(self):
        complete = rd.ListingTable({"2330": {}, "6488": {}}, markets=rd.LISTING_MARKETS)
        partial = rd.ListingTable({"6488": {}}, markets={"上櫃"})
        results = [partial, complete, partial]

        cache = rd._CachedTable(lambda: results.pop(0), ttl_sec=3600, retry_after_sec=0)
        self.assertIs(cache.get(), partial)  # cold cache: serve the partial table ...
        self.assertIs(cache.get(), complete)  # ... but reload on the next call (short TTL)
        cache._fresh_until = 0.0  # complete table expired
        self.assertIs(cache.get(), complete)  # a later partial load keeps the complete table
        self.assertEqual(results, [])

    def test_concurrent_callers_share_one_cold_load(self):
        import threading

        started, release = threading.Event(), threading.Event()
        calls = {"n": 0}

        def slow_loader():
            calls["n"] += 1
            started.set()
            release.wait(5)
            return {"2330": {"code": "2330"}}

        cache = rd._CachedTable(slow_loader, wait_timeout_sec=5)
        results = []
        first = threading.Thread(target=lambda: results.append(cache.get()))
        first.start()
        self.assertTrue(started.wait(5))
        second = threading.Thread(target=lambda: results.append(cache.get()))
        second.start()
        release.set()
        first.join(5)
        second.join(5)
        self.assertEqual(calls["n"], 1)
        self.assertEqual([("2330" in r) for r in results], [True, True])

    def test_cached_table_keeps_stale_data_when_refresh_fails(self):
        calls = {"n": 0}

        def loader():
            calls["n"] += 1
            if calls["n"] == 1:
                return {"2330": {"code": "2330"}}
            raise RuntimeError("network down")

        table = rd._CachedTable(loader, ttl_sec=0, retry_after_sec=0)
        self.assertIn("2330", table.get())
        self.assertIn("2330", table.get())  # refresh failed -> still serves stale data
        self.assertEqual(calls["n"], 2)

    def test_cached_table_unavailable_returns_none(self):
        table = rd._CachedTable(lambda: (_ for _ in ()).throw(RuntimeError("down")))
        self.assertIsNone(table.get())


class ResearchPromptTest(unittest.TestCase):
    def _prompt(self, **overrides):
        kwargs = dict(
            ticker="2543",
            stock_name="皇昌",
            market="上市",
            profile={},
            listing={"date": "2026-10-12", "close": 30.0},
            yf_symbol="2543.TW",
            stock_data={
                "price": 30.0,
                "change": 0.5,
                "pct_change": 1.69,
                "volume": 3_456_000,
                "avg_vol_5d": 2_000_000,
                "bar_date": "2026-10-12",
            },
            institutional=None,
            institutional_date=None,
            revenue=None,
            news=[{"title": "皇昌接單", "url": "https://example.com/n", "content": "第三季營收創高"}],
            date_str="2026-10-12",
        )
        kwargs.update(overrides)
        return build_research_prompt(**kwargs)

    def test_missing_fundamentals_are_marked_not_invented(self):
        prompt = self._prompt()
        self.assertIn("禁止自行推估或編造數字", prompt)
        self.assertIn("月營收：資料未提供", prompt)
        self.assertIn("不得自行推估", prompt)
        self.assertIn("本次沒有本機公司資料", prompt)
        self.assertIn("3,456 張", prompt)
        self.assertIn("摘要：第三季營收創高", prompt)
        self.assertIn("皇昌", prompt)

    def test_dates_follow_the_quoted_price_not_the_cached_exchange_table(self):
        today = dict(price=31.0, change=1.0, pct_change=3.33, volume=1_000_000, avg_vol_5d=900_000, bar_date="2026-10-13")
        inst = {"foreign_net": 1_000_000, "trust_net": 0, "total_net": 1_000_000}

        # Evening: today's T86 next to today's price, exchange table still on yesterday.
        prompt = self._prompt(stock_data=today, institutional=inst, institutional_date="2026-10-13")
        self.assertIn("股價日期 2026-10-13", prompt)
        self.assertNotIn("非當日", prompt.split("# 報告要求")[0])
        self.assertNotIn("交易所收盤", prompt)  # stale 10-12 close is not printed next to today's price

        # Before T86 is out: yesterday's 法人 next to today's price must be flagged.
        prompt = self._prompt(stock_data=today, institutional=inst, institutional_date="2026-10-12")
        self.assertIn("與股價日期 2026-10-13 不同", prompt)

        # Same session: the exchange close is shown as a cross-check.
        prompt = self._prompt(listing={"date": "2026-10-13", "close": 31.0}, stock_data=today)
        self.assertIn("交易所收盤（同日核對）：31.0 元（2026-10-13）", prompt)

    def test_unknown_price_date_never_claims_a_mismatch(self):
        undated = dict(price=31.0, change=1.0, pct_change=3.33, volume=1_000_000, avg_vol_5d=900_000)
        prompt = self._prompt(
            stock_data=undated,
            institutional={"foreign_net": 1_000_000, "trust_net": 0, "total_net": 1_000_000},
            institutional_date="2026-10-12",
        )
        self.assertIn("法人資料日期：2026-10-12", prompt)
        self.assertNotIn("與股價日期", prompt)
        self.assertNotIn("交易所收盤", prompt)

    def test_real_fetcher_shape_carries_the_bar_date(self):
        """The real DataFetcher.get_stock_data (yfinance and TWSE fallback) feeds 股價日期."""
        import pandas as pd

        from app.main_app import _fetch_price
        from modules import data_fetcher

        index = pd.DatetimeIndex(
            pd.date_range("2026-09-21", periods=17, freq="B", tz="Asia/Taipei"), name="Date"
        )
        hist = pd.DataFrame({"Close": [30.0 + i * 0.1 for i in range(17)], "Volume": [1_000_000] * 17}, index=index)
        ticker = mock.Mock()
        ticker.history.return_value = hist
        with mock.patch.object(data_fetcher, "TavilyClient"), mock.patch.object(
            data_fetcher.yf, "Ticker", return_value=ticker
        ):
            fetcher = data_fetcher.DataFetcher("test-key")
            symbol, data = _fetch_price(fetcher, "2543", "上市")
        self.assertEqual(symbol, "2543.TW")
        self.assertEqual(data["bar_date"], index[-1].date().isoformat())  # 2026-10-13

        prompt = self._prompt(
            stock_data=data,
            institutional={"foreign_net": 1_000_000, "trust_net": 0, "total_net": 1_000_000},
            institutional_date="2026-10-12",
        )
        self.assertIn("股價日期 2026-10-13", prompt)
        self.assertIn("與股價日期 2026-10-13 不同", prompt)

        rows = [{"date": f"2026-10-{d:02d}", "close": 30.0 + d, "volume": 1000, "change": 1.0} for d in (12, 13)]
        empty = mock.Mock()
        empty.history.return_value = pd.DataFrame()
        with mock.patch.object(data_fetcher, "TavilyClient"), mock.patch.object(
            data_fetcher.yf, "Ticker", return_value=empty
        ), mock.patch.object(data_fetcher.yf, "download", return_value=pd.DataFrame()), mock.patch.object(
            data_fetcher.DataFetcher, "_fetch_twse_daily_closes", return_value=rows
        ), mock.patch("builtins.print"):
            result = data_fetcher.DataFetcher("test-key").get_stock_data("2543.TW")
        self.assertEqual(result["meta"]["bar_date"], "2026-10-13")
        # Item 6: the payload itself carries bar_date too (gate / ledger / validator read it).
        self.assertEqual(result["data"]["bar_date"], "2026-10-13")

    def test_profile_and_revenue_are_injected(self):
        prompt = self._prompt(
            profile={"name": "台積電", "sector": "半導體", "supply_chain_role": "晶圓代工", "key_products": ["CoWoS"], "ai_connection": ""},
            revenue={"year_month": "2026-09", "revenue_100m": 3300.5, "mom_pct": 1.2, "yoy_pct": 30.4, "ytd_yoy_pct": 35.0, "industry": "半導體業", "note": "-"},
        )
        self.assertIn("產業定位：半導體", prompt)
        self.assertIn("主要產品：CoWoS", prompt)
        self.assertIn("當月營收：3,300.5 億元", prompt)
        self.assertIn("年增率：30.4%", prompt)
        self.assertNotIn("公司備註", prompt)


if __name__ == "__main__":
    unittest.main()
