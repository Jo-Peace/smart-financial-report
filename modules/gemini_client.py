"""
Unified Gemini access via the modern ``google.genai`` SDK.

All production modules should call ``generate_text`` / ``generate_json`` here
instead of the deprecated ``google.generativeai`` package.

Reliability rules:
- ``response_text`` never returns the SDK repr; an empty / blocked response is
  "" and ``generate_text`` raises ``GeminiEmptyResponse``.
- A candidate whose finish_reason is not STOP (MAX_TOKENS, SAFETY, RECITATION,
  ...) raises ``GeminiIncompleteResponse`` instead of being saved as a report.
  Errors marked ``retryable`` are retried by ``modules.api_retry``.
- The code default model is a pinned stable version (``STABLE_DEFAULT_MODEL``).
  ``GEMINI_MODEL`` still overrides it; preview / ``-latest`` aliases print a
  one-time warning.
- When the primary model fails with an overload / timeout / quota / not-found
  error, ``GEMINI_FALLBACK_MODELS`` (comma list; set to ``none`` to disable)
  are tried once each. Unset, the backup is ``gemini-2.5-flash``, or
  ``gemini-2.5-flash-lite`` when the primary already is the pinned default, so
  following the pinning advice never silently removes the backup. A fallback
  list that ends up empty after de-duplication prints a one-time warning. The
  usage log records the model that was actually called.
- Callers that wrap ``generate_text`` in a tenacity retry
  (``analyzer._call_gemini_with_retry`` for market_report / weekly / US /
  weekend reports, ``youtube_content_generator._generate_package_text``) use
  ``RetryPlan``: a *retryable* primary error (503 / 429 / timeout) is first
  retried on the primary with backoff, and the fallback model is only tried
  from attempt ``GEMINI_FALLBACK_AFTER_ATTEMPTS`` (default: the last attempt)
  or on the attempt right after a truncated (MAX_TOKENS) answer. A
  non-retryable but fallback-worthy primary error (404 retired model) goes to
  the fallback at once. A second MAX_TOKENS answer stops the retry, so one
  prompt that never fits costs at most 3 weighted calls.
- If the fallback fails too, the *primary* error is raised (fallback errors are
  attached as a note), so a 404 / 400 from a misconfigured fallback never
  turns a retryable 503 into a non-retryable failure.
"""

from __future__ import annotations

import os
from functools import lru_cache

from google import genai
from google.genai import types

from modules.ai_usage_guard import GeminiUsageLimitError, guarded_client_generate_content

# Pinned, GA model used when GEMINI_MODEL is unset. Never a -latest / -preview alias.
STABLE_DEFAULT_MODEL = "gemini-2.5-flash"
# Without GEMINI_FALLBACK_MODELS only the first entry that differs from the
# primary is used (one backup call), so the pinned default still has a backup.
DEFAULT_FALLBACK_MODELS = (STABLE_DEFAULT_MODEL, "gemini-2.5-flash-lite")
_DISABLED_VALUES = frozenset({"", "none", "off", "0", "false"})

_UNPINNED_MARKERS = ("preview", "-latest", "-exp", "experimental")
_BLOCKING_FINISH_REASONS = frozenset(
    {
        "SAFETY",
        "PROHIBITED_CONTENT",
        "BLOCKLIST",
        "SPII",
        "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT",
    }
)
_OK_FINISH_REASONS = frozenset({"STOP", "FINISH_REASON_UNSPECIFIED", ""})
# Extraction / packaging tasks may opt into lighter thinking via env.
LIGHT_THINKING_TASKS = frozenset(
    {
        "structured_data_extraction",
        "structured_data_extraction_retry",
        "weekend_structured_data_extraction",
        "youtube_package",
        "stock_db_classification",
    }
)

_warned_models: set[str] = set()
_warned_no_fallback: set[str] = set()


class GeminiResponseError(RuntimeError):
    """Gemini answered, but without usable / complete text."""

    def __init__(self, message: str, *, reason: str = "", retryable: bool = False):
        super().__init__(message)
        self.reason = reason
        self.retryable = retryable


class GeminiEmptyResponse(GeminiResponseError):
    pass


class GeminiIncompleteResponse(GeminiResponseError):
    pass


def is_unpinned_model(model_name: str) -> bool:
    name = str(model_name or "").strip().lower()
    return any(marker in name for marker in _UNPINNED_MARKERS)


def _warn_unpinned(model_name: str) -> None:
    if not is_unpinned_model(model_name) or model_name in _warned_models:
        return
    _warned_models.add(model_name)
    fallbacks = fallback_model_names(model_name)
    fb = f"；備援模型：{', '.join(fallbacks)}" if fallbacks else "；未設定備援模型"
    print(
        f"  ⚠️  GEMINI_MODEL={model_name} 不是固定版本（preview / -latest 可能改版或下架，產出會悄悄漂移）。"
        f"正式環境建議固定為穩定版本，例如 {STABLE_DEFAULT_MODEL}{fb}"
    )


def _warn_no_fallback(model_name: str) -> None:
    """One-time notice when GEMINI_FALLBACK_MODELS only repeats the primary."""
    raw = os.getenv("GEMINI_FALLBACK_MODELS")
    if raw is not None and raw.strip().lower() in _DISABLED_VALUES:
        return  # explicitly disabled
    if fallback_model_names(model_name) or model_name in _warned_no_fallback:
        return
    _warned_no_fallback.add(model_name)
    print(
        f"  ⚠️  GEMINI_FALLBACK_MODELS 只有主模型 {model_name}，去重後沒有備援模型；"
        f"主模型 503／下架時不會改試其他模型（可設 GEMINI_FALLBACK_MODELS=gemini-2.5-flash-lite）"
    )


def default_model_name() -> str:
    return (os.getenv("GEMINI_MODEL") or "").strip() or STABLE_DEFAULT_MODEL


def fallback_model_names(primary: str | None = None) -> list[str]:
    raw = os.getenv("GEMINI_FALLBACK_MODELS")
    if raw is None:
        default = next((name for name in DEFAULT_FALLBACK_MODELS if name != primary), "")
        return [default] if default else []
    if raw.strip().lower() in _DISABLED_VALUES:
        return []
    names = [part.strip() for part in raw.split(",") if part.strip()]
    out = []
    for name in names:
        if name and name != primary and name not in out:
            out.append(name)
    return out


@lru_cache(maxsize=4)
def get_client(api_key: str) -> genai.Client:
    if not api_key:
        raise ValueError("Gemini API key is required")
    # google.genai HttpOptions.timeout is milliseconds. Default 3 min so
    # generate_report cannot hang silently for 10+ minutes with no HTTP log.
    timeout_ms = int(os.getenv("GEMINI_HTTP_TIMEOUT_MS") or "180000")
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=timeout_ms),
    )


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


def response_text(response) -> str:
    """Best-effort text extraction from google.genai responses ("" when none)."""
    if response is None:
        return ""
    try:
        text = getattr(response, "text", None)
        if text:
            return str(text)
    except Exception:
        pass
    try:
        candidates = getattr(response, "candidates", None) or []
        for cand in candidates:
            content = getattr(cand, "content", None)
            parts = getattr(content, "parts", None) or []
            chunks = []
            for part in parts:
                if getattr(part, "thought", False):
                    continue
                part_text = getattr(part, "text", None)
                if part_text:
                    chunks.append(str(part_text))
            if chunks:
                return "".join(chunks)
    except Exception:
        pass
    # Never return str(response): the pydantic repr is not report content.
    return ""


def finish_reason(response) -> str:
    try:
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            return _enum_name(getattr(candidates[0], "finish_reason", None))
    except Exception:
        pass
    return ""


def checked_response_text(response, *, task_name: str = "", allow_incomplete: bool = False) -> str:
    """
    Return text only for a complete answer.

    Raises GeminiEmptyResponse (blocked prompt / no text) or
    GeminiIncompleteResponse (finish_reason other than STOP).
    """
    label = f"[{task_name}] " if task_name else ""
    feedback = getattr(response, "prompt_feedback", None) if response is not None else None
    block = _enum_name(getattr(feedback, "block_reason", None)) if feedback is not None else ""
    if block and block != "BLOCKED_REASON_UNSPECIFIED":
        raise GeminiEmptyResponse(
            f"{label}Gemini blocked the prompt (block_reason={block})",
            reason=block,
            retryable=False,
        )
    text = response_text(response)
    reason = finish_reason(response)
    if reason and reason not in _OK_FINISH_REASONS and not allow_incomplete:
        retryable = reason not in _BLOCKING_FINISH_REASONS
        raise GeminiIncompleteResponse(
            f"{label}Gemini response incomplete (finish_reason={reason}, chars={len(text)})",
            reason=reason,
            retryable=retryable,
        )
    if not text.strip():
        raise GeminiEmptyResponse(
            f"{label}Gemini returned empty text (finish_reason={reason or 'n/a'})",
            reason=reason or "EMPTY",
            retryable=reason not in _BLOCKING_FINISH_REASONS,
        )
    return text


def should_try_fallback(exc: BaseException) -> bool:
    """Overload / timeout / quota / missing-model errors may succeed on another model."""
    if isinstance(exc, GeminiUsageLimitError):
        return False
    if isinstance(exc, GeminiResponseError):
        return bool(exc.retryable)
    text = str(exc)
    upper = text.upper()
    if any(code in text for code in ("429", "500", "502", "503", "504", "404")):
        return True
    if any(
        token in upper
        for token in ("UNAVAILABLE", "RESOURCE_EXHAUSTED", "DEADLINE_EXCEEDED", "OVERLOADED", "NOT_FOUND", "INTERNAL")
    ):
        return True
    if "timed out" in text.lower() or "timeout" in text.lower():
        return True
    try:
        import httpx

        if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError)):
            return True
    except ImportError:
        pass
    return False


def _is_retryable(exc: BaseException) -> bool:
    from modules.api_retry import is_retryable_exception

    return is_retryable_exception(exc)


def fallback_after_attempts(max_attempts: int) -> int:
    """1-based outer-retry attempt from which the fallback model may be tried."""
    max_attempts = max(1, int(max_attempts or 1))
    raw = (os.getenv("GEMINI_FALLBACK_AFTER_ATTEMPTS") or "").strip()
    try:
        wanted = int(raw) if raw else max_attempts
    except ValueError:
        wanted = max_attempts
    return max(1, min(wanted, max_attempts))


class RetryPlan:
    """
    State shared by the attempts of one tenacity-wrapped Gemini call.

    ``start()`` begins an attempt and says whether the fallback model may be
    used on it; ``failed(exc)`` stops a third try after two MAX_TOKENS answers
    (each one burns a full call of the daily budget).
    """

    def __init__(self, max_attempts: int):
        self.max_attempts = max(1, int(max_attempts or 1))
        self.attempt = 0
        self.truncated = 0

    def start(self) -> bool:
        self.attempt += 1
        if self.truncated:
            # One truncation already: this may be the last try on this prompt.
            return True
        return self.attempt >= fallback_after_attempts(self.max_attempts)

    def failed(self, exc: BaseException) -> None:
        if isinstance(exc, GeminiIncompleteResponse) and exc.reason == "MAX_TOKENS":
            self.truncated += 1
            if self.truncated >= 2:
                exc.retryable = False


def unwrap_retry_error(exc: BaseException) -> BaseException:
    """tenacity ``RetryError`` → the last attempt's real exception (else ``exc``)."""
    last = getattr(getattr(exc, "last_attempt", None), "exception", None)
    try:
        cause = last() if callable(last) else None
    except Exception:
        cause = None
    return cause or exc


def _attach_note(exc: BaseException, note: str) -> None:
    add_note = getattr(exc, "add_note", None)
    if note and callable(add_note):
        try:
            add_note(note)
        except Exception:
            pass


def _light_thinking_config(task_name: str, model: str):
    if task_name not in LIGHT_THINKING_TASKS:
        return None
    name = str(model or "").lower()
    level = (os.getenv("GEMINI_LIGHT_THINKING_LEVEL") or "").strip().lower()
    budget = (os.getenv("GEMINI_LIGHT_THINKING_BUDGET") or "").strip()
    try:
        if level and name.startswith("gemini-3"):
            return types.ThinkingConfig(thinking_level=level)
        if budget and name.startswith("gemini-2.5"):
            return types.ThinkingConfig(thinking_budget=int(budget))
    except Exception as exc:
        print(f"  ⚠️  忽略無效的 thinking 設定（{task_name}）：{exc}")
    return None


def _build_config(task_name: str, model: str, response_mime_type: str | None, config_kwargs: dict):
    cfg = dict(config_kwargs)
    if response_mime_type:
        cfg["response_mime_type"] = response_mime_type
    if "thinking_config" not in cfg:
        thinking = _light_thinking_config(task_name, model)
        if thinking is not None:
            cfg["thinking_config"] = thinking
    if not cfg:
        return None
    return types.GenerateContentConfig(**cfg)


def _generate_once(client, *, task_name, model, contents, response_mime_type, allow_incomplete, config_kwargs) -> str:
    config = _build_config(task_name, model, response_mime_type, config_kwargs)
    kwargs = {}
    if config is not None:
        kwargs["config"] = config
    response = guarded_client_generate_content(
        client,
        task_name=task_name,
        model_name=model,
        contents=contents,
        **kwargs,
    )
    return checked_response_text(response, task_name=task_name, allow_incomplete=allow_incomplete)


def generate_text(
    api_key: str,
    contents,
    *,
    task_name: str,
    model_name: str | None = None,
    response_mime_type: str | None = None,
    allow_incomplete: bool = False,
    use_fallback: bool = True,
    **config_kwargs,
) -> str:
    """
    Generate content and return response text.

    ``contents`` may be a string or a list accepted by google.genai.
    Extra kwargs are passed into ``GenerateContentConfig``.
    Raises GeminiEmptyResponse / GeminiIncompleteResponse instead of returning
    unusable text; tries ``fallback_model_names`` on transient failures.
    ``use_fallback=False`` (an outer retry that is not on its last attempt)
    re-raises a retryable primary error so it is retried on the primary first.
    """
    primary = model_name or default_model_name()
    _warn_unpinned(primary)
    _warn_no_fallback(primary)
    client = get_client(api_key)
    call = dict(
        task_name=task_name,
        contents=contents,
        response_mime_type=response_mime_type,
        allow_incomplete=allow_incomplete,
        config_kwargs=config_kwargs,
    )
    try:
        return _generate_once(client, model=primary, **call)
    except Exception as exc:
        primary_exc = exc

    fallbacks = fallback_model_names(primary)
    if not fallbacks or not should_try_fallback(primary_exc):
        raise primary_exc
    primary_retryable = _is_retryable(primary_exc)
    if not use_fallback and primary_retryable:
        raise primary_exc

    errors: list[tuple[str, BaseException]] = []
    previous = primary
    for model in fallbacks:
        print(f"  ⚠️  [{task_name}] {previous} 失敗（{str(errors[-1][1] if errors else primary_exc)[:160]}），改試備援模型 {model}")
        try:
            text = _generate_once(client, model=model, **call)
            print(f"  ✅ [{task_name}] 主模型失敗，已改用備援模型 {model} 完成")
            return text
        except Exception as exc:
            errors.append((model, exc))
            previous = model
            if not should_try_fallback(exc):
                break

    # Keep the primary error: its retryability decides the outer retry. Only a
    # non-retryable primary (e.g. 404 retired model) yields to a retryable
    # fallback error, so the next attempt can reach the fallback again.
    chosen = primary_exc
    if not primary_retryable:
        chosen = next((exc for _model, exc in errors if _is_retryable(exc)), primary_exc)
    _attach_note(
        chosen,
        "; ".join(f"fallback {model} failed: {str(exc)[:200]}" for model, exc in errors if exc is not chosen)
        + ("" if chosen is primary_exc else f"; primary {primary} failed: {str(primary_exc)[:200]}"),
    )
    raise chosen


def generate_json(
    api_key: str,
    contents,
    *,
    task_name: str,
    model_name: str | None = None,
    **config_kwargs,
) -> str:
    """Generate content with JSON mime type; returns raw text for the caller to parse."""
    return generate_text(
        api_key,
        contents,
        task_name=task_name,
        model_name=model_name,
        response_mime_type="application/json",
        **config_kwargs,
    )
