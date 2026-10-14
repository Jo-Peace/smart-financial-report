"""Shared HTTP GET for TWSE / TPEx / TAIFEX open data.

- Global concurrency cap (BoundedSemaphore) so the parallel market fetch in
  /1 cannot burst dozens of STOCK_DAY requests at once. No fixed pre-call
  sleeps (Standing Decision); only retry backoff after a failure.
- TLS: verify with the default CA bundle first. Python 3.13+ enables
  VERIFY_X509_STRICT, which some exchange certificates fail; retry once with
  a still-verifying context that only drops that strict flag. Only then fall
  back to ``verify=False`` (opt-out with TWSE_ALLOW_INSECURE_SSL=0), and record
  the event so data_quality_gate can warn and fetch meta can carry
  ``tls_unverified``.
- Network down ≠ data not ready. A connect-phase failure (DNS, connection
  refused, connect timeout — the request never reached the server) gets a
  small budget (CONNECT_ATTEMPTS tries with CONNECT_BACKOFF_SEC × n backoff,
  connect timeout capped at CONNECT_TIMEOUT_SEC), so a Wi-Fi / DNS blip of a
  few seconds is absorbed inside one call.
- Per-host circuit: it opens only when connect failures *persist* — at least
  CIRCUIT_AFTER_CALLS failed calls and CIRCUIT_MIN_OUTAGE_SEC between the
  first failure and the latest. While open, calls fail at once for
  CIRCUIT_COOLDOWN_SEC; then one half-open probe (a single attempt) decides:
  success closes it, failure re-opens it. A dead network therefore costs
  seconds, not the minutes of "data not yet published" readiness waits (those
  stay in the callers, e.g. data_fetcher T86, which also gets a couple of short
  connect-recovery waits, see ``connect_recovery_wait``).
"""

from __future__ import annotations

import logging
import os
import ssl
import threading
import time
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0"}

_MAX_CONCURRENCY = max(1, int(os.getenv("TWSE_MAX_CONCURRENCY", "3") or 3))
_SEMAPHORE = threading.BoundedSemaphore(_MAX_CONCURRENCY)

_INSECURE_LOCK = threading.Lock()
_INSECURE_EVENTS: list[dict] = []

# Connect-phase failures (network down): bounded, fail-fast budget.
CONNECT_ATTEMPTS = 3
CONNECT_BACKOFF_SEC = 1.0  # waits 1 s, 2 s: a ~3 s blip never fails the call
CONNECT_TIMEOUT_SEC = 6.0
CIRCUIT_AFTER_CALLS = 2
CIRCUIT_MIN_OUTAGE_SEC = 5.0
CIRCUIT_COOLDOWN_SEC = 10.0
# A half-open probe that never reports back (its thread died) frees the slot after this.
_PROBE_WINDOW_SEC = CONNECT_TIMEOUT_SEC + 2.0
# Failures this far apart (no call in between) are separate blips, not one outage.
_STALE_FAILURE_SEC = 60.0

_CONNECT_LOCK = threading.Lock()
_CONNECT_FAILURES: dict[str, dict] = {}


class TwseHostUnreachable(requests.exceptions.ConnectionError):
    """Raised without a request while a host's connect circuit is open (network down)."""


def is_connect_failure(exc: BaseException) -> bool:
    """
    True for a failure before the server answered: DNS resolution, connection
    refused / unreachable, connect timeout. A TLS failure or a connection the
    server dropped mid-reply (urllib3 ProtocolError, "Connection aborted") is
    not: those keep the normal retries.
    """
    if isinstance(exc, TwseHostUnreachable) or isinstance(exc, requests.exceptions.ConnectTimeout):
        return True
    if isinstance(exc, requests.exceptions.SSLError) or not isinstance(exc, requests.exceptions.ConnectionError):
        return False
    try:
        from urllib3.exceptions import ProtocolError
    except Exception:  # pragma: no cover - urllib3 ships with requests
        ProtocolError = ()
    reason = exc.args[0] if exc.args else None
    if ProtocolError and isinstance(reason, ProtocolError):
        return False
    text = str(exc)
    return not any(marker in text for marker in ("Connection aborted", "RemoteDisconnected"))


def _host(url) -> str:
    return urlparse(str(url or "")).netloc or str(url or "")


def _clock() -> float:
    return time.monotonic()


def _circuit_is_open(state: dict | None) -> bool:
    return bool(
        state
        and state["calls"] >= CIRCUIT_AFTER_CALLS
        and state["at"] - state["first_at"] >= CIRCUIT_MIN_OUTAGE_SEC
    )


def _circuit_check(host: str) -> tuple[float, bool]:
    """
    (seconds the circuit stays open, half-open probe granted). (0, False) =
    closed; (0, True) = this call is the single half-open probe.
    """
    with _CONNECT_LOCK:
        state = _CONNECT_FAILURES.get(host)
        if not _circuit_is_open(state):
            return 0.0, False
        now = _clock()
        remaining = CIRCUIT_COOLDOWN_SEC - (now - state["at"])
        if remaining > 0:
            return remaining, False
        probe_at = state.get("probe_at")
        if probe_at is not None and now - probe_at < _PROBE_WINDOW_SEC:
            return max(0.1, _PROBE_WINDOW_SEC - (now - probe_at)), False  # another call is probing
        state["probe_at"] = now
        return 0.0, True


def circuit_remaining(url_or_host) -> float:
    """Seconds until the host's connect circuit lets a (probe) call through; 0 when closed."""
    host = _host(url_or_host)
    with _CONNECT_LOCK:
        state = _CONNECT_FAILURES.get(host)
        if not _circuit_is_open(state):
            return 0.0
        return max(0.0, CIRCUIT_COOLDOWN_SEC - (_clock() - state["at"]))


def _note_connect_failure(host: str, since: float | None = None) -> None:
    now = _clock()
    with _CONNECT_LOCK:
        state = _CONNECT_FAILURES.get(host)
        if state is None or now - state["at"] > _STALE_FAILURE_SEC:
            state = _CONNECT_FAILURES[host] = {"calls": 0, "at": now, "first_at": now if since is None else since}
        state["calls"] += 1
        state["at"] = now
        if since is not None:
            state["first_at"] = min(state["first_at"], since)
        state["probe_at"] = None


def _note_success(host: str) -> None:
    """The host answered (any reply, even an error): the network is up, close the circuit."""
    with _CONNECT_LOCK:
        _CONNECT_FAILURES.pop(host, None)


def reset_connect_failures() -> None:
    with _CONNECT_LOCK:
        _CONNECT_FAILURES.clear()


def _connect_capped(timeout):
    """(connect, read) timeout: a dead route fails within CONNECT_TIMEOUT_SEC."""
    if isinstance(timeout, (int, float)) and timeout > 0:
        return (min(float(timeout), CONNECT_TIMEOUT_SEC), float(timeout))
    return timeout


def _insecure_allowed() -> bool:
    return (os.getenv("TWSE_ALLOW_INSECURE_SSL", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}


class _RelaxedStrictAdapter(HTTPAdapter):
    """Still verifies chain + hostname; only drops Python 3.13's VERIFY_X509_STRICT."""

    def init_poolmanager(self, *args, **kwargs):
        try:
            import certifi

            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ctx = ssl.create_default_context()
        strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
        if strict:
            ctx.verify_flags &= ~strict
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def _relaxed_get(url, *, headers, timeout, params=None):
    with requests.Session() as session:
        session.mount("https://", _RelaxedStrictAdapter())
        return session.get(url, headers=headers, timeout=timeout, params=params)


def record_insecure_event(url: str, reason: str = "") -> None:
    host = urlparse(str(url or "")).netloc or str(url or "")
    with _INSECURE_LOCK:
        _INSECURE_EVENTS.append({"host": host, "url": str(url or "")[:200], "reason": reason[:160]})


def insecure_events() -> list[dict]:
    with _INSECURE_LOCK:
        return list(_INSECURE_EVENTS)


def reset_insecure_events() -> None:
    with _INSECURE_LOCK:
        _INSECURE_EVENTS.clear()


def _get_once(url, *, headers, timeout, params=None):
    """Single GET with the TLS ladder. Returns (response, tls_unverified)."""
    try:
        return requests.get(url, headers=headers, timeout=timeout, params=params), False
    except requests.exceptions.SSLError as first:
        try:
            return _relaxed_get(url, headers=headers, timeout=timeout, params=params), False
        except requests.exceptions.SSLError as second:
            if not _insecure_allowed():
                raise
            logger.warning("TLS verification failed for %s (%s); retrying unverified", url, second or first)
            print(f"  [Warning] TLS 憑證驗證失敗，改用未驗證連線（已標記 tls_unverified）：{urlparse(url).netloc}")
            record_insecure_event(url, str(second or first))
            return requests.get(url, headers=headers, timeout=timeout, params=params, verify=False), True


def twse_get(
    url, *, timeout=30, retries=3, backoff=1.2, headers=None, params=None, retry_on=None, connect_attempts=None
):
    """
    Throttled GET. Returns a requests.Response (attribute ``tls_unverified`` set).

    Retries network errors with linear backoff (``backoff * attempt``); raises
    the last error when every attempt fails. Connect-phase failures
    (is_connect_failure) only get ``connect_attempts`` tries (default
    min(retries, CONNECT_ATTEMPTS)), CONNECT_BACKOFF_SEC × n apart;
    a call that spends them counts once toward the per-host circuit. While the
    circuit is open the call raises TwseHostUnreachable at once; after the
    cooldown one call is let through as a single-attempt probe.
    """
    headers = headers or DEFAULT_HEADERS
    retry_on = retry_on or (requests.exceptions.RequestException,)
    host = _host(url)
    remaining, probe = _circuit_check(host)
    if remaining > 0:
        raise TwseHostUnreachable(f"{host} 連線失敗（網路中斷？），{remaining:.0f} 秒內不重試: {url}")
    attempts = max(1, int(retries))
    if probe:
        connect_budget = 1
    elif connect_attempts:
        connect_budget = max(1, int(connect_attempts))
    else:
        connect_budget = min(attempts, CONNECT_ATTEMPTS)
    connect_failures = 0
    first_failure_at = None
    attempt = 0
    while True:
        attempt += 1
        try:
            with _SEMAPHORE:
                resp, insecure = _get_once(url, headers=headers, timeout=_connect_capped(timeout), params=params)
            try:
                resp.tls_unverified = insecure
            except Exception:
                pass
            _note_success(host)
            return resp
        except retry_on as exc:
            if is_connect_failure(exc):
                connect_failures += 1
                if first_failure_at is None:
                    first_failure_at = _clock()
                if connect_failures >= connect_budget:
                    _note_connect_failure(host, since=first_failure_at)
                    raise
                time.sleep(CONNECT_BACKOFF_SEC * connect_failures)
                continue
            _note_success(host)  # the server was reached: not a network outage
            if attempt >= attempts:
                raise
            time.sleep(backoff * attempt)


def connect_recovery_wait(url, base_wait: float) -> float:
    """
    How long a caller should wait after a connect failure before its next try:
    ``base_wait``, or longer when the host's circuit is still open, so the next
    try is the half-open probe instead of an instant TwseHostUnreachable.
    """
    remaining = circuit_remaining(url)
    return max(float(base_wait), remaining + 0.1 if remaining > 0 else 0.0)


def twse_get_json(url, *, timeout=30, retries=3, backoff=1.2, params=None):
    """
    GET + JSON decode (decode errors are retried too). Returns (payload, tls_unverified).
    A connect-phase failure is not retried here (twse_get already spent its budget).
    """
    last_err = None
    for attempt in range(max(1, int(retries))):
        try:
            resp = twse_get(url, timeout=timeout, retries=1, params=params, connect_attempts=CONNECT_ATTEMPTS)
            resp.raise_for_status()
            return resp.json(), bool(getattr(resp, "tls_unverified", False))
        except (requests.exceptions.RequestException, ValueError) as exc:
            if is_connect_failure(exc):
                raise
            last_err = exc
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    if last_err:
        raise last_err
    return None, False
