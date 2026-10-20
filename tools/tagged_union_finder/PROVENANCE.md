# Original: `tagged_union_finder`

Written for PROCTOR (not vendored); edit in place.

## What it is

A **symbolic** (`syn` v2) detector for "fat struct" tagged unions in a
C2Rust+CRAT crate: a `struct` with an **enum-discriminant field** (`kind`/`type`)
plus per-variant **payload fields** — the AST / IR / token-node idiom (chibicc's
`Node` + `NodeKind`, 48 variants). These recover to a data-carrying Rust `enum`
(stage kind `tagged_union`).

It exists because an LLM `identify` scan reliably **misses** these in a large
crate — a small, self-contained container (a lone hash map) out-ranks a pervasive
48-variant AST node, so the model never surfaces the high-value target. This pass
finds them deterministically and ranks them by impact, so `abstraction_recovery`
gets `tagged_union` candidates it cannot overlook.

## What it detects (and what it does NOT)

- **Detects:** a struct whose field's type is an enum defined in the crate (the
  discriminant), with `>= --min-variants` variants (default 3) and at least one
  other (payload) field. Ranks by `variants + payload_fields` (a 48-way AST node
  far outranks a 3-way slot tag). Reports a crate-wide `match`-count as a
  dispatch-pervasiveness proxy.
- **Does NOT detect `void*`-payload tagged unions** (`struct { int kind; void* data; }`
  or a `f(void*, tag)` dispatch) — there is no discriminant+typed-fields shape
  there. Those are the **`void_finder`**'s job (the `c_void` signal). The two
  detectors are complementary and together cover the tagged-union space.

Purely syntactic: no name resolution / type inference (same best-effort posture
as `measure_unsafety` / `recovery_metrics`).

## Build / run

- `cargo build --release` -> `target/release/tagged_union_finder` (deps match the
  other tools, so it builds offline from the shared cargo cache).
- `tagged_union_finder --crate <crate_dir> [--min-variants N]` -> JSON array of
  candidates (file, type, discriminant, discriminant_enum, variants,
  payload_fields, crate_match_sites, score), highest score first.

Unit tests (`cargo test`) cover: flagging a fat-struct tagged union, ignoring a
below-threshold tag and a payload-less struct, and impact ranking.
