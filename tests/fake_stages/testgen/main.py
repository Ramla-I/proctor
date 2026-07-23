#!/usr/bin/env python3
"""Fake test-generation stage: writes a trivial always-pass package."""

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    envelope = json.loads(args.input.read_text(encoding="utf-8"))
    dest = Path(envelope["outputs"]["test_package"])
    (dest / "test_data").mkdir(parents=True)
    script = dest / "run_test.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)

    args.output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "stage_id": envelope["stage_id"],
                "status": "success",
                "outputs": {"test_package": str(dest)},
                "error": None,
            }
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    main()
