#!/usr/bin/env python3
import os
import sys
import unittest
from datetime import date, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.data_fetcher import supply_chain_radar_queries  # noqa: E402
from modules.event_radar import (  # noqa: E402
    build_event_radar,
    decay_impact_score,
    event_key,
    freshness_label,
    prior_days_seen,
    record_event_history,
)
from modules.pipeline_stages import (  # noqa: E402
    news_search_topics,
    pick_deep_dive_focus,
    tape_focus_names,
)
from modules.narrative_sanitize import sanitize_narrative_text, sanitize_structured_data  # noqa: E402
from modules.pipeline_support import should_expand_news_search  # noqa: E402


AMD_EVENT = {
    "event": "AMD宣布於台灣產業體系投資逾100億美元，加速建置AI基礎設施",
    "url": "https://example.com/amd-taiwan-100bn",
    "snippet": "AMD 百億美元投資台灣先進封裝與 AI 伺服器供應鏈",
}


class RadarQueryTest(unittest.TestCase):
    def test_lite_is_not_amd_only_pair(self):
        queries = supply_chain_radar_queries(lite=True)
        self.assertEqual(len(queries), 2)
        blob = " ".join(queries).lower()
        self.assertIn("台灣", blob)
        self.assertFalse(all("amd" in q.lower() for q in queries))
        self.assertNotEqual(queries[0].strip().lower()[:3], "amd")

    def test_lite_uses_today_focus_not_amd(self):
        queries = supply_chain_radar_queries(lite=True, focus_names=["聯發科", "友達"])
        self.assertEqual(len(queries), 2)
        self.assertIn("聯發科", queries[1])
        self.assertIn("友達", queries[1])
        self.assertNotIn("AMD", queries[1])


class RadarFreshnessTest(unittest.TestCase):
    def test_repeat_cannot_open_news_gate_alone(self):
        today = date(2026, 9, 21)
        history = record_event_history({}, [AMD_EVENT], today - timedelta(days=1))
        self.assertEqual(prior_days_seen(AMD_EVENT, history, today), 1)
        self.assertEqual(freshness_label(1), "repeat")
        self.assertLess(decay_impact_score(100, 1), 60)

        radar = build_event_radar([AMD_EVENT], history=history, today=today)
        self.assertTrue(radar)
        self.assertEqual(radar[0]["freshness"], "repeat")
        self.assertLess(radar[0]["impact_score"], 60)
        expand, reason = should_expand_news_search({}, [], radar)
        self.assertFalse(expand)
        self.assertIn("跳過", reason)

    def test_stale_third_day_is_background(self):
        today = date(2026, 9, 21)
        history = {}
        history = record_event_history(history, [AMD_EVENT], today - timedelta(days=3))
        history = record_event_history(history, [AMD_EVENT], today - timedelta(days=2))
        history = record_event_history(history, [AMD_EVENT], today - timedelta(days=1))
        radar = build_event_radar([AMD_EVENT], history=history, today=today)
        self.assertTrue(radar)
        self.assertEqual(radar[0]["freshness"], "stale")
        self.assertLessEqual(radar[0]["impact_score"], 40)

    def test_new_event_keeps_high_score(self):
        today = date(2026, 9, 21)
        radar = build_event_radar([AMD_EVENT], history={}, today=today)
        self.assertEqual(radar[0]["freshness"], "new")
        self.assertGreaterEqual(radar[0]["impact_score"], 60)
        self.assertEqual(event_key(AMD_EVENT), "url:https://example.com/amd-taiwan-100bn")


class NewsOrderTest(unittest.TestCase):
    def test_protagonist_queries_before_radar(self):
        dynamic = [
            "Taiwan stock 2454 聯發科 外資買超 原因",
            "Taiwan stock 2409 友達 外資買超 原因",
            "Taiwan Semiconductor market news today",
        ]
        radar = [
            {
                **AMD_EVENT,
                "freshness": "new",
                "impact_score": 100,
            }
        ]
        topics = news_search_topics(dynamic, radar, focus_names=["聯發科", "友達"])
        self.assertEqual(topics[0], dynamic[0])
        self.assertNotEqual(topics[0], AMD_EVENT["event"])
        self.assertNotIn(AMD_EVENT["event"], topics)

    def test_radar_appends_only_when_it_names_today_stock(self):
        dynamic = ["Taiwan stock 2454 聯發科 外資買超 原因"]
        radar = [
            {
                "event": "聯發科 ASIC 資料中心訂單擴大",
                "snippet": "聯發科 法說",
                "freshness": "new",
                "impact_score": 80,
            }
        ]
        topics = news_search_topics(dynamic, radar, focus_names=["聯發科", "友達"])
        self.assertEqual(topics[0], dynamic[0])
        self.assertEqual(topics[-1], "聯發科 ASIC 資料中心訂單擴大")

    def test_stale_radar_never_appended(self):
        dynamic = ["Taiwan stock 2454 聯發科 外資買超 原因"]
        radar = [{**AMD_EVENT, "freshness": "stale", "snippet": "聯發科也受惠"}]
        topics = news_search_topics(dynamic, radar, focus_names=["聯發科"])
        self.assertEqual(topics, dynamic)

    def test_generic_topics_do_not_lead(self):
        dynamic = [
            "Taiwan Semiconductor",
            "台股 今日強勢族群 逆勢抗跌",
            "Taiwan stock 2454 聯發科 外資買超 原因",
            "Taiwan stock 2409 友達 外資買超 原因",
        ]
        topics = news_search_topics(dynamic, [], focus_names=["聯發科", "友達"])
        self.assertEqual(topics[0], "Taiwan stock 2454 聯發科 外資買超 原因")
        self.assertNotIn("Taiwan Semiconductor", topics)

    def test_deep_dive_prefers_anomaly_ticker(self):
        focus = pick_deep_dive_focus(
            institutional_data={"top_buy": [{"id": "2409", "name": "友達"}]},
            signal_pack={"strongest_anomaly": {"ticker": "2454", "text": "聯發科未放量"}},
            volume_data=[{"id": "2454", "name": "聯發科"}],
        )
        self.assertEqual(focus["id"], "2454")
        self.assertEqual(focus["name"], "聯發科")

    def test_tape_focus_names_prefers_buy_list(self):
        names = tape_focus_names(
            institutional_data={
                "top_buy": [{"id": "2454", "name": "聯發科"}],
                "top_sell": [{"id": "3008", "name": "大立光"}],
            },
            volume_data=[{"id": "2409", "name": "友達"}],
            yesterday_predictions=[{"id": "2408", "name": "南亞科"}],
        )
        self.assertEqual(names[0], "聯發科")
        self.assertIn("友達", names)
        self.assertIn("南亞科", names)


class NarrativeSanitizeTest(unittest.TestCase):
    def test_strips_shadow_language(self):
        text = sanitize_narrative_text("聯發科影子建倉、靜默入侵、隱形掃貨")
        self.assertNotIn("影子建倉", text)
        self.assertNotIn("靜默入侵", text)
        self.assertNotIn("隱形掃貨", text)
        self.assertNotIn("榜外低量買超", text)
        self.assertIn("成交量未進前20", text)

    def test_structured_fields_are_walked(self):
        out = sanitize_structured_data(
            {
                "price_volume_divergence": ["聯發科 (外資買超第一但成交量未進前20，影子建倉)"],
                "url": "https://example.com/影子建倉",
            }
        )
        self.assertNotIn("影子建倉", out["price_volume_divergence"][0])
        self.assertIn("影子建倉", out["url"])


if __name__ == "__main__":
    unittest.main()
