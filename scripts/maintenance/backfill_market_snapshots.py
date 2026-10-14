"""
Backfill market_snapshots (prediction stock path + ^TWII) from yfinance, then
re-grade the sessions_v2 ledger rows through AnalyticsStore (the single hit
implementation: TWSE-session horizons, same-window TWII excess, dead band,
stop path). Legacy rows are left for recompute_prediction_results.py.
"""

import argparse
import datetime
import json
import os
import sqlite3
import sys
import time

import yfinance as yf


BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
DB_PATH = os.path.join(BASE_DIR, "data", "analytics.db")
HORIZONS = (1, 3, 5, 20)


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _yf_symbol(ticker):
    if ticker.startswith("^"):
        return ticker
    if ticker.isdigit():
        return f"{ticker}.TW"
    return ticker


def _fetch_history(ticker, start_date, end_date):
    symbols = [_yf_symbol(ticker)]
    if ticker.isdigit():
        symbols.append(f"{ticker}.TWO")

    for symbol in symbols:
        try:
            data = yf.Ticker(symbol).history(start=start_date, end=end_date, auto_adjust=False)
            if not data.empty:
                return symbol, data
        except Exception as e:
            print(f"  ⚠️  {symbol} 下載失敗: {e}")
    return None, None


def _row_date(index_value):
    return index_value.date().isoformat()


def _pct_change(current, base):
    if base in (None, 0):
        return None
    return (float(current) - float(base)) / float(base) * 100


def _insert_snapshot(cur, snapshot_date, ticker, row, overwrite=True):
    raw = {
        "symbol": ticker,
        "price": round(float(row["Close"]), 2),
        "volume": int(row["Volume"]) if not str(row["Volume"]) == "nan" else 0,
        "source": "backfill_market_snapshots.py",
        "bar_date": snapshot_date,
    }
    conflict = (
        """
        ON CONFLICT(snapshot_date, ticker) DO UPDATE SET
            price = excluded.price,
            volume = excluded.volume,
            raw_json = excluded.raw_json
        """
        if overwrite
        else "ON CONFLICT(snapshot_date, ticker) DO NOTHING"
    )
    cur.execute(
        f"""
        INSERT INTO market_snapshots (
            snapshot_date, ticker, price, pct_change, volume, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?)
        {conflict}
        """,
        (
            snapshot_date,
            ticker,
            raw["price"],
            None,
            raw["volume"],
            json.dumps(raw, ensure_ascii=False, sort_keys=True),
        ),
    )


def _insert_index_snapshots(cur, start_date, end_date):
    """^TWII closes for the same-window index return (no own hit math here)."""
    _, hist = _fetch_history("^TWII", start_date, end_date)
    if hist is None or hist.empty:
        print("  ⚠️  ^TWII 歷史下載失敗：同窗大盤報酬只能用既有 snapshot")
        return 0
    for idx, row in hist.iterrows():
        _insert_snapshot(cur, _row_date(idx), "^TWII", row, overwrite=False)
    return len(hist)


def backfill(limit=None, sleep_seconds=0.3):
    conn = _connect()
    cur = conn.cursor()
    predictions = cur.execute(
        """
        SELECT *
        FROM predictions
        ORDER BY prediction_date, ticker
        """
    ).fetchall()
    if limit:
        predictions = predictions[:limit]

    if not predictions:
        print("No predictions found in analytics.db.")
        conn.close()
        return

    min_date = min(datetime.date.fromisoformat(p["prediction_date"]) for p in predictions)
    max_date = max(datetime.date.fromisoformat(p["prediction_date"]) for p in predictions)
    start_date = (min_date - datetime.timedelta(days=5)).isoformat()
    end_date = (max_date + datetime.timedelta(days=45)).isoformat()
    _insert_index_snapshots(cur, start_date, end_date)
    conn.commit()

    updated = 0
    skipped = 0
    for prediction in predictions:
        pred_date = datetime.date.fromisoformat(prediction["prediction_date"])
        fetch_start = (pred_date - datetime.timedelta(days=5)).isoformat()
        fetch_end = (pred_date + datetime.timedelta(days=45)).isoformat()

        symbol, hist = _fetch_history(prediction["ticker"], fetch_start, fetch_end)
        if hist is None or hist.empty:
            skipped += 1
            print(f"  ⚠️  找不到行情: {prediction['prediction_date']} {prediction['ticker']}")
            continue

        trading_rows = []
        for idx, row in hist.iterrows():
            row_day = idx.date()
            if row_day <= pred_date:
                trading_rows.append((idx, row))
        if not trading_rows:
            skipped += 1
            print(f"  ⚠️  找不到預測日前基準價: {prediction['prediction_date']} {prediction['ticker']}")
            continue

        base_idx, base_row = trading_rows[-1]
        hist_rows = list(hist.iterrows())
        base_pos = next(i for i, (idx, _) in enumerate(hist_rows) if idx == base_idx)
        base_date = _row_date(base_idx)
        base_close = float(base_row["Close"])
        _insert_snapshot(cur, base_date, prediction["ticker"], base_row)

        print(f"  📊 {prediction['prediction_date']} {prediction['ticker']} {prediction['name']} ({symbol})")
        # Whole path (stop-loss check) up to the longest horizon; targets overwrite.
        targets = {base_pos + horizon for horizon in HORIZONS}
        for pos in range(base_pos + 1, min(len(hist_rows), base_pos + max(HORIZONS) + 1)):
            path_idx, path_row = hist_rows[pos]
            _insert_snapshot(cur, _row_date(path_idx), prediction["ticker"], path_row, overwrite=pos in targets)
            updated += 1
        for horizon in HORIZONS:
            target_pos = base_pos + horizon
            if target_pos < len(hist_rows):
                target_idx, target_row = hist_rows[target_pos]
                print(
                    f"     {horizon}日: {_row_date(target_idx)} "
                    f"{_pct_change(float(target_row['Close']), base_close):+.2f}%（未扣大盤；正式結果由帳本重算）"
                )

        conn.commit()
        time.sleep(sleep_seconds)

    conn.close()
    from modules.analytics_store import AnalyticsStore

    # sessions_v2 rows only: legacy rows are converted by
    # recompute_prediction_results.py (dry-run first, --apply backs up).
    store = AnalyticsStore(DB_PATH)
    regraded = store.recompute_prediction_results(regrade_legacy=False)
    legacy = store.legacy_result_count()
    print("\nMarket snapshot backfill complete.")
    print(f"  Snapshot rows written: {updated}")
    print(f"  Ledger results re-graded: {regraded}")
    if legacy:
        print(
            f"  Legacy (calendar-day) results left untouched: {legacy} — run "
            "scripts/maintenance/recompute_prediction_results.py (dry-run) and then --apply"
        )
    print(f"  Skipped predictions: {skipped}")
    print(f"  DB: {DB_PATH}")


def main():
    parser = argparse.ArgumentParser(
        description="Backfill market snapshots and deterministic prediction results with yfinance."
    )
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N predictions.")
    parser.add_argument("--sleep", type=float, default=0.3, help="Seconds to sleep between ticker requests.")
    args = parser.parse_args()
    backfill(limit=args.limit, sleep_seconds=args.sleep)


if __name__ == "__main__":
    main()
