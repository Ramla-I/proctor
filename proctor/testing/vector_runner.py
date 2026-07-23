#!/usr/bin/env python3
"""TRACTOR test-vector runner — the executable heart of a generated
test package. Stdlib-only and self-contained: `proctor make-tests`
copies this file verbatim into the package next to `run_test.sh`.

Comparison semantics mirror the corpus harness
(``Test-Corpus/tools/cando2/src/runners/runner.rs``), verified against
its source:

- absent ``stdout``/``stderr`` in a vector means "must be exactly
  empty" (the harness substitutes pattern \"\");
- non-regex patterns compare by exact string equality, trailing
  newlines included;
- ``is_regex: true`` uses an unanchored search over the full captured
  stream;
- expected ``rc`` defaults to 0; any mismatch (including signal
  deaths) fails;
- ``has_ub`` vectors are skipped and do not fail the package;
- directory vectors carrying ``setup`` or ``file_changes.tar.gz``
  need the harness's container/Falco infrastructure — skipped here,
  reported loudly;
- ``lib_state_in``/``lib_state_out`` vectors run through the case's
  real cando harness crate, bundled into the package (test_data/runner)
  and built once via cargo (cached under PROCTOR_CACHE_DIR).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

RUNNER_VERSION = 2
DEFAULT_TIMEOUT_S = 120  # matches the harness's --timeout default
HARNESS_BUILD_TIMEOUT_S = 900

PASS, SKIP, FAIL = "PASS", "SKIP", "FAIL"

# cando's TestOutcome variants -> our verdicts (Skip/NoCompare are
# non-fail in the harness; everything else fails)
_CANDO_RESULTS = {
    "Pass": PASS,
    "Skip": SKIP,
    "NoCompare": SKIP,
    "VectorComparisonFailed": FAIL,
    "Panic": FAIL,
    "SegmentationFault": FAIL,
    "Timeout": FAIL,
    "UnknownFailure": FAIL,
}


def load_vector(path: Path) -> tuple[dict[str, Any], str | None]:
    """Returns (vector json, unsupported-reason or None). Mirrors the
    harness's file-or-directory vector layout."""
    if path.is_dir():
        vector_file = path / "cando_vector.json"
        if not vector_file.is_file():
            return {}, "directory vector without cando_vector.json"
        data = json.loads(vector_file.read_text(encoding="utf-8"))
        if (path / "setup").exists() or (path / "file_changes.tar.gz").exists():
            return data, "setup/file-changes vectors need the harness containers"
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}, "vector is not a JSON object"
    if data.get("lib_state_in") is not None or data.get("lib_state_out") is not None:
        return data, "library-state vectors need the cando harness"
    return data, None


def compare_stream(name: str, expected: object, got: str) -> str | None:
    """None = match; otherwise a human-readable diff line.
    Absent field -> exact empty expected (harness behavior)."""
    if expected is None:
        expected = {"pattern": ""}
    if not isinstance(expected, dict):
        return f"{name}: malformed expectation {expected!r}"
    pattern = str(expected.get("pattern", ""))
    if expected.get("is_regex"):
        if re.search(pattern, got):
            return None
        return f"{name}: regex {pattern!r} did not match {got!r}"
    if pattern == got:
        return None
    return f"{name}: expected {pattern!r}, got {got!r}"


def run_vector(artifact: Path, vector: dict[str, Any]) -> tuple[str, str]:
    """Execute one binary vector; returns (PASS|FAIL, detail)."""
    if vector.get("has_ub"):
        return SKIP, "has_ub"

    argv = vector.get("argv") or []
    stdin = vector.get("stdin")
    try:
        proc = subprocess.run(
            [str(artifact), *[str(a) for a in argv]],
            input=stdin.encode() if isinstance(stdin, str) else None,
            capture_output=True,
            timeout=DEFAULT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return FAIL, f"timeout after {DEFAULT_TIMEOUT_S}s"
    except OSError as exc:
        return FAIL, f"could not execute artifact: {exc}"

    problems = []
    expected_rc = vector.get("rc")
    expected_rc = 0 if expected_rc is None else int(expected_rc)
    if proc.returncode != expected_rc:
        problems.append(f"rc: expected {expected_rc}, got {proc.returncode}")
    out_diff = compare_stream(
        "stdout", vector.get("stdout"), proc.stdout.decode(errors="replace")
    )
    if out_diff:
        problems.append(out_diff)
    err_diff = compare_stream(
        "stderr", vector.get("stderr"), proc.stderr.decode(errors="replace")
    )
    if err_diff:
        problems.append(err_diff)

    if problems:
        return FAIL, "; ".join(problems)
    return PASS, ""


def _cache_dir() -> Path:
    return Path(os.environ.get("PROCTOR_CACHE_DIR", Path.home() / ".cache" / "proctor"))


def ensure_harness_built(runner_src: Path) -> Path:
    """Build the case's cando harness crate once, cached by source hash
    under PROCTOR_CACHE_DIR/cando-runners (test_data is read-only, so
    the build never happens in place)."""
    digest = hashlib.sha256()
    for file in sorted(runner_src.rglob("*")):
        if file.is_file():
            digest.update(str(file.relative_to(runner_src)).encode())
            digest.update(file.read_bytes())
    build_dir = _cache_dir() / "cando-runners" / digest.hexdigest()[:20]

    import tomllib

    cargo = tomllib.loads((runner_src / "Cargo.toml").read_text(encoding="utf-8"))
    name = str(cargo["package"]["name"]).replace("-", "_")
    binary = build_dir / "crate" / "target" / "release" / name
    if binary.is_file():
        return binary

    crate_dir = build_dir / "crate"
    if crate_dir.exists():
        shutil.rmtree(crate_dir)
    shutil.copytree(runner_src, crate_dir)
    build = subprocess.run(
        ["cargo", "build", "--release"],
        cwd=crate_dir,
        capture_output=True,
        text=True,
        timeout=HARNESS_BUILD_TIMEOUT_S,
    )
    if build.returncode != 0 or not binary.is_file():
        print(build.stderr[-3000:], file=sys.stderr)
        raise RuntimeError(f"cando harness build failed in {crate_dir}")
    return binary


def expected_lib_filename(runner_src: Path, artifact: Path) -> str:
    """The harness loads ``lib<name>.so`` for the ``library: "<name>"``
    declared in its harness! macro — which may differ from the
    pipeline artifact's filename. Parse the declaration so the staged
    symlink carries the name the harness will look for."""
    main_rs = runner_src / "src" / "main.rs"
    if main_rs.is_file():
        match = re.search(r'library:\s*"([^"]+)"', main_rs.read_text(encoding="utf-8"))
        if match:
            return f"lib{match.group(1)}.so"
    return artifact.name


def run_lib_vector(
    harness: Path, artifact: Path, entry: Path, lib_filename: str
) -> tuple[str, str]:
    """Run one library vector through the case's cando harness.

    Stages a synthetic test root the way the corpus harness containers
    do: the artifact symlinked at translated_rust/target/release/ (under
    the harness's expected name) and the vector under test_vectors/,
    then invokes ``<harness> --test-root-dir <root> --rust -v <name> lib``.
    """
    with tempfile.TemporaryDirectory(prefix="cando-root-") as tmp:
        root = Path(tmp)
        lib_dir = root / "translated_rust" / "target" / "release"
        lib_dir.mkdir(parents=True)
        (lib_dir / lib_filename).symlink_to(artifact.resolve())
        vectors_dir = root / "test_vectors"
        vectors_dir.mkdir()
        if entry.is_dir():
            shutil.copytree(entry, vectors_dir / entry.name)
        else:
            shutil.copy2(entry, vectors_dir / entry.name)
        out_file = root / "cando_out.json"

        try:
            proc = subprocess.run(
                [
                    str(harness),
                    "--log-level",
                    "none",
                    "--test-root-dir",
                    str(root),
                    "--output",
                    str(out_file),
                    "-v",
                    entry.name,
                    "--rust",
                    "lib",
                ],
                capture_output=True,
                text=True,
                timeout=DEFAULT_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return FAIL, f"timeout after {DEFAULT_TIMEOUT_S}s"

        # CandoReturnCode: 2 ArtifactNotFound, 3 SymbolNotFound,
        # 4 Usage, 5 CandoFailure — all fail without an outcome file
        if proc.returncode in (2, 3):
            reason = "artifact" if proc.returncode == 2 else "symbol"
            return FAIL, f"cando: {reason} not found: {proc.stderr[-300:]}"
        if proc.returncode not in (0, 1) or not out_file.is_file():
            return FAIL, (
                f"cando internal error (rc {proc.returncode}): "
                f"{(proc.stderr or proc.stdout)[-300:]}"
            )

        outcome = json.loads(out_file.read_text(encoding="utf-8"))
        if not isinstance(outcome, dict) or len(outcome) != 1:
            return FAIL, f"unexpected cando output: {outcome!r}"
        result = next(iter(outcome.values()))
        result_type = str(result.get("result"))
        verdict = _CANDO_RESULTS.get(result_type)
        if verdict is None:
            return FAIL, f"unknown cando result {result_type!r}"
        detail = result.get("diff") or ""
        if verdict == FAIL and not detail:
            detail = result_type
        return verdict, detail.replace("\n", " | ")[:500]


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <test_data> <artifact>", file=sys.stderr)
        return 2
    test_data = Path(sys.argv[1])
    artifact = Path(sys.argv[2])
    vectors_dir = test_data / "vectors"
    if not vectors_dir.is_dir():
        print(f"no vectors directory at {vectors_dir}", file=sys.stderr)
        return 2

    entries = sorted(
        (p for p in vectors_dir.iterdir() if p.is_dir() or p.suffix == ".json"),
        key=lambda p: p.name,
    )
    if not entries:
        print(f"no vectors found under {vectors_dir}", file=sys.stderr)
        return 2

    runner_src = test_data / "runner"
    harness: Path | None = None
    lib_filename = artifact.name
    if runner_src.is_dir():  # library package: vectors go through cando
        try:
            harness = ensure_harness_built(runner_src)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            print(f"cannot build cando harness: {exc}", file=sys.stderr)
            return 2
        lib_filename = expected_lib_filename(runner_src, artifact)

    failed = 0
    for entry in entries:
        vector, unsupported = load_vector(entry)
        if unsupported and "library-state" not in unsupported:
            print(f"SKIP  {entry.name}  ({unsupported})")
            continue
        if harness is not None:
            verdict, detail = run_lib_vector(harness, artifact, entry, lib_filename)
        elif unsupported:  # library vector but no bundled harness
            print(f"SKIP  {entry.name}  ({unsupported})")
            continue
        else:
            verdict, detail = run_vector(artifact, vector)
        suffix = f"  ({detail})" if detail else ""
        print(f"{verdict}  {entry.name}{suffix}")
        if verdict == FAIL:
            failed += 1

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
