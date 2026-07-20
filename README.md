# PROCTOR Orchestration Framework

Shared infrastructure for the PROCTOR C-to-Rust translation pipeline:
a configurable pipeline orchestrator, vendor-agnostic LLM API, usage
tracker, prompt library, and code-context retrieval.

- Design and milestones: `plan_docs/orchestration_framework_implementation_plan.md`
- Requirements docs: `plan_docs/`

Pipeline stages are standalone programs in their own repositories,
pinned as git submodules under `stages/`, and invoked through a JSON
envelope contract (`docs/stage-contract.md`, from M1).

## Development

```bash
uv sync --dev
uv run pytest          # unit tests (fake stages, no toolchains)
uv run pytest -m e2e   # CRAT smoke test (needs rustup; builds CRAT once)
```

The legacy translation scripts and container live on the `master`
branch (`docker build -t proctor:june2026 .` there).
