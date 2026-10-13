"""The benchmark corpus itself: structure, honesty rules, and reproducibility.

These tests do not re-measure the engine. They check that the *instrument* is
trustworthy, because a benchmark that can quietly produce a favourable number is
worse than no benchmark at all.

What is asserted here:

* the corpus is versioned, bounded, and internally consistent;
* every declared fixture source exists and declares a real, checkable behaviour;
* no fixture name, target value, or expected trigger is referenced anywhere in
  the production policy modules, so the engine cannot have been tuned to this
  corpus;
* aggregates are computed only over fixtures every compared policy ran;
* a policy that failed to reproduce a defect is never averaged as a fast run.

The measurement itself runs in ``test_benchmark_integration.py`` against real
containers.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from edgehunter.benchmark import (
    CORPUS,
    FIXTURE_ROOT,
    BenchmarkFixture,
    CaseKind,
    CaseReport,
    Corpus,
    CorpusReport,
    FixtureVerdict,
    PolicyMeasurement,
    SeededDefect,
    aggregate_probes_to_first_reproduction,
    comparable_fixture_ids,
    head_to_head,
    run_fixture,
)
from edgehunter.benchmark.models import CORPUS_VERSION, write_corpus_report

SEARCH_SOURCE = (
    "static_priority",
    "feedback_guided",
)


def _measurement(
    fixture_id: str,
    policy: str,
    *,
    reproduced: int | None,
    probes: int,
    executions: int = 0,
) -> PolicyMeasurement:
    return PolicyMeasurement(
        fixture_id=fixture_id,
        policy=policy,
        candidate_universe=10,
        distinct_probes=probes,
        container_executions=executions or probes,
        match_count=1,
        mismatch_count=1 if reproduced else 0,
        inconclusive_count=0,
        reproduced_count=1 if reproduced else 0,
        first_mismatch_probe=probes if reproduced else None,
        first_reproduced_probe=reproduced,
        termination="violation_reproduced" if reproduced else "probe_budget_exhausted",
        termination_reason="",
        wall_clock_seconds=1.0,
    )


# --- corpus structure ---------------------------------------------------------


def test_the_corpus_is_versioned_and_bounded() -> None:
    assert CORPUS.version == CORPUS_VERSION
    assert CORPUS_VERSION
    assert 10 <= len(CORPUS.fixtures) <= 20, "the corpus must stay small enough to read"


def test_every_fixture_id_is_unique_and_kebab_case() -> None:
    ids = CORPUS.ids
    assert len(ids) == len(set(ids))
    for fixture_id in ids:
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", fixture_id), fixture_id


def test_every_declared_fixture_source_exists_and_is_readable() -> None:
    for fixture in CORPUS.fixtures:
        source = FIXTURE_ROOT / fixture.source_file
        assert source.is_file(), f"{fixture.fixture_id} names a missing source: {source}"
        assert source.read_text(encoding="utf-8").strip(), fixture.fixture_id


def test_every_fixture_states_its_target_and_description() -> None:
    for fixture in CORPUS.fixtures:
        assert "::" in fixture.target, fixture.fixture_id
        assert fixture.description.strip(), fixture.fixture_id
        assert fixture.kind in CaseKind, fixture.fixture_id
        assert fixture.defect in SeededDefect, fixture.fixture_id


def test_a_search_fixture_declares_a_contract_a_start_value_and_a_policy() -> None:
    for fixture in CORPUS.fixtures:
        if fixture.kind not in (CaseKind.SEARCH, CaseKind.NEGATIVE):
            continue
        assert fixture.contract_id, fixture.fixture_id
        assert fixture.start_int is not None, fixture.fixture_id
        assert fixture.policies, fixture.fixture_id
        assert set(fixture.policies) <= set(SEARCH_SOURCE), fixture.fixture_id


VALUE_SPECIFIC_DEFECTS = (
    SeededDefect.INTERIOR_VALUE_FAULT,
    SeededDefect.ANCHOR_FAULT,
    SeededDefect.SEED_FAULT,
    SeededDefect.FAR_VALUE_FAULT,
)
"""Defects defined by special-casing one literal, so the source must name it."""


def test_a_declared_trigger_is_actually_a_literal_in_a_value_specific_fixture() -> None:
    """A `known_trigger` must name a value the fixture source really contains.

    Otherwise the corpus would be asserting a defect at an input the fixture does
    not special-case, and the "expected" classification would be a guess.

    The check is limited to the value-specific defects. `non-monotonic` declares
    a trigger of -1 without naming it, because its fault is a property of the
    whole function (``x % 2``), not of one literal; demanding a literal there
    would be demanding the wrong thing.
    """

    for fixture in CORPUS.fixtures:
        if fixture.known_trigger is None or fixture.defect not in VALUE_SPECIFIC_DEFECTS:
            continue
        source = (FIXTURE_ROOT / fixture.source_file).read_text(encoding="utf-8")
        assert str(fixture.known_trigger) in source, (
            f"{fixture.fixture_id} declares trigger {fixture.known_trigger} "
            "but the fixture source never mentions it"
        )


def test_every_value_specific_fixture_has_a_declared_trigger() -> None:
    for fixture in CORPUS.fixtures:
        if fixture.defect in VALUE_SPECIFIC_DEFECTS:
            assert fixture.known_trigger is not None, fixture.fixture_id


def test_a_correct_target_declares_no_trigger() -> None:
    """A target that behaves correctly cannot have a triggering input."""

    for fixture in CORPUS.fixtures:
        if fixture.defect is SeededDefect.NONE:
            assert fixture.known_trigger is None, fixture.fixture_id


def test_the_corpus_contains_a_correct_target() -> None:
    """Without one, a false-positive failure mode would be invisible."""

    correct = [f for f in CORPUS.fixtures if f.defect is SeededDefect.NONE]
    assert correct, "the corpus must contain at least one target that behaves correctly"


def test_the_corpus_contains_cases_where_the_baseline_is_not_disfavoured() -> None:
    """A corpus that can only flatter one policy measures nothing.

    `seed-fault` and `far-value-fault` exist because the static baseline's
    documented order beats proximity on them. Without such a case the benchmark
    would be measuring the corpus, not the policies.
    """

    ids = set(CORPUS.ids)
    assert "seed-fault" in ids
    assert "far-value-fault" in ids
    assert SeededDefect.SEED_FAULT in {f.defect for f in CORPUS.fixtures}
    assert SeededDefect.FAR_VALUE_FAULT in {f.defect for f in CORPUS.fixtures}


def test_the_corpus_contains_a_non_monotonic_target() -> None:
    assert SeededDefect.NON_MONOTONIC in {f.defect for f in CORPUS.fixtures}


def test_by_id_rejects_an_unknown_fixture() -> None:
    with pytest.raises(KeyError):
        CORPUS.by_id("no-such-fixture")


def test_by_id_returns_the_named_fixture() -> None:
    assert CORPUS.by_id("anchor-fault").defect is SeededDefect.ANCHOR_FAULT


# --- no tuning toward the corpus --------------------------------------------


def test_no_policy_module_references_a_benchmark_fixture_or_trigger() -> None:
    """The decisive honesty check: the engine must not know this corpus exists.

    If any production policy or orchestrator source named a fixture id, or one of
    the distinctive seeded trigger values, the benchmark could only be measuring a
    lookup table rather than a search heuristic.

    Triggers of magnitude 1 and 2 are excluded from the scan and the reason is
    explicit: -1 is the contract's own domain anchor and -2 is its neighbour, so
    any honest neighbour rule produces them from arithmetic. Requiring their
    absence would be requiring the policy *not* to work.
    """

    production = Path(__file__).resolve().parents[1] / "src" / "edgehunter"
    identifiers = [fixture.fixture_id for fixture in CORPUS.fixtures]
    identifiers += [fixture.fixture_id.replace("-", "_") for fixture in CORPUS.fixtures]
    identifiers += [fixture.source_file.removesuffix(".py") for fixture in CORPUS.fixtures]
    distinctive = [
        str(fixture.known_trigger)
        for fixture in CORPUS.fixtures
        if fixture.known_trigger is not None and abs(fixture.known_trigger) > 2
    ]

    scanned = [
        production / "search" / "policy.py",
        production / "search" / "actions.py",
        production / "search" / "state.py",
        production / "search" / "repository.py",
    ]

    offenders: list[str] = []
    for path in scanned:
        assert path.is_file(), f"missing production module: {path}"
        text = path.read_text(encoding="utf-8")
        for token in [*identifiers, *distinctive]:
            if token and token in text:
                offenders.append(f"{path.name} references {token!r}")
    assert not offenders, offenders


def test_no_engine_module_imports_the_benchmark_package() -> None:
    """A policy that could see its own benchmark would not be a measurement.

    `cli.py` is excluded on purpose: it legitimately imports the corpus to
    expose the `benchmark` command, and it chooses no inputs. The rule is about
    the engine, which is where a selection decision could be influenced.
    """

    production = Path(__file__).resolve().parents[1] / "src" / "edgehunter"
    for path in sorted(production.rglob("*.py")):
        if "benchmark" in path.parts or path.name == "cli.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "edgehunter.benchmark" not in text, f"{path.name} imports the benchmark"


def test_the_policy_module_does_not_import_the_benchmark() -> None:
    """A policy that could see its own benchmark would not be a measurement."""

    policy = Path(__file__).resolve().parents[1] / "src" / "edgehunter" / "search" / "policy.py"
    text = policy.read_text(encoding="utf-8")
    assert "benchmark" not in text
    assert "fixtures" not in text


# --- aggregation honesty -----------------------------------------------------


def _report(*rows: PolicyMeasurement) -> CorpusReport:
    cases = tuple(
        CaseReport(
            fixture_id=row.fixture_id,
            kind=CaseKind.SEARCH,
            declared="violation_reproduced",
            observed=row.termination,
            verdict=FixtureVerdict.AS_DECLARED,
            measurements=(row,),
        )
        for row in rows
    )
    return CorpusReport(CORPUS_VERSION, cases)


def test_only_fixtures_both_policies_ran_are_comparable() -> None:
    report = _report(
        _measurement("a", "static_priority", reproduced=2, probes=2),
        _measurement("a", "feedback_guided", reproduced=3, probes=3),
        _measurement("b", "static_priority", reproduced=1, probes=1),
    )

    assert comparable_fixture_ids(report) == ("a",)


def test_a_single_policy_run_has_nothing_comparable() -> None:
    report = _report(_measurement("a", "static_priority", reproduced=1, probes=1))
    assert comparable_fixture_ids(report) == ()


def test_a_policy_that_found_nothing_is_not_averaged_as_fast() -> None:
    """The rule that keeps a miss from reading as an efficiency win."""

    report = _report(
        _measurement("a", "static_priority", reproduced=2, probes=2),
        _measurement("a", "feedback_guided", reproduced=None, probes=8),
    )

    summary = aggregate_probes_to_first_reproduction(report, "feedback_guided", restrict_to=("a",))
    assert "mean_probes_to_first_reproduction" not in summary
    assert summary["cases_reproduced"] == 0
    assert summary["cases_not_reproduced"] == 1
    assert "reward a policy for missing a defect" in str(summary["note"])


def test_aggregate_counts_are_taken_over_restricted_fixtures_only() -> None:
    report = _report(
        _measurement("a", "static_priority", reproduced=2, probes=2),
        _measurement("b", "static_priority", reproduced=9, probes=9),
    )

    restricted = aggregate_probes_to_first_reproduction(
        report, "static_priority", restrict_to=("a",)
    )
    everything = aggregate_probes_to_first_reproduction(report, "static_priority")

    assert restricted["cases_measured"] == 1
    assert restricted["mean_probes_to_first_reproduction"] == 2
    assert everything["cases_measured"] == 2
    assert everything["mean_probes_to_first_reproduction"] == 5.5


def test_head_to_head_credits_only_the_policy_that_reproduced() -> None:
    report = _report(
        _measurement("shared", "static_priority", reproduced=2, probes=2),
        _measurement("shared", "feedback_guided", reproduced=3, probes=3),
        _measurement("static-only", "static_priority", reproduced=1, probes=1),
        _measurement("static-only", "feedback_guided", reproduced=None, probes=8),
        _measurement("neither", "static_priority", reproduced=None, probes=8),
        _measurement("neither", "feedback_guided", reproduced=None, probes=8),
    )

    result = head_to_head(report)

    assert result["comparable_fixtures"] == ["neither", "shared", "static-only"]
    assert result["both_reproduced"] == 1
    assert result["reproduced_only_by"] == {"static_priority": 1}
    assert result["neither_reproduced"] == ["neither"]


def test_head_to_head_reports_a_fixture_nobody_reached() -> None:
    report = _report(
        _measurement("quiet", "static_priority", reproduced=None, probes=8),
        _measurement("quiet", "feedback_guided", reproduced=None, probes=8),
    )

    assert head_to_head(report)["neither_reproduced"] == ["quiet"]


def test_a_corpus_report_never_invents_a_measurement_field() -> None:
    fields = set(PolicyMeasurement.__dataclass_fields__)
    for invented in ("coverage", "mutation_score", "probability", "confidence", "severity"):
        assert invented not in fields
    assert {"distinct_probes", "container_executions", "first_reproduced_probe"} <= fields


# --- report writing ----------------------------------------------------------


def test_the_report_is_strict_versioned_json(tmp_path: Path) -> None:
    report = _report(
        _measurement("a", "static_priority", reproduced=2, probes=2),
        _measurement("a", "feedback_guided", reproduced=3, probes=3),
    )
    target = tmp_path / "report.json"

    write_corpus_report(report, str(target))
    text = target.read_text(encoding="utf-8")

    assert "NaN" not in text and "Infinity" not in text
    assert '"schema_version": 1' in text
    assert '"corpus_version"' in text
    assert "not a claim about any real repository" in text


def test_the_report_carries_both_policies_traces(tmp_path: Path) -> None:
    report = _report(
        _measurement("a", "static_priority", reproduced=2, probes=2),
        _measurement("a", "feedback_guided", reproduced=3, probes=3),
    )
    target = tmp_path / "report.json"
    write_corpus_report(report, str(target))

    assert '"a:static_priority"' in target.read_text(encoding="utf-8")
    assert '"a:feedback_guided"' in target.read_text(encoding="utf-8")


def test_the_report_run_id_is_sanitized(tmp_path: Path) -> None:
    report = _report(_measurement("a", "static_priority", reproduced=1, probes=1))
    target = tmp_path / "report.json"

    write_corpus_report(report, str(target), run_id="../../escape")

    assert target.is_file()


# --- fixture-level behaviour that needs no container -------------------------


def test_materialization_cases_run_without_docker(tmp_path: Path) -> None:
    """Two corpus cases assert properties that need no sandbox at all."""

    for fixture_id, expected_fragment in (
        ("bool-materialization", "2/2"),
        ("string-materialization", "5/5"),
    ):
        fixture = CORPUS.by_id(fixture_id)
        case = run_fixture(fixture, tmp_path)
        assert case.verdict is FixtureVerdict.AS_DECLARED, case.detail
        assert expected_fragment in case.observed


def test_a_missing_fixture_source_is_reported_not_ignored(tmp_path: Path) -> None:
    broken = BenchmarkFixture(
        fixture_id="broken",
        kind=CaseKind.SUBSYSTEM,
        source_file="does_not_exist.py",
        target="target.py::validate",
        description="deliberately broken",
        defect=SeededDefect.NONE,
        expected="",
    )

    with pytest.raises(FileNotFoundError):
        run_fixture(broken, tmp_path)


def test_a_fixture_declaring_no_policy_is_an_error_not_a_pass(tmp_path: Path) -> None:
    incomplete = BenchmarkFixture(
        fixture_id="incomplete",
        kind=CaseKind.SEARCH,
        source_file="correct_rejection.py",
        target="target.py::validate",
        description="declares no policy",
        defect=SeededDefect.NONE,
        expected="violation_reproduced",
        contract_id="c",
        start_int=-1,
        policies=(),
    )

    case = run_fixture(incomplete, tmp_path)

    assert case.verdict is FixtureVerdict.ERROR


def test_a_corpus_can_be_run_case_by_case(tmp_path: Path) -> None:
    """Running one fixture must not depend on another having run first."""

    first = run_fixture(CORPUS.by_id("correct-rejection"), tmp_path)
    second = run_fixture(CORPUS.by_id("correct-rejection"), tmp_path)

    assert first.verdict is second.verdict
    assert first.observed == second.observed


def test_a_custom_corpus_is_accepted_without_touching_the_default(tmp_path: Path) -> None:
    custom = Corpus(version="test", fixtures=(CORPUS.by_id("anchor-fault"),))
    assert custom.ids == ("anchor-fault",)
    assert CORPUS.version == CORPUS_VERSION, "the default corpus must not be mutated"
