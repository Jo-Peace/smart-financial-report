GENERIC_TOPIC_MARKERS = (
    "taiwan semiconductor",
    "今日強勢族群",
    "量價背離",
)


def _is_generic_news_topic(topic: str) -> bool:
    low = str(topic or "").lower()
    return any(marker in low for marker in GENERIC_TOPIC_MARKERS)


def build_dynamic_universe(base_symbols, base_topics, institutional_data, yesterday_predictions):
    dynamic_symbols = list(base_symbols or [])
    tape_topics = []

    if institutional_data:
        for item in (institutional_data.get("top_buy", []) or [])[:3]:
            symbol_yf = f"{item['id']}.TW"
            if symbol_yf not in dynamic_symbols:
                dynamic_symbols.append(symbol_yf)
            tape_topics.append(f"Taiwan stock {item['id']} {item['name']} 外資買超 原因")

        for item in (institutional_data.get("top_sell", []) or [])[:3]:
            symbol_yf = f"{item['id']}.TW"
            if symbol_yf not in dynamic_symbols:
                dynamic_symbols.append(symbol_yf)
            tape_topics.append(f"Taiwan stock {item['id']} {item['name']} 外資賣超 原因")

    if yesterday_predictions:
        for pred in yesterday_predictions:
            pred_id = pred.get("id")
            if not pred_id:
                continue
            pred_symbol = f"{pred_id}.TW"
            if pred_symbol not in dynamic_symbols:
                dynamic_symbols.append(pred_symbol)

    generic = [str(t).strip() for t in (base_topics or []) if str(t).strip()]
    return dynamic_symbols, tape_topics + generic


def tape_focus_names(institutional_data=None, volume_data=None, yesterday_predictions=None, limit=6):
    names = []
    for item in ((institutional_data or {}).get("top_buy") or [])[:3]:
        names.append(item.get("name") or item.get("id"))
    for item in ((institutional_data or {}).get("top_sell") or [])[:2]:
        names.append(item.get("name") or item.get("id"))
    for item in (volume_data or [])[:3]:
        names.append(item.get("name") or item.get("id"))
    for pred in yesterday_predictions or []:
        names.append(pred.get("name") or pred.get("id"))
    out = []
    seen = set()
    for name in names:
        text = str(name or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _radar_mentions_focus(event, focus_names):
    blob = f"{(event or {}).get('event', '')} {(event or {}).get('snippet', '')}".lower()
    for name in focus_names or []:
        token = str(name or "").strip().lower()
        if token and token in blob:
            return True
    return False


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

    for row in supply_chain_event_radar or []:
        if (row or {}).get("freshness") == "stale":
            continue
        if focus_names and not _radar_mentions_focus(row, focus_names):
            continue
        event = str((row or {}).get("event") or "").strip()
        if not event or event in topics:
            continue
        topics.append(event)
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
):
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

        for topic in priority_topics:
            query = f"{topic} market news today"
            results = unwrap_fetch_result(fetcher.get_news(query), default=[])
            if results:
                news_data.extend(results)
                if print_fn:
                    print_fn(f"  ✅ {topic}: 找到 {len(results)} 篇文章")
            elif print_fn:
                print_fn(f"  ⚠️  {topic}: 未找到文章")

    unique_news = list({n["url"]: n for n in news_data if n.get("url")}.values())

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
