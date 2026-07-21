#!/usr/bin/env python3
"""Envelope adapter for CRAT (stages/crat submodule).

Builds crat once per submodule commit (cached via a marker file), then
runs the pass chain over the input Rust project, feeding each pass's
output to the next. Pass list and flags mirror the legacy
scripts/transform.py. Purely symbolic: no LLM usage to report.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

STAGE_ID = "crat"
STAGE_VERSION = "0.1.0"
SCHEMA_VERSION = 1

# pass -> (previous pass, extra flags); "c2rust" marks the chain root.
PLUGINS: dict[str, tuple[str, list[str]]] = {
    "expand": ("c2rust", []),
    "extern": ("expand", ["--extern-ignore-return-type", "--extern-ignore-param-type"]),
    "preprocess": ("extern", []),
    "outparam": ("preprocess", ["--outparam-simplify"]),
    "punning": ("outparam", []),
    "enum": ("punning", []),
    "pointer": ("enum", []),
    "io": ("pointer", ["--io-assume-to-str-ok"]),
    "libc": ("io", []),
    "static": ("libc", []),
    "simpl": ("static", []),
    "interface": ("simpl", []),
    "unsafe": (
        "interface",
        [
            "--unsafe-remove-unused",
            "--unsafe-remove-no-mangle",
            "--unsafe-replace-pub",
            "--unsafe-remove-extern-c",
        ],
    ),
    "unexpand": ("unsafe", ["--unexpand-use-print"]),
    "split": ("unexpand", []),
    "bin": ("split", []),
}


class StageFailure(Exception):
    pass


def plugin_chain(final_pass: str) -> list[str]:
    if final_pass not in PLUGINS:
        raise StageFailure(f"unknown crat pass {final_pass!r}")
    chain = [final_pass]
    while PLUGINS[chain[-1]][0] != "c2rust":
        chain.append(PLUGINS[chain[-1]][0])
    chain.reverse()
    return chain


def run_logged(
    command: list[str],
    log_file: Path,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    with log_file.open("ab") as log:
        log.write(f"$ {' '.join(command)}\n".encode())
        log.flush()
        result = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=log)
    if result.returncode != 0:
        tail = log_file.read_text(encoding="utf-8", errors="replace")[-3000:]
        raise StageFailure(
            f"command failed ({result.returncode}): {' '.join(command)}\n...{tail}"
        )


def git_head(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def ensure_crat_built(crat_dir: Path, log_file: Path) -> Path:
    """Build crat once per commit; rust-toolchain.toml pins the nightly
    and components, so rustup handles toolchain setup on first build."""
    binary = crat_dir / "target" / "release" / "crat"
    marker = crat_dir / "target" / ".proctor-build-sha"
    head = git_head(crat_dir)
    if binary.is_file() and marker.is_file() and marker.read_text().strip() == head:
        return binary
    run_logged(["cargo", "build"], log_file, cwd=crat_dir / "deps_crate")
    run_logged(["cargo", "build", "--release", "--bin", "crat"], log_file, cwd=crat_dir)
    if not binary.is_file():
        raise StageFailure(f"crat build produced no binary at {binary}")
    marker.write_text(head + "\n", encoding="utf-8")
    return binary


def crat_env(crat_dir: Path) -> dict[str, str]:
    import os

    sysroot = subprocess.check_output(
        ["rustc", "--print", "sysroot"], cwd=crat_dir, text=True
    ).strip()
    env = os.environ.copy()
    env["DIR"] = str(crat_dir)
    env["SYSROOT"] = sysroot
    # Library search path: rustc sysroot (rustc_private libs), plus the
    # proctor cache's userspace z3 (tests/e2e/README.md recipe) when
    # present, plus whatever the caller already set.
    paths = [str(Path(sysroot) / "lib")]
    cache_z3 = (
        Path(os.environ.get("PROCTOR_CACHE_DIR", Path.home() / ".cache" / "proctor"))
        / "z3"
        / "bin"
    )
    if cache_z3.is_dir():
        paths.append(str(cache_z3))
    if env.get("LD_LIBRARY_PATH"):
        paths.append(env["LD_LIBRARY_PATH"])
    env["LD_LIBRARY_PATH"] = ":".join(paths)
    return env


def run_pass(
    crat_bin: Path,
    env: dict[str, str],
    plugin: str,
    input_dir: Path,
    output_root: Path,
    log_file: Path,
) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    _, flags = PLUGINS[plugin]
    command = [
        str(crat_bin),
        "-o",
        str(output_root),
        "--config",
        str(input_dir / "config.toml"),
        "--pass",
        plugin,
        *flags,
        str(input_dir),
    ]
    run_logged(command, log_file, env=env)
    produced = output_root / input_dir.name
    if not produced.is_dir():
        raise StageFailure(f"pass {plugin!r} produced nothing at {produced}")
    return produced


def run_stage(envelope: dict) -> dict:
    config = envelope.get("config", {})
    src = envelope["inputs"]["rust_project"]
    dst = envelope["outputs"]["rust_project"]
    workdir = envelope.get("framework", {}).get("workdir")
    artifacts = envelope["outputs"].get("artifacts_dir")
    if src is None or dst is None or workdir is None:
        raise StageFailure("crat adapter needs rust_project in/out and a workdir")

    src_dir = Path(src)
    if not (src_dir / "config.toml").is_file():
        raise StageFailure(
            f"{src_dir} has no config.toml (crat needs the c2rust-stage config)"
        )

    log_file = Path(artifacts or workdir) / "crat.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    adapter_dir = Path(__file__).resolve().parent
    crat_dir = (adapter_dir / config.get("crat_dir", "../crat")).resolve()
    if not crat_dir.is_dir():
        raise StageFailure(
            f"crat checkout not found at {crat_dir} "
            f"(run: git submodule update --init stages/crat)"
        )

    build_started = time.monotonic()
    crat_bin = ensure_crat_built(crat_dir, log_file)
    build_s = round(time.monotonic() - build_started, 1)
    env = crat_env(crat_dir)

    final_pass = config.get("final_pass", "bin")
    chain = plugin_chain(final_pass)
    pass_seconds: dict[str, float] = {}
    current = src_dir
    for plugin in chain:
        started = time.monotonic()
        current = run_pass(
            crat_bin, env, plugin, current, Path(workdir) / plugin, log_file
        )
        pass_seconds[plugin] = round(time.monotonic() - started, 2)

    shutil.copytree(current, dst)

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "success",
        "stage_id": STAGE_ID,
        "stage_version": STAGE_VERSION,
        "outputs": {"rust_project": dst, "rule_set": None},
        "config_used": {"final_pass": final_pass, "crat_dir": str(crat_dir)},
        "metrics": {
            "crat_commit": git_head(crat_dir),
            "build_s": build_s,
            "passes": len(chain),
            **{f"pass_s.{name}": secs for name, secs in pass_seconds.items()},
        },
        "logs": ["crat.log"],
        "metadata": {},
        "error": None,
    }


def build_only() -> int:
    """Warmup entry point: build crat, no pipeline work."""
    import os

    adapter_dir = Path(__file__).resolve().parent
    crat_dir = (adapter_dir / "../crat").resolve()
    cache = Path(
        os.environ.get("PROCTOR_CACHE_DIR", Path.home() / ".cache" / "proctor")
    )
    cache.mkdir(parents=True, exist_ok=True)
    log_file = cache / "crat-build.log"
    try:
        binary = ensure_crat_built(crat_dir, log_file)
    except StageFailure as exc:
        print(f"crat build failed: {exc}", file=sys.stderr)
        return 1
    print(f"crat ready: {binary}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.build_only:
        return build_only()
    if args.input is None or args.output is None:
        parser.error("--input and --output are required unless --build-only")

    envelope = json.loads(args.input.read_text(encoding="utf-8"))
    try:
        output = run_stage(envelope)
    except StageFailure as exc:
        output = {
            "schema_version": SCHEMA_VERSION,
            "status": "failure",
            "stage_id": STAGE_ID,
            "stage_version": STAGE_VERSION,
            "error": str(exc),
        }
    except Exception as exc:  # never die without an envelope
        output = {
            "schema_version": SCHEMA_VERSION,
            "status": "failure",
            "stage_id": STAGE_ID,
            "stage_version": STAGE_VERSION,
            "error": f"{type(exc).__name__}: {exc}",
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    return 0 if output["status"] != "failure" else 1


if __name__ == "__main__":
    sys.exit(main())
