"""T3 end to end: a library case's vectors verified through the real
cando harness. Builds the case's C library with cmake, generates the
package (bundled harness crate), and runs the §2.3 contract against
the .so. First run builds the harness crate (~1 min, cached after)."""

import subprocess
from pathlib import Path

import pytest

from proctor.testing.vectors import generate_test_package

REPO = Path(__file__).parent.parent.parent
CASE = REPO / "Test-Corpus" / "Public-Tests" / "B01_synthetic" / "001_helloworld_lib"

pytestmark = pytest.mark.e2e


def test_library_vectors_through_cando(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    subprocess.run(
        ["cmake", "-S", str(CASE / "test_case"), "-B", str(build_dir)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["cmake", "--build", str(build_dir)], check=True, capture_output=True
    )
    artifacts = list(build_dir.rglob("libhello.so"))
    assert artifacts, f"no libhello.so under {build_dir}"

    package = generate_test_package(CASE, tmp_path / "package")
    assert package.library

    proc = subprocess.run(
        [
            str(tmp_path / "package" / "run_test.sh"),
            str(tmp_path / "package" / "test_data"),
            str(artifacts[0]),
        ],
        capture_output=True,
        text=True,
        timeout=1200,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS  test1.json" in proc.stdout
