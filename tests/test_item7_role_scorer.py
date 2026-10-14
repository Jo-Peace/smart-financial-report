#!/usr/bin/env python3
"""Item 7: role_scorer wiring (volume / vol_ratio / price stats) and title_priority shaping."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.local_knowledge_compounder import LocalKnowledgeCompounder  # noqa: E402
from modules.compounding_loop import build_weekly_compounding_brief  # noqa: E402
from modules.data_fetcher import realized_vol_fields  # noqa: E402
from modules.role_scorer import (  # noqa: E402
    HIT_RATE_MIN_SAMPLES,
    HIT_RATE_MULT_MAX,
    attach_role_scores_to_structured,
    build_role_score_inputs,
    consecutive_price_stats,
    load_price_stats,
    normalize_ticker,
    rank_focus_stocks,
    ranking_is_reproducible,
    score_elasticity,
    score_foreign_buy_strength,
    score_ticker,
    score_volume_confirmation,
)


STOCK_DB = {
    "3037": {"name": "欣興", "sector": "PCB 載板", "market_role": {"base_role": "elastic_attack"}},
    "2409": {"name": "友達", "sector": "面板", "market_role": {"base_role": "theme_confirmation"}},
    "3443": {"name": "創意", "sector": "ASIC", "market_role": {"base_role": "theme_confirmation"}},
    "2330": {"name": "台積電", "sector": "晶圓代工", "market_role": {"base_role": "direction_indicator"}},
}


def _structured(date="2026-10-08"):
    return {
        "date": date,
        "safe_havens_bought": ["欣興 (+23.9億)", "創意 (+11.0億)"],
        "heavyweights_dumped": ["台積電 (-240.3億)"],
        "price_volume_divergence": ["友達：成交量全台第二，外資未進榜"],
        "prediction_targets": [],
    }


VOLUME_BOARD = [
    {"rank": 1, "id": "00406A", "name": "ETF", "volume": 280_000_000, "close_price": 10.2, "pct_change": -0.8},
    {"rank": 2, "id": "2409", "name": "友達", "volume": 250_000_000, "close_price": 14.0, "pct_change": -3.5},
]
MARKET = {
    "3037.TW": {"price": 200.0, "pct_change": -2.7, "volume": 5_000_000, "vol_ratio": 0.59},
    "2330.TW": {"price": 2550.0, "pct_change": -1.3, "volume": 22_000_000, "vol_ratio": 1.2},
}


class VolumeConfirmationTest(unittest.TestCase):
    def test_volume_rank_one_beats_off_board(self):
        top, _ = score_volume_confirmation(volume_rank=1)
        off, ev = score_volume_confirmation(in_divergence=True, divergence_text="未進成交量前20", buy_rank=1)
        self.assertGreater(top, off)
        self.assertEqual(off, 50)  # unknown, not thin and not a bonus
        self.assertIn("volume_unknown", ev)
        self.assertIn("flag=in_divergence_list", ev)

    def test_low_own_volume_is_low_confirmation(self):
        low, ev = score_volume_confirmation(vol_ratio=0.59, buy_rank=3)
        high, _ = score_volume_confirmation(vol_ratio=2.5)
        self.assertLess(low, 50)
        self.assertGreater(high, 80)
        self.assertIn("flag=shadow_low_volume_buy", ev)

    def test_pool_rank_does_not_override_own_volume(self):
        """A big-cap's value rank is size, not confirmation, when its own volume shrank."""
        big_but_thin, ev = score_volume_confirmation(volume_rank=2, vol_ratio=0.59, buy_rank=1)
        self.assertLess(big_but_thin, 50)
        self.assertIn("turnover_rank=2(context_only)", ev)
        board_only, _ = score_volume_confirmation(volume_rank=2)
        self.assertGreater(board_only, 80)

    def test_inputs_reach_the_ranking(self):
        inputs = build_role_score_inputs(
            _structured(), STOCK_DB, market_data=MARKET, volume_data=VOLUME_BOARD, analytics_db_path="/nonexistent"
        )
        # Same pool as the signal pack: off-board 2330 (561 億) outranks board 2409 (35 億).
        self.assertEqual(inputs["volume"]["2330"]["turnover_rank"], 1)
        self.assertFalse(inputs["volume"]["2330"]["on_share_board"])
        self.assertEqual(inputs["volume"]["2409"]["turnover_rank"], 2)
        self.assertEqual(inputs["market"]["3037"]["vol_ratio"], 0.59)
        out = attach_role_scores_to_structured(_structured(), STOCK_DB, limit=5, role_inputs=inputs)
        self.assertEqual(out["role_score_inputs"], inputs)
        rows = {r["ticker"]: r for r in out["focus_ranking"]}
        self.assertGreater(rows["2409"]["volume_confirmation"], rows["3037"]["volume_confirmation"])
        self.assertIn("shadow_low_volume_buy", rows["3037"]["volume_flags"])
        # rank_focus_stocks reads persisted inputs (regen path) and agrees.
        again = rank_focus_stocks(out, STOCK_DB, limit=5)
        self.assertEqual([r["ticker"] for r in again], [r["ticker"] for r in out["focus_ranking"]])

    def test_inputs_use_the_market_wide_value_rank_when_the_tape_is_known(self):
        """Same 成交值 basis as the signal pack and the prompt's 成交值前20 table."""
        value_ranking = {"stocks": [{"rank": 1, "id": "2330", "trade_value_yi": 561.0},
                                    {"rank": 7, "id": "2409", "trade_value_yi": 35.0}], "etfs": []}
        inputs = build_role_score_inputs(
            _structured(), STOCK_DB, market_data=MARKET, volume_data=VOLUME_BOARD,
            analytics_db_path="/nonexistent", value_ranking=value_ranking,
        )
        self.assertEqual(inputs["volume"]["2409"]["turnover_rank"], 7)  # pool rank would be 2
        self.assertEqual(inputs["volume"]["2409"]["rank_basis"], "market")
        self.assertEqual(inputs["volume"]["2330"]["turnover_rank"], 1)

    def test_market_rank_map_never_calls_a_top_board_name_thin(self):
        # Pool basis keeps its steep decay; market basis (~1,000 names) is gentler.
        self.assertEqual(score_volume_confirmation(volume_rank=31)[0], 30)
        self.assertEqual(score_volume_confirmation(volume_rank=1, volume_rank_basis="market")[0], 95)
        self.assertGreater(score_volume_confirmation(volume_rank=20, volume_rank_basis="market")[0], 60)
        for rank in (21, 31, 60, 150):
            score, ev = score_volume_confirmation(volume_rank=rank, volume_rank_basis="market")
            self.assertGreaterEqual(score, 50, rank)
            self.assertIn(f"turnover_rank={rank}(market)", ev)
        # Own volume still decides when it is known.
        thin, _ = score_volume_confirmation(volume_rank=3, vol_ratio=0.59, volume_rank_basis="market")
        self.assertLess(thin, 50)

    def test_share_board_name_with_a_market_rank_past_20_is_not_thin(self):
        """1802 台玻: #2 by shares, 102.5 億, market 成交值 #31, no vol_ratio (board-only name)."""
        stock_db = dict(STOCK_DB, **{"1802": {"name": "台玻", "sector": "玻璃", "market_role": {"base_role": "theme_confirmation"}}})
        structured = dict(_structured(), price_volume_divergence=["台玻：成交股數全台第二，量大卻跌"])
        board = [
            {"rank": 1, "id": "1303", "name": "南亞", "volume": 300_000_000, "close_price": 75.9, "pct_change": 2.0,
             "value_rank": 5, "value_rank_basis": "stocks_ex_etf", "trade_value_yi": 227.7},
            {"rank": 2, "id": "1802", "name": "台玻", "volume": 250_000_000, "close_price": 41.0, "pct_change": -1.0,
             "value_rank": 31, "value_rank_basis": "stocks_ex_etf", "trade_value_yi": 102.5},
        ]
        value_ranking = {"stocks": [{"rank": 1, "id": "2330", "trade_value_yi": 573.2},
                                    {"rank": 5, "id": "1303", "trade_value_yi": 227.7}], "etfs": []}
        inputs = build_role_score_inputs(
            structured, stock_db, market_data=MARKET, volume_data=board,
            analytics_db_path="/nonexistent", value_ranking=value_ranking,
        )
        self.assertEqual(inputs["volume"]["1802"]["turnover_rank"], 31)
        self.assertEqual(inputs["volume"]["1802"]["rank_basis"], "market")
        out = attach_role_scores_to_structured(structured, stock_db, limit=10, role_inputs=inputs)
        rows = {r["ticker"]: r for r in out["focus_ranking"]}
        self.assertIn("1802", rows)
        # Was 30 (pool decay applied to a market rank) → read as thin volume by broadcast_facts.
        self.assertGreaterEqual(rows["1802"]["volume_confirmation"], 50)
        # Without the tape the pool basis is unchanged (pool rank 3 here).
        pool_inputs = build_role_score_inputs(
            structured, stock_db, market_data=MARKET, volume_data=board, analytics_db_path="/nonexistent"
        )
        pool_rows = {r["ticker"]: r for r in attach_role_scores_to_structured(
            structured, stock_db, limit=10, role_inputs=pool_inputs)["focus_ranking"]}
        self.assertEqual(pool_inputs["volume"]["1802"]["rank_basis"], "pool")
        self.assertGreater(pool_rows["1802"]["volume_confirmation"], 50)


class ElasticityTest(unittest.TestCase):
    def test_prior_only_when_no_history(self):
        score, ev = score_elasticity("3037", STOCK_DB["3037"])
        self.assertIn("basis=category_prior(no_local_price_history)", ev)
        self.assertGreaterEqual(score, 78)

    def test_realized_stats_override_category(self):
        calm = {"vol_daily_pct": 1.1, "beta": 0.6, "n": 20, "beta_n": 20, "basis": "consecutive_20d"}
        wild = {"vol_daily_pct": 5.5, "beta": 1.8, "n": 20, "beta_n": 20, "basis": "consecutive_20d"}
        calm_score, calm_ev = score_elasticity("3037", STOCK_DB["3037"], price_stats=calm)
        wild_score, _ = score_elasticity("3443", STOCK_DB["3443"], price_stats=wild)
        self.assertLess(calm_score, 50)
        self.assertGreater(wild_score, calm_score)
        self.assertTrue(any(e.startswith("realized_vol_20d=") and "consecutive" in e for e in calm_ev))

    def test_sparse_snapshot_stats_are_labelled_and_capped(self):
        sparse = {"vol_daily_pct": 1.1, "beta": 0.6, "n": 30, "beta_n": 30}
        consecutive = dict(sparse, n=20, beta_n=20, basis="consecutive_20d")
        sparse_score, ev = score_elasticity("3037", STOCK_DB["3037"], price_stats=sparse)
        consecutive_score, _ = score_elasticity("3037", STOCK_DB["3037"], price_stats=consecutive)
        self.assertTrue(any("sparse snapshot days" in e for e in ev))
        self.assertTrue(any("trust=0.50" in e for e in ev))
        self.assertGreater(sparse_score, consecutive_score)  # shrunk halfway to the 載板 prior

    def test_few_samples_say_so_instead_of_no_history(self):
        _, ev = score_elasticity("1301", {"name": "台塑"}, price_stats={"vol_daily_pct": 2.0, "n": 5})
        self.assertIn("basis=category_prior(insufficient_local_samples n=5<8)", ev)
        self.assertNotIn("basis=category_prior(no_local_price_history)", ev)

    def test_consecutive_stats_from_market_payload(self):
        days = [f"2026-09-{d:02d}" for d in range(1, 23)]
        index_closes = [100.0]
        stock_closes = [50.0]
        for i in range(1, 22):
            step = 0.01 if i % 2 else -0.008
            index_closes.append(index_closes[-1] * (1 + step))
            stock_closes.append(stock_closes[-1] * (1 + 2 * step))
        market = {
            "^TWII": {"price": index_closes[-1], **realized_vol_fields(index_closes, days)},
            "3037.TW": {"price": stock_closes[-1], "volume": 1, **realized_vol_fields(stock_closes, days)},
            "2409.TW": {"price": 14.0, "volume": 1},  # no series: falls back to snapshots / prior
        }
        self.assertNotIn("2026-09-22", market["3037.TW"]["returns_20d"])  # latest session excluded
        stats = consecutive_price_stats(market)
        self.assertEqual(set(stats), {"3037"})
        self.assertEqual(stats["3037"]["n"], 20)
        self.assertEqual(stats["3037"]["basis"], "consecutive_20d")
        self.assertAlmostEqual(stats["3037"]["beta"], 2.0, delta=0.05)
        inputs = build_role_score_inputs(
            {"date": "2026-09-22", "safe_havens_bought": ["欣興 (+5.0億)", "友達 (+1.0億)"]},
            STOCK_DB, market_data=market, analytics_db_path="/nonexistent",
        )
        self.assertEqual(inputs["price_stats"]["3037"]["basis"], "consecutive_20d")
        self.assertNotIn("2409", inputs["price_stats"])

    def test_price_stats_point_in_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "analytics.db")
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE market_snapshots (snapshot_date TEXT, ticker TEXT, pct_change REAL)")
            rows = []
            for i in range(12):
                day = f"2026-09-{i + 1:02d}"
                idx = (-1) ** i * 0.5
                rows.append((day, "^TWII", idx))
                rows.append((day, "3037", idx * 2))
            rows.append(("2026-09-20", "3037", 9.9))  # after as_of
            conn.executemany("INSERT INTO market_snapshots VALUES (?,?,?)", rows)
            conn.commit()
            conn.close()
            stats = load_price_stats(["3037"], "2026-09-12", db_path=db)["3037"]
            self.assertEqual(stats["n"], 11)  # strictly before as_of
            self.assertEqual(stats["to"], "2026-09-11")
            self.assertAlmostEqual(stats["beta"], 2.0, places=3)
            self.assertEqual(load_price_stats(["3037"], "2026-09-12", db_path=os.path.join(tmp, "nope.db")), {})


class ForeignStrengthTest(unittest.TestCase):
    def test_intensity_lifts_concentrated_small_cap(self):
        mega, _ = score_foreign_buy_strength(buy_rank=1, amount_yi=111.9, intensity=0.03)
        small, ev = score_foreign_buy_strength(buy_rank=5, amount_yi=11.4, intensity=0.25)
        self.assertGreater(small, mega)
        self.assertTrue(any("flow_intensity" in e for e in ev))

    def test_mechanical_flow_shrinks_toward_neutral(self):
        normal, _ = score_foreign_buy_strength(buy_rank=1, amount_yi=50)
        msci, ev = score_foreign_buy_strength(buy_rank=1, amount_yi=50, mechanical_flow=True)
        self.assertLess(msci, normal)
        self.assertIn("mechanical_flow_day_shrink", ev)


class TitlePriorityTest(unittest.TestCase):
    def test_hit_rate_needs_samples_and_is_bounded(self):
        entry = {"market_role": {"base_role": "elastic_attack"},
                 "tracking": {"prediction_count": 4, "hit_count": 4, "hit_rate": 1.0}}
        few = score_ticker("3037", entry, buy_rank=3, amount_yi=15)
        self.assertTrue(any("skip samples=4" in e for e in few["score_evidence"]["title_priority"]))
        entry["tracking"] = {"prediction_count": 10, "hit_count": 10, "hit_rate": 1.0}
        many = score_ticker("3037", entry, buy_rank=3, amount_yi=15)
        note = next(e for e in many["score_evidence"]["title_priority"] if e.startswith("hit_rate_track samples=10"))
        mult = float(note.split("mult=")[1].split()[0])
        self.assertLessEqual(mult, HIT_RATE_MULT_MAX)
        self.assertLess(many["title_priority"], 100)

    def test_recent_protagonist_decays_and_order_is_deterministic(self):
        sd = {
            "date": "2026-10-12",
            "safe_havens_bought": ["欣興 (+15.5億)", "創意 (+15.5億)"],
            "heavyweights_dumped": [],
        }
        db = {
            "3037": {"name": "欣興", "market_role": {"base_role": "theme_confirmation"}},
            "3443": {"name": "創意", "market_role": {"base_role": "theme_confirmation"}},
        }
        base = rank_focus_stocks(sd, db, limit=2, role_inputs={})
        self.assertEqual(base[0]["ticker"], "3037")  # buy rank 1
        decayed = rank_focus_stocks(sd, db, limit=2, role_inputs={"recent_leads": ["3037", "3037", "3037"]})
        self.assertEqual(decayed[0]["ticker"], "3443")
        self.assertTrue(any("recent_lead_decay" in e for e in decayed[1]["score_evidence"]["title_priority"]))
        again = rank_focus_stocks(sd, db, limit=2, role_inputs={"recent_leads": ["3037", "3037", "3037"]})
        self.assertEqual([r["ticker"] for r in again], [r["ticker"] for r in decayed])

    def test_normalize_two(self):
        self.assertEqual(normalize_ticker("6488.TWO"), "6488")

    def test_regen_ranks_with_the_roles_scored_during_1(self):
        """After /1 the compounder rewrites stock_db roles; regen must still rank like /1."""
        sd = {
            "date": "2026-10-08",
            "safe_havens_bought": ["緯創 (+20.0億)", "南亞科 (+19.0億)"],
            "heavyweights_dumped": [],
        }
        before = {
            "3231": {"name": "緯創", "market_role": {"base_role": "elastic_attack", "label": "攻擊"}},
            "2408": {"name": "南亞科", "market_role": {"base_role": "theme_confirmation"}},
        }
        after = {
            "3231": {"name": "緯創", "market_role": {"base_role": "theme_confirmation", "label": "主題"}},
            "2408": {"name": "南亞科", "market_role": {"base_role": "elastic_attack", "label": "攻擊"}},
        }
        inputs = build_role_score_inputs(sd, before, analytics_db_path="/nonexistent")
        self.assertEqual(inputs["base_roles"]["3231"], "elastic_attack")
        live = attach_role_scores_to_structured(sd, before, limit=2, role_inputs=inputs)
        self.assertTrue(ranking_is_reproducible(live))
        regen = attach_role_scores_to_structured(live, after, limit=2)
        self.assertEqual(
            [(r["ticker"], r["base_role"]) for r in regen["focus_ranking"]],
            [(r["ticker"], r["base_role"]) for r in live["focus_ranking"]],
        )
        self.assertEqual(live["focus_ranking"][0]["ticker"], "3231")
        # Without pinned roles the new stock_db would flip the protagonist.
        drifted = rank_focus_stocks(sd, after, limit=2, role_inputs={})
        self.assertEqual(drifted[0]["ticker"], "2408")
        self.assertFalse(ranking_is_reproducible(sd))

    def test_weekly_brief_headings_use_hit_rate_constants(self):
        from modules import analytics_store

        real_store = analytics_store.AnalyticsStore
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            analytics_store, "AnalyticsStore", side_effect=lambda *a, **k: real_store(os.path.join(tmp, "a.db"))
        ):  # never open the real data/analytics.db from a test
            brief = build_weekly_compounding_brief(log_path="/nonexistent", stock_db_path="/nonexistent")
        self.assertNotIn("n≥3", brief)
        self.assertIn(f"n≥{HIT_RATE_MIN_SAMPLES} 且命中率 ≥65%", brief)
        self.assertIn(f"×{HIT_RATE_MULT_MAX:.2f}", brief)


class CompounderRoleTest(unittest.TestCase):
    def _run(self, structured, stock_db, flows=None):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        tmp = holder.name
        os.makedirs(os.path.join(tmp, "data"), exist_ok=True)
        with open(os.path.join(tmp, "data", "stock_db.json"), "w", encoding="utf-8") as f:
            json.dump(stock_db, f, ensure_ascii=False)
        if flows:
            conn = sqlite3.connect(os.path.join(tmp, "data", "analytics.db"))
            conn.execute(
                "CREATE TABLE institutional_flows (flow_date TEXT, ticker TEXT, side TEXT, rank INTEGER)"
            )
            conn.executemany("INSERT INTO institutional_flows VALUES (?,?,?,?)", flows)
            conn.commit()
            conn.close()
        LocalKnowledgeCompounder(base_dir=tmp)._update_stock_roles(structured, structured["date"])
        with open(os.path.join(tmp, "data", "stock_db.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_name_only_lists_set_flow_state_and_evidence_is_honest(self):
        db = {
            "2330": {"name": "台積電", "sector": "晶圓代工"},
            "3231": {"name": "緯創", "sector": "伺服器", "supply_chain_role": "AI 伺服器 基板 組裝"},
            "1303": {"name": "南亞", "sector": "塑化", "supply_chain_role": "載板 PCB 材料"},
            "2382": {"name": "廣達", "sector": "伺服器", "supply_chain_role": "AI 伺服器"},
        }
        sd = {
            "date": "2026-10-12",
            "safe_havens_bought": ["緯創 (+20.0億)", "南亞 (+5.0億)", "廣達 (+3.0億)"],
            "heavyweights_dumped": ["台積電 (-240.3億)"],
        }
        out = self._run(sd, db)
        tsmc = out["2330"]["market_role"]
        self.assertEqual(tsmc["latest_flow_state"], "foreign_sell_risk")
        self.assertEqual(tsmc["role_source"], "structural:mega_cap_index_weight")
        wistron = out["3231"]["market_role"]
        self.assertEqual(wistron["base_role"], "theme_confirmation")  # no override; 基板 rule
        self.assertTrue(any("matched 基板" in e for e in wistron["evidence"]))
        self.assertEqual(out["1303"]["market_role"]["base_role"], "elastic_attack")
        quanta = out["2382"]["market_role"]
        self.assertEqual(quanta["role_source"], "default")
        self.assertTrue(any("no role marker matched" in e for e in quanta["evidence"]))

    def test_repeated_top_sell_is_data_backed_risk_warning(self):
        db = {"2303": {"name": "聯電", "sector": "晶圓代工", "supply_chain_role": "成熟製程"}}
        flows = [(f"2026-10-0{d}", "2303", "sell", 2) for d in range(1, 5)]
        sd = {"date": "2026-10-12", "heavyweights_dumped": ["聯電 (-20.0億)"], "safe_havens_bought": []}
        role = self._run(sd, db, flows)["2303"]["market_role"]
        self.assertEqual(role["base_role"], "risk_warning")
        self.assertEqual(role["role_source"], "data:institutional_flows")
        self.assertTrue(any("prior=risk_warning" in e for e in role["score_evidence"]["elasticity"]))
        self.assertTrue(any("foreign_sell_top5=5/10" in e for e in role["evidence"]))


if __name__ == "__main__":
    unittest.main()
