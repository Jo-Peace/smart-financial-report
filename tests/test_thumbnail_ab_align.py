#!/usr/bin/env python3
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.thumbnail_generator import align_ab_overlays, parse_title_hero  # noqa: E402


SD = {
    "safe_havens_bought": ["盟立 (+23.5億)"],
    "heavyweights_dumped": ["台積電 (-292.5億)"],
    "prediction_targets": [{"id": "2330", "name": "台積電"}],
}


class TitleHeroTests(unittest.TestCase):
    def test_parses_opening_sell(self):
        hero = parse_title_hero(
            "外資292億狂砍台積電！大盤跌784點盟立卻噴9.8%，權值提款誰在撿？｜9/2 AI帶你看股市",
            SD,
        )
        self.assertEqual(hero["name"], "台積電")
        # the title states the local -292.5億 correctly as 292億: the cover repeats the title's number
        self.assertEqual(hero["amount"], "292億")
        self.assertEqual(hero["verb"], "砍")
        self.assertEqual(hero["ticker"], "2330")


class AlignTests(unittest.TestCase):
    def test_same_hero_complete_vs_fragment(self):
        titles = [
            "外資292億狂砍台積電！大盤跌784點盟立卻噴9.8%｜9/2 AI帶你看股市",
            "昨天說會噴今天卻重挫？這兩檔低調建倉｜9/2 AI帶你看股市",
        ]
        scenarios = [
            {"overlay_primary": "3008 大立光", "overlay_secondary": "外資買31.9億", "avoid": []},
            {
                "overlay_primary": "上集點名",
                "overlay_secondary": "3189 景碩",
                "family": "validation",
                "scenario_name": "驗收現場",
                "avoid": [],
            },
        ]
        out = align_ab_overlays(scenarios, titles, SD)
        self.assertIn("台積電", out[0]["overlay_primary"])
        self.assertEqual(out[0]["overlay_secondary"], "外資砍292億")
        self.assertEqual(out[0]["hero"]["name"], out[1]["hero"]["name"])
        self.assertNotIn("上集點名", out[1]["overlay_primary"])
        self.assertIn("砍", out[1]["overlay_primary"])
        self.assertIn("292億", out[1]["overlay_primary"])
        self.assertIn("?", out[1]["overlay_secondary"])
        self.assertNotIn("誰在收", out[1]["overlay_secondary"])
        self.assertNotIn("誰在買", out[1]["overlay_secondary"])
        self.assertTrue(any("驗收章" in bit for bit in out[1]["avoid"]))


if __name__ == "__main__":
    unittest.main()
