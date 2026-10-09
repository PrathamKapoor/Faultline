"""The evaluator: applicability first, then the verdict, never the reverse.

:func:`evaluate_oracle` decides one oracle against one observation.
:func:`evaluate_observation` runs the whole set for one invocation and reconciles
them.

The ordering is the load-bearing part. :func:`evaluate_oracle` returns a
``NOT_APPLICABLE`` result without ever comparing a value, so a structurally
unrelated expectation can never be read as a contradiction.

Reconciliation follows deliberately conservative rules:

* **One mismatch is not necessarily a finding.** If any *applicable* oracle
  matched, a contradiction elsewhere is recorded but not promoted, because the
  repository's own evidence disagrees and picking the convenient one is guessing.
* **Disagreeing applicable oracles are ``CONFLICTING_EVIDENCE``.** Two tests
  asserting ``f(1) == 2`` and ``f(1) == 3`` is a fact about the repository, not a
  fact about the code. No defect is claimed from it.
* **Infrastructure failures never become violations.** ``TIMEOUT``,
  ``IMPORT_FAILURE``, ``PROTOCOL_ERROR``, ``WORKER_FAILURE``, ``TARGET_NOT_FOUND``,
  ``RESOURCE_LIMIT``, and an unverified source snapshot are ``INCONCLUSIVE``.
* **A return expectation contradicted by a raised exception is ``MISMATCH``**,
  because the recorded expectation was that the call *returns*. That is different
  from the failure modes above, where the target was never observed at all.
* **A production assertion hit is observed, not confirmed.** It yields
  ``ASSERTION_OBSERVED`` and asks for review, because an assertion is frequently a
  precondition rather than a defect.
"""

from __future__ import annotations

from dataclasses import dataclass

from edgehunter.analysis.models import ComparisonOperator
from edgehunter.evaluation.applicability import (
    InvocationSignature,
    check_applicability,
    expectations_for_target,
    unresolved_for_target,
)
from edgehunter.evaluation.compare import (
    ComparisonOutcome,
    canonical_value_text_key,
    compare_typed_values,
    status_for,
)
from edgehunter.evaluation.exceptions import (
    ExceptionIdentity,
    match_exception_observed,
)
from edgehunter.evaluation.models import (
    ApplicabilityKind,
    AppliedOracle,
    BoundCall,
    OracleEvidenceRef,
    OracleKind,
    OracleStatus,
)
from edgehunter.evaluation.values import canonical_value_text, describe_typed, materialize
from edgehunter.execution.codec import ProtocolError, TypedValue, recognize_literal
from edgehunter.execution.models import ExecutionResult, OutcomeKind, RuntimeObservation
from edgehunter.oracle.models import (
    ExpectedOutcome,
    OracleEvidence,
    OracleEvidenceKind,
    ProductionPropertyCandidate,
    PropertyKind,
    TestExpectationCandidate,
)

# Outcomes where the target's own behaviour was never observed, so no oracle could
# have been contradicted by it.
_INFRASTRUCTURE_OUTCOMES: frozenset[OutcomeKind] = frozenset(
    {
        OutcomeKind.TIMEOUT,
        OutcomeKind.IMPORT_FAILURE,
        OutcomeKind.TARGET_NOT_FOUND,
        OutcomeKind.PROTOCOL_ERROR,
        OutcomeKind.WORKER_FAILURE,
        OutcomeKind.RESOURCE_LIMIT,
        OutcomeKind.INELIGIBLE,
    }
)

_EVIDENCE_KINDS: dict[OracleEvidenceKind, OracleKind] = {
    OracleEvidenceKind.TEST_ASSERTION: OracleKind.TEST_ASSERTION,
    OracleEvidenceKind.TEST_RAISES: OracleKind.TEST_RAISES,
    OracleEvidenceKind.PRODUCTION_ASSERTION: OracleKind.PRODUCTION_ASSERTION,
    OracleEvidenceKind.VALIDATION_GUARD: OracleKind.VALIDATION_GUARD,
}


def evaluate_oracle(
    expectation: TestExpectationCandidate,
    signature: InvocationSignature,
    actual: BoundCall,
    observation: RuntimeObservation,
    *,
    snapshot_verified: bool,
) -> AppliedOracle:
    """Decide one recorded test expectation against one observation.

    Applicability is checked first and unconditionally. Only an applicable oracle
    reaches a value comparison.
    """
    evidence = _evidence_refs(expectation.evidence)
    oracle_kind = _oracle_kind_for(expectation.kind)

    applicability = check_applicability(expectation, signature, actual)
    if not applicability.applicable:
        return AppliedOracle(
            oracle=oracle_kind,
            applicability_kind=applicability.kind,
            status=applicability.status,
            reason=applicability.reason,
            evidence=evidence,
            expected_description=_expected_description(expectation),
            snapshot_verified=snapshot_verified,
        )

    if not snapshot_verified:
        return AppliedOracle(
            oracle=oracle_kind,
            applicability_kind=ApplicabilityKind.EXACT_MATCH,
            status=OracleStatus.INCONCLUSIVE,
            reason=(
                "source snapshot not verified; the executed revision cannot be proven to be "
                "the analyzed one, so no verdict is reported"
            ),
            evidence=evidence,
            expected_description=_expected_description(expectation),
            snapshot_verified=False,
        )

    if observation.outcome in _INFRASTRUCTURE_OUTCOMES:
        return AppliedOracle(
            oracle=oracle_kind,
            applicability_kind=ApplicabilityKind.EXACT_MATCH,
            status=OracleStatus.INCONCLUSIVE,
            reason=(
                f"the execution ended as {observation.outcome.value} before the target's "
                "behaviour could be observed; an infrastructure failure is not an oracle "
                "violation"
            ),
            evidence=evidence,
            expected_description=_expected_description(expectation),
        )

    if expectation.expected_outcome is ExpectedOutcome.RAISES:
        return _evaluate_raises(expectation, observation, evidence, oracle_kind)
    return _evaluate_returns(expectation, observation, evidence, oracle_kind)


def _evaluate_raises(
    expectation: TestExpectationCandidate,
    observation: RuntimeObservation,
    evidence: tuple[OracleEvidenceRef, ...],
    oracle_kind: OracleKind,
) -> AppliedOracle:
    """Evaluate a ``pytest.raises(...)`` expectation.

    ``RETURNED`` against an expectation of a raise is a contradiction: the test
    explicitly said this call raises. ``RAISED`` goes to identity matching, which
    may still be inconclusive.
    """
    expected_text = _expected_description(expectation)
    if observation.outcome is OutcomeKind.RETURNED:
        return AppliedOracle(
            oracle=oracle_kind,
            applicability_kind=ApplicabilityKind.EXACT_MATCH,
            status=OracleStatus.MISMATCH,
            reason=(f"{expected_text} was expected to raise, but the invocation returned normally"),
            evidence=evidence,
            expected_description=expected_text,
            observed_description=observation.return_description or "returned normally",
        )

    verdict = match_exception_observed(
        expectation.exception,
        identity_from_observation(observation),
        expected_binding=expectation.exception_binding,
    )
    return AppliedOracle(
        oracle=oracle_kind,
        applicability_kind=ApplicabilityKind.EXACT_MATCH,
        status=verdict.status,
        reason=verdict.reason,
        evidence=evidence,
        expected_description=expected_text,
        observed_description=f"raised {identity_from_observation(observation).display_name}",
    )


def identity_from_observation(observation: RuntimeObservation) -> ExceptionIdentity:
    """Build the observed exception identity from what the sandbox reported.

    Missing fields stay ``None``. That is the whole point: an observation that
    carries no identity evidence must reach the matcher as *unknown*, so it keeps
    the Phase 6B ``INCONCLUSIVE`` behaviour rather than being read as evidence
    against a builtin expectation.
    """
    return ExceptionIdentity(
        name=observation.exception_type,
        module=observation.exception_module,
        qualname=observation.exception_qualname,
        is_builtin=observation.exception_builtin,
        builtin_lineage=observation.exception_builtin_lineage,
    )


def _evaluate_returns(
    expectation: TestExpectationCandidate,
    observation: RuntimeObservation,
    evidence: tuple[OracleEvidenceRef, ...],
    oracle_kind: OracleKind,
) -> AppliedOracle:
    """Evaluate an ``assert <call> <op> <value>`` expectation."""
    expected_text = _expected_description(expectation)
    operator = expectation.comparison_operator

    if operator is None or expectation.expected_expression is None:
        return AppliedOracle(
            oracle=oracle_kind,
            applicability_kind=ApplicabilityKind.EXACT_MATCH,
            status=OracleStatus.UNSUPPORTED,
            reason="the recorded expectation has no comparison to evaluate",
            evidence=evidence,
            expected_description=expected_text,
        )

    if observation.outcome is OutcomeKind.RAISED:
        # The expectation was that the call returns a value satisfying the
        # comparison. It raised instead, so the expectation is contradicted -- but
        # only by the target's own behaviour, never by a setup failure, which was
        # already excluded above.
        return AppliedOracle(
            oracle=oracle_kind,
            applicability_kind=ApplicabilityKind.EXACT_MATCH,
            status=OracleStatus.MISMATCH,
            reason=(
                f"{expected_text} expected a normal return, but the invocation raised "
                f"{observation.exception_type or 'an exception'}"
            ),
            evidence=evidence,
            expected_description=expected_text,
            observed_description=f"raised {observation.exception_type or 'an exception'}",
        )

    if observation.return_value is None:
        return AppliedOracle(
            oracle=oracle_kind,
            applicability_kind=ApplicabilityKind.EXACT_MATCH,
            status=OracleStatus.UNSUPPORTED,
            reason=(
                "the returned value is not representable by the protocol "
                f"({observation.opaque_return or 'no return value recorded'}), so no "
                "comparison is possible"
            ),
            evidence=evidence,
            expected_description=expected_text,
            observed_description=observation.opaque_return or "(unrepresentable)",
        )

    observed_text = describe_typed(observation.return_value)
    comparison = _compare(operator, observation.return_value, expectation.expected_expression)
    if comparison.outcome is ComparisonOutcome.UNSUPPORTED:
        return AppliedOracle(
            oracle=oracle_kind,
            applicability_kind=ApplicabilityKind.EXACT_MATCH,
            status=OracleStatus.UNSUPPORTED,
            reason=f"{expected_text} could not be checked: {comparison.reason}",
            evidence=evidence,
            expected_description=expected_text,
            observed_description=observed_text,
        )

    status = status_for(comparison.outcome)
    reason = f"{expected_text}: {comparison.reason}"
    if status is OracleStatus.MISMATCH:
        reason = (
            f"expected {expectation.call_expression} {operator.value} "
            f"{expectation.expected_expression}; observed {_plain(observation.return_value)}"
        )
    return AppliedOracle(
        oracle=oracle_kind,
        applicability_kind=ApplicabilityKind.EXACT_MATCH,
        status=status,
        reason=reason,
        evidence=evidence,
        expected_description=expected_text,
        observed_description=observed_text,
    )


@dataclass(frozen=True, slots=True)
class _ComparisonVerdict:
    """A comparison verdict plus its reason, without the observation identity."""

    outcome: ComparisonOutcome
    reason: str


def _compare(
    operator: ComparisonOperator, observed: TypedValue, expected_expression: str
) -> _ComparisonVerdict:
    """Compare one observed value against the recorded expectation text.

    The recorded expectation is source text, so it is put through the same literal
    whitelist as the protocol. An expectation the whitelist refuses yields
    ``UNSUPPORTED``: the repository wrote something EdgeHunter cannot safely read,
    which is not the same as the target contradicting it.
    """
    try:
        expected_value = recognize_literal(expected_expression)
    except ProtocolError as exc:
        return _ComparisonVerdict(
            ComparisonOutcome.UNSUPPORTED,
            f"the expected expression {expected_expression!r} is not a supported literal: {exc}",
        )

    try:
        left = materialize(observed)
        right = materialize(expected_value)
    except (TypeError, ValueError, OverflowError) as exc:  # pragma: no cover - defensive
        return _ComparisonVerdict(
            ComparisonOutcome.UNSUPPORTED, f"value could not be prepared: {exc}"
        )

    result = compare_typed_values(operator, left, right, observed_kind=observed.kind.value)
    return _ComparisonVerdict(result.outcome, result.reason)


def evaluate_observation(
    result: ExecutionResult,
    signature: InvocationSignature,
    actual: BoundCall,
    expectations: tuple[TestExpectationCandidate, ...],
    *,
    snapshot_verified: bool,
) -> tuple[AppliedOracle, ...]:
    """Evaluate every recorded expectation relevant to one executed invocation.

    Only expectations already resolved to this exact target are considered, which
    keeps this linear in the evidence for one target rather than in the whole
    repository.
    """
    observation = result.observation
    if observation is None:
        return ()
    applicable = expectations_for_target(expectations, signature.qualified_name)
    return tuple(
        evaluate_oracle(
            expectation,
            signature,
            actual,
            observation,
            snapshot_verified=snapshot_verified,
        )
        for expectation in applicable
    )


def report_unresolved_evidence(
    expectations: tuple[TestExpectationCandidate, ...], qualified_name: str
) -> tuple[AppliedOracle, ...]:
    """Surface expectations that name this target but could not be resolved.

    Reported as ``INCONCLUSIVE`` so "no applicable oracle" can be explained. They
    are never turned into a mismatch, and no target is chosen for them.
    """
    entries: list[AppliedOracle] = []
    for expectation in unresolved_for_target(expectations, qualified_name):
        kind = (
            ApplicabilityKind.AMBIGUOUS_TARGET
            if "several targets" in expectation.resolution_reason
            else ApplicabilityKind.UNRESOLVED_TARGET
        )
        entries.append(
            AppliedOracle(
                oracle=OracleKind.TEST_ASSERTION,
                applicability_kind=kind,
                status=OracleStatus.INCONCLUSIVE,
                reason=(
                    f"a test names {expectation.callee!r} but it could not be tied to one "
                    f"target ({expectation.resolution_reason}); no oracle is applied"
                ),
                evidence=_evidence_refs(expectation.evidence),
                expected_description=_expected_description(expectation),
            )
        )
    return tuple(entries)


def reconcile(entries: tuple[AppliedOracle, ...]) -> AppliedOracle | None:
    """Reduce evaluated oracles to one representative verdict for the run.

    A single applicable oracle wins outright. Several applicable oracles that
    disagree become ``CONFLICTING_EVIDENCE`` with no defect claimed. A match
    alongside a mismatch is reported as a conflict too, because the repository's
    evidence is genuinely inconsistent about this invocation.
    """
    applicable = [
        entry for entry in entries if entry.applicability_kind is ApplicabilityKind.EXACT_MATCH
    ]
    if not applicable:
        return _first_meaningful(entries)

    statuses = {entry.status for entry in applicable}
    if len(statuses) == 1:
        return applicable[0]
    if OracleStatus.MISMATCH in statuses and OracleStatus.MATCH in statuses:
        return _conflict(
            applicable,
            "applicable expectations disagree: at least one matched and at least one was "
            "contradicted by the same observation; the repository's own evidence is "
            "inconsistent about this invocation",
        )
    if OracleStatus.MISMATCH in statuses and OracleStatus.CONFLICTING_EVIDENCE in statuses:
        return _conflict(
            applicable,
            "applicable expectations contradict each other as well as the observation",
        )
    if OracleStatus.UNSUPPORTED in statuses and OracleStatus.MISMATCH in statuses:
        return _conflict(
            applicable,
            "one applicable expectation was contradicted while another could not be checked, "
            "so the contradiction cannot be isolated",
        )
    return _conflict(
        applicable,
        "applicable expectations reached different verdicts: "
        + ", ".join(sorted(entry.status.value for entry in applicable)),
    )


def _conflict(entries: list[AppliedOracle], reason: str) -> AppliedOracle:
    """Build one conflict result retaining every contributing location."""
    evidence: list[OracleEvidenceRef] = []
    for entry in entries:
        for item in entry.evidence:
            if item not in evidence:
                evidence.append(item)
    return AppliedOracle(
        oracle=OracleKind.TEST_ASSERTION,
        applicability_kind=ApplicabilityKind.EXACT_MATCH,
        status=OracleStatus.CONFLICTING_EVIDENCE,
        reason=reason,
        evidence=tuple(evidence),
        expected_description="; ".join(
            entry.expected_description for entry in entries if entry.expected_description
        ),
    )


def _first_meaningful(entries: tuple[AppliedOracle, ...]) -> AppliedOracle | None:
    """Pick the entry a user should read when nothing was applicable."""
    if not entries:
        return None
    for status in (
        OracleStatus.INCONCLUSIVE,
        OracleStatus.UNSUPPORTED,
        OracleStatus.NOT_APPLICABLE,
    ):
        for entry in entries:
            if entry.status is status:
                return entry
    return entries[0]


def assertion_observed(
    result: ExecutionResult,
    properties: tuple[ProductionPropertyCandidate, ...],
    qualified_name: str,
) -> AppliedOracle | None:
    """Describe a production assertion that execution reached.

    Deliberately returns ``ASSERTION_OBSERVED``-shaped evidence and never a
    ``MISMATCH``. ``assert balance >= 0`` in a public function states a
    precondition the caller is expected to honour; it is not a contract that every
    negative input is a bug. The finding built from this asks for review.
    """
    observation = result.observation
    if observation is None or observation.outcome is not OutcomeKind.RAISED:
        return None
    if observation.exception_type != "AssertionError":
        return None

    assertions = tuple(
        item
        for item in properties
        if item.kind is PropertyKind.ASSERTED_CONDITION and item.target == qualified_name
    )
    location = (
        f"{assertions[0].evidence[0].relative_path.as_posix()}:{assertions[0].evidence[0].lineno}"
        if assertions and assertions[0].evidence
        else "production assert (no location recorded)"
    )
    evidence = tuple(ref for item in assertions for ref in _evidence_refs(item.evidence))
    return AppliedOracle(
        oracle=OracleKind.PRODUCTION_ASSERTION,
        applicability_kind=ApplicabilityKind.NO_EXPECTATION,
        status=OracleStatus.INCONCLUSIVE,
        reason=(
            "the execution raised AssertionError"
            + (f" at {location}" if assertions else "")
            + ". An assertion is a condition the code expects when execution reaches it; it "
            "is not a universal contract on the function's inputs, so no defect is claimed "
            "and this is recorded for review"
        ),
        evidence=evidence,
        expected_description="production assertions on this target: "
        + (", ".join(item.condition for item in assertions) if assertions else "none recorded"),
        observed_description="AssertionError"
        + (f": {observation.exception_message}" if observation.exception_message else ""),
    )


def observation_key(result: ExecutionResult) -> str:
    """A stable identity for one observation, ignoring everything that varies.

    Duration, execution id, container id, and temporary paths are excluded on
    purpose. The key covers the outcome and, for a return, the canonical value; for
    a raise, the exception class only, since a message can embed a value or an
    address.
    """
    observation = result.observation
    if observation is None:
        return f"{result.outcome.value}:no-observation"
    if observation.outcome is OutcomeKind.RETURNED:
        if observation.return_value is not None:
            return f"returned:{canonical_value_text(observation.return_value)}"
        return f"returned:opaque:{observation.opaque_return or 'unknown'}"
    if observation.outcome is OutcomeKind.RAISED:
        return f"raised:{observation.exception_type or 'unknown'}"
    return f"{observation.outcome.value}:{result.detail or ''}"


def _plain(value: TypedValue) -> str:
    """A compact rendering of an observed value for a mismatch reason."""
    return canonical_value_text_key(_safe_materialize(value))


def _safe_materialize(value: TypedValue) -> object:
    try:
        return materialize(value)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return value.kind.value


def _oracle_kind_for(kind: PropertyKind) -> OracleKind:
    """Tier A kind for a mined expectation: a ``raises`` block or a comparison."""
    if kind is PropertyKind.EXAMPLE_RAISES:
        return OracleKind.TEST_RAISES
    return OracleKind.TEST_ASSERTION


def _expected_description(expectation: TestExpectationCandidate) -> str:
    """A readable rendering of what the expectation claims."""
    if expectation.kind is PropertyKind.EXAMPLE_RAISES:
        name = expectation.exception or "an unspecified exception"
        return f"{expectation.call_expression} raises {name}"
    operator = expectation.comparison_operator
    if operator is None:
        return f"{expectation.call_expression} (no comparison recorded)"
    return f"{expectation.call_expression} {operator.value} {expectation.expected_expression}"


def _evidence_refs(evidence: tuple[OracleEvidence, ...]) -> tuple[OracleEvidenceRef, ...]:
    return tuple(
        OracleEvidenceRef(
            kind=_EVIDENCE_KINDS.get(item.kind, OracleKind.TEST_ASSERTION),
            relative_path=item.relative_path,
            lineno=item.lineno,
            detail=item.detail,
        )
        for item in evidence
    )


def guard_evidence(
    properties: tuple[ProductionPropertyCandidate, ...], qualified_name: str
) -> tuple[OracleEvidenceRef, ...]:
    """Validation guards for a target, kept as explanation only.

    Reachability is not inferred. These are never evaluated and never produce a
    finding; they are attached so a reader can see what the source says was
    checked, and why EdgeHunter still declines to call the behaviour wrong.
    """
    return tuple(
        ref
        for item in properties
        if item.kind is PropertyKind.REJECTION_RULE and item.target == qualified_name
        for ref in _evidence_refs(item.evidence)
    )


__all__ = [
    "assertion_observed",
    "evaluate_observation",
    "evaluate_oracle",
    "guard_evidence",
    "observation_key",
    "reconcile",
    "report_unresolved_evidence",
]
