"""Provider protocol and registry.

A provider maps the vendor-agnostic ``Request`` onto one vendor's API.
Adding a provider = one module + a ``@register_provider`` decorator; no
caller changes (plan: Extensibility).
"""

from __future__ import annotations

from typing import Any, Protocol

from proctor.llm.types import Request, Response


class Provider(Protocol):
    name: str

    def complete(self, request: Request) -> Response: ...


_REGISTRY: dict[str, type[Any]] = {}


def register_provider(name: str) -> Any:
    def decorator(cls: type[Any]) -> type[Any]:
        _REGISTRY[name] = cls
        return cls

    return decorator


def create_provider(settings: dict[str, Any]) -> Provider:
    # ensure built-in provider modules are imported (registration side effect)
    from proctor.llm.providers import anthropic, openai, replay  # noqa: F401

    name = settings.get("provider")
    if not isinstance(name, str) or not name:
        raise ValueError("llm settings must include a 'provider' name")
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"unknown LLM provider {name!r}; registered: {sorted(_REGISTRY)}"
        )
    provider: Provider = cls(settings)
    return provider
