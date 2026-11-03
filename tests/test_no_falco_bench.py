"""bench_no_falco.sh's host-side driver: staging translations into the newer
corpus and rolling up the newer harness's per-case JUnit. The real nix-run
is exercised by bench_no_falco.sh, not here."""

import argparse
import json
from pathlib import Path

from proctor.testing import no_falco_bench as nfb


def _bench_case(bench_dir: Path, case: str, *, stage: str = "01-crat") -> None:
    rust = bench_dir / case / "stages" / stage / "out" / "rust"
    rust.mkdir(parents=True)
    (rust / "Cargo.toml").write_text("[package]\nname='x'\n", "utf-8")


def _corpus_case(corpus: Path, suite: str, case: str) -> None:
    (corpus / "Public-Tests" / suite / case / "test_vectors").mkdir(parents=True)


def test_stage_translations_stages_only_real_cases(tmp_path: Path) -> None:
    bench = tmp_path / "bench"
    corpus = tmp_path / "corpus"
    suite = "B01_synthetic"
    # two translated cases + one bench dir that isn't a corpus case
    _bench_case(bench, "001_helloworld")
    _bench_case(bench, "002_stdin_echo")
    (bench / "_corpus_ws").mkdir(parents=True)  # not a case
    _corpus_case(corpus, suite, "001_helloworld")
    _corpus_case(corpus, suite, "002_stdin_echo")

    staged = nfb.stage_translations(bench, corpus, suite)

    assert sorted(staged) == ["001_helloworld", "002_stdin_echo"]
    for case in staged:
        slot = corpus / "Public-Tests" / suite / case / "translated_rust"
        assert (slot / "Cargo.toml").is_file()


def test_stage_translations_skips_failed_translation(tmp_path: Path) -> None:
    bench = tmp_path / "bench"
    corpus = tmp_path / "corpus"
    suite = "B01_synthetic"
    (bench / "003_broken" / "stages").mkdir(parents=True)  # no out/rust
    _corpus_case(corpus, suite, "003_broken")
    assert nfb.stage_translations(bench, corpus, suite) == []


def test_stage_translations_match_filters(tmp_path: Path) -> None:
    bench = tmp_path / "bench"
    corpus = tmp_path / "corpus"
    suite = "B01_synthetic"
    _bench_case(bench, "001_helloworld")
    _bench_case(bench, "002_stdin_echo")
    _corpus_case(corpus, suite, "001_helloworld")
    _corpus_case(corpus, suite, "002_stdin_echo")
    assert nfb.stage_translations(bench, corpus, suite, match="stdin") == [
        "002_stdin_echo"
    ]


def test_rollup_junit(tmp_path: Path) -> None:
    junit = tmp_path / "j.xml"
    junit.write_text(
        "<testsuites>"
        "<testsuite name='Public-Tests/B/x'>"
        "<testcase name='config'/><testcase name='build'/>"
        "<testcase name='build-runners'/>"
        "<testcase name='v1'/><testcase name='v2'/>"
        "<testcase name='v3'><skipped message='fs'/></testcase>"
        "</testsuite>"
        "<testsuite name='Public-Tests/B/y'>"
        "<testcase name='build'/>"
        "<testcase name='v1'><failure message='boom'/></testcase>"
        "</testsuite>"
        "</testsuites>",
        encoding="utf-8",
    )
    rollups = {r.case: r for r in nfb.rollup_junit(junit)}

    x = rollups["Public-Tests/B/x"]
    assert (x.passed, x.skipped, x.failed, x.build_ok, x.ok) == (2, 1, 0, True, True)
    y = rollups["Public-Tests/B/y"]
    assert (y.passed, y.skipped, y.failed, y.ok) == (0, 0, 1, False)


def test_rollup_junit_build_failure_marks_not_ok(tmp_path: Path) -> None:
    junit = tmp_path / "j.xml"
    junit.write_text(
        "<testsuites><testsuite name='c'>"
        "<testcase name='build'><error message='no compile'/></testcase>"
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    (r,) = nfb.rollup_junit(junit)
    assert not r.build_ok and not r.ok


def test_rollup_junit_captures_skipped_names(tmp_path: Path) -> None:
    junit = tmp_path / "j.xml"
    junit.write_text(
        "<testsuites><testsuite name='c'>"
        "<testcase name='build'/>"
        "<testcase name='test_write'><skipped/></testcase>"
        "<testcase name='v1'/>"
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    (r,) = nfb.rollup_junit(junit)
    assert r.skipped_names == ["test_write"]
    assert r.skipped == 1 and r.passed == 1


def test_count_fs_skips(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    tv = corpus / "Public-Tests" / "Examples" / "fs_example" / "test_vectors"
    (tv / "test_write").mkdir(parents=True)
    (tv / "test_write" / "file_changes.tar.gz").write_bytes(b"x")  # file-change
    (tv / "test_read").mkdir()  # dir vector, no tarfile
    n = nfb.count_fs_skips(
        corpus,
        "Public-Tests/Examples/fs_example",
        ["test_write", "test_read", "test1.json"],  # json = plain file vector
    )
    assert n == 1


def _roll(
    case: str, passed: int, failed: int, build_ok: bool = True
) -> "nfb.CaseRollup":
    return nfb.CaseRollup(case=case, passed=passed, failed=failed, build_ok=build_ok)


def test_gate_keeps_absrec_when_not_regressing() -> None:
    # abs_rec passes at least as many and fails no more than crat -> keep abs_rec
    a = _roll("c", passed=10, failed=0)
    c = _roll("c", passed=10, failed=0)
    assert nfb.gate_decision(a, c) == ("abstraction_recovery", a)


def test_gate_falls_back_on_fewer_passes() -> None:
    a = _roll("c", passed=1, failed=9)
    c = _roll("c", passed=10, failed=0)
    stage, accepted = nfb.gate_decision(a, c)
    assert stage == "crat" and accepted is c


def test_gate_falls_back_on_build_fail() -> None:
    a = _roll("c", passed=0, failed=0, build_ok=False)  # abs_rec didn't build
    c = _roll("c", passed=15, failed=0)
    stage, accepted = nfb.gate_decision(a, c)
    assert stage == "crat" and accepted is c


def test_gate_keeps_absrec_when_no_crat_baseline() -> None:
    a = _roll("c", passed=3, failed=4)
    assert nfb.gate_decision(a, None) == ("abstraction_recovery", a)


# --- prefix gate ------------------------------------------------------------------


def test_prefix_length_stops_at_the_first_regression() -> None:
    crat = _roll("c", passed=10, failed=0)
    good, bad = _roll("c", 10, 0), _roll("c", 9, 1)
    assert nfb.prefix_length([good, good, bad, good], crat) == 2
    assert nfb.prefix_length([bad, good], crat) == 0
    assert nfb.prefix_length([good, good], crat) == 2
    assert nfb.regresses(_roll("c", 10, 0, build_ok=False), crat)


def _absrec_case(bench: Path, case: str, n_checkpoints: int) -> Path:
    """A bench case whose abstraction_recovery stage lists ``n_checkpoints``."""
    _bench_case(bench, case)
    _bench_case(bench, case, stage="02-abstraction_recovery")
    stage = bench / case / "stages" / "02-abstraction_recovery"
    cps = []
    for i in range(1, n_checkpoints + 1):
        rel = f"checkpoints/{i:02d}-vec-f{i}"
        cp = stage / "out" / "artifacts" / rel
        cp.mkdir(parents=True)
        (cp / "Cargo.toml").write_text("[package]\nname='x'\n", "utf-8")
        cps.append({"index": i, "kind": "vec", "function": f"f{i}", "path": rel})
    meta = {"metadata": {"checkpoints": cps}}
    (stage / "stage_output.json").write_text(json.dumps(meta), "utf-8")
    return stage / "out" / "rust"


def test_read_checkpoints_in_order(tmp_path: Path) -> None:
    rust = _absrec_case(tmp_path, "heap", 3)
    cps = nfb.read_checkpoints(rust)
    assert [c.index for c in cps] == [1, 2, 3]
    assert cps[0].rust.name == "01-vec-f1"
    assert (
        nfb.read_checkpoints(tmp_path / "heap" / "stages" / "01-crat" / "out" / "rust")
        == []
    )


def test_prefix_gate_keeps_the_last_checkpoint_before_a_regression(
    tmp_path: Path, monkeypatch
) -> None:
    bench, corpus, suite = tmp_path / "bench", tmp_path / "corpus", "B03"
    _absrec_case(bench, "heap", 3)
    _corpus_case(corpus, suite, "heap")
    case = f"Public-Tests/{suite}/heap"
    crat = _roll(case, passed=10, failed=0)
    final = _roll(case, passed=7, failed=3)  # checkpoint 3 = the stage output
    by_round = {"cp1": _roll(case, 10, 0), "cp2": _roll(case, 6, 4)}
    monkeypatch.setattr(nfb, "stage_cases", lambda c, s, items: [i[0] for i in items])
    monkeypatch.setattr(
        nfb,
        "_verify",
        lambda corpus, suite, cases, junit, log: [by_round[junit.stem.split(".")[-1]]],
    )
    args = argparse.Namespace(
        bench_dir=bench, suite=suite, junit_out=tmp_path / "verify.xml", log_file=None
    )
    rows = {case: nfb.GatedRow(crat, "crat", final)}  # the stage gate fell back
    nfb._prefix_gate(args, corpus, rows, {"heap": crat})
    row = rows[case]
    assert row.stage == "abstraction_recovery" and row.checkpoint == 1
    assert row.accepted is by_round["cp1"]
    assert [s["regressed"] for s in row.steps] == [False, True, True]


def test_prefix_gate_leaves_a_non_regressing_recovery_alone(
    tmp_path: Path, monkeypatch
) -> None:
    bench, corpus, suite = tmp_path / "bench", tmp_path / "corpus", "B03"
    _absrec_case(bench, "heap", 3)
    case = f"Public-Tests/{suite}/heap"
    final = _roll(case, passed=8, failed=2)
    crat = _roll(case, passed=8, failed=2)  # same failures as crat: no regression
    calls: list[str] = []
    monkeypatch.setattr(nfb, "_verify", lambda *a: calls.append("verify"))
    args = argparse.Namespace(
        bench_dir=bench, suite=suite, junit_out=tmp_path / "verify.xml", log_file=None
    )
    rows = {case: nfb.GatedRow(final, "abstraction_recovery", final)}
    nfb._prefix_gate(args, corpus, rows, {"heap": crat})
    assert calls == [] and rows[case].checkpoint is None
