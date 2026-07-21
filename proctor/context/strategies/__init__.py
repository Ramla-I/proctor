"""Built-in context-retrieval strategies.

M5 ships ``target_only`` and ``target_plus_types`` (plan: "first
implementation may support only a simple strategy"). Follow-ups —
``target_plus_types_one_hop`` (Intel-style), ``enclosing_module``,
``whole_submodule`` — are each one new function here.
"""

from __future__ import annotations

from typing import Any, Mapping

from proctor.context.api import Snippet, strategy
from proctor.context.index import CodeIndex, IndexedItem


def _snippet(item: IndexedItem, reason: str) -> Snippet:
    return Snippet(
        path=item.path,
        kind=item.kind,
        file=item.file,
        text=item.text,
        reason=reason,
    )


@strategy("target_only")
def target_only(
    index: CodeIndex, item: IndexedItem, config: Mapping[str, Any]
) -> list[Snippet]:
    return [_snippet(item, "target")]


@strategy("target_plus_types")
def target_plus_types(
    index: CodeIndex, item: IndexedItem, config: Mapping[str, Any]
) -> list[Snippet]:
    snippets = [_snippet(item, "target")]
    for referenced in index.type_refs_of(item.path):
        snippets.append(_snippet(referenced, f"type referenced by {item.path}"))
    return snippets
