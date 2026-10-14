#!/usr/bin/env python3
"""TWSE HTTP helper: TLS ladder is flagged, concurrency is capped."""
import os
import sys
import threading
import time
import unittest
from unittest import mock

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules import twse_http  # noqa: E402


class TlsLadderTest(unittest.TestCase):
    def setUp(self):
        twse_http.reset_insecure_events()
        twse_http.reset_connect_failures()

    def tearDown(self):
        twse_http.reset_insecure_events()
        twse_http.reset_connect_failures()

    def test_verified_response_is_not_flagged(self):
        resp = mock.Mock()
        with mock.patch.object(twse_http.requests, "get", return_value=resp) as get:
            out = twse_http.twse_get("https://www.twse.com.tw/x", retries=1)
        self.assertFalse(out.tls_unverified)
        self.assertNotIn("verify", get.call_args.kwargs)
        self.assertEqual(twse_http.insecure_events(), [])

    def test_strict_x509_retry_still_verifies(self):
        ok = mock.Mock()
        with mock.patch.object(twse_http.requests, "get", side_effect=requests.exceptions.SSLError("strict")), \
                mock.patch.object(twse_http, "_relaxed_get", return_value=ok) as relaxed:
            out = twse_http.twse_get("https://www.twse.com.tw/x", retries=1)
        self.assertIs(out, ok)
        self.assertFalse(out.tls_unverified)
        relaxed.assert_called_once()

    def test_unverified_fallback_is_recorded(self):
        insecure = mock.Mock()
        calls = []

        def fake_get(url, **kwargs):
            calls.append(kwargs)
            if kwargs.get("verify") is False:
                return insecure
            raise requests.exceptions.SSLError("bad chain")

        with mock.patch.object(twse_http.requests, "get", side_effect=fake_get), \
                mock.patch.object(twse_http, "_relaxed_get", side_effect=requests.exceptions.SSLError("bad")), \
                mock.patch.dict(os.environ, {"TWSE_ALLOW_INSECURE_SSL": "1"}):
            out = twse_http.twse_get("https://www.twse.com.tw/x", retries=1)
        self.assertTrue(out.tls_unverified)
        self.assertEqual(twse_http.insecure_events()[0]["host"], "www.twse.com.tw")

    def test_insecure_can_be_disabled(self):
        with mock.patch.object(twse_http.requests, "get", side_effect=requests.exceptions.SSLError("x")), \
                mock.patch.object(twse_http, "_relaxed_get", side_effect=requests.exceptions.SSLError("x")), \
                mock.patch.dict(os.environ, {"TWSE_ALLOW_INSECURE_SSL": "0"}), \
                mock.patch.object(twse_http.time, "sleep"):
            with self.assertRaises(requests.exceptions.SSLError):
                twse_http.twse_get("https://www.twse.com.tw/x", retries=2)


class NetworkDownTest(unittest.TestCase):
    """Smoke test 9: a dead network fails in seconds; a short blip is absorbed; "not published yet" keeps its waits."""

    def setUp(self):
        twse_http.reset_connect_failures()
        self.waits = []
        self.calls = []
        self.now = 0.0  # simulated clock: sleeps advance it, nothing really waits

        def _sleep(secs):
            self.waits.append(secs)
            self.now += secs

        patcher = mock.patch("time.sleep", side_effect=_sleep)
        patcher.start()
        self.addCleanup(patcher.stop)
        clock = mock.patch.object(twse_http, "_clock", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.addCleanup(twse_http.reset_connect_failures)
        env = mock.patch.dict(os.environ, {"FINMIND_FALLBACK": "0"})
        env.start()
        self.addCleanup(env.stop)

    def _down(self, url, **kwargs):
        self.calls.append(url)
        self.now += 0.05  # a DNS failure answers almost at once
        # What requests raises for a DNS failure / refused connection (urllib3 NewConnectionError).
        raise requests.exceptions.ConnectionError("Max retries exceeded (Failed to resolve 'www.twse.com.tw')")

    def _blip(self, until, ok):
        """Network down until simulated time ``until``, healthy afterwards."""

        def _get(url, **kwargs):
            if self.now < until:
                return self._down(url, **kwargs)
            self.calls.append(url)
            return ok

        return _get

    @staticmethod
    def _t86_ok(date_str="20261012"):
        resp = mock.Mock()
        resp.text = '{"stat": "OK"}'
        resp.json.return_value = {"stat": "OK", "date": date_str, "data": [["2330"]]}
        return resp

    def _fetcher(self):
        from modules.data_fetcher import DataFetcher

        fetcher = DataFetcher.__new__(DataFetcher)
        fetcher.search_log = []
        return fetcher

    def test_classifier(self):
        self.assertTrue(twse_http.is_connect_failure(requests.exceptions.ConnectionError("Failed to resolve")))
        self.assertTrue(twse_http.is_connect_failure(requests.exceptions.ConnectTimeout("connect timeout")))
        self.assertFalse(twse_http.is_connect_failure(requests.exceptions.SSLError("bad chain")))
        self.assertFalse(twse_http.is_connect_failure(requests.exceptions.ReadTimeout("read timeout")))
        self.assertFalse(twse_http.is_connect_failure(
            requests.exceptions.ConnectionError("('Connection aborted.', RemoteDisconnected('closed'))")))

    def test_twse_get_connect_budget_and_circuit(self):
        per_call_wait = sum(twse_http.CONNECT_BACKOFF_SEC * n for n in range(1, twse_http.CONNECT_ATTEMPTS))
        with mock.patch.object(twse_http.requests, "get", side_effect=self._down):
            with self.assertRaises(requests.exceptions.ConnectionError):
                twse_http.twse_get("https://www.twse.com.tw/a", retries=3, backoff=2)
            self.assertEqual(len(self.calls), twse_http.CONNECT_ATTEMPTS)
            with self.assertRaises(requests.exceptions.ConnectionError):
                twse_http.twse_get("https://www.twse.com.tw/b", retries=3, backoff=2)
            # Two failed calls spanning >= CIRCUIT_MIN_OUTAGE_SEC: the circuit is open.
            self.assertGreaterEqual(self.now, twse_http.CIRCUIT_MIN_OUTAGE_SEC)
            before = len(self.calls)
            # Circuit open: no request, no wait.
            with self.assertRaises(twse_http.TwseHostUnreachable):
                twse_http.twse_get_json("https://www.twse.com.tw/c", retries=3)
            self.assertEqual(len(self.calls), before)
            self.assertLessEqual(sum(self.waits), 2 * per_call_wait)
            # After the cooldown one single-attempt probe goes through; it fails, the circuit re-opens.
            self.now += twse_http.CIRCUIT_COOLDOWN_SEC + 0.01
            with self.assertRaises(requests.exceptions.ConnectionError):
                twse_http.twse_get("https://www.twse.com.tw/d", retries=3, backoff=2)
            self.assertEqual(len(self.calls), before + 1)
            with self.assertRaises(twse_http.TwseHostUnreachable):
                twse_http.twse_get("https://www.twse.com.tw/e", retries=3, backoff=2)
            self.assertEqual(len(self.calls), before + 1)
        # Network back: the next probe after the cooldown succeeds and closes the circuit.
        self.now += twse_http.CIRCUIT_COOLDOWN_SEC + 0.01
        ok = mock.Mock()
        with mock.patch.object(twse_http.requests, "get", return_value=ok):
            self.assertIs(twse_http.twse_get("https://www.twse.com.tw/f", retries=1), ok)
            self.assertEqual(twse_http.circuit_remaining("https://www.twse.com.tw/g"), 0.0)
            # Another host is not blocked by the TWSE circuit.
            self.assertIs(twse_http.twse_get("https://www.tpex.org.tw/x", retries=1), ok)

    def test_a_short_blip_is_absorbed_and_never_opens_the_circuit(self):
        ok = mock.Mock()
        # Reviewer repro: a 1.2 s DNS blip at the start of the MI_INDEX20 session scan.
        with mock.patch.object(twse_http.requests, "get", side_effect=self._blip(1.2, ok)):
            self.assertIs(twse_http.twse_get("https://www.twse.com.tw/MI_INDEX20?s=1", retries=3, backoff=2), ok)
        self.assertEqual(twse_http.circuit_remaining("www.twse.com.tw"), 0.0)
        # Two quick failed calls (< CIRCUIT_MIN_OUTAGE_SEC apart) do not open it either.
        twse_http.reset_connect_failures()
        self.now = 0.0
        with mock.patch.object(twse_http.requests, "get", side_effect=self._blip(2.0, ok)):
            for _ in range(2):
                with self.assertRaises(requests.exceptions.ConnectionError):
                    twse_http.twse_get("https://www.twse.com.tw/x", retries=1)
            self.assertLess(self.now, twse_http.CIRCUIT_MIN_OUTAGE_SEC)
            self.assertEqual(twse_http.circuit_remaining("www.twse.com.tw"), 0.0)
            self.now = 30.0  # network healthy long since: a later call goes through
            self.assertIs(twse_http.twse_get("https://www.twse.com.tw/y", retries=3), ok)
        # Two isolated failed calls minutes apart are two blips, not one outage.
        twse_http.reset_connect_failures()
        self.now = 0.0
        with mock.patch.object(twse_http.requests, "get", side_effect=self._down):
            for start in (0.0, 120.0):
                self.now = start
                with self.assertRaises(requests.exceptions.ConnectionError):
                    twse_http.twse_get("https://www.twse.com.tw/z", retries=1)
        self.assertEqual(twse_http.circuit_remaining("www.twse.com.tw"), 0.0)

    def test_t86_rides_out_a_short_outage(self):
        import datetime

        # Down for 6 s: longer than one twse_get connect budget, shorter than the recovery waits.
        with mock.patch.object(twse_http.requests, "get", side_effect=self._blip(6.0, self._t86_ok())):
            data, day = self._fetcher()._fetch_institutional_payload(datetime.datetime(2026, 10, 12), day_attempts=1)
        self.assertEqual(day, "2026-10-12")
        self.assertEqual(data["stat"], "OK")
        self.assertIn(5.0, self.waits)  # one connect-recovery wait, no 15 s readiness wait
        self.assertNotIn(15, self.waits)

    def test_institutional_fetch_is_bounded_when_the_network_is_down(self):
        import datetime

        fetcher = self._fetcher()
        today = datetime.datetime(2026, 10, 12)
        sessions = [today - datetime.timedelta(days=i) for i in range(15)]
        with mock.patch.object(twse_http.requests, "get", side_effect=self._down), \
                mock.patch.object(fetcher, "_session_dates_back", return_value=sessions), \
                mock.patch("modules.data_fetcher.now_tpe_naive", return_value=today):
            result = fetcher.get_institutional_data(top_n=10)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "NETWORK")
        # Was 8 readiness waits (15s…105s) + 3 tries per session ≈ 8.7 min of sleeps;
        # now 2 connect-recovery waits and a half-open probe: well under a minute.
        self.assertLessEqual(sum(self.waits), 30.0, self.waits)
        self.assertNotIn(15, self.waits)
        self.assertLessEqual(len(self.calls), 2 * twse_http.CONNECT_ATTEMPTS + 1)
        # compare-institutional's short path is bounded the same way.
        twse_http.reset_connect_failures()
        self.waits.clear()
        self.now = 0.0
        with mock.patch.object(twse_http.requests, "get", side_effect=self._down):
            with self.assertRaises(requests.exceptions.ConnectionError):
                fetcher._fetch_institutional_payload(today, day_attempts=2, label="compare")
        self.assertLessEqual(sum(self.waits), 30.0, self.waits)
        self.assertNotIn(15, self.waits)

    def test_not_published_yet_keeps_the_readiness_waits(self):
        import datetime

        not_ready = mock.Mock()
        not_ready.text = '{"stat": "很抱歉，沒有符合條件的資料!"}'
        not_ready.json.return_value = {"stat": "很抱歉，沒有符合條件的資料!"}
        with mock.patch.object(twse_http.requests, "get", return_value=not_ready):
            data, day = self._fetcher()._fetch_institutional_payload(datetime.datetime(2026, 10, 12), day_attempts=3)
        self.assertIsNone(data)
        self.assertEqual(self.waits, [15, 30])


class ConcurrencyCapTest(unittest.TestCase):
    def test_parallel_calls_are_capped(self):
        active = {"now": 0, "peak": 0}
        lock = threading.Lock()

        def slow_get(url, **kwargs):
            with lock:
                active["now"] += 1
                active["peak"] = max(active["peak"], active["now"])
            time.sleep(0.02)
            with lock:
                active["now"] -= 1
            return mock.Mock()

        sem = threading.BoundedSemaphore(2)
        with mock.patch.object(twse_http.requests, "get", side_effect=slow_get), \
                mock.patch.object(twse_http, "_SEMAPHORE", sem):
            threads = [
                threading.Thread(target=twse_http.twse_get, args=("https://www.twse.com.tw/x",), kwargs={"retries": 1})
                for _ in range(8)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertLessEqual(active["peak"], 2)


if __name__ == "__main__":
    unittest.main()
