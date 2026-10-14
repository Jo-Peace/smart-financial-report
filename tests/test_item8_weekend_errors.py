#!/usr/bin/env python3
"""Item 8: weekend / weekly / US scripts must not save Gemini error strings as reports."""

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

from modules import data_extractor  # noqa: E402
from modules.analyzer import abort_on_generation_error, is_report_error  # noqa: E402


class _FakeFetcher:
    def __init__(self, *args, **kwargs):
        pass

    def get_weekly_stock_data(self, symbol):
        return None

    def get_commodity_data(self):
        return {}

    def get_macro_events(self):
        return []

    def get_news(self, query):
        return []

    def get_stock_data(self, symbol):
        return None


class AbortHelperTest(unittest.TestCase):
    def test_error_string_and_empty_answer_abort(self):
        self.assertTrue(is_report_error("Error generating report: 503 UNAVAILABLE"))
        self.assertTrue(is_report_error("   "))
        self.assertTrue(is_report_error(None))
        self.assertFalse(is_report_error("# 週末特輯\n內容"))
        with redirect_stdout(StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                abort_on_generation_error("Error generating report: quota", label="週報")
        self.assertEqual(ctx.exception.code, 1)
        abort_on_generation_error("# 正常報告", label="週報")  # no-op


class WeekendExtractionTest(unittest.TestCase):
    def test_fenced_json_with_trailing_text_is_parsed(self):
        raw = '```json\n{"date": "2026-10-10", "us_market_hook": "x"}\n```\n多餘說明'
        with mock.patch.object(data_extractor, "generate_json", lambda *a, **k: raw):
            data = data_extractor.extract_weekend_structured_data("k", "報告")
        self.assertEqual(data["us_market_hook"], "x")

    def test_failure_returns_error_dict(self):
        def boom(*a, **k):
            raise RuntimeError("503 UNAVAILABLE")

        with mock.patch.object(data_extractor, "generate_json", boom), redirect_stdout(StringIO()):
            data = data_extractor.extract_weekend_structured_data("k", "報告")
        self.assertIn("error", data)


class WeekendMainTest(unittest.TestCase):
    """Item 8 guarantees on item 10's weekend script (topic folder + exit codes)."""

    def _run(self, report, extracted):
        import weekend_special_main as wsm

        analyzer = mock.MagicMock()
        analyzer._call_gemini_with_retry.return_value = report
        extract = mock.MagicMock(return_value=extracted)
        prompt = mock.MagicMock(return_value="prompt")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.multiple(
            wsm,
            TAVILY_API_KEY="t",
            GEMINI_API_KEY="g",
            REPORTS_DIR=tmp.name,
            DataFetcher=_FakeFetcher,
            MarketAnalyzer=mock.MagicMock(return_value=analyzer),
            extract_weekend_structured_data=extract,
            generate_weekend_special_prompt=prompt,
            select_skills=mock.MagicMock(return_value=[]),
        ), redirect_stdout(StringIO()):
            code = wsm.main()
        files = []
        for root, _dirs, names in os.walk(tmp.name):
            files.extend(names)
        return code, sorted(files), extract, prompt

    def test_error_report_is_not_written_or_extracted(self):
        code, files, extract, prompt = self._run("Error generating report: 429 quota", {})
        self.assertEqual(code, 4)  # EXIT_GEMINI_FAILED
        self.assertEqual(files, [])
        extract.assert_not_called()
        prompt.assert_not_called()

    def test_blank_answer_is_an_error_too(self):
        code, files, extract, _prompt = self._run("   ", {})
        self.assertEqual(code, 4)
        self.assertEqual(files, [])
        extract.assert_not_called()

    def test_extraction_error_skips_director_card(self):
        code, files, extract, prompt = self._run("# 週末特輯\n內容", {"error": "Failed", "raw_error": "x"})
        self.assertEqual(code, 5)  # EXIT_PERSIST_FAILED
        self.assertTrue(any(f.startswith("special_report_") for f in files))
        self.assertTrue(any(f.startswith("structured_data_failed_") for f in files))
        self.assertFalse(any(f.startswith("notebooklm_prompt_special_") for f in files))
        prompt.assert_not_called()

    def test_happy_path_still_writes_everything(self):
        code, files, _extract, prompt = self._run("# 週末特輯\n內容", {"us_market_hook": "x"})
        self.assertEqual(code, 0)
        self.assertTrue(any(f.startswith("notebooklm_prompt_special_") for f in files))
        prompt.assert_called_once()


if __name__ == "__main__":
    unittest.main()
