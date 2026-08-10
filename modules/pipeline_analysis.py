import datetime
import os

from modules.editorial_frame import build_editorial_frame, format_editorial_frame_for_prompt
from modules.event_radar import build_event_radar, format_event_radar_for_prompt
from modules.market_signal_scorer import (
    build_market_signal_pack,
    format_signal_pack_for_prompt,
    load_stock_db,
)
from modules.fetch_result import unwrap_fetch_result
from modules.pipeline_stages import collect_news_bundle
from modules.prediction_validator import (
    format_validation_results_for_prompt,
    validate_prediction_targets,
)
from modules.project_skill_registry import format_skill_context
from modules.pipeline_support import _read_tail, should_expand_news_search


def build_analysis_bundle(
    *,
    fetcher,
    institutional_data,
    market_data,
    commodity_data,
    volume_data,
    macro_events,
    yesterday_predictions,
    dynamic_topics,
    news_mode,
    supply_chain_event_radar,
    active_skills,
    channel_story_log,
    base_dir,
    gemini_api_key,
    report_date=None,
    validation_meta=None,
):
    report_date = report_date or datetime.datetime.now().strftime("%Y-%m-%d")
    validation_meta = validation_meta or {}

    validation_results = validate_prediction_targets(
        previous_targets=yesterday_predictions,
        market_data=market_data,
        institutional_data=institutional_data,
        volume_data=volume_data,
    )
    if validation_meta and not validation_meta.get("onair_validation_eligible"):
        reason = validation_meta.get("skip_reason") or "前次產出間隔過長，略過上集驗收"
        validation_summary = (
            f"# 本機驗收政策：略過上集驗收\n"
            f"- 原因：{reason}\n"
            f"- 前次日期：{validation_meta.get('source_date') or '未知'}\n"
            f"- 交易日距：{validation_meta.get('trading_days_gap')}\n"
            f"- 規則：僅當前次 /1 為上一交易日（或 gap≤VALIDATION_MAX_TRADING_GAP）才做「昨日/上集驗收」\n"
            f"- 請不要在標題/開場用驗收或「上集點名」敘事"
        )
        print(f"\n⏭️  驗收政策：{reason}")
    else:
        validation_summary = format_validation_results_for_prompt(validation_results)
        if validation_results:
            print("\n✅ 本機正式驗收結果：")
            for line in validation_summary.splitlines()[1:]:
                print(f"  {line}")

    if news_mode in {"lite", "smart", "full"} and not supply_chain_event_radar:
        print("\n🧭 正在跑低成本消息面哨兵（不做大規模新聞搜尋）...")
        supply_chain_event_candidates = []
        if news_mode != "off":
            supply_chain_event_candidates = fetcher.get_supply_chain_catalyst_events(
                days=7,
                lite=(news_mode != "full"),
            )
            supply_chain_event_candidates = unwrap_fetch_result(supply_chain_event_candidates, default=[])
        supply_chain_event_radar = build_event_radar(supply_chain_event_candidates)
    elif news_mode == "off":
        print("\n🧭 NEWS_MODE=off，跳過消息面事件雷達")

    if supply_chain_event_radar:
        top_event = supply_chain_event_radar[0]
        print(f"  ✅ 高分供應鏈事件: {top_event['event']}（{top_event['impact_score']}）")
    else:
        print("  ⚠️  未偵測到高分供應鏈事件")

    print("\n🔍 正在檢查是否有新股票需要 AI 供應鏈調查...")
    from modules.stock_db_updater import StockDatabaseUpdater

    try:
        updater = StockDatabaseUpdater(gemini_api_key)
        stocks_to_check = []
        for s in (institutional_data.get("top_buy", []) or []) + (institutional_data.get("top_sell", []) or []):
            stocks_to_check.append({"id": s["id"], "name": s["name"]})
        for s in volume_data or []:
            stocks_to_check.append({"id": s["id"], "name": s["name"]})
        updater.update_new_stocks(stocks_to_check)

        if yesterday_predictions:
            print("\n📊 [優化 D] 正在更新前次預測標的的績效追蹤...")
            updater.update_tracking_stats(
                yesterday_predictions=yesterday_predictions,
                todays_market_data=market_data,
                todays_institutional_data=institutional_data,
            )
    except Exception as e:
        print(f"  [Error] 自動資料庫調查/績效追蹤失敗: {e}")

    print("\n🧮 正在計算本地量化訊號包（不消耗 Gemini 額度）...")
    stock_db = load_stock_db(base_dir)
    signal_pack = build_market_signal_pack(
        market_data=market_data,
        institutional_data=institutional_data,
        volume_data=volume_data,
        stock_db=stock_db,
    )
    skill_context = format_skill_context(active_skills)

    if news_mode == "off":
        expand_news, news_reason = False, "NEWS_MODE=off"
    elif news_mode == "lite":
        expand_news, news_reason = False, "NEWS_MODE=lite，只跑低成本事件哨兵"
    elif news_mode == "full":
        expand_news, news_reason = True, "NEWS_MODE=full"
    else:
        from modules.pipeline_support import should_expand_news_search

        expand_news, news_reason = should_expand_news_search(signal_pack, validation_results, supply_chain_event_radar)
    print(f"\n📰 新聞搜尋閘門: {news_reason}")

    if expand_news:
        print("📰 正在獲取新聞（數據/事件觸發後的精準搜尋）...")
    else:
        print("  ⏭️  跳過擴大新聞搜尋，避免為普通盤面消耗 API/算力。")
    news_data, unique_news = collect_news_bundle(
        fetcher=fetcher,
        expand_news=expand_news,
        dynamic_topics=dynamic_topics,
        supply_chain_event_radar=supply_chain_event_radar,
        unwrap_fetch_result=unwrap_fetch_result,
    )
    print(f"\n  📋 獨特新聞文章總數: {len(unique_news)}")

    deep_dive_data = None
    top_buy_list = institutional_data.get("top_buy", [])
    if expand_news and top_buy_list:
        focus_stock = top_buy_list[0]
        focus_id = focus_stock.get("id", "")
        focus_name = focus_stock.get("name", "")
        print(f"\n🎯 [深度押注] 今日焦點股：{focus_id} {focus_name}")
        try:
            deep_dive_data = unwrap_fetch_result(fetcher.get_deep_dive_data(
                stock_id=focus_id,
                stock_name=focus_name,
                institutional_data=institutional_data,
            ))
        except Exception as e:
            print(f"  ⚠️  深度押注資料抓取失敗: {e}")
    else:
        print("\n🎯 深度押注: 未觸發，跳過個股深挖以節省 API/算力。")

    event_context = "\n".join(
        [str(item.get("title", "")) for item in (macro_events or [])]
        + [str(item.get("event", "")) for item in (supply_chain_event_radar or [])]
        + [str(item.get("title", "")) for item in unique_news[:12]]
    )
    editorial_frame = build_editorial_frame(
        run_date=report_date,
        signal_pack=signal_pack,
        validation_results=validation_results,
        event_context=event_context,
        channel_story=_read_tail(channel_story_log, max_chars=5000),
        validation_meta=validation_meta,
    )
    editorial_frame_summary = format_editorial_frame_for_prompt(editorial_frame)
    event_radar_summary = format_event_radar_for_prompt(supply_chain_event_radar)

    signal_summary_parts = []
    if skill_context:
        signal_summary_parts.append(skill_context)
    signal_summary_parts.extend(
        [
            editorial_frame_summary,
            event_radar_summary,
            format_signal_pack_for_prompt(signal_pack),
        ]
    )
    signal_summary = "\n\n".join(part for part in signal_summary_parts if part)

    strongest = signal_pack.get("strongest_anomaly")
    if strongest:
        print(f"  ✅ 今日可用訊號: {strongest['type']} - {strongest['text']}")
    else:
        print("  ⚠️  未偵測到明確法人/成交量背離，將以成交量與技術位共振為主。")
    watch_candidates = signal_pack.get("watch_candidates") or []
    if watch_candidates:
        preview = "、".join([f"{c['ticker']} {c.get('name', '')}".strip() for c in watch_candidates[:3]])
        print(f"  ✅ 明日觀察候選: {preview}")
    else:
        print("  ⚠️  本地門檻下無安全明日觀察候選。")

    print(
        f"  ✅ 本地主軸分類: {editorial_frame['label']} "
        f"(信心 {editorial_frame['confidence']}/100；"
        f"驗收={editorial_frame.get('validation_placement', 'n/a')})"
    )

    return {
        "validation_results": validation_results,
        "validation_summary": validation_summary,
        "validation_meta": validation_meta,
        "supply_chain_event_radar": supply_chain_event_radar,
        "signal_pack": signal_pack,
        "skill_context": skill_context,
        "expand_news": expand_news,
        "news_reason": news_reason,
        "news_data": news_data,
        "unique_news": unique_news,
        "deep_dive_data": deep_dive_data,
        "editorial_frame": editorial_frame,
        "editorial_frame_summary": editorial_frame_summary,
        "event_radar_summary": event_radar_summary,
        "signal_summary": signal_summary,
    }
