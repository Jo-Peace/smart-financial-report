#!/usr/bin/env python3
"""
Re-grade the whole prediction ledger with the current measurement contract.

Default is a DRY RUN on a temporary copy of analytics.db: nothing under data/
is modified. It prints the legacy per-horizon stats next to the recomputed
ones (TWSE-session horizons, same-window TWII excess with dead band, stop-loss
path, missing instead of later-day substitutes), plus ledger hygiene issues:

- duplicate (prediction_date, ticker) rows left by the old free-text key;
- orphan predictions that are not in that date's final structured_data.json
  (compared on (date, ticker) only — triggers were sometimes reworded later);
- stock_db tracking history rows repeated by same-day reruns.

--apply writes the same changes to the real files after a timestamped backup:
dedupe + recompute in analytics.db, and (with --stock-db-dedupe) tracking
dedupe in stock_db.json. --prune-orphans additionally deletes orphan
predictions and their results. No network, no Gemini.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.analytics_store import AnalyticsStore  # noqa: E402
from modules.horizon_reliability import LEDGER_METHOD, natural_reliability_sentences  # noqa: E402
from modules.report_paths import date_from_structured_path, iter_structured_data_paths  # noqa: E402

DB_PATH = os.path.join(BASE_DIR, "data", "analytics.db")
STOCK_DB_PATH = os.path.join(BASE_DIR, "data", "stock_db.json")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")


def _ro_connect(path):
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def legacy_horizon_stats(db_path):
    """
    What the old summary published: COUNT(*) / AVG(hit) / AVG(excess) over the
    legacy (pre-sessions_v2) rows. Rows the daily /1 path already wrote with
    the new method are left out so the comparison stays before/after.
    """
    conn = _ro_connect(db_path)
    try:
        has_method = any(r[1] == "method" for r in conn.execute("PRAGMA table_info(prediction_results)"))
        where = "WHERE method IS NULL OR method != ?" if has_method else ""
        rows = conn.execute(
            f"""
            SELECT horizon_days, COUNT(*) AS samples, AVG(hit) AS hit_rate,
                   AVG(return_pct) AS avg_return, AVG(excess_return_pct) AS avg_excess
            FROM prediction_results
            {where}
            GROUP BY horizon_days
            ORDER BY horizon_days
            """,
            (LEDGER_METHOD,) if has_method else (),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def final_targets_by_date(reports_dir):
    """{date: {ticker, ...}} from the final structured_data.json of each date."""
    out = {}
    from_daily_layout = set()
    daily_marker = os.sep + "daily" + os.sep
    for path in iter_structured_data_paths(reports_dir):
        run_date = date_from_structured_path(path)
        if not run_date:
            continue
        is_daily = daily_marker in path
        if run_date in from_daily_layout and not is_daily:
            continue  # reports/daily/<date>/structured_data.json is the final copy
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if "prediction_targets" not in data:
            continue
        tickers = set()
        for pred in data.get("prediction_targets") or []:
            if isinstance(pred, dict) and pred.get("id"):
                tickers.add(str(pred["id"]).replace(".TWO", "").replace(".TW", "").strip().upper())
        out[run_date] = tickers
        if is_daily:
            from_daily_layout.add(run_date)
    return out


def ledger_issues(conn, final_targets):
    rows = conn.execute("SELECT id, prediction_date, ticker FROM predictions ORDER BY id").fetchall()
    newest = {}
    duplicates = []
    for row in rows:
        key = (row["prediction_date"], row["ticker"])
        if key in newest:
            duplicates.append(newest[key])
        newest[key] = row["id"]
    orphans = []
    for (day, ticker), pred_id in sorted(newest.items()):
        if day in final_targets and ticker not in final_targets[day]:
            orphans.append({"id": pred_id, "prediction_date": day, "ticker": ticker})
    return {"duplicate_ids": sorted(duplicates), "orphans": orphans}


def _delete_predictions(conn, ids):
    for pred_id in ids:
        conn.execute("DELETE FROM prediction_results WHERE prediction_id = ?", (pred_id,))
        conn.execute("DELETE FROM predictions WHERE id = ?", (pred_id,))


PREDICTIONS_TABLE_SQL = """
CREATE TABLE {name} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    name TEXT,
    direction TEXT,
    trigger TEXT,
    stop_loss_price REAL,
    stop_loss_desc TEXT,
    raw_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(prediction_date, ticker)
)
"""
PREDICTION_COLUMNS = (
    "id, prediction_date, ticker, name, direction, trigger, "
    "stop_loss_price, stop_loss_desc, raw_json, created_at"
)


def migrate_prediction_key(conn):
    """
    Rebuild predictions with UNIQUE(prediction_date, ticker) once duplicates are gone.

    Uses SQLite's documented rebuild order (create new → copy → drop old →
    rename new) with foreign keys off. Renaming the *old* table away instead
    would make SQLite >= 3.26 rewrite prediction_results' FOREIGN KEY to point
    at the renamed table, which is then dropped (dangling reference).
    """
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='predictions'").fetchone()
    compact = "".join((sql[0] if sql else "").split()).lower()
    if "unique(prediction_date,ticker,direction,trigger)" not in compact:
        return False
    dup = conn.execute(
        "SELECT 1 FROM predictions GROUP BY prediction_date, ticker HAVING COUNT(*) > 1 LIMIT 1"
    ).fetchone()
    if dup:
        return False
    conn.commit()
    seq_row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'predictions'").fetchone()
    old_seq = int(seq_row[0]) if seq_row and seq_row[0] is not None else 0
    fk_was_on = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    conn.execute("PRAGMA foreign_keys=OFF")  # no-op inside a transaction, so set it first
    try:
        conn.executescript(
            "BEGIN;"
            "DROP INDEX IF EXISTS idx_predictions_date_ticker;"
            + PREDICTIONS_TABLE_SQL.format(name="predictions_new")
            + ";"
            f"INSERT INTO predictions_new ({PREDICTION_COLUMNS}) SELECT {PREDICTION_COLUMNS} FROM predictions;"
            "DROP TABLE predictions;"
            "ALTER TABLE predictions_new RENAME TO predictions;"
            # Keep AUTOINCREMENT from reusing ids of deleted duplicates / orphans.
            f"UPDATE sqlite_sequence SET seq = MAX(seq, {old_seq}) WHERE name = 'predictions';"
            "COMMIT;"
        )
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        if fk_was_on:
            conn.execute("PRAGMA foreign_keys=ON")
    problems = conn.execute("PRAGMA foreign_key_check").fetchall()
    if problems:
        raise RuntimeError(f"foreign_key_check failed after predictions rebuild: {problems[:5]}")
    return True


def regrade(db_path, as_of, final_targets, prune_orphans=False):
    """Dedupe (+ optional orphan prune), migrate key, recompute. Mutates db_path."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    issues = ledger_issues(conn, final_targets)
    _delete_predictions(conn, issues["duplicate_ids"])
    if prune_orphans:
        _delete_predictions(conn, [o["id"] for o in issues["orphans"]])
    conn.commit()
    migrated = migrate_prediction_key(conn)
    conn.commit()
    conn.close()
    store = AnalyticsStore(db_path)
    written = store.recompute_prediction_results(as_of, regrade_legacy=True)
    conn = sqlite3.connect(db_path)
    status_counts = dict(
        conn.execute(
            "SELECT COALESCE(status, 'legacy'), COUNT(*) FROM prediction_results GROUP BY 1"
        ).fetchall()
    )
    conn.close()
    return {
        "issues": issues,
        "migrated_key": migrated,
        "written": written,
        "status_counts": status_counts,
        "summary": store.get_horizon_performance_summary(),
    }


def tracking_dedupe_preview(stock_db):
    from modules.stock_db_updater import dedupe_tracking_history

    changes = []
    for sid, entry in sorted((stock_db or {}).items()):
        tracking = (entry or {}).get("tracking")
        if not tracking or not tracking.get("history"):
            continue
        before = (tracking.get("prediction_count"), tracking.get("hit_rate"))
        removed = dedupe_tracking_history(tracking)
        if removed:
            changes.append(
                {
                    "sid": sid,
                    "name": entry.get("name", ""),
                    "removed": removed,
                    "before": before,
                    "after": (tracking.get("prediction_count"), tracking.get("hit_rate")),
                }
            )
    return changes


def _pct(value, scale=100.0):
    return "N/A" if value is None else f"{float(value) * scale:.1f}%"


def _signed(value):
    return "N/A" if value is None else f"{float(value):+.2f}%"


def format_report(legacy, result, tracking_changes, apply):
    lines = [
        f"# Prediction ledger re-grade（{'APPLY' if apply else 'DRY RUN：data/ 未修改'}）",
        "",
        "## Legacy（日曆天、錯窗補登、單日大盤）",
        "",
        "| Horizon | n | Hit | Avg Return | Avg Excess |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in legacy:
        lines.append(
            f"| {row['horizon_days']}D | {row['samples']} | {_pct(row['hit_rate'])} | "
            f"{_signed(row['avg_return'])} | {_signed(row['avg_excess'])} |"
        )
    lines += [
        "",
        "## Recomputed（交易日窗、同窗大盤、dead band、停損路徑）",
        "",
        "| Horizon | n | Eff. n | Excess Hit | 95% CI | Raw Hit | TWII same-dir | Avg Excess | Coverage | Stop / Neutral / Cond. / Unjudged |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in result["summary"]:
        ci = (
            f"{row['hit_ci_low'] * 100:.0f}–{row['hit_ci_high'] * 100:.0f}%"
            if row.get("hit_ci_low") is not None
            else "N/A"
        )
        lines.append(
            f"| {row['horizon_days']}D | {row['samples']} | {row['effective_samples']} | {_pct(row['hit_rate'])} | "
            f"{ci} | {_pct(row['raw_hit_rate'])} | {_pct(row['index_up_rate'])} | {_signed(row['avg_excess'])} | "
            f"{_pct(row['coverage'])} | {row['stopped']} / {row['neutral']} / {row['conditional_entries']} / "
            f"{row['unjudged']} |"
        )
    lines += ["", "結論句（對外文字將使用）："]
    lines += [f"- {s}" for s in natural_reliability_sentences(result["summary"])] or ["- （無樣本）"]
    issues = result["issues"]
    lines += [
        "",
        "## 帳本整理",
        f"- 重複 (date, ticker) 預測：{len(issues['duplicate_ids'])} 筆（保留最新一筆）{issues['duplicate_ids']}",
        f"- 不在最終 structured_data.json 的孤兒預測：{len(issues['orphans'])} 筆"
        + ("" if not issues["orphans"] else "：" + "、".join(f"{o['prediction_date']} {o['ticker']}" for o in issues["orphans"])),
        f"- 唯一鍵改為 (prediction_date, ticker)：{'是' if result['migrated_key'] else '否（已是新鍵或仍有重複）'}",
        f"- 重算結果列數：{result['written']}；狀態分布：{result['status_counts']}",
    ]
    if tracking_changes is not None:
        lines += ["", "## stock_db tracking 同日重複"]
        if tracking_changes:
            for c in tracking_changes:
                lines.append(
                    f"- {c['sid']} {c['name']}：移除 {c['removed']} 筆；n/命中率 {c['before']} → {c['after']}"
                )
        else:
            lines.append("- 無")
    return "\n".join(lines) + "\n"


def run(
    db_path=DB_PATH,
    as_of=None,
    reports_dir=REPORTS_DIR,
    stock_db_path=STOCK_DB_PATH,
    apply=False,
    prune_orphans=False,
    stock_db_dedupe=False,
):
    as_of = str(as_of or datetime.date.today())
    if not os.path.isfile(db_path):
        raise FileNotFoundError(db_path)
    legacy = legacy_horizon_stats(db_path)
    final_targets = final_targets_by_date(reports_dir) if reports_dir and os.path.isdir(reports_dir) else {}
    stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    if apply:
        shutil.copy2(db_path, f"{db_path}.bak-{stamp}")
        result = regrade(db_path, as_of, final_targets, prune_orphans=prune_orphans)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            copy = os.path.join(tmp, "analytics.db")
            src = _ro_connect(db_path)
            dst = sqlite3.connect(copy)
            src.backup(dst)
            src.close()
            dst.close()
            result = regrade(copy, as_of, final_targets, prune_orphans=prune_orphans)

    tracking_changes = None
    if stock_db_path and os.path.isfile(stock_db_path):
        with open(stock_db_path, "r", encoding="utf-8") as f:
            stock_db = json.load(f)
        tracking_changes = tracking_dedupe_preview(stock_db)
        if apply and stock_db_dedupe and tracking_changes:
            shutil.copy2(stock_db_path, f"{stock_db_path}.bak-{stamp}")
            with open(stock_db_path, "w", encoding="utf-8") as f:
                json.dump(stock_db, f, ensure_ascii=False, indent=2)
    return format_report(legacy, result, tracking_changes, apply), result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Re-grade prediction_results with the session-based ledger (dry-run by default).")
    parser.add_argument("--db", default=DB_PATH, help="analytics.db path")
    parser.add_argument("--as-of", default=None, help="Grade horizons due on or before this date (default today)")
    parser.add_argument("--reports-dir", default=REPORTS_DIR, help="reports/ root for final structured_data.json")
    parser.add_argument("--stock-db", default=STOCK_DB_PATH, help="stock_db.json path (tracking dedupe preview)")
    parser.add_argument("--apply", action="store_true", help="Write changes (backs up the files first)")
    parser.add_argument("--prune-orphans", action="store_true", help="Also delete predictions missing from final JSON")
    parser.add_argument("--stock-db-dedupe", action="store_true", help="With --apply: dedupe stock_db tracking history")
    parser.add_argument("--output", default=None, help="Also write the Markdown report to this path")
    args = parser.parse_args(argv)

    text, _ = run(
        db_path=args.db,
        as_of=args.as_of,
        reports_dir=args.reports_dir,
        stock_db_path=args.stock_db,
        apply=args.apply,
        prune_orphans=args.prune_orphans,
        stock_db_dedupe=args.stock_db_dedupe,
    )
    print(text)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
