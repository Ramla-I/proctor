# Stages

Every entry here is either a **stage** (implements the envelope
contract directly, `docs/stage-contract.md`) or a **tool + adapter**
pair.

## Why adapters?

External tools like CRAT and c2rust predate the stage contract: they
have their own CLIs, build systems, and conventions (pass chains,
`config.toml`, rustc-driver env vars). Rather than forking them to
speak the envelope, each is pinned as a plain submodule and paired with
a thin adapter directory in this repo:

| Submodule (unmodified upstream) | Adapter (envelope shim) |
|---|---|
| `crat/` — Yale-PROCTOR/crat | `crat-adapter/` |
| `c2rust/` — Yale-PROCTOR/c2rust | `c2rust-adapter/` |

The adapter is what the pipeline config points at (`uses =
"stages/crat-adapter"`). It reads `stage_input.json`, builds the tool
once per pinned commit (cached; `warmup` pre-builds it), invokes it
with the right environment, and writes `stage_output.json`. This keeps
the upstream repos untouched — bumping a tool version is just moving
the submodule pin — while the orchestrator sees a perfectly ordinary
stage.

Stages written for this pipeline (e.g. `abstraction-recovery/`,
template: `example-stage/`) implement the contract natively and need
no adapter: their repo root carries `stage.toml` and the config's
`uses` points straight at the submodule.

Adapters are deliberately boring: stdlib-only Python, no framework
imports, so they run anywhere a `python3` exists.
