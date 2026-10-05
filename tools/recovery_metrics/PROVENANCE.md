# Original: `recovery_metrics`

Written for PROCTOR (not vendored). Unlike `tools/measure_unsafety` and
`tools/measure_idiomaticity` — which are third-party scorers we vendor
unmodified — this crate is ours and may be edited in place.

## What this tool is — and what it is NOT

A **symbolic structural** diff of a crate BEFORE recovery (the `crat` output)
vs AFTER (`abstraction_recovery`'s output). It parses with `syn` v2 (same
patterns as `measure_unsafety` / `crates/proctor-rust-index`) and is purely
syntactic: no name resolution, no type inference, no build — best-effort over
whatever parses.

It computes, per touched file (or every after-file with `--all-files`):

- **churn** (added/removed) as an order-insensitive multiset line diff over
  trimmed, blank-stripped lines — a coarse minimality proxy that ignores pure
  reformatting, **not** a true edit distance;
- **facade pre-filter** signals on the AFTER file: `raw_ptr_fields` (struct
  fields whose type contains a `*mut`/`*const`), `into_from_raw`
  (`into_raw`/`from_raw` uses), `malloc_free` (retained C-alloc decls/calls),
  `raw_derefs` (deref expressions `*e`, a proxy for raw-pointer work), and
  `adopts_target` (a std collection type is used);
- **`non_boundary_unsafe`**: `unsafe` blocks / `unsafe fn` / `unsafe impl`
  that are NOT inside an `extern "C"`/`#[no_mangle]` body — the boundary needs
  some unsafe, internal code should not (ideal 0);

plus a top-level **ABI diff** of every `extern "C"`/`#[no_mangle]` signature
(added / removed / changed), where a signature is normalized to name +
unsafety + abi + parameter TYPES + return type (binding `mut` and parameter
names are deliberately ignored; a type change is a real break).

It deliberately does **NOT** count unsafe *score* (that is `measure_unsafety`,
which also runs source-only) or clippy lints (that is `measure_idiomaticity`,
which needs two clippy builds). Single responsibility: structural/diff passes.
A caller — `proctor.testing.recovery_quality` offline, or the stage's in-loop
`quality.py` — composes this with the unsafe/clippy scorers into the quality
panel.

The facade signals are a **suspicion** score, not a verdict: some legitimate
`extern "C"` field syncing uses `*mut`. A semantic LLM verdict over this
pre-filter is deferred work.

## Build / run

- Build (stable rust): `cargo build --release --offline`
  -> `target/release/recovery_metrics`. Deps match `measure_unsafety`'s set so
  they are already in the cargo cache / Docker image; built on demand by
  `proctor.testing.recovery_quality`.
- Run: `recovery_metrics --before <crate_dir> --after <crate_dir> [--all-files]`
  -> JSON (`schema_version`, `touched_files`, `abi`, per-file `files`) on stdout.

Unit tests (`cargo test`) cover the facade-struct flags, a clean-collection
file, extern-C capture, signature normalization, retained malloc/free,
deref counting, and non-boundary-unsafe accounting.
