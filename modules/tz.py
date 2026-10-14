"""Asia/Taipei clock helpers.

The daily pipeline is defined by the TWSE session (16:10 cutoff, run_date,
T86 / MI_INDEX date params). Using naive ``datetime.now()`` ties all of that to
the host timezone, which breaks the moment /1 runs on a UTC box.

Callers that compare against naive datetimes (most legacy code) should use
``now_tpe_naive()``; new code can use the aware ``now_tpe()``.

This is the one Taipei clock: ``TAIPEI_TZ`` / ``taipei_now`` /
``taipei_today_str`` (the names special_context and the web app used) are
thin aliases of ``TPE`` / ``now_tpe`` / ``today_tpe_str``.
"""

from __future__ import annotations

import datetime

try:  # Python 3.9+; tzdata is present on macOS / most Linux images.
    from zoneinfo import ZoneInfo

    TPE = ZoneInfo("Asia/Taipei")
except Exception:  # pragma: no cover - fallback when tz database is missing
    # Taiwan has not observed DST since 1979, so a fixed +08:00 is exact.
    TPE = datetime.timezone(datetime.timedelta(hours=8), "Asia/Taipei")


def now_tpe() -> datetime.datetime:
    """Timezone-aware current time in Asia/Taipei."""
    return datetime.datetime.now(TPE)


def now_tpe_naive() -> datetime.datetime:
    """Current Asia/Taipei wall-clock time as a naive datetime (legacy-compatible)."""
    return now_tpe().replace(tzinfo=None)


def today_tpe() -> datetime.date:
    return now_tpe().date()


def today_tpe_str() -> str:
    return today_tpe().strftime("%Y-%m-%d")


# Backward-compatible names (special_context, app/database, app/research_data).
TAIPEI_TZ = TPE


def taipei_now() -> datetime.datetime:
    """Alias of now_tpe()."""
    return now_tpe()


def taipei_today_str() -> str:
    """Alias of today_tpe_str()."""
    return today_tpe_str()
