"""
SQLite database for report caching and IP quota management.
"""
import sqlite3
import datetime
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_research.db")
DAILY_FREE_QUOTA = 5
DAILY_GLOBAL_LIMIT = 20  # 全站每日新報告暫時設定為 20 份 (保護免費 API，等綁定信用卡後可改回 200)


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

    conn.commit()
    conn.close()


def get_today_str():
    """Get today's date string in YYYY-MM-DD format (UTC+8)."""
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")


# === Report Cache ===

def get_cached_report(ticker: str) -> dict | None:
    """
    Get cached report for a ticker from the last 3 days.
    Returns {"content": str, "cached": True, "date": str} or None.
    """
    conn = get_db()
    cursor = conn.cursor()
    
    base_time = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
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
    
    base_time = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    dates = [(base_time - datetime.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
    
    placeholders = ",".join("?" for _ in dates)
    query = f"SELECT ticker, MAX(date) as recent_date FROM report_cache WHERE date IN ({placeholders}) GROUP BY ticker ORDER BY recent_date DESC LIMIT ?"
    
    params = dates + [limit]
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    
    return [{"ticker": row["ticker"], "date": row["recent_date"]} for row in rows]


def get_top_hot_stocks(limit: int = 5) -> list:
    """Get top viewed stocks in the last 7 days + defaults if not enough."""
    conn = get_db()
    cursor = conn.cursor()
    
    base_time = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    dates = [(base_time - datetime.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    placeholders = ",".join("?" for _ in dates)
    
    # Base real views from recent cache
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
    
    # Calculate a boosted view to make the website feel active (150 baseline) + real views
    # People like big numbers, and an MVP needs to look credible.
    result = []
    for row in rows:
        # Give a small base multiplier or base offset for real views so it looks active
        boosted_views = 1500 + row["total_views"] * 7
        result.append({
            "ticker": row["ticker"], 
            "date": row["recent_date"], 
            "views": boosted_views
        })
    
    # If the system translates to empty / cold start, inject presets!
    # Preset: 2330, 2382, 2317, 2454, 2327
    default_tickers = ["2330", "2382", "2317", "2454", "2327"]
    idx = 0
    # Add defaults until we have at least 3 to 5
    min_count = max(3, min(limit, 5))
    while len(result) < min_count and idx < len(default_tickers):
        t = default_tickers[idx]
        if not any(r["ticker"] == t for r in result):
            # Fake date/views for empty cache cases
            fake_views = 1500 + (len(default_tickers) - idx) * 230
            result.append({"ticker": t, "date": "尚未更新", "views": fake_views})
        idx += 1
        
    return result[:limit]

def increment_views(ticker: str, date: str):
    """Increment view count for a specific cached report."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE report_cache SET views = views + 1 WHERE ticker = ? AND date = ?",
        (ticker.upper(), date)
    )
    conn.commit()
    conn.close()



def save_report(ticker: str, content: str):
    """Save a report to cache."""
    conn = get_db()
    cursor = conn.cursor()
    today = get_today_str()

    cursor.execute(
        # Insert with views=1, or if conflict, update only the content (keeping views and incrementing it)
        "INSERT INTO report_cache (ticker, date, content, views) VALUES (?, ?, ?, 1) "
        "ON CONFLICT(ticker, date) DO UPDATE SET content = excluded.content, views = views + 1",
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
