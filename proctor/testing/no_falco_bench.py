"""Host-level suite verification with the newer harness (Falco-free).

Companion to ``bench_no_falco.sh``. After ``bench`` has translated a suite
(c2rust -> crat, one run dir per case), this stages each case's final-stage
Rust into the newer TRACTOR corpus and verifies the selected cases with the
corpus's own orchestrator (``tools/test_runner --no-falco`` — newer cando2,
rustc 1.94.1, B03), all in one host-level ``nix run``.

This runs at HOST level (the orchestrator spawns a Docker container per
vector); it needs ``nix`` and ``docker`` on PATH — see ``bench_no_falco.sh``,
which sets up the environment and calls ``python -m
proctor.testing.no_falco_bench``.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from proctor.testing.vector_compare import stage_rust_outputs
from proctor.testing.vector_harness import VectorHarnessError

#: phase pseudo-tests the newer orchestrator emits alongside real vectors.
_PHASE_NAMES = frozenset({"config", "build", "build-runners"})


def stage_translations(
    bench_dir: Path, corpus: Path, suite: str, match: str | None = None
) -> list[str]:
    """Copy each case's final-stage Rust into the newer corpus case slot.

    ``bench_dir`` is a ``bench`` output dir (one run dir per case, named by
    the case leaf). Only cases that (a) exist in the corpus suite, and (b)
    produced a Rust project are staged. Returns the staged case names.
    """
    suite_root = corpus / "Public-Tests" / suite
    staged: list[str] = []
    for run_dir in sorted(p for p in bench_dir.iterdir() if p.is_dir()):
        case = run_dir.name
        if match and not re.search(match, case):
            continue
        case_dir = suite_root / case
        if not (case_dir / "test_vectors").is_dir():
            continue  # not a corpus case (e.g. the _corpus_ws copy, logs, ...)
        outputs = stage_rust_outputs(run_dir)
        if not outputs:
            continue  # translation produced no runnable Rust (failed/incomplete)
        _, rust = outputs[-1]
        slot = case_dir / "translated_rust"
        if slot.exists():
            shutil.rmtree(slot)
        shutil.copytree(rust, slot)
        staged.append(case)
    return staged


def run_suite_no_falco(
    corpus: Path,
    suite: str,
    cases: list[str],
    junit_out: Path,
    *,
    env: dict[str, str] | None = None,
    log_file: Path | None = None,
    timeout_s: int = 3600,
) -> subprocess.CompletedProcess[str]:
    """Verify the given staged ``cases`` with ``tools/test_runner
    --no-falco`` in a single host-level ``nix run`` (one ``--subset`` per
    case, so stale slots in other cases are ignored).

    ``log_file``: if given, the harness's (verbose) stdout/stderr is
    appended there instead of streamed to the console."""
    # Resolve to absolute paths: `nix run <relative>` is read as a flake
    # (GitHub) ref, not a local path; and the corpus/junit paths must be
    # unambiguous regardless of the harness's cwd.
    corpus = corpus.resolve()
    runner = corpus / "tools" / "test_runner"
    if not (runner / "flake.nix").is_file():
        raise VectorHarnessError(
            f"{runner} is not the newer tools/test_runner (no flake.nix); "
            "fetch it with ./fetch_corpus.sh --no-falco"
        )
    junit_out = junit_out.resolve()
    junit_out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "nix",
        "run",
        "--extra-experimental-features",
        "nix-command flakes",
        str(runner),
        "--",
        "--rust",
        "--no-falco",
        "--keep-going",
        "--root",
        str(corpus),
        "--junit-xml",
        str(junit_out),
    ]
    for case in cases:
        cmd += ["--subset", f"Public-Tests/{suite}/{case}"]
    if log_file is not None:
        with open(log_file, "a", encoding="utf-8") as lf:
            lf.write(">> " + " ".join(cmd) + "\n")
            lf.flush()
            return subprocess.run(
                cmd,
                env=env,
                text=True,
                stdout=lf,
                stderr=subprocess.STDOUT,
                timeout=timeout_s,
            )
    print(">> " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, env=env, text=True, timeout=timeout_s)


@dataclass
class CaseRollup:
    case: str  # "Public-Tests/<suite>/<case>"
    passed: int
    failed: int
    build_ok: bool
    skipped_names: list[str] = field(default_factory=list)

    @property
    def skipped(self) -> int:
        return len(self.skipped_names)

    @property
    def ok(self) -> bool:
        return self.build_ok and self.failed == 0


def rollup_junit(junit_path: Path) -> list[CaseRollup]:
    """Per-case pass/fail + skipped vector names from the newer harness's
    JUnit. Phase pseudo-tests (config/build/build-runners) drive
    ``build_ok``, not the vector counts."""
    root = ET.parse(junit_path).getroot()
    out: list[CaseRollup] = []
    for ts in root.iter("testsuite"):
        passed = failed = 0
        skipped_names: list[str] = []
        build_ok = True
        for tc in ts.findall("testcase"):
            name = tc.get("name", "")
            child = next(iter(tc), None)
            tag = child.tag.lower() if child is not None else ""
            if name in _PHASE_NAMES:
                if tag in ("failure", "error"):
                    build_ok = False
                continue
            if tag == "skipped":
                skipped_names.append(name)
            elif tag in ("failure", "error"):
                failed += 1
            else:
                passed += 1
        out.append(
            CaseRollup(ts.get("name", ""), passed, failed, build_ok, skipped_names)
        )
    return out


def count_fs_skips(corpus: Path, case_full: str, skipped_names: list[str]) -> int:
    """How many of a case's skipped vectors are file-change vectors — i.e.
    carry ``file_changes.tar.gz`` (the Falco-only ones that ``--no-falco``
    skips). ``case_full`` is ``Public-Tests/<suite>/<case>``."""
    test_vectors = corpus / case_full / "test_vectors"
    return sum(
        1
        for name in skipped_names
        if (test_vectors / name / "file_changes.tar.gz").is_file()
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Verify a translated suite with tools/test_runner --no-falco"
    )
    ap.add_argument("--bench-dir", type=Path, required=True, help="bench output dir")
    ap.add_argument("--corpus", type=Path, required=True, help="newer corpus root")
    ap.add_argument("--suite", required=True, help="suite name, e.g. B03_organic")
    ap.add_argument("--match", default=None, help="regex to select cases")
    ap.add_argument("--junit-out", type=Path, required=True)
    ap.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="append the harness's verbose output here instead of the console",
    )
    args = ap.parse_args(argv)

    corpus = args.corpus.resolve()
    staged = stage_translations(args.bench_dir, corpus, args.suite, args.match)
    if not staged:
        print("no translated cases to verify (translation failed for all?)")
        return 1
    print(f"verifying {len(staged)} translation(s) with tools/test_runner --no-falco")

    proc = run_suite_no_falco(
        corpus, args.suite, staged, args.junit_out, log_file=args.log_file
    )
    if not args.junit_out.is_file():
        print(f"newer harness produced no JUnit (exit {proc.returncode})")
        return 2

    rollups = rollup_junit(args.junit_out)
    tot_p = tot_s = tot_fs = tot_f = n_ok = 0
    case_rows: list[dict] = []
    for r in sorted(rollups, key=lambda r: r.case):
        leaf = r.case.split("/")[-1]
        fs = count_fs_skips(corpus, r.case, r.skipped_names)
        other = r.skipped - fs
        total = r.passed + r.skipped + r.failed
        notes = []
        if fs:
            notes.append(f"{fs} fs-skip")
        if other:
            notes.append(f"{other} skip")
        if not r.build_ok:
            notes.append("build-fail")
        tail = f"  ({', '.join(notes)})" if notes else ""
        print(
            f"  {'ok    ' if r.ok else 'FAILED'}  {leaf}  vectors {r.passed}/{total}{tail}"
        )
        tot_p += r.passed
        tot_s += r.skipped
        tot_fs += fs
        tot_f += r.failed
        n_ok += 1 if r.ok else 0
        case_rows.append(
            {
                "case": r.case,
                "ok": r.ok,
                "passed": r.passed,
                "skipped": r.skipped,
                "fs_skipped": fs,
                "failed": r.failed,
                "build_ok": r.build_ok,
            }
        )
    print(
        f"{n_ok}/{len(rollups)} cases ok  |  "
        f"vectors: {tot_p} pass, {tot_s} skip ({tot_fs} file-change), {tot_f} fail"
    )
    if tot_fs:
        print("(fs-skip = file-change vector, needs Falco; skipped under --no-falco)")

    summary = {
        "suite": args.suite,
        "staged": staged,
        "cases_ok": n_ok,
        "cases_total": len(rollups),
        "vectors": {
            "passed": tot_p,
            "skipped": tot_s,
            "fs_skipped": tot_fs,
            "failed": tot_f,
        },
        "cases": case_rows,
    }
    # Alongside the JUnit (a host-writable dir): the bench dir itself may be
    # owned by the container that ran the translation half.
    out_json = args.junit_out.resolve().parent / "bench_no_falco.json"
    out_json.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out_json}")
    return 0 if tot_f == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
