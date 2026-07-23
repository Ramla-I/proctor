"""Batch driver: the pipeline across a corpus, one run dir per case
(plan M7). `bench` is just N independent `run`s — same envelopes, same
checkpoints, same reports; there is no separate batch mode for stages.

Corpus layout is convention-over-configuration: a case is any directory
containing the ``[bench.layout]`` subpath for every artifact kind in
``[run] provides`` (defaults: c_project=c, rust_project=c2rust,
test_package=tests, rule_set=rules).

Rule-set policy across cases: ``independent`` only for now — the one
genuine cross-case coupling (cross-program rule learning) arrives with
the chained/merge policies later.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from proctor.config.load import ConfigError
from proctor.config.model import PipelineConfig
from proctor.orchestrator.run import RunError, RunResult, start_run

DEFAULT_LAYOUT = {
    "c_project": "c",
    "rust_project": "c2rust",
    "test_package": "tests",
    "rule_set": "rules",
}

_POLICIES = ("independent", "chained", "merge-per-round")


@dataclass(frozen=True)
class BenchSettings:
    jobs: int = 4
    layout: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_LAYOUT))
    vectors_path: str = "test_vectors"  # per-case vectors for package synthesis
    rule_set_policy: str = "independent"

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> BenchSettings:
        bench_raw = raw.get("bench", {})
        if not isinstance(bench_raw, dict):
            raise ConfigError("[bench] must be a table")
        jobs = bench_raw.get("jobs", 4)
        if not isinstance(jobs, int) or isinstance(jobs, bool) or jobs < 1:
            raise ConfigError("[bench] jobs must be a positive integer")
        layout = dict(DEFAULT_LAYOUT)
        layout_raw = bench_raw.get("layout", {})
        if not isinstance(layout_raw, dict):
            raise ConfigError("[bench.layout] must be a table")
        for kind, sub in layout_raw.items():
            if kind not in DEFAULT_LAYOUT or not isinstance(sub, str):
                raise ConfigError(
                    f"[bench.layout] {kind!r} must be one of "
                    f"{sorted(DEFAULT_LAYOUT)} mapped to a subpath"
                )
            layout[kind] = sub
        vectors_path = bench_raw.get("vectors_path", "test_vectors")
        if not isinstance(vectors_path, str) or not vectors_path:
            raise ConfigError("[bench] vectors_path must be a non-empty string")
        policy = bench_raw.get("rule_set_policy", "independent")
        if policy not in _POLICIES:
            raise ConfigError(f"[bench] rule_set_policy must be one of {_POLICIES}")
        if policy != "independent":
            raise ConfigError(
                f"[bench] rule_set_policy {policy!r} is not implemented yet; "
                f"use 'independent'"
            )
        return cls(
            jobs=jobs,
            layout=layout,
            vectors_path=vectors_path,
            rule_set_policy=policy,
        )


@dataclass(frozen=True)
class BenchCase:
    name: str
    inputs: dict[str, Path]
    synthesize_tests_from: Path | None = None  # vectors dir, when no package


def discover_cases(
    corpus: Path,
    provides: tuple[str, ...],
    layout: dict[str, str],
    *,
    vectors_path: str = "test_vectors",
) -> list[BenchCase]:
    """A case = a directory holding the layout subpath for every
    provided artifact kind. A missing test package is forgiven when the
    case carries TRACTOR vectors — the package is synthesized at run
    time (plan T2)."""
    if not corpus.is_dir():
        raise RunError(f"corpus directory {corpus} does not exist")
    cases: list[BenchCase] = []
    for candidate in sorted(p for p in corpus.rglob("*") if p.is_dir()):
        inputs: dict[str, Path] = {}
        synthesize: Path | None = None
        for kind in provides:
            sub = candidate / layout[kind]
            if sub.exists():
                inputs[kind] = sub
                continue
            vectors = candidate / vectors_path
            if (
                kind == "test_package"
                and vectors.is_dir()
                and any(p.is_dir() or p.suffix == ".json" for p in vectors.iterdir())
            ):
                synthesize = vectors
                continue
            break
        else:
            if provides:
                cases.append(
                    BenchCase(
                        name=str(candidate.relative_to(corpus)),
                        inputs=inputs,
                        synthesize_tests_from=synthesize,
                    )
                )
    # drop nested matches: a case must not contain another case
    names = {c.name for c in cases}
    return [
        c
        for c in cases
        if not any(
            other != c.name and c.name.startswith(other + "/") for other in names
        )
    ]


@dataclass
class BenchResult:
    bench_dir: Path
    cases: list[tuple[BenchCase, RunResult | Exception]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.cases) and all(
            isinstance(r, RunResult) and r.ok for _, r in self.cases
        )


def run_bench(
    config: PipelineConfig,
    root: Path,
    corpus: Path,
    *,
    name: str,
    jobs: int | None = None,
) -> BenchResult:
    settings = BenchSettings.from_config(config.raw)
    cases = discover_cases(
        corpus,
        config.run.provides,
        settings.layout,
        vectors_path=settings.vectors_path,
    )
    if not cases:
        raise RunError(
            f"no cases found under {corpus} for provides="
            f"{list(config.run.provides)} with layout {settings.layout}"
        )

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    bench_dir = root / config.run.output_dir / f"bench-{name}-{stamp}"
    bench_dir.mkdir(parents=True)
    result = BenchResult(bench_dir=bench_dir)
    started = time.monotonic()

    def one(case: BenchCase) -> tuple[BenchCase, RunResult | Exception]:
        safe_name = case.name.replace("/", "__")
        run_dir = bench_dir / safe_name
        try:
            inputs = dict(case.inputs)
            if case.synthesize_tests_from is not None:
                from proctor.testing.vectors import generate_test_package

                package_dir = bench_dir / "_synth_tests" / safe_name
                generate_test_package(case.synthesize_tests_from, package_dir)
                inputs["test_package"] = package_dir
            return case, start_run(
                config,
                root,
                name=safe_name,
                supplied_inputs=inputs,
                config_files=[],
                overrides=[],
                item=case.name,
                run_dir=run_dir,
            )
        except Exception as exc:  # a broken case must not sink the batch
            return case, exc

    workers = jobs if jobs is not None else settings.jobs
    with ThreadPoolExecutor(max_workers=workers) as pool:
        result.cases = list(pool.map(one, cases))

    summary = {
        "bench": name,
        "corpus": str(corpus),
        "wall_s": round(time.monotonic() - started, 1),
        "total": len(result.cases),
        "ok": sum(1 for _, r in result.cases if isinstance(r, RunResult) and r.ok),
        "cases": [
            {
                "name": case.name,
                "ok": isinstance(r, RunResult) and r.ok,
                "error": str(r) if isinstance(r, Exception) else None,
                "stages": (
                    [
                        {
                            "id": s.stage_id,
                            "status": s.status,
                            "duration_s": round(s.duration_s, 2),
                            "error": s.error,
                        }
                        for s in r.stages
                    ]
                    if isinstance(r, RunResult)
                    else []
                ),
            }
            for case, r in result.cases
        ],
    }
    (bench_dir / "bench.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return result
