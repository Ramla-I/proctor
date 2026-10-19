# Abstraction recovery — wrap-up plan

Goal: finish `abstraction_recovery` across the five abstraction families, with a
**modular** design (pick which abstractions to look for), a **static analyzer**
per family feeding the LLM, and **easy backend swap** (opencode agentic ↔ `llm`
single-shot loop). Grounded in the corpus scan in
[`future_abstraction_recovery_patterns.md`](future_abstraction_recovery_patterns.md)
and the hybrid approach in
[`hybrid_symbolic_neural_c_to_rust_translation.md`](hybrid_symbolic_neural_c_to_rust_translation.md).

**Decisions (agreed):** sequence **refactor-first** (Phase 0 before new families);
depth is **prompt rules + a static analyzer per family** (agentic does the rest;
add a *dynamic* analyzer only where the LLM proves unreliable, as we did for
chibicc's `Node`).

## The five families ← the 8 scanned patterns

| Family | Status | Scan pattern(s) | Corpus evidence |
|---|---|---|---|
| 1. Data structures | ✅ done | containers | array_list, cc_array, heaps, hashset |
| 2. Enums | 🟡 branch `07fc8dc` | #1 tagged-union→`enum`, #3 fn-ptr dispatch | chibicc (48-variant `Node`), circle_collide/aabb (`void*`+tag); cJSON blocked (public struct) |
| 3. Error handling | ⬜ | #5 codes→`Result`, #8 null→`Option` | cc_stat ×711 fns, 984 `NULL` sites |
| 4. Generics | ⬜ | #2 hand-monomorphized | generic_foreach (int/double/item_t), `void*` cc_* |
| 5. RAII / Drop | ⬜ | #4 `*_free`→`Drop`, #7 refcount→`Rc` | 100+ free sites; binomial_heap/chibicc refcount |
| *(warm-up)* type-punning | ⬜ | #6 union→`to_bits` | 042_float_union (trivial, zero-ABI) |

Two hard constraints from the scan hold for every family:
1. **`extern "C"` is the wall** — public signatures/structs are fixed; recoveries
   are *internal* (executables, or logic behind a preserved boundary).
2. **identify can't see the test runner** — the sealed oracle (the gate) is the
   only verifier; anything changing observable state fails it.

## Pillar 1 — Modular registry

Replace the monolithic identify prompt + `kind`-dispatch with an abstraction-module
registry. One module per family, backend-agnostic:

```python
class Abstraction:
    name: str                          # containers|enums|result|generics|raii|typepun
    kinds: frozenset[str]              # candidate kinds it owns (KIND_TARGET derived from modules)
    whole_crate: bool                  # type-level migration (needs agentic) vs local (single-shot ok)
    def identify_fragment() -> str     # recognition + DECLINE rules spliced into the shared prompt
    def transform_prompt(cand) -> str
    def analyzers() -> list[Analyzer]  # static (and later dynamic) hint producers; may be empty
```

- `config.abstractions = ["containers","enums","result"]` → the shared identify
  prompt is **assembled from only the active modules' fragments**; transform
  dispatches to the owning module; symbolic analyzers run only for active modules.
- `common.KIND_TARGET` / `WHOLE_CRATE_KINDS` are **derived** from the registry.
- No behavior change for containers/enums — they become the first two modules.

**Files (submodule `stages/abstraction-recovery/`):** new `abstractions/` package
(`base.py` + `containers.py`, `enums.py`, `result.py`, `generics.py`, `raii.py`,
`typepun.py`); `common.py` assembles the prompt + maps from the registry; `main.py`
runs active analyzers → merges hints → identify → dispatch transform by module.
Backends (`claude`/`llm`/`opencode`) untouched.

## Pillar 2 — A static analyzer per family (feeds the LLM)

Generalize the proven pattern (`tagged_union_finder` → injected so the LLM can't
miss it): each module's analyzer emits **structured JSON hints** injected into
identify (deterministic candidates) and transform (a spec). Reusable substrate:
syn AST passes (`tools/*_finder`, `measure_unsafety`), **crat's rustc `void_finder`**
(MIR void* typing), the **`rustc_private` analyzers** from discipline_repair
(ownership/aliasing), and the sealed oracle as the dynamic harness.

| Family | Static analyzer (build) → hint | Dynamic (only if needed) |
|---|---|---|
| Enums | `tagged_union_finder` + `void_ptr_finder` (have); **variant-shape inference** — per `case K:`/`if(x->kind==K)` collect fields touched → `K(fields…)` + ripple map | instrument crat binary under vectors, log `(kind, fields)` tuples → confirm shapes / dead variants |
| Result/Option | **sentinel dataflow** — fns returning the error enum/`NULL`, callers branching on `==CC_OK`/`==NULL`; propagation graph → where `?` replaces checks; detect `goto cleanup` | error-path coverage from vectors |
| Generics | **clone / anti-unification** — normalize syn ASTs (erase concrete types), cluster near-duplicate fn/struct families → monomorph set + type param | — (static suffices) |
| RAII/Drop | **alloc↔free pairing** + dominator "freed once on every path" (incl. goto); rustc MIR drop/move | **malloc/free trace** under vectors → single-free / no-leak / no-UAF before emitting `Drop` (safety-critical) |
| Type-punning | syn: `union` bit-reinterpret / scalar pointer-casts → `to_bits`/`from_bits` | — |

Analyzers live as `tools/<name>` Rust binaries (JSON CLI) like the existing ones;
Python modules are thin callers. Degrade gracefully (fail-open) if a tool is
unbuilt or a file won't parse.

## Pillar 3 — Backend swap (agentic ↔ single-shot)

Already one config line (`backend = "opencode"` ↔ `"llm"`). Keep the
`identify/transform/repair/refine` interface stable so modules are backend-agnostic.
Constraint: **whole-crate migrations (enums; cross-cutting Result/generics) need the
agentic backend**; local ones (type-punning, per-fn Result, containers) are
single-shot-feasible. `whole_crate=True` lets the `llm` backend auto-decline (as it
already does for `tagged_union`), so reverting to the single-shot loop to test limits
stays trivial and safe.

## Phased plan (refactor-first)

- **Phase 0 — Modular refactor.** Build the registry; re-home containers + enums as
  modules (no behavior change); land the two ready branches (enum `07fc8dc`,
  telemetry `d9deba6`). Add `config.abstractions`. Exit: existing batch reproduces
  identically with `abstractions=["containers","enums"]`.
- **Phase 1 — Type-punning.** Trivial, zero-ABI; proves the module interface **and**
  the single-shot `llm` path end-to-end. Static analyzer: union/pointer-cast finder.
- **Phase 2 — Error handling (Result/Option).** Highest count (cc_stat ×711, 984
  NULLs). Build the sentinel-dataflow analyzer; transform internal `Result`+`?`,
  convert at the boundary.
- **Phase 3 — RAII/Drop.** 100+ sites. Static alloc↔free pairing; add the malloc/free
  **dynamic trace** here (Drop safety warrants it).
- **Phase 4 — Generics.** Clone-detection analyzer; one generic core + concrete
  `extern "C"` wrappers.

Each phase: (1) module (identify fragment + transform prompt + declines),
(2) its static analyzer (`tools/…`), (3) tests (stub backend + tool; a fixture
case), (4) a live validation run (agentic, then an `llm`-backend spot-check where
feasible). The **gate stays the only correctness authority** throughout; fail-open.

## Config & routing

```toml
[stages.abstraction_recovery.config]
backend = "opencode"      # or "llm" to test limits
abstractions = ["containers","enums","result","generics","raii","typepun"]
max_candidates = 1
```

Routing unchanged: submodule (`stages/abstraction-recovery/…`, `abstractions/`) →
Yale-PROCTOR; parent (`tools/…`, configs, `plan_docs/…`) → Ramla-I fork.

## Open items folded in

- Merge the enum track (`07fc8dc`) + telemetry fix (`d9deba6`) in Phase 0.
- `cc_hashset_lib` **facade** (recovery raised unsafe) → the `recovery_metrics`
  quality gate (`feedback_metrics` branch) is the planned detector; wire it as a
  non-gating panel first, consider gating later.
- `void_ptr_finder` libc noise filter (`_IO_FILE`); revisit "route A" (reuse crat's
  rustc `void_finder`) for the enum/void* static analyzer.
