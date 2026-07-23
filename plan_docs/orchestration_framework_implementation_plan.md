# Orchestration Framework — Implementation Plan

Implementation plan for `orchestration_framework_plan.md`, consistent with the
interfaces in `proctor_pipeline_component_specification.md` and the pipeline
described in `hybrid_symbolic_neural_c_to_rust_translation.md`.

## 0. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Core language | Python 3.12+ | Provider SDKs are Python-first (cached-input and reasoning-token fields land there first); the core is glue that churns during experimentation; agent-SDK stages are Python-only. |
| Rust usage | Analysis tools only | `syn` forces Rust for the code index. Same applies later to the `#[proctor(N)]` label checker and anti-unification rule extractor. These are JSON-emitting binaries, not part of the orchestrator. |
| Stage model | Standalone program per stage, own git repo, pinned as a submodule under `stages/` | A stage is developed, tested, and released independently. The orchestrator never imports stage code. |
| Stage invocation | JSON envelope: `<exec> --input stage_input.json --output stage_output.json` | Two flags, forever. Stage config evolves without orchestrator changes. Language-agnostic; reproducible by hand. |
| Stage environments | One venv per stage, owned by `uv run --project <stage>` | Stages may pin conflicting SDK versions without breaking each other; the orchestrator writes no venv-management code — uv creates and caches per lockfile. |
| Config format | TOML | Consistent with `proctor.toml`, `Cargo.toml`, `rust-toolchain`. |

Process-per-stage costs ~50 ms against stage runtimes measured in minutes
(cargo builds, LLM loops). The "low runtime overhead" requirement is about not
paying serialization or scheduling costs *inside* a stage's hot loop — the
framework imposes none there.

## 1. Repository Layout

This branch holds only the framework. `aws-translate/`, `Dockerfile`,
`download.sh`, `scripts/`, `Test-Corpus/`, `PUBLIC-Test-Corpus/` stay on
`master` and are removed here (M0 — confirm before deleting).

```
proctor/
├── pyproject.toml                  # package `proctor`, CLI entrypoint
├── proctor/
│   ├── contracts/                  # ← the load-bearing module; everything else depends on it
│   │   ├── stage_io.py             # StageInput / StageOutput dataclasses + JSON (de)serialization
│   │   ├── manifest.py             # proctor.toml: target_kind, target_name, api_functions, wrappers
│   │   ├── artifacts.py            # CProject, RustProject, TestPackage, RuleSet path wrappers
│   │   └── schemas/*.json          # JSON Schema for stage_input / stage_output, versioned
│   ├── config/
│   │   ├── model.py                # typed config tree
│   │   ├── load.py                 # TOML load, multi-file overlay merge, --set overrides
│   │   └── resolve.py              # per-stage effective config (global → stage → CLI)
│   ├── orchestrator/
│   │   ├── run.py                  # sequencing, run dir, artifact wiring
│   │   ├── invoke.py               # subprocess via `uv run --project`, timeout
│   │   ├── checkpoint.py           # input-key hashing, resume
│   │   ├── events.py               # events.jsonl
│   │   └── record.py               # run.json provenance
│   ├── llm/
│   │   ├── types.py                # Request, Response, Usage, errors
│   │   ├── client.py               # retry, rate limit, truncation, tracker hook
│   │   └── providers/{anthropic,openai,replay}.py  # openai covers compat servers via base_url
│   ├── usage/
│   │   ├── tracker.py              # buffered JSONL sink
│   │   ├── pricing.py              # provider/model → $/Mtok, from config
│   │   └── report.py               # aggregation over run / stage / experiment / benchmark
│   ├── prompts/
│   │   ├── library.py              # id + version lookup, render, content hash
│   │   ├── lock.py                 # (deferred) prompts.lock version-bump enforcement
│   │   └── templates/*.md          # TOML frontmatter + body
│   ├── context/
│   │   ├── api.py                  # retrieve_context(strategy=, target=)
│   │   ├── index.py                # invokes proctor-rust-index, caches by tree hash
│   │   └── strategies/             # registry + built-ins
│   ├── testing/
│   │   └── runner.py               # build + run_test.sh contract (M6)
│   └── cli.py                      # proctor run|resume|bench|report|stages|validate|warmup
├── crates/
│   └── proctor-rust-index/         # syn-based item/dependency graph → JSON
├── stages/                         # git submodules, one per stage
│   ├── crat/                       # Yale-PROCTOR/crat submodule @ f598249 (symbolic passes)
│   ├── crat-adapter/               # in-repo shim: envelope ↔ crat pass chain (M2 smoke test)
│   ├── translation/                # C2Rust + CRAT plugins
│   ├── abstraction-recovery/
│   ├── discipline-repair/
│   └── local-transformation/
├── configs/
│   ├── default.toml
│   └── experiments/
├── docs/
│   ├── stage-contract.md           # ← published first; the contributor-facing document
│   ├── writing-a-stage.md
│   └── config-reference.md
└── tests/
    ├── fake_stages/                # noop, fail, slow, usage-reporting
    ├── cassettes/                  # recorded LLM responses for CI without keys
    └── e2e/
        ├── fixtures/001_helloworld/{c,c2rust}  # vendored TRACTOR case + its c2rust output
        └── crat_smoke.toml         # one-stage pipeline config for the CRAT smoke test
```

## 2. Stage Contract

The single most important deliverable. Published as `docs/stage-contract.md` in
M1 so contributors can build stages in parallel with the orchestrator.

### 2.1 Stage manifest — `stage.toml` in each stage repo root

```toml
id = "abstraction_recovery"
version = "0.1.0"
description = "Replace C-idiom data structures with Rust abstractions."

exec = ["python", "-m", "abstraction_recovery.cli"]   # argv prefix; --input/--output appended

[requires]                # orchestrator validates pipeline before running anything
c_project    = "optional" # required | optional | unused
rust_project = "required"
test_package = "required"
rule_set     = "unused"

[produces]
rust_project = true
rule_set     = false

[config]                  # optional documentation; the stage validates its own config
max_iterations = { type = "integer", default = 5, min = 1 }
abstractions   = { type = "array", item = "string", default = ["vec", "hashmap"] }
```

`requires`/`produces` let `proctor validate` reject an ill-formed pipeline
(local transformation before translation, a rule-set consumer with no producer
upstream) before any work starts.

### 2.2 Invocation

```
<exec...> --input /abs/.../stage_input.json --output /abs/.../stage_output.json
```

Envelopes are always written by the orchestrator, never by hand — one pair
per stage per test case. A stage is a single-case program; applying the
pipeline to a whole corpus is the batch driver's job (`proctor bench`, M7),
which fans out one pipeline run per case. This keeps checkpointing and
failure isolation per-case, parallelism orchestrator-side, and stages free
of job management. The files exist so any stage invocation can be
reproduced or debugged in isolation with two flags.

- All paths in the envelope are absolute. The orchestrator sets the working
  directory to the stage root; stages must not rely on the working directory
  for data paths.
- Exit 0 with a schema-valid `stage_output.json` ⇒ success.
- Nonzero exit, or exit 0 with missing/invalid output ⇒ stage failure.
- The stage must not modify any input path. It creates its output project at the
  given destination (per component spec §4 — copy-then-modify is expected).

### 2.3 `stage_input.json`

```json
{
  "schema_version": 1,
  "run_id": "baseline-20260720T174400-a1b2c3d",
  "stage_id": "abstraction_recovery",
  "stage_index": 2,
  "item": "Public-Tests/B01_synthetic/001_helloworld",

  "inputs": {
    "c_project":    "/runs/.../inputs/c",
    "rust_project": "/runs/.../stages/01-translation/out/rust",
    "test_package": "/runs/.../inputs/tests",
    "rule_set":     null
  },
  "outputs": {
    "rust_project":  "/runs/.../stages/02-abstraction_recovery/out/rust",
    "rule_set":      null,
    "artifacts_dir": "/runs/.../stages/02-abstraction_recovery/out/artifacts"
  },

  "config": { "max_iterations": 5, "abstractions": ["vec", "hashmap"] },

  "framework": {
    "llm": { "provider": "anthropic", "model": "claude-opus-4-8",
             "max_retries": 5, "context_overflow": "error" },
    "usage_log":      "/runs/.../stages/02-abstraction_recovery/usage.jsonl",
    "prompt_library": "/abs/proctor/prompts/templates",
    "workdir":        "/runs/.../stages/02-abstraction_recovery/work",
    "budget":         { "max_usd": 5.0, "max_tokens": null },
    "timeout_s":      3600
  }
}
```

`framework` is advisory. Per the plan's Integration Flexibility section a stage
may ignore it entirely — run its own LLM client, its own prompts, or shell out
to Claude Code — provided it reports the required information back in
`stage_output.json`.

### 2.4 `stage_output.json`

```json
{
  "schema_version": 1,
  "status": "success",
  "stage_id": "abstraction_recovery",
  "stage_version": "0.1.0",

  "outputs": { "rust_project": "/runs/.../out/rust", "rule_set": null },
  "config_used": { "max_iterations": 5, "abstractions": ["vec", "hashmap"] },

  "models": [{ "provider": "anthropic", "model": "claude-opus-4-8" }],
  "usage": {
    "calls": 14, "input_tokens": 182304, "cached_input_tokens": 120000,
    "output_tokens": 21044, "reasoning_tokens": 0, "cost_usd": 1.42
  },
  "prompts": [{ "id": "wrapper_preserve_update", "version": 3 }],

  "metrics": { "iterations": 3, "build_ok": true, "tests_passed": true,
               "abstractions_applied": 2 },
  "logs": ["stdout.log", "stderr.log"],
  "metadata": { },
  "error": null
}
```

- `metadata` is the free-form field from the plan — arbitrary structured or
  textual stage-specific content.
- `usage` is required only when the stage made LLM calls. Stages using
  `proctor.llm` get it for free; self-contained stages fill it in themselves.
  When a stage both writes `usage_log` and self-reports, the detailed log wins
  and a discrepancy is warned about.
- `status: "skipped"` means the stage decided it had nothing to do; the
  orchestrator forwards each declared input artifact (rust project, rule set)
  as the corresponding output.

### 2.5 Compatibility

`schema_version` is an integer bumped only on breaking change. The orchestrator
accepts any version it knows and refuses newer ones with a clear message.
Additive fields never bump it; stages must ignore unknown fields.

## 3. Pipeline Configuration

Keyed tables plus an explicit order list — this composes under overlays, which
arrays-of-tables do not.

```toml
# configs/default.toml
[run]
output_dir = "runs"
on_stage_failure = "stop"        # stop | continue
keep_intermediates = true

[pipeline]
order = ["translation", "abstraction_recovery", "discipline_repair", "local_transformation"]

[llm]                             # defaults for every stage
provider = "anthropic"
model = "claude-opus-4-8"
max_retries = 5
context_overflow = "error"        # error | truncate_head | truncate_middle
[llm.rate_limit]
requests_per_minute = 50
tokens_per_minute = 400000
[llm.pricing."anthropic/claude-opus-4-8"]   # $ per million tokens; filled per provider docs
input = 0.0
cached_input = 0.0
output = 0.0

[context]
default_strategy = "target_plus_types"

[testing]
after_each_stage = false          # M6

[stages.translation]
uses = "stages/translation"
enabled = true
[stages.translation.config]
crat_plugins = ["extern", "preprocess", "simpl"]

[stages.abstraction_recovery]
uses = "stages/abstraction-recovery"
enabled = true
timeout_s = 3600
[stages.abstraction_recovery.config]
max_iterations = 5
[stages.abstraction_recovery.llm]           # per-stage override
model = "claude-sonnet-5"

[stages.discipline_repair]
uses = "stages/discipline-repair"
[stages.local_transformation]
uses = "stages/local-transformation"
```

Note: the orchestration doc's default pipeline lists C2Rust and CRAT as
separate stages, while the component spec defines a single translation
component containing both. We follow the spec — one `translation` stage whose
CRAT plugin list is ordinary stage config. Splitting later is a new stage repo
plus an `order` change, with no framework impact.

Overlays and overrides:

```bash
proctor run -c configs/default.toml -c configs/experiments/sonnet.toml \
  --set stages.abstraction_recovery.config.max_iterations=8 \
  --input-c ./corpus/001_helloworld/c --tests ./corpus/001_helloworld/tests
```

Merge rules: tables deep-merge, scalars and arrays replace, later `-c` wins,
`--set` wins over all. An overlay disables a stage with
`[stages.x] enabled = false` or replaces `pipeline.order` wholesale. Every
experiment is therefore a small diff file, satisfying Configuration-Over-Code.

## 4. Run Directory & Reproducibility

```
runs/<run_id>/
├── run.toml                  # fully resolved effective config (not the inputs)
├── run.json                  # provenance
├── events.jsonl              # stage started/finished/skipped/failed, timings
├── usage.jsonl               # merged from per-stage logs at run end
├── inputs/{c,tests}          # recorded (copied or symlinked) run inputs
└── stages/
    └── 02-abstraction_recovery/
        ├── stage_input.json
        ├── stage_output.json
        ├── out/{rust,artifacts}
        ├── work/
        ├── usage.jsonl
        ├── stdout.log  stderr.log
        └── .checkpoint
```

`run.json` records: run id, wall times, framework git SHA + dirty flag, **each
stage submodule's git SHA**, resolved config hash, prompt ids+versions+content
hashes used, provider/model strings, tool versions (`rustc`, `cargo`, `c2rust`,
`crat`), and the host env subset that matters. This makes a run reproducible
from the superrepo alone.

Per-stage `usage.jsonl` files avoid concurrent-append hazards when the batch
driver runs many test cases in parallel; they are concatenated at run end.

### Checkpoint / resume

After each stage, `.checkpoint` stores a key:

```
hash(stage id, stage git SHA, resolved stage config hash,
     framework schema_version, upstream stage's checkpoint key)
```

A hash chain — no artifact-tree hashing. On `proctor resume <run_id>`, each
stage recomputes its key; a match with `status: "success"` reuses the existing
output directory, and the first mismatch invalidates that stage and everything
downstream. This assumes run-dir intermediates are never hand-edited; when you
do want to intervene, `proctor resume <run_id> --from <stage>` forces
re-execution from that stage regardless of key match.

## 5. Component Designs

### 5.1 Vendor-agnostic LLM API

```python
@dataclass(frozen=True)
class Request:
    messages: list[Message]
    system: str | None = None
    model: str | None = None              # overrides config
    max_tokens: int | None = None
    temperature: float | None = None
    tools: list[ToolDef] | None = None
    metadata: RequestMetadata = ...        # run_id, stage, item, prompt_id, prompt_version

@dataclass(frozen=True)
class Usage:
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int | None

@dataclass(frozen=True)
class Response:
    text: str
    tool_calls: list[ToolCall]
    finish_reason: Literal["stop", "length", "tool_use", "refusal", "error"]
    provider: str
    model: str
    latency_s: float
    usage: Usage
    raw: dict                              # provider-specific passthrough
```

- Providers (M3): `anthropic`, `openai`, `replay` (cassette playback for CI).
  The `openai` provider takes a `base_url`, which covers vLLM, Ollama, other
  local servers, and Gemini's OpenAI-compatible endpoint — three modules reach
  every provider class in the plan. A native `gemini` module is added only if
  compat-endpoint gaps (cached/reasoning token fields) start to matter.
- Normalization targets the fields the tracker needs: Anthropic's
  `cache_read_input_tokens` / `cache_creation_input_tokens` and OpenAI's
  `prompt_tokens_details.cached_tokens` / `completion_tokens_details.reasoning_tokens`
  both fold into `Usage`.
- Errors: `ContextLimitExceeded(needed, limit)`, `RateLimited`, `AuthError`,
  `ProviderError`, `BudgetExceeded`. Default `context_overflow = "error"` raises
  the structured error; truncation strategies are opt-in.
- Retry: exponential backoff with jitter on 429/5xx/timeout, honoring
  `Retry-After`. Rate limiting: token bucket per provider+model.
- Every call emits a usage record before returning, including failed attempts.

Provider modules are registered by name; adding one is a new file plus a
registry entry, with no change to callers.

### 5.2 Usage tracker

One JSONL record per call:

```json
{"ts":"2026-07-20T17:44:03Z","run_id":"...","stage":"abstraction_recovery",
 "item":"B01/001_helloworld","attempt":1,"provider":"anthropic",
 "model":"claude-opus-4-8","prompt_id":"wrapper_preserve_update","prompt_version":3,
 "prompt_hash":"sha256:...","input_tokens":12043,"cached_input_tokens":9000,
 "output_tokens":831,"reasoning_tokens":0,"latency_s":6.21,
 "finish_reason":"stop","cost_usd":0.0714,"error":null}
```

Records are appended synchronously, one line per call. A JSONL append is
microseconds against an LLM call measured in seconds — the plan's "no
synchronous metric persistence" concern targets framework overhead in the
stage execution path, not this — and synchronous writes mean a crashed or
killed stage loses no records. `proctor report` aggregates over any
set of run directories:

```bash
proctor report runs/baseline-*            --group-by stage,model
proctor report runs/ --experiment sonnet  --format csv
```

Answers the plan's target questions directly: tokens by stage, cost by
experiment, and — joined with the test results from M6 — accuracy per dollar.
Unknown model in the pricing table ⇒ `cost_usd: null` plus one warning, never a
silent zero.

### 5.3 Prompt library

`proctor/prompts/templates/<id>.md`:

```
+++
id = "wrapper_preserve_update"
version = 3
description = "Update a wrapper while preserving the public API signature."
variables = ["api_signature", "wrapper_body", "diagnostics"]
+++
You are updating a Rust wrapper function...
{{ api_signature }}
```

Jinja2 with `StrictUndefined` (missing variable ⇒ error, not silent empty).
`library.get(id)` returns the latest version; `library.get(id, version=2)` pins
one. The rendered-prompt SHA-256 is recorded on every usage record, so a run
remains interpretable even after a template changes.

Reproducibility comes from recording the rendered-prompt hash on every usage
record and template hashes in `run.json`. A `prompts.lock` CI check that
forces a version bump on content change is deferred — worth adding once
several people edit templates concurrently, not before.

The library is passed to stages as a *directory path*, so a stage in its own
venv can read templates without importing the framework.

### 5.4 Code context retrieval

**`crates/proctor-rust-index`** (Rust, `syn`): walks a crate and emits
`index.json` —

- *items*: fn, struct, enum, trait, impl, mod, const/static, type alias — each
  with crate-relative path, source file, byte span, and text;
- *edges*: calls, type references, impl-of, module containment, trait bounds.

Name resolution is best-effort and syntactic (`syn` has no type inference);
documented as such. The interface is designed so the backend can be swapped for
rust-analyzer or a rustc driver later without touching strategies.

**Python API** (matches the plan's example verbatim):

```python
bundle = retrieve_context(
    project,
    strategy="function_plus_one_hop",
    target="driver::tx::transmit",
    budget_tokens=8000,          # optional
)
bundle.render()                  # -> str for the prompt
```

`ContextBundle` is an ordered list of snippets, each carrying its path, kind,
source text, and the reason it was included — so prompts can be debugged.
Strategies register by decorator:

```python
@strategy("function_plus_one_hop")
def _(index: Index, target: ItemPath, cfg: Mapping) -> ContextBundle: ...
```

M5 ships two built-ins — `target_only` and `target_plus_types` — matching the
plan's "first implementation may support only a simple strategy."
`target_plus_types_one_hop` (the Intel-style variant), `enclosing_module`, and
`whole_submodule` are follow-ups; the registry makes each a single new function
with no changes to existing code. The index is cached per project by tree hash.

### 5.5 Test runner (M6, designed for now)

Implements the component spec §2.3 contract: `cargo build`, locate the artifact
by `target_kind`/`target_name` from `proctor.toml`, then
`run_test.sh <test_data> <artifact>`; exit 0 = pass.

```python
TestResult(build_ok: bool, passed: bool, exit_code: int,
           duration_s: float, stdout: str, stderr: str)
```

Available both as a library for stages (they must validate their own output
anyway, per the hybrid plan's iterate-until-tests-pass loops) and to the
orchestrator when `[testing] after_each_stage = true`, which records a result
per stage into `events.jsonl` and gates the pipeline. Test *generation* stays
out of scope; it slots in later as an ordinary stage producing a test package.

## 6. Containerized Execution

Eventually the orchestrator itself runs inside a container, the way the
current Dockerfile runs CRAT. The file-based, subprocess-per-stage design is
already the container-friendly shape — no daemons, no ports, no databases —
so this changes packaging, not architecture. Four provisions are baked in now
so containerization stays a Dockerfile problem later:

1. **Relocatable run dirs.** Internally (checkpoints, `run.json`, events) all
   paths are stored relative to the run root; absolute paths are materialized
   only when a `stage_input.json` is written at invocation time. A run started
   on the host can be inspected — or resumed — under a different mount point
   inside a container.
2. **One cache root.** Everything warm lives under a single directory —
   per-stage uv venvs, cargo caches, adapter builds (crat), context-index
   caches — default `~/.cache/proctor`, overridden by `PROCTOR_CACHE_DIR`.
   One volume mount (or one image layer) preserves all warm state.
3. **Secrets via environment only.** Provider keys are read exclusively from
   env vars (`ANTHROPIC_API_KEY`, ...), never from config files — composes
   with `docker run -e` and keeps keys out of run records.
4. **Image identity in provenance.** When `PROCTOR_IMAGE` is set (stamped at
   image build), `run.json` records it alongside the git SHAs.

`proctor warmup` resolves every stage venv, pre-builds adapters (crat), and
pre-fetches pinned toolchains. The framework Dockerfile is then: base deps →
`COPY` repo → `proctor warmup` → stamped image. The same verb speeds up fresh
host checkouts.

Two constraints worth stating:

- A stage must not itself require Docker — that would force socket-mounting
  or docker-in-docker once the orchestrator is containerized.
- If per-stage images are ever wanted, the contract already permits it: a
  stage's `exec` may be a `docker run` wrapper that mounts the run dir. The
  envelope doesn't change.

## 7. Milestones

**M0 — Branch reset & scaffolding.** Remove non-framework content from this
branch (confirm first; recoverable from `master`). `pyproject.toml`, package
skeleton, ruff + mypy + pytest, CI. *~0.5 day.*

**M1 — Contracts (highest priority, unblocks everyone).** `stage_io`,
`manifest`, `artifacts`, JSON Schemas, config loader/merger/resolver, plus
`docs/stage-contract.md` and `docs/writing-a-stage.md`. Ship a
`stages/example-stage` template repo and `proctor validate`. Publish and
announce before M2 is finished so stage authors can start. *~3 days.*

**M2 — Orchestrator.** Run directories, sequencing and artifact wiring, stage
invocation via `uv run --project` with timeout, events, run record,
checkpoint/resume, `proctor run|resume|stages`. Reference stages: `noop`,
`copy`, and a generic `external_command` stage. Unit tests use fake stages
only — no LLM, no cargo. Exit criterion: the CRAT smoke test (§8) passes —
one public TRACTOR case through the real CRAT stage imported as a submodule.
*~3 days, plus ~1 for the CRAT adapter and fixture.*

**M3 — LLM API + usage tracker.** Types, client with retry/rate-limit/truncation,
`anthropic` + `openai` (with `base_url` for compat servers) + `replay`
providers, JSONL tracker, pricing, `proctor report`. CI runs against
cassettes. *~3 days.*

**M4 — Prompt library.** Loader, versioning, Jinja2 strict rendering, first
templates (wrapper preservation/update, compile-error repair, test-failure
repair). *~1 day.*

**M5 — Context retrieval.** `proctor-rust-index` crate, Python index wrapper and
cache, strategy registry, `target_only` + `target_plus_types`. *~3 days.*

**M6 — Test integration.** Test-package runner, `after_each_stage` gating,
results joined into `proctor report` to give accuracy-per-dollar. *~2 days.*

**M7 — Batch driver.** `proctor bench -c cfg.toml --corpus <dir> --jobs N`:
runs the pipeline across a corpus in parallel, one run dir per test case,
aggregate report with per-stage pass rates and cost. Resume re-runs only
failed cases. Must decide a rule-set policy across cases (the one genuine
cross-case coupling — rules are learned across programs): `independent`
(clean experiments), `chained` (max learning, order-dependent, serializes
local transformation), or `merge-per-round`; default `independent`, policy
set in `[run]` config. *~2 days.*

**M8 — Container packaging.** Framework Dockerfile (base deps + toolchains +
`proctor warmup`), `PROCTOR_IMAGE` stamping into `run.json`, `docker run`
usage docs. The §6 provisions land earlier (they're part of M2/M3 code);
this milestone is only the image itself. *~1 day.*

M3–M5 are independent of each other and of stage development once M1 lands, so
they parallelize across contributors.

## 8. Testing Strategy for the Framework Itself

- **Fake stages** (`tests/fake_stages/`): noop, failing, timing-out,
  usage-reporting, output-corrupting. Cover sequencing, failure policy, resume,
  and validation without invoking a real toolchain.
- **CRAT smoke test (M2 exit criterion)** — the first real-stage end-to-end
  test. `stages/crat` is the `Yale-PROCTOR/crat` submodule pinned at the
  commit the Dockerfile uses today (`f598249`); `stages/crat-adapter/` is a
  thin stdlib-only shim that reads the envelope, builds crat once per
  submodule SHA (`deps_crate` then `cargo build --release`, toolchain from
  crat's own `rust-toolchain`), sets the `DIR`/`SYSROOT`/`LD_LIBRARY_PATH`
  env crat's rustc-driver needs, runs the pass chain (`expand → … → bin`,
  list configurable per stage config), and writes `stage_output.json` with
  per-pass timings and no usage block (symbolic stage). Input is a vendored
  fixture: the c2rust output of `Public-Tests/B01_synthetic/001_helloworld`
  (small cargo project + `config.toml`), generated once with the existing
  container and checked in — so the test needs no clang/cmake/c2rust.
  `pytest -m e2e` runs `proctor run -c tests/e2e/crat_smoke.toml`, then
  asserts the stage output is schema-valid, the resulting project passes
  `cargo build`, and a second `proctor resume` reuses the checkpoint. Note:
  the c2rust-stage fixture is a *library* crate even for executable test
  cases (`autobins = false`; `config.toml`'s `[bin] name` instructs CRAT's
  `bin` pass, which introduces the executable) — so the build assertion
  checks `libdriver.*` when the pass chain stops early, and the `driver`
  binary only after the `bin` pass. The fixture and CRAT pin share the same
  nightly (`nightly-2025-06-23`), so the test needs exactly one toolchain. Opt-in
  (needs rustup and crat's pinned nightly with `rustc-dev`; the first crat
  build takes minutes, cached after); default CI keeps to fake stages.
- **Cassette-based LLM tests**: the `replay` provider plays recorded responses;
  CI needs no API keys. A separate opt-in job exercises live providers.
- **Contract conformance suite**: `proctor check-stage <path>` runs any stage
  repo against synthetic inputs and asserts envelope conformance. Stage repos
  run it in their own CI, which keeps the ecosystem honest as the contract
  evolves.
- **Golden config tests**: resolved-config snapshots so merge/override semantics
  don't drift.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Contract churn breaks in-flight stage repos | `schema_version` + additive-only changes; `check-stage` conformance suite in each stage's CI; contract frozen at M1 and changed only by explicit bump. |
| `syn`-only resolution too imprecise for one-hop strategies | Backend is behind the `Index` interface; escalate to rust-analyzer/rustc driver without touching strategies or callers. |
| Self-contained stages under-report usage | Required fields in `stage_output.json`; `proctor report` flags stages with LLM models declared but no usage and shows coverage in the report. |
| Per-stage venvs slow to build | `uv run` resolves once per lockfile and reuses its cache; warm starts are milliseconds. |
| Large intermediate Rust projects fill disk | `keep_intermediates = false` prunes all but the final and failed stages; checkpoint keys are stored independently of the trees they describe. |

## 10. Open Questions

1. CRAT-as-a-stage is settled by the M2 smoke test (submodule + adapter).
   Still open: does the C2Rust half become a proper stage next, or does the
   existing `scripts/orchestrate.py` translation path get wrapped by
   `external_command` until someone extracts it? The adapter pattern from the
   smoke test is the template either way.
2. Should executables and libraries take different default pipelines
   (`target_kind` from `proctor.toml`), or is one order sufficient with stages
   self-skipping?
3. Rule-set file format — unspecified in the component spec, and only local
   transformation reads and writes it. Framework treats it as an opaque path
   until M7; confirm that's acceptable.
4. Budget enforcement granularity: per stage, per run, or per corpus batch. The
   plan is silent; per-stage with a run-level ceiling is proposed.
