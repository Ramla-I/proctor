"""E2E acceptance gate for the recovery-quality panel.

On real B03 fixtures where BOTH backends passed the test vectors, the panel must
rank Claude's transform cleaner than GPT's (residual unsafe Claude < GPT) and
fire a critique on GPT but not Claude. This is the concrete success criterion
for the feedback metrics.

Needs the built measure_unsafety + recovery_metrics binaries and the B03 bench
fixtures under out/; skips if they are absent.
"""

from __future__ import annotations

import glob
from pathlib import Path

import pytest

from proctor.testing.recovery_quality import measure_recovery_quality

pytestmark = pytest.mark.e2e

_ROOT = Path(__file__).resolve().parent.parent.parent
_CLAUDE_GLOB = "out/bench-B03_organic-*-claude-shared-prompt"
_GPT_GLOB = "out/bench-B03_organic-*-gpt-identification_principles"


def _latest(pattern: str) -> Path | None:
    matches = sorted(glob.glob(str(_ROOT / pattern)))
    return Path(matches[-1]) if matches else None


def _crate(run: Path, case: str, stage: str) -> Path:
    return run / f"{case}_lib" / "stages" / stage / "out" / "rust"


@pytest.mark.parametrize(
    "case,candidate",
    [
        ("binary_heap", "src/binary_heap.rs"),
        ("binomial_heap", "src/binomial_heap.rs"),
    ],
)
def test_panel_ranks_claude_cleaner_than_gpt(case: str, candidate: str) -> None:
    claude, gpt = _latest(_CLAUDE_GLOB), _latest(_GPT_GLOB)
    if not claude or not gpt:
        pytest.skip("B03 bench fixtures not present under out/")

    crates = {
        "cl_src": _crate(claude, case, "01-crat"),
        "cl_dst": _crate(claude, case, "02-abstraction_recovery"),
        "gp_src": _crate(gpt, case, "01-crat"),
        "gp_dst": _crate(gpt, case, "02-abstraction_recovery"),
    }
    for name, p in crates.items():
        if not p.is_dir():
            pytest.skip(f"fixture crate missing: {name} -> {p}")

    cl = measure_recovery_quality(
        crates["cl_src"], crates["cl_dst"], candidate=candidate
    )
    gp = measure_recovery_quality(
        crates["gp_src"], crates["gp_dst"], candidate=candidate
    )

    # The acceptance criterion: the panel ranks Claude's transform cleaner.
    assert cl.residual_unsafe < gp.residual_unsafe, (
        f"{case}: Claude residual {cl.residual_unsafe} !< GPT {gp.residual_unsafe}"
    )
    # Claude drops the C plumbing; GPT retains it (facade signal >= Claude's).
    assert cl.facade_signal <= gp.facade_signal
    # Clean transform -> no critique; the worse one -> a non-empty critique.
    assert cl.render_critique() == ""
    assert gp.render_critique() != ""
