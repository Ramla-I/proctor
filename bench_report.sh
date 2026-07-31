#!/usr/bin/env bash
# Per-case report of a bench run. Handles both:
#   - bench_no_falco.sh runs -> reads verify.json (the --no-falco results),
#     and ALSO computes each case's final-stage unsafe + idiomaticity
#   - bench.sh runs          -> reads bench.json (per-stage vectors)
#
#   ./bench_report.sh                          # latest bench under out/
#   ./bench_report.sh B03_organic              # latest bench for a suite
#   ./bench_report.sh out/bench-B03_organic-.. # a specific run dir
#
# For --no-falco runs the report adds unsafe (fast, source-only) and
# idiomaticity (clippy — builds each crate) of every case's final translation:
#   --no-idiomaticity   vectors + unsafe only (skip the per-case clippy build)
#   --no-metrics        vectors only (original fast report; no build at all)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

arg=""
metrics=1
idiom_flag=""
for a in "$@"; do
  case "$a" in
    --no-metrics) metrics=0 ;;
    --no-idiomaticity) idiom_flag="--no-idiomaticity" ;;
    -*) echo "unknown flag: $a" >&2; exit 2 ;;
    *) arg="$a" ;;
  esac
done

# Resolve the run directory.
if [ -z "$arg" ]; then
  run_dir=$(ls -dt "$ROOT"/out/bench-*/ 2>/dev/null | head -1 || true)
elif [ -d "$arg" ]; then
  run_dir="$arg"
elif [ -f "$arg" ]; then
  run_dir=$(dirname "$arg")
else
  run_dir=$(ls -dt "$ROOT"/out/bench-"$arg"-*/ 2>/dev/null | head -1 || true)
fi
run_dir="${run_dir%/}"

if [ -z "${run_dir:-}" ] || [ ! -d "$run_dir" ]; then
  echo "no bench run dir found (arg: '${arg:-<latest>}'); run ./bench.sh or ./bench_no_falco.sh first" >&2
  exit 1
fi
run_dir="$(cd "$run_dir" && pwd)"  # absolute, so `cd $ROOT` below is safe

# A bench_no_falco run has verify.json (vectors verified at host level, --no-falco);
# a bench.sh run records vectors inside bench.json. Prefer verify.json.
if [ -f "$run_dir/verify.json" ]; then
  # Full report (vectors + unsafe + idiomaticity) needs cargo, via the drivers.
  if [ "$metrics" -eq 1 ] && ! command -v cargo >/dev/null; then
    echo "note: cargo not found — metrics need it; showing vectors only" >&2
    metrics=0
  fi
  if [ "$metrics" -eq 1 ]; then
    cd "$ROOT"
    exec uv run python -m proctor.testing.suite_report "$run_dir" $idiom_flag
  fi

  # --no-metrics: original fast vectors-only report.
  echo "verify (--no-falco): $run_dir/verify.json"
  python3 - "$run_dir/verify.json" <<'PY'
import json, sys

d = json.load(open(sys.argv[1]))
v = d.get("vectors", {})
print("=" * 66)
print(f"{d.get('suite', '?')}   {d['cases_ok']}/{d['cases_total']} cases clean   (--no-falco)")
print("=" * 66)
for c in sorted(d.get("cases", []), key=lambda c: c["case"]):
    name = c["case"].split("/")[-1]
    fs = c.get("fs_skipped", 0)
    other = c.get("skipped", 0) - fs
    total = c.get("passed", 0) + c.get("skipped", 0) + c.get("failed", 0)
    notes = []
    if fs:
        notes.append(f"{fs} fs-skip")
    if other:
        notes.append(f"{other} skip")
    if not c.get("build_ok", True):
        notes.append("build-fail")
    tail = "  (" + ", ".join(notes) + ")" if notes else ""
    flag = "ok  " if c.get("ok") else "FAIL"
    print(f"   [{flag}] {name:<28} {c.get('passed', 0)}/{total} pass, {c.get('failed', 0)} fail{tail}")
print("-" * 66)
P, S, F, FS = v.get("passed", 0), v.get("skipped", 0), v.get("failed", 0), v.get("fs_skipped", 0)
rate = f"{100 * P / (P + F):.1f}%" if (P + F) else "n/a"
print(f"{d['cases_ok']}/{d['cases_total']} cases clean   "
      f"{P} pass, {F} fail, {S} skip ({FS} file-change)  ({rate})")
PY
  exit 0
fi

if [ ! -f "$run_dir/bench.json" ]; then
  echo "no verify.json or bench.json in $run_dir" >&2
  exit 1
fi

echo "bench: $run_dir/bench.json"
python3 - "$run_dir/bench.json" <<'PY'
import json, sys

d = json.load(open(sys.argv[1]))
print("=" * 66)
print(f"{d.get('bench', '?')}   {d['ok']}/{d['total']} translated"
      f"   wall {d.get('wall_s', '?')}s")
print("=" * 66)

P = F = S = clean = 0
for c in d["cases"]:
    vs = c.get("vectors") or []
    print(c["name"])
    if not vs:
        print("   (no vector verification — translation-only run;"
              " for --no-falco runs see verify.json)")
        continue
    for v in vs:
        if v.get("error"):
            print(f"   {v['stage']:<10} ERROR: {(v['error'] or '')[:55]}")
        else:
            bad = "" if v.get("build_ok") else "  build-fail"
            print(f"   {v['stage']:<10} {v['passed']}/{v['total']} pass, "
                  f"{v['failed']} fail, {v['skipped']} skip{bad}")
    last = vs[-1]  # summarize on the final verified stage
    if not last.get("error"):
        P += last.get("passed", 0)
        F += last.get("failed", 0)
        S += last.get("skipped", 0)
        if last.get("build_ok") and last.get("failed", 0) == 0:
            clean += 1

print("-" * 66)
rate = f"{100 * P / (P + F):.1f}%" if (P + F) else "n/a"
print(f"final-stage: {clean}/{d['total']} cases clean   "
      f"{P} pass, {F} fail, {S} skip ({rate})")
PY
