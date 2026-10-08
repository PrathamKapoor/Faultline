"""Turning an ``InvocationPlan`` into a correctly assembled call.

Phase 5 records each argument's *placement* but deliberately does not build a
call, because doing it naively is wrong. The trap this module exists to avoid:

.. code-block:: python

    def f(a, b=10, c=20): ...

Supplying ``a`` and ``c`` positionally as ``f(1, 2)`` binds ``2`` to ``b`` and
leaves ``c`` at its default. The correct call is ``f(1, c=2)``.

The rule implemented here is that a positional-or-keyword parameter may only be
passed positionally while **every** preceding positional-or-keyword parameter is
also passed positionally. The first omitted one closes the positional run, and
everything after it is passed by keyword. Positional-only parameters are always
passed positionally, because by Python's own syntax they always precede every
other kind.

Every planned value is converted through the strict literal recognizer in
:mod:`edgehunter.execution.codec`. A value that cannot be recognized aborts the
assembly; nothing is invented to make a call succeed.
"""

from __future__ import annotations

from edgehunter.execution.codec import ProtocolError, TypedValue, recognize_literal
from edgehunter.execution.models import InvocationArguments, TargetRef
from edgehunter.generation.actions import receiver_parameter
from edgehunter.generation.invocation import (
    ArgumentPlacement,
    InvocationPlan,
    InvocationStatus,
)
from edgehunter.scanner.models import FunctionInfo, ParameterKind

_VARIADIC_KINDS = frozenset({ParameterKind.VAR_POSITIONAL, ParameterKind.VAR_KEYWORD})


class AssemblyFailure(Exception):
    """A valid call could not be assembled without fabricating an argument."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def assemble_invocation(plan: InvocationPlan, function: FunctionInfo) -> InvocationArguments:
    """Assemble the call described by ``plan``.

    Raises :class:`AssemblyFailure` when a required parameter has no value, when
    a planned value cannot be recognized as a protocol value, or when the plan is
    not complete enough to describe a call at all.
    """
    if plan.status is not InvocationStatus.COMPLETE_FUNCTION:
        raise AssemblyFailure(f"plan status is {plan.status.value}, not complete")

    by_name = {item.parameter: item for item in plan.assignments}
    receiver = receiver_parameter(function)

    positional: list[TypedValue] = []
    keyword: list[tuple[str, TypedValue]] = []
    positional_run_open = True

    for parameter in function.parameters:
        if parameter is receiver:
            continue
        if parameter.kind in _VARIADIC_KINDS:
            # Variadics are left empty; nothing in the plan fills them.
            if parameter.kind is ParameterKind.VAR_POSITIONAL:
                positional_run_open = False
            continue

        assignment = by_name.get(parameter.name)
        if assignment is None or _is_unused(assignment.placement):
            if not parameter.has_default:
                raise AssemblyFailure(f"required parameter {parameter.name!r} has no planned value")
            positional_run_open = False
            continue

        value = _recognize(assignment.parameter, assignment.value_text)
        if parameter.kind is ParameterKind.POSITIONAL_ONLY:
            positional.append(value)
            continue
        if parameter.kind is ParameterKind.POSITIONAL_OR_KEYWORD and positional_run_open:
            positional.append(value)
            continue
        keyword.append((parameter.name, value))

    return InvocationArguments(args=tuple(positional), kwargs=tuple(keyword))


def _is_unused(placement: ArgumentPlacement) -> bool:
    """True when an assignment records "not supplied" rather than a value."""
    return placement in (
        ArgumentPlacement.OMITTED,
        ArgumentPlacement.VAR_POSITIONAL,
        ArgumentPlacement.VAR_KEYWORD,
    )


def _recognize(parameter: str, value_text: str | None) -> TypedValue:
    """Recognize one planned value, refusing anything outside the grammar."""
    if value_text is None:
        raise AssemblyFailure(f"parameter {parameter!r} was assigned no value")
    try:
        return recognize_literal(value_text)
    except ProtocolError as exc:
        raise AssemblyFailure(
            f"planned value for {parameter!r} is not a supported protocol value: {exc}"
        ) from exc


def action_identifier(plan: InvocationPlan) -> str:
    """A stable identifier for the plan's single adversarial action.

    Kept on the request and on the result so an observation can always be traced
    back to the action that produced it.
    """
    action = plan.primary_action
    reference = action.reference or ""
    return f"{plan.target}:{action.parameter}:{action.kind.value}:{reference}"


def build_target_ref(plan: InvocationPlan) -> TargetRef:
    """The target identity the worker will use, as a relative POSIX path."""
    return TargetRef(
        relative_file=plan.relative_path.as_posix(),
        qualified_name=plan.target,
        lineno=plan.lineno,
    )
