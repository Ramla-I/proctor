"""Context retrieval: resolution, strategies, budget — on a synthetic
index (no cargo needed)."""

import pytest

from proctor.context.api import available_strategies, retrieve_context
from proctor.context.index import CodeIndex, IndexError_

INDEX_DATA = {
    "schema_version": 1,
    "items": [
        {
            "path": "driver::tx::transmit",
            "kind": "fn",
            "file": "src/tx.rs",
            "start_line": 10,
            "end_line": 20,
            "text": "fn transmit(f: Frame, r: &mut Ring) -> Status { push(f, r) }",
        },
        {
            "path": "driver::Frame",
            "kind": "struct",
            "file": "src/lib.rs",
            "start_line": 1,
            "end_line": 4,
            "text": "struct Frame { data: Vec<u8> }",
        },
        {
            "path": "driver::Ring",
            "kind": "struct",
            "file": "src/lib.rs",
            "start_line": 6,
            "end_line": 9,
            "text": "struct Ring { slots: Vec<Frame> }",
        },
        {
            "path": "driver::Status",
            "kind": "enum",
            "file": "src/lib.rs",
            "start_line": 11,
            "end_line": 14,
            "text": "enum Status { Ok, Full }",
        },
        {
            "path": "driver::tx::push",
            "kind": "fn",
            "file": "src/tx.rs",
            "start_line": 22,
            "end_line": 30,
            "text": "fn push(f: Frame, r: &mut Ring) -> Status { Status::Ok }",
        },
        {
            "path": "other::transmit",
            "kind": "fn",
            "file": "src/other.rs",
            "start_line": 1,
            "end_line": 2,
            "text": "fn transmit() {}",
        },
    ],
    "edges": [
        {"from": "driver::tx::transmit", "to": "Frame", "kind": "type_ref"},
        {"from": "driver::tx::transmit", "to": "Ring", "kind": "type_ref"},
        {"from": "driver::tx::transmit", "to": "Status", "kind": "type_ref"},
        {"from": "driver::tx::transmit", "to": "push", "kind": "call"},
    ],
}


def _index() -> CodeIndex:
    return CodeIndex(INDEX_DATA)


def test_resolve_exact_and_suffix() -> None:
    index = _index()
    assert index.resolve("driver::tx::transmit").kind == "fn"
    assert index.resolve("tx::transmit").path == "driver::tx::transmit"
    assert index.resolve("Frame").path == "driver::Frame"


def test_resolve_ambiguous_and_missing() -> None:
    index = _index()
    with pytest.raises(IndexError_, match="ambiguous"):
        index.resolve("transmit")
    with pytest.raises(IndexError_, match="not found"):
        index.resolve("no_such_thing")


def test_target_only() -> None:
    bundle = retrieve_context(_index(), strategy="target_only", target="tx::transmit")
    assert [s.path for s in bundle.snippets] == ["driver::tx::transmit"]
    assert "fn transmit" in bundle.render()


def test_target_plus_types() -> None:
    bundle = retrieve_context(
        _index(), strategy="target_plus_types", target="tx::transmit"
    )
    paths = [s.path for s in bundle.snippets]
    assert paths[0] == "driver::tx::transmit"
    assert set(paths[1:]) == {"driver::Frame", "driver::Ring", "driver::Status"}
    # callees are NOT included by this strategy
    assert "driver::tx::push" not in paths


def test_budget_keeps_target_first() -> None:
    bundle = retrieve_context(
        _index(),
        strategy="target_plus_types",
        target="tx::transmit",
        budget_tokens=20,
    )
    assert bundle.snippets[0].path == "driver::tx::transmit"
    assert bundle.estimated_tokens() <= 40  # target always kept even if over


def test_unknown_strategy_lists_available() -> None:
    with pytest.raises(ValueError, match="available"):
        retrieve_context(_index(), strategy="nope", target="Frame")
    assert "target_only" in available_strategies()


def test_callees_edge_query() -> None:
    index = _index()
    callees = index.callees_of("driver::tx::transmit")
    assert [c.path for c in callees] == ["driver::tx::push"]
