"""The versioned typed-value protocol shared by the host and the sandbox worker.

Phase 5 reports planned values as *rendered source text* so CLI output stays
deterministic and readable. That text is for humans; it is **not** a wire format
and must never be turned into a Python value by evaluating it. This module is the
only bridge between the two, and it is deliberately the narrowest possible:

* :func:`recognize_literal` turns one of EdgeHunter's own rendered literals into a
  :class:`TypedValue` by **recognizing** a closed grammar — it is a parser with a
  whitelist, not an evaluator. Only literal constructs survive: numbers, strings,
  booleans, ``None``, and brackets. A name, attribute access, subscript, operator,
  comprehension, lambda, or f-string is rejected, as is every call except the
  three float spellings and the empty-set spelling EdgeHunter itself emits.
* :func:`encode_value` and :func:`decode_value` move :class:`TypedValue` across
  the container boundary as strict JSON with explicit type tags. Special floats
  travel as tagged strings, never as the non-standard JSON tokens ``NaN`` or
  ``Infinity``.

Nothing here uses ``eval``, ``exec``, ``literal_eval``, ``pickle``, or any other
object serializer, and no decoder branch constructs a class named by the input.
Decoding is bounded in size, depth, item count, and string length *before* any
container is built, so a hostile document cannot exhaust memory or recurse
without limit.
"""

from __future__ import annotations

import ast
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

PROTOCOL_VERSION: Final[int] = 1

# Bounds applied while decoding a request or a result document. They are checked
# before any container is constructed.
MAX_DOCUMENT_BYTES: Final[int] = 64 * 1024
MAX_NESTING_DEPTH: Final[int] = 4
MAX_COLLECTION_ITEMS: Final[int] = 100
MAX_STRING_LENGTH: Final[int] = 4096
MAX_MAPPING_ENTRIES: Final[int] = 100
MAX_RECOGNIZED_TEXT_LENGTH: Final[int] = 8192

_FLOAT_CALL_SPELLINGS: Final[frozenset[str]] = frozenset({"nan", "inf", "-inf"})
_EMPTY_SET_NAMES: Final[frozenset[str]] = frozenset({"set"})


class ProtocolError(Exception):
    """A document violated the protocol: bad shape, bad tag, or over a limit."""


class ValueKind(str, Enum):
    """The closed set of value types the protocol can represent."""

    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    STRING = "string"
    NONE = "none"
    LIST = "list"
    TUPLE = "tuple"
    SET = "set"
    MAPPING = "mapping"


_SCALAR_KINDS: Final[frozenset[ValueKind]] = frozenset(
    {
        ValueKind.INTEGER,
        ValueKind.FLOAT,
        ValueKind.BOOLEAN,
        ValueKind.STRING,
        ValueKind.NONE,
    }
)
_COLLECTION_KINDS: Final[frozenset[ValueKind]] = frozenset(
    {ValueKind.LIST, ValueKind.TUPLE, ValueKind.SET, ValueKind.MAPPING}
)


@dataclass(frozen=True, slots=True)
class TypedValue:
    """One protocol value.

    Scalars carry ``scalar`` as their canonical text form. Collections carry
    ``items``; a mapping additionally carries ``pairs``. Exactly one payload is
    populated, determined by ``kind``.
    """

    kind: ValueKind
    scalar: str | None = None
    items: tuple[TypedValue, ...] = ()
    pairs: tuple[tuple[TypedValue, TypedValue], ...] = ()

    def to_python(self) -> object:
        """Convert to a plain Python value.

        Used only inside the sandbox worker, where building ``int``, ``str``,
        ``list`` and friends is the entire point. Never call this on the host with
        untrusted input: the host never needs a Python value for a planned
        argument.
        """
        if self.kind is ValueKind.NONE:
            return None
        if self.kind in _COLLECTION_KINDS:
            if self.kind is ValueKind.MAPPING:
                pairs: dict[object, object] = {}
                for key, value in self.pairs:
                    pairs[key.to_python()] = value.to_python()
                return pairs
            built = tuple(item.to_python() for item in self.items)
            if self.kind is ValueKind.LIST:
                return list(built)
            if self.kind is ValueKind.TUPLE:
                return built
            return set(built)
        text = self.scalar or ""
        if self.kind is ValueKind.BOOLEAN:
            return text == "true"
        if self.kind is ValueKind.INTEGER:
            return int(text, 10)
        if self.kind is ValueKind.FLOAT:
            return _parse_float(text)
        return text


def _parse_float(text: str) -> float:
    """Parse a canonical float spelling, preserving negative zero and NaN."""
    if text == "nan":
        return math.nan
    if text == "inf":
        return math.inf
    if text == "-inf":
        return -math.inf
    value = float(text)
    if value == 0.0 and math.copysign(1.0, value) < 0:
        return -0.0
    return value


def encode_float(value: float) -> str:
    """Canonical text for a float, preserving special values and negative zero."""
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    if value == 0.0:
        return "-0.0" if math.copysign(1.0, value) < 0 else "0.0"
    return repr(value)


# --------------------------------------------------------------------------
# Rendering EdgeHunter's own literal text into a TypedValue
# --------------------------------------------------------------------------

# Tolerance for a value we cannot represent: the point is to refuse loudly, never
# to approximate. An integer too large for a 64-bit float-safe range is refused.
_MAX_INTEGER_DIGITS: Final[int] = 4096


def recognize_literal(text: str, *, depth: int = 0) -> TypedValue:
    """Recognize one rendered literal as a :class:`TypedValue`.

    Raises :class:`ProtocolError` when the text is not one of the closed literal
    forms EdgeHunter produces, or when a limit is exceeded. The text is parsed
    with :func:`ast.parse` and then walked with an explicit whitelist; no node
    that could reference a name, call a function, or perform an operation is
    ever evaluated.
    """
    if depth > MAX_NESTING_DEPTH:
        raise ProtocolError(f"literal nesting exceeds {MAX_NESTING_DEPTH}")
    if len(text) > MAX_RECOGNIZED_TEXT_LENGTH:
        raise ProtocolError("literal text is too long to recognize")
    try:
        parsed = ast.parse(text, mode="eval")
    except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
        raise ProtocolError(f"literal text is not parseable: {type(exc).__name__}") from exc

    body = parsed.body
    if not isinstance(body, ast.expr):
        raise ProtocolError("literal text did not produce an expression")
    return _recognize_node(body, depth)


def _recognize_node(node: ast.expr, depth: int) -> TypedValue:
    """Recognize one whitelisted AST node."""
    if depth > MAX_NESTING_DEPTH:
        raise ProtocolError(f"literal nesting exceeds {MAX_NESTING_DEPTH}")
    if isinstance(node, ast.Constant):
        return _recognize_constant(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _recognize_negation(node, depth)
    if isinstance(node, ast.List):
        return TypedValue(kind=ValueKind.LIST, items=_recognize_items(node.elts, depth + 1))
    if isinstance(node, ast.Tuple):
        return TypedValue(kind=ValueKind.TUPLE, items=_recognize_items(node.elts, depth + 1))
    if isinstance(node, ast.Set):
        return TypedValue(kind=ValueKind.SET, items=_recognize_items(node.elts, depth + 1))
    if isinstance(node, ast.Dict):
        return TypedValue(kind=ValueKind.MAPPING, pairs=_recognize_pairs(node, depth))
    if isinstance(node, ast.Call):
        return _recognize_call(node)
    raise ProtocolError(f"literal construct {type(node).__name__} is not permitted")


def _recognize_constant(value: object) -> TypedValue:
    """Recognize a literal constant, rejecting anything with no safe spelling."""
    if value is None:
        return TypedValue(kind=ValueKind.NONE)
    if isinstance(value, bool):
        # Checked before int because bool subclasses int.
        return TypedValue(kind=ValueKind.BOOLEAN, scalar="true" if value else "false")
    if isinstance(value, int):
        if abs(value) >= 10**_MAX_INTEGER_DIGITS:
            raise ProtocolError("integer literal is too large to encode")
        return TypedValue(kind=ValueKind.INTEGER, scalar=str(value))
    if isinstance(value, float):
        return TypedValue(kind=ValueKind.FLOAT, scalar=encode_float(value))
    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            raise ProtocolError("string literal exceeds the maximum length")
        return TypedValue(kind=ValueKind.STRING, scalar=value)
    raise ProtocolError(f"constant of type {type(value).__name__} is not permitted")


def _recognize_negation(node: ast.UnaryOp, depth: int) -> TypedValue:
    """Recognize a signed numeric literal such as ``-1`` or ``-0.0``."""
    operand = _recognize_node(node.operand, depth)
    if operand.kind not in (ValueKind.INTEGER, ValueKind.FLOAT):
        raise ProtocolError("only numeric literals may be negated")
    if isinstance(node.op, ast.UAdd):
        return operand
    if operand.kind is ValueKind.INTEGER:
        return TypedValue(kind=ValueKind.INTEGER, scalar=str(-int(operand.scalar or "0")))
    return TypedValue(kind=ValueKind.FLOAT, scalar=encode_float(-float(operand.scalar or "0")))


def _recognize_items(elements: Sequence[ast.expr], depth: int) -> tuple[TypedValue, ...]:
    """Recognize the elements of a list, tuple, or set literal."""
    if len(elements) > MAX_COLLECTION_ITEMS:
        raise ProtocolError(f"collection exceeds {MAX_COLLECTION_ITEMS} items")
    return tuple(_recognize_node(element, depth) for element in elements)


def _recognize_pairs(node: ast.Dict, depth: int) -> tuple[tuple[TypedValue, TypedValue], ...]:
    """Recognize the entries of a dict literal, refusing unpacking."""
    if node.keys is None:
        raise ProtocolError("dict unpacking is not permitted")
    if len(node.keys) > MAX_MAPPING_ENTRIES:
        raise ProtocolError(f"mapping exceeds {MAX_MAPPING_ENTRIES} entries")
    pairs: list[tuple[TypedValue, TypedValue]] = []
    for key, value in zip(node.keys, node.values, strict=True):
        if key is None:
            raise ProtocolError("dict unpacking is not permitted")
        pairs.append((_recognize_node(key, depth + 1), _recognize_node(value, depth + 1)))
    return tuple(pairs)


def _recognize_call(node: ast.Call) -> TypedValue:
    """Recognize only the two call spellings EdgeHunter itself emits."""
    target = node.func
    if not isinstance(target, ast.Name) or node.keywords:
        raise ProtocolError("only simple literal-helper calls are permitted")
    if target.id in _EMPTY_SET_NAMES:
        if node.args:
            raise ProtocolError("only the empty set spelling is permitted")
        return TypedValue(kind=ValueKind.SET)
    if target.id == "float":
        if len(node.args) != 1:
            raise ProtocolError("only the special-float spellings are permitted")
        argument = node.args[0]
        if not isinstance(argument, ast.Constant) or not isinstance(argument.value, str):
            raise ProtocolError("float helper takes only a literal string")
        if argument.value not in _FLOAT_CALL_SPELLINGS:
            raise ProtocolError(f"float({argument.value!r}) is not a permitted spelling")
        return TypedValue(kind=ValueKind.FLOAT, scalar=argument.value)
    raise ProtocolError(f"call to {target.id!r} is not permitted")


# --------------------------------------------------------------------------
# JSON encoding
# --------------------------------------------------------------------------


def encode_value(value: TypedValue) -> dict[str, object]:
    """Encode a typed value as a strict-JSON compatible mapping."""
    if value.kind in _SCALAR_KINDS:
        if value.kind is ValueKind.NONE:
            return {"kind": ValueKind.NONE.value}
        return {"kind": value.kind.value, "value": value.scalar or ""}
    if value.kind is ValueKind.MAPPING:
        return {
            "kind": value.kind.value,
            "entries": [[encode_value(key), encode_value(item)] for key, item in value.pairs],
        }
    encoded = [encode_value(item) for item in value.items]
    if value.kind is ValueKind.SET:
        # Sets have no defined iteration order, so sort by canonical text to keep
        # request encoding byte-for-byte deterministic.
        encoded.sort(key=_canonical_json)
    return {"kind": value.kind.value, "items": encoded}


def _canonical_json(value: object) -> str:
    """Deterministic text for one encoded node, used only for sorting."""
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))


def dumps(document: object) -> str:
    """Serialize a document, rejecting non-standard JSON tokens."""
    return json.dumps(document, allow_nan=False, separators=(",", ":"), sort_keys=True)


# --------------------------------------------------------------------------
# JSON decoding
# --------------------------------------------------------------------------


def decode_value(document: object, *, depth: int = 0) -> TypedValue:
    """Decode one protocol node, raising :class:`ProtocolError` on any deviation."""
    if depth > MAX_NESTING_DEPTH:
        raise ProtocolError(f"value nesting exceeds {MAX_NESTING_DEPTH}")
    if not isinstance(document, Mapping):
        raise ProtocolError("a value must be a JSON object")

    raw_kind = document.get("kind")
    if not isinstance(raw_kind, str):
        raise ProtocolError("a value requires a string 'kind'")
    try:
        kind = ValueKind(raw_kind)
    except ValueError as exc:
        raise ProtocolError(f"unknown value kind {raw_kind!r}") from exc

    if kind in _SCALAR_KINDS:
        _reject_extra_keys(document, {"kind"} if kind is ValueKind.NONE else {"kind", "value"})
        if kind is ValueKind.NONE:
            return TypedValue(kind=kind)
        text = document.get("value")
        if not isinstance(text, str):
            raise ProtocolError(f"{kind.value} requires a string 'value'")
        _validate_scalar(kind, text)
        return TypedValue(kind=kind, scalar=text)

    if kind is ValueKind.MAPPING:
        _reject_extra_keys(document, {"kind", "entries"})
        return TypedValue(kind=kind, pairs=_decode_pairs(document.get("entries"), depth))

    _reject_extra_keys(document, {"kind", "items"})
    return TypedValue(kind=kind, items=_decode_items(document.get("items"), kind, depth))


def _reject_extra_keys(document: Mapping[str, object], allowed: set[str]) -> None:
    """Refuse unknown keys so a document cannot smuggle extra structure."""
    extra = set(document) - allowed
    if extra:
        raise ProtocolError(f"unexpected key(s): {', '.join(sorted(extra))}")


def _validate_scalar(kind: ValueKind, text: str) -> None:
    """Validate one scalar's canonical text without building the value."""
    if kind is ValueKind.BOOLEAN:
        if text not in ("true", "false"):
            raise ProtocolError("boolean must be 'true' or 'false'")
        return
    if kind is ValueKind.STRING:
        if len(text) > MAX_STRING_LENGTH:
            raise ProtocolError("string exceeds the maximum length")
        return
    if kind is ValueKind.INTEGER:
        if not text or not text.lstrip("-").isdigit():
            raise ProtocolError("integer must be a decimal digit string")
        return
    if text in ("nan", "inf", "-inf"):
        return
    try:
        value = float(text)
    except ValueError as exc:
        raise ProtocolError("float is not a recognized spelling") from exc
    if math.isinf(value) or math.isnan(value):
        # Non-special spellings must not smuggle an infinity or NaN.
        if text not in ("inf", "-inf", "nan"):
            raise ProtocolError("float spelling is not canonical")


def _decode_items(raw: object, kind: ValueKind, depth: int) -> tuple[TypedValue, ...]:
    """Decode an ``items`` array for a list, tuple, or set."""
    if not isinstance(raw, list):
        raise ProtocolError("a collection requires an 'items' array")
    if len(raw) > MAX_COLLECTION_ITEMS:
        raise ProtocolError(f"collection exceeds {MAX_COLLECTION_ITEMS} items")
    items = tuple(decode_value(item, depth=depth + 1) for item in raw)
    if kind is ValueKind.SET:
        _require_hashable(items, "set element")
    return items


def _decode_pairs(raw: object, depth: int) -> tuple[tuple[TypedValue, TypedValue], ...]:
    """Decode an ``entries`` array for a mapping."""
    if not isinstance(raw, list):
        raise ProtocolError("a mapping requires an 'entries' array")
    if len(raw) > MAX_MAPPING_ENTRIES:
        raise ProtocolError(f"mapping exceeds {MAX_MAPPING_ENTRIES} entries")
    pairs: list[tuple[TypedValue, TypedValue]] = []
    for entry in raw:
        if not isinstance(entry, list) or len(entry) != 2:
            raise ProtocolError("a mapping entry must be a [key, value] pair")
        key = decode_value(entry[0], depth=depth + 1)
        value = decode_value(entry[1], depth=depth + 1)
        _require_hashable((key,), "mapping key")
        pairs.append((key, value))
    return tuple(pairs)


def _require_hashable(items: tuple[TypedValue, ...], role: str) -> None:
    """Refuse a set element or mapping key that could not be hashed.

    Refusing explicitly is required: silently stringifying a key would change the
    value the target receives.
    """
    for item in items:
        if item.kind in (ValueKind.LIST, ValueKind.TUPLE, ValueKind.SET, ValueKind.MAPPING):
            raise ProtocolError(f"{role} of kind {item.kind.value} is not hashable")


def check_document_size(text: str) -> None:
    """Refuse a document larger than the protocol's byte budget."""
    if len(text.encode("utf-8", errors="replace")) > MAX_DOCUMENT_BYTES:
        raise ProtocolError(f"document exceeds {MAX_DOCUMENT_BYTES} bytes")


def check_protocol_version(document: object) -> int:
    """Validate the protocol version and return it."""
    if not isinstance(document, Mapping):
        raise ProtocolError("a document must be a JSON object")
    version = document.get("protocol_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ProtocolError("a document requires an integer 'protocol_version'")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported protocol version {version}")
    return version


def decode_document(text: str) -> dict[str, Any]:
    """Parse a bounded, strict-JSON document and validate its version."""
    check_document_size(text)
    try:
        document = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ProtocolError("document is not valid JSON") from exc
    if not isinstance(document, dict):
        raise ProtocolError("a document must be a JSON object")
    check_protocol_version(document)
    return document
