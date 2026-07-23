"""Anthropic Messages API provider.

Pure ``build_payload``/``parse_http`` functions carry all the mapping
logic so tests never need the network. Usage normalization: Anthropic's
``input_tokens`` EXCLUDES cache reads/writes, so the total is
input + cache_read + cache_creation; ``cached_input_tokens`` is the
cache-read count.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from proctor.llm.providers.base import register_provider
from proctor.llm.types import (
    AuthError,
    ContextLimitExceeded,
    FinishReason,
    ProviderError,
    RateLimited,
    Request,
    Response,
    Usage,
)

DEFAULT_BASE_URL = "https://api.anthropic.com"
API_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 8192

_FINISH_REASONS: dict[str, FinishReason] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_use",
    "refusal": "refusal",
}


def build_payload(
    request: Request, default_model: str, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": request.model or default_model,
        "max_tokens": request.max_tokens or DEFAULT_MAX_TOKENS,
        "messages": [{"role": m.role, "content": m.content} for m in request.messages],
    }
    if request.system is not None:
        payload["system"] = request.system
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    # provider-specific passthrough from [llm] extra, e.g.
    # extra = { thinking = { type = "enabled", budget_tokens = 8000 } }
    if extra:
        payload.update(extra)
    return payload


def parse_http(
    status: int, headers: dict[str, str], body: dict[str, Any], latency_s: float
) -> Response:
    if status == 401 or status == 403:
        raise AuthError(f"anthropic auth failed ({status}): {_error_message(body)}")
    if status == 429:
        raise RateLimited(
            f"anthropic rate limited: {_error_message(body)}",
            retry_after_s=_retry_after(headers),
        )
    if status == 400 and "prompt is too long" in _error_message(body):
        raise ContextLimitExceeded(_error_message(body))
    if status >= 500 or status == 529:
        raise ProviderError(
            f"anthropic server error ({status}): {_error_message(body)}",
            status=status,
        )
    if status != 200:
        raise ProviderError(
            f"anthropic error ({status}): {_error_message(body)}", status=status
        )

    text = "".join(
        block.get("text", "")
        for block in body.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    )
    usage_raw = body.get("usage", {})
    cache_read = int(usage_raw.get("cache_read_input_tokens") or 0)
    cache_creation = int(usage_raw.get("cache_creation_input_tokens") or 0)
    uncached = int(usage_raw.get("input_tokens") or 0)
    return Response(
        text=text,
        finish_reason=_FINISH_REASONS.get(str(body.get("stop_reason")), "stop"),
        provider="anthropic",
        model=str(body.get("model", "")),
        latency_s=latency_s,
        usage=Usage(
            input_tokens=uncached + cache_read + cache_creation,
            cached_input_tokens=cache_read,
            output_tokens=int(usage_raw.get("output_tokens") or 0),
            reasoning_tokens=None,
        ),
        raw=body,
    )


def _error_message(body: dict[str, Any]) -> str:
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message", error))
    return str(body)[:500]


def _retry_after(headers: dict[str, str]) -> float | None:
    value = headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


@register_provider("anthropic")
class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings: dict[str, Any]) -> None:
        self._settings = settings
        self._base_url = str(settings.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
        key_env = str(settings.get("api_key_env", "ANTHROPIC_API_KEY"))
        self._api_key = os.environ.get(key_env)
        self._model = str(settings.get("model", ""))
        self._timeout = float(settings.get("request_timeout_s", 600))
        extra = settings.get("extra")
        self._extra: dict[str, Any] | None = extra if isinstance(extra, dict) else None

    def complete(self, request: Request) -> Response:
        if not self._api_key:
            raise AuthError(
                "no Anthropic API key: set ANTHROPIC_API_KEY (or api_key_env)"
            )
        started = time.monotonic()
        try:
            http = httpx.post(
                f"{self._base_url}/v1/messages",
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": API_VERSION,
                },
                json=build_payload(request, self._model, self._extra),
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"anthropic transport error: {exc}") from exc
        try:
            body = http.json()
        except ValueError:
            body = {"error": {"message": http.text[:500]}}
        return parse_http(
            http.status_code,
            dict(http.headers),
            body,
            time.monotonic() - started,
        )
