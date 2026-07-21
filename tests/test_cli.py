"""CLI verbs against the checked-in example config."""

from pathlib import Path

import pytest

from proctor.cli import main

REPO = Path(__file__).parent.parent


def test_validate_example_config(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        ["validate", "-c", str(REPO / "configs" / "example.toml"), "--root", str(REPO)]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.out
    assert "ok: 1 stage(s) validated: example" in captured.out


def test_stages_lists_example(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        ["stages", "-c", str(REPO / "configs" / "example.toml"), "--root", str(REPO)]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "example" in captured.out
    assert "produces: rust_project" in captured.out


def test_missing_config_reports_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["validate", "-c", str(tmp_path / "nope.toml")])
    captured = capsys.readouterr()
    assert code == 1
    assert "does not exist" in captured.err


def test_unimplemented_verb_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run"]) == 2
    assert "M2" in capsys.readouterr().out
