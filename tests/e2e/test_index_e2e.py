"""Build the real indexer and run it over the helloworld fixture."""

from pathlib import Path

import pytest

from proctor.context.api import retrieve_context
from proctor.context.index import build_index

REPO = Path(__file__).parent.parent.parent
FIXTURE = REPO / "tests" / "e2e" / "fixtures" / "001_helloworld" / "c2rust"

pytestmark = pytest.mark.e2e


def test_index_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCTOR_CACHE_DIR", str(tmp_path))
    index = build_index(FIXTURE)
    main_item = index.resolve("main_0")
    assert main_item.kind == "fn"
    assert "Hello World" in main_item.text

    bundle = retrieve_context(index, strategy="target_only", target="main_0")
    assert "printf" in bundle.render()

    # second call hits the cache (same tree hash)
    again = build_index(FIXTURE)
    assert again.resolve("main_0").text == main_item.text
