"""unsafe_eval: report math + per-file aggregation over measure_unsafety
(the binary is stubbed; the real tool is exercised by ./metrics.sh)."""

from pathlib import Path

import pytest

from proctor.testing import unsafe_eval as ue
from proctor.testing.unsafe_eval import UnsafeEvalError, UnsafeReport


def test_from_stats_summary_and_per_kloc() -> None:
    r = UnsafeReport.from_stats(
        {
            "unsafe_score": 40,
            "unsafe_statements": 25,
            "unsafe_fns": 6,
            "unsafe_pub_fns": 2,
            "unsafe_blocks": 8,
            "unsafe_impls": 1,
            "unsafe_other": 5,
            "total_lines": 2000,
            "total_statements": 300,
        }
    )
    assert r.score == 40 and r.blocks == 8 and r.total_lines == 2000
    assert r.per_kloc == 20.0  # 1000 * 40 / 2000
    assert "unsafe score 40" in r.summary() and "8 blocks" in r.summary()


def test_per_kloc_zero_lines() -> None:
    assert UnsafeReport.from_stats({"total_lines": 0}).per_kloc == 0.0


def _crate(tmp_path: Path, files: list[str]) -> Path:
    (tmp_path / "src").mkdir()
    for i, name in enumerate(files):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(f"// file {i}\n", "utf-8")
    return tmp_path


def test_measure_unsafe_sums_over_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    crate = _crate(tmp_path, ["lib.rs", "src/a.rs", "target/skip.rs"])
    monkeypatch.setattr(ue, "ensure_built", lambda **k: Path("/fake/measure_unsafety"))

    def fake_run(cmd, **kw):
        # one unsafe_score=2, total_lines=10 per real (non-target) file
        class P:
            returncode = 0
            stdout = '{"unsafe_score": 2, "unsafe_blocks": 1, "total_lines": 10}'
            stderr = ""

        return P()

    monkeypatch.setattr(ue.subprocess, "run", fake_run)
    r = ue.measure_unsafe(crate)
    # lib.rs + src/a.rs scanned; target/ excluded
    assert r.files_scanned == 2 and r.files_skipped == 0
    assert r.score == 4 and r.blocks == 2 and r.total_lines == 20


def test_measure_unsafe_skips_unparseable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    crate = _crate(tmp_path, ["a.rs", "b.rs"])
    monkeypatch.setattr(ue, "ensure_built", lambda **k: Path("/fake"))

    calls = {"n": 0}

    def fake_run(cmd, **kw):
        calls["n"] += 1

        class P:
            # first file parses, second "panics" (non-zero)
            returncode = 0 if calls["n"] == 1 else 101
            stdout = '{"unsafe_score": 3, "total_lines": 5}' if calls["n"] == 1 else ""
            stderr = "panicked: failed to parse"

        return P()

    monkeypatch.setattr(ue.subprocess, "run", fake_run)
    r = ue.measure_unsafe(crate)
    assert r.files_scanned == 1 and r.files_skipped == 1 and r.score == 3


def test_measure_unsafe_no_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ue, "ensure_built", lambda **k: Path("/fake"))
    with pytest.raises(UnsafeEvalError, match="no .rs files"):
        ue.measure_unsafe(tmp_path)
