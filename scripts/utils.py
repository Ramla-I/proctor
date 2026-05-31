import json
import os
import shutil
import shlex
import subprocess
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")
type ParamVal = str | int | bool
type Parameters = list[tuple[str, ParamVal]]

HELP_ARGS = {"-h", "--help"}


def show_progress(done: int, total: int) -> None:
    print(f"\r{done}/{total}", end="", flush=True)


def should_show_help(argv: list[str]) -> bool:
    return any(arg in HELP_ARGS for arg in argv[1:]) or (
        len(argv) > 1 and argv[1] == "help"
    )


def print_help(usage: str, *details: str) -> None:
    print(usage)
    for detail in details:
        print(detail)


def load_json(path: Path):
    with open(path, "r") as f:
        return json.load(f)


def dump_json(data, path: Path) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def load_toml(path: Path):
    import toml

    with open(path, "r") as f:
        return toml.load(f)


def dump_toml(data, path: Path) -> None:
    import toml

    with open(path, "w") as f:
        toml.dump(data, f)


def get_name_without_suffix(path: Path) -> str:
    return path.name.removesuffix("".join(path.suffixes))


def unique(values: list[T]) -> list[T]:
    return list(dict.fromkeys(values))


def _remove_workspace_table(cargo_toml: Path) -> None:
    data = load_toml(cargo_toml)
    if "workspace" not in data:
        return
    del data["workspace"]
    dump_toml(data, cargo_toml)


def _set_library_name(cargo_toml: Path, lib_name: str) -> None:
    data = load_toml(cargo_toml)
    package = data.get("package")
    if not isinstance(package, dict):
        raise ValueError(f"missing package table in {cargo_toml}")
    package["name"] = lib_name

    lib = data.get("lib")
    if lib is None:
        data["lib"] = {"name": lib_name, "path": "lib.rs", "crate-type": ["cdylib"]}
    elif isinstance(lib, dict):
        lib["name"] = lib_name
        lib["path"] = "lib.rs"
        lib["crate-type"] = ["cdylib"]
    else:
        raise ValueError(f"invalid lib table in {cargo_toml}")

    data.pop("bin", None)
    dump_toml(data, cargo_toml)


def _update_workspace_members(cargo_toml: Path, members: list[str]) -> None:
    data = load_toml(cargo_toml)
    workspace = data.get("workspace")
    if workspace is None:
        workspace = {}
        data["workspace"] = workspace
    if not isinstance(workspace, dict):
        raise ValueError(f"invalid workspace table in {cargo_toml}")

    members = [*members, "."]
    workspace["members"] = members
    workspace["default-members"] = members
    dump_toml(data, cargo_toml)


def _write_cargo_config(root_dir: Path) -> None:
    cargo_dir = root_dir / ".cargo"
    cargo_dir.mkdir(exist_ok=True)
    (cargo_dir / "config.toml").write_text(
        "[target.x86_64-unknown-linux-gnu]\n"
        'rustflags = ["-Clink-arg=-Wl,-z,lazy", "-Zplt=yes"]\n',
        encoding="utf-8",
    )


def _load_lib_names(root_dir: Path) -> list[str]:
    libs_json = root_dir / "libs.json"
    if not libs_json.exists():
        return []
    lib_names = load_json(libs_json)
    if not isinstance(lib_names, list) or not all(
        isinstance(lib_name, str) for lib_name in lib_names
    ):
        raise ValueError(f"invalid library list in {libs_json}")
    return unique(lib_names)


def copy_translated_rust(src_dir: Path, dst_dir: Path) -> None:
    if dst_dir.exists():
        shutil.rmtree(dst_dir)
    shutil.copytree(src_dir, dst_dir)

    _write_cargo_config(dst_dir)
    lib_names = _load_lib_names(dst_dir)
    if not lib_names:
        return

    crates_dir = dst_dir / "crates"
    if crates_dir.exists():
        shutil.rmtree(crates_dir)
    crates_dir.mkdir()

    workspace_members: list[str] = []
    for lib_name in lib_names:
        lib_dir = crates_dir / lib_name
        shutil.copytree(src_dir, lib_dir)

        rust_toolchain = lib_dir / "rust-toolchain"
        if rust_toolchain.exists():
            rust_toolchain.unlink()

        cargo_toml = lib_dir / "Cargo.toml"
        _remove_workspace_table(cargo_toml)
        _set_library_name(cargo_toml, lib_name)
        workspace_members.append(f"crates/{lib_name}")

    _update_workspace_members(dst_dir / "Cargo.toml", workspace_members)


def _append_log(log_path: Path, command: list[str], content: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"$ {shlex.join(command)}\n")
        if content:
            f.write(content)
            if not content.endswith("\n"):
                f.write("\n")
        f.write("\n")


def run(
    command: list[str],
    stdout_log: Path | None = None,
    stderr_log: Path | None = None,
    **kwargs,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        command,
        capture_output=True,
        **kwargs,
    )
    stdout = result.stdout.decode(errors="replace")
    stderr = result.stderr.decode(errors="replace")

    if stdout_log:
        _append_log(stdout_log, command, stdout)
    if stderr_log:
        _append_log(stderr_log, command, stderr)

    if result.returncode != 0:
        print(f"Command failed with {result.returncode}: {shlex.join(command)}")

    if result.returncode != 0 or "VERBOSE" in os.environ:
        print("stdout:")
        print(stdout, end="" if stdout.endswith("\n") else "\n")
        print("stderr:")
        print(stderr, end="" if stderr.endswith("\n") else "\n")
        result.check_returncode()

    return result
