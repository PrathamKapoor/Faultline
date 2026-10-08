"""Turning a proposed action into a concrete, symbolic, or unsupported value.

The only arithmetic here is on literal numbers, needed so that "just below 10"
can be written as ``9`` instead of the meaningless ``10 - 1``. Nothing in this
module imports, calls, or evaluates analyzed code, and no reference expression
is ever evaluated to obtain a value: a comparison against a name stays symbolic,
which is the conservative outcome rather than a fabricated number.

Literal text is parsed with ``int``/``float``, never ``eval`` or
``ast.literal_eval``, and only after the analysis layer has already classified
the expression as an integer or float literal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from edgehunter.analysis.models import BoundaryKind, ConstantKind
from edgehunter.generation.models import (
    AnnotationCategory,
    LiteralKind,
    MaterializationStatus,
    SemanticActionKind,
)

# How a value is written down. ``value_text`` is a self-contained Python
# expression for a concrete value; ``symbolic_text`` names what a symbolic value
# depends on. Both are source text, never evaluated objects.
_GENERIC_INTEGER_VALUES: Final[dict[BoundaryKind, tuple[str, LiteralKind]]] = {
    BoundaryKind.ZERO: ("0", LiteralKind.INTEGER),
    BoundaryKind.ONE: ("1", LiteralKind.INTEGER),
    BoundaryKind.NEGATIVE_ONE: ("-1", LiteralKind.INTEGER),
}

_GENERIC_FLOAT_VALUES: Final[dict[BoundaryKind, tuple[str, LiteralKind]]] = {
    BoundaryKind.ZERO: ("0.0", LiteralKind.FLOAT),
    BoundaryKind.ONE: ("1.0", LiteralKind.FLOAT),
    BoundaryKind.NEGATIVE_ONE: ("-1.0", LiteralKind.FLOAT),
    BoundaryKind.NEGATIVE_ZERO: ("-0.0", LiteralKind.FLOAT),
    BoundaryKind.NAN: ('float("nan")', LiteralKind.FLOAT),
    BoundaryKind.POSITIVE_INFINITY: ('float("inf")', LiteralKind.FLOAT),
    BoundaryKind.NEGATIVE_INFINITY: ('float("-inf")', LiteralKind.FLOAT),
}

_CONSTANT_TO_LITERAL_KIND: Final[dict[ConstantKind, LiteralKind]] = {
    ConstantKind.INTEGER: LiteralKind.INTEGER,
    ConstantKind.FLOAT: LiteralKind.FLOAT,
    ConstantKind.BOOL: LiteralKind.BOOLEAN,
    ConstantKind.STRING: LiteralKind.TEXT,
    ConstantKind.BYTES: LiteralKind.BYTES,
    ConstantKind.NONE: LiteralKind.NOTHING,
    ConstantKind.COMPLEX: LiteralKind.COMPLEX,
}

_COLLECTION_EMPTY_VALUES: Final[dict[AnnotationCategory, str]] = {
    AnnotationCategory.LIST: "[]",
    AnnotationCategory.TUPLE: "()",
    AnnotationCategory.MAPPING: "{}",
    AnnotationCategory.SET: "set()",
    AnnotationCategory.FROZENSET: "frozenset()",
}

# The element of a singleton collection is a placeholder: the element type is
# not known from a bare ``list``/``dict`` annotation. ``None`` is used because it
# is valid for every recognized collection, and because a target that rejects it
# is exercising its own validation, which is exactly the evidence EdgeHunter
# wants to reach.
_COLLECTION_SINGLETON_VALUES: Final[dict[AnnotationCategory, str]] = {
    AnnotationCategory.LIST: "[None]",
    AnnotationCategory.TUPLE: "(None,)",
    AnnotationCategory.MAPPING: "{None: None}",
    AnnotationCategory.SET: "{None}",
    AnnotationCategory.FROZENSET: "frozenset({None})",
}

_COLLECTION_ACTIONS: Final[frozenset[SemanticActionKind]] = frozenset(
    {SemanticActionKind.EMPTY_COLLECTION, SemanticActionKind.SINGLETON_COLLECTION}
)


@dataclass(frozen=True, slots=True)
class Materialization:
    """The outcome of materializing one action for one parameter."""

    status: MaterializationStatus
    reason: str
    value_text: str | None = None
    symbolic_text: str | None = None
    literal_kind: LiteralKind | None = None


def materialize_generic_numeric(
    boundary_kind: BoundaryKind, annotation: str | None
) -> Materialization:
    """Materialize a generic numeric boundary candidate for an annotated parameter.

    Boundary mining only emits these for an exact ``int`` or ``float``
    annotation, so the rendering is chosen to match the declared type: a ``float``
    parameter receives ``0.0`` rather than ``0``.
    """
    table = _GENERIC_FLOAT_VALUES if annotation == "float" else _GENERIC_INTEGER_VALUES
    entry = table.get(boundary_kind)
    if entry is None:
        return Materialization(
            status=MaterializationStatus.UNSUPPORTED,
            reason=f"no generic {boundary_kind.value} value for an {annotation} parameter",
        )
    value_text, literal_kind = entry
    return Materialization(
        status=MaterializationStatus.CONCRETE,
        reason=f"generic numeric candidate for an {annotation} parameter",
        value_text=value_text,
        literal_kind=literal_kind,
    )


def materialize_relational(
    action: SemanticActionKind, reference: str, constant_kind: ConstantKind | None
) -> Materialization:
    """Materialize a boundary-relative action against one comparison reference.

    A literal reference yields a concrete neighbour; a name or attribute yields a
    symbolic result, because stepping away from it would require knowing its
    runtime value.
    """
    if constant_kind is None:
        if action is SemanticActionKind.AT_BOUNDARY:
            return Materialization(
                status=MaterializationStatus.SYMBOLIC,
                reason=f"reference '{reference}' is a name, so its value is not known statically",
                symbolic_text=reference,
            )
        return Materialization(
            status=MaterializationStatus.SYMBOLIC,
            reason=(
                f"reference '{reference}' is a name, so no neighbour of it can be "
                "written down without knowing its runtime value"
            ),
            symbolic_text=reference,
        )

    if constant_kind is ConstantKind.INTEGER:
        return _integer_neighbour(action, reference)
    if constant_kind is ConstantKind.FLOAT:
        return _float_neighbour(action, reference)
    return _literal_reference(action, reference, constant_kind)


def materialize_collection(
    action: SemanticActionKind, category: AnnotationCategory
) -> Materialization:
    """Materialize an empty or singleton collection for a recognized annotation."""
    if action is SemanticActionKind.EMPTY_COLLECTION:
        values = _COLLECTION_EMPTY_VALUES
    elif action is SemanticActionKind.SINGLETON_COLLECTION:
        values = _COLLECTION_SINGLETON_VALUES
    else:
        return Materialization(
            status=MaterializationStatus.UNSUPPORTED,
            reason=f"{action.value} is not a collection action",
        )
    value_text = values.get(category)
    if value_text is None:
        return Materialization(
            status=MaterializationStatus.UNSUPPORTED,
            reason=f"no collection value known for category {category.value}",
        )
    return Materialization(
        status=MaterializationStatus.CONCRETE,
        reason=(
            f"parameter annotated {category.value}; the singleton element is a "
            "placeholder because the element type is not known statically"
        )
        if action is SemanticActionKind.SINGLETON_COLLECTION
        else f"parameter annotated {category.value}",
        value_text=value_text,
        literal_kind=LiteralKind.COLLECTION,
    )


def is_collection_action(action: SemanticActionKind) -> bool:
    """True for the two collection-shaped actions."""
    return action in _COLLECTION_ACTIONS


def parse_integer_literal(text: str) -> int | None:
    """Parse an integer literal's source text, or return ``None``.

    ``int`` is used rather than ``eval`` or ``ast.literal_eval``. The caller has
    already classified the expression as an integer literal, so a failure means
    the text is not one after all.
    """
    try:
        return int(text, 10)
    except ValueError:
        return None


def parse_float_literal(text: str) -> float | None:
    """Parse a float literal's source text, or return ``None``."""
    try:
        return float(text)
    except ValueError:
        return None


def _integer_neighbour(action: SemanticActionKind, reference: str) -> Materialization:
    """Exact integer neighbours, which are the only correct ones for ``int``."""
    value = parse_integer_literal(reference)
    if value is None:
        return _unreadable(action, reference, "integer")
    neighbour = _shift(action, value, -1, +1)
    if neighbour is None:
        return _unreadable(action, reference, "integer")
    return Materialization(
        status=MaterializationStatus.CONCRETE,
        reason=f"integer neighbour of literal {reference}",
        value_text=str(neighbour),
        literal_kind=LiteralKind.INTEGER,
    )


def _float_neighbour(action: SemanticActionKind, reference: str) -> Materialization:
    """Float neighbours via ``math.nextafter``, the adjacent representable value."""
    value = parse_float_literal(reference)
    if value is None:
        return _unreadable(action, reference, "float")
    if action is SemanticActionKind.JUST_BELOW_BOUNDARY:
        neighbour = math.nextafter(value, -math.inf)
    elif action is SemanticActionKind.JUST_ABOVE_BOUNDARY:
        neighbour = math.nextafter(value, math.inf)
    else:
        neighbour = value
    return Materialization(
        status=MaterializationStatus.CONCRETE,
        reason=f"float neighbour of literal {reference} via math.nextafter",
        value_text=repr(neighbour),
        literal_kind=LiteralKind.FLOAT,
    )


def _literal_reference(
    action: SemanticActionKind, reference: str, constant_kind: ConstantKind
) -> Materialization:
    """Non-numeric literals can be matched but not stepped away from."""
    literal_kind = _CONSTANT_TO_LITERAL_KIND.get(constant_kind)
    if action is not SemanticActionKind.AT_BOUNDARY or literal_kind is None:
        return Materialization(
            status=MaterializationStatus.UNSUPPORTED,
            reason=f"no neighbour of a {constant_kind.value} literal can be written down",
        )
    return Materialization(
        status=MaterializationStatus.CONCRETE,
        reason=f"exact {constant_kind.value} literal from the comparison",
        value_text=reference,
        literal_kind=literal_kind,
    )


def _shift(action: SemanticActionKind, value: int, below: int, above: int) -> int | None:
    """Return the integer neighbour the action asks for."""
    if action is SemanticActionKind.JUST_BELOW_BOUNDARY:
        return value + below
    if action is SemanticActionKind.JUST_ABOVE_BOUNDARY:
        return value + above
    return value


def _unreadable(action: SemanticActionKind, reference: str, kind: str) -> Materialization:
    """Report a literal that could not be parsed back into a number."""
    return Materialization(
        status=MaterializationStatus.UNSUPPORTED,
        reason=f"reference '{reference}' is not a readable {kind} literal",
    )
