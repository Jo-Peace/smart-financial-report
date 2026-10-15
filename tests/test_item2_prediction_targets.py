#!/usr/bin/env python3
"""Item 2: prediction_targets only from 〔明日觀察焦點〕 (findings 18/79)."""
import os
import sys
import unittest
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules import data_extractor  # noqa: E402
from modules.prediction_targets import filter_prediction_targets, focus_sections  # noqa: E402

# Shape of 2026-09-07: the deep-dive bet is the foreign buy #1 (台積電); the focus
# section names only 日月光投控. Gemini merged both.
REPORT_0907 = """# 日報

## 【今日深度押注：2330 台積電 】
- 法人籌碼：今日外資買超 120 億（上市第 1 名）
- 信心度：★★★

---

## 〔明日觀察焦點〕節奏 + 趨勢

1. **3711 日月光投控**：
   - **為何點名**：外資買超 30.1 億，站上 MA20。
   - **失效條件**：跌破 180 元。

---

## 〔操作建議〕
- 台積電先看方向，不追。
"""

REPORT_1014 = """## 〔明日觀察焦點〕節奏 + 趨勢

1. **1303 南亞**：
   - **為何點名**：外資買超 55.3 億居冠。
   - **失效條件**：跌破 319.0（MA5）。

2. **2327 國巨***：
   - **為何點名**：外資買超第 3 名（36.6 億）。
   - **失效條件**：收盤跌破 584.0（MA20）。

---
## 〔操作建議〕
- 南亞科先觀察。
"""


class PredictionTargetFilterTests(unittest.TestCase):
    def test_deep_dive_megacap_and_extra_rows_are_dropped(self):
        targets = [
            {"id": "3711", "name": "日月光投控", "direction": "多"},
            {"id": "2330", "name": "台積電", "direction": "多"},
        ]
        kept, audit = filter_prediction_targets(targets, REPORT_0907)
        self.assertEqual([t["id"] for t in kept], ["3711"])
        self.assertEqual(audit["dropped"][0]["id"], "2330")
        self.assertIn("超級權值股", audit["dropped"][0]["reason"])

    def test_only_section_names_kept_and_max_two_in_section_order(self):
        targets = [
            {"id": "2327", "name": "國巨*", "direction": "多"},
            {"id": "2408", "name": "南亞科", "direction": "多"},  # only in 操作建議
            {"id": "1303", "name": "南亞", "direction": "多"},
        ]
        kept, audit = filter_prediction_targets(targets, REPORT_1014)
        self.assertEqual([t["id"] for t in kept], ["1303", "2327"])
        self.assertEqual([d["id"] for d in audit["dropped"]], ["2408"])

    def test_name_match_is_exact_not_prefix(self):
        report = "## 〔明日觀察焦點〕\n- **南亞科**：外資買超 50 億\n---\n"
        kept, audit = filter_prediction_targets(
            [{"id": "", "name": "南亞", "direction": "多"}, {"id": "", "name": "南亞科", "direction": "多"}],
            report,
        )
        self.assertEqual([t["name"] for t in kept], ["南亞科"])
        self.assertEqual(audit["dropped"][0]["name"], "南亞")

    def test_longer_known_name_blocks_prefix_match(self):
        report = "## 〔明日觀察焦點〕\n- **南亞科**：外資買超 50 億\n---\n"
        kept, _ = filter_prediction_targets([{"id": "", "name": "南亞"}], report, known_names=["南亞科"])
        self.assertEqual(kept, [])

    def test_cap_at_two(self):
        report = "## 〔下週一觀察焦點〕節奏 + 趨勢\n- **2301 光寶科**\n- **2303 聯電**\n- **1301 台塑**\n---\n"
        targets = [{"id": t, "name": t} for t in ("1301", "2301", "2303")]
        kept, audit = filter_prediction_targets(targets, report)
        self.assertEqual([t["id"] for t in kept], ["2301", "2303"])
        self.assertIn("上限", audit["dropped"][0]["reason"])

    def test_no_safe_pick_section_clears_targets(self):
        report = "## 〔明日觀察焦點〕\n今日數據無安全明牌，現金為王，靜待訊號。\n---\n"
        kept, _ = filter_prediction_targets([{"id": "2344", "name": "華邦電"}], report)
        self.assertEqual(kept, [])

    def test_no_safe_pick_with_numbers_or_negative_example_clears_targets(self):
        for report in (
            "## 〔明日觀察焦點〕\n今日數據無安全明牌，現金為王。欣興雖強但已過熱；大盤 23000 點關卡。\n---\n",
            "## 〔明日觀察焦點〕\n今日數據無安全明牌，現金為王。欣興（3037）已過熱不追，2026 年底前觀望。\n---\n",
        ):
            with self.subTest(report=report):
                kept, audit = filter_prediction_targets([{"id": "3037", "name": "欣興"}], report)
                self.assertEqual(kept, [])
                self.assertIn("無安全明牌", audit["dropped"][0]["reason"])

    def test_structured_pick_survives_cash_wording(self):
        report = (
            "## 〔明日觀察焦點〕\n1. **1303 南亞**：\n   - **為何點名**：外資買超 55.3 億。\n"
            "   - **隔日節奏**：保留三成現金為王。\n   - **失效條件**：跌破 319.0。\n---\n"
        )
        kept, _ = filter_prediction_targets([{"id": "1303", "name": "南亞"}], report)
        self.assertEqual([t["id"] for t in kept], ["1303"])

    def test_numbers_with_units_are_not_tickers(self):
        from modules.prediction_targets import _TICKER

        text = "3711 日月光投控、2885 元大金、2393 億光；大盤 23000 點、2026年、2490元、3160億"
        self.assertEqual(_TICKER.findall(text), ["3711", "2885", "2393"])

    def test_section_stops_at_next_heading(self):
        sections = focus_sections(REPORT_1014)
        self.assertEqual(len(sections), 1)
        self.assertNotIn("南亞科", sections[0])

    def test_extractor_applies_filter_after_merge(self):
        fake = '{"prediction_targets": [{"id": "3711", "name": "日月光投控"}, {"id": "2330", "name": "台積電"}], "sectors": {}}'
        with mock.patch.object(data_extractor, "generate_json", return_value=fake):
            out = data_extractor.extract_structured_data("k", REPORT_0907, local_facts={"date": "2026-09-07"})
        self.assertEqual([t["id"] for t in out["prediction_targets"]], ["3711"])
        self.assertEqual(out["prediction_targets_filter"]["dropped"][0]["id"], "2330")


if __name__ == "__main__":
    unittest.main()
