# Original: `diff_metrics`

Written for PROCTOR (not vendored). Unlike `tools/measure_unsafety` and
`tools/measure_idiomaticity` — third-party scorers we vendor unmodified — this
crate is ours and may be edited in place.

## What it is

A **stage-neutral before/after diff** of two Rust crates — normally a stage's
input (the previous stage's `out/rust`) and its output — so every PROCTOR stage
is measured the same way. Purely syntactic (`syn` v2): no name resolution, no
type inference, no build. Unreadable inputs (a missing directory, an unreadable
file, a missing or invalid `Cargo.toml`, an unreadable `--api` file) are errors
(exit 2), never an empty or clean report.

It reports (`schema_version: 3`):

- **totals** — `files_changed`, `lines_added`, `lines_removed`, `touched_files`;
- **per changed file** — `status`, `lines_added`, `lines_removed`, `rustfmt_ok`;
- **targets** — for each side, the Cargo targets, and `unreachable_files` that no
  target compiles;
- **abi** — exported symbols `removed` / `added` / `changed`,
  `removed_bin_internal`, `types_changed`, `duplicate_symbols`,
  `unparsed_files`, the `c_type_model` used, and an overall `ok`.

## How lines are counted (`src/churn.rs`)

Both sides of a file whose text differs are run through `rustfmt` first, then
compared as a multiset of trimmed, non-blank lines: `lines_added` = lines in
after beyond before, `lines_removed` the reverse. A pure reformat is zero churn
and moving lines is not churn. A file whose lines are the same multiset in a
different order has status **`reordered`**: it counts in `files_changed` with 0
lines added and removed. If rustfmt is missing or rejects a file, raw text is
compared and the file says `rustfmt_ok: false`. This is a churn measure, **not**
an edit distance.

## Which files belong to which target (`src/layout.rs`)

A crate can build several artifacts: the library, whose exported symbols are
the ABI C code links against, and binaries, whose exports are no one's API. A
symbol a stage stops exporting is a break only if the library exported it, and
a file no target compiles exports nothing. So each file must be attributed to
the targets that actually compile it.

Targets come from `Cargo.toml`: `[lib]`, `[[bin]]`, and Cargo's defaults
(`src/lib.rs`, `src/main.rs`, `src/bin/*` unless `autobins = false`). Each
target's files are found by following `mod` declarations from its root —
`x.rs` / `x/mod.rs`, inline `mod x { .. }`, `#[path]` — as rustc does. So a
library module that happens to define `fn main` is library code, and a binary
module without `main` is binary code. Not handled: `#[cfg]` (every `mod` is
followed) and `include!`.

## How the ABI is compared (`src/abi.rs`)

**Exported symbols** are items with `#[no_mangle]` or `#[export_name = "…"]`
(including the `#[unsafe(…)]` forms): functions and statics, keyed by symbol
name. `extern "C"` alone is a calling convention, not an export; it is part of
the signature, so dropping it from an export is a change. Symbols exported by a
binary target are not library ABI: one that disappears is listed under
`removed_bin_internal`, not `removed`.

**Signatures** — calling convention, parameter types (or the static's type),
variadic, return type — are built as a type tree from the syntax, so `(T,)` (a
1-tuple) stays distinct from `(T)` while trailing commas and spacing don't
matter. Not part of the signature: parameter names and patterns, `unsafe`,
lifetimes, `-> ()`. Crate type aliases (`type X = Y;`, non-generic and
unambiguous) are expanded, and C type names (`size_t`, `c_int`, `c_long`,
`c_char`, `int32_t`, …, and `usize`/`isize`) map to the fixed-width primitive
they denote on the target in `c_type_model`. That model is the target the tool
is compiled for — `c_char` signedness and `c_long`/pointer width differ between
targets, e.g. `c_char` is `i8` on x86-64 Linux and `u8` on aarch64 Linux — which
is the target the pipeline builds and tests the crate on.

**Layouts.** The C ABI is structural, so crate types are compared by layout,
not by name: `#[repr]`, field names, order and types with generic arguments
substituted, enum variants and discriminants. Every crate type reached from an
exported signature is compared, through fields, pointers, arrays and generic
arguments; recursion through recursive types is cut by remembering compared
pairs. Consequences:

- a stage that replaces `struct list_int_t { head, size }` with a generic
  `List<i32>` of the same layout keeps the ABI;
- a reordered field, a changed field type or `#[repr]` is a `types_changed`
  entry (named `old -> new` when the type was also renamed);
- an integer that became a fieldless enum whose `#[repr]` is that integer
  (`u32` → `#[repr(u32)] enum cc_stat`) is reported with kind `int_to_enum` and
  is **not** breaking: it is the same in the C ABI, but Rust now assumes C only
  passes listed values — a contract the vector tests exercise, not this tool;
- c2rust's forward declarations (`extern { type T; }`) merge with T's
  definition; several *distinct* definitions under one name (path resolution
  would be needed to tell them apart) are compared as a set.

A layout change is **breaking** when the type is reached by value, when
`--api` lists it as a `type` (the C headers define its body, so C code may read
its fields), or when there is no `--api` to tell. A change to a type C only
handles through a pointer and the headers keep opaque is reported but not
breaking. `abi.ok` is false if any library symbol was removed or changed, any
layout change is breaking, a symbol is exported twice, or a compiled file did
not parse.

**`--api <file>`** — one entry per line: `symbol NAME` (or a bare `NAME`) for an
exported function or global, `type NAME` for a type whose layout the headers
define. c2rust exports every non-`static` C function, and CRAT internalizes the
ones the header doesn't declare; without `--api` those count as removed.

**Limits.** No `#[cfg]` evaluation, no path resolution (types are identified by
their last path segment), no size/alignment computation, no check of what a
function does with its arguments. It is a conservative change detector over
symbols, signatures and declared layouts — not a proof of ABI compatibility.

## Build / run

- `cargo build --release` → `target/release/diff_metrics` (`proctor.testing.diff_eval`
  builds it on first use). Deps are `measure_unsafety`'s set plus `toml` 0.8;
  the first build may need to fetch `toml` and its dependencies.
- `diff_metrics --before <crate> --after <crate> [--api <file>] [--no-rustfmt]`
  → JSON on stdout; errors on stderr with exit 2.
- `cargo test`: input errors, churn and `reordered`, rustfmt fallback, Cargo
  target and module-tree resolution (incl. c2rust's layout), export detection,
  signature normalization (aliases, 1-tuples, trailing commas, `unsafe`),
  structural layout comparison (field order/type/repr, generic renames,
  `int_to_enum`, opaque vs public behind pointers, forward declarations), `--api`
  parsing.
