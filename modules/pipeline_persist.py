import datetime
import json
import os

from modules.ai_usage_guard import print_today_usage
from modules.atomic_io import write_json_atomic
from modules.data_extractor import extract_structured_data
from modules.fetch_result import is_fetch_result
from modules.local_knowledge_compounder import LocalKnowledgeCompounder
from modules.knowledge_manager import KnowledgeUpdater
from modules.notebooklm_generator import generate_notebooklm_prompt
from modules.notebooklm_source_builder import save_notebooklm_source
from modules.analytics_store import AnalyticsStore
from modules.current_index import write_current_index
from modules.report_paths import daily_artifact_write_path
from modules.resume_inputs import save_resume_inputs
from modules.prediction_validator import format_validation_results_for_structured_data
from modules.structured_facts import build_local_structured_facts, summarize_local_facts
from modules.timeframe_builder import enrich_structured_data_with_timeframes
from modules.role_scorer import attach_role_scores_to_structured, build_role_score_inputs, load_stock_db
from modules.narrative_sanitize import (
    sanitize_narrative_text,
    sanitize_structured_data,
    strip_number_readings,
    summarize_hits,
)
from modules.report_fact_check import fact_check_report, format_fact_check_lines
from modules.tz import now_tpe


def write_fact_check_file(daily_dir, fact_check):
    """
    fact_check.json exists only while the *current* report has warn / fail
    findings. A same-day rerun that passes (or is skipped / off) removes the
    file from the earlier run, so it never disagrees with structured_data.
    """
    path = os.path.join(daily_dir, "fact_check.json")
    try:
        if (fact_check or {}).get("status") in {"warn", "fail"}:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(fact_check, f, ensure_ascii=False, indent=2)
        elif os.path.exists(path):
            os.remove(path)
    except Exception as fc_err:
        print(f"  ⚠️  fact_check.json 更新略過: {fc_err}")
    return path


def finalize_structured_data(
    structured_data,
    *,
    date_str,
    reports_dir,
    validation_results=None,
    validation_meta=None,
    editorial_frame=None,
    supply_chain_event_radar=None,
    similar_sessions=None,
    market_data=None,
    institutional_data=None,
    volume_data=None,
    fact_check=None,
    sanitize_hits=None,
    value_ranking=None,
):
    """
    Deterministic post-extraction steps shared by /1 and the resume script:
    local 驗收 overwrite (or explicit clearing), validation policy, calendar,
    role scores (with role_score_inputs), mid-term timeframes, broadcast
    ledger, similar sessions and, last, the narrative sanitize.

    The payloads (market_data / institutional_data / volume_data) are optional:
    resume passes the copies /1 saved in resume_inputs.json (schema 2); without
    them role inputs fall back to local data and the broadcast ledger to the
    stored ``local_market_facts``. ``fact_check`` is stored when given;
    ``sanitize_hits`` are the report-side rewrites (empty on resume).
    """
    validation_results = validation_results or []
    validation_meta = validation_meta or {}
    supply_chain_event_radar = supply_chain_event_radar or []
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
        role_db = load_stock_db()
        try:
            role_inputs = build_role_score_inputs(
                structured_data,
                role_db,
                market_data=market_data,
                volume_data=volume_data,
                reports_dir=reports_dir,
                value_ranking=value_ranking,
            )
        except Exception as inputs_err:
            print(f"  ⚠️  角色分數輸入（量榜／量增比／波動率）略過: {inputs_err}")
            role_inputs = None
        structured_data = attach_role_scores_to_structured(
            structured_data,
            role_db,
            limit=5,
            role_inputs=role_inputs,
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
    # After validation_results / radar / focus_ranking are attached, so the
    # mid-term candidate pool and the radar branch can see them.
    try:
        structured_data = enrich_structured_data_with_timeframes(
            structured_data, as_of=date_str, market_data=market_data
        )
    except Exception as timeframe_err:
        print(f"  ⚠️  中期時間窗略過: {timeframe_err}")
    try:
        from modules.broadcast_facts import attach_broadcast_facts

        # Hero-card numbers come from the local payloads (joined by ticker),
        # not from regex over Gemini prose.
        structured_data = attach_broadcast_facts(
            structured_data,
            institutional_data=institutional_data,
            volume_data=volume_data,
            market_data=market_data,
        )
    except Exception as broadcast_err:
        print(f"  ⚠️  播出帳本略過: {broadcast_err}")
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
    # Sanitize last so similar_sessions / broadcast_facts are covered too.
    sanitize_hits = sanitize_hits or []
    structured_hits = []
    structured_data = sanitize_structured_data(structured_data, hits=structured_hits)
    if fact_check is not None:
        structured_data["fact_check"] = fact_check
    structured_data["sanitize_hits"] = {
        "report": summarize_hits(sanitize_hits),
        "structured_data": summarize_hits(structured_hits),
    }
    if sanitize_hits or structured_hits:
        print(
            f"  🧹 意圖／陰謀用語已改寫：日報 {len(sanitize_hits)} 處、structured_data {len(structured_hits)} 處"
        )
    return structured_data


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
    ledger_market_data=None,
    institutional_data=None,
    volume_data=None,
    commodity_data=None,
    skill_context="",
    project_theme="",
    channel_story_log="",
    youtube_assets_dir="",
    similar_sessions=None,
    local_extras=None,
):
    """
    ``local_extras`` (optional): {global_overnight, market_extras, value_ranking,
    data_dates: {institutional, volume}, news_inputs: {...}} from main.py.
    """
    os.makedirs(reports_dir, exist_ok=True)
    local_extras = local_extras or {}
    validation_results = validation_results or []
    validation_meta = validation_meta or {}
    market_data = market_data or {}
    # In-flight ledger names (pipeline_stages.ledger_only_symbols): closes for
    # the prediction ledger only, never part of today's analysis inputs.
    ledger_market_data = ledger_market_data or {}
    institutional_data = institutional_data or {}
    volume_data = volume_data or []
    commodity_data = commodity_data or {}
    supply_chain_event_radar = supply_chain_event_radar or []

    sanitize_hits = []
    report_content = sanitize_narrative_text(report_content or "", hits=sanitize_hits)
    # Gemini-written Chinese number readings are often wrong; numbers stay Arabic.
    report_content = strip_number_readings(report_content)
    # Post-generation fact check: local leaderboards + claim check before anything is written.
    report_content, fact_check = fact_check_report(
        report_content,
        institutional_data=institutional_data,
        volume_data=volume_data,
        market_data=market_data,
        value_ranking=local_extras.get("value_ranking"),
    )
    for line in format_fact_check_lines(fact_check):
        print(f"  🔎 {line}" if not line.startswith("  ") else f"    {line.strip()}")
    filepath = daily_artifact_write_path(reports_dir, date_str, "report")
    filename = os.path.basename(filepath)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(report_content)
    write_fact_check_file(os.path.dirname(filepath), fact_check)

    news_inputs_path = write_news_inputs(reports_dir, date_str, local_extras.get("news_inputs"))
    if news_inputs_path:
        print(f"  ✅ 新聞輸入存檔（可稽核來源）: {os.path.relpath(news_inputs_path, reports_dir)}")

    try:
        from modules.market_extras import format_extras_lines

        extras_lines = format_extras_lines(local_extras.get("market_extras"))
    except Exception:
        extras_lines = []
    local_facts = build_local_structured_facts(
        date_str=date_str,
        market_data=market_data,
        institutional_data=institutional_data,
        volume_data=volume_data,
        commodity_data=commodity_data,
        validation_results=validation_results,
        global_overnight=local_extras.get("global_overnight"),
        market_extras=extras_lines,
        value_ranking=local_extras.get("value_ranking"),
    )
    try:
        save_resume_inputs(
            reports_dir,
            date_str,
            local_facts=local_facts,
            validation_results=validation_results,
            validation_meta=validation_meta,
            editorial_frame=editorial_frame,
            supply_chain_event_radar=supply_chain_event_radar,
            market_data=market_data,
            institutional_data=institutional_data,
            volume_data=volume_data,
            similar_sessions=similar_sessions,
            fact_check=fact_check,
            value_ranking=local_extras.get("value_ranking"),
        )
    except Exception as e:
        print(f"  ⚠️  補跑用輸入 resume_inputs.json 儲存失敗，略過: {e}")
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
        print("   日報已保留，可在 Gemini 額度恢復後只重跑後續素材流程：")
        print(f"   python3 scripts/maintenance/resume_daily_persist.py --date {date_str}")
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

    json_filepath = daily_artifact_write_path(reports_dir, date_str, "structured_data")
    json_filename = os.path.basename(json_filepath)
    # Enrich the dict first, then write once atomically: a failure while
    # writing keeps the previous structured_data.json intact (tomorrow's 驗收
    # reads it) instead of leaving an empty/truncated file.
    structured_data = finalize_structured_data(
        structured_data,
        date_str=date_str,
        reports_dir=reports_dir,
        validation_results=validation_results,
        validation_meta=validation_meta,
        editorial_frame=editorial_frame,
        supply_chain_event_radar=supply_chain_event_radar,
        similar_sessions=similar_sessions,
        market_data=market_data,
        institutional_data=institutional_data,
        volume_data=volume_data,
        fact_check=fact_check,
        sanitize_hits=sanitize_hits,
        value_ranking=local_extras.get("value_ranking"),
    )
    write_json_atomic(json_filepath, structured_data)
    print(f"  ✅ 結構化數據已儲存: {json_filename}")
    overwritten = [
        label
        for label, key in (("法人榜", "safe_havens_bought"), ("大盤", "index_action"), ("商品", "commodities"), ("海外", "global_overnight"))
        if local_facts.get(key)
    ]
    if overwritten:
        print(f"  ✅ 已用本機數據覆寫：{' / '.join(overwritten)}")
    if not local_facts.get("index_action"):
        print("  ⚠️  大盤欄位本機未取得，已標記「資料未取得」（不沿用 AI 數字）")

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
            market_data={**ledger_market_data, **market_data},
            institutional_data=institutional_data,
            volume_data=volume_data,
        )
        hit_rate = analytics_summary.get("hit_rate")
        if hit_rate is not None:
            hit_rate_str = f"{hit_rate:.1f}%"
        elif analytics_summary.get("legacy_results"):
            hit_rate_str = "暫無（舊制結果待轉換，見上方警告）"
        else:
            hit_rate_str = "尚無可驗收樣本"
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
    try:
        knowledge_updater = KnowledgeUpdater(gemini_api_key)
        knowledge_updater.update_knowledge_base(
            report_content,
            structured_data=structured_data,
            report_date=date_str,
        )
    except Exception as e:
        print(f"  ⚠️  Knowledge Wiki 更新失敗，略過（日報與 structured_data 已保存）: {e}")

    print("\n📺 [優化 C] 更新頻道敘事紀錄...")
    if channel_story_log and youtube_assets_dir:
        from modules.pipeline_support import append_channel_story_log

        try:
            append_channel_story_log(
                date_str=date_str,
                structured_data=structured_data,
                visual_theme="待 /2 設定",
                prediction_result_summary="待驗收",
                youtube_assets_dir=youtube_assets_dir,
                channel_story_log=channel_story_log,
            )
        except Exception as e:
            print(f"  ⚠️  頻道敘事紀錄更新失敗，略過: {e}")

    try:
        data_dates = local_extras.get("data_dates") or {}
        current_path = write_current_index(
            reports_dir,
            date_str,
            daily_ready=True,
            youtube_ready=False,
            institutional_date=data_dates.get("institutional") or institutional_data.get("data_date"),
            volume_date=data_dates.get("volume"),
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


def _news_row(item, in_prompt_urls=None):
    item = item or {}
    url = str(item.get("url") or "")
    row = {
        "title": str(item.get("title") or item.get("event") or "")[:200],
        "url": url,
        "domain": url.split("/")[2] if url.count("/") >= 2 else "",
        "published_date": item.get("published_date"),
        "snippet": str(item.get("snippet") or item.get("content") or "")[:300],
    }
    if in_prompt_urls is not None:
        row["in_prompt"] = url in in_prompt_urls
    return row


def build_news_inputs(news_inputs, date_str):
    """Audit record of every news / search input that could feed the daily report."""
    news_inputs = news_inputs or {}
    prompt_news = [_news_row(n) for n in news_inputs.get("prompt_news") or [] if n]
    radar = [
        {**_news_row(r), "impact_score": (r or {}).get("impact_score"), "freshness": (r or {}).get("freshness")}
        for r in news_inputs.get("radar") or []
        if r
    ]
    macro = [_news_row(m) for m in news_inputs.get("macro_events") or [] if m]
    in_prompt = {r["url"] for r in prompt_news + radar + macro if r["url"]}
    deep = news_inputs.get("deep_dive") or {}
    deep_news = [_news_row(n) for n in (deep.get("recent_news") or []) if n]
    in_prompt.update(r["url"] for r in deep_news[:3] if r["url"])
    # 同名異公司 results screened out before the prompt (tape topics + deep dive).
    rejected = []
    for item in list(news_inputs.get("rejected_news") or []) + list(deep.get("rejected_news") or []):
        if not item:
            continue
        row = _news_row(item, in_prompt)
        row["reason"] = item.get("reason") or "homonym"
        entity = item.get("entity") or {}
        if entity:
            row["entity"] = f"{entity.get('name') or ''} {entity.get('id') or ''}".strip()
        if item.get("query"):
            row["query"] = item.get("query")
        rejected.append(row)
    rejected_reason = {r["url"]: r["reason"] for r in rejected if r["url"] and not r["in_prompt"]}
    searches = []
    for entry in news_inputs.get("searches") or []:
        if not isinstance(entry, dict):
            continue
        results = []
        for r in entry.get("results") or []:
            row = _news_row(r, in_prompt)
            if row["url"] in rejected_reason:
                row["reason"] = rejected_reason[row["url"]]
            results.append(row)
        searches.append(
            {
                "stage": entry.get("stage"),
                "query": entry.get("query"),
                "params": entry.get("params") or {},
                "results": results,
            }
        )
    return {
        "date": date_str,
        "generated_at": now_tpe().isoformat(timespec="seconds"),
        "note": "Tavily 輸入稽核紀錄（本機保存，不進版控）。in_prompt=是否進入日報 prompt。",
        "prompt_news": prompt_news,
        "radar": radar,
        "macro_events": macro,
        "deep_dive": {
            "stock_id": deep.get("stock_id"),
            "stock_name": deep.get("stock_name"),
            "fundamental": deep.get("fundamental") or {},
            "recent_news": deep_news,
            "summary_for_ai": deep.get("summary_for_ai"),
        } if deep else None,
        "rejected_news": rejected,
        "searches": searches,
    }


def write_news_inputs(reports_dir, date_str, news_inputs):
    """Write reports/daily/<date>/news_inputs.json; never raises into /1."""
    if not news_inputs:
        return None
    try:
        from modules.report_paths import ensure_daily_dir

        path = os.path.join(ensure_daily_dir(reports_dir, date_str), "news_inputs.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(build_news_inputs(news_inputs, date_str), f, ensure_ascii=False, indent=2, default=str)
        return path
    except Exception as e:
        print(f"  ⚠️  新聞輸入存檔失敗，略過: {e}")
        return None
