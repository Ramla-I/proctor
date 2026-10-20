# Abstraction recovery — wrap-up plan

Goal: finish `abstraction_recovery` across the families that **need an LLM**, with a
**modular** design (pick which abstractions to look for), a **static analyzer** per
family *feeding* the LLM, and an **easy backend swap** (opencode agentic ↔ `llm`
single-shot loop). Grounded in the corpus scan in
[`future_abstraction_recovery_patterns.md`](future_abstraction_recovery_patterns.md)
and the hybrid approach in
[`hybrid_symbolic_neural_c_to_rust_translation.md`](hybrid_symbolic_neural_c_to_rust_translation.md).

## Charter — what belongs in this stage

**`abstraction_recovery` handles only transforms that need an LLM's semantic
judgment.** A transform a deterministic rule-based pass can do *correctly* belongs
in **CRAT**, not here. Concretely:

- **Static analysis in this stage is detection-only** — it helps the LLM *find and
  understand* a candidate (like the chibicc `Node` finder), and never performs the
  rewrite.
- **A static *transform* is, by definition, CRAT's job** — e.g. type-punning (see
  *Handed to CRAT* below).
- So the registry has one recoverer flavor: **LLM-transform**. There is deliberately
  no "static-transform" module here.

Two hard constraints from the scan also hold for every family:
1. **`extern "C"` is the wall** — public signatures/structs are fixed; recoveries are
   *internal* (executables, or logic behind a preserved boundary).
2. **identify can't see the test runner** — the sealed oracle (the gate) is the only
   verifier; anything changing observable state fails it.

## The families (all genuinely LLM-needed)

| Family | Status | Scan pattern(s) | Why it needs an LLM (not a CRAT pass) |
|---|---|---|---|
| 1. Data structures | ✅ done | containers | *recognize* the hand-rolled structure, remap its API, judge Ownership/Contract/Gap |
| 2. Enums | 🟡 in progress | #1 tagged-union→`enum`, #3 fn-ptr dispatch | infer per-variant payloads; whole-crate type migration |
| 3. Error handling | ⬜ | #5 codes→`Result`, #8 null→`Option` | thread `?` through call chains, pick the success value, convert goto-cleanup + boundary |
| 4. RAII / Drop | ⬜ | #4 `*_free`→`Drop`, #7 refcount→`Rc` | ownership/lifetime judgment — who frees what, exactly once, aliasing |
| 5. Generics | ⬜ | #2 hand-monomorphized | reconcile *non-identical* near-clones + choose bounds while keeping the ABI wrappers |

Corpus evidence: chibicc (48-variant `Node`), circle_collide/aabb (`void*`+tag);
cc_stat ×711 fns, 984 `NULL` sites; 100+ `*_free` sites (mem_free ×117, …);
generic_foreach (int/double/item_t). cJSON's tagged union is **blocked** (public
struct crosses the ABI).

## Handed to CRAT (not this stage)

These are deterministic rewrites — a rule-based CRAT pass does them correctly
without semantic judgment, so they are **out of scope** for `abstraction_recovery`:

- **Type-punning (#6)** — `union { float; uint32_t }` or `*(uint32_t*)&x` →
  `f32::to_bits()` / `from_bits()`; a single-scalar `char*` reinterpret →
  `to_ne_bytes()` / `from_ne_bytes()`. Local, zero-ABI, mechanically detectable; fits
  beside CRAT's existing `enum` / `unsafe` / `simpl` passes. Keep it **conservative**:
  only the write-field-A / read-field-B reinterpret idiom — leave real tagged unions
  (those are the *enums* family) and `char*` used for memcpy / strlen / byte iteration
  alone; `to_ne_bytes` preserves the original punning semantics (only use le/be if the
  code commits to a fixed endianness). Example: `042_float_union`, `034–039
  cast_to_char_ptr*` (B01).
- **Mechanical subsets of the LLM families:** merging *identical* monomorph clones
  (exact anti-unification) and a *single-site* `return NULL`→`Option` with no
  propagation are rule-based → CRAT. The semantic remainder (non-identical clones +
  bounds; `Option` threaded across callers) stays here.

Action: file type-punning as a **CRAT pass proposal** (separate track); this note
preserves the analysis.

## Pillar 1 — Modular registry  ✅ (Phase 0, landed)

One module per family, backend-agnostic; `common` assembles the identify prompt and
kind maps from the **active** modules (`config.abstractions`, default all):

```python
@dataclass
class Abstraction:
    name: str              # containers | enums | result | generics | raii
    kinds: tuple[str, ...] # candidate kinds it owns (KIND_TARGET derived from modules)
    kind_target: dict[str, str]
    kind_rows: str         # its rows of the shared identify kind-table
    detail: str            # its recognition + DECLINE paragraphs
    impact_note: str = ""
    whole_crate: bool = False   # type-level migration (agentic) vs local (single-shot ok)
```

- `config.abstractions = ["containers","enums","result"]` → identify prompt assembled
  from only those families; `main` drops candidates of inactive families and runs only
  active families' detection analyzers.
- `KIND_TARGET` / `WHOLE_CRATE_KINDS` / `IDENTIFY_PROMPT` are **derived** from the
  registry; default (all families) is content-identical to the old monolith
  (`IDENTIFY_VERSION 10→11`).

**Landed:** submodule `modular-abstractions` (`4b5d1f1`, Yale-PROCTOR) — `abstractions/`
package (`base` + `containers` + `enums`), backends call `common.identify_prompt(config,
limit)`, `main` honors `config.abstractions`; 32 tests pass. Parent `abstraction-wrapup`
(`56cc67c`, Ramla-I) — gitlink bump, finder tools, config `abstractions` key.

## Pillar 2 — A static analyzer per family (DETECTION only, feeds the LLM)

Generalize the proven pattern (`tagged_union_finder` → injected so the LLM can't miss
it): each family's analyzer emits **structured JSON hints** that go into identify
(deterministic candidates) and transform (a spec). It never rewrites — that's the
LLM's job here (and CRAT's for the mechanical cases). Reusable substrate: syn AST
passes (`tools/*_finder`, `measure_unsafety`), **crat's rustc `void_finder`** (MIR
void* typing), the **`rustc_private` analyzers** from discipline_repair
(ownership/aliasing), and the sealed oracle as the dynamic harness.

| Family | Static analyzer (detection) → hint | Dynamic (only if needed) |
|---|---|---|
| Enums | `tagged_union_finder` + `void_ptr_finder` (have); **variant-shape inference** — per `case K:`/`if(x->kind==K)` collect fields touched → `K(fields…)` + ripple map | instrument the crat binary under the vectors, log `(kind, fields)` tuples → confirm shapes / dead variants |
| Error handling | **sentinel dataflow** — fns returning the error enum/`NULL`, callers branching on `==CC_OK`/`==NULL`; propagation graph → where `?` replaces checks; detect `goto cleanup` | error-path coverage from the vectors |
| RAII/Drop | **alloc↔free pairing** + dominator "freed once on every path" (incl. goto); rustc MIR drop/move | **malloc/free trace** under the vectors → single-free / no-leak / no-UAF before emitting `Drop` (safety-critical) |
| Generics | **clone / anti-unification** — normalize syn ASTs (erase concrete types), cluster near-duplicate fn/struct families → monomorph set + type param | — (static suffices) |

Analyzers live as `tools/<name>` Rust binaries (JSON CLI) like the existing finders;
Python modules are thin callers. Degrade gracefully (fail-open) if a tool is unbuilt
or a file won't parse.

## Pillar 3 — Backend swap (agentic ↔ single-shot)

One config line (`backend = "opencode"` ↔ `"llm"`); the `identify/transform/repair/
refine` interface stays stable so families are backend-agnostic. **Whole-crate
migrations (enums; cross-cutting Result/generics) need the agentic backend**; local
ones (per-fn Result, containers) are single-shot-feasible. `whole_crate=True` makes the
`llm` backend auto-decline, so reverting to the single-shot loop to test limits stays
trivial and safe.

## Phased plan

- **Phase 0 — Modular registry.** ✅ landed (see Pillar 1). Still open: a live batch
  re-run with `abstractions=["containers","enums"]` to confirm identical recoveries
  (unit + stage-venv integration already verified).
- **Phase 1 — Error handling (Result/Option).** Highest corpus count (cc_stat ×711,
  984 NULLs). Build the sentinel-dataflow detection analyzer; LLM transform: internal
  `Result`+`?`, convert at the `extern "C"` boundary. Single-shot-feasible per-fn.
- **Phase 2 — RAII/Drop.** 100+ sites. Static alloc↔free pairing; add the malloc/free
  **dynamic trace** here (Drop safety warrants it). Agentic.
- **Phase 3 — Generics.** Clone-detection analyzer; LLM reconciles the *non-identical*
  clones into one generic core + concrete `extern "C"` wrappers (exact clones → CRAT).

(Enums is in progress — finish/merge alongside Phase 0.)

Each phase: (1) family module (identify fragment + transform prompt + declines),
(2) its **detection** analyzer (`tools/…`), (3) tests (stub backend + tool; a fixture
case), (4) a live validation run (agentic, then an `llm`-backend spot-check where
feasible). The **gate stays the only correctness authority** throughout; fail-open.

## Config & routing

```toml
[stages.abstraction_recovery.config]
backend = "opencode"      # or "llm" to test limits
abstractions = ["containers","enums","result","generics","raii"]  # type-punning is a CRAT pass, not here
max_candidates = 1
```

Routing: submodule (`stages/abstraction-recovery/…`, `abstractions/`) → Yale-PROCTOR;
parent (`tools/…`, configs, `plan_docs/…`) → Ramla-I fork.

## Open items

- Open PRs for the landed Phase 0: `modular-abstractions` → abstraction_recovery `main`;
  `abstraction-wrapup` → proctor `master`.
- File the **type-punning CRAT pass** proposal (out of this stage; analysis above).
- `cc_hashset_lib` **facade** (recovery raised unsafe) → the `recovery_metrics` quality
  gate (`feedback_metrics` branch) is the planned detector; wire it as a non-gating
  panel first, consider gating later.
- `void_ptr_finder` libc-noise filter (`_IO_FILE`); revisit "route A" (reuse crat's
  rustc `void_finder`) for the enum/void* detection analyzer.
