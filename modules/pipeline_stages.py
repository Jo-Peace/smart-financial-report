def build_dynamic_universe(base_symbols, base_topics, institutional_data, yesterday_predictions):
    dynamic_symbols = list(base_symbols or [])
    dynamic_topics = list(base_topics or [])

    if institutional_data:
        for item in (institutional_data.get("top_buy", []) or [])[:3]:
            symbol_yf = f"{item['id']}.TW"
            if symbol_yf not in dynamic_symbols:
                dynamic_symbols.append(symbol_yf)
                dynamic_topics.append(f"Taiwan stock {item['id']} {item['name']} 外資買超 原因")

        for item in (institutional_data.get("top_sell", []) or [])[:3]:
            symbol_yf = f"{item['id']}.TW"
            if symbol_yf not in dynamic_symbols:
                dynamic_symbols.append(symbol_yf)
                dynamic_topics.append(f"Taiwan stock {item['id']} {item['name']} 外資賣超 原因")

    if yesterday_predictions:
        for pred in yesterday_predictions:
            pred_id = pred.get("id")
            if not pred_id:
                continue
            pred_symbol = f"{pred_id}.TW"
            if pred_symbol not in dynamic_symbols:
                dynamic_symbols.append(pred_symbol)

    return dynamic_symbols, dynamic_topics


def collect_news_bundle(fetcher, expand_news, dynamic_topics, supply_chain_event_radar, unwrap_fetch_result, print_fn=print):
    news_data = []
    if expand_news:
        priority_topics = list(dynamic_topics[:4])
        if supply_chain_event_radar:
            priority_topics.insert(0, supply_chain_event_radar[0]["event"])

        for topic in priority_topics[:5]:
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
