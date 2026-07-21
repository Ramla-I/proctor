"""Config loading: TOML files, overlay merge, ``--set`` overrides.

Merge rules (Configuration Over Code, plan §3): tables deep-merge,
scalars and arrays replace, later files win, ``--set`` wins over all.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """A configuration file or override is malformed."""


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Return a new dict: tables deep-merge, everything else replaces."""
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def parse_override(expr: str) -> tuple[list[str], Any]:
    """Parse a ``--set dotted.path=value`` override.

    The value is parsed as TOML when possible (so ``8``, ``true``,
    ``["a","b"]`` get their natural types) and falls back to a plain
    string otherwise.
    """
    path_part, sep, raw_value = expr.partition("=")
    if not sep or not path_part:
        raise ConfigError(f"--set expects dotted.path=value, got {expr!r}")
    keys = path_part.strip().split(".")
    if not all(keys):
        raise ConfigError(f"--set path has an empty segment: {path_part!r}")
    try:
        value = tomllib.loads(f"v = {raw_value}")["v"]
    except tomllib.TOMLDecodeError:
        value = raw_value
    return keys, value


def apply_override(config: dict[str, Any], expr: str) -> None:
    """Apply one ``--set`` override in place, creating tables as needed."""
    keys, value = parse_override(expr)
    table = config
    for key in keys[:-1]:
        existing = table.get(key)
        if existing is None:
            existing = {}
            table[key] = existing
        elif not isinstance(existing, dict):
            raise ConfigError(
                f"--set path {'.'.join(keys)!r} passes through non-table key {key!r}"
            )
        table = existing
    table[keys[-1]] = value


def load_config(
    paths: list[Path], overrides: list[str] | None = None
) -> dict[str, Any]:
    """Load and merge config files in order, then apply overrides."""
    merged: dict[str, Any] = {}
    for path in paths:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ConfigError(f"config file {path} does not exist") from None
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
        merged = deep_merge(merged, data)
    for expr in overrides or []:
        apply_override(merged, expr)
    return merged
