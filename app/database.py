"""
SQLite database for report caching and IP quota management.
"""
import sqlite3
import datetime
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_research.db")
DAILY_FREE_QUOTA = 3
DAILY_GLOBAL_LIMIT = 20  # 全站每日新報告上限（保護 Gemini API 額度）


def get_db():
    """Get a database connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize database tables."""
    conn = get_db()
    cursor = conn.cursor()

    # Report cache table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS report_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ticker, date)
        )
    """)

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

    conn.commit()
    conn.close()


def get_today_str():
    """Get today's date string in YYYY-MM-DD format (UTC+8)."""
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")


# === Report Cache ===

def get_cached_report(ticker: str) -> dict | None:
    """
    Get cached report for a ticker from today.
    Returns {"content": str, "cached": True} or None.
    """
    conn = get_db()
    cursor = conn.cursor()
    today = get_today_str()

    cursor.execute(
        "SELECT content FROM report_cache WHERE ticker = ? AND date = ?",
        (ticker.upper(), today)
    )
    row = cursor.fetchone()
    conn.close()

    if row:
        return {"content": row["content"], "cached": True}
    return None


def save_report(ticker: str, content: str):
    """Save a report to cache."""
    conn = get_db()
    cursor = conn.cursor()
    today = get_today_str()

    cursor.execute(
        "INSERT OR REPLACE INTO report_cache (ticker, date, content) VALUES (?, ?, ?)",
        (ticker.upper(), today, content)
    )
    conn.commit()
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
    Use one query quota for an IP.
    Returns True if quota was available, False if exceeded.
    """
    conn = get_db()
    cursor = conn.cursor()
    today = get_today_str()

    # Check current usage
    cursor.execute(
        "SELECT used_count FROM ip_quota WHERE ip_address = ? AND date = ?",
        (ip, today)
    )
    row = cursor.fetchone()
    used = row["used_count"] if row else 0

    if used >= DAILY_FREE_QUOTA:
        conn.close()
        return False

    # Increment
    cursor.execute(
        "INSERT INTO ip_quota (ip_address, date, used_count) VALUES (?, ?, 1) "
        "ON CONFLICT(ip_address, date) DO UPDATE SET used_count = used_count + 1",
        (ip, today)
    )
    conn.commit()
    conn.close()
    return True


def get_global_usage_today() -> int:
    """Get the number of new reports generated today (globally)."""
    conn = get_db()
    cursor = conn.cursor()
    today = get_today_str()
    cursor.execute("SELECT COUNT(*) as count FROM report_cache WHERE date = ?", (today,))
    count = cursor.fetchone()["count"]
    conn.close()
    return count


def check_global_limit() -> bool:
    """Check if global daily limit has been reached. Returns True if OK to proceed."""
    return get_global_usage_today() < DAILY_GLOBAL_LIMIT


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
