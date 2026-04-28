"""
regen_assets.py — 素材補跑腳本
================================
用途：當 main.py 因 Gemini 429 速率限制在中途中斷時，
      直接讀取今日已存在的 report + structured_data，
      只重跑後半段素材生成（Podcast、YouTube素材、Knowledge Wiki）。

執行方式：
  cd smart_financial_report
  python regen_assets.py

  # 若要補跑特定日期（預設為今天）：
  python regen_assets.py --date 2026-04-27
"""

import os
import sys
import json
import time
import glob
import datetime
import argparse
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PROJECT_THEME = os.getenv("PROJECT_THEME", "")

REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
YOUTUBE_ASSETS_DIR = os.path.join(REPORTS_DIR, "06_YouTube_Assets")
CHANNEL_STORY_LOG = os.path.join(YOUTUBE_ASSETS_DIR, "channel_story_log.md")

SLEEP_BETWEEN_CALLS = 8  # 秒，避免超過 15 RPM


def is_generation_error(content):
    return isinstance(content, str) and content.strip().startswith("Error generating")


def find_todays_files(date_str):
    """找今天的 report 和 structured_data"""
    report_path = os.path.join(REPORTS_DIR, f"daily_report_V21_{date_str}.md")
    json_path = os.path.join(REPORTS_DIR, f"structured_data_{date_str.replace('-', '')}.json")

    if not os.path.exists(report_path):
        print(f"❌ 找不到今日報告：{report_path}")
        return None, None

    if not os.path.exists(json_path):
        print(f"❌ 找不到今日結構化數據：{json_path}")
        return None, None

    return report_path, json_path


def main():
    parser = argparse.ArgumentParser(description="補跑今日素材（Podcast / YouTube / Knowledge Wiki）")
    parser.add_argument("--date", default=datetime.datetime.now().strftime("%Y-%m-%d"),
                        help="要補跑的日期，格式 YYYY-MM-DD（預設：今天）")
    parser.add_argument("--skip-podcast",   action="store_true", help="跳過 Podcast 腳本")
    parser.add_argument("--skip-youtube",   action="store_true", help="跳過 YouTube 素材")
    parser.add_argument("--skip-thumbnail", action="store_true", help="跳過縮圖生成")
    parser.add_argument("--skip-wiki",      action="store_true", help="跳過 Knowledge Wiki 更新")
    args = parser.parse_args()

    date_str = args.date
    print(f"\n{'='*50}")
    print(f"  素材補跑模式 (regen_assets.py)")
    print(f"  目標日期：{date_str}")
    print(f"{'='*50}\n")

    if not GEMINI_API_KEY:
        print("❌ 請在 .env 設定 GEMINI_API_KEY")
        sys.exit(1)

    # ── 讀取今日既有檔案 ──────────────────────────────
    report_path, json_path = find_todays_files(date_str)
    if not report_path:
        sys.exit(1)

    with open(report_path, "r", encoding="utf-8") as f:
        report_content = f.read()
    with open(json_path, "r", encoding="utf-8") as f:
        structured_data = json.load(f)

    print(f"✅ 已載入報告：{os.path.basename(report_path)} ({len(report_content):,} chars)")
    print(f"✅ 已載入結構化數據：{os.path.basename(json_path)}")

    # ── Step 1：Podcast 腳本 ──────────────────────────
    nl_filepath = os.path.join(REPORTS_DIR, f"notebooklm_prompt_podcast_{date_str.replace('-', '')}.md")
    if not args.skip_podcast:
        if os.path.exists(nl_filepath):
            print(f"\n⚠️  Podcast 腳本已存在，略過（若要強制重跑請刪除後再執行）")
            print(f"   {os.path.basename(nl_filepath)}")
        else:
            print(f"\n⏳ [速率限制保護] 等待 {SLEEP_BETWEEN_CALLS} 秒後呼叫 Gemini 生成 Podcast 腳本...")
            time.sleep(SLEEP_BETWEEN_CALLS)
            print("🎧 正在生成 NotebookLM Podcast 腳本指令...")
            from modules.notebooklm_generator import generate_notebooklm_prompt
            notebooklm_prompt_content = generate_notebooklm_prompt(
                GEMINI_API_KEY, structured_data, date_str, current_theme=PROJECT_THEME
            )
            if is_generation_error(notebooklm_prompt_content):
                print(f"  ⚠️  Podcast 腳本生成失敗：{notebooklm_prompt_content.splitlines()[0]}")
            elif notebooklm_prompt_content:
                with open(nl_filepath, "w", encoding="utf-8") as f:
                    f.write(notebooklm_prompt_content)
                print(f"  ✅ Podcast 腳本已儲存：{os.path.basename(nl_filepath)}")
    else:
        print("\n⏭️  跳過 Podcast 腳本（--skip-podcast）")

    # ── Step 2：YouTube 素材 ──────────────────────────
    if not args.skip_youtube:
        from modules.youtube_content_generator import generate_youtube_package, save_youtube_package
        yt_asset_path = os.path.join(YOUTUBE_ASSETS_DIR, f"youtube_assets_{date_str.replace('-', '')}.md")
        if os.path.exists(yt_asset_path):
            print(f"\n⚠️  YouTube 素材已存在，略過")
            print(f"   {os.path.basename(yt_asset_path)}")
        else:
            print(f"\n⏳ [速率限制保護] 等待 {SLEEP_BETWEEN_CALLS} 秒後呼叫 Gemini 生成 YouTube 素材...")
            time.sleep(SLEEP_BETWEEN_CALLS)
            print("📢 正在生成 YouTube 上架素材（標題/描述/置頂留言）...")
            yt_package = generate_youtube_package(GEMINI_API_KEY, structured_data, report_content, date_str)
            if yt_package.get("titles"):
                yt_package_path = save_youtube_package(yt_package, YOUTUBE_ASSETS_DIR, date_str)
                print(f"  ✅ YouTube 素材包已儲存：{os.path.basename(yt_package_path)}")
                for i, title in enumerate(yt_package["titles"]):
                    print(f"     標題{'ABC'[i]}：{title}")
            else:
                print("  ⚠️  YouTube 素材生成失敗")
    else:
        print("\n⏭️  跳過 YouTube 素材（--skip-youtube）")

    # ── Step 3：縮圖 ─────────────────────────────────
    if not args.skip_thumbnail and OPENAI_API_KEY:
        print(f"\n⏳ [速率限制保護] 等待 {SLEEP_BETWEEN_CALLS} 秒後呼叫 Gemini 生成縮圖 Prompt...")
        time.sleep(SLEEP_BETWEEN_CALLS)
        print("🎨 正在用 DALL-E 3 生成 YouTube 縮圖背景...")
        from modules.thumbnail_generator import generate_ab_test_thumbnails, print_ab_test_summary
        try:
            ab_results = generate_ab_test_thumbnails(
                gemini_api_key=GEMINI_API_KEY,
                openai_api_key=OPENAI_API_KEY,
                report_content=report_content,
                reports_dir=YOUTUBE_ASSETS_DIR,
                styles=["dc_comics", "cyberpunk"],
                num_titles=3,
            )
            print_ab_test_summary(ab_results)
        except Exception as e:
            print(f"  ⚠️  縮圖生成失敗，略過：{e}")
    elif args.skip_thumbnail:
        print("\n⏭️  跳過縮圖生成（--skip-thumbnail）")
    else:
        print("\n⚠️  未設定 OPENAI_API_KEY，跳過縮圖生成。")

    # ── Step 4：Knowledge Wiki ────────────────────────
    if not args.skip_wiki:
        print(f"\n⏳ [速率限制保護] 等待 {SLEEP_BETWEEN_CALLS} 秒後呼叫 Gemini 更新 Knowledge Wiki...")
        time.sleep(SLEEP_BETWEEN_CALLS)
        print("🧠 正在更新 Knowledge Wiki...")
        from modules.knowledge_manager import KnowledgeUpdater
        knowledge_updater = KnowledgeUpdater(GEMINI_API_KEY)
        knowledge_updater.update_knowledge_base(report_content)
    else:
        print("\n⏭️  跳過 Knowledge Wiki（--skip-wiki）")

    print(f"\n{'='*50}")
    print("  ✅ 素材補跑完成！")
    print(f"  📄 報告來源：{os.path.basename(report_path)}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
