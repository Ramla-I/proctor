"""M0 scaffold sanity checks."""

import proctor


def test_package_imports() -> None:
    assert proctor.__version__ == "0.1.0"
