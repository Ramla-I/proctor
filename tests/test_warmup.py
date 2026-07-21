"""proctor warmup: stage venv sync and warmup commands."""

from pathlib import Path

import pytest

from proctor.cli import main
from proctor.contracts.stage_manifest import StageManifest, StageManifestError


def _stage(tmp_path: Path, *, warmup_ok: bool = True) -> Path:
    stage = tmp_path / "stages" / "warm"
    stage.mkdir(parents=True)
    exit_code = 0 if warmup_ok else 1
    (stage / "stage.toml").write_text(
        'id = "warm"\nversion = "0.1.0"\nexec = ["python3", "main.py"]\n'
        f'warmup = ["python3", "-c", '
        f"\"import pathlib, sys; pathlib.Path('warmed.marker').touch(); "
        f'sys.exit({exit_code})"]\n'
        '[requires]\nrust_project = "optional"\n',
        encoding="utf-8",
    )
    return stage


def _config_file(tmp_path: Path) -> Path:
    file = tmp_path / "config.toml"
    file.write_text(
        "[run]\nprovides = ['rust_project']\n"
        "[pipeline]\norder = ['warm']\n"
        "[stages.warm]\nuses = 'stages/warm'\n",
        encoding="utf-8",
    )
    return file


def test_warmup_runs_stage_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import proctor.context.index as index_mod

    monkeypatch.setenv("PROCTOR_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(index_mod, "ensure_index_binary", lambda: tmp_path)
    stage = _stage(tmp_path)
    code = main(["warmup", "-c", str(_config_file(tmp_path)), "--root", str(tmp_path)])
    assert (stage / "warmed.marker").is_file()
    assert code == 0, capsys.readouterr().out


def test_warmup_failure_propagates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import proctor.context.index as index_mod

    monkeypatch.setenv("PROCTOR_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(index_mod, "ensure_index_binary", lambda: tmp_path)
    _stage(tmp_path, warmup_ok=False)
    code = main(["warmup", "-c", str(_config_file(tmp_path)), "--root", str(tmp_path)])
    assert code == 1
    assert "warmup failed" in capsys.readouterr().out


def test_stage_manifest_warmup_validation(tmp_path: Path) -> None:
    (tmp_path / "stage.toml").write_text(
        'id = "x"\nversion = "1"\nexec = ["a"]\nwarmup = []\n', encoding="utf-8"
    )
    with pytest.raises(StageManifestError, match="warmup"):
        StageManifest.load(tmp_path)
    (tmp_path / "stage.toml").write_text(
        'id = "x"\nversion = "1"\nexec = ["a"]\nwarmup = ["b", "c"]\n',
        encoding="utf-8",
    )
    assert StageManifest.load(tmp_path).warmup == ("b", "c")
