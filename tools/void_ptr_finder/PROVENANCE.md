# Original: `void_ptr_finder`

Written for PROCTOR (not vendored); edit in place.

## What it is

A **symbolic** (`syn` v2) detector for the **void-pointer class** of recoveries
in a C2Rust+CRAT crate. In C a tagged type is often a `void*` payload selected by
a type tag, and a generic container is a `void*`-erased store; C2Rust emits these
as explicit `core::ffi::c_void`, so a syntactic scan finds them. It reports three
shapes:

- **DISPATCH** -- a fn with a `*mut/*const c_void` param AND an enum (tag) param
  -> `tagged_union` (match on the tag). The `c2Collided(void*, void*, C2_TYPE)`
  idiom.
- **UNION** -- a struct with a `c_void` field AND an enum-discriminant field
  -> `tagged_union` (void* payload).
- **CONTAINER** -- a struct with a `c_void` field and no enum discriminant
  -> `container` (void*-erased element store; a generics / typed-collection hint).

`abstraction_recovery` consumes the `tagged_union` ones (via
`common.find_tagged_unions`, alongside `tagged_union_finder`), so an LLM scan
can't miss void*-based unions. The `container` ones are reported for future
generics work.

## Relationship to crat's `void_finder`

This is the **lightweight stand-in** for crat's `crates/finders/src/void_finder.rs`,
which is a **rustc plugin** (resolves types precisely via `TyCtxt`). This tool
matches `c_void` **syntactically** -- cheaper, no rustc-dev toolchain / crat bump,
but coarser (it can flag libc types like `_IO_FILE` that carry a `void*`, and it
won't follow type aliases). Reusing the rustc `void_finder` for precision is a
documented later upgrade (route A).

## Build / run

- `cargo build --release` -> `target/release/void_ptr_finder` (deps match the
  other tools; builds offline from the shared cargo cache).
- `void_ptr_finder --crate <crate_dir>` -> JSON array `{file, kind, target,
  shape, signal}`.

Unit tests (`cargo test`): a void*+tag dispatch fn, container-vs-union
classification, and ignoring void-free code.
