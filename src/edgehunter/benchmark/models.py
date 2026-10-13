"""Typed vocabulary for the versioned benchmark corpus and its measurements.

The corpus exists to answer one question honestly: *does feedback-guided search
beat a deterministic baseline, and by how much?* That question cannot be answered
by one fixture, so the corpus is a small set of independently understandable
Python targets with **known** seeded behaviour, and the harness records what
actually happened rather than what would be convenient.

Three things are deliberately absent from every measurement here, and their
absence is the point:

* no coverage figure, because nothing instruments coverage;
* no mutation score, because nothing mutates;
* no probability that a defect exists, because a reproduced contradiction against
  a user-declared contract is evidence of *disagreement with that contract* and
  says nothing about whether the contract is right.

The corpus contains cases where the baseline is better than the adaptive policy
and cases where neither policy finds anything. A corpus that could only produce
a positive result would measure nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

__all__ = [
    "BENCHMARK_SCHEMA",
    "CORPUS_VERSION",
    "BenchmarkFixture",
    "CaseKind",
    "Corpus",
    "CorpusReport",
    "FixtureVerdict",
    "PolicyMeasurement",
    "SeededDefect",
    "aggregate_probes_to_first_reproduction",
    "comparable_fixture_ids",
    "head_to_head",
    "write_corpus_report",
]


class CaseKind(str, Enum):
    """What a corpus case actually exercises."""

    SEARCH = "search"
    """Run one or both search policies and record their measurements."""

    SUBSYSTEM = "subsystem"
    """Assert one specific subsystem behaviour instead of measuring a policy.

    Used for materialization, applicability, refusal, and resource cases. Forcing
    every case through the search loop would produce a number that means nothing,
    because the property under test is not about which input was chosen next.
    """

    NEGATIVE = "negative"
    """Both policies must fail to find anything.

    A corpus that cannot show the search finding nothing -- and saying so -- would
    not be able to detect a policy that reports a contradiction on a correct
    target.
    """


class SeededDefect(str, Enum):
    """The known behaviour deliberately written into a fixture.

    Naming it makes the corpus auditable: a reader can check the source against
    the declaration. ``NONE`` is a first-class value, because "this target is
    correct" is a case the corpus must contain, not an absence of one.
    """

    NONE = "none"
    """The target behaves as its contract declares across the whole domain."""

    MISSING_REJECTION = "missing_rejection"
    """One in-domain value returns normally where the contract expects a raise."""

    WRONG_EXCEPTION = "wrong_exception"
    """The expected exception class is not the one raised."""

    INTERIOR_VALUE_FAULT = "interior_value_fault"
    """A fault at one specific interior value, away from the domain anchor."""

    ANCHOR_FAULT = "anchor_fault"
    """A fault at the domain anchor itself, which every policy meets first."""

    SEED_FAULT = "seed_fault"
    """A fault at the user-supplied starting value.

    Deliberately included because it is the case where the static baseline's
    documented order is *better* than proximity.
    """

    FAR_VALUE_FAULT = "far_value_fault"
    """A fault far from both the anchor and the seed.

    Deliberately included because proximity cannot reach it within a small
    budget, which is the honest failure mode of the adaptive policy.
    """

    NON_MONOTONIC = "non_monotonic"
    """Behaviour alternates across the domain, so no neighbourhood rule is right."""

    UNSUPPORTED_SHAPE = "unsupported_shape"
    """The target is outside the supported shape and must be refused."""

    CONFLICTING_ORACLE = "conflicting_oracle"
    """An exact test expectation contradicts the declared contract."""


class FixtureVerdict(str, Enum):
    """What the harness observed for one case, checked against the declaration."""

    AS_DECLARED = "as_declared"
    """The observation matched the fixture's declared expectation."""

    UNEXPECTED = "unexpected"
    """The observation contradicted the declaration.

    This is a finding about the *corpus*, not about the target: either the engine
    changed behaviour or the declaration was wrong. It is recorded rather than
    smoothed over, because a benchmark whose cases silently disagree with its own
    documentation is worse than no benchmark.
    """

    ERROR = "error"
    """The harness itself could not run the case."""


@dataclass(frozen=True, slots=True)
class BenchmarkFixture:
    """One independently understandable Python target with known behaviour."""

    fixture_id: str
    """Stable identifier used in every report. Never derived from a path."""

    kind: CaseKind
    source_file: str
    """The shipped fixture source, e.g. ``correct_rejection.py``.

    Deliberately separate from :attr:`target`: every fixture is copied into a
    scratch repository under one common name so that a contract can bind it, and
    conflating the two would make the contract's target depend on which file the
    corpus happened to store it in.
    """

    target: str
    """``target.py::qualified_name`` as it exists inside the scratch repository."""

    description: str
    defect: SeededDefect
    expected: str
    """The classification the engine is expected to reach, or ``""`` when the case
    is descriptive rather than assertive."""

    known_trigger: int | None = None
    """The input known to contradict the contract, when one exists."""

    contract_id: str | None = None
    """``None`` means the case has no reusable contract and must be refused."""

    start_int: int | None = None
    domain: tuple[str, str] = ("lt", "0")
    expectation: str = "ValueError"

    policies: tuple[str, ...] = ()
    """Which policies to run. A case measuring one subsystem names none."""

    subsystem: str = ""
    """What a ``SUBSYSTEM`` case asserts, in one readable line."""

    def domain_text(self) -> str:
        operator, reference = self.domain
        return f"{operator} {reference}"


@dataclass(frozen=True, slots=True)
class PolicyMeasurement:
    """One policy's observed run of one fixture.

    Every field is counted from a real execution. Nothing here is estimated, and
    nothing here is compared across cases that were not measured the same way.
    """

    fixture_id: str
    policy: str
    candidate_universe: int
    distinct_probes: int
    container_executions: int
    match_count: int
    mismatch_count: int
    inconclusive_count: int
    reproduced_count: int
    first_mismatch_probe: int | None
    first_reproduced_probe: int | None
    termination: str
    termination_reason: str
    wall_clock_seconds: float
    reproduction_executions: int = 0
    """Container executions spent on verification reruns.

    Tracked separately from ``distinct_probes`` because a rerun is not a new
    probe: it repeats an input already tried. A reproduced finding must always
    cost at least one of these, which is the trace-level proof that reproduction
    was never skipped.
    """

    trace: tuple[dict[str, object], ...] = ()
    """The full per-probe trace, so a number can always be traced back to a run."""

    @property
    def found_within_budget(self) -> bool:
        return self.termination == "violation_reproduced"

    def as_row(self) -> dict[str, object]:
        return {
            "fixture": self.fixture_id,
            "policy": self.policy,
            "universe": self.candidate_universe,
            "probes": self.distinct_probes,
            "executions": self.container_executions,
            "match": self.match_count,
            "mismatch": self.mismatch_count,
            "inconclusive": self.inconclusive_count,
            "reproduced": self.reproduced_count,
            "recheck_executions": self.reproduction_executions,
            "first_mismatch": self.first_mismatch_probe,
            "first_reproduced": self.first_reproduced_probe,
            "termination": self.termination,
            "wall_clock_s": self.wall_clock_seconds,
        }


@dataclass(frozen=True, slots=True)
class Corpus:
    """A versioned, bounded set of fixtures."""

    version: str
    fixtures: tuple[BenchmarkFixture, ...]

    def by_id(self, fixture_id: str) -> BenchmarkFixture:
        for fixture in self.fixtures:
            if fixture.fixture_id == fixture_id:
                return fixture
        known = ", ".join(sorted(f.fixture_id for f in self.fixtures))
        raise KeyError(f"unknown benchmark fixture {fixture_id!r}; known fixtures: {known}")

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(fixture.fixture_id for fixture in self.fixtures)


@dataclass(frozen=True, slots=True)
class CaseReport:
    """One case's declaration next to what was actually observed."""

    fixture_id: str
    kind: CaseKind
    declared: str
    observed: str
    verdict: FixtureVerdict
    detail: str = ""
    measurements: tuple[PolicyMeasurement, ...] = ()

    def as_row(self) -> dict[str, object]:
        return {
            "fixture": self.fixture_id,
            "kind": self.kind.value,
            "declared": self.declared,
            "observed": self.observed,
            "verdict": self.verdict.value,
        }


@dataclass(frozen=True, slots=True)
class CorpusReport:
    """The whole run, with aggregates computed only where they are comparable."""

    corpus_version: str
    cases: tuple[CaseReport, ...] = ()
    aggregates: dict[str, object] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    """Comparability caveats, recorded next to the numbers they qualify."""

    @property
    def as_declared(self) -> int:
        return sum(1 for case in self.cases if case.verdict is FixtureVerdict.AS_DECLARED)

    @property
    def unexpected(self) -> int:
        return sum(1 for case in self.cases if case.verdict is FixtureVerdict.UNEXPECTED)

    def measurements(self, policy: str | None = None) -> tuple[PolicyMeasurement, ...]:
        rows = [
            measurement
            for case in self.cases
            for measurement in case.measurements
            if policy is None or measurement.policy == policy
        ]
        return tuple(rows)


BENCHMARK_SCHEMA: Final[int] = 1
CORPUS_VERSION: Final[str] = "1.0.0"


def comparable_fixture_ids(report: CorpusReport) -> tuple[str, ...]:
    """Fixtures every compared policy actually ran.

    A mean over "the cases this policy happened to run" is not a comparison. If
    one policy ran two extra easy fixtures its average would drop for reasons
    that have nothing to do with how well it chooses inputs. Aggregates are
    therefore computed over the intersection only, with the per-policy totals
    reported beside them so the difference stays visible.
    """

    by_fixture: dict[str, set[str]] = {}
    for row in report.measurements():
        by_fixture.setdefault(row.fixture_id, set()).add(row.policy)

    policies = {row.policy for row in report.measurements()}
    if len(policies) < 2:
        return ()
    return tuple(sorted(fixture_id for fixture_id, seen in by_fixture.items() if policies <= seen))


def aggregate_probes_to_first_reproduction(
    report: CorpusReport, policy: str, *, restrict_to: tuple[str, ...] = ()
) -> dict[str, object]:
    """Probe counts for one policy, over the fixtures it is being compared on.

    Only cases that **reproduced** a contradiction contribute a probe count. A
    case that found nothing has no "probes to first reproduction" to average,
    and averaging it as zero would invent a result the run did not produce --
    which matters most here, because a policy that missed a seeded defect would
    otherwise look *better* for having used fewer probes.
    """

    allowed = set(restrict_to)
    rows = [row for row in report.measurements(policy) if not allowed or row.fixture_id in allowed]
    reproduced = [row.first_reproduced_probe for row in rows if row.first_reproduced_probe]

    summary: dict[str, object] = {
        "policy": policy,
        "cases_measured": len(rows),
        "cases_reproduced": len(reproduced),
        "cases_not_reproduced": len(rows) - len(reproduced),
    }
    if reproduced:
        summary["mean_probes_to_first_reproduction"] = round(sum(reproduced) / len(reproduced), 2)
        summary["min_probes_to_first_reproduction"] = min(reproduced)
        summary["max_probes_to_first_reproduction"] = max(reproduced)
    summary["note"] = (
        "The mean covers only cases that reproduced a contradiction. A case that reproduced "
        "nothing has no probes-to-reproduction to contribute and is counted under "
        "cases_not_reproduced; treating it as zero would reward a policy for missing a defect."
    )
    return summary


def head_to_head(report: CorpusReport) -> dict[str, object]:
    """Per-fixture comparison over the fixtures every policy ran.

    This is the only fair shape for the question being asked. One average per
    policy over its own case set would answer "which set of fixtures was
    easier", not "which policy chose better inputs".
    """

    comparable = comparable_fixture_ids(report)
    per_fixture: list[dict[str, object]] = []
    wins: dict[str, int] = {}
    ties = 0
    undetected: list[str] = []

    for fixture_id in comparable:
        rows = {row.policy: row for row in report.measurements() if row.fixture_id == fixture_id}
        entry: dict[str, object] = {"fixture": fixture_id}
        reproduced_by: list[str] = []
        for policy, row in sorted(rows.items()):
            entry[policy] = {
                "reproduced": row.reproduced_count,
                "probes_to_reproduction": row.first_reproduced_probe,
                "executions": row.container_executions,
                "termination": row.termination,
            }
            if row.reproduced_count:
                reproduced_by.append(policy)
        entry["reproduced_by"] = reproduced_by
        per_fixture.append(entry)

        if not reproduced_by:
            undetected.append(fixture_id)
        elif len(reproduced_by) == len(rows):
            ties += 1
        else:
            for policy in reproduced_by:
                wins[policy] = wins.get(policy, 0) + 1

    return {
        "comparable_fixtures": list(comparable),
        "per_fixture": per_fixture,
        "reproduced_only_by": dict(sorted(wins.items())),
        "both_reproduced": ties,
        "neither_reproduced": undetected,
        "note": (
            "A fixture counts only for a policy that reproduced the seeded contradiction. A "
            "policy that never reached it gains nothing, and a fixture neither policy reached "
            "is reported rather than dropped."
        ),
    }


def write_corpus_report(report: CorpusReport, path: str, *, run_id: str = "benchmark") -> str:
    """Write the corpus report as strict JSON.

    No measured duration is invented: `wall_clock_seconds` is a real observation
    and is recorded as one, alongside the rest.
    """

    from edgehunter.evaluation.repository import _safe_run_id

    document = {
        "schema_version": BENCHMARK_SCHEMA,
        "kind": "benchmark_corpus",
        "run_id": _safe_run_id(run_id),
        "corpus_version": report.corpus_version,
        "cases": [case.as_row() for case in report.cases],
        "measurements": [row.as_row() for row in report.measurements()],
        "traces": {
            f"{row.fixture_id}:{row.policy}": list(row.trace) for row in report.measurements()
        },
        "aggregates": report.aggregates,
        "notes": list(report.notes),
        "meaning": (
            "Synthetic fixtures with deliberately seeded behaviour, executed through the real "
            "EdgeHunter engine against a real Docker sandbox. These are measurements of this "
            "corpus only. They are not a claim about any real repository, not a coverage figure, "
            "and not evidence that one policy is generally better than another."
        ),
    }
    target = path
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    return target
