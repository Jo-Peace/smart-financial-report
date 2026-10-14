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
    def test_preserves_today_special_and_iphone(self):
        reports_dir = os.path.join(BASE_DIR, "reports")
        md = build_current_index(reports_dir, "2026-09-15", daily_ready=True, youtube_ready=True)
        self.assertIn("今日獨立專題", md)
        self.assertIn("2026-09-11_cpi_core_scenarios", md)
        self.assertIn("iPhone 18", md)
        self.assertIn("SpaceX 專題報告", md)

    def test_write_keeps_existing_special_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            prev = "\n".join(
                [
                    "# 今日產出索引",
                    "",
                    "## 今日獨立專題",
                    "",
                    "- 狀態：手寫保留",
                    "- 報告：[`keep.md`](./keep.md)",
                    "",
                    "## 最近完成的獨立專題",
                    "",
                ]
            )
            open(os.path.join(tmp, "keep.md"), "w", encoding="utf-8").write("ok")
            path = os.path.join(tmp, "CURRENT.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(prev)
            write_current_index(tmp, "2026-09-15", daily_ready=False, youtube_ready=False)
            out = open(path, encoding="utf-8").read()
            self.assertIn("手寫保留", out)
            self.assertIn("keep.md", out)


if __name__ == "__main__":
    unittest.main()
