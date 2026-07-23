"""Full Translation-component e2e: C source → c2rust → CRAT → tested
Rust project carrying proctor.toml. Needs a c2rust-transpile (PATH,
cache, or buildable submodule) — see tests/e2e/README.md.
"""

from pathlib import Path

import pytest

from proctor.config.load import load_config
from proctor.config.model import PipelineConfig
from proctor.contracts.manifest import ProjectManifest
from proctor.orchestrator.run import resume_run, start_run
from proctor.testing.runner import run_tests

REPO = Path(__file__).parent.parent.parent
FIXTURE = REPO / "tests" / "e2e" / "fixtures" / "001_helloworld"
SMOKE_CONFIG = REPO / "tests" / "e2e" / "translation_smoke.toml"

pytestmark = pytest.mark.e2e


def test_c_source_to_tested_rust(tmp_path: Path) -> None:
    merged = load_config([SMOKE_CONFIG], [f"run.output_dir='{tmp_path / 'runs'}'"])
    config = PipelineConfig.from_dict(merged)

    result = start_run(
        config,
        REPO,
        name="translation-smoke",
        supplied_inputs={
            "c_project": FIXTURE / "c",
            "test_package": FIXTURE / "tests",
        },
        config_files=[SMOKE_CONFIG],
        overrides=[],
        item="Public-Tests/B01_synthetic/001_helloworld",
    )
    assert result.ok, [s.error for s in result.stages]
    assert [s.status for s in result.stages] == ["success", "success"]

    final = result.final["rust_project"]

    # the Translation component's contract: proctor.toml with an empty
    # wrapper list, correct target identity
    manifest = ProjectManifest.load(final)
    assert manifest.target_kind == "executable"
    assert manifest.target_name == "driver"
    assert manifest.api_functions == ()
    assert manifest.wrappers == ()

    # final project builds and passes the TRACTOR-style test package
    outcome = run_tests(final, FIXTURE / "tests")
    assert outcome.ok, outcome.stderr

    # both stages resume from checkpoints
    resumed = resume_run(result.run_dir, REPO)
    assert resumed.ok
    assert [s.status for s in resumed.stages] == ["reused", "reused"]
