"""M2 exit criterion: one public TRACTOR case through the real CRAT
stage, orchestrated end-to-end, output builds and runs, resume reuses.

Opt-in (pytest -m e2e): needs rustup; the first run builds CRAT
(minutes, cached afterwards).
"""

import subprocess
from pathlib import Path

import pytest

from proctor.config.load import load_config
from proctor.config.model import PipelineConfig
from proctor.orchestrator.run import resume_run, start_run

REPO = Path(__file__).parent.parent.parent
FIXTURE = REPO / "tests" / "e2e" / "fixtures" / "001_helloworld" / "c2rust"
SMOKE_CONFIG = REPO / "tests" / "e2e" / "crat_smoke.toml"

pytestmark = pytest.mark.e2e


def test_crat_pipeline_end_to_end(tmp_path: Path) -> None:
    merged = load_config([SMOKE_CONFIG], [f"run.output_dir='{tmp_path / 'runs'}'"])
    config = PipelineConfig.from_dict(merged)

    result = start_run(
        config,
        REPO,
        name="crat-smoke",
        supplied_inputs={"rust_project": FIXTURE},
        config_files=[SMOKE_CONFIG],
        overrides=[],
        item="Public-Tests/B01_synthetic/001_helloworld",
    )
    assert result.ok, [s.error for s in result.stages]
    assert [s.status for s in result.stages] == ["success"]

    final = result.final["rust_project"]
    assert (final / "Cargo.toml").is_file()

    # The transformed project must build — and, after the full chain to
    # the `bin` pass, produce a runnable executable.
    build_dir = tmp_path / "build_check"
    subprocess.run(["cp", "-r", str(final), str(build_dir)], check=True)
    build = subprocess.run(
        ["cargo", "build"],
        cwd=build_dir,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert build.returncode == 0, build.stderr[-3000:]

    binaries = list((build_dir / "target" / "debug").glob("*"))
    executables = [
        b
        for b in binaries
        if b.is_file() and b.suffix == "" and b.stat().st_mode & 0o111
    ]
    assert executables, f"no executable in target/debug: {[b.name for b in binaries]}"
    run = subprocess.run(
        [str(executables[0])], capture_output=True, text=True, timeout=60
    )
    assert run.returncode == 0
    assert "Hello World!" in run.stdout

    # Resume must reuse the checkpoint without re-running CRAT.
    resumed = resume_run(result.run_dir, REPO)
    assert resumed.ok
    assert [s.status for s in resumed.stages] == ["reused"]
