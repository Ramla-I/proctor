# PROCTOR Orchestration Framework

Shared infrastructure for the PROCTOR C-to-Rust translation pipeline:
a configurable pipeline orchestrator, vendor-agnostic LLM API, usage
tracker, prompt library, and code-context retrieval.

Pipeline stages are standalone programs in their own repositories,
pinned as git submodules under `stages/`, invoked through a JSON
envelope contract (`docs/stage-contract.md`). Current real stages:
`c2rust` and `crat` — together the spec's Translation component,
turning a TRACTOR C project into a tested unsafe-Rust project carrying
`proctor.toml`.

## Setup

```bash
git clone <this repo> && cd proctor
git submodule update --init stages/crat stages/c2rust
./fetch_corpus.sh                # fetch the TRACTOR test corpus (DARPA
                                     # access required); not committed here
uv sync
uv run proctor warmup -c tests/e2e/translation_smoke.toml   # pre-build stages
```

The TRACTOR test corpus is **not** vendored/submoduled — `./fetch_corpus.sh`
clones it at the exact pinned commit (add `--with-aws` for the
`aws-translate` packaging tool used below).

Host toolchain requirements (or use Docker below, which has them all):
`rustup`, `cmake`, `make`/`ninja`, and CRAT's build deps — see
`tests/e2e/README.md` for the libclang/z3 setup with and without sudo,
and for how the c2rust transpiler is located.

Run the following command to generate test case bundles under
`tractor-test-corpus/bundles`:

```bash
python3 \
  tractor-test-corpus/aws-translate/scripts/package/package.py \
  -o "$(pwd)/tractor-test-corpus/bundles" \
  --root tractor-test-corpus/Test-Corpus
```

You only need to do this when you want to pass a bundle to `c2rust-adapter`.

## Running the pipeline

One test case, C source to tested Rust:

```bash
uv run proctor run -c tests/e2e/translation_smoke.toml \
  --input-c tests/e2e/fixtures/001_helloworld/c \
  --tests   tests/e2e/fixtures/001_helloworld/tests
```

Every run creates a self-contained directory under `runs/<run_id>/`
with the resolved config (`run.toml`), provenance (`run.json`), event
log, per-stage envelopes, outputs, and checkpoints.

Useful verbs:

```bash
uv run proctor validate -c <cfg>                 # check pipeline wiring before running
uv run proctor stages   -c <cfg>                 # list configured stages
uv run proctor resume   runs/<run_id>            # reuse checkpoints, redo the rest
uv run proctor resume   runs/<run_id> --from crat  # force re-run from a stage
uv run proctor bench    -c <cfg> --corpus <dir> --jobs 8   # whole corpus, one run dir per case
uv run proctor report   runs/ --group-by stage,model       # LLM token/cost aggregation
```

### Verifying translations against the TRACTOR vectors

`bench` can check each case's translated Rust against the corpus's own
test vectors, using TRACTOR's authoritative `runtests.rust` harness
(vendored under `tools/tractor_runtests/`) — we drive their runner, we
don't reimplement it. Enable it in config:

```toml
[bench]
verify_vectors = true      # verify the final Rust output per case
verify_all_stages = false  # true: verify every stage's output (per-stage delta)
```

Each corpus case must carry a `test_vectors/` directory (the standard
TRACTOR layout). Results land in `bench.json` (`vectors_ok` per case and
a top-level pass count) and print inline as `vectors 3/3 (crat)`. Needs
only `cargo`/`cmake`/`ninja` on `PATH` — no Docker or Falco. File-change
vectors need Falco (the one remaining gap); B03 and the newer-corpus
library cases can be verified Falco-free with the newer harness — see
"Verifying without Falco" below and `plan_docs/falco_integration_notes.md`.

### Running on the TRACTOR test corpus

Two scripts at the repo root wrap the whole flow — fetch the corpus and
bench a suite, no manual `docker run`:

```bash
./fetch_corpus.sh                         # once: clone the corpus at the pinned commit
docker build -t proctor-framework:dev .   # once: build the framework image

./bench.sh B02_organic                    # translate + vector-verify a suite, in-container
./bench.sh B01_synthetic --all            # per-stage delta (c2rust vs crat)
JOBS=8 ./bench.sh B02_synthetic           # tune parallelism
```

`./bench.sh <suite> [case] [--all]` runs c2rust → crat over the cases in
`Public-Tests/<suite>` (an optional case name/regex runs just one; `--all`
verifies every stage, not just the final). It mounts the full
`Test-Corpus` (library cases need its `tools/cando2`) plus the repo's
`configs/` and `proctor/` (so config/code edits apply without an image
rebuild), and checks each translation against its `test_vectors/` with the
vendored harness. It prints per-case results and `N/M cases ok`; full
detail lands in `out/bench-<suite>-<timestamp>/bench.json`.

```bash
./bench.sh B02_organic arr_del_lib --all   # one case, every stage
./bench_report.sh B02_organic              # per-stage breakdown of the latest run
```

`./bench_report.sh [suite | bench-dir]` prints the per-stage vector
breakdown from a run's `bench.json` (defaults to the latest under `out/`).

Suites available at the pinned corpus: `B01_synthetic`, `B01_organic`,
`B02_synthetic`, `B02_organic`. B03 lives in the newer corpus; verify it
(and any case) Falco-free with the newer harness — see the next section.

### Verifying without Falco (newer corpus, incl. B03)

The newer TRACTOR corpus ships its own orchestrator (`tools/test_runner`),
which natively matches that era — cando2 (`lib_fn!`, rustc 1.94.1), the
`_cando_librunner` naming, and **B03**. Yale's `no-falco` branch of
`Test-Corpus` adds a `--no-falco` flag so it runs state / stdout /
library-state vectors **without Falco**; only file-change vectors (those
carrying `file_changes.tar.gz`) are skipped.

Unlike the vendored harness above, this orchestrator spawns a Docker
container per vector, so it runs at **host level** and needs `nix` + `docker`
on the host (it can't run nested inside the framework container):

```bash
./fetch_corpus.sh --no-falco                      # once: Yale corpus @ no-falco
                                                  #   -> tractor-test-corpus-newer/

# bench a suite: like ./bench.sh, but the newer cando2 / B03 harness.
# Translates each case (c2rust -> crat) in the container, then verifies each
# translation against the newer corpus at host level (Falco-free):
./bench_no_falco.sh B03_organic                      # whole suite
./bench_no_falco.sh B01_synthetic 001_helloworld     # one case (name is a regex)
./bench_report.sh                                    # per-case vectors + final-stage unsafe + idiomaticity
./bench_report.sh --per-stage                        # a per-stage table per case (c2rust -> crat -> ...)
./bench_report.sh --no-idiomaticity                  # skip the per-case clippy build (vectors + unsafe)
./bench_report.sh --no-metrics                       # vectors only (fast)

# add the LLM abstraction_recovery stage (needs the claude CLI in the image,
# which the Dockerfile installs, plus ANTHROPIC_API_KEY in your env):
CONFIG=configs/c2rust_crat_absrec.toml ./bench_no_falco.sh B03_organic array_list

# --gate: accept abstraction_recovery only if it doesn't regress the vectors,
# else fall back to crat (recovery keeps its safety/idiomaticity wins but can
# never cost correctness). Recommended whenever abstraction_recovery is on:
CONFIG=configs/c2rust_crat_absrec.toml ./bench_no_falco.sh B03_organic --gate

# or verify a single case directly:
./no_falco_verify.sh Public-Tests/B03_organic/array_list          # C reference
./no_falco_verify.sh Public-Tests/B01_synthetic/001_helloworld <translated_rust>
```

Each run is one self-contained dir owned by you, `out/bench-<suite>-<pid>-<stamp>/`:
the per-case translations plus `bench.json` (did it translate), `verify.json` +
`verify.xml` (the `--no-falco` vector results), and `run.log`. Runs on different
cases translate in **parallel**; only the verify step serializes (TRACTOR's
harness isn't concurrency-safe). Default config is a plain c2rust → crat
translation (`configs/bench.toml`); `CONFIG=configs/c2rust_crat_absrec.toml`
adds the LLM `abstraction_recovery` stage.

**The `--gate` flag** makes `abstraction_recovery` safe to run: its only
self-check is `cargo build`, which can't catch a transform that compiles but
changes observable behavior (or the `extern "C"` ABI), so an over-eager
recovery can *regress* the vectors. With `--gate`, each case the final stage
didn't pass cleanly is re-verified against the previous stage (crat), and the
result that doesn't regress is kept — so recovery keeps its safety/idiomaticity
gains where it's correct and falls back to crat where it isn't. `verify.json`
records the `accepted_stage` per case and how many `fell_back`.

`bench_no_falco.sh` is the batch equivalent of `bench.sh` on the newer corpus;
`proctor.testing.vector_harness.run_vectors_no_falco()` is the single-case
programmatic entry point. See `plan_docs/falco_integration_notes.md` for the
design, the exact `Test-Corpus` changes, and current verification results.

### Measuring `unsafe` + idiomaticity

Alongside correctness (vectors), score each translation's **safety** (`unsafe`
usage) and **idiomaticity** (clippy lint density) — per stage, so you can see
what each stage reduces. Both drive vendored authoritative tools (not
reimplemented): DARPA's `measure_unsafety` (a `syn` scorer) and Yale's
`measure_idiomaticity` (`cargo clippy`), under `tools/`.

```bash
./metrics.sh <rust_project>                 # one crate (Cargo.toml inside)
./metrics.sh out/bench-.../array_list       # per stage (a run dir with stages/)
./metrics.sh <crate> --no-idiomaticity      # unsafe only (no build/clippy)
./metrics.sh <crate> --complexity           # + cognitive-complexity histogram
./metrics.sh <crate> --json metrics.json    # also write JSON
```

Unsafe is **source-only** (no toolchain — works even if the crate doesn't
build); idiomaticity runs **clippy**, so it needs the crate to build with
clippy for its toolchain (the tool runs `rustup component add clippy` in the
crate). A run dir prints a per-stage table with the reduction vs the first
stage (c2rust → crat → …). See
`plan_docs/{unsafe,idiomaticity}_evaluation_plan.md` for the tool survey.

For a whole `--no-falco` run, `./bench_report.sh <suite>` folds these in
directly: one row per case with its vectors **and** the final-stage `unsafe`
score + clippy count. Add `--per-stage` for a table per case showing **every**
stage (c2rust → crat → abstraction_recovery) with the reduction vs the first
stage, plus suite totals — the suite-wide version of what `metrics.sh` prints
for a single case. (`--no-idiomaticity` skips the clippy build; `--no-metrics`
is vectors only. Vectors come from `verify.json`, which records the final
translation only.)

Experiments are config overlays — later files win, `--set` wins over all:

```bash
uv run proctor run -c base.toml -c experiments/sonnet.toml \
  --set stages.crat.config.final_pass=simpl ...
```

## Running in Docker

```bash
docker build -t proctor-framework:dev .
docker run --rm proctor-framework:dev run -c tests/e2e/translation_smoke.toml \
  --input-c tests/e2e/fixtures/001_helloworld/c \
  --tests   tests/e2e/fixtures/001_helloworld/tests
```

The image bakes in every toolchain (LLVM, rustup, uv) and pre-builds
the stages via `proctor warmup`, so containerized runs need zero host
setup. Mount a volume over `/home/proctor/proctor/runs` to keep run
directories. `PROCTOR_IMAGE` is stamped into each run's `run.json`.

## Adding a stage

A stage is a standalone program in its own repo — any language, any
internal machinery (own LLM client, agent SDK, Claude Code) — that
reads a `stage_input.json` and writes a `stage_output.json`:

1. Start from the template: copy `stages/example-stage/` (or the
   richer scaffold in the `abstraction_recovery` repo). Declare what
   you consume/produce in `stage.toml`; pin your own dependencies in
   your `pyproject.toml` (each stage gets an isolated venv).
2. Pin it here: `git submodule add <url> stages/<name>`.
3. Wire it into a config:

   ```toml
   [pipeline]
   order = ["c2rust", "crat", "<name>"]
   [stages.<name>]
   uses = "stages/<name>"
   [stages.<name>.config]
   your_option = 3
   ```

4. Check the wiring: `uv run proctor validate -c <cfg>` — it rejects
   the pipeline if a required artifact has no producer.

Full walkthrough: `docs/writing-a-stage.md`; field-by-field envelope
reference: `docs/stage-contract.md`.

## Development

```bash
uv run pytest          # unit tests (fake stages, no toolchains needed)
uv run pytest -m e2e   # real-stage tests (see tests/e2e/README.md)
uv run ruff check . && uv run ruff format . && uv run mypy proctor
```

- Design and milestones: `plan_docs/orchestration_framework_implementation_plan.md`
- Stage authors start at `docs/writing-a-stage.md` + `stages/example-stage/`
- The envelope contract: `docs/stage-contract.md`

The legacy translation scripts and container live on the `master`
branch (`docker build -t proctor:june2026 .` there).

## Using the LLM API

Stages that use the shared LLM client configure everything — provider,
model, API-key env var, reasoning effort, pricing — through the `[llm]`
config table; see **`proctor/llm/README.md`** for the full guide.
`stages/example-llm-stage/` is a complete working example:

```bash
export ANTHROPIC_API_KEY=...    # keys always come from env, never config
uv run proctor run -c configs/llm_example.toml \
  --input-rust tests/e2e/fixtures/001_helloworld/c2rust
uv run proctor report runs/ --group-by stage,model   # tokens + cost
```
