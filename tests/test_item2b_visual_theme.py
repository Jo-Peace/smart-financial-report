#!/usr/bin/env python3
"""
Item 2b (Task A): automatic daily /2 visual theme — local scene library,
archetype classifier, recency / ban exclusion, date-seeded choice and the
explicit → logged → auto → 財經戰情室 resolution in regen_assets.

Fixture: tests/fixtures/item2_2026-10-14_facts.json (real 10/14 /1 numbers:
南亞 +55.3億 buy #1 +9.40%; 台積電 -168.1億 sell #1, 0.00%).
"""
import copy
import json
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

from modules import visual_scene  # noqa: E402
from modules.packaging_bans import scene_only  # noqa: E402
from modules.thumbnail_generator import _is_sports_anime_theme  # noqa: E402
from modules.title_fact_gate import build_fact_index, check_text, has_errors  # noqa: E402

FIXTURE = os.path.join(BASE_DIR, "tests", "fixtures", "item2_2026-10-14_facts.json")
TITLE_A = "南亞買 55.3 億噴 9%！南亞科卻遭砍 110 億？AI 基礎材料換手真相｜10/14 AI帶你看股市"
TITLE_B = "台積電砍 168 億卻守平盤？南亞買 55 億奪冠，這類 AI 材料要接棒了嗎？｜10/14 AI帶你看股市"

HEADER = (
    "| 日期 | 核心標的 | 預測方向 | 隔日驗收 | 視覺風格 | 標題方向 | 影片ID | 使用標題 | 觀看數 |\n"
    "|------|---------|---------|---------|---------|---------|--------|---------|--------|\n"
)
# The five TWSE sessions before 10/14 (10/09–10/10 closed) with their real scenes.
PREVIOUS = [
    ("2026-10-05", "溫室／遮光簾漫畫（漲紅跌綠）：大盤紅色日照燈亮到漲1236點"),
    ("2026-10-06", "氣象站／雨量筒漫畫（漲紅跌綠）：聯發科綠色雨雲壓下202億。禁止行李轉盤／保稅倉／填息油漆"),
    ("2026-10-07", "燈塔／雙光束漫畫（漲紅跌綠）：華邦電塔身被外資紅色光束灌進37億"),
    ("2026-10-08", "窯廠／出窯口漫畫（漲紅跌綠）：南亞科紅色窯火噴出112億"),
    ("2026-10-12", "實驗室／滴定管漫畫（漲紅跌綠）：創意燒杯被紅色試劑灌進30億"),
    ("2026-10-13", "水庫／洩洪閘漫畫（漲紅跌綠）：台積電綠色閘門一次洩出639億"),
]


# Real channel_story_log 視覺風格 cells (verbatim). 10/01 / 09/23 / 09/22 open with a
# clause that mentions 「昨天／上週」, which the /2 scene split treats as a title rule;
# 10/13 carries the operator's cumulative scene ban list.
REAL_0922 = "超商退貨櫃檯漫畫（漲紅跌綠）：南亞科綠色退貨箱116億蓋退貨章、上週進貨標被劃掉；友達紅色熱銷箱82億連補兩日、量燈亮編號1"
REAL_0923 = (
    "雲霄飛車／俯衝軌道漫畫（漲紅跌綠）：友達紅色車廂從昨天高點俯衝綠軌-5.32%、量燈仍亮編號1；"
    "台積電紅色維修棚175億安靜推進、場次燈沒亮編號。博士看高度計，韭菜抓安全桿"
)
REAL_1001 = (
    "印刷廠／隔夜改版漫畫（漲紅跌綠）：群創昨天的紅色印版被整版改印成綠色51億、量燈仍亮編號2；"
    "國巨紅色清樣128億在隔音校對室印完噴9.85%，大廳叫號才排到第16。博士拿昨天紅樣對今天綠樣，韭菜還握著昨天的漲停傳單。"
    "A兩行股名+金額，B短句大字。禁止美股紅跌綠漲；禁止AMD開場；禁止外資狂掃／狂砍開場；禁止賣A卻買B同構（9/29已用）；"
    "禁止買A、B才是量第一同構（9/30已用）；禁止影子建倉／榜外吸籌當標題；禁止誰在收；禁止被砍連用；"
    "禁止直播間打賞／夾娃娃機／車站剪票／雲霄飛車／超商退貨／電影院／晶圓盒／填息油漆／保稅倉／行李轉盤。"
)
REAL_1013 = (
    "水庫／洩洪閘漫畫（漲紅跌綠）：台積電綠色閘門一次洩出639億，水位尺只降不到2%。博士看閘門，韭菜盯水位。"
    "A第一行金額、第二行反差，B短句。今天週二，明天開盤可以說。禁止美股紅跌綠漲；禁止AMD開場；禁止外資狂掃／狂砍開場；"
    "禁止賣A卻買B（9/29）；禁止買A、台積電卻被砍（9/24）；禁止買A、B才是量第一（9/30）；禁止量第一收黑（10/5）；"
    "禁止鎖漲停、權值少（10/2）；禁止一日倒轉（10/1）；禁止大盤仍漲同構（10/6）；禁止投信倒同構（10/7）；"
    "禁止逆勢噴同構（10/8低樣本）；禁止股價跌同構（10/12低樣本）；禁止沒進量榜／榜外吸籌當標題；"
    "禁止誰在收／誰在接／誰在買／誰在逃；禁止被砍；禁止華通、聯發科、華邦電、南亞科當標題主角；"
    "禁止實驗室滴定管／窯廠／燈塔／氣象站雨量筒／溫室遮光簾／電錶箱／印刷廠／直播間打賞／夾娃娃機／車站剪票／"
    "雲霄飛車／超商退貨／電影院／晶圓盒／填息油漆／保稅倉／行李轉盤。"
)


def _sd():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def _scene(scene_id):
    return next(s for s in visual_scene.load_library()["scenes"] if s["id"] == scene_id)


def _row(date, theme):
    return f"| {date} | 南亞 | 南亞(多) | 待驗收 | {theme} | 上漲 | - | - | 待更新 |\n"


def _write_log(path, rows, today_theme="待 /2 設定"):
    with open(path, "w", encoding="utf-8") as f:
        f.write("# 頻道敘事紀錄\n\n" + HEADER)
        for date, theme in rows:
            f.write(_row(date, theme))
        if today_theme is not None:
            f.write(_row("2026-10-14", today_theme))


def _quiet(fn, *args, **kwargs):
    with redirect_stdout(StringIO()):
        return fn(*args, **kwargs)


class LibraryTests(unittest.TestCase):
    def test_library_has_forty_everyday_scenes_with_templates(self):
        library = visual_scene.load_library()
        scenes = library["scenes"]
        self.assertGreaterEqual(len(scenes), 40)
        self.assertEqual(len({s["id"] for s in scenes}), len(scenes))
        known = set(visual_scene.ARCHETYPE_ORDER)
        for scene in scenes:
            with self.subTest(scene=scene["id"]):
                self.assertTrue(scene["label"])
                self.assertTrue(set(scene["archetypes"]) <= known)
                for side in ("buy", "sell", "divergence"):
                    self.assertIn("{hero}", scene["templates"][side])
                    self.assertIn("{amount}", scene["templates"][side])
                self.assertIn("{pct}", scene["templates"]["divergence"])
                self.assertFalse(_is_sports_anime_theme(scene["label"] + json.dumps(scene, ensure_ascii=False)))
        for archetype in known:
            self.assertGreaterEqual(sum(archetype in s["archetypes"] for s in scenes), 6, archetype)

    def test_every_scene_composes_a_fact_checked_theme(self):
        sd = _sd()
        facts = build_fact_index(sd)
        for titles in ([TITLE_A], [TITLE_B]):
            ctx = visual_scene.build_context(sd, titles=titles, facts=facts)
            for scene in visual_scene.load_library()["scenes"]:
                with self.subTest(scene=scene["id"], hero=ctx["hero"]["name"]):
                    theme, with_other, _cast = visual_scene._compose_checked(scene, ctx, "換手")
                    self.assertTrue(theme)
                    self.assertTrue(with_other)
                    self.assertLessEqual(len(theme), visual_scene.MAX_CHARS)
                    self.assertEqual(scene_only(theme).rstrip("。"), theme)  # no clause is split off
                    metaphor = theme.split("：", 1)[1]
                    self.assertFalse(has_errors(check_text(metaphor.split("；")[0], facts=facts)), theme)


class ArchetypeTests(unittest.TestCase):
    def setUp(self):
        self.sd = _sd()
        self.facts = build_fact_index(self.sd)

    def _names(self, sd, titles):
        facts = build_fact_index(sd)
        ctx = visual_scene.build_context(sd, titles=titles, facts=facts)
        return [row["archetype"] for row in ctx["archetypes"]]

    def test_rotation_day(self):
        names = self._names(self.sd, [TITLE_A])
        self.assertEqual(names[0], "換手")
        self.assertIn("單股暴衝", names)  # 南亞 +9.40% on a 55.3億 buy

    def test_index_extremes_and_divergence_come_first(self):
        sd = copy.deepcopy(self.sd)
        sd["index_action"] = "下跌 980.0點（-2.10%）"
        self.assertEqual(self._names(sd, [TITLE_A])[0], "大跌守線")
        sd = copy.deepcopy(self.sd)
        sd["local_market_facts"]["stocks"]["1303"]["pct_change"] = -3.1
        self.assertEqual(self._names(sd, [TITLE_A])[0], "量價背離")
        # 台積電 -168億 but flat: a heavyweight flow that the price ignored
        self.assertEqual(self._names(self.sd, [TITLE_B])[0], "量價背離")

    def test_quiet_day(self):
        sd = {
            "index_action": "上漲 20.0點（+0.10%）",
            "safe_havens_bought": ["南亞 (+5.3億)"],
            "heavyweights_dumped": ["台積電 (-8.1億)"],
            "local_market_facts": {"stocks": {"1303": {"ticker": "1303", "name": "南亞", "foreign_amount": 5.3, "pct_change": 0.8}}},
        }
        self.assertEqual(self._names(sd, ["南亞買5.3億"]), ["盤整觀望"])


class AutoThemeTests(unittest.TestCase):
    def setUp(self):
        self.sd = _sd()
        self.tmp = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmp.name, "channel_story_log.md")
        _write_log(self.log, PREVIOUS)

    def tearDown(self):
        self.tmp.cleanup()

    def _auto(self, date="2026-10-14", bans_state=None, titles=(TITLE_A, TITLE_B)):
        return visual_scene.auto_visual_theme(date, self.sd, titles=list(titles), log_path=self.log, bans_state=bans_state)

    def test_b1_theme_skips_recent_scenes_and_names_the_cover_facts(self):
        result = self._auto()
        theme = result["theme"]
        self.assertLessEqual(len(theme), 150)
        self.assertIn("漫畫（漲紅跌綠）：", theme)
        # no old two-variable cover-text note: the week's A/B hypothesis sets the cover text
        for note in ("A兩行", "B短句", "大字"):
            self.assertNotIn(note, theme)
        # never one of the last 5 sessions' scenes (10/06 氣象站 … 10/13 水庫)
        for word in ("氣象站", "燈塔", "窯廠", "實驗室", "水庫"):
            self.assertNotIn(word, result["label"])
        self.assertEqual(set(result["excluded_recent"]), {"weather_station", "lighthouse", "kiln", "titration_lab", "reservoir"})
        # 10/05 is the 6th session back: not excluded by the 5-session rule
        self.assertNotIn("greenhouse", result["excluded_recent"])
        # older ban lists inside a logged theme do not count as used scenes
        self.assertNotIn("baggage_carousel", result["excluded_recent"])
        # the same hero / number the cover prints, in Taiwan colours
        self.assertEqual(result["hero"]["key"], "1303")
        self.assertIn("南亞紅色", theme)
        self.assertIn("買超55.3億", theme)
        self.assertIn("噴9.4%", theme)
        # the counterpart is the one title A names (南亞科卻遭砍 110 億), not the sell leader 台積電
        self.assertEqual((result["other"]["key"], result["other"]["source"]), ("2408", "title_a"))
        self.assertIn("南亞科綠色", theme)
        self.assertIn("賣超110億", theme)
        self.assertNotIn("台積電", theme)
        self.assertLess(theme.index("南亞科綠色"), theme.index("南亞紅色"))  # 換手: the money leaves first
        self.assertEqual(result["archetype"], "換手")

    def test_counterpart_falls_back_to_the_opposite_leader(self):
        result = self._auto(titles=("南亞買 55.3 億噴 9%！AI 材料搶籌｜10/14 AI帶你看股市",))
        self.assertEqual((result["other"]["key"], result["other"]["source"]), ("2330", "leaderboard"))
        self.assertIn("台積電綠色", result["theme"])
        self.assertIn("賣超168.1億", result["theme"])

    def test_same_date_same_theme(self):
        self.assertEqual(self._auto()["theme"], self._auto()["theme"])
        themes = {self._auto(date=f"2026-10-{day:02d}")["scene_id"] for day in (14, 15, 16, 19, 20, 21)}
        self.assertGreater(len(themes), 1)  # seeded by the date, not a constant

    def test_banned_scene_is_skipped(self):
        first = self._auto()
        keyword = visual_scene.scene_keywords(next(s for s in visual_scene.load_library()["scenes"] if s["id"] == first["scene_id"]))[0]
        bans = {"bans": [{"type": "visual_object", "pattern": keyword, "status": "active"}]}
        second = self._auto(bans_state=bans)
        self.assertNotEqual(second["scene_id"], first["scene_id"])
        self.assertIn(first["scene_id"], second["excluded_banned"])

    def test_no_hero_amount_means_no_auto_theme(self):
        sd = {"index_action": "上漲 20點（+0.10%）"}
        self.assertIsNone(visual_scene.auto_visual_theme("2026-10-14", sd, titles=[], log_path=self.log))

    def test_sell_hero_divergence_theme(self):
        result = self._auto(titles=(TITLE_B,))
        theme = result["theme"]
        self.assertEqual(result["hero"]["key"], "2330")
        self.assertIn("台積電綠色", theme)
        self.assertIn("賣超168億", theme)  # the cover repeats the title's wording of -168.13億
        self.assertIn("卻守平盤", theme)
        # colour marks the flow (green = 賣超) on a flat stock; the price is in words
        self.assertIn("南亞紅色", theme)
        self.assertIn("買超55億", theme)  # title B's own wording of 南亞's 55.33億


class RealLogTests(unittest.TestCase):
    """Real 10/14 situation: operator ban lists and old-format rows from the real channel log."""

    def setUp(self):
        self.sd = _sd()
        self.tmp = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmp.name, "channel_story_log.md")

    def tearDown(self):
        self.tmp.cleanup()

    def test_label_is_read_even_when_the_first_clause_mentions_yesterday(self):
        self.assertEqual(visual_scene.theme_scene_head(REAL_1001), "印刷廠／隔夜改版漫畫（漲紅跌綠）")
        self.assertEqual(visual_scene.theme_scene_head(REAL_0923), "雲霄飛車／俯衝軌道漫畫（漲紅跌綠）")
        self.assertEqual(visual_scene.theme_scene_head(REAL_0922), "超商退貨櫃檯漫畫（漲紅跌綠）")
        # a row without a label: its ban list never counts as a use
        head = visual_scene.theme_scene_head("國巨紅色清樣128億在校對室印完。禁止印刷廠／電影院／夾娃娃機")
        self.assertNotIn("印刷廠", head)

    def test_old_rows_count_in_the_twenty_session_window(self):
        _write_log(self.log, [("2026-09-22", REAL_0922), ("2026-09-23", REAL_0923), ("2026-10-01", REAL_1001)])
        fresh_window = visual_scene.recent_theme_cells(self.log, "2026-10-14", visual_scene.FRESH_SESSIONS)
        self.assertEqual(visual_scene.scene_used_in(_scene("print_shop"), fresh_window), ["2026-10-01"])
        self.assertEqual(visual_scene.scene_used_in(_scene("roller_coaster"), fresh_window), ["2026-09-23"])
        self.assertEqual(visual_scene.scene_used_in(_scene("store_returns"), fresh_window), ["2026-09-22"])
        # 10/01's own ban list names 雲霄飛車／超商退貨…, but it is 6+ sessions back: no ban, only "used"
        self.assertEqual(visual_scene.logged_scene_bans(self.log, "2026-10-14"), [])
        sd = copy.deepcopy(self.sd)
        sd["local_market_facts"]["stocks"]["1303"]["pct_change"] = -3.1  # 南亞 bought but falls: 量價背離
        result = visual_scene.auto_visual_theme("2026-10-14", sd, titles=[TITLE_A, TITLE_B], log_path=self.log)
        self.assertEqual(result["archetype"], "量價背離")
        self.assertNotIn(result["scene_id"], {"print_shop", "roller_coaster"})  # fresh scenes come first

    def test_operator_ban_list_of_the_last_sessions_is_honoured(self):
        rows = list(PREVIOUS[:-1]) + [("2026-10-13", REAL_1013)]
        _write_log(self.log, [("2026-10-01", REAL_1001)] + rows)
        bans = visual_scene.logged_scene_bans(self.log, "2026-10-14")
        for word in ("印刷廠", "雲霄飛車", "超商退貨", "電錶箱", "行李轉盤"):
            self.assertIn(word, bans)
        self.assertNotIn("美股紅跌綠漲", bans)  # only scene-object lists, not title / colour rules
        sd = copy.deepcopy(self.sd)
        sd["local_market_facts"]["stocks"]["1303"]["pct_change"] = -3.1
        for titles in ([TITLE_A, TITLE_B], [TITLE_B]):
            for case in (self.sd, sd):
                with self.subTest(titles=titles[0][:6], pct=case["local_market_facts"]["stocks"]["1303"]["pct_change"]):
                    result = visual_scene.auto_visual_theme("2026-10-14", case, titles=titles, log_path=self.log)
                    for banned in ("print_shop", "roller_coaster", "store_returns", "electric_meter", "train_station"):
                        self.assertNotEqual(result["scene_id"], banned)
                        self.assertIn(banned, result["excluded_banned"])
                    self.assertNotIn("印刷廠", result["theme"])
        # the bans lapse 5 TWSE sessions after the operator's last paste (10/13)
        self.assertEqual(visual_scene.logged_scene_bans(self.log, "2026-10-21"), [])


class ResolveVisualThemeTests(unittest.TestCase):
    def setUp(self):
        from scripts.youtube import regen_assets

        self.regen = regen_assets
        self.sd = _sd()
        self.tmp = tempfile.TemporaryDirectory()
        self.reports = os.path.join(self.tmp.name, "reports")
        os.makedirs(self.reports)
        self.log = os.path.join(self.tmp.name, "channel_story_log.md")
        self.bans = os.path.join(self.tmp.name, "packaging_bans.json")
        _write_log(self.log, PREVIOUS)
        self.patches = [
            mock.patch.object(regen_assets, "CHANNEL_STORY_LOG", self.log),
            mock.patch.object(regen_assets, "REPORTS_DIR", self.reports),
            # hermetic: the learning window never opens data/analytics.db
            mock.patch("modules.youtube_learning.load_metric_series", return_value={}),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def _choose(self, raw="", **kwargs):
        kwargs.setdefault("structured_data", self.sd)
        kwargs.setdefault("titles", [TITLE_A, TITLE_B])
        out = StringIO()
        with redirect_stdout(out):
            choice = self.regen.choose_visual_theme(raw, "2026-10-14", bans_path=self.bans, **kwargs)
        choice["log"] = out.getvalue()
        return choice

    def _set_today(self, theme):
        self.regen._update_channel_story_visual_theme("2026-10-14", theme)

    def test_blank_theme_is_the_auto_scene_and_is_logged(self):
        choice = self._choose()
        self.assertEqual(choice["source"], "auto")
        self.assertIn("auto", choice["log"])
        self.regen.record_theme_choice("2026-10-14", choice)
        _quiet(self._set_today, choice["theme"])
        # rerun: the logged auto theme is re-resolved from the same facts → same theme
        again = self._choose()
        self.assertEqual(again["source"], "auto")
        self.assertEqual(again["theme"], choice["theme"])
        # a rerun whose titles name another hero gets a scene for that hero
        other = self._choose(titles=[TITLE_B])
        self.assertEqual(other["source"], "auto")
        self.assertIn("台積電", other["theme"].split("：", 1)[1].split("，")[0])

    def test_b4_explicit_wins_and_logged_operator_theme_is_kept(self):
        choice = self._choose("貨櫃碼頭漫畫。禁止AMD開場。")
        self.assertEqual(choice["source"], "explicit")
        self.assertEqual(choice["theme"].rstrip("。"), "貨櫃碼頭漫畫")
        self.regen.record_theme_choice("2026-10-14", choice)
        _quiet(self._set_today, choice["theme"])
        kept = self._choose()
        self.assertEqual(kept["source"], "logged")
        self.assertEqual(kept["theme"], "貨櫃碼頭漫畫")
        # an explicit theme always beats today's logged one
        self.assertEqual(self._choose("水庫漫畫")["theme"].rstrip("。"), "水庫漫畫")

    def test_explicit_war_room_is_kept_but_a_legacy_default_is_replaced(self):
        _quiet(self._set_today, "財經戰情室")
        self.assertEqual(self._choose()["source"], "auto")  # pre-auto default, nobody chose it
        choice = self._choose("財經戰情室")
        self.assertEqual(choice["source"], "explicit")
        self.regen.record_theme_choice("2026-10-14", choice)
        self.assertEqual(self._choose()["source"], "logged")

    def test_old_cover_text_note_is_dropped_from_operator_themes(self):
        choice = self._choose("水庫／洩洪閘漫畫（漲紅跌綠）：台積電綠色閘門洩出168億；A兩行股名+金額，B短句大字")
        self.assertEqual(choice["source"], "explicit")
        self.assertEqual(choice["theme"].rstrip("。"), "水庫／洩洪閘漫畫（漲紅跌綠）：台積電綠色閘門洩出168億")
        self.regen.record_theme_choice("2026-10-14", choice)
        _quiet(self._set_today, choice["theme"])
        kept = self._choose()
        self.assertEqual(kept["source"], "logged")
        self.assertNotIn("B短句", kept["theme"])
        section = self.regen._notebooklm_cover_section_from_thumbnail("", "燈塔漫畫。A第一行金額、第二行反差，B短句。")
        self.assertIn("封面視覺主題請與 YouTube 封面一致：燈塔漫畫。", section)
        self.assertNotIn("B短句", section)

    def test_theme_follows_the_cover_hero_when_titles_come_later(self):
        auto = self._choose(titles=[])  # --skip-youtube without a package: hero from the watch list
        self.assertEqual(auto["auto"]["hero"]["key"], "1303")
        covers = {"titles": [TITLE_B], "hero": {"key": "2330"}}
        self.assertTrue(self.regen.theme_needs_cover_hero(auto, covers))
        self.assertFalse(self.regen.theme_needs_cover_hero(auto, {"titles": [TITLE_A], "hero": {"key": "1303"}}))
        fallback = {"theme": "財經戰情室", "source": "fallback", "bans_state": None, "auto": None}
        self.assertTrue(self.regen.theme_needs_cover_hero(fallback, covers))
        explicit = {"theme": "水庫漫畫", "source": "explicit", "bans_state": None, "auto": None}
        self.assertFalse(self.regen.theme_needs_cover_hero(explicit, covers))
        with redirect_stdout(StringIO()):
            final = self.regen.rechoose_theme_for_titles(auto, "", "2026-10-14", self.sd, covers["titles"])
        self.assertEqual(final["auto"]["hero"]["key"], "2330")
        self.assertFalse(self.regen.theme_needs_cover_hero(final, covers))

    def test_fallback_without_facts(self):
        choice = self._choose(structured_data=None, titles=[])
        self.assertEqual((choice["source"], choice["theme"]), ("fallback", "財經戰情室"))

    def test_rechoose_for_final_titles_only_touches_generated_themes(self):
        auto = self._choose(titles=[])  # no package yet: hero from the watch list (南亞)
        self.assertEqual(auto["source"], "auto")
        with redirect_stdout(StringIO()):
            final = self.regen.rechoose_theme_for_titles(auto, "", "2026-10-14", self.sd, [TITLE_B])
        self.assertIn("台積電", final["theme"])
        explicit = {"theme": "水庫漫畫", "source": "explicit", "bans_state": None, "auto": None}
        self.assertIs(self.regen.rechoose_theme_for_titles(explicit, "水庫漫畫", "2026-10-14", self.sd, [TITLE_B]), explicit)


class ReadOnlyAnalyticsTests(unittest.TestCase):
    """Resolving a /2 theme (learning window) must never write analytics.db."""

    def setUp(self):
        import sqlite3

        from modules.analytics_store import AnalyticsStore

        self.sqlite3 = sqlite3
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "analytics.db")
        AnalyticsStore(self.db)
        conn = sqlite3.connect(self.db)
        conn.execute("DROP TABLE maintenance_log")  # a DB from before the one-time cleanup marker
        rows = [
            ("2026-10-14", "2002", "中鋼", "buy", 10, 15.02),
            ("2026-10-14", "2313", "華通", "buy", 10, 11.6),  # stale row of an earlier run
        ]
        conn.executemany(
            "INSERT INTO institutional_flows (flow_date, ticker, name, side, rank, est_amount, raw_json) VALUES (?,?,?,?,?,?,'{}')",
            rows,
        )
        conn.execute(
            "INSERT INTO youtube_video_metrics (run_date, measured_at, video_id, hours_since_publish, views, published_at) "
            "VALUES ('2026-10-13', '2026-10-14 15:00:00', 'vid1', 20.0, 18, '2026-10-13 19:00:00')"
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def _digest(self):
        import hashlib

        with open(self.db, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def test_learning_window_reads_without_writing(self):
        from modules.youtube_learning import load_metric_series

        before = self._digest()
        series = load_metric_series(["vid1", "missing"], db_path=self.db)
        self.assertEqual(series["vid1"]["series"], [(20.0, 18)])
        self.assertEqual(series["vid1"]["published_at"], "2026-10-13 19:00:00")
        self.assertEqual(self._digest(), before)  # no migration, no stale-row cleanup, no marker
        self.assertEqual(load_metric_series(["vid1"], db_path=os.path.join(self.tmp.name, "none.db")), {})
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "none.db")))  # never created

    def test_stale_flow_cleanup_runs_once_per_db(self):
        from modules.analytics_store import AnalyticsStore

        with redirect_stdout(StringIO()):
            AnalyticsStore(self.db)
        conn = self.sqlite3.connect(self.db)
        buys = conn.execute("SELECT ticker FROM institutional_flows WHERE side='buy' ORDER BY rank").fetchall()
        marker = conn.execute("SELECT detail FROM maintenance_log WHERE name='drop_stale_flow_rows_v1'").fetchone()
        conn.close()
        self.assertEqual([r[0] for r in buys], ["2002"])
        self.assertIn("2313", marker[0])
        before = self._digest()
        AnalyticsStore(self.db)  # marker present: nothing re-scanned or deleted
        self.assertEqual(self._digest(), before)


if __name__ == "__main__":
    unittest.main()
