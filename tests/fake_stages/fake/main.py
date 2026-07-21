#!/usr/bin/env python3
"""Configurable fake stage for orchestrator tests.

``config.behavior`` selects: ok (default) | fail | skip | hang | bad_output.
No pyproject.toml on purpose — runs without uv.
"""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    envelope = json.loads(args.input.read_text(encoding="utf-8"))
    config = envelope.get("config", {})
    behavior = config.get("behavior", "ok")
    output = {
        "schema_version": 1,
        "stage_id": envelope["stage_id"],
        "status": "success",
        "config_used": config,
        "error": None,
    }

    if behavior == "hang":
        time.sleep(config.get("sleep_s", 60))
        behavior = "ok"

    if behavior == "bad_output":
        return 0  # exit cleanly without writing stage_output.json

    if behavior == "fail":
        output["status"] = "failure"
        output["error"] = "deliberate failure"
        args.output.write_text(json.dumps(output), encoding="utf-8")
        return 1

    if behavior == "skip":
        output["status"] = "skipped"
        args.output.write_text(json.dumps(output), encoding="utf-8")
        return 0

    src = envelope["inputs"]["rust_project"]
    dst = envelope["outputs"]["rust_project"]
    if config.get("fail_if_flag") and (Path(src) / "FAIL.txt").exists():
        output["status"] = "failure"
        output["error"] = "input project carries FAIL.txt"
        args.output.write_text(json.dumps(output), encoding="utf-8")
        return 1
    shutil.copytree(src, dst)
    marker = config.get("marker", "")
    (Path(dst) / "MARKER.txt").write_text(
        f"{marker}:{time.time_ns()}", encoding="utf-8"
    )
    output["outputs"] = {"rust_project": dst}
    output["metrics"] = {"marker": marker}
    args.output.write_text(json.dumps(output), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
