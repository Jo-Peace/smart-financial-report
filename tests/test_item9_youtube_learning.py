#!/usr/bin/env python3
"""Item 9: same-age learning, relative thresholds, lift lessons, structure IDs and rotation."""
import datetime
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.packaging_fatigue import (  # noqa: E402
    analyze_title_opener_streaks,
    build_fatigue_state,
    sanitize_title,
)
from modules.pipeline_support import CHANNEL_STORY_HEADER  # noqa: E402
from modules.youtube_learning import (  # noqa: E402
    B_ROTATION_FAMILIES,
    annotate_rows,
    build_youtube_performance_summary,
    comparable_sample,
    format_compact_channel_story,
    learning_window,
    lift_lessons,
    parse_offsite_dates,
    structure_rotation_lines,
    title_structure_id,
    views_at_age,
)

NOW = datetime.datetime(2026, 10, 13, 17, 0)
NAMES = ["台積電", "南亞科", "華邦電", "創意", "友達", "聯發科", "臻鼎"]


def _log(rows):
    lines = [CHANNEL_STORY_HEADER]
    for i, (date, title, views) in enumerate(rows):
        lines.append(f"| {date} | A | x(多) | 待驗收 | 場景 | 上漲 | vid{i} | {title} | {views} |\n")
    return "".join(lines)


def _sample_rows():
    rows = []
    day = datetime.date(2026, 8, 20)
    for i in range(24):
        day += datetime.timedelta(days=1)
        if i % 2 == 0:
            rows.append((day.isoformat(), f"南亞科買{100 + i}億！友達卻收黑｜AI帶你看股市", 600 + i * 10))
        else:
            rows.append((day.isoformat(), f"外資{50 + i}億狂掃聯發科噴{i}%｜AI帶你看股市", 40 + i))
    rows.append(("2026-10-12", "創意買30億！股價跌3.1%｜10/12 AI帶你看股市", 20))  # < 72h old
    return rows


class SameAgeTest(unittest.TestCase):
    def test_views_at_age_interpolates_but_never_extrapolates(self):
        self.assertAlmostEqual(views_at_age([(24, 100), (72, 300)], 48), 200)
        self.assertIsNone(views_at_age([(60, 100), (90, 300)], 48))
        self.assertIsNone(views_at_age([], 48))
        # opt-in fallback: nearest snapshot inside the window, never outside it
        self.assertEqual(views_at_age([(60, 100), (90, 300)], 48, fallback_window=(40, 80)), 100)
        self.assertIsNone(views_at_age([(94, 300)], 48, fallback_window=(40, 80)))
        self.assertIsNone(views_at_age([(20, 30)], 48, fallback_window=(40, 80)))

    def test_friday_upload_keeps_a_same_age_metric(self):
        # Friday ~18:00 upload: first /1 refresh is Monday 16:10 (~70h), then Tuesday (~94h)
        text = _log(_sample_rows())
        series = {f"vid{i}": {"published_at": None, "series": [(24, 10 * i), (72, 30 * i)]} for i in range(10)}
        series["vid10"] = {"published_at": None, "series": [(70, 1090), (94, 1200)]}
        window = learning_window(text=text, now=NOW, metric_series=series)
        friday = next(r for r in window["rows"] if r["video_id"] == "vid10")
        self.assertEqual(friday["views_same_age"], 1090)
        self.assertTrue(friday["views_same_age_approx"])
        self.assertIn("vid10", [r["video_id"] for r in window["sample"]])
        self.assertIn("40–80h", window["basis"])

    def test_maturity_uses_measurement_age_not_now(self):
        # log views were refreshed when the video was 20h old; /2 runs two days later
        rows = [{"date": "2026-10-09", "video_id": "v", "title": "t", "views": 40}]
        later = datetime.datetime(2026, 10, 12, 20, 0)
        stale = annotate_rows(rows, now=later, metric_series={"v": {"published_at": None, "series": [(20, 40)]}})
        self.assertFalse(stale[0]["mature"])
        self.assertEqual(stale[0]["age_hours"], 20)
        # no snapshot for this video, but the last refresh (any video) was 22h after publish
        other = {"w": {"published_at": None, "last_measured_at": "2026-10-10 16:10:00", "series": [(100, 5)]}}
        no_series = annotate_rows(rows, now=later, metric_series=other)
        self.assertFalse(no_series[0]["mature"])
        self.assertTrue(annotate_rows(rows, now=later, metric_series={})[0]["mature"])  # legacy: age now

    def test_young_videos_never_low_samples_and_same_age_basis(self):
        text = _log(_sample_rows())
        window = learning_window(text=text, now=NOW, metric_series={})
        self.assertIn("累積觀看", window["basis"])
        self.assertEqual([r["date"] for r in window["young"]], ["2026-10-12"])
        self.assertNotIn("2026-10-12", [r["date"] for r in window["sample"]])
        series = {f"vid{i}": {"published_at": None, "series": [(24, 10 * i), (72, 30 * i)]} for i in range(10)}
        rows = annotate_rows(window["rows"], now=NOW, metric_series=series)
        sample, basis = comparable_sample(rows)
        self.assertTrue(basis.startswith("views@48h"))
        self.assertEqual(len(sample), 10)

    def test_offsite_seeded_videos_are_excluded(self):
        text = _log(_sample_rows()) + (
            "\n## 站外導流變數（獨立於標題／視覺）\n\n| 日期 | 站外= | 動作細節 | 人工判斷 | Studio 線索 |\n"
            "|---|---|---|---|---|\n| 2026-08-21 | FB | 留言 | 有用 | x |\n"
        )
        self.assertEqual(parse_offsite_dates(text=text), {"2026-08-21": "FB"})
        window = learning_window(text=text, now=NOW, metric_series={})
        self.assertNotIn("2026-08-21", [r["date"] for r in window["sample"]])


class LessonsAndStructureTest(unittest.TestCase):
    def test_lift_requires_difference_and_drops_constant_features(self):
        window = learning_window(text=_log(_sample_rows()), now=NOW, metric_series={})
        lines, detail = lift_lessons(window["sample"], NAMES)
        features = {d["feature"] for d in detail}
        self.assertNotIn("含金額（億）", features)  # present in every title → dropped
        self.assertTrue(any("股名+金額開場" in line and "高觀看組較常見" in line for line in lines))
        few, _ = lift_lessons(window["sample"][:6], NAMES)
        self.assertIn("樣本不足", few[0])

    def test_lift_label_prints_the_real_group_share(self):
        window = learning_window(text=_log(_sample_rows()), now=NOW, metric_series={})
        sample = window["sample"][:12]
        lines, _ = lift_lessons(sample, NAMES)
        self.assertTrue(lines)
        for line in lines:
            self.assertNotIn("25%", line)
            self.assertIn("前 5 支 vs 後 5 支，各占 42%，n=12", line)

    def test_summary_is_data_driven(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "log.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(_log(_sample_rows()))
            text = build_youtube_performance_summary(path, now=NOW, metric_series={}, stock_names=NAMES)
        self.assertNotIn("資金真的換車了", text)
        self.assertIn("中位數 × 2", text)
        self.assertIn("觀察中", text)
        self.assertNotIn("視覺：", text)

    def test_regeneration_keeps_the_hand_written_rules(self):
        """Smoke test 12: legacy /2 cover rules migrate once, then survive every rewrite."""
        from unittest import mock

        from modules.youtube_learning import (
            MANUAL_RULES_END,
            MANUAL_RULES_START,
            extract_manual_rules,
            update_youtube_performance_wiki,
        )

        # The production legacy section: every bullet but the last was written by the
        # pre-090535f generator (and dropped from it on purpose); only the last is hand-written.
        legacy = (
            "# YouTube Performance Patterns\n\n## /2 包裝規則\n\n"
            "- 標題不要只回報今日數字，要提出一個可被影片回答的市場問題。\n"
            "- 優先使用「具體資金動作 + 觀眾關心的風險 + 明確推測問題」。\n"
            "- 若近 5 天已出現相同 scenario 或封面大字，今日必須換另一種家族（例如驗收、逆勢、事件、天量陷阱）。\n"
            "- 數據牌可以當副標，不要讓封面只剩股票代號與買超金額。\n"
            "- 恐懼標題必須提供出口：誰可能受惠、明天看哪個驗收點、失效條件是什麼。\n"
            "- 主角股名一律放在標題前 8 字內。\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "log.md")
            with open(log, "w", encoding="utf-8") as f:
                f.write(_log(_sample_rows()))
            wiki = os.path.join(tmp, "youtube_performance_patterns.md")
            with open(wiki, "w", encoding="utf-8") as f:
                f.write(legacy)
            with mock.patch("modules.youtube_learning.load_metric_series", return_value={}):
                out = StringIO()
                with redirect_stdout(out):
                    update_youtube_performance_wiki(log, wiki)
                with open(wiki, encoding="utf-8") as f:
                    first = f.read()
                # An operator adds a rule inside the block; the next rewrite keeps it verbatim.
                with open(wiki, "w", encoding="utf-8") as f:
                    f.write(first.replace(MANUAL_RULES_END, "- 封面不放三檔以上股票。\n" + MANUAL_RULES_END))
                update_youtube_performance_wiki(log, wiki)
                with open(wiki, encoding="utf-8") as f:
                    second = f.read()
        manual = extract_manual_rules(first)
        self.assertEqual(manual, "- 主角股名一律放在標題前 8 字內。")
        # Retired generated rules are never frozen as hand-written.
        for retired in ("數據牌可以當副標", "若近 5 天已出現相同 scenario", "優先使用「具體資金動作", "誰可能受惠"):
            self.assertNotIn(retired, manual)
        self.assertNotIn("標題不要只回報今日數字", manual)
        # The operator is told what was migrated.
        self.assertIn("主角股名一律放在標題前 8 字內", out.getvalue())
        self.assertEqual(first.count(MANUAL_RULES_START), 1)
        self.assertIn("主角股名一律放在標題前 8 字內", second)
        self.assertIn("封面不放三檔以上股票", second)
        self.assertEqual(second.count(MANUAL_RULES_START), 1)
        self.assertTrue(second.rstrip().endswith(MANUAL_RULES_END))  # in the tail /1 and /2 read
        self.assertIn("中位數 × 2", second)

    def test_a_lost_marker_keeps_the_manual_block(self):
        from modules.youtube_learning import (
            MANUAL_RULES_END,
            MANUAL_RULES_START,
            manual_rules_section,
            manual_rules_with_notes,
        )

        body = "- 封面不放三檔以上股票。\n- 主角股名一律放在標題前 8 字內。"
        full = "# YouTube Performance Patterns\n\n## /2 包裝規則\n\n- 數據牌可以當副標。\n\n" + "\n".join(
            manual_rules_section(body)
        )
        self.assertEqual(manual_rules_with_notes(full), (body, []))
        for broken in (full.replace(MANUAL_RULES_END, ""), full.replace(MANUAL_RULES_START, "")):
            manual, notes = manual_rules_with_notes(broken)
            self.assertEqual(manual, body)  # not the legacy fallback (which would lose it)
            self.assertEqual(len(notes), 1)
            self.assertIn("請確認", notes[0])

    def test_structure_ids(self):
        self.assertEqual(title_structure_id("台積電買244億！友達量第一收黑｜10/5 AI帶你看股市", NAMES)["family"], "amount_lead")
        self.assertEqual(title_structure_id("外資26億狂掃強茂噴6%！", NAMES)["family"], "foreign_amount_lead")
        self.assertEqual(title_structure_id("【驗收】華邦電+6.33%續航！", NAMES)["family"], "validation_lead")
        self.assertEqual(title_structure_id("創意漲3%卻被賣30億？", NAMES)["family"], "move_lead")
        sig_a = title_structure_id("台積電買244億！友達量第一收黑", NAMES)["signature"]
        sig_b = title_structure_id("南亞科買112億！友達量第一收黑", NAMES)["signature"]
        self.assertEqual(sig_a, sig_b)

    def test_rotation_keeps_a_as_control_and_rotates_b(self):
        window = learning_window(text=_log(_sample_rows()), now=NOW, metric_series={})
        lines = structure_rotation_lines(window=window, stock_names=NAMES)
        joined = "\n".join(lines)
        self.assertIn("今日 A（對照，硬規則）：股名+金額開場", joined)
        b_line = next(line for line in lines if line.startswith("- 今日 B"))
        self.assertTrue(any(fam_label in b_line for fam_label in ("矛盾句", "問句", "漲跌")))
        self.assertEqual(len(B_ROTATION_FAMILIES), 3)

    def test_compact_story_has_no_visual_column(self):
        text = format_compact_channel_story(text=_log(_sample_rows()), n_rows=5, metric_series={})
        self.assertIn("觀看（上架時長）", text)
        self.assertNotIn("場景", text)
        self.assertEqual(len(text.strip().splitlines()), 7)


class CompoundingBriefTest(unittest.TestCase):
    def test_brief_uses_the_shared_comparable_sample(self):
        from unittest import mock

        import modules.analytics_store as analytics_store
        from modules.compounding_loop import build_weekly_compounding_brief

        rows = _sample_rows()
        rows.append(("2026-10-13", "華通買9億！大盤崩960點｜AI帶你看股市", 99999))  # < 72h old
        text = _log(rows) + (
            "\n## 站外導流變數（獨立於標題／視覺）\n\n| 日期 | 站外= | 動作細節 | 人工判斷 | Studio 線索 |\n"
            "|---|---|---|---|---|\n| 2026-09-12 | FB | 留言 | 有用 | x |\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "log.md")
            with open(log, "w", encoding="utf-8") as f:
                f.write(text)
            with mock.patch("modules.youtube_learning.load_metric_series", return_value={}), \
                    mock.patch.object(analytics_store, "AnalyticsStore", side_effect=RuntimeError("no db")):
                brief = build_weekly_compounding_brief(log_path=log, stock_db_path=os.path.join(tmp, "db.json"))
        copy = brief.split("## 本週可複製（標題／選鉤）")[1].split("## 本週禁止")[0]
        self.assertIn("累積觀看（僅上架 ≥72 小時", copy)
        self.assertNotIn("99,999", copy)  # young video is never a copy sample
        self.assertNotIn("2026-09-12", copy)  # off-site seeded video excluded
        self.assertNotIn("公式優先", brief)  # no hard-coded "verified formula"
        self.assertIn("高觀看組較常見", copy)

    def test_titles_breaking_active_bans_are_not_positive_examples(self):
        """Smoke test 11: a 「被砍」 / 「狂掃」-opener title is never 「本週可複製」, however many views."""
        import json
        from unittest import mock

        import modules.analytics_store as analytics_store
        from modules.compounding_loop import build_weekly_compounding_brief

        rows = _sample_rows()
        rows.insert(3, ("2026-08-24", "景碩買48億！台積電卻被砍117億｜AI帶你看股市", 9000))
        rows.insert(5, ("2026-08-26", "外資80億狂掃創意噴5%｜AI帶你看股市", 8000))
        bans = {"version": 1, "bans": [
            {"type": "title_structure", "pattern": "被砍", "raw": "禁止被砍同構", "created": "2026-10-12",
             "expires": "2099-01-01", "status": "active"},
            {"type": "title_rule", "pattern": "AMD／狂掃開場", "raw": "禁止AMD／狂掃開場", "created": "2026-10-12",
             "expires": "2099-01-01", "status": "active"},
            {"type": "title_structure", "pattern": "卻收黑", "raw": "禁止卻收黑同構", "created": "2026-01-01",
             "expires": "2026-01-08", "status": "expired"},  # expired: no longer filters
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "log.md")
            with open(log, "w", encoding="utf-8") as f:
                f.write(_log(rows))
            bans_path = os.path.join(tmp, "packaging_bans.json")
            with open(bans_path, "w", encoding="utf-8") as f:
                json.dump(bans, f, ensure_ascii=False)
            with mock.patch("modules.youtube_learning.load_metric_series", return_value={}), \
                    mock.patch.object(analytics_store, "AnalyticsStore", side_effect=RuntimeError("no db")):
                brief = build_weekly_compounding_brief(
                    log_path=log, stock_db_path=os.path.join(tmp, "db.json"), bans_path=bans_path
                )
                unfiltered = build_weekly_compounding_brief(
                    log_path=log, stock_db_path=os.path.join(tmp, "db.json"), bans_path=os.path.join(tmp, "none.json")
                )
        copy = brief.split("## 本週可複製（標題／選鉤）")[1].split("## 標題特徵對照")[0]
        self.assertNotIn("被砍", copy.split("- 已略過")[0])
        self.assertNotIn("狂掃創意", copy)
        self.assertIn("已略過 2 支違反有效包裝禁令的標題", copy)
        self.assertIn("友達卻收黑", copy)  # the expired ban filters nothing
        self.assertIn("被砍", unfiltered.split("## 本週可複製（標題／選鉤）")[1].split("## 標題特徵對照")[0])


class OpenerSkeletonTest(unittest.TestCase):
    TITLES = [
        "南亞科買159億！藥華藥卻被砍109億｜9/18 AI帶你看股市",
        "台積電買175億！友達量第一卻摔｜9/23 AI帶你看股市",
        "聯發科賣247億！南亞卻買72億｜9/29 AI帶你看股市",
        "華邦電買66億！群創漲停才是量第一｜9/30 AI帶你看股市",
        "國巨買128億！群創一日倒轉51億｜10/1 AI帶你看股市",
    ]

    def test_skeleton_streak_warns_at_five_but_never_blocks_or_rewrites(self):
        self.assertFalse(analyze_title_opener_streaks(self.TITLES[:4])["warn"])
        streak = analyze_title_opener_streaks(self.TITLES)
        self.assertEqual(streak["trailing_label"], "股名金額開場")
        self.assertTrue(streak["warn"])
        self.assertFalse(streak["block"])
        state = {"opener_streak": streak, "fatigued_title_terms": []}
        self.assertEqual(sanitize_title("創意買30億！股價跌3.1%？", state), "創意買30億！股價跌3.1%？")

    def test_panic_terms_are_replaced_in_titles(self):
        out = sanitize_title("戰火引爆美股血洗！台積電ADR暴跌9.5%週一面臨斷頭危機？資金全逃", build_fatigue_state(channel_story=""))
        for word in ("血洗", "斷頭", "全逃"):
            self.assertNotIn(word, out)


if __name__ == "__main__":
    unittest.main()
