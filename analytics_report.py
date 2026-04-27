import argparse
import datetime
import os
import sqlite3


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "analytics.db")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
DEFAULT_OUTPUT = os.path.join(REPORTS_DIR, "analytics_summary.md")


def _connect():
    conn = sqlite3.connect(DB_PATH)
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


def _table(headers, rows):
    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        lines.append("| " + " | ".join(str(cell).replace("\n", " ") for cell in row) + " |")
    return "\n".join(lines)


def build_report(limit=12):
    conn = _connect()
    cur = conn.cursor()

    counts = cur.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM daily_runs) AS daily_runs,
            (SELECT COUNT(*) FROM predictions) AS predictions,
            (SELECT COUNT(*) FROM prediction_results) AS evaluated_results,
            (SELECT COUNT(*) FROM ai_picks) AS ai_picks
        """
    ).fetchone()

    by_horizon = cur.execute(
        """
        SELECT
            horizon_days,
            COUNT(*) AS samples,
            AVG(hit) AS hit_rate,
            AVG(return_pct) AS avg_return,
            AVG(excess_return_pct) AS avg_excess
        FROM prediction_results
        GROUP BY horizon_days
        ORDER BY horizon_days
        """
    ).fetchall()

    by_direction = cur.execute(
        """
        SELECT
            p.direction,
            COUNT(*) AS samples,
            AVG(r.hit) AS hit_rate,
            AVG(r.return_pct) AS avg_return,
            AVG(r.excess_return_pct) AS avg_excess
        FROM prediction_results r
        JOIN predictions p ON p.id = r.prediction_id
        GROUP BY p.direction
        ORDER BY samples DESC
        """
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
            r.horizon_days, r.evaluated_date, r.return_pct,
            r.index_return_pct, r.excess_return_pct, r.hit
        FROM prediction_results r
        JOIN predictions p ON p.id = r.prediction_id
        ORDER BY r.evaluated_date DESC, p.prediction_date DESC, p.ticker, r.horizon_days
        LIMIT ?
        """,
        (limit,),
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
        f"- Evaluated results: {counts['evaluated_results']}",
        f"- AI pick records: {counts['ai_picks']}",
        "",
        "## Performance By Horizon",
    ]

    if by_horizon:
        lines.append(
            _table(
                ["Horizon", "Samples", "Hit Rate", "Avg Return", "Avg Excess"],
                [
                    [
                        f"{row['horizon_days']}D",
                        row["samples"],
                        _fmt_hit_rate(row["hit_rate"]),
                        _fmt_pct(row["avg_return"]),
                        _fmt_pct(row["avg_excess"]),
                    ]
                    for row in by_horizon
                ],
            )
        )
    else:
        lines.append("No evaluated prediction results yet.")

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

    lines.extend(["", "## Recent Prediction Results"])
    if recent_results:
        lines.append(
            _table(
                ["Pred Date", "Ticker", "Name", "Dir", "Horizon", "Eval Date", "Return", "Index", "Excess", "Hit"],
                [
                    [
                        row["prediction_date"],
                        row["ticker"],
                        row["name"] or "",
                        row["direction"] or "",
                        f"{row['horizon_days']}D",
                        row["evaluated_date"],
                        _fmt_pct(row["return_pct"]),
                        _fmt_pct(row["index_return_pct"]),
                        _fmt_pct(row["excess_return_pct"]),
                        "Y" if row["hit"] == 1 else "N",
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


def write_report(output_path=DEFAULT_OUTPUT, limit=12):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    content = build_report(limit=limit)
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
