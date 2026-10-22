import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import main_app as m  # noqa: E402
from modules.data_quality_gate import evaluate_single_stock_quality  # noqa: E402


def _q(**kw):
    base = dict(run_date="2026-10-22", price_date="2026-10-22", institutional={"foreign": 1},
                institutional_date="2026-10-22", revenue={"revenue_100m": 1}, news_count=3)
    base.update(kw)
    return evaluate_single_stock_quality(**base)


class WebSkillsQualityTest(unittest.TestCase):
    def test_quality_clean(self):
        assert _q().issues == []

    def test_quality_flags_stale_and_missing(self):
        codes = {i.code for i in _q(price_date="2026-10-15", institutional=None, revenue=None, news_count=0).issues}
        assert {"price_stale", "institutional_missing", "revenue_missing", "news_missing"} <= codes

    def test_quality_before_close_accepts_previous_session(self):
        assert _q(price_date="2026-10-21", institutional_date="2026-10-21", after_close=False).issues == []

    def test_otc_skips_institutional_and_revenue(self):
        assert _q(institutional=None, revenue=None, institutional_expected=False, revenue_expected=False).issues == []

    def test_hit_record_format(self):
        assert "命中 2 次" in m.format_hit_record({"hits": 2, "total": 3, "last_date": "2026-10-15"})
        assert "無歷史命中紀錄" in m.format_hit_record(None)

    def test_hit_rates_file_has_no_private_fields(self):
        allowed = {"name", "hits", "total", "last_date", "role", "role_label"}
        for rec in m.load_hit_rates().values():
            assert set(rec) <= allowed

    def test_classify_gemini_error(self):
        assert m.classify_gemini_error("503 UNAVAILABLE overloaded") == "overloaded"
        assert m.classify_gemini_error("429 RESOURCE_EXHAUSTED") == "rate_limited"
        assert m.classify_gemini_error("400 API_KEY_INVALID") == "auth"

    def test_page_notes_show_warning(self):
        notes = m.format_page_notes(_q(news_count=0), None, "")
        assert "[!WARNING]" in notes and "[!NOTE]" in notes


if __name__ == '__main__':
    unittest.main()
