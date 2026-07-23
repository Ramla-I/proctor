"""Run the template stage as a real subprocess against the fixture."""

import subprocess
import sys
from pathlib import Path

from proctor.contracts import (
    InputArtifacts,
    OutputDestinations,
    StageInput,
    StageOutput,
)

REPO = Path(__file__).parent.parent
STAGE_MAIN = REPO / "stages" / "example-stage" / "main.py"
FIXTURE_RUST = REPO / "tests" / "e2e" / "fixtures" / "001_helloworld" / "c2rust"


def _run_stage(stage_input: StageInput, tmp_path: Path) -> tuple[int, StageOutput]:
    input_file = tmp_path / "stage_input.json"
    output_file = tmp_path / "stage_output.json"
    stage_input.write(input_file)
    process = subprocess.run(
        [
            sys.executable,
            str(STAGE_MAIN),
            "--input",
            str(input_file),
            "--output",
            str(output_file),
        ],
        capture_output=True,
        text=True,
        cwd=STAGE_MAIN.parent,
    )
    return process.returncode, StageOutput.read(output_file)


def test_copies_fixture_project(tmp_path: Path) -> None:
    dst = tmp_path / "out" / "rust"
    fixture_files = sorted(
        p.relative_to(FIXTURE_RUST) for p in FIXTURE_RUST.rglob("*") if p.is_file()
    )
    stage_input = StageInput(
        run_id="test-run",
        stage_id="example",
        stage_index=0,
        inputs=InputArtifacts(rust_project=FIXTURE_RUST),
        outputs=OutputDestinations(rust_project=dst),
        config={"marker": "from-test"},
    )
    returncode, output = _run_stage(stage_input, tmp_path)
    assert returncode == 0
    assert output.status == "success"
    assert output.stage_id == "example"
    assert output.metrics["marker"] == "from-test"
    assert output.metrics["files_copied"] == len(fixture_files)
    copied = sorted(p.relative_to(dst) for p in dst.rglob("*") if p.is_file())
    assert copied == fixture_files
    # inputs untouched
    assert (
        sorted(
            p.relative_to(FIXTURE_RUST) for p in FIXTURE_RUST.rglob("*") if p.is_file()
        )
        == fixture_files
    )


def test_missing_input_reports_failure(tmp_path: Path) -> None:
    stage_input = StageInput(
        run_id="test-run",
        stage_id="example",
        stage_index=0,
        inputs=InputArtifacts(),
        outputs=OutputDestinations(rust_project=tmp_path / "out"),
    )
    returncode, output = _run_stage(stage_input, tmp_path)
    assert returncode == 1
    assert output.status == "failure"
    assert output.error and "rust_project" in output.error


def test_refuses_to_overwrite_existing_output(tmp_path: Path) -> None:
    dst = tmp_path / "out" / "rust"
    dst.mkdir(parents=True)
    stage_input = StageInput(
        run_id="test-run",
        stage_id="example",
        stage_index=0,
        inputs=InputArtifacts(rust_project=FIXTURE_RUST),
        outputs=OutputDestinations(rust_project=dst),
    )
    returncode, output = _run_stage(stage_input, tmp_path)
    assert returncode == 1
    assert output.status == "failure"
