"""Vector→package conformance suite.

Each test pins one comparison rule extracted from the corpus harness
source (``Test-Corpus/tools/cando2/src/runners/runner.rs``), exercised
through the actual generated package (`run_test.sh` contract) against
synthetic artifacts.
"""

import json
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from proctor.cli import main
from proctor.testing.vectors import (
    VectorError,
    generate_test_package,
)


def _artifact(tmp_path: Path, script: str) -> Path:
    """A tiny sh 'compiled artifact' with controlled behavior."""
    artifact = tmp_path / "artifact"
    artifact.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
    artifact.chmod(artifact.stat().st_mode | stat.S_IEXEC)
    return artifact


def _package(tmp_path: Path, vectors: dict[str, Any]) -> Path:
    """Generate a package from named vector dicts."""
    vectors_dir = tmp_path / "test_vectors"
    vectors_dir.mkdir(exist_ok=True)
    for name, vector in vectors.items():
        (vectors_dir / f"{name}.json").write_text(json.dumps(vector), encoding="utf-8")
    out = tmp_path / "package"
    generate_test_package(vectors_dir, out)
    return out


def _run(package: Path, artifact: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [str(package / "run_test.sh"), str(package / "test_data"), str(artifact)],
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_exact_match_including_trailing_newline(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path, 'printf "Hello World!\\n"')
    package = _package(
        tmp_path, {"ok": {"argv": [], "stdout": {"pattern": "Hello World!\n"}}}
    )
    code, out = _run(package, artifact)
    assert code == 0, out

    # missing trailing newline in the pattern must FAIL (exact equality)
    package2 = tmp_path / "p2"
    (tmp_path / "tv2").mkdir()
    (tmp_path / "tv2" / "v.json").write_text(
        json.dumps({"stdout": {"pattern": "Hello World!"}}), encoding="utf-8"
    )
    generate_test_package(tmp_path / "tv2", package2)
    code, out = _run(package2, artifact)
    assert code == 1
    assert "stdout" in out


def test_absent_stream_means_exactly_empty(tmp_path: Path) -> None:
    noisy = _artifact(tmp_path, 'printf "noise"')
    quiet = _artifact(
        tmp_path / "q" if (tmp_path / "q").mkdir() is None else tmp_path, "true"
    )
    package = _package(tmp_path, {"v": {"argv": []}})  # no stdout/stderr fields
    code, out = _run(package, noisy)
    assert code == 1  # harness rule: absent field = expect empty
    code, _ = _run(package, quiet)
    assert code == 0


def test_regex_is_unanchored_search(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path, 'printf "prefix 42 suffix\\n"')
    package = _package(
        tmp_path,
        {"v": {"stdout": {"pattern": r"\d+", "is_regex": True}}},
    )
    code, out = _run(package, artifact)
    assert code == 0, out


def test_rc_default_zero_and_explicit(tmp_path: Path) -> None:
    failing = _artifact(tmp_path, "exit 3")
    package_default = _package(tmp_path, {"v": {}})
    code, out = _run(package_default, failing)
    assert code == 1 and "rc: expected 0, got 3" in out

    (tmp_path / "tv3").mkdir()
    (tmp_path / "tv3" / "v.json").write_text(json.dumps({"rc": 3}), encoding="utf-8")
    out_dir = tmp_path / "p3"
    generate_test_package(tmp_path / "tv3", out_dir)
    code, _ = _run(out_dir, failing)
    assert code == 0


def test_stdin_and_argv_forwarding(tmp_path: Path) -> None:
    echo = _artifact(tmp_path, 'read line; printf "%s|%s\\n" "$line" "$1"')
    package = _package(
        tmp_path,
        {
            "v": {
                "argv": ["arg1"],
                "stdin": "from-stdin\n",
                "stdout": {"pattern": "from-stdin|arg1\n"},
            }
        },
    )
    code, out = _run(package, echo)
    assert code == 0, out


def test_has_ub_vector_skips_without_failing(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path, "exit 99")  # would fail if executed
    package = _package(
        tmp_path,
        {"ub": {"has_ub": "signed overflow", "rc": 0}, "ok": {"rc": 99}},
    )
    code, out = _run(package, artifact)
    assert code == 0
    assert "SKIP  ub.json" in out


def test_setup_dir_vector_skips_loudly(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path, "true")
    vectors_dir = tmp_path / "test_vectors"
    (vectors_dir / "dirvec").mkdir(parents=True)
    (vectors_dir / "dirvec" / "cando_vector.json").write_text("{}", encoding="utf-8")
    (vectors_dir / "dirvec" / "setup").write_text("#!/bin/sh\n", encoding="utf-8")
    out = tmp_path / "package"
    package = generate_test_package(vectors_dir, out)
    assert package.unsupported == ("dirvec",)
    code, output = _run(out, artifact)
    assert code == 0
    assert "SKIP  dirvec" in output


def test_library_state_vectors_refused(tmp_path: Path) -> None:
    vectors_dir = tmp_path / "test_vectors"
    vectors_dir.mkdir()
    (vectors_dir / "lib.json").write_text(
        json.dumps({"lib_state_in": {"x": 1}, "lib_state_out": {"x": 2}}),
        encoding="utf-8",
    )
    with pytest.raises(VectorError, match="library-state"):
        generate_test_package(vectors_dir, tmp_path / "package")


def test_case_dir_and_nonempty_output_refused(tmp_path: Path) -> None:
    case = tmp_path / "case"
    (case / "test_vectors").mkdir(parents=True)
    (case / "test_vectors" / "v.json").write_text("{}", encoding="utf-8")
    out = tmp_path / "package"
    out.mkdir()
    (out / "junk").write_text("x", encoding="utf-8")
    with pytest.raises(VectorError, match="not empty"):
        generate_test_package(case, out)
    with pytest.raises(VectorError, match="no test vectors"):
        generate_test_package(tmp_path / "nowhere", tmp_path / "p2")


def test_make_tests_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = tmp_path / "case"
    (case / "test_vectors").mkdir(parents=True)
    (case / "test_vectors" / "v.json").write_text(
        json.dumps({"stdout": {"pattern": "x\n"}}), encoding="utf-8"
    )
    code = main(["make-tests", str(case), str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "1 vector(s)" in captured.out
    assert (tmp_path / "out" / "run_test.sh").exists()
    assert (tmp_path / "out" / "run_vectors.py").exists()
