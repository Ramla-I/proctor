"""metrics CLI: path routing (crate / run dir / neither), the per-stage
table, and JSON output — with the measure functions stubbed."""

from pathlib import Path

import pytest

from proctor.testing import metrics
from proctor.testing.idiomaticity_eval import IdiomReport
from proctor.testing.unsafe_eval import UnsafeReport


def _u(score: int, lines: int) -> UnsafeReport:
    return UnsafeReport.from_stats(
        {"unsafe_score": score, "unsafe_blocks": score, "total_lines": lines}
    )


def _i(total: int) -> IdiomReport:
    return IdiomReport(by_group={"style": {"needless_return": total}}, loc=100)


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch):
    # score/lint decrease across stages so we can see reduction
    seq = {"n": 0}

    def fake_unsafe(crate, **kw):
        seq["n"] += 1
        return _u(100 // seq["n"], 300)

    monkeypatch.setattr(metrics, "measure_unsafe", fake_unsafe)
    monkeypatch.setattr(metrics, "measure_idiomaticity", lambda c, **kw: _i(30))
    return seq


def test_single_crate(tmp_path: Path, stub, capsys) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n", "utf-8")
    (tmp_path / "src").mkdir()
    rc = metrics.main([str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "unsafe:" in out and "idiomaticity:" in out


def _stage(run: Path, ordinal: str, sid: str) -> None:
    rust = run / "stages" / f"{ordinal}-{sid}" / "out" / "rust"
    rust.mkdir(parents=True)
    (rust / "Cargo.toml").write_text("[package]\nname='x'\n", "utf-8")
    (rust / "src").mkdir()


def test_per_stage_table_and_json(tmp_path: Path, stub, capsys) -> None:
    run = tmp_path / "case"
    _stage(run, "00", "c2rust")
    _stage(run, "01", "crat")
    out_json = tmp_path / "m.json"
    rc = metrics.main([str(run), "--json", str(out_json)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "c2rust" in out and "crat" in out and "per-stage metrics" in out

    import json

    data = json.loads(out_json.read_text())
    assert [d["stage"] for d in data] == ["c2rust", "crat"]
    # crat's unsafe score is lower than c2rust's (100 -> 50 in the stub)
    assert data[0]["unsafe"]["score"] > data[1]["unsafe"]["score"]
    assert "idiomaticity" in data[0]


def test_not_a_project(tmp_path: Path, capsys) -> None:
    rc = metrics.main([str(tmp_path)])
    assert rc == 2
    assert "neither a Cargo project nor a run dir" in capsys.readouterr().err


def test_no_idiomaticity_flag(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n", "utf-8")
    (tmp_path / "src").mkdir()
    monkeypatch.setattr(metrics, "measure_unsafe", lambda c, **kw: _u(5, 100))

    def boom(*a, **k):  # must NOT be called with --no-idiomaticity
        raise AssertionError("idiomaticity should be skipped")

    monkeypatch.setattr(metrics, "measure_idiomaticity", boom)
    rc = metrics.main([str(tmp_path), "--no-idiomaticity"])
    assert rc == 0
    # the idiomaticity line prints "N clippy lints"; it must be absent
    assert "clippy lints" not in capsys.readouterr().out
