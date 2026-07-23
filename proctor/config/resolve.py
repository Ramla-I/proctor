"""Per-stage effective configuration.

Resolution order for LLM settings: global ``[llm]`` deep-merged with
``[stages.<id>.llm]`` (stage wins). Stage config and timeout come from
the stage entry directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from proctor.config.load import deep_merge
from proctor.config.model import PipelineConfig, StageEntry


@dataclass(frozen=True)
class ResolvedStage:
    """A stage entry with all defaults applied, ready for invocation."""

    entry: StageEntry
    index: int
    stage_dir: Path
    llm: dict[str, Any]

    @property
    def id(self) -> str:
        return self.entry.id


def enabled_stages(config: PipelineConfig) -> list[StageEntry]:
    """The enabled stages in pipeline order."""
    return [
        config.stages[stage_id]
        for stage_id in config.order
        if config.stages[stage_id].enabled
    ]


def resolve_stages(config: PipelineConfig, root: Path) -> list[ResolvedStage]:
    """Resolve every enabled stage: effective llm settings and the stage
    directory (``uses`` interpreted relative to ``root`` unless absolute)."""
    resolved: list[ResolvedStage] = []
    for index, entry in enumerate(enabled_stages(config)):
        stage_dir = entry.uses if entry.uses.is_absolute() else root / entry.uses
        resolved.append(
            ResolvedStage(
                entry=entry,
                index=index,
                stage_dir=stage_dir,
                llm=deep_merge(config.llm_defaults, entry.llm),
            )
        )
    return resolved
