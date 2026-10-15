#!/usr/bin/env python3
"""
Item 2 「已播出的錯誤數字」: /2 cover & title facts (findings 43–48, 120–122, 156–159).

Fixture: tests/fixtures/item2_2026-10-14_facts.json, trimmed from the real
2026-10-14 /1 output (台積電 2490 → 2490, 0.00%; 南亞 +55.3億 buy #1 +9.40%;
南亞科 -110.4億 sell #3).
"""
import json
import os
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.packaging_fatigue import enforce_packaging_title_rules  # noqa: E402
from modules.thumbnail_generator import (  # noqa: E402
    DEFAULT_THUMBNAIL_STYLES,
    _buy_amount_for_stock,
    _build_local_title_candidates,
    _collect_scenario_candidates,
    _resolve_thumbnail_styles,
    _sell_amount_for_stock,
    generate_ab_test_thumbnails,
    parse_title_hero,
)
from modules.packaging_fatigue import build_fatigue_state  # noqa: E402
from modules.title_fact_gate import (  # noqa: E402
    apply_fact_gate_block,
    build_fact_index,
    check_cover,
    check_text,
    check_titles,
    choose_hero,
    index_move_word,
)
from scripts.youtube import check_youtube_package  # noqa: E402

FIXTURE = os.path.join(BASE_DIR, "tests", "fixtures", "item2_2026-10-14_facts.json")
TITLE_A = "南亞買 55.3 億噴 9%！南亞科卻遭砍 110 億？AI 基礎材料換手真相｜10/14 AI帶你看股市"
TITLE_B = "台積電砍 168 億卻收紅？南亞買 55 億奪冠，這類 AI 材料要接棒了嗎？｜10/14 AI帶你看股市"
TITLE_B_FIXED = "台積電砍 168 億卻守平盤？南亞買 55 億奪冠，這類 AI 材料要接棒了嗎？｜10/14 AI帶你看股市"


def _sd():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def _errors(issues):
    return [i for i in issues if i["level"] == "error"]


class TitleGateTests(unittest.TestCase):
    def setUp(self):
        self.sd = _sd()
        self.facts = build_fact_index(self.sd, stock_db={})

    def test_a1_title_b_shou_hong_contradicts_flat_close(self):
        issues = check_titles([TITLE_A, TITLE_B], facts=self.facts)
        errors = _errors(issues)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["where"], "標題B")
        self.assertEqual(errors[0]["claim"], "收紅")
        self.assertIn("台積電", errors[0]["message"])
        self.assertIn("0.00%", errors[0]["fact"])

    def test_a1_title_a_and_fixed_b_pass(self):
        self.assertEqual(_errors(check_titles([TITLE_A, TITLE_B_FIXED], facts=self.facts)), [])

    def test_group_object_after_verb_is_not_the_carried_stock(self):
        # Shadow run 2026-10-14: 「反手砍電子權值」 cuts the electronics heavyweights, not 南亞.
        title = "南亞買55.3億卻反手砍電子權值？台積電平盤背後誰在提款？｜AI帶你看股市"
        self.assertEqual(_errors(check_text(title, facts=self.facts)), [])
        self.assertEqual(_errors(check_text("電子權值卻被砍，南亞買55.3億噴9%", facts=self.facts)), [])
        # Without a group object the clause still inherits 南亞 and is a contradiction.
        errors = _errors(check_text("南亞買55.3億卻遭砍？", facts=self.facts))
        self.assertEqual(len(errors), 1)
        self.assertIn("南亞", errors[0]["message"])

    def test_amount_direction_and_cross_attribution(self):
        issues = _errors(check_text("南亞科買55.3億！", facts=self.facts))
        messages = " ".join(i["message"] for i in issues)
        self.assertIn("110.4億", messages)
        self.assertIn("1303 南亞 的金額", messages)
        self.assertIn("賣超", messages)

    def test_price_state_words(self):
        cases = {
            "南亞漲停！": "漲停",  # +9.40% is not limit-up
            "台積電收黑": "收黑",  # 0.00%
            "大盤狂飆284點": "狂飆",  # +0.59%
            "大盤漲284點，南亞逆勢噴9%": "逆勢",  # same direction as the index
            "南亞科逆勢收紅": "收紅",
        }
        for text, claim in cases.items():
            with self.subTest(text=text):
                self.assertIn(claim, [i["claim"] for i in _errors(check_text(text, facts=self.facts))])
        self.assertEqual(_errors(check_text("台塑漲停！台積電守平盤", facts=self.facts)), [])

    def test_index_points_and_rounding(self):
        self.assertEqual(_errors(check_text("大盤漲284點", facts=self.facts)), [])
        self.assertTrue(_errors(check_text("大盤跌284點", facts=self.facts)))
        self.assertTrue(_errors(check_text("大盤漲484點", facts=self.facts)))

    def test_subject_carry_and_other_objects(self):
        # 「股價…」 continues the previous stock; 「這類…」 is not a stock
        self.assertTrue(_errors(check_text("南亞科砍110億！股價漲1.5%", facts=self.facts)))
        self.assertEqual(_errors(check_text("台積電砍168億！這類 AI 材料要接棒嗎", facts=self.facts)), [])

    def test_market_wording_and_questions_are_not_flow_claims(self):
        for text in (
            "南亞科買點浮現？",
            "南亞科該買嗎？",
            "南亞科可以買嗎？",
            "南亞科值得買嗎？",
            "南亞科買氣回溫",
            "南亞科買盤回籠",
            "南亞科買不買？",
            "南亞噴9%後賣壓出籠？",
            "南亞賣點到了嗎？",
            "台積電漲不動？外資砍168億",
            "台積電跌不下去？",
        ):
            with self.subTest(text=text):
                self.assertEqual(_errors(check_text(text, facts=self.facts)), [])
        # real flow verbs and moves are still checked
        for text, claim in (
            ("南亞科外資買", "買"),
            ("外資狂買南亞科", "狂買"),
            ("南亞外資賣超", "賣超"),
            ("台積電漲不停", "漲"),
        ):
            with self.subTest(text=text):
                self.assertIn(claim, [i["claim"] for i in _errors(check_text(text, facts=self.facts))])

    def test_integer_claims_round_down_or_half_up_only(self):
        for text, claim in (
            ("南亞買 56 億噴 9%！", "56 億"),  # 55.33億
            ("南亞買 55 億噴 10%！", "10%"),  # +9.40% is not limit-up
            ("南亞科賣超 111 億", "111 億"),  # 110.36億
            ("台積電砍 169 億", "169 億"),  # 168.13億
        ):
            with self.subTest(text=text):
                self.assertIn(claim, [i["claim"] for i in _errors(check_text(text, facts=self.facts))])
        for text in ("南亞買 55 億噴 9%！", "南亞科賣超 110 億", "台積電砍 168 億", "大盤漲285點"):
            with self.subTest(text=text):
                self.assertEqual(_errors(check_text(text, facts=self.facts)), [])

    def test_prompt_block_keeps_streak_lower_bound(self):
        from modules.title_fact_gate import format_price_state_block

        sd = _sd()
        sd["local_market_facts"]["stocks"]["1303"].update(foreign_streak_days=2, foreign_streak_complete=False)
        block = format_price_state_block(sd)
        self.assertIn("外資連續買超 至少 2 個交易日", block)
        sd["local_market_facts"]["stocks"]["1303"]["foreign_streak_complete"] = True
        block = format_price_state_block(sd)
        self.assertIn("外資連續買超 2 個交易日", block)
        self.assertNotIn("至少 2", block)

    def test_package_block_is_replaced_not_duplicated(self):
        package = "# pkg\n\n**標題 A（x）：**\n```\nA\n```\n\n**標題 B（y）：**\n```\nB\n```\n\n---\nrest\n"
        issues = check_titles([TITLE_A, TITLE_B], facts=self.facts)
        once = apply_fact_gate_block(package, issues)
        twice = apply_fact_gate_block(once, issues)
        self.assertEqual(once, twice)
        self.assertEqual(once.count("title-fact-gate:start"), 1)
        self.assertIn("收紅", once)
        self.assertNotIn("title-fact-gate", apply_fact_gate_block(once, []))


class CoverFactTests(unittest.TestCase):
    def setUp(self):
        self.sd = _sd()

    def test_a2_hero_and_overlays_from_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = generate_ab_test_thumbnails(
                None,
                None,
                "",
                tmp,
                styles=["財經戰情室"],
                structured_data=self.sd,
                run_date="2026-10-14",
                preferred_titles=[TITLE_A, TITLE_B],
                ab_hypothesis="H2",  # text: statement vs question, same stock and number
            )
            prompts = []
            for thumb in result["thumbnails"]:
                with open(thumb["path"], encoding="utf-8") as f:
                    prompts.append(f.read())
        self.assertEqual(result["hero"]["ticker"], "1303")
        self.assertIn("- Primary: 1303 南亞", prompts[0])
        self.assertIn("- Secondary: 外資買55.3億", prompts[0])
        self.assertNotIn("外資砍55.3億", prompts[0])
        self.assertIn("- Primary: 1303 南亞", prompts[1])
        self.assertIn("- Secondary: 外資為何買55.3億？", prompts[1])
        self.assertNotIn("誰在買", prompts[1])
        for prompt in prompts:
            self.assertIn("- Hero (fact source): 1303 南亞（外資 +55.3億，漲跌 +9.40%）", prompt)
            main = [line for line in prompt.splitlines() if line.startswith("- Main stocks:")][0]
            self.assertTrue(main.startswith("- Main stocks: 1303 南亞"), main)
            self.assertNotIn("(+", main)
            scene = [line for line in prompt.splitlines() if line.startswith("- Scene:")][0]
            self.assertIn("南亞", scene)
            self.assertNotIn("強彈", prompt)  # +0.59% is not 強彈 (finding 122)

    def test_cover_repeats_the_title_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = generate_ab_test_thumbnails(
                None,
                None,
                "",
                tmp,
                styles=["財經戰情室"],
                structured_data=self.sd,
                run_date="2026-10-14",
                preferred_titles=[TITLE_B_FIXED, TITLE_A],
                # characters: the cover text stays identical (on a 財經戰情室 day H1 is swapped for H2)
                ab_hypothesis="H3",
            )
            prompts = []
            for thumb in result["thumbnails"]:
                with open(thumb["path"], encoding="utf-8") as f:
                    prompts.append(f.read())
        self.assertEqual(result["hero"]["ticker"], "2330")
        self.assertIn("- Secondary: 外資砍168億", prompts[0])
        self.assertIn("- Secondary: 外資砍168億", prompts[1])  # same number on both covers
        for prompt in prompts:
            self.assertIn("- Hero (fact source): 2330 台積電（外資 -168.1億", prompt)  # the fact line keeps the local value
            worded = "\n".join(line for line in prompt.splitlines() if not line.startswith("- Hero (fact source):"))
            self.assertNotIn("168.1億", worded)

    def test_anomaly_scene_follows_hero_flow_sign(self):
        from modules.thumbnail_generator import _anchor_scenario_to_hero

        hero = choose_hero([TITLE_B_FIXED], self.sd)
        self.assertEqual((hero["ticker"], hero["verb"]), ("2330", "砍"))
        cards = _collect_scenario_candidates(self.sd, [TITLE_B_FIXED], hero=hero)
        anomaly = [c for c in cards if c["scenario_name"] == "量價異常"]
        self.assertTrue(anomaly)
        self.assertIn("賣超", anomaly[0]["scene"])
        self.assertNotIn("買超", anomaly[0]["scene"])
        # a scene that names the hero but only speaks of the opposite flow is rebuilt
        row = _anchor_scenario_to_hero({"scene": "台積電 桌面下方暗示買超與成交量的落差", "main_stocks": []}, hero)
        self.assertNotIn("買超", row["scene"])
        self.assertIn("賣壓", row["scene"])

    def test_a3_nanya_vs_nanya_tech_never_swap_amounts(self):
        nanya_tech = parse_title_hero("南亞科卻遭砍 110 億？", self.sd)
        # 110 億 is a correct headline rounding of -110.36億: the cover repeats the title's number
        self.assertEqual((nanya_tech["ticker"], nanya_tech["amount"], nanya_tech["verb"]), ("2408", "110億", "砍"))
        nanya_tech = parse_title_hero("南亞科卻遭砍？", self.sd)
        self.assertEqual((nanya_tech["ticker"], nanya_tech["amount"], nanya_tech["verb"]), ("2408", "110.4億", "砍"))
        nanya = parse_title_hero("南亞買 55.3 億噴 9%！", self.sd)
        self.assertEqual((nanya["ticker"], nanya["amount"], nanya["verb"]), ("1303", "55.3億", "買"))
        self.assertEqual(_buy_amount_for_stock("南亞", self.sd), "55.3億")
        self.assertEqual(_sell_amount_for_stock("南亞", self.sd), "")
        self.assertEqual(_sell_amount_for_stock("2408 南亞科", self.sd), "110.4億")
        self.assertEqual(_buy_amount_for_stock("南亞科", self.sd), "")
        facts = build_fact_index(self.sd, stock_db={})
        self.assertTrue(_errors(check_cover("2408 南亞科", "外資買55.3億", facts=facts)))
        self.assertTrue(_errors(check_cover("1303 南亞", "外資砍110.4億", facts=facts)))

    def test_verb_never_defaults_to_buy_or_follows_title_keywords(self):
        sd = {
            "safe_havens_bought": ["華邦電 (+36.9億)"],
            "heavyweights_dumped": ["聯發科 (-202.1億)"],
            "index_action": "上漲 110.5點（+0.22%）",
            "prediction_targets": [{"id": "2454", "name": "聯發科"}],
        }
        hero = parse_title_hero("聯發科少202億！大盤仍漲110點｜10/6 AI帶你看股市", sd)
        self.assertEqual((hero["verb"], hero["amount"]), ("砍", "202億"))
        unknown = parse_title_hero("創意買30億！", {"prediction_targets": [{"id": "3443", "name": "創意"}]})
        self.assertEqual(unknown.get("verb", ""), "")  # no local amount → no verb, never 「買」

    def test_longest_name_wins_changrong(self):
        sd = {
            "safe_havens_bought": ["長榮航 (+12.0億)"],
            "heavyweights_dumped": ["長榮 (-30.0億)"],
            "prediction_targets": [{"id": "2618", "name": "長榮航"}, {"id": "2603", "name": "長榮"}],
        }
        hero = parse_title_hero("長榮航買12億！", sd)
        self.assertEqual((hero["ticker"], hero["amount"]), ("2618", "12億"))
        self.assertTrue(_errors(check_text("長榮買12億", sd)))

    def test_spaced_number_is_not_a_ticker(self):
        sd = dict(self.sd)
        hero = parse_title_hero("AMD 砸 3160 億！南亞買55.3億", sd)
        self.assertEqual(hero["ticker"], "1303")

    def test_off_board_and_index_scenarios_need_facts(self):
        sd = _sd()
        sd["price_volume_divergence"] = ["聯發科：外資賣超 157 億，成交量未進前 20 名"]
        hero = choose_hero([TITLE_A], sd)
        names = [c["scenario_name"] for c in _collect_scenario_candidates(sd, [TITLE_A], hero=hero)]
        self.assertNotIn("榜外吸籌", names)  # a sell is not 吸籌 (finding 120)
        self.assertNotIn("強彈追蹤", names)  # +0.59% (finding 122)
        self.assertNotIn("大跌守線", names)
        sd["index_action"] = "下跌 980.0點（-2.10%）"
        cards = _collect_scenario_candidates(sd, [TITLE_A], hero=choose_hero([TITLE_A], sd))
        crash = [c for c in cards if c["scenario_name"] == "大跌守線"]
        self.assertTrue(crash)
        self.assertTrue(crash[0]["overlay_primary"].startswith("大跌"))
        self.assertEqual(index_move_word(-0.02), "小跌")
        self.assertEqual(index_move_word(0.59), "上漲")
        self.assertEqual(index_move_word(-2.6), "重挫")

    def test_local_titles_are_fact_checked(self):
        sd = _sd()
        titles = _build_local_title_candidates(sd, "10/14", build_fatigue_state(channel_story=""))
        self.assertTrue(titles)
        facts = build_fact_index(sd)
        for title in titles:
            self.assertEqual(_errors(check_text(title, facts=facts)), [], title)

    def test_default_theme_label_maps_to_war_room_presets(self):
        self.assertEqual(_resolve_thumbnail_styles(["財經戰情室"]), DEFAULT_THUMBNAIL_STYLES)


class CheckYoutubePackageGateTests(unittest.TestCase):
    def _cover(self, primary, secondary, main="1303 南亞"):
        return (
            f"## Thumbnail Headline Text\n\n- Primary: {primary}\n- Secondary: {secondary}\n"
            f"- Small date label: 10/14 台股盤後\n\n## Scenario Brief\n\n- Main stocks: {main}\n"
        )

    def _run(self, title_b, covers):
        package = f"**標題 A（對照組）：**\n```\n{TITLE_A}\n```\n\n**標題 B（實驗組）：**\n```\n{title_b}\n```\n"
        failures, warnings = [], []
        check_youtube_package.check_fact_gate("2026-10-14", package, covers, failures, warnings, structured_data=_sd())
        return failures, warnings

    def test_fails_on_title_contradiction(self):
        failures, _ = self._run(TITLE_B, [self._cover("1303 南亞", "外資買55.3億"), self._cover("買55.3億", "噴9.4%！")])
        self.assertEqual(len(failures), 1)
        self.assertIn("收紅", failures[0])
        self.assertIn("標題B", failures[0])

    def test_fails_on_buggy_cover_and_passes_fixed(self):
        failures, _ = self._run(TITLE_B_FIXED, [self._cover("1303 南亞", "外資砍55.3億"), self._cover("砍55.3億", "誰在買?")])
        self.assertEqual(len(failures), 2)
        self.assertTrue(all("南亞" in f for f in failures))
        failures, warnings = self._run(
            TITLE_B_FIXED, [self._cover("1303 南亞", "外資買55.3億"), self._cover("買55.3億", "噴9%！", main="國巨* (+36.6億)")]
        )
        self.assertEqual(failures, [])
        self.assertTrue(any("Main stocks" in w for w in warnings))


class DefaultVisualThemeTests(unittest.TestCase):
    def test_empty_theme_uses_documented_default_and_updates_log(self):
        from unittest import mock

        from scripts.youtube import regen_assets

        with tempfile.TemporaryDirectory() as tmp:
            bans = os.path.join(tmp, "packaging_bans.json")
            log = os.path.join(tmp, "channel_story_log.md")
            with open(log, "w", encoding="utf-8") as f:
                f.write(
                    "| 日期 | 主角 | 預測 | 驗收 | 視覺主題 | 大盤 | 影片ID | 標題 | 觀看 |\n"
                    "|---|---|---|---|---|---|---|---|---|\n"
                    "| 2026-10-14 | 南亞 | 南亞(多) | 待驗收 | 待 /2 設定 | 上漲 284.5點（+0.59%） | - | - | 待更新 |\n"
                )
            with mock.patch.object(regen_assets, "CHANNEL_STORY_LOG", log), mock.patch.object(
                regen_assets, "REPORTS_DIR", tmp
            ):
                # no structured_data → no auto scene → the documented fallback
                theme, _state = regen_assets.resolve_visual_theme("", "2026-10-14", bans_path=bans)
                self.assertEqual(theme, "財經戰情室")
                scene, _state = regen_assets.resolve_visual_theme("水庫漫畫。禁止AMD開場。", "2026-10-14", bans_path=bans)
                self.assertEqual(scene.rstrip("。"), "水庫漫畫")
                regen_assets._update_channel_story_visual_theme("2026-10-14", theme, title_used=TITLE_A)
                with open(log, encoding="utf-8") as f:
                    row = f.read().splitlines()[-1]
                self.assertIn("| 財經戰情室 |", row)
                self.assertNotIn("待 /2 設定", row)
                # a same-day rerun without --visual-theme keeps the theme the operator set
                # today (only a previous DAY's theme is never carried forward)
                regen_assets._update_channel_story_visual_theme("2026-10-14", "水庫漫畫")
                again, _state = regen_assets.resolve_visual_theme("", "2026-10-14", bans_path=bans)
                self.assertEqual(again, "水庫漫畫")
                regen_assets._update_channel_story_visual_theme("2026-10-14", again)
                with open(log, encoding="utf-8") as f:
                    self.assertIn("| 水庫漫畫 |", f.read().splitlines()[-1])


STOCK_DB = {
    "2330": {"name": "台積電"},
    "2408": {"name": "南亞科"},
    "1303": {"name": "南亞"},
    "3443": {"name": "創意"},
}


class TitleRuleTests(unittest.TestCase):
    def test_number_moves_with_its_stock(self):
        out = enforce_packaging_title_rules("台積電遭外資大賣超，記憶體南亞科逆勢掃貨112億｜10/8 AI帶你看股市", stock_db=STOCK_DB)
        self.assertTrue(out.startswith("記憶體南亞科逆勢掃貨112億！"), out)
        self.assertFalse(out.startswith("112億"), out)

    def test_subjectless_number_clause_is_not_fronted(self):
        # Fronting 「外資再砍110億」 would read as 台積電's 110億 (finding 121).
        for title in (
            "台積電權值股今天全面退潮，南亞科也守不住？外資再砍110億｜10/14 AI帶你看股市",
            "台積電法說會前夕資金先撤，創意終於被看見？外資買30億，股價跌3.1%｜10/14 AI帶你看股市",
        ):
            with self.subTest(title=title):
                self.assertEqual(enforce_packaging_title_rules(title, stock_db=STOCK_DB), title)


if __name__ == "__main__":
    unittest.main()
