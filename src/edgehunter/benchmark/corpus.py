"""The versioned corpus declaration.

Every fixture here is a real Python file shipped in this package, executed
through the real engine against a real Docker sandbox. Nothing is mocked: the
harness calls the same `explore_integer` the CLI calls, through the same
staging, container, and oracle-evaluation route.

The corpus is deliberately mixed. It contains cases where the feedback-guided
policy reaches a contradiction first, a case where it ties, two cases where the
static baseline is *better* (the fault sits at the user seed, and far from the
anchor), and a correct target where **neither** policy may claim anything. A
corpus that could only produce a favourable result would measure nothing.

Fixtures are data, not project code: they are excluded from this project's own
lint and type-check gates in `pyproject.toml`, for the same reason test fixtures
are excluded elsewhere -- they are deliberately written targets, not library code.
"""

from __future__ import annotations

from edgehunter.benchmark.models import (
    CORPUS_VERSION,
    BenchmarkFixture,
    CaseKind,
    Corpus,
    SeededDefect,
)

__all__ = ["CORPUS", "CORPUS_VERSION"]

_BOTH = ("static_priority", "feedback_guided")

CORPUS: Corpus = Corpus(
    version=CORPUS_VERSION,
    fixtures=(
        # --- search cases, run under both policies -----------------------
        BenchmarkFixture(
            fixture_id="correct-rejection",
            kind=CaseKind.NEGATIVE,
            source_file="correct_rejection.py",
            target="target.py::validate",
            description="A correct validator that raises for every in-domain input.",
            defect=SeededDefect.NONE,
            expected="no_reproduced_violation",
            known_trigger=None,
            contract_id="negative-rejected",
            start_int=-64,
            policies=_BOTH,
        ),
        BenchmarkFixture(
            fixture_id="interior-value-fault",
            kind=CaseKind.SEARCH,
            source_file="missing_rejection.py",
            target="target.py::validate",
            description="Rejection missing at one interior value (-5).",
            defect=SeededDefect.INTERIOR_VALUE_FAULT,
            expected="violation_reproduced",
            known_trigger=-5,
            contract_id="negative-rejected",
            start_int=-64,
            policies=_BOTH,
        ),
        BenchmarkFixture(
            fixture_id="anchor-fault",
            kind=CaseKind.SEARCH,
            source_file="anchor_fault.py",
            target="target.py::validate",
            description="Rejection missing at the domain anchor (-1): a tie between policies.",
            defect=SeededDefect.ANCHOR_FAULT,
            expected="violation_reproduced",
            known_trigger=-1,
            contract_id="negative-rejected",
            start_int=-64,
            policies=_BOTH,
        ),
        BenchmarkFixture(
            fixture_id="seed-fault",
            kind=CaseKind.SEARCH,
            source_file="seed_fault.py",
            target="target.py::validate",
            description=(
                "Rejection missing at the user seed (-64), where the static order is better "
                "than proximity."
            ),
            defect=SeededDefect.SEED_FAULT,
            expected="violation_reproduced",
            known_trigger=-64,
            contract_id="negative-rejected",
            start_int=-64,
            policies=_BOTH,
        ),
        BenchmarkFixture(
            fixture_id="far-value-fault",
            kind=CaseKind.SEARCH,
            source_file="far_value_fault.py",
            target="target.py::validate",
            description=(
                "Rejection missing at -1000, out of reach of a neighbourhood walk: the "
                "adaptive policy's honest failure mode."
            ),
            defect=SeededDefect.FAR_VALUE_FAULT,
            expected="violation_reproduced",
            known_trigger=-1000,
            contract_id="negative-rejected",
            start_int=-64,
            policies=_BOTH,
        ),
        BenchmarkFixture(
            fixture_id="non-monotonic",
            kind=CaseKind.SEARCH,
            source_file="non_monotonic.py",
            target="target.py::validate",
            description="Behaviour alternates across the domain; no neighbourhood rule is right.",
            defect=SeededDefect.NON_MONOTONIC,
            expected="violation_reproduced",
            known_trigger=-1,
            contract_id="negative-rejected",
            start_int=-64,
            policies=("feedback_guided",),
        ),
        BenchmarkFixture(
            fixture_id="wrong-exception",
            kind=CaseKind.SEARCH,
            source_file="wrong_exception.py",
            target="target.py::validate",
            description="Raises where it should reject, but with the wrong class.",
            defect=SeededDefect.WRONG_EXCEPTION,
            expected="violation_reproduced",
            known_trigger=-1,
            contract_id="negative-rejected",
            start_int=-64,
            policies=("feedback_guided",),
        ),
        # --- subsystem cases: assert one property, measure no policy ----
        BenchmarkFixture(
            fixture_id="unsupported-shape",
            kind=CaseKind.SUBSYSTEM,
            source_file="unsupported_shape.py",
            target="target.py::validate",
            description="A two-parameter, defaulted, non-int-returning target.",
            defect=SeededDefect.UNSUPPORTED_SHAPE,
            expected="refused",
            contract_id="negative-rejected",
            start_int=-64,
            subsystem="An unsupported target shape is refused before any execution.",
        ),
        BenchmarkFixture(
            fixture_id="fingerprint-mismatch",
            kind=CaseKind.SUBSYSTEM,
            source_file="correct_rejection.py",
            target="target.py::validate",
            description="A contract bound to a digest the source no longer has.",
            defect=SeededDefect.NONE,
            expected="refused",
            contract_id="negative-rejected",
            start_int=-64,
            subsystem="A stale source fingerprint is refused, never silently rebound.",
        ),
        BenchmarkFixture(
            fixture_id="bool-materialization",
            kind=CaseKind.SUBSYSTEM,
            source_file="mixed_types.py",
            target="target.py::describe",
            description="A boolean parameter must reach concrete False and True plans.",
            defect=SeededDefect.NONE,
            expected="",
            subsystem="bool parameters produce concrete True/False materializations.",
        ),
        BenchmarkFixture(
            fixture_id="string-materialization",
            kind=CaseKind.SUBSYSTEM,
            source_file="mixed_types.py",
            target="target.py::describe",
            description="A string parameter must reach all five declared edge shapes.",
            defect=SeededDefect.NONE,
            expected="",
            subsystem="str parameters produce all five edge-case materializations.",
        ),
        BenchmarkFixture(
            fixture_id="sandbox-unavailable",
            kind=CaseKind.SUBSYSTEM,
            source_file="correct_rejection.py",
            target="target.py::validate",
            description="A simulated backend outage, with no host fallback permitted.",
            defect=SeededDefect.NONE,
            expected="sandbox_unavailable",
            contract_id="negative-rejected",
            start_int=-64,
            subsystem="An unavailable sandbox is reported; nothing is executed on the host.",
        ),
    ),
)
