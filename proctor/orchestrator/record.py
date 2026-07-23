"""``run.json`` — provenance for reproducibility (plan §4, §6).

Records everything needed to reproduce the run from the superrepo:
framework and per-stage git state, resolved config hash, tool versions,
and the container image identity when present. Paths inside the run
directory are stored run-root-relative so a run dir survives relocation.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _tool_version(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip().splitlines()[0] if result.stdout.strip() else None


def _git_state(cwd: Path) -> dict[str, Any]:
    def git(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    sha = git("rev-parse", "HEAD")
    porcelain = git("status", "--porcelain")
    return {
        "sha": sha,
        "dirty": bool(porcelain) if porcelain is not None else None,
    }


def relativize(path: Path, run_dir: Path) -> str:
    """Run-root-relative when inside the run dir, absolute otherwise."""
    try:
        return str(path.resolve().relative_to(run_dir.resolve()))
    except ValueError:
        return str(path)


def sha256_of(data: dict[str, Any]) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_run_record(
    *,
    run_id: str,
    run_dir: Path,
    framework_root: Path,
    config_files: list[Path],
    overrides: list[str],
    resolved_config: dict[str, Any],
    stages: list[dict[str, Any]],
    inputs: dict[str, str | None],
    item: str | None = None,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "item": item,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "argv": sys.argv,
        "config_files": [str(p) for p in config_files],
        "overrides": overrides,
        "resolved_config_hash": sha256_of(resolved_config),
        "framework": {
            "version": _framework_version(),
            "git": _git_state(framework_root),
        },
        "stages": stages,
        "inputs": inputs,
        "image": os.environ.get("PROCTOR_IMAGE"),
        "host": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "rustc": _tool_version(["rustc", "--version"]),
            "cargo": _tool_version(["cargo", "--version"]),
            "uv": _tool_version(["uv", "--version"]),
        },
    }


def _framework_version() -> str:
    from proctor import __version__

    return __version__


def write_run_record(run_dir: Path, record: dict[str, Any]) -> None:
    (run_dir / "run.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
