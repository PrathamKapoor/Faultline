"""Oracle applicability: deciding whether a recorded expectation covers this exact call.

This is the layer the whole package exists to get right, because it runs *before*
any evaluation. Getting it wrong in the permissive direction invents bugs; getting
it wrong in the strict direction misses real ones. The bias is deliberately toward
refusal.

Applicability requires all of the following:

1. **Same target identity.** The expectation must have resolved to exactly one
   production target, and it must be the target that was executed. An ambiguous or
   unresolved expectation is never attached to anything.
2. **Every recorded argument is a literal.** A fixture, a module constant, or a
   constructor call means the recorded value is unknown, so exact matching is
   impossible. Nothing is inferred and nothing is invented.
3. **No starred arguments.** ``f(*items)`` expands to an unknown number of values
   and can never identify one recorded invocation.
4. **The same bound arguments.** Argument identity is established through the
   target's own signature, so ``add(1, 2)`` and ``add(a=1, b=2)`` are recognized
   as the same invocation while ``add(1)`` and ``add(1, 10)`` are not. An omitted
   declared default is never evaluated, so a plan that relies on one is described
   as written and a differently-shaped call is a refusal, not a contradiction.

:func:`bind_recorded_call` and :func:`bind_actual_call` produce the same
:class:`BoundCall` type for the two sides, so "the same invocation" is a
structural comparison of canonical text rather than a match on rendered source.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from edgehunter.analysis.models import ComparisonOperator
from edgehunter.evaluation.models import ApplicabilityKind, BoundCall, OracleStatus
from edgehunter.evaluation.values import canonical_value_text
from edgehunter.execution.codec import ProtocolError, recognize_literal
from edgehunter.generation.invocation import ArgumentPlacement, InvocationPlan
from edgehunter.oracle.models import (
    ExpectedOutcome,
    PropertyKind,
    TargetResolution,
    TestExpectationCandidate,
)
from edgehunter.scanner.models import FunctionInfo, ParameterKind

# Parameter kinds that can receive a positional argument.
_POSITIONAL_KINDS: Final[frozenset[ParameterKind]] = frozenset(
    {ParameterKind.POSITIONAL_ONLY, ParameterKind.POSITIONAL_OR_KEYWORD}
)

# Parameter kinds that no supported invocation can supply.
_VARIADIC_KINDS: Final[frozenset[ParameterKind]] = frozenset(
    {ParameterKind.VAR_POSITIONAL, ParameterKind.VAR_KEYWORD}
)

# The comparison operators the runtime evaluator can decide.
EVALUABLE_OPERATORS: Final[frozenset[ComparisonOperator]] = frozenset(
    {
        ComparisonOperator.EQUAL,
        ComparisonOperator.NOT_EQUAL,
        ComparisonOperator.LESS_THAN,
        ComparisonOperator.LESS_THAN_OR_EQUAL,
        ComparisonOperator.GREATER_THAN,
        ComparisonOperator.GREATER_THAN_OR_EQUAL,
        ComparisonOperator.IS,
        ComparisonOperator.IS_NOT,
    }
)


@dataclass(frozen=True, slots=True)
class InvocationParameter:
    """One parameter of the executed target, reduced to what binding needs."""

    name: str
    kind: ParameterKind
    has_default: bool

    @property
    def accepts_positional(self) -> bool:
        return self.kind in _POSITIONAL_KINDS

    @property
    def accepts_keyword(self) -> bool:
        """True when this parameter can be supplied by name.

        ``POSITIONAL_ONLY`` is the one kind that cannot: Python itself refuses to
        bind a keyword argument to it, so a recorded ``f(x=1)`` against a
        positional-only parameter cannot describe a call that would work.
        """
        return self.kind is not ParameterKind.POSITIONAL_ONLY


@dataclass(frozen=True, slots=True)
class InvocationSignature:
    """The executed target's parameters, in declaration order."""

    target_identity: str
    qualified_name: str
    parameters: tuple[InvocationParameter, ...]

    def positional_names(self) -> tuple[str, ...]:
        """Parameter names a positional argument binds to, in order."""
        return tuple(
            parameter.name for parameter in self.parameters if parameter.accepts_positional
        )

    def parameter_named(self, name: str) -> InvocationParameter | None:
        for parameter in self.parameters:
            if parameter.name == name:
                return parameter
        return None


@dataclass(frozen=True, slots=True)
class OracleApplicability:
    """The verdict of the applicability layer, before any evaluation happens."""

    applicable: bool
    kind: ApplicabilityKind
    reason: str
    status: OracleStatus
    signature: InvocationSignature | None = None
    recorded: BoundCall | None = None
    actual: BoundCall | None = None


def signature_for_target(target_identity: str, function: FunctionInfo) -> InvocationSignature:
    """Build the binding signature for one executed target.

    The receiver is excluded because no eligible plan can supply it, and variadics
    are excluded because a single recorded invocation can never describe them.
    """
    from edgehunter.generation.actions import receiver_parameter

    receiver = receiver_parameter(function)
    parameters = tuple(
        InvocationParameter(
            name=parameter.name,
            kind=parameter.kind,
            has_default=parameter.has_default,
        )
        for parameter in function.parameters
        if parameter is not receiver and parameter.kind not in _VARIADIC_KINDS
    )
    return InvocationSignature(
        target_identity=target_identity,
        qualified_name=function.qualified_name,
        parameters=parameters,
    )


def recognize_recorded_value(text: str) -> str | None:
    """Canonical text for one recorded literal, or ``None`` when unsupported.

    Recognition goes through the Phase 6A literal whitelist, so nothing here can
    evaluate a target expression. Returning canonical text rather than an object
    keeps the comparison structural.
    """
    try:
        return canonical_value_text(recognize_literal(text))
    except ProtocolError:
        return None


def bind_recorded_call(
    expectation: TestExpectationCandidate,
    signature: InvocationSignature,
) -> tuple[BoundCall | None, ApplicabilityKind, str]:
    """Bind a mined test call's literal arguments onto the target's parameters.

    Returns ``None`` plus the reason whenever the recorded call cannot be bound
    exactly. A non-literal argument is never evaluated and an omitted declared
    default is never filled in.
    """
    if expectation.starred_arguments:
        return (
            None,
            ApplicabilityKind.ARGUMENTS_NOT_LITERAL,
            ("the recorded call uses *args or **kwargs, so it does not identify one invocation"),
        )
    if len(expectation.literal_arguments) != expectation.positional_argument_count:
        return (
            None,
            ApplicabilityKind.ARGUMENTS_NOT_LITERAL,
            (
                f"the recorded call passes {expectation.positional_argument_count} positional "
                f"argument(s) but only {len(expectation.literal_arguments)} are literals; a "
                "fixture or name cannot be resolved without executing the test"
            ),
        )
    if len(expectation.keyword_arguments) != expectation.keyword_argument_count:
        return (
            None,
            ApplicabilityKind.ARGUMENTS_NOT_LITERAL,
            (
                f"the recorded call passes {expectation.keyword_argument_count} keyword "
                f"argument(s) but only {len(expectation.keyword_arguments)} are literals"
            ),
        )

    positional_names = signature.positional_names()
    bindings: list[tuple[str, str]] = []
    for text, position in zip(
        expectation.literal_arguments, expectation.argument_positions, strict=True
    ):
        if position >= len(positional_names):
            return (
                None,
                ApplicabilityKind.ARGUMENTS_NOT_LITERAL,
                (
                    f"the recorded call passes more positional arguments than the target binds "
                    f"({len(positional_names)} positionally)"
                ),
            )
        value = recognize_recorded_value(text)
        if value is None:
            return (
                None,
                ApplicabilityKind.ARGUMENTS_NOT_LITERAL,
                (f"recorded argument {text!r} is not a value this phase can compare"),
            )
        bindings.append((positional_names[position], value))

    for name, text in expectation.keyword_arguments:
        parameter = signature.parameter_named(name)
        if parameter is None:
            return (
                None,
                ApplicabilityKind.ARGUMENTS_NOT_LITERAL,
                (f"the recorded call passes keyword {name!r}, which the target does not declare"),
            )
        if not parameter.accepts_keyword:
            return (
                None,
                ApplicabilityKind.ARGUMENTS_NOT_LITERAL,
                (f"the recorded call passes keyword {name!r}, which cannot be passed by name"),
            )
        value = recognize_recorded_value(text)
        if value is None:
            return (
                None,
                ApplicabilityKind.ARGUMENTS_NOT_LITERAL,
                (f"recorded keyword argument {text!r} is not a value this phase can compare"),
            )
        bindings.append((name, value))

    ordered = tuple(sorted(bindings, key=lambda item: item[0]))
    return (
        BoundCall(target_identity=signature.target_identity, bindings=ordered),
        ApplicabilityKind.EXACT_MATCH,
        "the recorded call binds the same arguments to the same parameters",
    )


def bind_actual_call(
    signature: InvocationSignature,
    assignments: tuple[tuple[str, str], ...],
) -> BoundCall:
    """Describe the executed call as bindings already reduced to canonical text.

    ``assignments`` is ``(parameter name, canonical value text)``, so this
    function performs no recognition and cannot fail.
    """
    return BoundCall(
        target_identity=signature.target_identity,
        bindings=tuple(sorted(assignments, key=lambda item: item[0])),
    )


def check_applicability(
    expectation: TestExpectationCandidate,
    signature: InvocationSignature,
    actual: BoundCall,
) -> OracleApplicability:
    """Decide whether one recorded expectation covers this exact invocation.

    An ambiguous or unresolved expectation is refused first, because attaching it
    to a target would be precisely the guess this project refuses to make.
    """
    if expectation.resolution is TargetResolution.AMBIGUOUS:
        return _refused(
            ApplicabilityKind.AMBIGUOUS_TARGET,
            OracleStatus.INCONCLUSIVE,
            f"{expectation.callee} matched several targets "
            f"({expectation.resolution_reason}); no target is chosen",
            signature,
            actual,
        )
    if expectation.resolution is not TargetResolution.RESOLVED:
        return _refused(
            ApplicabilityKind.UNRESOLVED_TARGET,
            OracleStatus.INCONCLUSIVE,
            f"{expectation.callee} did not resolve to one target ({expectation.resolution_reason})",
            signature,
            actual,
        )

    if expectation.target != signature.qualified_name:
        return _refused(
            ApplicabilityKind.TARGET_DIFFERS,
            OracleStatus.NOT_APPLICABLE,
            f"the expectation belongs to {expectation.target!r}, not the executed "
            f"{signature.qualified_name!r}",
            signature,
            actual,
        )

    recorded, kind, reason = bind_recorded_call(expectation, signature)
    if recorded is None:
        return _refused(kind, OracleStatus.UNSUPPORTED, reason, signature, actual)

    if recorded.bindings != actual.bindings:
        return OracleApplicability(
            applicable=False,
            kind=ApplicabilityKind.ARGUMENTS_DIFFER,
            reason=(
                f"the recorded example is {recorded.render()} but the execution was "
                f"{actual.render()}; an expectation covers one exact invocation only"
            ),
            status=OracleStatus.NOT_APPLICABLE,
            signature=signature,
            recorded=recorded,
            actual=actual,
        )

    return OracleApplicability(
        applicable=True,
        kind=ApplicabilityKind.EXACT_MATCH,
        reason=reason,
        status=OracleStatus.MATCH,
        signature=signature,
        recorded=recorded,
        actual=actual,
    )


def _refused(
    kind: ApplicabilityKind,
    status: OracleStatus,
    reason: str,
    signature: InvocationSignature,
    actual: BoundCall,
) -> OracleApplicability:
    return OracleApplicability(
        applicable=False,
        kind=kind,
        reason=reason,
        status=status,
        signature=signature,
        actual=actual,
    )


def expectations_for_target(
    expectations: tuple[TestExpectationCandidate, ...],
    qualified_name: str,
) -> tuple[TestExpectationCandidate, ...]:
    """Index the expectations that resolved to exactly this target.

    This is the lookup that keeps evaluation from comparing every oracle against
    every execution: only expectations already resolved to this qualified name are
    ever considered, and the unresolved ones are represented separately by
    :func:`unresolved_for_target`.
    """
    return tuple(
        item
        for item in expectations
        if item.resolution is TargetResolution.RESOLVED and item.target == qualified_name
    )


def unresolved_for_target(
    expectations: tuple[TestExpectationCandidate, ...],
    qualified_name: str,
) -> tuple[TestExpectationCandidate, ...]:
    """Expectations that name this target's callee but did not resolve to it.

    These are reported so "no applicable oracle" can be explained, rather than
    leaving the user to wonder whether EdgeHunter simply missed the test.
    """
    return tuple(
        item
        for item in expectations
        if item.resolution is not TargetResolution.RESOLVED
        and item.target is None
        and item.callee == qualified_name.split(".")[-1]
    )


def expectation_is_evaluable(expectation: TestExpectationCandidate) -> bool:
    """True when the expectation states something the evaluator can decide."""
    if expectation.kind is PropertyKind.EXAMPLE_RAISES:
        return expectation.expected_outcome is ExpectedOutcome.RAISES
    if expectation.comparison_operator is None:
        return False
    return expectation.comparison_operator in EVALUABLE_OPERATORS


def supplied_bindings(plan: InvocationPlan) -> tuple[tuple[str, str], ...]:
    """Canonical ``(parameter, value)`` text for the arguments a plan supplies.

    An omitted declared default contributes nothing, because its value is not
    known and never evaluated. A value the literal whitelist refuses also
    contributes nothing, which makes exact invocation identity impossible; the
    caller reports ``UNSUPPORTED`` rather than completing the gap by guessing.
    """
    pairs: list[tuple[str, str]] = []
    for item in plan.assignments:
        if item.value_text is None or item.placement is ArgumentPlacement.OMITTED:
            continue
        value = recognize_recorded_value(item.value_text)
        if value is None:
            continue
        pairs.append((item.parameter, value))
    return tuple(sorted(pairs, key=lambda pair: pair[0]))


def unencodable_parameters(plan: InvocationPlan) -> tuple[str, ...]:
    """Parameters whose planned value could not be recognized as a literal.

    Reported alongside ``NOT_APPLICABLE`` so the user sees *why* exact invocation
    identity was unavailable, rather than a refusal with no stated cause.
    """
    unresolved: list[str] = []
    for item in plan.assignments:
        if item.value_text is None:
            if item.placement is not ArgumentPlacement.OMITTED:
                unresolved.append(item.parameter)
            continue
        if recognize_recorded_value(item.value_text) is None:
            unresolved.append(item.parameter)
    return tuple(unresolved)
