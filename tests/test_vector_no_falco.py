"""Newer-harness engine: fold phase entries, stage the slot, and build the
`tools/test_runner --no-falco` invocation (subprocess stubbed). The real
host-level run (nix + docker) is exercised by ./no_falco_verify.sh, not here."""

from pathlib import Path

import pytest

from proctor.testing import vector_harness as vh
from proctor.testing.vector_harness import (
    VectorReport,
    VectorResult,
    run_vectors_no_falco,
)


# --- phase folding --------------------------------------------------------


def _report(*results: VectorResult) -> VectorReport:
    return VectorReport(case="c", results=results)


def test_fold_phase_entries_counts_only_vectors() -> None:
    folded = vh._fold_phase_entries(
        _report(
            VectorResult("config", "pass"),
            VectorResult("build", "pass"),
            VectorResult("build-runners", "pass"),
            VectorResult("test1", "pass"),
            VectorResult("test2", "skip"),
        )
    )
    assert folded.build_ok
    assert folded.passed == 1
    assert folded.skipped == 1
    assert folded.total == 2  # phase pseudo-tests excluded
    assert {r.name for r in folded.results} == {"build", "test1", "test2"}


def test_fold_phase_entries_build_failure() -> None:
    folded = vh._fold_phase_entries(
        _report(
            VectorResult("config", "pass"),
            VectorResult("build", "fail"),
            VectorResult("test1", "pass"),
        )
    )
    assert not folded.build_ok
    assert not folded.ok


# --- run_vectors_no_falco (subprocess stubbed) ----------------------------


def _newer_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "Test-Corpus"
    (corpus / "tools" / "test_runner").mkdir(parents=True)
    (corpus / "tools" / "test_runner" / "flake.nix").write_text("{}", "utf-8")
    return corpus


def _translated(tmp_path: Path) -> Path:
    t = tmp_path / "translated"
    t.mkdir()
    (t / "Cargo.toml").write_text("[package]\nname='x'\n", "utf-8")
    return t


def test_run_vectors_no_falco_stages_and_parses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = _newer_corpus(tmp_path)
    case = corpus / "Public-Tests" / "B01" / "001_hello"
    (case / "test_vectors").mkdir(parents=True)
    translated = _translated(tmp_path)

    seen: dict[str, object] = {}

    def fake_run(cmd: list[str], **kw: object) -> object:
        seen["cmd"] = cmd
        junit = Path(cmd[cmd.index("--junit-xml") + 1])
        junit.write_text(
            "<testsuites><testsuite name='s'>"
            "<testcase name='config'/><testcase name='build'/>"
            "<testcase name='build-runners'/>"
            "<testcase name='v1'/>"
            "<testcase name='v2'><skipped message='fs'/></testcase>"
            "</testsuite></testsuites>",
            encoding="utf-8",
        )

        class P:
            returncode = 0
            stdout = ""
            stderr = ""

        return P()

    monkeypatch.setattr(vh.subprocess, "run", fake_run)
    report = run_vectors_no_falco(
        translated, case, corpus_root=corpus, junit_out=tmp_path / "j.xml"
    )

    # the translated_rust slot was populated in place
    assert (case / "translated_rust" / "Cargo.toml").is_file()
    # invocation is the newer orchestrator with the right flags
    cmd = seen["cmd"]
    assert "--rust" in cmd and "--no-falco" in cmd
    assert cmd[cmd.index("--subset") + 1] == "Public-Tests/B01/001_hello"
    # phase entries folded; only v1/v2 count, v2 skipped
    assert report.build_ok and report.ok
    assert report.passed == 1 and report.skipped == 1 and report.total == 2


def test_run_vectors_no_falco_rejects_case_outside_corpus(tmp_path: Path) -> None:
    corpus = _newer_corpus(tmp_path)
    outside = tmp_path / "elsewhere" / "case"
    (outside / "test_vectors").mkdir(parents=True)
    with pytest.raises(vh.VectorHarnessError, match="not under corpus root"):
        run_vectors_no_falco(
            _translated(tmp_path),
            outside,
            corpus_root=corpus,
            junit_out=tmp_path / "j.xml",
        )


def test_run_vectors_no_falco_requires_newer_test_runner(tmp_path: Path) -> None:
    corpus = tmp_path / "Test-Corpus"  # no tools/test_runner/flake.nix
    case = corpus / "case"
    (case / "test_vectors").mkdir(parents=True)
    with pytest.raises(vh.VectorHarnessError, match="tools/test_runner"):
        run_vectors_no_falco(
            _translated(tmp_path),
            case,
            corpus_root=corpus,
            junit_out=tmp_path / "j.xml",
        )
