#!/usr/bin/env python3
"""Ops hardening: exit codes, atomic writes, flag visibility, run_daily.sh, previous-run fallback."""
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules import exit_codes  # noqa: E402
from modules.atomic_io import atomic_write, write_json_atomic  # noqa: E402
from modules.pipeline_support import find_previous_structured_data  # noqa: E402
from modules.runtime_flags import collect_flag_notices  # noqa: E402


class ExitCodesTest(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(exit_codes.normalize(None), exit_codes.EXIT_OK)
        self.assertEqual(exit_codes.normalize(exit_codes.EXIT_QUALITY_GATE), 3)
        self.assertEqual(exit_codes.normalize("x"), exit_codes.EXIT_UNHANDLED)

    def test_codes_are_distinct_and_described(self):
        codes = [
            exit_codes.EXIT_DATA_NOT_READY,
            exit_codes.EXIT_QUALITY_GATE,
            exit_codes.EXIT_GEMINI_FAILED,
            exit_codes.EXIT_PERSIST_FAILED,
            exit_codes.EXIT_CONFIG,
            exit_codes.EXIT_TOO_EARLY,
            exit_codes.EXIT_NOT_SESSION,  # item 6 session guard
        ]
        self.assertEqual(len(set(codes)), len(codes))
        self.assertNotIn(0, codes)
        for code in codes:
            self.assertNotIn("未知", exit_codes.describe(code))

    def test_workflow_and_docs_list_every_exit_code(self):
        """The /1 agent workflow and env_flags.md must tell the agent what each code means."""
        with open(os.path.join(BASE_DIR, ".agents", "workflows", "1.md"), encoding="utf-8") as f:
            workflow = f.read()
        with open(os.path.join(BASE_DIR, "docs", "env_flags.md"), encoding="utf-8") as f:
            docs = f.read()
        for code in sorted(exit_codes.DESCRIPTIONS):
            self.assertIn(f"`{code}`", workflow, code)
            if code != exit_codes.EXIT_UNHANDLED:
                self.assertIn(f"`{code}`", docs, code)

    def test_main_returns_codes_on_every_controlled_stop(self):
        with open(os.path.join(BASE_DIR, "main.py"), encoding="utf-8") as f:
            source = f.read()
        body = source.split("def main():", 1)[1].split('if __name__ == "__main__":', 1)[0]
        bare = [line for line in body.splitlines() if line.strip() == "return"]
        self.assertEqual(bare, [], "main() must not stop with a bare return (exit code 0)")
        self.assertIn("sys.exit(normalize_exit_code(main()))", source)


class AtomicWriteTest(unittest.TestCase):
    def test_failure_keeps_previous_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "structured_data.json")
            write_json_atomic(path, {"date": "2026-10-12", "ok": True})
            with self.assertRaises(TypeError):
                with atomic_write(path) as f:
                    json.dump({"bad": object()}, f)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"date": "2026-10-12", "ok": True})
            self.assertEqual(os.listdir(tmp), ["structured_data.json"])

    def test_success_replaces_and_keeps_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.json")
            write_json_atomic(path, {"v": 1})
            os.chmod(path, 0o644)
            write_json_atomic(path, {"v": 2})
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["v"], 2)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o644)


class RuntimeFlagsTest(unittest.TestCase):
    def test_defaults_are_quiet(self):
        self.assertEqual(collect_flag_notices({}), [])
        self.assertEqual(collect_flag_notices({"DATA_QUALITY_GATE": "strict", "NEWS_MODE": "smart"}), [])

    def test_report_safety_checks_disabled_are_warnings(self):
        """Item 8 checks: relaxing them is visible at start-up; the documented defaults stay quiet."""
        quiet = {"REPORT_FACT_CHECK": "annotate", "REPORT_LOCAL_TABLES": "1", "REPORT_SECTION_CHECK": "strict"}
        self.assertEqual(collect_flag_notices(quiet), [])
        for env, needle in (
            ({"REPORT_FACT_CHECK": "off"}, "事實核對已關閉"),
            ({"REPORT_FACT_CHECK": "warn"}, "只記錄"),
            ({"REPORT_FACT_CHECK": "anotate"}, "不是有效值"),
            ({"REPORT_LOCAL_TABLES": "0"}, "REPORT_LOCAL_TABLES=0"),
            ({"REPORT_SECTION_CHECK": "off"}, "缺少核心章節"),
            ({"GEMINI_FALLBACK_MODELS": "none"}, "備援模型已停用"),
            ({"GEMINI_RESERVED_CALLS": "0"}, "GEMINI_RESERVED_CALLS=0"),
        ):
            notices = collect_flag_notices(env)
            self.assertTrue(any(level == "warn" and needle in msg for level, msg in notices), (env, notices))
        info = " ".join(msg for _, msg in collect_flag_notices({"GEMINI_ERROR_CALL_WEIGHT": "1"}))
        self.assertIn("GEMINI_ERROR_CALL_WEIGHT=1", info)

    def test_every_pipeline_env_flag_is_documented(self):
        import glob
        import re

        names = set()
        for path in glob.glob(os.path.join(BASE_DIR, "modules", "*.py")) + [os.path.join(BASE_DIR, "main.py")]:
            with open(path, encoding="utf-8") as f:
                names |= set(re.findall(r'(?:getenv|environ\.get|_env_int|_env_float)\(\s*"([A-Z][A-Z0-9_]+)"', f.read()))
        with open(os.path.join(BASE_DIR, "docs", "env_flags.md"), encoding="utf-8") as f:
            docs = f.read()
        with open(os.path.join(BASE_DIR, ".env.example"), encoding="utf-8") as f:
            example = f.read()
        for name in ("REPORT_FACT_CHECK", "REPORT_LOCAL_TABLES", "REPORT_SECTION_CHECK", "GEMINI_FALLBACK_MODELS",
                     "GEMINI_ERROR_CALL_WEIGHT", "GEMINI_RESERVED_CALLS"):
            self.assertIn(name, names)
            self.assertIn(name, example, name)
        for name in sorted(names):
            self.assertIn(f"`{name}`", docs, name)

    def test_gate_off_is_a_warning_including_legacy_alias(self):
        for env in ({"DATA_QUALITY_GATE": "off"}, {"DATA_QUALITY_GATE_MODE": "0"}):
            notices = collect_flag_notices(env)
            self.assertTrue(any(level == "warn" and "閘門已關閉" in msg for level, msg in notices), env)

    def test_preferred_name_wins_like_the_readers(self):
        notices = collect_flag_notices({"DATA_QUALITY_GATE": "strict", "DATA_QUALITY_GATE_MODE": "off"})
        self.assertFalse(any("閘門已關閉" in msg for _, msg in notices))

    def test_early_run_and_force_fail_warn(self):
        notices = collect_flag_notices({"FORCE_RUN_BEFORE_1610": "1", "ALLOW_DATA_QUALITY_FAIL": "yes"})
        messages = " ".join(msg for _, msg in notices)
        self.assertIn("提早執行", messages)
        self.assertIn("ALLOW_DATA_QUALITY_FAIL", messages)
        self.assertIn("FORCE_RUN_BEFORE_1610 是舊名稱", messages)


    def test_banner_matches_what_the_readers_accept(self):
        from modules import data_quality_gate, finmind_client, pipeline_context

        for value in ("on", "1", "y"):
            env = {"ALLOW_DATA_QUALITY_FAIL": value, "ALLOW_EARLY_RUN": value, "FINMIND_FALLBACK": value}
            with mock.patch.dict(os.environ, env, clear=False):
                reader_fail = data_quality_gate._force_continue()
                reader_early = pipeline_context.build_pipeline_context().allow_early_run
                reader_finmind = finmind_client.finmind_fallback_enabled()
            messages = " ".join(msg for level, msg in collect_flag_notices(env) if level == "warn")
            info = " ".join(msg for _, msg in collect_flag_notices(env))
            self.assertEqual("ALLOW_DATA_QUALITY_FAIL=1" in messages, reader_fail, value)
            self.assertEqual("提早執行已啟用" in messages, reader_early, value)
            self.assertEqual("FinMind 備援已啟用" in info, reader_finmind, value)
        ignored = " ".join(msg for _, msg in collect_flag_notices({"ALLOW_EARLY_RUN": "on"}))
        self.assertIn("不會生效", ignored)

    def test_unknown_gate_value_is_reported_and_still_blocks(self):
        from modules.data_quality_gate import DataQualityReport, GateIssue, apply_data_quality_gate

        notices = collect_flag_notices({"DATA_QUALITY_GATE": "stric"})
        self.assertTrue(any(level == "warn" and "不是有效值" in msg for level, msg in notices))
        report = DataQualityReport(run_date="2026-10-13", issues=[GateIssue(level="fail", code="x", message="bad")])
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.dict(os.environ, {"ALLOW_DATA_QUALITY_FAIL": ""}):
            self.assertFalse(apply_data_quality_gate(report, mode="stric"))


class PreviousStructuredFallbackTest(unittest.TestCase):
    def test_corrupt_latest_falls_back_to_previous_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = os.path.join(tmp, "daily", "2026-10-12", "structured_data.json")
            bad = os.path.join(tmp, "daily", "2026-10-13", "structured_data.json")
            os.makedirs(os.path.dirname(good))
            os.makedirs(os.path.dirname(bad))
            with open(good, "w", encoding="utf-8") as f:
                json.dump({"date": "2026-10-12", "prediction_targets": [{"id": "2330"}]}, f)
            with open(bad, "w", encoding="utf-8") as f:
                f.write('{"date": "2026-10-13", "prediction_ta')  # truncated write
            with contextlib.redirect_stdout(io.StringIO()):
                targets, data, meta = find_previous_structured_data(tmp, as_of_date="2026-10-14")
            self.assertEqual(data["date"], "2026-10-12")
            self.assertEqual(meta["unreadable_paths"], [bad])
            self.assertEqual(meta["trading_days_gap"], 2)
            self.assertFalse(meta["onair_validation_eligible"])

    def test_all_unreadable_reports_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "daily", "2026-10-12", "structured_data.json")
            os.makedirs(os.path.dirname(bad))
            with open(bad, "w", encoding="utf-8") as f:
                f.write("")
            with contextlib.redirect_stdout(io.StringIO()):
                targets, data, meta = find_previous_structured_data(tmp, as_of_date="2026-10-13")
            self.assertEqual(targets, [])
            self.assertIsNone(data)
            self.assertIn("讀取失敗", meta["skip_reason"])


@unittest.skipUnless(shutil.which("bash"), "bash required")
class RunDailyScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        shutil.copy(os.path.join(BASE_DIR, "run_daily.sh"), os.path.join(self.root, "run_daily.sh"))
        self.fake_python = os.path.join(self.root, "fake_python")
        with open(self.fake_python, "w", encoding="utf-8") as f:
            f.write(
                "#!/bin/bash\n"
                'if [ "$1" = "-c" ]; then\n'
                '  case "$2" in *describe*) echo "fake-meaning";; esac\n'
                "  exit 0\n"
                "fi\n"
                'echo "fake main $*"\n'
                'exit "${FAKE_MAIN_RC:-0}"\n'
            )
        os.chmod(self.fake_python, 0o755)
        self.lock_tmp = os.path.join(self.root, "locks")
        os.makedirs(self.lock_tmp)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, rc=0):
        env = dict(os.environ)
        env.update(
            {
                "PYTHON_BIN": self.fake_python,
                "FAKE_MAIN_RC": str(rc),
                "RUN_DAILY_LOCK_DIR": os.path.join(self.lock_tmp, "stock-youtube-run-daily.lock"),
                "TMPDIR": os.path.join(self.root, "other-tmpdir"),  # must not influence the lock
                "RUN_DAILY_NOTIFY": "0",
            }
        )
        return subprocess.run(
            ["bash", os.path.join(self.root, "run_daily.sh")],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

    def _log_text(self):
        log_dir = os.path.join(self.root, "reports", "archive", "misc")
        text = ""
        for name in os.listdir(log_dir):
            with open(os.path.join(log_dir, name), encoding="utf-8") as f:
                text += f.read()
        return text

    def test_propagates_main_exit_code_and_logs_meaning(self):
        result = self._run(rc=2)
        self.assertEqual(result.returncode, 2)
        log = self._log_text()
        self.assertIn("退出碼 2", log)
        self.assertIn("fake-meaning", log)
        self.assertIn("fake main main.py", log)
        self.assertFalse(os.path.exists(os.path.join(self.lock_tmp, "stock-youtube-run-daily.lock")))

    def test_live_lock_blocks_second_run(self):
        lock = os.path.join(self.lock_tmp, "stock-youtube-run-daily.lock")
        os.makedirs(lock)
        with open(os.path.join(lock, "pid"), "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        result = self._run(rc=0)
        self.assertEqual(result.returncode, 75)
        self.assertTrue(os.path.isdir(lock))

    def test_lock_path_does_not_depend_on_tmpdir(self):
        with open(os.path.join(BASE_DIR, "run_daily.sh"), encoding="utf-8") as f:
            source = f.read()
        lock_line = [line for line in source.splitlines() if line.startswith("LOCK_DIR=")][0]
        self.assertNotIn("TMPDIR", lock_line)
        self.assertIn("RUN_DAILY_LOCK_DIR", lock_line)

    def test_stale_lock_is_recovered(self):
        lock = os.path.join(self.lock_tmp, "stock-youtube-run-daily.lock")
        os.makedirs(lock)
        with open(os.path.join(lock, "pid"), "w", encoding="utf-8") as f:
            f.write("999999")
        result = self._run(rc=0)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
