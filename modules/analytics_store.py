import datetime
import json
import os
import sqlite3


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "analytics.db")


def _json_dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _normalize_ticker(value):
    if value is None:
        return ""
    return str(value).replace(".TW", "").replace(".TWO", "").strip().upper()


def _parse_date(value):
    if isinstance(value, datetime.date):
        return value
    return datetime.datetime.strptime(str(value), "%Y-%m-%d").date()


class AnalyticsStore:
    """
    Local analytics database for compounding project data without extra AI calls.

    The database stores structured report output, market snapshots, prediction
    targets, and deterministic evaluation results. It intentionally avoids
    Gemini/OpenAI usage.
    """

    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        conn = self._connect()
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_runs (
                run_date TEXT PRIMARY KEY,
                report_path TEXT,
                structured_data_path TEXT,
                index_action TEXT,
                conservative_strategy TEXT,
                aggressive_strategy TEXT,
                raw_json TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_picks (
                run_date TEXT NOT NULL,
                rank INTEGER NOT NULL,
                pick_text TEXT NOT NULL,
                PRIMARY KEY (run_date, rank)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
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
                UNIQUE(prediction_date, ticker, direction, trigger)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS prediction_results (
                prediction_id INTEGER NOT NULL,
                horizon_days INTEGER NOT NULL,
                evaluated_date TEXT NOT NULL,
                return_pct REAL,
                index_return_pct REAL,
                excess_return_pct REAL,
                hit INTEGER,
                close_price REAL,
                result_desc TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (prediction_id, horizon_days),
                FOREIGN KEY (prediction_id) REFERENCES predictions(id)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS market_snapshots (
                snapshot_date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                price REAL,
                pct_change REAL,
                volume INTEGER,
                avg_vol_5d INTEGER,
                vol_ratio REAL,
                ma5 REAL,
                ma20 REAL,
                rsi REAL,
                raw_json TEXT NOT NULL,
                PRIMARY KEY (snapshot_date, ticker)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS institutional_flows (
                flow_date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                name TEXT,
                side TEXT NOT NULL,
                rank INTEGER NOT NULL,
                foreign_net INTEGER,
                trust_net INTEGER,
                total_net INTEGER,
                est_amount REAL,
                raw_json TEXT NOT NULL,
                PRIMARY KEY (flow_date, ticker, side)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS volume_rankings (
                ranking_date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                name TEXT,
                rank INTEGER NOT NULL,
                volume INTEGER,
                close_price REAL,
                pct_change REAL,
                raw_json TEXT NOT NULL,
                PRIMARY KEY (ranking_date, ticker)
            )
        """)

        conn.commit()
        conn.close()

    def record_daily_run(
        self,
        run_date,
        structured_data,
        report_path=None,
        structured_data_path=None,
        market_data=None,
        institutional_data=None,
        volume_data=None,
    ):
        run_date = str(run_date)
        structured_data = structured_data or {}

        conn = self._connect()
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO daily_runs (
                run_date, report_path, structured_data_path, index_action,
                conservative_strategy, aggressive_strategy, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_date) DO UPDATE SET
                report_path = excluded.report_path,
                structured_data_path = excluded.structured_data_path,
                index_action = excluded.index_action,
                conservative_strategy = excluded.conservative_strategy,
                aggressive_strategy = excluded.aggressive_strategy,
                raw_json = excluded.raw_json
            """,
            (
                run_date,
                report_path,
                structured_data_path,
                structured_data.get("index_action", ""),
                structured_data.get("conservative_strategy", ""),
                structured_data.get("aggressive_strategy", ""),
                _json_dumps(structured_data),
            ),
        )

        cur.execute("DELETE FROM ai_picks WHERE run_date = ?", (run_date,))
        for idx, pick in enumerate(structured_data.get("ai_data_picks", []) or [], start=1):
            cur.execute(
                "INSERT INTO ai_picks (run_date, rank, pick_text) VALUES (?, ?, ?)",
                (run_date, idx, str(pick)),
            )

        for pred in structured_data.get("prediction_targets", []) or []:
            ticker = _normalize_ticker(pred.get("id"))
            if not ticker:
                continue
            cur.execute(
                """
                INSERT INTO predictions (
                    prediction_date, ticker, name, direction, trigger,
                    stop_loss_price, stop_loss_desc, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(prediction_date, ticker, direction, trigger) DO UPDATE SET
                    name = excluded.name,
                    stop_loss_price = excluded.stop_loss_price,
                    stop_loss_desc = excluded.stop_loss_desc,
                    raw_json = excluded.raw_json
                """,
                (
                    run_date,
                    ticker,
                    pred.get("name", ""),
                    pred.get("direction", "多"),
                    pred.get("trigger", ""),
                    pred.get("stop_loss_price"),
                    pred.get("stop_loss_desc", ""),
                    _json_dumps(pred),
                ),
            )

        self._record_market_snapshots(cur, run_date, market_data or {})
        self._record_institutional_flows(cur, run_date, institutional_data or {})
        self._record_volume_rankings(cur, run_date, volume_data or [])
        self._evaluate_due_predictions(cur, run_date)

        conn.commit()
        summary = self.get_summary(conn)
        conn.close()
        return summary

    def _record_market_snapshots(self, cur, run_date, market_data):
        for symbol, data in market_data.items():
            if not data:
                continue
            ticker = _normalize_ticker(symbol)
            cur.execute(
                """
                INSERT INTO market_snapshots (
                    snapshot_date, ticker, price, pct_change, volume, avg_vol_5d,
                    vol_ratio, ma5, ma20, rsi, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_date, ticker) DO UPDATE SET
                    price = excluded.price,
                    pct_change = excluded.pct_change,
                    volume = excluded.volume,
                    avg_vol_5d = excluded.avg_vol_5d,
                    vol_ratio = excluded.vol_ratio,
                    ma5 = excluded.ma5,
                    ma20 = excluded.ma20,
                    rsi = excluded.rsi,
                    raw_json = excluded.raw_json
                """,
                (
                    run_date,
                    ticker,
                    data.get("price"),
                    data.get("pct_change"),
                    data.get("volume"),
                    data.get("avg_vol_5d"),
                    data.get("vol_ratio"),
                    data.get("ma5"),
                    data.get("ma20"),
                    data.get("rsi"),
                    _json_dumps(data),
                ),
            )

    def _record_institutional_flows(self, cur, run_date, institutional_data):
        data_date = institutional_data.get("data_date") or run_date
        groups = [("buy", institutional_data.get("top_buy", [])), ("sell", institutional_data.get("top_sell", []))]
        for side, rows in groups:
            for idx, row in enumerate(rows or [], start=1):
                ticker = _normalize_ticker(row.get("id"))
                if not ticker:
                    continue
                cur.execute(
                    """
                    INSERT INTO institutional_flows (
                        flow_date, ticker, name, side, rank, foreign_net,
                        trust_net, total_net, est_amount, raw_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(flow_date, ticker, side) DO UPDATE SET
                        name = excluded.name,
                        rank = excluded.rank,
                        foreign_net = excluded.foreign_net,
                        trust_net = excluded.trust_net,
                        total_net = excluded.total_net,
                        est_amount = excluded.est_amount,
                        raw_json = excluded.raw_json
                    """,
                    (
                        data_date,
                        ticker,
                        row.get("name", ""),
                        side,
                        idx,
                        row.get("foreign_net"),
                        row.get("trust_net"),
                        row.get("total_net"),
                        row.get("est_amount"),
                        _json_dumps(row),
                    ),
                )

    def _record_volume_rankings(self, cur, run_date, volume_data):
        for row in volume_data or []:
            ticker = _normalize_ticker(row.get("id"))
            if not ticker:
                continue
            cur.execute(
                """
                INSERT INTO volume_rankings (
                    ranking_date, ticker, name, rank, volume, close_price,
                    pct_change, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ranking_date, ticker) DO UPDATE SET
                    name = excluded.name,
                    rank = excluded.rank,
                    volume = excluded.volume,
                    close_price = excluded.close_price,
                    pct_change = excluded.pct_change,
                    raw_json = excluded.raw_json
                """,
                (
                    run_date,
                    ticker,
                    row.get("name", ""),
                    row.get("rank"),
                    row.get("volume"),
                    row.get("close_price"),
                    row.get("pct_change"),
                    _json_dumps(row),
                ),
            )

    def _evaluate_due_predictions(self, cur, run_date):
        today = _parse_date(run_date)
        rows = cur.execute("SELECT * FROM predictions").fetchall()
        for pred in rows:
            prediction_date = _parse_date(pred["prediction_date"])
            elapsed_days = (today - prediction_date).days
            if elapsed_days <= 0:
                continue

            for horizon in (1, 3, 5, 20):
                if elapsed_days < horizon:
                    continue
                exists = cur.execute(
                    "SELECT 1 FROM prediction_results WHERE prediction_id = ? AND horizon_days = ?",
                    (pred["id"], horizon),
                ).fetchone()
                if exists:
                    continue

                snapshot = cur.execute(
                    """
                    SELECT price, pct_change
                    FROM market_snapshots
                    WHERE snapshot_date = ? AND ticker = ?
                    """,
                    (run_date, pred["ticker"]),
                ).fetchone()
                if not snapshot:
                    continue

                base_snapshot = cur.execute(
                    """
                    SELECT price
                    FROM market_snapshots
                    WHERE snapshot_date = ? AND ticker = ?
                    """,
                    (pred["prediction_date"], pred["ticker"]),
                ).fetchone()

                index_snapshot = cur.execute(
                    """
                    SELECT pct_change
                    FROM market_snapshots
                    WHERE snapshot_date = ? AND ticker IN ('^TWII', 'TWII')
                    LIMIT 1
                    """,
                    (run_date,),
                ).fetchone()

                if base_snapshot and base_snapshot["price"] and snapshot["price"]:
                    return_pct = (float(snapshot["price"]) - float(base_snapshot["price"])) / float(base_snapshot["price"]) * 100
                elif horizon == 1:
                    return_pct = snapshot["pct_change"]
                else:
                    continue

                index_return_pct = index_snapshot["pct_change"] if index_snapshot else None
                excess = None
                if return_pct is not None and index_return_pct is not None:
                    excess = float(return_pct) - float(index_return_pct)

                direction = pred["direction"] or "多"
                hit = None
                if return_pct is not None:
                    if direction == "空":
                        hit = 1 if float(return_pct) < 0 else 0
                    else:
                        hit = 1 if float(return_pct) > 0 else 0

                if hit is None:
                    desc = "無法驗收"
                else:
                    desc = f"{horizon}日驗收: {float(return_pct):+.2f}% -> {'命中' if hit else '失準'}"

                cur.execute(
                    """
                    INSERT INTO prediction_results (
                        prediction_id, horizon_days, evaluated_date, return_pct,
                        index_return_pct, excess_return_pct, hit, close_price,
                        result_desc
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pred["id"],
                        horizon,
                        run_date,
                        return_pct,
                        index_return_pct,
                        excess,
                        hit,
                        snapshot["price"],
                        desc,
                    ),
                )

    def get_summary(self, conn=None):
        own_conn = conn is None
        if own_conn:
            conn = self._connect()
        cur = conn.cursor()

        daily_runs = cur.execute("SELECT COUNT(*) AS count FROM daily_runs").fetchone()["count"]
        predictions = cur.execute("SELECT COUNT(*) AS count FROM predictions").fetchone()["count"]
        evaluated = cur.execute("SELECT COUNT(*) AS count FROM prediction_results").fetchone()["count"]
        hit_row = cur.execute(
            """
            SELECT AVG(hit) AS hit_rate
            FROM prediction_results
            WHERE hit IS NOT NULL
            """
        ).fetchone()
        hit_rate = hit_row["hit_rate"]

        if own_conn:
            conn.close()

        return {
            "db_path": self.db_path,
            "daily_runs": daily_runs,
            "predictions": predictions,
            "evaluated_results": evaluated,
            "hit_rate": round(hit_rate * 100, 1) if hit_rate is not None else None,
        }
