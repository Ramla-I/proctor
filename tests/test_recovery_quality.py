"""Unit tests for the recovery-quality panel.

The tools are stubbed (no cargo / binaries); the real symbolic passes are
exercised by recovery_metrics' cargo tests, and the whole panel by the e2e
fixture test. Mirrors tests/test_unsafe_eval.py (pure-object tests + one
stubbed-seam integration test).
"""

from __future__ import annotations

from typing import Any

import proctor.testing.recovery_quality as rq
from proctor.testing.recovery_quality import (
    FileQuality,
    RecoveryQualityReport,
    measure_recovery_quality,
)


def _fq(**kw: Any) -> FileQuality:
    base: dict[str, Any] = dict(
        file="src/x.rs",
        residual_unsafe=0,
        net_unsafe=0,
        churn_added=0,
        churn_removed=0,
        raw_ptr_fields=0,
        into_from_raw=0,
        raw_derefs=0,
        malloc_free=0,
        non_boundary_unsafe=0,
        adopts_target=True,
        parse_ok=True,
    )
    base.update(kw)
    return FileQuality(**base)


def _report(files: list[FileQuality], **kw: Any) -> RecoveryQualityReport:
    base: dict[str, Any] = dict(
        src="a",
        dst="b",
        touched_files=[f.file for f in files],
        wandering_files=[],
        abi_changed=[],
        abi_removed=[],
        abi_added=[],
        files=files,
    )
    base.update(kw)
    return RecoveryQualityReport(**base)


def test_clean_recovery_needs_no_repair() -> None:
    r = _report([_fq(residual_unsafe=8, net_unsafe=-58)])
    assert r.residual_unsafe == 8
    assert r.facade_signal == 0
    assert not r.needs_repair()
    assert r.render_critique() == ""


def test_facade_signals_trigger_repair_and_named_critique() -> None:
    r = _report(
        [_fq(residual_unsafe=24, net_unsafe=-42, raw_ptr_fields=1, malloc_free=5)],
        abi_changed=["binary_heap_new", "binary_heap_pop"],
    )
    assert r.facade_signal == 6
    assert r.needs_repair()
    crit = r.render_critique()
    assert "retained C allocation" in crit
    assert "raw-pointer struct field" in crit
    assert "binary_heap_new" in crit
    assert "24" in crit  # residual reported as context


def test_dropped_api_triggers_repair() -> None:
    r = _report([_fq(residual_unsafe=8)], abi_removed=["binary_heap_pop"])
    assert r.needs_repair()
    assert "DROPPED" in r.render_critique()


def test_wandering_edit_triggers_repair() -> None:
    r = _report(
        [_fq(), _fq(file="src/other.rs")],
        wandering_files=["src/other.rs"],
    )
    assert r.needs_repair()
    assert "src/other.rs" in r.render_critique()


def test_residual_alone_is_not_a_trigger_without_threshold() -> None:
    # residual has no peer-free floor (the extern "C" boundary needs some), so
    # it is not a standalone trigger — only an explicit threshold makes it one.
    r = _report([_fq(residual_unsafe=8)])
    assert not r.needs_repair()
    assert r.needs_repair(residual_threshold=5)


def test_measure_composes_metrics_and_unsafe(monkeypatch: Any, tmp_path: Any) -> None:
    metrics = {
        "touched_files": ["src/binary_heap.rs"],
        "abi": {
            "changed": [{"name": "binary_heap_new", "before": "a", "after": "b"}],
            "removed": [],
            "added": [],
        },
        "files": {
            "src/binary_heap.rs": {
                "churn_added": 79,
                "churn_removed": 97,
                "raw_ptr_fields": 1,
                "into_from_raw": 0,
                "malloc_free": 5,
                "non_boundary_unsafe": 2,
                "adopts_target": True,
                "in_after": True,
                "parse_ok": True,
            }
        },
    }
    monkeypatch.setattr(rq, "_run_metrics", lambda src, dst, **k: metrics)
    scores = {("dst", "src/binary_heap.rs"): 24, ("src", "src/binary_heap.rs"): 66}
    monkeypatch.setattr(
        rq, "_file_unsafe", lambda crate, rel: scores[(crate.name, rel)]
    )

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    r = measure_recovery_quality(src, dst, candidate="src/binary_heap.rs")

    assert r.residual_unsafe == 24
    assert r.net_unsafe == 24 - 66
    assert r.non_boundary_unsafe == 2
    assert r.abi_changed == ["binary_heap_new"]
    assert r.files[0].malloc_free == 5
    assert r.wandering_files == []
    assert r.needs_repair()


def test_scope_crate_aggregates_all_after_files(
    monkeypatch: Any, tmp_path: Any
) -> None:
    # touched=binary_heap.rs, but the crate also has an untouched mod.rs; crate
    # scope should aggregate BOTH; touched scope only the changed file.
    metrics = {
        "touched_files": ["src/binary_heap.rs"],
        "abi": {"changed": [], "removed": [], "added": []},
        "files": {
            "src/binary_heap.rs": {"in_after": True, "non_boundary_unsafe": 2},
            "src/mod.rs": {"in_after": True, "non_boundary_unsafe": 3},
        },
    }
    monkeypatch.setattr(rq, "_run_metrics", lambda src, dst, **k: metrics)
    per = {"src/binary_heap.rs": 24, "src/mod.rs": 4}
    monkeypatch.setattr(rq, "_file_unsafe", lambda crate, rel: per.get(rel, 0))

    src, dst = tmp_path / "src", tmp_path / "dst"
    touched = measure_recovery_quality(src, dst, scope="touched")
    crate = measure_recovery_quality(src, dst, scope="crate")
    assert touched.residual_unsafe == 24 and touched.non_boundary_unsafe == 2
    assert crate.residual_unsafe == 28 and crate.non_boundary_unsafe == 5
