"""bench_no_falco.sh's host-side driver: staging translations into the newer
corpus and rolling up the newer harness's per-case JUnit. The real nix-run
is exercised by bench_no_falco.sh, not here."""

from pathlib import Path

from proctor.testing import no_falco_bench as nfb


def _bench_case(bench_dir: Path, case: str, *, stage: str = "01-crat") -> None:
    rust = bench_dir / case / "stages" / stage / "out" / "rust"
    rust.mkdir(parents=True)
    (rust / "Cargo.toml").write_text("[package]\nname='x'\n", "utf-8")


def _corpus_case(corpus: Path, suite: str, case: str) -> None:
    (corpus / "Public-Tests" / suite / case / "test_vectors").mkdir(parents=True)


def test_stage_translations_stages_only_real_cases(tmp_path: Path) -> None:
    bench = tmp_path / "bench"
    corpus = tmp_path / "corpus"
    suite = "B01_synthetic"
    # two translated cases + one bench dir that isn't a corpus case
    _bench_case(bench, "001_helloworld")
    _bench_case(bench, "002_stdin_echo")
    (bench / "_corpus_ws").mkdir(parents=True)  # not a case
    _corpus_case(corpus, suite, "001_helloworld")
    _corpus_case(corpus, suite, "002_stdin_echo")

    staged = nfb.stage_translations(bench, corpus, suite)

    assert sorted(staged) == ["001_helloworld", "002_stdin_echo"]
    for case in staged:
        slot = corpus / "Public-Tests" / suite / case / "translated_rust"
        assert (slot / "Cargo.toml").is_file()


def test_stage_translations_skips_failed_translation(tmp_path: Path) -> None:
    bench = tmp_path / "bench"
    corpus = tmp_path / "corpus"
    suite = "B01_synthetic"
    (bench / "003_broken" / "stages").mkdir(parents=True)  # no out/rust
    _corpus_case(corpus, suite, "003_broken")
    assert nfb.stage_translations(bench, corpus, suite) == []


def test_stage_translations_match_filters(tmp_path: Path) -> None:
    bench = tmp_path / "bench"
    corpus = tmp_path / "corpus"
    suite = "B01_synthetic"
    _bench_case(bench, "001_helloworld")
    _bench_case(bench, "002_stdin_echo")
    _corpus_case(corpus, suite, "001_helloworld")
    _corpus_case(corpus, suite, "002_stdin_echo")
    assert nfb.stage_translations(bench, corpus, suite, match="stdin") == [
        "002_stdin_echo"
    ]


def test_rollup_junit(tmp_path: Path) -> None:
    junit = tmp_path / "j.xml"
    junit.write_text(
        "<testsuites>"
        "<testsuite name='Public-Tests/B/x'>"
        "<testcase name='config'/><testcase name='build'/>"
        "<testcase name='build-runners'/>"
        "<testcase name='v1'/><testcase name='v2'/>"
        "<testcase name='v3'><skipped message='fs'/></testcase>"
        "</testsuite>"
        "<testsuite name='Public-Tests/B/y'>"
        "<testcase name='build'/>"
        "<testcase name='v1'><failure message='boom'/></testcase>"
        "</testsuite>"
        "</testsuites>",
        encoding="utf-8",
    )
    rollups = {r.case: r for r in nfb.rollup_junit(junit)}

    x = rollups["Public-Tests/B/x"]
    assert (x.passed, x.skipped, x.failed, x.build_ok, x.ok) == (2, 1, 0, True, True)
    y = rollups["Public-Tests/B/y"]
    assert (y.passed, y.skipped, y.failed, y.ok) == (0, 0, 1, False)


def test_rollup_junit_build_failure_marks_not_ok(tmp_path: Path) -> None:
    junit = tmp_path / "j.xml"
    junit.write_text(
        "<testsuites><testsuite name='c'>"
        "<testcase name='build'><error message='no compile'/></testcase>"
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    (r,) = nfb.rollup_junit(junit)
    assert not r.build_ok and not r.ok
