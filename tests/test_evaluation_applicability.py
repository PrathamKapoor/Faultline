"""Applicability: which recorded expectation, if any, covers this exact invocation.

Every test here is about *refusing* to apply an oracle. A permissive applicability
check is the single fastest way to invent bugs, so these tests assert refusal far
more often than they assert success.

No Docker, no target import, no target execution: the evaluator is a pure function
over already-decoded observations and already-mined evidence, and these tests hold
it to that.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from edgehunter.analysis.models import ComparisonOperator
from edgehunter.evaluation.applicability import (
    InvocationParameter,
    InvocationSignature,
    bind_actual_call,
    bind_recorded_call,
    check_applicability,
    expectations_for_target,
    recognize_recorded_value,
    supplied_bindings,
    unencodable_parameters,
)
from edgehunter.evaluation.models import ApplicabilityKind, BoundCall, OracleStatus
from edgehunter.execution.codec import ProtocolError, recognize_literal
from edgehunter.generation.invocation import (
    ArgumentAssignment,
    ArgumentPlacement,
    AssignmentOrigin,
    InvocationPlan,
    InvocationStatus,
)
from edgehunter.generation.models import (
    ActionOrigin,
    LiteralKind,
    MaterializationStatus,
    SemanticAction,
    SemanticActionKind,
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
from edgehunter.scanner.annotations import AnnotationKind, StaticAnnotation
from edgehunter.scanner.models import ParameterKind

TEST_FILE = Path("tests/test_calculator.py")


def _evidence() -> tuple[OracleEvidence, ...]:
    return (
        OracleEvidence(
            kind=OracleEvidenceKind.TEST_ASSERTION,
            relative_path=TEST_FILE,
            lineno=7,
            detail="assert add(1, 2) == 3",
        ),
    )


def _expectation(
    *,
    operator: ComparisonOperator | None = ComparisonOperator.EQUAL,
    expected: str = "3",
    literals: tuple[str, ...] = ("1", "2"),
    positions: tuple[int, ...] = (0, 1),
    positional_count: int = 2,
    keywords: tuple[tuple[str, str], ...] = (),
    keyword_count: int = 0,
    starred: bool = False,
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
        callee="add" if target is None or "." not in target else target.split(".")[-1],
        call_expression=call,
        comparison_operator=operator,
        expected_expression=expected,
        evidence=_evidence(),
        target=target,
        resolution=resolution,
        resolution_reason="unique target with this name"
        if resolution is TargetResolution.RESOLVED
        else "2 targets share this name",
        exception=exception,
        literal_arguments=literals,
        argument_positions=positions,
        positional_argument_count=positional_count,
        keyword_arguments=keywords,
        keyword_argument_count=keyword_count,
        starred_arguments=starred,
    )


def _signature(
    parameters: tuple[tuple[str, ParameterKind, bool], ...] = (
        ("a", ParameterKind.POSITIONAL_OR_KEYWORD, False),
        ("b", ParameterKind.POSITIONAL_OR_KEYWORD, False),
    ),
    identity: str = "calculator.py::add",
    qualified: str = "add",
) -> InvocationSignature:
    return InvocationSignature(
        target_identity=identity,
        qualified_name=qualified,
        parameters=tuple(
            InvocationParameter(name=name, kind=kind, has_default=has_default)
            for name, kind, has_default in parameters
        ),
    )


def _actual(*bindings: tuple[str, str], identity: str = "calculator.py::add") -> BoundCall:
    return bind_actual_call(_signature(identity=identity), bindings)


class TestExactArgumentsMatch:
    def test_same_target_and_same_arguments_are_applicable(self) -> None:
        verdict = check_applicability(
            _expectation(), _signature(), _actual(("a", "integer:1"), ("b", "integer:2"))
        )

        assert verdict.applicable is True
        assert verdict.kind is ApplicabilityKind.EXACT_MATCH

    def test_keyword_and_positional_forms_of_the_same_call_are_the_same_invocation(self) -> None:
        positional = _expectation()
        keyword = _expectation(
            literals=(),
            positions=(),
            positional_count=0,
            keywords=(("a", "1"), ("b", "2")),
            keyword_count=2,
            call="add(a=1, b=2)",
        )

        from_positional = check_applicability(
            positional, _signature(), _actual(("a", "integer:1"), ("b", "integer:2"))
        )
        from_keyword = check_applicability(
            keyword, _signature(), _actual(("a", "integer:1"), ("b", "integer:2"))
        )

        assert from_positional.applicable is True
        # Python binds both forms to the same parameters with the same values, so
        # the recorded example genuinely describes this invocation.
        assert from_keyword.applicable is True


class TestDifferentArgumentsRefuse:
    def test_different_first_argument_is_not_applicable(self) -> None:
        verdict = check_applicability(
            _expectation(), _signature(), _actual(("a", "integer:0"), ("b", "integer:2"))
        )

        assert verdict.applicable is False
        assert verdict.kind is ApplicabilityKind.ARGUMENTS_DIFFER
        assert verdict.status is OracleStatus.NOT_APPLICABLE

    def test_different_second_argument_is_not_applicable(self) -> None:
        verdict = check_applicability(
            _expectation(), _signature(), _actual(("a", "integer:1"), ("b", "integer:0"))
        )

        assert verdict.applicable is False
        assert verdict.kind is ApplicabilityKind.ARGUMENTS_DIFFER

    def test_omitted_default_is_not_the_same_invocation_as_an_explicit_value(self) -> None:
        """`f(1)` is not `f(1, 10)` even though the second argument has a default.

        Equivalence cannot be established without evaluating the default, which
        this phase never does, so the two shapes stay distinct.
        """
        signature = _signature(
            parameters=(
                ("a", ParameterKind.POSITIONAL_OR_KEYWORD, False),
                ("b", ParameterKind.POSITIONAL_OR_KEYWORD, True),
            )
        )
        expectation = _expectation(
            literals=("1",),
            positions=(0,),
            positional_count=1,
            call="add(1)",
        )

        omitted = check_applicability(
            expectation, signature, bind_actual_call(signature, (("a", "integer:1"),))
        )
        explicit = check_applicability(
            expectation,
            signature,
            bind_actual_call(signature, (("a", "integer:1"), ("b", "integer:10"))),
        )

        assert omitted.applicable is True
        assert explicit.applicable is False
        assert explicit.kind is ApplicabilityKind.ARGUMENTS_DIFFER

    def test_a_literal_binds_by_its_call_position_not_its_ordinal(self) -> None:
        """`add(fixture, 2)` binds `2` to `b`, not to `a`.

        The recorded literal carries its own call position precisely so a skipped
        earlier argument does not shift every later one.
        """
        expectation = _expectation(literals=("1", "2"), positions=(0, 1), positional_count=2)

        bound, _, _ = bind_recorded_call(expectation, _signature())

        assert bound is not None
        assert bound.bindings == (("a", "integer:1"), ("b", "integer:2"))

    def test_a_recorded_call_longer_than_its_literals_is_refused(self) -> None:
        expectation = _expectation(literals=("2",), positions=(1,), positional_count=2)

        bound, kind, reason = bind_recorded_call(expectation, _signature())

        assert bound is None
        assert kind is ApplicabilityKind.ARGUMENTS_NOT_LITERAL
        assert "only 1 are literals" in reason


class TestDifferentTargetRefuses:
    def test_expectation_for_another_target_is_not_applicable(self) -> None:
        expectation = _expectation(target="subtract")

        verdict = check_applicability(
            expectation, _signature(), _actual(("a", "integer:1"), ("b", "integer:2"))
        )

        assert verdict.applicable is False
        assert verdict.kind is ApplicabilityKind.TARGET_DIFFERS
        assert verdict.status is OracleStatus.NOT_APPLICABLE


class TestAmbiguousTargetRefuses:
    def test_ambiguous_resolution_is_inconclusive_and_picks_no_target(self) -> None:
        expectation = replace(
            _expectation(),
            target=None,
            resolution=TargetResolution.AMBIGUOUS,
            resolution_reason="2 targets share this name",
        )

        verdict = check_applicability(
            expectation, _signature(), _actual(("a", "integer:1"), ("b", "integer:2"))
        )

        assert verdict.applicable is False
        assert verdict.kind is ApplicabilityKind.AMBIGUOUS_TARGET
        # Inconclusive, not a mismatch: the repository is ambiguous here and
        # choosing a target would be the guess this project refuses to make.
        assert verdict.status is OracleStatus.INCONCLUSIVE
        assert "several targets" in verdict.reason

    def test_unresolved_receiver_call_is_inconclusive(self) -> None:
        expectation = replace(
            _expectation(),
            callee="service.calculate",
            target=None,
            resolution=TargetResolution.UNRESOLVED,
            resolution_reason="receiver resolution not implemented",
        )

        verdict = check_applicability(
            expectation, _signature(), _actual(("a", "integer:1"), ("b", "integer:2"))
        )

        assert verdict.applicable is False
        assert verdict.kind is ApplicabilityKind.UNRESOLVED_TARGET
        assert verdict.status is OracleStatus.INCONCLUSIVE


class TestNonLiteralArgumentsRefuse:
    def test_a_fixture_argument_makes_exact_matching_impossible(self) -> None:
        expectation = _expectation(literals=("2",), positions=(1,), positional_count=2)

        bound, kind, reason = bind_recorded_call(expectation, _signature())

        assert bound is None
        assert kind is ApplicabilityKind.ARGUMENTS_NOT_LITERAL
        assert "cannot be resolved without executing the test" in reason

    def test_a_starred_argument_makes_the_call_unidentifiable(self) -> None:
        expectation = _expectation(starred=True)

        bound, kind, reason = bind_recorded_call(expectation, _signature())

        assert bound is None
        assert kind is ApplicabilityKind.ARGUMENTS_NOT_LITERAL
        assert "*args" in reason

    def test_a_keyword_the_target_does_not_declare_is_refused(self) -> None:
        expectation = _expectation(
            literals=(),
            positions=(),
            positional_count=0,
            keywords=(("c", "1"),),
            keyword_count=1,
            call="add(c=1)",
        )

        bound, kind, reason = bind_recorded_call(expectation, _signature())

        assert bound is None
        assert kind is ApplicabilityKind.ARGUMENTS_NOT_LITERAL
        assert "does not declare" in reason

    def test_a_keyword_for_a_positional_only_parameter_is_refused(self) -> None:
        """Python cannot bind a keyword to a positional-only parameter, so the
        recorded call describes something the target would reject."""
        signature = _signature(
            parameters=(
                ("a", ParameterKind.POSITIONAL_ONLY, False),
                ("b", ParameterKind.POSITIONAL_OR_KEYWORD, False),
            )
        )
        expectation = _expectation(
            literals=(),
            positions=(),
            positional_count=0,
            keywords=(("a", "1"),),
            keyword_count=1,
            call="add(a=1)",
        )

        bound, kind, reason = bind_recorded_call(expectation, signature)

        assert bound is None
        assert kind is ApplicabilityKind.ARGUMENTS_NOT_LITERAL
        assert "cannot be passed by name" in reason

    def test_more_positional_arguments_than_the_target_binds_is_refused(self) -> None:
        expectation = _expectation(
            literals=("1", "2", "3"), positions=(0, 1, 2), positional_count=3
        )

        bound, kind, _ = bind_recorded_call(expectation, _signature())

        assert bound is None
        assert kind is ApplicabilityKind.ARGUMENTS_NOT_LITERAL


class TestPositionalOnlyAndKeywordOnly:
    def test_positional_only_parameters_bind_positionally(self) -> None:
        signature = _signature(
            parameters=(
                ("a", ParameterKind.POSITIONAL_ONLY, False),
                ("b", ParameterKind.POSITIONAL_OR_KEYWORD, False),
            )
        )
        expectation = _expectation(literals=("1", "2"), positions=(0, 1), positional_count=2)

        bound, _, _ = bind_recorded_call(expectation, signature)

        assert bound is not None
        assert bound.bindings == (("a", "integer:1"), ("b", "integer:2"))

    def test_keyword_only_parameters_are_reachable_by_name(self) -> None:
        signature = _signature(
            parameters=(
                ("a", ParameterKind.POSITIONAL_OR_KEYWORD, False),
                ("b", ParameterKind.KEYWORD_ONLY, False),
            )
        )
        expectation = _expectation(
            literals=("1",),
            positions=(0,),
            positional_count=1,
            keywords=(("b", "2"),),
            keyword_count=1,
        )

        bound, _, _ = bind_recorded_call(expectation, signature)

        assert bound is not None
        assert bound.bindings == (("a", "integer:1"), ("b", "integer:2"))
        assert signature.positional_names() == ("a",)


class TestLiteralRecognition:
    @pytest.mark.parametrize(
        "text",
        [
            "1",
            "-1",
            "1.5",
            '"hello"',
            "True",
            "False",
            "None",
            "[1, 2]",
            "(1, 2)",
            "{}",
            "[]",
            "()",
        ],
    )
    def test_permitted_literals_are_recognized(self, text: str) -> None:
        assert recognize_recorded_value(text) is not None

    @pytest.mark.parametrize(
        "text",
        [
            "fixture",
            "CONST",
            'os.getenv("X")',
            'int("123")',
            "some_property.value",
            "User()",
            "a + b",
        ],
    )
    def test_non_literal_expressions_are_never_recognized(self, text: str) -> None:
        """The recorded text goes through the literal whitelist, not an evaluator."""
        assert recognize_recorded_value(text) is None

    def test_nan_and_negative_zero_get_distinct_canonical_text(self) -> None:
        assert recognize_recorded_value("float('nan')") != recognize_recorded_value("0.0")
        assert recognize_recorded_value("-0.0") != recognize_recorded_value("0.0")


class TestSourceSnapshotMismatch:
    def test_an_unverifiable_snapshot_cannot_be_hidden_by_applicability(self) -> None:
        """A perfectly applicable oracle is still unevaluated without a verified
        snapshot. The refusal is enforced in the evaluator, and this pins the
        signature so it cannot be bypassed by a caller."""
        from edgehunter.evaluation.repository import SourceSnapshot

        snapshot = SourceSnapshot.verify(analyzed="a" * 64, staged_digests=(), relative_file="x.py")

        assert snapshot.verified is False
        assert "source snapshot not verified" in snapshot.detail

    def test_matching_digests_verify(self) -> None:
        from edgehunter.evaluation.repository import SourceSnapshot

        digest = "b" * 64
        snapshot = SourceSnapshot.verify(
            analyzed=digest, staged_digests=(("x.py", digest),), relative_file="x.py"
        )

        assert snapshot.verified is True

    def test_differing_digests_do_not_verify(self) -> None:
        from edgehunter.evaluation.repository import SourceSnapshot

        snapshot = SourceSnapshot.verify(
            analyzed="c" * 64, staged_digests=(("x.py", "d" * 64),), relative_file="x.py"
        )

        assert snapshot.verified is False
        assert "differ" in snapshot.detail


class TestLookup:
    def test_only_expectations_resolved_to_this_target_are_considered(self) -> None:
        expectations = (
            _expectation(target="add"),
            _expectation(target="subtract"),
            replace(_expectation(), target=None, resolution=TargetResolution.UNRESOLVED),
        )

        relevant = expectations_for_target(expectations, "add")

        assert [item.target for item in relevant] == ["add"]


class TestPlanBindings:
    def test_omitted_defaults_contribute_no_binding(self) -> None:
        plan = _plan(
            assignments=(
                _assignment("a", ArgumentPlacement.POSITIONAL, "1"),
                _assignment("b", ArgumentPlacement.OMITTED, None),
            )
        )

        assert supplied_bindings(plan) == (("a", "integer:1"),)
        assert unencodable_parameters(plan) == ()

    def test_an_unrecognizable_value_is_reported_rather_than_dropped_silently(self) -> None:
        plan = _plan(assignments=(_assignment("a", ArgumentPlacement.POSITIONAL, "fixture"),))

        assert supplied_bindings(plan) == ()
        assert unencodable_parameters(plan) == ("a",)


def _assignment(
    parameter: str, placement: ArgumentPlacement, value_text: str | None
) -> ArgumentAssignment:
    return ArgumentAssignment(
        parameter=parameter,
        origin=AssignmentOrigin.ADVERSARIAL_ACTION,
        placement=placement,
        value_text=value_text,
        literal_kind=None,
        reason="test fixture",
    )


def _plan(*, assignments: tuple[ArgumentAssignment, ...]) -> InvocationPlan:
    return InvocationPlan(
        relative_path=Path("calculator.py"),
        target="add",
        lineno=1,
        signature="add(a, b)",
        primary_action=SemanticAction(
            relative_path=Path("calculator.py"),
            target="add",
            lineno=1,
            signature="add(a, b)",
            parameter="a",
            kind=SemanticActionKind.SET_ZERO,
            origin=ActionOrigin.ANNOTATION,
            status=MaterializationStatus.CONCRETE,
            annotation=StaticAnnotation(kind=AnnotationKind.INT, rendered="int"),
            boundary_kind=None,
            reference=None,
            value_text="0",
            symbolic_text=None,
            literal_kind=LiteralKind.INTEGER,
            reason="numeric candidate",
            materialization_reason="concrete",
            evidence=(),
        ),
        assignments=assignments,
        missing_parameters=(),
        requires_receiver=False,
        receiver_name=None,
        symbolic_dependencies=(),
        status=InvocationStatus.COMPLETE_FUNCTION,
    )


def test_recognize_literal_is_the_same_grammar_the_evaluator_uses() -> None:
    """Guard against the evaluator and the protocol drifting apart.

    The codec refuses a bare name at the whitelist itself, so a recorded
    expression like ``fixture`` cannot survive to be evaluated even if this
    layer's handling of it changed.
    """
    assert recognize_recorded_value("[1, 2]") is not None
    with pytest.raises(ProtocolError):
        recognize_literal("fixture")
    assert recognize_recorded_value("fixture") is None
