"""Context retrieval API (plan §5.4).

    bundle = retrieve_context(project, strategy="target_plus_types",
                              target="driver::tx::transmit")
    prompt_part = bundle.render()

Strategies register with ``@strategy("name")``; adding one is a new
function, no changes to existing code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from proctor.context.index import CodeIndex, IndexedItem, build_index


@dataclass(frozen=True)
class Snippet:
    path: str
    kind: str
    file: str
    text: str
    reason: str  # why this snippet is in the bundle — for debugging prompts

    def estimated_tokens(self) -> int:
        return max(1, len(self.text) // 4)


@dataclass(frozen=True)
class ContextBundle:
    target: str
    strategy: str
    snippets: tuple[Snippet, ...]

    def render(self) -> str:
        parts = []
        for snippet in self.snippets:
            parts.append(
                f"// {snippet.kind} {snippet.path} ({snippet.file})\n{snippet.text}"
            )
        return "\n\n".join(parts)

    def estimated_tokens(self) -> int:
        return sum(s.estimated_tokens() for s in self.snippets)


StrategyFn = Callable[[CodeIndex, IndexedItem, Mapping[str, Any]], list[Snippet]]

_STRATEGIES: dict[str, StrategyFn] = {}


def strategy(name: str) -> Callable[[StrategyFn], StrategyFn]:
    def decorator(fn: StrategyFn) -> StrategyFn:
        _STRATEGIES[name] = fn
        return fn

    return decorator


def available_strategies() -> list[str]:
    _load_builtins()
    return sorted(_STRATEGIES)


def _load_builtins() -> None:
    from proctor.context import strategies  # noqa: F401  (registration side effect)


def retrieve_context(
    project: Path | CodeIndex,
    *,
    strategy: str,
    target: str,
    budget_tokens: int | None = None,
    config: Mapping[str, Any] | None = None,
) -> ContextBundle:
    """Retrieve prompt context for ``target`` using a named strategy.

    ``project`` may be a project directory (indexed and cached) or an
    already-built ``CodeIndex``.
    """
    _load_builtins()
    fn = _STRATEGIES.get(strategy)
    if fn is None:
        raise ValueError(
            f"unknown context strategy {strategy!r}; available: {sorted(_STRATEGIES)}"
        )
    index = project if isinstance(project, CodeIndex) else build_index(project)
    item = index.resolve(target)
    snippets = fn(index, item, config or {})

    if budget_tokens is not None:
        kept: list[Snippet] = []
        total = 0
        for snippet in snippets:
            cost = snippet.estimated_tokens()
            if kept and total + cost > budget_tokens:
                break  # keep at least the target snippet
            kept.append(snippet)
            total += cost
        snippets = kept

    return ContextBundle(target=item.path, strategy=strategy, snippets=tuple(snippets))
