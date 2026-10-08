"""Tests for deterministic semantic-fact extraction."""

from __future__ import annotations

from pathlib import Path

from edgehunter.analysis import (
    ArithmeticContext,
    ArithmeticOperator,
    ComparisonOperator,
    ConstantKind,
    ExpressionKind,
    ModuleSemantics,
    PredicateKind,
    TargetSemantics,
    extract_module_semantics,
)
from edgehunter.scanner import ModuleAnalysis, build_module_analysis, parse_source_file


def analyze(tmp_path: Path, source: str, name: str = "module.py") -> ModuleSemantics:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    parsed = parse_source_file(path, Path(name))
    module: ModuleAnalysis = build_module_analysis(parsed)
    return extract_module_semantics(parsed, module)


def target_of(semantics: ModuleSemantics, qualified_name: str) -> TargetSemantics:
    for target in semantics.targets:
        if target.qualified_name == qualified_name:
            return target
    raise AssertionError(f"target not found: {qualified_name}")


def test_simple_numeric_comparison(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(x: int):\n    if x >= 10:\n        return x\n")

    target = target_of(semantics, "f")
    assert len(target.predicates) == 1
    predicate = target.predicates[0]
    assert predicate.kind is PredicateKind.IF
    assert predicate.expression.text == "x >= 10"
    assert predicate.expression.kind is ExpressionKind.BINARY
    assert len(target.comparisons) == 1

    comparison = target.comparisons[0]
    assert comparison.operator is ComparisonOperator.GREATER_THAN_OR_EQUAL
    assert comparison.left.text == "x"
    assert comparison.left.names == ("x",)
    assert comparison.left.kind is ExpressionKind.NAME
    assert comparison.right.text == "10"
    assert comparison.right.constant_kind is ConstantKind.INTEGER
    assert comparison.lineno == 2


def test_reversed_comparison_is_recorded_verbatim(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(x: int):\n    if 0 <= x:\n        return x\n")

    comparison = target_of(semantics, "f").comparisons[0]
    assert comparison.left.text == "0"
    assert comparison.operator is ComparisonOperator.LESS_THAN_OR_EQUAL
    assert comparison.right.text == "x"


def test_chained_comparison_is_decomposed(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(x: int):\n    if 0 < x <= 10:\n        return x\n")

    target = target_of(semantics, "f")
    assert len(target.predicates) == 1
    rendered = [
        (comparison.left.text, comparison.operator.value, comparison.right.text)
        for comparison in target.comparisons
    ]
    assert rendered == [("0", "<", "x"), ("x", "<=", "10")]


def test_boolean_predicate_keeps_container_and_both_comparisons(tmp_path: Path) -> None:
    source = "def f(x: int, y: int):\n    if x > 0 and y < 10:\n        return x + y\n"

    semantics = analyze(tmp_path, source)

    target = target_of(semantics, "f")
    assert len(target.predicates) == 1
    assert target.predicates[0].expression.text == "x > 0 and y < 10"
    assert [comparison.operator for comparison in target.comparisons] == [
        ComparisonOperator.GREATER_THAN,
        ComparisonOperator.LESS_THAN,
    ]
    assert len(target.arithmetic) == 1
    assert target.arithmetic[0].expression.text == "x + y"


def test_method_owns_its_predicate(tmp_path: Path) -> None:
    source = (
        "class Account:\n"
        "    def withdraw(self, amount: float):\n"
        "        if amount <= self.balance:\n"
        "            return self.balance - amount\n"
    )

    semantics = analyze(tmp_path, source)

    target = target_of(semantics, "Account.withdraw")
    assert target.comparisons[0].left.text == "amount"
    assert target.comparisons[0].right.text == "self.balance"
    assert target.comparisons[0].right.kind is ExpressionKind.ATTRIBUTE
    assert target.comparisons[0].right.names == ("self",)
    assert target.arithmetic[0].expression.text == "self.balance - amount"


def test_nested_function_owns_its_own_predicate(tmp_path: Path) -> None:
    source = (
        "def outer(x: int):\n"
        "    if x > 0:\n"
        "        ...\n"
        "\n"
        "    def inner(y: int):\n"
        "        if y < 5:\n"
        "            return y\n"
    )

    semantics = analyze(tmp_path, source)

    outer = target_of(semantics, "outer")
    inner = target_of(semantics, "outer.inner")
    assert [comparison.left.text for comparison in outer.comparisons] == ["x"]
    assert [comparison.left.text for comparison in inner.comparisons] == ["y"]


def test_negative_numeric_literal_is_recorded(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(x: int):\n    if x >= -1:\n        return x\n")

    constants = target_of(semantics, "f").constants
    assert [(item.value_text, item.kind) for item in constants] == [("-1", ConstantKind.INTEGER)]


def test_signed_float_literal_is_recorded(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(x: float):\n    if x <= -0.5:\n        return x\n")

    constants = target_of(semantics, "f").constants
    assert [(item.value_text, item.kind) for item in constants] == [("-0.5", ConstantKind.FLOAT)]


def test_bool_literal_is_not_numeric(tmp_path: Path) -> None:
    source = "def f(flag: bool):\n    if flag == True:\n        ...\n"

    semantics = analyze(tmp_path, source)

    target = target_of(semantics, "f")
    assert [(item.value_text, item.kind) for item in target.constants] == [
        ("True", ConstantKind.BOOL)
    ]
    assert target.comparisons[0].right.constant_kind is ConstantKind.BOOL


def test_none_comparison_is_recorded(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(x):\n    if x is None:\n        ...\n")

    comparison = target_of(semantics, "f").comparisons[0]
    assert comparison.operator is ComparisonOperator.IS
    assert comparison.right.constant_kind is ConstantKind.NONE


def test_string_equality_is_recorded(tmp_path: Path) -> None:
    source = "def f(role: str):\n    if role == 'admin':\n        ...\n"

    semantics = analyze(tmp_path, source)

    target = target_of(semantics, "f")
    assert target.comparisons[0].operator is ComparisonOperator.EQUAL
    assert target.comparisons[0].right.text == "'admin'"
    assert target.constants[0].kind is ConstantKind.STRING


def test_membership_and_identity_operators_are_recorded(tmp_path: Path) -> None:
    source = (
        "def f(item, allowed):\n"
        "    if item in allowed:\n"
        "        ...\n"
        "    if item is not None:\n"
        "        ...\n"
    )

    semantics = analyze(tmp_path, source)

    operators = [comparison.operator for comparison in target_of(semantics, "f").comparisons]
    assert operators == [ComparisonOperator.IN, ComparisonOperator.IS_NOT]


def test_arithmetic_in_return_context(tmp_path: Path) -> None:
    source = "def withdraw(balance: float, amount: float):\n    return balance - amount\n"

    semantics = analyze(tmp_path, source)

    arithmetic = target_of(semantics, "withdraw").arithmetic
    assert len(arithmetic) == 1
    assert arithmetic[0].expression.text == "balance - amount"
    assert arithmetic[0].operators == (ArithmeticOperator.SUBTRACT,)
    assert arithmetic[0].context is ArithmeticContext.RETURN
    assert arithmetic[0].expression.names == ("balance", "amount")
    assert arithmetic[0].lineno == 2


def test_arithmetic_in_assignment_context(tmp_path: Path) -> None:
    source = "def f(x: int):\n    doubled = x * 2\n    return doubled\n"

    semantics = analyze(tmp_path, source)

    arithmetic = target_of(semantics, "f").arithmetic
    assert arithmetic[0].expression.text == "x * 2"
    assert arithmetic[0].operators == (ArithmeticOperator.MULTIPLY,)
    assert arithmetic[0].context is ArithmeticContext.ASSIGNMENT


def test_arithmetic_in_augmented_assignment_context(tmp_path: Path) -> None:
    source = "class A:\n    def update(self, amount):\n        self.balance -= amount\n"

    semantics = analyze(tmp_path, source)

    arithmetic = target_of(semantics, "A.update").arithmetic
    assert arithmetic[0].expression.text == "self.balance -= amount"
    assert arithmetic[0].operators == (ArithmeticOperator.SUBTRACT,)
    assert arithmetic[0].context is ArithmeticContext.AUGMENTED_ASSIGNMENT


def test_nested_arithmetic_records_maximal_expression(tmp_path: Path) -> None:
    source = "def f(x, y):\n    return (x + y) * 2\n"

    semantics = analyze(tmp_path, source)

    arithmetic = target_of(semantics, "f").arithmetic
    assert len(arithmetic) == 1
    assert arithmetic[0].expression.text == "(x + y) * 2"
    assert arithmetic[0].operators == (
        ArithmeticOperator.MULTIPLY,
        ArithmeticOperator.ADD,
    )


def test_assert_predicate_is_recorded(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(x):\n    assert x >= 0\n")

    predicate = target_of(semantics, "f").predicates[0]
    assert predicate.kind is PredicateKind.ASSERT
    assert predicate.comparisons[0].operator is ComparisonOperator.GREATER_THAN_OR_EQUAL


def test_while_predicate_is_recorded(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(x):\n    while x < 5:\n        x += 1\n")

    target = target_of(semantics, "f")
    assert target.predicates[0].kind is PredicateKind.WHILE
    assert target.comparisons[0].right.text == "5"


def test_elif_produces_its_own_predicate(tmp_path: Path) -> None:
    source = "def f(x):\n    if x < 0:\n        ...\n    elif x == 0:\n        ...\n"

    semantics = analyze(tmp_path, source)

    target = target_of(semantics, "f")
    assert len(target.predicates) == 2
    assert [predicate.lineno for predicate in target.predicates] == [2, 4]


def test_comprehension_filters_are_not_predicates(tmp_path: Path) -> None:
    source = "def f(items):\n    return [x for x in items if x > 0]\n"

    semantics = analyze(tmp_path, source)

    target = target_of(semantics, "f")
    assert target.predicates == ()
    assert target.comparisons == ()


def test_lambdas_are_not_targets(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "double = lambda v: v * 2\n")

    assert semantics.targets == ()
    assert semantics.boundaries == ()


def test_class_level_code_belongs_to_no_target(tmp_path: Path) -> None:
    source = (
        "LIMIT = 10\n"
        "if LIMIT > 5:\n"
        "    MARKER = 'big'\n"
        "\n"
        "class C:\n"
        "    VALUE = 3\n"
        "    if VALUE > 1:\n"
        "        OTHER = 'big'\n"
    )

    semantics = analyze(tmp_path, source)

    assert semantics.targets == ()


def test_module_with_syntax_issue_yields_no_facts(tmp_path: Path) -> None:
    path = tmp_path / "broken.py"
    path.write_text("def broken(\n", encoding="utf-8")
    parsed = parse_source_file(path, Path("broken.py"))
    module = build_module_analysis(parsed)

    semantics = extract_module_semantics(parsed, module)

    assert semantics.targets == ()
    assert semantics.boundaries == ()
    assert semantics.path == path


def test_fact_counts_match_records(tmp_path: Path) -> None:
    source = "def f(x: int, y: int):\n    if x > 0 and y < 10:\n        return x * 2\n"

    semantics = analyze(tmp_path, source)

    assert semantics.predicate_count == 1
    assert semantics.comparison_count == 2
    assert semantics.arithmetic_expression_count == 1
