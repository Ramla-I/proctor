"""Stage and artifact contracts — the load-bearing module.

Everything the orchestrator and stages agree on lives here:
the envelope (stage_io), the project manifest (manifest), the artifact
path wrappers (artifacts), and the stage manifest (stage_manifest).
"""

from proctor.contracts.artifacts import (
    CProject,
    RuleSetFile,
    RustProject,
    TestPackage,
)
from proctor.contracts.manifest import (
    MANIFEST_NAME,
    ManifestError,
    ProjectManifest,
    TargetKind,
    WrapperEntry,
)
from proctor.contracts.stage_io import (
    SCHEMA_VERSION,
    Budget,
    ContractError,
    FrameworkSettings,
    InputArtifacts,
    ModelInfo,
    OutputDestinations,
    PromptUse,
    StageInput,
    StageOutput,
    Status,
    UsageSummary,
)
from proctor.contracts.stage_manifest import (
    ARTIFACT_KINDS,
    PRODUCIBLE_KINDS,
    STAGE_MANIFEST_NAME,
    Requirement,
    StageManifest,
    StageManifestError,
)

__all__ = [
    "ARTIFACT_KINDS",
    "MANIFEST_NAME",
    "PRODUCIBLE_KINDS",
    "SCHEMA_VERSION",
    "STAGE_MANIFEST_NAME",
    "Budget",
    "CProject",
    "ContractError",
    "FrameworkSettings",
    "InputArtifacts",
    "ManifestError",
    "ModelInfo",
    "OutputDestinations",
    "ProjectManifest",
    "PromptUse",
    "Requirement",
    "RuleSetFile",
    "RustProject",
    "StageInput",
    "StageManifest",
    "StageManifestError",
    "StageOutput",
    "Status",
    "TargetKind",
    "TestPackage",
    "UsageSummary",
    "WrapperEntry",
]
