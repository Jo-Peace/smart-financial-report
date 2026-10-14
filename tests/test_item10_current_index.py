#!/usr/bin/env python3
"""CURRENT.md special-topic section: folder scan + date-checked「今日」."""
import os
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.current_index import (  # noqa: E402
    _extract_section,
    build_current_index,
    is_current_special_date,
    scan_special_topics,
)


def _make_topic(reports_dir, date, slug, title="專題標題"):
    folder = os.path.join(reports_dir, "special_topics", f"{date}_{slug}")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"special_report_{date}_{slug}.md"), "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n內容\n")
    with open(os.path.join(folder, f"notebooklm_prompt_special_{date.replace('-', '')}_{slug}.md"), "w", encoding="utf-8") as f:
        f.write("prompt")
    return folder


class SpecialTopicIndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reports = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _today(self, md):
        return _extract_section(md, "今日獨立專題")

    def _recent(self, md):
        return _extract_section(md, "最近完成的獨立專題")

    def test_scan_orders_newest_first_and_reads_title(self):
        _make_topic(self.reports, "2026-09-10", "old", "舊專題")
        _make_topic(self.reports, "2026-10-12", "new", "新專題")
        os.makedirs(os.path.join(self.reports, "special_topics", "not_a_topic"))
        topics = scan_special_topics(self.reports)
        self.assertEqual([t["slug"] for t in topics], ["new", "old"])
        self.assertEqual(topics[0]["title"], "新專題")
        self.assertTrue(topics[0]["main"][1].endswith("special_report_2026-10-12_new.md"))
        self.assertEqual([label for label, _ in topics[0]["files"]], ["報告", "NotebookLM prompt"])

    def test_current_window_includes_weekend_and_holiday_specials(self):
        self.assertTrue(is_current_special_date("2026-10-12", "2026-10-12"))
        # Sunday special belongs to Monday's /1.
        self.assertTrue(is_current_special_date("2026-10-11", "2026-10-12"))
        # 2026-09-28 (教師節) special belongs to the 2026-09-29 reopen.
        self.assertTrue(is_current_special_date("2026-09-28", "2026-09-29"))
        self.assertFalse(is_current_special_date("2026-09-28", "2026-10-12"))
        self.assertFalse(is_current_special_date("2026-10-13", "2026-10-12"))

    def test_same_day_topic_is_today(self):
        _make_topic(self.reports, "2026-10-12", "cpi", "CPI 專題")
        md = build_current_index(self.reports, "2026-10-12", daily_ready=False)
        self.assertIn("專題（2026-10-12）：CPI 專題", self._today(md))
        self.assertNotIn("2026-10-12_cpi", self._recent(md))

    def test_old_topic_is_not_labelled_today(self):
        _make_topic(self.reports, "2026-09-28", "gap", "連假缺口")
        _make_topic(self.reports, "2026-09-11", "cpi", "CPI 專題")
        md = build_current_index(self.reports, "2026-10-12", daily_ready=False)
        today = self._today(md)
        self.assertIn("2026-10-12 沒有新的獨立專題", today)
        self.assertIn("最近一次獨立專題（2026-09-28）", today)
        self.assertIn("2026-09-11 CPI 專題", self._recent(md))

    def test_stale_hand_written_section_is_replaced(self):
        _make_topic(self.reports, "2026-09-28", "gap", "連假缺口")
        prev = "\n".join(
            [
                "## 今日獨立專題",
                "",
                "- 狀態：已產出（10:25 此刻）",
                "- 報告：[`r.md`](./special_topics/2026-09-28_gap/special_report_2026-09-28_gap.md)",
                "",
            ]
        )
        md = build_current_index(self.reports, "2026-10-12", daily_ready=False, previous_markdown=prev)
        self.assertNotIn("10:25 此刻", md)
        self.assertIn("沒有新的獨立專題", self._today(md))

    def test_current_hand_written_section_kept_and_new_topic_appended(self):
        _make_topic(self.reports, "2026-10-12", "a", "專題 A")
        _make_topic(self.reports, "2026-10-12", "b", "專題 B")
        prev = "\n".join(
            [
                "## 今日獨立專題",
                "",
                "- 狀態：手寫備註",
                "- 報告：[`a.md`](./special_topics/2026-10-12_a/special_report_2026-10-12_a.md)",
                "",
            ]
        )
        md = build_current_index(self.reports, "2026-10-12", daily_ready=False, previous_markdown=prev)
        today = self._today(md)
        self.assertIn("手寫備註", today)
        self.assertIn("專題 B", today)

    def test_no_topics_at_all(self):
        md = build_current_index(self.reports, "2026-10-12", daily_ready=False)
        self.assertNotIn("## 今日獨立專題", md)
        self.assertIn("尚無其他專題", md)


if __name__ == "__main__":
    unittest.main()
