# `proctor` — the framework package

What lives where:

| Module | Role |
|---|---|
| `contracts/` | The load-bearing module: `stage_input.json`/`stage_output.json` envelopes (`stage_io`), the `proctor.toml` project manifest (`manifest`), artifact path wrappers (`artifacts`), the `stage.toml` stage manifest (`stage_manifest`), and the versioned JSON Schemas (`schemas/`). Everything else depends on this; it depends on nothing. |
| `config/` | TOML loading with overlay merge and `--set` overrides (`load`), the typed config tree (`model`), per-stage effective settings (`resolve`). |
| `orchestrator/` | The pipeline runner: run directories and artifact threading (`run`), stage subprocess invocation via `uv run --project` with process-group timeouts (`invoke`), hash-chain checkpoints for resume (`checkpoint`), `events.jsonl` (`events`), `run.json` provenance (`record`), pre-run pipeline validation (`validate`), the corpus batch driver (`bench`). |
| `llm/` | Vendor-agnostic LLM API: types and errors (`types`), the retry/rate-limit/truncation client (`client`), providers behind a registry (`providers/` — anthropic, openai incl. compat servers via `base_url`, cassette replay). |
| `usage/` | Per-call JSONL usage records (`tracker`), $/Mtok pricing (`pricing`), `proctor report` aggregation (`report`). |
| `prompts/` | Versioned prompt templates with strict Jinja2 rendering and content hashes (`library`); the templates themselves in `templates/`. |
| `context/` | Code-context retrieval: the index built by `crates/proctor-rust-index` (`index`), the `retrieve_context(strategy=, target=)` API (`api`), pluggable strategies (`strategies/`). |
| `testing/` | The test-package contract runner — `cargo build` + `run_test.sh <test_data> <artifact>` (`runner`); also powers `[testing] after_each_stage` gating. |
| `cli.py` | `proctor validate|stages|run|resume|bench|report|warmup`. |

Design rules of the package: stages never import it by requirement
(the envelope is the contract; these are convenience libraries), all
run-time state lives in run directories rather than process memory,
and anything a stage subprocess needs is passed through the envelope,
never ambient globals.

Docs: `../docs/stage-contract.md` (the contract),
`../docs/writing-a-stage.md` (stage authoring),
`../plan_docs/orchestration_framework_implementation_plan.md` (design).
