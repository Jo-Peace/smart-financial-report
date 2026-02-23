"""
YouTube Thumbnail Generator with A/B Testing Support.
Uses Gemini's native image generation with 16:9 aspect ratio.
Generates multiple style variants and title options for A/B testing.
"""
import os
import time
import datetime
from google import genai
from google.genai import types


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

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[prompt],
    )
    
    titles = [line.strip() for line in response.text.strip().split("\n") if line.strip()]
    return titles[:num_titles]


def generate_thumbnail(client, style_key, title, date_str, output_path, max_retries=3):
    """
    Generate a single YouTube thumbnail using Gemini native image generation.
    Includes retry logic with backoff for rate limit handling.
    Returns True if successful, False otherwise.
    """
    style = STYLE_PRESETS.get(style_key)
    if not style:
        print(f"  [Error] 未知的風格: {style_key}")
        return False

    image_prompt = f"""
    Generate a YouTube thumbnail image in wide landscape 16:9 format.

    Visual Style: {style['prompt']}

    Text overlay requirements (MUST include these Chinese characters prominently):
    - Main title: "{title}" in very large, bold font with strong outline and shadow
    - Date: "{date_str}" in a banner or badge in the corner
    
    The text must be clearly readable, very large, and eye-catching.
    This is a YouTube thumbnail so it needs to grab attention even at small sizes.
    Professional quality, no human faces, focus on financial/stock market theme.
    """

    wait_times = [30, 45, 60]

    for attempt in range(max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash-image",
                contents=[image_prompt],
                config=types.GenerateContentConfig(
                    response_modalities=["Image"],
                    image_config=types.ImageConfig(
                        aspect_ratio="16:9",
                    ),
                ),
            )

            for part in response.parts:
                if part.inline_data is not None:
                    image = part.as_image()
                    image.save(output_path)
                    return True

            print(f"     [Warning] Gemini 未返回圖片")
            return False

        except Exception as e:
            error_str = str(e)
            is_rate_limit = "429" in error_str or "RESOURCE_EXHAUSTED" in error_str

            if is_rate_limit and attempt < max_retries:
                wait = wait_times[min(attempt, len(wait_times) - 1)]
                print(f"     [Retry] API 速率限制，等待 {wait} 秒後重試 ({attempt + 1}/{max_retries})...")
                time.sleep(wait)
            else:
                print(f"     [Error] 圖片生成失敗: {e}")
                return False


def generate_ab_test_thumbnails(api_key, report_content, reports_dir, 
                                 styles=None, num_titles=3):
    """
    Generate multiple thumbnails + titles for A/B testing.
    
    Args:
        api_key: Gemini API key
        report_content: The weekly report text (for title generation)
        reports_dir: Directory to save thumbnails
        styles: List of style keys to use. If None, uses all presets.
        num_titles: Number of title variations to generate.
    
    Returns:
        dict with 'titles' and 'thumbnails' lists
    """
    client = genai.Client(api_key=api_key)
    date_str = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%m/%d")
    date_full = datetime.datetime.now().strftime("%Y-%m-%d")

    if styles is None:
        styles = list(STYLE_PRESETS.keys())

    results = {"titles": [], "thumbnails": []}

    # === Step 1: Generate title variations ===
    print("\n🎯 正在生成 YouTube 標題變體（A/B Test）...")
    titles = generate_titles(client, report_content, num_titles)
    results["titles"] = titles
    for i, title in enumerate(titles):
        print(f"  📝 標題 {i+1}: {title}")

    # === Step 2: Generate thumbnails with different styles ===
    print(f"\n🎨 正在生成 {len(styles)} 種風格的 YouTube 縮圖（16:9）...")
    print(f"   （每張之間會間隔 15 秒以避免 API 速率限制）")

    for i, style_key in enumerate(styles):
        style_name = STYLE_PRESETS[style_key]["name"]
        # Pair each style with a title (cycle if more styles than titles)
        title = titles[i % len(titles)]
        short_title = title[:15] + "..." if len(title) > 15 else title

        filename = f"yt_thumbnail_{date_full}_{style_key}.png"
        output_path = os.path.join(reports_dir, filename)

        # Wait between requests to avoid rate limiting
        if i > 0:
            print(f"\n     ⏳ 等待 15 秒避免速率限制...")
            time.sleep(15)

        print(f"\n  🖼️  風格 {i+1}/{len(styles)}: {style_name}")
        print(f"     標題: {short_title}")
        print(f"     生成中...")

        success = generate_thumbnail(client, style_key, title, date_str, output_path)

        if success:
            print(f"     ✅ 已儲存: {filename}")
            results["thumbnails"].append({
                "style": style_name,
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
