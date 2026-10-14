import datetime
import json
import os
import re
import sqlite3

from modules import twse_calendar
from modules.horizon_reliability import LEDGER_METHOD, wilson_interval
from modules.prediction_validator import classify_hit, direction_sign
from modules.tickers import bare_ticker


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "analytics.db")

# Prediction ledger measurement contract (see _evaluate_due_predictions):
# horizon_days counts TWSE sessions (not calendar days) after the anchor session.
EVAL_METHOD = LEDGER_METHOD  # "sessions_v2"
EVAL_HORIZONS = (1, 3, 5, 20)
ENTRY_BASIS = "close_to_close"  # anchor close → exit close; not a tradable fill
INDEX_TICKERS = ("^TWII", "TWII")
BAR_DATE_KEYS = ("bar_date", "price_date", "trade_date", "data_date")
INFLIGHT_MAX_SYMBOLS = 15
EXCESS_HIT_BASELINE = 0.5

# The only path that converts legacy (pre-sessions_v2, method NULL) result rows.
# Dry-run first; --apply writes a timestamped backup of analytics.db before changing it.
RECOMPUTE_COMMAND = "python3 cli.py maintenance recompute-predictions"


def legacy_ledger_warning(legacy: int, current: int | None = None) -> str:
    """One-line warning: hit-rate stats only count sessions_v2 rows until legacy rows are converted."""
    if not legacy:
        return ""
    counted = f"只算 {current} 筆新制" if current is not None else "只算新制結果"
    return (
        f"⚠️  命中率統計不完整：帳本有 {legacy} 筆舊制（method=NULL，日曆天窗）驗收結果未轉換，"
        f"命中率／時間窗{counted}；不會自動轉換——先 `{RECOMPUTE_COMMAND}`（dry-run 比對），"
        f"確認後加 `--apply`（會先備份 analytics.db）"
    )


def ledger_legacy_status(db_path: str = DB_PATH) -> dict:
    """
    {legacy, current} result-row counts, read-only (never creates, migrates or
    regrades anything); {} when the DB or the table is missing.
    """
    if not db_path or not os.path.exists(db_path):
        return {}
    try:
        from pathlib import Path

        conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(prediction_results)")}
        if not columns:
            return {}
        if "method" not in columns:
            return {"legacy": conn.execute("SELECT COUNT(*) FROM prediction_results").fetchone()[0], "current": 0}
        legacy = conn.execute(
            "SELECT COUNT(*) FROM prediction_results WHERE method IS NULL OR method != ?", (EVAL_METHOD,)
        ).fetchone()[0]
        current = conn.execute("SELECT COUNT(*) FROM prediction_results WHERE method = ?", (EVAL_METHOD,)).fetchone()[0]
        return {"legacy": legacy, "current": current}
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


PREDICTION_RESULT_EXTRA_COLUMNS = (
    ("target_date", "TEXT"),
    ("status", "TEXT"),
    ("raw_hit", "INTEGER"),
    ("base_price", "REAL"),
    ("sessions_elapsed", "INTEGER"),
    ("path_coverage", "REAL"),
    ("dead_band_pct", "REAL"),
    ("entry_basis", "TEXT"),
    ("entry_conditional", "INTEGER"),
    ("method", "TEXT"),
)

# Trigger wording that makes the entry conditional (open above X, pullback
# holds Y ...). Without OHLC bars the ledger cannot check those conditions, so
# such rows are flagged and disclosed instead of silently counted as filled.
CONDITIONAL_TRIGGER_PATTERN = re.compile(
    r"(若|如果|需(?:要)?(?:站|守|回測|高於|低於|量|突破|維持|收在|收盤|帶量)"
    r"|開盤價?[需要]?[高低]|回測|不破|不跌破|確認條件|觀察是否)"
)


def _json_dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _normalize_ticker(value):
    return bare_ticker(value)


def _parse_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _to_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
        return float(match.group(0)) if match else None


def _payload_bar_date(data):
    """Bar date declared by a market payload (YYYY-MM-DD) or None."""
    if not isinstance(data, dict):
        return None
    for key in BAR_DATE_KEYS:
        value = data.get(key)
        if not value:
            continue
        text = str(value).strip()
        if len(text) == 8 and text.isdigit():
            text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
        try:
            return _parse_date(text).isoformat()
        except ValueError:
            continue
    return None


def _raw_json_bar_date(raw):
    if not raw:
        return None
    try:
        return _payload_bar_date(json.loads(raw))
    except (TypeError, ValueError):
        return None


def anchor_session(day):
    """The session a prediction is measured from (prediction day, or the session before it)."""
    day = _parse_date(day)
    if twse_calendar.is_twse_session(day):
        return day
    return twse_calendar.prev_session(day)


def nth_session_after(day, n):
    cursor = _parse_date(day)
    for _ in range(int(n)):
        cursor = twse_calendar.next_session(cursor)
    return cursor


def nth_session_before(day, n):
    cursor = _parse_date(day)
    for _ in range(int(n)):
        cursor = twse_calendar.prev_session(cursor)
    return cursor


def sessions_between(start_exclusive, end_inclusive):
    """TWSE sessions in (start_exclusive, end_inclusive]."""
    start = _parse_date(start_exclusive)
    end = _parse_date(end_inclusive)
    out = []
    cursor = start
    for _ in range(400):
        cursor = twse_calendar.next_session(cursor)
        if cursor > end:
            break
        out.append(cursor)
    return out


def _stop_breached(direction, price, stop):
    if price is None or stop is None:
        return False
    if direction_sign(direction) < 0:
        return price >= stop
    return price <= stop


def _valid_stop(direction, stop, base):
    """Ignore stops on the wrong side of the entry (would 'stop out' instantly)."""
    stop = _to_float(stop)
    if stop is None or stop <= 0 or base is None:
        return None
    if direction_sign(direction) < 0:
        return stop if stop > base else None
    return stop if stop < base else None


def _pct(current, base):
    if current is None or base in (None, 0):
        return None
    return (float(current) - float(base)) / float(base) * 100


def _cluster_key(ticker, day_text, horizon):
    """
    Dependence cluster for overlapping windows: same ticker within one block of
    roughly `horizon` sessions (min one week). Used for the effective n.
    """
    block_days = max(7, int(round(int(horizon or 1) * 7 / 5)))
    try:
        ordinal = _parse_date(day_text).toordinal()
    except ValueError:
        return (ticker, str(day_text))
    return (ticker, ordinal // block_days)


def is_conditional_trigger(trigger):
    return bool(CONDITIONAL_TRIGGER_PATTERN.search(str(trigger or "")))


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


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
                UNIQUE(prediction_date, ticker)
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
        self._ensure_columns(cur, "prediction_results", PREDICTION_RESULT_EXTRA_COLUMNS)
        self._ensure_prediction_key_index(cur)

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

        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_closes (
                close_date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                name TEXT,
                close_price REAL,
                volume INTEGER,
                source TEXT,
                PRIMARY KEY (close_date, ticker)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_editorial_frames (
                run_date TEXT PRIMARY KEY,
                frame TEXT NOT NULL,
                label TEXT,
                confidence INTEGER,
                reasons TEXT,
                allowed_tones TEXT,
                prohibited_tones TEXT,
                raw_json TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS youtube_asset_plans (
                run_date TEXT PRIMARY KEY,
                youtube_package_path TEXT,
                video_plan_path TEXT,
                visual_theme TEXT,
                main_topic TEXT,
                raw_json TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS youtube_title_variants (
                run_date TEXT NOT NULL,
                variant TEXT NOT NULL,
                title_type TEXT,
                title TEXT NOT NULL,
                selected INTEGER DEFAULT 0,
                PRIMARY KEY (run_date, variant),
                FOREIGN KEY (run_date) REFERENCES youtube_asset_plans(run_date)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS youtube_thumbnail_variants (
                run_date TEXT NOT NULL,
                variant TEXT NOT NULL,
                cover_text TEXT,
                badges TEXT,
                prompt TEXT,
                selected INTEGER DEFAULT 0,
                PRIMARY KEY (run_date, variant),
                FOREIGN KEY (run_date) REFERENCES youtube_asset_plans(run_date)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS youtube_video_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT NOT NULL,
                measured_at TEXT NOT NULL,
                video_id TEXT,
                hours_since_publish REAL,
                views INTEGER,
                likes INTEGER,
                comments INTEGER,
                impressions INTEGER,
                ctr REAL,
                avg_view_duration_seconds REAL,
                avg_view_percentage REAL,
                title_variant TEXT,
                thumbnail_variant TEXT,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._migrate_youtube_metrics(cur)

        conn.commit()
        conn.close()

    @staticmethod
    def _ensure_columns(cur, table, columns):
        existing = {row[1] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, kind in columns:
            if name not in existing:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")

    @staticmethod
    def _ensure_prediction_key_index(cur):
        """
        Enforce one prediction per (prediction_date, ticker) on DBs created with
        the legacy UNIQUE(date, ticker, direction, trigger). If old duplicates
        are still present the index is skipped (ledger reads dedupe them; run
        scripts/maintenance/recompute_prediction_results.py --apply to clean).
        """
        try:
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_predictions_date_ticker "
                "ON predictions(prediction_date, ticker)"
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def predictions_unique_key_is_legacy(self, conn=None):
        """True when the predictions table still has UNIQUE(date, ticker, direction, trigger)."""
        own_conn = conn is None
        if own_conn:
            conn = self._connect()
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'predictions'"
        ).fetchone()
        if own_conn:
            conn.close()
        sql = re.sub(r"\s+", "", (row[0] if row else "") or "").lower()
        return "unique(prediction_date,ticker,direction,trigger)" in sql
    def _migrate_youtube_metrics(self, cur):
        """Add time-series columns to youtube_video_metrics (idempotent)."""
        cols = {row[1] for row in cur.execute("PRAGMA table_info(youtube_video_metrics)").fetchall()}
        for name in ("published_at", "title", "source"):
            if name not in cols:
                cur.execute(f"ALTER TABLE youtube_video_metrics ADD COLUMN {name} TEXT")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_youtube_metrics_video_time "
            "ON youtube_video_metrics(video_id, measured_at)"
        )

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

        self._sync_predictions(cur, run_date, structured_data)

        self._record_market_snapshots(cur, run_date, market_data or {})
        self._record_institutional_flows(cur, run_date, institutional_data or {})
        self._record_volume_rankings(cur, run_date, volume_data or [])
        self._record_editorial_frame(cur, run_date, structured_data.get("editorial_frame") or {})
        self._evaluate_due_predictions(cur, run_date)
        legacy = self.legacy_result_count(conn)
        if legacy:
            current = conn.execute(
                "SELECT COUNT(*) FROM prediction_results WHERE method = ?", (EVAL_METHOD,)
            ).fetchone()[0]
            print(f"  [Analytics] {legacy_ledger_warning(legacy, current)}")

        conn.commit()
        summary = self.get_summary(conn)
        conn.close()
        return summary

    def record_youtube_asset_plan(
        self,
        run_date,
        asset_record,
        youtube_package_path=None,
        video_plan_path=None,
        visual_theme="",
    ):
        run_date = str(run_date)
        asset_record = asset_record or {}

        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO youtube_asset_plans (
                run_date, youtube_package_path, video_plan_path,
                visual_theme, main_topic, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_date) DO UPDATE SET
                youtube_package_path = excluded.youtube_package_path,
                video_plan_path = excluded.video_plan_path,
                visual_theme = excluded.visual_theme,
                main_topic = excluded.main_topic,
                raw_json = excluded.raw_json
            """,
            (
                run_date,
                youtube_package_path,
                video_plan_path,
                visual_theme,
                asset_record.get("main_topic", ""),
                _json_dumps(asset_record),
            ),
        )

        cur.execute("DELETE FROM youtube_title_variants WHERE run_date = ?", (run_date,))
        for title in asset_record.get("title_variants", []) or []:
            cur.execute(
                """
                INSERT INTO youtube_title_variants (
                    run_date, variant, title_type, title, selected
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_date,
                    title.get("variant", ""),
                    title.get("title_type", ""),
                    title.get("title", ""),
                    1 if title.get("selected") else 0,
                ),
            )

        if "thumbnail_variants" in asset_record:
            # Planner-stage records may omit thumbnails; the finalized covers are written
            # later by record_youtube_thumbnail_variants and must not be wiped here.
            self._replace_thumbnail_variants(cur, run_date, asset_record.get("thumbnail_variants") or [])

        conn.commit()
        summary = self.get_summary(conn)
        conn.close()
        return summary

    def _replace_thumbnail_variants(self, cur, run_date, variants):
        cur.execute("DELETE FROM youtube_thumbnail_variants WHERE run_date = ?", (run_date,))
        for thumb in variants or []:
            cur.execute(
                """
                INSERT INTO youtube_thumbnail_variants (
                    run_date, variant, cover_text, badges, prompt, selected
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_date,
                    thumb.get("variant", ""),
                    "\n".join(thumb.get("cover_text", []) or []),
                    "\n".join(thumb.get("badges", []) or []),
                    thumb.get("prompt", ""),
                    1 if thumb.get("selected") else 0,
                ),
            )

    def record_youtube_thumbnail_variants(self, run_date, variants):
        """Store the finalized (actually generated) thumbnail covers for ``run_date``."""
        conn = self._connect()
        cur = conn.cursor()
        self._replace_thumbnail_variants(cur, str(run_date), variants)
        conn.commit()
        conn.close()
        return len(variants or [])

    def get_youtube_title_variants(self, run_date):
        conn = self._connect()
        rows = conn.execute(
            "SELECT variant, title, selected FROM youtube_title_variants WHERE run_date = ? ORDER BY variant",
            (str(run_date),),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def set_selected_title_variant(self, run_date, variant):
        """Mark the variant that is actually live on YouTube (None clears all → manual title)."""
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("UPDATE youtube_title_variants SET selected = 0 WHERE run_date = ?", (str(run_date),))
        if variant:
            cur.execute(
                "UPDATE youtube_title_variants SET selected = 1 WHERE run_date = ? AND lower(variant) = lower(?)",
                (str(run_date), str(variant)),
            )
        conn.commit()
        conn.close()

    def record_youtube_metric_snapshots(self, rows, measured_at=None, source="data_api", min_gap_minutes=30):
        """
        Batch-insert view snapshots (one per video) with publish time and video age, so
        learning can compare videos at the same age. A video already snapshotted within
        ``min_gap_minutes`` is skipped (main.py refreshes twice in a row).
        """
        measured_dt = measured_at or datetime.datetime.now().replace(microsecond=0)
        if isinstance(measured_dt, str):
            measured_dt = datetime.datetime.fromisoformat(measured_dt)
        measured_text = measured_dt.isoformat(sep=" ")
        cutoff = (measured_dt - datetime.timedelta(minutes=min_gap_minutes)).isoformat(sep=" ")
        conn = self._connect()
        cur = conn.cursor()
        inserted = 0
        for row in rows or []:
            video_id = str(row.get("video_id") or "").strip()
            if not video_id:
                continue
            recent = cur.execute(
                "SELECT 1 FROM youtube_video_metrics WHERE video_id = ? AND measured_at >= ? LIMIT 1",
                (video_id, cutoff),
            ).fetchone()
            if recent:
                continue
            hours = None
            published_at = row.get("published_at") or None
            if published_at:
                try:
                    pub = datetime.datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
                    if pub.tzinfo is not None:
                        pub = pub.astimezone().replace(tzinfo=None)
                    hours = round((measured_dt - pub).total_seconds() / 3600.0, 2)
                except ValueError:
                    hours = None
            cur.execute(
                """
                INSERT INTO youtube_video_metrics (
                    run_date, measured_at, video_id, hours_since_publish, views, likes, comments,
                    title_variant, notes, published_at, title, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(row.get("run_date") or ""),
                    measured_text,
                    video_id,
                    hours,
                    row.get("views"),
                    row.get("likes"),
                    row.get("comments"),
                    row.get("title_variant") or "",
                    row.get("notes") or "",
                    published_at,
                    row.get("title") or "",
                    source,
                ),
            )
            inserted += 1
        conn.commit()
        conn.close()
        return inserted

    def get_youtube_metric_series(self, video_ids):
        """
        {video_id: {"published_at": str|None, "last_measured_at": str|None,
                    "series": [(hours_since_publish, views), ...]}} (series oldest → newest).
        """
        ids = [str(v) for v in (video_ids or []) if v]
        if not ids:
            return {}
        conn = self._connect()
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"""
            SELECT video_id, published_at, measured_at, hours_since_publish, views
            FROM youtube_video_metrics
            WHERE video_id IN ({placeholders}) AND views IS NOT NULL
            ORDER BY measured_at
            """,
            ids,
        ).fetchall()
        conn.close()
        out = {}
        for row in rows:
            item = out.setdefault(row["video_id"], {"published_at": None, "last_measured_at": None, "series": []})
            if row["published_at"]:
                item["published_at"] = row["published_at"]
            item["last_measured_at"] = row["measured_at"]
            if row["hours_since_publish"] is not None:
                item["series"].append((float(row["hours_since_publish"]), int(row["views"])))
        return out

    def record_youtube_metric(
        self,
        run_date,
        measured_at=None,
        video_id="",
        hours_since_publish=None,
        views=None,
        likes=None,
        comments=None,
        impressions=None,
        ctr=None,
        avg_view_duration_seconds=None,
        avg_view_percentage=None,
        title_variant="",
        thumbnail_variant="",
        notes="",
    ):
        run_date = str(run_date)
        measured_at = measured_at or datetime.datetime.now().replace(microsecond=0).isoformat(sep=" ")

        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO youtube_video_metrics (
                run_date, measured_at, video_id, hours_since_publish,
                views, likes, comments, impressions, ctr,
                avg_view_duration_seconds, avg_view_percentage,
                title_variant, thumbnail_variant, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_date,
                measured_at,
                video_id,
                hours_since_publish,
                views,
                likes,
                comments,
                impressions,
                ctr,
                avg_view_duration_seconds,
                avg_view_percentage,
                title_variant,
                thumbnail_variant,
                notes,
            ),
        )
        conn.commit()
        summary = self.get_summary(conn)
        conn.close()
        return summary

    def _sync_predictions(self, cur, run_date, structured_data):
        """
        Mirror the final published prediction_targets of run_date into the ledger.

        Key is (prediction_date, ticker): a same-day rerun that rewords the
        Gemini trigger updates the row instead of adding one, and targets that
        were dropped from the final JSON are removed together with their results.
        Skipped when structured_data has no prediction_targets key (old archives).
        """
        if "prediction_targets" not in structured_data:
            return
        if not structured_data.get("prediction_targets"):
            # An empty list on an existing date is far more often a failed
            # extraction than a deliberate retraction; keep the ledger rows.
            existing = cur.execute(
                "SELECT COUNT(*) FROM predictions WHERE prediction_date = ?", (run_date,)
            ).fetchone()[0]
            if existing:
                print(
                    f"  ⚠️  [Analytics] {run_date} prediction_targets 為空，保留帳本既有 {existing} 筆預測（未刪除）"
                )
            return
        final = []
        seen = set()
        for pred in structured_data.get("prediction_targets") or []:
            if not isinstance(pred, dict):
                continue
            ticker = _normalize_ticker(pred.get("id"))
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            final.append((ticker, pred))

        keep = {}
        drop = []
        for row in cur.execute(
            "SELECT id, ticker FROM predictions WHERE prediction_date = ? ORDER BY id DESC",
            (run_date,),
        ).fetchall():
            if row["ticker"] in seen and row["ticker"] not in keep:
                keep[row["ticker"]] = row["id"]
            else:
                drop.append(row["id"])
        for pred_id in drop:
            cur.execute("DELETE FROM prediction_results WHERE prediction_id = ?", (pred_id,))
            cur.execute("DELETE FROM predictions WHERE id = ?", (pred_id,))

        for ticker, pred in final:
            values = (
                pred.get("name", ""),
                pred.get("direction") or "多",
                pred.get("trigger", ""),
                _to_float(pred.get("stop_loss_price")),
                pred.get("stop_loss_desc", ""),
                _json_dumps(pred),
            )
            if ticker in keep:
                cur.execute(
                    """
                    UPDATE predictions
                    SET name = ?, direction = ?, trigger = ?, stop_loss_price = ?,
                        stop_loss_desc = ?, raw_json = ?
                    WHERE id = ?
                    """,
                    (*values, keep[ticker]),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO predictions (
                        name, direction, trigger, stop_loss_price, stop_loss_desc,
                        raw_json, prediction_date, ticker
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*values, run_date, ticker),
                )

    def _record_market_snapshots(self, cur, run_date, market_data):
        for symbol, data in market_data.items():
            if not data:
                continue
            ticker = _normalize_ticker(symbol)
            # Store the bar under its own trading date when the payload declares
            # one, so a stale bar is never filed as run_date's close.
            snapshot_date = run_date
            bar_date = _payload_bar_date(data)
            if bar_date and bar_date <= str(run_date):
                snapshot_date = bar_date
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
                    snapshot_date,
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

    def record_daily_closes(self, close_date, rows, source="twse_day_all"):
        """Persist full-market closes for ranking / later backtests. Not used on-air."""
        close_date = str(close_date or "")[:10]
        if not close_date or not rows:
            return 0
        conn = self._connect()
        cur = conn.cursor()
        written = 0
        for row in rows:
            ticker = _normalize_ticker(row.get("id") or row.get("ticker"))
            price = row.get("close_price") or row.get("close")
            if not ticker or not price:
                continue
            try:
                price = float(price)
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            volume = row.get("volume")
            try:
                volume = int(volume) if volume not in (None, "") else None
            except (TypeError, ValueError):
                volume = None
            cur.execute(
                """
                INSERT INTO daily_closes (
                    close_date, ticker, name, close_price, volume, source
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(close_date, ticker) DO UPDATE SET
                    name = excluded.name,
                    close_price = excluded.close_price,
                    volume = excluded.volume,
                    source = excluded.source
                """,
                (
                    close_date,
                    ticker,
                    row.get("name") or "",
                    price,
                    volume,
                    source,
                ),
            )
            written += 1
        conn.commit()
        conn.close()
        return written

    def load_close_map(self, close_date=None):
        """Exact-date closes if present; otherwise newest close per ticker."""
        conn = self._connect()
        out = {}
        if close_date:
            rows = conn.execute(
                """
                SELECT ticker, close_price FROM daily_closes
                WHERE close_date = ? AND close_price > 0
                """,
                (str(close_date)[:10],),
            )
            for ticker, price in rows:
                out[str(ticker)] = float(price)
        if not out:
            rows = conn.execute(
                """
                SELECT ticker, close_price FROM daily_closes
                WHERE close_price > 0
                ORDER BY close_date DESC
                """
            )
            for ticker, price in rows:
                ticker = str(ticker)
                if ticker not in out:
                    out[ticker] = float(price)
        conn.close()
        return out

    def _record_editorial_frame(self, cur, run_date, frame):
        if not frame or not frame.get("frame"):
            return
        cur.execute(
            """
            INSERT INTO daily_editorial_frames (
                run_date, frame, label, confidence, reasons,
                allowed_tones, prohibited_tones, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_date) DO UPDATE SET
                frame = excluded.frame,
                label = excluded.label,
                confidence = excluded.confidence,
                reasons = excluded.reasons,
                allowed_tones = excluded.allowed_tones,
                prohibited_tones = excluded.prohibited_tones,
                raw_json = excluded.raw_json
            """,
            (
                run_date,
                frame.get("frame", ""),
                frame.get("label", ""),
                frame.get("confidence"),
                "\n".join(frame.get("reasons") or []),
                "\n".join(frame.get("allowed_tones") or []),
                "\n".join(frame.get("prohibited_tones") or []),
                _json_dumps(frame),
            ),
        )

    # ------------------------------------------------------------------
    # Price lookups (market_snapshots first, then full-tape daily_closes)
    # ------------------------------------------------------------------
    def _load_closes(self, cur, ticker, start, end):
        """{session_date: close} for ticker within [start, end], sessions only."""
        start_s, end_s = str(start)[:10], str(end)[:10]
        closes = {}
        for row in cur.execute(
            """
            SELECT snapshot_date, price, raw_json
            FROM market_snapshots
            WHERE ticker = ? AND snapshot_date BETWEEN ? AND ?
            """,
            (ticker, start_s, end_s),
        ).fetchall():
            price = _to_float(row["price"])
            if price is None or price <= 0:
                continue
            declared = _raw_json_bar_date(row["raw_json"])
            if declared and declared != row["snapshot_date"]:
                continue  # stale bar filed under the wrong day
            if not twse_calendar.is_twse_session(row["snapshot_date"]):
                continue
            closes[row["snapshot_date"]] = price
        if ticker not in INDEX_TICKERS:
            for row in cur.execute(
                """
                SELECT close_date, close_price
                FROM daily_closes
                WHERE ticker = ? AND close_date BETWEEN ? AND ?
                """,
                (ticker, start_s, end_s),
            ).fetchall():
                price = _to_float(row["close_price"])
                if row["close_date"] in closes or price is None or price <= 0:
                    continue
                if twse_calendar.is_twse_session(row["close_date"]):
                    closes[row["close_date"]] = price
        return closes

    def _load_index_closes(self, cur, start, end):
        closes = {}
        for ticker in INDEX_TICKERS:
            for day, price in self._load_closes(cur, ticker, start, end).items():
                closes.setdefault(day, price)
        return closes

    # ------------------------------------------------------------------
    # Prediction ledger evaluation
    # ------------------------------------------------------------------
    def _canonical_predictions(self, cur):
        """One row per (prediction_date, ticker): the newest write wins."""
        canonical = {}
        duplicates = []
        for pred in cur.execute("SELECT * FROM predictions ORDER BY id DESC").fetchall():
            key = (pred["prediction_date"], pred["ticker"])
            if key in canonical:
                duplicates.append(pred["id"])
            else:
                canonical[key] = pred
        return list(canonical.values()), duplicates

    def _evaluate_due_predictions(self, cur, run_date, as_of=None, regrade_legacy=False):
        """
        Deterministically (re)compute every due (prediction, horizon) result.

        - horizon = N TWSE sessions after the anchor session (prediction day);
          a result is due once target_date <= as_of (default run_date) and is
          always priced at target_date, never at a later substitute day;
        - base / exit prices come from market_snapshots or daily_closes; a
          missing price is written as status='missing' (no pct_change fallback);
        - a numeric stop_loss_price is checked on every available close on the
          path; the first breach settles the trade at that close (status='stopped');
        - index return is TWII over the same anchor → exit window, and the hit
          rule is prediction_validator.classify_hit (excess with dead band);
        - results are upserted, so reruns and later backfills recompute them.

        regrade_legacy=False is the daily /1 path: it never touches legacy rows
        (method != sessions_v2). Predictions that still carry a legacy row are
        skipped, nothing is deleted, and only missing or sessions_v2 rows are
        written. Converting the legacy ledger (dedupe, drop not-due legacy
        rows, overwrite) happens only with regrade_legacy=True, i.e. through
        scripts/maintenance/recompute_prediction_results.py (dry-run first,
        --apply backs the DB up), so the before/after comparison survives.
        """
        as_of_day = _parse_date(as_of or run_date)
        predictions, duplicates = self._canonical_predictions(cur)
        legacy_ids = set()
        if regrade_legacy:
            for pred_id in duplicates:
                cur.execute("DELETE FROM prediction_results WHERE prediction_id = ?", (pred_id,))
        else:
            legacy_ids = self._legacy_result_prediction_ids(cur)

        plans = []
        earliest = None
        for pred in predictions:
            if pred["id"] in legacy_ids:
                continue
            try:
                anchor = anchor_session(pred["prediction_date"])
            except ValueError:
                continue
            if anchor >= as_of_day:
                continue
            targets = {h: nth_session_after(anchor, h) for h in EVAL_HORIZONS}
            self._warn_calendar_coverage(max(targets.values()))
            not_due = [h for h, t in targets.items() if t > as_of_day]
            if not_due and regrade_legacy:
                placeholders = ",".join("?" for _ in not_due)
                # Legacy (calendar-day) rows that are not actually due yet.
                cur.execute(
                    f"""
                    DELETE FROM prediction_results
                    WHERE prediction_id = ? AND horizon_days IN ({placeholders})
                      AND (method IS NULL OR method != ?)
                    """,
                    (pred["id"], *not_due, EVAL_METHOD),
                )
            due = {h: t for h, t in targets.items() if t <= as_of_day}
            if not due:
                continue
            plans.append((pred, anchor, due))
            earliest = anchor if earliest is None or anchor < earliest else earliest

        if not plans:
            return 0
        index_closes = self._load_index_closes(cur, earliest, as_of_day)
        written = 0
        for pred, anchor, due in plans:
            closes = self._load_closes(cur, pred["ticker"], anchor, max(due.values()))
            for horizon, target in sorted(due.items()):
                row = self._evaluate_one(pred, horizon, anchor, target, closes, index_closes)
                written += self._upsert_result(cur, row, overwrite_legacy=regrade_legacy)
        return written

    @staticmethod
    def _legacy_result_prediction_ids(cur):
        """Prediction ids that still hold a pre-sessions_v2 result row."""
        return {
            row[0]
            for row in cur.execute(
                "SELECT DISTINCT prediction_id FROM prediction_results WHERE method IS NULL OR method != ?",
                (EVAL_METHOD,),
            ).fetchall()
        }

    def legacy_result_count(self, conn=None):
        """Result rows not yet converted to sessions_v2 (see recompute_prediction_results.py)."""
        own_conn = conn is None
        if own_conn:
            conn = self._connect()
        count = conn.execute(
            "SELECT COUNT(*) FROM prediction_results WHERE method IS NULL OR method != ?",
            (EVAL_METHOD,),
        ).fetchone()[0]
        if own_conn:
            conn.close()
        return count

    def _warn_calendar_coverage(self, day):
        """Print once per store when a window runs past data/twse_closed_days.json."""
        if getattr(self, "_calendar_warned", False):
            return
        warning = twse_calendar.coverage_warning(day)
        if warning:
            self._calendar_warned = True
            print(f"  ⚠️  [Analytics] {warning}")

    def _evaluate_one(self, pred, horizon, anchor, target, closes, index_closes):
        direction = pred["direction"] or "多"
        anchor_s = anchor.isoformat()
        base = closes.get(anchor_s)
        stop = _valid_stop(direction, pred["stop_loss_price"], base)
        path = sessions_between(anchor, target)
        exit_day, exit_price, status = target, None, "ok"
        checked = 0
        walked = 0
        if base is not None:
            for day in path:
                walked += 1
                price = closes.get(day.isoformat())
                if price is None:
                    continue
                checked += 1
                if day == target:
                    exit_price = price
                if stop is not None and _stop_breached(direction, price, stop):
                    exit_day, exit_price, status = day, price, "stopped"
                    break
        if base is None:
            status, reason = "missing", "缺基準日收盤"
        elif exit_price is None:
            status, reason = "missing", "缺目標日收盤"
        else:
            reason = ""

        return_pct = _pct(exit_price, base) if status != "missing" else None
        index_return = None
        if status != "missing":
            index_return = _pct(index_closes.get(exit_day.isoformat()), index_closes.get(anchor_s))
        sessions_elapsed = len(sessions_between(anchor, exit_day)) if status != "missing" else None
        # A stop-loss exit is judged on the window it actually lasted, and a
        # stopped loser is a miss (never neutral) — see classify_hit.
        verdict = classify_hit(
            direction,
            return_pct,
            index_return,
            sessions_elapsed if status == "stopped" else horizon,
            stopped_out=status == "stopped",
        )
        path_coverage = round(checked / walked, 3) if walked else None

        if status == "missing":
            desc = f"{horizon}D（目標 {target.isoformat()}）：{reason}，記為 missing（不計入命中率）"
        else:
            idx_text = f"{index_return:+.2f}%" if index_return is not None else "N/A"
            exc = verdict["excess_pct"]
            exc_text = f"{exc:+.2f}%" if exc is not None else "N/A"
            label = {"hit": "命中", "miss": "失準", "neutral": "與大盤持平（不計）"}.get(verdict["outcome"], "未判定")
            desc = (
                f"{horizon}D（{anchor_s}→{exit_day.isoformat()}）：報酬 {return_pct:+.2f}%，"
                f"大盤 {idx_text}，超額 {exc_text} → {label}"
            )
            if status == "stopped":
                desc += f"；{exit_day.isoformat()} 收盤觸及停損 {stop:g} 出場"
        conditional = is_conditional_trigger(pred["trigger"])
        if conditional and status != "missing":
            desc += "；條件式進場（條件是否成立未驗證）"
        return {
            "prediction_id": pred["id"],
            "horizon_days": horizon,
            "evaluated_date": exit_day.isoformat(),
            "return_pct": return_pct,
            "index_return_pct": index_return,
            "excess_return_pct": verdict["excess_pct"],
            "hit": verdict["hit"],
            "close_price": exit_price if status != "missing" else None,
            "result_desc": desc,
            "target_date": target.isoformat(),
            "status": status,
            "raw_hit": verdict["raw_hit"],
            "base_price": base,
            "sessions_elapsed": sessions_elapsed,
            "path_coverage": path_coverage,
            "dead_band_pct": verdict["dead_band_pct"],
            "entry_basis": ENTRY_BASIS,
            "entry_conditional": 1 if conditional else 0,
            "method": EVAL_METHOD,
        }

    @staticmethod
    def _upsert_result(cur, row, overwrite_legacy=True):
        """Insert or update one result; returns 1 when a row was written."""
        columns = list(row.keys())
        updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c not in ("prediction_id", "horizon_days"))
        guard = "" if overwrite_legacy else f" WHERE prediction_results.method = '{EVAL_METHOD}'"
        cur.execute(
            f"""
            INSERT INTO prediction_results ({", ".join(columns)})
            VALUES ({", ".join("?" for _ in columns)})
            ON CONFLICT(prediction_id, horizon_days) DO UPDATE SET {updates}{guard}
            """,
            tuple(row[c] for c in columns),
        )
        return 1 if cur.rowcount and cur.rowcount > 0 else 0

    def recompute_prediction_results(self, as_of=None, regrade_legacy=True):
        """
        Recompute the ledger from stored prices (maintenance / backfills).

        regrade_legacy=True also converts legacy rows (dedupe, overwrite); only
        scripts/maintenance/recompute_prediction_results.py should do that,
        after its dry-run / backup. False refreshes sessions_v2 rows only.
        """
        as_of = str(as_of or datetime.date.today())
        conn = self._connect()
        cur = conn.cursor()
        written = self._evaluate_due_predictions(cur, as_of, as_of=as_of, regrade_legacy=regrade_legacy)
        conn.commit()
        conn.close()
        return written

    def get_inflight_prediction_tickers(
        self,
        as_of=None,
        max_horizon=None,
        limit=INFLIGHT_MAX_SYMBOLS,
        conn=None,
        skip_if_closed_on_tape=True,
    ):
        """
        Tickers whose longest horizon window is still open on as_of.

        These need a close every session (target day + stop path), so /1 adds
        them to the fetch universe even when they are off today's lists.
        Order: tickers with a horizon target *on* as_of first, then newest
        predictions. With skip_if_closed_on_tape, tickers that already have an
        as_of close in daily_closes (TWSE full tape) are skipped: the ledger
        reads that close directly, so no extra fetch is needed.
        """
        as_of_day = anchor_session(as_of or datetime.date.today())
        max_horizon = max_horizon or max(EVAL_HORIZONS)
        own_conn = conn is None
        if own_conn:
            conn = self._connect()
        cutoff = (as_of_day - datetime.timedelta(days=max_horizon * 3 + 10)).isoformat()
        rows = conn.execute(
            """
            SELECT prediction_date, ticker
            FROM predictions
            WHERE prediction_date >= ? AND prediction_date < ?
            ORDER BY prediction_date DESC, id DESC
            """,
            (cutoff, as_of_day.isoformat()),
        ).fetchall()
        on_tape = set()
        if skip_if_closed_on_tape:
            on_tape = {
                r[0]
                for r in conn.execute(
                    "SELECT ticker FROM daily_closes WHERE close_date = ? AND close_price > 0",
                    (as_of_day.isoformat(),),
                ).fetchall()
            }
        if own_conn:
            conn.close()
        status = {}  # ticker -> [due_today, first_seen_order]
        for order, row in enumerate(rows):
            ticker = _normalize_ticker(row["ticker"])
            if not ticker or ticker in INDEX_TICKERS or ticker in on_tape:
                continue
            try:
                anchor = anchor_session(row["prediction_date"])
            except ValueError:
                continue
            if not (anchor < as_of_day <= nth_session_after(anchor, max_horizon)):
                continue
            due = any(nth_session_after(anchor, h) == as_of_day for h in EVAL_HORIZONS if h <= max_horizon)
            if ticker not in status:
                status[ticker] = [due, order]
            elif due:
                status[ticker][0] = True
        out = sorted(status, key=lambda t: (not status[t][0], status[t][1]))
        return out[:limit] if limit else out

    def has_tape_closes(self, as_of=None, conn=None):
        """True when daily_closes holds the TWSE full tape for the as_of session."""
        day = anchor_session(as_of or datetime.date.today()).isoformat()
        own_conn = conn is None
        if own_conn:
            conn = self._connect()
        row = conn.execute("SELECT 1 FROM daily_closes WHERE close_date = ? LIMIT 1", (day,)).fetchone()
        if own_conn:
            conn.close()
        return row is not None

    def get_summary(self, conn=None):
        own_conn = conn is None
        if own_conn:
            conn = self._connect()
        cur = conn.cursor()

        daily_runs = cur.execute("SELECT COUNT(*) AS count FROM daily_runs").fetchone()["count"]
        predictions = cur.execute("SELECT COUNT(*) AS count FROM predictions").fetchone()["count"]
        evaluated = cur.execute(
            "SELECT COUNT(*) AS count FROM prediction_results WHERE method = ? AND return_pct IS NOT NULL",
            (EVAL_METHOD,),
        ).fetchone()["count"]
        youtube_asset_plans = cur.execute("SELECT COUNT(*) AS count FROM youtube_asset_plans").fetchone()["count"]
        youtube_metric_samples = cur.execute("SELECT COUNT(*) AS count FROM youtube_video_metrics").fetchone()["count"]
        hit_row = cur.execute(
            """
            SELECT AVG(hit) AS hit_rate
            FROM prediction_results
            WHERE hit IS NOT NULL AND method = ?
            """,
            (EVAL_METHOD,),
        ).fetchone()
        hit_rate = hit_row["hit_rate"]
        legacy_results = self.legacy_result_count(conn)

        if own_conn:
            conn.close()

        return {
            "db_path": self.db_path,
            "daily_runs": daily_runs,
            "predictions": predictions,
            "evaluated_results": evaluated,
            "youtube_asset_plans": youtube_asset_plans,
            "youtube_metric_samples": youtube_metric_samples,
            "hit_rate": round(hit_rate * 100, 1) if hit_rate is not None else None,
            "legacy_results": legacy_results,
        }

    @staticmethod
    def _summarize_result_rows(rows):
        """
        Stats for one group of ledger rows (see get_horizon_performance_summary).

        Only directional calls are graded; 觀察/中性 rows are counted in
        non_directional and kept out of every rate. Buckets of the rest:
          decisive  hit 0/1 (excess outside the dead band, or a stopped loss)
          neutral   priced, same-window TWII known, excess inside the band
          unjudged  priced but the same-window TWII close is missing
          missing   no base / target close
        evaluated = decisive + neutral (judgeable), coverage = evaluated / due.
        avg_return / avg_excess are direction-adjusted: a short call on a stock
        that falls 3% contributes +3%. conditional_entries counts decisive
        rows whose trigger made the entry conditional (condition unverified).
        """
        directional = [r for r in rows if direction_sign(r["direction"]) != 0]
        priced = [r for r in directional if r["return_pct"] is not None]
        decisive = [r for r in priced if r["hit"] is not None]
        neutral = [r for r in priced if r["hit"] is None and r["excess_return_pct"] is not None]
        unjudged = [r for r in priced if r["hit"] is None and r["excess_return_pct"] is None]
        evaluated = decisive + neutral
        raw = [r for r in priced if r["raw_hit"] is not None]
        with_index = [r for r in priced if r["index_return_pct"] is not None]
        samples = len(decisive)
        hit_rate = _mean([float(r["hit"]) for r in decisive])

        def _clusters(group):
            return len({_cluster_key(r["ticker"], r["prediction_date"], r["horizon_days"]) for r in group})

        def _signed(row, key):
            value = row[key]
            return None if value is None else direction_sign(row["direction"]) * float(value)

        effective = min(samples, _clusters(decisive))
        ci_low, ci_high = wilson_interval(hit_rate, effective)
        raw_rate = _mean([float(r["raw_hit"]) for r in raw])
        raw_low, raw_high = wilson_interval(raw_rate, min(len(raw), _clusters(raw)))
        index_up = _mean(
            [1.0 if direction_sign(r["direction"]) * float(r["index_return_pct"]) > 0 else 0.0 for r in with_index]
        )
        due = len(directional)
        return {
            "samples": samples,
            "hit_rate": hit_rate,
            "baseline": EXCESS_HIT_BASELINE,
            "effective_samples": effective,
            "hit_ci_low": ci_low,
            "hit_ci_high": ci_high,
            "avg_return": _mean([_signed(r, "return_pct") for r in evaluated]),
            "avg_excess": _mean([_signed(r, "excess_return_pct") for r in evaluated]),
            "raw_samples": len(raw),
            "raw_hit_rate": raw_rate,
            "raw_ci_low": raw_low,
            "raw_ci_high": raw_high,
            "index_up_rate": index_up,
            "due": due,
            "evaluated": len(evaluated),
            "missing": due - len(priced),
            "unjudged": len(unjudged),
            "non_directional": len(rows) - due,
            "coverage": (len(evaluated) / due) if due else None,
            "neutral": len(neutral),
            "stopped": sum(1 for r in evaluated if r["status"] == "stopped"),
            "conditional_entries": sum(1 for r in decisive if r["entry_conditional"]),
        }

    def _ledger_rows(self, cur):
        return cur.execute(
            """
            SELECT r.*, p.ticker, p.prediction_date, p.direction, p.raw_json AS prediction_raw_json
            FROM prediction_results r
            JOIN predictions p ON p.id = r.prediction_id
            WHERE r.method = ?
              AND p.id IN (SELECT MAX(id) FROM predictions GROUP BY prediction_date, ticker)
            """,
            (EVAL_METHOD,),
        ).fetchall()

    def get_horizon_performance_summary(self, conn=None, as_of=None):
        """
        Per-horizon ledger stats (sessions_v2 rows only).

        `as_of` keeps only results evaluated strictly before that date (/1
        evaluates today's results after reading this), so a regen / resume for
        a past date does not see later evaluations.

        samples / hit_rate: excess-vs-TWII hits outside the dead band, plus
        stopped-out losses as misses (neutral, unjudged and missing rows are
        not in the denominator; bucket definitions in _summarize_result_rows).
        baseline=0.5 (a random
        pick beats the index about half the time) with a cluster-adjusted 95%
        Wilson interval: effective_samples counts one cluster per ticker per
        block of ~horizon sessions, because overlapping windows of the same
        stock are not independent. raw_hit_rate is the plain direction sign;
        compare it with index_up_rate (share of the same windows where TWII
        itself moved in the predicted direction). coverage = evaluated / due;
        avg_return / avg_excess are direction-adjusted. Prices are close-to-close (prediction-day close is not a tradable fill).
        """
        own_conn = conn is None
        if own_conn:
            conn = self._connect()
        rows = self._ledger_rows(conn.cursor())
        if own_conn:
            conn.close()
        if as_of:
            cutoff = str(as_of)[:10]
            rows = [r for r in rows if str(r["evaluated_date"] or "") < cutoff]
        grouped = {}
        for row in rows:
            grouped.setdefault(int(row["horizon_days"]), []).append(row)
        out = []
        for horizon in sorted(grouped):
            stats = self._summarize_result_rows(grouped[horizon])
            stats.update(
                {
                    "horizon_days": horizon,
                    "horizon_unit": "sessions",
                    "method": EVAL_METHOD,
                    "entry_basis": ENTRY_BASIS,
                }
            )
            out.append(stats)
        return out

    def get_signal_attribution_summary(self, horizons=(5, 20), conn=None):
        """
        Hit rate / excess grouped by signal features observable at prediction time.

        Features are joined from the same DB on (prediction_date, ticker):
        foreign-flow rank (institutional_flows), vol_ratio and close vs MA20
        (market_snapshots). When a prediction target carries signal_type /
        candidate_source (raw_json), those are grouped too; older rows show
        "未記錄".
        """
        own_conn = conn is None
        if own_conn:
            conn = self._connect()
        cur = conn.cursor()
        rows = [r for r in self._ledger_rows(cur) if int(r["horizon_days"]) in set(horizons)]
        flows = {}
        for row in cur.execute("SELECT flow_date, ticker, side, rank FROM institutional_flows").fetchall():
            flows.setdefault((row["flow_date"], row["ticker"]), []).append((row["side"], row["rank"]))
        snaps = {}
        for row in cur.execute("SELECT snapshot_date, ticker, price, vol_ratio, ma20 FROM market_snapshots").fetchall():
            snaps[(row["snapshot_date"], row["ticker"])] = row
        if own_conn:
            conn.close()

        def _flow_bucket(key):
            entries = flows.get(key) or []
            buys = [rank for side, rank in entries if side == "buy"]
            if buys:
                return "外資買超前3" if min(buys) <= 3 else "外資買超4-10"
            if any(side == "sell" for side, _ in entries):
                return "外資賣超榜"
            return "未上外資榜"

        def _vol_bucket(key):
            snap = snaps.get(key)
            ratio = _to_float(snap["vol_ratio"]) if snap else None
            if ratio is None:
                return "量增比N/A"
            return "量增比≥2" if ratio >= 2 else "量增比<2"

        def _ma_bucket(key):
            snap = snaps.get(key)
            price = _to_float(snap["price"]) if snap else None
            ma20 = _to_float(snap["ma20"]) if snap else None
            if price is None or ma20 is None:
                return "MA20 N/A"
            return "收盤在MA20上" if price >= ma20 else "收盤在MA20下"

        def _raw_field(row, *names):
            try:
                raw = json.loads(row["prediction_raw_json"] or "{}")
            except (TypeError, ValueError):
                raw = {}
            for name in names:
                value = str((raw or {}).get(name) or "").strip()
                if value:
                    return value
            return "未記錄"

        grouped = {}
        for row in rows:
            key = (row["prediction_date"], row["ticker"])
            for feature, bucket in (
                ("外資排名", _flow_bucket(key)),
                ("量增比", _vol_bucket(key)),
                ("MA20", _ma_bucket(key)),
                ("訊號類型", _raw_field(row, "signal_type")),
                ("候選來源", _raw_field(row, "candidate_source", "source")),
            ):
                grouped.setdefault((int(row["horizon_days"]), feature, bucket), []).append(row)
        out = []
        for (horizon, feature, bucket), group in sorted(grouped.items()):
            stats = self._summarize_result_rows(group)
            out.append(
                {
                    "horizon_days": horizon,
                    "feature": feature,
                    "bucket": bucket,
                    "samples": stats["samples"],
                    "hit_rate": stats["hit_rate"],
                    "hit_ci_low": stats["hit_ci_low"],
                    "hit_ci_high": stats["hit_ci_high"],
                    "avg_excess": stats["avg_excess"],
                    "evaluated": stats["evaluated"],
                }
            )
        return out

    def get_recent_prediction_results(self, limit=10, conn=None, as_of=None):
        """Newest evaluations; `as_of` keeps only those evaluated strictly before it."""
        own_conn = conn is None
        if own_conn:
            conn = self._connect()
        cur = conn.cursor()
        where = "AND r.evaluated_date < ?" if as_of else ""
        params = (EVAL_METHOD,) + ((str(as_of)[:10],) if as_of else ()) + (limit,)
        rows = cur.execute(
            f"""
            SELECT
                p.prediction_date, p.ticker, p.name, p.direction,
                r.horizon_days, r.evaluated_date, r.target_date, r.status,
                r.return_pct, r.index_return_pct, r.excess_return_pct,
                r.hit, r.raw_hit, r.result_desc
            FROM prediction_results r
            JOIN predictions p ON p.id = r.prediction_id
            WHERE r.method = ? AND r.return_pct IS NOT NULL
            {where}
            ORDER BY r.evaluated_date DESC, p.prediction_date DESC, p.ticker, r.horizon_days
            LIMIT ?
            """,
            params,
        ).fetchall()
        if own_conn:
            conn.close()
        return [dict(row) for row in rows]

    def get_recent_market_trends(
        self,
        tickers,
        windows=(5, 10, 20),
        conn=None,
        as_of=None,
        latest_overrides=None,
        max_stale_sessions=1,
    ):
        """
        Multi-session trends that are honest about their dates.

        - windows are TWSE sessions: base = the close exactly N sessions before
          the latest close; if that close is not stored the window is dropped
          (never back-filled with an older snapshot);
        - only data on or before as_of (default today) is read, so backfills
          have no look-ahead;
        - latest_overrides (today's market_data payloads) supply the as_of close
          before it is written to market_snapshots;
        - tickers whose latest close lags as_of by more than max_stale_sessions,
          or that have no valid window, are omitted.
        """
        tickers = sorted(t for t in {_normalize_ticker(t) for t in (tickers or [])} if t)
        if not tickers:
            return {}
        try:
            as_of_day = _parse_date(as_of or datetime.date.today())
        except ValueError:
            as_of_day = datetime.date.today()
        as_of_anchor = anchor_session(as_of_day)
        windows = [int(w) for w in (windows or []) if int(w) > 0]
        if not windows:
            return {}
        lookback = nth_session_before(as_of_anchor, max(windows) + max_stale_sessions + 1)

        overrides = {}
        for symbol, data in (latest_overrides or {}).items():
            if not isinstance(data, dict):
                continue
            price = _to_float(data.get("price"))
            if price is None or price <= 0:
                continue
            day = _payload_bar_date(data) or as_of_anchor.isoformat()
            if day > as_of_day.isoformat() or not twse_calendar.is_twse_session(day):
                continue
            overrides[_normalize_ticker(symbol)] = (day, price, _to_float(data.get("pct_change")))

        own_conn = conn is None
        if own_conn:
            conn = self._connect()
        cur = conn.cursor()
        result = {}

        for ticker in tickers:
            closes = self._load_closes(cur, ticker, lookback, as_of_day)
            latest_pct = None
            override = overrides.get(ticker)
            if override:
                closes[override[0]] = override[1]
            if not closes:
                continue
            latest_date = max(closes)
            if override and override[0] == latest_date:
                latest_pct = override[2]
            else:
                snap = cur.execute(
                    "SELECT pct_change FROM market_snapshots WHERE ticker = ? AND snapshot_date = ?",
                    (ticker, latest_date),
                ).fetchone()
                latest_pct = snap["pct_change"] if snap else None
            lag = len(sessions_between(latest_date, as_of_anchor))
            if lag > max_stale_sessions:
                continue
            latest_price = closes[latest_date]
            trends = {}
            for window in windows:
                base_day = nth_session_before(latest_date, window).isoformat()
                base_price = closes.get(base_day)
                if base_price in (None, 0):
                    continue
                trends[f"{window}D"] = {
                    "from_date": base_day,
                    "to_date": latest_date,
                    "base_price": base_price,
                    "latest_price": latest_price,
                    "pct_change": _pct(latest_price, base_price),
                    "sessions": window,
                }
            if not trends:
                continue
            result[ticker] = {
                "latest_date": latest_date,
                "latest_price": latest_price,
                "latest_pct_change": latest_pct,
                "as_of": as_of_day.isoformat(),
                "trends": trends,
            }

        if own_conn:
            conn.close()
        return result
