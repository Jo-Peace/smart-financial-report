#!/usr/bin/env python3
"""Item 9: channel_story_log integrity + YouTube metric snapshots (no network, temp files only)."""
import datetime
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules import youtube_analytics  # noqa: E402
from modules.analytics_store import AnalyticsStore  # noqa: E402
from modules.packaging_fatigue import _parse_channel_story_titles  # noqa: E402
from modules.pipeline_support import (  # noqa: E402
    CHANNEL_STORY_HEADER,
    append_channel_story_log,
    merge_duplicate_story_rows,
    read_story_table_tail,
    update_channel_story_cells,
)
from modules.youtube_learning import parse_channel_story_rows  # noqa: E402


def _row(date, vid="-", title="-", views="待更新", visual="未指定", target="A"):
    return f"| {date} | {target} | x(多) | 待驗收 | {visual} | 上漲 | {vid} | {title} | {views} |\n"


class ChannelStoryUpsertTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmp.name, "channel_story_log.md")

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, body):
        with open(self.log, "w", encoding="utf-8") as f:
            f.write(CHANNEL_STORY_HEADER + body)

    def _rows(self):
        with open(self.log, encoding="utf-8") as f:
            return [line for line in f if line.startswith("| 20")]

    def test_rerun_updates_same_date_row_and_keeps_video_fields(self):
        self._write(_row("2026-10-08", "FETn_6Bf9SY", "南亞科買112億！逆勢噴3.5%", "50", "窯廠漫畫"))
        sd = {"ai_data_picks": ["創意", "長榮"], "prediction_targets": [{"name": "長榮", "direction": "多"}],
              "index_action": "下跌 11.5點"}
        append_channel_story_log("2026-10-12", sd, channel_story_log=self.log)
        append_channel_story_log("2026-10-12", dict(sd, ai_data_picks=["創意"]), channel_story_log=self.log)
        rows = self._rows()
        self.assertEqual(len(rows), 2)
        update_channel_story_cells(self.log, "2026-10-12", visual_theme="實驗室|滴定管", title_used="創意買30億！")
        append_channel_story_log("2026-10-12", sd, channel_story_log=self.log)  # third /1 rerun
        rows = self._rows()
        self.assertEqual(len(rows), 2)
        cells = [c.strip() for c in rows[-1].strip().strip("|").split("|")]
        self.assertEqual(len(cells), 9)
        self.assertEqual(cells[4], "實驗室｜滴定管")  # half-width pipe sanitized, kept on rerun
        self.assertEqual(cells[7], "創意買30億！")

    def test_merge_duplicates_and_parsers_dedupe_by_video(self):
        body = (
            _row("2026-06-02", "-", "-", "待更新", target="緯創、南亞科")
            + _row("2026-06-02", "zaTBvWaKXNM", "【驗收】緯創噴9.77%", "492")
            + _row("2026-06-04", "Mf-yVB1GEcc", "06/04 台股盤後", "52")
            + _row("2026-06-05", "Mf-yVB1GEcc", "196億！聯電", "52")
            + _row("2026-06-05", "Mf-yVB1GEcc", "196億！聯電", "52")
            + _row("2026-06-03", "qmMPIKhr7VQ", "籌碼大背離", "80")
            + _row("2026-06-03", "KYq2BMi8nN8", "06/03 台股", "90")
        )
        self._write(body)
        with open(self.log, encoding="utf-8") as f:
            content = f.read()
        merged, removed = merge_duplicate_story_rows(content)
        self.assertEqual(removed, 2)
        self.assertEqual(merged.count("Mf-yVB1GEcc"), 2)  # different dates are kept for review
        rows = parse_channel_story_rows(text=content, include_unviewed=False)
        self.assertEqual([r["video_id"] for r in rows].count("Mf-yVB1GEcc"), 1)
        self.assertEqual(len([r for r in rows if r["date"] == "2026-06-03"]), 2)
        titles = _parse_channel_story_titles(content, limit=20)
        self.assertEqual(titles.count("196億！聯電"), 1)
        self.assertEqual(len(read_story_table_tail(self.log, n_rows=3).splitlines()), 3 + 3)  # header + 3 rows

    def test_channel_match_never_reuses_an_assigned_video(self):
        self._write(_row("2026-06-04", "Mf-yVB1GEcc", "06/04 台股盤後", "52") + _row("2026-06-05"))
        videos = [{"video_id": "Mf-yVB1GEcc", "title": "196億！聯電", "published_at": "2026-06-05T10:00:00Z",
                   "views": 60, "likes": 1, "comments": 0}]
        with mock.patch.object(youtube_analytics, "get_channel_recent_videos", return_value=videos):
            results = youtube_analytics.update_log_from_channel(self.log, "k", "ch", record_snapshots=False)
        self.assertEqual(results, [])
        self.assertEqual(self._rows()[-1].count("Mf-yVB1GEcc"), 0)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _item(vid, views, title="t", published="2026-10-12T10:00:00Z"):
    return {"id": vid, "snippet": {"title": title, "publishedAt": published},
            "statistics": {"viewCount": str(views), "likeCount": "1", "commentCount": "0"}}


class YoutubeMetricsTest(unittest.TestCase):
    def test_batch_requests_50_ids_and_dedupes(self):
        ids = [f"v{i}" for i in range(120)] + ["v1", "-", ""]
        calls = []

        def fake_get(url, params=None, timeout=None):
            chunk = params["id"].split(",")
            calls.append(len(chunk))
            return _Resp({"items": [_item(v, 10) for v in chunk]})

        with mock.patch.object(youtube_analytics.requests, "get", side_effect=fake_get):
            out = youtube_analytics.get_videos_stats_batch(ids, "k")
        self.assertEqual(calls, [50, 50, 20])
        self.assertEqual(len(out), 120)

    def test_time_budget_stops_without_raising(self):
        with mock.patch.object(youtube_analytics.time, "monotonic", side_effect=[0.0, 0.5, 999.0, 999.0]), \
                mock.patch.object(youtube_analytics.requests, "get",
                                  side_effect=lambda url, params=None, timeout=None: _Resp(
                                      {"items": [_item(v, 1) for v in params["id"].split(",")]})):
            out = youtube_analytics.get_videos_stats_batch([f"v{i}" for i in range(60)], "k", time_budget_s=1)
        self.assertEqual(len(out), 50)

    def test_refresh_writes_views_title_and_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "log.md")
            with open(log, "w", encoding="utf-8") as f:
                f.write(CHANNEL_STORY_HEADER + _row("2026-10-12", "ABC", "舊標題", "10")
                        + _row("2026-10-12", "ABC", "舊標題", "10"))
            db = os.path.join(tmp, "a.db")
            store = AnalyticsStore(db)
            store.record_youtube_asset_plan("2026-10-12", {"title_variants": [
                {"variant": "a", "title": "創意買30億！股價跌3.1%"}, {"variant": "b", "title": "創意30億，股價跌？"}]})
            with mock.patch.object(youtube_analytics, "get_videos_stats_batch",
                                   return_value={"ABC": {"video_id": "ABC", "title": "創意30億，股價跌？", "views": 116,
                                                         "likes": 2, "comments": 1,
                                                         "published_at": "2026-10-12T10:00:00Z"}}) as batch:
                results = youtube_analytics.update_log_with_stats(log, "k", db_path=db)
            self.assertEqual(batch.call_args[0][0], ["ABC", "ABC"])
            self.assertEqual(len(results), 1)
            with open(log, encoding="utf-8") as f:
                text = f.read()
            self.assertEqual(text.count("| 116 |"), 2)
            self.assertIn("創意30億，股價跌？", text)
            series = store.get_youtube_metric_series(["ABC"])
            self.assertEqual(series["ABC"]["published_at"], "2026-10-12T10:00:00Z")
            self.assertEqual(len(series["ABC"]["series"]), 1)
            selected = [v["variant"] for v in store.get_youtube_title_variants("2026-10-12") if v["selected"]]
            self.assertEqual(selected, ["b"])
            conn = sqlite3.connect(db)
            self.assertEqual(conn.execute("SELECT title_variant FROM youtube_video_metrics").fetchone()[0], "b")
            conn.close()

    def test_snapshot_gap_and_old_schema_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "old.db")
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE youtube_video_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, run_date TEXT NOT NULL,"
                         " measured_at TEXT NOT NULL, video_id TEXT, hours_since_publish REAL, views INTEGER, likes INTEGER,"
                         " comments INTEGER, impressions INTEGER, ctr REAL, avg_view_duration_seconds REAL,"
                         " avg_view_percentage REAL, title_variant TEXT, thumbnail_variant TEXT, notes TEXT,"
                         " created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            conn.commit()
            conn.close()
            store = AnalyticsStore(db)
            row = {"video_id": "x", "views": 5, "published_at": "2026-10-12T10:00:00Z", "run_date": "2026-10-12"}
            t0 = datetime.datetime(2026, 10, 13, 18, 0)
            self.assertEqual(store.record_youtube_metric_snapshots([row], measured_at=t0), 1)
            self.assertEqual(store.record_youtube_metric_snapshots([row], measured_at=t0 + datetime.timedelta(minutes=5)), 0)
            self.assertEqual(store.record_youtube_metric_snapshots([row], measured_at=t0 + datetime.timedelta(hours=24)), 1)
            info = store.get_youtube_metric_series(["x"])["x"]
            hours = [h for h, _ in info["series"]]
            self.assertEqual(info["last_measured_at"], (t0 + datetime.timedelta(hours=24)).isoformat(sep=" "))
            self.assertEqual(len(hours), 2)
            self.assertAlmostEqual(hours[1] - hours[0], 24.0)

    def test_planner_record_keeps_finalized_thumbnails(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AnalyticsStore(os.path.join(tmp, "a.db"))
            store.record_youtube_thumbnail_variants("2026-10-12", [{"variant": "a", "cover_text": ["創意30億", "股價跌3.1%"]}])
            store.record_youtube_asset_plan("2026-10-12", {"title_variants": [{"variant": "a", "title": "t"}]})
            conn = sqlite3.connect(store.db_path)
            covers = conn.execute("SELECT cover_text FROM youtube_thumbnail_variants WHERE run_date='2026-10-12'").fetchall()
            conn.close()
            self.assertEqual(covers, [("創意30億\n股價跌3.1%",)])


class AnalyticsReportVariantTest(unittest.TestCase):
    def test_variant_stats_one_sample_per_video_without_weekend_specials(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "analytics_report", os.path.join(BASE_DIR, "scripts", "maintenance", "analytics_report.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            store = AnalyticsStore(os.path.join(tmp, "a.db"))
            store.record_youtube_metric("2026-03-07", video_id="w", views=16136, title_variant="A")  # Saturday special
            for views in (50, 80, 120):
                store.record_youtube_metric("2026-10-12", measured_at=f"2026-10-1{views // 50} 18:00:00",
                                            video_id="v", views=views, title_variant="A")
            store.record_youtube_metric("2026-10-08", video_id="u", views=40, title_variant="A")
            conn = sqlite3.connect(store.db_path)
            conn.row_factory = sqlite3.Row
            stats = module._youtube_variant_stats(conn.cursor(), 12)
            conn.close()
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["samples"], 2)
        self.assertEqual(stats[0]["max_views"], 120)

    def test_measurement_quality_uses_the_channel_median_rule(self):
        """No fixed 900 views: valid only at ≥ channel median × 2; <72h is not judged."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "analytics_report", os.path.join(BASE_DIR, "scripts", "maintenance", "analytics_report.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            store = AnalyticsStore(os.path.join(tmp, "a.db"))
            for vid, views in (("a", 100), ("b", 200), ("c", 300)):
                store.record_youtube_metric("2026-10-12", video_id=vid, views=views, hours_since_publish=96)
            store.record_youtube_metric("2026-10-13", video_id="young", views=5000, hours_since_publish=20)
            store.record_youtube_metric("2026-10-10", video_id="sat", views=90000, hours_since_publish=200)  # Saturday
            conn = sqlite3.connect(store.db_path)
            conn.row_factory = sqlite3.Row
            baseline = module._youtube_channel_baseline(conn.cursor())
            conn.close()
            # No channel_story log: the DB fallback, labelled as a different basis.
            report = module.build_report(db_path=store.db_path, log_path=os.path.join(tmp, "missing.md"))
        self.assertEqual((baseline["median"], baseline["win_threshold"]), (200, 400))
        self.assertFalse(baseline["shared_basis"])
        self.assertEqual(module._youtube_sample_quality(450, 96, 400), "valid (≥2× median)")
        self.assertEqual(module._youtube_sample_quality(950, 96, 4000), "observe")  # 900+ is no longer valid by itself
        self.assertTrue(module._youtube_sample_quality(5000, 20, 400).startswith("young"))
        self.assertIn("頻道中位數 200 × 2 = 400", report)
        self.assertIn("與 /2、週報基準不同", report)
        self.assertNotIn("low-sample", report)

    def test_measurement_quality_uses_the_same_baseline_as_slash_2(self):
        """The printed threshold is youtube_learning.learning_window's (last 30 rows, off-site excluded)."""
        import importlib.util

        from modules.youtube_learning import learning_window

        spec = importlib.util.spec_from_file_location(
            "analytics_report", os.path.join(BASE_DIR, "scripts", "maintenance", "analytics_report.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            store = AnalyticsStore(os.path.join(tmp, "a.db"))
            # An old DB-only video would drag a DB-wide median up; /2 never sees it.
            for vid, views in (("old1", 9000), ("old2", 9000), ("old3", 9000)):
                store.record_youtube_metric("2026-03-02", video_id=vid, views=views, hours_since_publish=900)
            log = os.path.join(tmp, "channel_story_log.md")
            rows = [(f"2026-09-{d:02d}", f"v{d}", 100 + d) for d in (1, 2, 3, 4, 7, 8, 9, 10, 11)]
            with open(log, "w", encoding="utf-8") as f:
                f.write(CHANNEL_STORY_HEADER + "".join(
                    f"| {day} | A | x(多) | 待驗收 | 場景 | 上漲 | {vid} | 標題{vid} | {views} |\n" for day, vid, views in rows
                ))
            window = learning_window(log, db_path=store.db_path)
            conn = sqlite3.connect(store.db_path)
            conn.row_factory = sqlite3.Row
            baseline = module._youtube_channel_baseline(conn.cursor(), log_path=log, db_path=store.db_path)
            conn.close()
            report = module.build_report(db_path=store.db_path, log_path=log)
        self.assertTrue(baseline["shared_basis"])
        self.assertEqual(baseline["median"], window["baseline"]["median"])
        self.assertEqual(baseline["win_threshold"], window["baseline"]["win_threshold"])
        self.assertLess(baseline["median"], 9000)
        self.assertIn(f"頻道中位數 {window['baseline']['median']:.0f} × 2", report)
        self.assertIn("與 /2、週報同一基準", report)



class TitleGuardInputTest(unittest.TestCase):
    """The prompt gets the compact story; the local title guard must still see raw rows."""

    TITLES = [f"台積電買{100 + i}億！友達量第一收黑｜10/{i + 1} AI帶你看股市" for i in range(8)]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmp.name, "channel_story_log.md")
        with open(self.log, "w", encoding="utf-8") as f:
            f.write(CHANNEL_STORY_HEADER + "".join(
                _row(f"2026-10-{i + 1:02d}", f"vid{i}", title, "100") for i, title in enumerate(self.TITLES)
            ))
        patcher = mock.patch("modules.youtube_learning.load_metric_series", return_value={})  # no analytics.db
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_packaging_context_keeps_raw_rows_for_the_guard(self):
        from modules.packaging_fatigue import build_fatigue_state
        from modules.pipeline_support import build_packaging_context

        ctx = build_packaging_context(self.tmp.name, self.tmp.name, self.log)
        self.assertEqual(_parse_channel_story_titles(ctx["channel_story"], limit=20), [])  # compact layout
        state = build_fatigue_state(channel_story=ctx["channel_story_rows"], youtube_assets_dir=self.tmp.name)
        self.assertEqual(state["opener_streak"]["trailing_label"], "股名金額開場")
        self.assertEqual(state["opener_streak"]["trailing_streak"], 8)

    def test_generate_youtube_package_feeds_raw_rows_to_the_guard(self):
        import modules.youtube_content_generator as ycg
        from modules.pipeline_support import build_packaging_context

        ctx = build_packaging_context(self.tmp.name, self.tmp.name, self.log)
        seen = []
        real_state = ycg.build_fatigue_state

        def spy(channel_story="", **kwargs):
            seen.append(channel_story)
            return real_state(channel_story=channel_story, youtube_assets_dir=self.tmp.name)

        output = "###TITLE_A\n台積電賣240億！創意卻買30億\n###TITLE_B\n創意買30億卻跌3%？\n###DESCRIPTION\n描述\n###PINNED_COMMENT\n留言\n###END"
        with mock.patch.object(ycg, "generate_text", return_value=output), mock.patch.object(ycg, "build_fatigue_state", spy):
            result = ycg.generate_youtube_package("key", {"date": "2026-10-13"}, "報告", "2026-10-13", packaging_context=ctx)
        self.assertTrue(result["titles"])
        self.assertTrue(seen)
        self.assertEqual(len(_parse_channel_story_titles(seen[0], limit=20)), 8)


if __name__ == "__main__":
    unittest.main()
