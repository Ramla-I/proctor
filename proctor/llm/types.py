"""Vendor-agnostic LLM request/response types (plan §5.1).

Text-only in v1; tool use is a later extension. Usage normalization:
``input_tokens`` is the TOTAL prompt tokens (cached included) and
``cached_input_tokens`` is the cached subset — providers that report
them differently are normalized in their modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant"]
FinishReason = Literal["stop", "length", "tool_use", "refusal", "error"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class RequestMetadata:
    """Attribution recorded with every usage record."""

    run_id: str | None = None
    stage: str | None = None
    item: str | None = None
    prompt_id: str | None = None
    prompt_version: int | None = None
    prompt_hash: str | None = None


@dataclass(frozen=True)
class Request:
    messages: tuple[Message, ...]
    system: str | None = None
    model: str | None = None  # overrides the client's configured model
    max_tokens: int | None = None
    temperature: float | None = None
    metadata: RequestMetadata = field(default_factory=RequestMetadata)


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int | None = None


@dataclass(frozen=True)
class Response:
    text: str
    finish_reason: FinishReason
    provider: str
    model: str
    latency_s: float
    usage: Usage
    raw: dict[str, Any] = field(default_factory=dict)


class LlmError(Exception):
    """Base for all LLM API errors."""


class ProviderError(LlmError):
    """Provider-side failure (5xx, malformed response). Retryable."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class AuthError(LlmError):
    """Bad or missing credentials. Not retryable."""


class RateLimited(LlmError):
    """429; retry after backoff."""

    def __init__(self, message: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class ContextLimitExceeded(LlmError):
    """Structured error for inputs exceeding the model's context window."""

    def __init__(
        self,
        message: str,
        *,
        needed_tokens: int | None = None,
        limit_tokens: int | None = None,
    ) -> None:
        super().__init__(message)
        self.needed_tokens = needed_tokens
        self.limit_tokens = limit_tokens


class BudgetExceeded(LlmError):
    """The stage's advisory budget was exhausted."""
