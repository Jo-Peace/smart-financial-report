import argparse
import datetime
import os
import sqlite3

from modules.analytics_store import EVAL_METHOD, AnalyticsStore


BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(BASE_DIR, "data", "analytics.db")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
DEFAULT_OUTPUT = os.path.join(REPORTS_DIR, "analytics_summary.md")


def _connect(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _fmt_pct(value):
    if value is None:
        return "N/A"
    return f"{float(value):+.2f}%"


def _fmt_hit_rate(value):
    if value is None:
        return "N/A"
    return f"{float(value) * 100:.1f}%"


def _default_channel_log():
    from modules.report_paths import resolve_shared_artifact

    return resolve_shared_artifact(REPORTS_DIR, "channel_story_log")


def _youtube_channel_baseline(cur, log_path=None, db_path=None):
    """
    Channel-relative sample rule (replaces the old fixed 900 views): a
    measurement is a valid A/B sample only at ≥ channel median × 2.

    The median is youtube_learning.learning_window's baseline — the one /2 and
    the weekly brief use: the last 30 channel_story rows, off-site-seeded
    videos excluded, views@48h when ≥ 8 same-age snapshots exist, else the
    cumulative views of ≥72h videos. Only when the channel_story log gives no
    sample does it fall back to this DB (latest views of every mature weekday
    video ever), and ``basis`` / ``shared_basis`` say the basis differs.
    """
    import datetime

    from modules.youtube_learning import MATURE_HOURS, channel_baseline, learning_window

    if log_path and os.path.exists(log_path):
        try:
            window = learning_window(log_path, db_path=db_path)
        except Exception:
            window = None
        if window and (window.get("baseline") or {}).get("n"):
            return dict(
                window["baseline"],
                basis=f"與 /2、週報同一基準：近 30 支 channel_story、排除站外導流，{window['basis']}",
                shared_basis=True,
            )

    rows = cur.execute(
        """
        SELECT run_date, video_id, views, hours_since_publish
        FROM youtube_video_metrics
        WHERE views IS NOT NULL
        ORDER BY measured_at, id
        """
    ).fetchall()
    latest = {}
    for row in rows:
        try:
            if datetime.date.fromisoformat(str(row["run_date"])[:10]).weekday() >= 5:
                continue  # weekend special: different format, not comparable
        except ValueError:
            pass
        latest[row["video_id"] or f"date:{row['run_date']}"] = row
    sample = [
        {"metric": int(r["views"])}
        for r in latest.values()
        if r["hours_since_publish"] is None or float(r["hours_since_publish"]) >= MATURE_HOURS
    ]
    return dict(
        channel_baseline(sample),
        basis="channel_story_log 無樣本，改用本機 DB 全部平日影片的最新累積觀看（與 /2、週報基準不同）",
        shared_basis=False,
    )


def _youtube_sample_quality(views, hours_since_publish=None, win_threshold=None):
    """valid only at ≥ channel median × 2; <72h is still young; else an observation."""
    from modules.youtube_learning import MATURE_HOURS

    if views is None:
        return "unknown"
    if hours_since_publish is not None and float(hours_since_publish) < MATURE_HOURS:
        return f"young (<{MATURE_HOURS}h)"
    if not win_threshold:
        return "no-baseline"
    return "valid (≥2× median)" if int(views) >= float(win_threshold) else "observe"


def _table(headers, rows):
    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        lines.append("| " + " | ".join(str(cell).replace("\n", " ") for cell in row) + " |")
    return "\n".join(lines)


def _fmt_ci(low, high):
    if low is None or high is None:
        return "N/A"
    return f"{float(low) * 100:.0f}–{float(high) * 100:.0f}%"


def _hit_cell(hit):
    if hit == 1:
        return "Y"
    if hit == 0:
        return "N"
    return "–"


def _calendar_warning(today=None):
    """Warn when a 20-session window opened today runs past the closed-day list."""
    from modules import twse_calendar
    from modules.analytics_store import EVAL_HORIZONS, nth_session_after

    today = today or datetime.date.today()
    return twse_calendar.coverage_warning(nth_session_after(today, max(EVAL_HORIZONS)))


def _youtube_variant_stats(cur, limit):
    """
    One sample per video (latest measurement), weekend-special formats excluded, median
    instead of mean: repeated refresh snapshots and two weekend outliers (2/28, 3/7)
    used to inflate「A 組平均 4,411」 to ~31× the channel median.
    """
    import datetime
    import statistics

    rows = cur.execute(
        """
        SELECT run_date, measured_at, video_id, views, ctr,
               COALESCE(NULLIF(title_variant, ''), 'N/A') AS title_variant,
               COALESCE(NULLIF(thumbnail_variant, ''), 'N/A') AS thumbnail_variant
        FROM youtube_video_metrics
        WHERE views IS NOT NULL
        ORDER BY measured_at, id
        """
    ).fetchall()
    latest = {}
    for row in rows:
        try:
            weekday = datetime.date.fromisoformat(str(row["run_date"])[:10]).weekday()
        except ValueError:
            weekday = 0
        if weekday >= 5:
            continue  # weekend special: different format, not comparable
        key = row["video_id"] or f"date:{row['run_date']}"
        latest[key] = row
    groups = {}
    for row in latest.values():
        groups.setdefault((row["title_variant"], row["thumbnail_variant"]), []).append(row)
    stats = []
    for (title_variant, thumbnail_variant), items in groups.items():
        views = [int(r["views"]) for r in items]
        ctrs = [float(r["ctr"]) for r in items if r["ctr"] is not None]
        stats.append(
            {
                "title_variant": title_variant,
                "thumbnail_variant": thumbnail_variant,
                "samples": len(items),
                "median_views": statistics.median(views),
                "max_views": max(views),
                "avg_ctr": (sum(ctrs) / len(ctrs)) if ctrs else None,
            }
        )
    stats.sort(key=lambda r: -r["median_views"])
    return stats[:limit]


def build_report(limit=12, db_path=None, log_path=None):
    db_path = db_path or DB_PATH
    log_path = log_path or _default_channel_log()
    store = AnalyticsStore(db_path)
    horizon_summary = store.get_horizon_performance_summary()
    attribution = store.get_signal_attribution_summary(horizons=(5, 20))
    conn = _connect(db_path)
    cur = conn.cursor()

    counts = cur.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM daily_runs) AS daily_runs,
            (SELECT COUNT(*) FROM predictions) AS predictions,
            (SELECT COUNT(*) FROM prediction_results
             WHERE method = :method AND return_pct IS NOT NULL) AS evaluated_results,
            (SELECT COUNT(*) FROM prediction_results
             WHERE method = :method AND return_pct IS NULL) AS missing_results,
            (SELECT COUNT(*) FROM ai_picks) AS ai_picks,
            (SELECT COUNT(*) FROM daily_editorial_frames) AS editorial_frames,
            (SELECT COUNT(*) FROM youtube_asset_plans) AS youtube_asset_plans,
            (SELECT COUNT(*) FROM youtube_video_metrics) AS youtube_metric_samples
        """,
        {"method": EVAL_METHOD},
    ).fetchone()

    by_direction = cur.execute(
        """
        SELECT
            p.direction,
            COUNT(r.hit) AS samples,
            AVG(r.hit) AS hit_rate,
            AVG(r.return_pct) AS avg_return,
            AVG(r.excess_return_pct) AS avg_excess
        FROM prediction_results r
        JOIN predictions p ON p.id = r.prediction_id
        WHERE r.method = ? AND r.return_pct IS NOT NULL
        GROUP BY p.direction
        ORDER BY samples DESC
        """,
        (EVAL_METHOD,),
    ).fetchall()

    recent_predictions = cur.execute(
        """
        SELECT
            prediction_date, ticker, name, direction,
            COALESCE(trigger, '') AS trigger
        FROM predictions
        ORDER BY prediction_date DESC, ticker
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    recent_results = cur.execute(
        """
        SELECT
            p.prediction_date, p.ticker, p.name, p.direction,
            r.horizon_days, r.evaluated_date, r.target_date, r.status, r.return_pct,
            r.index_return_pct, r.excess_return_pct, r.hit
        FROM prediction_results r
        JOIN predictions p ON p.id = r.prediction_id
        WHERE r.method = ? AND r.return_pct IS NOT NULL
        ORDER BY r.evaluated_date DESC, p.prediction_date DESC, p.ticker, r.horizon_days
        LIMIT ?
        """,
        (EVAL_METHOD, limit),
    ).fetchall()

    top_picks = cur.execute(
        """
        SELECT pick_text, COUNT(*) AS count
        FROM ai_picks
        GROUP BY pick_text
        ORDER BY count DESC, pick_text
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    recent_youtube_metrics = cur.execute(
        """
        SELECT
            run_date, measured_at, views, likes, comments, ctr,
            title_variant, thumbnail_variant, notes, hours_since_publish
        FROM youtube_video_metrics
        ORDER BY measured_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    editorial_frame_stats = cur.execute(
        """
        SELECT
            COALESCE(NULLIF(label, ''), frame) AS label,
            COUNT(*) AS samples,
            AVG(confidence) AS avg_confidence
        FROM daily_editorial_frames
        GROUP BY label
        ORDER BY samples DESC, label
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    youtube_variant_stats = _youtube_variant_stats(cur, limit)
    youtube_baseline = _youtube_channel_baseline(cur, log_path=log_path, db_path=db_path)

    conn.close()

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    db_display_path = os.path.relpath(DB_PATH, BASE_DIR)
    lines = [
        f"# Analytics Summary",
        "",
        f"- Generated at: {now}",
        f"- Database: `{db_display_path}`",
        f"- Daily runs: {counts['daily_runs']}",
        f"- Predictions: {counts['predictions']}",
        f"- Evaluated results: {counts['evaluated_results']} (due but unpriced → missing: {counts['missing_results']})",
        f"- AI pick records: {counts['ai_picks']}",
        f"- Editorial frames: {counts['editorial_frames']}",
        f"- YouTube asset plans: {counts['youtube_asset_plans']}",
        f"- YouTube metric samples: {counts['youtube_metric_samples']}",
        "",
        "## Performance By Horizon",
        "",
        "Horizon = N TWSE sessions after the prediction-day close; hit = excess return vs the "
        "same-window TWII move beyond a dead band (neutral and missing rows are excluded). "
        "Close-to-close, stop-loss checked on the close path; not a tradable fill. "
        "Baseline 50%; 95% Wilson CI on cluster-adjusted n. "
        "Raw Hit = direction sign only; compare with TWII Same-Dir (share of windows where "
        "the index itself moved the predicted way). Avg Return / Avg Excess are direction-adjusted "
        "(a short call on a falling stock is positive). Stop = stop-loss exits (always misses); "
        "Neutral = inside the dead band (not counted); Cond. = conditional entries in the "
        "denominator whose condition was not verified (no OHLC); Unjudged = priced but no "
        "same-window TWII close (not counted).",
        "",
    ]
    calendar_warning = _calendar_warning()
    if calendar_warning:
        lines.extend([f"> ⚠️ {calendar_warning}", ""])

    if horizon_summary:
        from modules.horizon_reliability import analyze_horizon_reliability, natural_reliability_sentences

        info = analyze_horizon_reliability(horizon_summary)
        supported = {r["horizon_days"] for r in info["supported"]}
        for sentence in natural_reliability_sentences(horizon_summary):
            lines.append(f"- {sentence}")
        lines.append("")

        def _read_as(days):
            if info["primary_leads"] and days == 20:
                return "主指標（CI 下界 > 基準）"
            if days in supported:
                return "高於基準"
            return "樣本不足以判定"

        lines.append(
            _table(
                [
                    "Horizon", "Samples", "Eff. n", "Excess Hit", "95% CI", "Raw Hit", "TWII Same-Dir",
                    "Avg Return", "Avg Excess", "Coverage", "Stop / Neutral / Cond. / Unjudged", "Read as",
                ],
                [
                    [
                        f"{row['horizon_days']}D",
                        row["samples"],
                        row["effective_samples"],
                        _fmt_hit_rate(row["hit_rate"]),
                        _fmt_ci(row["hit_ci_low"], row["hit_ci_high"]),
                        _fmt_hit_rate(row["raw_hit_rate"]),
                        _fmt_hit_rate(row["index_up_rate"]),
                        _fmt_pct(row["avg_return"]),
                        _fmt_pct(row["avg_excess"]),
                        _fmt_hit_rate(row["coverage"]),
                        f"{row['stopped']} / {row['neutral']} / {row.get('conditional_entries', 0)} / "
                        f"{row.get('unjudged', 0)}",
                        _read_as(int(row["horizon_days"])),
                    ]
                    for row in horizon_summary
                ],
            )
        )
    else:
        lines.append("No evaluated prediction results yet (session-based ledger).")

    lines.extend(["", "## Performance By Direction"])
    if by_direction:
        lines.append(
            _table(
                ["Direction", "Samples", "Hit Rate", "Avg Return", "Avg Excess"],
                [
                    [
                        row["direction"] or "N/A",
                        row["samples"],
                        _fmt_hit_rate(row["hit_rate"]),
                        _fmt_pct(row["avg_return"]),
                        _fmt_pct(row["avg_excess"]),
                    ]
                    for row in by_direction
                ],
            )
        )
    else:
        lines.append("No evaluated direction stats yet.")

    lines.extend(["", "## Performance By Signal Feature (5D / 20D)"])
    unrecorded = {row["feature"] for row in attribution if row["bucket"] == "未記錄"}
    attribution = [row for row in attribution if row["samples"] and row["bucket"] != "未記錄"]
    if attribution:
        lines.append(
            "Features observable at prediction time, joined on (prediction_date, ticker). "
            "Small cells are noise; read the CI."
            + (f" Not persisted yet (shown as 未記錄): {', '.join(sorted(unrecorded))}." if unrecorded else "")
        )
        lines.append("")
        lines.append(
            _table(
                ["Horizon", "Feature", "Bucket", "Samples", "Excess Hit", "95% CI", "Avg Excess"],
                [
                    [
                        f"{row['horizon_days']}D",
                        row["feature"],
                        row["bucket"],
                        row["samples"],
                        _fmt_hit_rate(row["hit_rate"]),
                        _fmt_ci(row["hit_ci_low"], row["hit_ci_high"]),
                        _fmt_pct(row["avg_excess"]),
                    ]
                    for row in attribution
                ],
            )
        )
    else:
        lines.append("No signal attribution yet.")

    lines.extend(["", "## Recent Prediction Results"])
    if recent_results:
        lines.append(
            _table(
                [
                    "Pred Date", "Ticker", "Name", "Dir", "Horizon", "Target", "Exit Date", "Status",
                    "Return", "Index", "Excess", "Hit",
                ],
                [
                    [
                        row["prediction_date"],
                        row["ticker"],
                        row["name"] or "",
                        row["direction"] or "",
                        f"{row['horizon_days']}D",
                        row["target_date"] or "",
                        row["evaluated_date"],
                        row["status"] or "",
                        _fmt_pct(row["return_pct"]),
                        _fmt_pct(row["index_return_pct"]),
                        _fmt_pct(row["excess_return_pct"]),
                        _hit_cell(row["hit"]),
                    ]
                    for row in recent_results
                ],
            )
        )
    else:
        lines.append("No evaluated prediction results yet.")

    lines.extend(["", "## Recent Predictions"])
    if recent_predictions:
        lines.append(
            _table(
                ["Date", "Ticker", "Name", "Dir", "Trigger"],
                [
                    [
                        row["prediction_date"],
                        row["ticker"],
                        row["name"] or "",
                        row["direction"] or "",
                        row["trigger"][:80],
                    ]
                    for row in recent_predictions
                ],
            )
        )
    else:
        lines.append("No predictions yet.")

    lines.extend(["", "## YouTube Title / Thumbnail Metrics", "", "（每支影片取最新一次量測、排除週末特輯；顯示中位數）", ""])
    if youtube_variant_stats:
        lines.append(
            _table(
                ["Title Var", "Thumb Var", "Videos", "Median Views", "Max Views", "Avg CTR"],
                [
                    [
                        row["title_variant"],
                        row["thumbnail_variant"],
                        row["samples"],
                        f"{float(row['median_views']):.0f}" if row["median_views"] is not None else "N/A",
                        row["max_views"] if row["max_views"] is not None else "N/A",
                        f"{float(row['avg_ctr']):.2f}%" if row["avg_ctr"] is not None else "N/A",
                    ]
                    for row in youtube_variant_stats
                ],
            )
        )
    else:
        lines.append("No YouTube metric samples yet.")

    lines.extend(["", "## Editorial Frame Mix"])
    if editorial_frame_stats:
        lines.append(
            _table(
                ["Frame", "Samples", "Avg Confidence"],
                [
                    [
                        row["label"],
                        row["samples"],
                        f"{float(row['avg_confidence']):.0f}" if row["avg_confidence"] is not None else "N/A",
                    ]
                    for row in editorial_frame_stats
                ],
            )
        )
    else:
        lines.append("No editorial frame records yet.")

    lines.extend(["", "## Recent YouTube Measurements"])
    if youtube_baseline.get("win_threshold"):
        lines.extend(
            [
                "",
                f"（Quality：≥ 頻道中位數 {youtube_baseline['median']:.0f} × 2 = {youtube_baseline['win_threshold']:.0f} views"
                f" 才是 valid，可判 A/B；未達只作觀察；上架未滿 72 小時不判。n={youtube_baseline['n']}；"
                f"中位數基準：{youtube_baseline.get('basis') or '—'}）",
                "",
            ]
        )
    if recent_youtube_metrics:
        lines.append(
            _table(
                ["Date", "Measured At", "Views", "Quality", "Likes", "Comments", "CTR", "Title", "Thumb", "Notes"],
                [
                    [
                        row["run_date"],
                        row["measured_at"],
                        row["views"] if row["views"] is not None else "N/A",
                        _youtube_sample_quality(
                            row["views"], row["hours_since_publish"], youtube_baseline.get("win_threshold")
                        ),
                        row["likes"] if row["likes"] is not None else "N/A",
                        row["comments"] if row["comments"] is not None else "N/A",
                        f"{float(row['ctr']):.2f}%" if row["ctr"] is not None else "N/A",
                        row["title_variant"] or "",
                        row["thumbnail_variant"] or "",
                        (row["notes"] or "")[:80],
                    ]
                    for row in recent_youtube_metrics
                ],
            )
        )
    else:
        lines.append("No YouTube measurements yet.")

    lines.extend(["", "## Frequent AI Picks"])
    if top_picks:
        lines.append(
            _table(
                ["Count", "Pick"],
                [[row["count"], row["pick_text"]] for row in top_picks],
            )
        )
    else:
        lines.append("No AI picks yet.")

    return "\n".join(lines) + "\n"


def write_report(output_path=DEFAULT_OUTPUT, limit=12, db_path=None):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    content = build_report(limit=limit, db_path=db_path)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Generate a Markdown analytics summary from analytics.db.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output Markdown path.")
    parser.add_argument("--limit", type=int, default=12, help="Rows to show in recent sections.")
    args = parser.parse_args()
    output_path = write_report(args.output, args.limit)
    print(f"Analytics summary written to: {output_path}")


if __name__ == "__main__":
    main()
