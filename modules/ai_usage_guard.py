"""
Daily Gemini budget guard + append-only usage log.

Budget rules (all env-tunable):
- Usage is *weighted by status*: success / incomplete = 1 (tokens were spent),
  error = GEMINI_ERROR_CALL_WEIGHT (default 0.25; a 1-second 503 should not burn
  a whole call), blocked = 0. Legacy rows without a status count as 1.
  ``incomplete`` marks an answered call whose text is unusable (prompt blocked,
  finish_reason other than STOP, or empty text).
- Priority tasks (/1 report, structured extraction, /2 youtube_package) may use
  the whole GEMINI_DAILY_CALL_LIMIT. Every other task stops once the remaining
  budget falls to GEMINI_RESERVED_CALLS (default 3), so optional calls such as
  stock_db_classification cannot starve the daily report / packaging.
- Calls refused by the guard are logged with status=blocked so a "missing"
  youtube_package can be explained from the log.
- Token counts (prompt / output / thoughts / total) and finish_reason are read
  from response.usage_metadata when the SDK provides them.

Prompts and API keys are never written to the log.
"""

import datetime
import json
import os
import sys


DEFAULT_DAILY_LIMIT = 8
DEFAULT_RESERVED_CALLS = 3
DEFAULT_ERROR_CALL_WEIGHT = 0.25

# Tasks that keep the reserved budget. Anything else is treated as optional.
PRIORITY_TASKS = frozenset(
    {
        "market_report",
        "structured_data_extraction",
        "structured_data_extraction_retry",
        "youtube_package",
        "weekend_structured_data_extraction",
    }
)


class GeminiUsageLimitError(RuntimeError):
    pass


def _base_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def usage_log_path():
    # GEMINI_USAGE_LOG_PATH lets the web service keep the log on a persistent disk.
    return os.getenv("GEMINI_USAGE_LOG_PATH") or os.path.join(_base_dir(), "data", "gemini_usage_log.jsonl")


def _today():
    return datetime.datetime.now().strftime("%Y-%m-%d")


def _env_int(name, default, minimum=0):
    raw = os.getenv(name, str(default)).strip()
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def _env_float(name, default, minimum=0.0):
    raw = os.getenv(name, str(default)).strip()
    try:
        return max(minimum, float(raw))
    except ValueError:
        return default


def _daily_limit():
    return _env_int("GEMINI_DAILY_CALL_LIMIT", DEFAULT_DAILY_LIMIT)


def _reserved_calls():
    return _env_int("GEMINI_RESERVED_CALLS", DEFAULT_RESERVED_CALLS)


def _status_weight(status):
    status = str(status or "").strip().lower()
    if status in {"success", "incomplete"} or not status:
        return 1.0
    if status == "blocked":
        return 0.0
    return _env_float("GEMINI_ERROR_CALL_WEIGHT", DEFAULT_ERROR_CALL_WEIGHT)


def is_priority_task(task_name):
    return str(task_name or "").strip() in PRIORITY_TASKS


def _iter_log_rows():
    path = usage_log_path()
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except Exception:
        return


def _today_rows():
    today = _today()
    return [row for row in (_iter_log_rows() or []) if str(row.get("date")) == today]


def get_today_gemini_call_count():
    """Raw number of calls sent today (blocked rows excluded; nothing was sent)."""
    return sum(
        1
        for row in _today_rows()
        if str(row.get("status") or "").strip().lower() != "blocked"
    )


def get_today_gemini_usage():
    """Status-weighted usage that the daily limit is compared against."""
    total = sum(_status_weight(row.get("status")) for row in _today_rows())
    return round(total, 2)


def get_today_status_counts():
    counts = {"success": 0, "incomplete": 0, "error": 0, "blocked": 0}
    for row in _today_rows():
        status = str(row.get("status") or "success").strip().lower()
        counts[status] = counts.get(status, 0) + 1
    return counts


def _format_usage(value):
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def check_budget(task_name):
    """
    Return (allowed, message). Priority tasks may use the full limit; optional
    tasks must leave GEMINI_RESERVED_CALLS for priority tasks.
    """
    limit = _daily_limit()
    if not limit:
        return True, ""
    used = get_today_gemini_usage()
    if is_priority_task(task_name):
        if used >= limit:
            return False, (
                f"Gemini daily call limit reached: {_format_usage(used)}/{limit} "
                f"(task={task_name}). Set GEMINI_DAILY_CALL_LIMIT higher or 0 to disable."
            )
        return True, ""
    reserve = min(_reserved_calls(), max(0, limit - 1))
    ceiling = limit - reserve
    if used >= ceiling:
        return False, (
            f"Gemini optional-task budget reached: {_format_usage(used)}/{limit} "
            f"(task={task_name}; {reserve} calls reserved for "
            f"{'/'.join(sorted(PRIORITY_TASKS))}). "
            "Set GEMINI_RESERVED_CALLS lower or GEMINI_DAILY_CALL_LIMIT higher."
        )
    return True, ""


def _append_usage_row(row):
    path = usage_log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _prompt_size(contents):
    if contents is None:
        return 0
    if isinstance(contents, str):
        return len(contents)
    try:
        return len(json.dumps(contents, ensure_ascii=False))
    except TypeError:
        return len(str(contents))


def _enum_name(value) -> str:
    """Enum label such as "STOP" (google.genai str enums and int-valued proto enums)."""
    if value is None:
        return ""
    raw = getattr(value, "value", None)
    if isinstance(raw, str) and raw:
        return raw
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name
    return str(value)


def response_usage_fields(response):
    """Token counts + finish_reason from a google.genai response (best effort)."""
    fields = {}
    if response is None:
        return fields
    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        for out_key, attr in (
            ("prompt_tokens", "prompt_token_count"),
            ("output_tokens", "candidates_token_count"),
            ("thoughts_tokens", "thoughts_token_count"),
            ("total_tokens", "total_token_count"),
        ):
            value = getattr(usage, attr, None)
            if isinstance(value, (int, float)):
                fields[out_key] = int(value)
    try:
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            reason = _enum_name(getattr(candidates[0], "finish_reason", None))
            if reason:
                fields["finish_reason"] = reason
        feedback = getattr(response, "prompt_feedback", None)
        block = _enum_name(getattr(feedback, "block_reason", None)) if feedback is not None else ""
        if block:
            fields["block_reason"] = block
    except Exception:
        pass
    model_version = getattr(response, "model_version", None)
    if isinstance(model_version, str) and model_version:
        fields["model_version"] = model_version
    return fields


_OK_FINISH = {"", "STOP", "FINISH_REASON_UNSPECIFIED"}


def response_status(response):
    """"success" for usable text, "incomplete" for blocked / truncated / empty answers."""
    fields = response_usage_fields(response)
    block = fields.get("block_reason") or ""
    if block and block != "BLOCKED_REASON_UNSPECIFIED":
        return "incomplete"
    if fields.get("finish_reason", "") not in _OK_FINISH:
        return "incomplete"
    if _response_chars(response) <= 0:
        return "incomplete"
    return "success"


def _response_chars(response):
    try:
        return len(getattr(response, "text", "") or "")
    except Exception:
        return 0


def _base_row(started, task_name, model_name, contents):
    return {
        "date": started.strftime("%Y-%m-%d"),
        "time": started.strftime("%H:%M:%S"),
        "script": os.path.basename(sys.argv[0]) if sys.argv else "",
        "task": task_name,
        "model": model_name or os.getenv("GEMINI_MODEL", ""),
        "priority": "priority" if is_priority_task(task_name) else "optional",
        "prompt_chars": _prompt_size(contents),
    }


def _enforce_budget(task_name, model_name, contents):
    allowed, message = check_budget(task_name)
    if allowed:
        return
    row = _base_row(datetime.datetime.now(), task_name, model_name, contents)
    row.update(
        {
            "response_chars": 0,
            "status": "blocked",
            "error": message[:500],
            "duration_sec": 0.0,
        }
    )
    try:
        _append_usage_row(row)
    except Exception:
        pass
    raise GeminiUsageLimitError(message)


def _log_call(started, task_name, model_name, contents, response, status, error):
    ended = datetime.datetime.now()
    row = _base_row(started, task_name, model_name, contents)
    row.update(
        {
            "response_chars": _response_chars(response),
            "status": status,
            "error": error,
            "duration_sec": round((ended - started).total_seconds(), 2),
        }
    )
    row.update(response_usage_fields(response))
    try:
        _append_usage_row(row)
    except Exception:
        pass


def guarded_generate_content(model, contents, task_name, model_name=None, **kwargs):
    """
    Wrap legacy google.generativeai generate_content calls with the daily budget
    and append-only usage logs.
    """
    _enforce_budget(task_name, model_name, contents)
    started = datetime.datetime.now()
    status = "success"
    error = ""
    response = None
    try:
        response = model.generate_content(contents, **kwargs)
        status = response_status(response)
        return response
    except Exception as exc:
        status = "error"
        error = str(exc)[:500]
        raise
    finally:
        _log_call(started, task_name, model_name, contents, response, status, error)


def guarded_client_generate_content(client, task_name, model_name, contents, **kwargs):
    """Guard google-genai client.models.generate_content calls."""
    _enforce_budget(task_name, model_name, contents)
    started = datetime.datetime.now()
    status = "success"
    error = ""
    response = None
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            **kwargs,
        )
        status = response_status(response)
        return response
    except Exception as exc:
        status = "error"
        error = str(exc)[:500]
        raise
    finally:
        _log_call(started, task_name, model_name, contents, response, status, error)


def print_today_usage(prefix=""):
    limit = _daily_limit()
    used = get_today_gemini_usage()
    counts = get_today_status_counts()
    incomplete = counts.get("incomplete", 0)
    detail = (
        f"成功 {counts.get('success', 0)}"
        + (f"、不完整 {incomplete}" if incomplete else "")
        + f"、失敗 {counts.get('error', 0)}"
        f"（每筆計 {_format_usage(_status_weight('error'))}）、被擋 {counts.get('blocked', 0)}"
    )
    if limit:
        print(
            f"{prefix}Gemini 今日用量：{_format_usage(used)}/{limit}（{detail}；"
            f"非優先任務保留 {min(_reserved_calls(), max(0, limit - 1))} 次給日報/萃取/包裝）"
        )
    else:
        print(f"{prefix}Gemini 今日用量：{_format_usage(used)}（{detail}；未設上限）")
