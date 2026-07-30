# Vendored: DARPA `measure_unsafety`

Unmodified copy of the unsafe-usage scorer from
**DARPA-TRACTOR-Program/pipeline-automation**, path
`evaluate_unsafe_usage/measure_unsafety`.

A **syntactic** (syn AST) scorer — DARPA's own "how `unsafe` is this project"
measurement, the one performers are scored with. It walks `<project>/src/**.rs`
and counts unsafe blocks / fns / (pub) fns / impls / statements and a headline
`unsafe_score`. Because it parses rather than compiles, it needs no target
toolchain and works even if the crate doesn't build.

- Build (stable rust): `cargo build --release` → `target/release/measure_unsafety`
  (built on demand by `proctor.testing.unsafe_eval`).
- Run: `measure_unsafety <project_root>` → JSON stats on stdout.

Driven via `proctor.testing.unsafe_eval`. License: as in the source repo.
Do not modify — re-vendor from upstream to update.
