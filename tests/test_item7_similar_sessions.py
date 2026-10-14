#!/usr/bin/env python3
"""Item 7: similar sessions ignore index-weight noise, require same direction, stay point-in-time."""
import json
import os
import sys
import tempfile
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.similar_sessions import (  # noqa: E402
    MIN_SCORE,
    QUIET_BASE,
    card_from_structured,
    find_similar,
    format_for_prompt,
    load_history,
    no_analog_reason,
    pair_threshold,
    score_pair,
    summarize_outcomes,
)

DB = {
    "2891": {"name": "中信金"},
    "3443": {"name": "創意"},
    "3008": {"name": "大立光"},
    "2303": {"name": "聯電"},
    "2330": {"name": "台積電"},
    "2317": {"name": "鴻海"},
    "2308": {"name": "台達電"},
    "2408": {"name": "南亞科"},
    "2344": {"name": "華邦電"},
    "3037": {"name": "欣興"},
    "2603": {"name": "長榮"},
}


def card(date, index, bought, sold, results=None):
    return card_from_structured(
        {
            "date": date,
            "index_action": index,
            "safe_havens_bought": bought,
            "heavyweights_dumped": sold,
            "prev_day_picks_result": results or [],
        },
        stock_db=DB,
    )


class ScoringTest(unittest.TestCase):
    def test_mega_cap_sell_overlap_is_not_an_analog(self):
        # Same index direction and size, so only the flow-list comparison decides.
        query = card("2026-10-12", "下跌 280.5點（-1.20%）", ["長榮 (+29.6億)"], ["台積電 (-240.3億)", "鴻海 (-30.0億)"])
        hist = card("2026-03-31", "下跌 290.1點（-1.25%）", ["欣興 (+10.0億)"],
                    ["台積電 (-100.0億)", "鴻海 (-20.0億)", "台達電 (-5.0億)"])
        self.assertEqual(query["index_dir"], hist["index_dir"])
        score, reasons = score_pair(query, hist)
        self.assertIn("大盤同向", reasons)
        # Counting 2330/2317 would add sell Jaccard 2/3 (+25) and a shared sell leader (+12) → 67.
        self.assertLess(score, MIN_SCORE)
        self.assertFalse(any("2330" in r or "2317" in r for r in reasons))
        self.assertEqual(find_similar(query, [hist]), [])

    def test_non_mega_cap_overlap_still_scores(self):
        query = card("2026-10-12", "下跌 280.5點（-1.20%）", ["長榮 (+29.6億)"], ["台積電 (-240.3億)", "南亞科 (-80.0億)"])
        hist = card("2026-03-31", "下跌 290.1點（-1.25%）", ["欣興 (+10.0億)"], ["台積電 (-100.0億)", "南亞科 (-60.0億)"])
        score, reasons = score_pair(query, hist)
        self.assertGreaterEqual(score, MIN_SCORE)
        self.assertTrue(any("2408" in r for r in reasons))
        self.assertFalse(any("2330" in r for r in reasons))
        self.assertEqual([m["date"] for m in find_similar(query, [hist])], ["2026-03-31"])

    def test_text_direction_and_no_bonus_without_pct(self):
        hist = card("2026-03-31", "下跌 795.17點", [], [])
        self.assertEqual(hist["index_dir"], "down")
        self.assertIsNone(hist["index_pct"])
        flat_query = card("2026-10-12", "下跌 11.5點（-0.02%）", [], [])
        score, reasons = score_pair(flat_query, hist)
        self.assertEqual(score, 0)  # flat vs down: direction differs

    def test_opposite_direction_never_matches(self):
        q = card("2026-09-01", "上漲 820.2點（+1.78%）", ["欣興 (+50億)"], ["南亞科 (-200億)"])
        h = card("2026-07-15", "下跌 510點（-1.21%）", ["欣興 (+50億)"], ["南亞科 (-200億)"])
        self.assertEqual(score_pair(q, h)[0], 0)

    def test_flat_session_compares_with_quiet_moves_of_either_sign(self):
        """Smoke test 10: a -0.02% day is a dead band, not a direction of its own."""
        flat = card("2026-10-12", "下跌 11.5點（-0.02%）", ["長榮 (+29.6億)", "欣興 (+15.5億)", "華邦電 (+9.0億)"],
                    ["台積電 (-240.3億)", "南亞科 (-40.0億)", "台達電 (-37.1億)"])
        quiet_down = card("2026-08-17", "下跌 90點（-0.39%）", ["長榮 (+54.3億)", "欣興 (+12.0億)", "華邦電 (+8.0億)"],
                          ["台積電 (-241.0億)", "南亞科 (-30.0億)", "台達電 (-20.0億)"])
        big_down = card("2026-08-03", "下跌 400點（-1.60%）", ["長榮 (+54.3億)", "欣興 (+12.0億)", "華邦電 (+8.0億)"],
                        ["台積電 (-241.0億)", "南亞科 (-30.0億)", "台達電 (-20.0億)"])
        score, reasons = score_pair(flat, quiet_down)
        self.assertIn("大盤都是小幅震盪（平盤 vs 小漲跌）", reasons)
        self.assertEqual(score_pair(flat, big_down)[0], 0)
        # Same flow evidence as a directional pair: the quiet base only lowers the bar by the base gap.
        self.assertEqual(pair_threshold(QUIET_BASE), MIN_SCORE - (22 - QUIET_BASE))
        self.assertGreaterEqual(score, pair_threshold(QUIET_BASE))
        self.assertEqual([m["date"] for m in find_similar(flat, [quiet_down, big_down])], ["2026-08-17"])

    def test_empty_result_explains_why(self):
        flat = card("2026-10-12", "下跌 11.5點（-0.02%）", ["長榮 (+29.6億)", "創意 (+30.4億)"], ["大立光 (-40.9億)"])
        weak = card("2026-05-20", "下跌 90點（-0.39%）", ["中信金 (+59.2億)", "長榮 (+54.3億)"], ["聯電 (-59.5億)"])
        opposite = card("2026-05-21", "上漲 300點（+1.40%）", ["長榮 (+54.3億)"], ["聯電 (-59.5億)"])
        diag = {}
        self.assertEqual(find_similar(flat, [weak, opposite], diagnostics=diag), [])
        self.assertEqual((diag["compared"], diag["comparable"]), (2, 1))
        self.assertEqual(diag["best"]["date"], "2026-05-20")
        self.assertIn("2026-05-20", no_analog_reason(diag))
        prompt_reason = no_analog_reason(diag, with_date=False)
        self.assertNotIn("2026-05-20", prompt_reason)
        self.assertIn("未達門檻", prompt_reason)
        text = format_for_prompt([], no_analog=prompt_reason)
        self.assertIn("原因：", text)
        self.assertNotIn("2026-05-20", text)

    def test_recent_and_future_sessions_excluded(self):
        q = card("2026-10-07", "上漲 100點（+0.40%）", ["欣興 (+50億)"], ["南亞科 (-200億)"])
        yesterday = card("2026-10-06", "上漲 90點（+0.30%）", ["欣興 (+40億)"], ["南亞科 (-150億)"])
        future = card("2026-10-12", "上漲 90點（+0.30%）", ["欣興 (+40億)"], ["南亞科 (-150億)"])
        older = card("2026-08-20", "上漲 90點（+0.30%）", ["欣興 (+40億)"], ["南亞科 (-150億)"])
        dates = [m["date"] for m in find_similar(q, [yesterday, future, older])]
        self.assertEqual(dates, ["2026-08-20"])


class HistoryTest(unittest.TestCase):
    def _write(self, root, date, index, results=None):
        path = os.path.join(root, "daily", date)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "structured_data.json"), "w", encoding="utf-8") as f:
            json.dump(
                {"date": date, "index_action": index, "safe_havens_bought": ["欣興 (+10億)"],
                 "heavyweights_dumped": ["南亞科 (-10億)"], "prev_day_picks_result": results or []},
                f,
                ensure_ascii=False,
            )

    def test_before_date_hides_the_future(self):
        with tempfile.TemporaryDirectory() as root:
            self._write(root, "2026-08-03", "上漲 10點（+0.10%）")
            self._write(root, "2026-08-04", "上漲 10點（+0.10%）", ["3037 欣興 (+1.00%, 預測成立)"])
            self._write(root, "2026-08-20", "上漲 10點（+0.10%）", ["3037 欣興 (-1.00%, 預測未成立)"])
            history = load_history(root, stock_db=DB, before_date="2026-08-20", index_db_path=None)
            self.assertEqual([c["date"] for c in history], ["2026-08-03", "2026-08-04"])
            self.assertEqual(history[0]["next_date"], "2026-08-04")
            self.assertIsNone(history[1]["next_date"])  # 08-20 is the query, not known history


class PromptTest(unittest.TestCase):
    def test_outcomes_are_pooled(self):
        matches = [
            {"date": "2026-08-03", "score": 60, "next_date": "2026-08-04", "next_index_dir": "up",
             "next_day_results": ["3037 欣興 (+1.00%, 預測成立)"]},
            {"date": "2026-07-03", "score": 50, "next_date": "2026-07-06", "next_index_dir": "down",
             "next_day_results": ["2408 南亞科 (-2.00%, 預測未成立)"]},
        ]
        summary = summarize_outcomes(matches)
        self.assertEqual((summary["prediction_hits"], summary["prediction_misses"]), (1, 1))
        text = format_for_prompt(matches)
        self.assertIn("彙總", text)
        self.assertIn("樣本不足", text)
        self.assertIn("至少 3 個相似盤", text)


if __name__ == "__main__":
    unittest.main()
