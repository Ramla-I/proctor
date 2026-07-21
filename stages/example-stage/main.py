#!/usr/bin/env python3
"""Template proctor stage: copies the input Rust project unchanged.

Deliberately stdlib-only and framework-free, to demonstrate that a stage
needs nothing from proctor beyond the envelope contract. Copy this file
as the starting point for a real stage.

Invocation (see docs/stage-contract.md):

    python3 main.py --input <stage_input.json> --output <stage_output.json>
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

STAGE_ID = "example"
STAGE_VERSION = "0.1.0"
SCHEMA_VERSION = 1


def run(envelope: dict[str, Any]) -> dict[str, Any]:
    config = envelope.get("config", {})
    src = envelope["inputs"]["rust_project"]
    dst = envelope["outputs"]["rust_project"]
    if src is None or dst is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failure",
            "stage_id": STAGE_ID,
            "stage_version": STAGE_VERSION,
            "config_used": config,
            "error": "example stage needs inputs.rust_project and outputs.rust_project",
        }

    # Contract: never modify inputs; create the output at the given
    # destination (copy-then-modify is the expected shape).
    shutil.copytree(src, dst, dirs_exist_ok=False)
    files_copied = sum(1 for p in Path(dst).rglob("*") if p.is_file())

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "success",
        "stage_id": STAGE_ID,
        "stage_version": STAGE_VERSION,
        "outputs": {"rust_project": dst, "rule_set": None},
        "config_used": config,
        "metrics": {
            "files_copied": files_copied,
            "marker": config.get("marker", ""),
        },
        "metadata": {},
        "error": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    envelope = json.loads(args.input.read_text(encoding="utf-8"))
    try:
        output = run(envelope)
    except Exception as exc:  # a stage must report failure, not just die
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
