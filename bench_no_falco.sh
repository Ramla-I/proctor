#!/usr/bin/env bash
# Like bench.sh, but verifies against the NEWER TRACTOR corpus using its own
# orchestrator (tools/test_runner --no-falco) — i.e. the newer cando2, rustc
# 1.94.1, and B03 — instead of the vendored direct harness. Two halves:
#
#   1. TRANSLATE each case (c2rust -> crat) IN the framework container,
#      reusing configs/bench.toml (translation only), then
#   2. VERIFY each translation against the newer corpus at HOST level
#      (nix + docker, Falco-free) via proctor.testing.no_falco_bench.
#
# The newer orchestrator spawns a container per vector, so step 2 must run at
# host level; file-change vectors are skipped (Falco-only). See
# plan_docs/falco_integration_notes.md.
#
#   ./fetch_corpus.sh --no-falco            # once: fetch the newer corpus
#   ./bench_no_falco.sh B03_organic            # whole suite
#   ./bench_no_falco.sh B01_synthetic 001_helloworld   # one case (name is a regex)
#   JOBS=8 ./bench_no_falco.sh B02_organic
#
# Requires: docker (framework image proctor-framework:dev), nix, and uv.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SUITE="${1:?usage: ./bench_no_falco.sh <suite> [case]   e.g. B03_organic array_list}"
shift

CASE=""
for arg in "$@"; do
  case "$arg" in
    --*) echo "unknown flag: $arg" >&2; exit 1 ;;
    *) CASE="$arg" ;;  # a case name/regex
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

# --- 1. translate the suite in the framework container (no vectors) ---------
echo ">> translating $SUITE (c2rust -> crat) in the framework container"
docker run --rm \
  -v "$CORPUS:/corpus:ro" \
  -v "$ROOT/out:/out" \
  -v "$ROOT/configs:/home/proctor/proctor/configs:ro" \
  -v "$ROOT/proctor:/home/proctor/proctor/proctor:ro" \
  proctor-framework:dev \
  bench -c configs/bench.toml \
  --corpus "/corpus/Public-Tests/$SUITE" --name "$SUITE" \
  "${MATCH[@]}" \
  --set run.output_dir=/out \
  --jobs "${JOBS:-16}"

# newest bench dir for this suite (created by the container, so it may be
# owned by the container's user — we only read translations from it).
BENCH_DIR="$(ls -dt "$ROOT"/out/bench-"$SUITE"-* 2>/dev/null | head -1 || true)"
[ -n "$BENCH_DIR" ] || { echo "error: no bench output dir produced under out/" >&2; exit 1; }
echo ">> translations at $BENCH_DIR"

# Host-owned results dir (the host-level harness writes the JUnit here; it
# can't write into the container-owned bench dir).
RESULTS="$ROOT/out/nofalco-$(basename "$BENCH_DIR")"
mkdir -p "$RESULTS"

# --- 2. verify each translation against the newer corpus, Falco-free --------
echo ">> verifying with tools/test_runner --no-falco (host level, TMPDIR=$TMPDIR)"
uv run python -m proctor.testing.no_falco_bench \
  --bench-dir "$BENCH_DIR" \
  --corpus "$CORPUS" \
  --suite "$SUITE" \
  "${MATCH[@]}" \
  --junit-out "$RESULTS/no_falco.xml"
