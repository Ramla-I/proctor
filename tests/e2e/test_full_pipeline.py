"""The full default pipeline config: c2rust → crat → abstraction
recovery (scaffold: skips until identify/transform land)."""

from pathlib import Path

import pytest

from proctor.config.load import load_config
from proctor.config.model import PipelineConfig
from proctor.contracts.manifest import ProjectManifest
from proctor.orchestrator.run import start_run

REPO = Path(__file__).parent.parent.parent
FIXTURE = REPO / "tests" / "e2e" / "fixtures" / "001_helloworld"
CONFIG = REPO / "configs" / "full_pipeline.toml"

pytestmark = pytest.mark.e2e


def test_full_pipeline_with_scaffold_stage(tmp_path: Path) -> None:
    merged = load_config([CONFIG], [f"run.output_dir='{tmp_path / 'runs'}'"])
    config = PipelineConfig.from_dict(merged)

    result = start_run(
        config,
        REPO,
        name="full-pipeline",
        supplied_inputs={
            "c_project": FIXTURE / "c",
            "test_package": FIXTURE / "tests",
        },
        config_files=[CONFIG],
        overrides=[],
        item="Public-Tests/B01_synthetic/001_helloworld",
    )
    assert result.ok, [s.error for s in result.stages]
    # scaffold stage skips (no candidates) and forwards crat's output
    assert [s.status for s in result.stages] == ["success", "success", "skipped"]
    final = result.final["rust_project"]
    assert "01-crat" in str(final)  # skip forwarded, not copied
    assert ProjectManifest.load(final).target_name == "driver"
