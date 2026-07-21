"""Path wrappers for the shared pipeline artifacts (component spec §2).

Each wrapper carries a path plus a ``problems()`` check returning a list
of human-readable defects (empty list = valid). Checks are structural
only — they never build or run anything.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from proctor.contracts.manifest import MANIFEST_NAME, ProjectManifest


@dataclass(frozen=True)
class CProject:
    """A C project in DARPA TRACTOR Test-Corpus format."""

    path: Path

    def problems(self) -> list[str]:
        if not self.path.is_dir():
            return [f"C project directory {self.path} does not exist"]
        return []


@dataclass(frozen=True)
class RustProject:
    """A Cargo project produced or consumed by a pipeline stage."""

    path: Path

    @property
    def manifest_path(self) -> Path:
        return self.path / MANIFEST_NAME

    def load_manifest(self) -> ProjectManifest:
        return ProjectManifest.load(self.manifest_path)

    def problems(self, *, require_manifest: bool = False) -> list[str]:
        if not self.path.is_dir():
            return [f"Rust project directory {self.path} does not exist"]
        found: list[str] = []
        if not (self.path / "Cargo.toml").is_file():
            found.append(f"{self.path} has no Cargo.toml")
        if require_manifest and not self.manifest_path.is_file():
            found.append(f"{self.path} has no {MANIFEST_NAME}")
        return found


@dataclass(frozen=True)
class TestPackage:
    """A test package: ``run_test.sh`` plus a ``test_data/`` directory.

    Invoked as ``run_test.sh <test_data> <compiled-artifact>``; exit 0
    means success (component spec §2.3).
    """

    path: Path

    @property
    def run_test_sh(self) -> Path:
        return self.path / "run_test.sh"

    @property
    def test_data(self) -> Path:
        return self.path / "test_data"

    def problems(self) -> list[str]:
        if not self.path.is_dir():
            return [f"test package directory {self.path} does not exist"]
        found: list[str] = []
        if not self.run_test_sh.is_file():
            found.append(f"{self.path} has no run_test.sh")
        elif not os.access(self.run_test_sh, os.X_OK):
            found.append(f"{self.run_test_sh} is not executable")
        if not self.test_data.is_dir():
            found.append(f"{self.path} has no test_data/ directory")
        return found


@dataclass(frozen=True)
class RuleSetFile:
    """The local-transformation rule-set file. Format is opaque to the
    framework; only local transformation reads or writes it."""

    path: Path

    def problems(self) -> list[str]:
        if not self.path.is_file():
            return [f"rule-set file {self.path} does not exist"]
        return []
