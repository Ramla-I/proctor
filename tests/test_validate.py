"""Pipeline validation against stage.toml requires/produces chains."""

from pathlib import Path

from proctor.config.model import PipelineConfig
from proctor.orchestrator.validate import validate_pipeline


def _write_stage(
    root: Path,
    stage_id: str,
    *,
    requires: dict[str, str] | None = None,
    produces: dict[str, bool] | None = None,
) -> None:
    stage_dir = root / "stages" / stage_id
    stage_dir.mkdir(parents=True)
    lines = [
        f'id = "{stage_id}"',
        'version = "0.1.0"',
        'exec = ["python3", "main.py"]',
    ]
    if requires:
        lines.append("[requires]")
        lines.extend(f'{kind} = "{level}"' for kind, level in requires.items())
    if produces:
        lines.append("[produces]")
        lines.extend(
            f"{kind} = {'true' if flag else 'false'}" for kind, flag in produces.items()
        )
    (stage_dir / "stage.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _config(order: list[str]) -> PipelineConfig:
    return PipelineConfig.from_dict(
        {
            "pipeline": {"order": order},
            "stages": {sid: {"uses": f"stages/{sid}"} for sid in order},
        }
    )


def test_well_formed_chain_passes(tmp_path: Path) -> None:
    _write_stage(
        tmp_path,
        "translation",
        requires={"c_project": "required"},
        produces={"rust_project": True},
    )
    _write_stage(
        tmp_path,
        "repair",
        requires={"rust_project": "required", "test_package": "required"},
        produces={"rust_project": True},
    )
    result, validated = validate_pipeline(_config(["translation", "repair"]), tmp_path)
    assert result.ok, result.errors
    assert [v.resolved.id for v in validated] == ["translation", "repair"]


def test_missing_producer_rejected(tmp_path: Path) -> None:
    _write_stage(tmp_path, "repair", requires={"rust_project": "required"})
    result, _ = validate_pipeline(_config(["repair"]), tmp_path)
    assert not result.ok
    assert "requires 'rust_project'" in result.errors[0]


def test_rule_set_chain(tmp_path: Path) -> None:
    _write_stage(
        tmp_path,
        "translation",
        requires={"c_project": "required"},
        produces={"rust_project": True},
    )
    _write_stage(
        tmp_path,
        "local",
        requires={"rust_project": "required", "rule_set": "required"},
        produces={"rust_project": True, "rule_set": True},
    )
    # rule_set required but nothing produces or provides it
    result, _ = validate_pipeline(_config(["translation", "local"]), tmp_path)
    assert not result.ok
    # explicitly provided by the runner -> fine
    result, _ = validate_pipeline(
        _config(["translation", "local"]),
        tmp_path,
        provided=frozenset({"c_project", "test_package", "rule_set"}),
    )
    assert result.ok, result.errors


def test_missing_stage_directory_reported(tmp_path: Path) -> None:
    result, _ = validate_pipeline(_config(["ghost"]), tmp_path)
    assert not result.ok
    assert "does not exist" in result.errors[0]


def test_id_mismatch_warns(tmp_path: Path) -> None:
    _write_stage(tmp_path, "other-name", requires={"c_project": "optional"})
    config = PipelineConfig.from_dict(
        {
            "pipeline": {"order": ["mine"]},
            "stages": {"mine": {"uses": "stages/other-name"}},
        }
    )
    result, _ = validate_pipeline(config, tmp_path)
    assert result.ok
    assert result.warnings and "declares id" in result.warnings[0]


def test_produced_test_package_satisfies_requirement(tmp_path: Path) -> None:
    _write_stage(
        tmp_path,
        "gen",
        requires={"c_project": "required"},
        produces={"test_package": True},
    )
    _write_stage(tmp_path, "checker", requires={"test_package": "required"})
    result, _ = validate_pipeline(
        _config(["gen", "checker"]),
        tmp_path,
        provided=frozenset({"c_project"}),
    )
    assert result.ok, result.errors
