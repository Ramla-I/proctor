# Writing a Stage

How to build a new pipeline stage against the
[stage contract](stage-contract.md). The fastest start is copying
`stages/example-stage/`.

## 1. Repository layout

A stage is its own repository (or a directory in this repo for
adapters), pinned as a git submodule under `stages/`:

```
my-stage/
├── stage.toml          # the stage manifest — required
├── pyproject.toml      # your dependencies; uv-managed
├── main.py             # or any entry point stage.toml's exec names
└── ...
```

Your `pyproject.toml` is yours alone: pin any SDKs you need. The
orchestrator runs your stage through `uv run --project <your-dir>`,
so your dependencies never conflict with another stage's.

## 2. Declare the manifest

`stage.toml` tells the orchestrator how to invoke you and what you
consume/produce — see the contract doc for the full format. Be precise
with `[requires]`: `proctor validate` uses it to catch broken pipelines
before anything runs, and reviewers use it to understand your stage.

## 3. Implement the loop

1. Parse `--input` / `--output`.
2. Read `stage_input.json`; take your config from `config`.
3. Copy the input Rust project to `outputs.rust_project`; modify the
   copy. Never touch inputs. Scratch space: `framework.workdir`.
4. Build/test your output (a stage owns its own validation — the
   contract for transformation stages is "Cargo-buildable and
   test-passing", running `run_test.sh <test_data> <artifact>`).
5. Update `proctor.toml` in your output if you changed wrappers.
6. Write `stage_output.json` — including `models`, `usage`, and
   `prompts` if you used LLMs. Wrap your main in a try/except that
   writes a `failure` output; a stack trace with no envelope is a
   contract violation.

Python stages may use the framework's helpers instead of hand-rolling
JSON (add `proctor` to your dependencies):

```python
from proctor.contracts import StageInput, StageOutput, OutputDestinations

stage_input = StageInput.read(input_path)
...
StageOutput(
    status="success",
    stage_id=stage_input.stage_id,
    outputs=OutputDestinations(rust_project=stage_input.outputs.rust_project),
    config_used=stage_input.config,
).write(output_path)
```

Using the shared LLM client / prompt library / usage tracker (M3/M4) is
optional — `framework.*` in the envelope tells you where they point.
A stage that drives Claude Code or another agent internally is fine; it
just self-reports `usage` in its output.

## 4. Test it

- Hand-build an envelope (copy one from any run dir or the contract
  doc) and run your stage directly — no orchestrator needed.
- `proctor validate -c your-pipeline.toml` checks your manifest wiring.
- The repo's fixture `tests/e2e/fixtures/001_helloworld/c2rust/` is a
  small real input Rust project for smoke tests.

## 5. Wire it into a pipeline

```toml
[pipeline]
order = ["translation", "my_stage", "local_transformation"]

[stages.my_stage]
uses = "stages/my-stage"
[stages.my_stage.config]
my_option = 3
```

Then pin your repo: `git submodule add <url> stages/my-stage`.
