#!/usr/bin/env python3
"""
Resume /1 from an existing daily_report.md when structured_data extraction failed.

Uses the same deterministic inputs and post-processing as /1:
- local facts / 驗收 results / editorial frame saved by /1 in
  daily/<date>/resume_inputs.json (modules/resume_inputs.py), plus (schema 2)
  the raw market / institutional / volume payloads, similar sessions and the
  report fact check;
- modules.pipeline_persist.finalize_structured_data (local 驗收 overwrite or
  explicit clearing, role scores, broadcast ledger, sanitize, similar sessions)
  fed with those payloads, so turnover rank / vol_ratio / local hero-card
  numbers match what /1 would have produced.

Without resume_inputs.json (runs older than this change) no 驗收 is claimed:
validation_results stay empty instead of being invented. A schema 1 file has
no payloads: role inputs and hero cards then fall back to local lists only.
"""

import argparse
import datetime
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv

load_dotenv(os.path.join(BASE_DIR, ".env"))

from modules.atomic_io import atomic_write
from modules.current_index import write_current_index
from modules.data_extractor import extract_structured_data
from modules.editorial_frame import build_editorial_frame
from modules.event_radar import carry_forward_radar
from modules.knowledge_manager import KnowledgeUpdater
from modules.local_knowledge_compounder import LocalKnowledgeCompounder
from modules.notebooklm_generator import generate_notebooklm_prompt
from modules.notebooklm_source_builder import save_notebooklm_source
from modules.analytics_store import AnalyticsStore
from modules.pipeline_context import build_pipeline_context
from modules.pipeline_persist import finalize_structured_data
from modules.pipeline_support import find_previous_structured_data
from modules.report_paths import daily_artifact_write_path, resolve_daily_artifact
from modules.similar_sessions import parse_index_pct
from modules.resume_inputs import load_resume_inputs, resume_inputs_path, saved_payloads


def resolve_resume_inputs(reports_dir, date_str):
    """
    Return (local_facts, validation_results, validation_meta, editorial_frame,
    supply_chain_event_radar, source) for the resume run.
    """
    saved = load_resume_inputs(reports_dir, date_str)
    if saved:
        return (
            saved.get("local_facts") or {},
            saved.get("validation_results") or [],
            saved.get("validation_meta") or {},
            saved.get("editorial_frame"),
            saved.get("supply_chain_event_radar") or [],
            "saved",
        )

    _, prev_structured, validation_meta = find_previous_structured_data(reports_dir, as_of_date=date_str)
    # No saved local 驗收: never fabricate one. Mark it as skipped so packaging
    # does not claim「昨日驗收」.
    validation_meta = dict(validation_meta or {})
    if validation_meta.get("onair_validation_eligible"):
        validation_meta["onair_validation_eligible"] = False
        validation_meta["skip_reason"] = "補跑缺少 /1 本機驗收結果（resume_inputs.json），不寫驗收"
    # The previous session's radar is yesterday's news: keep it as background
    # (re-tagged repeat/stale) but never as today's event evidence. The frame
    # is built after extraction (it needs today's index move), with no radar.
    radar = carry_forward_radar(
        (prev_structured or {}).get("supply_chain_event_radar") or [],
        from_date=validation_meta.get("source_date"),
    )
    return {}, [], validation_meta, None, radar, "rebuilt"


def build_resumed_structured_data(gemini_api_key, report_content, reports_dir, date_str):
    """Extract + finalize structured_data exactly like /1. Returns (data, error_message)."""
    local_facts, validation_results, validation_meta, editorial_frame, radar, source = resolve_resume_inputs(
        reports_dir, date_str
    )
    payloads = saved_payloads(load_resume_inputs(reports_dir, date_str)) if source == "saved" else {}
    if source == "saved":
        print(f"  ✅ 讀取 /1 本機輸入: {os.path.basename(resume_inputs_path(reports_dir, date_str))}")
        if not payloads.get("market_data") and not payloads.get("volume_data"):
            print("  ⚠️  resume_inputs.json 沒有行情 payload（舊版 schema）：量榜名次／量增比／主角卡本機數字會缺")
    else:
        print("  ⚠️  找不到 resume_inputs.json：法人榜/大盤數字改由日報萃取，且不寫入驗收結果")

    structured_data = extract_structured_data(gemini_api_key, report_content, local_facts=local_facts)
    if isinstance(structured_data, dict) and structured_data.get("error"):
        return None, str(structured_data.get("raw_error", ""))[:300]

    if editorial_frame is None:
        editorial_frame = build_editorial_frame(
            run_date=date_str,
            signal_pack={"strongest_anomaly": None, "watch_candidates": []},
            validation_results=validation_results,
            event_context="",
            channel_story="",
            validation_meta=validation_meta,
            event_radar=[],
            index_pct=parse_index_pct(structured_data.get("index_action")),
        )
    # finalize_structured_data enriches timeframes after validation / radar /
    # focus_ranking are attached (as_of = date_str), like /1.
    structured_data = finalize_structured_data(
        structured_data,
        date_str=date_str,
        reports_dir=reports_dir,
        validation_results=validation_results,
        validation_meta=validation_meta,
        editorial_frame=editorial_frame,
        supply_chain_event_radar=radar,
        similar_sessions=payloads.get("similar_sessions"),
        market_data=payloads.get("market_data"),
        institutional_data=payloads.get("institutional_data"),
        volume_data=payloads.get("volume_data"),
        fact_check=payloads.get("fact_check"),
        value_ranking=payloads.get("value_ranking"),
    )
    return structured_data, None


def main():
    parser = argparse.ArgumentParser(description="Resume daily persist from existing daily_report.md")
    parser.add_argument("--date", default=datetime.date.today().isoformat(), help="YYYY-MM-DD")
    parser.add_argument(
        "--model",
        default=os.getenv("GEMINI_RESUME_MODEL", "gemini-2.5-flash"),
        help="Gemini model for structured_data extraction",
    )
    args = parser.parse_args()

    os.environ["GEMINI_MODEL"] = args.model
    ctx = build_pipeline_context()
    date_str = args.date
    report_path = resolve_daily_artifact(ctx.reports_dir, date_str, "report")
    if not report_path or not os.path.exists(report_path):
        print(f"❌ 找不到日報: {report_path}")
        return 1

    with open(report_path, "r", encoding="utf-8") as f:
        report_content = f.read()

    print(f"{'=' * 50}")
    print(f"  /1 Resume — {date_str}")
    print(f"  Model: {args.model}")
    print(f"{'=' * 50}\n")

    print("📦 萃取 structured_data...")
    structured_data, error = build_resumed_structured_data(
        ctx.gemini_api_key, report_content, ctx.reports_dir, date_str
    )
    if structured_data is None:
        print(f"❌ 萃取失敗: {error}")
        return 1
    print("  ✅ structured_data 萃取成功")

    json_path = daily_artifact_write_path(ctx.reports_dir, date_str, "structured_data")
    with atomic_write(json_path) as f:
        json.dump(structured_data, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 已寫入: {os.path.basename(json_path)}")

    try:
        src = save_notebooklm_source(structured_data, ctx.reports_dir, date_str=date_str)
        print(f"  ✅ NotebookLM source: {os.path.basename(src)}")
    except Exception as exc:
        print(f"  ⚠️  NotebookLM source 失敗: {exc}")

    try:
        # resume is a rerun path: the rebuilt structured_data replaces that date's memory entry
        compound = LocalKnowledgeCompounder().update_from_structured_data(
            structured_data=structured_data,
            report_content=report_content,
            report_date=date_str,
            replace_existing=True,
        )
        print(
            f"  ✅ 本機知識更新: 記憶 {'新增' if compound['memory_appended'] else '已存在'}；"
            f"角色 {compound['roles_updated']} 檔"
        )
    except Exception as exc:
        print(f"  ⚠️  本機知識更新失敗: {exc}")

    try:
        AnalyticsStore().record_daily_run(
            run_date=date_str,
            structured_data=structured_data,
            report_path=report_path,
            structured_data_path=json_path,
        )
        print("  ✅ Analytics DB 已更新")
    except Exception as exc:
        print(f"  ⚠️  Analytics DB 失敗: {exc}")

    print("\n🎧 生成 NotebookLM prompt（本機模板）...")
    prompt = generate_notebooklm_prompt(
        ctx.gemini_api_key,
        structured_data,
        date_str,
        current_theme=ctx.project_theme,
        skill_context="",
    )
    nl_path = daily_artifact_write_path(ctx.reports_dir, date_str, "notebooklm_prompt")
    if prompt is None:
        print("  ⏭  NotebookLM prompt 已關閉（NOTEBOOKLM_PROMPT_MODE=off）")
    elif isinstance(prompt, str) and not prompt.strip().startswith("Error generating"):
        with open(nl_path, "w", encoding="utf-8") as f:
            f.write(prompt)
        print(f"  ✅ NotebookLM prompt: {os.path.basename(nl_path)}")
    else:
        print("  ⚠️  NotebookLM prompt 生成失敗，略過")

    print("\n🧠 更新 Knowledge Wiki（預設本機模式）...")
    try:
        KnowledgeUpdater(ctx.gemini_api_key).update_knowledge_base(
            report_content,
            structured_data=structured_data,
            report_date=date_str,
            replace_existing=True,
        )
        print("  ✅ Knowledge Wiki 已更新")
    except Exception as exc:
        print(f"  ⚠️  Knowledge Wiki 失敗: {exc}")

    try:
        current_path = write_current_index(
            ctx.reports_dir,
            date_str,
            daily_ready=True,
            youtube_ready=False,
        )
        print(f"  ✅ CURRENT 索引: {os.path.basename(current_path)}")
    except Exception as exc:
        print(f"  ⚠️  CURRENT 索引失敗: {exc}")

    print(f"\n✅ /1 Resume 完成 — {date_str}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
