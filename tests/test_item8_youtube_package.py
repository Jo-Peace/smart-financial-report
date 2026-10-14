#!/usr/bin/env python3
"""Item 8: YouTube package chapters, sand-table scenarios, 主盯 consistency."""

import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules import youtube_content_generator as ycg  # noqa: E402
from modules.narrative_sanitize import PANIC_TERMS  # noqa: E402


def _sd():
    return {
        "date": "2026-10-12",
        "index_action": "下跌 11.5點（-0.02%）",
        "safe_havens_bought": ["創意 (+30.4億)", "長榮 (+29.6億)", "欣興 (+15.5億)"],
        "heavyweights_dumped": ["台積電 (-240.3億)"],
        "price_volume_divergence": ["創意 (3443)：股價下跌 3.11% 但外資大買 30.4 億元"],
        "ai_data_picks": ["創意 (外資大買/籌碼鎖貨)"],
        "prediction_targets": [
            {"id": "2603", "name": "長榮", "direction": "多", "stop_loss_price": 238, "stop_loss_desc": "收盤跌破 238 元"},
            {"id": "3443", "name": "創意", "direction": "多", "stop_loss_price": 7558, "stop_loss_desc": "收盤跌破 MA20"},
        ],
        "local_market_facts": {
            "stocks": {
                "2603": {"ticker": "2603", "name": "長榮", "close": 246.0, "ma20": 250.0},
                "3443": {"ticker": "3443", "name": "創意", "close": 7150.0, "ma20": 7558.0},
                "3037": {"ticker": "3037", "name": "欣興", "close": 1270.0},
            }
        },
        "editorial_frame": {"validation_placement": "brief"},
    }


class ChapterTest(unittest.TestCase):
    def test_duration_parsing(self):
        self.assertEqual(ycg.parse_video_duration("754"), 754)
        self.assertEqual(ycg.parse_video_duration("12:34"), 754)
        self.assertEqual(ycg.parse_video_duration("1:02:03"), 3723)
        self.assertEqual(ycg.parse_video_duration("12m34s"), 754)
        self.assertIsNone(ycg.parse_video_duration("soon"))
        self.assertIsNone(ycg.parse_video_duration(""))

    def test_timestamps_follow_real_length(self):
        with mock.patch.dict(os.environ, {"YOUTUBE_VIDEO_DURATION": ""}):
            short = ycg.build_chapter_markers(_sd(), video_duration_sec=420)
            long = ycg.build_chapter_markers(_sd(), video_duration_sec=900)
        self.assertTrue(short[0].startswith("00:00 "))
        self.assertNotEqual(short[1:], long[1:])
        self.assertIn("05:15 明日觀察標的", short[4])  # 0.75 * 420s
        self.assertNotIn("11:00", " ".join(short))
        stamps = [ycg.parse_video_duration(line.split()[0]) or 0 for line in short]
        self.assertEqual(stamps, sorted(stamps))
        self.assertTrue(all(b - a >= 10 for a, b in zip(stamps, stamps[1:])))

    def test_unknown_length_gives_untimed_outline(self):
        with mock.patch.dict(os.environ, {"YOUTUBE_VIDEO_DURATION": ""}):
            markers = ycg.build_chapter_markers(_sd())
        self.assertEqual(markers[0], ycg._OUTLINE_HEADER)
        self.assertFalse(any(ycg._TIMESTAMP_LINE.match(line) for line in markers))
        # The header is published: no internal instruction in it.
        self.assertNotIn("上傳前", ycg._OUTLINE_HEADER)

    def test_old_instruction_header_is_replaced(self):
        old_header = "📌 節目段落（未提供影片長度，未加時間軸；上傳前可依實際長度補上）："
        description = f"第一行\n\n{old_header}\n・開場\n・主軸\n\n⚠️ 免責"
        with mock.patch.dict(os.environ, {"YOUTUBE_VIDEO_DURATION": ""}):
            out = ycg._normalize_description_chapters(description, _sd())
        self.assertNotIn(old_header, out)
        self.assertEqual(out.count(ycg._OUTLINE_HEADER), 1)
        self.assertIn("⚠️ 免責", out)

    def test_env_duration_is_used(self):
        with mock.patch.dict(os.environ, {"YOUTUBE_VIDEO_DURATION": "10:00"}):
            markers = ycg.build_chapter_markers(_sd())
        self.assertEqual(markers[-1], "09:30 操作建議")

    def test_gemini_fixed_timestamps_are_replaced(self):
        description = "第一行\n\n00:00 開場\n01:20 主軸\n03:30 驗收\n11:00 操作建議\n\n⚠️ 免責"
        with mock.patch.dict(os.environ, {"YOUTUBE_VIDEO_DURATION": "8:00"}):
            out = ycg._normalize_description_chapters(description, _sd())
        self.assertNotIn("11:00", out)
        self.assertIn("07:36 操作建議", out)
        self.assertEqual(out.count("00:00"), 1)
        self.assertIn("⚠️ 免責", out)


class ScenarioTest(unittest.TestCase):
    def test_three_distinct_price_anchored_scenarios(self):
        sd = _sd()
        out = ycg.build_target_scenarios(sd["prediction_targets"][0], sd, close=246.0)
        self.assertEqual(out["alert"], "")
        self.assertIn("238", out["base"])
        self.assertIn("250", out["bull"])
        self.assertIn("跌破失效價 238", out["bear"])
        self.assertEqual(len({out["base"], out["bull"], out["bear"]}), 3)

    def test_already_triggered_stop_is_flagged(self):
        sd = _sd()
        out = ycg.build_target_scenarios(sd["prediction_targets"][1], sd, close=7150.0)
        self.assertIn("已低於失效價 7558", out["alert"])

    def test_section_uses_local_close_and_skips_invalid_simulation(self):
        text = ycg._build_sand_table_section(_sd())
        self.assertIn("失效價 238", text)
        self.assertNotIn("轉向防禦/金融", text)
        self.assertNotIn("3443 創意：若 100 萬", text)  # stop above close → no position plan
        self.assertTrue(ycg.sand_table_scenarios_are_distinct(text))

    def test_short_target_scenarios_are_mirrored(self):
        sd = _sd()
        sd["local_market_facts"]["stocks"]["2603"]["ma20"] = 240.0
        short = {"id": "2603", "name": "長榮", "direction": "空", "stop_loss_price": 255}
        out = ycg.build_target_scenarios(short, sd, close=246.0)
        self.assertEqual(out["alert"], "")
        self.assertIn("站上失效價 255", out["bull"])
        self.assertIn("空方假說失效", out["bull"])
        self.assertIn("跌破 MA20 240", out["bear"])
        self.assertIn("外資續賣", out["bear"])
        self.assertEqual(len({out["base"], out["bull"], out["bear"]}), 3)
        triggered = ycg.build_target_scenarios(short, sd, close=256.0)
        self.assertIn("已高於失效價 255", triggered["alert"])
        text_only = ycg.build_target_scenarios(short, sd)
        self.assertIn("收盤站上 255", text_only["bull"])

    def test_short_sand_table_pnl_has_the_right_sign(self):
        sd = _sd()
        sd["prediction_targets"] = [
            {"id": "2603", "name": "長榮", "direction": "空", "stop_loss_price": 255, "stop_loss_desc": "收盤站上 255 元"}
        ]
        text = ycg._build_sand_table_section(sd)
        self.assertIn("246.0 元放空", text)
        self.assertIn("255.0 元回補", text)
        self.assertIn("損益約 -36,585 元（-3.66%）", text)  # short loses when the price rises to the stop
        self.assertIn("失效條件：收盤站上 255 元", text)

    def test_past_trends_are_history_not_a_validation_window_result(self):
        sd = _sd()
        sd["prediction_targets"] = [
            {"id": "2603", "name": "長榮", "direction": "空", "stop_loss_price": 255, "stop_loss_desc": "收盤站上 255 元"}
        ]
        sd["timeframe_history"] = {
            "recent_trends": {
                "2603": {
                    "latest_date": "2026-10-12",
                    "latest_price": 246.0,
                    "trends": {
                        "5D": {"from_date": "2026-10-02", "to_date": "2026-10-12", "pct_change": -6.0},
                        "10D": {"from_date": "2026-09-25", "to_date": "2026-10-12", "pct_change": 4.0},
                    },
                }
            }
        }
        text = ycg._build_sand_table_section(sd)
        self.assertNotIn("驗證窗，約", text)
        self.assertNotIn("10D 約", text)
        self.assertNotIn("+60,000 元", text)  # a past -6% drop is not the short's profit
        self.assertIn("過去 5 個交易日（2026-10-02→2026-10-12）已 -6.0%", text)
        self.assertIn("已發生的走勢，不是預測", text)
        self.assertEqual(ycg.past_trend_text({}), "")
        self.assertEqual(ycg.past_trend_text(None), "")

    def test_manual_rewrite_with_same_condition_is_detected(self):
        text = (
            "- 基準情境：創意先看 8100 能否守住，長榮先看 255.5\n"
            "- 樂觀情境：創意守住 8100，長榮守住 255.5\n"
            "- 悲觀情境：跌破失效價\n"
        )
        self.assertFalse(ycg.sand_table_scenarios_are_distinct(text))


class ConsistencyTest(unittest.TestCase):
    def test_description_must_name_every_official_stock(self):
        sd = _sd()
        self.assertEqual(ycg.check_main_watch_consistency(["下一交易日主盯長榮、創意。"], sd), [])
        missing = ycg.check_main_watch_consistency(["下一交易日主盯創意。"], sd)
        self.assertTrue(any("漏了" in w and "長榮" in w for w in missing))
        extra = ycg.check_main_watch_consistency(["明日主盯創意、長榮、欣興"], sd)
        self.assertTrue(any("非正式追蹤" in w and "3037" in w for w in extra))
        self.assertEqual(ycg.check_main_watch_consistency(["今天只是整理日"], sd), [])

    def test_prices_and_comparison_stocks_are_not_extra_watches(self):
        sd = _sd()
        sd["local_market_facts"]["stocks"]["2330"] = {"ticker": "2330", "name": "台積電"}
        for text in (
            "明日主盯 3443 創意（失效價 8100）與 2603 長榮，台積電只當對照。",
            "主盯：長榮、創意（台積電僅供參考）",
            "主盯創意、長榮、台積電當對照",
            "明日主盯 2603 長榮 238 元、3443 創意 7558 元",
        ):
            self.assertEqual(ycg.check_main_watch_consistency([text], sd), [], text)
        extra = ycg.check_main_watch_consistency(["明日主盯 2603、3443、2330"], sd)
        self.assertTrue(any("非正式追蹤" in w and "2330" in w for w in extra))

    def test_no_official_stock_but_description_says_main_watch(self):
        sd = _sd()
        sd["prediction_targets"] = []
        warnings = ycg.check_main_watch_consistency(["下一個交易日主盯台達電"], sd)
        self.assertTrue(warnings)

    def test_generate_package_sanitizes_and_reports_consistency(self):
        sd = _sd()
        output = (
            "###TITLE_A\n創意買30億！長榮也買｜10/12 AI帶你看股市\n"
            "###TITLE_B\n創意30億？｜10/12 AI帶你看股市\n"
            "###DESCRIPTION\n創意法人靜默吸籌。下一交易日主盯創意。\n\n00:00 開場\n11:00 操作建議\n\n⚠️ 免責\n"
            "###PINNED_COMMENT\n你怎麼看？\n###END\n"
        )
        captured = {}

        def fake_package_text(api_key, prompt, model_name):
            captured["prompt"] = prompt
            return output

        with mock.patch.object(ycg, "_generate_package_text", fake_package_text), mock.patch.dict(
            os.environ, {"YOUTUBE_VIDEO_DURATION": ""}
        ), redirect_stdout(StringIO()):
            result = ycg.generate_youtube_package("k", sd, "報告 240.3 (二百四十點三) 億，主力低調掃貨", "2026-10-12")
        self.assertIn("2603 長榮、3443 創意", captured["prompt"])
        self.assertNotIn("二百四十點三", captured["prompt"])
        self.assertNotIn("低調掃貨", captured["prompt"])
        self.assertNotIn("吸籌", result["description"])
        self.assertNotIn("11:00", result["description"])
        self.assertTrue(any("長榮" in w for w in result.get("consistency_warnings", [])))
        with tempfile.TemporaryDirectory() as tmp:
            path = ycg.save_youtube_package(result, tmp, "2026-10-12", structured_data=sd)
            with open(path, encoding="utf-8") as f:
                saved = f.read()
        self.assertIn("主盯一致性檢查", saved)
        self.assertIn("章節沒有時間軸", saved)


class PackagePromptTest(unittest.TestCase):
    _OUTPUT = (
        "###TITLE_A\n創意買30億！台股崩盤前誰在逃命｜10/12 AI帶你看股市\n"
        "###TITLE_B\n長榮斷頭危機？｜10/12 AI帶你看股市\n"
        "###DESCRIPTION\n台股血洗，資金全逃。下一交易日主盯長榮、創意。\n\n⚠️ 免責\n"
        "###PINNED_COMMENT\n崩盤了嗎？\n###END\n"
    )

    def _run(self, sd):
        captured = {}

        def fake_package_text(api_key, prompt, model_name):
            captured["prompt"] = prompt
            return self._OUTPUT

        with mock.patch.object(ycg, "_generate_package_text", fake_package_text), mock.patch.dict(
            os.environ, {"YOUTUBE_VIDEO_DURATION": ""}
        ), redirect_stdout(StringIO()):
            result = ycg.generate_youtube_package("k", sd, "報告", "2026-10-12")
        return captured["prompt"], result

    def test_event_led_title_only_on_verified_event_day(self):
        sd = _sd()
        self.assertFalse(ycg.is_verified_major_event_day(sd))
        prompt, _ = self._run(sd)
        self.assertIn("今日不是已驗證的重大事件日", prompt)
        self.assertNotIn("事件 → 台股數字 → 觀察條件", prompt)

        sd["editorial_frame"] = {"frame": "major_event_day", "validation_placement": "brief"}
        sd["supply_chain_event_radar"] = [{"title": "old", "impact_score": 90, "freshness": "repeat"}]
        self.assertFalse(ycg.is_verified_major_event_day(sd))  # only a stale event
        sd["supply_chain_event_radar"].append({"title": "關稅", "impact_score": 72, "freshness": "new"})
        self.assertTrue(ycg.is_verified_major_event_day(sd))
        prompt, _ = self._run(sd)
        self.assertIn("事件 → 台股數字 → 觀察條件", prompt)

    def test_prompt_has_no_contradicting_tone_rules(self):
        prompt, _ = self._run(_sd())
        self.assertNotIn("主力還在", prompt)
        self.assertNotIn("「外資已買 / 偷偷買 / 掃貨」等語氣", prompt)
        self.assertNotIn("若沒有對應數字", prompt)
        self.assertNotIn("資金全逃去哪了", prompt)  # old high-view sample no longer offered as a model
        self.assertIn("有數字也一樣", prompt)

    def test_published_packaging_has_no_panic_words(self):
        _prompt, result = self._run(_sd())
        published = result["titles"] + [result["description"], result["pinned_comment"]]
        for text in published:
            for term in PANIC_TERMS:
                self.assertNotIn(term, text, text)

    def test_fallback_package_is_sanitized_too(self):
        sd = _sd()
        sd["index_action"] = "崩盤 500 點（-2.1%）"
        with mock.patch.dict(os.environ, {"YOUTUBE_VIDEO_DURATION": ""}), redirect_stdout(StringIO()):
            package = ycg.build_fallback_youtube_package(sd, "報告", "2026-10-12", reason="test")
        for text in package["titles"] + [package["description"], package["pinned_comment"]]:
            self.assertNotIn("崩盤", text)


if __name__ == "__main__":
    unittest.main()
