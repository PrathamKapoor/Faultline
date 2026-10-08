"""Tests for the versioned typed-value protocol.

These are the tests that stand between a rendered string and a Python value, so
they cover the special float spellings, the collection type distinctions, every
bound, and the requirement that a malicious string stays a string.
"""

from __future__ import annotations

import math

import pytest

from edgehunter.execution.codec import (
    MAX_COLLECTION_ITEMS,
    MAX_DOCUMENT_BYTES,
    MAX_MAPPING_ENTRIES,
    MAX_NESTING_DEPTH,
    MAX_STRING_LENGTH,
    PROTOCOL_VERSION,
    ProtocolError,
    TypedValue,
    ValueKind,
    check_document_size,
    decode_document,
    decode_value,
    dumps,
    encode_float,
    encode_value,
    recognize_literal,
)


def _round_trip(text: str) -> object:
    """Recognize a literal, encode it, decode it, and rebuild the value."""
    return recognize_literal(text).to_python()


def test_integer_round_trip() -> None:
    value = recognize_literal("42")

    assert value.kind is ValueKind.INTEGER
    assert value.scalar == "42"
    assert _round_trip("42") == 42
    assert decode_value(encode_value(value)).scalar == "42"


@pytest.mark.parametrize("text", ["0.0", "1.0", "-1.0", "1.5", "1e-3", "-2.5e10"])
def test_finite_float_round_trip(text: str) -> None:
    assert _round_trip(text) == float(text)


def test_negative_zero_keeps_its_sign() -> None:
    result = _round_trip("-0.0")

    assert isinstance(result, float)
    assert result == 0.0
    assert math.copysign(1.0, result) < 0.0


def test_positive_zero_is_positive() -> None:
    result = _round_trip("0.0")

    assert isinstance(result, float)
    assert math.copysign(1.0, result) > 0.0


def test_nan_round_trip_stays_nan() -> None:
    result = _round_trip('float("nan")')

    assert isinstance(result, float)
    assert math.isnan(result)


def test_nan_is_not_ordinary_equal() -> None:
    value = recognize_literal('float("nan")')

    assert value.scalar == "nan"
    # Encoding twice must produce identical text even though NaN != NaN.
    assert encode_value(value) == encode_value(value)
    assert dumps(encode_value(value)) == dumps(encode_value(value))


@pytest.mark.parametrize(
    ("text", "expected"),
    [('float("inf")', math.inf), ('float("-inf")', -math.inf)],
)
def test_infinities_round_trip(text: str, expected: float) -> None:
    result = _round_trip(text)

    assert isinstance(result, float)
    assert result == expected
    assert math.isinf(result)


def test_smallest_adjacent_float_survives() -> None:
    text = encode_float(math.nextafter(1.0, 2.0))

    assert _round_trip(text) == math.nextafter(1.0, 2.0)


def test_large_finite_float_survives() -> None:
    assert _round_trip("1e+300") == 1e300


def test_boolean_is_distinct_from_integer() -> None:
    boolean = recognize_literal("True")
    integer = recognize_literal("1")

    assert boolean.kind is ValueKind.BOOLEAN
    assert integer.kind is ValueKind.INTEGER
    assert _round_trip("True") is True
    assert _round_trip("False") is False
    assert _round_trip("1") == 1
    # Python makes bool a subclass of int, so identity is the real distinction.
    assert type(_round_trip("True")) is bool
    assert type(_round_trip("1")) is int


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('""', ""),
        ('"abc"', "abc"),
        ('"é"', "é"),
        ('"\\x00"', "\x00"),
        ('"line\\nbreak"', "line\nbreak"),
    ],
)
def test_string_with_unicode_and_control_characters(text: str, expected: str) -> None:
    assert _round_trip(text) == expected


def test_none_round_trip() -> None:
    assert recognize_literal("None").kind is ValueKind.NONE
    assert _round_trip("None") is None


def test_list_and_tuple_are_distinguished() -> None:
    assert recognize_literal("[]").kind is ValueKind.LIST
    assert recognize_literal("()").kind is ValueKind.TUPLE
    assert isinstance(_round_trip("[]"), list)
    assert isinstance(_round_trip("()"), tuple)
    assert _round_trip("[0]") == [0]
    assert _round_trip("[0, 0]") == [0, 0]
    assert _round_trip("(0,)") == (0,)
    assert _round_trip("(0, 0)") == (0, 0)


def test_empty_set_and_empty_dict_are_distinguished() -> None:
    assert recognize_literal("set()").kind is ValueKind.SET
    assert recognize_literal("{}").kind is ValueKind.MAPPING
    assert _round_trip("set()") == set()
    assert _round_trip("{}") == {}


def test_set_and_dict_contents_survive() -> None:
    assert _round_trip("{False}") == {False}
    assert _round_trip('{"": 0}') == {"": 0}


def test_nested_collections_round_trip() -> None:
    assert _round_trip("[[0]]") == [[0]]
    assert _round_trip('{"": [0]}') == {"": [0]}
    assert _round_trip("[[], []]") == [[], []]


def test_maximum_nesting_is_accepted() -> None:
    text = "0"
    for _ in range(MAX_NESTING_DEPTH - 1):
        text = f"[{text}]"

    assert recognize_literal(text).kind is ValueKind.LIST


def test_nesting_beyond_the_maximum_is_rejected() -> None:
    text = "0"
    for _ in range(MAX_NESTING_DEPTH + 2):
        text = f"[{text}]"

    with pytest.raises(ProtocolError):
        recognize_literal(text)


def test_too_many_items_is_rejected() -> None:
    text = "[" + ",".join("0" for _ in range(MAX_COLLECTION_ITEMS + 1)) + "]"

    with pytest.raises(ProtocolError):
        recognize_literal(text)


def test_too_many_mapping_entries_is_rejected() -> None:
    body = ",".join(f"{index}: 0" for index in range(MAX_MAPPING_ENTRIES + 1))

    with pytest.raises(ProtocolError):
        recognize_literal("{" + body + "}")


def test_oversized_string_is_rejected() -> None:
    text = '"' + "a" * (MAX_STRING_LENGTH + 1) + '"'

    with pytest.raises(ProtocolError):
        recognize_literal(text)


def test_oversized_document_is_rejected() -> None:
    payload = "x" * (MAX_DOCUMENT_BYTES + 1)

    with pytest.raises(ProtocolError):
        check_document_size(payload)


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ProtocolError):
        decode_value({"kind": "complex", "value": "1j"})


def test_missing_kind_is_rejected() -> None:
    with pytest.raises(ProtocolError):
        decode_value({"value": "1"})


def test_extra_keys_are_rejected() -> None:
    with pytest.raises(ProtocolError):
        decode_value({"kind": "integer", "value": "1", "extra": True})


def test_non_string_value_is_rejected() -> None:
    with pytest.raises(ProtocolError):
        decode_value({"kind": "integer", "value": 1})


def test_malformed_boolean_is_rejected() -> None:
    with pytest.raises(ProtocolError):
        decode_value({"kind": "boolean", "value": "True"})


def test_non_canonical_integer_is_rejected() -> None:
    with pytest.raises(ProtocolError):
        decode_value({"kind": "integer", "value": "1.0"})


def test_unhashable_mapping_key_is_rejected() -> None:
    document = {
        "kind": "mapping",
        "entries": [
            [
                {"kind": "list", "items": [{"kind": "integer", "value": "1"}]},
                {"kind": "integer", "value": "1"},
            ]
        ],
    }

    with pytest.raises(ProtocolError):
        decode_value(document)


def test_unhashable_set_element_is_rejected() -> None:
    document = {
        "kind": "set",
        "items": [{"kind": "tuple", "items": [{"kind": "integer", "value": "1"}]}],
    }

    with pytest.raises(ProtocolError):
        decode_value(document)


def test_invalid_protocol_version_is_rejected() -> None:
    with pytest.raises(ProtocolError):
        decode_document(dumps({"protocol_version": 99}))


def test_boolean_version_is_rejected() -> None:
    with pytest.raises(ProtocolError):
        decode_document(dumps({"protocol_version": True}))


def test_missing_protocol_version_is_rejected() -> None:
    with pytest.raises(ProtocolError):
        decode_document(dumps({"kind": "integer"}))


def test_invalid_json_is_rejected() -> None:
    with pytest.raises(ProtocolError):
        decode_document("{not json")


def test_malicious_expression_is_treated_as_data_not_executed() -> None:
    """A string that looks like code must stay a string, or be refused."""
    marker_free_text = "__import__('os').system('echo pwned')"

    assert _round_trip(f'"{marker_free_text}"') == marker_free_text

    for hostile in (
        "__import__('os').system('echo pwned')",
        "open('/etc/passwd').read()",
        "[x for x in range(3)]",
        "lambda: 1",
        "eval('1+1')",
        "(1).__class__",
        "os.system('x')",
        "{**{}}",
        "f'{1}'",
        "a.b",
        "a[0]",
        "1 + 1",
    ):
        with pytest.raises(ProtocolError):
            recognize_literal(hostile)


def test_call_spellings_are_limited_to_the_permitted_ones() -> None:
    for permitted in ('float("nan")', 'float("inf")', 'float("-inf")', "set()"):
        assert recognize_literal(permitted).kind in (ValueKind.FLOAT, ValueKind.SET)

    for refused in ('float("1.0")', "set(1)", "frozenset()", "dict()", "list()", "int('1')"):
        with pytest.raises(ProtocolError):
            recognize_literal(refused)


def test_dict_unpacking_is_rejected() -> None:
    with pytest.raises(ProtocolError):
        recognize_literal("{**{}}")


def test_bytes_and_complex_literals_are_rejected() -> None:
    with pytest.raises(ProtocolError):
        recognize_literal("b'x'")
    with pytest.raises(ProtocolError):
        recognize_literal("1j")


def test_ellipsis_is_rejected() -> None:
    with pytest.raises(ProtocolError):
        recognize_literal("...")


def test_encoding_is_deterministic_for_identical_input() -> None:
    first = dumps(encode_value(recognize_literal("[0, 0]")))
    second = dumps(encode_value(recognize_literal("[0, 0]")))

    assert first == second


def test_set_encoding_is_order_independent() -> None:
    def set_value(values: list[int]) -> TypedValue:
        return TypedValue(
            kind=ValueKind.SET,
            items=tuple(
                TypedValue(kind=ValueKind.INTEGER, scalar=str(number)) for number in values
            ),
        )

    assert dumps(encode_value(set_value([1, 2]))) == dumps(encode_value(set_value([2, 1])))


def test_non_standard_json_tokens_are_never_emitted() -> None:
    encoded = dumps(encode_value(recognize_literal('float("nan")')))

    assert "NaN" not in encoded
    assert "Infinity" not in encoded
    assert '"nan"' in encoded


def test_protocol_version_constant() -> None:
    assert PROTOCOL_VERSION == 1
