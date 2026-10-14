#!/usr/bin/env python3
import os
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.current_index import build_current_index, write_current_index  # noqa: E402


class CurrentIndexTest(unittest.TestCase):
    def test_real_reports_list_latest_and_recent_specials(self):
        reports_dir = os.path.join(BASE_DIR, "reports")
        md = build_current_index(reports_dir, "2026-09-15", daily_ready=True, youtube_ready=True)
        self.assertIn("今日獨立專題", md)
        # No topic on 2026-09-15: the newest earlier one is shown as「最近一次」, not as today's.
        self.assertIn("最近一次獨立專題（2026-09-11）", md)
        self.assertIn("2026-09-11_cpi_core_scenarios", md)
        self.assertIn("iPhone 18", md)
        self.assertIn("SpaceX", md)
        # Topics after the index date are not listed.
        self.assertNotIn("2026-09-28_holiday_gap_us30min", md)

    def test_write_keeps_current_hand_written_special_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "special_topics", "2026-09-15_manual")
            os.makedirs(folder)
            with open(os.path.join(folder, "keep.md"), "w", encoding="utf-8") as f:
                f.write("ok")
            prev = "\n".join(
                [
                    "# 今日產出索引",
                    "",
                    "## 今日獨立專題",
                    "",
                    "- 狀態：手寫保留",
                    "- 報告：[`keep.md`](./special_topics/2026-09-15_manual/keep.md)",
                    "",
                    "## 最近完成的獨立專題",
                    "",
                ]
            )
            path = os.path.join(tmp, "CURRENT.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(prev)
            write_current_index(tmp, "2026-09-15", daily_ready=False, youtube_ready=False)
            with open(path, encoding="utf-8") as f:
                out = f.read()
            self.assertIn("手寫保留", out)
            self.assertIn("keep.md", out)


if __name__ == "__main__":
    unittest.main()
