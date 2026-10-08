"""Tests for safe source reading and AST-based module analysis."""

from __future__ import annotations

from pathlib import Path

from edgehunter.scanner import (
    ModuleAnalysis,
    ParameterKind,
    TargetKind,
    analyze_python_file,
)


def analyze(tmp_path: Path, source: str, name: str = "module.py") -> ModuleAnalysis:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return analyze_python_file(path, Path(name))


def test_function_extraction(tmp_path: Path) -> None:
    module = analyze(tmp_path, "def add(a: int, b: int) -> int:\n    return a + b\n")

    assert module.is_parsed
    assert len(module.functions) == 1
    function = module.functions[0]
    assert function.name == "add"
    assert function.qualified_name == "add"
    assert function.kind is TargetKind.FUNCTION
    assert function.lineno == 1
    assert function.end_lineno == 2
    assert function.is_async is False
    assert function.owner is None
    assert [parameter.name for parameter in function.parameters] == ["a", "b"]
    assert [parameter.annotation for parameter in function.parameters] == ["int", "int"]
    assert [parameter.has_default for parameter in function.parameters] == [False, False]
    assert function.return_annotation == "int"
    assert function.decorators == ()
    assert module.classes == ()


def test_async_function_extraction(tmp_path: Path) -> None:
    module = analyze(tmp_path, "async def fetch(url: str) -> bytes:\n    ...\n")

    function = module.functions[0]
    assert function.kind is TargetKind.ASYNC_FUNCTION
    assert function.is_async is True
    assert function.signature == "async fetch(url: str) -> bytes"


def test_method_extraction(tmp_path: Path) -> None:
    source = "class Account:\n    def withdraw(self, amount: float) -> float:\n        return 0.0\n"

    module = analyze(tmp_path, source)

    function = module.functions[0]
    assert function.qualified_name == "Account.withdraw"
    assert function.kind is TargetKind.METHOD
    assert function.owner == "Account"
    assert function.lineno == 2
    assert function.signature == "Account.withdraw(self, amount: float) -> float"


def test_async_method_extraction(tmp_path: Path) -> None:
    source = "class Account:\n    async def refresh(self) -> None:\n        ...\n"

    module = analyze(tmp_path, source)

    function = module.functions[0]
    assert function.qualified_name == "Account.refresh"
    assert function.kind is TargetKind.ASYNC_METHOD
    assert function.is_async is True
    assert function.owner == "Account"


def test_nested_function_extraction(tmp_path: Path) -> None:
    source = "def outer():\n    def inner():\n        return 1\n\n    return inner\n"

    module = analyze(tmp_path, source)

    assert [function.qualified_name for function in module.functions] == ["outer", "outer.inner"]
    outer, inner = module.functions
    assert outer.kind is TargetKind.FUNCTION
    assert inner.kind is TargetKind.NESTED_FUNCTION
    assert inner.owner is None


def test_nested_class_qualification(tmp_path: Path) -> None:
    source = "class Outer:\n    class Inner:\n        def run(self):\n            ...\n"

    module = analyze(tmp_path, source)

    assert [klass.qualified_name for klass in module.classes] == ["Outer", "Outer.Inner"]
    function = module.functions[0]
    assert function.qualified_name == "Outer.Inner.run"
    assert function.kind is TargetKind.METHOD
    assert function.owner == "Outer.Inner"


def test_parameter_kinds(tmp_path: Path) -> None:
    source = (
        "def process(\n"
        "    x: int,\n"
        "    /,\n"
        "    y: str = 'x',\n"
        "    *items: float,\n"
        "    enabled: bool = True,\n"
        "    **options: object,\n"
        ") -> list[str]:\n"
        "    return []\n"
    )

    module = analyze(tmp_path, source)

    function = module.functions[0]
    assert [parameter.kind for parameter in function.parameters] == [
        ParameterKind.POSITIONAL_ONLY,
        ParameterKind.POSITIONAL_OR_KEYWORD,
        ParameterKind.VAR_POSITIONAL,
        ParameterKind.KEYWORD_ONLY,
        ParameterKind.VAR_KEYWORD,
    ]
    assert [parameter.name for parameter in function.parameters] == [
        "x",
        "y",
        "items",
        "enabled",
        "options",
    ]
    assert [parameter.annotation for parameter in function.parameters] == [
        "int",
        "str",
        "float",
        "bool",
        "object",
    ]
    assert [parameter.has_default for parameter in function.parameters] == [
        False,
        True,
        False,
        True,
        False,
    ]
    assert function.signature == (
        "process(x: int, /, y: str = ..., *items: float, "
        "enabled: bool = ..., **options: object) -> list[str]"
    )


def test_defaults_are_detected_without_evaluation(tmp_path: Path) -> None:
    source = (
        "def build(value: int = 1 / 0) -> int:\n"
        "    return value\n"
        "\n"
        "def keyword(*, flag=missing_name()):\n"
        "    ...\n"
    )

    module = analyze(tmp_path, source)

    build, keyword = module.functions
    assert build.parameters[0].has_default is True
    assert keyword.parameters[0].has_default is True
    assert module.is_parsed


def test_annotation_syntax_is_preserved(tmp_path: Path) -> None:
    source = "def parse(value: list[str] | None) -> dict[str, int]:\n    return {}\n"

    module = analyze(tmp_path, source)

    function = module.functions[0]
    assert function.parameters[0].annotation == "list[str] | None"
    assert function.return_annotation == "dict[str, int]"


def test_decorators_are_extracted_statically(tmp_path: Path) -> None:
    source = '@staticmethod\n@cache\n@router.get("/users")\ndef get_users():\n    ...\n'

    module = analyze(tmp_path, source)

    assert module.functions[0].decorators == (
        "staticmethod",
        "cache",
        "router.get('/users')",
    )


def test_class_metadata(tmp_path: Path) -> None:
    source = "@dataclass\nclass SavingsAccount(Account, Auditable):\n    ...\n"

    module = analyze(tmp_path, source)

    klass = module.classes[0]
    assert klass.name == "SavingsAccount"
    assert klass.qualified_name == "SavingsAccount"
    assert klass.lineno == 2
    assert klass.end_lineno == 3
    assert klass.bases == ("Account", "Auditable")
    assert klass.decorators == ("dataclass",)


def test_syntax_error_is_captured(tmp_path: Path) -> None:
    module = analyze(tmp_path, "def broken(\n")

    assert module.is_parsed is False
    assert module.has_issue is True
    assert module.syntax_issue is not None
    assert module.read_issue is None
    assert module.syntax_issue.line == 1
    assert module.syntax_issue.column is not None
    assert "never closed" in module.syntax_issue.message
    assert module.functions == ()
    assert module.classes == ()


def test_empty_file_parses_successfully(tmp_path: Path) -> None:
    module = analyze(tmp_path, "")

    assert module.is_parsed is True
    assert module.functions == ()
    assert module.classes == ()
    assert module.syntax_issue is None
    assert module.read_issue is None


def test_comment_only_file_parses_successfully(tmp_path: Path) -> None:
    module = analyze(tmp_path, "# only a comment\n\n# and another\n")

    assert module.is_parsed is True
    assert module.functions == ()
    assert module.classes == ()


def test_undecodable_file_is_a_read_issue(tmp_path: Path) -> None:
    path = tmp_path / "latin.py"
    path.write_bytes(b"# -*- coding: ascii -*-\nname = 'caf\xe9'\n")

    module = analyze_python_file(path, Path("latin.py"))

    assert module.read_issue is not None
    assert module.syntax_issue is None
    assert module.functions == ()
    assert "codec" in module.read_issue.message


def test_unknown_encoding_cookie_is_a_read_issue(tmp_path: Path) -> None:
    path = tmp_path / "weird.py"
    path.write_bytes(b"# -*- coding: not-a-real-codec -*-\nvalue = 1\n")

    module = analyze_python_file(path, Path("weird.py"))

    assert module.read_issue is not None
    assert module.syntax_issue is None
    assert "unknown encoding" in module.read_issue.message


def test_definitions_inside_compound_statements_are_found(tmp_path: Path) -> None:
    source = (
        "if True:\n"
        "    def conditional():\n"
        "        ...\n"
        "\n"
        "try:\n"
        "    async def loader():\n"
        "        ...\n"
        "except ImportError:\n"
        "    def loader():\n"
        "        ...\n"
    )

    module = analyze(tmp_path, source)

    assert [function.qualified_name for function in module.functions] == [
        "conditional",
        "loader",
        "loader",
    ]
    assert [function.kind for function in module.functions] == [
        TargetKind.FUNCTION,
        TargetKind.ASYNC_FUNCTION,
        TargetKind.FUNCTION,
    ]


def test_class_inside_function_keeps_qualified_names(tmp_path: Path) -> None:
    source = (
        "def outer():\n"
        "    class Helper:\n"
        "        def run(self):\n"
        "            ...\n"
        "    return Helper\n"
    )

    module = analyze(tmp_path, source)

    assert [klass.qualified_name for klass in module.classes] == ["outer.Helper"]
    assert [function.qualified_name for function in module.functions] == [
        "outer",
        "outer.Helper.run",
    ]
    assert module.functions[1].kind is TargetKind.METHOD
    assert module.functions[1].owner == "outer.Helper"


def test_lambdas_are_not_targets(tmp_path: Path) -> None:
    module = analyze(tmp_path, "double = lambda x: x * 2\n")

    assert module.is_parsed is True
    assert module.functions == ()
