"""Typed invocation plans: one primary action plus trustworthy filler values.

A Phase 4 candidate case names one adversarial action and refuses to invent
anything else, which is right for the action layer but leaves most cases
unrunnable. This module completes a case into an :class:`InvocationPlan` by
filling only the arguments where static evidence makes a value trustworthy, and
by naming the ones it could not fill.

The rules that keep the result honest:

* **Exactly one adversarial assignment.** Every other entry is a filler and
  records which kind it is (:class:`AssignmentOrigin`), so a later failure can
  be attributed to the action rather than to the filler.
* **A baseline is a filler, not an oracle.** ``0`` is the simplest
  statically constructible integer; nothing here claims the target accepts it.
* **A declared default is satisfiable without being evaluated.** The plan marks
  the parameter as using its default and never reads the default expression.
* **A method is not locally invocable.** Its ``self``/``cls`` receiver is never
  synthesized, so such a plan reports :attr:`InvocationStatus.NEEDS_RECEIVER`
  rather than claiming to be complete.
* **A symbolic reference is resolved only when it names another parameter of the
  same target** and that parameter has a trustworthy baseline. ``x <= limit``
  with ``limit: int`` resolves through the very same adjacency materializer the
  literal path uses. ``x <= self.limit`` does not resolve, because no receiver
  exists.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from edgehunter.analysis.models import ConstantKind
from edgehunter.generation.actions import receiver_parameter, required_parameters
from edgehunter.generation.baselines import BaselineSource, BaselineValue, baseline_for
from edgehunter.generation.materialize import materialize_relational
from edgehunter.generation.models import (
    CandidateCase,
    LiteralKind,
    MaterializationStatus,
    SemanticAction,
)
from edgehunter.scanner.annotations import AnnotationKind
from edgehunter.scanner.models import FunctionInfo, ParameterInfo, ParameterKind

# Shapes whose baseline text can be handed back to the relational materializer as
# if it had been written as a literal in the comparison.
_SHAPE_TO_CONSTANT_KIND: Mapping[AnnotationKind, ConstantKind] = {
    AnnotationKind.INT: ConstantKind.INTEGER,
    AnnotationKind.FLOAT: ConstantKind.FLOAT,
    AnnotationKind.BOOL: ConstantKind.BOOL,
    AnnotationKind.STR: ConstantKind.STRING,
    AnnotationKind.NONE: ConstantKind.NONE,
}


class AssignmentOrigin(str, Enum):
    """Why one argument receives the value it does."""

    ADVERSARIAL_ACTION = "adversarial_action"
    STATIC_TYPE_BASELINE = "static_type_baseline"
    EXISTING_TEST_BASELINE = "existing_test_baseline"
    DECLARED_DEFAULT = "declared_default"
    DEPENDENCY_BASELINE = "dependency_baseline"


class ArgumentPlacement(str, Enum):
    """How a parameter would have to be supplied to the call."""

    POSITIONAL_ONLY = "positional_only"
    POSITIONAL = "positional"
    KEYWORD = "keyword"
    OMITTED = "omitted"
    """The parameter is not passed at all and its declared default applies."""

    VAR_POSITIONAL = "var_positional"
    VAR_KEYWORD = "var_keyword"


class InvocationStatus(str, Enum):
    """Whether a plan could be turned into a call, and what is still missing."""

    COMPLETE_FUNCTION = "complete_function"
    """Every argument is accounted for and no receiver is required."""

    NEEDS_RECEIVER = "needs_receiver"
    """All arguments are accounted for, but a receiver must be constructed."""

    PARTIAL_ARGUMENTS = "partial_arguments"
    """At least one required parameter has no trustworthy value."""

    SYMBOLIC_DEPENDENCY = "symbolic_dependency"
    """The primary action's own value still depends on an unknown value."""


@dataclass(frozen=True, slots=True)
class ArgumentAssignment:
    """One parameter's planned value and why it has that value."""

    parameter: str
    origin: AssignmentOrigin
    placement: ArgumentPlacement
    value_text: str | None
    literal_kind: LiteralKind | None
    reason: str


@dataclass(frozen=True, slots=True)
class InvocationPlan:
    """One candidate case completed with trustworthy filler values.

    Assembling an actual Python call is deliberately *not* done here. The plan
    records each parameter's placement so a later phase can do that correctly,
    including the rule that an omitted positional parameter cannot precede a
    supplied one.
    """

    relative_path: Path
    target: str
    lineno: int
    signature: str
    primary_action: SemanticAction
    assignments: tuple[ArgumentAssignment, ...]
    missing_parameters: tuple[str, ...]
    requires_receiver: bool
    receiver_name: str | None
    symbolic_dependencies: tuple[str, ...]
    status: InvocationStatus

    @property
    def positional_assignments(self) -> tuple[ArgumentAssignment, ...]:
        """Assignments that would be passed positionally."""
        return tuple(
            item
            for item in self.assignments
            if item.placement in (ArgumentPlacement.POSITIONAL_ONLY, ArgumentPlacement.POSITIONAL)
        )

    @property
    def keyword_assignments(self) -> tuple[ArgumentAssignment, ...]:
        """Assignments that would be passed by keyword."""
        return tuple(
            item for item in self.assignments if item.placement is ArgumentPlacement.KEYWORD
        )

    @property
    def adversarial_assignments(self) -> tuple[ArgumentAssignment, ...]:
        """Assignments that came from the primary action; there is exactly one."""
        return tuple(
            item for item in self.assignments if item.origin is AssignmentOrigin.ADVERSARIAL_ACTION
        )


def plan_case(
    case: CandidateCase,
    function: FunctionInfo,
    test_literals: tuple[str, ...] = (),
) -> InvocationPlan:
    """Complete one candidate case into an invocation plan.

    ``test_literals`` are literal arguments observed in a resolved existing test
    for this target, already ordered. They are stronger evidence than a generic
    baseline, so they take precedence.
    """
    parameters = _plannable_parameters(function)
    primary_name = case.action.parameter
    observed = _map_observed_literals(parameters, test_literals)

    dependency_name, dependency_value = _resolve_symbolic_dependency(case.action, parameters)

    assignments: list[ArgumentAssignment] = []
    missing: list[str] = []
    symbolic_dependencies: list[str] = []

    for parameter in parameters:
        if parameter.name == primary_name:
            primary = _primary_assignment(parameter, case.action, dependency_value)
            assignments.append(primary)
            if primary.value_text is None:
                symbolic_dependencies.append(primary_name)
            continue
        filler = _filler_assignment(parameter, observed.get(parameter.name), dependency_name)
        if filler is None:
            if not parameter.has_default:
                missing.append(parameter.name)
            continue
        assignments.append(filler)

    receiver = receiver_parameter(function)
    status = _status(
        symbolic_dependencies=symbolic_dependencies,
        missing=missing,
        requires_receiver=receiver is not None,
    )
    return InvocationPlan(
        relative_path=case.relative_path,
        target=case.target,
        lineno=case.lineno,
        signature=case.signature,
        primary_action=case.action,
        assignments=tuple(assignments),
        missing_parameters=tuple(name for name in required_parameters(function) if name in missing),
        requires_receiver=receiver is not None,
        receiver_name=receiver.name if receiver is not None else None,
        symbolic_dependencies=tuple(symbolic_dependencies),
        status=status,
    )


def _plannable_parameters(function: FunctionInfo) -> tuple[ParameterInfo, ...]:
    """Parameters a plan can assign, excluding the receiver and variadics."""
    receiver = receiver_parameter(function)
    return tuple(
        parameter
        for parameter in function.parameters
        if parameter is not receiver
        and parameter.kind not in (ParameterKind.VAR_POSITIONAL, ParameterKind.VAR_KEYWORD)
    )


def _primary_assignment(
    parameter: ParameterInfo,
    action: SemanticAction,
    dependency_value: BaselineValue | None,
) -> ArgumentAssignment:
    """The single adversarial assignment for this plan."""
    placement = _placement_of_kind(parameter.kind)
    if action.status is MaterializationStatus.CONCRETE:
        return ArgumentAssignment(
            parameter=action.parameter,
            origin=AssignmentOrigin.ADVERSARIAL_ACTION,
            placement=placement,
            value_text=action.value_text,
            literal_kind=action.literal_kind,
            reason=action.materialization_reason,
        )
    if dependency_value is not None:
        return ArgumentAssignment(
            parameter=action.parameter,
            origin=AssignmentOrigin.ADVERSARIAL_ACTION,
            placement=placement,
            value_text=dependency_value.value_text,
            literal_kind=dependency_value.literal_kind,
            reason=(
                f"{action.materialization_reason}; resolved through the "
                f"{action.symbolic_text} baseline {dependency_value.value_text}"
            ),
        )
    return ArgumentAssignment(
        parameter=action.parameter,
        origin=AssignmentOrigin.ADVERSARIAL_ACTION,
        placement=placement,
        value_text=None,
        literal_kind=None,
        reason=action.materialization_reason,
    )


def _placement_of_kind(kind: ParameterKind) -> ArgumentPlacement:
    if kind is ParameterKind.POSITIONAL_ONLY:
        return ArgumentPlacement.POSITIONAL_ONLY
    if kind is ParameterKind.KEYWORD_ONLY:
        return ArgumentPlacement.KEYWORD
    return ArgumentPlacement.POSITIONAL


def _filler_assignment(
    parameter: ParameterInfo,
    observed: str | None,
    dependency_name: str | None,
) -> ArgumentAssignment | None:
    """The non-adversarial assignment for one parameter, or ``None`` when absent."""
    placement = _placement_of_kind(parameter.kind)

    if observed is not None:
        return ArgumentAssignment(
            parameter=parameter.name,
            origin=AssignmentOrigin.EXISTING_TEST_BASELINE,
            placement=placement,
            value_text=observed,
            literal_kind=None,
            reason=(
                "literal argument observed in a resolved existing test for this "
                "target; observed usage, not a validity claim"
            ),
        )

    if parameter.name == dependency_name:
        baseline = baseline_for(parameter.shape)
        if baseline is not None:
            return ArgumentAssignment(
                parameter=parameter.name,
                origin=AssignmentOrigin.DEPENDENCY_BASELINE,
                placement=placement,
                value_text=baseline.value_text,
                literal_kind=baseline.literal_kind,
                reason=(
                    "filled so another parameter's symbolic boundary could be resolved through it"
                ),
            )

    baseline = baseline_for(parameter.shape)
    if parameter.has_default:
        return ArgumentAssignment(
            parameter=parameter.name,
            origin=AssignmentOrigin.DECLARED_DEFAULT,
            placement=ArgumentPlacement.OMITTED,
            value_text=None,
            literal_kind=None,
            reason=(
                "omitted so the declared default applies; the default expression is never evaluated"
            ),
        )

    if baseline is not None:
        return ArgumentAssignment(
            parameter=parameter.name,
            origin=AssignmentOrigin.STATIC_TYPE_BASELINE,
            placement=placement,
            value_text=baseline.value_text,
            literal_kind=baseline.literal_kind,
            reason=baseline.reason,
        )
    return None


def _map_observed_literals(
    parameters: tuple[ParameterInfo, ...], test_literals: tuple[str, ...]
) -> dict[str, str]:
    """Map literal arguments observed in a test onto this target's parameters.

    Positional literals fill parameters in declaration order. Anything that does
    not line up with a positional-capable parameter is dropped rather than
    guessed.
    """
    positional = [
        parameter
        for parameter in parameters
        if parameter.kind in (ParameterKind.POSITIONAL_ONLY, ParameterKind.POSITIONAL_OR_KEYWORD)
    ]
    mapped: dict[str, str] = {}
    for parameter, literal in zip(positional, test_literals, strict=False):
        mapped.setdefault(parameter.name, literal)
    return mapped


def _resolve_symbolic_dependency(
    action: SemanticAction, parameters: tuple[ParameterInfo, ...]
) -> tuple[str | None, BaselineValue | None]:
    """Resolve a symbolic reference that names another parameter of this target.

    Returns the referenced parameter's name and the baseline used. A dotted
    reference such as ``self.limit`` never resolves, because no receiver exists.
    """
    if action.status is not MaterializationStatus.SYMBOLIC:
        return None, None
    reference = action.symbolic_text
    if reference is None or not reference.isidentifier():
        return None, None

    for parameter in parameters:
        if parameter.name != reference:
            continue
        constant_kind = _SHAPE_TO_CONSTANT_KIND.get(parameter.shape.non_nullable().kind)
        if constant_kind is None:
            return None, None
        baseline = baseline_for(parameter.shape)
        if baseline is None:
            return None, None
        outcome = materialize_relational(action.kind, baseline.value_text, constant_kind)
        if outcome.status is not MaterializationStatus.CONCRETE:
            return None, None
        return parameter.name, BaselineValue(
            source=BaselineSource.DEPENDENCY,
            value_text=outcome.value_text or "",
            literal_kind=outcome.literal_kind or LiteralKind.INTEGER,
            reason=f"neighbour of the {reference} baseline {baseline.value_text}",
        )
    return None, None


def _status(
    *,
    symbolic_dependencies: list[str],
    missing: list[str],
    requires_receiver: bool,
) -> InvocationStatus:
    """Classify the plan, most fundamental gap first."""
    if symbolic_dependencies:
        return InvocationStatus.SYMBOLIC_DEPENDENCY
    if missing:
        return InvocationStatus.PARTIAL_ARGUMENTS
    if requires_receiver:
        return InvocationStatus.NEEDS_RECEIVER
    return InvocationStatus.COMPLETE_FUNCTION
