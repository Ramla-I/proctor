"""OpenAI Chat Completions provider.

With ``base_url`` this also covers vLLM, Ollama, other local servers,
and Gemini's OpenAI-compatible endpoint (plan §5.1). Usage
normalization: ``prompt_tokens`` already includes cached tokens;
``prompt_tokens_details.cached_tokens`` is the cached subset and
``completion_tokens_details.reasoning_tokens`` maps to
``reasoning_tokens``.
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

DEFAULT_BASE_URL = "https://api.openai.com/v1"

_FINISH_REASONS: dict[str, FinishReason] = {
    "stop": "stop",
    "length": "length",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "refusal",
}


def build_payload(
    request: Request, default_model: str, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    messages: list[dict[str, str]] = []
    if request.system is not None:
        messages.append({"role": "system", "content": request.system})
    messages.extend({"role": m.role, "content": m.content} for m in request.messages)
    payload: dict[str, Any] = {
        "model": request.model or default_model,
        "messages": messages,
    }
    if request.max_tokens is not None:
        payload["max_tokens"] = request.max_tokens
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    # provider-specific passthrough from [llm] extra, e.g.
    # extra = { reasoning_effort = "high" }
    if extra:
        payload.update(extra)
    return payload


def parse_http(
    status: int, headers: dict[str, str], body: dict[str, Any], latency_s: float
) -> Response:
    if status in (401, 403):
        raise AuthError(f"openai auth failed ({status}): {_error_message(body)}")
    if status == 429:
        raise RateLimited(
            f"openai rate limited: {_error_message(body)}",
            retry_after_s=_retry_after(headers),
        )
    if status == 400 and _error_code(body) == "context_length_exceeded":
        raise ContextLimitExceeded(_error_message(body))
    if status >= 500:
        raise ProviderError(
            f"openai server error ({status}): {_error_message(body)}", status=status
        )
    if status != 200:
        raise ProviderError(
            f"openai error ({status}): {_error_message(body)}", status=status
        )

    choices = body.get("choices") or [{}]
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message", {})
    usage_raw = body.get("usage") or {}
    prompt_details = usage_raw.get("prompt_tokens_details") or {}
    completion_details = usage_raw.get("completion_tokens_details") or {}
    reasoning = completion_details.get("reasoning_tokens")
    return Response(
        text=str(message.get("content") or ""),
        finish_reason=_FINISH_REASONS.get(str(choice.get("finish_reason")), "stop"),
        provider="openai",
        model=str(body.get("model", "")),
        latency_s=latency_s,
        usage=Usage(
            input_tokens=int(usage_raw.get("prompt_tokens") or 0),
            cached_input_tokens=int(prompt_details.get("cached_tokens") or 0),
            output_tokens=int(usage_raw.get("completion_tokens") or 0),
            reasoning_tokens=int(reasoning) if reasoning is not None else None,
        ),
        raw=body,
    )


def _error_message(body: dict[str, Any]) -> str:
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message", error))
    return str(body)[:500]


def _error_code(body: dict[str, Any]) -> str | None:
    error = body.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        return str(code) if code is not None else None
    return None


def _retry_after(headers: dict[str, str]) -> float | None:
    value = headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


@register_provider("openai")
class OpenAiProvider:
    name = "openai"

    def __init__(self, settings: dict[str, Any]) -> None:
        self._settings = settings
        self._base_url = str(settings.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
        key_env = str(settings.get("api_key_env", "OPENAI_API_KEY"))
        self._api_key = os.environ.get(key_env, "")
        self._model = str(settings.get("model", ""))
        self._timeout = float(settings.get("request_timeout_s", 600))
        extra = settings.get("extra")
        self._extra: dict[str, Any] | None = extra if isinstance(extra, dict) else None

    def complete(self, request: Request) -> Response:
        headers = {"Content-Type": "application/json"}
        if self._api_key:  # local servers often need no key
            headers["Authorization"] = f"Bearer {self._api_key}"
        started = time.monotonic()
        try:
            http = httpx.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=build_payload(request, self._model, self._extra),
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"openai transport error: {exc}") from exc
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
