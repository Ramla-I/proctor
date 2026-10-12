#!/usr/bin/env python3
"""Compare two pipeline stages' Rust output for a case — without typing long paths.

You give a CASE (or run dir) and two stage nicknames; the script finds the run
dir and the `stages/<NN-stage>/out/rust` crate for each, then prints a summary:
files changed, unsafe/idiomaticity (measure_unsafety), and per-stage time/tokens/
cost. With --code it also prints `git diff --no-index` of the changed *source*
files only (.rs + Cargo.toml) — never target/ or .git.

Stage nicknames:  c2rust | crat | testgen | absrec | disrep   (also 0..4, or the
full name / NN-name). Declined stages (no out/rust) fall back to the crate that
flowed through unchanged, and are marked [forwarded].

Examples:
  scripts/compare_stages.py array_list crat absrec
  scripts/compare_stages.py array_list c2rust disrep --code
  scripts/compare_stages.py chibicc 1 3 --code --context 6
  scripts/compare_stages.py out/batch-5stage/array_list-2026...-9fc6ac crat absrec

Notes:
  - Run dir: a CASE name resolves to the latest matching run dir (by mtime),
    searched recursively under --base (default out/), and that choice is echoed
    to stderr. Pass a run-dir path as the first arg, or --run DIR, to pin a
    specific run -- recommended whenever a case has more than one run dir.
  - --context N: how many unchanged lines surround each change in the --code
    diffs (default 3; same as `git diff -U<N>`). Use 0 for changed lines only,
    a larger N to see more of the enclosing function. No effect without --code.
  - --base DIR: root searched (recursively) for run dirs. --no-color: plain diffs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE = (
    REPO_ROOT / "out"
)  # searched recursively; newest matching run wins (--base to change)
PRUNE_DIRS = {
    "target",
    ".git",
    ".github",
    "node_modules",
    "__pycache__",
    "test_vectors",
}
SOURCE_SUFFIX = ".rs"
SOURCE_NAMES = {"Cargo.toml"}

# nickname -> the stage_id (the part after the NN- prefix in stages/<NN-stage>)
ALIASES = {
    "c2rust": "c2rust",
    "0": "c2rust",
    "crat": "crat",
    "1": "crat",
    "testgen": "test_generation",
    "test_gen": "test_generation",
    "test_generation": "test_generation",
    "tg": "test_generation",
    "2": "test_generation",
    "absrec": "abstraction_recovery",
    "abstraction_recovery": "abstraction_recovery",
    "ar": "abstraction_recovery",
    "recovery": "abstraction_recovery",
    "3": "abstraction_recovery",
    "disrep": "discipline_repair",
    "discipline_repair": "discipline_repair",
    "dr": "discipline_repair",
    "4": "discipline_repair",
}


def die(msg: str) -> "None":
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(2)


# ----- locate run dir & stages -------------------------------------------------


def stage_dirs(run: Path) -> "list[Path]":
    """All stages/<NN-stage> dirs for a run, ordered by NN."""
    sdir = run / "stages"
    if not sdir.is_dir():
        die(f"no stages/ under {run}")
    return sorted(
        (d for d in sdir.iterdir() if d.is_dir() and d.name[:2].isdigit()),
        key=lambda d: d.name,
    )


def resolve_run(case: str, base: Path, override: "Path | None") -> Path:
    if override:
        if not (override / "stages").is_dir():
            die(f"--run {override} has no stages/")
        return override
    p = Path(case)
    if (p / "stages").is_dir():  # CASE given as a direct run dir
        return p
    # search --base recursively (bounded depth) for <case>-* run dirs; run dirs
    # live under out/<batch-or-bench>/<case>-<ts>, so check a few levels.
    seen: dict[Path, Path] = {}
    for pat in (f"{case}-*", f"*/{case}-*", f"*/*/{case}-*"):
        for d in base.glob(pat):
            if d.is_dir() and (d / "stages").is_dir():
                seen[d.resolve()] = d
    cands = list(seen.values())
    complete = [d for d in cands if (d / "stages" / "04-discipline_repair").is_dir()]
    pool = complete or cands
    if not pool:
        die(f"no run dir for case '{case}' under {base} (try --base or --run)")
    chosen = max(pool, key=lambda d: d.stat().st_mtime)  # the latest run
    try:
        shown = chosen.relative_to(base)
    except ValueError:
        shown = Path(chosen.name)
    extra = f" ({len(pool)} matched; latest by mtime)" if len(pool) > 1 else ""
    # never silently pick "latest" — announce the resolved dir on stderr.
    print(
        f"note: resolved '{case}' -> {shown}{extra}; pass a run dir or --run to pin one",
        file=sys.stderr,
    )
    return chosen


def resolve_stage(run: Path, ident: str) -> "tuple[Path, str, bool]":
    """Return (crate_path, label, forwarded?) for a stage nickname/number/name."""
    sid = ALIASES.get(ident.lower(), ident.lower().split("-", 1)[-1])
    order = stage_dirs(run)
    match = next((d for d in order if d.name.split("-", 1)[1] == sid), None)
    if match is None:
        names = ", ".join(d.name.split("-", 1)[1] for d in order)
        die(f"stage '{ident}' not found in {run.name} (have: {names})")
    idx = order.index(match)
    # walk back to the last stage that actually produced a crate (declined -> forwarded)
    for j in range(idx, -1, -1):
        crate = order[j] / "out" / "rust"
        if (crate / "Cargo.toml").is_file():
            return crate, match.name, j != idx
    die(f"no out/rust found at or before stage {match.name}")


# ----- source files ------------------------------------------------------------


def source_files(crate: Path) -> "dict[str, str]":
    """rel_path -> md5 for each source file under the crate (prunes target/, .git...)."""
    out: dict[str, str] = {}
    for dp, dns, fns in os.walk(crate):
        dns[:] = [d for d in dns if d not in PRUNE_DIRS and not d.startswith(".")]
        for f in fns:
            if f.endswith(SOURCE_SUFFIX) or f in SOURCE_NAMES:
                fp = Path(dp) / f
                rel = str(fp.relative_to(crate))
                try:
                    out[rel] = hashlib.md5(fp.read_bytes()).hexdigest()
                except OSError:
                    pass
    return out


# ----- metrics -----------------------------------------------------------------


def run_unsafety(crate: Path) -> "dict | None":
    mu = REPO_ROOT / "tools/measure_unsafety/target/release/measure_unsafety"
    if not mu.exists():
        return None
    try:
        r = subprocess.run(
            [str(mu), str(crate)], capture_output=True, text=True, timeout=120
        )
        return json.loads(r.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return None


def durations(run: Path) -> "dict[str, float]":
    """stage_id -> wall seconds, from events.jsonl (authoritative)."""
    out: dict[str, float] = {}
    ev = run / "events.jsonl"
    if ev.is_file():
        for line in ev.read_text().splitlines():
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("event") == "stage_finished" and "duration_s" in o:
                out[o["stage_id"]] = float(o["duration_s"])
    return out


def resources(stage_dir: Path) -> "dict":
    """time is filled by caller; tokens/cost from the stage envelope."""
    env_path = stage_dir / "stage_output.json"
    tok = cost = None
    if env_path.is_file():
        try:
            e = json.loads(env_path.read_text())
        except json.JSONDecodeError:
            e = {}
        m = e.get("metrics") or {}
        u = e.get("usage") or {}
        tok = m.get("total_tokens")
        if tok is None and u:
            tok = (
                sum(
                    int(u.get(k, 0) or 0)
                    for k in (
                        "input_tokens",
                        "cached_input_tokens",
                        "cached_write_tokens",
                        "output_tokens",
                        "reasoning_tokens",
                    )
                )
                or None
            )
        cost = m.get("cost_usd") if m.get("cost_usd") is not None else u.get("cost_usd")
    return {"tokens": tok, "cost": cost}


# ----- rendering ---------------------------------------------------------------


def fmt(n, kind="int"):
    if n is None:
        return "—"
    if kind == "int":
        return f"{int(n):,}"
    if kind == "s":
        return f"{float(n):.1f}"
    if kind == "usd":
        return f"${float(n):.3f}"
    return str(n)


def git_diff(a: "Path | str", b: "Path | str", context: int, color: bool) -> str:
    cmd = [
        "git",
        "-c",
        f"color.ui={'always' if color else 'never'}",
        "--no-pager",
        "diff",
        "--no-index",
        f"--unified={context}",
        str(a),
        str(b),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout  # exit 1 just means "differs"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Compare two pipeline stages' Rust output for a case.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("case", help="case name (e.g. array_list) or a run-dir path")
    ap.add_argument(
        "stage_a", help="first stage: c2rust|crat|testgen|absrec|disrep (or 0..4)"
    )
    ap.add_argument("stage_b", help="second stage")
    ap.add_argument(
        "--code", action="store_true", help="also show git diff of changed source files"
    )
    ap.add_argument(
        "--base",
        type=Path,
        default=DEFAULT_BASE,
        help=f"run-dir root (default {DEFAULT_BASE})",
    )
    ap.add_argument("--run", type=Path, help="explicit run dir (overrides case lookup)")
    ap.add_argument(
        "--context", type=int, default=3, help="diff context lines (default 3)"
    )
    ap.add_argument("--no-color", action="store_true", help="disable colored diff")
    args = ap.parse_args()

    run = resolve_run(args.case, args.base, args.run)
    crate_a, label_a, fwd_a = resolve_stage(run, args.stage_a)
    crate_b, label_b, fwd_b = resolve_stage(run, args.stage_b)
    order = stage_dirs(run)
    idx = {d.name: i for i, d in enumerate(order)}
    ia, ib = idx[label_a], idx[label_b]
    if ia > ib:
        label_a, label_b = label_b, label_a
        crate_a, crate_b = crate_b, crate_a
        fwd_a, fwd_b = fwd_b, fwd_a
        ia, ib = ib, ia

    bar = "─" * 72
    print(bar)
    print(f"  {run.name}")
    tag_a = "  [forwarded]" if fwd_a else ""
    tag_b = "  [forwarded]" if fwd_b else ""
    print(f"  {label_a}{tag_a}   ─▶   {label_b}{tag_b}")
    print(bar)

    # --- files changed ---
    fa, fb = source_files(crate_a), source_files(crate_b)
    added = sorted(set(fb) - set(fa))
    removed = sorted(set(fa) - set(fb))
    modified = sorted(f for f in (set(fa) & set(fb)) if fa[f] != fb[f])
    print(
        f"\nFILES CHANGED (source only): "
        f"{len(modified)} modified · {len(added)} added · {len(removed)} removed"
    )
    for f in modified:
        print(f"   ~ {f}")
    for f in added:
        print(f"   + {f}")
    for f in removed:
        print(f"   - {f}")
    if not (modified or added or removed):
        print("   (no source changes)")

    # --- unsafe / idiomaticity ---
    ua, ub = run_unsafety(crate_a), run_unsafety(crate_b)
    print("\nUNSAFE / IDIOMATICITY  (measure_unsafety)")
    if ua and ub:
        rows = [
            ("unsafe_score", "unsafe_score"),
            ("unsafe statements", "unsafe_statements"),
            ("unsafe fns", "unsafe_fns"),
            ("total lines", "total_lines"),
            ("total files", "total_files"),
        ]
        w = max(len(r[0]) for r in rows)
        print(
            f"   {'':<{w}}  {label_a.split('-', 1)[1]:>12}  {label_b.split('-', 1)[1]:>12}  {'Δ':>8}"
        )
        for lbl, key in rows:
            a, b = ua.get(key), ub.get(key)
            d = (b - a) if (isinstance(a, int) and isinstance(b, int)) else None
            ds = f"{d:+d}" if d is not None else "—"
            print(f"   {lbl:<{w}}  {fmt(a):>12}  {fmt(b):>12}  {ds:>8}")
    else:
        print("   (measure_unsafety unavailable — build tools/measure_unsafety/)")

    # --- time / tokens / cost for the transforming stages (A, B] ---
    durs = durations(run)
    print("\nRESOURCES  (per stage; A→B cost = stages after A)")
    print(f"   {'stage':<22}{'time_s':>9}{'tokens':>14}{'cost':>10}")
    tt = ttok = tcost = 0.0
    for i in range(ia, ib + 1):
        d = order[i]
        sid = d.name.split("-", 1)[1]
        res = resources(d)
        dur = durs.get(sid)
        mark = " (start)" if i == ia else ""
        print(
            f"   {d.name:<22}{fmt(dur, 's'):>9}{fmt(res['tokens']):>14}"
            f"{fmt(res['cost'], 'usd'):>10}{mark}"
        )
        if i > ia:  # transforming A -> B
            tt += dur or 0
            ttok += res["tokens"] or 0
            tcost += res["cost"] or 0
    print(f"   {'─' * 55}")
    print(
        f"   {'A→B total':<22}{tt:>9.1f}{int(ttok):>14,}{'$' + format(tcost, '.3f'):>10}"
    )

    # --- code ---
    if args.code:
        color = sys.stdout.isatty() and not args.no_color

        def short(txt: str) -> str:  # replace the long abs paths with stage labels
            return txt.replace("a" + str(crate_a), "a/" + label_a).replace(
                "b" + str(crate_b), "b/" + label_b
            )

        print(f"\n{bar}\nCODE CHANGES (source files)\n{bar}")
        for f in modified:
            print(
                short(git_diff(crate_a / f, crate_b / f, args.context, color)), end=""
            )
        for f in added:
            print(
                short(git_diff("/dev/null", crate_b / f, args.context, color)), end=""
            )
        for f in removed:
            print(
                short(git_diff(crate_a / f, "/dev/null", args.context, color)), end=""
            )
        if not (modified or added or removed):
            print("(no source changes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
