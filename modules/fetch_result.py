"""Helpers for standardized fetch results.

The project still consumes many legacy raw payloads, so we keep the return
shape backward-compatible by wrapping the actual payload in a fixed envelope:

    {
        "ok": bool,
        "data": any,
        "error": str | None,
        "source": str,
        "meta": dict,
    }

Callers can keep working during the migration by using `unwrap_fetch_result()`.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

try:
    import requests
except Exception:  # pragma: no cover - requests is available in normal runtime
    requests = None


def make_fetch_result(
    *,
    ok: bool,
    data: Any,
    error: str | None = None,
    code: str | None = None,
    source: str = "",
    meta: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "ok": ok,
        "data": data,
        "error": error,
        "code": code,
        "source": source,
        "meta": meta or {},
    }


def fetch_ok(data: Any, *, source: str = "", meta: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return make_fetch_result(ok=True, data=data, error=None, code=None, source=source, meta=meta)


def fetch_error(
    error: str,
    *,
    data: Any = None,
    code: str | None = None,
    source: str = "",
    meta: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return make_fetch_result(ok=False, data=data, error=error, code=code, source=source, meta=meta)


def classify_exception(exc: Exception | str, *, source: str = "") -> str:
    text = str(exc).lower()
    if requests is not None:
        if isinstance(exc, requests.exceptions.SSLError):
            return "NETWORK_SSL"
        if isinstance(exc, (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout, TimeoutError)):
            return "NETWORK_TIMEOUT"
        if isinstance(exc, requests.exceptions.ConnectionError):
            if any(
                token in text
                for token in (
                    "name or service not known",
                    "temporary failure",
                    "name resolution",
                    "nodename nor servname",
                    "dns",
                    "failed to resolve",
                )
            ):
                return "NETWORK_DNS"
            return "NETWORK_CONNECTION"

    if isinstance(exc, (ValueError, IndexError, KeyError, TypeError, json.JSONDecodeError)):
        return "DATA_FORMAT"

    if any(token in text for token in ("429", "rate limit", "too many requests")):
        return "RATE_LIMIT"
    if any(token in text for token in ("401", "403", "unauthorized", "forbidden", "auth", "api key")):
        return "AUTH_ERROR"
    if any(token in text for token in ("empty", "no data", "無法取得", "not found")):
        return "EMPTY_DATA"
    return "UNKNOWN_ERROR"


def fetch_error_from_exception(
    exc: Exception,
    *,
    source: str = "",
    data: Any = None,
    meta: Dict[str, Any] | None = None,
    default_message: str | None = None,
) -> Dict[str, Any]:
    code = classify_exception(exc, source=source)
    message = default_message or str(exc) or code
    logging.getLogger(source or __name__).error(
        "fetch failure [%s] %s",
        code,
        message,
        exc_info=exc,
    )
    return fetch_error(message, data=data, code=code, source=source, meta=meta)


def is_fetch_result(value: Any) -> bool:
    return isinstance(value, dict) and {"ok", "data", "error"}.issubset(value.keys())


def unwrap_fetch_result(value: Any, default: Any = None) -> Any:
    if is_fetch_result(value):
        return value.get("data", default)
    return default if value is None else value
