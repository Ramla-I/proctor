"""Batch driver: discovery, parallel case runs, summary, failure
isolation — all via the fake stage."""

import json
from pathlib import Path

import pytest

from proctor.config.load import ConfigError
from proctor.config.model import PipelineConfig
from proctor.orchestrator.bench import (
    BenchSettings,
    discover_cases,
    run_bench,
)
from proctor.orchestrator.run import RunError, RunResult

FAKE = Path(__file__).parent / "fake_stages" / "fake"


def _corpus(tmp_path: Path, cases: dict[str, bool]) -> Path:
    """cases: name -> should this case carry the FAIL flag."""
    corpus = tmp_path / "corpus"
    for name, fail in cases.items():
        rust = corpus / name / "rust"
        rust.mkdir(parents=True)
        (rust / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
        if fail:
            (rust / "FAIL.txt").write_text("boom", encoding="utf-8")
    return corpus


def _config(**extra: object) -> PipelineConfig:
    return PipelineConfig.from_dict(
        {
            "run": {"provides": ["rust_project"]},
            "bench": {"layout": {"rust_project": "rust"}, "jobs": 2},
            "pipeline": {"order": ["a"]},
            "stages": {"a": {"uses": str(FAKE), "config": {"fail_if_flag": True}}},
            **extra,
        }
    )


def test_discovery_layout_and_nesting(tmp_path: Path) -> None:
    corpus = _corpus(
        tmp_path, {"suite1/case1": False, "suite1/case2": False, "solo": False}
    )
    (corpus / "not_a_case").mkdir()  # no rust/ subdir -> skipped
    cases = discover_cases(corpus, ("rust_project",), {"rust_project": "rust"})
    assert [c.name for c in cases] == ["solo", "suite1/case1", "suite1/case2"]
    assert cases[0].inputs["rust_project"] == corpus / "solo" / "rust"


def test_bench_runs_all_cases(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, {"s/c1": False, "s/c2": False})
    result = run_bench(_config(), tmp_path, corpus, name="t")
    assert result.ok
    assert len(result.cases) == 2
    summary = json.loads((result.bench_dir / "bench.json").read_text())
    assert summary["ok"] == 2 and summary["total"] == 2
    # each case is a complete, ordinary run dir
    case_dir = result.bench_dir / "s__c1"
    assert (case_dir / "run.json").is_file()
    assert (case_dir / "stages" / "00-a" / "stage_output.json").is_file()


def test_bench_isolates_failures(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path, {"good": False, "bad": True})
    result = run_bench(_config(), tmp_path, corpus, name="t")
    assert not result.ok
    by_name = {
        case.name: run for case, run in result.cases if isinstance(run, RunResult)
    }
    assert by_name["good"].ok
    assert not by_name["bad"].ok
    summary = json.loads((result.bench_dir / "bench.json").read_text())
    assert summary["ok"] == 1
    bad = next(c for c in summary["cases"] if c["name"] == "bad")
    assert bad["stages"][0]["error"] == "input project carries FAIL.txt"


def test_bench_empty_corpus_errors(tmp_path: Path) -> None:
    (tmp_path / "corpus").mkdir()
    with pytest.raises(RunError, match="no cases"):
        run_bench(_config(), tmp_path, tmp_path / "corpus", name="t")


def test_bench_settings_validation() -> None:
    with pytest.raises(ConfigError, match="not implemented"):
        BenchSettings.from_config({"bench": {"rule_set_policy": "chained"}})
    with pytest.raises(ConfigError, match="jobs"):
        BenchSettings.from_config({"bench": {"jobs": 0}})
    settings = BenchSettings.from_config({})
    assert settings.layout["c_project"] == "c"


def test_bench_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from proctor.cli import main

    corpus = _corpus(tmp_path, {"c1": False})
    config_file = tmp_path / "bench.toml"
    config_file.write_text(
        "[run]\nprovides = ['rust_project']\n"
        "[bench]\njobs = 1\n[bench.layout]\nrust_project = 'rust'\n"
        "[pipeline]\norder = ['a']\n"
        f"[stages.a]\nuses = '{FAKE}'\n",
        encoding="utf-8",
    )
    code = main(
        [
            "bench",
            "-c",
            str(config_file),
            "--root",
            str(tmp_path),
            "--corpus",
            str(corpus),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "1/1 cases ok" in out


def _vector_corpus(tmp_path: Path, cases: dict[str, bool]) -> Path:
    """Cases with rust projects and TRACTOR vectors but NO test package.
    value True -> vectors include a library-state vector (unsupported)."""
    corpus = tmp_path / "corpus"
    for name, lib_state in cases.items():
        rust = corpus / name / "rust"
        rust.mkdir(parents=True)
        (rust / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
        vectors = corpus / name / "test_vectors"
        vectors.mkdir()
        vector: dict[str, object] = {"rc": 0}
        if lib_state:
            vector["lib_state_in"] = {}
        (vectors / "t1.json").write_text(json.dumps(vector), encoding="utf-8")
    return corpus


def test_bench_synthesizes_test_packages(tmp_path: Path) -> None:
    corpus = _vector_corpus(tmp_path, {"case1": False})
    config = PipelineConfig.from_dict(
        {
            "run": {"provides": ["rust_project", "test_package"]},
            "bench": {"layout": {"rust_project": "rust"}, "jobs": 1},
            "pipeline": {"order": ["a"]},
            "stages": {"a": {"uses": str(FAKE)}},
        }
    )
    result = run_bench(config, tmp_path, corpus, name="synth")
    assert result.ok
    # the synthesized package was recorded into the case's run dir
    run_dir = result.bench_dir / "case1"
    assert (run_dir / "inputs" / "tests" / "run_test.sh").is_file()
    assert (run_dir / "inputs" / "tests" / "run_vectors.py").is_file()


def test_bench_synthesis_failure_isolated(tmp_path: Path) -> None:
    corpus = _vector_corpus(tmp_path, {"good": False, "libcase": True})
    config = PipelineConfig.from_dict(
        {
            "run": {"provides": ["rust_project", "test_package"]},
            "bench": {"layout": {"rust_project": "rust"}, "jobs": 2},
            "pipeline": {"order": ["a"]},
            "stages": {"a": {"uses": str(FAKE)}},
        }
    )
    result = run_bench(config, tmp_path, corpus, name="mixed")
    assert not result.ok
    outcomes = {case.name: run for case, run in result.cases}
    assert isinstance(outcomes["good"], RunResult) and outcomes["good"].ok
    assert isinstance(outcomes["libcase"], Exception)
    assert "library-state" in str(outcomes["libcase"])
