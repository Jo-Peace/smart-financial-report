#!/usr/bin/env python3
"""In-flight ledger predictions are priced every session for the ledger only (fail-safe)."""
import os
import sys
import unittest
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.pipeline_stages import (  # noqa: E402
    build_dynamic_universe,
    fetch_market_universe,
    inflight_prediction_symbols,
    ledger_only_symbols,
)


class _Store:
    def __init__(self, tickers=None, error=None, tape=False):
        self.tickers = tickers or []
        self.error = error
        self.tape = tape
        self.calls = []

    def has_tape_closes(self, as_of=None):
        return self.tape

    def get_inflight_prediction_tickers(self, as_of=None, limit=None):
        self.calls.append((as_of, limit))
        if self.error:
            raise self.error
        return self.tickers[:limit]


class InflightUniverseTest(unittest.TestCase):
    def test_inflight_symbols_stay_out_of_the_analysis_universe(self):
        inst = {"top_buy": [{"id": "2330", "name": "台積電"}], "top_sell": []}
        inflight = ["2408.TW", "3443.TW", "2330.TW"]
        symbols, _ = build_dynamic_universe(["^TWII", "2330.TW"], [], inst, [{"id": "2408"}], inflight_symbols=inflight)
        self.assertEqual(symbols, ["^TWII", "2330.TW", "2408.TW"])
        self.assertEqual(ledger_only_symbols(symbols, inflight, print_fn=None), ["3443.TW"])

    def test_store_lookup_respects_limit_and_env(self):
        store = _Store(["3443", "2603", "2344"])
        self.assertEqual(inflight_prediction_symbols("2026-10-13", limit=2, store=store), ["3443.TW", "2603.TW"])
        with mock.patch.dict(os.environ, {"INFLIGHT_PRICE_FETCH_MAX": "0"}):
            self.assertEqual(inflight_prediction_symbols("2026-10-13", store=store, print_fn=None), [])

    def test_off_tape_previous_target_is_fetched_as_otc(self):
        store = _Store(["6488"], tape=True)
        inflight = inflight_prediction_symbols("2026-10-13", limit=5, store=store)
        self.assertEqual(inflight, ["6488.TWO"])
        symbols, _ = build_dynamic_universe(["^TWII"], [], {}, [{"id": "6488"}], inflight_symbols=inflight)
        # Yesterday's OTC target: `.TW` has no data, so the analysis symbol becomes `.TWO`.
        self.assertEqual(symbols, ["^TWII", "6488.TWO"])
        self.assertEqual(ledger_only_symbols(symbols, inflight, print_fn=None), [])

    def test_store_errors_degrade_to_no_extra_symbols(self):
        store = _Store(error=RuntimeError("db locked"))
        self.assertEqual(inflight_prediction_symbols("2026-10-13", limit=5, store=store, print_fn=None), [])


class LedgerOnlyFetchTest(unittest.TestCase):
    def _payload(self, price):
        return {"price": price, "pct_change": 5.0, "volume": 9_000_000, "avg_vol_5d": 1_000_000,
                "vol_ratio": 9.0, "ma5": price * 0.95, "ma20": price * 0.9, "rsi": 70}

    def test_inflight_names_never_reach_the_signal_pack(self):
        from modules.market_signal_scorer import build_market_signal_pack

        payloads = {"^TWII": self._payload(20000), "2330.TW": self._payload(1000), "3443.TW": self._payload(1500)}
        seen = []
        market_data, ledger_data = fetch_market_universe(
            payloads.get,
            ["^TWII", "2330.TW"],
            ["3443.TW", "9999.TW"],
            on_result=lambda symbol, data, ledger: seen.append((symbol, bool(data), ledger)),
        )
        self.assertEqual(sorted(market_data), ["2330.TW", "^TWII"])
        self.assertEqual(sorted(ledger_data), ["3443.TW"])
        self.assertIn(("9999.TW", False, True), seen)
        pack = build_market_signal_pack(market_data, institutional_data={}, volume_data=[], stock_db={})
        self.assertNotIn("3443", repr(pack))

    def test_one_failing_symbol_does_not_stop_the_fetch(self):
        def fetch(symbol):
            if symbol == "BAD.TW":
                raise RuntimeError("yahoo blocked")
            return self._payload(10)

        market_data, ledger_data = fetch_market_universe(fetch, ["^TWII", "BAD.TW"], ["1234.TW"])
        self.assertEqual(sorted(market_data), ["^TWII"])
        self.assertEqual(sorted(ledger_data), ["1234.TW"])


if __name__ == "__main__":
    unittest.main()
