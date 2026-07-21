"""Pipeline validation: reject ill-formed pipelines before any work runs.

Walks the enabled stages in order, tracking which artifact kinds are
available, and checks each stage's ``stage.toml`` requires/produces
declarations against that state. What the runner provides at run start
comes from ``[run] provides`` (default: c_project + test_package); an
artifact a stage requires must be provided or produced earlier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from proctor.config.model import PipelineConfig
from proctor.config.resolve import ResolvedStage, resolve_stages
from proctor.contracts.stage_manifest import StageManifest, StageManifestError


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class ValidatedStage:
    resolved: ResolvedStage
    manifest: StageManifest


def validate_pipeline(
    config: PipelineConfig,
    root: Path,
    *,
    provided: frozenset[str] | None = None,
) -> tuple[ValidationResult, list[ValidatedStage]]:
    """Validate the enabled pipeline; returns problems plus the loaded
    stage manifests (for reuse by callers such as ``proctor stages``).

    ``provided`` overrides the config's ``[run] provides`` when given.
    """
    result = ValidationResult()
    validated: list[ValidatedStage] = []
    available = set(provided) if provided is not None else set(config.run.provides)

    for resolved in resolve_stages(config, root):
        stage_id = resolved.id
        if not resolved.stage_dir.is_dir():
            result.errors.append(
                f"stage '{stage_id}': directory {resolved.stage_dir} does not "
                f"exist (is the submodule initialized?)"
            )
            continue
        try:
            manifest = StageManifest.load(resolved.stage_dir)
        except StageManifestError as exc:
            result.errors.append(f"stage '{stage_id}': {exc}")
            continue

        if manifest.id != stage_id:
            result.warnings.append(
                f"stage '{stage_id}': stage.toml declares id "
                f"'{manifest.id}' (config key wins for wiring)"
            )

        for kind, level in manifest.requires.items():
            if level == "required" and kind not in available:
                result.errors.append(
                    f"stage '{stage_id}' requires '{kind}' but no earlier "
                    f"stage produces it and the runner does not provide it"
                )

        for kind in manifest.produced_kinds():
            available.add(kind)

        validated.append(ValidatedStage(resolved=resolved, manifest=manifest))

    return result, validated
