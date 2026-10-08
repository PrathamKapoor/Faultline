"""Turning a proposed action into a concrete, symbolic, or unsupported value.

The only arithmetic here is on literal numbers, needed so that "just below 10"
can be written as ``9`` instead of the meaningless ``10 - 1``. Nothing in this
module imports, calls, or evaluates analyzed code, and no reference expression
is ever evaluated to obtain a value: a comparison against a name stays symbolic,
which is the conservative outcome rather than a fabricated number.

Collection values are built from the element baseline the static annotation
shape actually supports. When the element type is unknown there is **no**
value: an unrecognized element yields ``UNSUPPORTED`` with a reason, never a
``None`` placeholder standing in for a type EdgeHunter does not know.

Literal text is parsed with ``int``/``float``, never ``eval`` or
``ast.literal_eval``, and only after the analysis layer has already classified
the expression as an integer or float literal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from edgehunter.analysis.models import BoundaryKind, ConstantKind
from edgehunter.generation.baselines import baseline_for
from edgehunter.generation.models import LiteralKind, MaterializationStatus, SemanticActionKind
from edgehunter.scanner.annotations import AnnotationKind, StaticAnnotation

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

# An empty container needs no element knowledge at all, which is why these are
# keyed purely by container kind.
_COLLECTION_EMPTY_VALUES: Final[dict[AnnotationKind, str]] = {
    AnnotationKind.LIST: "[]",
    AnnotationKind.TUPLE: "()",
    AnnotationKind.SET: "set()",
    AnnotationKind.DICT: "{}",
}

_COLLECTION_ACTIONS: Final[frozenset[SemanticActionKind]] = frozenset(
    {
        SemanticActionKind.EMPTY_COLLECTION,
        SemanticActionKind.SINGLETON_COLLECTION,
        SemanticActionKind.DUPLICATE_ELEMENT_COLLECTION,
    }
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
    boundary_kind: BoundaryKind, shape: StaticAnnotation
) -> Materialization:
    """Materialize a generic numeric boundary candidate for an annotated parameter.

    The rendering follows the declared shape: a ``float`` parameter receives
    ``0.0`` rather than ``0``, and a nullable integer is still an integer.
    """
    table = _GENERIC_FLOAT_VALUES if shape.kind is AnnotationKind.FLOAT else _GENERIC_INTEGER_VALUES
    entry = table.get(boundary_kind)
    if entry is None:
        return Materialization(
            status=MaterializationStatus.UNSUPPORTED,
            reason=f"no generic {boundary_kind.value} value for a {shape.description} parameter",
        )
    value_text, literal_kind = entry
    return Materialization(
        status=MaterializationStatus.CONCRETE,
        reason=f"generic numeric candidate for a {shape.description} parameter",
        value_text=value_text,
        literal_kind=literal_kind,
    )


def materialize_none(shape: StaticAnnotation) -> Materialization:
    """Materialize ``None`` for a parameter whose annotation permits it."""
    if not shape.is_nullable:
        return Materialization(
            status=MaterializationStatus.UNSUPPORTED,
            reason=f"{shape.description} does not permit None",
        )
    return Materialization(
        status=MaterializationStatus.CONCRETE,
        reason=f"annotation {shape.description} permits None",
        value_text="None",
        literal_kind=LiteralKind.NOTHING,
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


def materialize_collection(action: SemanticActionKind, shape: StaticAnnotation) -> Materialization:
    """Materialize a collection shape for a recognized annotation.

    ``EMPTY_COLLECTION`` is always available for a recognized container kind.
    ``SINGLETON_COLLECTION`` additionally needs a trustworthy element baseline,
    and ``DUPLICATE_ELEMENT_COLLECTION`` needs one too and is limited to ordered
    containers where a repeated element is meaningful. When the element shape is
    unknown the result is ``UNSUPPORTED``; no placeholder element is invented.
    """
    empty = _COLLECTION_EMPTY_VALUES.get(shape.kind)
    if action is SemanticActionKind.EMPTY_COLLECTION:
        if empty is None:
            return _not_a_collection(action, shape)
        return Materialization(
            status=MaterializationStatus.CONCRETE,
            reason=f"{shape.description}: empty container needs no element value",
            value_text=empty,
            literal_kind=LiteralKind.COLLECTION,
        )

    if action is SemanticActionKind.DUPLICATE_ELEMENT_COLLECTION and shape.kind not in (
        AnnotationKind.LIST,
        AnnotationKind.TUPLE,
    ):
        return Materialization(
            status=MaterializationStatus.UNSUPPORTED,
            reason=(
                "a repeated element is only meaningful for a list or tuple, "
                f"not {shape.description}"
            ),
        )

    entry = _collection_entry(action, shape)
    if entry is None:
        return _no_element_baseline(action, shape)
    value_text, note = entry
    return Materialization(
        status=MaterializationStatus.CONCRETE,
        reason=f"{shape.description}: {note}",
        value_text=value_text,
        literal_kind=LiteralKind.COLLECTION,
    )


def is_collection_action(action: SemanticActionKind) -> bool:
    """True for the three collection-shaped actions."""
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


def _collection_entry(
    action: SemanticActionKind, shape: StaticAnnotation
) -> tuple[str, str] | None:
    """Build the rendered collection text, or ``None`` when unsupported."""
    if shape.kind is AnnotationKind.DICT:
        return _dict_entry(action, shape)
    if shape.kind not in (AnnotationKind.LIST, AnnotationKind.TUPLE, AnnotationKind.SET):
        return None
    return _sequence_entry(action, shape)


def _sequence_entry(action: SemanticActionKind, shape: StaticAnnotation) -> tuple[str, str] | None:
    """Render a list/tuple/set entry from a known element baseline.

    A nested container element is filled with *its own* baseline rather than by
    recursing, so a ``list[list[int]]`` singleton is ``[[]]``: the inner
    ``list[int]`` receives its own simplest trustworthy value.
    """
    element = shape.element
    if element is None:
        return None
    baseline = baseline_for(element)
    if baseline is None:
        return None
    if shape.kind is AnnotationKind.SET and baseline.literal_kind is LiteralKind.COLLECTION:
        # A collection element is unhashable, so ``{[0]}`` is not valid Python.
        return None

    if action is SemanticActionKind.SINGLETON_COLLECTION:
        note = f"singleton using the {element.description} baseline {baseline.value_text}"
    else:
        note = f"repeated {element.description} baseline {baseline.value_text}"

    if shape.kind is AnnotationKind.SET:
        return f"{{{baseline.value_text}}}", note
    if action is SemanticActionKind.SINGLETON_COLLECTION:
        if shape.kind is AnnotationKind.LIST:
            return f"[{baseline.value_text}]", note
        return f"({baseline.value_text},)", note
    if shape.kind is AnnotationKind.LIST:
        return f"[{baseline.value_text}, {baseline.value_text}]", note
    return f"({baseline.value_text}, {baseline.value_text})", note


def _dict_entry(action: SemanticActionKind, shape: StaticAnnotation) -> tuple[str, str] | None:
    """Render a dict entry from known key and value baselines."""
    if shape.key is None or shape.value is None:
        return None
    key = baseline_for(shape.key)
    value = baseline_for(shape.value)
    if key is None or value is None:
        return None
    if key.literal_kind is LiteralKind.COLLECTION:
        return None
    note = (
        f"using {shape.key.description} key baseline {key.value_text} and "
        f"{shape.value.description} value baseline {value.value_text}"
    )
    return f"{{{key.value_text}: {value.value_text}}}", note


def _no_element_baseline(action: SemanticActionKind, shape: StaticAnnotation) -> Materialization:
    """Report a container whose element type has no trustworthy value."""
    return Materialization(
        status=MaterializationStatus.UNSUPPORTED,
        reason=(
            f"no trustworthy element baseline for {shape.description}, so "
            f"{action.value} cannot be written down; an unknown element type is "
            "not treated as nullable"
        ),
    )


def _not_a_collection(action: SemanticActionKind, shape: StaticAnnotation) -> Materialization:
    """Report a non-container annotation asked for a collection action."""
    return Materialization(
        status=MaterializationStatus.UNSUPPORTED,
        reason=f"{shape.description} is not a container",
    )


def _integer_neighbour(action: SemanticActionKind, reference: str) -> Materialization:
    """Exact integer neighbours, which are the only correct ones for ``int``."""
    value = parse_integer_literal(reference)
    if value is None:
        return _unreadable(reference, "integer")
    neighbour = _shift(action, value, -1, +1)
    if neighbour is None:
        return _unreadable(reference, "integer")
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
        return _unreadable(reference, "float")
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


def _unreadable(reference: str, kind: str) -> Materialization:
    """Report a literal that could not be parsed back into a number."""
    return Materialization(
        status=MaterializationStatus.UNSUPPORTED,
        reason=f"reference '{reference}' is not a readable {kind} literal",
    )
