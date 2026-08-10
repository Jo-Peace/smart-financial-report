import datetime
import os
import statistics


def _parse_views(value):
    text = str(value or "").strip()
    if text in {"", "-", "待更新"}:
        return None
    try:
        return int(text.replace(",", ""))
    except ValueError:
        return None


def _parse_channel_story_rows(log_path):
    if not os.path.exists(log_path):
        return []

    rows = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.startswith("| ") or "---" in line or "日期" in line:
                continue
            parts = [part.strip() for part in line.strip().strip("|").split("|")]
            if len(parts) < 9:
                continue
            views = _parse_views(parts[8])
            if views is None:
                continue
            rows.append(
                {
                    "date": parts[0],
                    "target": parts[1],
                    "prediction": parts[2],
                    "visual_style": parts[4],
                    "story_direction": parts[5],
                    "video_id": parts[6],
                    "title": parts[7],
                    "views": views,
                }
            )
    return rows


def _contains_any(text, keywords):
    text = str(text or "")
    return any(keyword in text for keyword in keywords)


def _infer_lessons(top_rows, low_rows):
    lessons = []
    top_titles = " ".join(row["title"] for row in top_rows)
    low_titles = " ".join(row["title"] for row in low_rows)

    if _contains_any(top_titles, ["換車", "接棒", "轉向"]):
        lessons.append("高觀看樣本偶爾偏好「資金是否換方向」的問題感，但不可連續多日重複同一問法；要保留金額與個股，改問逆勢原因或量價矛盾。")
    if _contains_any(top_titles, ["崩盤", "預警", "風險"]):
        lessons.append("風險型標題有效，但要搭配可檢查的數據或下一步，而不是只製造恐懼。")
    if _contains_any(top_titles, ["外資", "億"]):
        lessons.append("標題前半段放入外資具體金額與個股，通常比抽象族群描述更有辨識度。")
    if _contains_any(top_titles, ["真的", "嗎", "？", "?"]):
        lessons.append("問句型標題能提高好奇心，尤其是「資金真的換車了？」這種能被影片回答的問題。")

    if _contains_any(low_titles, ["驗收"]):
        lessons.append("只用「驗收」當主鉤子容易疲乏；驗收要服務更大的市場推測。")
    if _contains_any(low_titles, ["影子建倉", "資金輪動"]):
        lessons.append("影子建倉、資金輪動類詞彙已高頻使用，應改寫成具體行為：誰砍、誰買、誰接棒。")
    if _contains_any(low_titles, ["台股盤後"]) and not _contains_any(low_titles, ["？", "?"]):
        lessons.append("純盤後資訊標題缺少懸念，應補上明日劇本或資金方向問題。")

    if not lessons:
        lessons.append("目前樣本仍少，優先採用具體個股 + 外資金額 + 明確問題的穩健公式。")
    return lessons


def build_youtube_performance_summary(log_path, recent_limit=30, top_n=8, low_n=6):
    rows = _parse_channel_story_rows(log_path)
    if not rows:
        return "# YouTube Performance Patterns\n\n尚無可分析的觀看數資料。\n"

    recent = rows[-recent_limit:]
    views = [row["views"] for row in recent]
    median_views = statistics.median(views)
    avg_views = sum(views) / len(views)
    top_rows = sorted(recent, key=lambda row: row["views"], reverse=True)[:top_n]
    low_rows = sorted(recent, key=lambda row: row["views"])[:low_n]
    lessons = _infer_lessons(top_rows, low_rows)

    lines = [
        "# YouTube Performance Patterns",
        "",
        f"更新時間：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "用途：這份檔案是 `/2` 生成 YouTube 標題、封面與上架包前的成效學習摘要。它不是原始資料庫，而是從 `channel_story_log.md` 提煉出的可執行規則。",
        "",
        "## 近期基準",
        "",
        f"- 分析樣本：最近 {len(recent)} 支有觀看數紀錄的影片",
        f"- 中位數觀看數：{median_views:.0f}",
        f"- 平均觀看數：{avg_views:.1f}",
        "",
        "## 高觀看樣本",
        "",
    ]
    for row in top_rows:
        lines.append(f"- {row['date']}｜{row['views']:,} views｜{row['title']}｜視覺：{row['visual_style']}")

    lines.extend(["", "## 低觀看樣本", ""])
    for row in low_rows:
        lines.append(f"- {row['date']}｜{row['views']:,} views｜{row['title']}｜視覺：{row['visual_style']}")

    lines.extend(["", "## 可執行學習", ""])
    for lesson in lessons:
        lines.append(f"- {lesson}")

    lines.extend(
        [
            "",
            "## /2 包裝規則",
            "",
            "- 標題不要只回報今日數字，要提出一個可被影片回答的市場問題。",
            "- 優先使用「具體資金動作 + 觀眾關心的風險 + 明確推測問題」。",
            "- 封面主字優先做可被影片回答的問題牌，但需輪替題型：逆勢原因、量價矛盾、驗收現場、事件衝擊、明日失效價位；不要連續使用『榜外吸籌 / 被砍 / 接棒』組合。",
            "- 若近 5 天已出現相同 scenario 或封面大字，今日必須換另一種家族（例如驗收、逆勢、事件、天量陷阱）。",
            "- 數據牌可以當副標，不要讓封面只剩股票代號與買超金額。",
            "- 恐懼標題必須提供出口：誰可能受惠、明天看哪個驗收點、失效條件是什麼。",
            "- 若沒有強訊號，承認訊號普通，改用等待確認與明日觀察條件，不硬做警報。",
            "",
        ]
    )
    return "\n".join(lines)


def update_youtube_performance_wiki(log_path, output_path):
    content = build_youtube_performance_summary(log_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    return output_path
