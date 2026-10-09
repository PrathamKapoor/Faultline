"""Production oracles, findings, and reproducibility.

Three things are asserted here, all of them refusals or honest labels:

* A production assertion that fires produces ``ASSERTION_OBSERVED`` and asks for
  review. It is never turned into a confirmed bug, because an assertion is often a
  precondition.
* Validation guards are retained as context and never evaluated, because
  reachability is not inferred from a condition existing in the source.
* Reproduction reports what actually happened: reproduced, flaky, recheck failed,
  or not rechecked. An environment failure is never confirmation.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from edgehunter.evaluation.evaluator import assertion_observed, guard_evidence
from edgehunter.evaluation.findings import (
    build_findings,
    deduplicate_findings,
    finding_identifier,
    is_consistent,
    summarize_reproduction,
)
from edgehunter.evaluation.models import (
    ApplicabilityKind,
    AppliedOracle,
    BoundCall,
    FindingCandidate,
    FindingKind,
    OracleEvidenceRef,
    OracleKind,
    OracleStatus,
    ReproductionStatus,
    ReproductionSummary,
)
from edgehunter.execution.codec import TypedValue, ValueKind
from edgehunter.execution.models import (
    ExecutionResult,
    InvocationArguments,
    OutcomeKind,
    RuntimeObservation,
    SandboxState,
    SandboxStatus,
    TargetRef,
)
from edgehunter.oracle.models import (
    ExpectedOutcome,
    OracleEvidence,
    OracleEvidenceKind,
    OracleStrength,
    OracleSupport,
    ProductionPropertyCandidate,
    PropertyKind,
    TargetOracleSupport,
)

TARGET = TargetRef(relative_file="accounts.py", qualified_name="withdraw", lineno=3)
READY = SandboxStatus(backend="docker", state=SandboxState.READY)
SOURCE = Path("accounts.py")
DIGEST = "9" * 64


def _assertion(condition: str, lineno: int) -> ProductionPropertyCandidate:
    return ProductionPropertyCandidate(
        kind=PropertyKind.ASSERTED_CONDITION,
        target="withdraw",
        condition=condition,
        strength=OracleStrength.STRONG,
        expected_outcome=ExpectedOutcome.CONDITION_HOLDS,
        evidence=(
            OracleEvidence(
                kind=OracleEvidenceKind.PRODUCTION_ASSERTION,
                relative_path=SOURCE,
                lineno=lineno,
                detail=f"assert {condition}",
            ),
        ),
    )


def _guard(condition: str, lineno: int) -> ProductionPropertyCandidate:
    return ProductionPropertyCandidate(
        kind=PropertyKind.REJECTION_RULE,
        target="withdraw",
        condition=condition,
        strength=OracleStrength.STRONG,
        expected_outcome=ExpectedOutcome.RAISES,
        evidence=(
            OracleEvidence(
                kind=OracleEvidenceKind.VALIDATION_GUARD,
                relative_path=SOURCE,
                lineno=lineno,
                detail=f"raises ValueError when {condition}",
            ),
        ),
        exception="ValueError",
    )


def _result(
    observation: RuntimeObservation, *, outcome: OutcomeKind | None = None
) -> ExecutionResult:
    return ExecutionResult(
        execution_id="run-1",
        target=TARGET,
        action_id="withdraw:balance:set_negative_one:0",
        outcome=outcome or observation.outcome,
        sandbox=READY,
        observation=observation,
        arguments=InvocationArguments(args=(), kwargs=()),
    )


def _assertion_error(message: str = "balance must be non-negative") -> RuntimeObservation:
    return RuntimeObservation(
        outcome=OutcomeKind.RAISED, exception_type="AssertionError", exception_message=message
    )


class TestProductionAssertionsAreNotUniversalContracts:
    def test_a_fired_assertion_asks_for_review_rather_than_claiming_a_bug(self) -> None:
        result = _result(_assertion_error())

        observed = assertion_observed(result, (_assertion("balance >= 0", 4),), "withdraw")

        assert observed is not None
        assert observed.status is OracleStatus.INCONCLUSIVE
        assert "not a universal contract" in observed.reason
        assert "no defect is claimed" in observed.reason

    def test_the_assertion_location_is_retained(self) -> None:
        observed = assertion_observed(
            _result(_assertion_error()), (_assertion("balance >= 0", 4),), "withdraw"
        )

        assert observed is not None
        assert observed.evidence[0].relative_path == SOURCE
        assert observed.evidence[0].lineno == 4

    def test_an_assertion_with_no_recorded_property_still_reports_the_observation(self) -> None:
        observed = assertion_observed(_result(_assertion_error()), (), "withdraw")

        assert observed is not None
        assert observed.status is OracleStatus.INCONCLUSIVE
        assert "none recorded" in observed.expected_description

    def test_a_non_assertion_exception_is_not_an_assertion_observation(self) -> None:
        result = _result(
            RuntimeObservation(outcome=OutcomeKind.RAISED, exception_type="ValueError")
        )

        assert assertion_observed(result, (_assertion("balance >= 0", 4),), "withdraw") is None

    def test_a_returning_call_is_not_an_assertion_observation(self) -> None:
        result = _result(RuntimeObservation(outcome=OutcomeKind.RETURNED))

        assert assertion_observed(result, (_assertion("balance >= 0", 4),), "withdraw") is None

    def test_the_finding_for_an_assertion_is_assertion_observed_not_a_mismatch(self) -> None:
        entry = AppliedOracle(
            oracle=OracleKind.PRODUCTION_ASSERTION,
            applicability_kind=ApplicabilityKind.NO_EXPECTATION,
            status=OracleStatus.INCONCLUSIVE,
            reason="the execution raised AssertionError; no defect is claimed",
            evidence=(
                OracleEvidenceRef(
                    kind=OracleKind.PRODUCTION_ASSERTION,
                    relative_path=SOURCE,
                    lineno=4,
                    detail="assert balance >= 0",
                ),
            ),
            expected_description="production assertions on this target: balance >= 0",
            observed_description="AssertionError",
        )

        findings = build_findings(
            _result(_assertion_error()),
            entry,
            actual=_actual(),
            reproduction=_summary(ReproductionStatus.NOT_RECHECKED, 1),
            snapshot_digest=DIGEST,
        )

        assert len(findings) == 1
        assert findings[0].kind is FindingKind.ASSERTION_OBSERVED
        # No severity anywhere: a structural risk score is not bug severity.
        assert not hasattr(findings[0], "severity")


class TestValidationGuardsAreNotEvaluated:
    def test_guards_are_retained_as_evidence_only(self) -> None:
        guards = guard_evidence((_guard("amount < 0", 8),), "withdraw")

        assert len(guards) == 1
        assert guards[0].kind is OracleKind.VALIDATION_GUARD
        assert guards[0].lineno == 8

    def test_a_guard_for_another_target_is_not_attached(self) -> None:
        assert guard_evidence((_guard("amount < 0", 8),), "deposit") == ()

    def test_a_finding_mentions_that_guards_were_not_evaluated(self) -> None:
        entry = AppliedOracle(
            oracle=OracleKind.TEST_ASSERTION,
            applicability_kind=ApplicabilityKind.EXACT_MATCH,
            status=OracleStatus.MISMATCH,
            reason="the observation contradicted the recorded expectation",
            evidence=(
                OracleEvidenceRef(
                    kind=OracleKind.TEST_ASSERTION,
                    relative_path=Path("tests/test_accounts.py"),
                    lineno=10,
                    detail="assert withdraw(100, 20) == 80",
                ),
            ),
            expected_description="withdraw(100, 20) == 80",
            observed_description="79",
        )

        findings = build_findings(
            _result(RuntimeObservation(outcome=OutcomeKind.RAISED, exception_type="ValueError")),
            entry,
            actual=_actual(),
            reproduction=_summary(ReproductionStatus.NOT_RECHECKED, 1),
            snapshot_digest=DIGEST,
            guards=guard_evidence((_guard("amount < 0", 8),), "withdraw"),
        )

        assert "validation guards are retained as context only, not evaluated" in findings[0].detail

    def test_a_non_raising_return_is_never_called_a_guard_violation(self) -> None:
        """`if amount < 0: raise` existing in the source says nothing about whether
        that branch ran for this input. Reachability is not inferred, so a normal
        return alongside a guard produces no finding at all."""
        entry = AppliedOracle(
            oracle=OracleKind.VALIDATION_GUARD,
            applicability_kind=ApplicabilityKind.NO_EXPECTATION,
            status=OracleStatus.INCONCLUSIVE,
            reason="reachability not established",
            evidence=(
                OracleEvidenceRef(
                    kind=OracleKind.VALIDATION_GUARD,
                    relative_path=SOURCE,
                    lineno=8,
                    detail="raises ValueError when amount < 0",
                ),
            ),
            expected_description="amount < 0 raises ValueError",
            observed_description="returned",
        )

        findings = build_findings(
            _result(
                RuntimeObservation(
                    outcome=OutcomeKind.RETURNED,
                    return_value=TypedValue(kind=ValueKind.INTEGER, scalar="20"),
                )
            ),
            entry,
            actual=_actual(),
            reproduction=_summary(ReproductionStatus.NOT_RECHECKED, 1),
            snapshot_digest=DIGEST,
            guards=guard_evidence((_guard("amount < 0", 8),), "withdraw"),
        )

        assert findings == ()


def _actual() -> BoundCall:
    return BoundCall(
        target_identity=TARGET.identity,
        bindings=(("amount", "integer:20"), ("balance", "integer:100")),
    )


def _summary(
    status: ReproductionStatus,
    attempts: int,
    *,
    mismatches: int | None = None,
    failures: int = 0,
    keys: tuple[str, ...] = (),
) -> ReproductionSummary:
    return ReproductionSummary(
        status=status,
        attempts=attempts,
        equivalent_mismatches=attempts if mismatches is None else mismatches,
        failures=failures,
        observation_keys=keys,
    )


class TestReproduction:
    def test_three_identical_mismatches_are_reproduced(self) -> None:
        summary = summarize_reproduction(
            statuses=(OracleStatus.MISMATCH,) * 3,
            observation_keys=("returned:integer:79",) * 3,
            requested_rechecks=3,
        )

        assert summary.status is ReproductionStatus.REPRODUCED
        assert summary.equivalent_mismatches == 3
        assert is_consistent(summary)

    def test_a_mismatch_match_mismatch_sequence_is_flaky(self) -> None:
        summary = summarize_reproduction(
            statuses=(OracleStatus.MISMATCH, OracleStatus.MATCH, OracleStatus.MISMATCH),
            observation_keys=("a", "b", "a"),
            requested_rechecks=3,
        )

        assert summary.status is ReproductionStatus.FLAKY
        assert not is_consistent(summary)

    def test_an_unusable_attempt_fails_the_recheck_and_never_confirms(self) -> None:
        summary = summarize_reproduction(
            statuses=(OracleStatus.MISMATCH, OracleStatus.INCONCLUSIVE),
            observation_keys=("returned:integer:79", "timeout:"),
            requested_rechecks=2,
        )

        assert summary.status is ReproductionStatus.RECHECK_FAILED
        assert summary.failures == 1

    def test_a_single_observation_with_no_recheck_requested_is_not_rechecked(self) -> None:
        summary = summarize_reproduction(
            statuses=(OracleStatus.MISMATCH,),
            observation_keys=("returned:integer:79",),
            requested_rechecks=0,
        )

        assert summary.status is ReproductionStatus.NOT_RECHECKED

    def test_an_unsupported_attempt_does_not_count_as_a_mismatch(self) -> None:
        summary = summarize_reproduction(
            statuses=(OracleStatus.UNSUPPORTED,),
            observation_keys=("returned:opaque:Widget",),
            requested_rechecks=1,
        )

        # The comparison could not be performed, so it confirms nothing.
        assert summary.equivalent_mismatches == 0
        assert summary.status is ReproductionStatus.RECHECK_FAILED

    def test_reproduced_mismatch_is_promoted_in_the_finding_kind(self) -> None:
        findings = build_findings(
            _result(RuntimeObservation(outcome=OutcomeKind.RAISED, exception_type="ValueError")),
            _mismatch_entry(),
            actual=_actual(),
            reproduction=_summary(ReproductionStatus.REPRODUCED, 3),
            snapshot_digest=DIGEST,
        )

        assert findings[0].kind is FindingKind.REPRODUCED_EXPECTATION_MISMATCH
        assert findings[0].attempts == 3

    def test_a_single_observation_stays_an_observed_mismatch(self) -> None:
        findings = build_findings(
            _result(RuntimeObservation(outcome=OutcomeKind.RAISED, exception_type="ValueError")),
            _mismatch_entry(),
            actual=_actual(),
            reproduction=_summary(ReproductionStatus.NOT_RECHECKED, 1),
            snapshot_digest=DIGEST,
        )

        assert findings[0].kind is FindingKind.OBSERVED_EXPECTATION_MISMATCH

    def test_flaky_behaviour_is_not_promoted_to_reproduced(self) -> None:
        findings = build_findings(
            _result(RuntimeObservation(outcome=OutcomeKind.RAISED, exception_type="ValueError")),
            _mismatch_entry(),
            actual=_actual(),
            reproduction=_summary(ReproductionStatus.FLAKY, 3, mismatches=2),
            snapshot_digest=DIGEST,
        )

        assert findings[0].kind is FindingKind.OBSERVED_EXPECTATION_MISMATCH
        assert findings[0].consistent is False


def _mismatch_entry() -> AppliedOracle:
    return AppliedOracle(
        oracle=OracleKind.TEST_ASSERTION,
        applicability_kind=ApplicabilityKind.EXACT_MATCH,
        status=OracleStatus.MISMATCH,
        reason="expected withdraw(100, 20) == 80; observed integer:79",
        evidence=(
            OracleEvidenceRef(
                kind=OracleKind.TEST_ASSERTION,
                relative_path=Path("tests/test_accounts.py"),
                lineno=10,
                detail="assert withdraw(100, 20) == 80",
            ),
        ),
        expected_description="withdraw(100, 20) == 80",
        observed_description="79",
    )


class TestFindingClassification:
    def _build(
        self, entry: AppliedOracle | None, result: ExecutionResult | None = None
    ) -> tuple[FindingCandidate, ...]:
        return build_findings(
            result
            or _result(RuntimeObservation(outcome=OutcomeKind.RAISED, exception_type="ValueError")),
            entry,
            actual=_actual(),
            reproduction=_summary(ReproductionStatus.NOT_RECHECKED, 1),
            snapshot_digest=DIGEST,
        )

    def test_a_match_produces_no_finding(self) -> None:
        assert self._build(replace(_mismatch_entry(), status=OracleStatus.MATCH)) == ()

    def test_an_unsupported_comparison_produces_no_finding(self) -> None:
        """EdgeHunter did not check the assertion, so there is nothing to report."""
        assert self._build(replace(_mismatch_entry(), status=OracleStatus.UNSUPPORTED)) == ()

    def test_a_conflict_produces_an_inconclusive_finding_not_a_defect(self) -> None:
        findings = self._build(replace(_mismatch_entry(), status=OracleStatus.CONFLICTING_EVIDENCE))

        assert findings[0].kind is FindingKind.INCONCLUSIVE

    def test_a_raise_with_no_applicable_oracle_is_only_suspicious(self) -> None:
        entry = AppliedOracle(
            oracle=OracleKind.TEST_ASSERTION,
            applicability_kind=ApplicabilityKind.NO_EXPECTATION,
            status=OracleStatus.INCONCLUSIVE,
            reason="no explicit expectation for this invocation",
            evidence=(),
            expected_description="no applicable explicit oracle",
            observed_description="raised ZeroDivisionError",
        )

        findings = self._build(entry)

        assert findings[0].kind is FindingKind.SUSPICIOUS_BEHAVIOR

    def test_no_evaluation_produces_no_finding(self) -> None:
        assert self._build(None) == ()

    def test_an_infrastructure_failure_produces_no_finding(self) -> None:
        timeout = _result(
            RuntimeObservation(outcome=OutcomeKind.TIMEOUT), outcome=OutcomeKind.TIMEOUT
        )

        findings = self._build(
            replace(_mismatch_entry(), status=OracleStatus.INCONCLUSIVE), timeout
        )

        assert findings == ()


class TestDeduplication:
    def _finding(
        self,
        *,
        target_identity: str | None = None,
        source_digest: str | None = None,
        argument_description: str | None = None,
        oracle_location: str | None = None,
    ) -> FindingCandidate:
        """Build one finding with a varied identity field.

        The identifier is recomputed from the new identity, exactly as it would be
        when the finding is really built from a different target, input, or source
        snapshot.
        """
        entry = _mismatch_entry()
        base = build_findings(
            _result(RuntimeObservation(outcome=OutcomeKind.RAISED, exception_type="ValueError")),
            entry,
            actual=_actual(),
            reproduction=_summary(ReproductionStatus.NOT_RECHECKED, 1),
            snapshot_digest=DIGEST,
        )[0]
        updated = replace(
            base,
            target_identity=target_identity or base.target_identity,
            source_digest=source_digest or base.source_digest,
            argument_description=argument_description or base.argument_description,
            oracle_location=oracle_location or base.oracle_location,
        )
        return replace(
            updated,
            finding_id=finding_identifier(
                target=updated.target_identity,
                digest=updated.source_digest,
                location=updated.oracle_location,
                invocation=updated.argument_description,
                category=updated.kind.value,
            ),
        )

    def test_an_identical_mismatch_collapses_to_one_finding(self) -> None:
        unique = deduplicate_findings((self._finding(), self._finding()))

        assert len(unique) == 1

    def test_the_same_exception_on_different_targets_stays_two_findings(self) -> None:
        other = self._finding(target_identity="accounts.py::transfer")

        assert len(deduplicate_findings((self._finding(), other))) == 2

    def test_the_same_target_with_different_inputs_stays_two_findings(self) -> None:
        other = self._finding(
            argument_description="accounts.py::withdraw(amount=integer:30, balance=integer:100)"
        )

        assert len(deduplicate_findings((self._finding(), other))) == 2

    def test_the_same_input_with_different_oracles_stays_two_findings(self) -> None:
        other = self._finding(oracle_location="tests/test_accounts.py:22")

        assert len(deduplicate_findings((self._finding(), other))) == 2

    def test_a_different_source_snapshot_stays_two_findings(self) -> None:
        other = self._finding(source_digest="8" * 64)

        assert len(deduplicate_findings((self._finding(), other))) == 2

    def test_two_findings_never_share_an_identifier(self) -> None:
        unique = deduplicate_findings(
            (
                self._finding(),
                self._finding(target_identity="accounts.py::transfer"),
                self._finding(
                    argument_description=(
                        "accounts.py::withdraw(amount=integer:30, balance=integer:100)"
                    )
                ),
            )
        )

        assert len({item.finding_id for item in unique}) == len(unique)

    def test_identifiers_are_deterministic_across_builds(self) -> None:
        first = self._finding()
        second = self._finding()

        assert first.finding_id == second.finding_id

    def test_deduplication_is_order_independent(self) -> None:
        first = self._finding()
        second = self._finding(target_identity="accounts.py::transfer")

        assert deduplicate_findings((first, second)) == deduplicate_findings((second, first))


class TestOracleSupportIsNotRisk:
    def test_a_strong_oracle_support_level_is_not_turned_into_severity(self) -> None:
        """Structural risk and oracle support are separate aggregates (D-022, D-023)."""
        support = (
            TargetOracleSupport(
                target="withdraw",
                level=OracleSupport.STRONG,
                assertion_count=1,
                validation_rule_count=1,
                resolved_test_expectation_count=1,
            ),
        )

        findings = build_findings(
            _result(RuntimeObservation(outcome=OutcomeKind.RAISED, exception_type="ValueError")),
            _mismatch_entry(),
            actual=_actual(),
            reproduction=_summary(ReproductionStatus.NOT_RECHECKED, 1),
            snapshot_digest=DIGEST,
        )

        assert support[0].level is OracleSupport.STRONG
        assert not hasattr(findings[0], "severity")
        assert findings[0].kind is FindingKind.OBSERVED_EXPECTATION_MISMATCH
