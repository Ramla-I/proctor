"""Test-package runner: target inference and the build/test flow
(subprocess monkeypatched — no cargo in unit tests)."""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from proctor.contracts.manifest import ProjectManifest
from proctor.testing import runner
from proctor.testing.runner import TestRunnerError, _infer_target, run_tests


def _test_package(tmp_path: Path) -> Path:
    package = tmp_path / "tests"
    (package / "test_data").mkdir(parents=True)
    script = package / "run_test.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    return package


def _project(tmp_path: Path, cargo: str) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "Cargo.toml").write_text(cargo, encoding="utf-8")
    return project


def test_infer_from_proctor_toml(tmp_path: Path) -> None:
    project = _project(tmp_path, "[package]\nname = 'x'\n")
    ProjectManifest(target_kind="library", target_name="foo").dump(project)
    assert _infer_target(project) == ("library", "foo")


def test_infer_bin_wins_over_cdylib(tmp_path: Path) -> None:
    # crat's bin pass emits BOTH lib and bin tables for executables
    project = _project(
        tmp_path,
        '[package]\nname = "driver"\n'
        '[lib]\nname = "driver"\ncrate-type = ["cdylib"]\n'
        '[[bin]]\nname = "driver"\npath = "src_main_main.rs"\n',
    )
    assert _infer_target(project) == ("executable", "driver")


def test_infer_cdylib_only_is_library(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        '[package]\nname = "x"\n[lib]\nname = "x"\ncrate-type = ["cdylib"]\n',
    )
    assert _infer_target(project) == ("library", "x")


def test_invalid_package_raises(tmp_path: Path) -> None:
    project = _project(tmp_path, "[package]\nname = 'x'\n")
    with pytest.raises(TestRunnerError, match="does not exist"):
        run_tests(project, tmp_path / "nonexistent")


def _fake_subprocess(project: Path, *, build_rc: int = 0, test_rc: int = 0) -> Any:
    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "cargo":
            if build_rc == 0:
                artifact = project / "target" / "debug" / "driver"
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, build_rc, "", "build err")
        return subprocess.CompletedProcess(cmd, test_rc, "test out", "")

    return fake_run


def test_run_tests_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path, '[package]\nname = "p"\n[[bin]]\nname = "driver"\n')
    package = _test_package(tmp_path)
    monkeypatch.setattr(runner.subprocess, "run", _fake_subprocess(project))
    result = run_tests(project, package)
    assert result.ok
    assert result.artifact and result.artifact.endswith("driver")


def test_run_tests_build_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, '[package]\nname = "p"\n[[bin]]\nname = "driver"\n')
    package = _test_package(tmp_path)
    monkeypatch.setattr(
        runner.subprocess, "run", _fake_subprocess(project, build_rc=101)
    )
    result = run_tests(project, package)
    assert not result.ok
    assert not result.build_ok


def test_run_tests_test_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, '[package]\nname = "p"\n[[bin]]\nname = "driver"\n')
    package = _test_package(tmp_path)
    monkeypatch.setattr(runner.subprocess, "run", _fake_subprocess(project, test_rc=3))
    result = run_tests(project, package)
    assert result.build_ok and not result.passed
    assert result.exit_code == 3
