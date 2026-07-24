"""Focused tests for c2rust-adapter input preparation."""

from __future__ import annotations

import importlib.util
import io
import sys
import tarfile
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).parent.parent
ADAPTER_MAIN = REPO / "stages" / "c2rust-adapter" / "main.py"


def _load_adapter() -> ModuleType:
    spec = importlib.util.spec_from_file_location("c2rust_adapter_main", ADAPTER_MAIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ADAPTER = _load_adapter()


def _write_project(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.10)\n", encoding="utf-8"
    )


def test_prepare_c_root_keeps_directory_input(tmp_path: Path) -> None:
    project = tmp_path / "input" / "test_case"
    _write_project(project)

    assert ADAPTER.prepare_c_root(tmp_path / "input", tmp_path / "work") == project
    assert not (tmp_path / "work").exists()


@pytest.mark.parametrize("project_prefix", [Path(), Path("bundle"), Path("test_case")])
def test_prepare_c_root_extracts_tar_input_without_extension(
    tmp_path: Path, project_prefix: Path
) -> None:
    source = tmp_path / "source"
    _write_project(source / project_prefix)
    archive_path = tmp_path / "recorded-input"
    with tarfile.open(archive_path, mode="w:gz") as archive:
        for path in source.rglob("*"):
            archive.add(path, arcname=path.relative_to(source), recursive=False)

    root = ADAPTER.prepare_c_root(archive_path, tmp_path / "work")

    assert root == tmp_path / "work" / "c-project" / project_prefix
    assert (root / "CMakeLists.txt").is_file()


def test_prepare_c_root_prefers_tar_root_over_test_case(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_project(source)
    _write_project(source / "test_case")
    archive_path = tmp_path / "input.tar.gz"
    with tarfile.open(archive_path, mode="w:gz") as archive:
        for path in source.rglob("*"):
            archive.add(path, arcname=path.relative_to(source), recursive=False)

    root = ADAPTER.prepare_c_root(archive_path, tmp_path / "work")

    assert root == tmp_path / "work" / "c-project"


def test_prepare_c_root_rejects_unsafe_tar_input(tmp_path: Path) -> None:
    archive_path = tmp_path / "input.tar"
    with tarfile.open(archive_path, mode="w") as archive:
        member = tarfile.TarInfo("../outside")
        member.size = 1
        archive.addfile(member, io.BytesIO(b"x"))

    with pytest.raises(ADAPTER.StageFailure, match="not a readable tar archive"):
        ADAPTER.prepare_c_root(archive_path, tmp_path / "work")
    assert not (tmp_path / "outside").exists()
