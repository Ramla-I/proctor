#!/usr/bin/env python3

import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from utils import print_help, should_show_help, show_progress
from orchestrate import orchestrate


def _usage() -> str:
    return f"Usage: {sys.argv[0]} <bundles> <Test-Corpus>"


def _orchestrate(args: tuple[Path, Path]) -> None:
    return orchestrate(args[0], args[1])


def main() -> None:
    if should_show_help(sys.argv):
        print_help(_usage())
        sys.exit(0)

    if len(sys.argv) != 3:
        print(_usage())
        sys.exit(1)

    bundles_dir = Path(sys.argv[1])
    corpus_dir = Path(sys.argv[2])

    configurable_tcs = []
    non_configurable_tcs = []

    for path in corpus_dir.glob("*/*/*"):
        test_case_dir = path / "test_case"
        if not test_case_dir.exists():
            continue
        if (test_case_dir / "configuration.json").exists():
            configurable_tcs.append(path)
        else:
            non_configurable_tcs.append(path)

    configurable_tcs = sorted(configurable_tcs)
    non_configurable_tcs = sorted(non_configurable_tcs)

    def bundle_path(tc_dir: Path) -> Path:
        return bundles_dir / tc_dir.relative_to(corpus_dir).with_suffix(".tar.gz")

    jobs = [
        (bundle_path(tc_dir), tc_dir / "translated_rust")
        for tc_dir in non_configurable_tcs
    ]
    total_num = len(non_configurable_tcs)
    show_progress(0, total_num)
    with ProcessPoolExecutor() as executor:
        for done_num, _ in enumerate(executor.map(_orchestrate, jobs), start=1):
            show_progress(done_num, total_num)
    print()

    total_num = len(configurable_tcs)
    show_progress(0, total_num)
    for done_num, tc_dir in enumerate(configurable_tcs, start=1):
        orchestrate(bundle_path(tc_dir), tc_dir / "translated_rust")
        show_progress(done_num, total_num)
    print()


if __name__ == "__main__":
    main()
