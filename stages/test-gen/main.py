#!/usr/bin/env python3
"""Test Generation stage (component spec §5.1): C project in, spec-§2.3
test package out.

This first implementation converts the case's TRACTOR ``test_vectors/``
via the framework's harness-faithful converter. Richer generators
(LLM-written vectors, coverage-driven inputs) slot in behind the same
stage interface later.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from proctor.contracts import OutputDestinations, StageInput, StageOutput
from proctor.testing.vectors import VectorError, generate_test_package

STAGE_ID = "test_generation"
STAGE_VERSION = "0.1.0"


def run_stage(stage_input: StageInput) -> StageOutput:
    c_project = stage_input.inputs.c_project
    dest = stage_input.outputs.test_package
    assert c_project is not None and dest is not None

    package = generate_test_package(c_project, dest)
    return StageOutput(
        status="success",
        stage_id=STAGE_ID,
        stage_version=STAGE_VERSION,
        outputs=OutputDestinations(test_package=package.package_dir),
        config_used=dict(stage_input.config),
        metrics={
            "vectors": package.vectors,
            "skip_at_runtime": len(package.unsupported),
        },
        metadata={"unsupported": list(package.unsupported)},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    stage_input = StageInput.read(args.input)
    try:
        output = run_stage(stage_input)
    except VectorError as exc:
        output = StageOutput(
            status="failure",
            stage_id=STAGE_ID,
            stage_version=STAGE_VERSION,
            error=str(exc),
        )
    except Exception as exc:  # never die without an envelope
        output = StageOutput(
            status="failure",
            stage_id=STAGE_ID,
            stage_version=STAGE_VERSION,
            error=f"{type(exc).__name__}: {exc}",
        )
    output.write(args.output)
    return 0 if output.status != "failure" else 1


if __name__ == "__main__":
    sys.exit(main())
