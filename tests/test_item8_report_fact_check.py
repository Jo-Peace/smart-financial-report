#!/usr/bin/env python3
"""Item 8: daily_report.md numbers / ranks reconciled with local TWSE payloads."""

import json
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

from modules.report_fact_check import (  # noqa: E402
    check_report_facts,
    enforce_local_tables,
    fact_check_report,
    missing_required_sections,
)


def _institutional():
    buys = [
        ("3443", "創意", 30.39),
        ("2603", "長榮", 29.59),
        ("3037", "欣興", 15.52),
        ("6278", "台表科", 14.14),
        ("4938", "和碩", 14.02),
        ("2887", "台新新光金", 13.82),
        ("3711", "日月光投控", 13.34),
        ("2609", "陽明", 11.91),
        ("2313", "華通", 10.22),
        ("3605", "宏致", 9.87),
    ]
    sells = [
        ("2330", "台積電", -240.28),
        ("2454", "聯發科", -98.82),
        ("2317", "鴻海", -63.12),
        ("3008", "大立光", -40.86),
        ("2308", "台達電", -37.1),
        ("1303", "南亞", -26.46),
        ("2368", "金像電", -25.86),
        ("0050", "元大台灣50", -24.61),
        ("4958", "臻鼎-KY", -21.76),
        ("2303", "聯電", -20.65),
    ]
    return {
        "data_date": "2026-10-12",
        "top_buy": [{"id": t, "name": n, "est_amount": a} for t, n, a in buys],
        "top_sell": [{"id": t, "name": n, "est_amount": a} for t, n, a in sells],
    }


def _volume():
    rows = [
        ("2409", "友達", 309571757, -2.87),
        ("00406A", "主動中信台灣收益", 229191572, 0.2),
        ("00685L", "群益臺灣加權正2", 177649917, 0.15),
        ("1301", "台塑", 175230874, -0.74),
        ("00631L", "元大台灣50正2", 130463306, -0.02),
        ("009816", "凱基台灣TOP50", 129923389, 0.12),
        ("1101", "台泥", 128673485, 5.06),
        ("00403A", "主動統一升級50", 115174051, 0.0),
        ("2609", "陽明", 109474138, 6.53),
        ("1312", "國喬", 106556179, 5.94),
        ("2330", "台積電", 106500000, -1.2),
        ("0050", "元大台灣50", 106348658, -0.13),
    ]
    rows += [(f"9{i:03d}", f"量{i}", 1000 - i, 0.1) for i in range(8)]
    return [
        {"rank": i + 1, "id": t, "name": n, "volume": v, "close_price": 10.0, "pct_change": p}
        for i, (t, n, v, p) in enumerate(rows)
    ]


# Shape of the 2026-10-12 daily report (Gemini kept 5 volume rows and 5+5 flows).
REPORT = """## 〔今日主軸〕[重大事件日]

外資對 **2330 台積電** 單日賣超 240.3 億元。

---

## 〔成交量排名〕今日最真實的市場投票

| 排名 | 代號 | 名稱 | 成交量 | 漲跌幅 | 今日角色 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | 2409 | 友達 | 309,571,757 | -2.87% | 量大卻跌 |
| 4 | 1301 | 台塑 | 175,230,874 | -0.74% | 基層材料換手 |
| 12 | 0050 | 元大台灣50 | 106,348,658 | -0.13% | 外資賣超 24.6 億，成為今日最主要的提款機。 |

---

## 〔法人籌碼〕外資買賣超（資料日期：2026-10-11）

**外資買超前 5 名（估計金額）：**
1. **3443 創意**：約 +30.4 億元（未進量榜）
2. **2603 長榮**：約 +29.6 億元

**外資賣超前 5 名（估計金額）：**
1. **2330 台積電**：約 -240.3 億元（長假後系統性調節）

---

## 〔量價對比〕法人說的 vs 市場做的

- 外資今日砸下 30.4 億元買進 **3443 創意**，這金額足以排上全台第一。
- 雖然昨日外資大買 **2409 友達** 81 億，但今日反手。
- **2603 長榮**：外資買超第 5 名。

## 〔相似盤對照〕

- **2026-06-09**：外資同樣買超 **2313 華通** 奪冠，但隔日華通卻重挫 6.16%。

## 〔明日觀察焦點〕節奏 + 趨勢
"""


class FactCheckTest(unittest.TestCase):
    def setUp(self):
        self.inst = _institutional()
        self.volume = _volume()
        self.env = mock.patch.dict(os.environ, {"REPORT_FACT_CHECK": "annotate", "REPORT_LOCAL_TABLES": "1"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_superlative_and_rank_mismatches_fail(self):
        result = check_report_facts(REPORT, institutional_data=self.inst, volume_data=self.volume, known_names=set())
        self.assertEqual(result["status"], "fail")
        by_ticker = {i["ticker"]: i for i in result["issues"]}
        self.assertIn("0050", by_ticker)
        self.assertIn("第 8 名", by_ticker["0050"]["correction"])
        self.assertIn("2330 台積電", by_ticker["0050"]["correction"])
        self.assertIn("2603", by_ticker)  # claimed 買超第 5, locally 第 2
        # Verified claims and other-day / similar-session claims are not flagged.
        self.assertNotIn("3443", by_ticker)
        self.assertNotIn("2409", by_ticker)
        self.assertNotIn("2313", by_ticker)

    def test_local_tables_replace_partial_ai_tables(self):
        text, notes = enforce_local_tables(REPORT, institutional_data=self.inst, volume_data=self.volume)
        self.assertIn("| 10 | 1312 | 國喬 |", text)
        self.assertIn("| 2 | 00406A |", text)
        self.assertIn("量大卻跌", text)  # AI role cell kept
        self.assertIn("| 12 | 0050 | 元大台灣50 |", text)  # AI-chosen extra row keeps its real rank
        self.assertIn("10. **2303 聯電**：約 -20.6 億元", text)
        self.assertIn("8. **0050 元大台灣50**：約 -24.6 億元", text)
        self.assertIn("1. **2330 台積電**：約 -240.3 億元（長假後系統性調節）", text)
        self.assertIn("資料日期：2026-10-12", text)
        self.assertTrue(any("成交量表" in n for n in notes))
        self.assertTrue(any("法人籌碼" in n for n in notes))

    def test_full_hook_annotates_wrong_claims(self):
        text, summary = fact_check_report(REPORT, institutional_data=self.inst, volume_data=self.volume)
        self.assertIn("最主要的提款機（本機核對：0050 元大台灣50 為外資賣超第 8 名", text)
        self.assertGreaterEqual(summary["annotated"], 1)
        self.assertEqual(summary["status"], "fail")
        self.assertIn("〔明日觀察焦點〕", text)

    def test_warn_mode_records_without_rewriting_prose(self):
        with mock.patch.dict(os.environ, {"REPORT_FACT_CHECK": "warn"}):
            text, summary = fact_check_report(REPORT, institutional_data=self.inst, volume_data=self.volume)
        self.assertNotIn("本機核對", text)
        self.assertEqual(summary["status"], "fail")

    def test_no_local_data_is_a_noop(self):
        text, summary = fact_check_report(REPORT, institutional_data={}, volume_data=[])
        self.assertEqual(text, REPORT)
        self.assertEqual(summary["status"], "skipped")

    def test_amount_sign_mismatch(self):
        report = "## 〔量價對比〕\n- 外資今日大買 **2330 台積電** 240 億元。\n- 今天外資狂掃台積電 240 億元。\n"
        result = check_report_facts(report, institutional_data=self.inst, volume_data=self.volume, known_names=set())
        self.assertEqual([i["type"] for i in result["issues"]], ["amount_sign", "amount_sign"])

    def test_multi_day_and_other_group_ranks_are_not_checked(self):
        # The local lists are foreign single-day ranks; these claims are about
        # something else and must not get a false 「本機核對」 correction.
        for line in (
            "長榮是外資本週買超第 1 名。",
            "長榮近5日外資買超第一名。",
            "- 長榮：外資連續 3 日買超第 1 名",
            "投信買超第一的長榮。",
            "長榮是投信本週買超第 1 名",
            "長榮投信買超冠軍。",
            "三大法人合計買超第一的長榮。",
            "長榮近 5 日成交量第 1 名。",
        ):
            report = "## 〔量價對比〕\n" + line + "\n"
            result = check_report_facts(report, institutional_data=self.inst, volume_data=self.volume, known_names=set())
            self.assertEqual(result["issues"], [], line)
            text, _summary = fact_check_report(report, institutional_data=self.inst, volume_data=self.volume)
            self.assertNotIn("本機核對", text, line)
        # A plain single-day foreign claim is still checked, even after another clause about 投信.
        result = check_report_facts(
            "## 〔量價對比〕\n投信大買長榮，長榮是外資買超第 1 名。\n",
            institutional_data=self.inst,
            volume_data=self.volume,
            known_names=set(),
        )
        self.assertEqual(result["status"], "fail")
        self.assertIn("第 2 名", result["issues"][0]["correction"])

    def test_local_tables_state_units(self):
        text, _notes = enforce_local_tables(REPORT, institutional_data=self.inst, volume_data=self.volume)
        self.assertIn("| 排名 | 代號 | 名稱 | 成交量（股） | 漲跌幅 | 今日角色 |", text)
        self.assertIn("外資股數×收盤", text)
        self.assertNotIn("外資張數", text)

    def test_fact_check_file_follows_the_latest_run(self):
        from modules.pipeline_persist import write_fact_check_file

        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(StringIO()):
            path = write_fact_check_file(tmp, {"status": "fail", "issues": [{"ticker": "0050"}]})
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["status"], "fail")
            # Same-day rerun that passes: the earlier file must not linger.
            write_fact_check_file(tmp, {"status": "pass", "issues": []})
            self.assertFalse(os.path.exists(path))
            write_fact_check_file(tmp, {"status": "warn", "issues": []})
            self.assertTrue(os.path.exists(path))
            write_fact_check_file(tmp, {"status": "off"})
            self.assertFalse(os.path.exists(path))
            write_fact_check_file(tmp, {"status": "skipped"})  # nothing to remove: no error
            self.assertFalse(os.path.exists(path))

    def test_strict_section_abort_names_the_override(self):
        from modules.analyzer import MarketAnalyzer

        with mock.patch.dict(os.environ, {"REPORT_SECTION_CHECK": "strict"}):
            problems = MarketAnalyzer._daily_report_problems("no sections")
        self.assertIn("REPORT_SECTION_CHECK=warn", problems[0])

    def test_value_rank_claims_use_the_market_wide_value_ranking(self):
        value_ranking = {
            "data_date": "2026-10-12",
            "stocks": [
                {"rank": 1, "id": "2330", "name": "台積電", "trade_value_yi": 573.2},
                {"rank": 2, "id": "3443", "name": "創意", "trade_value_yi": 300.0},
                {"rank": 3, "id": "2603", "name": "長榮", "trade_value_yi": 102.0},
            ],
            "etfs": [],
        }
        report = (
            "## 〔今日主軸〕\n\n"
            "- 今日 **3443 創意** 成交值第 1 名，資金湧入。\n"
            "- **2603 長榮** 成交值第 3 名。\n"
            "- **2409 友達** 觀察池成交值第 2 名。\n"
        )
        result = check_report_facts(
            report, institutional_data=self.inst, volume_data=self.volume, value_ranking=value_ranking, known_names=set()
        )
        by_ticker = {i["ticker"]: i for i in result["issues"]}
        self.assertIn("3443", by_ticker)
        self.assertIn("個股成交值第 2 名", by_ticker["3443"]["correction"])
        self.assertIn("2330 台積電", by_ticker["3443"]["correction"])
        self.assertNotIn("2603", by_ticker)  # verified
        self.assertNotIn("2409", by_ticker)  # pool rank: a different list, not checked
        # Without value_ranking a 成交值 claim is unverifiable, never flagged.
        quiet = check_report_facts(report, institutional_data=self.inst, volume_data=self.volume, known_names=set())
        self.assertFalse(any(i["claim"].startswith("個股成交值") for i in quiet["issues"]))
        # The persist hook passes value_ranking through.
        _text, summary = fact_check_report(
            report, institutional_data=self.inst, volume_data=self.volume, value_ranking=value_ranking
        )
        self.assertTrue(any(i["ticker"] == "3443" and i["type"] == "rank" for i in summary["issues"]))

    def test_generic_first_uses_the_keyword_nearest_the_superlative(self):
        value_ranking = {
            "data_date": "2026-10-12",
            "stocks": [
                {"rank": 1, "id": "2330", "name": "台積電", "trade_value_yi": 573.2},
                {"rank": 2, "id": "2603", "name": "長榮", "trade_value_yi": 102.0},
            ],
            "etfs": [],
        }
        for vr in (value_ranking, None):
            # 成交值 earlier in the sentence does not turn a foreign-buy
            # superlative into a 成交值 claim: 3443 創意 is foreign-buy #1.
            for line in (
                "今日創意成交值放大、外資加碼力道全市場第一",
                "創意成交金額 50 億、外資買盤全市場第一",
                "今日創意成交值放大、外資買盤全市場第一",
            ):
                result = check_report_facts(
                    "## 〔量價對比〕\n- " + line + "\n",
                    institutional_data=self.inst,
                    volume_data=self.volume,
                    value_ranking=vr,
                    known_names=set(),
                )
                self.assertEqual(result["issues"], [], (line, bool(vr)))
            # 2603 長榮 is foreign-buy #2: still flagged as a buy claim, with or without the tape.
            result = check_report_facts(
                "## 〔量價對比〕\n- 今日長榮成交值放大、外資買盤全市場第一\n",
                institutional_data=self.inst,
                volume_data=self.volume,
                value_ranking=vr,
                known_names=set(),
            )
            self.assertEqual([i["claim"] for i in result["issues"]], ["外資買超第1名"], bool(vr))
            self.assertIn("外資買超第 2 名", result["issues"][0]["correction"])
        # The nearest keyword 成交值 still makes it a 成交值 claim.
        result = check_report_facts(
            "## 〔量價對比〕\n- 外資買超的長榮今日成交值全市場第一\n",
            institutional_data=self.inst,
            volume_data=self.volume,
            value_ranking=value_ranking,
            known_names=set(),
        )
        self.assertEqual([i["claim"] for i in result["issues"]], ["個股成交值第1名"])
        # A 觀察池 成交值 superlative is a different list: not checked.
        result = check_report_facts(
            "## 〔量價對比〕\n- 長榮外資買盤強，觀察池成交值全市場第一\n",
            institutional_data=self.inst,
            volume_data=self.volume,
            value_ranking=value_ranking,
            known_names=set(),
        )
        self.assertEqual(result["issues"], [])

    def test_required_sections(self):
        self.assertEqual(missing_required_sections(REPORT), [])
        self.assertEqual(
            missing_required_sections("## 〔今日主軸〕x"),
            ["法人籌碼", "觀察焦點"],
        )
        self.assertEqual(missing_required_sections("〔今日主軸〕〔法人籌碼〕〔下週一觀察焦點〕"), [])


if __name__ == "__main__":
    unittest.main()
