"""
Few-shot arm of the 「baseline vs few-shot」 packaging experiment.

A (baseline) = the normal package, untouched. B (few-shot) = generate_youtube_package
with 2-3 bank examples appended (same regime first, random rotation). B's cover
headline is derived locally from B's own title hero and must differ from A's cover
text. Result is written to reports/youtube/<date>/fewshot_ab.json (chmod 600) and
appended to the package as a clearly separate section; nothing in A changes.

Disable with FEWSHOT_AB=off.
"""

import datetime
import os
import random
import re

from modules.fewshot_bank import (
    FEWSHOT_HYPOTHESIS_ID,
    fewshot_block,
    load_bank,
    regime_for_structured_data,
    sample_examples,
    write_day_meta,
)

SECTION_HEADING = "## 🧪 Few-shot 標題實驗（A=原方法／B=few-shot）"


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("FEWSHOT_AB", "on")).strip().lower() not in {"off", "0", "false", "no"}


def _overlay_text(pair):
    return "\n".join(p for p in pair if p)


def cover_text_for_title(title, structured_data, avoid=""):
    """Cover headline that follows the title's angle (?→question, 卻→fragment, else control), ≠ avoid."""
    from modules.thumbnail_generator import _control_overlay, _fragment_overlay, _question_overlay, parse_title_hero

    hero = parse_title_hero(title, structured_data or {})
    if not (hero.get("name") or hero.get("ticker")):
        return ""
    builders = [_control_overlay, _fragment_overlay, _question_overlay]
    if re.search(r"[?？]", title or ""):
        builders = [_question_overlay, _fragment_overlay, _control_overlay]
    elif "卻" in (title or ""):
        builders = [_fragment_overlay, _question_overlay, _control_overlay]
    candidates = [_overlay_text(b(hero)) for b in builders]
    for text in candidates:
        if text and text != (avoid or "").strip():
            return text
    return ""


def baseline_cover_text(titles, structured_data):
    from modules.thumbnail_generator import _control_overlay, choose_hero

    hero = choose_hero(list(titles or [])[:1], structured_data or {}) if titles else {}
    if not hero or not (hero.get("name") or hero.get("ticker")):
        return ""
    return _overlay_text(_control_overlay(hero))


def run_fewshot_variant(
    gemini_api_key,
    structured_data,
    report_content,
    date_str,
    packaging_context,
    baseline_titles,
    reports_dir,
    bank=None,
    rng=None,
    generate=None,
):
    """Returns the day meta dict (also saved), or None when skipped / failed. Never raises."""
    try:
        bank = bank if bank is not None else load_bank()
        if not (bank or {}).get("regimes"):
            print("  ⏭  few-shot 實驗：沒有 data/fewshot_title_bank.json，略過")
            return None
        regime = regime_for_structured_data(structured_data)
        rng = rng or random.Random()
        examples = sample_examples(bank, regime, k=rng.choice([2, 3]), rng=rng, exclude_dates={date_str})
        if not examples:
            return None
        if generate is None:
            from modules.youtube_content_generator import generate_youtube_package as generate
        result = generate(
            gemini_api_key,
            structured_data,
            report_content,
            date_str,
            packaging_context=packaging_context,
            fewshot_block=fewshot_block(examples),
        )
        fs_titles = [t for t in (result or {}).get("titles") or [] if t]
        if not fs_titles:
            print("  ⚠️  few-shot 實驗：生成失敗，今日只有 baseline")
            return None
        title_a = (baseline_titles or [""])[0]
        title_b = fs_titles[0]
        if title_b.strip() == title_a.strip() and len(fs_titles) > 1:
            title_b = fs_titles[1]
        cover_a = baseline_cover_text([title_a], structured_data)
        cover_b = cover_text_for_title(title_b, structured_data, avoid=cover_a)
        warnings = []
        try:
            from modules.title_fact_gate import check_titles, format_issue

            warnings = [format_issue(i) for i in check_titles([title_b], structured_data) if i.get("level") == "error"]
        except Exception:
            pass
        meta = {
            "hypothesis_id": FEWSHOT_HYPOTHESIS_ID,
            "date": date_str,
            "fewshot_arm": "b",
            "baseline_arm": "a",
            "regime": regime,
            "arms": {
                "a": {"method": "baseline", "title": title_a, "cover_text": cover_a},
                "b": {"method": "fewshot", "title": title_b, "cover_text": cover_b},
            },
            "examples": [
                {k: e.get(k) for k in ("video_id", "run_date", "regime", "metric_value")} for e in examples
            ],
            "fact_gate_errors": warnings,
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        write_day_meta(reports_dir, date_str, meta)
        return meta
    except Exception as exc:  # experiment must never break the daily run
        print(f"  ⚠️  few-shot 實驗失敗，略過：{exc}")
        return None


def package_section(meta) -> str:
    if not meta:
        return ""
    a, b = meta["arms"]["a"], meta["arms"]["b"]

    def cover(t):
        return " / ".join(x for x in (t or "").splitlines() if x) or "（無）"

    lines = [
        "---",
        "",
        SECTION_HEADING,
        "",
        f"- 盤勢分類：{meta.get('regime')}；範例：{', '.join(e['run_date'] for e in meta.get('examples') or [])}",
        f"- A（baseline，原方法）標題：`{a['title']}`；封面大字：{cover(a['cover_text'])}",
        f"- B（few-shot）標題：`{b['title']}`；封面大字：{cover(b['cover_text'])}",
        "- 用法：今日「測試與比較」就是這組（A 標題＋封面 A vs B 標題＋封面 B）。",
        "- 記錄：`python3 cli.py youtube record-ab-result --date "
        f"{meta.get('date')} --winner a|b|none`（預設 FS）；累積：`python3 scripts/youtube/fewshot_ab_summary.py`",
    ]
    if meta.get("fact_gate_errors"):
        lines.append("- ⛔ B 標題事實對帳 FAIL：" + "；".join(meta["fact_gate_errors"]) + "（修正前不要用 B）")
    return "\n".join(lines) + "\n"


def replace_package_title_b(package_path, title_b):
    """Swap the package's title B block for the few-shot title (title A untouched)."""
    if not package_path or not os.path.exists(package_path) or not title_b:
        return False
    with open(package_path, "r", encoding="utf-8") as f:
        text = f.read()
    new, n = re.subn(
        r"(\*\*標題 B[^*\n]*\*\*\s*```\s*\n)(.*?)(\n```)",
        lambda m: m.group(1) + title_b + m.group(3),
        text,
        count=1,
        flags=re.DOTALL,
    )
    if n:
        with open(package_path, "w", encoding="utf-8") as f:
            f.write(new)
    return bool(n)


def append_package_section(package_path, meta):
    section = package_section(meta)
    if not section or not package_path or not os.path.exists(package_path):
        return False
    with open(package_path, "r", encoding="utf-8") as f:
        text = f.read()
    if SECTION_HEADING in text:
        text = text[: text.index(SECTION_HEADING)].rstrip().removesuffix("---").rstrip() + "\n"
    with open(package_path, "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n\n" + section)
    return True
