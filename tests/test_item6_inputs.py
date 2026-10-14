#!/usr/bin/env python3
"""Input fixes: revenue YoY, calendar week, homonym-safe news, news audit, market extras."""
import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules import data_fetcher as df_mod  # noqa: E402
from modules import market_extras as mx  # noqa: E402
from modules.data_fetcher import DataFetcher, supply_chain_radar_queries  # noqa: E402
from modules.entity_match import mentions_entity, news_query_for_topic  # noqa: E402
from modules.pipeline_persist import build_news_inputs, write_news_inputs  # noqa: E402
from modules.pipeline_stages import collect_news_bundle, tape_focus_entities  # noqa: E402


def _fetcher():
    fetcher = DataFetcher.__new__(DataFetcher)
    fetcher.tavily_search_count = 0
    fetcher.tavily_search_limit = 10
    fetcher.search_log = []
    return fetcher


class RevenueYoYTest(unittest.TestCase):
    """2026-10-08 published 南亞科「營收年增 684%」: that was latest quarter vs 3 quarters back."""

    @staticmethod
    def _fin(cols, revenue, gross):
        return pd.DataFrame([revenue, gross], index=["Total Revenue", "Gross Profit"], columns=pd.to_datetime(cols))

    def test_yoy_uses_same_quarter_last_year(self):
        cols = ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"]
        fin = self._fin(cols, [200.0, 150.0, 120.0, 100.0, 80.0], [100.0, 60.0, 48.0, 40.0, 32.0])
        out = DataFetcher.quarterly_fundamentals(fin)
        self.assertEqual(out["revenue_yoy_pct"], 150.0)  # 200 vs 80, not 200 vs 100
        self.assertEqual(out["revenue_yoy_basis"], "2026-06 vs 2025-06")
        self.assertEqual(out["gross_margin_4q"], [50.0, 40.0, 40.0, 40.0])
        self.assertEqual(out["margin_trend"], "改善")

    def test_without_year_ago_column_only_labelled_qoq(self):
        cols = ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"]
        fin = self._fin(cols, [200.0, 160.0, 120.0, 100.0], [100.0, 60.0, 48.0, 40.0])
        out = DataFetcher.quarterly_fundamentals(fin)
        self.assertNotIn("revenue_yoy_pct", out)
        self.assertEqual(out["revenue_qoq_pct"], 25.0)

    def test_gap_check_rejects_non_year_gap(self):
        cols = ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30", "2024-06-30"]
        fin = self._fin(cols, [200.0, 150.0, 120.0, 100.0, 80.0], [1.0] * 5)
        self.assertNotIn("revenue_yoy_pct", DataFetcher.quarterly_fundamentals(fin))

    def test_monthly_revenue_parse(self):
        rows = [
            {"公司代號": "2603", "資料年月": "11509", "營業收入-當月營收": "49711309",
             "營業收入-上月比較增減(%)": "1.48", "營業收入-去年同月增減(%)": "63.7474",
             "累計營業收入-前期比較增減(%)": "15.36", "備註": "運價受市場供需影響"},
        ]
        out = mx.parse_monthly_revenue(rows, "2603")
        self.assertEqual(out["month"], "2026-09")
        self.assertEqual(out["yoy_pct"], 63.7)
        self.assertIsNone(mx.parse_monthly_revenue(rows, "2408"))


class CalendarWeekTest(unittest.TestCase):
    MARCH = ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06", "2026-03-09"]

    def test_tuesday_run_reports_last_full_week(self):
        pos, meta = DataFetcher.select_week_positions(self.MARCH, today=datetime.date(2026, 3, 10))
        self.assertEqual([self.MARCH[p] for p in pos], self.MARCH[:5])
        self.assertTrue(meta["week_complete"])
        self.assertEqual(meta["iso_week"], "2026-W10")

    def test_weekend_run_uses_week_ending_friday(self):
        dates = self.MARCH[:5]
        pos, _ = DataFetcher.select_week_positions(dates, today=datetime.date(2026, 3, 7))
        self.assertEqual(len(pos), 5)

    # TWSE 2026-10-09 (國慶 Friday) — bars 09-28 .. 10-08 (Thursday).
    OCT = ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02",
           "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"]

    def test_weekend_after_holiday_friday_reports_short_week(self):
        for today in (datetime.date(2026, 10, 10), datetime.date(2026, 10, 11)):
            pos, meta = DataFetcher.select_week_positions(self.OCT, today=today)
            self.assertEqual([self.OCT[p] for p in pos], self.OCT[5:], today)
            self.assertEqual(meta["iso_week"], "2026-W41")
            self.assertTrue(meta["week_complete"])

    def test_holiday_friday_itself_needs_exchange_calendar(self):
        friday = datetime.date(2026, 10, 9)
        pos, meta = DataFetcher.select_week_positions(self.OCT, today=friday)
        self.assertEqual(meta["iso_week"], "2026-W40")  # no calendar → conservative
        from modules.twse_calendar import is_twse_session
        pos, meta = DataFetcher.select_week_positions(self.OCT, today=friday, session_checker=is_twse_session)
        self.assertEqual(meta["iso_week"], "2026-W41")
        self.assertEqual(len(pos), 4)

    def test_midweek_run_still_reports_previous_week(self):
        from modules.twse_calendar import is_twse_session
        dates = self.OCT[:7]  # through Tue 10-06
        pos, meta = DataFetcher.select_week_positions(dates, today=datetime.date(2026, 10, 7), session_checker=is_twse_session)
        self.assertEqual(meta["iso_week"], "2026-W40")
        self.assertEqual([dates[p] for p in pos], dates[:5])

    def test_weekly_payload_is_dated(self):
        dates = ["2026-02-27"] + self.MARCH
        closes = [100, 101, 102, 103, 104, 110, 90]
        idx = pd.DatetimeIndex(pd.to_datetime(dates)).tz_localize("America/New_York")
        hist = pd.DataFrame({"Close": closes, "High": closes, "Low": closes, "Open": closes, "Volume": [10] * 7}, index=idx)
        ticker = mock.Mock()
        ticker.history.return_value = hist
        with mock.patch.object(df_mod.yf, "Ticker", return_value=ticker):
            res = _fetcher().get_weekly_stock_data("^VIX", as_of="2026-03-10")
        data = res["data"]
        self.assertEqual((data["week_start"], data["week_end"]), ("2026-03-02", "2026-03-06"))
        self.assertEqual(data["week_close"], 110)
        self.assertEqual(data["pre_week_close"], 100)
        self.assertEqual(data["week_pct_change"], 10.0)
        self.assertEqual(data["daily_series"][-1]["weekday"], "週五")
        self.assertEqual(data["daily_series"][-1]["date"], "03/06(週五)")
        self.assertEqual(data["daily_series"][-1]["pct_change"], 5.77)


class HomonymTest(unittest.TestCase):
    KNOWN = {"長榮": "2603", "長榮航": "2618", "長榮航太": "2645", "南亞": "1303", "南亞科": "2408", "創意": "3443"}

    def test_evergreen_marine_vs_airways(self):
        text = "長榮航連三年舉辦綠色供應鏈計畫頒獎"
        self.assertFalse(mentions_entity(text, {"id": "2603", "name": "長榮"}, self.KNOWN))
        self.assertTrue(mentions_entity(text, {"id": "2618", "name": "長榮航"}, self.KNOWN))
        self.assertTrue(mentions_entity("長榮(2603)運價走強", {"id": "2603", "name": "長榮"}, self.KNOWN))
        self.assertTrue(mentions_entity("長榮海運第三季", {"id": "2603", "name": "長榮"}, self.KNOWN))

    def test_nanya_and_common_words(self):
        self.assertFalse(mentions_entity("東南亞產能擴張", {"id": "1303", "name": "南亞"}, self.KNOWN))
        self.assertFalse(mentions_entity("南亞科 DRAM 報價", {"id": "1303", "name": "南亞"}, self.KNOWN))
        self.assertFalse(mentions_entity("充滿創意的行銷", {"id": "3443", "name": "創意"}, self.KNOWN))
        self.assertTrue(mentions_entity("創意電子 ASIC 接單", {"id": "3443", "name": "創意"}, self.KNOWN))
        self.assertTrue(mentions_entity("3443 創意 法說", {"id": "3443", "name": "創意"}, self.KNOWN))
        self.assertFalse(mentions_entity("代號 34430", {"id": "3443", "name": "創意"}, self.KNOWN))

    def test_radar_query_carries_codes(self):
        entities = tape_focus_entities({"top_buy": [{"id": "3443", "name": "創意"}, {"id": "2603", "name": "長榮"}]})
        queries = supply_chain_radar_queries(lite=True, focus_names=entities[:2])
        self.assertIn("創意(3443)", queries[1])
        self.assertIn("長榮(2603)", queries[1])


class NewsQueryTest(unittest.TestCase):
    def test_tape_topic_becomes_chinese_entity_query(self):
        self.assertEqual(news_query_for_topic("Taiwan stock 2603 長榮 外資買超 原因"), "長榮 2603 外資買超 股價 新聞")
        self.assertEqual(news_query_for_topic("Taiwan Semiconductor"), "Taiwan Semiconductor stock news")
        self.assertEqual(news_query_for_topic("台股 今日強勢族群 逆勢抗跌"), "台股 今日強勢族群 逆勢抗跌")

    def test_fallback_keeps_entity(self):
        self.assertEqual(DataFetcher._broad_news_query("長榮 2603 外資買超 股價 新聞"), "長榮 2603")
        self.assertIsNone(DataFetcher._broad_news_query("長榮"))

    def test_get_news_uses_news_topic_and_logs(self):
        fetcher = _fetcher()
        fetcher.tavily_client = mock.Mock()
        fetcher.tavily_client.search.side_effect = [
            {"results": []},
            {"results": [{"title": "長榮運價", "url": "https://news.example/a", "content": "x"}]},
        ]
        with mock.patch("modules.api_retry.api_retry", lambda f: f):
            res = fetcher.get_news("長榮 2603 外資買超 股價 新聞")
        self.assertTrue(res["ok"])
        first_kwargs = fetcher.tavily_client.search.call_args_list[0].kwargs
        self.assertEqual(first_kwargs["topic"], "news")
        self.assertEqual(first_kwargs["days"], 2)
        self.assertEqual(fetcher.tavily_client.search.call_args_list[1].args[0], "長榮 2603")
        self.assertEqual([e["stage"] for e in fetcher.search_log], ["news", "news_fallback"])

    def test_general_topic_days_become_time_range(self):
        out = DataFetcher._recency_kwargs({"days": 7, "max_results": 2})
        self.assertEqual(out, {"time_range": "week", "max_results": 2})
        self.assertEqual(DataFetcher._recency_kwargs({"days": 2, "topic": "news"}), {"days": 2, "topic": "news"})

    def test_radar_urls_are_not_duplicated_into_news(self):
        from modules.fetch_result import fetch_ok, unwrap_fetch_result

        fetcher = mock.Mock()
        fetcher.get_news.return_value = fetch_ok(
            [{"url": "https://radar.example/x", "title": "r"}, {"url": "https://news.example/y", "title": "n"}],
            source="tavily",
        )

        _, unique = collect_news_bundle(
            fetcher, True, ["Taiwan stock 2603 長榮 外資買超 原因"], [{"url": "https://radar.example/x", "event": "e"}],
            unwrap_fetch_result, print_fn=None,
        )
        self.assertEqual([n["url"] for n in unique], ["https://news.example/y"])
        self.assertEqual(fetcher.get_news.call_args_list[0].args[0], "長榮 2603 外資買超 股價 新聞")


class HomonymNewsScreenTest(unittest.TestCase):
    """Finding 58: a 長榮航 (2618) article returned for 長榮 2603 must not reach the prompt as 長榮 news."""

    AIRWAYS = {"title": "長榮航第三季客運量創高", "url": "https://n/air", "content": "長榮航（2618）旅運需求強勁"}
    MARINE = {"title": "長榮運價回升 外資買超", "url": "https://n/marine", "content": "長榮(2603)今日外資買超"}
    WRAP = {"title": "台股盤後：外資賣超台積電", "url": "https://n/wrap", "content": "加權指數小跌"}

    def test_topic_entity(self):
        from modules.entity_match import topic_entity

        self.assertEqual(topic_entity("Taiwan stock 2603 長榮 外資買超 原因"), {"id": "2603", "name": "長榮"})
        focus = [{"id": "3443", "name": "創意"}]
        self.assertEqual(topic_entity("創意 3443 訂單 法說 營運", focus), {"id": "3443", "name": "創意"})
        self.assertIsNone(topic_entity("台股 今日強勢族群 逆勢抗跌", focus))

    def test_screen_rejects_only_homonyms(self):
        from modules.entity_match import screen_homonym_news

        kept, rejected = screen_homonym_news([self.AIRWAYS, self.MARINE, self.WRAP], {"id": "2603", "name": "長榮"})
        self.assertEqual([k["url"] for k in kept], ["https://n/marine", "https://n/wrap"])
        self.assertEqual([(r["url"], r["reason"]) for r in rejected], [("https://n/air", "homonym")])
        word = {"title": "創意行銷大賽", "url": "https://n/word", "content": "品牌創意"}
        kept, rejected = screen_homonym_news([word], {"id": "3443", "name": "創意"})
        self.assertEqual((kept, [r["url"] for r in rejected]), ([], ["https://n/word"]))
        self.assertEqual(screen_homonym_news([word], None), ([word], []))

    def test_tape_topic_results_are_screened_and_recorded(self):
        from modules.fetch_result import fetch_ok, unwrap_fetch_result

        fetcher = mock.Mock()
        fetcher.get_news.return_value = fetch_ok([self.AIRWAYS, self.MARINE], source="tavily")
        rejected = []
        news, unique = collect_news_bundle(
            fetcher, True, ["Taiwan stock 2603 長榮 外資買超 原因"], [], unwrap_fetch_result,
            print_fn=None, focus_names=[{"id": "2603", "name": "長榮"}], rejected=rejected,
        )
        self.assertEqual([n["url"] for n in news], ["https://n/marine"])
        self.assertEqual([n["url"] for n in unique], ["https://n/marine"])
        self.assertEqual(rejected[0]["url"], "https://n/air")
        self.assertEqual(rejected[0]["query"], "長榮 2603 外資買超 股價 新聞")

        record = build_news_inputs(
            {
                "searches": [{"stage": "news", "query": "長榮 2603 外資買超 股價 新聞",
                              "results": [self.AIRWAYS, self.MARINE]}],
                "prompt_news": unique,
                "rejected_news": rejected,
            },
            "2026-10-12",
        )
        self.assertEqual(record["rejected_news"][0]["reason"], "homonym")
        self.assertFalse(record["rejected_news"][0]["in_prompt"])
        self.assertEqual(record["rejected_news"][0]["entity"], "長榮 2603")
        rows = {r["url"]: r for r in record["searches"][0]["results"]}
        self.assertEqual((rows["https://n/air"]["in_prompt"], rows["https://n/air"]["reason"]), (False, "homonym"))
        self.assertTrue(rows["https://n/marine"]["in_prompt"])
        self.assertNotIn("reason", rows["https://n/marine"])

    def test_deep_dive_news_is_screened(self):
        fetcher = _fetcher()
        ticker = mock.Mock()
        ticker.quarterly_financials = None
        ticker.history.return_value = pd.DataFrame()
        responses = [{"results": [self.AIRWAYS, self.MARINE]}, {"results": []}, {"results": []}]
        with mock.patch.object(df_mod.yf, "Ticker", return_value=ticker), \
                mock.patch("modules.market_extras.fetch_monthly_revenue", return_value=None), \
                mock.patch.object(fetcher, "_tavily_search", side_effect=responses):
            res = fetcher.get_deep_dive_data("2603", "長榮")
        data = res["data"] if isinstance(res, dict) and "data" in res and "ok" in res else res
        self.assertEqual([n["url"] for n in data["recent_news"]], ["https://n/marine"])
        self.assertEqual([(n["url"], n["reason"]) for n in data["rejected_news"]], [("https://n/air", "homonym")])
        self.assertNotIn("長榮航", data["summary_for_ai"])
        record = build_news_inputs({"deep_dive": data}, "2026-10-12")
        self.assertEqual([r["url"] for r in record["rejected_news"]], ["https://n/air"])


class NewsInputsArchiveTest(unittest.TestCase):
    def test_archive_marks_prompt_usage(self):
        inputs = {
            "searches": [
                {"stage": "news", "query": "長榮 2603", "params": {"topic": "news"},
                 "results": [{"title": "A", "url": "https://n/a", "snippet": "s"}, {"title": "B", "url": "https://n/b"}]},
            ],
            "prompt_news": [{"title": "A", "url": "https://n/a", "content": "c"}],
            "radar": [{"event": "R", "url": "https://r/1", "snippet": "rs", "impact_score": 80}],
            "macro_events": [],
            "deep_dive": {"stock_id": "2603", "stock_name": "長榮", "recent_news": [{"title": "D", "url": "https://d/1", "snippet": "ds"}],
                          "summary_for_ai": "【2603】"},
        }
        record = build_news_inputs(inputs, "2026-10-12")
        flags = {r["url"]: r["in_prompt"] for r in record["searches"][0]["results"]}
        self.assertEqual(flags, {"https://n/a": True, "https://n/b": False})
        self.assertEqual(record["radar"][0]["impact_score"], 80)
        self.assertEqual(record["deep_dive"]["recent_news"][0]["url"], "https://d/1")
        with tempfile.TemporaryDirectory() as tmp:
            path = write_news_inputs(tmp, "2026-10-12", inputs)
            self.assertTrue(path.endswith(os.path.join("daily", "2026-10-12", "news_inputs.json")))
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["date"], "2026-10-12")
            self.assertIsNone(write_news_inputs(tmp, "2026-10-12", None))


class MarketExtrasTest(unittest.TestCase):
    FUT = [
        {"Date": "20261012", "ContractCode": "臺股期貨", "Item": "外資及陸資", "OpenInterest(Net)": "-82806", "TradingVolume(Net)": "643"},
        {"Date": "20261012", "ContractCode": "臺股期貨", "Item": "投信", "OpenInterest(Net)": "76848", "TradingVolume(Net)": "509"},
        {"Date": "20261012", "ContractCode": "電子期貨", "Item": "外資及陸資", "OpenInterest(Net)": "-131", "TradingVolume(Net)": "-8"},
    ]
    PCR = [{"Date": "20261012", "PutCallVolumeRatio%": "102.35", "PutCallOIRatio%": "84.78"}]
    MARGIN = {
        "stat": "OK", "date": "20261012",
        "tables": [{"fields": ["項目", "買進", "賣出", "現金(券)償還", "前日餘額", "今日餘額"],
                    "data": [["融資(交易單位)", "372,347", "314,516", "8,099", "9,403,368", "9,453,100"],
                             ["融券(交易單位)", "17,138", "22,000", "619", "211,946", "216,189"],
                             ["融資金額(仟元)", "1", "1", "1", "646,405,767", "653,329,500"]]}],
    }
    TPEX_FIELDS = ["代號", "名稱"] + ["買進股數", "賣出股數", "買賣超股數"] * 7 + ["三大法人買賣超股數合計"]
    TPEX = {
        "stat": "ok", "date": "20261012",
        "tables": [{"date": "115/10/12", "fields": TPEX_FIELDS, "data": [
            ["3105", "穩懋", "5,510,570", "6,482,603", "-972,033", "0", "0", "0", "5,510,570", "6,482,603", "-972,033",
             "25,240", "1,959,000", "-1,933,760", "138,000", "218,600", "-80,600", "50,164", "78,143", "-27,979",
             "188,164", "296,743", "-108,579", "-3,014,372"],
        ]}],
    }
    TPEX_CLOSE = {"stat": "ok", "date": "20261012", "tables": [{"date": "115/10/12", "fields": ["代號", "名稱", "收盤 "], "data": [["3105", "穩懋", "580.00"]]}]}

    def test_parsers_are_date_checked(self):
        oi = mx.parse_taifex_futures_oi(self.FUT, "2026-10-12")
        self.assertEqual(oi["foreign_oi_net"], -82806)
        self.assertEqual(oi["trust_oi_net"], 76848)
        self.assertIsNone(mx.parse_taifex_futures_oi(self.FUT, "2026-10-13"))  # openapi serves latest day only
        self.assertEqual(mx.parse_taifex_pc_ratio(self.PCR, "2026-10-12")["pc_oi_ratio_pct"], 84.78)
        margin = mx.parse_twse_margin(self.MARGIN, "2026-10-12")
        self.assertEqual(margin["margin_change_lots"], 49_732)
        self.assertEqual(margin["margin_amount_change_yi"], 69.2)
        self.assertIsNone(mx.parse_twse_margin({"stat": "很抱歉，沒有符合條件的資料"}, "2026-10-13"))

    def test_tpex_ranking(self):
        rows = mx.parse_tpex_institutional(self.TPEX, "2026-10-12")
        self.assertEqual(rows[0]["foreign_net"], -972_033)
        self.assertEqual(rows[0]["trust_net"], -1_933_760)
        self.assertEqual(rows[0]["total_net"], -3_014_372)
        closes = mx.parse_tpex_close(self.TPEX_CLOSE, "2026-10-12")
        ranked = mx.rank_tpex(rows, closes, "2026-10-12")
        self.assertAlmostEqual(ranked["top_sell"][0]["est_amount"], -5.64, places=2)  # -972,033 股 × 580
        bad = dict(self.TPEX, tables=[dict(self.TPEX["tables"][0], fields=self.TPEX_FIELDS[:-1])])
        self.assertEqual(mx.parse_tpex_institutional(bad, "2026-10-12"), [])

    def test_fetch_failures_never_raise(self):
        with mock.patch.object(mx, "_get_json", side_effect=ValueError("boom")):
            out = mx.fetch_market_extras("2026-10-12")
        self.assertEqual({k: out[k] for k in ("taifex_oi", "pc_ratio", "margin", "tpex")}, dict.fromkeys(("taifex_oi", "pc_ratio", "margin", "tpex")))
        with mock.patch.dict(os.environ, {"MARKET_EXTRAS": "0"}), mock.patch.object(mx, "_get_json") as get:
            mx.fetch_market_extras("2026-10-12")
        get.assert_not_called()
        self.assertEqual(set(mx.missing_extras(out)), {"期貨未平倉", "P/C ratio", "融資融券", "上櫃法人"})


class GlobalOvernightTest(unittest.TestCase):
    def test_quotes_are_dated_and_labelled(self):
        idx = pd.DatetimeIndex(pd.to_datetime(["2026-10-09", "2026-10-12"])).tz_localize("America/New_York")
        hist = pd.DataFrame({"Close": [300.0, 297.0]}, index=idx)
        ticker = mock.Mock()
        ticker.history.return_value = hist
        with mock.patch.object(df_mod.yf, "Ticker", return_value=ticker):
            res = _fetcher().get_global_overnight(symbols=["TSM", "TWD=X"])
        tsm, twd = res["data"]["TSM"], res["data"]["TWD=X"]
        self.assertEqual(tsm["bar_date"], "2026-10-12")
        self.assertEqual(tsm["pct_change"], -1.0)
        self.assertEqual(twd["quote_kind"], "latest_quote")

    def test_failure_is_empty_not_raise(self):
        with mock.patch.object(df_mod.yf, "Ticker", side_effect=RuntimeError("blocked")):
            res = _fetcher().get_global_overnight()
        self.assertFalse(res["ok"])
        self.assertEqual(res["data"], {})


if __name__ == "__main__":
    unittest.main()
