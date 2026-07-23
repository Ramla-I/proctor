"""Code index: build (via the proctor-rust-index binary) and query.

The index is cached per project by a content hash of its ``.rs`` files
under ``PROCTOR_CACHE_DIR`` (default ``~/.cache/proctor``). Resolution
is syntactic and best-effort — edge targets are matched by exact path,
then by unique suffix.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_CRATE = Path(__file__).parent.parent.parent / "crates" / "proctor-rust-index"


class IndexError_(ValueError):
    """The index cannot be built or a target cannot be resolved."""


@dataclass(frozen=True)
class IndexedItem:
    path: str
    kind: str
    file: str
    start_line: int
    end_line: int
    text: str


@dataclass(frozen=True)
class IndexedEdge:
    from_path: str
    to: str
    kind: str


class CodeIndex:
    def __init__(self, data: dict[str, Any]) -> None:
        self.items = [
            IndexedItem(
                path=str(i["path"]),
                kind=str(i["kind"]),
                file=str(i["file"]),
                start_line=int(i["start_line"]),
                end_line=int(i["end_line"]),
                text=str(i["text"]),
            )
            for i in data.get("items", [])
        ]
        self.edges = [
            IndexedEdge(from_path=str(e["from"]), to=str(e["to"]), kind=str(e["kind"]))
            for e in data.get("edges", [])
        ]
        self._by_path: dict[str, list[IndexedItem]] = {}
        for item in self.items:
            self._by_path.setdefault(item.path, []).append(item)

    def resolve(self, target: str) -> IndexedItem:
        """Exact path first, then unique suffix match."""
        exact = self._by_path.get(target)
        if exact:
            return exact[0]
        suffix_matches = [
            item
            for item in self.items
            if item.path == target or item.path.endswith(f"::{target}")
        ]
        if len(suffix_matches) == 1:
            return suffix_matches[0]
        if not suffix_matches:
            raise IndexError_(
                f"target {target!r} not found in index ({len(self.items)} items)"
            )
        paths = sorted({m.path for m in suffix_matches})
        raise IndexError_(f"target {target!r} is ambiguous: {paths}")

    def _resolve_edge_target(self, text: str) -> IndexedItem | None:
        try:
            return self.resolve(text)
        except IndexError_:
            # try the last path segment alone (e.g. `foo` for `super::foo`)
            last = text.rsplit("::", 1)[-1]
            if last != text:
                try:
                    return self.resolve(last)
                except IndexError_:
                    return None
            return None

    def edges_from(self, path: str, kinds: tuple[str, ...]) -> list[IndexedItem]:
        found: list[IndexedItem] = []
        seen: set[str] = set()
        for edge in self.edges:
            if edge.from_path == path and edge.kind in kinds:
                item = self._resolve_edge_target(edge.to)
                if item is not None and item.path not in seen and item.path != path:
                    seen.add(item.path)
                    found.append(item)
        return found

    def type_refs_of(self, path: str) -> list[IndexedItem]:
        return self.edges_from(path, ("type_ref",))

    def callees_of(self, path: str) -> list[IndexedItem]:
        return self.edges_from(path, ("call",))


def _tree_hash(project_dir: Path) -> str:
    digest = hashlib.sha256()
    for file in sorted(project_dir.rglob("*.rs")):
        if "target" in file.relative_to(project_dir).parts:
            continue
        digest.update(str(file.relative_to(project_dir)).encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()[:24]


def _cache_dir() -> Path:
    return Path(os.environ.get("PROCTOR_CACHE_DIR", Path.home() / ".cache" / "proctor"))


def ensure_index_binary() -> Path:
    """Build the indexer crate once; rebuilt only when cargo decides to."""
    binary = _CRATE / "target" / "release" / "proctor-rust-index"
    if binary.is_file():
        return binary
    result = subprocess.run(
        ["cargo", "build", "--release"],
        cwd=_CRATE,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not binary.is_file():
        raise IndexError_(
            f"failed to build proctor-rust-index: {result.stderr[-2000:]}"
        )
    return binary


def build_index(project_dir: Path) -> CodeIndex:
    """Index a Rust project, using the content-hash cache when possible."""
    cache_file = _cache_dir() / "rust-index" / f"{_tree_hash(project_dir)}.json"
    if cache_file.is_file():
        return CodeIndex(json.loads(cache_file.read_text(encoding="utf-8")))
    binary = ensure_index_binary()
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [str(binary), str(project_dir), "--output", str(cache_file)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise IndexError_(f"indexing failed: {result.stderr[-2000:]}")
    return CodeIndex(json.loads(cache_file.read_text(encoding="utf-8")))
