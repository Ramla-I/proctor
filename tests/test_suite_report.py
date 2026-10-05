"""suite_report: reads a bench_no_falco verify.json and prints per-case
vectors + final-stage unsafe + idiomaticity — with the measure functions
stubbed (no cargo/clippy needed)."""

import json
from pathlib import Path

import pytest

from proctor.testing import suite_report
from proctor.testing.idiomaticity_eval import IdiomReport
from proctor.testing.recovery_quality import FileQuality, RecoveryQualityReport
from proctor.testing.unsafe_eval import UnsafeReport


def _u(score: int, lines: int) -> UnsafeReport:
    return UnsafeReport.from_stats(
        {"unsafe_score": score, "unsafe_blocks": score, "total_lines": lines}
    )


def _write_case(
    run: Path, leaf: str, stages=(("00", "c2rust"), ("01", "crat"))
) -> None:
    """A case dir with one Cargo project per stage (default final = crat)."""
    for ordinal, sid in stages:
        rust = run / leaf / "stages" / f"{ordinal}-{sid}" / "out" / "rust"
        rust.mkdir(parents=True)
        (rust / "Cargo.toml").write_text("[package]\nname='x'\n", "utf-8")
        (rust / "src").mkdir()


def _verify_json(run: Path, cases: list[dict]) -> None:
    passed = sum(c["passed"] for c in cases)
    failed = sum(c["failed"] for c in cases)
    skipped = sum(c["skipped"] for c in cases)
    (run / "verify.json").write_text(
        json.dumps(
            {
                "suite": "B03_organic",
                "cases_ok": sum(1 for c in cases if c["ok"]),
                "cases_total": len(cases),
                "vectors": {"passed": passed, "failed": failed, "skipped": skipped},
                "cases": cases,
            }
        ),
        "utf-8",
    )


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(suite_report, "measure_unsafe", lambda c, **kw: _u(42, 300))
    monkeypatch.setattr(
        suite_report,
        "measure_idiomaticity",
        lambda c, **kw: IdiomReport(
            by_group={"style": {"needless_return": 7}}, loc=300
        ),
    )


def test_full_report(tmp_path: Path, stub, capsys) -> None:
    _write_case(tmp_path, "array_list")
    _verify_json(
        tmp_path,
        [
            {
                "case": "Public-Tests/B03_organic/array_list",
                "passed": 10,
                "failed": 0,
                "skipped": 0,
                "fs_skipped": 0,
                "ok": True,
                "build_ok": True,
            }
        ],
    )
    rc = suite_report.main([str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "array_list" in out
    assert "10/10" in out  # vectors
    assert "42" in out  # unsafe score
    assert "clippy 7" in out  # idiomaticity total in the totals line


def test_no_idiomaticity_skips_clippy(tmp_path: Path, monkeypatch, capsys) -> None:
    _write_case(tmp_path, "array_list")
    _verify_json(
        tmp_path,
        [
            {
                "case": "Public-Tests/B03_organic/array_list",
                "passed": 5,
                "failed": 0,
                "skipped": 2,
                "fs_skipped": 2,
                "ok": True,
                "build_ok": True,
            }
        ],
    )
    monkeypatch.setattr(suite_report, "measure_unsafe", lambda c, **kw: _u(3, 100))

    def boom(*a, **k):
        raise AssertionError("idiomaticity must be skipped")

    monkeypatch.setattr(suite_report, "measure_idiomaticity", boom)
    rc = suite_report.main([str(tmp_path), "--no-idiomaticity"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "clippy (skipped)" in out
    assert "2 fs-skip" in out  # skip breakdown surfaced


def test_per_stage_report(tmp_path: Path, monkeypatch, capsys) -> None:
    # _write_case lays down two stages: c2rust then crat.
    _write_case(tmp_path, "array_list")
    _verify_json(
        tmp_path,
        [
            {
                "case": "Public-Tests/B03_organic/array_list",
                "passed": 10,
                "failed": 0,
                "skipped": 0,
                "fs_skipped": 0,
                "ok": True,
                "build_ok": True,
            }
        ],
    )
    # --per-stage drives metrics._measure, so patch there; scores halve per stage.
    from proctor.testing import metrics

    seq = {"n": 0}

    def fake_unsafe(crate, **kw):
        seq["n"] += 1
        return _u(100 // seq["n"], 300)

    monkeypatch.setattr(metrics, "measure_unsafe", fake_unsafe)
    monkeypatch.setattr(
        metrics,
        "measure_idiomaticity",
        lambda c, **kw: IdiomReport(by_group={"style": {"x": 20 // seq["n"]}}, loc=300),
    )

    rc = suite_report.main([str(tmp_path), "--per-stage"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "== array_list ==" in out
    assert "c2rust" in out and "crat" in out  # every stage shown
    assert "-50%" in out  # unsafe 100 -> 50 vs first stage
    assert "suite totals" in out


def test_per_stage_shows_recovery_quality(tmp_path: Path, monkeypatch, capsys) -> None:
    # a case that ran abstraction_recovery (crat -> abstraction_recovery).
    _write_case(
        tmp_path,
        "binary_heap_lib",
        stages=(("00", "c2rust"), ("01", "crat"), ("02", "abstraction_recovery")),
    )
    _verify_json(
        tmp_path,
        [
            {
                "case": "Public-Tests/B03_organic/binary_heap_lib",
                "passed": 12,
                "failed": 0,
                "skipped": 0,
                "fs_skipped": 0,
                "ok": True,
                "build_ok": True,
            }
        ],
    )
    from proctor.testing import metrics

    monkeypatch.setattr(metrics, "measure_unsafe", lambda c, **kw: _u(10, 300))
    monkeypatch.setattr(
        metrics,
        "measure_idiomaticity",
        lambda c, **kw: IdiomReport(by_group={}, loc=300),
    )

    seen: dict[str, Path] = {}

    def fake_recovery(src: Path, dst: Path, **kw) -> RecoveryQualityReport:
        seen["src"], seen["dst"] = src, dst
        return RecoveryQualityReport(
            src=str(src),
            dst=str(dst),
            touched_files=["src/binary_heap.rs"],
            wandering_files=[],
            abi_changed=[],
            abi_removed=[],
            abi_added=[],
            files=[
                FileQuality(
                    file="src/binary_heap.rs",
                    residual_unsafe=24,
                    net_unsafe=-42,
                    churn_added=79,
                    churn_removed=97,
                    raw_ptr_fields=1,
                    into_from_raw=0,
                    raw_derefs=6,
                    malloc_free=2,
                    non_boundary_unsafe=2,
                    adopts_target=True,
                    parse_ok=True,
                )
            ],
        )

    monkeypatch.setattr(suite_report, "measure_recovery_quality", fake_recovery)

    rc = suite_report.main([str(tmp_path), "--per-stage", "--no-idiomaticity"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "recovery (crat -> recovered):" in out
    assert "residual unsafe 24" in out
    assert "GATE: repair" in out  # facade signal (raw_ptr_fields + malloc_free) > 0
    # measured crat -> abstraction_recovery (not c2rust)
    assert seen["src"].parent.parent.name == "01-crat"
    assert seen["dst"].parent.parent.name == "02-abstraction_recovery"


def test_per_stage_no_recovery_line_without_absrec(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # a plain c2rust -> crat case must NOT call recovery_quality or print the line.
    _write_case(tmp_path, "array_list")
    _verify_json(
        tmp_path,
        [
            {
                "case": "Public-Tests/B03_organic/array_list",
                "passed": 3,
                "failed": 0,
                "skipped": 0,
                "fs_skipped": 0,
                "ok": True,
                "build_ok": True,
            }
        ],
    )
    from proctor.testing import metrics

    monkeypatch.setattr(metrics, "measure_unsafe", lambda c, **kw: _u(5, 100))

    def boom(*a, **k):
        raise AssertionError(
            "recovery_quality must not run without abstraction_recovery"
        )

    monkeypatch.setattr(suite_report, "measure_recovery_quality", boom)
    rc = suite_report.main([str(tmp_path), "--per-stage", "--no-idiomaticity"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "recovery (crat -> recovered):" not in out


def test_missing_verify_json(tmp_path: Path, capsys) -> None:
    rc = suite_report.main([str(tmp_path)])
    assert rc == 1
    assert "no verify.json" in capsys.readouterr().err
