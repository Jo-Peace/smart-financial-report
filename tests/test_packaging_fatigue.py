#!/usr/bin/env python3
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.packaging_fatigue import (  # noqa: E402
    analyze_title_opener_streaks,
    guard_youtube_titles,
    title_is_unusable,
)


class PackagingFatigueTest(unittest.TestCase):
    def test_foreign_amount_opener_blocks_on_second_streak(self):
        titles = [
            "外資109億狂砍華邦電！量能第6還在殺｜9/9 AI帶你看股市",
            "外資26億狂掃強茂噴6%！台積電卻被抽270億｜9/14 AI帶你看股市",
        ]
        streak = analyze_title_opener_streaks(titles)
        self.assertEqual(streak["trailing_label"], "外資金額開場")
        self.assertEqual(streak["trailing_streak"], 2)
        self.assertTrue(streak["block"])

    def test_rejects_shadow_and_who_receives_and_unpublished_rate(self):
        fatigue = {
            "recent_titles": ["強茂噴6%，誰在收？｜9/14 AI帶你看股市"],
            "opener_streak": {},
        }
        self.assertTrue(title_is_unusable("外資31億影子建倉台達電｜9/15", fatigue, "2026-09-15"))
        self.assertTrue(title_is_unusable("台積電砍101億，誰在收？｜9/15", fatigue, "2026-09-15"))
        self.assertTrue(title_is_unusable("升息來了台積電怎麼辦｜9/15", fatigue, "2026-09-15"))
        self.assertFalse(title_is_unusable("明天台積電除息7元！外資卻先砍101億｜9/15", fatigue, "2026-09-15"))

    def test_guard_swaps_dead_formula_for_local(self):
        recent = ["外資26億狂掃強茂噴6%！台積電卻被抽270億｜9/14 AI帶你看股市"]
        fatigue = {
            "recent_titles": recent,
            "opener_streak": analyze_title_opener_streaks(recent),
        }
        self.assertTrue(fatigue["opener_streak"]["warn"])
        guarded = guard_youtube_titles(
            ["外資31億狂掃台達電！台積電卻被砍101億｜9/15 AI帶你看股市"],
            fatigue_state=fatigue,
            local_candidates=["明天台積電除息7元！外資卻先砍101億｜9/15 AI帶你看股市"],
            short_date="9/15",
            date_str="2026-09-15",
        )
        self.assertTrue(guarded)
        self.assertFalse(any("狂掃" in title for title in guarded))
        self.assertTrue(any("除息" in title for title in guarded))


if __name__ == "__main__":
    unittest.main()
