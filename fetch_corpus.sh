#!/usr/bin/env bash
# Fetch the TRACTOR test corpus at an exact commit. The corpus is a large,
# access-restricted repo, so it is NOT committed/submoduled here — pull it on
# demand with this script. Your git/gh credentials are used.
#
# Two corpora, for the two vector-harness engines:
#
#   ./fetch_corpus.sh              # default: DARPA @ 0319ab0, the pre-Falco
#                                  #   direct-harness era that matches the
#                                  #   vendored tools/tractor_runtests.
#                                  #   -> tractor-test-corpus/Test-Corpus
#   ./fetch_corpus.sh --with-aws   # also aws-translate (packaging)
#
#   ./fetch_corpus.sh --no-falco   # newer corpus + the --no-falco harness
#                                  #   (Yale-PROCTOR/Test-Corpus @ no-falco).
#                                  #   Latest DARPA upstream (B03, cando2,
#                                  #   rustc 1.94.1) plus tools/test_runner's
#                                  #   --no-falco flag. Runs at host level via
#                                  #   `nix run ./tools/test_runner`.
#                                  #   -> tractor-test-corpus-newer/Test-Corpus
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

# repo -> pinned commit.
TEST_CORPUS_REPO="https://github.com/DARPA-TRACTOR-Program/Test-Corpus.git"
TEST_CORPUS_COMMIT="0319ab0ae6fdcc5c354d2fbf7ee646049e5006b1"
AWS_TRANSLATE_REPO="https://github.com/DARPA-TRACTOR-Program/aws-translate.git"
AWS_TRANSLATE_COMMIT="b4bad32031a1983820bc5e2396ec4f9aa2b97721"

# Newer corpus with --no-falco. This is DARPA main @ 3e3b487 (B03) plus the
# --no-falco flag on tools/test_runner; see that branch's commit and
# plan_docs/falco_integration_notes.md.
NO_FALCO_CORPUS_REPO="https://github.com/Yale-PROCTOR/Test-Corpus.git"
NO_FALCO_CORPUS_COMMIT="c627c097a066eaf8d85918c0be6fbe648c2ca820"

fetch_repo() {
  local dest="$1" repo="$2" commit="$3"
  if [ -d "$dest/.git" ] &&
     git -C "$dest" rev-parse -q --verify "${commit}^{commit}" >/dev/null 2>&1; then
    git -C "$dest" checkout -q "$commit"
    echo "present: $dest @ ${commit:0:7}"
    return
  fi
  mkdir -p "$dest"
  git -C "$dest" init -q
  git -C "$dest" remote add origin "$repo" 2>/dev/null ||
    git -C "$dest" remote set-url origin "$repo"
  # fetch just the pinned commit (GitHub serves reachable SHAs); fall back
  # to a full fetch if the server refuses a bare-SHA fetch.
  if git -C "$dest" fetch -q --depth 1 origin "$commit" 2>/dev/null; then
    git -C "$dest" checkout -q FETCH_HEAD
  else
    git -C "$dest" fetch -q origin
    git -C "$dest" checkout -q "$commit"
  fi
  echo "fetched: $dest @ ${commit:0:7}"
}

want_aws=""
want_no_falco=""
for arg in "$@"; do
  case "$arg" in
    --with-aws) want_aws=1 ;;
    --no-falco) want_no_falco=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ -n "$want_no_falco" ]; then
  fetch_repo "$ROOT/tractor-test-corpus-newer/Test-Corpus" \
    "$NO_FALCO_CORPUS_REPO" "$NO_FALCO_CORPUS_COMMIT"
  echo "done."
  exit 0
fi

fetch_repo "$ROOT/tractor-test-corpus/Test-Corpus" \
  "$TEST_CORPUS_REPO" "$TEST_CORPUS_COMMIT"

if [ -n "$want_aws" ]; then
  fetch_repo "$ROOT/tractor-test-corpus/aws-translate" \
    "$AWS_TRANSLATE_REPO" "$AWS_TRANSLATE_COMMIT"
fi

echo "done."
