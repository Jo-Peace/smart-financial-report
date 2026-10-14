#!/usr/bin/env python3
"""Item 8: Gemini client completeness checks, model fallback, usage guard, retry wiring."""

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

from google.genai import types  # noqa: E402
from tenacity import retry, retry_if_exception, stop_after_attempt  # noqa: E402

from modules import ai_usage_guard, api_retry, gemini_client  # noqa: E402
from modules.ai_usage_guard import GeminiUsageLimitError  # noqa: E402
from modules.gemini_client import (  # noqa: E402
    GeminiEmptyResponse,
    GeminiIncompleteResponse,
    checked_response_text,
    response_text,
)


def _response(text="ok", finish="STOP", block=None, usage=None):
    parts = [types.Part(text=text)] if text is not None else None
    candidates = [types.Candidate(content=types.Content(parts=parts), finish_reason=finish)] if finish else []
    kwargs = {"candidates": candidates}
    if block:
        kwargs["prompt_feedback"] = types.GenerateContentResponsePromptFeedback(block_reason=block)
    if usage:
        kwargs["usage_metadata"] = types.GenerateContentResponseUsageMetadata(**usage)
    return types.GenerateContentResponse(**kwargs)


class _FakeModels:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def generate_content(self, model, contents, **kwargs):
        self.calls.append(model)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class _FakeClient:
    def __init__(self, script):
        self.models = _FakeModels(script)


def _no_wait_retry(max_attempts=4, min_wait=2, max_wait=60):
    return retry(
        retry=retry_if_exception(api_retry.is_retryable_exception),
        stop=stop_after_attempt(max_attempts),
    )


class _GuardedTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log_path = os.path.join(self.tmp.name, "usage.jsonl")
        self.patches = [
            mock.patch.object(ai_usage_guard, "usage_log_path", lambda: self.log_path),
            mock.patch.dict(
                os.environ,
                {
                    "GEMINI_DAILY_CALL_LIMIT": "8",
                    "GEMINI_RESERVED_CALLS": "3",
                    "GEMINI_ERROR_CALL_WEIGHT": "0.25",
                    "GEMINI_FALLBACK_MODELS": "gemini-2.5-flash",
                    "GEMINI_MODEL": "gemini-3-flash-preview",
                },
            ),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def rows(self):
        if not os.path.exists(self.log_path):
            return []
        with open(self.log_path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def seed_rows(self, statuses, task="market_report"):
        today = ai_usage_guard._today()
        with open(self.log_path, "a", encoding="utf-8") as f:
            for status in statuses:
                f.write(json.dumps({"date": today, "task": task, "status": status}) + "\n")


class ResponseTextTest(unittest.TestCase):
    def test_never_returns_repr(self):
        self.assertEqual(response_text(_response(text=None, finish="SAFETY")), "")
        self.assertEqual(response_text(types.GenerateContentResponse(candidates=[])), "")
        blocked = _response(text=None, finish=None, block="SAFETY")
        self.assertEqual(response_text(blocked), "")
        self.assertNotIn("GenerateContentResponse", response_text(blocked))

    def test_thought_parts_are_skipped(self):
        resp = types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(
                        parts=[types.Part(text="thinking...", thought=True), types.Part(text="answer")]
                    ),
                    finish_reason="STOP",
                )
            ]
        )
        self.assertEqual(response_text(resp), "answer")

    def test_truncated_and_blocked_answers_raise(self):
        with self.assertRaises(GeminiIncompleteResponse) as ctx:
            checked_response_text(_response(text="half a report", finish="MAX_TOKENS"))
        self.assertTrue(ctx.exception.retryable)
        with self.assertRaises(GeminiIncompleteResponse) as ctx:
            checked_response_text(_response(text=None, finish="SAFETY"))
        self.assertFalse(ctx.exception.retryable)
        with self.assertRaises(GeminiEmptyResponse):
            checked_response_text(_response(text=None, finish=None, block="PROHIBITED_CONTENT"))
        with self.assertRaises(GeminiEmptyResponse):
            checked_response_text(_response(text="   ", finish="STOP"))
        self.assertEqual(checked_response_text(_response(text="full", finish="STOP")), "full")


class ModelSelectionTest(unittest.TestCase):
    def test_default_model_is_pinned(self):
        with mock.patch.dict(os.environ, {"GEMINI_MODEL": ""}):
            self.assertEqual(gemini_client.default_model_name(), gemini_client.STABLE_DEFAULT_MODEL)
            self.assertFalse(gemini_client.is_unpinned_model(gemini_client.default_model_name()))
        self.assertTrue(gemini_client.is_unpinned_model("gemini-3-flash-preview"))
        self.assertTrue(gemini_client.is_unpinned_model("gemini-flash-latest"))

    def test_fallback_list_env(self):
        with mock.patch.dict(os.environ, {"GEMINI_FALLBACK_MODELS": "a, b ,a"}):
            self.assertEqual(gemini_client.fallback_model_names("b"), ["a"])
        with mock.patch.dict(os.environ, {"GEMINI_FALLBACK_MODELS": "none"}):
            self.assertEqual(gemini_client.fallback_model_names("x"), [])

    def test_pinned_default_keeps_a_backup(self):
        env = {k: v for k, v in os.environ.items() if k not in {"GEMINI_FALLBACK_MODELS", "GEMINI_MODEL"}}
        with mock.patch.dict(os.environ, env, clear=True):
            primary = gemini_client.default_model_name()
            self.assertEqual(primary, gemini_client.STABLE_DEFAULT_MODEL)
            backups = gemini_client.fallback_model_names(primary)
            self.assertEqual(len(backups), 1)
            self.assertNotEqual(backups[0], primary)
            self.assertEqual(gemini_client.fallback_model_names("gemini-3-flash-preview"), [primary])

    def test_fallback_that_only_repeats_primary_warns_once(self):
        gemini_client._warned_no_fallback.discard("gemini-2.5-flash")
        out = StringIO()
        with mock.patch.dict(os.environ, {"GEMINI_FALLBACK_MODELS": "gemini-2.5-flash"}), redirect_stdout(out):
            gemini_client._warn_no_fallback("gemini-2.5-flash")
            gemini_client._warn_no_fallback("gemini-2.5-flash")
        self.assertEqual(out.getvalue().count("沒有備援模型"), 1)
        out = StringIO()
        with mock.patch.dict(os.environ, {"GEMINI_FALLBACK_MODELS": "none"}), redirect_stdout(out):
            gemini_client._warn_no_fallback("gemini-x")
        self.assertEqual(out.getvalue(), "")


class GenerateTextFallbackTest(_GuardedTestCase):
    def test_overload_falls_back_and_logs_actual_model(self):
        client = _FakeClient(
            [
                RuntimeError("503 UNAVAILABLE. The model is overloaded."),
                _response(text="report", usage={"prompt_token_count": 10, "candidates_token_count": 5, "total_token_count": 15}),
            ]
        )
        with mock.patch.object(gemini_client, "get_client", lambda key: client):
            text = gemini_client.generate_text("k", "prompt", task_name="market_report")
        self.assertEqual(text, "report")
        self.assertEqual(client.models.calls, ["gemini-3-flash-preview", "gemini-2.5-flash"])
        rows = self.rows()
        self.assertEqual([r["model"] for r in rows], ["gemini-3-flash-preview", "gemini-2.5-flash"])
        self.assertEqual([r["status"] for r in rows], ["error", "success"])
        self.assertEqual(rows[1]["prompt_tokens"], 10)
        self.assertEqual(rows[1]["total_tokens"], 15)
        self.assertEqual(rows[1]["finish_reason"], "STOP")

    def test_truncated_answer_is_logged_incomplete_and_retried_on_fallback(self):
        client = _FakeClient([_response(text="cut", finish="MAX_TOKENS"), _response(text="whole")])
        with mock.patch.object(gemini_client, "get_client", lambda key: client):
            text = gemini_client.generate_text("k", "prompt", task_name="market_report")
        self.assertEqual(text, "whole")
        self.assertEqual([r["status"] for r in self.rows()], ["incomplete", "success"])

    def test_safety_block_is_not_retried_on_fallback(self):
        client = _FakeClient([_response(text=None, finish="SAFETY")])
        with mock.patch.object(gemini_client, "get_client", lambda key: client):
            with self.assertRaises(GeminiIncompleteResponse):
                gemini_client.generate_text("k", "prompt", task_name="market_report")
        self.assertEqual(len(client.models.calls), 1)

    def test_budget_refusal_is_not_retried(self):
        self.seed_rows(["success"] * 8)
        client = _FakeClient([])
        with mock.patch.object(gemini_client, "get_client", lambda key: client):
            with self.assertRaises(GeminiUsageLimitError) as ctx:
                gemini_client.generate_text("k", "prompt", task_name="market_report")
        self.assertEqual(client.models.calls, [])
        self.assertFalse(api_retry.is_retryable_exception(ctx.exception))
        self.assertEqual(self.rows()[-1]["status"], "blocked")


class UsageGuardTest(_GuardedTestCase):
    def test_errors_are_weighted_and_blocked_rows_free(self):
        self.seed_rows(["success", "error", "error", "blocked", "incomplete"])
        self.assertAlmostEqual(ai_usage_guard.get_today_gemini_usage(), 2.5)
        self.assertEqual(ai_usage_guard.get_today_gemini_call_count(), 4)
        counts = ai_usage_guard.get_today_status_counts()
        self.assertEqual(counts["blocked"], 1)
        self.assertEqual(counts["incomplete"], 1)

    def test_optional_tasks_leave_reserve_for_priority(self):
        self.seed_rows(["success"] * 5)
        allowed, message = ai_usage_guard.check_budget("stock_db_classification")
        self.assertFalse(allowed)
        self.assertIn("reserved", message)
        allowed, _ = ai_usage_guard.check_budget("youtube_package")
        self.assertTrue(allowed)
        self.seed_rows(["success"] * 3)
        allowed, _ = ai_usage_guard.check_budget("youtube_package")
        self.assertFalse(allowed)

    def test_bad_day_503s_do_not_starve_packaging(self):
        # Six 1-second 503s plus the successful report: packaging still has budget.
        self.seed_rows(["error"] * 6 + ["success", "success"])
        allowed, _ = ai_usage_guard.check_budget("youtube_package")
        self.assertTrue(allowed)

    def test_limit_zero_disables(self):
        self.seed_rows(["success"] * 20)
        with mock.patch.dict(os.environ, {"GEMINI_DAILY_CALL_LIMIT": "0"}):
            self.assertTrue(ai_usage_guard.check_budget("anything")[0])


class RetryPlanWiringTest(_GuardedTestCase):
    """Tenacity-wrapped callers: primary first with backoff, one capped fallback."""

    def _market(self):
        from modules import analyzer as analyzer_mod

        market = analyzer_mod.MarketAnalyzer.__new__(analyzer_mod.MarketAnalyzer)
        market.api_key = "k"
        market.model_name = "gemini-3-flash-preview"
        return market

    def test_persistent_max_tokens_costs_at_most_three_calls(self):
        client = _FakeClient([_response(text="cut", finish="MAX_TOKENS") for _ in range(12)])
        with mock.patch.object(gemini_client, "get_client", lambda key: client), mock.patch.object(
            api_retry, "get_api_retry_decorator", _no_wait_retry
        ), redirect_stdout(StringIO()):
            out = self._market()._call_gemini_with_retry("p")  # 4 attempts
        self.assertTrue(out.startswith("Error generating report"))
        self.assertIn("MAX_TOKENS", out)
        self.assertLessEqual(len(client.models.calls), 3)
        self.assertEqual(client.models.calls, ["gemini-3-flash-preview", "gemini-3-flash-preview", "gemini-2.5-flash"])
        self.assertLessEqual(ai_usage_guard.get_today_gemini_usage(), 3.0)
        # The rest of the day still has budget for extraction and packaging.
        self.assertTrue(ai_usage_guard.check_budget("structured_data_extraction")[0])
        self.assertTrue(ai_usage_guard.check_budget("youtube_package")[0])

    def test_transient_503_retries_primary_before_fallback(self):
        client = _FakeClient([RuntimeError("503 UNAVAILABLE. The model is overloaded."), _response(text="report")])
        with mock.patch.object(gemini_client, "get_client", lambda key: client), mock.patch.object(
            api_retry, "get_api_retry_decorator", _no_wait_retry
        ), redirect_stdout(StringIO()):
            out = self._market()._call_gemini_with_retry("p")
        self.assertEqual(out, "report")
        self.assertEqual(client.models.calls, ["gemini-3-flash-preview", "gemini-3-flash-preview"])

    def test_fallback_is_used_on_the_last_attempt(self):
        overloaded = [RuntimeError("503 UNAVAILABLE") for _ in range(4)]
        client = _FakeClient(overloaded + [_response(text="from fallback")])
        with mock.patch.object(gemini_client, "get_client", lambda key: client), mock.patch.object(
            api_retry, "get_api_retry_decorator", _no_wait_retry
        ), redirect_stdout(StringIO()):
            out = self._market()._call_gemini_with_retry("p", max_retries=3)
        self.assertEqual(out, "from fallback")
        self.assertEqual(client.models.calls, ["gemini-3-flash-preview"] * 4 + ["gemini-2.5-flash"])

    def test_retired_primary_goes_to_fallback_at_once(self):
        client = _FakeClient([RuntimeError("404 NOT_FOUND model retired"), _response(text="ok")])
        with mock.patch.object(gemini_client, "get_client", lambda key: client), mock.patch.object(
            api_retry, "get_api_retry_decorator", _no_wait_retry
        ), redirect_stdout(StringIO()):
            out = self._market()._call_gemini_with_retry("p")
        self.assertEqual(out, "ok")
        self.assertEqual(client.models.calls, ["gemini-3-flash-preview", "gemini-2.5-flash"])

    def test_youtube_package_max_tokens_is_capped_and_error_unwrapped(self):
        from modules import youtube_content_generator as ycg

        client = _FakeClient([_response(text="cut", finish="MAX_TOKENS") for _ in range(9)])
        with mock.patch.object(gemini_client, "get_client", lambda key: client), mock.patch.object(
            api_retry, "get_api_retry_decorator", _no_wait_retry
        ), redirect_stdout(StringIO()):
            with self.assertRaises(GeminiIncompleteResponse):
                ycg._generate_package_text("k", "p", "gemini-3-flash-preview")
        self.assertLessEqual(len(client.models.calls), 3)

    def test_youtube_package_exhausted_retries_raise_the_real_error(self):
        from tenacity import RetryError

        from modules import youtube_content_generator as ycg

        client = _FakeClient([RuntimeError("503 UNAVAILABLE") for _ in range(6)])
        with mock.patch.object(gemini_client, "get_client", lambda key: client), mock.patch.object(
            api_retry, "get_api_retry_decorator", _no_wait_retry
        ), mock.patch.dict(os.environ, {"YOUTUBE_PACKAGE_MAX_ATTEMPTS": "2"}), redirect_stdout(StringIO()):
            with self.assertRaises(RuntimeError) as ctx:
                ycg._generate_package_text("k", "p", "gemini-3-flash-preview")
        self.assertNotIsInstance(ctx.exception, RetryError)
        self.assertIn("503", str(ctx.exception))
        self.assertEqual(client.models.calls, ["gemini-3-flash-preview", "gemini-3-flash-preview", "gemini-2.5-flash"])


class StockDbClassificationRetryTest(unittest.TestCase):
    def _run(self, outcomes):
        from modules import stock_db_updater

        calls, sleeps = [], []

        def fake_generate(*args, **kwargs):
            calls.append(kwargs.get("task_name"))
            item = outcomes.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

        updater = stock_db_updater.StockDatabaseUpdater.__new__(stock_db_updater.StockDatabaseUpdater)
        updater.api_key = "k"
        updater.model_name = "m"
        with mock.patch.object(stock_db_updater, "generate_text", fake_generate), mock.patch.object(
            stock_db_updater.time, "sleep", sleeps.append
        ), redirect_stdout(StringIO()):
            result = updater._query_gemini_for_stocks([{"id": "2603", "name": "長榮"}])
        return result, calls, sleeps

    def test_budget_refusal_returns_at_once_without_sleeping(self):
        result, calls, sleeps = self._run([GeminiUsageLimitError("reserved for priority tasks")])
        self.assertIsNone(result)
        self.assertEqual(len(calls), 1)
        self.assertEqual(sleeps, [])

    def test_bad_json_is_retried_without_sleep(self):
        result, calls, sleeps = self._run(["not json", '{"2603": {"name": "長榮"}}'])
        self.assertEqual(result, {"2603": {"name": "長榮"}})
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [])

    def test_transient_error_backs_off_but_not_after_last_attempt(self):
        result, calls, sleeps = self._run([RuntimeError("503 UNAVAILABLE")] * 3)
        self.assertIsNone(result)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [5, 10])


class RetryWiringTest(unittest.TestCase):
    def test_retryable_flag_wins(self):
        self.assertTrue(api_retry.is_retryable_exception(GeminiIncompleteResponse("x", retryable=True)))
        self.assertFalse(api_retry.is_retryable_exception(GeminiIncompleteResponse("503 x", retryable=False)))
        self.assertTrue(api_retry.is_retryable_exception(RuntimeError("503 UNAVAILABLE")))

    def test_analyzer_retries_incomplete_report_then_returns_error(self):
        from modules import analyzer as analyzer_mod

        calls = []

        def fake_generate(api_key, prompt, **kwargs):
            calls.append(prompt)
            return "## 〔今日主軸〕\n只有主軸"

        with mock.patch.object(api_retry, "get_api_retry_decorator", _no_wait_retry), mock.patch.object(
            analyzer_mod, "generate_text", fake_generate
        ):
            market = analyzer_mod.MarketAnalyzer.__new__(analyzer_mod.MarketAnalyzer)
            market.api_key = "k"
            market.model_name = "m"
            out = market._call_gemini_with_retry(
                "p",
                max_retries=2,
                validate=lambda text: market._daily_report_problems(text, next_observe="明日觀察"),
            )
        self.assertEqual(len(calls), 3)
        self.assertTrue(out.startswith("Error generating report"))
        self.assertIn("〔明日觀察焦點〕", out)

    def test_analyzer_accepts_complete_report(self):
        from modules import analyzer as analyzer_mod

        report = "## 〔今日主軸〕a\n## 〔法人籌碼〕b\n## 〔下週一觀察焦點〕c"
        with mock.patch.object(api_retry, "get_api_retry_decorator", _no_wait_retry), mock.patch.object(
            analyzer_mod, "generate_text", lambda *a, **k: report
        ):
            market = analyzer_mod.MarketAnalyzer.__new__(analyzer_mod.MarketAnalyzer)
            market.api_key = "k"
            market.model_name = "m"
            out = market._call_gemini_with_retry("p", validate=market._daily_report_problems)
        self.assertEqual(out, report)

    def test_section_check_warn_mode(self):
        from modules.analyzer import MarketAnalyzer

        with mock.patch.dict(os.environ, {"REPORT_SECTION_CHECK": "warn"}):
            self.assertEqual(MarketAnalyzer._daily_report_problems("no sections"), [])
        with mock.patch.dict(os.environ, {"REPORT_SECTION_CHECK": "strict"}):
            self.assertTrue(MarketAnalyzer._daily_report_problems("no sections"))

    def test_youtube_package_call_retries_transient_errors(self):
        from modules import youtube_content_generator as ycg

        outcomes = [RuntimeError("503 UNAVAILABLE"), "###TITLE_A\nA\n###END"]

        def fake_generate(*args, **kwargs):
            item = outcomes.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

        with mock.patch.object(api_retry, "get_api_retry_decorator", _no_wait_retry), mock.patch.object(
            ycg, "generate_text", fake_generate
        ):
            self.assertEqual(ycg._generate_package_text("k", "p", "m"), "###TITLE_A\nA\n###END")
        self.assertEqual(outcomes, [])


if __name__ == "__main__":
    unittest.main()
