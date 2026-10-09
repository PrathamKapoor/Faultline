"""Evaluation verdicts: comparisons, expected exceptions, and refusals.

The evaluator is exercised here as a pure function over a fabricated
``ExecutionResult``. No Docker, no target import, no target execution -- that is a
hard architectural requirement, not a convenience, and this module is the proof.

Most tests assert *refusal*. An unsupported comparison, an unverifiable snapshot,
an ambiguous expectation, and an infrastructure failure must all refuse to produce
a verdict, because each of them is a place where a permissive evaluator would
manufacture a bug report.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from edgehunter.analysis.models import ComparisonOperator
from edgehunter.evaluation.applicability import InvocationSignature, bind_actual_call
from edgehunter.evaluation.compare import (
    ComparisonOutcome,
    canonical_value_text_key,
    compare_typed_values,
    status_for,
)
from edgehunter.evaluation.evaluator import (
    evaluate_oracle,
    observation_key,
    reconcile,
)
from edgehunter.evaluation.exceptions import (
    exception_descendants,
    is_builtin_exception,
)
from edgehunter.evaluation.models import (
    ApplicabilityKind,
    AppliedOracle,
    OracleEvidenceRef,
    OracleKind,
    OracleStatus,
)
from edgehunter.evaluation.repository import SourceSnapshot
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
    PropertyKind,
    TargetResolution,
    TestExpectationCandidate,
)

TARGET = TargetRef(relative_file="calculator.py", qualified_name="add", lineno=1)
READY = SandboxStatus(backend="docker", state=SandboxState.READY)
TEST_FILE = Path("tests/test_calculator.py")
DIGEST = "a" * 64
VERIFIED = SourceSnapshot.verify(
    analyzed=DIGEST, staged_digests=(("calculator.py", DIGEST),), relative_file="calculator.py"
)


def _expectation(
    *,
    operator: ComparisonOperator | None = ComparisonOperator.EQUAL,
    expected: str | None = "3",
    literals: tuple[str, ...] = ("1", "2"),
    positions: tuple[int, ...] = (0, 1),
    positional_count: int = 2,
    target: str | None = "add",
    resolution: TargetResolution = TargetResolution.RESOLVED,
    kind: PropertyKind = PropertyKind.EXAMPLE_EXPECTATION,
    expected_outcome: ExpectedOutcome = ExpectedOutcome.RETURNS,
    exception: str | None = None,
    call: str = "add(1, 2)",
) -> TestExpectationCandidate:
    return TestExpectationCandidate(
        kind=kind,
        strength=OracleStrength.STRONG,
        expected_outcome=expected_outcome,
        callee="add",
        call_expression=call,
        comparison_operator=operator,
        expected_expression=expected,
        evidence=(
            OracleEvidence(
                kind=(
                    OracleEvidenceKind.TEST_RAISES
                    if kind is PropertyKind.EXAMPLE_RAISES
                    else OracleEvidenceKind.TEST_ASSERTION
                ),
                relative_path=TEST_FILE,
                lineno=7,
                detail="recorded expectation",
            ),
        ),
        target=target,
        resolution=resolution,
        resolution_reason="unique target with this name",
        exception=exception,
        literal_arguments=literals,
        argument_positions=positions,
        positional_argument_count=positional_count,
    )


def _result(
    observation: RuntimeObservation, *, outcome: OutcomeKind | None = None
) -> ExecutionResult:
    return ExecutionResult(
        execution_id="run-1",
        target=TARGET,
        action_id="add:a:set_zero:",
        outcome=outcome or observation.outcome,
        sandbox=READY,
        duration_seconds=0.031,
        observation=observation,
        arguments=InvocationArguments(args=(), kwargs=()),
    )


def _returned(value: TypedValue) -> RuntimeObservation:
    return RuntimeObservation(outcome=OutcomeKind.RETURNED, return_value=value)


def _integer(text: str) -> TypedValue:
    return TypedValue(kind=ValueKind.INTEGER, scalar=text)


def _signature() -> InvocationSignature:
    from edgehunter.evaluation.applicability import InvocationParameter
    from edgehunter.scanner.models import ParameterKind

    return InvocationSignature(
        target_identity=TARGET.identity,
        qualified_name="add",
        parameters=(
            InvocationParameter("a", ParameterKind.POSITIONAL_OR_KEYWORD, False),
            InvocationParameter("b", ParameterKind.POSITIONAL_OR_KEYWORD, False),
        ),
    )


def _evaluate(
    expectation: TestExpectationCandidate,
    observation: RuntimeObservation,
    *,
    snapshot: SourceSnapshot = VERIFIED,
    bindings: tuple[tuple[str, str], ...] = (("a", "integer:1"), ("b", "integer:2")),
) -> AppliedOracle:
    signature = _signature()
    return evaluate_oracle(
        expectation,
        signature,
        bind_actual_call(signature, bindings),
        observation,
        snapshot_verified=snapshot.verified,
    )


class TestEqualityAndOrdering:
    @pytest.mark.parametrize(
        ("observed", "operator", "expected", "status"),
        [
            ("3", ComparisonOperator.EQUAL, "3", OracleStatus.MATCH),
            ("4", ComparisonOperator.EQUAL, "3", OracleStatus.MISMATCH),
            ("3", ComparisonOperator.NOT_EQUAL, "4", OracleStatus.MATCH),
            ("3", ComparisonOperator.NOT_EQUAL, "3", OracleStatus.MISMATCH),
            ("3", ComparisonOperator.LESS_THAN, "4", OracleStatus.MATCH),
            ("4", ComparisonOperator.LESS_THAN, "3", OracleStatus.MISMATCH),
            ("4", ComparisonOperator.LESS_THAN_OR_EQUAL, "4", OracleStatus.MATCH),
            ("3", ComparisonOperator.GREATER_THAN, "2", OracleStatus.MATCH),
            ("2", ComparisonOperator.GREATER_THAN, "3", OracleStatus.MISMATCH),
            ("3", ComparisonOperator.GREATER_THAN_OR_EQUAL, "3", OracleStatus.MATCH),
        ],
    )
    def test_supported_comparisons_reach_the_expected_verdict(
        self, observed: str, operator: ComparisonOperator, expected: str, status: OracleStatus
    ) -> None:
        verdict = _evaluate(
            _expectation(operator=operator, expected=expected), _returned(_integer(observed))
        )

        assert verdict.applicability_kind is ApplicabilityKind.EXACT_MATCH
        assert verdict.status is status

    def test_a_mismatch_names_the_expected_expression_and_the_observed_value(self) -> None:
        verdict = _evaluate(_expectation(expected="3"), _returned(_integer("4")))

        assert verdict.status is OracleStatus.MISMATCH
        assert "add(1, 2) == 3" in verdict.reason
        assert "integer:4" in verdict.reason


class TestPythonSemanticsPreserved:
    def test_true_equals_one_so_the_assertion_is_satisfied(self) -> None:
        """Python says `True == 1`. Reporting a mismatch here would contradict the
        language and would break a repository's own passing test."""
        verdict = _evaluate(
            _expectation(operator=ComparisonOperator.EQUAL, expected="1"), _returned(_integer("1"))
        )

        assert verdict.status is OracleStatus.MATCH

    def test_bool_observed_against_one_expectation_also_matches(self) -> None:
        verdict = _evaluate(
            _expectation(operator=ComparisonOperator.EQUAL, expected="1"),
            _returned(TypedValue(kind=ValueKind.BOOLEAN, scalar="true")),
        )

        assert verdict.status is OracleStatus.MATCH

    def test_a_numeric_ordering_against_a_string_is_unsupported_not_a_mismatch(self) -> None:
        verdict = _evaluate(
            _expectation(operator=ComparisonOperator.LESS_THAN, expected='"abc"'),
            _returned(_integer("3")),
        )

        assert verdict.status is OracleStatus.UNSUPPORTED
        assert "Python does not define" in verdict.reason


class TestIdentityAssertions:
    def test_none_is_none_matches_a_none_return(self) -> None:
        verdict = _evaluate(
            _expectation(operator=ComparisonOperator.IS, expected="None"),
            _returned(TypedValue(kind=ValueKind.NONE)),
        )

        assert verdict.status is OracleStatus.MATCH

    def test_none_is_not_none_is_contradicted_by_a_none_return(self) -> None:
        verdict = _evaluate(
            _expectation(operator=ComparisonOperator.IS_NOT, expected="None"),
            _returned(TypedValue(kind=ValueKind.NONE)),
        )

        assert verdict.status is OracleStatus.MISMATCH

    def test_identity_against_a_non_none_value_is_unsupported(self) -> None:
        verdict = _evaluate(
            _expectation(operator=ComparisonOperator.IS, expected="0"), _returned(_integer("0"))
        )

        assert verdict.status is OracleStatus.UNSUPPORTED
        assert "only supported against None" in verdict.reason


class TestSpecialFloatsAndStrings:
    def test_nan_does_not_break_deduplication_or_identity(self) -> None:
        """`nan == nan` is false, so identity must never be decided by equality."""
        assert canonical_value_text_key(float("nan")) == "float:nan"
        assert canonical_value_text_key(float("nan")) == canonical_value_text_key(float("nan"))
        assert canonical_value_text_key(float("nan")) != canonical_value_text_key(0.0)

    def test_negative_zero_stays_distinguishable_from_positive_zero(self) -> None:
        assert canonical_value_text_key(-0.0) == "float:-0.0"
        assert canonical_value_text_key(0.0) == "float:0.0"

    def test_nan_against_nan_is_a_contradiction_because_python_says_so(self) -> None:
        verdict = _evaluate(
            _expectation(operator=ComparisonOperator.EQUAL, expected="float('nan')"),
            _returned(TypedValue(kind=ValueKind.FLOAT, scalar="nan")),
        )

        # Python's own equality is the rule; inventing `nan != nan -> unsupported`
        # would contradict an existing passing test.
        assert verdict.status is OracleStatus.MISMATCH

    def test_infinities_compare(self) -> None:
        verdict = _evaluate(
            _expectation(operator=ComparisonOperator.EQUAL, expected="float('inf')"),
            _returned(TypedValue(kind=ValueKind.FLOAT, scalar="inf")),
        )

        assert verdict.status is OracleStatus.MATCH

    def test_unicode_strings_compare_by_value(self) -> None:
        verdict = _evaluate(
            _expectation(operator=ComparisonOperator.EQUAL, expected='"héllo → 🎯"'),
            _returned(TypedValue(kind=ValueKind.STRING, scalar="héllo → 🎯")),
        )

        assert verdict.status is OracleStatus.MATCH


class TestCollectionsAndUnsupportedResults:
    def test_lists_compare_by_value(self) -> None:
        observed = TypedValue(kind=ValueKind.LIST, items=(_integer("1"), _integer("2")))
        verdict = _evaluate(
            _expectation(operator=ComparisonOperator.EQUAL, expected="[1, 2]"), _returned(observed)
        )

        assert verdict.status is OracleStatus.MATCH

    def test_tuples_compare_by_value(self) -> None:
        observed = TypedValue(kind=ValueKind.TUPLE, items=(_integer("1"),))
        verdict = _evaluate(
            _expectation(operator=ComparisonOperator.EQUAL, expected="(1,)"), _returned(observed)
        )

        assert verdict.status is OracleStatus.MATCH

    def test_an_opaque_return_is_unsupported_not_a_mismatch(self) -> None:
        observation = RuntimeObservation(
            outcome=OutcomeKind.RETURNED, opaque_return="returned Widget (not representable)"
        )

        verdict = _evaluate(_expectation(), observation)

        assert verdict.status is OracleStatus.UNSUPPORTED
        assert "not representable" in verdict.reason

    def test_a_custom_object_is_never_compared_on_the_host(self) -> None:
        """The worker already refused to serialize it; nothing here revives it."""
        observation = RuntimeObservation(
            outcome=OutcomeKind.RETURNED, opaque_return="returned Widget (not representable)"
        )

        verdict = _evaluate(
            _expectation(operator=ComparisonOperator.EQUAL, expected="[1, 2]"), observation
        )

        assert verdict.status is OracleStatus.UNSUPPORTED
        assert verdict.observed_description


class TestExpectedExceptions:
    def _raises_expectation(self, exception: str) -> TestExpectationCandidate:
        return _expectation(
            kind=PropertyKind.EXAMPLE_RAISES,
            expected_outcome=ExpectedOutcome.RAISES,
            exception=exception,
            expected=None,
            operator=None,
            call="add(1, 2)",
        )

    def test_the_expected_builtin_exception_matches(self) -> None:
        verdict = _evaluate(
            self._raises_expectation("ValueError"),
            RuntimeObservation(outcome=OutcomeKind.RAISED, exception_type="ValueError"),
        )

        assert verdict.status is OracleStatus.MATCH

    def test_returning_normally_contradicts_an_expected_exception(self) -> None:
        verdict = _evaluate(self._raises_expectation("ValueError"), _returned(_integer("3")))

        assert verdict.status is OracleStatus.MISMATCH
        assert "expected to raise" in verdict.reason

    def test_a_different_builtin_exception_contradicts(self) -> None:
        verdict = _evaluate(
            self._raises_expectation("ValueError"),
            RuntimeObservation(outcome=OutcomeKind.RAISED, exception_type="KeyError"),
        )

        assert verdict.status is OracleStatus.MISMATCH

    def test_a_builtin_subclass_satisfies_a_builtin_expectation(self) -> None:
        verdict = _evaluate(
            self._raises_expectation("ValueError"),
            RuntimeObservation(outcome=OutcomeKind.RAISED, exception_type="UnicodeDecodeError"),
        )

        assert verdict.status is OracleStatus.MATCH
        assert "subclass" in verdict.reason

    def test_a_custom_exception_matched_by_bare_name_is_inconclusive(self) -> None:
        """Two modules may each define `PaymentError`; nothing proves they are one
        class, and importing the target to find out is forbidden."""
        verdict = _evaluate(
            self._raises_expectation("PaymentError"),
            RuntimeObservation(outcome=OutcomeKind.RAISED, exception_type="PaymentError"),
        )

        assert verdict.status is OracleStatus.INCONCLUSIVE
        assert "identity cannot be established" in verdict.reason

    def test_a_qualified_expectation_against_a_bare_observation_is_inconclusive(self) -> None:
        verdict = _evaluate(
            self._raises_expectation("billing.PaymentError"),
            RuntimeObservation(outcome=OutcomeKind.RAISED, exception_type="PaymentError"),
        )

        assert verdict.status is OracleStatus.INCONCLUSIVE
        assert "unqualified" in verdict.reason

    def test_two_qualified_names_that_match_are_a_match(self) -> None:
        verdict = _evaluate(
            self._raises_expectation("builtins.ValueError"),
            RuntimeObservation(outcome=OutcomeKind.RAISED, exception_type="builtins.ValueError"),
        )

        assert verdict.status is OracleStatus.MATCH

    def test_an_expectation_naming_no_exception_is_inconclusive(self) -> None:
        verdict = _evaluate(
            self._raises_expectation(""),
            RuntimeObservation(outcome=OutcomeKind.RAISED, exception_type="ValueError"),
        )

        assert verdict.status is OracleStatus.INCONCLUSIVE

    def test_an_observation_naming_no_exception_is_inconclusive(self) -> None:
        verdict = _evaluate(
            self._raises_expectation("ValueError"), RuntimeObservation(outcome=OutcomeKind.RAISED)
        )

        assert verdict.status is OracleStatus.INCONCLUSIVE

    def test_the_builtin_hierarchy_is_derived_from_builtins_not_hand_written(self) -> None:
        assert is_builtin_exception("ValueError")
        assert "UnicodeDecodeError" in exception_descendants("ValueError")
        assert not is_builtin_exception("PaymentError")
        assert exception_descendants("PaymentError") == frozenset()


class TestReturnExpectationAgainstARaisedException:
    def test_a_raise_contradicts_an_expectation_of_a_normal_return(self) -> None:
        verdict = _evaluate(
            _expectation(),
            RuntimeObservation(outcome=OutcomeKind.RAISED, exception_type="ZeroDivisionError"),
        )

        assert verdict.status is OracleStatus.MISMATCH
        assert "expected a normal return" in verdict.reason


class TestInfrastructureFailuresAreNeverViolations:
    @pytest.mark.parametrize(
        "outcome",
        [
            OutcomeKind.TIMEOUT,
            OutcomeKind.IMPORT_FAILURE,
            OutcomeKind.TARGET_NOT_FOUND,
            OutcomeKind.PROTOCOL_ERROR,
            OutcomeKind.WORKER_FAILURE,
            OutcomeKind.RESOURCE_LIMIT,
            OutcomeKind.INELIGIBLE,
        ],
    )
    def test_an_applicable_oracle_stays_inconclusive_when_the_run_failed(
        self, outcome: OutcomeKind
    ) -> None:
        result = _result(RuntimeObservation(outcome=outcome), outcome=outcome)
        assert result.observation is not None

        verdict = evaluate_oracle(
            _expectation(),
            _signature(),
            bind_actual_call(_signature(), (("a", "integer:1"), ("b", "integer:2"))),
            result.observation,
            snapshot_verified=True,
        )

        assert verdict.applicability_kind is ApplicabilityKind.EXACT_MATCH
        assert verdict.status is OracleStatus.INCONCLUSIVE
        assert "not an oracle violation" in verdict.reason

    def test_a_timeout_is_not_a_bug(self) -> None:
        result = _result(
            RuntimeObservation(outcome=OutcomeKind.TIMEOUT), outcome=OutcomeKind.TIMEOUT
        )

        assert observation_key(result) == "timeout:"


class TestSnapshotVerification:
    def test_an_applicable_oracle_is_not_evaluated_without_a_verified_snapshot(self) -> None:
        unverified = SourceSnapshot.verify(
            analyzed="e" * 64,
            staged_digests=(("calculator.py", "f" * 64),),
            relative_file="calculator.py",
        )

        verdict = _evaluate(_expectation(), _returned(_integer("4")), snapshot=unverified)

        assert verdict.applicability_kind is ApplicabilityKind.EXACT_MATCH
        assert verdict.status is OracleStatus.INCONCLUSIVE
        assert "source snapshot not verified" in verdict.reason
        assert verdict.snapshot_verified is False


class TestConflictHandling:
    def _entry(self, status: OracleStatus, line: int) -> AppliedOracle:
        return AppliedOracle(
            oracle=OracleKind.TEST_ASSERTION,
            applicability_kind=ApplicabilityKind.EXACT_MATCH,
            status=status,
            reason="test",
            evidence=(_evidence_ref(line),),
            expected_description="add(1, 2) == 3",
        )

    def test_two_conflicting_expectations_are_reported_as_a_conflict(self) -> None:
        verdict = reconcile(
            (self._entry(OracleStatus.MISMATCH, 7), self._entry(OracleStatus.MATCH, 14))
        )

        assert verdict is not None
        assert verdict.status is OracleStatus.CONFLICTING_EVIDENCE
        # Both locations are retained: discarding either would hide the conflict.
        assert {item.lineno for item in verdict.evidence} == {7, 14}

    def test_a_conflict_never_becomes_a_defect_claim(self) -> None:
        verdict = reconcile(
            (self._entry(OracleStatus.MISMATCH, 7), self._entry(OracleStatus.MATCH, 14))
        )

        assert verdict is not None
        assert "inconsistent" in verdict.reason

    def test_one_consistent_mismatch_is_kept_as_a_mismatch(self) -> None:
        verdict = reconcile((self._entry(OracleStatus.MISMATCH, 7),))

        assert verdict is not None
        assert verdict.status is OracleStatus.MISMATCH

    def test_one_consistent_match_is_kept_as_a_match(self) -> None:
        verdict = reconcile((self._entry(OracleStatus.MATCH, 7),))

        assert verdict is not None
        assert verdict.status is OracleStatus.MATCH

    def test_a_mismatch_beside_an_unsupported_check_is_not_isolated(self) -> None:
        verdict = reconcile(
            (self._entry(OracleStatus.MISMATCH, 7), self._entry(OracleStatus.UNSUPPORTED, 14))
        )

        assert verdict is not None
        assert verdict.status is OracleStatus.CONFLICTING_EVIDENCE

    def test_nothing_applicable_falls_back_to_the_most_informative_entry(self) -> None:
        inapplicable = AppliedOracle(
            oracle=OracleKind.TEST_ASSERTION,
            applicability_kind=ApplicabilityKind.ARGUMENTS_DIFFER,
            status=OracleStatus.NOT_APPLICABLE,
            reason="different arguments",
            evidence=(_evidence_ref(7),),
        )

        verdict = reconcile((inapplicable,))

        assert verdict is inapplicable

    def test_reconcile_of_nothing_is_none(self) -> None:
        assert reconcile(()) is None


def _evidence_ref(line: int) -> OracleEvidenceRef:
    return OracleEvidenceRef(
        kind=OracleKind.TEST_ASSERTION, relative_path=TEST_FILE, lineno=line, detail="recorded"
    )


class TestCompareHelper:
    def test_an_unsupported_operator_is_refused(self) -> None:
        result = compare_typed_values(ComparisonOperator.IN, [1], [1])

        assert result.outcome is ComparisonOutcome.UNSUPPORTED

    def test_a_type_error_becomes_unsupported(self) -> None:
        result = compare_typed_values(ComparisonOperator.GREATER_THAN, "a", 1)

        assert result.outcome is ComparisonOutcome.UNSUPPORTED

    def test_status_translation_is_exhaustive(self) -> None:
        assert status_for(ComparisonOutcome.SATISFIED) is OracleStatus.MATCH
        assert status_for(ComparisonOutcome.CONTRADICTED) is OracleStatus.MISMATCH
        assert status_for(ComparisonOutcome.UNSUPPORTED) is OracleStatus.UNSUPPORTED


class TestObservationIdentity:
    def test_duration_and_ids_do_not_affect_identity(self) -> None:
        first = _returned(_integer("3"))
        second = replace(_returned(_integer("3")))

        slow = replace(_result(first), duration_seconds=9.9)
        fast = replace(_result(second), duration_seconds=0.001, execution_id="other")

        assert observation_key(slow) == observation_key(fast)

    def test_different_returned_values_have_different_identity(self) -> None:
        assert observation_key(_result(_returned(_integer("3")))) != observation_key(
            _result(_returned(_integer("4")))
        )

    def test_an_exception_message_does_not_affect_identity(self) -> None:
        first = RuntimeObservation(
            outcome=OutcomeKind.RAISED, exception_type="ValueError", exception_message="at 0x1"
        )
        second = RuntimeObservation(
            outcome=OutcomeKind.RAISED, exception_type="ValueError", exception_message="at 0x999"
        )

        assert observation_key(_result(first)) == observation_key(_result(second))
