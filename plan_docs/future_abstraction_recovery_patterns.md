# Future abstraction_recovery patterns (B01/B02/B03 scan)

Recovered from a design discussion; the `abstraction_recovery` stage today only
targets **container** recoveries (`vec, vecdeque, hashmap, hashset, binaryheap,
btreemap, btreeset, bytebuf` — see `common.KIND_TARGET`, `IDENTIFY_VERSION=6`).
This catalogs the *other* C→Rust abstractions found by scanning the actual
B01/B02/B03 sources, each with concrete examples and a viability verdict against
the two hard constraints the container work established:

1. **The `extern "C"` ABI is the wall.** Most of these can only be *internal*
   rewrites (the public signatures/structs stay fixed); they apply fully only to
   **executables** (where the type is internal) or **purely-local** code.
2. **identify can't see the test runner.** Any recovery that changes observable
   state (a runner reading `->type` or a struct field) fails the gate. The gate
   stays the only verifier.

## Patterns

### 1. Tagged union / `void*` + type tag → Rust `enum`  ← the `enum_pattern_matching` work
- **`chibicc` (B03)** — richest case. `NodeKind` (`ND_ADD`, `ND_ASSIGN`, `ND_CAST`,
  `ND_COND`, `ND_BLOCK`, … ~40) + `TokenKind` (`TK_*`); AST `Node`/`Token` are
  `kind` + payload. → `enum Node { Add(Box<Node>,Box<Node>), Num(i64), Var(Obj), … }`.
  **Viable** — chibicc is an *executable*, so the AST is internal (only stdout
  observed). Strongest enum-recovery candidate in the corpus.
- **`cJSON` (B02)** — `struct cJSON { int type; char *valuestring; double
  valuedouble; struct cJSON *child; … }`, tags `cJSON_Number/String/Array/Object/…`.
  Classic tagged union → `enum JsonValue {…}`. **Blocked at the ABI** — `cJSON` is
  a *public struct*; callers read `item->type`/`item->valuestring`. Internal-only.

### 2. Hand-monomorphized C → Rust generics
- **`generic_foreach` (B02)** — three copies of one container (`array_int_*`,
  `array_double_*`, `array_item_t_*`, + `list_*`). → one generic `struct Array<T>`.
  **Viable internally**: `extern "C"` can't be generic, but the concrete fns can
  become thin wrappers over one generic core. (`void*`-erased cc_*/stb_ds are the
  ABI-level version — `void*` is C's generics; `Vec<T>` where the API allows.)

### 3. Function-pointer dispatch → `enum` + `match` (or trait/closure)
- **`inreftree` (B02)** — `typedef int (*OperationFunc)(…)`, `add_op`/`divide_op`/
  `modulo_op`, `get_operation_func(op)` = `switch(op){…}`. → `enum Op{…}` + `match`,
  or a trait. **Viable** (internal dispatch).
- **cc_\* comparator/allocator fn-ptrs** (`int (*cmp)(…)`, `void* (*mem_alloc)(…)`)
  → `Ord` bound (already folded into the `BinaryHeap`/`BTreeSet` recoveries); the
  `mem_alloc`/`mem_free` injection can be *deleted* in favor of the global allocator.

### 4. Manual `*_free` / `*_destroy` → `Drop` (RAII)
- Ubiquitous: `mem_free` (117 sites), `cc_array_destroy` (17), `arraylist_free`/
  `tree_destroy` (10 each), `job_queue_free`, `task_free`, `scheduler_free`, … all
  called explicitly incl. in `goto cleanup` paths. → `impl Drop` deletes the manual
  `free()` *and* the error-cleanup boilerplate. **Viable internally**: extern
  `xxx_free(ptr)` stays but becomes a thin `drop(Box::from_raw(ptr))`.

### 5. Error codes → `Result`
- **`enum cc_stat` (B03)** — `CC_OK`, `CC_ERR_ALLOC`, `CC_ERR_OUT_OF_RANGE`,
  `CC_ERR_KEY_NOT_FOUND`, `CC_ITER_END`, … returned by **711 functions**. →
  `Result<T, CcError>` / `Option`. **ABI-constrained** (extern fn returns the int),
  but internal logic can use `Result` + `?` and convert at the boundary — huge win.
- **`errno` (B01)** — `007_errno_pow`, `023/025_struct_and_errno`, `008_long_run`.

### 6. Type-punning unions → safe conversions  (cleanest win — no ABI impact)
- **`042_float_union` (B01)** — float↔int `union` for bit reinterpretation →
  `f32::to_bits()` / `f32::from_bits()`. Purely internal, zero ABI concern.
- **`034–039 cast_to_char_ptr*` (B01)** — `char*` type-punning → `to_ne_bytes`/`from_ne_bytes`.

### 7. Manual refcounting → `Rc` / `Arc`
- `mutable_duplication_dag` (B02), `binomial_heap`/`_lib` (B03), `chibicc` (B03). →
  `Rc<T>`, eliminating manual inc/dec/free.

### 8. Nullable `*mut T` → `Option`
- **984** `return NULL` / `== NULL` sites corpus-wide. → `Option<T>` /
  `Option<Box<T>>`. Internal (ABI returns raw ptr; convert at the boundary).

## Viability picture (sorted by usability under the ABI + hidden-runner constraints)

| Abstraction | Best fit | Why viable / blocked |
|---|---|---|
| Type-punning → `to_bits` | **any** (042_float_union) | purely internal, no ABI, no observability risk — easiest win |
| Error codes → `Result` (internal) | **any** (cc_stat ×711) | internal + convert at boundary; ABI int-return preserved |
| RAII / `Drop` (internal) | **any** (100+ free sites) | extern `free` becomes thin; internal ownership gets RAII |
| Tagged union → `enum` | **executables** (chibicc AST) | works when the type is internal; blocked for public structs (cJSON) |
| Generics | **internal core** (generic_foreach) | ABI can't be generic; one generic core + concrete wrappers |
| fn-ptr → `enum`/trait | internal dispatch (inreftree) | fine internally |
| Nullable → `Option` | internal | ABI returns raw ptr; convert at boundary |
| `Rc`/`Arc` | internal ownership | fine internally |

## Recommended order to extend the pipeline
- **Highest value / lowest risk:** (a) type-punning unions → `to_bits` (trivial,
  local); (b) internal `Result`/`Option` error handling (711 `cc_stat` fns);
  (c) `Drop`/RAII (100+ sites).
- **Most impressive:** chibicc's AST → `enum` (an executable where the tagged
  union is fully internal) — the `enum_pattern_matching` track.

Each new pattern needs: (1) an identify-prompt recognition rule (the way
`bytebuf → Vec<u8>` was added at `IDENTIFY_VERSION=6`), and (2) a `KIND_TARGET`
entry if it maps to a named target. The gate remains the backstop.

---

# Implementation plan: tagged-union → Rust `enum` (pattern #1)

## Why it's a different kind of transform than container recovery
Container recovery swaps **one field's backing** in **one struct** and keeps the
type + `extern "C"` ABI — a local, one-file change. Enum recovery replaces the
**type itself**:

| | Container (today) | Enum (new) |
|---|---|---|
| Change | one field `*mut T` → `Vec<T>` | the whole type: `kind`+payload struct → `enum` |
| Construction | unchanged | every `new_node(ND_ADD,…)` → `Node::Add(…)` |
| Reads | unchanged | every `switch(x->kind){…}` → `match x {…}` |
| Scope | 1 file | **whole crate** (cross-cutting type migration) |

So it does not fit the stage's "one candidate file, change only this structure"
model — it is a program-wide type migration.

## Viability (grounded in the corpus)
- ✅ **Executable, internal type → chibicc.** `Node`/`Token`/`Type` never cross an
  FFI boundary; only stdout is observed, so the gate can verify. The clean target.
- ◑ **`void*`+tag dispatch → circle_collide_lib / aabb_lib (B02).**
  `c2Collided(const void *A, const void *B, C2_TYPE typeB)` + `switch(typeB)`. The
  tag crosses the lib ABI, so the signature stays fixed and the enum is internal to
  the dispatch body — smaller payoff, but a tiny, cheap warm-up (one `lib.c`,
  3 variants: CIRCLE/AABB/CAPSULE).
- ✗ **Public tagged-union struct → cJSON (B02).** Callers read `item->type` → ABI-
  blocked (same Contract wall as the containers).

## chibicc specifics
- 3 tagged unions: `NodeKind` (**48 variants**, `struct Node`), `TokenKind`
  (`struct Token`), `TypeKind` (`struct Type`).
- Construction: `new_node(kind, tok)` / `new_binary` / `new_unary` in `parse.c`.
  Dispatch: `switch(node->kind){ case ND_… }` in `codegen.c` (and type.c).
- Scale: ~8,633 LOC across ~9 files; `Node` ripples through tokenize → parse →
  type → codegen.

## Stage changes
- **identify** — recognize a struct with a `kind`/`type` enum-discriminant field +
  variant-specific payload dispatched by `switch(x->kind)` / `if (x->kind==…)`.
  Must DECLINE public-struct tagged unions (extend the Contract disqualifier).
- **KIND_TARGET** — `enum` is not a named std type; add `tagged_union → "enum"`
  with its own transform prompt (define enum + variants, replace constructors,
  convert switch/if-chains to `match`).
- **transform** — multi-file ⇒ **agentic-only** (claude/opencode); the single-shot
  `llm` backend (one file) cannot do it. Relax "change only this file" for this kind.
- **gate** — unchanged; the cando2 oracle (stdout for chibicc) is the backstop.

## Risks
- Scale/completion (8.6K LOC, 48 variants) → agent may partially convert → fail-open
  to `crat`. Mitigate by scoping one union at a time (TokenKind/TypeKind before Node).
- Whole-crate edits trip the quality/"wandering" metrics — expected for this kind.
- Oracle only catches what the test inputs exercise.

## Staging
- **Phase 0 — warm-up:** `circle_collide_lib`/`aabb_lib` (`void*`+tag → `match`),
  validates the identify rule + agentic transform + gate for pennies.
- **Phase 1 — chibicc, one union:** `TokenKind`/`TypeKind` before `Node`.
- **Phase 2 — generalize:** identify rule + `KIND_TARGET` + prompt, wire
  `tagged_union` into the config, run gated.

## Validation so far (Phase 0 + Phase 1)

- **Phase 0 — hand-conversion (B02 circle_collide_lib).** `void*`+`C2_TYPE`
  dispatch -> `enum Shape` + `match`: compiles, and byte-identical behavior vs
  the crat baseline over ~4,851 inputs (FNV match). Public `extern "C"` entry
  unchanged.
- **Recognition (identify v8).** v7 false-declined chibicc's `Node`: C2Rust
  marks every field/fn `pub`, which the CONTRACT rule read as a public contract.
  v8 judges tagged-union viability by `extern "C"` exposure (not `pub`) and names
  the "fat struct" AST idiom. node-only chibicc flipped `[]` (v7) -> `tagged_union`
  on `Node` (v8).
- **Surfacing (identify v9).** Report *every* distinct candidate, not a single
  best. Full chibicc now surfaces a `tagged_union` -- but the hashmap slot-state
  one, NOT the large 48-variant `Node` AST, which stays out-ranked by easier
  targets. OPEN LEVER: reliably surface the harder target (the "nudge").
- **Transform (opencode / openai·gpt-5.6, `Node` forced).** Whole-crate migration
  -> 48-variant data-carrying `enum Node` (shared fields in `NodeBase`, a
  tag->variant constructor), ~1,478 lines across codegen/parse/type, 39 `match`
  sites. Compiles.
- **Behavior (chibicc).** Built crat vs enum binaries and compiled chibicc's
  **41** test programs through each -> **41/41 identical** asm/stdout/rc. The
  migration is behavior-preserving.
- **Caveats.** The enum keeps `*mut Node` children (a tagged enum over the
  existing pointer tree, not a `Box` ownership rewrite); `Node` is not yet
  auto-surfaced (forced for the test); behavior verified empirically, not proven.

