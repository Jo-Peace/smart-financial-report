"""Few-shot vs baseline packaging experiment: bank, prompt arm, metadata, recording."""

import json
import os
import random
import sqlite3
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules import fewshot_bank as fb  # noqa: E402
from modules import fewshot_variant as fv  # noqa: E402
from modules import youtube_content_generator as ycg  # noqa: E402

_OUTPUT = (
    "###TITLE_A\n創意買30億卻跌3.11%？長榮也買｜10/12 AI帶你看股市\n"
    "###TITLE_B\n創意30億？｜10/12 AI帶你看股市\n"
    "###DESCRIPTION\n台股今日。\n\n⚠️ 免責\n"
    "###PINNED_COMMENT\n你怎麼看？\n###END\n"
)


def _sd():
    return {
        "date": "2026-10-12",
        "index_action": "下跌 300點（-1.80%）",
        "safe_havens_bought": ["創意 (+30.4億)", "長榮 (+29.6億)"],
        "heavyweights_dumped": ["台積電 (-240.3億)"],
        "price_volume_divergence": ["創意 (3443)：股價下跌 3.11% 但外資大買 30.4 億元"],
        "ai_data_picks": ["創意"],
        "local_market_facts": {"stocks": {"3443": {"ticker": "3443", "name": "創意", "close": 7150.0}}},
        "editorial_frame": {"validation_placement": "brief"},
    }


def _bank():
    def e(regime, day, title, cover):
        return {"video_id": day, "run_date": day, "regime": regime, "title": title, "cover_text": cover,
                "metric": "views_rel_median", "metric_value": 3.0, "views": 900}

    return {
        "regimes": {
            "大跌": [e("大跌", f"2026-07-0{i}", f"緯創買{i}0億卻跌停！{i}", "3231 緯創\n外資買10億") for i in range(1, 5)],
            "盤整": [e("盤整", "2026-08-01", "臻鼎買71億鎖漲停！", "臻鼎\n買71億")],
            "其他": [e("其他", "2026-08-02", "星運 財神倍倍發", "x")],
        }
    }


class RegimeAndBankTest(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(fb.classify_regime(-2.0, ""), "大跌")
        self.assertEqual(fb.classify_regime(1.6, "major_event_day"), "大漲")
        self.assertEqual(fb.classify_regime(0.9, "major_event_day"), "個股新聞")
        self.assertEqual(fb.classify_regime(0.2, "validation_day"), "盤整")
        self.assertEqual(fb.classify_regime(1.0, "validation_day"), "其他")
        self.assertEqual(fb.regime_for_structured_data(_sd()), "大跌")

    def test_exclusions(self):
        self.assertTrue(fb.is_excluded("今日星運"))
        self.assertTrue(fb.is_excluded("", "TaiwanCasinoGuide"))
        self.assertFalse(fb.is_excluded("台積電買100億"))

    def test_sample_prefers_regime_and_rotates(self):
        bank = _bank()
        picks = {tuple(e["run_date"] for e in fb.sample_examples(bank, "大跌", k=2, rng=random.Random(s))) for s in range(8)}
        self.assertTrue(all(all(d.startswith("2026-07") for d in p) for p in picks))
        self.assertGreater(len(picks), 1)
        topped = fb.sample_examples(bank, "盤整", k=3, rng=random.Random(1))
        self.assertEqual(len(topped), 3)
        self.assertFalse(any("星運" in e["title"] for e in fb.sample_examples(bank, "其他", k=3)))

    def test_build_bank_from_temp_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "a.db")
            conn = sqlite3.connect(db)
            conn.executescript(
                "CREATE TABLE youtube_video_metrics (video_id, run_date, views, hours_since_publish, impressions, ctr, title, measured_at);"
                "CREATE TABLE youtube_thumbnail_variants (run_date, variant, cover_text);"
                "CREATE TABLE market_snapshots (snapshot_date, ticker, pct_change);"
                "CREATE TABLE daily_editorial_frames (run_date, frame);"
            )
            for i, (views, title) in enumerate([(1000, "緯創買62億逆勢噴5%！"), (100, "南亞科買27億"), (90, "星運大爆發"), (80, "台積電砍50億")]):
                day = f"2026-10-0{i + 1}"
                conn.execute("INSERT INTO youtube_video_metrics VALUES (?,?,?,?,0,0,?,?)", (f"v{i}", day, views, 200, title, day))
                conn.execute("INSERT INTO youtube_thumbnail_variants VALUES (?, 'a', ?)", (day, title[:3] + "\n外資買"))
                conn.execute("INSERT INTO market_snapshots VALUES (?, '^TWII', -2.0)", (day,))
            conn.commit()
            conn.close()
            bank = fb.build_bank(db_path=db, reports_dir=tmp)
            self.assertEqual(bank["metric"], "views_rel_median")
            titles = [e["title"] for e in bank["regimes"]["大跌"]]
            self.assertEqual(titles[0], "緯創買62億逆勢噴5%！")
            self.assertFalse(any("星運" in t for t in titles))
            path = fb.save_private_json(os.path.join(tmp, "sub", "bank.json"), bank)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)


class PromptArmTest(unittest.TestCase):
    def _prompt(self, **kw):
        captured = {}

        def fake(api_key, prompt, model_name):
            captured["prompt"] = prompt
            return _OUTPUT

        with mock.patch.object(ycg, "_generate_package_text", fake), mock.patch.dict(
            os.environ, {"YOUTUBE_VIDEO_DURATION": ""}
        ), redirect_stdout(StringIO()):
            ycg.generate_youtube_package("k", _sd(), "報告", "2026-10-12", **kw)
        return captured["prompt"]

    def test_baseline_prompt_unchanged_and_fewshot_block_inserted(self):
        base = self._prompt()
        self.assertEqual(base, self._prompt(fewshot_block=None))
        self.assertEqual(base, self._prompt(fewshot_block=""))
        self.assertNotIn("few-shot", base)
        block = fb.fewshot_block(fb.sample_examples(_bank(), "大跌", k=2, rng=random.Random(0)))
        self.assertIn("嚴禁", block)
        self.assertIn("只學", block)
        fs = self._prompt(fewshot_block=block)
        self.assertEqual(fs.replace(block.rstrip() + "\n\n", ""), base)
        self.assertLess(fs.index("few-shot"), fs.index("===輸出格式"))


class VariantTest(unittest.TestCase):
    def test_run_writes_private_meta_with_distinct_cover(self):
        def fake_generate(*args, **kw):
            self.assertTrue(kw.get("fewshot_block"))
            return {"titles": ["創意為何買30.4億？｜10/12 AI帶你看股市", "x"]}

        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(StringIO()):
                meta = fv.run_fewshot_variant(
                    "k", _sd(), "報告", "2026-10-12", {}, ["創意買30.4億！長榮也買｜10/12 AI帶你看股市"], tmp,
                    bank=_bank(), rng=random.Random(0), generate=fake_generate,
                )
            self.assertEqual(meta["fewshot_arm"], "b")
            self.assertEqual(meta["regime"], "大跌")
            a, b = meta["arms"]["a"], meta["arms"]["b"]
            self.assertTrue(a["cover_text"] and b["cover_text"])
            self.assertNotEqual(a["cover_text"], b["cover_text"])
            self.assertIn("？", b["cover_text"])
            path = os.path.join(tmp, "youtube", "2026-10-12", fb.FEWSHOT_META_NAME)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(fb.read_day_meta(tmp, "2026-10-12")["arms"]["b"]["method"], "fewshot")
            pkg = os.path.join(tmp, "pkg.md")
            with open(pkg, "w", encoding="utf-8") as f:
                f.write("# pkg\n")
            fv.append_package_section(pkg, meta)
            fv.append_package_section(pkg, meta)
            with open(pkg, encoding="utf-8") as f:
                self.assertEqual(f.read().count(fv.SECTION_HEADING), 1)

    def test_failure_never_raises(self):
        def boom(*a, **k):
            raise RuntimeError("429")

        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(StringIO()):
            self.assertIsNone(fv.run_fewshot_variant("k", _sd(), "", "2026-10-12", {}, ["t"], tmp, bank=_bank(), generate=boom))
            self.assertIsNone(fv.run_fewshot_variant("k", _sd(), "", "2026-10-12", {}, ["t"], tmp, bank={}))

    def test_kill_switch(self):
        self.assertTrue(fv.enabled({}))
        self.assertFalse(fv.enabled({"FEWSHOT_AB": "off"}))


class RecordingTest(unittest.TestCase):
    def test_record_fs_result_and_summary(self):
        from modules.analytics_store import AnalyticsStore
        from scripts.youtube import fewshot_ab_summary, record_ab_result

        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "analytics.db")
            reports = os.path.join(tmp, "reports")
            store = AnalyticsStore(db)
            store.record_youtube_thumbnail_variants(
                "2026-10-12", [{"variant": "a", "cover_text": ["x"]}, {"variant": "b", "cover_text": ["y"]}]
            )
            with mock.patch.object(record_ab_result, "REPORTS_DIR", reports), redirect_stdout(StringIO()):
                with self.assertRaises(SystemExit):
                    record_ab_result.main(["--date", "2026-10-12", "--hypothesis", "FS", "--winner", "b", "--db", db])
            fb.write_day_meta(reports, "2026-10-12", {"fewshot_arm": "b"})
            fb.write_day_meta(reports, "2026-10-13", {"fewshot_arm": "a"})
            with mock.patch.object(record_ab_result, "REPORTS_DIR", reports), redirect_stdout(StringIO()) as out:
                record_ab_result.main(["--date", "2026-10-12", "--hypothesis", "FS", "--winner", "b", "--db", db])
                record_ab_result.main(["--date", "2026-10-13", "--hypothesis", "fewshot", "--winner", "b", "--db", db])
            self.assertIn("few-shot 勝 1／baseline 勝 1", out.getvalue())
            rows = store.get_ab_test_results()
            self.assertEqual({r["hypothesis_id"] for r in rows}, {"FS"})
            self.assertIn("fewshot_arm=b", rows[0]["notes"])
            # daily package test: the stored covers ARE the A/B arms, so the winner is marked
            self.assertTrue(any(v["selected"] for v in store.get_youtube_thumbnail_variants("2026-10-12")))
            with redirect_stdout(StringIO()) as out2:
                fewshot_ab_summary.main(["--db", db, "--reports-dir", reports])
            self.assertIn("n=2", out2.getvalue())


class DailyPackageTestTest(unittest.TestCase):
    def test_current_hypothesis_is_daily_fs_or_pk(self):
        from modules import cover_ab_test as cab

        self.assertEqual(cab.current_hypothesis("2026-10-14", env={})["id"], "FS")
        self.assertEqual(cab.current_hypothesis("2026-10-19", env={})["id"], "FS")
        self.assertEqual(cab.current_hypothesis("2026-10-14", env={"FEWSHOT_AB": "off"})["id"], "PK")
        self.assertEqual(cab.current_hypothesis("2026-10-14", env={"AB_HYPOTHESIS": "H3"})["id"], "H3")
        self.assertFalse(cab.enabled_hypotheses(cab.load_plan()))  # weekly rotation retired
        line = "- A/B variable: " + cab.variable_line(cab.package_hypothesis(), "b")
        self.assertEqual(cab.parse_variable_line(line), {"variable": "fewshot", "hypothesis_id": "FS", "arm": "b"})

    def _covers(self, hypothesis, titles, cover_texts=None):
        from modules.thumbnail_generator import generate_ab_test_thumbnails

        sd = _sd()
        sd["local_market_facts"] = {
            "stocks": {
                "3443": {"ticker": "3443", "name": "創意", "close": 7150.0, "pct_change": -3.11, "foreign_net_yi": 30.4},
            }
        }
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(StringIO()):
            result = generate_ab_test_thumbnails(
                None, None, "", tmp, styles=["dc_comics"], structured_data=sd, run_date="2026-10-12",
                preferred_titles=titles, ab_hypothesis=hypothesis, cover_texts=cover_texts,
            )
            prompts = []
            for t in result["thumbnails"]:
                with open(t["path"], encoding="utf-8") as f:
                    prompts.append(f.read())
        return result, prompts

    def test_fs_covers_pair_own_title_and_headline(self):
        from scripts.youtube import check_youtube_package as chk

        titles = ["創意買30.4億！長榮也買｜10/12 AI帶你看股市", "創意為何買30.4億？｜10/12 AI帶你看股市"]
        result, prompts = self._covers("FS", titles, {"a": "3443 創意\n外資買30.4億", "b": "3443 創意\n外資為何買30.4億？"})
        self.assertEqual([t["title"] for t in result["thumbnails"]], titles)
        self.assertEqual([t["cover_text"] for t in result["thumbnails"]], ["3443 創意\n外資買30.4億", "3443 創意\n外資為何買30.4億？"])
        self.assertIn("外資為何買30.4億？", prompts[1])
        self.assertNotIn("外資為何買30.4億？", prompts[0])
        failures, warnings = [], []
        with redirect_stdout(StringIO()):
            chk.check_ab_covers(prompts, failures, warnings)
        self.assertEqual(failures, [])

    def test_pk_fallback_gives_different_headlines(self):
        titles = ["創意買30.4億！長榮也買｜10/12 AI帶你看股市", "創意30.4億卻跌？｜10/12 AI帶你看股市"]
        result, _ = self._covers("PK", titles)
        a, b = (t["cover_text"] for t in result["thumbnails"])
        self.assertTrue(a and b)
        self.assertNotEqual(a, b)

    def test_check_fails_on_same_headline(self):
        from scripts.youtube import check_youtube_package as chk
        from modules import cover_ab_test as cab

        h = cab.package_hypothesis()
        mk = lambda l, t: f"Title pairing: {t}\n- Primary: 創意\n- Secondary: 外資買30億\n- A/B variable: {cab.variable_line(h, l)}\n"
        failures = []
        with redirect_stdout(StringIO()):
            chk.check_ab_covers([mk("a", "x"), mk("b", "y")], failures, [])
        self.assertTrue(any("大字相同" in f for f in failures))

    def test_replace_title_b(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = os.path.join(tmp, "p.md")
            with open(pkg, "w", encoding="utf-8") as f:
                f.write("**標題 A（原方法；配封面 A）：**\n```\nAAA\n```\n**標題 B（測試組；配封面 B）：**\n```\nOLD\n```\n")
            self.assertTrue(fv.replace_package_title_b(pkg, "NEWB"))
            with open(pkg, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("AAA", text)
            self.assertIn("NEWB", text)
            self.assertNotIn("OLD", text)


if __name__ == "__main__":
    unittest.main()
