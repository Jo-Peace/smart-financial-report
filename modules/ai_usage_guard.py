import datetime
import json
import os
import sys


DEFAULT_DAILY_LIMIT = 8


class GeminiUsageLimitError(RuntimeError):
    pass


def _base_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def usage_log_path():
    return os.path.join(_base_dir(), "data", "gemini_usage_log.jsonl")


def _today():
    return datetime.datetime.now().strftime("%Y-%m-%d")


def _daily_limit():
    raw = os.getenv("GEMINI_DAILY_CALL_LIMIT", str(DEFAULT_DAILY_LIMIT)).strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_DAILY_LIMIT


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


def get_today_gemini_call_count():
    today = _today()
    count = 0
    for row in _iter_log_rows() or []:
        if str(row.get("date")) == today:
            count += 1
    return count


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


def guarded_generate_content(model, contents, task_name, model_name=None, **kwargs):
    """
    Wrap Gemini generate_content calls with daily call limiting and append-only usage logs.
    This intentionally logs metadata only; prompts and API keys are never written.
    """
    limit = _daily_limit()
    used = get_today_gemini_call_count()
    if limit and used >= limit:
        raise GeminiUsageLimitError(
            f"Gemini daily call limit reached: {used}/{limit}. "
            "Set GEMINI_DAILY_CALL_LIMIT higher or 0 to disable."
        )

    started = datetime.datetime.now()
    status = "success"
    error = ""
    response = None
    try:
        response = model.generate_content(contents, **kwargs)
        return response
    except Exception as exc:
        status = "error"
        error = str(exc)[:500]
        raise
    finally:
        ended = datetime.datetime.now()
        response_chars = 0
        try:
            response_chars = len(getattr(response, "text", "") or "")
        except Exception:
            response_chars = 0
        row = {
            "date": started.strftime("%Y-%m-%d"),
            "time": started.strftime("%H:%M:%S"),
            "script": os.path.basename(sys.argv[0]) if sys.argv else "",
            "task": task_name,
            "model": model_name or os.getenv("GEMINI_MODEL", ""),
            "prompt_chars": _prompt_size(contents),
            "response_chars": response_chars,
            "status": status,
            "error": error,
            "duration_sec": round((ended - started).total_seconds(), 2),
        }
        try:
            _append_usage_row(row)
        except Exception:
            pass


def guarded_client_generate_content(client, task_name, model_name, contents, **kwargs):
    """Guard google-genai client.models.generate_content calls."""
    limit = _daily_limit()
    used = get_today_gemini_call_count()
    if limit and used >= limit:
        raise GeminiUsageLimitError(
            f"Gemini daily call limit reached: {used}/{limit}. "
            "Set GEMINI_DAILY_CALL_LIMIT higher or 0 to disable."
        )

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
        return response
    except Exception as exc:
        status = "error"
        error = str(exc)[:500]
        raise
    finally:
        ended = datetime.datetime.now()
        response_chars = 0
        try:
            response_chars = len(getattr(response, "text", "") or "")
        except Exception:
            response_chars = 0
        row = {
            "date": started.strftime("%Y-%m-%d"),
            "time": started.strftime("%H:%M:%S"),
            "script": os.path.basename(sys.argv[0]) if sys.argv else "",
            "task": task_name,
            "model": model_name,
            "prompt_chars": _prompt_size(contents),
            "response_chars": response_chars,
            "status": status,
            "error": error,
            "duration_sec": round((ended - started).total_seconds(), 2),
        }
        try:
            _append_usage_row(row)
        except Exception:
            pass


def print_today_usage(prefix=""):
    limit = _daily_limit()
    used = get_today_gemini_call_count()
    if limit:
        print(f"{prefix}Gemini 今日用量：{used}/{limit} calls")
    else:
        print(f"{prefix}Gemini 今日用量：{used} calls（未設上限）")
