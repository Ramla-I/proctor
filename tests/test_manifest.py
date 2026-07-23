"""proctor.toml manifest model."""

from pathlib import Path

import pytest

from proctor.contracts import ManifestError, ProjectManifest, WrapperEntry


def test_round_trip_via_directory(tmp_path: Path) -> None:
    manifest = ProjectManifest(
        target_kind="library",
        target_name="example",
        api_functions=("foo", "bar"),
        wrappers=(
            WrapperEntry(wrapped="implementation::foo_impl", wrapper="api::foo"),
        ),
    )
    manifest.dump(tmp_path)
    assert (tmp_path / "proctor.toml").is_file()
    assert ProjectManifest.load(tmp_path) == manifest


def test_executable_with_api_functions_refused() -> None:
    with pytest.raises(ManifestError, match="api_functions"):
        ProjectManifest(
            target_kind="executable",
            target_name="driver",
            api_functions=("main",),
        )


def test_executable_round_trips(tmp_path: Path) -> None:
    manifest = ProjectManifest(target_kind="executable", target_name="driver")
    manifest.dump(tmp_path / "proctor.toml")
    assert ProjectManifest.load(tmp_path / "proctor.toml") == manifest


def test_bad_target_kind_refused() -> None:
    with pytest.raises(ManifestError, match="target_kind"):
        ProjectManifest.from_dict({"target_kind": "shared_object", "target_name": "x"})


def test_bad_wrapper_entry_refused() -> None:
    with pytest.raises(ManifestError, match="wrapper"):
        ProjectManifest.from_dict(
            {
                "target_kind": "library",
                "target_name": "x",
                "wrappers": [{"wrapped": "a::b"}],
            }
        )


def test_missing_file_reports_path(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="does not exist"):
        ProjectManifest.load(tmp_path / "proctor.toml")
