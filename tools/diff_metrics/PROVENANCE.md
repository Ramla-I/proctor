# Original: `diff_metrics`

Written for PROCTOR (not vendored). Unlike `tools/measure_unsafety` and
`tools/measure_idiomaticity` — third-party scorers we vendor unmodified — this
crate is ours and may be edited in place. Ported and generalized from the
unmerged `recovery_metrics` (branch `feedback_metrics`).

## What it is

A **stage-neutral before/after diff** of two Rust crates — normally a stage's
input (the previous stage's `out/rust`) and its output — so every PROCTOR stage
is measured the same way. Purely syntactic (`syn` v2): no name resolution, no
type inference, no build.

It reports:

- **totals** — `files_changed`, `lines_added`, `lines_removed`;
- **per changed file** — `status` (`modified` / `added` / `removed`),
  `lines_added` / `lines_removed`, `rustfmt_ok`, plus structural signals on the
  AFTER version used by stage feedback: `raw_ptr_fields`, `into_from_raw`,
  `malloc_free`, `raw_derefs`, `adopts_target` (a std collection is used),
  `non_boundary_unsafe` (unsafe outside an `extern "C"` body);
- **ABI diff** of `extern "C"` / `#[no_mangle]` functions — `removed`,
  `removed_bin_internal`, `added`, `changed`.

## How lines are counted

Both sides of a file whose text differs are run through `rustfmt` first, then
compared as an order-insensitive multiset of trimmed, non-blank lines. So a pure
reformat is zero churn and a moved block is not counted as change. If rustfmt is
missing or rejects a file, raw text is used and the file says `rustfmt_ok:
false`. This is a churn measure, **not** a true edit distance.

## How the ABI is compared

A signature is normalized to name + `extern "<abi>"` + parameter **types** +
return type. Deliberately ignored, because they don't change the C ABI:
parameter names and binding `mut`; `unsafe`; C type aliases (`size_t`→`usize`,
`c_int`→`i32`, `c_double`→`f64`, `core::ffi::` / `libc::` paths, …, LP64);
token-spacing and trailing commas.

Two caveats a caller handles:

- **c2rust over-exports.** It marks every non-`static` C function
  `#[no_mangle] pub`, and CRAT's `extern` pass internalizes the ones the public
  header doesn't declare. Pass `--api <file>` (one public symbol per line, e.g.
  from the case's C headers) to restrict the ABI diff to the real API.
- **Binaries.** Externs defined in a file with `fn main` are reported under
  `removed_bin_internal`, not `removed` — they aren't exported library ABI.

## Build / run

- `cargo build --release --offline` → `target/release/diff_metrics` (deps match
  `measure_unsafety`'s set, so they're in the cargo cache / Docker image).
- `diff_metrics --before <crate> --after <crate> [--all-files] [--no-rustfmt] [--api <file>]`
  → JSON on stdout (`schema_version: 2`).
- `cargo test`: facade signals, ABI capture and normalization (aliases,
  trailing commas, binding `mut`), bin-root detection, churn, and the rustfmt
  normalization + fallback.
