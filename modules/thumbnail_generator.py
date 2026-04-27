"""
YouTube Thumbnail Generator with A/B Testing Support.
Uses DALL-E 3 (OpenAI) for image generation and Gemini for title generation.
Generates multiple style variants and title options for A/B testing.
"""
import os
import base64
import time
import datetime
import requests as _requests
from google import genai
from google.genai import types
from openai import OpenAI


# === Visual Style Presets ===
STYLE_PRESETS = {
    "dc_comics": {
        "name": "DC 美漫風",
        "prompt": (
            "DC American comic book art style with bold black ink outlines, halftone dot shading, "
            "dramatic cinematic lighting. A powerful charging bull with golden horns surrounded by "
            "explosive energy and flying stock chart papers. Wall Street skyscrapers and NYSE building "
            "in dark navy blue background with lightning bolts. "
            "Color scheme: deep navy blue, metallic gold, crimson red, emerald green. "
            "High contrast, dramatic shadows, comic book panel borders. "
            "Think 'The Dark Knight meets Wall Street'."
        ),
    },
    "cyberpunk": {
        "name": "賽博龐克科技風",
        "prompt": (
            "Cyberpunk futuristic style with neon glow effects, holographic stock charts floating "
            "in the air, dark city skyline with glowing circuit board patterns. A digital bull made of "
            "blue neon light and data streams charging through a futuristic trading floor. "
            "Color scheme: deep black, electric blue (#00d4ff), hot pink/magenta (#ff00ff), "
            "neon green accents. Glitch effects, scan lines, holographic UI elements. "
            "Blade Runner meets Wall Street aesthetic."
        ),
    },
    "epic_cinematic": {
        "name": "史詩電影風",
        "prompt": (
            "Epic cinematic movie poster style with dramatic volumetric lighting and lens flare. "
            "A golden bull statue on top of a mountain of gold coins, with stock market charts rising "
            "like aurora borealis in the sky behind it. Storm clouds parting to reveal golden sunlight. "
            "Color scheme: deep black, warm gold, amber, with cool blue shadows. "
            "Ultra dramatic lighting like a Marvel movie poster. Premium, luxurious, powerful."
        ),
    },
    "chen_uen_ink": {
        "name": "鄭問水墨風",
        "prompt": (
            "Chen Uen (鄭問) Taiwanese ink wash comic art style. Bold black Chinese ink brush strokes "
            "with dramatic ink splatter and wash effects. A majestic war horse (馬) charging forward "
            "through splashing ink and gold paint, reminiscent of classical Chinese warrior paintings. "
            "Stock market candlestick charts rendered as ink brush strokes rising behind the horse. "
            "Traditional Chinese seal stamps (印章) as accent elements. "
            "Color palette: black ink, vermillion red (朱紅), burnished gold leaf, rice paper white. "
            "Dramatic composition with explosive energy, combining Eastern calligraphic art with "
            "modern financial imagery. Premium, powerful, uniquely Taiwanese aesthetic."
        ),
    },
}


def generate_titles(client, report_content, num_titles=3):
    """
    Use Gemini to generate multiple YouTube title variations for A/B testing.
    """
    prompt = f"""
    你是一位專業的 YouTube 財經頻道標題撰寫專家。
    根據以下影片文案內容，生成 {num_titles} 個不同風格的 YouTube 標題。

    文案摘要：
    {report_content[:1500]}

    要求：
    1. 每個標題要有不同的「鉤子」策略（好奇心、緊迫感、數據驅動等）
    2. 標題長度控制在 25-35 個中文字以內
    3. 必須包含日期（明天的日期）
    4. 使用 emoji 增加點擊率
    5. 針對台灣投資人

    請直接輸出標題，每行一個，不要編號，不要其他說明文字。
    """

    model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    response = client.models.generate_content(
        model=model_name,
        contents=[prompt],
    )
    
    titles = [line.strip() for line in response.text.strip().split("\n") if line.strip()]
    return titles[:num_titles]


def _build_image_prompt(style_key_or_custom, title):
    """
    Returns the final image prompt string.
    - If style_key_or_custom matches a STYLE_PRESETS key → use that preset.
    - Otherwise treat it as a free-form custom style description (daily override).
    """
    preset = STYLE_PRESETS.get(style_key_or_custom)
    visual_desc = preset["prompt"] if preset else style_key_or_custom

    return (
        "YouTube thumbnail background image, landscape 16:9 format. "
        f"{visual_desc} "
        "Leave a bold clear area at the bottom 25% of the image for text overlay. "
        f"The composition should visually suggest the theme: '{title}'. "
        "No text, no letters, no characters in the image — pure visual only. "
        "Ultra high quality, dramatic, eye-catching at small sizes."
    )


def generate_thumbnail(openai_client, style_key_or_custom, title, date_str, output_path, max_retries=2):
    """
    Generate a single YouTube thumbnail using DALL-E 3 (OpenAI).
    style_key_or_custom: a STYLE_PRESETS key (e.g. "dc_comics") OR a free-form style description.
    Produces a clean visual background (1792x1024, ~16:9).
    Text overlay should be added separately in Canva or similar tools.
    Returns True if successful, False otherwise.
    """
    image_prompt = _build_image_prompt(style_key_or_custom, title)

    wait_times = [20, 40]

    for attempt in range(max_retries + 1):
        try:
            response = openai_client.images.generate(
                model="dall-e-3",
                prompt=image_prompt,
                size="1792x1024",
                quality="hd",
                n=1,
                response_format="b64_json",
            )
            image_data = base64.b64decode(response.data[0].b64_json)
            with open(output_path, "wb") as f:
                f.write(image_data)
            return True

        except Exception as e:
            error_str = str(e)
            is_rate_limit = "429" in error_str or "rate_limit" in error_str.lower()

            if is_rate_limit and attempt < max_retries:
                wait = wait_times[min(attempt, len(wait_times) - 1)]
                print(f"     [Retry] 速率限制，等待 {wait} 秒後重試 ({attempt + 1}/{max_retries})...")
                time.sleep(wait)
            else:
                print(f"     [Error] DALL-E 3 圖片生成失敗: {e}")
                return False


def generate_ab_test_thumbnails(gemini_api_key, openai_api_key, report_content, reports_dir,
                                 styles=None, num_titles=3):
    """
    Generate thumbnails (DALL-E 3) + titles (Gemini) for A/B testing.

    styles 可以是：
      - None              → 讀 THUMBNAIL_STYLE 環境變數；若沒設定則用預設兩種 preset
      - ["dc_comics"]     → 指定 STYLE_PRESETS 中的 key
      - ["午夜霓虹，紫金配色"] → 直接用作自訂 prompt（每天換風格就改這裡）

    Returns:
        dict with 'titles' and 'thumbnails' lists
    """
    gemini_client = genai.Client(api_key=gemini_api_key)
    openai_client = OpenAI(api_key=openai_api_key)

    date_str = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%m/%d")
    date_full = datetime.datetime.now().strftime("%Y-%m-%d")

    # 優先順序：函數參數 → THUMBNAIL_STYLE 環境變數 → 預設兩種 preset
    if styles is None:
        env_style = os.getenv("THUMBNAIL_STYLE", "").strip()
        if env_style:
            styles = [env_style]
            print(f"\n  🎨 使用今日自訂風格: {env_style[:60]}...")
        else:
            styles = ["dc_comics", "cyberpunk"]

    results = {"titles": [], "thumbnails": []}

    # === Step 1: Generate title variations with Gemini ===
    print("\n🎯 正在生成 YouTube 標題變體（A/B Test）...")
    titles = generate_titles(gemini_client, report_content, num_titles)
    results["titles"] = titles
    for i, title in enumerate(titles):
        print(f"  📝 標題 {i+1}: {title}")

    # === Step 2: Generate thumbnails with DALL-E 3 ===
    print(f"\n🎨 正在用 DALL-E 3 生成 {len(styles)} 張縮圖背景（1792x1024）...")

    for i, style in enumerate(styles):
        # 判斷是 preset key 還是自訂描述
        preset = STYLE_PRESETS.get(style)
        style_label = preset["name"] if preset else f"自訂風格 {i+1}"

        title = titles[i % len(titles)] if titles else "台股今日分析"
        short_title = title[:20] + "..." if len(title) > 20 else title

        filename = f"yt_thumbnail_{date_full}_{i+1}.png"
        output_path = os.path.join(reports_dir, filename)

        if i > 0:
            print(f"\n     ⏳ 等待 8 秒...")
            time.sleep(8)

        print(f"\n  🖼️  縮圖 {i+1}/{len(styles)}: {style_label}")
        print(f"     主題方向: {short_title}")

        success = generate_thumbnail(openai_client, style, title, date_str, output_path)

        if success:
            print(f"     ✅ 已儲存: {filename}（請在 Canva 加上標題文字）")
            results["thumbnails"].append({
                "style": style_label,
                "title": title,
                "path": output_path,
            })
        else:
            print(f"     ❌ 生成失敗")

    return results


def print_ab_test_summary(results):
    """Print a formatted summary of A/B test options."""
    print(f"\n{'='*60}")
    print(f"  📊 A/B Test 素材總覽")
    print(f"{'='*60}")

    print("\n  📝 標題選項：")
    for i, title in enumerate(results["titles"]):
        print(f"     {chr(65+i)}. {title}")

    print(f"\n  🖼️  縮圖選項：")
    for i, thumb in enumerate(results["thumbnails"]):
        print(f"     {chr(65+i)}. [{thumb['style']}] {os.path.basename(thumb['path'])}")
        print(f"        搭配標題: {thumb['title'][:30]}...")

    print(f"\n  💡 建議：選擇 1 個標題 + 1 張縮圖上傳，")
    print(f"     48 小時後根據 CTR 數據決定是否替換。")
    print(f"{'='*60}\n")
