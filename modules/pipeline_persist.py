import datetime
import json
import os

from modules.ai_usage_guard import print_today_usage
from modules.data_extractor import extract_structured_data
from modules.fetch_result import is_fetch_result
from modules.local_knowledge_compounder import LocalKnowledgeCompounder
from modules.knowledge_manager import KnowledgeUpdater
from modules.notebooklm_generator import generate_notebooklm_prompt
from modules.notebooklm_source_builder import save_notebooklm_source
from modules.analytics_store import AnalyticsStore
from modules.current_index import write_current_index
from modules.report_paths import daily_artifact_write_path
from modules.prediction_validator import format_validation_results_for_structured_data
from modules.structured_facts import build_local_structured_facts, summarize_local_facts
from modules.timeframe_builder import enrich_structured_data_with_timeframes
from modules.role_scorer import attach_role_scores_to_structured, load_stock_db
from modules.narrative_sanitize import sanitize_narrative_text, sanitize_structured_data


def persist_daily_artifacts(
    *,
    gemini_api_key,
    report_content,
    reports_dir,
    date_str,
    structured_data=None,
    validation_results=None,
    validation_meta=None,
    editorial_frame=None,
    supply_chain_event_radar=None,
    market_data=None,
    institutional_data=None,
    volume_data=None,
    commodity_data=None,
    skill_context="",
    project_theme="",
    channel_story_log="",
    youtube_assets_dir="",
    similar_sessions=None,
):
    os.makedirs(reports_dir, exist_ok=True)
    validation_results = validation_results or []
    validation_meta = validation_meta or {}
    market_data = market_data or {}
    institutional_data = institutional_data or {}
    volume_data = volume_data or []
    commodity_data = commodity_data or {}
    supply_chain_event_radar = supply_chain_event_radar or []

    report_content = sanitize_narrative_text(report_content or "")
    filepath = daily_artifact_write_path(reports_dir, date_str, "report")
    filename = os.path.basename(filepath)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(report_content)

    local_facts = build_local_structured_facts(
        date_str=date_str,
        market_data=market_data,
        institutional_data=institutional_data,
        volume_data=volume_data,
        commodity_data=commodity_data,
        validation_results=validation_results,
    )
    print("\n📦 正在萃取結構化數據（本機行情覆寫數字欄位，無固定等待）...")
    print(f"  📌 本機事實: {summarize_local_facts(local_facts)}")
    structured_data = extract_structured_data(
        gemini_api_key,
        report_content,
        local_facts=local_facts,
    )
    if isinstance(structured_data, dict) and structured_data.get("error"):
        failed_json_filename = f"structured_data_failed_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        failed_json_filepath = daily_artifact_write_path(reports_dir, date_str, "structured_data").replace(
            "structured_data.json", failed_json_filename
        )
        with open(failed_json_filepath, "w", encoding="utf-8") as f:
            json.dump(structured_data, f, ensure_ascii=False, indent=2)
        print("\n❌ 結構化數據萃取失敗，停止後續素材生成以避免覆蓋正式 JSON。")
        print(f"   錯誤紀錄已儲存: {failed_json_filename}")
        print("   日報已保留，可在 Gemini 額度恢復後只重跑後續素材流程。")
        return {
            "ok": False,
            "report_path": filepath,
            "structured_data": None,
            "json_path": None,
            "notebooklm_prompt_path": None,
            "notebooklm_source_path": None,
            "filename": filename,
            "structured_data_filename": failed_json_filename,
        }

    structured_data = enrich_structured_data_with_timeframes(structured_data)

    json_filepath = daily_artifact_write_path(reports_dir, date_str, "structured_data")
    json_filename = os.path.basename(json_filepath)
    with open(json_filepath, "w", encoding="utf-8") as f:
        deterministic_prev_results = format_validation_results_for_structured_data(validation_results)
        if deterministic_prev_results:
            structured_data["prev_day_picks_result"] = deterministic_prev_results
            structured_data["validation_results"] = validation_results
        else:
            # Explicit empty when gap-skip: avoid Gemini leftover 「昨日驗收」字串
            structured_data["prev_day_picks_result"] = []
            structured_data["validation_results"] = []
        if validation_meta:
            structured_data["validation_policy"] = {
                "source_date": validation_meta.get("source_date"),
                "trading_days_gap": validation_meta.get("trading_days_gap"),
                "onair_validation_eligible": validation_meta.get("onair_validation_eligible"),
                "skip_reason": validation_meta.get("skip_reason"),
                "max_trading_gap": validation_meta.get("max_trading_gap"),
            }
        structured_data["editorial_frame"] = editorial_frame
        structured_data["supply_chain_event_radar"] = supply_chain_event_radar
        try:
            from modules.market_calendar import events_for_structured

            structured_data["calendar_events"] = events_for_structured(date_str)
        except Exception:
            structured_data["calendar_events"] = []
        try:
            structured_data = attach_role_scores_to_structured(
                structured_data,
                load_stock_db(),
                limit=5,
            )
            top = (structured_data.get("focus_ranking") or [])[:3]
            if top:
                preview = "、".join(
                    f"{r.get('ticker')} {r.get('name') or ''}(P{r.get('title_priority')})".strip()
                    for r in top
                )
                print(f"  ✅ 主角量化排序: {preview}")
        except Exception as score_err:
            print(f"  ⚠️  角色量化分數略過: {score_err}")
        try:
            from modules.broadcast_facts import attach_broadcast_facts

            structured_data = attach_broadcast_facts(structured_data)
        except Exception as broadcast_err:
            print(f"  ⚠️  播出帳本略過: {broadcast_err}")
        structured_data = sanitize_structured_data(structured_data)
        try:
            from modules.similar_sessions import find_similar_for_structured

            if similar_sessions is None:
                similar_sessions = find_similar_for_structured(structured_data, reports_dir)
            structured_data["similar_sessions"] = similar_sessions or []
            if similar_sessions:
                preview = "、".join(
                    f"{row.get('date')}({row.get('score')})" for row in similar_sessions
                )
                print(f"  ✅ 相似盤已寫入 structured_data: {preview}")
            else:
                print("  ⚠️  無足夠相似盤可寫入 structured_data")
        except Exception as similar_err:
            print(f"  ⚠️  相似盤寫入略過: {similar_err}")
        json.dump(structured_data, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 結構化數據已儲存: {json_filename}")
    if local_facts.get("safe_havens_bought") or local_facts.get("heavyweights_dumped"):
        print("  ✅ 法人榜 / 大盤 / 商品欄位已用本機數據覆寫")

    try:
        notebooklm_source_path = save_notebooklm_source(structured_data, reports_dir, date_str=date_str)
        print(f"  ✅ NotebookLM 乾淨資料源已儲存: {os.path.basename(notebooklm_source_path)}")
    except Exception as e:
        print(f"  ⚠️  NotebookLM 乾淨資料源產生失敗，略過: {e}")
        notebooklm_source_path = None

    print("\n🧠 正在更新本機知識複利（市場記憶 + 個股角色分類，不消耗 AI 額度）...")
    try:
        compound_result = LocalKnowledgeCompounder().update_from_structured_data(
            structured_data=structured_data,
            report_content=report_content,
            report_date=date_str,
        )
        print(
            "  ✅ 本機知識已更新："
            f"市場記憶 {'新增' if compound_result['memory_appended'] else '已存在'}；"
            f"個股角色更新 {compound_result['roles_updated']} 檔"
        )
    except Exception as e:
        print(f"  ⚠️  本機知識複利更新失敗，略過: {e}")

    print("\n📈 正在寫入本地 Analytics DB（資料複利，不消耗 AI 額度）...")
    try:
        analytics_summary = AnalyticsStore().record_daily_run(
            run_date=date_str,
            structured_data=structured_data,
            report_path=filepath,
            structured_data_path=json_filepath,
            market_data=market_data,
            institutional_data=institutional_data,
            volume_data=volume_data,
        )
        hit_rate = analytics_summary.get("hit_rate")
        hit_rate_str = f"{hit_rate:.1f}%" if hit_rate is not None else "尚無可驗收樣本"
        print(
            "  ✅ Analytics DB 已更新: "
            f"日報 {analytics_summary['daily_runs']} 筆、"
            f"預測 {analytics_summary['predictions']} 筆、"
            f"驗收 {analytics_summary['evaluated_results']} 筆、"
            f"命中率 {hit_rate_str}"
        )
        try:
            from scripts.maintenance.analytics_report import write_report as write_analytics_report

            analytics_report_path = write_analytics_report()
            print(f"  ✅ Analytics 摘要已更新: {os.path.basename(analytics_report_path)}")
        except Exception as report_error:
            print(f"  ⚠️  Analytics 摘要產生失敗: {report_error}")
        try:
            from scripts.maintenance.build_dashboard_stock_watch import build_payload, DEFAULT_OUTPUT

            watch = build_payload(date_str)
            DEFAULT_OUTPUT.write_text(
                json.dumps(watch, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(
                f"  ✅ 股票雷達已更新: {os.path.basename(DEFAULT_OUTPUT)} "
                f"（{watch.get('sourceDate') or date_str}）"
            )
        except Exception as watch_error:
            print(f"  ⚠️  股票雷達摘要產生失敗: {watch_error}")
    except Exception as e:
        print(f"  ⚠️  Analytics DB 寫入失敗，略過本地統計: {e}")

    print("\n🎧 正在生成 NotebookLM Podcast 指令（本機模板，不消耗 Gemini）...")
    print(f"  📌 目前專案主題標籤: {project_theme if project_theme else '無 (日常盤勢)'}")
    print("  🎨 今日視覺設定: 延後由 /2 設定")
    notebooklm_prompt_content = generate_notebooklm_prompt(
        gemini_api_key,
        structured_data,
        date_str,
        current_theme=project_theme,
        skill_context=skill_context,
    )
    if isinstance(notebooklm_prompt_content, str) and notebooklm_prompt_content.strip().startswith("Error generating"):
        print("  ⚠️  NotebookLM prompt 生成失敗，跳過寫入。")
        notebooklm_prompt_content = None
    elif notebooklm_prompt_content is None:
        print("  ⏭  NotebookLM prompt 已關閉（NOTEBOOKLM_PROMPT_MODE=off）")

    nl_filepath = daily_artifact_write_path(reports_dir, date_str, "notebooklm_prompt")
    nl_filename = os.path.basename(nl_filepath)
    if notebooklm_prompt_content:
        with open(nl_filepath, "w", encoding="utf-8") as f:
            f.write(notebooklm_prompt_content)
        print(f"  ✅ NotebookLM 本機指令已寫入: {nl_filename}")

    print(f"\n✅ 報告已儲存至: {filepath}")
    if notebooklm_prompt_content:
        print(f"✅ NotebookLM 腳本指令已儲存至: {nl_filepath}")

    print("\n📢 YouTube 標題、封面 Prompt 與上架素材已延後至 /2 產生。")
    print("   /2 會讀取本次 daily_report 與 structured_data，再套用當次指定的視覺主題。")

    print("\n🧠 正在更新 Knowledge Wiki（預設本機模式，不消耗 Gemini）...")
    knowledge_updater = KnowledgeUpdater(gemini_api_key)
    knowledge_updater.update_knowledge_base(
        report_content,
        structured_data=structured_data,
        report_date=date_str,
    )

    print("\n📺 [優化 C] 更新頻道敘事紀錄...")
    if channel_story_log and youtube_assets_dir:
        from modules.pipeline_support import append_channel_story_log

        append_channel_story_log(
            date_str=date_str,
            structured_data=structured_data,
            visual_theme="待 /2 設定",
            prediction_result_summary="待驗收",
            youtube_assets_dir=youtube_assets_dir,
            channel_story_log=channel_story_log,
        )

    try:
        current_path = write_current_index(
            reports_dir,
            date_str,
            daily_ready=True,
            youtube_ready=False,
        )
        print(f"  ✅ 今日索引已更新: {os.path.basename(current_path)}")
    except Exception as e:
        print(f"  ⚠️  今日索引更新失敗，略過: {e}")

    return {
        "ok": True,
        "report_path": filepath,
        "structured_data": structured_data,
        "json_path": json_filepath,
        "notebooklm_prompt_path": nl_filepath if notebooklm_prompt_content else None,
        "notebooklm_source_path": notebooklm_source_path,
        "filename": filename,
        "structured_data_filename": json_filename,
        "notebooklm_prompt_content": notebooklm_prompt_content,
    }
