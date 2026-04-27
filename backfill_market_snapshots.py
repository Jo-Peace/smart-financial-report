import argparse
import datetime
import json
import os
import sqlite3
import time

import yfinance as yf


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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


def _insert_snapshot(cur, snapshot_date, ticker, row):
    raw = {
        "symbol": ticker,
        "price": round(float(row["Close"]), 2),
        "volume": int(row["Volume"]) if not str(row["Volume"]) == "nan" else 0,
        "source": "backfill_market_snapshots.py",
    }
    cur.execute(
        """
        INSERT INTO market_snapshots (
            snapshot_date, ticker, price, pct_change, volume, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_date, ticker) DO UPDATE SET
            price = excluded.price,
            volume = excluded.volume,
            raw_json = excluded.raw_json
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


def _upsert_result(cur, prediction, horizon, evaluated_date, return_pct, index_return_pct, close_price):
    direction = prediction["direction"] or "多"
    hit = None
    if return_pct is not None:
        if direction == "空":
            hit = 1 if return_pct < 0 else 0
        else:
            hit = 1 if return_pct > 0 else 0

    excess = None
    if return_pct is not None and index_return_pct is not None:
        excess = return_pct - index_return_pct

    if hit is None:
        result_desc = "無法驗收"
    else:
        result_desc = f"{horizon}日驗收: {return_pct:+.2f}% -> {'命中' if hit else '失準'}"

    cur.execute(
        """
        INSERT INTO prediction_results (
            prediction_id, horizon_days, evaluated_date, return_pct,
            index_return_pct, excess_return_pct, hit, close_price, result_desc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(prediction_id, horizon_days) DO UPDATE SET
            evaluated_date = excluded.evaluated_date,
            return_pct = excluded.return_pct,
            index_return_pct = excluded.index_return_pct,
            excess_return_pct = excluded.excess_return_pct,
            hit = excluded.hit,
            close_price = excluded.close_price,
            result_desc = excluded.result_desc
        """,
        (
            prediction["id"],
            horizon,
            evaluated_date,
            return_pct,
            index_return_pct,
            excess,
            hit,
            close_price,
            result_desc,
        ),
    )


def _index_returns(start_date, end_date):
    _, hist = _fetch_history("^TWII", start_date, end_date)
    if hist is None or hist.empty:
        return {}

    closes = list(hist["Close"])
    dates = [_row_date(idx) for idx in hist.index]
    result = {}
    for base_idx, base_close in enumerate(closes):
        base_date = dates[base_idx]
        for horizon in HORIZONS:
            target_idx = base_idx + horizon
            if target_idx >= len(closes):
                continue
            result[(base_date, horizon)] = _pct_change(closes[target_idx], base_close)
    return result


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
    index_return_lookup = _index_returns(start_date, end_date)

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
        for horizon in HORIZONS:
            target_pos = base_pos + horizon
            if target_pos >= len(hist_rows):
                continue

            target_idx, target_row = hist_rows[target_pos]
            target_date = _row_date(target_idx)
            target_close = float(target_row["Close"])
            return_pct = _pct_change(target_close, base_close)
            index_return_pct = index_return_lookup.get((base_date, horizon))

            _insert_snapshot(cur, target_date, prediction["ticker"], target_row)
            _upsert_result(
                cur,
                prediction,
                horizon,
                target_date,
                return_pct,
                index_return_pct,
                round(target_close, 2),
            )
            updated += 1
            print(f"     {horizon}日: {target_date} {return_pct:+.2f}%")

        conn.commit()
        time.sleep(sleep_seconds)

    conn.close()
    print("\nMarket snapshot backfill complete.")
    print(f"  Updated result rows: {updated}")
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
