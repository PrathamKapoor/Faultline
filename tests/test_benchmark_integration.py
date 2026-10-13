"""Real-Docker execution of the benchmark corpus.

This is where the numbers come from. Every measurement is produced by the real
engine against a real Linux container: the same `explore_integer`, the same
staging, the same worker, the same oracle evaluation, and the same reproduction
rule the CLI uses. There is no simulation and no mock runner.

What this file asserts is deliberately modest. It does **not** assert that the
adaptive policy wins, because on this corpus it does not: the deterministic
baseline reaches two seeded defects the neighbourhood policy never reaches.
Asserting a win would mean tuning the policy against the benchmark, which is the
one thing a benchmark must never be used for.

What it does assert:

* every case is classified as its declaration says it should be;
* the correct target produces no finding under either policy;
* both policies run under identical budgets on identical snapshots;
* the head-to-head aggregate covers only fixtures both policies ran;
* no container survives the run.

If Docker is unusable the module is skipped rather than silently passing.
"""

from __future__ import annotations

import subprocess

import pytest

from edgehunter.benchmark import (
    CORPUS,
    CorpusReport,
    FixtureVerdict,
    comparable_fixture_ids,
    head_to_head,
    run_corpus,
)
from edgehunter.execution.docker import docker_available
from edgehunter.execution.repository import WORKER_IMAGE, runtime_context

pytestmark = pytest.mark.integration

SEARCH_POLICIES = ("static_priority", "feedback_guided")


@pytest.fixture(scope="session")
def corpus_report() -> CorpusReport:
    available, detail = docker_available()
    if not available:
        pytest.skip(f"Docker is not usable here: {detail}")
    from edgehunter.execution.repository import ensure_image

    ready, image_detail = ensure_image(runtime_context())
    if not ready:
        pytest.skip(f"worker image could not be prepared: {image_detail}")
    assert WORKER_IMAGE
    return run_corpus(CORPUS)


def _edgehunter_containers() -> list[str]:
    listing = subprocess.run(
        ["docker", "ps", "--all", "--filter", "name=edgehunter-run-", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return [line for line in listing.stdout.splitlines() if line.strip()]


# --- corpus-level integrity --------------------------------------------------


def test_every_case_is_classified_as_declared(corpus_report: CorpusReport) -> None:
    """A corpus whose results disagree with its own documentation is a finding."""

    failures = [
        f"{case.fixture_id}: declared {case.declared!r}, observed {case.observed!r}"
        f"{' (' + case.detail + ')' if case.detail else ''}"
        for case in corpus_report.cases
        if case.verdict is not FixtureVerdict.AS_DECLARED
    ]

    assert not failures, "\n".join(failures)
    assert corpus_report.as_declared == len(corpus_report.cases)


def test_no_case_errored(corpus_report: CorpusReport) -> None:
    errored = [f"{case.fixture_id}: {case.detail}" for case in corpus_report.cases if case.detail]
    assert corpus_report.unexpected == 0, errored


# --- the negative result that makes the corpus worth having ------------------


def test_a_correct_target_produces_no_finding_under_either_policy(
    corpus_report: CorpusReport,
) -> None:
    """The false-positive check: silence is the only correct outcome here."""

    case = corpus_report.cases[0]
    assert case.fixture_id == "correct-rejection"

    for measurement in case.measurements:
        assert measurement.reproduced_count == 0
        assert measurement.mismatch_count == 0
        assert measurement.termination != "violation_reproduced"
        assert measurement.distinct_probes > 0, "it must actually have executed something"


def _head(report: CorpusReport) -> tuple[int, dict[str, int], list[str], list[dict[str, object]]]:
    """Narrow the head-to-head summary into the concrete shapes the tests use.

    `head_to_head` returns a JSON-shaped mapping so it can be written straight to
    the report file; this recovers the concrete types rather than asserting on
    `object` values.
    """

    raw = head_to_head(report)
    ties = raw.get("both_reproduced", 0)
    neither = raw.get("neither_reproduced", [])
    wins_raw = raw.get("reproduced_only_by", {})
    per_fixture = raw.get("per_fixture", [])
    assert isinstance(ties, int)
    assert isinstance(neither, list)
    assert isinstance(wins_raw, dict)
    assert isinstance(per_fixture, list)
    wins = {str(key): int(value) for key, value in wins_raw.items()}
    rows = [row for row in per_fixture if isinstance(row, dict)]
    return ties, wins, [str(name) for name in neither], rows


def test_the_head_to_head_reports_a_genuinely_mixed_result(corpus_report: CorpusReport) -> None:
    """Assert the *shape* of the comparison, never which policy wins.

    The observed result on this corpus is that the deterministic baseline reaches
    two seeded defects the neighbourhood policy never reaches, and that the
    neighbourhood policy wins none outright. That is reported, not asserted: if a
    future policy genuinely improves, the right response is to re-run and record
    the new numbers, not to have a test fail because the baseline stopped winning.

    What *is* asserted is that the comparison is genuinely mixed -- at least one
    fixture both policies reached, at least one case no policy reached, at least
    one outright win -- because a corpus where one policy swept everything would
    not be measuring anything.
    """

    ties, wins, neither, _rows = _head(corpus_report)

    assert ties > 0, wins
    assert neither, wins
    assert sum(wins.values()) > 0, wins


def test_a_policy_that_reaches_a_defect_the_other_misses_is_counted(
    corpus_report: CorpusReport,
) -> None:
    """The win accounting must be capable of crediting either policy.

    A win table hard-wired to favour one policy would make a future improvement
    invisible. This asserts only that the accounting is symmetric and that the
    recorded wins reconcile exactly with the per-fixture rows.
    """

    ties, wins, neither, rows = _head(corpus_report)
    sole_wins = [row for row in rows if len(row.get("reproduced_by", [])) == 1]  # type: ignore[arg-type]

    assert sum(wins.values()) == len(sole_wins)
    assert set(wins) <= set(SEARCH_POLICIES)
    assert ties + len(sole_wins) + len(neither) == len(rows)


# --- fairness ----------------------------------------------------------------


def test_every_compared_policy_ran_on_identical_budgets_and_snapshots(
    corpus_report: CorpusReport,
) -> None:
    comparable = comparable_fixture_ids(corpus_report)
    assert comparable, "no fixture was run by both policies"

    for fixture_id in comparable:
        rows = [row for row in corpus_report.measurements() if row.fixture_id == fixture_id]
        assert len(rows) == 2
        universes = {row.candidate_universe for row in rows}
        assert len(universes) == 1, f"{fixture_id}: policies saw different universes {universes}"


def test_reproduction_required_a_second_execution_everywhere(corpus_report: CorpusReport) -> None:
    """A reproduced finding must cost more executions than it has probes."""

    for row in corpus_report.measurements():
        if row.reproduced_count:
            assert row.container_executions > row.distinct_probes, row.as_row()


def test_no_reported_finding_exceeded_the_default_budget(corpus_report: CorpusReport) -> None:
    for row in corpus_report.measurements():
        assert row.distinct_probes <= 8, row.as_row()
        assert row.container_executions <= 12, row.as_row()


def test_the_trace_of_every_measurement_is_recorded(corpus_report: CorpusReport) -> None:
    for row in corpus_report.measurements():
        assert len(row.trace) == row.distinct_probes + row.reproduction_executions, row.as_row()


# --- honesty of the aggregate ------------------------------------------------


def test_the_head_to_head_covers_only_fixtures_both_policies_ran(
    corpus_report: CorpusReport,
) -> None:
    comparable = set(comparable_fixture_ids(corpus_report))
    _ties, _wins, _neither, rows = _head(corpus_report)

    assert rows
    for entry in rows:
        name = entry.get("fixture")
        assert name in comparable
        policies = {key for key in entry if key not in ("fixture", "reproduced_by")}
        assert policies == set(SEARCH_POLICIES)


def test_a_missed_defect_is_never_counted_as_a_fast_reproduction(
    corpus_report: CorpusReport,
) -> None:
    for row in corpus_report.measurements():
        if row.reproduced_count == 0:
            assert row.first_reproduced_probe is None
            assert "mean" not in str(row.as_row())


def test_every_measurement_records_a_termination_and_a_reason(corpus_report: CorpusReport) -> None:
    for row in corpus_report.measurements():
        assert row.termination
        assert row.termination_reason.strip(), row.as_row()


# --- cleanup -----------------------------------------------------------------


def test_no_edgehunter_container_survives_the_corpus() -> None:
    assert _edgehunter_containers() == []
