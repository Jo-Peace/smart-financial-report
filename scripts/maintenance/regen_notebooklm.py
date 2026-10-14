#!/usr/bin/env python3
"""
一鍵重產當日 NotebookLM 包（不重跑抓盤、不呼叫 Gemini）。

  python3 cli.py maintenance regen-notebooklm --date 2026-07-08
  python3 cli.py maintenance regen-notebooklm --date 2026-07-08 --refresh-frame
  python3 cli.py maintenance regen-notebooklm --date 2026-07-08 \\
      --visual-theme "FIFA 32強" --title-a "主標" --title-b "副標"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv

load_dotenv(os.path.join(BASE_DIR, ".env"))

from modules.editorial_frame import build_editorial_frame
from modules.notebooklm_generator import generate_notebooklm_prompt
from modules.notebooklm_source_builder import save_notebooklm_source
from modules.pipeline_context import build_pipeline_context
from modules.report_paths import (
    daily_artifact_write_path,
    resolve_daily_artifact,
    resolve_shared_artifact,
    resolve_youtube_artifact,
)
from modules.role_scorer import attach_role_scores_to_structured, load_stock_db, ranking_is_reproducible


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _extract_titles_from_package(text: str) -> tuple[str, str]:
    """Parse A/B titles from youtube_package.md (title often in fenced ``` block)."""
    title_a = title_b = ""
    if not text:
        return title_a, title_b

    def _after_label(which: str) -> str:
        # Match **標題 A（...）：** then optional ``` fence then the real title line.
        pat = rf"\*\*標題\s*{which}[^*]*\*\*\s*\n+(?:```[^\n]*\n)?([^\n`]+)"
        m = re.search(pat, text)
        if not m:
            pat2 = rf"標題\s*{which}[^\n]*\n+(?:```[^\n]*\n)?([^\n`]+)"
            m = re.search(pat2, text)
        if not m:
            return ""
        candidate = (m.group(1) or "").strip().strip("#*- ")
        if not candidate or candidate.startswith("（") or candidate.startswith("("):
            return ""
        if candidate in {"```", "---"} or len(candidate) < 8:
            return ""
        return candidate

    title_a = _after_label("A")
    title_b = _after_label("B")

    # Prefer explicit "Title pairing:" / "對應標題：" lines from thumbnail section
    pairings = re.findall(r"(?:Title pairing|對應標題)[:：]\s*([^\n]+)", text)
    pairings = [p.strip() for p in pairings if p.strip() and len(p.strip()) >= 8]
    if pairings:
        if not title_a:
            title_a = pairings[0]
        if not title_b and len(pairings) > 1:
            title_b = pairings[1]

    return title_a, title_b


def _extract_title_from_thumbnail(path: str) -> str:
    text = _read_text(path)
    for line in text.splitlines():
        if line.startswith("Title:") or line.startswith("標題:"):
            return line.split(":", 1)[1].strip()
        if line.startswith("- Primary:"):
            # overlay not full title; keep looking
            continue
    # Some prompts put the chosen title early
    for line in text.splitlines()[:15]:
        s = line.strip()
        if s.startswith("#") and 6 <= len(s) <= 80:
            return s.lstrip("# ").strip()
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="重產 NotebookLM source + prompt（本機、無 Gemini）")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--refresh-frame",
        action="store_true",
        help="用 validation_results + channel_story 重算 editorial_frame 並寫回 structured_data",
    )
    parser.add_argument("--visual-theme", default="", help="封面視覺主題（寫入導演卡）")
    parser.add_argument("--title-a", default="", help="封面主標題（可手動指定）")
    parser.add_argument("--title-b", default="", help="封面副標題（可手動指定）")
    parser.add_argument(
        "--no-write-structured",
        action="store_true",
        help="即使 --refresh-frame 也不覆寫 structured_data.json",
    )
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="舊檔（role_score_inputs 無 base_roles）也用目前 stock_db 重排主角（可能與 /1 不同）",
    )
    args = parser.parse_args()

    ctx = build_pipeline_context()
    date_str = args.date
    reports_dir = ctx.reports_dir

    sd_path = resolve_daily_artifact(reports_dir, date_str, "structured_data")
    if not sd_path or not os.path.exists(sd_path):
        print(f"❌ 找不到 structured_data：{date_str}")
        return 1

    structured = _load_json(sd_path)
    print(f"{'=' * 50}")
    print(f"  Regen NotebookLM — {date_str}")
    print(f"{'=' * 50}")

    channel_story = _read_text(resolve_shared_artifact(reports_dir, "channel_story_log"))

    if args.refresh_frame:
        validation = structured.get("validation_results") or []
        event_bits = []
        for ev in structured.get("supply_chain_event_radar") or []:
            if isinstance(ev, dict):
                event_bits.append(str(ev.get("event") or ""))
            else:
                event_bits.append(str(ev))
        try:
            from modules.similar_sessions import parse_index_pct

            index_pct = parse_index_pct(structured.get("index_action"))
        except Exception:
            index_pct = None
        frame = build_editorial_frame(
            run_date=date_str,
            signal_pack={},
            validation_results=validation,
            event_context="\n".join(event_bits),
            channel_story=channel_story[-5000:],
            event_radar=[ev for ev in structured.get("supply_chain_event_radar") or [] if isinstance(ev, dict)],
            index_pct=index_pct,
        )
        structured["editorial_frame"] = frame
        print(
            f"  ✅ 重算 frame: {frame.get('label')} / placement={frame.get('validation_placement')} "
            f"(score={frame.get('validation_story_score')})"
        )
        if not args.no_write_structured:
            out_sd = daily_artifact_write_path(reports_dir, date_str, "structured_data")
            with open(out_sd, "w", encoding="utf-8") as f:
                json.dump(structured, f, ensure_ascii=False, indent=2)
            print(f"  ✅ 已寫回 structured_data: {os.path.basename(out_sd)}")
    else:
        ef = structured.get("editorial_frame") or {}
        print(
            f"  📌 既有 frame: {ef.get('label') or ef.get('frame') or 'N/A'} / "
            f"placement={ef.get('validation_placement') or '（舊檔可能無此欄）'}"
        )

    # Cover titles: CLI > youtube package > thumbnail prompts
    title_a = args.title_a.strip()
    title_b = args.title_b.strip()
    if not title_a or not title_b:
        pkg_path = resolve_youtube_artifact(reports_dir, date_str, "package")
        if pkg_path and os.path.exists(pkg_path):
            pa, pb = _extract_titles_from_package(_read_text(pkg_path))
            title_a = title_a or pa
            title_b = title_b or pb
            if pa or pb:
                print(f"  📌 自 YouTube package 讀取標題 A/B")
    if not title_a:
        t1 = resolve_youtube_artifact(reports_dir, date_str, "thumbnail_1")
        if t1 and os.path.exists(t1):
            title_a = _extract_title_from_thumbnail(t1)
    if not title_b:
        t2 = resolve_youtube_artifact(reports_dir, date_str, "thumbnail_2")
        if t2 and os.path.exists(t2):
            title_b = _extract_title_from_thumbnail(t2)

    visual_theme = (
        args.visual_theme.strip()
        or os.getenv("DAILY_VISUAL_THEME", "").strip()
        or os.getenv("THUMBNAIL_STYLE", "").strip()
        or ctx.daily_visual_theme
        or ""
    )

    try:
        # /1 persists the roles it scored with (role_score_inputs.base_roles), so
        # a re-rank reproduces the published protagonist. Older files lack them:
        # re-ranking would use roles the compounder wrote after /1, so keep the
        # /1 ranking unless --rescore.
        if args.rescore or not structured.get("focus_ranking") or ranking_is_reproducible(structured):
            structured = attach_role_scores_to_structured(structured, load_stock_db(), limit=5)
        else:
            print("  📌 沿用 /1 主角排序（舊檔無 base_roles；要用目前 stock_db 重排請加 --rescore）")
        from modules.broadcast_facts import attach_broadcast_facts

        structured = attach_broadcast_facts(structured)
        top = (structured.get("focus_ranking") or [])[:3]
        if top:
            preview = "、".join(
                f"{r.get('ticker')} {r.get('name') or ''}(P{r.get('title_priority')})".strip() for r in top
            )
            print(f"  ✅ 主角量化排序: {preview}")
            if not args.no_write_structured:
                # Always persist ranking when regenerating so /2 can reuse it
                out_sd = daily_artifact_write_path(reports_dir, date_str, "structured_data")
                with open(out_sd, "w", encoding="utf-8") as f:
                    json.dump(structured, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"  ⚠️  角色量化分數略過: {exc}")

    source_path = save_notebooklm_source(structured, reports_dir, date_str=date_str)
    print(f"  ✅ source: {source_path}")

    prompt = generate_notebooklm_prompt(
        None,
        structured,
        date_str=date_str,
        current_theme=ctx.project_theme or "",
        skill_context="",
        visual_theme=visual_theme,
        title_a=title_a,
        title_b=title_b,
    )
    if prompt is None:
        print("  ⏭ prompt 已關閉（NOTEBOOKLM_PROMPT_MODE=off）")
        return 0

    prompt_path = daily_artifact_write_path(reports_dir, date_str, "notebooklm_prompt")
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt)
    print(f"  ✅ prompt: {prompt_path}")
    if visual_theme:
        print(f"  🎨 visual_theme: {visual_theme}")
    if title_a:
        print(f"  📝 title_a: {title_a[:48]}")
    if title_b:
        print(f"  📝 title_b: {title_b[:48]}")

    print("\n✅ NotebookLM 包已更新（無 Gemini）。上傳 source，導演卡用 prompt。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
