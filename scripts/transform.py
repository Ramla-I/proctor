#!/usr/bin/env python3

import os
import shutil
import subprocess
import sys
from pathlib import Path

from utils import print_help, run, should_show_help

plugins: dict[str, tuple[str, list[str]]] = {
    "expand": ("c2rust", []),
    "extern": (
        "expand",
        [
            "--extern-ignore-return-type",
            "--extern-ignore-param-type",
        ],
    ),
    "preprocess": ("extern", []),
    "outparam": ("preprocess", ["--outparam-simplify"]),
    "punning": ("outparam", []),
    "enum": ("punning", []),
    "pointer": ("enum", []),
    "io": ("pointer", ["--io-assume-to-str-ok"]),
    "libc": ("io", []),
    "static": ("libc", []),
    "simpl": ("static", []),
    "interface": ("simpl", []),
    "unsafe": (
        "interface",
        [
            "--unsafe-remove-unused",
            "--unsafe-remove-no-mangle",
            "--unsafe-replace-pub",
            "--unsafe-remove-extern-c",
        ],
    ),
    "unexpand": ("unsafe", ["--unexpand-use-print"]),
    "split": ("unexpand", []),
    "bin": ("split", []),
}


def _crat_env(crat_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    sysroot = subprocess.check_output(
        ["rustc", "--print", "sysroot"],
        cwd=crat_dir,
        text=True,
    ).strip()
    env["DIR"] = str(crat_dir)
    env["SYSROOT"] = sysroot
    env["LD_LIBRARY_PATH"] = str(Path(sysroot) / "lib")
    return env


def _crat_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "crat"


def build_crat() -> None:
    crat = _crat_dir()
    debug = "DEBUG" in os.environ
    build_command = ["cargo", "build", "--bin", "crat"]
    if not debug:
        build_command.append("--release")
    subprocess.run(build_command, cwd=crat, check=True)


def _plugin_chain(plugin: str) -> list[str]:
    if plugin not in plugins:
        raise ValueError(f"Unsupported plugin: {plugin}")

    chain = [plugin]
    while plugins[chain[-1]][0] != "c2rust":
        chain.append(plugins[chain[-1]][0])
    chain.reverse()
    return chain


def _transform_core(
    crat_bin: str, env: dict[str, str], output_dir: Path, input_dir: Path, plugin: str
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    _, flags = plugins[plugin]
    command = [
        crat_bin,
        "-o",
        str(output_dir),
        "--config",
        str(input_dir / "config.toml"),
        "--pass",
        plugin,
    ]
    command.extend(flags)
    command.append(str(input_dir))

    dst_dir = output_dir / input_dir.name
    stdout_log = dst_dir / "stdout.log"
    stderr_log = dst_dir / "stderr.log"
    run(command, stdout_log=stdout_log, stderr_log=stderr_log, env=env)

    if "CHECK_BUILD" in os.environ:
        command = ["cargo", "build"]
        env = {
            **dict(os.environ),
            "RUSTFLAGS": "-Awarnings",
        }
        run(command, stdout_log=stdout_log, stderr_log=stderr_log, cwd=dst_dir, env=env)
        shutil.rmtree(dst_dir / "target")


def _transform_one_tc(translation_dir: Path, tc_dir: Path, plugin: str) -> None:
    project_dir = Path(__file__).resolve().parent.parent

    tc_dir = tc_dir.resolve()
    tc_name = tc_dir.name
    tc_p_dir_name = tc_dir.parent.name
    tc_pp_dir_name = tc_dir.parent.parent.name

    if plugin not in plugins:
        raise ValueError(f"Unsupported plugin: {plugin}")
    prev, _ = plugins[plugin]

    crat = _crat_dir()
    env = _crat_env(crat)
    debug = "DEBUG" in os.environ
    profile = "debug" if debug else "release"
    crat_bin = crat / "target" / profile / "crat"
    output_dir = translation_dir / plugin / tc_pp_dir_name / tc_p_dir_name
    input_root_dir = (
        project_dir / "c2rust-translated"
        if prev == "c2rust"
        else translation_dir / prev
    )
    input_dir = input_root_dir / tc_pp_dir_name / tc_p_dir_name / tc_name
    _transform_core(str(crat_bin), env, output_dir, input_dir, plugin)


def transform_tc(
    translation_dir: Path,
    tc_dir: Path,
    plugin: str,
    build: bool,
    run_dependencies: bool = False,
) -> None:
    if build:
        build_crat()

    for plugin in _plugin_chain(plugin) if run_dependencies else [plugin]:
        _transform_one_tc(translation_dir, tc_dir, plugin)


def _transform_one(workspace: Path, name: str, plugin: str) -> None:
    prev, _ = plugins[plugin]
    env = os.environ.copy()
    input_dir = workspace / prev / name
    output_dir = workspace / plugin
    _transform_core("crat", env, output_dir, input_dir, plugin)


def transform(workspace: Path, name: str) -> None:
    for plugin in _plugin_chain("bin"):
        _transform_one(workspace, name, plugin)


def _usage() -> str:
    return (
        f"Usage: {sys.argv[0]} <translation_dir> <tc_dir> <plugin> "
        "[--run-dependencies]"
    )


def main() -> None:
    if should_show_help(sys.argv):
        print_help(_usage())
        sys.exit(0)

    if len(sys.argv) not in {4, 5}:
        print(_usage())
        sys.exit(1)
    if len(sys.argv) == 5 and sys.argv[4] != "--run-dependencies":
        print(_usage())
        sys.exit(1)

    translation_dir = Path(sys.argv[1])
    tc_dir = Path(sys.argv[2])
    plugin = sys.argv[3]
    run_dependencies = len(sys.argv) == 5
    transform_tc(
        translation_dir,
        tc_dir,
        plugin,
        build=True,
        run_dependencies=run_dependencies,
    )


if __name__ == "__main__":
    main()
