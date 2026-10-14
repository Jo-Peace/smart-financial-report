import os

# Ledger predictions whose 1/3/5/20-session window is still open need a close
# every session (target day + stop-loss path), not only on days they happen to
# re-enter today's lists. 0 disables; tickers already on the TWSE full tape
# (daily_closes) are skipped because the ledger reads that close directly.
INFLIGHT_FETCH_ENV = "INFLIGHT_PRICE_FETCH_MAX"
INFLIGHT_FETCH_DEFAULT = 15

GENERIC_TOPIC_MARKERS = (
    "taiwan semiconductor",
    "今日強勢族群",
    "量價背離",
)


def _is_generic_news_topic(topic: str) -> bool:
    low = str(topic or "").lower()
    return any(marker in low for marker in GENERIC_TOPIC_MARKERS)


def inflight_prediction_symbols(as_of_date=None, limit=None, store=None, print_fn=print):
    """yfinance symbols for in-flight ledger predictions (fail-safe: [] on any error)."""
    if limit is None:
        try:
            limit = int(os.getenv(INFLIGHT_FETCH_ENV, str(INFLIGHT_FETCH_DEFAULT)))
        except ValueError:
            limit = INFLIGHT_FETCH_DEFAULT
    if limit <= 0:
        return []
    try:
        if store is None:
            from modules.analytics_store import AnalyticsStore

            store = AnalyticsStore()
        tickers = store.get_inflight_prediction_tickers(as_of=as_of_date, limit=limit)
        # With today's TWSE tape stored, listed names were already skipped, so the
        # rest are OTC (TPEx) names; without the tape keep the usual .TW guess.
        has_tape = bool(getattr(store, "has_tape_closes", lambda *_: False)(as_of_date))
    except Exception as exc:  # analytics DB is optional for the daily run
        if print_fn:
            print_fn(f"  ⚠️  在途預測標的讀取略過：{exc}")
        return []
    suffix = ".TWO" if has_tape else ".TW"
    return [f"{ticker}{suffix}" for ticker in tickers if ticker]


def _bare_symbol(symbol):
    # Suffix-only (".TWO" before ".TW"; else "6488.TWO" -> "6488O").
    from modules.tickers import bare_ticker

    return bare_ticker(symbol)


OTC_MARKETS = {"tpex", "otc", "two", "上櫃"}


def yf_symbol_for(item) -> str:
    """Yahoo symbol for a ranking row: .TWO when the row says 上櫃 (FinMind TPEx), else .TW."""
    sid = str((item or {}).get("id") or "").strip()
    market = str((item or {}).get("market") or "").strip().lower()
    return f"{sid}.TWO" if market in OTC_MARKETS else f"{sid}.TW"


def build_dynamic_universe(
    base_symbols,
    base_topics,
    institutional_data,
    yesterday_predictions,
    inflight_symbols=None,
):
    """
    Analysis universe to fetch today: base symbols, top foreign buy/sell and
    the previous prediction targets.

    In-flight ledger names are never added here: they only need a close for
    the prediction ledger, and anything in this list reaches today's analysis
    (signal pack scoring, report stock list, data-quality gate). Fetch them
    with ledger_only_symbols(). inflight_symbols is used only to fetch an OTC
    name that is also a previous target under `.TWO` (the ledger knows it is
    off the TWSE tape; `.TW` has no data for it on yfinance or STOCK_DAY).
    """
    dynamic_symbols = list(base_symbols or [])
    tape_topics = []

    if institutional_data:
        for item in (institutional_data.get("top_buy", []) or [])[:3]:
            symbol_yf = yf_symbol_for(item)
            if symbol_yf not in dynamic_symbols:
                dynamic_symbols.append(symbol_yf)
            tape_topics.append(f"Taiwan stock {item['id']} {item['name']} 外資買超 原因")

        for item in (institutional_data.get("top_sell", []) or [])[:3]:
            symbol_yf = yf_symbol_for(item)
            if symbol_yf not in dynamic_symbols:
                dynamic_symbols.append(symbol_yf)
            tape_topics.append(f"Taiwan stock {item['id']} {item['name']} 外資賣超 原因")

    if yesterday_predictions:
        for pred in yesterday_predictions:
            pred_id = pred.get("id")
            if not pred_id:
                continue
            pred_symbol = yf_symbol_for(pred)
            if pred_symbol not in dynamic_symbols:
                dynamic_symbols.append(pred_symbol)

    otc = {_bare_symbol(s) for s in inflight_symbols or [] if str(s or "").endswith(".TWO")}
    if otc:
        upgraded = []
        for symbol in dynamic_symbols:
            if str(symbol).endswith(".TW") and _bare_symbol(symbol) in otc:
                symbol = f"{_bare_symbol(symbol)}.TWO"
            if symbol not in upgraded:
                upgraded.append(symbol)
        dynamic_symbols = upgraded

    generic = [str(t).strip() for t in (base_topics or []) if str(t).strip()]
    return dynamic_symbols, tape_topics + generic


def ledger_only_symbols(dynamic_symbols, inflight_symbols, print_fn=print):
    """
    In-flight ledger symbols not already in the analysis universe. Their
    payloads go to the ledger only (record_daily_run market snapshots), never
    into the market_data used for today's analysis.
    """
    covered = {_bare_symbol(s) for s in dynamic_symbols or []}
    out = []
    for symbol in inflight_symbols or []:
        bare = _bare_symbol(symbol)
        if not bare or bare in covered:
            continue
        covered.add(bare)
        out.append(symbol)
    if out and print_fn:
        print_fn(f"  📌 在途預測補抓價：{len(out)} 檔（驗收窗未結束、今日不在榜上；僅寫入驗收帳本，不進今日分析）")
    return out


def fetch_market_universe(fetch_one, analysis_symbols, ledger_symbols=None, max_workers=10, on_result=None):
    """
    Fetch analysis + ledger-only symbols in one thread pool.

    fetch_one(symbol) returns the payload dict (falsy on failure).
    Returns (market_data, ledger_market_data). Only analysis_symbols land in
    market_data (the dict every analysis step reads); ledger_symbols land in
    ledger_market_data, which is merged into the ledger write only
    (persist_daily_artifacts(ledger_market_data=...)). on_result(symbol, data,
    ledger) is called per symbol (data None on failure) for progress output.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    analysis_symbols = list(analysis_symbols or [])
    ledger_set = set(ledger_symbols or []) - set(analysis_symbols)
    symbols = analysis_symbols + [s for s in (ledger_symbols or []) if s in ledger_set]
    market_data, ledger_market_data = {}, {}
    if not symbols:
        return market_data, ledger_market_data
    workers = max(1, min(max_workers, len(symbols)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_one, sym): sym for sym in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                data = future.result()
            except Exception:  # one failed symbol must not stop /1
                data = None
            ledger = symbol in ledger_set
            if data:
                (ledger_market_data if ledger else market_data)[symbol] = data
            if on_result:
                on_result(symbol, data, ledger)
    return market_data, ledger_market_data


def tape_focus_entities(institutional_data=None, volume_data=None, yesterday_predictions=None, limit=6):
    """Today's protagonists as {id, name} (the id disambiguates 長榮 2603 vs 長榮航 2618)."""
    items = []
    items.extend(((institutional_data or {}).get("top_buy") or [])[:3])
    items.extend(((institutional_data or {}).get("top_sell") or [])[:2])
    items.extend((volume_data or [])[:3])
    items.extend(yesterday_predictions or [])
    out = []
    seen = set()
    for item in items:
        sid = str((item or {}).get("id") or "").strip()
        name = str((item or {}).get("name") or "").strip() or sid
        key = sid or name
        if not key or key in seen or name in seen:
            continue
        seen.update({key, name})
        out.append({"id": sid, "name": name})
        if len(out) >= limit:
            break
    return out


def tape_focus_names(institutional_data=None, volume_data=None, yesterday_predictions=None, limit=6):
    return [
        row["name"] or row["id"]
        for row in tape_focus_entities(institutional_data, volume_data, yesterday_predictions, limit=limit)
    ]


def _radar_mentions_focus(event, focus_names):
    from modules.entity_match import mentions_any

    blob = f"{(event or {}).get('event', '')} {(event or {}).get('snippet', '')}"
    return mentions_any(blob, focus_names) is not None


def news_search_topics(dynamic_topics, supply_chain_event_radar, focus_names=None, max_topics=5):
    """Protagonist queries first; radar last and only if it names today's tape."""
    topics = []
    generic = []
    for topic in list(dynamic_topics or []):
        text = str(topic or "").strip()
        if not text or text in topics or text in generic:
            continue
        if _is_generic_news_topic(text):
            generic.append(text)
            continue
        topics.append(text)
        if len(topics) >= max(1, max_topics - 1):
            break
    if len(topics) < 2:
        for text in generic:
            if text not in topics:
                topics.append(text)
            if len(topics) >= max(1, max_topics - 1):
                break

    from modules.entity_match import entity_query, mentions_any

    for row in supply_chain_event_radar or []:
        if (row or {}).get("freshness") == "stale":
            continue
        blob = f"{(row or {}).get('event', '')} {(row or {}).get('snippet', '')}"
        entity = mentions_any(blob, focus_names) if focus_names else None
        if focus_names and entity is None:
            continue
        # Search the named company + catalyst theme, not the radar headline itself
        # (re-searching the title only re-finds the article the radar already has).
        if entity is not None:
            topic = entity_query(entity, "訂單 法說 營運")
        else:
            topic = str((row or {}).get("event") or "").strip()
        if not topic or topic in topics:
            continue
        topics.append(topic)
        break

    return topics[:max_topics]


def collect_news_bundle(
    fetcher,
    expand_news,
    dynamic_topics,
    supply_chain_event_radar,
    unwrap_fetch_result,
    print_fn=print,
    focus_names=None,
    rejected=None,
):
    """
    Search the priority topics. Results for a stock topic are screened for
    同名異公司 (entity_match.screen_homonym_news); rejected items are appended
    to ``rejected`` (when a list is given) for the news_inputs audit and never
    enter news_data.
    """
    news_data = []
    if expand_news:
        priority_topics = news_search_topics(
            dynamic_topics,
            supply_chain_event_radar,
            focus_names=focus_names,
            max_topics=5,
        )
        if print_fn and priority_topics:
            print_fn(f"  🔎 新聞搜尋順序: {' → '.join(priority_topics)}")

        from modules.entity_match import news_query_for_topic, screen_homonym_news, topic_entity

        for topic in priority_topics:
            query = news_query_for_topic(topic)
            results = unwrap_fetch_result(fetcher.get_news(query), default=[])
            entity = topic_entity(topic, focus_names)
            results, dropped = screen_homonym_news(results, entity)
            if dropped:
                if rejected is not None:
                    rejected.extend(dict(item, query=query) for item in dropped)
                if print_fn:
                    print_fn(f"  🚫 {topic}: 排除 {len(dropped)} 篇同名異公司文章（非 {entity['name']} {entity['id']}）")
            if results:
                news_data.extend(results)
                if print_fn:
                    print_fn(f"  ✅ {topic}: 找到 {len(results)} 篇文章")
            elif print_fn:
                print_fn(f"  ⚠️  {topic}: 未找到文章")

    radar_urls = {str((row or {}).get("url") or "") for row in supply_chain_event_radar or []} - {""}
    unique_news = list(
        {n["url"]: n for n in news_data if n.get("url") and n.get("url") not in radar_urls}.values()
    )

    return news_data, unique_news


def pick_deep_dive_focus(institutional_data=None, signal_pack=None, volume_data=None):
    """Prefer today's anomaly ticker over a hardcoded buy-leader."""
    anomaly = (signal_pack or {}).get("strongest_anomaly") or {}
    ticker = str(anomaly.get("ticker") or "").strip()
    name = ""
    roster = list((institutional_data or {}).get("top_buy") or [])
    roster.extend((institutional_data or {}).get("top_sell") or [])
    roster.extend(volume_data or [])
    if ticker:
        for item in roster:
            if str(item.get("id") or "") == ticker:
                name = str(item.get("name") or "")
                break
        return {"id": ticker, "name": name or ticker}

    watch = ((signal_pack or {}).get("watch_candidates") or [{}])[0]
    watch_id = str(watch.get("ticker") or watch.get("id") or "").strip()
    if watch_id:
        return {"id": watch_id, "name": str(watch.get("name") or watch_id)}

    top = (institutional_data or {}).get("top_buy") or []
    if top:
        return {"id": str(top[0].get("id") or ""), "name": str(top[0].get("name") or "")}
    return None
