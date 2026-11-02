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

C code calls the Rust library through the symbols it exports. A stage keeps the
ABI only if C code compiled against the original header still works with the
new library. The tool checks three things, in order: which symbols are
exported, the signature of each exported symbol, and the layout of the types
those signatures use.

### Exported symbols

A function or static is exported when it has `#[no_mangle]` or
`#[export_name = "name"]` (or the `#[unsafe(no_mangle)]` form). The tool tracks
each export by its symbol name, which is the function's own name for
`#[no_mangle]` and the given name for `#[export_name]`.

`extern "C"` on its own does not export anything. It only sets the calling
convention. The tool records it as part of the signature, so a stage that
removes `extern "C"` from an exported function changes that function's
signature.

Only the library's exports are ABI. If a binary target stops exporting a
symbol, the tool lists it under `removed_bin_internal` and does not count it as
a break.

### Signatures

For each exported function the tool compares the calling convention, the
parameter types, whether it is variadic, and the return type. For an exported
static it compares the static's type and whether it is `mut`.

These differences do not change the ABI, so the tool ignores them:

- parameter names and patterns: `fn f(mut x: i32)` and `fn f(y: i32)` match;
- `unsafe`: it is a promise inside Rust, not part of the call;
- lifetimes, and `-> ()` versus no return type;
- spacing and trailing commas.

The tool compares parsed types, not text. So `(T,)`, a one-element tuple, is
still different from `(T)`, which is just `T`.

Two kinds of type names are resolved before comparing:

- **Type aliases in the crate.** `type size_t = c_ulong;` is replaced by
  `c_ulong`. Only aliases without generic parameters are expanded, and only when
  the crate defines the name one way.
- **C type names.** `size_t`, `c_int`, `c_long`, `c_char`, `int32_t` and similar
  names, as well as `usize` and `isize`, are replaced by the fixed-size Rust
  integer they stand for. So a stage that rewrites `c_int` as `i32` does not
  change the signature.

Some C types have a different size on different machines. `c_long` is 64 bits on
Linux x86-64 and 32 bits on Windows, and `c_char` is signed on x86-64 Linux but
unsigned on ARM Linux. The tool uses the sizes of the machine it was compiled
on, which is the machine that builds and tests the crate. The JSON output lists
the sizes it used under `abi.c_type_model`.

### Type layouts

A function's signature can stay the same while the struct it takes changes.
C code reads a struct's fields at fixed byte offsets, so swapping two fields
breaks it even though the signature looks identical. The tool therefore also
compares every crate type that an exported signature uses, either directly or
through fields, pointers, arrays and generic arguments.

C only sees bytes, not Rust type names. So the tool compares two types by their
layout:

- the `#[repr(...)]` attributes;
- for a struct or union, the field names, their order and their types;
- for an enum, the variants, their fields and their discriminant values.

Generic arguments are filled in before comparing. A stage that replaces
`struct list_int_t { head: *mut node_int, len: usize }` with a generic
`List<i32>` that has the same fields therefore keeps the ABI. Recursive types
such as `struct Node { next: *mut Node }` are handled by remembering which pairs
of types have already been compared.

Each type whose layout differs is listed under `abi.types_changed`. If the stage
also renamed the type, the entry is named `old -> new`.

Two special cases:

- **Integer to enum.** CRAT sometimes turns a `u32` status code into a
  `#[repr(u32)]` enum with no fields. C passes both the same way, so this is
  reported with kind `int_to_enum` and is not a break. The Rust side does now
  assume C only passes values the enum lists; the tests check that, this tool
  does not.
- **Forward declarations.** c2rust emits `extern "C" { type T; }` for a struct
  it only sees declared, next to the full `struct T` from another file. The tool
  treats the declaration and the definition as one type.

### When a layout change is a break

C code can only depend on a layout it can see. A layout change is a break when:

- the type is passed or returned by value, or is a field of such a type; or
- the C header defines the type's body, so C code may read its fields. You tell
  the tool this with a `type NAME` line in the `--api` file; or
- no `--api` file was given, so the tool cannot tell and assumes the worst.

A type that C only handles through a pointer, and that the header leaves
opaque (`typedef struct list list;`), can change freely. Abstraction recovery
relies on this, for example to replace a linked list's fields with a `Vec`.
The tool reports such a change but does not count it as a break.

`abi.ok` is false when an exported library symbol was removed or its signature
changed, a layout change is a break, the library exports the same symbol twice,
or a file the crate compiles did not parse.

### The `--api` file

c2rust exports every C function that is not `static`, including helpers the
header never declares. A later stage such as CRAT makes those helpers private,
which is not a break. The `--api` file limits the check to the real public API.
It has one entry per line:

- `symbol NAME` (or just `NAME`): an exported function or global the header
  declares;
- `type NAME`: a type whose body the header defines.

Blank lines and lines starting with `#` are ignored.

### Limits

The tool reads source code only. It does not evaluate `#[cfg]`, does not
resolve paths (it identifies a type by its last path segment, so two types with
the same name in different modules are treated as one), does not compute sizes
or alignment, and does not look at what a function does with its arguments. It
reports changes that may break the ABI. It does not prove that the ABI is
compatible.

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
