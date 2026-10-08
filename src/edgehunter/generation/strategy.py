"""Framework-independent strategy plans for a target's parameters.

A :class:`StrategyPlan` describes *how* a parameter could be generated, not what
a generator library should be handed. The intended pipeline is

``SemanticAction`` -> materialization -> ``StrategyPlan`` -> (later) an adapter

so the research/action layer never depends on one property-based testing
library. Phase 5 deliberately stops at the plan: nothing here constructs a
strategy object, and nothing samples a value.

Two properties matter for the plans themselves:

* **Determinism.** The plan is a fixed, ordered list of descriptor/value pairs,
  so CLI output never changes between runs.
* **Honest deduplication.** Values are deduplicated by their *descriptor*, not by
  equality. ``NaN != NaN``, so equality-based dedup would either keep duplicates
  or behave unpredictably for the float specials; descriptors are stable keys.
* **Honest provenance.** A boundary-derived example is labelled with the boundary
  it came from, so a reader can tell "the generic integer zero" from "one above
  the literal 10 in this function".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from edgehunter.analysis.models import BoundaryKind
from edgehunter.generation.materialize import Materialization, materialize_collection
from edgehunter.generation.models import (
    LiteralKind,
    MaterializationStatus,
    SemanticAction,
    SemanticActionKind,
)
from edgehunter.scanner.annotations import AnnotationKind, StaticAnnotation
from edgehunter.scanner.models import FunctionInfo, ParameterInfo, ParameterKind

_LONG_STRING: Final[str] = "a" * 64


class StrategyKind(str, Enum):
    """The shape of generation a parameter's plan describes."""

    JUST = "just"
    """A single deterministic value."""

    SAMPLED_FROM = "sampled_from"
    """A fixed, ordered set of deterministic values."""

    SYMBOLIC = "symbolic"
    """The value depends on something not knowable statically."""

    UNSUPPORTED = "unsupported"
    """No trustworthy generation is available for this annotation."""


@dataclass(frozen=True, slots=True)
class StrategyValue:
    """One deterministic example with a stable semantic descriptor."""

    descriptor: str
    text: str
    literal_kind: LiteralKind


@dataclass(frozen=True, slots=True)
class ParameterStrategy:
    """How one parameter of one target could be generated."""

    parameter: str
    annotation: StaticAnnotation
    kind: StrategyKind
    values: tuple[StrategyValue, ...]
    depends_on: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class StrategyPlan:
    """The generation plan for every parameter of one target."""

    relative_path: Path
    target: str
    lineno: int
    signature: str
    parameters: tuple[ParameterStrategy, ...]


# The deterministic high-value set for each recognized primitive shape. These are
# EdgeHunter's own semantic choices, not a library's defaults.
_PRIMITIVE_EXAMPLES: Final[dict[AnnotationKind, tuple[tuple[str, str, LiteralKind], ...]]] = {
    AnnotationKind.INT: (
        ("zero", "0", LiteralKind.INTEGER),
        ("one", "1", LiteralKind.INTEGER),
        ("negative_one", "-1", LiteralKind.INTEGER),
    ),
    AnnotationKind.FLOAT: (
        ("zero", "0.0", LiteralKind.FLOAT),
        ("one", "1.0", LiteralKind.FLOAT),
        ("negative_one", "-1.0", LiteralKind.FLOAT),
        ("negative_zero", "-0.0", LiteralKind.FLOAT),
        ("nan", 'float("nan")', LiteralKind.FLOAT),
        ("positive_infinity", 'float("inf")', LiteralKind.FLOAT),
        ("negative_infinity", 'float("-inf")', LiteralKind.FLOAT),
    ),
    AnnotationKind.BOOL: (
        ("false", "False", LiteralKind.BOOLEAN),
        ("true", "True", LiteralKind.BOOLEAN),
    ),
    AnnotationKind.STR: (
        ("empty", '""', LiteralKind.TEXT),
        ("space", '" "', LiteralKind.TEXT),
        ("unicode", '"é"', LiteralKind.TEXT),
        ("nul", '"\\x00"', LiteralKind.TEXT),
        ("long", f'"{_LONG_STRING}"', LiteralKind.TEXT),
    ),
    AnnotationKind.NONE: (("none", "None", LiteralKind.NOTHING),),
}

_BOUNDARY_DESCRIPTORS: Final[dict[BoundaryKind, str]] = {
    BoundaryKind.JUST_BELOW: "just_below",
    BoundaryKind.AT_BOUNDARY: "at_boundary",
    BoundaryKind.JUST_ABOVE: "just_above",
    BoundaryKind.ZERO: "zero",
    BoundaryKind.ONE: "one",
    BoundaryKind.NEGATIVE_ONE: "negative_one",
    BoundaryKind.NEGATIVE_ZERO: "negative_zero",
    BoundaryKind.NAN: "nan",
    BoundaryKind.POSITIVE_INFINITY: "positive_infinity",
    BoundaryKind.NEGATIVE_INFINITY: "negative_infinity",
}

_CONTAINER_ACTIONS: Final[tuple[tuple[str, SemanticActionKind], ...]] = (
    ("empty", SemanticActionKind.EMPTY_COLLECTION),
    ("singleton", SemanticActionKind.SINGLETON_COLLECTION),
    ("repeated_element", SemanticActionKind.DUPLICATE_ELEMENT_COLLECTION),
)

_VARIADIC_KINDS: Final[frozenset[ParameterKind]] = frozenset(
    {ParameterKind.VAR_POSITIONAL, ParameterKind.VAR_KEYWORD}
)


def plan_strategies(
    relative_path: Path,
    function: FunctionInfo,
    actions: tuple[SemanticAction, ...],
) -> StrategyPlan:
    """Build the strategy plan for one target from its annotation and actions."""
    by_parameter: dict[str, list[SemanticAction]] = {}
    for action in actions:
        by_parameter.setdefault(action.parameter, []).append(action)

    parameters = tuple(
        _parameter_strategy(parameter, tuple(by_parameter.get(parameter.name, ())))
        for parameter in function.parameters
        if parameter.kind not in _VARIADIC_KINDS
    )
    return StrategyPlan(
        relative_path=relative_path,
        target=function.qualified_name,
        lineno=function.lineno,
        signature=function.signature,
        parameters=parameters,
    )


def _parameter_strategy(
    parameter: ParameterInfo, actions: tuple[SemanticAction, ...]
) -> ParameterStrategy:
    """Build one parameter's strategy entry."""
    shape = parameter.shape
    values = _collect_values(shape, actions)
    depends_on = _dependencies(actions)

    if shape.kind is AnnotationKind.UNION:
        kind = StrategyKind.UNSUPPORTED
        reason = f"{shape.description}: a multi-type union has no trustworthy value set"
    elif shape.kind is AnnotationKind.UNKNOWN:
        if depends_on:
            kind = StrategyKind.SYMBOLIC
            reason = f"{shape.description}: values depend on {', '.join(depends_on)}"
        else:
            kind = StrategyKind.UNSUPPORTED
            reason = f"{shape.description}: annotation not recognized statically"
    elif not values:
        kind = StrategyKind.UNSUPPORTED
        reason = f"{shape.description}: no deterministic example is available"
    elif len(values) == 1:
        kind = StrategyKind.JUST
        reason = f"single deterministic example for {shape.description}"
    else:
        kind = StrategyKind.SAMPLED_FROM
        reason = f"deterministic example set for {shape.description}"

    if depends_on and kind is not StrategyKind.UNSUPPORTED:
        reason = f"{reason}; depends on {', '.join(depends_on)}"

    return ParameterStrategy(
        parameter=parameter.name,
        annotation=shape,
        kind=kind,
        values=values,
        depends_on=depends_on,
        reason=reason,
    )


def _collect_values(
    shape: StaticAnnotation, actions: tuple[SemanticAction, ...]
) -> tuple[StrategyValue, ...]:
    """Merge the primitive examples, the nullable example, and action values.

    Deduplication is by rendered text, which is the real identity of a concrete
    source-text value. Two descriptors describing the same text — the primitive
    ``empty`` and an ``empty_collection`` action, say — therefore collapse to a
    single entry instead of listing the same input twice. Text is also safe for
    the float specials, where value equality would fail because ``NaN != NaN``.
    """
    ordered: dict[str, StrategyValue] = {}

    for descriptor, text, literal_kind in _PRIMITIVE_EXAMPLES.get(shape.non_nullable().kind, ()):
        ordered.setdefault(text, StrategyValue(descriptor, text, literal_kind))

    if shape.is_nullable:
        ordered.setdefault("None", StrategyValue("none", "None", LiteralKind.NOTHING))

    for value in _container_values(shape):
        ordered.setdefault(value.text, value)

    for value in _action_values(actions):
        ordered.setdefault(value.text, value)

    return tuple(ordered.values())


def _container_values(shape: StaticAnnotation) -> tuple[StrategyValue, ...]:
    """The empty/singleton/repeated examples for a recognized container shape."""
    values: list[StrategyValue] = []
    for descriptor, action in _CONTAINER_ACTIONS:
        outcome: Materialization = materialize_collection(action, shape)
        if outcome.status is MaterializationStatus.CONCRETE and outcome.value_text is not None:
            values.append(StrategyValue(descriptor, outcome.value_text, LiteralKind.COLLECTION))
    return tuple(values)


def _action_values(actions: tuple[SemanticAction, ...]) -> tuple[StrategyValue, ...]:
    """Boundary-derived examples, labelled with the boundary that produced them."""
    values: list[StrategyValue] = []
    for action in actions:
        if action.status is not MaterializationStatus.CONCRETE:
            continue
        if action.value_text is None or action.literal_kind is None:
            continue
        values.append(
            StrategyValue(_descriptor_for(action), action.value_text, action.literal_kind)
        )
    return tuple(values)


def _descriptor_for(action: SemanticAction) -> str:
    """A stable descriptor naming where this example came from."""
    if action.boundary_kind is None:
        return action.kind.value
    label = _BOUNDARY_DESCRIPTORS.get(action.boundary_kind, action.kind.value)
    if action.reference is None:
        return label
    return f"{label}.{action.reference}"


def _dependencies(actions: tuple[SemanticAction, ...]) -> tuple[str, ...]:
    """Sorted names this parameter's values depend on."""
    names = {
        action.symbolic_text
        for action in actions
        if action.status is MaterializationStatus.SYMBOLIC and action.symbolic_text is not None
    }
    return tuple(sorted(names))
