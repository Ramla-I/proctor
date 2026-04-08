#!/usr/bin/env python3
import concurrent.futures
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import toml
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from clang.cindex import Cursor, CursorKind, Index


T = TypeVar("T")

CRAT_PASSES: list[str] = [
    "expand",
    "extern",
    "preprocess",
    "outparam",
    "punning",
    "pointer",
    "io",
    "libc",
    "static",
    "simpl",
    "check",
    "interface",
    "unsafe",
    "unexpand",
    "split",
    "bin",
]

PARAMETER_VALUES: dict[str, list[str]] = {
    "HASH_BACKEND": ["blake", "sha2", "shake", "haraka"],
    "SECPAR": ["128f", "128s", "192f", "192s", "256f", "256s"],
    "THASH": ["simple", "robust"],
}

CLIPPY_FIX_HINT = "run `cargo clippy --fix"
MAX_CLIPPY_FIX_RUNS = 10


@dataclass(frozen=True)
class Artifact:
    name: str
    artifact_type: str
    sources: list[Path]
    link_args: list[str]


@dataclass(frozen=True)
class Job:
    num: int
    parameters: list[tuple[str, str]]
    workspace_root: Path

    @property
    def name(self) -> str:
        values = [value for _, value in self.parameters]
        if not values:
            return f"{self.num:03d}"
        return f"{self.num:03d}_{'_'.join(values)}"

    @property
    def translation_dir(self) -> Path:
        return (self.workspace_root / self.name).resolve()


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class TranslationRecord:
    name: str
    parameters: list[tuple[str, str]]
    artifacts: list["ArtifactTranslation"]


@dataclass(frozen=True)
class ArtifactTranslation:
    artifact_name: str
    artifact_type: str
    dir: str


@dataclass(frozen=True)
class GroupedArtifactTranslations:
    artifact_type: str
    records: list[TranslationRecord]


class TranslationError(RuntimeError):
    pass


@dataclass(frozen=True)
class TranslationMappingReport:
    mappings: list[tuple[Path, Path]]
    missing_rs: list[Path]
    extra_rs: list[Path]


active_process: subprocess.Popen[bytes] | None = None


def _load_json(path: Path):
    with open(path, "r") as f:
        return json.load(f)


def _dump_json(data, path: Path) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _iter_trace_lines(text: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line in text.splitlines():
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            records.append(data)
    return records


def _parse_cache_sets(records: list[dict[str, object]]) -> list[tuple[str, str]]:
    cache_sets: list[tuple[str, str]] = []
    for record in records:
        if record.get("cmd") != "set":
            continue
        args = record.get("args")
        if not isinstance(args, list) or "CACHE" not in args:
            continue
        cache_index = args.index("CACHE")
        if cache_index < 2:
            continue
        name = args[0]
        if not isinstance(name, str):
            continue
        values = [arg for arg in args[1:cache_index] if isinstance(arg, str)]
        value = ";".join(values)
        if name.startswith("CMAKE_") or "${" in name or "${" in value:
            continue
        cache_sets.append((name, value))
    return cache_sets


def _dump_config(names: list[str], path: Path) -> None:
    escaped = [json.dumps(name) for name in names]
    with open(path, "w") as f:
        f.write(f'c_exposed_fns = [ {", ".join(escaped)},]\n')


def _is_source_path(path: Path) -> bool:
    return path.suffix == ".c"


def _unique(values: list[T]) -> list[T]:
    return list(dict.fromkeys(values))


def _preserve_option(option: str) -> bool:
    return (
        option.startswith("-D")
        or option.startswith("-I")
        or option.startswith("-std=")
        or option.startswith("-m")
    )


def _command_args(command: dict[str, object]) -> list[str]:
    arguments = command.get("arguments")
    if isinstance(arguments, list):
        values = [str(value) for value in arguments[1:]]
    else:
        values = shlex.split(str(command["command"]))[1:]
    return [value for value in values if _preserve_option(value)]


def _visit_header_functions(node: Cursor, names: set[str], source_root: Path) -> None:
    if node.kind == CursorKind.FUNCTION_DECL and node.location.file is not None:
        decl_file = Path(node.location.file.name).resolve()
        if decl_file.is_relative_to(source_root) and decl_file.suffix == ".h":
            names.add(node.spelling)
    for child in node.get_children():
        _visit_header_functions(child, names, source_root)


def _write_config(commands_file: Path, source_root: Path, output_file: Path) -> None:
    source_root = source_root.resolve()
    commands = _load_json(commands_file)
    parse_args = [
        "-x",
        "c-header",
        *_unique([arg for command in commands for arg in _command_args(command)]),
    ]
    names: set[str] = set()
    index = Index.create()
    for header in sorted(source_root.rglob("*.h")):
        translation_unit = index.parse(str(header), args=parse_args)
        _visit_header_functions(translation_unit.cursor, names, source_root)
    _dump_config(sorted(names), output_file)


def _verify_artifact_sources(artifact: Artifact, root: Path) -> None:
    expected_root = root.resolve()
    for source in artifact.sources:
        resolved = source.resolve()
        if not resolved.is_relative_to(expected_root):
            raise TranslationError(f"source escapes root: {resolved}")
        if not resolved.exists():
            raise TranslationError(f"missing source: {resolved}")


def _get_target(build_root: Path) -> list[Artifact]:
    cmake_reply_root = build_root / ".cmake" / "api" / "v1" / "reply"

    index_path = next(cmake_reply_root.glob("index-*.json"))
    index = _load_json(index_path)

    codemodel_filename = index["reply"]["codemodel-v2"]["jsonFile"]
    codemodel_path = cmake_reply_root / codemodel_filename
    codemodel = _load_json(codemodel_path)
    source_root = Path(codemodel["paths"]["source"])

    target_entries = codemodel["configurations"][0]["targets"]
    targets = {
        entry["id"]: _load_json(cmake_reply_root / entry["jsonFile"])
        for entry in target_entries
    }

    cache: dict[str, list[Path]] = {}

    def resolve_sources(target_id: str) -> list[Path]:
        if target_id in cache:
            return cache[target_id]
        target = targets[target_id]
        sources = [
            source_root / source["path"]
            for source in target.get("sources", [])
            if "path" in source and _is_source_path(Path(source["path"]))
        ]
        for dependency in target.get("dependencies", []):
            dependency_id = dependency["id"]
            if dependency_id in targets:
                sources.extend(resolve_sources(dependency_id))
        resolved = _unique(sources)
        cache[target_id] = resolved
        return resolved

    def resolve_link_args(target_id: str) -> list[str]:
        target = targets[target_id]
        fragments = [
            fragment["fragment"]
            for fragment in target.get("link", {}).get("commandFragments", [])
            if fragment.get("role") == "libraries"
            and fragment["fragment"].startswith("-l")
        ]
        return _unique(fragments)

    artifacts: list[Artifact] = []
    for target in targets.values():
        artifact_name = str(target["name"])
        artifact_type = str(target["type"])
        if "sphincs_core" in artifact_name:
            continue
        if artifact_type not in {"EXECUTABLE", "SHARED_LIBRARY"}:
            continue
        artifacts.append(
            Artifact(
                artifact_name,
                artifact_type,
                resolve_sources(target["id"]),
                resolve_link_args(target["id"]),
            )
        )
    return artifacts


def _add_link_args_to_build_rs(build_rs: Path, link_args: list[str]) -> None:
    if not link_args:
        return
    lines = build_rs.read_text(encoding="utf-8").splitlines(keepends=True)
    insert_at = next(
        index + 1 for index, line in enumerate(lines) if line.strip() == "fn main() {"
    )
    for link_arg in reversed(link_args):
        lines.insert(insert_at, f'    println!("cargo:rustc-link-arg={link_arg}");\n')
    build_rs.write_text("".join(lines), encoding="utf-8")


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        process.wait()


def _terminate_active_process() -> None:
    global active_process
    if active_process is None:
        return
    _terminate_process(active_process)
    active_process = None


def _handle_worker_signal(signum: int, _: object) -> None:
    _terminate_active_process()
    raise KeyboardInterrupt(signum)


def _run_command(args: list[str], cwd: Path) -> CommandResult:
    global active_process
    process = subprocess.Popen(
        args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    active_process = process
    try:
        stdout, stderr = process.communicate()
    except BaseException:
        _terminate_process(process)
        process.wait()
        raise
    finally:
        active_process = None
    return CommandResult(
        args=args,
        returncode=process.returncode,
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
    )


def _require_success(result: CommandResult, job: Job) -> None:
    if result.returncode == 0:
        return
    command = shlex.join(result.args)
    details = [
        f"[{job.name}] command failed: {command}",
        f"[{job.name}] exit code: {result.returncode}",
    ]
    if result.stdout:
        details.append(f"[{job.name}] stdout:\n{result.stdout}")
    if result.stderr:
        details.append(f"[{job.name}] stderr:\n{result.stderr}")
    raise TranslationError("\n".join(details))


def _cleanup_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _extract_cmake_cache_defaults(source_dir: Path) -> list[tuple[str, str]]:
    build_dir = source_dir / "build-ninja"
    command = [
        "cmake",
        "--trace",
        "--trace-format",
        "json-v1",
        "-S",
        str(source_dir),
        "-B",
        str(build_dir),
        "-G",
        "Ninja",
    ]
    result = _run_command(command, cwd=source_dir)
    if result.returncode != 0:
        command_text = shlex.join(command)
        details = [
            f"command failed: {command_text}",
            f"exit code: {result.returncode}",
        ]
        if result.stdout:
            details.append(f"stdout:\n{result.stdout}")
        if result.stderr:
            details.append(f"stderr:\n{result.stderr}")
        raise TranslationError("\n".join(details))
    records = _iter_trace_lines(result.stdout) + _iter_trace_lines(result.stderr)
    return _parse_cache_sets(records)


def _extract_cmake_presets_cache_variables(
    source_dir: Path,
) -> list[tuple[str, str]] | None:
    presets_path = source_dir / "CMakePresets.json"
    if not presets_path.exists():
        return None
    data = _load_json(presets_path)
    for preset in data.get("configurePresets", []):
        if preset.get("name") != "test":
            continue
        values = preset.get("cacheVariables", {})
        if not isinstance(values, dict):
            return []
        pairs: list[tuple[str, str]] = []
        for name, value in values.items():
            if isinstance(value, dict):
                value = value.get("value", "")
            pairs.append((str(name), str(value)))
        return pairs
    return []


def _extract_parameter_names(parameters: list[tuple[str, str]]) -> list[str]:
    return _unique([name for name, _ in parameters])


def _extract_supported_parameters(
    parameters: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    return [(name, value) for name, value in parameters if name in PARAMETER_VALUES]


def _parameter_value_lists(parameter_names: list[str]) -> list[list[str]]:
    value_lists: list[list[str]] = []
    for name in parameter_names:
        values = PARAMETER_VALUES.get(name)
        if values is None:
            raise TranslationError(f"unknown parameter from cmake build: {name}")
        value_lists.append(values)
    return value_lists


def _build_jobs(
    workspace_root: Path, cmake_cache_defaults: list[tuple[str, str]]
) -> list[Job]:
    parameter_names = _extract_parameter_names(cmake_cache_defaults)
    value_lists = _parameter_value_lists(parameter_names)
    jobs: list[Job] = []
    num = 1
    parameter_sets: list[list[tuple[str, str]]] = [[]]
    for name, values in zip(parameter_names, value_lists):
        parameter_sets = [
            [*current, (name, value)] for current in parameter_sets for value in values
        ]
    for parameters in parameter_sets:
        jobs.append(Job(num, parameters, workspace_root))
        num += 1
    return jobs


def _filter_commands(
    commands: list[dict[str, object]], target_sources: list[Path]
) -> list[dict[str, object]]:
    source_set = {path.resolve() for path in target_sources}
    return [
        command
        for command in commands
        if Path(str(command["file"])).resolve() in source_set
    ]


def _translate_artifact(
    job: Job,
    artifact: Artifact,
    commands: list[dict[str, object]],
    config_file: Path,
    translation_dir: Path,
    workspace_root: Path,
) -> ArtifactTranslation:
    _verify_artifact_sources(artifact, translation_dir / "source")
    target_name = artifact.name
    dst_dir = translation_dir / target_name
    dst_dir.mkdir()
    commands_file = dst_dir / "compile_commands.json"
    _dump_json(_filter_commands(commands, artifact.sources), commands_file)

    try:
        result = _run_command(
            [
                "c2rust-transpile",
                "-o",
                str(dst_dir),
                "-e",
                str(commands_file),
            ],
            cwd=workspace_root,
        )
        _require_success(result, job)
    finally:
        if commands_file.exists():
            commands_file.unlink()
    _add_link_args_to_build_rs(dst_dir / "build.rs", artifact.link_args)

    crat_args = [
        "crat",
        "--config",
        str(config_file),
        "--inplace",
        "--extern-ignore-return-type",
        "--extern-ignore-param-type",
        "--outparam-simplify",
        "--io-assume-to-str-ok",
        "--unsafe-remove-unused",
        "--unsafe-remove-no-mangle",
        "--unsafe-replace-pub",
        "--unsafe-remove-extern-c",
        "--unexpand-use-print",
    ]
    if artifact.artifact_type == "EXECUTABLE":
        crat_args.extend(["--bin-name", target_name])
    crat_args.extend(["--pass", ",".join(CRAT_PASSES), str(dst_dir)])
    result = _run_command(crat_args, cwd=workspace_root)
    _require_success(result, job)

    result = _run_command(
        [
            "cargo",
            "build",
        ],
        cwd=dst_dir,
    )
    _require_success(result, job)
    count = 0
    while count < MAX_CLIPPY_FIX_RUNS:
        result = _run_command(
            [
                "cargo",
                "clippy",
                "--fix",
                "--allow-no-vcs",
            ],
            cwd=dst_dir,
        )
        _require_success(result, job)
        if CLIPPY_FIX_HINT not in f"{result.stdout}\n{result.stderr}":
            break
        count += 1
    return ArtifactTranslation(
        artifact_name=target_name,
        artifact_type=artifact.artifact_type,
        dir=str(dst_dir.resolve()),
    )


def _translate(job: Job) -> TranslationRecord:
    signal.signal(signal.SIGTERM, _handle_worker_signal)
    signal.signal(signal.SIGINT, _handle_worker_signal)
    translation_dir = job.translation_dir
    source_dir = (job.workspace_root / "source").resolve()
    copied_source_dir = translation_dir / "source"
    try:
        _cleanup_dir(translation_dir)
        translation_dir.mkdir()
        shutil.copytree(source_dir, copied_source_dir)

        build_dir = translation_dir / "build-ninja"
        query_dir = build_dir / ".cmake" / "api" / "v1" / "query"
        query_dir.mkdir(parents=True)
        (query_dir / "codemodel-v2").touch()

        result = _run_command(
            [
                "cmake",
                *[f"-D{name}={value}" for name, value in job.parameters],
                "-DCMAKE_EXPORT_COMPILE_COMMANDS=1",
                "-S",
                str(copied_source_dir),
                "-B",
                str(build_dir),
                "-G",
                "Ninja",
            ],
            cwd=translation_dir,
        )
        _require_success(result, job)

        commands_file = build_dir / "compile_commands.json"
        config_file = translation_dir / "config.toml"
        _write_config(commands_file, copied_source_dir, config_file)
        commands = _load_json(commands_file)
        targets = _get_target(build_dir)
        artifacts = [
            _translate_artifact(
                job,
                artifact,
                commands,
                config_file,
                translation_dir,
                job.workspace_root,
            )
            for artifact in targets
        ]
        if not artifacts:
            raise TranslationError(f"[{job.name}] no artifacts found")
        return TranslationRecord(
            name=job.name,
            parameters=job.parameters,
            artifacts=artifacts,
        )
    except BaseException:
        _terminate_active_process()
        _cleanup_dir(translation_dir)
        raise


def _stop_executor(executor: concurrent.futures.ProcessPoolExecutor) -> None:
    if hasattr(executor, "terminate_workers"):
        executor.terminate_workers()
    else:
        executor.shutdown(wait=False, cancel_futures=True)


def _cleanup_incomplete_jobs(jobs: list[Job], completed: set[str]) -> None:
    for job in jobs:
        if job.name not in completed:
            _cleanup_dir(job.translation_dir)


def _extract_archive(archive_path: Path, destination: Path) -> None:
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        archive.extractall(destination, members=members)


def _parse_paths(argv: list[str]) -> tuple[Path, Path]:
    if len(argv) != 3:
        raise TranslationError(
            f"usage: {Path(argv[0]).name} <path-to.tar.gz> <output-dir>"
        )
    archive_path = Path(argv[1]).expanduser().resolve()
    output_dir = Path(argv[2]).expanduser().resolve()
    return archive_path, output_dir


def _group_artifact_translations(
    records: list[TranslationRecord],
) -> dict[str, GroupedArtifactTranslations]:
    grouped: dict[str, GroupedArtifactTranslations] = {}
    for record in records:
        for artifact in record.artifacts:
            record_for_artifact = TranslationRecord(
                name=record.name,
                parameters=record.parameters,
                artifacts=[artifact],
            )
            existing = grouped.get(artifact.artifact_name)
            if existing is None:
                grouped[artifact.artifact_name] = GroupedArtifactTranslations(
                    artifact_type=artifact.artifact_type,
                    records=[record_for_artifact],
                )
                continue
            if existing.artifact_type != artifact.artifact_type:
                raise TranslationError(
                    "artifact type mismatch for "
                    f"{artifact.artifact_name}: "
                    f"{existing.artifact_type} != {artifact.artifact_type}"
                )
            grouped[artifact.artifact_name] = GroupedArtifactTranslations(
                artifact_type=existing.artifact_type,
                records=[*existing.records, record_for_artifact],
            )
    return grouped


def _write_translations_json(records: list[TranslationRecord], path: Path) -> None:
    _dump_json(
        [
            {
                "dir": record.artifacts[0].dir,
                **{name: value for name, value in record.parameters},
            }
            for record in sorted(records, key=lambda record: record.name)
        ],
        path,
    )


def _update_workspace_members(cargo_toml: Path, members: list[str]) -> None:
    with cargo_toml.open(encoding="utf-8") as f:
        data = toml.load(f)
    workspace = data.get("workspace")
    if not isinstance(workspace, dict):
        raise TranslationError(f"missing workspace table in {cargo_toml}")
    members.append(".")
    workspace["members"] = members
    workspace["default-members"] = members
    with cargo_toml.open("w", encoding="utf-8") as f:
        toml.dump(data, f)


def _remove_workspace_table(cargo_toml: Path) -> None:
    with cargo_toml.open(encoding="utf-8") as f:
        data = toml.load(f)
    if "workspace" not in data:
        return
    del data["workspace"]
    with cargo_toml.open("w", encoding="utf-8") as f:
        toml.dump(data, f)


def _set_cdylib_crate_type(cargo_toml: Path) -> None:
    with cargo_toml.open(encoding="utf-8") as f:
        data = toml.load(f)
    lib = data.get("lib")
    if lib is None:
        data["lib"] = {"crate-type": ["cdylib"]}
    elif isinstance(lib, dict):
        lib["crate-type"] = ["cdylib"]
    else:
        raise TranslationError(f"invalid lib table in {cargo_toml}")
    with cargo_toml.open("w", encoding="utf-8") as f:
        toml.dump(data, f)


def _feature_name(parameter: tuple[str, str]) -> str:
    name, value = parameter
    return f"{name.lower()}_{value.lower()}"


def _set_default_features(cargo_toml: Path, parameters: list[tuple[str, str]]) -> None:
    if not parameters:
        return
    with cargo_toml.open(encoding="utf-8") as f:
        data = toml.load(f)
    features = data.get("features")
    if features is None:
        return
    if not isinstance(features, dict):
        raise TranslationError(f"invalid features table in {cargo_toml}")
    existing_features = set(features)
    defaults = [
        feature_name
        for feature_name in (_feature_name(parameter) for parameter in parameters)
        if feature_name in existing_features
    ]
    features["default"] = defaults
    with cargo_toml.open("w", encoding="utf-8") as f:
        toml.dump(data, f)


def _workspace_cargo_tomls(root_dir: Path) -> list[Path]:
    root_cargo_toml = root_dir / "Cargo.toml"
    with root_cargo_toml.open(encoding="utf-8") as f:
        data = toml.load(f)
    workspace = data.get("workspace")
    if not isinstance(workspace, dict):
        return [root_cargo_toml]
    members = workspace.get("members")
    if not isinstance(members, list):
        raise TranslationError(f"invalid workspace members in {root_cargo_toml}")
    cargo_tomls: list[Path] = []
    for member in members:
        if not isinstance(member, str):
            raise TranslationError(f"invalid workspace member in {root_cargo_toml}")
        cargo_tomls.append((root_dir / member / "Cargo.toml").resolve())
    return cargo_tomls


def _set_workspace_default_features(
    root_dir: Path, parameters: list[tuple[str, str]]
) -> None:
    for cargo_toml in _workspace_cargo_tomls(root_dir):
        _set_default_features(cargo_toml, parameters)


def _assemble_merged_workspace(
    merged_groups: list[tuple[str, GroupedArtifactTranslations, Path]],
) -> Path:
    if len(merged_groups) == 1:
        return merged_groups[0][2]
    chosen_group: tuple[str, GroupedArtifactTranslations, Path] | None = None
    for group in merged_groups:
        if group[1].artifact_type == "EXECUTABLE":
            chosen_group = group
            break
    if chosen_group is None:
        raise TranslationError("no executable merged group found")
    _, _, root_dir = chosen_group
    crates_dir = root_dir / "crates"
    _cleanup_dir(crates_dir)
    crates_dir.mkdir()
    cargo_dir = root_dir / ".cargo"
    cargo_dir.mkdir(exist_ok=True)
    (cargo_dir / "config.toml").write_text(
        "[target.x86_64-unknown-linux-gnu]\n"
        'rustflags = ["-Clink-arg=-Wl,-z,lazy", "-Zplt=yes"]\n',
        encoding="utf-8",
    )
    workspace_members: list[str] = []
    for artifact_name, _, merged_dir in merged_groups:
        if merged_dir == root_dir:
            continue
        destination = crates_dir / artifact_name
        _cleanup_dir(destination)
        moved_dir = shutil.move(str(merged_dir), str(destination))
        moved_rust_toolchain = Path(moved_dir) / "rust-toolchain"
        if moved_rust_toolchain.exists():
            moved_rust_toolchain.unlink()
        moved_cargo_toml = Path(moved_dir) / "Cargo.toml"
        if moved_cargo_toml.exists():
            _remove_workspace_table(moved_cargo_toml)
        workspace_members.append(f"crates/{artifact_name}")
    _update_workspace_members(root_dir / "Cargo.toml", workspace_members)
    return root_dir


def translate_archive(archive_path: Path, output_dir: Path) -> int:
    workspace_root = Path(tempfile.mkdtemp(prefix="tmp-", dir=Path.cwd()))
    try:
        _extract_archive(archive_path, workspace_root / "source")
    except (TranslationError, tarfile.TarError) as exc:
        _cleanup_dir(workspace_root)
        print(str(exc), file=sys.stderr)
        return 1
    source_dir = workspace_root / "source"

    source_copy_dir = workspace_root / "source_copied"
    shutil.copytree(source_dir, source_copy_dir)
    cmake_cache_defaults = _extract_cmake_cache_defaults(source_copy_dir)
    shutil.rmtree(source_copy_dir, ignore_errors=True)

    preset_cache_defaults = _extract_cmake_presets_cache_variables(source_dir)

    try:
        jobs = _build_jobs(workspace_root, cmake_cache_defaults)
    except TranslationError as exc:
        _cleanup_dir(workspace_root)
        print(str(exc), file=sys.stderr)
        return 1

    completed: set[str] = set()
    records: list[TranslationRecord] = []
    max_workers = max(os.cpu_count() or 1, 1)
    try:
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=max_workers
        ) as executor:
            futures = {executor.submit(_translate, job): job for job in jobs}
            try:
                for future in concurrent.futures.as_completed(futures):
                    try:
                        record = future.result()
                        completed.add(record.name)
                        records.append(record)
                    except KeyboardInterrupt:
                        _stop_executor(executor)
                        _cleanup_incomplete_jobs(jobs, completed)
                        print("translation interrupted", file=sys.stderr)
                        return 130
                    except BaseException as exc:
                        _stop_executor(executor)
                        _cleanup_incomplete_jobs(jobs, completed)
                        print(str(exc), file=sys.stderr)
                        return 1
            except KeyboardInterrupt:
                _stop_executor(executor)
                _cleanup_incomplete_jobs(jobs, completed)
                print("translation interrupted", file=sys.stderr)
                return 130
    except KeyboardInterrupt:
        _cleanup_incomplete_jobs(jobs, completed)
        print("translation interrupted", file=sys.stderr)
        return 130

    grouped_records = _group_artifact_translations(records)
    merged_groups: list[tuple[str, GroupedArtifactTranslations, Path]] = []
    for artifact_name, grouped_artifact in sorted(grouped_records.items()):
        translations_path = workspace_root / f"translations.{artifact_name}.json"
        _write_translations_json(grouped_artifact.records, translations_path)
        res = _run_command(
            [
                "crat-merge",
                str(translations_path),
                ".",
            ],
            cwd=workspace_root,
        )
        if res.returncode != 0:
            print(f"merging translations failed for {artifact_name}", file=sys.stderr)
            print(res.stdout, file=sys.stderr)
            print(res.stderr, file=sys.stderr)
            return 1
        merged_dir = workspace_root / artifact_name
        if grouped_artifact.artifact_type == "SHARED_LIBRARY":
            try:
                _set_cdylib_crate_type(merged_dir / "Cargo.toml")
            except TranslationError as exc:
                print(str(exc), file=sys.stderr)
                return 1
        merged_groups.append((artifact_name, grouped_artifact, merged_dir))

    try:
        root_dir = _assemble_merged_workspace(merged_groups)
    except TranslationError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    default_parameters = _extract_supported_parameters(
        preset_cache_defaults
        if preset_cache_defaults is not None
        else cmake_cache_defaults
    )
    try:
        _set_workspace_default_features(root_dir, default_parameters)
    except TranslationError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    _cleanup_dir(output_dir)
    shutil.move(str(root_dir), str(output_dir))
    _cleanup_dir(workspace_root)

    _run_command(["cargo", "fmt"], cwd=output_dir)
    _cleanup_dir(output_dir / "target")

    return 0


if __name__ == "__main__":
    try:
        archive_path, output_dir = _parse_paths(sys.argv)
    except TranslationError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(translate_archive(archive_path, output_dir))
