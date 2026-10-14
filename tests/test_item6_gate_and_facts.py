#!/usr/bin/env python3
"""Gate: stale session / bar dates / pricing / TLS; facts: no AI fallback for local fields."""
import os
import sys
import tempfile
import unittest
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.current_index import build_current_index  # noqa: E402
from modules.data_quality_gate import drop_off_session_bars, evaluate_data_quality  # noqa: E402
from modules.prediction_validator import validate_prediction_targets  # noqa: E402
from modules.structured_facts import (  # noqa: E402
    INDEX_MISSING_TEXT,
    build_local_structured_facts,
    format_ground_truth_block,
    format_local_market_prompt_block,
    merge_structured_with_local_facts,
    overnight_rule_text,
)


def _inst(date="2026-10-12", price_date="2026-10-12", **extra):
    return {
        "data_date": date,
        "top_buy": [{"id": "3443", "name": "創意", "foreign_net": 424_993, "est_amount": 34.42, "price_date": price_date}],
        "top_sell": [{"id": "2330", "name": "台積電", "foreign_net": -9_422_826, "est_amount": -239.34, "price_date": price_date}],
        **extra,
    }


def _market(bar="2026-10-12", twii_bar=None):
    return {
        "^TWII": {"price": 49301.96, "change": -11.48, "pct_change": -0.02, "bar_date": twii_bar or bar},
        "2330.TW": {"price": 2540.0, "change": -10.0, "pct_change": -0.39, "bar_date": bar},
    }


def _codes(report, level=None):
    return {i.code for i in report.issues if level is None or i.level == level}


def _gate(**kw):
    args = dict(
        run_date="2026-10-12",
        institutional_data=_inst(),
        market_data=_market(),
        volume_data=[{"id": "2409", "close_price": 20.0, "pct_change": 1.0}],
        volume_date="2026-10-12",
        expected_symbols=["^TWII", "2330.TW"],
    )
    args.update(kw)
    return evaluate_data_quality(**args)


class GateTest(unittest.TestCase):
    def test_clean_same_session_passes(self):
        self.assertEqual(_codes(_gate(), "fail"), set())

    def test_previous_session_on_a_trading_day_fails(self):
        report = _gate(
            run_date="2026-10-13",
            institutional_data=_inst(date="2026-10-12"),
            volume_date="2026-10-12",
        )
        self.assertIn("institutional_stale", _codes(report, "fail"))

    def test_holiday_run_only_warns(self):
        # 2026-10-10 is a Saturday: main.py stops earlier; the gate itself just warns.
        report = _gate(
            run_date="2026-10-10",
            institutional_data=_inst(date="2026-10-08", price_date="2026-10-08"),
            market_data=_market(bar="2026-10-08"),
            volume_date="2026-10-08",
        )
        self.assertIn("non_session_run", _codes(report, "warn"))
        self.assertNotIn("institutional_stale", _codes(report))

    def test_index_bar_from_another_day_fails(self):
        report = _gate(market_data=_market(twii_bar="2026-10-08"))
        self.assertIn("index_bar_date_mismatch", _codes(report, "fail"))

    def test_stock_bar_from_another_day_warns(self):
        market = _market()
        market["2603.TW"] = {"price": 255.5, "change": 23.0, "pct_change": 9.89, "bar_date": "2026-10-08"}
        report = _gate(market_data=market, expected_symbols=["^TWII", "2330.TW", "2603.TW"])
        self.assertIn("bar_date_mismatch", _codes(report, "warn"))
        self.assertEqual(_codes(report, "fail"), set())

    def test_key_symbol_stale_or_missing_bar_fails(self):
        market = _market()
        market["2330.TW"]["bar_date"] = "2026-10-08"
        report = _gate(market_data=market)
        self.assertIn("key_bar_date_mismatch", _codes(report, "fail"))
        self.assertNotIn("index_bar_date_mismatch", _codes(report))
        market = _market()
        market["2330.TW"].pop("bar_date")
        self.assertIn("key_bar_date_mismatch", _codes(_gate(market_data=market), "fail"))
        market = _market()
        market["2330.TW"] = None
        self.assertIn("key_symbol_missing", _codes(_gate(market_data=market), "fail"))

    def test_key_symbol_ex_rights_day_is_not_a_failure(self):
        # 2330 X day with Yahoo a session behind: the tape close is dated today, pct is unknowable.
        market = _market()
        market["2330.TW"] = {"price": 2540.0, "change": None, "pct_change": None, "ex_rights": True, "bar_date": "2026-10-12"}
        report = _gate(market_data=market)
        self.assertEqual(_codes(report, "fail"), set())
        self.assertIn("key_symbol_ex_rights", _codes(report, "warn"))
        self.assertNotIn("market_pct_flat", _codes(report))
        cleaned, dropped = drop_off_session_bars(market, "2026-10-12")
        self.assertEqual(dropped, [])
        self.assertTrue(cleaned["2330.TW"]["ex_rights"])
        rows = validate_prediction_targets([{"id": "2330", "name": "台積電", "direction": "空"}], market, institutional_data=_inst())
        self.assertIsNone(rows[0]["hit"])
        self.assertIn("除權息不比價", "；".join(rows[0]["evidence"]))

    def test_off_session_bars_are_dropped_after_the_gate(self):
        market = _market()
        market["2603.TW"] = {"price": 255.5, "pct_change": 9.89, "bar_date": "2026-10-08"}
        market["2317.TW"] = {"price": 249.0, "pct_change": 1.0}  # legacy payload without bar_date
        cleaned, dropped = drop_off_session_bars(market, "2026-10-12")
        self.assertIsNone(cleaned["2603.TW"])
        self.assertEqual(dropped, ["2603.TW@2026-10-08"])
        self.assertEqual(cleaned["2330.TW"]["price"], 2540.0)
        self.assertEqual(cleaned["2317.TW"]["price"], 249.0)
        self.assertEqual(market["2603.TW"]["price"], 255.5)  # input untouched

    def test_validator_never_scores_a_stale_bar(self):
        market = _market()
        market["2603.TW"] = {"price": 255.5, "pct_change": 9.89, "bar_date": "2026-10-08"}
        # Shared hit rule (item 4): excess vs same-session TWII must clear the
        # 0.5pt dead band, so give the same-session short a clear miss by TWII.
        market["2330.TW"]["pct_change"] = -1.5
        rows = validate_prediction_targets(
            [{"id": "2603", "name": "長榮", "direction": "多"}, {"id": "2330", "name": "台積電", "direction": "空"}],
            market,
            institutional_data=_inst(),
        )
        by_id = {r["id"]: r for r in rows}
        self.assertIsNone(by_id["2603"]["hit"])
        self.assertIn("K 棒停在 2026-10-08", "；".join(by_id["2603"]["evidence"]))
        self.assertTrue(by_id["2330"]["hit"])

    def test_missing_index_fails(self):
        market = _market()
        market["^TWII"] = None
        self.assertIn("index_missing", _codes(_gate(market_data=market), "fail"))

    def test_stale_price_in_leaderboard_fails(self):
        report = _gate(institutional_data=_inst(price_date="2026-09-18"))
        self.assertIn("institutional_price_stale", _codes(report, "fail"))

    def test_sparse_pricing_fails_and_tls_warns(self):
        inst = _inst(pricing={"coverage_top30": {"checked": 30, "priced": 28, "ratio": 0.9333, "unpriced": [{"id": "9999", "name": "X"}]}})
        report = _gate(institutional_data=inst, tls_events=[{"host": "www.twse.com.tw"}])
        self.assertIn("institutional_pricing_sparse", _codes(report, "fail"))  # < 95%
        self.assertIn("tls_unverified", _codes(report, "warn"))
        inst = _inst(pricing={"coverage_top30": {"checked": 30, "priced": 29, "ratio": 0.9667, "unpriced": []}})
        self.assertNotIn("institutional_pricing_sparse", _codes(_gate(institutional_data=inst)))

    def test_unpriced_leader_fails_even_with_full_share_coverage(self):
        inst = _inst(
            pricing={
                "coverage_top30": {"checked": 30, "priced": 30, "ratio": 1.0, "unpriced": []},
                "material_unpriced": [
                    {"id": "2330", "name": "台積電", "reference_amount": -240.28, "reason": "reference_above_cut_line"},
                    {"id": "3443", "name": "創意", "reference_amount": None, "reason": "high_price_no_reference"},
                ],
            }
        )
        report = _gate(institutional_data=inst)
        self.assertIn("institutional_leader_unpriced", _codes(report, "fail"))
        message = next(i.message for i in report.issues if i.code == "institutional_leader_unpriced")
        self.assertIn("2330 台積電（參考約 -240.3億）", message)
        self.assertIn("3443 創意", message)

    def test_missing_calendar_year_warns(self):
        report = _gate(
            run_date="2027-01-04",
            institutional_data=_inst(date="2027-01-04", price_date="2027-01-04"),
            market_data=_market(bar="2027-01-04"),
            volume_date="2027-01-04",
        )
        self.assertIn("calendar_year_missing", _codes(report, "warn"))
        self.assertNotIn("calendar_year_missing", _codes(_gate()))


class FactsTest(unittest.TestCase):
    def test_missing_local_index_never_keeps_ai_value(self):
        local = build_local_structured_facts(date_str="2026-10-12", market_data={"^TWII": None}, institutional_data=_inst())
        self.assertIsNone(local["index_action"])
        merged = merge_structured_with_local_facts(
            {"index_action": "上漲 300 點", "global_overnight": ["台積電ADR 大漲 9%"], "sectors": ["AI"]}, local
        )
        self.assertEqual(merged["index_action"], INDEX_MISSING_TEXT)
        self.assertEqual(merged["global_overnight"], [])
        self.assertEqual(merged["sectors"], ["AI"])

    def test_local_index_and_scope(self):
        local = build_local_structured_facts(date_str="2026-10-12", market_data=_market(), institutional_data=_inst())
        self.assertEqual(local["index_action"], "下跌 11.5點（-0.02%）")
        self.assertEqual(local["data_scope"]["institutional_scope_label"], "上市")
        self.assertIn("創意 (+34.4億)", local["safe_havens_bought"])

    def test_commodity_quote_is_not_called_close(self):
        local = build_local_structured_facts(
            date_str="2026-10-12",
            commodity_data={"GC=F": {"name": "黃金 (Gold)", "price": 2650.5, "pct_change": 0.5, "quote_kind": "latest_quote", "change_basis": "1d"}},
        )
        self.assertEqual(local["commodities"], ["黃金 (報價$2650.5, 1日+0.50%)"])

    def test_overnight_quotes_keep_every_fetched_digit(self):
        overnight = {
            "NQ=F": {"label": "那斯達克期貨", "price": 25012.75, "pct_change": 0.31, "bar_date": "2026-10-13", "quote_kind": "latest_quote"},
            "TWD=X": {"label": "美元兌台幣", "price": 32.45, "pct_change": -0.1, "bar_date": "2026-10-13", "quote_kind": "latest_quote"},
            "X": {"label": "小數", "price": 7.1234, "pct_change": None, "bar_date": "2026-10-13", "quote_kind": "latest_quote"},
            "Y": {"label": "整數", "price": 120.0, "pct_change": None, "bar_date": "2026-10-13", "quote_kind": "close"},
        }
        items = build_local_structured_facts(date_str="2026-10-13", global_overnight=overnight)["global_overnight"]
        self.assertEqual(items[0], "那斯達克期貨 2026-10-13 報價25012.75 (+0.31%)")  # {:g} gave 25012.8
        self.assertEqual(items[1], "美元兌台幣 2026-10-13 報價32.45 (-0.10%)")
        self.assertEqual(items[2], "小數 2026-10-13 報價7.1234")
        self.assertEqual(items[3], "整數 2026-10-13 收120")
        gold = build_local_structured_facts(
            date_str="2026-10-13", commodity_data={"GC=F": {"name": "黃金 (Gold)", "price": 10123.45, "quote_kind": "latest_quote"}}
        )["commodities"]
        self.assertEqual(gold, ["黃金 (報價$10123.45)"])

    def test_overnight_rule_and_blocks(self):
        overnight = {
            "TSM": {"label": "台積電ADR", "price": 310.5, "pct_change": -1.2, "bar_date": "2026-10-12", "quote_kind": "close"},
        }
        local = build_local_structured_facts(date_str="2026-10-12", global_overnight=overnight)
        self.assertEqual(local["global_overnight"], ["台積電ADR 2026-10-12 收310.5 (-1.20%)"])
        self.assertIn("台積電ADR 2026-10-12", overnight_rule_text(local))
        self.assertIn("不得寫這些數字", overnight_rule_text({}))
        self.assertIn("global_overnight", format_ground_truth_block(local))
        block = format_local_market_prompt_block(
            institutional_data=_inst(),
            global_overnight=overnight,
            market_extras={"taifex_oi": {"foreign_oi_net": -82806, "date": "2026-10-12"}},
            value_ranking={"stocks": [{"id": "2330", "name": "台積電", "trade_value_yi": 596.0}], "etfs": []},
        )
        self.assertIn("上市", block)
        self.assertIn("台積電 成交值596億", block)
        self.assertIn("台指期外資未平倉淨口數 -82,806 口（2026-10-12）", block)
        self.assertIn("融資融券", block)  # listed as 未取得


class TickerSuffixTest(unittest.TestCase):
    """Every normalizer strips .TWO as a suffix (上櫃 '6488.TWO' used to become '6488O')."""

    def test_two_suffix(self):
        from modules import (
            analytics_store,
            data_quality_gate,
            editorial_frame,
            market_signal_scorer,
            prediction_validator,
            role_scorer,
            similar_sessions,
            structured_facts,
        )
        from modules.tickers import bare_ticker

        normalizers = [
            bare_ticker,
            analytics_store._normalize_ticker,
            data_quality_gate._normalize_ticker,
            editorial_frame._normalize_ticker,
            market_signal_scorer._normalize_ticker,
            prediction_validator._normalize_ticker,
            role_scorer.normalize_ticker,
            similar_sessions._norm_ticker,
            structured_facts._normalize_ticker,
        ]
        for fn in normalizers:
            self.assertEqual(fn("6488.TWO"), "6488", fn)
            self.assertEqual(fn("2330.tw"), "2330", fn)
            self.assertEqual(fn(" 2330.TW "), "2330", fn)
            self.assertEqual(fn("^TWII"), "^TWII", fn)
            self.assertEqual(fn(None), "", fn)

    def test_two_market_key_round_trips_to_bare_id(self):
        import sqlite3

        from modules.analytics_store import AnalyticsStore
        from modules.market_signal_scorer import build_market_signal_pack

        market = {"6488.TWO": {"price": 600.0, "pct_change": 3.1, "vol_ratio": 2.4, "bar_date": "2026-10-12"}}
        inst = {"top_buy": [{"id": "6488", "name": "環球晶", "foreign_net": 1_000_000, "est_amount": 6.0}], "top_sell": []}
        pack = build_market_signal_pack(market, institutional_data=inst)
        tickers = {row["ticker"] for row in pack["top_candidates"]}
        self.assertNotIn("6488O", tickers)
        row = next(r for r in pack["top_candidates"] if r["ticker"] == "6488")
        self.assertEqual(row.get("foreign_buy_rank"), 1)  # price row and institutional row joined
        self.assertEqual(row.get("price"), 600.0)

        with tempfile.TemporaryDirectory() as tmp:
            store = AnalyticsStore(os.path.join(tmp, "analytics.db"))
            store.init_db()
            conn = sqlite3.connect(store.db_path)
            store._record_market_snapshots(conn.cursor(), "2026-10-12", market)
            conn.commit()
            self.assertEqual([r[0] for r in conn.execute("SELECT ticker FROM market_snapshots")], ["6488"])
            conn.close()

        from modules.stock_db_updater import StockDatabaseUpdater

        updater = StockDatabaseUpdater.__new__(StockDatabaseUpdater)  # no Gemini, no data/stock_db.json
        updater.stock_db = {"6488": {"name": "環球晶"}}
        with mock.patch.object(StockDatabaseUpdater, "_save_stock_db"):
            # Same-session TWII bar: the shared hit rule judges excess vs the index.
            with_index = dict(market, **{"^TWII": {"price": 23000.0, "pct_change": 0.4, "bar_date": "2026-10-12"}})
            updater.update_tracking_stats([{"id": "6488.TWO", "name": "環球晶", "direction": "多"}], with_index)
        tracking = updater.stock_db["6488"]["tracking"]
        self.assertEqual(tracking["hit_count"], 1)  # 6488.TWO price found under the bare id
        self.assertIn("+3.10%", tracking["history"][-1]["result"])


class CurrentIndexNoteTest(unittest.TestCase):
    def test_same_day_claim_only_with_matching_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok = build_current_index(tmp, "2026-10-12", institutional_date="2026-10-12", volume_date="2026-10-12")
            self.assertIn("法人資料與成交量排行皆為 2026-10-12", ok)
            stale = build_current_index(tmp, "2026-10-13", institutional_date="2026-10-12", volume_date="2026-10-12")
            self.assertNotIn("同日資料閘門", stale)
            self.assertIn("法人資料日 2026-10-12", stale)
            unknown = build_current_index(tmp, "2026-10-13")
            self.assertNotIn("同日資料閘門", unknown)

    def test_rewrite_without_dates_keeps_previous_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = build_current_index(tmp, "2026-10-12", institutional_date="2026-10-12", volume_date="2026-10-12")
            again = build_current_index(tmp, "2026-10-12", youtube_ready=True, previous_markdown=first)
            self.assertIn("法人資料與成交量排行皆為 2026-10-12", again)


class SessionGuardTest(unittest.TestCase):
    def test_guard(self):
        from modules.pipeline_context import allow_stale_session, session_guard

        self.assertEqual(session_guard("2026-10-12"), (True, ""))
        ok, msg = session_guard("2026-10-10")
        self.assertFalse(ok)
        self.assertIn("不是台股交易日", msg)
        ok, msg = session_guard("2026-10-09")  # 國慶日補假 in data/twse_closed_days.json
        self.assertFalse(ok)
        ok, _ = session_guard("2026-10-09", allow_stale=True)
        self.assertTrue(ok)
        with mock.patch.dict(os.environ, {"ALLOW_STALE_SESSION": "1"}):
            self.assertTrue(allow_stale_session())


class StaleRelabelTest(unittest.TestCase):
    """ALLOW_STALE_SESSION may relabel run_date only on a holiday or for an explicit date."""

    def _env(self, value):
        env = {k: v for k, v in os.environ.items() if k not in {"ALLOW_STALE_SESSION", "ALLOW_HOLIDAY_RUN"}}
        if value is not None:
            env["ALLOW_STALE_SESSION"] = value
        return mock.patch.dict(os.environ, env, clear=True)

    def test_trading_day_flag_left_on_does_not_relabel_stale_t86(self):
        from modules.pipeline_context import allow_stale_session, stale_relabel_decision, stale_session_target

        with self._env("1"):
            self.assertTrue(allow_stale_session())
            self.assertIsNone(stale_session_target())
            relabel, msg = stale_relabel_decision("2026-10-13", "2026-10-12", target=stale_session_target())
        self.assertFalse(relabel)
        self.assertIn("ALLOW_STALE_SESSION=2026-10-12", msg)
        # run_date stays 2026-10-13, so the gate still stops the late-T86 run (exit 3).
        report = _gate(
            run_date="2026-10-13",
            institutional_data=_inst(date="2026-10-12"),
            volume_date="2026-10-12",
        )
        self.assertIn("institutional_stale", _codes(report, "fail"))

    def test_trading_day_explicit_date_back_fills_that_session_only(self):
        from modules.pipeline_context import allow_stale_session, stale_relabel_decision, stale_session_target

        with self._env("2026-10-12"):
            self.assertTrue(allow_stale_session())
            target = stale_session_target()
        self.assertEqual(target, "2026-10-12")
        self.assertTrue(stale_relabel_decision("2026-10-13", "2026-10-12", target=target)[0])
        # A leftover date never relabels another day's stale data.
        self.assertFalse(stale_relabel_decision("2026-10-14", "2026-10-13", target=target)[0])

    def test_holiday_back_fill_still_relabels(self):
        from modules.pipeline_context import stale_relabel_decision

        self.assertTrue(stale_relabel_decision("2026-10-10", "2026-10-08")[0])  # Saturday
        self.assertTrue(stale_relabel_decision("2026-10-09", "2026-10-08", target="2026-10-08")[0])
        self.assertFalse(stale_relabel_decision("2026-10-09", "2026-10-08", target="2026-10-07")[0])

    def test_main_uses_the_decision_and_banner_names_the_date(self):
        from modules.runtime_flags import collect_flag_notices

        with open(os.path.join(BASE_DIR, "main.py"), encoding="utf-8") as f:
            source = f.read()
        self.assertIn("stale_relabel_decision(run_date, institutional_date", source)
        text = " ".join(m for _l, m in collect_flag_notices({"ALLOW_STALE_SESSION": "2026-10-12"}))
        self.assertIn("ALLOW_STALE_SESSION=2026-10-12", text)
        text = " ".join(m for _l, m in collect_flag_notices({"ALLOW_STALE_SESSION": "1"}))
        self.assertIn("交易日不改標", text)


if __name__ == "__main__":
    unittest.main()
