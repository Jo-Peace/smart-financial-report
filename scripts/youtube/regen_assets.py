"""
regen_assets.py — 素材補跑 / /2 包裝腳本
=========================================
用途：
  1. 當 main.py 因 Gemini 429 速率限制在中途中斷時，直接讀取今日已存在的
     report + structured_data，補跑後半段素材。
  2. 作為 /2 快捷的執行入口：先由 /1 產出資料，再用本腳本讀取資料並套用
     當次指定的視覺主題，生成 YouTube package 與封面 Prompt。

執行方式：
  cd smart_financial_report
  python scripts/youtube/regen_assets.py

  # 若要補跑特定日期（預設為今天）：
  python scripts/youtube/regen_assets.py --date 2026-04-27

  # /2 常用：只產 YouTube package + 封面 Prompt，並指定視覺主題：
  python scripts/youtube/regen_assets.py --skip-podcast --skip-wiki --visual-theme "黑客帝國"
"""

import os
import sys
import json
import time
import glob
import datetime
import argparse
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv
from modules.ai_usage_guard import print_today_usage
from modules.current_index import write_current_index
from modules.project_skill_registry import format_skill_context, format_skill_summary, select_skills
from modules.report_paths import (
    daily_artifact_write_path,
    ensure_youtube_dir,
    resolve_daily_artifact,
    resolve_shared_artifact,
    resolve_youtube_artifact,
)
from modules.youtube_learning import update_youtube_performance_wiki

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PROJECT_THEME = os.getenv("PROJECT_THEME", "")
DAILY_VISUAL_THEME = os.getenv("DAILY_VISUAL_THEME", os.getenv("THUMBNAIL_STYLE", ""))

REPORTS_DIR = os.path.join(BASE_DIR, "reports")
CHANNEL_STORY_LOG = resolve_shared_artifact(REPORTS_DIR, "channel_story_log")
YOUTUBE_PERFORMANCE_WIKI = os.path.join(BASE_DIR, "data", "knowledge_wiki", "youtube_performance_patterns.md")

SLEEP_BETWEEN_CALLS = 8  # 秒，避免超過 15 RPM


def _read_tail(path, max_chars=5000):
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return content[-max_chars:]
    except Exception:
        return ""


def build_packaging_context(structured_data):
    from modules.editorial_frame import (
        build_editorial_frame,
        build_title_fatigue_report,
        format_editorial_frame_for_prompt,
    )
    from modules.event_radar import format_event_radar_for_prompt
    from modules.market_calendar import packaging_calendar_block

    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    skill_context = ""
    try:
        selected_skills = select_skills(
            [
                "youtube",
                "thumbnail",
                "visual",
                "transcript",
                "youtube_learning",
                "content_research",
            ],
            base_dir=BASE_DIR,
        )
        skill_context = format_skill_context(selected_skills)
    except Exception:
        skill_context = ""
    title_rules_path = os.path.join(BASE_DIR, "project_skills", "youtube_title_packaging.md")
    thumbnail_rules_path = os.path.join(BASE_DIR, "project_skills", "thumbnail_ab_testing.md")
    packaging_playbook_path = resolve_shared_artifact(REPORTS_DIR, "youtube_packaging_playbook")
    analytics_path = os.path.join(REPORTS_DIR, "analytics_summary.md")
    youtube_performance_path = os.path.join(BASE_DIR, "data", "knowledge_wiki", "youtube_performance_patterns.md")
    local_memory_path = os.path.join(BASE_DIR, "data", "knowledge_wiki", "local_market_memory.md")
    role_taxonomy_path = os.path.join(BASE_DIR, "data", "knowledge_wiki", "stock_role_taxonomy.md")
    validation_results = structured_data.get("validation_results") or []
    if validation_results:
        validation_lines = ["# 本機正式驗收結果"]
        for row in validation_results:
            status = "✅" if row.get("hit") is True else ("❌" if row.get("hit") is False else "⚠️")
            validation_lines.append(
                f"- {status} {row.get('id')} {row.get('name')}：{row.get('result')}；"
                f"漲跌 {row.get('pct_change')}%；成交量排名 {row.get('volume_rank') or '未進榜'}"
            )
        validation_summary = "\n".join(validation_lines)
    else:
        validation_summary = "\n".join(structured_data.get("prev_day_picks_result") or [])

    editorial_frame = structured_data.get("editorial_frame") or build_editorial_frame(
        run_date=structured_data.get("date") or datetime.datetime.now().strftime("%Y-%m-%d"),
        validation_results=structured_data.get("validation_results") or [],
        event_context="\n".join(
            [
                str(structured_data.get("index_action", "")),
                "\n".join(str(item) for item in (structured_data.get("macro_events") or [])),
                "\n".join(str(item) for item in (structured_data.get("tech_catalyst_events") or [])),
            ]
        ),
        channel_story=_read_tail(CHANNEL_STORY_LOG, max_chars=5000),
    )

    return {
        "validation_summary": validation_summary,
        "editorial_frame": format_editorial_frame_for_prompt(editorial_frame),
        "event_radar": format_event_radar_for_prompt(structured_data.get("supply_chain_event_radar") or []),
        "title_fatigue_report": build_title_fatigue_report(
            _read_tail(CHANNEL_STORY_LOG, max_chars=5000),
            youtube_assets_dir=REPORTS_DIR,
        ),
        "calendar_rules": packaging_calendar_block(structured_data.get("date")),
        "analytics_summary": _read_tail(analytics_path, max_chars=4500),
        "youtube_performance_patterns": _read_tail(youtube_performance_path, max_chars=5000),
        "channel_story": _read_tail(CHANNEL_STORY_LOG, max_chars=3500),
        "title_packaging_rules": _read_tail(title_rules_path, max_chars=5000),
        "thumbnail_testing_rules": _read_tail(thumbnail_rules_path, max_chars=3500),
        "packaging_playbook": _read_tail(packaging_playbook_path, max_chars=5000),
        "skill_context": skill_context,
        "local_market_memory": _read_tail(local_memory_path, max_chars=3500),
        "stock_role_taxonomy": _read_tail(role_taxonomy_path, max_chars=2500),
        "audience_profile": (
            "觀眾偏好：標題與封面要具體點名個股；先講情境或矛盾，再回答哪幾檔。"
            "避免只說 AI 族群、資金輪動、前景看好。"
            "但也不要每天硬套背離、退潮、警報或資金流；先判斷今日是驗收日、重大事件日、異常日、續航日、主題日或整理日。"
            "若是整理日，包裝要誠實聚焦等待確認與明日觀察條件。"
            "近期頻道流量以 YouTube 瀏覽功能為重要入口，標題與縮圖要優先服務首頁停留與 CTR。"
            "同一支股票要分清角色：方向指標、彈性攻擊、題材確認、資金載體要分開包裝。"
            "角色判斷必須參照本機 stock_db 的 market_role.evidence，不可只憑印象。"
        ),
    }


def is_generation_error(content):
    return isinstance(content, str) and content.strip().startswith("Error generating")


def find_todays_files(date_str):
    """找今天的 report 和 structured_data"""
    report_path = resolve_daily_artifact(REPORTS_DIR, date_str, "report")
    json_path = resolve_daily_artifact(REPORTS_DIR, date_str, "structured_data")

    if not report_path:
        print(f"❌ 找不到今日報告（date={date_str}）")
        return None, None

    if not json_path:
        print(f"❌ 找不到今日結構化數據（date={date_str}）")
        return None, None

    return report_path, json_path


def _youtube_outputs_ready(date_str):
    kinds = ("package", "thumbnail_1", "thumbnail_2")
    return all(resolve_youtube_artifact(REPORTS_DIR, date_str, kind) for kind in kinds)


def _update_channel_story_visual_theme(date_str, visual_theme, title_used=""):
    """Update the latest channel log row after /2 chooses the actual visual theme."""
    if not visual_theme or not os.path.exists(CHANNEL_STORY_LOG):
        return

    try:
        with open(CHANNEL_STORY_LOG, "r", encoding="utf-8") as f:
            lines = f.readlines()

        changed = False
        for idx in range(len(lines) - 1, -1, -1):
            line = lines[idx]
            if not line.startswith(f"| {date_str} |"):
                continue

            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) < 9:
                break

            cells[4] = visual_theme
            if title_used and cells[7] in {"-", "", "待更新"}:
                cells[7] = title_used
            lines[idx] = "| " + " | ".join(cells) + " |\n"
            changed = True
            break

        if changed:
            with open(CHANNEL_STORY_LOG, "w", encoding="utf-8") as f:
                f.writelines(lines)
            print(f"  ✅ 頻道敘事紀錄已更新視覺主題：{visual_theme}")
    except Exception as e:
        print(f"  ⚠️  頻道敘事紀錄視覺主題更新失敗，略過：{e}")


def _read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_thumbnail_text_fields(prompt_content):
    fields = {}
    for key, label in [
        ("primary", "Primary"),
        ("secondary", "Secondary"),
        ("date", "Small date label"),
    ]:
        match = re.search(rf"^- {re.escape(label)}:\s*(.+)$", prompt_content, flags=re.MULTILINE)
        fields[key] = match.group(1).strip() if match else ""
    return fields


def _thumbnail_section(thumbnails):
    lines = [
        "### A/B 完整封面 Prompt（以此為準）",
        "",
        "以下內容由本機 `thumbnail_generator` 產生，取代舊版 package 內建封面 prompt。",
        "獨立 `yt_thumbnail_prompt` 檔只作備份；日常上架請優先看本段。",
        "",
    ]

    labels = ["A", "B"]
    for idx, thumb in enumerate(thumbnails[:2]):
        label = labels[idx] if idx < len(labels) else str(idx + 1)
        path = thumb.get("path", "")
        filename = os.path.basename(path) if path else ""
        content = _read_file(path) if path and os.path.exists(path) else thumb.get("prompt", "")
        title = thumb.get("title", "")
        scenario = thumb.get("scenario", "")

        lines.extend([
            f"#### 封面 Prompt {label}",
            "",
            f"- 對應標題：{title}",
            f"- 情境：{scenario}",
            f"- 備份檔：{filename}",
            "",
            "```markdown",
            content.rstrip(),
            "```",
            "",
        ])

    return "\n".join(lines).rstrip()


def _notebooklm_cover_section_from_thumbnail(first_prompt_content, visual_theme):
    fields = _extract_thumbnail_text_fields(first_prompt_content)
    primary = fields.get("primary") or "主力方向曝光"
    secondary = fields.get("secondary") or "明天驗收"
    date_label = fields.get("date") or "台股盤後"

    return "\n".join([
        "### NotebookLM Video Overview 封面視覺統一指令",
        "",
        "```text",
        "請將影片第一張投影片設計成封面頁，不要直接進入內容。",
        f"封面主標題請使用：「{primary}」",
        f"封面副標題請使用：「{secondary}」",
        f"左上角小字日期請使用：「{date_label}」",
        f"封面視覺主題請與 YouTube 封面一致：{visual_theme if visual_theme else '今日 /2 指定視覺主題'}。",
        "請保留博士與韭菜兩個角色感，使用高對比深色背景、白色/黃色大字、紅綠 K 線元素。",
        "請不要自行改寫主標題與副標題。",
        "```",
    ])


def integrate_thumbnail_prompts_into_package(package_path, ab_results, visual_theme=""):
    """Replace package cover prompt blocks with the finalized local thumbnail prompts."""
    thumbnails = (ab_results or {}).get("thumbnails") or []
    if not package_path or not os.path.exists(package_path) or not thumbnails:
        return False

    package = _read_file(package_path)
    prompt_section = _thumbnail_section(thumbnails)
    first_path = thumbnails[0].get("path", "")
    first_content = _read_file(first_path) if first_path and os.path.exists(first_path) else thumbnails[0].get("prompt", "")
    notebook_section = _notebooklm_cover_section_from_thumbnail(first_content, visual_theme)

    # Strip previously appended cover blocks before inserting. The stop heading
    # in the addendum is "## 48 小時驗收欄位", not "###". Doing this after insert
    # deletes the block just written whenever that heading level does not match.
    checklist_stop = r"\n(?:###|##) 48 小時驗收欄位"
    package = re.sub(
        rf"\n### A/B 完整封面 Prompt（以此為準）[\s\S]*?(?={checklist_stop}|\Z)",
        "\n",
        package,
        count=1,
    )
    package = re.sub(
        rf"\n(?:###|##) NotebookLM Video Overview 封面視覺統一指令[\s\S]*?(?={checklist_stop}|\Z)",
        "\n",
        package,
        count=1,
    )

    cover_pattern = re.compile(
        r"### A 組封面設定.*?(?=(?:###|##) NotebookLM Video Overview 封面視覺統一指令)",
        flags=re.DOTALL,
    )
    package, cover_replacements = cover_pattern.subn(prompt_section + "\n\n", package, count=1)

    notebook_pattern = re.compile(
        rf"(?:###|##) NotebookLM Video Overview 封面視覺統一指令.*?(?={checklist_stop}|\Z)",
        flags=re.DOTALL,
    )
    package, notebook_replacements = notebook_pattern.subn(notebook_section + "\n\n", package, count=1)

    if not cover_replacements:
        # NotebookLM heading is stripped above, so the A-group block often
        # survives and still carries the planner's old cover words.
        package = re.sub(
            rf"\n### A 組封面設定[\s\S]*?(?=\n### A/B 完整封面 Prompt|{checklist_stop}|\Z)",
            "\n",
            package,
            count=1,
        )
        checklist_pattern = re.compile(r"(?=### 48 小時驗收欄位|## 48 小時驗收欄位)")
        if checklist_pattern.search(package):
            package = checklist_pattern.sub(prompt_section + "\n\n", package, count=1)
        else:
            package = package.rstrip() + "\n\n" + prompt_section + "\n"
    if not notebook_replacements:
        checklist_pattern = re.compile(r"(?=### 48 小時驗收欄位|## 48 小時驗收欄位)")
        if checklist_pattern.search(package):
            package = checklist_pattern.sub(notebook_section + "\n\n", package, count=1)
        else:
            package = package.rstrip() + "\n\n" + notebook_section + "\n"

    with open(package_path, "w", encoding="utf-8") as f:
        f.write(package.rstrip() + "\n")
    return True


def main():
    parser = argparse.ArgumentParser(description="補跑今日素材（Podcast / YouTube / Knowledge Wiki）")
    parser.add_argument("--date", default=datetime.datetime.now().strftime("%Y-%m-%d"),
                        help="要補跑的日期，格式 YYYY-MM-DD（預設：今天）")
    parser.add_argument("--skip-podcast",   action="store_true", help="跳過 Podcast 腳本")
    parser.add_argument("--skip-youtube",   action="store_true", help="跳過 YouTube 素材")
    parser.add_argument("--skip-thumbnail", action="store_true", help="跳過縮圖 Prompt")
    parser.add_argument("--skip-wiki",      action="store_true", help="跳過 Knowledge Wiki 更新")
    parser.add_argument("--visual-theme", default="", help="/2 指定的封面視覺主題")
    parser.add_argument("--force-youtube", action="store_true", help="即使 YouTube package 已存在也重新產生")
    args = parser.parse_args()

    date_str = args.date
    visual_theme = args.visual_theme.strip() or DAILY_VISUAL_THEME
    print(f"\n{'='*50}")
    print(f"  素材補跑模式 (scripts/youtube/regen_assets.py)")
    print(f"  目標日期：{date_str}")
    print(f"  視覺主題：{visual_theme if visual_theme else '未指定 (預設風格)'}")
    print(f"{'='*50}\n")
    print_today_usage(prefix="  ")

    try:
        update_youtube_performance_wiki(CHANNEL_STORY_LOG, YOUTUBE_PERFORMANCE_WIKI)
        print(f"✅ YouTube 成效學習摘要已更新：{os.path.relpath(YOUTUBE_PERFORMANCE_WIKI, BASE_DIR)}")
    except Exception as e:
        print(f"⚠️  YouTube 成效學習摘要更新失敗，略過：{e}")

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

    try:
        from modules.notebooklm_source_builder import save_notebooklm_source

        notebooklm_source_path = save_notebooklm_source(structured_data, REPORTS_DIR, date_str=date_str)
        print(f"✅ NotebookLM 乾淨資料源已更新：{os.path.basename(notebooklm_source_path)}")
    except Exception as e:
        print(f"  ⚠️  NotebookLM 乾淨資料源更新失敗，略過：{e}")

    print("\n🧠 正在更新本機知識複利（市場記憶 + 個股角色分類，不消耗 AI 額度）...")
    try:
        from modules.local_knowledge_compounder import LocalKnowledgeCompounder

        compound_result = LocalKnowledgeCompounder().update_from_structured_data(
            structured_data=structured_data,
            report_content=report_content,
            report_date=date_str,
        )
        print(
            "  ✅ 本機知識已更新："
            f"市場記憶 {'新增' if compound_result['memory_appended'] else '已存在'}；"
            f"個股角色更新 {compound_result['roles_updated']} 檔"
        )
    except Exception as e:
        print(f"  ⚠️  本機知識複利更新失敗，略過：{e}")

    # ── Step 1：Podcast 腳本 ──────────────────────────
    nl_filepath = daily_artifact_write_path(REPORTS_DIR, date_str, "notebooklm_prompt")
    if not args.skip_podcast:
        existing_prompt = resolve_daily_artifact(REPORTS_DIR, date_str, "notebooklm_prompt")
        if existing_prompt:
            print(f"\n⚠️  Podcast 腳本已存在，略過（若要強制重跑請刪除後再執行）")
            print(f"   {os.path.basename(existing_prompt)}")
        else:
            print("\n🎧 正在生成本機 NotebookLM Podcast 指令（不消耗 Gemini）...")
            from modules.notebooklm_generator import generate_notebooklm_prompt
            notebooklm_prompt_content = generate_notebooklm_prompt(
                GEMINI_API_KEY, structured_data, date_str, current_theme=PROJECT_THEME
            )
            if notebooklm_prompt_content is None:
                print("  ⏭  NotebookLM prompt 已關閉（NOTEBOOKLM_PROMPT_MODE=off）")
            elif is_generation_error(notebooklm_prompt_content):
                print(f"  ⚠️  Podcast 腳本生成失敗：{notebooklm_prompt_content.splitlines()[0]}")
            elif notebooklm_prompt_content:
                with open(nl_filepath, "w", encoding="utf-8") as f:
                    f.write(notebooklm_prompt_content)
                print(f"  ✅ Podcast 腳本已儲存：{os.path.basename(nl_filepath)}")
    else:
        print("\n⏭️  跳過 Podcast 腳本（--skip-podcast）")

    # ── Step 2：YouTube 素材 ──────────────────────────
    yt_package = None
    yt_package_path = resolve_youtube_artifact(REPORTS_DIR, date_str, "package")
    if not args.skip_youtube:
        from modules.youtube_content_generator import (
            build_fallback_youtube_package,
            generate_youtube_package,
            save_youtube_package,
        )
        if yt_package_path and not args.force_youtube:
            print(f"\n⚠️  YouTube 素材已存在，略過")
            print(f"   {os.path.basename(yt_package_path)}")
            print("   若要依新視覺主題重產，請加上 --force-youtube")
        else:
            print(f"\n⏳ [速率限制保護] 等待 {SLEEP_BETWEEN_CALLS} 秒後呼叫 Gemini 生成 YouTube 素材...")
            time.sleep(SLEEP_BETWEEN_CALLS)
            print("📢 正在生成 YouTube 上架素材（標題/描述/置頂留言）...")
            yt_package = generate_youtube_package(
                GEMINI_API_KEY,
                structured_data,
                report_content,
                date_str,
                packaging_context=build_packaging_context(structured_data),
            )
            if yt_package.get("titles"):
                yt_package_path = save_youtube_package(
                    yt_package,
                    REPORTS_DIR,
                    date_str,
                    structured_data=structured_data,
                )
                print(f"  ✅ YouTube 素材包已儲存：{os.path.basename(yt_package_path)}")
                for i, title in enumerate(yt_package["titles"][:2]):
                    print(f"     標題{'AB'[i]}：{title}")

                try:
                    from modules.video_asset_planner import (
                        append_youtube_package_addendum,
                        build_video_asset_record,
                        build_youtube_package_addendum,
                    )

                    addendum = build_youtube_package_addendum(
                        structured_data=structured_data,
                        report_content=report_content,
                        youtube_package=yt_package,
                        date_str=date_str,
                        visual_theme=visual_theme,
                    )
                    append_youtube_package_addendum(yt_package_path, addendum)

                    from modules.analytics_store import AnalyticsStore

                    asset_record = build_video_asset_record(
                        structured_data=structured_data,
                        youtube_package=yt_package,
                        date_str=date_str,
                        visual_theme=visual_theme,
                    )
                    AnalyticsStore().record_youtube_asset_plan(
                        run_date=date_str,
                        asset_record=asset_record,
                        youtube_package_path=yt_package_path,
                        video_plan_path=yt_package_path,
                        visual_theme=visual_theme,
                    )
                    print("  ✅ YouTube 素材包已補入封面/NotebookLM/驗收計畫")
                    _update_channel_story_visual_theme(
                        date_str=date_str,
                        visual_theme=visual_theme,
                        title_used=(yt_package.get("titles") or [""])[0],
                    )
                except Exception as e:
                    print(f"  ⚠️  影片製作計畫產生失敗，略過：{e}")
            else:
                print("  ⚠️  YouTube 素材生成失敗，改用本機 fallback package")
                yt_package = build_fallback_youtube_package(
                    structured_data,
                    report_content,
                    date_str,
                    reason="Gemini YouTube package 生成失敗或遭遇速率限制",
                )
                yt_package_path = save_youtube_package(
                    yt_package,
                    REPORTS_DIR,
                    date_str,
                    structured_data=structured_data,
                )
                print(f"  ✅ Fallback YouTube 素材包已儲存：{os.path.basename(yt_package_path)}")
                _update_channel_story_visual_theme(
                    date_str=date_str,
                    visual_theme=visual_theme,
                    title_used=(yt_package.get("titles") or [""])[0],
                )
    else:
        print("\n⏭️  跳過 YouTube 素材（--skip-youtube）")

    # ── Step 3：縮圖 Prompt ──────────────────────────
    if not args.skip_thumbnail:
        print("\n🎨 正在用本機規則產生 YouTube 情境式縮圖 Prompt（手動生圖用，不呼叫 Gemini）...")
        from modules.thumbnail_generator import generate_ab_test_thumbnails, print_ab_test_summary
        try:
            thumbnail_styles = [visual_theme] if visual_theme else None
            youtube_output_dir = ensure_youtube_dir(REPORTS_DIR, date_str)
            preferred_titles = []
            if isinstance(yt_package, dict):
                preferred_titles = [
                    str(title).strip()
                    for title in (yt_package.get("titles") or [])
                    if str(title or "").strip()
                ]
            if (not preferred_titles) and yt_package_path and os.path.exists(yt_package_path):
                # Recover A/B titles from an existing package when Gemini path already ran.
                # Package format: **標題 A（...）：**\n```\n<title>\n```
                package_text = _read_file(yt_package_path)
                preferred_titles = re.findall(
                    r"\*\*標題 [AB][^*]*\*\*\s*```\s*(.*?)\s*```",
                    package_text,
                    flags=re.DOTALL,
                )
                preferred_titles = [title.strip() for title in preferred_titles if title.strip()]

            ab_results = generate_ab_test_thumbnails(
                gemini_api_key=GEMINI_API_KEY,
                openai_api_key=None,
                report_content=report_content,
                reports_dir=youtube_output_dir,
                styles=thumbnail_styles,
                num_titles=2,
                structured_data=structured_data,
                run_date=date_str,
                channel_story=_read_tail(CHANNEL_STORY_LOG, max_chars=5000),
                preferred_titles=preferred_titles,
            )
            print_ab_test_summary(ab_results)
            if integrate_thumbnail_prompts_into_package(yt_package_path, ab_results, visual_theme=visual_theme):
                print(f"  ✅ YouTube package 已改用本機完整封面 Prompt：{os.path.basename(yt_package_path)}")
            else:
                print("  ⚠️  未整合封面 Prompt 到 YouTube package（可能尚未產生 package）")
        except Exception as e:
            print(f"  ⚠️  縮圖 Prompt 生成失敗，略過：{e}")
    elif args.skip_thumbnail:
        print("\n⏭️  跳過縮圖 Prompt（--skip-thumbnail）")

    # ── Step 4：Knowledge Wiki ────────────────────────
    if not args.skip_wiki:
        wiki_mode = os.getenv("KNOWLEDGE_WIKI_MODE", "local").strip().lower()
        if wiki_mode in {"ai", "gemini", "llm"}:
            print(f"\n⏳ [速率限制保護] 等待 {SLEEP_BETWEEN_CALLS} 秒後呼叫 Gemini 更新 Knowledge Wiki...")
            time.sleep(SLEEP_BETWEEN_CALLS)
        print("🧠 正在更新 Knowledge Wiki...")
        from modules.knowledge_manager import KnowledgeUpdater
        knowledge_updater = KnowledgeUpdater(GEMINI_API_KEY)
        knowledge_updater.update_knowledge_base(
            report_content,
            structured_data=structured_data if isinstance(structured_data, dict) else None,
            report_date=date_str,
        )
    else:
        print("\n⏭️  跳過 Knowledge Wiki（--skip-wiki）")

    try:
        current_path = write_current_index(
            REPORTS_DIR,
            date_str,
            daily_ready=True,
            youtube_ready=_youtube_outputs_ready(date_str),
            youtube_visual_theme=visual_theme,
        )
        print(f"  ✅ 今日索引已更新：{os.path.basename(current_path)}")
    except Exception as e:
        print(f"  ⚠️  今日索引更新失敗，略過：{e}")

    print(f"\n{'='*50}")
    print("  ✅ 素材補跑完成！")
    print(f"  📄 報告來源：{os.path.basename(report_path)}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
