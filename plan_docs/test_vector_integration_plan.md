# Test-Vector Integration Plan

> **Status (2026-07-23):** implemented on the `test-vectors` branch.
> T1 (converter + `make-tests` + conformance suite), T2 (bench
> synthesis + per-stage `gate_tests`), T4 (`test_generation` stage +
> `test_package` producible), and T3 (library vectors through the
> case's real cando harness crate, bundled + built at run time) are
> all done. §3's open question resolved: each `_lib` case ships a
> `runner/` harness crate using cando2's `harness!` macro; packages
> bundle it with the cando2 path dependency rewritten to the corpus
> checkout. One noted deviation: the package comparator is generated
> Python behind the `run_test.sh` shim rather than pure POSIX sh —
> exact-bytes/regex fidelity is not expressible in shell.

How TRACTOR test vectors become first-class verification inside the
pipeline. Companion to `orchestration_framework_implementation_plan.md`
(whose Future Work section anticipated this); interfaces follow
`proctor_pipeline_component_specification.md` §2.3 and §5.1.

## 1. Current State and Gap

- Corpus cases carry `test_vectors/*.json` (argv, expected stdout
  `pattern`, and — in richer cases — stdin, files, exit codes).
- The framework verifies pipeline outputs through the §2.3 test-package
  contract: `run_test.sh <test_data> <artifact>`, exit 0 = pass. The
  M6 runner and `[testing] after_each_stage` gating work today.
- **Nothing bridges the two.** The only existing test package
  (helloworld) is handwritten and checks less than the case's own
  vectors specify. Vector execution currently happens only in the
  legacy corpus harness
  (`Test-Corpus/deployment/scripts/github-actions/run_rust.sh`),
  outside the framework — no per-stage gating, no report joins.

## 2. Design: Vectors → Test Package Converter

One converter, three surfaces, added in this order.

### 2.1 The converter (core)

`proctor.testing.vectors`: given a case directory containing
`test_vectors/*.json`, emit a spec-§2.3 package:

```
<out>/
├── run_test.sh        # generated; iterates every vector
└── test_data/
    └── vectors/*.json # copied verbatim
```

The generated `run_test.sh` is self-contained POSIX shell (test
packages must run anywhere, including inside containers without
Python): for each vector, invoke the artifact with the vector's argv
and stdin, capture stdout/exit code, compare against expectations;
any mismatch prints the vector name and diff, exits nonzero.

**Fidelity rule: the corpus harness is the oracle.** Before writing the
comparator, read the TRACTOR harness source (master's `Test-Corpus`
submodule, `deployment/scripts/`) and mirror its semantics exactly —
in particular whether `stdout.pattern` is exact-match, regex, or
glob; how trailing newlines are treated; which vector fields exist
beyond argv/stdout (stdin, env, files, exit code, timeouts); and how
per-vector timeouts are enforced. A conformance check (same case run
through both harnesses, same verdict) belongs in the converter's
tests.

### 2.2 Surface 1: CLI helper (first)

```
proctor make-tests <case-dir> <out-package-dir>
```

One-off generation for development and for vendoring fixture packages.
Replaces the handwritten helloworld package with a generated one (the
e2e then verifies all three vectors, including the argv variants it
silently ignores today).

### 2.3 Surface 2: bench auto-synthesis

During `proctor bench` discovery: a case with `test_vectors/` but no
test package gets one synthesized into the case's run directory
(recorded in `run.json` as generated, with the converter version).
Every translated corpus case then flows through vector verification,
and `bench.json` + `proctor report` yield pass rates joined with cost —
the plan's accuracy-per-dollar metric at corpus scale.

Layout addition: `[bench.layout] test_vectors = "test_vectors"`
(relative to the case's C project by default).

### 2.4 Surface 3: a `test_generation` stage (later)

The spec's §5.1 component: C project in, test package out. Wrapping
the converter as a stage makes room for richer generators (LLM-
generated vectors, coverage-driven inputs) behind the same interface.
Requires the one anticipated contract extension:

- add `test_package` to `PRODUCIBLE_KINDS` in
  `proctor/contracts/stage_manifest.py`;
- orchestrator: thread a produced test package into downstream
  `inputs.test_package` (state update, same as rust_project);
- schema/docs updates. Additive — no `schema_version` bump.

## 3. Library Targets

Vectors as observed drive executables (argv/stdout). Library cases
(`target_kind = "library"`, cdylib artifact) are verified differently —
the harness presumably loads the `.so` and exercises `api_functions`.
**Open question to resolve from the harness source before 2.3 lands:**
what artifact do library-case vectors run against, and does the corpus
ship per-case driver programs? Until answered, the converter refuses
library cases loudly rather than generating a wrong package.

## 4. Per-Stage Gating Refinement

Vector packages sharpen the existing mid-pipeline gating problem: the
c2rust stage's output is a library crate even for executable cases, so
executing vectors against it fails spuriously (why
`translation_smoke.toml` keeps the gate off). With vectors integrated:

- add `[stages.<id>] gate_tests = true|false` overriding the global
  `[testing] after_each_stage` — off for `c2rust`, on from `crat`
  onward;
- every post-CRAT LLM stage then gets automatic vector regression
  checking, which is exactly the guardrail those stages need.

## 5. Milestones

| Step | Contents | Effort |
|---|---|---|
| T1 | Harness-semantics study + converter + conformance tests; `proctor make-tests`; regenerate the helloworld fixture package | ~1 day |
| T2 | bench auto-synthesis + `gate_tests` per-stage flag; corpus bench run producing pass-rate/cost report | ~1 day |
| T3 | Library-target answer implemented (per §3) | ~0.5–1 day |
| T4 | `test_generation` stage + `test_package` producible-kind extension | ~1 day |

T1/T2 need the corpus checked out (master submodule or the public
DARPA repo) for the harness source and for bench-scale cases.

## 6. Risks

| Risk | Mitigation |
|---|---|
| Comparator semantics drift from the TRACTOR harness | Harness is the oracle; conformance test runs both against the same case; converter version recorded in run.json |
| Vector fields exist that the fixture doesn't show (stdin, files, env) | Semantics study first (T1) surveys the full corpus for field usage before the comparator is written |
| Library cases mis-verified | Refuse loudly until §3 is answered |
| Generated shell portability | POSIX sh only, no bashisms; exercised in the framework container in CI-adjacent e2e |
