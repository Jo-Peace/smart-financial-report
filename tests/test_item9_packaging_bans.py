#!/usr/bin/env python3
"""Item 9: structured packaging bans, scene-only image prompts, data-driven planner copy, director card."""
import datetime
import json
import os
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.notebooklm_generator import build_compact_notebooklm_prompt  # noqa: E402
from modules.packaging_bans import (  # noqa: E402
    active_bans,
    apply_visual_theme,
    format_bans_for_title_prompt,
    load_bans,
    parse_ban_clauses,
    recent_visual_objects,
    recent_visual_rules,
    scene_only,
    split_visual_theme,
    title_ban_hits,
)
from modules.thumbnail_generator import _normalize_custom_style  # noqa: E402
from modules.video_asset_planner import _packaging_formula_lines, build_video_asset_record  # noqa: E402

THEME = (
    "實驗室／滴定管漫畫（漲紅跌綠）：創意燒杯被紅色試劑灌進30億，液面計卻降到跌3.1%。博士看滴定管，韭菜盯液面。"
    "A第一行金額、第二行反差，B短句。禁止美股紅跌綠漲；禁止AMD開場；"
    "禁止買A、B才是量第一（9/30；可寫量王收黑）；禁止量第一收黑同構（10/5剛用且33 views）；"
    "禁止投信倒同構（10/7）；禁止逆勢噴同構（10/8低樣本）；禁止誰在收／誰在接；"
    "禁止華通、華邦電當標題主角；禁止窯廠／燈塔／氣象站雨量筒／溫室遮光簾。"
)


def _window(views_by_date):
    sample, rows = [], []
    for i, (date, title, views, mature) in enumerate(views_by_date):
        row = {"date": date, "video_id": f"v{i}", "title": title, "views": views, "mature": mature}
        rows.append(row)
        if mature:
            sample.append(dict(row, metric=views))
    metrics = sorted(r["metric"] for r in sample)
    return {
        "rows": rows,
        "sample": sample,
        "baseline": {"median": metrics[len(metrics) // 2], "q75": metrics[int(len(metrics) * 0.75)], "n": len(metrics)},
        "basis": "test",
    }


WINDOW = _window(
    [
        ("2026-09-30", "華邦電買66億！群創漲停才是量第一", 327, True),
        ("2026-10-02", "臻鼎買71億鎖漲停！權值少148億", 1090, True),
        ("2026-10-05", "台積電買244億！友達量第一收黑", 116, True),
        ("2026-10-06", "聯發科少202億！大盤仍漲110點", 113, True),
        ("2026-10-07", "華邦電買37億！投信倒13億", 583, True),
        ("2026-10-08", "南亞科買112億！逆勢噴3.5%", 50, False),
        ("2026-09-24", "景碩買48億！台積電卻被砍117億", 53, True),
        ("2026-10-01", "國巨買128億！群創一日倒轉51億", 61, True),
    ]
)


class SplitThemeTest(unittest.TestCase):
    def test_scene_keeps_only_positive_description(self):
        scene, rules = split_visual_theme(THEME)
        self.assertTrue(scene.startswith("實驗室／滴定管漫畫（漲紅跌綠）"))
        self.assertLessEqual(len(scene), 151)
        for word in ("禁止", "華通", "燈塔"):
            self.assertNotIn(word, scene)
        # A/B cover-text layout notes are cover directions: they stay for the thumbnail prompt
        self.assertIn("A第一行金額、第二行反差，B短句。", scene)
        self.assertIn("禁止買A、B才是量第一（9/30；可寫量王收黑）", rules)  # parentheses not split

    def test_clause_level_split_keeps_visual_directions(self):
        theme = (
            "粗線條半色調漫畫、高對比紅綠，禁止真實公司Logo。今天週二，明天開盤可以說。"
            "但不要使用 Marvel 角色。畫面要有高科技感、清楚大字。上一交易日說上週四，禁止AMD開場，改用台積電"
        )
        scene, rules = split_visual_theme(theme)
        self.assertEqual(scene, "粗線條半色調漫畫、高對比紅綠。畫面要有高科技感、清楚大字。")
        self.assertIn("今天週二，明天開盤可以說", rules)  # narration rule stays one clause
        self.assertIn("不要使用 Marvel 角色", rules)
        self.assertIn("禁止AMD開場，改用台積電", rules)
        types = {i["pattern"]: (i["type"], bool(i.get("positive"))) for i in parse_ban_clauses(rules, "2026-10-13")}
        self.assertEqual(types["真實公司Logo"], ("visual_rule", False))
        self.assertEqual(types["使用 Marvel 角色"], ("visual_rule", False))
        self.assertEqual(types["今天週二，明天開盤可以說"], ("title_rule", True))
        self.assertEqual(types["AMD開場，改用台積電"], ("title_rule", False))
        # negatives in mid-clause never stay in the image scene either
        scene, rules = split_visual_theme("海港漫畫，南亞科不要當主角，封面不要放Logo。博士看雷達")
        self.assertEqual(scene, "海港漫畫。博士看雷達。")
        types = {i["pattern"]: (i["type"], bool(i.get("positive"))) for i in parse_ban_clauses(rules, "2026-10-13")}
        self.assertEqual(types, {"南亞科不要當主角": ("title_rule", True), "封面不要放Logo": ("visual_rule", False)})

    def test_english_style_is_cut_at_a_word_boundary(self):
        style = (
            "Cinematic neon trading floor at night with glowing candlestick holograms, rain on glass windows, "
            "a calm professor detective pointing at a giant red arrow while a nervous investor clutches his phone"
        )
        scene = scene_only(style)
        self.assertLessEqual(len(scene), 150)
        self.assertTrue(style.startswith(scene))
        self.assertIn(style[len(scene)], " ,")  # never mid-word
        self.assertFalse(scene.endswith("。"))

    def test_clause_types(self):
        _, rules = split_visual_theme(THEME)
        items = parse_ban_clauses(rules, datetime.date(2026, 10, 12))
        by_type = {}
        for item in items:
            by_type.setdefault(item["type"], []).append(item["pattern"])
        self.assertIn("量第一收黑", by_type["title_structure"])
        self.assertEqual(sorted(by_type["stock_hero"]), ["華通", "華邦電"])
        self.assertIn("燈塔", by_type["visual_object"])
        self.assertIn("誰在收／誰在接", by_type["title_rule"])
        src = {i["pattern"]: i.get("source_date") for i in items if i["type"] == "title_structure"}
        self.assertEqual(src["投信倒"], "2026-10-07")

    def test_image_prompt_style_has_no_bans(self):
        style = _normalize_custom_style(THEME)
        self.assertIn("實驗室", style)
        for word in ("禁止", "華通", "燈塔", "誰在收"):
            self.assertNotIn(word, style)


class RegistryTest(unittest.TestCase):
    def test_ttl_exemptions_caps_and_no_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "packaging_bans.json")
            scene, state = apply_visual_theme(THEME, "2026-10-12", path=path, window=WINDOW)
            self.assertNotIn("禁止", scene)
            status = {(b["type"], b["pattern"]): b["status"] for b in state["bans"]}
            self.assertEqual(status[("title_structure", "買A、B才是量第一")], "expired")  # 9/30 + 5 sessions
            self.assertEqual(status[("title_structure", "投信倒")], "exempt")  # 583 views ≥ q75
            self.assertEqual(status[("title_structure", "逆勢噴")], "exempt")  # source < 72h old
            self.assertEqual(status[("stock_hero", "華邦電")], "exempt")
            self.assertEqual(status[("stock_hero", "華通")], "active")
            prompt = format_bans_for_title_prompt(state)
            self.assertNotIn("燈塔", prompt)  # scene nouns never reach a prompt
            self.assertIn("已豁免", prompt)
            self.assertIn("燈塔", recent_visual_objects(state))
            title_bans = [b for b in active_bans(state) if not b.get("positive")]
            self.assertLessEqual(len(title_bans), 8)
            expires = {b["pattern"]: b["expires"] for b in state["bans"]}
            # re-pasting the same list days later never extends a ban
            _, later = apply_visual_theme(THEME, "2026-10-14", path=path, window=WINDOW)
            self.assertEqual({b["pattern"]: b["expires"] for b in later["bans"] if b["pattern"] in expires}, expires)
            _, much_later = apply_visual_theme(THEME, "2026-10-21", path=path, window=WINDOW)
            self.assertEqual([b for b in active_bans(much_later) if b["type"] == "stock_hero"], [])
            with open(path, encoding="utf-8") as f:
                self.assertIn("bans", json.load(f))

    def test_positive_instruction_is_active_on_every_paste_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "packaging_bans.json")
            theme = "水庫漫畫。上一交易日說上週四；禁止AMD開場。"
            for day in ("2026-10-12", "2026-10-13"):
                _, state = apply_visual_theme(theme, day, path=path, window=WINDOW)
                prompt = format_bans_for_title_prompt(state)
                self.assertIn("[今日操作者指示] 上一交易日說上週四", prompt, day)
                self.assertIn("AMD開場", prompt)
            ban = next(b for b in state["bans"] if b["pattern"] == "AMD開場")
            self.assertEqual(ban["created"], "2026-10-12")  # real bans still never extended
            # next session without that instruction: gone from the prompt, AMD ban still active
            _, state = apply_visual_theme("水庫漫畫。禁止AMD開場。", "2026-10-14", path=path, window=WINDOW)
            prompt = format_bans_for_title_prompt(state)
            self.assertNotIn("上一交易日說上週四", prompt)
            self.assertIn("AMD開場", prompt)
            # same-day rerun without --visual-theme keeps today's instruction
            _, state = apply_visual_theme("海港漫畫。今天週三，明天開盤可以說", "2026-10-14", path=path, window=WINDOW)
            _, state = apply_visual_theme("", "2026-10-14", path=path, window=WINDOW)
            self.assertIn("今天週三，明天開盤可以說", format_bans_for_title_prompt(state))
            # a new paste that has no instructions replaces them
            _, state = apply_visual_theme("海港漫畫。", "2026-10-14", path=path, window=WINDOW)
            self.assertNotIn("明天開盤可以說", format_bans_for_title_prompt(state))

    def test_visual_rules_never_reach_the_title_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "packaging_bans.json")
            _, state = apply_visual_theme("海港漫畫，禁止真實公司Logo；但不要使用 Marvel 角色", "2026-10-13", path=path)
            prompt = format_bans_for_title_prompt(state)
            self.assertNotIn("Logo", prompt)
            self.assertNotIn("Marvel", prompt)
            self.assertEqual(sorted(recent_visual_rules(state)), ["使用 Marvel 角色", "真實公司Logo"])

    def test_broken_registry_degrades_to_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "packaging_bans.json")
            with open(path, "w") as f:
                f.write("{not json")
            self.assertEqual(load_bans(path)["bans"], [])
            scene, state = apply_visual_theme("海港漫畫", "2026-10-12", path=path)
            self.assertEqual(scene, "海港漫畫。")
            self.assertEqual(state["bans"], [])


class TitleBanHitsTest(unittest.TestCase):
    """Bans parsed from real operator phrasing must still match the titles they ban."""

    def _state(self, *clauses):
        items = parse_ban_clauses(list(clauses), "2026-10-14")
        return {"bans": [dict(item, status="active") for item in items]}

    def test_bans_with_a_leading_verb_still_match(self):
        cases = (
            ("不要用AMD開場", "AMD狂飆5%！台積電續強｜AI帶你看股市"),
            ("不要再用被砍同構", "景碩買48億！台積電卻被砍117億｜AI帶你看股市"),
            ("禁止用「誰在收」", "誰在收？外資買超創意30億"),
            ("禁止寫「誰在收」", "外資賣、誰在收？"),
            ("不要使用「外資狂掃」同構（10/9）", "外資狂掃創意30億｜AI帶你看股市"),
            ("勿用「漲停」字眼", "群創漲停才是量第一"),
            ("不要讓華通當標題主角", "華通買10億！"),
        )
        for clause, title in cases:
            state = self._state(clause)
            self.assertTrue(title_ban_hits(title, state), (clause, state["bans"]))

    def test_opener_and_clean_titles_do_not_match(self):
        state = self._state("不要用AMD開場", "不要再用被砍同構", "禁止用「誰在收」", "不要讓華通當標題主角")
        # AMD outside the opening clause is not an AMD opener.
        self.assertEqual(title_ban_hits("台積電買175億！AMD財報前觀望", state), [])
        self.assertEqual(title_ban_hits("南亞科買159億！友達量第一卻摔", state), [])
        self.assertEqual(title_ban_hits("台積電買175億！華通跟漲", state), [])


class PlannerAndDirectorTest(unittest.TestCase):
    def test_formula_lines_are_computed_not_hard_coded(self):
        lines = _packaging_formula_lines(WINDOW)
        joined = "\n".join(lines)
        self.assertNotIn("時事情緒鉤子", joined)
        self.assertIn("頻道中位數", joined)
        empty = "\n".join(_packaging_formula_lines({"sample": []}))
        self.assertIn("不宣稱", empty)

    def test_asset_record_selects_nothing_at_planning_time(self):
        record = build_video_asset_record({}, {"titles": ["A 標題", "B 標題"]}, "2026-10-12")
        self.assertFalse(any(v["selected"] for v in record["title_variants"]))

    def test_director_card_delivers_title_promise(self):
        card = build_compact_notebooklm_prompt(
            {"date": "2026-10-12"}, "2026-10-12", title_a="創意買30億！股價跌3.1%｜10/12 AI帶你看股市"
        )
        self.assertIn("先兌現標題「創意買30億！股價跌3.1%」", card)
        self.assertIn("標題承諾（前 30 秒先兌現）：創意買30億！股價跌3.1%", card)
        plain = build_compact_notebooklm_prompt({"date": "2026-10-12"}, "2026-10-12")
        self.assertNotIn("先兌現標題「", plain)

    def test_director_card_cover_gets_scene_only_from_any_caller(self):
        # regen_notebooklm passes the raw --visual-theme / DAILY_VISUAL_THEME straight in
        card = build_compact_notebooklm_prompt(
            {"date": "2026-10-12"}, "2026-10-12", visual_theme=THEME, title_a="創意買30億！股價跌3.1%"
        )
        self.assertIn("封面視覺主題請與 YouTube 封面一致：實驗室／滴定管漫畫", card)
        for word in ("華通", "燈塔", "誰在收", "AMD開場", "美股紅跌綠漲"):
            self.assertNotIn(word, card)


if __name__ == "__main__":
    unittest.main()
