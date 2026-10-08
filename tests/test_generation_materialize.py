"""Tests for materializing a proposed action into a value."""

from __future__ import annotations

import math

import pytest

from edgehunter.analysis.models import BoundaryKind, ConstantKind
from edgehunter.generation import (
    AnnotationCategory,
    LiteralKind,
    MaterializationStatus,
    SemanticActionKind,
    materialize_collection,
    materialize_generic_numeric,
    materialize_relational,
    parse_float_literal,
    parse_integer_literal,
)

AT = SemanticActionKind.AT_BOUNDARY
BELOW = SemanticActionKind.JUST_BELOW_BOUNDARY
ABOVE = SemanticActionKind.JUST_ABOVE_BOUNDARY


def test_integer_neighbours_are_exact() -> None:
    assert materialize_relational(BELOW, "10", ConstantKind.INTEGER).value_text == "9"
    assert materialize_relational(AT, "10", ConstantKind.INTEGER).value_text == "10"
    assert materialize_relational(ABOVE, "10", ConstantKind.INTEGER).value_text == "11"


def test_integer_neighbours_cross_zero() -> None:
    assert materialize_relational(BELOW, "0", ConstantKind.INTEGER).value_text == "-1"
    assert materialize_relational(ABOVE, "0", ConstantKind.INTEGER).value_text == "1"


def test_float_neighbour_uses_the_adjacent_representable_value() -> None:
    outcome = materialize_relational(BELOW, "0.5", ConstantKind.FLOAT)

    assert outcome.status is MaterializationStatus.CONCRETE
    assert outcome.value_text == "0.49999999999999994"
    assert float(str(outcome.value_text)) == math.nextafter(0.5, -math.inf)
    assert outcome.literal_kind is LiteralKind.FLOAT


def test_float_boundary_keeps_the_exact_value() -> None:
    outcome = materialize_relational(AT, "0.1", ConstantKind.FLOAT)

    assert outcome.value_text == "0.1"
    assert float(str(outcome.value_text)) == 0.1


def test_name_reference_stays_symbolic() -> None:
    outcome = materialize_relational(AT, "balance", None)

    assert outcome.status is MaterializationStatus.SYMBOLIC
    assert outcome.value_text is None
    assert outcome.symbolic_text == "balance"
    assert outcome.literal_kind is None


def test_name_reference_cannot_be_stepped() -> None:
    outcome = materialize_relational(BELOW, "cfg.max", None)

    assert outcome.status is MaterializationStatus.SYMBOLIC
    assert outcome.symbolic_text == "cfg.max"
    assert "no neighbour" in outcome.reason


def test_complex_literal_cannot_be_stepped() -> None:
    below = materialize_relational(BELOW, "1j", ConstantKind.COMPLEX)
    above = materialize_relational(ABOVE, "1j", ConstantKind.COMPLEX)

    assert below.status is MaterializationStatus.UNSUPPORTED
    assert above.status is MaterializationStatus.UNSUPPORTED
    assert below.value_text is None


def test_complex_literal_can_be_matched_exactly() -> None:
    outcome = materialize_relational(AT, "1j", ConstantKind.COMPLEX)

    assert outcome.status is MaterializationStatus.CONCRETE
    assert outcome.value_text == "1j"
    assert outcome.literal_kind is LiteralKind.COMPLEX


def test_bool_literal_is_materialized_as_written() -> None:
    outcome = materialize_relational(AT, "True", ConstantKind.BOOL)

    assert outcome.value_text == "True"
    assert outcome.literal_kind is LiteralKind.BOOLEAN


@pytest.mark.parametrize(
    ("constant_kind", "expected"),
    [
        (ConstantKind.STRING, LiteralKind.TEXT),
        (ConstantKind.BYTES, LiteralKind.BYTES),
        (ConstantKind.NONE, LiteralKind.NOTHING),
    ],
)
def test_other_literals_match_but_do_not_step(
    constant_kind: ConstantKind, expected: LiteralKind
) -> None:
    matched = materialize_relational(AT, "x", constant_kind)
    stepped = materialize_relational(BELOW, "x", constant_kind)

    assert matched.literal_kind is expected
    assert stepped.status is MaterializationStatus.UNSUPPORTED


def test_generic_numeric_values_for_an_int_parameter() -> None:
    outcome = materialize_generic_numeric(BoundaryKind.ZERO, "int")

    assert outcome.status is MaterializationStatus.CONCRETE
    assert outcome.value_text == "0"
    assert outcome.literal_kind is LiteralKind.INTEGER


def test_generic_numeric_values_for_a_float_parameter() -> None:
    outcome = materialize_generic_numeric(BoundaryKind.ZERO, "float")

    assert outcome.value_text == "0.0"
    assert outcome.literal_kind is LiteralKind.FLOAT


def test_generic_nan_has_no_int_rendering() -> None:
    outcome = materialize_generic_numeric(BoundaryKind.NAN, "int")

    assert outcome.status is MaterializationStatus.UNSUPPORTED
    assert outcome.value_text is None


@pytest.mark.parametrize(
    ("category", "empty", "singleton"),
    [
        (AnnotationCategory.LIST, "[]", "[None]"),
        (AnnotationCategory.TUPLE, "()", "(None,)"),
        (AnnotationCategory.MAPPING, "{}", "{None: None}"),
        (AnnotationCategory.SET, "set()", "{None}"),
        (AnnotationCategory.FROZENSET, "frozenset()", "frozenset({None})"),
    ],
)
def test_collection_values_per_category(
    category: AnnotationCategory, empty: str, singleton: str
) -> None:
    assert materialize_collection(SemanticActionKind.EMPTY_COLLECTION, category).value_text == empty
    outcome = materialize_collection(SemanticActionKind.SINGLETON_COLLECTION, category)
    assert outcome.value_text == singleton
    assert outcome.literal_kind is LiteralKind.COLLECTION


def test_collection_value_for_an_unknown_category() -> None:
    outcome = materialize_collection(
        SemanticActionKind.EMPTY_COLLECTION, AnnotationCategory.UNKNOWN
    )

    assert outcome.status is MaterializationStatus.UNSUPPORTED


def test_non_collection_action_is_rejected() -> None:
    outcome = materialize_collection(SemanticActionKind.SET_ZERO, AnnotationCategory.LIST)

    assert outcome.status is MaterializationStatus.UNSUPPORTED


def test_literal_parsers_reject_non_literals() -> None:
    assert parse_integer_literal("balance") is None
    assert parse_float_literal("balance") is None
    assert parse_integer_literal("10") == 10
    assert parse_float_literal("0.5") == 0.5
