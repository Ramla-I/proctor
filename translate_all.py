#!/usr/bin/env python3
from pathlib import Path
import sys

from translate import translate_archive


def _output_dir_for(archive_path: Path) -> Path:
    relative_path = archive_path.relative_to("bundles")
    return (
        Path("Test-Corpus")
        / relative_path.parent
        / archive_path.name.removesuffix(".tar.gz")
        / "translated_rust"
    )


def main() -> int:
    failed: list[str] = []
    try:
        for archive_path in sorted(Path("bundles").glob("*/*/*.tar.gz")):
            name = archive_path.name.removesuffix(".tar.gz")
            output_dir = _output_dir_for(archive_path)
            result = translate_archive(archive_path.resolve(), output_dir.resolve())
            if result == 130:
                return 130
            result_str = "succeeded" if result == 0 else "failed"
            print(f"{name} translation {result_str}")
            if result != 0:
                failed.append(name)
                continue
    except KeyboardInterrupt:
        print("translation interrupted", file=sys.stderr)
        return 130
    if failed:
        print(
            f"failed translations: {', '.join(failed)}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
