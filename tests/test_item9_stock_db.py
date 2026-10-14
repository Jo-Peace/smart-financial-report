#!/usr/bin/env python3
"""Item 9: stock_db atomic writes, load-failure guard, Gemini drafts never overwrite."""
import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.atomic_io import atomic_write_json, atomic_write_text, backup_file_daily  # noqa: E402
from modules.stock_db_updater import (  # noqa: E402
    StockDatabaseUpdater,
    build_draft_entries,
    format_stock_db_for_prompt,
)


class AtomicIoTest(unittest.TestCase):
    def test_atomic_write_replaces_and_keeps_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{}")
            os.chmod(path, 0o644)
            atomic_write_json(path, {"x": 1})
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"x": 1})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)
            self.assertEqual([n for n in os.listdir(tmp) if n.endswith(".tmp")], [])

    def test_unserializable_payload_never_truncates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.json")
            atomic_write_text(path, '{"keep": true}')
            with self.assertRaises(TypeError):
                atomic_write_json(path, {"bad": object()})
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"keep": True})

    def test_backup_once_per_day_and_prunes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "stock_db.json")
            atomic_write_text(path, "{}")
            backup_dir = os.path.join(tmp, "backup")
            for day in range(1, 6):
                target = backup_file_daily(path, backup_dir=backup_dir, keep=3, today=datetime.date(2026, 10, day))
                self.assertTrue(target and os.path.exists(target))
            self.assertIsNone(backup_file_daily(path, backup_dir=backup_dir, keep=3, today=datetime.date(2026, 10, 5)))
            self.assertEqual(len(os.listdir(backup_dir)), 3)


class DraftEntriesTest(unittest.TestCase):
    def test_only_requested_new_ids_are_accepted_as_unreviewed(self):
        existing = {"3231": {"name": "緯創", "sector": "電腦", "tracking": {"prediction_count": 6}}}
        gemini = {
            "00961": {"name": "FT臺灣永續高息", "sector": "ETF", "supply_chain_role": "高股息 ETF"},
            "3231.TW": {"name": "緯創", "sector": "AI 伺服器", "supply_chain_role": "x"},
            "2317": {"name": "鴻海", "sector": "AI", "supply_chain_role": "y"},
            "2340": {"name": "台亞", "sector": "", "supply_chain_role": "z"},
        }
        requested = [{"id": "00961", "name": "FT臺灣永續高息"}, {"id": "3231", "name": "緯創"}, {"id": "2340", "name": "台亞"}]
        accepted, rejected = build_draft_entries(gemini, requested, existing, today="2026-10-13")
        self.assertEqual(set(accepted), {"00961"})
        self.assertFalse(accepted["00961"]["reviewed"])
        self.assertEqual(accepted["00961"]["source"], "gemini_draft")
        self.assertEqual(accepted["00961"]["not_classify_as"], [])
        reasons = dict(rejected)
        self.assertEqual(reasons["3231"], "既有條目不覆寫")
        self.assertEqual(reasons["2317"], "非本次查詢的股票")
        self.assertEqual(reasons["2340"], "欄位不完整")

    def test_ai_connection_requires_evidence(self):
        requested = [{"id": "1301", "name": "台塑"}, {"id": "2330", "name": "台積電"}]
        gemini = {
            "1301": {"name": "台塑", "sector": "塑膠工業", "supply_chain_role": "石化", "ai_connection": "可能用於 AI 伺服器"},
            "2330": {"name": "台積電", "sector": "半導體業", "supply_chain_role": "晶圓代工",
                     "ai_connection": "AI 加速器代工", "ai_evidence": "法說會 HPC 營收比重"},
        }
        accepted, _ = build_draft_entries(gemini, requested, {})
        self.assertIsNone(accepted["1301"]["ai_connection"])
        self.assertEqual(accepted["2330"]["ai_connection"], "AI 加速器代工")

    def test_prompt_rendering_never_emits_forbidden_rules_for_unreviewed(self):
        db = {
            "2603": {"name": "長榮", "sector": "全球 AI 基礎建設海運供應鏈", "supply_chain_role": "AI 伺服器運力",
                     "not_classify_as": ["傳統航運"]},
            "2330": {"name": "台積電", "sector": "半導體", "supply_chain_role": "晶圓代工",
                     "not_classify_as": ["傳產"], "reviewed": True},
        }
        text = format_stock_db_for_prompt(db, {"2603", "2330", "9999"})
        self.assertIn("未審核", text)
        self.assertNotIn("傳統航運", text)
        self.assertNotIn("絕對禁止", text)
        self.assertIn("避免稱為：傳產", text)
        self.assertEqual(format_stock_db_for_prompt({}, {"2330"}), "（目前資料庫為空，請依常理判斷）")


class StockDbUpdaterGuardTest(unittest.TestCase):
    def test_corrupt_db_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "stock_db.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"2330": {"name": "台積電"')  # truncated JSON
            updater = StockDatabaseUpdater("dummy", db_path=path)
            self.assertTrue(updater.load_error)
            with mock.patch.object(updater, "_query_gemini_for_stocks") as query:
                updater.update_new_stocks([{"id": "2330", "name": "台積電"}])
                query.assert_not_called()
            self.assertFalse(updater._save_stock_db())
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), '{"2330": {"name": "台積電"')

    def test_update_new_stocks_merges_drafts_and_backs_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "stock_db.json")
            atomic_write_json(path, {"2330": {"name": "台積電", "tracking": {"prediction_count": 9}}})
            updater = StockDatabaseUpdater("dummy", db_path=path)
            reply = {
                "2330": {"name": "台積電", "sector": "AI", "supply_chain_role": "overwrite"},
                "2340": {"name": "台亞", "sector": "光電業", "supply_chain_role": "LED 與功率元件"},
            }
            with mock.patch.object(updater, "_query_gemini_for_stocks", return_value=reply):
                updater.update_new_stocks([{"id": "2330", "name": "台積電"}, {"id": "2340", "name": "台亞"}])
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["2330"]["tracking"]["prediction_count"], 9)
            self.assertNotIn("sector", saved["2330"])
            self.assertFalse(saved["2340"]["reviewed"])
            self.assertTrue(os.listdir(os.path.join(tmp, "backup")))


if __name__ == "__main__":
    unittest.main()
