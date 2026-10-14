#!/usr/bin/env python3
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.broadcast_facts import (  # noqa: E402
    build_broadcast_facts,
    is_off_rank,
    parse_amount_yi,
)
from modules.notebooklm_generator import build_compact_notebooklm_prompt  # noqa: E402
from modules.notebooklm_source_builder import build_notebooklm_source  # noqa: E402


class BroadcastFactsTest(unittest.TestCase):
    def setUp(self):
        self.sd = {
            "date": "2026-08-17",
            "index_action": "上漲 46.3點（+0.10%）",
            "safe_havens_bought": ["欣興 (+231.9億)", "南亞科 (+62.7億)"],
            "heavyweights_dumped": ["力積電 (-50.8億)"],
            "price_volume_divergence": [
                "欣興：外資大買 231.9 億元為市場第一，但成交量未進前 20 名",
                "力積電：成交量高達 25 萬張居第 4 名，但股價跌 4.85%，外資大砍 50.8 億",
            ],
            "prediction_targets": [
                {
                    "id": "3037",
                    "name": "欣興",
                    "direction": "多",
                    "trigger": "外資買超 231 億",
                    "stop_loss_desc": "收盤跌破 1026 元 (MA5)",
                    "stop_loss_price": 1026,
                }
            ],
            "focus_ranking": [
                {
                    "ticker": "3037",
                    "name": "欣興",
                    "role_label": "彈性攻擊股",
                    "volume_confirmation": 72,
                    "title_priority": 100,
                    "source_text": "欣興 (+231.9億)",
                },
                {
                    "ticker": "6770",
                    "name": "力積電",
                    "role_label": "彈性攻擊股",
                    "volume_confirmation": 60,
                    "title_priority": 62,
                    "source_text": "力積電：成交量高達 25 萬張居第 4 名，但股價跌 4.85%，外資大砍 50.8 億",
                },
            ],
            "editorial_frame": {"label": "重大事件日", "validation_placement": "brief", "confidence": 76},
            "horizon_reliability": {"one_liner": "20D 命中率 57.9% 優於短線窗。"},
        }

    def test_parse_helpers(self):
        self.assertEqual(parse_amount_yi("欣興 (+231.9億)"), 231.9)
        self.assertTrue(is_off_rank("成交量未進前 20 名"))

    def test_hook_is_not_truncated(self):
        facts = build_broadcast_facts(self.sd, stock_db={})
        hook = facts["opening"]["hook_line"]
        self.assertIn("231.9", hook)
        self.assertIn("50.8", hook)
        self.assertNotIn("…", hook)
        self.assertEqual(facts["heroes"][0]["admission"], "official")

    def test_source_and_prompt_hide_internal_scores(self):
        source = build_notebooklm_source(self.sd, date_str="2026-08-17")
        prompt = build_compact_notebooklm_prompt(self.sd, date_str="2026-08-17")
        self.assertIn("今日必引數字", source)
        self.assertIn("正式追蹤", source)
        self.assertNotIn("title_priority=100", source)
        self.assertNotIn("P100/E", prompt)
        self.assertIn("231.9", prompt)
        self.assertIn("-50.8", prompt)
        self.assertNotIn("Project skill guidance", prompt)
        self.assertIn("不要說鎖籌碼", prompt)

    def test_sell_day_clears_stale_buy_streak(self):
        facts = build_broadcast_facts(
            self.sd,
            stock_db={
                "6770": {
                    "tracking": {
                        "consecutive_foreign_buy_days": 2,
                        "last_foreign_buy_date": "2026-08-14",
                    }
                }
            },
        )
        lijing = next(h for h in facts["heroes"] if h["ticker"] == "6770")
        self.assertEqual(lijing["foreign_streak_days"], 0)
        source = build_notebooklm_source(self.sd, date_str="2026-08-17")
        self.assertIn("今日賣超（連續買超中斷", source)

    def test_source_strips_lockchip_claims(self):
        dirty = dict(self.sd)
        dirty["price_volume_divergence"] = [
            "欣興：外資大買 231.9 億卻未進成交量前 20 名，顯示機構級鎖籌碼",
            "外資進行靜默入侵",
        ]
        dirty["prediction_targets"] = [
            {
                "id": "3037",
                "name": "欣興",
                "direction": "多",
                "trigger": "外資買超 231 億元居全台第一，機構鎖籌碼意圖明顯",
                "stop_loss_desc": "收盤跌破 1026 元 (MA5)",
            }
        ]
        source = build_notebooklm_source(dirty, date_str="2026-08-17")
        self.assertNotIn("鎖籌", source)
        self.assertNotIn("靜默入侵", source)
        self.assertIn("成交量未進前20", source)
        dirty["price_volume_divergence"] = ["聯發科影子建倉"]
        source2 = build_notebooklm_source(dirty, date_str="2026-08-17")
        self.assertNotIn("影子建倉", source2)


if __name__ == "__main__":
    unittest.main()
