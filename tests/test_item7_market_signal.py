#!/usr/bin/env python3
"""Item 7: market_signal_scorer unit fixes + mechanical-flow calendar."""
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from unittest import mock  # noqa: E402

from modules import market_calendar  # noqa: E402
from modules.market_calendar import (  # noqa: E402
    analyzer_calendar_block,
    calendar_one_liner,
    mechanical_flow_events,
    mechanical_flow_for_ticker,
    nearby_market_events,
)
from modules.market_signal_scorer import (  # noqa: E402
    _normalize_ticker,
    build_market_signal_pack,
    format_signal_pack_for_prompt,
    rank_volume_by_turnover,
)


def _buy(sid, name, amount, rank_hint=None):
    return {"id": sid, "name": name, "est_amount": amount, "foreign_net": int(amount * 1e6)}


def _mkt(price, pct, volume, vol_ratio, ma20=None):
    return {
        "price": price,
        "pct_change": pct,
        "volume": volume,
        "vol_ratio": vol_ratio,
        "ma5": price,
        "ma20": ma20 if ma20 is not None else price * 0.95,
        "rsi": 55,
    }


# Share-count board: ETFs and low-priced names dominate.
VOLUME_BOARD = [
    {"rank": 1, "id": "00406A", "name": "ETF", "volume": 280_000_000, "close_price": 10.2, "pct_change": -0.8},
    {"rank": 2, "id": "2409", "name": "友達", "volume": 250_000_000, "close_price": 14.0, "pct_change": -3.5},
    {"rank": 3, "id": "1301", "name": "台塑", "volume": 150_000_000, "close_price": 55.0, "pct_change": 1.0},
    {"rank": 4, "id": "2603", "name": "長榮", "volume": 40_000_000, "close_price": 255.0, "pct_change": 9.9},
]


class TurnoverRankTest(unittest.TestCase):
    def test_volume_board_reranked_by_traded_value_without_etfs(self):
        ranks = rank_volume_by_turnover(VOLUME_BOARD)
        self.assertNotIn("00406A", ranks)
        # 長榮 255 × 40M ≈ 102 億 beats 友達 14 × 250M = 35 億
        self.assertEqual(ranks["2603"]["turnover_rank"], 1)
        self.assertEqual(ranks["2409"]["share_rank"], 2)
        self.assertGreater(ranks["2603"]["turnover_yi"], ranks["2409"]["turnover_yi"])

    def test_off_board_mega_cap_outranks_board_leader(self):
        """2330 is never on the share board; the pool must still rank it by value."""
        board = [
            {"rank": 1, "id": "1303", "name": "南亞", "volume": 300_000_000, "close_price": 75.9, "pct_change": 2.0},
            {"rank": 2, "id": "1802", "name": "台玻", "volume": 250_000_000, "close_price": 41.0, "pct_change": 1.0},
        ]
        market = {
            "^TWII": {"price": 22000.0, "pct_change": 0.5, "volume": 0},
            "2330.TW": _mkt(2550.0, 1.2, 22_480_000, 1.1),   # ≈ 573 億
            "0050.TW": _mkt(200.0, 0.5, 90_000_000, 1.0),    # ETF: excluded
        }
        ranks = rank_volume_by_turnover(board, market)
        self.assertEqual(ranks["2330"]["turnover_rank"], 1)
        self.assertIsNone(ranks["2330"]["share_rank"])
        self.assertEqual(ranks["1303"]["turnover_rank"], 2)
        self.assertTrue(ranks["1303"]["on_share_board"])
        self.assertNotIn("0050", ranks)
        self.assertNotIn("^TWII", ranks)
        # Backward compatible: board-only call still works.
        self.assertEqual(rank_volume_by_turnover(board)["1303"]["turnover_rank"], 1)

        pack = build_market_signal_pack(market, {"top_buy": [], "top_sell": []}, board, {})
        rows = {r["ticker"]: r for r in pack["top_candidates"]}
        self.assertEqual(rows["1303"]["turnover_rank"], 2)
        text = "；".join(rows["1303"]["signals"])
        self.assertIn("觀察池成交值第 2 名", text)
        self.assertNotIn("市場關注度極高", text)
        self.assertFalse(any(s.startswith("成交值第") for r in rows.values() for s in r["signals"]))
        # Index weights top any value ranking: their rank is not 「熱但法人缺席」.
        self.assertFalse(any(a["ticker"] == "2330" for a in pack["anomalies"]))
        hot = [a for a in pack["anomalies"] if a["type"] == "成交量熱但法人缺席"]
        self.assertTrue(hot and all("觀察池成交值第" in a["text"] for a in hot))
        prompt = format_signal_pack_for_prompt(pack)
        self.assertIn("不是全市場排名", prompt)

    def test_market_wide_value_rank_replaces_the_pool_rank(self):
        """One prompt, one 成交值 rank: value_ranking (成交值前20) wins over the pool rank."""
        board = [
            {"rank": 1, "id": "1303", "name": "南亞", "volume": 300_000_000, "close_price": 75.9, "pct_change": 2.0,
             "value_rank": 5, "value_rank_basis": "stocks_ex_etf", "trade_value_yi": 227.7},
            {"rank": 2, "id": "1802", "name": "台玻", "volume": 250_000_000, "close_price": 41.0, "pct_change": 1.0,
             "value_rank": 31, "value_rank_basis": "stocks_ex_etf", "trade_value_yi": 102.5},
        ]
        market = {
            "2330.TW": _mkt(2550.0, 1.2, 22_480_000, 1.1),
            "2603.TW": _mkt(255.0, 2.0, 40_000_000, 1.3),
        }
        value_ranking = {
            "data_date": "2026-10-12",
            "stocks": [
                {"rank": 1, "id": "2330", "name": "台積電", "trade_value_yi": 573.2},
                {"rank": 2, "id": "3443", "name": "創意", "trade_value_yi": 300.0},
                {"rank": 5, "id": "1303", "name": "南亞", "trade_value_yi": 227.7},
            ],
            "etfs": [{"rank": 1, "id": "0050", "trade_value_yi": 120.0}],
        }
        ranks = rank_volume_by_turnover(board, market, value_ranking=value_ranking)
        self.assertEqual(ranks["1303"]["turnover_rank"], 5)  # pool rank would be 2
        self.assertEqual(ranks["1303"]["rank_basis"], "market")
        self.assertEqual(ranks["1802"]["turnover_rank"], 31)  # share board value_rank, same basis
        self.assertIsNone(ranks["2603"]["turnover_rank"])  # outside the known market ranks
        self.assertEqual(ranks["3443"]["turnover_rank"], 2)
        self.assertNotIn("0050", ranks)
        # Without value_ranking the pool basis is unchanged.
        self.assertEqual(rank_volume_by_turnover(board, market)["1303"]["rank_basis"], "pool")

        pack = build_market_signal_pack(market, {"top_buy": [], "top_sell": []}, board, {}, value_ranking=value_ranking)
        rows = {r["ticker"]: r for r in pack["top_candidates"]}
        self.assertEqual(rows["1303"]["turnover_rank"], 5)
        self.assertEqual(rows["1303"]["volume_rank"], 5)
        text = "；".join(rows["1303"]["signals"])
        self.assertIn("個股成交值第 5 名", text)
        self.assertNotIn("觀察池", text)
        self.assertNotIn("turnover_rank", rows["2603"])
        prompt = format_signal_pack_for_prompt(pack)
        self.assertNotIn("觀察池成交值", prompt)
        self.assertIn("同一排名", prompt)

    def test_normalize_two_suffix(self):
        self.assertEqual(_normalize_ticker("6488.TWO"), "6488")
        self.assertEqual(_normalize_ticker("2330.TW"), "2330")


class QuietAccumulationTest(unittest.TestCase):
    def test_off_share_board_is_not_low_volume(self):
        """High-price buy leader absent from the share board must not become 「未放量」."""
        inst = {"top_buy": [_buy("3443", "創意", 30.4)], "top_sell": []}
        pack = build_market_signal_pack({}, inst, VOLUME_BOARD, {})
        types = {a["type"] for a in pack["anomalies"]}
        self.assertNotIn("法人買超但量縮", types)
        self.assertFalse(any("未放量" in a["text"] for a in pack["anomalies"]))

    def test_limit_up_with_heavy_volume_is_not_quiet_accumulation(self):
        market = {"2603.TW": _mkt(255.5, 9.89, 39_552_358, 2.94)}
        inst = {"top_buy": [_buy("9999", "其他", 40.0), _buy("2603", "長榮", 29.6)], "top_sell": []}
        pack = build_market_signal_pack(market, inst, VOLUME_BOARD, {})
        row = next(r for r in pack["top_candidates"] if r["ticker"] == "2603")
        self.assertTrue(row.get("limit_up"))
        self.assertFalse(any(a["ticker"] == "2603" and "量縮" in a["type"] for a in pack["anomalies"]))
        hypothesis = next(h for h in pack["operator_hypotheses"] if h["ticker"] == "2603")
        self.assertIn("不是低調吸籌", hypothesis["logic"])

    def test_limit_up_lock_labelled_as_lock(self):
        market = {"3017.TW": _mkt(800.0, 9.95, 2_000_000, 0.45)}
        inst = {"top_buy": [_buy("3017", "奇鋐", 12.0)], "top_sell": []}
        pack = build_market_signal_pack(market, inst, [], {})
        lock = [a for a in pack["anomalies"] if a["type"] == "漲停鎖量"]
        self.assertEqual(len(lock), 1)
        self.assertIn("鎖死", lock[0]["text"])

    def test_buy_with_own_volume_shrink_uses_vol_ratio_and_intensity(self):
        market = {"3037.TW": _mkt(200.0, -1.0, 50_000_000, 0.6)}  # turnover 100 億
        inst = {"top_buy": [_buy("3037", "欣興", 20.0)], "top_sell": []}
        pack = build_market_signal_pack(market, inst, [], {})
        shrink = [a for a in pack["anomalies"] if a["type"] == "法人買超但量縮"]
        self.assertEqual(len(shrink), 1)
        self.assertIn("0.60x", shrink[0]["text"])
        self.assertIn("占成交值 20.0%", shrink[0]["text"])

    def test_anomaly_score_is_contradiction_strength_not_attention(self):
        market = {
            "3037.TW": _mkt(200.0, -1.0, 50_000_000, 0.6),
            "2603.TW": _mkt(255.0, 3.0, 40_000_000, 2.5),
        }
        inst = {
            "top_buy": [_buy("2603", "長榮", 50.0), _buy("3037", "欣興", 20.0)],
            "top_sell": [],
        }
        pack = build_market_signal_pack(market, inst, VOLUME_BOARD, {})
        for anomaly in pack["anomalies"]:
            self.assertGreaterEqual(anomaly["score"], 0)
            self.assertLessEqual(anomaly["score"], 100)
        rows = {r["ticker"]: r for r in pack["top_candidates"]}
        self.assertIn("attention_score", rows["2603"])
        self.assertIn("direction_score", rows["2603"])
        # attention is higher for 長榮 (turnover #1 + buy #1) but it is not an anomaly
        self.assertGreater(rows["2603"]["score"], rows["3037"]["score"])
        self.assertEqual(pack["strongest_anomaly"]["ticker"], "3037")


class IndexWeightAndBuyLeaderTest(unittest.TestCase):
    def _pool(self):
        # 2330 sells but is the pool's #1 by traded value; five non-mega names carry real signals.
        market = {
            "2330.TW": _mkt(2550.0, -0.8, 22_000_000, 1.1),   # ≈ 561 億, turnover #1
            "2409.TW": _mkt(14.0, 2.5, 250_000_000, 1.6),
            "3037.TW": _mkt(200.0, 1.5, 20_000_000, 1.2),
            "2603.TW": _mkt(255.0, 1.0, 30_000_000, 1.0),
            "3443.TW": _mkt(1500.0, -1.2, 2_000_000, 1.0),
            "1301.TW": _mkt(55.0, 1.0, 60_000_000, 1.0),
        }
        inst = {
            "top_buy": [_buy("3443", "創意", 30.4), _buy("3037", "欣興", 12.0), _buy("2603", "長榮", 8.0)],
            "top_sell": [{"id": "2330", "name": "台積電", "est_amount": -120.0, "foreign_net": -40_000_000}],
        }
        return market, inst

    def test_index_weight_value_rank_is_context_only(self):
        market, inst = self._pool()
        pack = build_market_signal_pack(market, inst, [], {})
        rows = {r["ticker"]: r for r in pack["top_candidates"]}
        tsmc = rows["2330"]
        self.assertEqual(tsmc["turnover_rank"], 1)          # rank kept as context
        self.assertFalse(any("觀察池成交值" in s for s in tsmc["signals"]))
        hyp = tsmc["operator_logic"]
        self.assertFalse(any("觀察池成交值" in e for e in hyp["evidence"]))
        # Sell #1 attention only: 18 - 1 = 17 (+8 MA20), no +23 turnover points.
        self.assertEqual(tsmc["score"], 25)

    def test_sell_side_index_weight_does_not_displace_informative_hypothesis(self):
        market, inst = self._pool()
        pack = build_market_signal_pack(market, inst, [], {})
        tickers = [h["ticker"] for h in pack["operator_hypotheses"]]
        self.assertEqual(len(tickers), 5)
        self.assertIn("2409", tickers)
        logics = [h["logic"] for h in pack["operator_hypotheses"]]
        # Fallback rows only fill slots after every informative row.
        seen_fallback = False
        for logic in logics:
            if logic.startswith("訊號不足"):
                seen_fallback = True
            else:
                self.assertFalse(seen_fallback, logics)

    def test_buy_leader_closing_down_is_not_bearish_distribution(self):
        market, inst = self._pool()
        pack = build_market_signal_pack(market, inst, [], {})
        hyp = next(h for h in pack["operator_hypotheses"] if h["ticker"] == "3443")
        self.assertNotEqual(hyp["next_day_bias"], "偏空")
        self.assertNotIn("調節", hyp["logic"])
        self.assertIn("外資買超", hyp["logic"])


class PromptTest(unittest.TestCase):
    def test_prompt_shows_grade_not_raw_confidence(self):
        market = {"3037.TW": _mkt(200.0, -1.0, 50_000_000, 0.6)}
        inst = {"top_buy": [_buy("3037", "欣興", 20.0)], "top_sell": []}
        text = format_signal_pack_for_prompt(build_market_signal_pack(market, inst, [], {}))
        self.assertIn("信心等級", text)
        self.assertIn("未校準", text)
        self.assertNotRegex(text, r"信心 \d+/100")
        self.assertIn("關注度", text)


CONFIRMED_MSCI = {
    "date": "2026-11-24", "kind": "msci_rebalance", "priority": 3,
    "text": "MSCI 季度調整今日收盤生效（官方公告）。",
}


class MechanicalFlowTest(unittest.TestCase):
    def test_msci_and_ftse_dates(self):
        aug = {(r["date"], r["kind"]) for r in nearby_market_events("2026-08-31", back_days=0, forward_days=0)}
        self.assertIn(("2026-08-31", "msci_rebalance_window"), aug)
        self.assertNotIn(("2026-08-31", "msci_rebalance"), aug)
        sep = {(r["date"], r["kind"]) for r in nearby_market_events("2026-09-18", back_days=0, forward_days=0)}
        self.assertIn(("2026-09-18", "ftse_rebalance"), sep)
        self.assertEqual(mechanical_flow_events("2026-10-12"), [])

    def test_rule_derived_msci_is_an_estimate_not_a_fact(self):
        """Nov reviews have landed before month-end: the rule date must not drive flow or the prompt."""
        self.assertEqual(mechanical_flow_events("2026-11-30"), [])
        row = next(r for r in nearby_market_events("2026-11-30", back_days=0, forward_days=0)
                   if r["kind"] == "msci_rebalance_window")
        self.assertTrue(row["estimated"])
        self.assertEqual(row["priority"], 1)
        self.assertNotIn("MSCI", calendar_one_liner("2026-11-30"))
        self.assertIn("（估計，非事實）MSCI", analyzer_calendar_block("2026-11-30"))

    def test_confirmed_msci_date_flags_flow_and_replaces_estimate(self):
        extras = [CONFIRMED_MSCI]
        with mock.patch.object(market_calendar, "_load_json_events", return_value=extras):
            nov = nearby_market_events("2026-11-24", back_days=0, forward_days=10)
            kinds = {(r["date"], r["kind"]) for r in nov}
            self.assertIn(("2026-11-24", "msci_rebalance"), kinds)
            self.assertFalse(any(k == "msci_rebalance_window" for _, k in kinds))
            flow = mechanical_flow_events("2026-11-24")
            self.assertEqual([r["kind"] for r in flow], ["msci_rebalance"])
            self.assertEqual(flow[0]["scope"], "market")
            self.assertEqual(mechanical_flow_events("2026-11-30"), [])

    def test_ex_div_applies_only_to_named_ticker(self):
        rows = mechanical_flow_events("2026-09-16")
        exdiv = [r for r in rows if r["kind"] == "ex_div"]
        self.assertTrue(exdiv)
        self.assertEqual(exdiv[0]["scope"], "tickers")
        self.assertTrue(mechanical_flow_for_ticker(rows, "2330.TW"))
        self.assertFalse(mechanical_flow_for_ticker(rows, "3037"))
        unknown = [{"date": "2026-09-16", "kind": "ex_div", "text": "某股除息"}]
        self.assertFalse(mechanical_flow_for_ticker(mechanical_flow_events("2026-09-16", unknown), "3037"))

    def test_rebalance_day_discounts_flow_anomaly(self):
        market = {"3037.TW": _mkt(200.0, -1.0, 50_000_000, 0.6)}
        inst = {"top_buy": [_buy("3037", "欣興", 20.0)], "top_sell": []}
        normal = build_market_signal_pack(market, inst, [], {})
        msci = build_market_signal_pack(
            market, inst, [], {}, mechanical_flow=mechanical_flow_events("2026-11-24", [CONFIRMED_MSCI])
        )
        a0 = normal["strongest_anomaly"]
        a1 = msci["strongest_anomaly"]
        self.assertLess(a1["score"], a0["score"])
        self.assertTrue(a1.get("mechanical_flow"))
        self.assertIn("被動", a1["text"])
        self.assertIn("機械性", format_signal_pack_for_prompt(msci))


if __name__ == "__main__":
    unittest.main()
