"""
Unified Gemini access via the modern ``google.genai`` SDK.

All production modules should call ``generate_text`` / ``generate_json`` here
instead of the deprecated ``google.generativeai`` package.
"""

from __future__ import annotations

import os
from functools import lru_cache

from google import genai
from google.genai import types

from modules.ai_usage_guard import guarded_client_generate_content


def default_model_name() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-flash-latest")


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


def response_text(response) -> str:
    """Best-effort text extraction from google.genai responses."""
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
                part_text = getattr(part, "text", None)
                if part_text:
                    chunks.append(str(part_text))
            if chunks:
                return "".join(chunks)
    except Exception:
        pass
    return str(response or "")


def generate_text(
    api_key: str,
    contents,
    *,
    task_name: str,
    model_name: str | None = None,
    response_mime_type: str | None = None,
    **config_kwargs,
) -> str:
    """
    Generate content and return response text.

    ``contents`` may be a string or a list accepted by google.genai.
    Extra kwargs are passed into ``GenerateContentConfig``.
    """
    model = model_name or default_model_name()
    client = get_client(api_key)

    config = None
    if response_mime_type or config_kwargs:
        cfg = dict(config_kwargs)
        if response_mime_type:
            cfg["response_mime_type"] = response_mime_type
        config = types.GenerateContentConfig(**cfg)

    # google.genai accepts str or Content lists; normalize bare strings to list for consistency.
    payload = contents if not isinstance(contents, str) else contents

    kwargs = {}
    if config is not None:
        kwargs["config"] = config

    response = guarded_client_generate_content(
        client,
        task_name=task_name,
        model_name=model,
        contents=payload,
        **kwargs,
    )
    return response_text(response)


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
