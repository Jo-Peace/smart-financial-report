"""
SQLite database for report caching and IP quota management.

DB location: ``STOCK_RESEARCH_DB_PATH`` (default ``app/stock_research.db``). On
Render this must point at a persistent disk (see render.yaml); the container
filesystem is wiped on every deploy/restart.
"""
import sqlite3
import datetime
import hashlib
import logging
import os

logger = logging.getLogger(__name__)

from modules import tz as _tz  # the one Taipei clock
from modules.tz import TAIPEI_TZ  # noqa: F401

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_research.db")
DB_PATH = os.getenv("STOCK_RESEARCH_DB_PATH") or DEFAULT_DB_PATH
DAILY_FREE_QUOTA = 5
DAILY_GLOBAL_LIMIT = 20  # 全站每日新報告暫時設定為 20 份 (保護免費 API，等綁定信用卡後可改回 200)
# Generation attempts (success + failure) per day. Failed attempts still spend
# Tavily / TWSE / yfinance calls, so they need their own ceiling.
DAILY_GLOBAL_ATTEMPT_LIMIT = DAILY_GLOBAL_LIMIT * 2
# Failed (refunded) generations one IP may cause per day; caps a single client's
# share of DAILY_GLOBAL_ATTEMPT_LIMIT since failures cost it no personal quota.
DAILY_IP_FAILED_ATTEMPT_LIMIT = 3
# Configured path that _ensure_db_dir had to abandon (None = no fallback taken).
STORAGE_FALLBACK_FROM = None

# Cold-start editor picks: shown without fabricated view counts or dates.
EDITOR_PICK_TICKERS = ["2330", "2382", "2317", "2454", "2327"]
# A viewer counts at most once per ticker per day toward the 熱門 view counts.
VIEWER_RETENTION_DAYS = 8


def get_db():
    """Get a database connection with row factory."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_db_dir() -> None:
    """
    Create the DB directory; fall back to the in-app default when the configured
    location (e.g. a Render disk that is not mounted) is not writable, so the
    service still starts. /healthz reports the storage as non-persistent then.
    """
    global DB_PATH, STORAGE_FALLBACK_FROM
    db_dir = os.path.dirname(DB_PATH)
    try:
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        if db_dir and not os.access(db_dir, os.W_OK):
            raise PermissionError(f"{db_dir} is not writable")
    except OSError as exc:
        if DB_PATH == DEFAULT_DB_PATH:
            raise
        logger.warning("DB path %s unusable (%s); falling back to %s", DB_PATH, exc, DEFAULT_DB_PATH)
        STORAGE_FALLBACK_FROM = DB_PATH
        DB_PATH = DEFAULT_DB_PATH
        return
    if DB_PATH != DEFAULT_DB_PATH:
        STORAGE_FALLBACK_FROM = None


def storage_is_persistent() -> bool:
    """True when the DB lives outside the app directory (i.e. on a configured disk)."""
    return os.path.abspath(DB_PATH) != os.path.abspath(DEFAULT_DB_PATH)


def storage_fallback_from() -> str | None:
    """The configured DB path that was unusable at start-up (None when it is in use)."""
    return STORAGE_FALLBACK_FROM


def init_db():
    """Initialize database tables."""
    _ensure_db_dir()
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        pass

    # Report cache table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS report_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            views INTEGER DEFAULT 1,
            UNIQUE(ticker, date)
        )
    """)

    # Check if views column exists (for backward compatibility migration)
    try:
        cursor.execute("ALTER TABLE report_cache ADD COLUMN views INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # IP quota table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ip_quota (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL,
            date TEXT NOT NULL,
            used_count INTEGER DEFAULT 0,
            UNIQUE(ip_address, date)
        )
    """)
    try:
        cursor.execute("ALTER TABLE ip_quota ADD COLUMN failed_count INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # One row per (ticker, view day, hashed viewer): cache hits cannot inflate views
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS report_viewers (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            viewer TEXT NOT NULL,
            UNIQUE(ticker, date, viewer)
        )
    """)

    # Every generation attempt (including failures) counts toward the global cap
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS generation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def taipei_now() -> datetime.datetime:
    """Current time in Asia/Taipei (cache keys, quotas and prompts share one day boundary); modules.tz."""
    return _tz.now_tpe()


def get_today_str():
    """Get today's date string in YYYY-MM-DD format (Asia/Taipei)."""
    return taipei_now().strftime("%Y-%m-%d")


# === Report Cache ===

def get_cached_report(ticker: str) -> dict | None:
    """
    Get cached report for a ticker from the last 3 days.
    Returns {"content": str, "cached": True, "date": str} or None.
    """
    conn = get_db()
    cursor = conn.cursor()

    base_time = taipei_now()
    dates = [(base_time - datetime.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(3)]

    placeholders = ",".join("?" for _ in dates)
    query = f"SELECT content, date FROM report_cache WHERE ticker = ? AND date IN ({placeholders}) ORDER BY date DESC LIMIT 1"

    params = [ticker.upper()] + dates
    cursor.execute(query, params)
    row = cursor.fetchone()
    conn.close()

    if row:
        return {"content": row["content"], "cached": True, "date": row["date"]}
    return None

def get_recent_reports(days: int = 3, limit: int = 12) -> list:
    """Get a list of recently searched tickers in the last few days."""
    conn = get_db()
    cursor = conn.cursor()

    base_time = taipei_now()
    dates = [(base_time - datetime.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]

    placeholders = ",".join("?" for _ in dates)
    query = f"SELECT ticker, MAX(date) as recent_date FROM report_cache WHERE date IN ({placeholders}) GROUP BY ticker ORDER BY recent_date DESC LIMIT ?"

    params = dates + [limit]
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    return [{"ticker": row["ticker"], "date": row["recent_date"]} for row in rows]


def get_top_hot_stocks(limit: int = 5) -> list:
    """
    Top viewed cached reports in the last 7 days, with their REAL view counts.

    When the cache is cold, editor picks fill the list; they carry
    ``editor_pick=True`` and no views/date so the UI never shows invented numbers.
    """
    conn = get_db()
    cursor = conn.cursor()

    base_time = taipei_now()
    dates = [(base_time - datetime.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    placeholders = ",".join("?" for _ in dates)

    query = f"""
        SELECT ticker, MAX(date) as recent_date, SUM(views) as total_views
        FROM report_cache
        WHERE date IN ({placeholders})
        GROUP BY ticker
        ORDER BY total_views DESC
        LIMIT ?
    """
    params = dates + [limit]
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        result.append({
            "ticker": row["ticker"],
            "date": row["recent_date"],
            "views": int(row["total_views"] or 0),
            "editor_pick": False,
        })

    min_count = max(3, min(limit, 5))
    for ticker in EDITOR_PICK_TICKERS:
        if len(result) >= min_count:
            break
        if not any(r["ticker"] == ticker for r in result):
            result.append({"ticker": ticker, "date": None, "views": None, "editor_pick": True})

    return result[:limit]

def _viewer_key(viewer: str) -> str:
    """Hashed viewer id (the raw IP is not stored for view counting)."""
    return hashlib.sha256(str(viewer).encode("utf-8")).hexdigest()[:24]


def _claim_view(conn, ticker: str, viewer) -> bool:
    """True the first time ``viewer`` sees ``ticker`` today; no viewer = always counts."""
    if not viewer:
        return True
    cursor = conn.execute(
        "INSERT OR IGNORE INTO report_viewers (ticker, date, viewer) VALUES (?, ?, ?)",
        (ticker.upper(), get_today_str(), _viewer_key(viewer)),
    )
    return cursor.rowcount == 1


def increment_views(ticker: str, date: str, viewer: str | None = None) -> bool:
    """
    Count one view of a cached report. With ``viewer`` (client IP) a repeat view
    of the same ticker on the same day is ignored, so looping cache hits cannot
    push a ticker up the 熱門 list. Returns True when the view was counted.
    """
    conn = get_db()
    try:
        if not _claim_view(conn, ticker, viewer):
            conn.commit()
            return False
        conn.execute(
            "UPDATE report_cache SET views = views + 1 WHERE ticker = ? AND date = ?",
            (ticker.upper(), date),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def save_report(ticker: str, content: str, viewer: str | None = None):
    """Save a report to cache (the generating viewer's view is the first one)."""
    conn = get_db()
    try:
        today = get_today_str()
        counted = _claim_view(conn, ticker, viewer)
        conn.execute(
            # Insert with views=1; a same-day regeneration keeps views (+1 for a new viewer)
            "INSERT INTO report_cache (ticker, date, content, views) VALUES (?, ?, ?, 1) "
            "ON CONFLICT(ticker, date) DO UPDATE SET content = excluded.content, views = views + ?",
            (ticker.upper(), today, content, 1 if counted else 0),
        )
        cutoff = (taipei_now() - datetime.timedelta(days=VIEWER_RETENTION_DAYS)).strftime("%Y-%m-%d")
        conn.execute("DELETE FROM report_viewers WHERE date < ?", (cutoff,))
        conn.commit()
    finally:
        conn.close()


# === IP Quota ===

def get_remaining_quota(ip: str) -> int:
    """Get remaining free queries for an IP today."""
    conn = get_db()
    cursor = conn.cursor()
    today = get_today_str()

    cursor.execute(
        "SELECT used_count FROM ip_quota WHERE ip_address = ? AND date = ?",
        (ip, today)
    )
    row = cursor.fetchone()
    conn.close()

    used = row["used_count"] if row else 0
    return max(0, DAILY_FREE_QUOTA - used)


def use_quota(ip: str) -> bool:
    """
    Atomically use one query quota for an IP.
    Returns True if quota was available (and consumed), False if exceeded.
    """
    conn = get_db()
    try:
        today = get_today_str()
        conn.execute(
            "INSERT INTO ip_quota (ip_address, date, used_count) VALUES (?, ?, 0) "
            "ON CONFLICT(ip_address, date) DO NOTHING",
            (ip, today),
        )
        cursor = conn.execute(
            "UPDATE ip_quota SET used_count = used_count + 1 "
            "WHERE ip_address = ? AND date = ? AND used_count < ?",
            (ip, today, DAILY_FREE_QUOTA),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def refund_quota(ip: str) -> None:
    """Give back one quota unit after a failed generation (never below zero)."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE ip_quota SET used_count = used_count - 1 "
            "WHERE ip_address = ? AND date = ? AND used_count > 0",
            (ip, get_today_str()),
        )
        conn.commit()
    finally:
        conn.close()


def record_failed_attempt(ip: str) -> None:
    """Count one failed (refunded) generation for an IP today."""
    conn = get_db()
    try:
        today = get_today_str()
        conn.execute(
            "INSERT INTO ip_quota (ip_address, date, used_count, failed_count) VALUES (?, ?, 0, 0) "
            "ON CONFLICT(ip_address, date) DO NOTHING",
            (ip, today),
        )
        conn.execute(
            "UPDATE ip_quota SET failed_count = COALESCE(failed_count, 0) + 1 WHERE ip_address = ? AND date = ?",
            (ip, today),
        )
        conn.commit()
    finally:
        conn.close()


def get_failed_attempts(ip: str) -> int:
    """Failed (refunded) generations for an IP today."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT failed_count FROM ip_quota WHERE ip_address = ? AND date = ?",
            (ip, get_today_str()),
        ).fetchone()
        return int((row["failed_count"] if row else 0) or 0)
    finally:
        conn.close()


def log_generation(ticker: str, status: str) -> None:
    """Record a generation attempt (status: started / success / failed)."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO generation_log (date, ticker, status) VALUES (?, ?, ?)",
            (get_today_str(), str(ticker).upper(), status),
        )
        conn.commit()
    finally:
        conn.close()


def get_generation_attempts_today() -> int:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM generation_log WHERE date = ? AND status = 'started'",
            (get_today_str(),),
        ).fetchone()
        return int(row["count"] or 0)
    finally:
        conn.close()


def get_global_usage_today() -> int:
    """Get the number of new reports generated today (globally)."""
    conn = get_db()
    cursor = conn.cursor()
    today = get_today_str()
    cursor.execute("SELECT COUNT(*) as count FROM report_cache WHERE date = ?", (today,))
    count = cursor.fetchone()["count"]
    conn.close()
    return count


def get_global_remaining() -> int:
    """New reports still allowed today (successful reports AND attempts are both capped)."""
    by_reports = DAILY_GLOBAL_LIMIT - get_global_usage_today()
    by_attempts = DAILY_GLOBAL_ATTEMPT_LIMIT - get_generation_attempts_today()
    return max(0, min(by_reports, by_attempts))


def check_global_limit() -> bool:
    """Check if global daily limit has been reached. Returns True if OK to proceed."""
    return get_global_remaining() > 0


def get_cache_stats() -> dict:
    """Get cache statistics for today."""
    conn = get_db()
    cursor = conn.cursor()
    today = get_today_str()

    cursor.execute("SELECT COUNT(*) as count FROM report_cache WHERE date = ?", (today,))
    cached = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(DISTINCT ip_address) as count FROM ip_quota WHERE date = ?", (today,))
    users = cursor.fetchone()["count"]

    conn.close()
    return {"cached_reports_today": cached, "unique_users_today": users}


def healthcheck() -> bool:
    """True when the DB is reachable and writable."""
    conn = get_db()
    try:
        conn.execute("SELECT 1").fetchone()
        conn.execute("CREATE TABLE IF NOT EXISTS _healthcheck (id INTEGER PRIMARY KEY)")
        conn.commit()
        return True
    finally:
        conn.close()
