"""``proctor.toml`` — the Rust-project manifest (component spec §3).

Lives at the root of every pipeline Rust project. Records the build
target, the API functions whose external signatures must be preserved,
and the current wrapper relationships.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import tomli_w

MANIFEST_NAME = "proctor.toml"

TargetKind = Literal["executable", "library"]
_TARGET_KINDS = ("executable", "library")


class ManifestError(ValueError):
    """proctor.toml is missing or malformed."""


@dataclass(frozen=True)
class WrapperEntry:
    """An existing wrapper relationship, both crate-relative full paths."""

    wrapped: str
    wrapper: str


@dataclass(frozen=True)
class ProjectManifest:
    target_kind: TargetKind
    target_name: str
    api_functions: tuple[str, ...] = ()
    wrappers: tuple[WrapperEntry, ...] = ()

    def __post_init__(self) -> None:
        if self.target_kind not in _TARGET_KINDS:
            raise ManifestError(
                f"target_kind must be one of {_TARGET_KINDS}, got {self.target_kind!r}"
            )
        if not self.target_name:
            raise ManifestError("target_name must be non-empty")
        if self.target_kind == "executable" and self.api_functions:
            raise ManifestError("api_functions must be empty for executable targets")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectManifest:
        target_kind = data.get("target_kind")
        if not isinstance(target_kind, str) or target_kind not in _TARGET_KINDS:
            raise ManifestError(
                f"target_kind must be one of {_TARGET_KINDS}, got {target_kind!r}"
            )
        target_name = data.get("target_name")
        if not isinstance(target_name, str) or not target_name:
            raise ManifestError("target_name must be a non-empty string")

        api_functions_raw = data.get("api_functions", [])
        if not isinstance(api_functions_raw, list) or not all(
            isinstance(name, str) for name in api_functions_raw
        ):
            raise ManifestError("api_functions must be an array of strings")

        wrappers_raw = data.get("wrappers", [])
        if not isinstance(wrappers_raw, list):
            raise ManifestError("wrappers must be an array of tables")
        wrappers: list[WrapperEntry] = []
        for entry in wrappers_raw:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("wrapped"), str)
                or not isinstance(entry.get("wrapper"), str)
            ):
                raise ManifestError(
                    "each wrapper entry must be a table with string "
                    "'wrapped' and 'wrapper' keys"
                )
            wrappers.append(
                WrapperEntry(wrapped=entry["wrapped"], wrapper=entry["wrapper"])
            )

        return cls(
            target_kind=target_kind,  # type: ignore[arg-type]  # checked above
            target_name=target_name,
            api_functions=tuple(api_functions_raw),
            wrappers=tuple(wrappers),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_kind": self.target_kind,
            "target_name": self.target_name,
            "api_functions": list(self.api_functions),
            "wrappers": [
                {"wrapped": entry.wrapped, "wrapper": entry.wrapper}
                for entry in self.wrappers
            ],
        }

    @classmethod
    def load(cls, path: Path) -> ProjectManifest:
        """Load from a proctor.toml file or a Rust project directory."""
        file = path / MANIFEST_NAME if path.is_dir() else path
        try:
            data = tomllib.loads(file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ManifestError(f"{file} does not exist") from None
        except tomllib.TOMLDecodeError as exc:
            raise ManifestError(f"{file} is not valid TOML: {exc}") from exc
        return cls.from_dict(data)

    def dump(self, path: Path) -> None:
        """Write to a proctor.toml file or into a Rust project directory."""
        file = path / MANIFEST_NAME if path.is_dir() else path
        file.write_text(tomli_w.dumps(self.to_dict()), encoding="utf-8")
