#!/usr/bin/env python3
"""Item 8: intent-language regex families, local-number broadcast ledger, official watch."""

import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.broadcast_facts import (  # noqa: E402
    attach_broadcast_facts,
    build_broadcast_facts,
    build_hero_cards,
    build_local_market_facts,
    cn_numeral_to_int,
    format_invalidation,
    mentions_stock,
    parse_volume_rank,
    stop_breached,
)
from modules.narrative_sanitize import (  # noqa: E402
    PANIC_TERMS,
    find_narrative_residue,
    sanitize_narrative_text,
    sanitize_packaging_text,
    sanitize_structured_data,
    strip_number_readings,
    summarize_hits,
)
from modules.notebooklm_generator import build_compact_notebooklm_prompt  # noqa: E402
from modules.notebooklm_source_builder import build_notebooklm_source, save_notebooklm_source  # noqa: E402

# Verbatim lines from the 2026-10-06 / 10-07 / 10-08 / 10-12 NotebookLM sources and reports.
OCTOBER_FIXTURES = [
    "外資買超第1名且帶量突破MA20，法人鎖貨跡象明顯",
    "PCB族群(華通、欣興、臻鼎-KY)：合計買超破百億，但無一進入成交量前10名，屬法人悄悄鎖貨之隱形多頭。",
    "外資買超第1名且成交量尚未過熱，法人靜默吸籌",
    "4958 臻鼎-KY：外資買超全台第一但成交量未進前 20 名 (主力低調掃貨)",
    "波段：鎖定 4958 臻鼎-KY，利用其法人靜默吸籌的優勢，在量能爆發前卡位。",
    "南亞科：外資買超111.9億全台第一，但未進入成交量前20名榜單，屬安靜吸籌。",
    "外資買超金額全台第1名，回檔時法人進行結構性鎖貨",
    "創意 (3443)：股價下跌 3.11% 但外資大買 30.4 億元，呈現低調掃貨背離",
    "我翻遍了數據，終於在「影子名單」裡找到了答案",
    "長榮：約 +29.6 億元（量增比 2.94 倍，影子卡位）",
    "創意 (外資大買/籌碼鎖貨)",
    "PCB 族群的「集體靜默換手」",
]


class NarrativeFamilyTest(unittest.TestCase):
    def test_october_variants_are_rewritten(self):
        for line in OCTOBER_FIXTURES:
            out = sanitize_narrative_text(line)
            self.assertEqual(find_narrative_residue(out), [], f"{line!r} -> {out!r}")
            for term in ("鎖貨", "掃貨", "吸籌", "靜默", "影子", "隱形多頭", "悄悄", "低調", "安靜吸"):
                self.assertNotIn(term, out, f"{line!r} -> {out!r}")

    def test_rewrites_never_inject_volume_rank(self):
        for phrase in ("機構級鎖籌碼", "靜默買進", "影子建倉", "鎖籌碼", "隱形掃貨"):
            out = sanitize_narrative_text(f"聯發科{phrase}")
            self.assertNotIn("成交量未進前20", out)
            self.assertIn("買超", out)
        # 「榜外」 is dropped, not restated: off-board facts come from the local
        # ledger (broadcast_facts.volume_say_bit), never from sanitized prose.
        self.assertNotIn("成交量未進前20", sanitize_narrative_text("聯發科榜外買超"))
        self.assertIn("買超", sanitize_narrative_text("聯發科榜外買超"))

    def test_safe_words_survive(self):
        for text in (
            "鎖定 4958 臻鼎-KY",
            "群創鎖漲停",
            "吸引買盤",
            "隱形冠軍",
            "靜默期",
            "打掃戰場",
            # Shipping / port / retail words that contain 鎖碼／鎖貨／掃貨 (長榮／陽明 news).
            "封鎖貨輪航道",
            "港口罷工封鎖碼頭",
            "連鎖貨運",
            "打掃貨架",
            "陸客掃貨",
            "輝達鎖貨源",
        ):
            self.assertEqual(sanitize_narrative_text(text), text)
            self.assertEqual(find_narrative_residue(text), [], text)

    def test_bare_intent_verbs_still_rewritten(self):
        for text in ("外資鎖碼台積電", "主力掃貨", "法人吸籌", "長榮：鎖貨"):
            out = sanitize_narrative_text(text)
            self.assertIn("買超", out, text)
            self.assertEqual(find_narrative_residue(out), [], text)

    def test_buyer_nouns_are_not_turned_into_verbs(self):
        self.assertEqual(sanitize_narrative_text("神秘買家進場"), "買盤進場")
        self.assertEqual(sanitize_narrative_text("偷偷的買方"), "買盤")
        self.assertNotIn("買超家", sanitize_narrative_text("低調買家"))
        self.assertEqual(sanitize_narrative_text("悄悄買進"), "買超")

    def test_dumping_keeps_actor_only_when_stated(self):
        self.assertEqual(sanitize_narrative_text("外資倒貨"), "外資賣超")
        self.assertEqual(sanitize_narrative_text("外資持續倒貨"), "外資持續賣超")
        # 外資 is not the one dumping here: only the intent word goes.
        self.assertEqual(
            sanitize_narrative_text("外資卻趁跌倒貨時瘋狂撿便宜"),
            "外資卻趁跌賣壓時瘋狂撿便宜",
        )

    def test_packaging_rewrites_panic_words(self):
        hits = []
        title = "戰火引爆美股血洗！台積電 ADR 暴跌 9.5% 週一台股開盤面臨斷頭危機？資金全逃去哪了！"
        out = sanitize_packaging_text(title, hits=hits)
        for term in PANIC_TERMS:
            self.assertNotIn(term, out)
        self.assertIn("9.5%", out)
        self.assertIn("面臨融資賣壓", out)
        self.assertTrue(hits)
        self.assertEqual(sanitize_packaging_text("融資斷頭潮"), "融資賣壓")
        self.assertEqual(sanitize_packaging_text("逃命潮"), "減碼潮")
        # Report text keeps factual market terms such as 融資斷頭.
        self.assertEqual(sanitize_narrative_text("融資斷頭"), "融資斷頭")

    def test_unhedged_intent_gets_possibly(self):
        out = sanitize_narrative_text("這是法人在利用「伯恩斯坦報告」釋出的利多")
        self.assertIn("法人可能在利用", out)
        self.assertEqual(sanitize_narrative_text("可能是法人在調節"), "可能是法人在調節")

    def test_hits_are_counted(self):
        hits = []
        sanitize_structured_data({"a": ["法人靜默吸籌", "主力低調掃貨"], "url": "x/影子建倉"}, hits=hits)
        summary = summarize_hits(hits)
        self.assertEqual(summary.get("靜默吸籌"), 1)
        self.assertEqual(summary.get("低調掃貨"), 1)

    def test_number_readings_stripped_but_weekday_kept(self):
        self.assertEqual(strip_number_readings("240.3 (二百四十點三) 億"), "240.3 億")
        self.assertEqual(strip_number_readings("跌 1.18% (一點一八趴)"), "跌 1.18%")
        self.assertEqual(strip_number_readings("超過 10 (十) 億元"), "超過 10 億元")
        self.assertEqual(strip_number_readings("10/12 (一) 開盤"), "10/12 (一) 開盤")
        self.assertEqual(strip_number_readings("聯發科 (2454)"), "聯發科 (2454)")

    def test_source_write_rewrites_residue(self):
        sd = {
            "date": "2026-10-07",
            "index_action": "上漲 10點（+0.05%）",
            "safe_havens_bought": ["臻鼎-KY (+40.0億)"],
            "heavyweights_dumped": ["台積電 (-100.0億)"],
            "aggressive_strategy": "鎖定 4958 臻鼎-KY，利用其法人靜默吸籌的優勢",
            "price_volume_divergence": ["臻鼎-KY：外資買超全台第一但成交量未進前 20 名 (主力低調掃貨)"],
        }
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(StringIO()):
            path = save_notebooklm_source(sd, tmp, date_str="2026-10-07")
            with open(path, encoding="utf-8") as f:
                content = f.read()
        self.assertEqual(find_narrative_residue(content), [])
        self.assertNotIn("吸籌", content)
        self.assertNotIn("掃貨", content)


def _payloads():
    institutional = {
        "data_date": "2026-10-12",
        "top_buy": [
            {"id": "3443", "name": "創意", "est_amount": 30.39, "foreign_net": 424993, "close_price": 7150.0},
            {"id": "2603", "name": "長榮", "est_amount": 29.59, "foreign_net": 12029425, "close_price": 246.0},
            {"id": "3037", "name": "欣興", "est_amount": 15.52, "foreign_net": 1222373, "close_price": 1270.0},
        ],
        "top_sell": [
            {"id": "2330", "name": "台積電", "est_amount": -240.28, "close_price": 2550.0},
            {"id": "0050", "name": "元大台灣50", "est_amount": -24.61, "close_price": 106.9},
        ],
    }
    volume = [
        {"rank": i + 1, "id": f"9{i:03d}", "name": f"量{i}", "volume": 1000 - i, "close_price": 10.0, "pct_change": 0.1}
        for i in range(20)
    ]
    volume[0] = {"rank": 1, "id": "2409", "name": "友達", "volume": 309571757, "close_price": 35.55, "pct_change": -2.87}
    market = {"3443.TW": {"symbol": "3443.TW", "price": 7150.0, "pct_change": -3.11, "ma20": 7558.0}}
    return institutional, volume, market


def _structured():
    return {
        "date": "2026-10-12",
        "index_action": "下跌 11.5點（-0.02%）",
        "safe_havens_bought": ["創意 (+30.4億)", "長榮 (+29.6億)", "欣興 (+15.5億)"],
        "heavyweights_dumped": ["台積電 (-240.3億)", "0050 元大台灣50 (-24.6億)"],
        "price_volume_divergence": [
            # Gemini prose with numbers that disagree with the local tape on purpose.
            "創意 (3443)：股價下跌 9.99% 但外資大買 99.9 億元，呈現低調掃貨背離",
            "友達 (2409)：成交量全台第一但收跌 2.87%，量大不漲顯示籌碼鬆動",
        ],
        "prediction_targets": [
            {
                "id": "2603",
                "name": "長榮",
                "direction": "多",
                "trigger": "外資買超第2名且量增比2.94倍",
                "stop_loss_price": 238,
                "stop_loss_desc": "收盤跌破今日放量起漲點 238 元",
            },
            {
                "id": "3443",
                "name": "創意",
                "direction": "多",
                "trigger": "外資買超金額全台第1名，回檔時法人進行結構性鎖貨",
                "stop_loss_price": 7558,
                "stop_loss_desc": "收盤跌破 MA20",
            },
        ],
        "focus_ranking": [
            {"ticker": "3037", "name": "欣興", "volume_confirmation": 25, "title_priority": 100, "source_text": "欣興 (+15.5億)"},
            {"ticker": "3443", "name": "創意", "volume_confirmation": 72, "title_priority": 88, "source_text": "創意 (+30.4億)"},
            {
                "ticker": "2409",
                "name": "友達",
                "volume_confirmation": 60,
                "title_priority": 75,
                "source_text": "友達 (2409)：成交量全台第一但收跌 2.87%，量大不漲顯示籌碼鬆動",
            },
            # Official tracking ranked 4th: used to be cut by the top-3 slice.
            {"ticker": "2603", "name": "長榮", "volume_confirmation": 25, "title_priority": 69, "source_text": "長榮 (+29.6億)"},
        ],
        "editorial_frame": {"label": "重大事件日", "validation_placement": "brief"},
    }


class BroadcastLedgerTest(unittest.TestCase):
    def setUp(self):
        self.inst, self.volume, self.market = _payloads()
        self.sd = attach_broadcast_facts(
            _structured(),
            stock_db={},
            institutional_data=self.inst,
            volume_data=self.volume,
            market_data=self.market,
        )
        self.facts = self.sd["broadcast_facts"]
        self.cards = {c["ticker"]: c for c in self.facts["heroes"]}

    def test_local_numbers_win_over_llm_text(self):
        card = self.cards["3443"]
        self.assertEqual(card["foreign_amount"], 30.39)
        self.assertEqual(card["pct_change"], -3.11)
        self.assertEqual(card["number_source"]["foreign_amount"], "local")
        self.assertEqual(card["llm_numbers"].get("pct_change"), -9.99)
        self.assertNotIn("99.9", card["say_line"])
        self.assertNotIn("9.99", card["say_line"])
        # Verified (full top-20 list known), spoken with item 7's caveat.
        self.assertIn("未進股數量榜（不代表量縮）", card["say_line"])
        self.assertNotIn("成交量未進前 20", card["say_line"])

    def test_ledger_is_stored_for_rebuilds(self):
        ledger = self.sd["local_market_facts"]
        self.assertTrue(ledger["volume_complete"])
        self.assertEqual(ledger["stocks"]["2409"]["volume_rank"], 1)
        rebuilt = build_broadcast_facts(self.sd, stock_db={})  # no payloads passed
        self.assertEqual({c["ticker"]: c["foreign_amount"] for c in rebuilt["heroes"]}["3443"], 30.39)

    def test_every_prediction_target_is_official(self):
        official = [c["ticker"] for c in self.facts["official_watch"]]
        self.assertEqual(official, ["2603", "3443"])
        self.assertEqual(self.facts["heroes"][0]["ticker"], "2603")
        # 2603 is off-rank with thin volume confirmation: still official, only cautioned.
        self.assertEqual(self.cards["2603"]["admission"], "official")
        self.assertTrue(self.cards["2603"]["caution"])

    def test_invalidation_keeps_price_and_distance(self):
        self.assertIn("7558", self.cards["3443"]["invalidation"])
        self.assertIn("距今收盤", self.cards["3443"]["invalidation"])
        self.assertIn("已觸及失效價", self.cards["3443"]["invalidation"])
        self.assertEqual(
            format_invalidation("收盤跌破今日放量起漲點 238 元", 238, 246, "多"),
            "收盤跌破今日放量起漲點 238 元（距今收盤 -3.3%）",
        )
        self.assertEqual(format_invalidation("收盤跌破 MA20", 7558), "收盤跌破 MA20（7558 元）")

    def test_director_card_lists_all_official(self):
        prompt = build_compact_notebooklm_prompt(self.sd, date_str="2026-10-12")
        self.assertIn("2603 長榮；失效", prompt)
        self.assertIn("3443 創意；失效", prompt)
        self.assertIn("收尾盯 2603 長榮、3443 創意", prompt)
        source = build_notebooklm_source(self.sd, date_str="2026-10-12")
        self.assertIn("### 主角 1：2603 長榮", source)
        self.assertIn("- 收盤：7150（MA20 7558，距 MA20 -5.4%）", source)
        self.assertNotIn("結構性鎖貨", source)

    def test_legacy_structured_data_without_ledger(self):
        sd = _structured()
        facts = build_broadcast_facts(sd, stock_db={})
        card = {c["ticker"]: c for c in facts["heroes"]}["3443"]
        self.assertEqual(card["number_source"]["foreign_amount"], "local_list")
        self.assertAlmostEqual(card["foreign_amount"], 30.4)
        self.assertIsNone(card["pct_change"])  # only LLM prose had a % → not quoted
        self.assertEqual(card["number_source"]["pct_change"], "llm_text")


class ShortSideAndPriceSourceTest(unittest.TestCase):
    def test_short_stop_sits_above_the_close(self):
        self.assertTrue(stop_breached("空", 255, 256))
        self.assertFalse(stop_breached("空", 255, 250))
        self.assertTrue(stop_breached("多", 238, 237))
        self.assertFalse(stop_breached("多", 238, 246))
        self.assertFalse(stop_breached("", 238, 200))
        self.assertFalse(stop_breached("空", None, 250))
        self.assertEqual(format_invalidation("", 255, 246, "空"), "收盤站上 255 元（距今收盤 +3.7%）")
        self.assertIn("已觸及失效價", format_invalidation("", 240, 246, "空"))
        self.assertEqual(format_invalidation("", 238, 246, "多"), "收盤跌破 238 元（距今收盤 -3.3%）")

    def test_direction_parsing_is_the_validator_rule(self):
        """偏空 is a short, 多空交錯 is no call: broadcast and /2 follow prediction_validator.direction_sign."""
        from modules.broadcast_facts import is_short_direction
        from modules.prediction_validator import direction_sign
        from modules.youtube_content_generator import _is_short

        for direction in ("空", "偏空", "多", "偏多", "多空交錯", "觀察"):
            sign = direction_sign(direction)
            self.assertEqual(is_short_direction(direction), sign < 0, direction)
            self.assertEqual(_is_short(direction), sign < 0, direction)
        self.assertTrue(stop_breached("偏空", 255, 256))
        self.assertFalse(stop_breached("多空交錯", 238, 200))
        self.assertEqual(format_invalidation("", 255, 246, "偏空"), "收盤站上 255 元（距今收盤 +3.7%）")

    def _inst(self):
        return {
            "data_date": "2026-10-12",
            "top_buy": [
                # Fetcher-shaped rows: apply_amount_estimates stamps price_date.
                {"id": "2618", "name": "長榮航", "est_amount": 5.2, "foreign_net": 13000000, "close_price": 40.0, "price_date": "2026-10-12"},
                {"id": "2610", "name": "華航", "est_amount": 3.1, "foreign_net": 10000000, "close_price": 31.0, "price_date": "2026-10-08"},
                {"id": "2606", "name": "裕民", "est_amount": 2.0, "foreign_net": 4000000, "close_price": 50.0},
            ],
            "top_sell": [],
        }

    def _volume(self):
        return [
            {"rank": i + 1, "id": f"9{i:03d}", "name": f"量{i}", "volume": 1000 - i, "close_price": 10.0, "pct_change": 0.1}
            for i in range(20)
        ]

    def test_only_same_day_institutional_price_is_spoken_as_close(self):
        ledger = build_local_market_facts(self._inst(), self._volume(), {})
        stocks = ledger["stocks"]
        self.assertEqual(stocks["2618"]["close"], 40.0)
        self.assertEqual(stocks["2618"]["close_source"], "institutional_price_map")
        self.assertNotIn("close", stocks["2610"])  # stale snapshot: amount only
        self.assertEqual(stocks["2610"]["est_price"], 31.0)
        self.assertEqual(stocks["2610"]["est_price_date"], "2026-10-08")
        self.assertNotIn("close", stocks["2606"])  # date unknown
        self.assertEqual(stocks["2606"]["est_price"], 50.0)

    def test_hero_card_gets_close_and_distance_from_same_day_price(self):
        sd = {
            "prediction_targets": [
                {"id": "2618", "name": "長榮航", "direction": "多", "stop_loss_price": 38, "stop_loss_desc": "收盤跌破 38 元"}
            ]
        }
        ledger = build_local_market_facts(self._inst(), self._volume(), {})
        card = build_hero_cards(sd, stock_db={}, local_facts=ledger)[0]
        self.assertEqual(card["close"], 40.0)
        self.assertIn("收盤 40", card["say_line"])
        self.assertIn("距今收盤 -5.0%", card["invalidation"])

    def test_off_rank_only_for_known_twse_tickers(self):
        market = {
            "6488.TWO": {"symbol": "6488.TWO", "price": 600.0, "pct_change": 1.2},
            "2368.TW": {"symbol": "2368.TW", "price": 500.0, "pct_change": 2.0},
        }
        ledger = build_local_market_facts(self._inst(), self._volume(), market)
        self.assertEqual(ledger["stocks"]["2618"]["listing"], "twse")
        self.assertEqual(ledger["stocks"]["6488"]["listing"], "tpex")
        sd = {
            "prediction_targets": [
                {"id": "2618", "name": "長榮航", "direction": "多"},
                {"id": "6488", "name": "環球晶", "direction": "多"},
                {"id": "2368", "name": "金像電", "direction": "多"},
            ]
        }
        cards = {c["ticker"]: c for c in build_hero_cards(sd, stock_db={}, local_facts=ledger)}
        self.assertTrue(cards["2618"]["off_rank"])  # T86 row: 上市, absent from the full list
        self.assertIn("未進股數量榜（不代表量縮）", cards["2618"]["say_line"])
        for ticker in ("6488", "2368"):  # 上櫃 / listing unknown: not a verified fact
            self.assertFalse(cards[ticker]["off_rank"], ticker)
            self.assertNotIn("成交量未進前", cards[ticker]["say_line"], ticker)
            self.assertNotIn("未進股數量榜", cards[ticker]["say_line"], ticker)

    def test_finmind_rows_do_not_prove_twse_listing(self):
        inst = dict(self._inst(), source="finmind")
        ledger = build_local_market_facts(inst, self._volume(), {})
        self.assertNotIn("listing", ledger["stocks"]["2618"])

    def test_name_only_seed_is_never_off_rank(self):
        sd = {"focus_ranking": [{"name": "PCB族群", "title_priority": 90, "source_text": "PCB族群 合計買超 100 億"}]}
        ledger = build_local_market_facts(self._inst(), self._volume(), {})
        cards = build_hero_cards(sd, stock_db={}, local_facts=ledger)
        self.assertTrue(all(not c["off_rank"] for c in cards))

    def test_target_and_ranking_row_are_one_card(self):
        sd = {
            "prediction_targets": [{"id": "2618", "name": "長榮航", "direction": "多"}],
            # Same stock without a ticker in the ranking: must not be a second card.
            "focus_ranking": [{"name": "長榮航", "title_priority": 90, "source_text": "長榮航 外資買超 5.2 億"}],
        }
        ledger = build_local_market_facts(self._inst(), self._volume(), {})
        cards = build_hero_cards(sd, stock_db={}, local_facts=ledger)
        self.assertEqual([c["ticker"] for c in cards], ["2618"])


class ParsingHelpersTest(unittest.TestCase):
    def test_volume_rank_needs_volume_context(self):
        self.assertEqual(parse_volume_rank("成交量全台第一但收跌 2.87%"), 1)
        self.assertEqual(parse_volume_rank("成交量高達 25 萬張居第 4 名"), 4)
        self.assertEqual(parse_volume_rank("成交量冠軍"), 1)
        self.assertIsNone(parse_volume_rank("外資買超第2名且量增比2.94倍"))
        self.assertEqual(cn_numeral_to_int("十二"), 12)
        self.assertEqual(cn_numeral_to_int("二十"), 20)

    def test_name_mentions_do_not_match_longer_names(self):
        known = {"南亞", "南亞科"}
        self.assertFalse(mentions_stock("2408 南亞科 外資買超", "1303", "南亞", known))
        self.assertTrue(mentions_stock("南亞跌停，南亞科大漲", "1303", "南亞", known))
        self.assertTrue(mentions_stock("1303 外資賣超", "1303", "南亞", known))

    def test_local_ledger_tolerates_bad_input(self):
        ledger = build_local_market_facts(None, [{"bad": 1}], {"^TWII": {"price": 1}})
        self.assertEqual(ledger["stocks"], {})
        self.assertFalse(ledger["volume_complete"])


if __name__ == "__main__":
    unittest.main()
