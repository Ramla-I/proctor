#!/usr/bin/env bash
# Measure `unsafe` usage + idiomaticity of a translated Rust project, or per
# stage across a run dir. Drives the vendored DARPA measure_unsafety (syntactic
# unsafe scorer) and Yale measure_idiomaticity (cargo clippy).
#
#   ./metrics.sh <rust_project>                 # one crate (Cargo.toml inside)
#   ./metrics.sh out/bench-.../array_list       # per stage (dir with stages/)
#   ./metrics.sh <crate> --no-idiomaticity      # unsafe only (no build/clippy)
#   ./metrics.sh <crate> --complexity           # + cognitive-complexity histogram
#   ./metrics.sh <crate> --json metrics.json    # also write JSON
#
# Unsafe needs no toolchain (source-only). Idiomaticity runs clippy, so it needs
# cargo + the crate's toolchain, and the crate must build; if it can't, the
# idiomaticity line is marked skipped and the unsafe result still prints.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
command -v cargo >/dev/null || { echo "error: cargo not found (needed to build the scorer and run clippy)" >&2; exit 1; }
cd "$ROOT"
exec uv run python -m proctor.testing.metrics "$@"
