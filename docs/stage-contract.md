# Stage Contract

The interface between the proctor orchestrator and a pipeline stage.
This document is authoritative; the JSON Schemas in
`proctor/contracts/schemas/` are its machine-readable form, and
`stages/example-stage/` is a working template.

A stage is a standalone single-test-case program. It may be written in
any language, live in its own repository (pinned as a git submodule
under `stages/`), and use any internal machinery — its own LLM client,
an external agent framework, Claude Code — as long as it honors this
contract. Applying a pipeline to a whole corpus is the orchestrator's
job, never the stage's.

## Invocation

```
<exec...> --input /abs/path/stage_input.json --output /abs/path/stage_output.json
```

- `exec` comes from the stage's `stage.toml`. For Python stages the
  orchestrator runs it through `uv run --project <stage-dir>`, so the
  stage's own `pyproject.toml` dependencies are available.
- The working directory is the stage's root directory. Never resolve
  data paths against it — every data path arrives absolute in the
  envelope.
- Exit `0` with a schema-valid `stage_output.json` ⇒ success (or an
  explicit `skipped`).
- Nonzero exit, or exit `0` without a valid output file ⇒ stage failure.
- Timeouts are enforced by the orchestrator (`framework.timeout_s` is
  informational).

## Rules

1. **Never modify an input path.** Inputs are read-only; a later resume
   may reuse them.
2. **Create outputs at the given destinations.** `outputs.rust_project`
   is nonexistent or empty when the stage starts. Copy-then-modify is
   the expected shape.
3. **Report honestly.** `status: "failure"` requires a non-empty
   `error`; `success`/`skipped` require `error: null`.
4. **`skipped` means untouched.** The orchestrator forwards each input
   artifact as the corresponding output; don't copy anything.
5. **Ignore unknown fields** in `stage_input.json` (additive evolution);
   never emit a `schema_version` newer than the one you read.
6. **Scratch space** goes in `framework.workdir` (pre-created), not in
   the output tree.
7. **`proctor.toml`** (project manifest): read it from the input Rust
   project, write the updated version into your output project if your
   stage changes wrappers. Inspect existing wrapper entries before
   introducing another wrapper.

## `stage.toml`

Ships at the stage root:

```toml
id = "abstraction_recovery"          # [a-z0-9][a-z0-9_-]*
version = "0.1.0"
description = "Replace C-idiom data structures with Rust abstractions."

exec = ["python3", "main.py"]        # argv; --input/--output are appended

[requires]                            # required | optional | unused
c_project    = "optional"             # unlisted kinds default to "unused"
rust_project = "required"
test_package = "required"
rule_set     = "unused"

[produces]                            # only rust_project and rule_set
rust_project = true
rule_set     = false

[config]                              # optional docs; the stage validates
max_iterations = { type = "integer", default = 5 }
```

`proctor validate` walks the pipeline in order and rejects it if a
stage's `required` artifact is neither runner-provided (`[run] provides`
in the pipeline config; default `c_project` + `test_package`) nor
produced by an earlier stage.

## `stage_input.json`

```json
{
  "schema_version": 1,
  "run_id": "baseline-20260721-a1b2c3d",
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
  "config": { "max_iterations": 5 },
  "framework": {
    "llm": { "provider": "anthropic", "model": "claude-opus-4-8" },
    "usage_log":      "/runs/.../02-abstraction_recovery/usage.jsonl",
    "prompt_library": "/abs/proctor/prompts/templates",
    "workdir":        "/runs/.../02-abstraction_recovery/work",
    "budget":         { "max_usd": 5.0, "max_tokens": null },
    "timeout_s":      3600
  }
}
```

| Field | Meaning |
|---|---|
| `run_id`, `stage_id`, `stage_index` | Identity of this invocation within the run. |
| `item` | The test case being processed (or null for ad-hoc runs). |
| `inputs.*` | Absolute read-only paths; null = not provided. |
| `outputs.rust_project` | Where to create the transformed project. |
| `outputs.rule_set` | Where to write the updated rule set (local transformation only). |
| `outputs.artifacts_dir` | Pre-created directory for extra stage outputs (reports, dumps). |
| `config` | The stage's resolved config table, passed through opaquely. |
| `framework.*` | **Advisory.** Defaults for stages using the shared components. A self-contained stage may ignore all of it, but must still self-report usage in its output. |

## `stage_output.json`

```json
{
  "schema_version": 1,
  "status": "success",
  "stage_id": "abstraction_recovery",
  "stage_version": "0.1.0",
  "outputs": { "rust_project": "/runs/.../out/rust", "rule_set": null },
  "config_used": { "max_iterations": 5 },
  "models": [{ "provider": "anthropic", "model": "claude-opus-4-8" }],
  "usage": {
    "calls": 14, "input_tokens": 182304, "cached_input_tokens": 120000,
    "output_tokens": 21044, "reasoning_tokens": 0, "cost_usd": 1.42
  },
  "prompts": [{ "id": "wrapper_preserve_update", "version": 3 }],
  "metrics": { "iterations": 3, "build_ok": true, "tests_passed": true },
  "logs": ["stdout.log", "stderr.log"],
  "metadata": { },
  "error": null
}
```

| Field | Required | Meaning |
|---|---|---|
| `status` | yes | `success` \| `failure` \| `skipped`. |
| `stage_id` | yes | Must echo the input's `stage_id`. |
| `stage_version` | no | The stage's own version string. |
| `outputs` | on success | Where outputs were actually created (normally echoes the input's destinations). |
| `config_used` | yes | The config parameters that actually took effect. |
| `models` | when LLMs used | Every provider/model touched. |
| `usage` | when LLMs used | Aggregate token usage and cost. Stages writing per-call records to `framework.usage_log` still fill this in; on discrepancy the detailed log wins. |
| `prompts` | when applicable | Prompt ids + versions used, for reproducibility. |
| `metrics` | no | Flat stage-defined numbers/booleans, aggregated by `proctor report`. |
| `logs` | no | Log file names relative to `artifacts_dir`. |
| `metadata` | no | Arbitrary structured or textual stage-specific content. |
| `error` | on failure | Non-empty message; null otherwise. |

## Compatibility

`schema_version` is bumped only on breaking changes; additive fields
never bump it. The orchestrator refuses envelopes newer than it
understands; stages should do the same. Unknown fields are always
ignored, never an error.

## Reproducing a stage run by hand

Every run directory keeps the envelope pair. To re-run stage N of a
finished run exactly:

```bash
cd stages/<stage>/
uv run --project . <exec...> \
  --input  /runs/<run_id>/stages/<N>-<id>/stage_input.json \
  --output /tmp/replay_output.json
```

(Delete or redirect the output destinations first; the stage will
refuse to overwrite existing output.)
