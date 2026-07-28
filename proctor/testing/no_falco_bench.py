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
from dataclasses import dataclass
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
    timeout_s: int = 3600,
) -> subprocess.CompletedProcess[str]:
    """Verify the given staged ``cases`` with ``tools/test_runner
    --no-falco`` in a single host-level ``nix run`` (one ``--subset`` per
    case, so stale slots in other cases are ignored)."""
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
    print(">> " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, env=env, text=True, timeout=timeout_s)


@dataclass
class CaseRollup:
    case: str
    passed: int
    skipped: int
    failed: int
    build_ok: bool

    @property
    def ok(self) -> bool:
        return self.build_ok and self.failed == 0


def rollup_junit(junit_path: Path) -> list[CaseRollup]:
    """Per-case pass/skip/fail from the newer harness's JUnit. Phase
    pseudo-tests (config/build/build-runners) drive ``build_ok``, not the
    vector counts."""
    root = ET.parse(junit_path).getroot()
    out: list[CaseRollup] = []
    for ts in root.iter("testsuite"):
        passed = skipped = failed = 0
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
                skipped += 1
            elif tag in ("failure", "error"):
                failed += 1
            else:
                passed += 1
        out.append(CaseRollup(ts.get("name", ""), passed, skipped, failed, build_ok))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Verify a translated suite with tools/test_runner --no-falco"
    )
    ap.add_argument("--bench-dir", type=Path, required=True, help="bench output dir")
    ap.add_argument("--corpus", type=Path, required=True, help="newer corpus root")
    ap.add_argument("--suite", required=True, help="suite name, e.g. B03_organic")
    ap.add_argument("--match", default=None, help="regex to select cases")
    ap.add_argument("--junit-out", type=Path, required=True)
    args = ap.parse_args(argv)

    staged = stage_translations(args.bench_dir, args.corpus, args.suite, args.match)
    if not staged:
        print("no translated cases to verify (translation failed for all?)")
        return 1
    print(f"staged {len(staged)} translation(s): {', '.join(staged)}")

    proc = run_suite_no_falco(args.corpus, args.suite, staged, args.junit_out)
    if not args.junit_out.is_file():
        print(f"newer harness produced no JUnit (exit {proc.returncode})")
        return 2

    rollups = rollup_junit(args.junit_out)
    print("\nper-case vectors (newer harness, --no-falco):")
    tot_p = tot_s = tot_f = n_ok = 0
    for r in sorted(rollups, key=lambda r: r.case):
        flag = "ok " if r.ok else "FAIL"
        build = "" if r.build_ok else " build-failed"
        print(
            f"  [{flag}] {r.case}: pass={r.passed} skip={r.skipped} fail={r.failed}{build}"
        )
        tot_p += r.passed
        tot_s += r.skipped
        tot_f += r.failed
        n_ok += 1 if r.ok else 0
    print(
        f"\n{n_ok}/{len(rollups)} cases ok  |  "
        f"vectors: {tot_p} pass, {tot_s} skip, {tot_f} fail"
    )

    summary = {
        "suite": args.suite,
        "staged": staged,
        "cases_ok": n_ok,
        "cases_total": len(rollups),
        "vectors": {"passed": tot_p, "skipped": tot_s, "failed": tot_f},
        "cases": [
            {
                "case": r.case,
                "ok": r.ok,
                "passed": r.passed,
                "skipped": r.skipped,
                "failed": r.failed,
                "build_ok": r.build_ok,
            }
            for r in rollups
        ],
    }
    # Alongside the JUnit (a host-writable dir): the bench dir itself may be
    # owned by the container that ran the translation half.
    out_json = args.junit_out.resolve().parent / "bench_no_falco.json"
    out_json.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out_json}")
    return 0 if tot_f == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
