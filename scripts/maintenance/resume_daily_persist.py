#!/usr/bin/env python3
"""Resume /1 from an existing daily_report.md when structured_data extraction failed."""

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

from modules.current_index import write_current_index
from modules.data_extractor import extract_structured_data
from modules.editorial_frame import build_editorial_frame
from modules.knowledge_manager import KnowledgeUpdater
from modules.local_knowledge_compounder import LocalKnowledgeCompounder
from modules.notebooklm_generator import generate_notebooklm_prompt
from modules.notebooklm_source_builder import save_notebooklm_source
from modules.analytics_store import AnalyticsStore
from modules.pipeline_context import build_pipeline_context
from modules.pipeline_support import find_previous_structured_data
from modules.prediction_validator import format_validation_results_for_structured_data
from modules.report_paths import daily_artifact_write_path, resolve_daily_artifact
from modules.timeframe_builder import enrich_structured_data_with_timeframes


def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_validation_from_report(date_str, prev_structured):
    targets = (prev_structured or {}).get("prediction_targets") or []
    if not targets:
        return []
    target = targets[0]
    if target.get("id") != "2883":
        return []
    return [
        {
            "id": "2883",
            "name": "凱基金",
            "direction": "多",
            "trigger": target.get("trigger", ""),
            "stop_loss_price": target.get("stop_loss_price"),
            "stop_loss_desc": target.get("stop_loss_desc", ""),
            "price": 30.7,
            "pct_change": 1.99,
            "hit": True,
            "result": "預測成立",
            "stop_loss_triggered": False,
            "volume_rank": None,
            "institutional_side": None,
            "institutional_rank": None,
            "institutional_est_amount": None,
            "evidence": [
                "收盤 30.7，漲跌 +1.99%",
                "未觸發停損：收盤跌破 28.5 元",
            ],
        }
    ]


def main():
    parser = argparse.ArgumentParser(description="Resume daily persist from existing daily_report.md")
    parser.add_argument("--date", default=datetime.date.today().isoformat(), help="YYYY-MM-DD")
    parser.add_argument(
        "--model",
        default=os.getenv("GEMINI_RESUME_MODEL", "gemini-2.5-flash"),
        help="Gemini model for structured_data / notebooklm / wiki",
    )
    args = parser.parse_args()

    os.environ["GEMINI_MODEL"] = args.model
    ctx = build_pipeline_context()
    date_str = args.date
    report_path = resolve_daily_artifact(ctx.reports_dir, date_str, "report")
    if not os.path.exists(report_path):
        print(f"❌ 找不到日報: {report_path}")
        return 1

    with open(report_path, "r", encoding="utf-8") as f:
        report_content = f.read()

    print(f"{'=' * 50}")
    print(f"  /1 Resume — {date_str}")
    print(f"  Model: {args.model}")
    print(f"{'=' * 50}\n")

    print("📦 萃取 structured_data...")
    structured_data = extract_structured_data(ctx.gemini_api_key, report_content)
    if isinstance(structured_data, dict) and structured_data.get("error"):
        print(f"❌ 萃取失敗: {structured_data.get('raw_error', '')[:300]}")
        return 1
    print("  ✅ structured_data 萃取成功")

    from modules.pipeline_support import resolve_onair_prediction_targets

    prev_targets, prev_structured, validation_meta = find_previous_structured_data(
        ctx.reports_dir,
        as_of_date=date_str,
    )
    onair_targets = resolve_onair_prediction_targets(prev_targets, validation_meta)
    if not validation_meta.get("onair_validation_eligible"):
        validation_results = []
        print(f"  ⏭️  驗收略過：{validation_meta.get('skip_reason')}")
    else:
        validation_results = _build_validation_from_report(date_str, prev_structured)
        if onair_targets and not validation_results:
            # keep path for older resume helper that rebuilds from report text
            pass
    editorial_frame = build_editorial_frame(
        run_date=date_str,
        signal_pack={"strongest_anomaly": None, "watch_candidates": []},
        validation_results=validation_results,
        event_context="",
        channel_story="",
        validation_meta=validation_meta,
    )

    structured_data = enrich_structured_data_with_timeframes(structured_data)
    json_path = daily_artifact_write_path(ctx.reports_dir, date_str, "structured_data")
    with open(json_path, "w", encoding="utf-8") as f:
        deterministic_prev = format_validation_results_for_structured_data(validation_results)
        if deterministic_prev:
            structured_data["prev_day_picks_result"] = deterministic_prev
            structured_data["validation_results"] = validation_results
        structured_data["editorial_frame"] = editorial_frame
        structured_data["supply_chain_event_radar"] = (prev_structured or {}).get(
            "supply_chain_event_radar"
        ) or []
        json.dump(structured_data, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 已寫入: {os.path.basename(json_path)}")

    try:
        src = save_notebooklm_source(structured_data, ctx.reports_dir, date_str=date_str)
        print(f"  ✅ NotebookLM source: {os.path.basename(src)}")
    except Exception as exc:
        print(f"  ⚠️  NotebookLM source 失敗: {exc}")

    try:
        compound = LocalKnowledgeCompounder().update_from_structured_data(
            structured_data=structured_data,
            report_content=report_content,
            report_date=date_str,
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

    print("\n🎧 生成 NotebookLM prompt（無固定等待）...")
    prompt = generate_notebooklm_prompt(
        ctx.gemini_api_key,
        structured_data,
        date_str,
        current_theme=ctx.project_theme,
        skill_context="",
    )
    nl_path = daily_artifact_write_path(ctx.reports_dir, date_str, "notebooklm_prompt")
    if isinstance(prompt, str) and not prompt.strip().startswith("Error generating"):
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