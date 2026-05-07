"""
YouTube Content Package Generator
Generates CTR-optimized titles, description, hashtags, and pinned comment
for Taiwan stock analysis YouTube videos.
"""

import os
import datetime
import google.generativeai as genai


def generate_youtube_package(gemini_api_key, structured_data, report_content, date_str, packaging_context=None):
    """
    Generates a complete YouTube upload package using Gemini.

    Returns dict with:
        titles         - list of 2 title variants (A/B)
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
    packaging_context = packaging_context or {}
    validation_summary = packaging_context.get("validation_summary", "")
    analytics_summary = packaging_context.get("analytics_summary", "")
    channel_story = packaging_context.get("channel_story", "")
    title_packaging_rules = packaging_context.get("title_packaging_rules", "")
    local_market_memory = packaging_context.get("local_market_memory", "")
    stock_role_taxonomy = packaging_context.get("stock_role_taxonomy", "")
    audience_profile = packaging_context.get("audience_profile", "")

    prompt = f"""你是一位專業的 YouTube 頻道成長策略師，深度熟悉台灣股市投資者的行為心理與 YouTube 演算法。

===今日核心數據（必須用這些數據，不得捏造）===
大盤狀況：{index_action}
AI觀察焦點：{', '.join(ai_picks) if ai_picks else '無明確標的'}
法人買超或強勢板塊：{', '.join(safe_havens) if safe_havens else '分散'}
法人賣超或弱勢標的：{', '.join(heavyweights_dumped) if heavyweights_dumped else '無明顯'}
量價背離現象：{', '.join(price_divergence) if price_divergence else '無明顯背離'}
穩健策略：{conservative}
波段策略：{aggressive}

===本機正式驗收結果（若有，標題可使用「驗收 / 上集點名」連續劇鉤子）===
{validation_summary if validation_summary else '（無本機驗收資料）'}

===近期頻道敘事與觀看數（用來避免重複、延續有效敘事）===
{channel_story if channel_story else '（無頻道敘事紀錄）'}

===近期 Analytics / YouTube 學習（900 views 以上才是有效樣本）===
{analytics_summary if analytics_summary else '（無 Analytics 摘要）'}

===本頻道觀眾偏好與包裝規則===
{audience_profile if audience_profile else '觀眾偏好：具體點名個股、外資買賣金額、驗收結果、情境推演；避免只有抽象族群。'}

{title_packaging_rules if title_packaging_rules else ''}

===本機市場記憶與個股角色分類（用來判斷誰適合當標題主角）===
{local_market_memory if local_market_memory else '（無本機市場記憶）'}

{stock_role_taxonomy if stock_role_taxonomy else '（無個股角色分類規則）'}

===今日報告開場段落（用來抓取核心鉤子）===
{report_content[:1000]}

===標題撰寫規則（最關鍵）===
只生成兩個標題，方便累積乾淨的 A/B Test：
- 標題A：主推高勝率公式。優先檢查今日是否有重大「時事槓桿」；若有，標題A必須從時事切入，再連到台股風險/機會。若沒有時事槓桿，再檢查是否可呼應上集或昨日預測；最後才使用「大盤反差 + 核心股外資金額 + 隱藏買盤標的」。
  範例格式：「上集說XX是地雷，但外資今天砸了XX億進去！這是接刀還是提前卡位？｜{short_date} AI帶你看股市」
- 標題B：探索替換版。使用不同角度的好奇心缺口或數據衝擊，作為 48 小時後可替換版本。
  範例格式：「XX億！外資狂砍權值股，卻反手鎖定這兩檔記憶體｜{short_date} AI帶你看股市」

===已驗證較有效的頻道標題公式（高權重參考）===
學習門檻：觀看數低於 900 的影片只能視為觀察樣本，不可作為 A/B Test 勝負依據。只有 900 觀看以上的案例，才視為可強化的標題/封面公式。

近期表現較好的標題使用了這種結構：
「台股大漲/大跌卻暗藏殺機？台積電大漲但外資狂砍XX億！主力反手鎖定『這檔』記憶體股｜AI帶你看股市」

另一個表現較好的標題使用了「上集驗收 / 連續劇」結構：
「上集說XX是地雷，但外資今天砸了XX億進去！股價卻繼續跌——這是接刀還是提前卡位？」

另一個高觀看樣本使用了「節假日後風險預警」結構：
「228連假後台股崩盤預警!?」

目前最高觀看樣本使用了「地緣政治 / 美股血洗 / 台股風險傳導」結構：
「戰火引爆美股血洗！台積電 ADR 暴跌 9.5% 週一台股開盤面臨斷頭危機？資金全逃去哪了！」

請優先模仿這個邏輯，而不是照抄文字：
1. 先判斷今天有沒有重大時事槓桿：戰爭、美股暴跌、台積電 ADR、輝達、VIX、Fed、匯率、關稅、長假、重大財報、台指期夜盤。
2. 若有時事槓桿，標題A順序為：時事事件 → 台股會怎樣 → 核心標的/數字 → 散戶該怕什麼或看什麼。
3. 若沒有時事槓桿，再給大盤表面現象：狂飆、重挫、翻紅、收黑。
4. 馬上接反差：卻暗藏殺機、法人動作不對勁、散戶可能看錯。
5. 放入最有辨識度的大型股或核心標的，例如台積電，並附上真實外資買賣超金額。
6. 最後給觀眾一個想點進來的答案缺口：這檔、這兩檔、記憶體、AI伺服器、ETF 等具體題材。
7. 不要只寫「資金輪動」。若要表達資金移動，請寫成具體行為：外資砍台積電、投信買超、三大法人反手買、成交量爆出異常。
8. 若今日資料能呼應昨日或上集的預測，標題可使用「上集說過 / 昨天點名 / 真的驗收」結構，讓觀眾感覺這不是單集資訊，而是連續追蹤。
9. 連續劇標題要有明確驗收點：上集說了什麼、今天發生什麼、外資買賣多少、股價是否反著走。
10. 若今天接近長假、休市、重大總經事件或連假後開盤，標題A可使用「節假日後風險預警」結構，但必須搭配具體籌碼、期貨、美股或權值股風險證據。
11. 若美股、VIX、台積電 ADR、輝達、油金、地緣政治出現重大異常，標題A可使用「全球風險傳導到台股」結構：全球觸發事件 + 台灣核心資產跌幅 + 下一交易日風險 + 投資人該追問的問題。

標題硬規則：
1. 每個標題 35-60 字（含符號）；若今日數據很強，可以略長，但前半段必須完整有鉤子
2. 前 22 字是黃金區——必須出現大盤反差、核心股、或外資金額之一
3. 末尾固定加「｜AI帶你看股市」作為品牌識別
4. 數字必須來自今日真實數據，不得捏造
5. 繁體中文，禁止「穩了」「嗨了」「躺平」等中國大陸流行語
6. 可加 1-2 個 emoji，放最前面或最後面（末尾品牌標語前）
7. 不要使用「資金輪動」；標題要講具體事件，例如三大法人買賣超、成交量、量價背離。
8. 標題必須至少點名 1 檔具體股票或 1 個具體族群；若標題點名族群，描述第一行或第二行必須補上個股。
9. 若使用「外資已買 / 偷偷買 / 掃貨」等語氣，必須在標題或描述中放入實際金額或正式資料依據。
10. 若核心股是權值大股但彈性有限，標題應區分「看方向」與「看彈性」，不要把權值核心包裝成最大飆股。

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
###DESCRIPTION
（完整描述，包含所有段落和hashtag）
###PINNED_COMMENT
（置頂留言）
###END
"""

    try:
        response = model.generate_content(prompt)
        output = response.text.strip()

        sections = {"TITLE_A": [], "TITLE_B": [], "DESCRIPTION": [], "PINNED_COMMENT": []}
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

    lines.append("## 📝 A/B 標題選項（先用 A，48 小時後再判斷是否換 B）\n")
    labels = ["A（主推高勝率公式）", "B（探索替換版）"]
    for i, title in enumerate(result.get("titles", [])[:2]):
        label = labels[i] if i < len(labels) else str(i + 1)
        lines.append(f"**標題 {label}：**\n```\n{title}\n```\n")

    lines.append("---\n\n## 📄 影片描述\n\n```")
    lines.append(result.get("description", "（未生成）"))
    lines.append("```\n")

    lines.append("---\n\n## 💬 置頂留言（上傳後10分鐘內貼上效果最好）\n\n```")
    lines.append(result.get("pinned_comment", "（未生成）"))
    lines.append("```\n")

    lines.append("---\n\n## 📌 上架流程提醒\n")
    lines.append("1. 先用標題 A 上傳")
    lines.append("2. 在 Canva 打開縮圖背景，加上今日標題文字")
    lines.append("3. 上傳影片，貼上描述和縮圖")
    lines.append("4. 發布後立即貼置頂留言")
    lines.append("5. 觀看數低於 900 不判斷 A/B 勝負，只記錄觀察")
    lines.append("6. 48小時後若 views >= 900 且 CTR < 5%，再測 B 組\n")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return filepath
