#!/usr/bin/env python3
"""Item 9: knowledge wiki — verifiable fields only, digest trigger, prompt budget, upsert/dedupe."""
import datetime
import os
import sys
import tempfile
import unittest
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import modules.knowledge_manager as km  # noqa: E402
from modules.knowledge_manager import (  # noqa: E402
    KnowledgeUpdater,
    build_wiki_prompt_context,
    covered_digest_week,
    dedupe_dated_blocks,
    sort_dated_blocks,
    split_dated_blocks,
    upsert_dated_block,
)
from modules.local_knowledge_compounder import LocalKnowledgeCompounder  # noqa: E402
from modules.pipeline_support import (  # noqa: E402
    TITLE_RULES_MAX_CHARS,
    _read_tail,
    read_markdown_head_sections,
    read_recent_dated_entries,
)

D = datetime.date


def _structured(date_str, **extra):
    data = {
        "date": date_str,
        "index_action": "下跌 11.5點（-0.02%）",
        "safe_havens_bought": ["創意 (+30.4億)", "長榮 (+29.6億)"],
        "heavyweights_dumped": ["台積電 (-240.3億)"],
        "price_volume_divergence": ["創意：股價下跌但外資大買，呈現低調掃貨背離"],
        "conservative_strategy": "保留 30% 以上現金靜待權值股止穩",
        "prediction_targets": [
            {"id": "3443", "name": "創意", "direction": "多", "stop_loss_price": 7558,
             "trigger": "法人進行結構性鎖貨"},
        ],
        "trust_conflicts": ["台積電：外資賣、投信買，外資 -240.3億，投信 +15.0億"],
    }
    data.update(extra)
    return data


def _updater(tmp):
    with mock.patch("modules.gemini_client.default_model_name", return_value="m"):
        updater = KnowledgeUpdater(api_key=None)
    updater.wiki_dir = tmp
    return updater


class DatedBlockTest(unittest.TestCase):
    CONTENT = (
        "# 年表\n\n說明\n\n"
        "### 2026-10-05（週一）\n- a\n\n"
        "### 2026-10-07（週三）\n- b\n\n"
        "## 手寫備註\n不要動\n"
    )

    def test_insert_in_date_order_and_replace_only_that_span(self):
        text, action = upsert_dated_block(self.CONTENT, "2026-10-06", "### 2026-10-06（週二）\n- new")
        self.assertEqual(action, "inserted")
        dates = [d for d, _ in split_dated_blocks(text)[1]]
        self.assertEqual(dates, ["2026-10-05", "2026-10-06", "2026-10-07"])
        text2, action = upsert_dated_block(text, "2026-10-07", "### 2026-10-07（週三）\n- fixed", replace=True)
        self.assertEqual(action, "replaced")
        self.assertIn("- fixed", text2)
        self.assertIn("## 手寫備註\n不要動", text2)
        self.assertEqual(upsert_dated_block(text2, "2026-10-07", "x")[1], "skipped")
        self.assertEqual(
            upsert_dated_block(text2, "2026-10-07", "### 2026-10-07（週三）\n- fixed", replace=True)[1],
            "unchanged",
        )

    def test_dedupe_keeps_last_and_sort_reorders_backfill(self):
        dup = "# t\n\n## 2026-05-27\n- old\n\n## 2026-05-27\n- new\n\n## 2026-06-30\n- x\n\n## 2026-03-06\n- backfill\n"
        cleaned, removed = dedupe_dated_blocks(dup)
        self.assertEqual(removed, 1)
        self.assertNotIn("- old", cleaned)
        ordered, moved = sort_dated_blocks(cleaned)
        self.assertTrue(moved)
        self.assertEqual([d for d, _ in split_dated_blocks(ordered)[1]], ["2026-03-06", "2026-05-27", "2026-06-30"])
        self.assertTrue(ordered.startswith("# t\n\n"))


class DigestTriggerTest(unittest.TestCase):
    def test_friday_holiday_uses_thursday_and_monday_catches_up(self):
        # 2026-10-09 (Fri) is a TWSE holiday in data/twse_closed_days.json.
        self.assertEqual(covered_digest_week("2026-10-08"), (D(2026, 10, 5), D(2026, 10, 8)))
        self.assertEqual(covered_digest_week("2026-10-12"), (D(2026, 10, 5), D(2026, 10, 8)))
        self.assertEqual(covered_digest_week("2026-10-16"), (D(2026, 10, 12), D(2026, 10, 16)))
        self.assertEqual(covered_digest_week("2026-10-14"), (D(2026, 10, 5), D(2026, 10, 8)))

    def test_should_run_uses_report_date_not_host_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            updater = _updater(tmp)
            with open(os.path.join(tmp, ".last_digest_date"), "w") as f:
                f.write("2026-10-02")
            self.assertTrue(updater._should_run_weekly_digest("2026-10-12"))
            updater._save_digest_stamp(D(2026, 10, 8))
            self.assertFalse(updater._should_run_weekly_digest("2026-10-13"))
            self.assertTrue(updater._should_run_weekly_digest("2026-10-16"))

    def test_local_digest_covers_one_week_with_both_sides(self):
        with tempfile.TemporaryDirectory() as tmp:
            updater = _updater(tmp)
            for day in ("2026-10-01", "2026-10-05", "2026-10-06", "2026-10-08"):
                updater._append_local_from_structured(_structured(day), day)
            updater._run_weekly_digest_local("2026-10-12")
            with open(os.path.join(tmp, "weekly_digest.md"), encoding="utf-8") as f:
                digest = f.read()
            self.assertIn("<!-- digest_end: 2026-10-08 -->", digest)
            self.assertNotIn("2026-10-01", digest)
            self.assertIn("買超/轉強證據", digest)
            self.assertIn("賣超/轉弱證據", digest)
            self.assertIn("重大事件", digest)
            with open(os.path.join(tmp, ".last_digest_date")) as f:
                self.assertEqual(f.read().strip(), "2026-10-08")


class LocalEntriesTest(unittest.TestCase):
    def test_entries_drop_gemini_narrative(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = _updater(tmp).build_local_entries(_structured("2026-10-12"))
            blob = "\n".join(entries.values())
            self.assertNotIn("掃貨", blob)
            self.assertNotIn("鎖貨", blob)
            self.assertNotIn("現金", blob)
            self.assertIn("停損 7558", blob)
            self.assertIn("外資／投信對作", blob)

    def test_rerun_replace_and_first_run_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            updater = _updater(tmp)
            updater._append_local_from_structured(_structured("2026-10-12"), "2026-10-12")
            fixed = _structured("2026-10-12", heavyweights_dumped=["台積電 (-200.0億)"])
            res = updater._append_local_from_structured(fixed, "2026-10-12")
            self.assertEqual(res["skipped_existing"], 3)
            res = updater._append_local_from_structured(fixed, "2026-10-12", replace_existing=True)
            self.assertEqual(res["files_updated"], 1)
            with open(os.path.join(tmp, "capital_rotation.md"), encoding="utf-8") as f:
                text = f.read()
            self.assertIn("賣超/轉弱證據**：台積電 (-200.0億)", text)
            self.assertNotIn("賣超/轉弱證據**：台積電 (-240.3億)", text)
            self.assertEqual(text.count("### 2026-10-12"), 1)


class MacroEventsTest(unittest.TestCase):
    def test_only_events_since_previous_session(self):
        calendar = [
            {"date": "2026-10-02", "when": "已發生", "text": "美國非農 NFP。細節"},
            {"date": "2026-10-09", "when": "已發生", "text": "台股休市（國慶日補假）。禁止把這天講成開盤"},
        ]
        radar = [{"event": "緯創法說前逼近歷史高點 - 經濟日報", "impact_score": 80, "freshness": "fresh"}]
        with tempfile.TemporaryDirectory() as tmp:
            updater = _updater(tmp)
            macro_1007 = updater.build_local_entries(_structured("2026-10-07", calendar_events=calendar))["macro_events.md"]
            macro_1012 = updater.build_local_entries(
                _structured("2026-10-12", calendar_events=calendar, supply_chain_event_radar=radar)
            )["macro_events.md"]
        self.assertNotIn("非農", macro_1007)
        self.assertIn("2026-10-09 台股休市（國慶日補假）", macro_1012)
        # an event dated on the previous session was already written that day as 「今天」
        calendar.append({"date": "2026-10-08", "when": "已發生", "text": "台積電法說會。細節"})
        with tempfile.TemporaryDirectory() as tmp:
            macro_again = _updater(tmp).build_local_entries(
                _structured("2026-10-12", calendar_events=calendar)
            )["macro_events.md"]
        self.assertNotIn("法說會", macro_again)
        self.assertNotIn("禁止", macro_1012)
        self.assertIn("事件雷達：緯創法說前逼近歷史高點（分數 80）", macro_1012)

    def test_estimated_msci_window_is_not_written_as_fact(self):
        calendar = [
            {"date": "2026-11-27", "when": "今天", "kind": "msci_rebalance_window", "estimated": True,
             "text": "MSCI 11 月季度調整生效日未確認（估計在月底前後）。未經公告確認前不是日曆事實"},
            {"date": "2026-11-27", "when": "今天", "kind": "nfp", "text": "美國非農 NFP。細節"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            macro = _updater(tmp).build_local_entries(_structured("2026-11-27", calendar_events=calendar))["macro_events.md"]
        self.assertNotIn("MSCI", macro)
        self.assertIn("美國非農 NFP", macro)


class RepairScriptTest(unittest.TestCase):
    def test_dry_run_reports_and_apply_rewrites_with_backup(self):
        import importlib.util
        import json

        spec = importlib.util.spec_from_file_location(
            "repair_learning_logs", os.path.join(BASE_DIR, "scripts", "maintenance", "repair_learning_logs.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            wiki = os.path.join(tmp, "data", "knowledge_wiki")
            os.makedirs(wiki)
            os.makedirs(os.path.join(tmp, "reports", "shared"))
            os.makedirs(os.path.join(tmp, "reports", "daily", "2026-10-12"))
            with open(os.path.join(tmp, "reports", "daily", "2026-10-12", "structured_data.json"), "w", encoding="utf-8") as f:
                json.dump(_structured("2026-10-12"), f, ensure_ascii=False)
            with open(os.path.join(wiki, "macro_events.md"), "w", encoding="utf-8") as f:
                f.write("# m\n\n### 2026-05-27\n- a\n\n### 2026-05-27\n- a\n\n### 2026-10-12（週一）\n- **重大事件**：低調掃貨背離\n")
            with open(os.path.join(wiki, "local_market_memory.md"), "w", encoding="utf-8") as f:
                f.write("# mem\n\n## 2026-06-30\n- x\n\n## 2026-03-06\n- y\n")
            module.BASE_DIR = tmp
            module.WIKI_DIR = wiki
            module.REPORTS_DIR = os.path.join(tmp, "reports")
            report = module.repair(apply=False, rebuild_since="2026-10-01")
            self.assertTrue(any("macro_events.md: 去重 1" in line for line in report))
            with open(os.path.join(wiki, "macro_events.md"), encoding="utf-8") as f:
                self.assertIn("掃貨", f.read())  # dry-run leaves files alone
            module.repair(apply=True, rebuild_since="2026-10-01")
            with open(os.path.join(wiki, "macro_events.md"), encoding="utf-8") as f:
                macro = f.read()
            self.assertNotIn("掃貨", macro)
            self.assertEqual(macro.count("### 2026-05-27"), 1)
            with open(os.path.join(wiki, "local_market_memory.md"), encoding="utf-8") as f:
                memory = f.read()
            self.assertLess(memory.index("## 2026-03-06"), memory.index("## 2026-06-30"))
            self.assertIn("## 2026-10-12", memory)
            self.assertTrue(os.listdir(os.path.join(tmp, "data", "backup")))


class WikiPromptContextTest(unittest.TestCase):
    def _write(self, tmp, name, text):
        with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
            f.write(text)

    def test_budget_keeps_every_file_whole_entries_and_skips_stale_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            long_line = "- **買超/轉強證據**：" + "某股 (+1.0億)；" * 40
            for name in ("capital_rotation.md", "macro_events.md", "trading_strategies.md"):
                body = "# t\n\n" + "\n\n".join(
                    f"### 2026-10-{d:02d}（週）\n{long_line if name == 'capital_rotation.md' else '- 短條目 ' + name}"
                    for d in (5, 6, 7, 8, 12)
                )
                self._write(tmp, name, body)
            self._write(tmp, "weekly_digest.md", "# 週摘要：2026-09-28～2026-10-02（本機）\n<!-- digest_end: 2026-10-02 -->\n- x\n")
            text = build_wiki_prompt_context(report_date="2026-10-12", wiki_dir=tmp, max_chars=3500)
            self.assertNotIn("weekly_digest", text)  # 10 days old
            self.assertIn("trading_strategies.md", text)
            self.assertNotIn("2026-10-12", text)  # no same-day self-feedback
            self.assertLessEqual(len(text), 3600)
            text2 = build_wiki_prompt_context(report_date="2026-10-06", wiki_dir=tmp)
            self.assertIn("截至 2026-10-02", text2)
            self.assertNotIn("digest_end", text2)


class DigestPromptClipTest(unittest.TestCase):
    def test_every_digest_section_reaching_the_prompt_has_a_line(self):
        many = [f"某某電子股{i} (+{i}0.5億)" for i in range(1, 9)]
        with tempfile.TemporaryDirectory() as tmp:
            updater = _updater(tmp)
            for day in ("2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"):
                updater._append_local_from_structured(
                    _structured(day, safe_havens_bought=many, heavyweights_dumped=many), day
                )
            updater._run_weekly_digest_local("2026-10-12")
            with open(os.path.join(tmp, "weekly_digest.md"), encoding="utf-8") as f:
                self.assertGreater(len(f.read()), 1500)
            text = build_wiki_prompt_context(report_date="2026-10-13", wiki_dir=tmp)
        digest = text.split("=== weekly_digest.md")[1].split("\n=== ")[0]
        lines = digest.splitlines()
        headers = [i for i, line in enumerate(lines) if line.startswith("## ")]
        self.assertEqual(
            [lines[i] for i in headers],
            ["## 本週買賣證據鏈", "## 本週大盤與事件", "## 本週預測績效", "## 下週持續追蹤標的"],
        )
        for i in headers:
            self.assertTrue(i + 1 < len(lines) and lines[i + 1].startswith("- "), lines[i])
        self.assertIn("2026-10-08", digest)  # newest day first when the budget is tight
        self.assertNotIn("本摘要由本機條目自動壓縮", digest)
        self.assertLessEqual(len(digest), 900)


class LegacyRebuildTest(unittest.TestCase):
    def test_one_time_rebuild_strips_old_narrative_with_backup(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            updater = _updater(os.path.join(tmp, "wiki"))
            os.makedirs(updater.wiki_dir)
            updater.reports_dir = os.path.join(tmp, "reports")
            updater.backup_dir = os.path.join(tmp, "backup")
            for day in ("2026-10-08", "2026-10-12"):
                os.makedirs(os.path.join(updater.reports_dir, "daily", day))
                with open(os.path.join(updater.reports_dir, "daily", day, "structured_data.json"), "w", encoding="utf-8") as f:
                    json.dump(_structured(day), f, ensure_ascii=False)
            legacy = "# m\n\n### 2026-10-08（週四）\n- **重大事件**：南亞科屬安靜吸籌\n\n## 手寫\n保留\n"
            with open(os.path.join(updater.wiki_dir, "macro_events.md"), "w", encoding="utf-8") as f:
                f.write(legacy)
            with open(os.path.join(updater.wiki_dir, "local_market_memory.md"), "w", encoding="utf-8") as f:
                f.write("# mem\n\n## 2026-10-08\n\n- **量價/籌碼異常**：呈現低調掃貨背離\n")
            result = updater.rebuild_legacy_entries_once("2026-10-13")
            self.assertEqual(result["replaced"], {"macro_events.md": 1, "local_market_memory.md": 1})
            with open(os.path.join(updater.wiki_dir, "macro_events.md"), encoding="utf-8") as f:
                macro = f.read()
            self.assertNotIn("安靜吸籌", macro)
            self.assertIn("## 手寫\n保留", macro)
            self.assertNotIn("2026-10-12", macro)  # existing entries only, no back-fill
            with open(os.path.join(updater.wiki_dir, "local_market_memory.md"), encoding="utf-8") as f:
                self.assertNotIn("掃貨", f.read())
            self.assertTrue(os.listdir(updater.backup_dir))
            self.assertTrue(updater.rebuild_legacy_entries_once("2026-10-14")["skipped"])  # once only


class MemoryAndTailTest(unittest.TestCase):
    def test_compounder_memory_insert_order_replace_and_no_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            comp = LocalKnowledgeCompounder(base_dir=tmp)
            self.assertTrue(comp._append_local_market_memory(_structured("2026-10-12"), "2026-10-12"))
            self.assertTrue(comp._append_local_market_memory(_structured("2026-10-08"), "2026-10-08"))
            self.assertFalse(comp._append_local_market_memory(_structured("2026-10-08"), "2026-10-08"))
            fixed = _structured("2026-10-08", index_action="下跌 492.9點（-0.99%）")
            self.assertTrue(comp._append_local_market_memory(fixed, "2026-10-08", replace=True))
            with open(comp.local_memory_path, encoding="utf-8") as f:
                text = f.read()
            self.assertLess(text.index("## 2026-10-08"), text.index("## 2026-10-12"))
            self.assertIn("492.9", text)
            self.assertNotIn("鎖貨", text)
            self.assertNotIn("掃貨", text)

    def test_playbook_sections_prefer_pinned_and_newest_dated(self):
        body = "x" * 300 + "\n"
        text = (
            "# Playbook\n\n## 目前有效方向\n" + body + "## 2026-05-06 舊規則\n" + body
            + "## 2026-07-07 CTR 修正規則（生效中）\n" + body + "## 2026-05-15 標題假設\n" + body
            + "## 站外導流規則（2026-07-31）\n" + body
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "p.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            head = read_markdown_head_sections(path, max_chars=1000)
            recent = read_markdown_head_sections(path, max_chars=1000, prefer_recent=True)
        self.assertNotIn("站外導流規則", head.split("（以下章節")[0])
        kept = recent.split("（以下章節")[0]
        for title in ("目前有效方向", "CTR 修正規則（生效中）", "站外導流規則（2026-07-31）"):
            self.assertIn(title, kept)
        self.assertNotIn("2026-05-06 舊規則\n", kept)
        self.assertLess(kept.index("CTR 修正規則"), kept.index("站外導流規則"))  # file order kept
        self.assertIn("以下章節因長度略過：2026-05-06 舊規則、2026-05-15 標題假設", recent)

    def test_title_rules_file_is_loaded_whole(self):
        path = os.path.join(BASE_DIR, "project_skills", "youtube_title_packaging.md")
        with open(path, encoding="utf-8") as f:
            self.assertLessEqual(len(f.read()), TITLE_RULES_MAX_CHARS)
        self.assertIn("## Priority Order", read_markdown_head_sections(path, max_chars=TITLE_RULES_MAX_CHARS))

    def test_tail_readers_start_on_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "m.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write("# m\n\n## 2026-10-12\n- newest\n\n## 2026-03-06\n- backfill\n\n## 2026-10-08\n- " + "x" * 50 + "\n")
            tail = _read_tail(path, max_chars=30)
            self.assertTrue(tail.startswith("- ") or tail.startswith("#") or tail == "")
            recent = read_recent_dated_entries(path, max_chars=100)
            self.assertNotIn("backfill", recent)
            self.assertLess(recent.index("2026-10-08"), recent.index("2026-10-12"))
            self.assertNotIn("2026-10-12", read_recent_dated_entries(path, max_chars=200, before_date="2026-10-12"))


if __name__ == "__main__":
    unittest.main()
