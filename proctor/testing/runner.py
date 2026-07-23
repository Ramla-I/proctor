"""Test-package runner (component spec §2.3, plan §5.5).

Builds the Rust project, locates the compiled artifact from
``proctor.toml`` (falling back to Cargo.toml inference for projects
that predate the manifest), then invokes::

    run_test.sh <test_data> <artifact>

Exit 0 means pass. Available as a library for stages (which own their
own validation loops) and to the orchestrator for
``[testing] after_each_stage`` gating.
"""

from __future__ import annotations

import subprocess
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from proctor.contracts.artifacts import TestPackage
from proctor.contracts.manifest import ManifestError, ProjectManifest


@dataclass(frozen=True)
class TestResult:
    __test__ = False  # not a pytest class, despite the name

    build_ok: bool
    passed: bool
    exit_code: int
    duration_s: float
    stdout: str
    stderr: str
    artifact: str | None = None

    @property
    def ok(self) -> bool:
        return self.build_ok and self.passed


class TestRunnerError(ValueError):
    """The project/test package is malformed (not a test failure)."""

    __test__ = False  # not a pytest class, despite the name


def _infer_target(project: Path) -> tuple[str, str]:
    """(kind, name) from proctor.toml, else Cargo.toml inference."""
    try:
        manifest = ProjectManifest.load(project)
        return manifest.target_kind, manifest.target_name
    except ManifestError:
        pass
    cargo_file = project / "Cargo.toml"
    if not cargo_file.is_file():
        raise TestRunnerError(f"{project} has no Cargo.toml")
    cargo = tomllib.loads(cargo_file.read_text(encoding="utf-8"))
    # [[bin]] wins: crat's bin pass emits both a lib and a bin table for
    # executable test cases; cdylib-only projects are libraries.
    bins = cargo.get("bin")
    if isinstance(bins, list) and bins and isinstance(bins[0], dict):
        name = bins[0].get("name")
        if isinstance(name, str):
            return "executable", name
    lib = cargo.get("lib")
    if isinstance(lib, dict) and "cdylib" in (lib.get("crate-type") or []):
        name = lib.get("name") or cargo.get("package", {}).get("name")
        if not isinstance(name, str):
            raise TestRunnerError(f"{cargo_file}: cannot determine lib name")
        return "library", name
    name = cargo.get("package", {}).get("name")
    if not isinstance(name, str):
        raise TestRunnerError(f"{cargo_file}: cannot determine target name")
    return "executable", name


def _artifact_path(project: Path, kind: str, name: str, profile: str) -> Path:
    base = project / "target" / profile
    if kind == "library":
        return base / f"lib{name}.so"
    return base / name


def run_tests(
    project: Path,
    test_package: Path,
    *,
    profile: str = "debug",
    build_timeout_s: int = 900,
    test_timeout_s: int = 600,
) -> TestResult:
    package = TestPackage(test_package)
    problems = package.problems()
    if problems:
        raise TestRunnerError("; ".join(problems))

    kind, name = _infer_target(project)

    started = time.monotonic()
    build_cmd = ["cargo", "build"]
    if profile == "release":
        build_cmd.append("--release")
    build = subprocess.run(
        build_cmd,
        cwd=project,
        capture_output=True,
        text=True,
        timeout=build_timeout_s,
    )
    if build.returncode != 0:
        return TestResult(
            build_ok=False,
            passed=False,
            exit_code=build.returncode,
            duration_s=time.monotonic() - started,
            stdout=build.stdout[-5000:],
            stderr=build.stderr[-5000:],
        )

    artifact = _artifact_path(project, kind, name, profile)
    if not artifact.is_file():
        return TestResult(
            build_ok=False,
            passed=False,
            exit_code=-1,
            duration_s=time.monotonic() - started,
            stdout="",
            stderr=f"build succeeded but artifact {artifact} not found",
        )

    test = subprocess.run(
        [str(package.run_test_sh), str(package.test_data), str(artifact)],
        capture_output=True,
        text=True,
        timeout=test_timeout_s,
    )
    return TestResult(
        build_ok=True,
        passed=test.returncode == 0,
        exit_code=test.returncode,
        duration_s=time.monotonic() - started,
        stdout=test.stdout[-5000:],
        stderr=test.stderr[-5000:],
        artifact=str(artifact),
    )
