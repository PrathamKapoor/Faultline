"""Deterministic semantic-fact extraction from parsed Python ASTs.

This module turns one parsed module into ``TargetSemantics``: branch
predicates (``if``, ``while``, ``assert``), the comparisons inside them,
literal constants, and maximal arithmetic expressions. Nothing is evaluated,
imported, or executed; every fact is source text plus a line number.

Scope rule: facts belong to the callable target that lexically contains them.
Nested functions and classes are skipped here, so a nested function's
conditions are never attributed to its parent.
"""

from __future__ import annotations

import ast
from typing import Final

from edgehunter.analysis.boundaries import mine_module_boundaries
from edgehunter.analysis.models import (
    ArithmeticContext,
    ArithmeticInfo,
    ArithmeticOperator,
    ComparisonInfo,
    ComparisonOperator,
    ConstantInfo,
    ConstantKind,
    ExpressionInfo,
    ExpressionKind,
    ModuleSemantics,
    PredicateInfo,
    PredicateKind,
    TargetSemantics,
)
from edgehunter.scanner.ast_parser import SourceFile
from edgehunter.scanner.models import ModuleAnalysis
from edgehunter.scanner.rendering import render_source
from edgehunter.scanner.targets import CallableNode, collect_callable_nodes

_COMPARISON_OPERATORS: Final[dict[type[ast.cmpop], ComparisonOperator]] = {
    ast.Lt: ComparisonOperator.LESS_THAN,
    ast.LtE: ComparisonOperator.LESS_THAN_OR_EQUAL,
    ast.Gt: ComparisonOperator.GREATER_THAN,
    ast.GtE: ComparisonOperator.GREATER_THAN_OR_EQUAL,
    ast.Eq: ComparisonOperator.EQUAL,
    ast.NotEq: ComparisonOperator.NOT_EQUAL,
    ast.Is: ComparisonOperator.IS,
    ast.IsNot: ComparisonOperator.IS_NOT,
    ast.In: ComparisonOperator.IN,
    ast.NotIn: ComparisonOperator.NOT_IN,
}

_ARITHMETIC_OPERATORS: Final[dict[type[ast.operator], ArithmeticOperator]] = {
    ast.Add: ArithmeticOperator.ADD,
    ast.Sub: ArithmeticOperator.SUBTRACT,
    ast.Mult: ArithmeticOperator.MULTIPLY,
    ast.Div: ArithmeticOperator.DIVIDE,
    ast.FloorDiv: ArithmeticOperator.FLOOR_DIVIDE,
    ast.Mod: ArithmeticOperator.MODULO,
    ast.Pow: ArithmeticOperator.POWER,
    ast.MatMult: ArithmeticOperator.MATRIX_MULTIPLY,
}

_PREDICATE_KINDS: Final[tuple[tuple[type[ast.stmt], PredicateKind], ...]] = (
    (ast.If, PredicateKind.IF),
    (ast.While, PredicateKind.WHILE),
    (ast.Assert, PredicateKind.ASSERT),
)


def extract_module_semantics(source: SourceFile, module: ModuleAnalysis) -> ModuleSemantics:
    """Extract semantic facts and mine boundaries for one parsed module.

    A file that failed to read or parse contributes no facts; the structured
    issue already recorded by the scanner remains the only report for it.
    """
    if source.tree is None:
        return ModuleSemantics(
            path=source.path,
            relative_path=source.relative_path,
            targets=(),
            boundaries=(),
        )

    targets = tuple(_target_semantics(item) for item in collect_callable_nodes(source.tree))
    return ModuleSemantics(
        path=source.path,
        relative_path=source.relative_path,
        targets=targets,
        boundaries=mine_module_boundaries(module, targets),
    )


def _target_semantics(item: CallableNode) -> TargetSemantics:
    collector = _FactCollector()
    collector.visit_statements(item.node.body)

    predicates = tuple(collector.predicates)
    return TargetSemantics(
        qualified_name=item.info.qualified_name,
        lineno=item.info.lineno,
        predicates=predicates,
        comparisons=tuple(
            comparison for predicate in predicates for comparison in predicate.comparisons
        ),
        arithmetic=tuple(collector.arithmetic),
        constants=tuple(collector.constants),
    )


class _FactCollector:
    """Collects facts for a single callable body in source order."""

    def __init__(self) -> None:
        self.predicates: list[PredicateInfo] = []
        self.arithmetic: list[ArithmeticInfo] = []
        self.constants: list[ConstantInfo] = []

    def visit_statements(self, body: list[ast.stmt]) -> None:
        for statement in body:
            self._visit(statement, ArithmeticContext.EXPRESSION)

    def _visit(self, node: ast.AST, context: ArithmeticContext) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return

        if isinstance(node, ast.stmt):
            predicate_kind = _predicate_kind(node)
            if predicate_kind is not None:
                self._record_predicate(node, predicate_kind)

        if isinstance(node, ast.Return) and node.value is not None:
            self._visit(node.value, ArithmeticContext.RETURN)
            return
        if isinstance(node, ast.Assign):
            self._visit(node.value, ArithmeticContext.ASSIGNMENT)
            return
        if isinstance(node, ast.AnnAssign):
            if node.value is not None:
                self._visit(node.value, ArithmeticContext.ASSIGNMENT)
            return
        if isinstance(node, ast.AugAssign):
            operator = _ARITHMETIC_OPERATORS.get(type(node.op))
            if operator is None:
                self._collect_constants_in(node.value)
            else:
                self._record_augmented_assignment(node, operator)
            return

        signed = _signed_numeric_literal(node)
        if signed is not None:
            self.constants.append(signed)
            return

        if isinstance(node, ast.BinOp):
            if type(node.op) in _ARITHMETIC_OPERATORS:
                self._record_arithmetic(node, context)
            else:
                self._visit_children(node, context)
            return

        if isinstance(node, ast.Constant):
            self._record_constant(node)

        self._visit_children(node, context)

    def _visit_children(self, node: ast.AST, context: ArithmeticContext) -> None:
        for child in ast.iter_child_nodes(node):
            self._visit(child, context)

    def _record_predicate(self, node: ast.stmt, kind: PredicateKind) -> None:
        test = _predicate_test(node)
        if test is None:
            return
        comparisons: list[ComparisonInfo] = []
        self._collect_comparisons(test, comparisons)
        self.predicates.append(
            PredicateInfo(
                kind=kind,
                expression=_expression_info(test),
                comparisons=tuple(comparisons),
                lineno=_lineno(node),
            )
        )

    def _collect_comparisons(self, node: ast.AST, collected: list[ComparisonInfo]) -> None:
        if isinstance(node, ast.Lambda):
            return
        if isinstance(node, ast.Compare):
            collected.extend(_decompose_comparison(node))
        for child in ast.iter_child_nodes(node):
            self._collect_comparisons(child, collected)

    def _record_arithmetic(self, node: ast.BinOp, context: ArithmeticContext) -> None:
        self.arithmetic.append(
            ArithmeticInfo(
                expression=ExpressionInfo(
                    text=render_source(node),
                    kind=ExpressionKind.BINARY,
                    names=_names_in(node),
                    simple=False,
                ),
                operators=_arithmetic_operators_in(node),
                context=context,
                lineno=_lineno(node),
            )
        )
        self._collect_constants_in(node)

    def _record_augmented_assignment(
        self, node: ast.AugAssign, operator: ArithmeticOperator
    ) -> None:
        self.arithmetic.append(
            ArithmeticInfo(
                expression=ExpressionInfo(
                    text=render_source(node),
                    kind=ExpressionKind.BINARY,
                    names=_names_in(node),
                    simple=False,
                ),
                operators=(operator,),
                context=ArithmeticContext.AUGMENTED_ASSIGNMENT,
                lineno=_lineno(node),
            )
        )
        self._collect_constants_in(node.value)

    def _collect_constants_in(self, node: ast.AST) -> None:
        if isinstance(node, ast.Lambda):
            return
        if isinstance(node, ast.Constant):
            self._record_constant(node)
        for child in ast.iter_child_nodes(node):
            self._collect_constants_in(child)

    def _record_constant(self, node: ast.Constant) -> None:
        kind = _constant_kind(node.value)
        if kind is None:
            return
        self.constants.append(
            ConstantInfo(
                value_text=render_source(node),
                kind=kind,
                lineno=_lineno(node),
            )
        )


def _predicate_kind(node: ast.stmt) -> PredicateKind | None:
    for node_type, kind in _PREDICATE_KINDS:
        if isinstance(node, node_type):
            return kind
    return None


def _predicate_test(node: ast.stmt) -> ast.expr | None:
    if isinstance(node, (ast.If, ast.While, ast.Assert)):
        return node.test
    return None


def _decompose_comparison(node: ast.Compare) -> list[ComparisonInfo]:
    operands = [node.left, *node.comparators]
    comparisons: list[ComparisonInfo] = []
    for index, operator in enumerate(node.ops):
        mapped = _COMPARISON_OPERATORS.get(type(operator))
        if mapped is None:
            continue
        comparisons.append(
            ComparisonInfo(
                left=_expression_info(operands[index]),
                operator=mapped,
                right=_expression_info(operands[index + 1]),
                lineno=_lineno(node),
            )
        )
    return comparisons


def _arithmetic_operators_in(node: ast.AST) -> tuple[ArithmeticOperator, ...]:
    operators: list[ArithmeticOperator] = []
    if isinstance(node, ast.BinOp):
        mapped = _ARITHMETIC_OPERATORS.get(type(node.op))
        if mapped is not None and mapped not in operators:
            operators.append(mapped)
    for child in ast.iter_child_nodes(node):
        for operator in _arithmetic_operators_in(child):
            if operator not in operators:
                operators.append(operator)
    return tuple(operators)


def _signed_numeric_literal(node: ast.AST) -> ConstantInfo | None:
    if not isinstance(node, ast.UnaryOp) or not isinstance(node.op, (ast.UAdd, ast.USub)):
        return None
    operand = node.operand
    if not isinstance(operand, ast.Constant):
        return None
    if isinstance(operand.value, bool) or not isinstance(operand.value, (int, float, complex)):
        return None
    kind = _constant_kind(operand.value)
    if kind is None:
        return None
    return ConstantInfo(
        value_text=render_source(node),
        kind=kind,
        lineno=_lineno(node),
    )


def _expression_info(node: ast.expr) -> ExpressionInfo:
    return ExpressionInfo(
        text=render_source(node),
        kind=_expression_kind(node),
        names=_names_in(node),
        simple=_is_simple_reference(node),
        constant_kind=_constant_kind(node.value) if isinstance(node, ast.Constant) else None,
    )


def _expression_kind(node: ast.AST) -> ExpressionKind:
    if isinstance(node, ast.Name):
        return ExpressionKind.NAME
    if isinstance(node, ast.Attribute):
        return ExpressionKind.ATTRIBUTE
    if isinstance(node, ast.Constant):
        return ExpressionKind.CONSTANT
    if isinstance(node, ast.Call):
        return ExpressionKind.CALL
    if isinstance(node, ast.Subscript):
        return ExpressionKind.SUBSCRIPT
    if isinstance(node, (ast.BinOp, ast.BoolOp, ast.Compare)):
        return ExpressionKind.BINARY
    if isinstance(node, ast.UnaryOp):
        return ExpressionKind.UNARY
    return ExpressionKind.OTHER


def _is_simple_reference(node: ast.expr) -> bool:
    """True for literals, bare names, and plain dotted name chains."""
    if isinstance(node, (ast.Constant, ast.Name)):
        return True
    if isinstance(node, ast.Attribute):
        current: ast.expr = node
        while isinstance(current, ast.Attribute):
            current = current.value
        return isinstance(current, ast.Name)
    return False


def _names_in(node: ast.AST) -> tuple[str, ...]:
    names: list[str] = []
    if isinstance(node, ast.Name) and node.id not in names:
        names.append(node.id)
    for child in ast.iter_child_nodes(node):
        for name in _names_in(child):
            if name not in names:
                names.append(name)
    return tuple(names)


def _constant_kind(value: object) -> ConstantKind | None:
    """Classify a literal value; ``None`` means "not a recorded literal"."""
    if value is None:
        return ConstantKind.NONE
    if isinstance(value, bool):
        return ConstantKind.BOOL
    if isinstance(value, int):
        return ConstantKind.INTEGER
    if isinstance(value, float):
        return ConstantKind.FLOAT
    if isinstance(value, complex):
        return ConstantKind.COMPLEX
    if isinstance(value, str):
        return ConstantKind.STRING
    if isinstance(value, bytes):
        return ConstantKind.BYTES
    return None


def _lineno(node: ast.AST) -> int:
    lineno: int | None = getattr(node, "lineno", None)
    return lineno if lineno is not None else 0
