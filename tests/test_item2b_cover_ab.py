#!/usr/bin/env python3
"""
Item 2b (Task B): single-variable cover A/B tests.

Covers A and B share hero, verified number, title pairing (title A), scene and
composition; only the week's hypothesis variable differs. The package points to
YouTube Studio 「測試與比較」, results are recorded with record-ab-result, and
learning only promotes an arm after >= 3 decided tests with a majority.
"""
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules import cover_ab_test  # noqa: E402
from modules.thumbnail_generator import generate_ab_test_thumbnails  # noqa: E402
from modules.title_fact_gate import titles_from_package  # noqa: E402
from scripts.youtube import check_youtube_package  # noqa: E402

FIXTURE = os.path.join(BASE_DIR, "tests", "fixtures", "item2_2026-10-14_facts.json")
TITLE_A = "南亞買 55.3 億噴 9%！南亞科卻遭砍 110 億？AI 基礎材料換手真相｜10/14 AI帶你看股市"
TITLE_B = "台積電砍 168 億卻守平盤？南亞買 55 億奪冠，這類 AI 材料要接棒了嗎？｜10/14 AI帶你看股市"
THEME = "收費站／車道閘門漫畫（漲紅跌綠）：台積電綠色車道湧出賣超168.1億，南亞紅色車道放行買超55.3億噴9.4%；A兩行股名+金額，B短句大字"

SHARED_FIELDS = ("Title pairing", "Scenario", "Composition", "Hero (fact source)", "Main stocks", "Scene", "Emotion", "Visual metaphor")

OLD_PACKAGE = """# YouTube 上架素材包 2026-10-14

## 📝 A/B 標題選項（先用 A，48 小時後再判斷是否換 B）

**標題 A（對照組：完整句，先上架）：**
```
{a}
```

**標題 B（實驗組：殘句／問句，同股同數字）：**
```
{b}
```

---

## 📌 上架流程提醒

1. 先用標題 A 上傳
2. 在 Canva 打開縮圖背景，加上今日標題文字
3. 上傳影片，貼上描述和縮圖
4. 發布後立即貼置頂留言
5. 觀看數未達頻道中位數×2（見 youtube_performance_patterns.md）不判斷 A/B 勝負，只記錄觀察
6. 48小時觀看低於頻道中位數且 Studio CTR 低於頻道平均時，再測 B 組（可行時改用 Studio「測試與比較」）


---

## 🎬 今日影片製作計畫

- 未達頻道中位數 ×2（約 285 views）不判斷 A/B 勝負，只記錄觀察。

## 48 小時驗收欄位

| 項目 | 數值 | 判斷 |
|------|------|------|
| CTR |  |  |
| 是否更換 B 組 |  |  |
""".format(a=TITLE_A, b=TITLE_B)


def _sd():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def _field(content, label):
    if label in ("Title pairing", "Scenario", "Composition"):
        match = re.search(rf"^{re.escape(label)}:\s*(.+)$", content, flags=re.MULTILINE)
    else:
        match = re.search(rf"^- {re.escape(label)}:\s*(.+)$", content, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _image_prompt(content):
    return content.split("## Image Prompt", 1)[1].strip()


def _covers(hypothesis, titles=(TITLE_A, TITLE_B), style=THEME):
    # hermetic: the fatigue window never reads the real reports/ cover prompts
    with tempfile.TemporaryDirectory() as tmp, redirect_stdout(StringIO()), mock.patch(
        "modules.packaging_fatigue.iter_thumbnail_prompt_paths", return_value=[]
    ):
        result = generate_ab_test_thumbnails(
            None,
            None,
            "",
            tmp,
            styles=[style],
            structured_data=_sd(),
            run_date="2026-10-14",
            preferred_titles=list(titles),
            ab_hypothesis=hypothesis,
        )
        prompts = []
        for thumb in result["thumbnails"]:
            with open(thumb["path"], encoding="utf-8") as f:
                prompts.append(f.read())
    return result, prompts


class PlanTests(unittest.TestCase):
    def test_plan_has_single_variable_hypotheses(self):
        plan = cover_ab_test.load_plan()
        hypotheses = cover_ab_test.enabled_hypotheses(plan)
        self.assertGreaterEqual(len(hypotheses), 4)
        self.assertEqual(
            {h["variable"] for h in hypotheses} & set(cover_ab_test.LEVER_DEFAULTS),
            {"style", "text", "characters", "number_layout"},
        )
        for h in hypotheses:
            self.assertEqual(set(h["arms"]), {"a", "b"})
            self.assertNotEqual(h["arms"]["a"]["mode"], h["arms"]["b"]["mode"])

    def test_iso_week_rotation_and_overrides(self):
        plan = cover_ab_test.load_plan()
        week = [cover_ab_test.current_hypothesis(f"2026-10-{d}", plan=plan, env={})["id"] for d in (12, 14, 16, 18)]
        self.assertEqual(set(week), {"H1"})  # one hypothesis per ISO week (2026-W42)
        self.assertEqual(cover_ab_test.current_hypothesis("2026-10-19", plan=plan, env={})["id"], "H2")
        self.assertEqual(cover_ab_test.current_hypothesis("2026-11-09", plan=plan, env={})["id"], "H1")  # 4-week cycle
        self.assertEqual(cover_ab_test.current_hypothesis("2026-10-14", plan=plan, env={})["iso_week"], "2026-W42")
        env = {"AB_HYPOTHESIS": "H3"}
        self.assertEqual(cover_ab_test.current_hypothesis("2026-10-14", plan=plan, env=env)["source"], "env")
        self.assertEqual(cover_ab_test.current_hypothesis("2026-10-14", plan=plan, env=env)["id"], "H3")
        self.assertEqual(cover_ab_test.current_hypothesis("2026-10-14", plan=plan, override="text", env=env)["id"], "H2")
        with redirect_stdout(StringIO()):
            self.assertEqual(cover_ab_test.current_hypothesis("2026-10-14", plan=plan, env={"AB_HYPOTHESIS": "H9"})["id"], "H1")


class CoverPairTests(unittest.TestCase):
    def _assert_pair(self, prompts, hypothesis_id, variable):
        a, b = prompts
        for label in SHARED_FIELDS:
            with self.subTest(field=label):
                self.assertEqual(_field(a, label), _field(b, label))
                self.assertTrue(_field(a, label))
        self.assertEqual(_field(a, "Title pairing"), TITLE_A)  # both covers pair with title A
        self.assertIn("1303 南亞", _field(a, "Hero (fact source)"))
        for letter, content in zip("ab", prompts):
            declared = cover_ab_test.parse_variable_line(content)
            self.assertEqual(declared, {"variable": variable, "hypothesis_id": hypothesis_id, "arm": letter})
        self.assertNotEqual(_image_prompt(a), _image_prompt(b))

    def test_b2_style_arm_changes_only_the_visual_world(self):
        result, (a, b) = _covers("H1")
        self._assert_pair((a, b), "H1", "style")
        self.assertEqual(result["hero"]["ticker"], "1303")
        for label in ("Primary", "Secondary"):
            self.assertEqual(_field(a, label), _field(b, label))
        self.assertEqual((_field(a, "Primary"), _field(a, "Secondary")), ("1303 南亞", "外資買55.3億"))
        self.assertIn("收費站／車道閘門", _image_prompt(a))
        self.assertIn("war-room DATA BOARD", _image_prompt(b))
        self.assertNotIn("收費站", _image_prompt(b))
        # the theme's old 「A兩行…，B短句大字」 layout note never reaches an image prompt
        self.assertNotIn("B短句大字", _image_prompt(a))
        # A stages the scenario as the comic scene; B's data board never stages it
        # the rotation counterpart is the stock title A names (南亞科卻遭砍 110 億), as in the auto theme
        self.assertIn("Core situation: 南亞科 遭外資提款 110億", _image_prompt(a))
        self.assertIn("Visual metaphor: 南亞科 票據裂開", _image_prompt(a))
        self.assertNotIn("Core situation: ", _image_prompt(b))
        self.assertNotIn("票據裂開", _image_prompt(b))
        self.assertIn("only as small background data panels", _image_prompt(b))
        self.assertIn("Visual metaphor: none on this cover", _image_prompt(b))
        self.assertIn("Recurring character rules", _image_prompt(b))  # characters are held constant (H3 tests them)
        # under H1 the scene itself is the variable: only the composition is held constant
        for content in (a, b):
            held = _field(content, "A/B held constant")
            self.assertIn("同構圖", held)
            self.assertNotIn("場景", held)
        strip = lambda text: re.sub(
            r"Visual metaphor: .*? Recurring character rules",
            "M Recurring character rules",
            re.sub(
                r"(?:Core situation|Story behind the number)[^:]*: .*? Emotional tone",
                "S Emotional tone",
                re.sub(r"Visual world: .*? Paired title idea", "Visual world: X Paired title idea", text),
            ),
        )
        self.assertEqual(strip(_image_prompt(a)), strip(_image_prompt(b)))

    def test_war_room_day_tests_the_next_hypothesis_instead_of_h1(self):
        # 財經戰情室 fallback: H1 would compare a war-room picture with a war-room board
        result, (a, b) = _covers("H1", style="財經戰情室")
        hypothesis = result["ab_test"]["hypothesis"]
        self.assertEqual((hypothesis["id"], hypothesis["replaces"], hypothesis["source"]), ("H2", "H1", "substitute"))
        self._assert_pair((a, b), "H2", "text")
        self.assertEqual([t["style"] for t in result["thumbnails"]], ["財經戰情室", "財經戰情室"])
        self.assertNotIn("war-room DATA BOARD", _image_prompt(b))
        section = cover_ab_test.package_section(hypothesis, "2026-10-14", hero_label="1303 南亞", number="55.3億")
        self.assertIn("H2", section)
        self.assertIn("H1 兩組會幾乎一樣", section)
        # a comic-scene theme keeps this week's H1
        self.assertEqual(cover_ab_test.hypothesis_for_style(cover_ab_test.find_hypothesis(cover_ab_test.load_plan(), "H1"), THEME)["id"], "H1")

    def test_text_arm_is_a_question_with_the_same_number(self):
        _result, (a, b) = _covers("H2")
        self._assert_pair((a, b), "H2", "text")
        self.assertEqual(_field(b, "Primary"), _field(a, "Primary"))
        self.assertEqual(_field(b, "Secondary"), "外資為何買55.3億？")
        self.assertIn("55.3億", _field(a, "Secondary"))

    def test_character_arm(self):
        _result, (a, b) = _covers("H3")
        self._assert_pair((a, b), "H3", "characters")
        self.assertIn("Recurring character rules", _image_prompt(a))
        self.assertIn("NO CHARACTERS (A/B variable)", _image_prompt(b))
        self.assertNotIn("Recurring character rules", _image_prompt(b))
        self.assertEqual(_field(a, "Secondary"), _field(b, "Secondary"))

    def test_number_layout_arm(self):
        _result, (a, b) = _covers("H4")
        self._assert_pair((a, b), "H4", "number_layout")
        self.assertIn("lower-left", _image_prompt(a))
        self.assertIn("upper-right corner", _image_prompt(b))
        self.assertEqual(_field(a, "Secondary"), _field(b, "Secondary"))

    def test_check_youtube_package_ab_rules(self):
        _result, prompts = _covers("H1")
        failures, warnings = [], []
        with redirect_stdout(StringIO()):
            check_youtube_package.check_ab_covers(prompts, failures, warnings)
        self.assertEqual((failures, warnings), ([], []))
        other_number = prompts[1].replace("- Secondary: 外資買55.3億", "- Secondary: 外資買47.5億")
        other_hero = prompts[1].replace("- Hero (fact source): 1303 南亞", "- Hero (fact source): 1301 台塑")
        undeclared = re.sub(r"^- A/B variable:.*\n", "", prompts[1], flags=re.MULTILINE)
        for broken, expect in ((other_number, "數字不同"), (other_hero, "主角不同")):
            failures, warnings = [], []
            with redirect_stdout(StringIO()):
                check_youtube_package.check_ab_covers([prompts[0], broken], failures, warnings)
            self.assertTrue(any(expect in f for f in failures), failures)
        failures, warnings = [], []
        with redirect_stdout(StringIO()):
            check_youtube_package.check_ab_covers([prompts[0], undeclared], failures, warnings)
        self.assertEqual(failures, [])
        self.assertTrue(any("A/B variable" in w for w in warnings))


class CoverFatigueTests(unittest.TestCase):
    def test_a_cover_pair_counts_once_and_a_rerun_ignores_its_own_day(self):
        from modules.packaging_fatigue import build_fatigue_state

        with tempfile.TemporaryDirectory() as tmp:
            reports = os.path.join(tmp, "reports")
            for day, scenario in (("2026-10-12", "事件衝擊"), ("2026-10-13", "事件衝擊"), ("2026-10-14", "資金換手")):
                folder = os.path.join(reports, "youtube", day)
                os.makedirs(folder)
                for index in (1, 2):  # A/B share one scenario
                    with open(os.path.join(folder, f"yt_thumbnail_prompt_{index}.md"), "w", encoding="utf-8") as f:
                        f.write(f"Scenario: {scenario}\n- Primary: 1303 南亞\n- Secondary: 外資買55.3億\n")
            state = build_fatigue_state(channel_story="", youtube_assets_dir=reports)
            self.assertIn("事件衝擊", state["fatigued_scenarios"])  # two different days
            self.assertNotIn("資金換手", state["fatigued_scenarios"])  # one day's A/B pair counts once
            rerun = build_fatigue_state(channel_story="", youtube_assets_dir=reports, exclude_date="2026-10-14")
            self.assertEqual(rerun["fatigued_scenarios"], state["fatigued_scenarios"])


class PackageGuidanceTests(unittest.TestCase):
    def setUp(self):
        hypothesis = cover_ab_test.current_hypothesis("2026-10-14", env={})
        self.section = cover_ab_test.package_section(hypothesis, "2026-10-14", hero_label="1303 南亞", number="55.3億")

    def test_old_48_hour_guidance_is_replaced_once(self):
        once = cover_ab_test.refresh_package_ab_guidance(OLD_PACKAGE, self.section)
        twice = cover_ab_test.refresh_package_ab_guidance(once, self.section)
        self.assertEqual(once, twice)
        self.assertEqual(once.count(cover_ab_test.BLOCK_START), 1)
        for gone in ("48 小時後再判斷", "再測 B 組", "是否更換 B 組", "對照組", "實驗組", "不判斷 A/B 勝負"):
            self.assertNotIn(gone, once)
        self.assertIn("## 📝 A/B 標題選項", once)  # check-daily-workflow's required heading
        self.assertIn("測試與比較", once)
        self.assertIn("record-ab-result --date 2026-10-14", once)
        self.assertIn("主角 1303 南亞、數字 55.3億", once)
        self.assertEqual(titles_from_package(once), [TITLE_A, TITLE_B])
        self.assertLess(once.index(cover_ab_test.BLOCK_START), once.index("## 🎬 今日影片製作計畫"))

    def test_new_package_has_the_section(self):
        from modules.youtube_content_generator import save_youtube_package

        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(StringIO()):
            path = save_youtube_package(
                {"titles": [TITLE_A, TITLE_B], "description": "d", "pinned_comment": "p"},
                tmp,
                "2026-10-14",
                structured_data=_sd(),
            )
            with open(path, encoding="utf-8") as f:
                text = f.read()
        self.assertIn(cover_ab_test.BLOCK_START, text)
        self.assertNotIn("48 小時後再判斷", text)
        self.assertIn("1303 南亞", text.split(cover_ab_test.BLOCK_START)[1])
        self.assertEqual(titles_from_package(text), [TITLE_A, TITLE_B])


class RecordResultTests(unittest.TestCase):
    def test_b3_record_ab_result_round_trip_on_a_temp_db(self):
        from modules.analytics_store import AnalyticsStore
        from scripts.youtube import record_ab_result

        _result, prompts = _covers("H1")
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "analytics.db")
            reports = os.path.join(tmp, "reports")
            day_dir = os.path.join(reports, "youtube", "2026-10-14")
            os.makedirs(day_dir)
            for index, content in enumerate(prompts, start=1):
                with open(os.path.join(day_dir, f"yt_thumbnail_prompt_{index}.md"), "w", encoding="utf-8") as f:
                    f.write(content)
            store = AnalyticsStore(db)
            store.record_youtube_thumbnail_variants(
                "2026-10-14", [{"variant": "a", "cover_text": ["x"]}, {"variant": "b", "cover_text": ["y"]}]
            )
            with mock.patch.object(record_ab_result, "REPORTS_DIR", reports), redirect_stdout(StringIO()) as out:
                record_ab_result.main(
                    ["--date", "2026-10-14", "--winner", "b", "--value-a", "44.1", "--value-b", "55.9", "--db", db]
                )
            self.assertIn("H1", out.getvalue())
            rows = store.get_ab_test_results()
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(
                (row["run_date"], row["hypothesis_id"], row["variable"], row["winner"], row["metric"]),
                ("2026-10-14", "H1", "style", "b", "watch_time_share"),
            )
            self.assertEqual((row["value_a"], row["value_b"]), (44.1, 55.9))
            self.assertTrue(row["recorded_at"])
            selected = {v["variant"]: v["selected"] for v in store.get_youtube_thumbnail_variants("2026-10-14")}
            self.assertEqual(selected, {"a": 0, "b": 1})
            # a /2 rerun that rewrites the covers keeps YouTube's pick
            store.record_youtube_thumbnail_variants(
                "2026-10-14", [{"variant": "a", "cover_text": ["x"]}, {"variant": "b", "cover_text": ["y"]}]
            )
            selected = {v["variant"]: v["selected"] for v in store.get_youtube_thumbnail_variants("2026-10-14")}
            self.assertEqual(selected, {"a": 0, "b": 1})
            # re-recording replaces the row; `none` clears the pick an earlier a/b result made
            with mock.patch.object(record_ab_result, "REPORTS_DIR", reports), redirect_stdout(StringIO()):
                record_ab_result.main(["--date", "2026-10-14", "--winner", "none", "--db", db])
            self.assertEqual([r["winner"] for r in store.get_ab_test_results()], ["none"])
            self.assertEqual({v["selected"] for v in store.get_youtube_thumbnail_variants("2026-10-14")}, {0})
            # the read-only loader used by the learning summaries sees the same rows
            self.assertEqual(cover_ab_test.load_results(db)[0]["hypothesis_id"], "H1")
            conn = sqlite3.connect(db)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(ab_test_results)")}
            conn.close()
            self.assertTrue({"run_date", "hypothesis_id", "variable", "winner", "metric", "value_a", "value_b", "recorded_at"} <= cols)


    def test_undeclared_covers_are_never_credited_to_the_week(self):
        """Real 10/13: old two-variable covers (no A/B variable line) and a legacy selected=1 on A."""
        from modules.analytics_store import AnalyticsStore
        from scripts.youtube import record_ab_result

        old_cover = "# YouTube Thumbnail Prompt\n\nScenario: 事件衝擊\n- Primary: 台積電少639億\n- Secondary: 跌幅不到2%\n"
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "analytics.db")
            reports = os.path.join(tmp, "reports")
            day_dir = os.path.join(reports, "youtube", "2026-10-13")
            os.makedirs(day_dir)
            for index in (1, 2):
                with open(os.path.join(day_dir, f"yt_thumbnail_prompt_{index}.md"), "w", encoding="utf-8") as f:
                    f.write(old_cover)
            store = AnalyticsStore(db)
            store.record_youtube_thumbnail_variants(
                "2026-10-13",
                [{"variant": "a", "cover_text": ["x"], "selected": True}, {"variant": "b", "cover_text": ["y"]}],
            )
            with mock.patch.object(record_ab_result, "REPORTS_DIR", reports), redirect_stdout(StringIO()):
                with self.assertRaises(SystemExit) as refused:
                    record_ab_result.main(["--date", "2026-10-13", "--winner", "none", "--db", db])
            self.assertIn("A/B variable", str(refused.exception.code))  # a message → exit status 1
            # through `cli.py youtube record-ab-result`: exit 1 and the reason is printed, not swallowed
            import cli
            from scripts.tool_registry import find_tool

            err = StringIO()
            with mock.patch.object(record_ab_result, "REPORTS_DIR", reports), redirect_stdout(StringIO()), mock.patch(
                "sys.stderr", err
            ):
                code = cli.run_tool(
                    find_tool("youtube", "record-ab-result"), ["--date", "2026-10-13", "--winner", "a", "--db", db]
                )
            self.assertEqual(code, 1)
            self.assertIn("A/B variable", err.getvalue())
            self.assertEqual(store.get_ab_test_results(), [])
            legacy = {v["variant"]: v["selected"] for v in store.get_youtube_thumbnail_variants("2026-10-13")}
            self.assertEqual(legacy, {"a": 1, "b": 0})
            # explicit --hypothesis is the operator's call; `none` still leaves the legacy flag alone
            with mock.patch.object(record_ab_result, "REPORTS_DIR", reports), redirect_stdout(StringIO()) as out:
                record_ab_result.main(["--date", "2026-10-13", "--winner", "none", "--hypothesis", "H3", "--db", db])
            self.assertIn("沒有 A/B variable 宣告", out.getvalue())
            self.assertEqual([r["hypothesis_id"] for r in store.get_ab_test_results()], ["H3"])
            legacy = {v["variant"]: v["selected"] for v in store.get_youtube_thumbnail_variants("2026-10-13")}
            self.assertEqual(legacy, {"a": 1, "b": 0})


class LearningTests(unittest.TestCase):
    def _rows(self, *winners, hid="H1"):
        return [{"run_date": f"2026-10-{12 + i:02d}", "hypothesis_id": hid, "winner": w} for i, w in enumerate(winners)]

    def _row(self, rows, hid="H1"):
        return next(r for r in cover_ab_test.summarize_results(rows) if r["hypothesis_id"] == hid)

    def test_promotion_needs_three_decided_tests_and_a_majority(self):
        self.assertIsNone(self._row(self._rows("a", "a"))["promoted"])  # 2 decided
        self.assertIsNone(self._row(self._rows("a", "a", "none", "none"))["promoted"])  # none is not decided
        self.assertEqual(self._row(self._rows("a", "b", "a"))["promoted"], "a")
        self.assertIsNone(self._row(self._rows("a", "b", "a", "b"))["promoted"])  # tie
        self.assertEqual(self._row(self._rows("b", "b", "a", "none"))["promoted"], "b")
        row = self._row(self._rows("b", "b", "a", "none"))
        self.assertEqual((row["n"], row["decided"], row["wins_a"], row["wins_b"], row["no_difference"]), (4, 3, 1, 2, 1))

    def test_learning_lines_show_n_and_never_ban(self):
        lines = cover_ab_test.format_learning_lines(cover_ab_test.summarize_results(self._rows("a", "a")))
        text = "\n".join(lines)
        self.assertIn("n=2", text)
        self.assertIn("樣本不足", text)
        self.assertIn("不產生任何禁令", text)
        self.assertIn("H4", text)  # every hypothesis of the plan is listed, even without results

    def test_wiki_and_brief_include_the_ab_section(self):
        from modules.compounding_loop import build_weekly_compounding_brief
        from modules.youtube_learning import build_youtube_performance_summary

        rows = self._rows("b", "b", "b")
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "log.md")
            with open(log, "w", encoding="utf-8") as f:
                f.write(
                    "| 日期 | 核心標的 | 預測方向 | 隔日驗收 | 視覺風格 | 標題方向 | 影片ID | 使用標題 | 觀看數 |\n"
                    "|---|---|---|---|---|---|---|---|---|\n"
                    "| 2026-10-01 | 南亞 | 南亞(多) | 待驗收 | x | y | vid1 | 南亞買55億！ | 300 |\n"
                )
            # hermetic: neither the learning window nor the horizon summary opens data/analytics.db
            store = mock.MagicMock()
            store.return_value.get_horizon_performance_summary.return_value = []
            with mock.patch("modules.youtube_learning.load_metric_series", return_value={}), mock.patch(
                "modules.analytics_store.AnalyticsStore", store
            ):
                wiki = build_youtube_performance_summary(log, metric_series={}, stock_names={}, ab_results=rows)
                brief = build_weekly_compounding_brief(
                    log_path=log, stock_db_path=os.path.join(tmp, "db.json"), ab_results=rows,
                    bans_path=os.path.join(tmp, "bans.json"),
                )
        for text in (wiki, brief):
            self.assertIn("封面 A/B", text)
            # a promotion is a recommendation: nothing changes LEVER_DEFAULTS automatically
            self.assertIn("可升級為預設（需手動調整 cover_ab_test.LEVER_DEFAULTS）：B 戰情室數據大字牌", text)
            self.assertNotIn("✅ 升級為預設", text)
        self.assertTrue(wiki.rstrip().endswith("<!-- manual-rules:end -->"))


if __name__ == "__main__":
    unittest.main()
