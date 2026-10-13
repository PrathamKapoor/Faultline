"""Whether the available oracle evidence survives changing an input.

This module is the answer to the question Phase 6B left open: an exact test
expectation describes one invocation, so it is not a starting point for
shrinking. The rules are one-way, and they are all refusals except one:

============================================  =====================
evidence                                       shrinkability
============================================  =====================
``assert f(100) == 200``                        ``EXACT_INPUT_ONLY``
``with pytest.raises(ValueError): f(100)``      ``EXACT_INPUT_ONLY``
unresolved or ambiguous callee                  ``INCONCLUSIVE``
production ``assert``                           ``UNSUPPORTED_ORACLE``
production ``if cond: raise`` guard             ``UNSUPPORTED_ORACLE``
unverified source snapshot                      ``UNVERIFIED_SOURCE``
explicitly selected user contract               ``REUSABLE_CONTRACT``
============================================  =====================

The refusal is a refusal, not a deferral: an exact-input oracle is rejected
*before* anything is executed, so a smaller input is never run and then judged
against an oracle that does not cover it. There is no flag that bypasses this,
and a production guard is never promoted to a reusable contract no matter how
simple its condition looks.
"""

from __future__ import annotations

from edgehunter.evaluation.models import OracleStatus
from edgehunter.oracle.models import (
    ExpectedOutcome,
    OracleEvidence,
    ProductionPropertyCandidate,
    PropertyKind,
    TargetResolution,
    TestExpectationCandidate,
)
from edgehunter.shrink.models import (
    OracleSource,
    ShrinkabilityAssessment,
    ShrinkabilityKind,
)


def _location(evidence: tuple[OracleEvidence, ...]) -> str:
    """``path:line`` of the first evidence record, or an empty string."""

    for entry in evidence:
        path = getattr(entry, "relative_path", None)
        line = getattr(entry, "lineno", None)
        if path is not None and line is not None:
            return f"{path.as_posix()}:{line}"
    return ""


def assess_test_expectation(expectation: TestExpectationCandidate) -> ShrinkabilityAssessment:
    """Classify one mined test expectation.

    Both mined shapes are exact-input: `assert f(1) == 2` and
    `with pytest.raises(E): f(1)` each record one invocation with one set of
    arguments. Neither states anything about a different input, so neither can
    authorize a shrink step.
    """

    location = _location(expectation.evidence)
    source = (
        OracleSource.TEST_ASSERTION
        if expectation.kind is PropertyKind.EXAMPLE_EXPECTATION
        else OracleSource.TEST_RAISES
    )
    description = expectation.call_expression or expectation.callee

    if expectation.resolution is not TargetResolution.RESOLVED:
        reason = (
            "the expectation's callee did not resolve to exactly one target "
            f"({expectation.resolution_reason}), so its applicability is unknown"
        )
        return ShrinkabilityAssessment(
            kind=ShrinkabilityKind.INCONCLUSIVE,
            reason=reason,
            source=source,
            oracle_description=description,
            oracle_location=location,
        )

    if (
        expectation.expected_outcome is ExpectedOutcome.RAISES
        and expectation.exception is not None
        and not is_builtin_exception_name(expectation.exception)
    ):
        # A custom expected class is never identity-resolved, so even the exact
        # invocation's verdict is undecided, let alone a different one.
        return ShrinkabilityAssessment(
            kind=ShrinkabilityKind.INCONCLUSIVE,
            reason=(
                f"the expectation names {expectation.exception!r}, which is not a real builtin "
                "exception class, so its identity is never resolved and no verdict is available"
            ),
            source=source,
            oracle_description=description,
            oracle_location=location,
        )

    return ShrinkabilityAssessment(
        kind=ShrinkabilityKind.EXACT_INPUT_ONLY,
        reason=(
            "the oracle is an exact-input expectation: it describes one recorded invocation, so "
            "changing the input invalidates its applicability and no reusable property has been "
            "established"
        ),
        source=source,
        oracle_description=description,
        oracle_location=location,
    )


def assess_production_property(
    property_candidate: ProductionPropertyCandidate,
) -> ShrinkabilityAssessment:
    """Classify one mined production property.

    Neither an ``assert`` nor an ``if cond: raise`` guard is a contract over an
    input domain. An assertion states a precondition at one execution point, and a
    guard says nothing about whether its branch is reached. Phase 6B already
    refuses to generalize them (D-063), and shrinking is the same mistake with
    more machinery: it would re-apply a domain the repository never declared.
    """

    location = _location(property_candidate.evidence)
    if property_candidate.kind is PropertyKind.ASSERTED_CONDITION:
        source = OracleSource.PRODUCTION_ASSERTION
        reason = (
            "the oracle is a production assertion: it constrains a condition at one execution "
            "point, not an input domain, and guard reachability is never inferred"
        )
    else:
        source = OracleSource.VALIDATION_GUARD
        reason = (
            "the oracle is a production validation guard: the source shows an `if ...: raise` "
            "rule, but nothing proves which inputs reach it, so it is not a reusable contract"
        )
    return ShrinkabilityAssessment(
        kind=ShrinkabilityKind.UNSUPPORTED_ORACLE,
        reason=reason,
        source=source,
        oracle_description=property_candidate.condition,
        oracle_location=location,
    )


def assess_unverified_source(
    assessments: tuple[ShrinkabilityAssessment, ...], detail: str
) -> ShrinkabilityAssessment:
    """Classify the operation when the executed revision cannot be proven."""

    return ShrinkabilityAssessment(
        kind=ShrinkabilityKind.UNVERIFIED_SOURCE,
        reason=(
            f"the source snapshot could not be verified: {detail}; without a proven executed "
            "revision no observation can be attributed to a specific contract"
        ),
        source=OracleSource.NONE,
        oracle_description=f"{len(assessments)} candidate oracle(s) considered",
    )


def is_builtin_exception_name(name: str) -> bool:
    """Whether a bare name is a real standard-library exception class.

    Delegates to the evaluator's own hierarchy derivation so there is one
    implementation of "is this a builtin exception" in the project.
    """

    from edgehunter.evaluation.exceptions import is_builtin_exception

    return is_builtin_exception(name.rsplit(".", 1)[-1])


def refuse_conflicting(assessments: tuple[ShrinkabilityAssessment, ...]) -> str | None:
    """Describe an exact-input oracle that covers the selected invocation, if any.

    Phase 6B's conflict rule (D-064) applies here with full force: a repository's
    own test is not discarded because a user declaration is more convenient. If
    an exact test expectation applies to the invocation about to be shrunk, the
    shrinker refuses rather than choosing the oracle that permits shrinking.
    """

    for assessment in assessments:
        if assessment.kind is ShrinkabilityKind.EXACT_INPUT_ONLY:
            where = f" at {assessment.oracle_location}" if assessment.oracle_location else ""
            return (
                "an exact-input expectation from the repository applies to this invocation"
                f"{where} ({assessment.oracle_description}); it must be evaluated, not discarded "
                "in favour of a user declaration, so shrinking is refused as conflicting evidence"
            )
    return None


def assess_target_shrinkability(
    expectations: tuple[TestExpectationCandidate, ...],
    properties: tuple[ProductionPropertyCandidate, ...],
    target_name: str,
) -> ShrinkabilityAssessment:
    """Decide whether the repository's own evidence alone permits an input change.

    This is the §7 question asked directly: with nothing but what the repository
    records about this target, is there any oracle that survives a different
    input? For mined test expectations and mined production properties the answer
    is always no -- that is the whole point -- and this function names which
    evidence was found and why it is not reusable.

    A ``REUSABLE_CONTRACT`` result is deliberately unreachable from here. The only
    source of one is an explicitly selected contract file, so returning it from a
    function that reads only mined evidence would be a lie.
    """

    assessments: list[ShrinkabilityAssessment] = [
        assess_test_expectation(item)
        for item in expectations
        if item.target is not None and item.target == target_name
    ]
    assessments.extend(
        assess_production_property(item) for item in properties if item.target == target_name
    )

    if not assessments:
        return ShrinkabilityAssessment(
            kind=ShrinkabilityKind.INCONCLUSIVE,
            reason=(
                f"the repository records no oracle evidence at all for {target_name}, so there is "
                "no expectation a smaller input could be checked against"
            ),
            source=OracleSource.NONE,
            oracle_description=f"no evidence for {target_name}",
        )

    exact = [item for item in assessments if item.kind is ShrinkabilityKind.EXACT_INPUT_ONLY]
    if exact:
        first = exact[0]
        where = f" at {first.oracle_location}" if first.oracle_location else ""
        return ShrinkabilityAssessment(
            kind=ShrinkabilityKind.EXACT_INPUT_ONLY,
            reason=(
                f"the oracle is an exact-input expectation{where} ({first.oracle_description}); it "
                "describes one recorded invocation, so changing the input invalidates its "
                "applicability. No reusable property has been established, so this input cannot "
                "be shrunk against it."
            ),
            source=first.source,
            oracle_description=first.oracle_description,
            oracle_location=first.oracle_location,
        )

    first = assessments[0]
    return ShrinkabilityAssessment(
        kind=first.kind,
        reason=first.reason,
        source=first.source,
        oracle_description=first.oracle_description,
        oracle_location=first.oracle_location,
    )


def is_decided(status: OracleStatus) -> bool:
    """Whether a status is a usable verdict rather than a refusal.

    Used by the contract adapter: an ``INCONCLUSIVE`` or ``UNSUPPORTED`` result
    must never be treated as a mismatch to shrink against.
    """

    return status in (OracleStatus.MATCH, OracleStatus.MISMATCH)
