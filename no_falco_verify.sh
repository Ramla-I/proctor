#!/usr/bin/env bash
# Verify a case against the newer TRACTOR corpus WITHOUT Falco, using the
# corpus's own orchestrator (tools/test_runner) with --no-falco.
#
# This runs at HOST level: tools/test_runner spawns a Docker container per
# vector, so it needs `nix` and `docker` on the host (it cannot run nested
# inside the framework container). It covers state / stdout / library-state
# vectors on B01/B02/B03; file-change vectors (those carrying
# file_changes.tar.gz) are skipped — that is the only Falco-only gap.
# See plan_docs/falco_integration_notes.md.
#
# Usage:
#   ./no_falco_verify.sh <case_rel> [translated_rust_dir]
#
#   <case_rel>              Case path under the corpus root, e.g.
#                          Public-Tests/B01_synthetic/001_helloworld
#   [translated_rust_dir]  A Cargo project (a stage's Rust output). If given,
#                          it is dropped into the case's translated_rust slot
#                          and verified with --rust. If omitted, the case's C
#                          reference is verified (a Falco-free harness smoke).
#
# Env:
#   NEWER_CORPUS   corpus root (default: ./tractor-test-corpus-newer/Test-Corpus)
#   TMPDIR         scratch for the per-vector Docker volumes; must be a path
#                  the host Docker can bind-mount. Defaults to a $HOME dir
#                  (snap-Docker cannot bind-mount /tmp).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
CASE_REL="${1:?usage: ./no_falco_verify.sh <case_rel> [translated_rust_dir]}"
TRANSLATED="${2:-}"

CORPUS="${NEWER_CORPUS:-$ROOT/tractor-test-corpus-newer/Test-Corpus}"
export TMPDIR="${TMPDIR:-$HOME/.cache/nofalco-tmp}"
mkdir -p "$TMPDIR"

# nix on PATH (single-user install)
if ! command -v nix >/dev/null 2>&1 && [ -f "$HOME/.nix-profile/etc/profile.d/nix.sh" ]; then
  # shellcheck disable=SC1091
  . "$HOME/.nix-profile/etc/profile.d/nix.sh"
fi
command -v nix    >/dev/null || { echo "error: nix not found (needed for tools/test_runner)"    >&2; exit 1; }
command -v docker >/dev/null || { echo "error: docker not found (tools/test_runner spawns containers)" >&2; exit 1; }

if [ ! -d "$CORPUS/tools/test_runner" ]; then
  echo "error: newer corpus not found at $CORPUS" >&2
  echo "       fetch it with: ./fetch_corpus.sh --no-falco" >&2
  exit 1
fi

RUST_ARGS=()
if [ -n "$TRANSLATED" ]; then
  [ -f "$TRANSLATED/Cargo.toml" ] || { echo "error: $TRANSLATED is not a Cargo project" >&2; exit 1; }
  SLOT="$CORPUS/$CASE_REL/translated_rust"
  echo "staging $TRANSLATED -> $SLOT"
  rm -rf "$SLOT"
  cp -a "$TRANSLATED" "$SLOT"
  RUST_ARGS=(--rust)
fi

JUNIT="$TMPDIR/no_falco_${CASE_REL//\//_}.xml"
echo "running tools/test_runner --no-falco on $CASE_REL (TMPDIR=$TMPDIR)"
set -x
nix run --extra-experimental-features "nix-command flakes" \
  "$CORPUS/tools/test_runner" -- \
  "${RUST_ARGS[@]}" --no-falco --keep-going \
  --root "$CORPUS" --subset "$CASE_REL" \
  --junit-xml "$JUNIT"
set +x
echo "JUnit: $JUNIT"
