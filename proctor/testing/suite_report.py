"""Combined per-case scorecard for a bench_no_falco run: vectors (from
verify.json) plus the `unsafe` + idiomaticity of each case's FINAL-stage
translation. Driven by ./bench_report.sh.

Unsafe is source-only (fast). Idiomaticity runs clippy, which builds each
crate — skip it with --no-idiomaticity for a quick vectors+unsafe view.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from proctor.testing.idiomaticity_eval import IdiomEvalError, measure_idiomaticity
from proctor.testing.unsafe_eval import UnsafeEvalError, measure_unsafe
from proctor.testing.vector_compare import stage_rust_outputs


def _vectors_cell(c: dict) -> str:
    total = c.get("passed", 0) + c.get("skipped", 0) + c.get("failed", 0)
    cell = f"{c.get('passed', 0)}/{total}"
    skip = c.get("skipped", 0)
    if skip:
        fs = c.get("fs_skipped", 0)
        cell += f" ({fs} fs-skip)" if fs == skip else f" ({skip} skip)"
    if c.get("failed", 0):
        cell += " FAIL"
    return cell


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="suite_report")
    ap.add_argument(
        "run_dir", type=Path, help="a bench_no_falco run dir (has verify.json)"
    )
    ap.add_argument(
        "--no-idiomaticity",
        action="store_true",
        help="skip clippy (vectors + unsafe only; no per-case build)",
    )
    args = ap.parse_args(argv)

    vj = args.run_dir / "verify.json"
    if not vj.is_file():
        print(f"no verify.json in {args.run_dir}", file=sys.stderr)
        return 1
    d = json.loads(vj.read_text(encoding="utf-8"))
    cases = sorted(d.get("cases", []), key=lambda c: c["case"])
    do_idiom = not args.no_idiomaticity

    print(f"verify + metrics: {vj}")
    print(
        f"{d.get('suite', '?')}   {d.get('cases_ok', 0)}/{d.get('cases_total', 0)} cases clean"
    )
    print("=" * 78)
    print(f"{'case':<26}{'vectors':<18}{'unsafe (score/KLOC)':<24}{'clippy':<8}")
    print("-" * 78)

    tot_pass = tot_fail = tot_skip = tot_unsafe = tot_lints = 0
    for c in cases:
        leaf = c["case"].split("/")[-1]
        tot_pass += c.get("passed", 0)
        tot_fail += c.get("failed", 0)
        tot_skip += c.get("skipped", 0)

        us = it = "-"
        outputs = stage_rust_outputs(args.run_dir / leaf)
        if outputs:
            _, final = outputs[-1]
            loc = 0
            try:
                u = measure_unsafe(final)
                us = f"{u.score} ({u.per_kloc}/K)"
                tot_unsafe += u.score
                loc = u.total_lines
            except (UnsafeEvalError, OSError):
                us = "err"
            if do_idiom:
                with tempfile.TemporaryDirectory() as td:
                    try:
                        i = measure_idiomaticity(final, workdir=Path(td), loc=loc)
                        it = str(i.total)
                        tot_lints += i.total
                    except (IdiomEvalError, OSError):
                        it = "skip"
        print(f"{leaf:<26}{_vectors_cell(c):<18}{us:<24}{it:<8}")

    print("-" * 78)
    idiom = f"clippy {tot_lints}" if do_idiom else "clippy (skipped)"
    print(
        f"totals: vectors {tot_pass} pass / {tot_fail} fail / {tot_skip} skip   "
        f"unsafe {tot_unsafe}   {idiom}"
    )
    print(
        "(unsafe/clippy are for each case's FINAL-stage translation; lower is better)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
