"""Self-diagnostic quality panel for an abstraction_recovery transform.

Composes the symbolic `tools/` passes over a crate BEFORE (crat) and AFTER
(recovered) into one report a backend can act on *without a peer to compare
against*:

  - M1 residual & net unsafe   -> `measure_unsafety --file` (via unsafe_eval)
  - M2 churn / wandering edits  -> `tools/recovery_metrics`
  - M3 facade pre-filter        -> `tools/recovery_metrics` (raw-ptr fields,
                                   into_raw/from_raw, retained malloc/free)
  - M5 ABI guardrail            -> `tools/recovery_metrics` (extern-C sig diff)

The structural signals (retained C allocation, raw-pointer fields, changed ABI,
wandering edits) each have a *knowable ideal of 0*, so `render_critique` leads
with those — they tell the model exactly which lines to fix. Residual unsafe has
no peer-free floor (the `extern "C"` boundary legitimately needs some), so it is
reported as context, not a standalone target.

This is a thin caller: the analysis lives in the tools, so the same computation
runs offline here and in the stage's isolated venv (which shells the binaries by
path). Mirrors how `idiomaticity_eval` wraps `measure_idiomaticity`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from proctor.testing.unsafe_eval import UnsafeEvalError, measure_unsafe_file

_TOOL_DIR = Path(__file__).resolve().parent.parent.parent / "tools" / "recovery_metrics"
_BIN = _TOOL_DIR / "target" / "release" / "recovery_metrics"


class RecoveryQualityError(RuntimeError):
    """recovery_metrics could not be built or run (not a quality finding)."""


def ensure_built(*, timeout_s: int = 900) -> Path:
    """Build the vendored recovery_metrics binary on demand (stable rust)."""
    if _BIN.is_file():
        return _BIN
    proc = subprocess.run(
        ["cargo", "build", "--release"],
        cwd=str(_TOOL_DIR),
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    if proc.returncode != 0 or not _BIN.is_file():
        raise RecoveryQualityError(
            f"failed to build recovery_metrics:\n{(proc.stderr or proc.stdout)[-1500:]}"
        )
    return _BIN


@dataclass
class FileQuality:
    """Per-touched-file signals. ``residual`` is the AFTER file's unsafe_score;
    ``net`` is AFTER − BEFORE (negative means the transform removed C unsafe)."""

    file: str
    residual_unsafe: int
    net_unsafe: int
    churn_added: int
    churn_removed: int
    raw_ptr_fields: int
    into_from_raw: int
    raw_derefs: int
    malloc_free: int
    non_boundary_unsafe: int
    adopts_target: bool
    parse_ok: bool

    @property
    def facade_signal(self) -> int:
        """Retained C plumbing a std collection makes unnecessary (ideal 0)."""
        return self.raw_ptr_fields + self.malloc_free


@dataclass
class RecoveryQualityReport:
    src: str
    dst: str
    touched_files: list[str]
    wandering_files: list[str]
    abi_changed: list[str]
    abi_removed: list[str]
    abi_added: list[str]
    files: list[FileQuality] = field(default_factory=list)

    @property
    def residual_unsafe(self) -> int:
        return sum(f.residual_unsafe for f in self.files)

    @property
    def net_unsafe(self) -> int:
        return sum(f.net_unsafe for f in self.files)

    @property
    def facade_signal(self) -> int:
        return sum(f.facade_signal for f in self.files)

    @property
    def non_boundary_unsafe(self) -> int:
        """Unsafe outside the `extern \"C\"` boundary (ideal 0)."""
        return sum(f.non_boundary_unsafe for f in self.files)

    @property
    def raw_derefs(self) -> int:
        """Raw-pointer dereferences (`*p`); fewer is better — the main driver of
        residual unsafe. Not zero-floored (the boundary needs a few)."""
        return sum(f.raw_derefs for f in self.files)

    def needs_repair(self, *, residual_threshold: int | None = None) -> bool:
        """True when an ideal-0 signal is nonzero (retained C plumbing, a changed
        or dropped ABI signature, or an edit outside the candidate file), or when
        residual unsafe exceeds an explicit threshold. Residual alone is not a
        trigger — its floor (the `extern "C"` boundary) is unknown peer-free."""
        if self.facade_signal > 0 or self.abi_changed or self.abi_removed:
            return True
        if self.wandering_files:
            return True
        if residual_threshold is not None and self.residual_unsafe > residual_threshold:
            return True
        return False

    def render_critique(self) -> str:
        """A self-diagnostic offender list to feed back to the transform. Names
        the specific ideal-0 violations first, residual unsafe as context."""
        lines: list[str] = []
        if self.abi_removed:
            lines.append(
                f'- {len(self.abi_removed)} `extern "C"`/`#[no_mangle]` function(s) '
                f"were DROPPED ({', '.join(self.abi_removed)}): restore them — the "
                "public API must be preserved exactly."
            )
        if self.abi_changed:
            lines.append(
                f'- {len(self.abi_changed)} `extern "C"` signature(s) changed '
                f"({', '.join(self.abi_changed)}): restore the ORIGINAL signatures "
                "byte-for-byte; callers must not notice."
            )
        for f in self.files:
            if f.malloc_free:
                lines.append(
                    f"- `{f.file}`: {f.malloc_free} retained C allocation "
                    "call/decl (malloc/free/calloc/realloc) — remove them; the "
                    "collection owns its storage."
                )
            if f.raw_ptr_fields:
                lines.append(
                    f"- `{f.file}`: {f.raw_ptr_fields} raw-pointer struct field(s) "
                    "— a std collection owns its elements by value; store what you "
                    "need in the element, not a raw pointer."
                )
        if self.wandering_files:
            lines.append(
                f"- edits outside the target data structure: {', '.join(self.wandering_files)} "
                "— change only the one structure; leave other files unchanged."
            )
        if not lines:
            return ""
        # extra guidance, only shown alongside a hard offender (not a standalone
        # trigger — a good recovery can have a little boundary-adjacent unsafe):
        if self.non_boundary_unsafe:
            lines.append(
                f"- {self.non_boundary_unsafe} `unsafe` block(s)/fn(s) OUTSIDE the "
                '`extern "C"` boundary — move that logic into safe code; only the '
                "boundary functions themselves should be unsafe."
            )
        if self.raw_derefs:
            lines.append(
                f"- {self.raw_derefs} raw-pointer dereference(s) (`*p`) — access "
                "elements through the collection's safe API (indexing/iteration) "
                "rather than dereferencing pointers."
            )
        header = (
            "The recovered code still keeps C-idiom plumbing a Rust std "
            "collection makes unnecessary. Fix these, keeping the crate compiling "
            "and the vectors passing:"
        )
        footer = (
            f"Residual `unsafe` across the touched file(s) is {self.residual_unsafe} "
            "(net vs the C baseline: "
            f"{self.net_unsafe:+d}). Re-express the structure's operations through "
            "the safe std API; the only unsafe should be the minimum at the "
            '`extern "C"` boundary itself.'
        )
        return "\n".join([header, *lines, footer])

    def summary(self) -> str:
        return (
            f"residual unsafe {self.residual_unsafe} (net {self.net_unsafe:+d})  "
            f"derefs {self.raw_derefs}  non-boundary {self.non_boundary_unsafe}  "
            f"facade {self.facade_signal}  abi Δ{len(self.abi_changed)}  "
            f"wandering {len(self.wandering_files)}  touched {len(self.touched_files)}"
        )


def _run_metrics(
    src: Path, dst: Path, *, all_files: bool = False, timeout_s: int = 300
) -> dict[str, Any]:
    binary = ensure_built()
    cmd = [str(binary), "--before", str(src), "--after", str(dst)]
    if all_files:
        cmd.append("--all-files")
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    if proc.returncode != 0:
        raise RecoveryQualityError(
            f"recovery_metrics failed:\n{(proc.stderr or proc.stdout)[-1000:]}"
        )
    try:
        return json.loads(proc.stdout)  # type: ignore[no-any-return]
    except json.JSONDecodeError as e:
        raise RecoveryQualityError("recovery_metrics gave non-JSON output") from e


def _file_unsafe(crate: Path, rel: str) -> int:
    """Per-file unsafe_score, or 0 if the file is absent/unparseable."""
    path = crate / rel
    if not path.is_file():
        return 0
    try:
        return measure_unsafe_file(path).score
    except UnsafeEvalError:
        return 0


def measure_recovery_quality(
    src_crate: Path,
    dst_crate: Path,
    *,
    candidate: str | None = None,
    scope: str = "touched",
) -> RecoveryQualityReport:
    """Compose the symbolic passes over crat (``src``) vs recovered (``dst``).

    ``candidate`` is the crate-root-relative path of the file the transform was
    supposed to change; if given, any *other* touched file is a wandering edit.
    ``scope`` selects which files the numbers cover: ``"touched"`` (default —
    only the files the transform changed, so it is graded on its own work) or
    ``"crate"`` (every file in the recovered crate).
    """
    m = _run_metrics(src_crate, dst_crate, all_files=(scope == "crate"))
    touched: list[str] = list(m.get("touched_files", []))
    abi = m.get("abi", {})
    files_meta: dict[str, Any] = m.get("files", {})

    if scope == "crate":
        rels = [rel for rel, fm in files_meta.items() if fm.get("in_after")]
    else:
        rels = touched

    files: list[FileQuality] = []
    for rel in rels:
        fm = files_meta.get(rel, {})
        residual = _file_unsafe(dst_crate, rel)
        before = _file_unsafe(src_crate, rel)
        files.append(
            FileQuality(
                file=rel,
                residual_unsafe=residual,
                net_unsafe=residual - before,
                churn_added=int(fm.get("churn_added", 0)),
                churn_removed=int(fm.get("churn_removed", 0)),
                raw_ptr_fields=int(fm.get("raw_ptr_fields", 0)),
                into_from_raw=int(fm.get("into_from_raw", 0)),
                raw_derefs=int(fm.get("raw_derefs", 0)),
                malloc_free=int(fm.get("malloc_free", 0)),
                non_boundary_unsafe=int(fm.get("non_boundary_unsafe", 0)),
                adopts_target=bool(fm.get("adopts_target", False)),
                parse_ok=bool(fm.get("parse_ok", False)),
            )
        )

    wandering = [f for f in touched if candidate and f != candidate]
    return RecoveryQualityReport(
        src=str(src_crate),
        dst=str(dst_crate),
        touched_files=touched,
        wandering_files=wandering,
        abi_changed=[c["name"] for c in abi.get("changed", [])],
        abi_removed=list(abi.get("removed", [])),
        abi_added=list(abi.get("added", [])),
        files=files,
    )


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Self-diagnostic quality panel for an abstraction_recovery "
        "transform (crat crate vs recovered crate)."
    )
    ap.add_argument("src_crate", type=Path, help="BEFORE crate dir (crat output)")
    ap.add_argument("dst_crate", type=Path, help="AFTER crate dir (recovered output)")
    ap.add_argument("--candidate", help="crate-relative path the transform targeted")
    ap.add_argument(
        "--scope",
        choices=["touched", "crate"],
        default="touched",
        help="score only the transform's touched files (default) or the whole crate",
    )
    ap.add_argument("--critique", action="store_true", help="print the critique text")
    args = ap.parse_args(argv)

    try:
        report = measure_recovery_quality(
            args.src_crate, args.dst_crate, candidate=args.candidate, scope=args.scope
        )
    except (RecoveryQualityError, UnsafeEvalError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"[scope: {args.scope}] " + report.summary())
    for f in report.files:
        print(
            f"  {f.file}: residual {f.residual_unsafe} (net {f.net_unsafe:+d})  "
            f"derefs {f.raw_derefs}  non-boundary {f.non_boundary_unsafe}  "
            f"raw_ptr_fields {f.raw_ptr_fields}  malloc_free {f.malloc_free}  "
            f"into/from_raw {f.into_from_raw}  churn +{f.churn_added}/-{f.churn_removed}"
        )
    if args.critique:
        crit = report.render_critique()
        print("\n--- critique ---\n" + crit if crit else "\n(no critique — clean)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
