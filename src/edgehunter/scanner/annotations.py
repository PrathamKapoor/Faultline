"""Conservative static interpretation of annotation *syntax*.

This module is the single canonical place where an annotation is turned into a
shape. Every later stage — boundary mining, action selection, baseline
synthesis, invocation planning, strategy planning — consumes the resulting
:class:`StaticAnnotation` instead of inspecting raw annotation text, so there is
exactly one annotation interpreter in the project.

Two properties are deliberate and load-bearing:

* **Nothing is resolved.** Recognition is purely syntactic. ``List[int]`` is
  recognized because of the *shape* of that syntax, not because EdgeHunter has
  proved the name ``List`` refers to ``typing.List``. A user alias such as
  ``MyInt = int`` stays :attr:`AnnotationKind.UNKNOWN`, and so does every
  imported or project-specific type.
* **Nothing is evaluated.** Annotation expressions are inspected as AST nodes
  that already exist from the single repository parse. A forward reference such
  as ``"User"`` is *not* parsed as code, and an annotation that is a call
  expression is classified ``UNKNOWN`` and never invoked.

Unrecognized shapes are recorded as ``UNKNOWN`` rather than approximated, so
"EdgeHunter does not know" stays distinguishable from "EdgeHunter decided".
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, replace
from enum import Enum
from typing import Final

from edgehunter.scanner.rendering import render_source


class AnnotationKind(str, Enum):
    """The annotation shapes this project recognizes statically."""

    UNKNOWN = "unknown"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    STR = "str"
    NONE = "none"
    LIST = "list"
    TUPLE = "tuple"
    SET = "set"
    DICT = "dict"
    UNION = "union"


@dataclass(frozen=True, slots=True)
class StaticAnnotation:
    """One interpreted annotation shape.

    ``kind`` is the underlying shape and ``nullable`` records that ``None`` is
    additionally permitted, so ``int | None`` is ``kind=INT, nullable=True``
    rather than a separate kind. That keeps every ``INT`` rule — numeric
    boundary candidates, the ``0`` baseline — applicable to a nullable integer,
    while ``description`` still renders it as ``OPTIONAL[INT]``.

    ``element``/``key``/``value`` describe container contents and are ``None``
    when the shape is unknown or the container is bare. ``variadic`` is set only
    for a homogeneous variadic tuple such as ``tuple[str, ...]``.
    ``rendered`` keeps the original annotation text for display.
    """

    kind: AnnotationKind
    rendered: str | None = None
    nullable: bool = False
    element: StaticAnnotation | None = None
    key: StaticAnnotation | None = None
    value: StaticAnnotation | None = None
    variadic: bool = False

    @property
    def is_known(self) -> bool:
        """True when the shape was recognized, not merely not-empty."""
        return self.kind is not AnnotationKind.UNKNOWN

    @property
    def is_nullable(self) -> bool:
        """True when ``None`` is an additionally permitted value."""
        return self.nullable or self.kind is AnnotationKind.NONE

    def non_nullable(self) -> StaticAnnotation:
        """Return the same shape with ``nullable`` cleared."""
        if not self.nullable:
            return self
        return replace(self, nullable=False)

    @property
    def description(self) -> str:
        """Compact symbolic rendering, for example ``OPTIONAL[LIST[INT]]``."""
        if self.kind is AnnotationKind.UNION:
            return self._wrap("UNION")
        if self.kind is AnnotationKind.DICT:
            inner = f"{_describe(self.key)}, {_describe(self.value)}"
            return self._wrap(f"DICT[{inner}]")
        if self.kind is AnnotationKind.TUPLE:
            if self.element is None:
                # Bare ``tuple``, or a heterogeneous fixed-length tuple whose
                # element shape is not a single type.
                return self._wrap("TUPLE[?]")
            suffix = ", ..." if self.variadic else ""
            return self._wrap(f"TUPLE[{_describe(self.element)}{suffix}]")
        if self.kind in (AnnotationKind.LIST, AnnotationKind.SET):
            return self._wrap(f"{self.kind.value.upper()}[{_describe(self.element)}]")
        return self._wrap(self.kind.value.upper())

    def _wrap(self, text: str) -> str:
        if self.nullable and self.kind is not AnnotationKind.NONE:
            return f"OPTIONAL[{text}]"
        return text


UNKNOWN_ANNOTATION: Final[StaticAnnotation] = StaticAnnotation(kind=AnnotationKind.UNKNOWN)

_SIMPLE_NAMES: Final[dict[str, AnnotationKind]] = {
    "int": AnnotationKind.INT,
    "float": AnnotationKind.FLOAT,
    "bool": AnnotationKind.BOOL,
    "str": AnnotationKind.STR,
}

_COLLECTION_NAMES: Final[dict[str, AnnotationKind]] = {
    "list": AnnotationKind.LIST,
    "List": AnnotationKind.LIST,
    "tuple": AnnotationKind.TUPLE,
    "Tuple": AnnotationKind.TUPLE,
    "set": AnnotationKind.SET,
    "Set": AnnotationKind.SET,
    "dict": AnnotationKind.DICT,
    "Dict": AnnotationKind.DICT,
}

_OPTIONAL_NAMES: Final[frozenset[str]] = frozenset({"Optional"})


def build_static_annotation(node: ast.expr | None) -> StaticAnnotation:
    """Interpret one annotation AST node as a conservative static shape.

    ``node`` is taken from the repository's single existing parse, so building a
    shape costs no extra read or parse. A missing annotation is ``UNKNOWN``;
    an unrecognized one is also ``UNKNOWN``, and always keeps its rendered
    text so the original syntax stays visible in reports.
    """
    if node is None:
        return UNKNOWN_ANNOTATION
    rendered = render_source(node)
    shape = _from_node(node, rendered)
    if shape.rendered is None:
        return replace(shape, rendered=rendered)
    return shape


def _from_node(node: ast.expr, rendered: str | None) -> StaticAnnotation:
    """Classify one annotation node, dispatching on its AST shape."""
    if _is_none_literal(node):
        return StaticAnnotation(kind=AnnotationKind.NONE, rendered=rendered)
    if isinstance(node, ast.Constant):
        # A string literal is a forward reference such as ``"User"``. It is
        # deliberately not parsed: resolving it would require importing the
        # module that defines the name.
        return StaticAnnotation(kind=AnnotationKind.UNKNOWN, rendered=rendered)
    if isinstance(node, ast.BinOp):
        return _from_binop(node, rendered)
    if isinstance(node, ast.Subscript):
        return _from_subscript(node, rendered)

    name = _recognizable_name(node)
    if name is None:
        return StaticAnnotation(kind=AnnotationKind.UNKNOWN, rendered=rendered)
    return _from_name(name, rendered)


def _from_name(name: str, rendered: str | None) -> StaticAnnotation:
    """Classify a bare (or ``typing.``-prefixed) annotation name."""
    simple = _SIMPLE_NAMES.get(name)
    if simple is not None:
        return StaticAnnotation(kind=simple, rendered=rendered)
    collection = _COLLECTION_NAMES.get(name)
    if collection is not None:
        return StaticAnnotation(kind=collection, rendered=rendered)
    if name == "None":
        return StaticAnnotation(kind=AnnotationKind.NONE, rendered=rendered)
    if name in _OPTIONAL_NAMES:
        return StaticAnnotation(kind=AnnotationKind.UNKNOWN, rendered=rendered)
    if name == "Union":
        return StaticAnnotation(kind=AnnotationKind.UNION, rendered=rendered)
    return StaticAnnotation(kind=AnnotationKind.UNKNOWN, rendered=rendered)


def _from_subscript(node: ast.Subscript, rendered: str | None) -> StaticAnnotation:
    """Classify a subscripted annotation such as ``list[int]``."""
    name = _recognizable_name(node.value)
    if name is None:
        return StaticAnnotation(kind=AnnotationKind.UNKNOWN, rendered=rendered)

    if name in _OPTIONAL_NAMES:
        inner = _single_argument(node)
        if inner is None:
            return StaticAnnotation(kind=AnnotationKind.UNKNOWN, rendered=rendered)
        return replace(_from_node(inner, None), nullable=True, rendered=rendered)

    if name == "Union":
        return _from_union_arguments(node, rendered)

    kind = _COLLECTION_NAMES.get(name)
    if kind is None:
        return StaticAnnotation(kind=AnnotationKind.UNKNOWN, rendered=rendered)
    if kind is AnnotationKind.DICT:
        return _from_dict_subscript(node, rendered)
    return _from_sequence_subscript(kind, node, rendered)


def _from_sequence_subscript(
    kind: AnnotationKind, node: ast.Subscript, rendered: str | None
) -> StaticAnnotation:
    """Interpret ``list[T]``, ``set[T]``, and both tuple spellings."""
    arguments = _subscript_arguments(node)
    if not arguments:
        return StaticAnnotation(kind=kind, rendered=rendered)

    if kind is AnnotationKind.TUPLE:
        if len(arguments) == 2 and _is_ellipsis(arguments[1]):
            # ``tuple[str, ...]``: homogeneous and open-ended.
            return StaticAnnotation(
                kind=kind,
                rendered=rendered,
                element=_from_node(arguments[0], None),
                variadic=True,
            )
        if len(arguments) == 1:
            return StaticAnnotation(
                kind=kind, rendered=rendered, element=_from_node(arguments[0], None)
            )
        # A heterogeneous fixed-length tuple such as ``tuple[int, str]`` has no
        # single element shape, so it is recorded without one.
        return StaticAnnotation(kind=kind, rendered=rendered)

    return StaticAnnotation(kind=kind, rendered=rendered, element=_from_node(arguments[0], None))


def _from_dict_subscript(node: ast.Subscript, rendered: str | None) -> StaticAnnotation:
    """Interpret ``dict[K, V]``."""
    arguments = _subscript_arguments(node)
    if len(arguments) != 2:
        return StaticAnnotation(kind=AnnotationKind.DICT, rendered=rendered)
    return StaticAnnotation(
        kind=AnnotationKind.DICT,
        rendered=rendered,
        key=_from_node(arguments[0], None),
        value=_from_node(arguments[1], None),
    )


def _from_binop(node: ast.BinOp, rendered: str | None) -> StaticAnnotation:
    """Interpret a PEP 604 union such as ``int | None``.

    Exactly one recognized type plus ``None`` is the supported form. Anything
    else is recorded as ``UNION``, which yields no generated values.
    """
    if not isinstance(node.op, ast.BitOr):
        return StaticAnnotation(kind=AnnotationKind.UNKNOWN, rendered=rendered)
    left = _from_node(node.left, None)
    right = _from_node(node.right, None)

    if right.kind is AnnotationKind.NONE:
        return replace(left, nullable=True, rendered=rendered)
    if left.kind is AnnotationKind.NONE:
        return replace(right, nullable=True, rendered=rendered)

    if left.kind is right.kind and not left.nullable and not right.nullable:
        return replace(left, rendered=rendered)
    return StaticAnnotation(kind=AnnotationKind.UNION, rendered=rendered)


def _from_union_arguments(node: ast.Subscript, rendered: str | None) -> StaticAnnotation:
    """Interpret ``Union[...]`` with the same rules as PEP 604 unions."""
    arguments = _subscript_arguments(node)
    members = [_from_node(argument, None) for argument in arguments]
    nullable = any(member.kind is AnnotationKind.NONE for member in members)
    concrete = [member for member in members if member.kind is not AnnotationKind.NONE]

    if len(concrete) == 1:
        return replace(concrete[0], nullable=nullable, rendered=rendered)
    return StaticAnnotation(kind=AnnotationKind.UNION, rendered=rendered, nullable=nullable)


def _recognizable_name(node: ast.expr) -> str | None:
    """Return the annotation name when it is a bare name or ``typing.``-prefixed.

    Any other qualifier is rejected: ``mylib.List[int]`` is not evidence that
    the shape is a list, because the alias could bind anything.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if node.value.id == "typing":
            return node.attr
        return None
    return None


def _subscript_arguments(node: ast.Subscript) -> list[ast.expr]:
    """Return the subscript's index expressions as a list."""
    index = node.slice
    if isinstance(index, ast.Tuple):
        return list(index.elts)
    return [index]


def _single_argument(node: ast.Subscript) -> ast.expr | None:
    arguments = _subscript_arguments(node)
    if len(arguments) != 1:
        return None
    return arguments[0]


def _is_none_literal(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _is_ellipsis(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is Ellipsis


def _describe(annotation: StaticAnnotation | None) -> str:
    if annotation is None:
        return "?"
    return annotation.description
