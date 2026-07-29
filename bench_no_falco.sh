#!/usr/bin/env bash
# Like bench.sh, but verifies against the NEWER TRACTOR corpus using its own
# orchestrator (tools/test_runner --no-falco) — i.e. the newer cando2, rustc
# 1.94.1, and B03 — instead of the vendored direct harness. Two halves:
#
#   1. TRANSLATE each case IN the framework container (default config
#      c2rust -> crat -> abstraction_recovery; override with CONFIG=...), then
#   2. VERIFY each translation against the newer corpus at HOST level
#      (nix + docker, Falco-free) via proctor.testing.no_falco_bench.
#
# The newer orchestrator spawns a container per vector, so step 2 must run at
# host level; file-change vectors are skipped (Falco-only). See
# plan_docs/falco_integration_notes.md.
#
# The verbose build/harness output goes to a log; only a per-case summary
# (like bench.sh) is printed. Each run gets its own results dir under out/
# holding the log, JUnit, and JSON. Override the log path with a *.log arg.
#
#   ./fetch_corpus.sh --no-falco                     # once: fetch the newer corpus
#   ./bench_no_falco.sh B03_organic                  # whole suite
#   ./bench_no_falco.sh B03_organic run.log          # whole suite, custom log path
#   ./bench_no_falco.sh B01_synthetic 001_helloworld # one case (name is a regex)
#   JOBS=8 ./bench_no_falco.sh B02_organic
#
# Requires: docker (framework image proctor-framework:dev), nix, and uv.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SUITE="${1:?usage: ./bench_no_falco.sh <suite> [case] [logfile.log]}"
shift

CASE=""
LOG=""
for arg in "$@"; do
  case "$arg" in
    --*) echo "unknown flag: $arg" >&2; exit 1 ;;
    *.log|*/*) LOG="$arg" ;;   # a log path (ends in .log or contains a slash)
    *) CASE="$arg" ;;          # a case name/regex
  esac
done

CORPUS="$ROOT/tractor-test-corpus-newer/Test-Corpus"
if [ ! -d "$CORPUS/Public-Tests/$SUITE" ]; then
  echo "no such suite: $SUITE at $CORPUS" >&2
  echo "  fetch the newer corpus first: ./fetch_corpus.sh --no-falco" >&2
  exit 1
fi

MATCH=()
[ -n "$CASE" ] && MATCH=(--match "$CASE")

# nix on PATH (single-user install); host-level docker needs a mountable TMPDIR
if ! command -v nix >/dev/null 2>&1 && [ -f "$HOME/.nix-profile/etc/profile.d/nix.sh" ]; then
  # shellcheck disable=SC1091
  . "$HOME/.nix-profile/etc/profile.d/nix.sh"
fi
export TMPDIR="${TMPDIR:-$HOME/.cache/nofalco-tmp}"
mkdir -p "$TMPDIR"
command -v nix    >/dev/null || { echo "error: nix not found (needed for tools/test_runner)" >&2; exit 1; }
command -v docker >/dev/null || { echo "error: docker not found" >&2; exit 1; }

mkdir -p "$ROOT/out" && chmod 777 "$ROOT/out"

# One host-owned results dir per run — holds the log, JUnit, and JSON. Made
# up front (with its own timestamp) so the log can live here from the start;
# the translation half's bench-* dir, created by the container, stays
# separate (we only read translations from it).
RESULTS="$ROOT/out/bench_no_falco-$SUITE-$(date +%Y%m%dT%H%M%S)"
mkdir -p "$RESULTS"
LOG="${LOG:-$RESULTS/bench_no_falco.log}"
mkdir -p "$(dirname "$LOG")"
: > "$LOG"
echo "results: $RESULTS"
echo "log:     $LOG"

# --- 1. translate the suite in the framework container -----------------------
# Pipeline config (override with CONFIG=... ). The default runs the full
# component pipeline incl. abstraction_recovery (LLM); the image ships the
# claude CLI and we forward ANTHROPIC_API_KEY below. Use CONFIG=configs/bench.toml
# for a plain c2rust -> crat translation with no LLM.
CONFIG="${CONFIG:-configs/c2rust_crat_absrec.toml}"
TARGET="$SUITE"
[ -n "$CASE" ] && TARGET="$SUITE/$CASE"
echo ">> translating $TARGET  [$(basename "$CONFIG" .toml)] ..."
docker run --rm \
  -e ANTHROPIC_API_KEY \
  -v "$CORPUS:/corpus:ro" \
  -v "$ROOT/out:/out" \
  -v "$ROOT/configs:/home/proctor/proctor/configs:ro" \
  -v "$ROOT/proctor:/home/proctor/proctor/proctor:ro" \
  proctor-framework:dev \
  bench -c "$CONFIG" \
  --corpus "/corpus/Public-Tests/$SUITE" --name "$SUITE" \
  "${MATCH[@]}" \
  --set run.output_dir=/out \
  --set bench.layout.c_project=. \
  --jobs "${JOBS:-16}" >>"$LOG" 2>&1

# newest translation dir for this suite (created by the container). It holds
# the per-case, per-stage outputs (<case>/stages/NN-<stage>/out/rust) — the
# same layout bench.sh produces. It's container-owned, so we can't write our
# log/JUnit/JSON into it; link it into the results dir so everything's
# reachable from one place, as results/translations.
#
# NOTE: those linked files are owned by the container's `proctor` user, so
# they are READ-ONLY to the host (same as bench.sh's out/bench-* output). The
# symlink is a view, not a copy — to edit a translation, copy it out first:
#   cp -r <results>/translations/<case> ~/edit-<case>
BENCH_DIR="$(ls -dt "$ROOT"/out/bench-"$SUITE"-* 2>/dev/null | head -1 || true)"
[ -n "$BENCH_DIR" ] || { echo "error: translation produced no bench dir; see $LOG" >&2; exit 1; }
ln -sfn "$BENCH_DIR" "$RESULTS/translations"

# --- 2. verify each translation against the newer corpus, Falco-free --------
echo ">> verifying against the newer corpus (--no-falco) ..."
uv run python -m proctor.testing.no_falco_bench \
  --bench-dir "$BENCH_DIR" \
  --corpus "$CORPUS" \
  --suite "$SUITE" \
  "${MATCH[@]}" \
  --junit-out "$RESULTS/no_falco.xml" \
  --log-file "$LOG"
echo "translations: $RESULTS/translations  (-> $(basename "$BENCH_DIR"); read-only, copy to edit)"
