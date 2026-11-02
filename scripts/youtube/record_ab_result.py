"""
Record a YouTube Studio 「測試與比較」 (Test & compare) cover result.

  python3 cli.py youtube record-ab-result --date 2026-10-14 --winner a \
      --metric watch_time_share --value-a 54.2 --value-b 45.8

Daily default (2026-10-30+): the covers declare FS (A 原方法 vs B few-shot, title + cover
headline tested together) or PK (FEWSHOT_AB=off: title A vs title B), so no
--hypothesis is needed; FS reads which arm is few-shot from fewshot_ab.json.

The hypothesis is read from that day's cover prompts (``- A/B variable:`` line);
``--hypothesis`` overrides. Covers without that line predate the single-variable
test (A and B changed title syntax AND composition), so their result cannot be
credited to any hypothesis: the command refuses (exit 1) unless ``--hypothesis``
is given explicitly — the ISO-week plan is never used to guess. The result goes to
analytics.db ``ab_test_results`` and marks the winning cover in
``youtube_thumbnail_variants.selected`` (``none`` leaves earlier flags alone unless
they came from an a/b result recorded for that date). Local only (no API calls).
"""

import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.cover_ab_test import (  # noqa: E402
    DEFAULT_METRIC,
    WINNERS,
    arm,
    find_hypothesis,
    format_learning_lines,
    load_plan,
    parse_variable_line,
    summarize_results,
)
from modules.report_paths import resolve_youtube_artifact  # noqa: E402

REPORTS_DIR = os.path.join(BASE_DIR, "reports")


def _declared_hypothesis(date_str, reports_dir=None):
    """{"variable", "hypothesis_id", "arm"} declared in the day's cover prompt A (None when absent)."""
    reports_dir = reports_dir or REPORTS_DIR  # resolved at call time (tests point REPORTS_DIR at a copy)
    for kind in ("thumbnail_1", "thumbnail_2"):
        path = resolve_youtube_artifact(reports_dir, date_str, kind)
        if not path:
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                declared = parse_variable_line(f.read())
        except OSError:
            continue
        if declared:
            return declared
    return None


def _fewshot_hypothesis(date_str, reports_dir=None):
    """Few-shot vs baseline title/cover test: needs that day's fewshot_ab.json (which arm is few-shot)."""
    from modules.fewshot_bank import FEWSHOT_HYPOTHESIS_ID, read_day_meta

    meta = read_day_meta(reports_dir or REPORTS_DIR, date_str)
    if not meta:
        raise SystemExit(f"❌ {date_str} 沒有 reports/youtube/{date_str}/fewshot_ab.json（當天沒有 few-shot 組），未記錄")
    fs = str(meta.get("fewshot_arm") or "b").lower()
    labels = {fs: "few-shot", ("a" if fs == "b" else "b"): "baseline（原方法）"}
    return {
        "id": FEWSHOT_HYPOTHESIS_ID,
        "variable": "fewshot",
        "label": "few-shot 包裝",
        "fewshot_arm": fs,
        "arms": {"a": {"label": labels["a"]}, "b": {"label": labels["b"]}},
    }


def resolve_hypothesis(date_str, override="", plan=None, reports_dir=None):
    """
    (hypothesis dict, source) — --hypothesis > the covers' declaration. Covers that
    declare no A/B variable are refused (SystemExit, exit 1) without --hypothesis:
    they changed title syntax and composition at once, so crediting the week's
    hypothesis would count a confounded result toward its n and its promotion.
    """
    plan = plan if plan is not None else load_plan()
    if str(override or "").strip().lower() in {"fs", "fewshot"}:
        return _fewshot_hypothesis(date_str, reports_dir), "fewshot_ab.json"
    declared = _declared_hypothesis(date_str, reports_dir)
    if override:
        found = find_hypothesis(plan, override)
        if not found:
            raise SystemExit(f"❌ --hypothesis {override} 不在 data/ab_test_plan.json")
        if not declared:
            print(f"  ⚠️  {date_str} 的封面 Prompt 沒有 A/B variable 宣告；依你指定的 --hypothesis {found.get('id')} 記錄")
        elif declared["hypothesis_id"] != found.get("id"):
            print(
                f"  ⚠️  封面 Prompt 宣告的是 {declared['hypothesis_id']}（{declared['variable']}），"
                f"你指定 {found.get('id')}；依 --hypothesis 記錄"
            )
        return found, "--hypothesis"
    if declared and declared["hypothesis_id"] == "FS":
        return _fewshot_hypothesis(date_str, reports_dir), "封面 Prompt 的 A/B variable（每日 FS）"
    if declared:
        found = find_hypothesis(plan, declared["hypothesis_id"]) or {
            "id": declared["hypothesis_id"],
            "variable": declared["variable"],
        }
        return found, "封面 Prompt 的 A/B variable"
    raise SystemExit(
        f"❌ {date_str} 的封面 Prompt 沒有「- A/B variable:」宣告（單一變數 A/B 之前的封面：A/B 同時換了標題句型與構圖），"
        "結果無法歸因到任何假設，未記錄。確定這天是某個假設的單一變數測試，才加 --hypothesis H1（或變數名）。"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="記錄 YouTube「測試與比較」封面 A/B 結果到 analytics.db")
    parser.add_argument("--date", required=True, help="影片／報告日期 YYYY-MM-DD")
    parser.add_argument("--winner", required=True, type=str.lower, choices=WINNERS, help="a、b 或 none（無明顯差異）")
    parser.add_argument("--metric", default=DEFAULT_METRIC, help=f"YouTube 判定指標（預設 {DEFAULT_METRIC}）")
    parser.add_argument("--value-a", type=float, default=None, help="封面 A 的指標值（例如觀看時間占比 %%）")
    parser.add_argument("--value-b", type=float, default=None, help="封面 B 的指標值")
    parser.add_argument("--hypothesis", default="", help="覆寫假設（H1 或變數名 style）")
    parser.add_argument("--video-id", default="", help="YouTube 影片 ID（可省略）")
    parser.add_argument("--notes", default="")
    parser.add_argument("--db", default="", help="analytics.db 路徑（預設 data/analytics.db；測試可指向副本）")
    args = parser.parse_args(argv)

    from modules.analytics_store import AnalyticsStore

    plan = load_plan()
    hypothesis, source = resolve_hypothesis(args.date, override=args.hypothesis, plan=plan)
    if args.value_a is not None and args.value_b is not None and args.winner in {"a", "b"}:
        winning, losing = (args.value_a, args.value_b) if args.winner == "a" else (args.value_b, args.value_a)
        if winning < losing:
            print(f"  ⚠️  勝出組的 {args.metric} 比較低（{winning} < {losing}），請確認 --winner 沒填反")

    store = AnalyticsStore(args.db) if args.db else AnalyticsStore()
    outcome = store.record_ab_test_result(
        run_date=args.date,
        hypothesis_id=hypothesis.get("id", ""),
        variable=hypothesis.get("variable", ""),
        arm_a=arm(hypothesis, "a").get("label", ""),
        arm_b=arm(hypothesis, "b").get("label", ""),
        winner=args.winner,
        metric=args.metric,
        value_a=args.value_a,
        value_b=args.value_b,
        video_id=args.video_id,
        notes=(f"fewshot_arm={hypothesis['fewshot_arm']}" + (f"; {args.notes}" if args.notes else ""))
        if hypothesis.get("fewshot_arm")
        else args.notes,
    )
    print(
        f"✅ 已記錄 {args.date} 封面 A/B：{hypothesis.get('id')} {hypothesis.get('variable')}（來源：{source}）"
        f" → winner={args.winner}"
        + (f"（A {args.value_a} / B {args.value_b} {args.metric}）" if args.value_a is not None or args.value_b is not None else "")
    )
    if args.winner in {"a", "b"}:
        if outcome["selected_rows"]:
            print(f"   youtube_thumbnail_variants：{args.winner.upper()} 標為 selected")
        else:
            print("   ⚠️  analytics.db 沒有這天的封面變體列（/2 Step 3 未寫入？），只記錄結果")
    elif outcome.get("cleared"):
        print("   youtube_thumbnail_variants：先前記錄的勝出封面已取消 selected")
    print(f"   DB: {store.db_path}")
    print("\n封面 A/B 累積（同一假設 ≥3 次有勝負且過半才升級預設；不產生禁令）：")
    for line in format_learning_lines(summarize_results(store.get_ab_test_results(), plan=plan)):
        print(f"  {line}")
    if hypothesis.get("fewshot_arm"):
        from modules.fewshot_bank import read_day_meta, summarize_fewshot

        rows = store.get_ab_test_results()
        metas = {r.get("run_date"): read_day_meta(REPORTS_DIR, r.get("run_date")) for r in rows}
        fs = summarize_fewshot(rows, metas)
        print(
            f"\nfew-shot vs baseline 累積：n={fs['n']}，few-shot 勝 {fs['fewshot_wins']}／baseline 勝 {fs['baseline_wins']}"
            f"／無明顯差異 {fs['no_difference']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
