"""
YouTube Content Package Generator
Generates CTR-optimized titles, description, hashtags, and pinned comment
for Taiwan stock analysis YouTube videos.
"""

import os
import datetime
import google.generativeai as genai


def generate_youtube_package(gemini_api_key, structured_data, report_content, date_str):
    """
    Generates a complete YouTube upload package using Gemini.

    Returns dict with:
        titles         - list of 3 title variants (A/B/C)
        description    - full SEO-optimized description
        pinned_comment - pinned comment template
    """
    genai.configure(api_key=gemini_api_key)
    model_name = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    model = genai.GenerativeModel(model_name)

    date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    short_date = f"{date_obj.month}/{date_obj.day}"  # e.g. "4/23"

    ai_picks = structured_data.get("ai_data_picks", [])
    index_action = structured_data.get("index_action", "")
    safe_havens = structured_data.get("safe_havens_bought", [])
    heavyweights_dumped = structured_data.get("heavyweights_dumped", [])
    price_divergence = structured_data.get("price_volume_divergence", [])
    conservative = structured_data.get("conservative_strategy", "")
    aggressive = structured_data.get("aggressive_strategy", "")

    prompt = f"""你是一位專業的 YouTube 頻道成長策略師，深度熟悉台灣股市投資者的行為心理與 YouTube 演算法。

===今日核心數據（必須用這些數據，不得捏造）===
大盤狀況：{index_action}
AI觀察焦點：{', '.join(ai_picks) if ai_picks else '無明確標的'}
法人買超或強勢板塊：{', '.join(safe_havens) if safe_havens else '分散'}
法人賣超或弱勢標的：{', '.join(heavyweights_dumped) if heavyweights_dumped else '無明顯'}
量價背離現象：{', '.join(price_divergence) if price_divergence else '無明顯背離'}
穩健策略：{conservative}
波段策略：{aggressive}

===今日報告開場段落（用來抓取核心鉤子）===
{report_content[:1000]}

===標題撰寫規則（最關鍵）===
生成三個標題，各用不同心理鉤子：
- 標題A：好奇心缺口型（製造「咦？這怎麼可能？」的疑問，讓人必須點進去看答案）
  範例格式：「外資狂買XX億，這支股票卻跌了？背後原因出乎意料｜{short_date} AI帶你看股市」
- 標題B：數據衝擊型（用最震撼的具體數字開頭，前15字必須有數字）
  範例格式：「XX億！外資今天創歷史最大買超 卻沒人知道為什麼｜{short_date} AI帶你看股市」
- 標題C：利益相關型（直接戳中持股者的焦慮或機會感）
  範例格式：「持有這類股的注意！外資今天悄悄在做一件事｜{short_date} AI帶你看股市」

標題硬規則：
1. 每個標題 28-40 字（含符號）
2. 前 15 字是黃金區——最有吸引力的詞必須在最前面
3. 末尾固定加「｜AI帶你看股市」作為品牌識別
4. 數字必須來自今日真實數據，不得捏造
5. 繁體中文，禁止「穩了」「嗨了」「躺平」等中國大陸流行語
6. 可加 1-2 個 emoji，放最前面或最後面（末尾品牌標語前）
7. 不要使用「資金輪動」；標題要講具體事件，例如三大法人買賣超、成交量、量價背離。

===描述撰寫規則===
第1行（≤55字）：今日最強異常現象 + 核心數字 + 日期（包含「台股」）
第2行（≤55字）：AI觀察焦點 + 對散戶的實際意義
第3行：訂閱召喚 + 開啟通知提示

空一行，加章節標記：
00:00 昨日預測驗收（說到做到）
01:30 今日最強異常現象
04:00 成交量排名完整解析
07:00 外資法人籌碼深挖
10:00 明日觀察標的 + 停損設定
12:00 操作建議

空一行，加免責聲明（繁體中文，一行）：
⚠️ 本節目所有內容僅供參考，不構成任何投資建議，請務必自行判斷風險。

空一行，加 hashtag（6-8個，最相關的放前面）：
#台股 #台股分析 #AI選股 等

===置頂留言規則===
60-90 字，格式：
1. 拋出一個今日盤面相關的開放性問題
2. 邀請觀眾分享自己的持股或看法
3. 說明留言區的用途（博士和阿明會看）
4. 加 2-3 個相關 emoji

===輸出格式（嚴格遵守，不得添加任何說明文字或markdown標記）===

###TITLE_A
（標題A）
###TITLE_B
（標題B）
###TITLE_C
（標題C）
###DESCRIPTION
（完整描述，包含所有段落和hashtag）
###PINNED_COMMENT
（置頂留言）
###END
"""

    try:
        response = model.generate_content(prompt)
        output = response.text.strip()

        sections = {"TITLE_A": [], "TITLE_B": [], "TITLE_C": [], "DESCRIPTION": [], "PINNED_COMMENT": []}
        current = None

        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith("###END"):
                if current and sections.get(current) is not None:
                    pass
                break
            if stripped.startswith("###"):
                key = stripped.replace("###", "").strip()
                current = key if key in sections else None
                continue
            if current is not None:
                sections[current].append(line)

        result = {
            "titles": [
                "\n".join(sections["TITLE_A"]).strip(),
                "\n".join(sections["TITLE_B"]).strip(),
                "\n".join(sections["TITLE_C"]).strip(),
            ],
            "description": "\n".join(sections["DESCRIPTION"]).strip(),
            "pinned_comment": "\n".join(sections["PINNED_COMMENT"]).strip(),
        }
        result["titles"] = [t for t in result["titles"] if t]
        return result

    except Exception as e:
        print(f"  [Error] YouTube 內容生成失敗: {e}")
        return {"titles": [], "description": "", "pinned_comment": ""}


def save_youtube_package(result, output_dir, date_str):
    """Save the YouTube package to a markdown file and return the filepath."""
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, f"youtube_package_{date_str}.md")

    lines = [f"# YouTube 上架素材包 {date_str}\n"]

    lines.append("## 📝 標題選項（選一個上傳，48小時後看數據再決定換不換）\n")
    labels = ["A（好奇心缺口）", "B（數據衝擊）", "C（利益相關）"]
    for i, title in enumerate(result.get("titles", [])):
        label = labels[i] if i < len(labels) else str(i + 1)
        lines.append(f"**標題 {label}：**\n```\n{title}\n```\n")

    lines.append("---\n\n## 📄 影片描述\n\n```")
    lines.append(result.get("description", "（未生成）"))
    lines.append("```\n")

    lines.append("---\n\n## 💬 置頂留言（上傳後10分鐘內貼上效果最好）\n\n```")
    lines.append(result.get("pinned_comment", "（未生成）"))
    lines.append("```\n")

    lines.append("---\n\n## 📌 上架流程提醒\n")
    lines.append("1. 選一個標題上傳（建議先用標題A）")
    lines.append("2. 在 Canva 打開縮圖背景，加上今日標題文字")
    lines.append("3. 上傳影片，貼上描述和縮圖")
    lines.append("4. 發布後立即貼置頂留言")
    lines.append("5. 48小時後檢查 CTR：< 3% 換標題，< 5% 換縮圖\n")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return filepath
