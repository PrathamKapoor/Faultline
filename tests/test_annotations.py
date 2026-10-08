"""Tests for the conservative static annotation-shape interpreter."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from edgehunter.analysis import analyze_repository_semantics
from edgehunter.scanner import (
    UNKNOWN_ANNOTATION,
    AnnotationKind,
    ModuleAnalysis,
    StaticAnnotation,
    build_module_analysis,
    build_static_annotation,
    parse_source_file,
)

SIMPLE = "int"
FLOAT = "float"
OPTIONAL_INT = "Optional[int]"
PEP604_INT = "int | None"
PEP604_STR = "str | None"
PEP604_LIST = "list[int] | None"
CUSTOM = "User"
FORWARD = '"User"'
DANGEROUS = "dangerous()"
ALIAS = "MyInt"
LIST_INT = "list[int]"
TUPLE_VARIADIC = "tuple[str, ...]"
TUPLE_FIXED = "tuple[int]"
SET_BOOL = "set[bool]"
DICT_STR_INT = "dict[str, int]"
BARE_LIST = "list"
BARE_DICT = "dict"
NESTED_LIST = "list[list[int]]"
NESTED_DICT = "dict[str, list[int]]"
NULLABLE_ELEMENT = "list[int | None]"
TYPING_LIST = "typing.List[int]"
TYPING_OPTIONAL = "typing.Optional[int]"
UNION_TWO = "int | str"
UNION_THREE = "int | str | None"
UNION_ONE_NONE = "Union[int, None]"
ANNOTATED = "Annotated[int, 'meta']"
LITERAL = "Literal['a', 'b']"
SEQUENCE = "Sequence[int]"
UNSUBSCRIPTED = "tuple[int, str]"
NONE_ANNOTATION = "None"


def shape_of(annotation: str) -> StaticAnnotation:
    """Interpret one annotation expression written as source text."""
    parsed = ast.parse(f"def f(x: {annotation}): ...", mode="exec")
    function = parsed.body[0]
    assert isinstance(function, ast.FunctionDef)
    arguments = function.args.args
    return build_static_annotation(arguments[0].annotation)


@pytest.mark.parametrize(
    ("annotation", "kind"),
    [
        (SIMPLE, AnnotationKind.INT),
        (FLOAT, AnnotationKind.FLOAT),
        ("bool", AnnotationKind.BOOL),
        ("str", AnnotationKind.STR),
        (NONE_ANNOTATION, AnnotationKind.NONE),
        (LIST_INT, AnnotationKind.LIST),
        (TUPLE_VARIADIC, AnnotationKind.TUPLE),
        (TUPLE_FIXED, AnnotationKind.TUPLE),
        (SET_BOOL, AnnotationKind.SET),
        (DICT_STR_INT, AnnotationKind.DICT),
        (BARE_LIST, AnnotationKind.LIST),
        (BARE_DICT, AnnotationKind.DICT),
        (TYPING_LIST, AnnotationKind.LIST),
        (UNION_TWO, AnnotationKind.UNION),
    ],
)
def test_recognized_annotations(annotation: str, kind: AnnotationKind) -> None:
    assert shape_of(annotation).kind is kind


@pytest.mark.parametrize(
    ("annotation", "kind", "nullable"),
    [
        (OPTIONAL_INT, AnnotationKind.INT, True),
        (PEP604_INT, AnnotationKind.INT, True),
        (PEP604_STR, AnnotationKind.STR, True),
        (PEP604_LIST, AnnotationKind.LIST, True),
        (TYPING_OPTIONAL, AnnotationKind.INT, True),
        (UNION_ONE_NONE, AnnotationKind.INT, True),
        (UNION_THREE, AnnotationKind.UNION, True),
    ],
)
def test_nullable_annotations(annotation: str, kind: AnnotationKind, nullable: bool) -> None:
    result = shape_of(annotation)

    assert result.kind is kind
    assert result.nullable is nullable
    assert result.is_nullable is nullable


@pytest.mark.parametrize(
    "annotation",
    [
        CUSTOM,
        FORWARD,
        DANGEROUS,
        ALIAS,
        ANNOTATED,
        LITERAL,
        SEQUENCE,
        "NewType",
        "TypeVar",
        "Protocol",
        "Callable[[int], int]",
        "frozenset",
        "mylib.List[int]",
    ],
)
def test_unrecognized_annotations_stay_unknown(annotation: str) -> None:
    result = shape_of(annotation)

    assert result.kind is AnnotationKind.UNKNOWN
    assert result.is_known is False


def test_missing_annotation_is_unknown() -> None:
    assert build_static_annotation(None) == UNKNOWN_ANNOTATION


def test_forward_reference_text_is_preserved_but_not_parsed() -> None:
    result = shape_of(FORWARD)

    assert result.kind is AnnotationKind.UNKNOWN
    assert result.rendered == "'User'"


def test_container_element_and_key_value_shapes() -> None:
    listing = shape_of(LIST_INT)
    assert listing.element is not None
    assert listing.element.kind is AnnotationKind.INT

    mapping = shape_of(DICT_STR_INT)
    assert mapping.key is not None
    assert mapping.value is not None
    assert mapping.key.kind is AnnotationKind.STR
    assert mapping.value.kind is AnnotationKind.INT


def test_variadic_tuple_is_flagged() -> None:
    assert shape_of(TUPLE_VARIADIC).variadic is True
    assert shape_of(TUPLE_FIXED).variadic is False


def test_heterogeneous_tuple_has_no_single_element() -> None:
    result = shape_of(UNSUBSCRIPTED)

    assert result.kind is AnnotationKind.TUPLE
    assert result.element is None


def test_bare_containers_have_no_element() -> None:
    assert shape_of(BARE_LIST).element is None
    assert shape_of(BARE_DICT).key is None
    assert shape_of(BARE_DICT).value is None


def test_nested_generics_do_not_crash() -> None:
    nested_list = shape_of(NESTED_LIST)
    nested_dict = shape_of(NESTED_DICT)

    assert nested_list.element is not None
    assert nested_list.element.kind is AnnotationKind.LIST
    assert nested_dict.value is not None
    assert nested_dict.value.kind is AnnotationKind.LIST


def test_nullable_element_is_recorded() -> None:
    element = shape_of(NULLABLE_ELEMENT).element

    assert element is not None
    assert element.kind is AnnotationKind.INT
    assert element.nullable is True


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (SIMPLE, "INT"),
        (FLOAT, "FLOAT"),
        ("bool", "BOOL"),
        (LIST_INT, "LIST[INT]"),
        (TUPLE_VARIADIC, "TUPLE[STR, ...]"),
        (SET_BOOL, "SET[BOOL]"),
        (DICT_STR_INT, "DICT[STR, INT]"),
        (BARE_LIST, "LIST[?]"),
        (BARE_DICT, "DICT[?, ?]"),
        (UNSUBSCRIPTED, "TUPLE[?]"),
        (PEP604_INT, "OPTIONAL[INT]"),
        (PEP604_LIST, "OPTIONAL[LIST[INT]]"),
        (CUSTOM, "UNKNOWN"),
        (UNION_TWO, "UNION"),
        (NONE_ANNOTATION, "NONE"),
    ],
)
def test_descriptions(annotation: str, expected: str) -> None:
    assert shape_of(annotation).description == expected


def test_non_nullable_returns_the_same_shape() -> None:
    nullable = shape_of(PEP604_INT)
    plain = nullable.non_nullable()

    assert plain.kind is AnnotationKind.INT
    assert plain.nullable is False
    assert plain.rendered == nullable.rendered


def test_shape_is_built_during_the_single_parse(tmp_path: Path) -> None:
    """The shape comes from the traversal, so no second parse is needed."""
    target = tmp_path / "service.py"
    target.write_text("def f(x: list[int]):\n    return x\n", encoding="utf-8")

    source = parse_source_file(target, Path("service.py"))
    module = build_module_analysis(source)
    parameter = module.functions[0].parameters[0]

    assert parameter.annotation == "list[int]"
    assert parameter.shape.kind is AnnotationKind.LIST
    assert parameter.shape.element is not None
    assert parameter.shape.element.kind is AnnotationKind.INT


def test_shapes_reach_the_semantic_layer(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text("def f(x: int | None, y: list[int]):\n    return x, y\n", encoding="utf-8")

    semantics = analyze_repository_semantics(target)
    module: ModuleAnalysis = semantics.structure.modules[0]
    first, second = module.functions[0].parameters

    assert first.shape.kind is AnnotationKind.INT
    assert first.shape.nullable is True
    assert second.shape.kind is AnnotationKind.LIST


def test_shapes_are_immutable() -> None:
    result = shape_of(LIST_INT)

    with pytest.raises(AttributeError):
        result.kind = AnnotationKind.INT  # type: ignore[misc]
