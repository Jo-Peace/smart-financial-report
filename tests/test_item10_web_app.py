#!/usr/bin/env python3
"""Regression tests for the AI 個股研究員 web app (quota, cache, validation, errors)."""
import logging
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Keep the import-time init_db() away from the real app/stock_research.db.
_IMPORT_TMP = tempfile.mkdtemp(prefix="item10_webapp_")
os.environ["STOCK_RESEARCH_DB_PATH"] = os.path.join(_IMPORT_TMP, "import.db")

from fastapi.testclient import TestClient  # noqa: E402

from app import database  # noqa: E402
from app import main_app  # noqa: E402
from modules.fetch_result import fetch_ok, fetch_error  # noqa: E402

STOCK_PAYLOAD = {
    "symbol": "2330.TW",
    "price": 1000.0,
    "change": 10.0,
    "pct_change": 1.01,
    "volume": 25_000_000,
    "avg_vol_5d": 20_000_000,
    "ma5": 990.0,
    "ma20": 950.0,
    "rsi": 60.0,
}


def _quiet_app_logs(test):
    for name in ("app.main_app", "app.database", "app.research_data"):
        logger = logging.getLogger(name)
        previous = logger.level
        logger.setLevel(logging.CRITICAL)
        test.addCleanup(logger.setLevel, previous)


def _fetcher_factory(stock=STOCK_PAYLOAD, calls=None, price_meta=None):
    calls = calls if calls is not None else []

    class FakeFetcher:
        def __init__(self, *args, **kwargs):
            calls.append(("init", kwargs))

        def get_stock_data(self, symbol):
            calls.append(("price", symbol))
            if stock is None:
                return fetch_error("no data", source="yfinance")
            return fetch_ok(dict(stock, symbol=symbol), source="yfinance", meta=price_meta)

        def get_news(self, query, days=2):
            calls.append(("news", query))
            return fetch_ok([{"title": "台積電法說", "url": "https://example.com/a", "content": "營收年增 30%"}])

        def get_single_stock_institutional_data(self, ticker):
            calls.append(("inst", ticker))
            return fetch_ok(
                {"foreign_net": 5_000_000, "trust_net": -200_000, "total_net": 4_800_000},
                meta={"data_date": "2026-10-12"},
            )

    return FakeFetcher, calls


def _analyzer_factory(reply="# 報告\n內容"):
    class FakeAnalyzer:
        def __init__(self, *args, **kwargs):
            pass

        def _call_gemini_with_retry(self, prompt, max_retries=3):
            FakeAnalyzer.last_prompt = prompt
            FakeAnalyzer.last_retries = max_retries
            if isinstance(reply, Exception):
                raise reply
            return reply

    return FakeAnalyzer


class WebAppTestBase(unittest.TestCase):
    def setUp(self):
        _quiet_app_logs(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        self._db_patch = mock.patch.object(database, "DB_PATH", self.db_path)
        self._db_patch.start()
        database.init_db()
        main_app._inflight_tickers.clear()
        patches = [
            mock.patch.object(main_app, "GEMINI_API_KEY", "test-gemini"),
            mock.patch.object(main_app, "TAVILY_API_KEY", "test-tavily"),
            mock.patch.object(main_app, "gemini_calls_remaining", return_value=None),
            mock.patch.object(main_app, "listing_known", return_value=True),
            mock.patch.object(
                main_app,
                "lookup_listing",
                return_value={"code": "2330", "name": "台積電", "market": "上市", "date": "2026-10-12", "close": 1000.0},
            ),
            mock.patch.object(main_app, "stock_profile", return_value={}),
            mock.patch.object(main_app, "monthly_revenue", return_value=None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(main_app.app)
        self.headers = {"X-Forwarded-For": "1.1.1.1"}

    def tearDown(self):
        self._db_patch.stop()
        self.tmp.cleanup()

    def _quota_used(self, ip="1.1.1.1"):
        return database.DAILY_FREE_QUOTA - database.get_remaining_quota(ip)

    def _generation_rows(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return [row[0] for row in conn.execute("SELECT status FROM generation_log ORDER BY id")]
        finally:
            conn.close()


class ClientIpTest(unittest.TestCase):
    def _request(self, headers, host="10.0.0.1"):
        req = mock.Mock()
        req.headers = headers
        req.client = mock.Mock(host=host)
        return req

    def test_spoofed_leftmost_hop_is_ignored(self):
        with mock.patch.object(main_app, "TRUSTED_PROXY_HOPS", 1), mock.patch.object(main_app, "CLIENT_IP_HEADER", ""):
            ip = main_app.get_client_ip(self._request({"X-Forwarded-For": "6.6.6.6, 203.0.113.9"}))
        self.assertEqual(ip, "203.0.113.9")

    def test_two_trusted_hops(self):
        with mock.patch.object(main_app, "TRUSTED_PROXY_HOPS", 2), mock.patch.object(main_app, "CLIENT_IP_HEADER", ""):
            ip = main_app.get_client_ip(self._request({"X-Forwarded-For": "6.6.6.6, 203.0.113.9, 172.16.0.1"}))
        self.assertEqual(ip, "203.0.113.9")

    def test_internal_hop_at_the_trusted_position_is_skipped(self):
        with mock.patch.object(main_app, "TRUSTED_PROXY_HOPS", 1), mock.patch.object(main_app, "CLIENT_IP_HEADER", ""):
            # Platform appended an internal hop after the real client: do not key everyone on 10.1.2.3.
            ip = main_app.get_client_ip(self._request({"X-Forwarded-For": "6.6.6.6, 203.0.113.9, 10.1.2.3"}))
            self.assertEqual(ip, "203.0.113.9")
            # Only internal hops (local / private network): keep the configured position.
            ip = main_app.get_client_ip(self._request({"X-Forwarded-For": "192.168.1.5, 10.1.2.3"}))
            self.assertEqual(ip, "10.1.2.3")
            # A client-sent internal address on the left never wins over the appended public hop.
            ip = main_app.get_client_ip(self._request({"X-Forwarded-For": "10.0.0.9, 198.51.100.4"}))
            self.assertEqual(ip, "198.51.100.4")

    def test_platform_header_wins(self):
        with mock.patch.object(main_app, "CLIENT_IP_HEADER", "True-Client-IP"):
            ip = main_app.get_client_ip(
                self._request({"True-Client-IP": "198.51.100.7", "X-Forwarded-For": "6.6.6.6"})
            )
        self.assertEqual(ip, "198.51.100.7")

    def test_no_forwarded_header_uses_socket_peer(self):
        with mock.patch.object(main_app, "CLIENT_IP_HEADER", ""):
            self.assertEqual(main_app.get_client_ip(self._request({})), "10.0.0.1")

    def test_header_diagnostics_log_once_when_enabled(self):
        with mock.patch.object(main_app, "LOG_CLIENT_IP_HEADERS", True), mock.patch.object(
            main_app, "_ip_headers_logged", False
        ), mock.patch.object(main_app, "CLIENT_IP_HEADER", ""), mock.patch.object(main_app, "TRUSTED_PROXY_HOPS", 1):
            with self.assertLogs("app.main_app", level="WARNING") as logs:
                main_app.get_client_ip(self._request({"X-Forwarded-For": "6.6.6.6, 203.0.113.9"}))
                main_app.get_client_ip(self._request({"X-Forwarded-For": "7.7.7.7"}))
        self.assertEqual(len(logs.records), 1)
        self.assertIn("6.6.6.6, 203.0.113.9", logs.output[0])
        self.assertIn("resolved=203.0.113.9", logs.output[0])


class ResearchEndpointTest(WebAppTestBase):
    def test_invalid_ticker_rejected_without_quota_or_network(self):
        fake_fetcher, calls = _fetcher_factory()
        with mock.patch.object(main_app, "DataFetcher", fake_fetcher):
            for raw in ["AAPL", "12", "<img src=x onerror=alert(1)>", "2330" * 5]:
                res = self.client.post("/api/research", json={"ticker": raw}, headers=self.headers)
                self.assertEqual(res.status_code, 400, raw)
        self.assertEqual(calls, [])
        self.assertEqual(self._quota_used(), 0)

    def test_unknown_listing_returns_404_without_quota(self):
        fake_fetcher, calls = _fetcher_factory()
        with mock.patch.object(main_app, "listing_known", return_value=False), mock.patch.object(
            main_app, "DataFetcher", fake_fetcher
        ):
            res = self.client.post("/api/research", json={"ticker": "9999"}, headers=self.headers)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(calls, [])
        self.assertEqual(self._quota_used(), 0)

    def test_success_charges_once_then_serves_cache_for_free(self):
        fake_fetcher, _ = _fetcher_factory()
        with mock.patch.object(main_app, "DataFetcher", fake_fetcher), mock.patch.object(
            main_app, "MarketAnalyzer", _analyzer_factory()
        ):
            first = self.client.post("/api/research", json={"ticker": "2330"}, headers=self.headers)
            second = self.client.post("/api/research", json={"ticker": "2330.tw"}, headers=self.headers)
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json()["cached"])
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["cached"])
        self.assertEqual(self._quota_used(), 1)
        self.assertEqual(self._generation_rows(), ["started", "success"])

    def test_missing_price_refunds_quota_and_skips_news(self):
        fake_fetcher, calls = _fetcher_factory(stock=None)
        with mock.patch.object(main_app, "DataFetcher", fake_fetcher), mock.patch.object(
            main_app, "MarketAnalyzer", _analyzer_factory()
        ):
            res = self.client.post("/api/research", json={"ticker": "2330"}, headers=self.headers)
        self.assertEqual(res.status_code, 404)
        self.assertNotIn("news", [c[0] for c in calls])
        self.assertEqual(self._quota_used(), 0)
        self.assertEqual(self._generation_rows(), ["started", "failed"])

    def test_gemini_limit_is_503_with_refund_and_no_internal_text(self):
        fake_fetcher, _ = _fetcher_factory()
        reply = "Error generating report: Gemini daily call limit reached: 8/8. Set GEMINI_DAILY_CALL_LIMIT higher"
        with mock.patch.object(main_app, "DataFetcher", fake_fetcher), mock.patch.object(
            main_app, "MarketAnalyzer", _analyzer_factory(reply)
        ):
            res = self.client.post("/api/research", json={"ticker": "2330"}, headers=self.headers)
        self.assertEqual(res.status_code, 503)
        self.assertNotIn("GEMINI_DAILY_CALL_LIMIT", res.text)
        self.assertEqual(self._quota_used(), 0)
        self.assertIsNone(database.get_cached_report("2330"))

    def test_unexpected_exception_is_generic_500(self):
        fake_fetcher, _ = _fetcher_factory()
        with mock.patch.object(main_app, "DataFetcher", fake_fetcher), mock.patch.object(
            main_app, "MarketAnalyzer", _analyzer_factory(RuntimeError("secret internal path /etc/x"))
        ):
            res = self.client.post("/api/research", json={"ticker": "2330"}, headers=self.headers)
        self.assertEqual(res.status_code, 500)
        self.assertNotIn("secret internal path", res.text)
        self.assertEqual(self._quota_used(), 0)

    def test_quota_exhausted_blocks_new_reports(self):
        for _ in range(database.DAILY_FREE_QUOTA):
            self.assertTrue(database.use_quota("1.1.1.1"))
        self.assertFalse(database.use_quota("1.1.1.1"))
        res = self.client.post("/api/research", json={"ticker": "2330"}, headers=self.headers)
        self.assertEqual(res.status_code, 429)

    def test_failed_attempts_count_toward_global_cap(self):
        for _ in range(database.DAILY_GLOBAL_ATTEMPT_LIMIT):
            database.log_generation("2330", "started")
        self.assertFalse(database.check_global_limit())
        res = self.client.post("/api/research", json={"ticker": "2317"}, headers=self.headers)
        self.assertEqual(res.status_code, 503)

    def test_repeated_failures_from_one_ip_are_capped(self):
        fake_fetcher, _ = _fetcher_factory(stock=None)
        with mock.patch.object(main_app, "DataFetcher", fake_fetcher), mock.patch.object(
            main_app, "MarketAnalyzer", _analyzer_factory()
        ):
            codes = [res.status_code for res in (
                self.client.post("/api/research", json={"ticker": "2330"}, headers=self.headers)
                for _ in range(database.DAILY_IP_FAILED_ATTEMPT_LIMIT + 1)
            )]
            other_ip = self.client.post("/api/research", json={"ticker": "2330"}, headers={"X-Forwarded-For": "2.2.2.2"})
        self.assertEqual(codes[:-1], [404] * database.DAILY_IP_FAILED_ATTEMPT_LIMIT)
        self.assertEqual(codes[-1], 429)  # blocked before any new generation attempt
        self.assertEqual(self._generation_rows().count("started"), database.DAILY_IP_FAILED_ATTEMPT_LIMIT + 1)
        self.assertEqual(other_ip.status_code, 404)  # other clients are unaffected
        self.assertEqual(self._quota_used(), 0)

    def test_price_date_from_fetch_meta_drives_the_institutional_check(self):
        fake_fetcher, _ = _fetcher_factory(price_meta={"bar_date": "2026-10-13"})
        analyzer = _analyzer_factory()
        with mock.patch.object(main_app, "DataFetcher", fake_fetcher), mock.patch.object(
            main_app, "MarketAnalyzer", analyzer
        ):
            self.client.post("/api/research", json={"ticker": "2330"}, headers=self.headers)
        self.assertIn("股價日期 2026-10-13", analyzer.last_prompt)
        self.assertIn("與股價日期 2026-10-13 不同", analyzer.last_prompt)  # T86 fake is 10-12
        self.assertNotIn("交易所收盤", analyzer.last_prompt)  # listing table is still 10-12

    def test_web_uses_reduced_gemini_retries(self):
        fake_fetcher, calls = _fetcher_factory()
        analyzer = _analyzer_factory()
        with mock.patch.object(main_app, "DataFetcher", fake_fetcher), mock.patch.object(
            main_app, "MarketAnalyzer", analyzer
        ):
            self.client.post("/api/research", json={"ticker": "2330"}, headers=self.headers)
        self.assertEqual(analyzer.last_retries, main_app.WEB_GEMINI_MAX_RETRIES)
        init_kwargs = [c[1] for c in calls if c[0] == "init"][0]
        self.assertEqual(init_kwargs.get("tavily_search_limit"), main_app.WEB_TAVILY_SEARCHES_PER_REPORT)
        # Units are converted to 張 and the institutional data date is labelled.
        self.assertIn("25,000 張", analyzer.last_prompt)
        self.assertIn("外資 +5,000 張", analyzer.last_prompt)
        self.assertIn("法人資料日期：2026-10-12", analyzer.last_prompt)


class QuotaAndListingEndpointsTest(WebAppTestBase):
    def test_quota_global_remaining_respects_gemini_budget(self):
        with mock.patch.object(main_app, "gemini_calls_remaining", return_value=3):
            data = self.client.get("/api/quota", headers=self.headers).json()
        self.assertEqual(data["global_remaining"], 3)
        self.assertEqual(data["total"], database.DAILY_FREE_QUOTA)

    def test_hot_stocks_show_real_views_and_unnumbered_editor_picks(self):
        database.save_report("2330", "x")
        database.increment_views("2330", database.get_today_str())
        rows = self.client.get("/api/hot_stocks").json()
        real = [r for r in rows if not r["editor_pick"]]
        picks = [r for r in rows if r["editor_pick"]]
        self.assertEqual(real[0]["ticker"], "2330")
        self.assertEqual(real[0]["views"], 2)  # save_report counts the first view
        self.assertTrue(picks)
        for pick in picks:
            self.assertIsNone(pick["views"])
            self.assertIsNone(pick["date"])

    def test_repeated_cache_hits_count_one_view_per_viewer_per_day(self):
        fake_fetcher, _ = _fetcher_factory()
        with mock.patch.object(main_app, "DataFetcher", fake_fetcher), mock.patch.object(
            main_app, "MarketAnalyzer", _analyzer_factory()
        ):
            self.client.post("/api/research", json={"ticker": "2330"}, headers=self.headers)  # generates: view 1
            for _ in range(5):
                res = self.client.post("/api/research", json={"ticker": "2330"}, headers=self.headers)
                self.assertTrue(res.json()["cached"])
            self.client.post("/api/research", json={"ticker": "2330"}, headers={"X-Forwarded-For": "2.2.2.2"})
        hot = [r for r in self.client.get("/api/hot_stocks").json() if r["ticker"] == "2330"]
        self.assertEqual(hot[0]["views"], 2)
        conn = sqlite3.connect(self.db_path)
        try:
            stored = [row[0] for row in conn.execute("SELECT viewer FROM report_viewers")]
        finally:
            conn.close()
        self.assertEqual(len(stored), 2)
        self.assertNotIn("1.1.1.1", stored)  # hashed, not the raw IP

    def test_healthz_and_security_headers(self):
        res = self.client.get("/healthz")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["checks"]["db"])
        self.assertIn("script-src", res.headers.get("Content-Security-Policy", ""))
        with mock.patch.object(main_app, "GEMINI_API_KEY", None):
            self.assertEqual(self.client.get("/healthz").status_code, 503)

    def test_healthz_fails_when_configured_disk_fell_back(self):
        with mock.patch.object(database, "STORAGE_FALLBACK_FROM", "/var/data/stock_research.db"):
            res = self.client.get("/healthz")
        self.assertEqual(res.status_code, 503)
        self.assertFalse(res.json()["checks"]["configured_storage"])

    def test_docs_pages_are_not_blanked_by_csp(self):
        docs = self.client.get("/docs")
        self.assertEqual(docs.status_code, 200)
        self.assertNotIn("Content-Security-Policy", docs.headers)
        self.assertEqual(docs.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertIn("Content-Security-Policy", self.client.get("/").headers)


class WebFlagDocsTest(unittest.TestCase):
    def test_every_web_env_flag_is_documented(self):
        import glob
        import re

        names = set()
        for path in glob.glob(os.path.join(BASE_DIR, "app", "*.py")):
            with open(path, encoding="utf-8") as f:
                names |= set(re.findall(r'(?:getenv|_int_env)\("([A-Z][A-Z0-9_]+)"', f.read()))
        with open(os.path.join(BASE_DIR, "docs", "env_flags.md"), encoding="utf-8") as f:
            docs = f.read()
        with open(os.path.join(BASE_DIR, ".env.example"), encoding="utf-8") as f:
            example = f.read()
        self.assertIn("LOG_CLIENT_IP_HEADERS", names)
        for name in sorted(names):
            self.assertIn(f"`{name}`", docs, name)
            self.assertIn(name, example, name)


class FrontendQuotaTest(unittest.TestCase):
    def test_exhausted_quota_does_not_block_free_cache_reads(self):
        with open(os.path.join(BASE_DIR, "app", "static", "app.js"), encoding="utf-8") as f:
            source = f.read()
        body = source.split("async function updateQuota(", 1)[1].split("// === Search ===", 1)[0]
        self.assertNotIn(".disabled = true", body)
        self.assertIn("仍可查詢已生成的報告", body)


class DatabaseFallbackTest(unittest.TestCase):
    def test_unwritable_configured_dir_falls_back_to_default(self):
        _quiet_app_logs(self)
        with tempfile.TemporaryDirectory() as tmp:
            default = os.path.join(tmp, "default.db")
            with mock.patch.object(database, "DEFAULT_DB_PATH", default), mock.patch.object(
                database, "DB_PATH", "/nonexistent-root-dir-item10/x/research.db"
            ), mock.patch.object(database, "STORAGE_FALLBACK_FROM", None), mock.patch(
                "os.makedirs", side_effect=PermissionError("read-only")
            ):
                database._ensure_db_dir()
                self.assertEqual(database.DB_PATH, default)
                self.assertFalse(database.storage_is_persistent())
                self.assertEqual(database.storage_fallback_from(), "/nonexistent-root-dir-item10/x/research.db")


if __name__ == "__main__":
    unittest.main()
