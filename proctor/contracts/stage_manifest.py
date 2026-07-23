"""``stage.toml`` — the manifest each stage ships at its root.

Declares how the orchestrator invokes the stage (``exec``), which
artifacts it requires and produces (used by ``proctor validate`` to
reject ill-formed pipelines before any work runs), and optional
config documentation.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

STAGE_MANIFEST_NAME = "stage.toml"

ARTIFACT_KINDS = ("c_project", "rust_project", "test_package", "rule_set")
PRODUCIBLE_KINDS = ("rust_project", "rule_set")

Requirement = Literal["required", "optional", "unused"]
_REQUIREMENTS = ("required", "optional", "unused")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class StageManifestError(ValueError):
    """stage.toml is missing or malformed."""


@dataclass(frozen=True)
class StageManifest:
    id: str
    version: str
    exec: tuple[str, ...]
    description: str = ""
    requires: dict[str, Requirement] = field(default_factory=dict)
    produces: dict[str, bool] = field(default_factory=dict)
    config_docs: dict[str, Any] = field(default_factory=dict)
    warmup: tuple[str, ...] | None = None  # optional pre-build command

    def requirement(self, kind: str) -> Requirement:
        """Requirement level for an artifact kind (default: unused)."""
        return self.requires.get(kind, "unused")

    def produced_kinds(self) -> tuple[str, ...]:
        return tuple(kind for kind in PRODUCIBLE_KINDS if self.produces.get(kind))

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, source: str = "stage.toml"
    ) -> StageManifest:
        stage_id = data.get("id")
        if not isinstance(stage_id, str) or not _ID_RE.match(stage_id):
            raise StageManifestError(
                f"{source}: 'id' must match {_ID_RE.pattern!r}, got {stage_id!r}"
            )
        version = data.get("version")
        if not isinstance(version, str) or not version:
            raise StageManifestError(f"{source}: 'version' must be a non-empty string")

        exec_raw = data.get("exec")
        if (
            not isinstance(exec_raw, list)
            or not exec_raw
            or not all(isinstance(part, str) and part for part in exec_raw)
        ):
            raise StageManifestError(
                f"{source}: 'exec' must be a non-empty array of strings"
            )

        description = data.get("description", "")
        if not isinstance(description, str):
            raise StageManifestError(f"{source}: 'description' must be a string")

        requires_raw = data.get("requires", {})
        if not isinstance(requires_raw, dict):
            raise StageManifestError(f"{source}: [requires] must be a table")
        requires: dict[str, Requirement] = {}
        for kind, level in requires_raw.items():
            if kind not in ARTIFACT_KINDS:
                raise StageManifestError(
                    f"{source}: unknown artifact kind {kind!r} in [requires]; "
                    f"known kinds: {ARTIFACT_KINDS}"
                )
            if level not in _REQUIREMENTS:
                raise StageManifestError(
                    f"{source}: requires.{kind} must be one of {_REQUIREMENTS}, "
                    f"got {level!r}"
                )
            requires[kind] = level

        produces_raw = data.get("produces", {})
        if not isinstance(produces_raw, dict):
            raise StageManifestError(f"{source}: [produces] must be a table")
        produces: dict[str, bool] = {}
        for kind, flag in produces_raw.items():
            if kind not in PRODUCIBLE_KINDS:
                raise StageManifestError(
                    f"{source}: unknown artifact kind {kind!r} in [produces]; "
                    f"producible kinds: {PRODUCIBLE_KINDS}"
                )
            if not isinstance(flag, bool):
                raise StageManifestError(f"{source}: produces.{kind} must be a boolean")
            produces[kind] = flag

        config_docs = data.get("config", {})
        if not isinstance(config_docs, dict):
            raise StageManifestError(f"{source}: [config] must be a table")

        warmup_raw = data.get("warmup")
        if warmup_raw is not None and (
            not isinstance(warmup_raw, list)
            or not warmup_raw
            or not all(isinstance(part, str) and part for part in warmup_raw)
        ):
            raise StageManifestError(
                f"{source}: 'warmup' must be a non-empty array of strings"
            )

        return cls(
            id=stage_id,
            version=version,
            exec=tuple(exec_raw),
            description=description,
            requires=requires,
            produces=produces,
            config_docs=config_docs,
            warmup=tuple(warmup_raw) if warmup_raw is not None else None,
        )

    @classmethod
    def load(cls, path: Path) -> StageManifest:
        """Load from a stage.toml file or a stage directory."""
        file = path / STAGE_MANIFEST_NAME if path.is_dir() else path
        try:
            data = tomllib.loads(file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise StageManifestError(f"{file} does not exist") from None
        except tomllib.TOMLDecodeError as exc:
            raise StageManifestError(f"{file} is not valid TOML: {exc}") from exc
        return cls.from_dict(data, source=str(file))
