"""Command-line interface for the proctor orchestration framework.

Implemented: validate, stages. Coming with later milestones: run,
resume (M2), report (M3), bench (M7), warmup (M8).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from proctor import __version__
from proctor.config.load import ConfigError, load_config
from proctor.config.model import PipelineConfig
from proctor.orchestrator.validate import validate_pipeline

_NOT_YET = {
    "run": "M2",
    "resume": "M2",
    "report": "M3",
    "bench": "M7",
    "warmup": "M8",
}


def _add_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-c",
        "--config",
        action="append",
        required=True,
        metavar="FILE",
        help="config file; repeat to overlay (later files win)",
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        dest="overrides",
        metavar="PATH=VALUE",
        help="override a config value, e.g. --set stages.crat.enabled=false",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="directory that stage 'uses' paths are relative to (default: cwd)",
    )


def _load_pipeline_config(args: argparse.Namespace) -> PipelineConfig:
    merged = load_config([Path(p) for p in args.config], list(args.overrides))
    return PipelineConfig.from_dict(merged)


def _cmd_validate(args: argparse.Namespace) -> int:
    config = _load_pipeline_config(args)
    result, validated = validate_pipeline(config, args.root)
    for warning in result.warnings:
        print(f"warning: {warning}")
    for error in result.errors:
        print(f"error: {error}")
    if result.ok:
        stage_ids = [v.resolved.id for v in validated]
        print(f"ok: {len(stage_ids)} stage(s) validated: {', '.join(stage_ids)}")
        return 0
    return 1


def _cmd_stages(args: argparse.Namespace) -> int:
    config = _load_pipeline_config(args)
    result, validated = validate_pipeline(config, args.root)
    for stage_id in config.order:
        entry = config.stages[stage_id]
        if not entry.enabled:
            print(f"  - {stage_id}  (disabled)")
            continue
        match = next((v for v in validated if v.resolved.id == stage_id), None)
        if match is None:
            print(f"  ! {stage_id}  (invalid — see 'proctor validate')")
            continue
        manifest = match.manifest
        produces = ", ".join(manifest.produced_kinds()) or "nothing"
        print(
            f"  * {stage_id}  v{manifest.version}  "
            f"[{match.resolved.stage_dir}]  produces: {produces}"
        )
    return 0 if result.ok else 1


def _cmd_not_yet(verb: str, milestone: str) -> int:
    print(f"proctor {verb} arrives with {milestone}; not implemented yet.")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proctor",
        description="Orchestration framework for the PROCTOR pipeline.",
    )
    parser.add_argument("--version", action="version", version=f"proctor {__version__}")
    subparsers = parser.add_subparsers(dest="verb", required=True)

    validate = subparsers.add_parser(
        "validate", help="check the pipeline config and stage manifests"
    )
    _add_config_args(validate)
    validate.set_defaults(func=_cmd_validate)

    stages = subparsers.add_parser("stages", help="list the configured pipeline stages")
    _add_config_args(stages)
    stages.set_defaults(func=_cmd_stages)

    for verb, milestone in _NOT_YET.items():
        stub = subparsers.add_parser(verb, help=f"(arrives with {milestone})")
        stub.set_defaults(func=lambda _args, v=verb, m=milestone: _cmd_not_yet(v, m))

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result: int = args.func(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return result


if __name__ == "__main__":
    sys.exit(main())
