"""Mining of explicit expectations from existing test files, without running them.

Only a deliberately small, conservative subset of test syntax is recognized:

* ``assert <call> <op> <expected>`` (and the reversed form, normalized only
  where the operator inversion is provably safe);
* ``with pytest.raises(<Exc>): <one direct call>``.

Test files are recognized by filename only (``test_*.py`` or ``*_test.py``), so
a production module that happens to contain ``assert`` calls is never mistaken
for a test.

Nothing is executed and pytest is never imported: recognition is purely
syntactic.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from typing import Final

from edgehunter.analysis.models import ComparisonOperator, invert_comparison_operator
from edgehunter.oracle.models import (
    ExpectedOutcome,
    OracleEvidence,
    OracleEvidenceKind,
    OracleStrength,
    PropertyKind,
    TargetResolution,
    TestExpectationCandidate,
)
from edgehunter.scanner.ast_parser import SourceFile
from edgehunter.scanner.rendering import render_source, source_lineno

TEST_FILE_PREFIXES: Final[tuple[str, ...]] = ("test_",)
TEST_FILE_SUFFIXES: Final[tuple[str, ...]] = ("_test.py",)

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


def is_test_file(relative_path: Path) -> bool:
    """True when the filename matches the documented test-file convention."""
    name = relative_path.name
    return name.startswith(TEST_FILE_PREFIXES) or name.endswith(TEST_FILE_SUFFIXES)


def extract_test_expectations(source: SourceFile) -> tuple[TestExpectationCandidate, ...]:
    """Mine expectations from one test module, leaving target resolution open."""
    if source.tree is None or not is_test_file(source.relative_path):
        return ()

    mined: list[TestExpectationCandidate] = []
    for node in ast.walk(source.tree):
        if isinstance(node, ast.Assert) and node.test is not None:
            mined.extend(_assert_expectations(node, source.relative_path))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            mined.extend(_raises_expectations(node, source.relative_path))

    return deduplicate_expectations(mined)


def _assert_expectations(node: ast.Assert, relative_path: Path) -> list[TestExpectationCandidate]:
    test = node.test
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return []
    operator = _COMPARISON_OPERATORS.get(type(test.ops[0]))
    if operator is None:
        return []

    left, right = test.left, test.comparators[0]
    if isinstance(left, ast.Call):
        return [_expectation(relative_path, node, left, operator, right)]
    if isinstance(right, ast.Call):
        inverted = invert_comparison_operator(operator)
        if inverted is None:
            return []
        return [_expectation(relative_path, node, right, inverted, left)]
    return []


def _expectation(
    relative_path: Path,
    node: ast.Assert,
    call: ast.Call,
    operator: ComparisonOperator,
    expected: ast.expr,
) -> TestExpectationCandidate:
    call_text = render_source(call)
    return TestExpectationCandidate(
        kind=PropertyKind.EXAMPLE_EXPECTATION,
        strength=OracleStrength.STRONG,
        expected_outcome=ExpectedOutcome.RETURNS,
        callee=_callee_name(call),
        call_expression=call_text,
        comparison_operator=operator,
        expected_expression=render_source(expected),
        literal_arguments=_literal_arguments(call),
        evidence=(
            OracleEvidence(
                kind=OracleEvidenceKind.TEST_ASSERTION,
                relative_path=relative_path,
                lineno=source_lineno(node),
                detail=f"assert {call_text} {operator.value} {render_source(expected)}",
            ),
        ),
    )


def _literal_arguments(call: ast.Call) -> tuple[str, ...]:
    """Positional arguments that are pure literals, in call order.

    These are real observed inputs a repository author already chose, which makes
    them stronger evidence than a synthesized baseline. Anything that is not
    literal syntax — a fixture, a name, a constructor call, a subscript — is
    skipped rather than evaluated.
    """
    literals: list[str] = []
    for argument in call.args:
        if isinstance(argument, ast.Starred):
            continue
        text = _literal_text(argument)
        if text is not None:
            literals.append(text)
    return tuple(literals)


def _literal_text(node: ast.expr) -> str | None:
    """Render one argument as literal text, or return ``None`` when it is not one."""
    if isinstance(node, ast.Constant):
        return render_source(node)
    if isinstance(node, ast.List) and not node.elts:
        return "[]"
    if isinstance(node, ast.Tuple) and not node.elts:
        return "()"
    if isinstance(node, ast.Dict) and not node.keys:
        return "{}"
    return None


def _raises_expectations(
    node: ast.With | ast.AsyncWith, relative_path: Path
) -> list[TestExpectationCandidate]:
    call = _raises_call(node)
    if call is None:
        return []
    body_call = _single_direct_call(node)
    if body_call is None:
        return []

    exception = _pytest_raises_argument(call)
    call_text = render_source(body_call)
    return [
        TestExpectationCandidate(
            kind=PropertyKind.EXAMPLE_RAISES,
            strength=OracleStrength.STRONG,
            expected_outcome=ExpectedOutcome.RAISES,
            callee=_callee_name(body_call),
            call_expression=call_text,
            exception=exception,
            literal_arguments=_literal_arguments(body_call),
            evidence=(
                OracleEvidence(
                    kind=OracleEvidenceKind.TEST_RAISES,
                    relative_path=relative_path,
                    lineno=source_lineno(node),
                    detail=(
                        f"pytest.raises({exception if exception is not None else '...'}) "
                        f"around {call_text}"
                    ),
                ),
            ),
        )
    ]


def _raises_call(node: ast.With | ast.AsyncWith) -> ast.Call | None:
    for item in node.items:
        context = item.context_expr
        if (
            isinstance(context, ast.Call)
            and isinstance(context.func, ast.Attribute)
            and context.func.attr == "raises"
            and isinstance(context.func.value, ast.Name)
            and context.func.value.id == "pytest"
        ):
            return context
    return None


def _pytest_raises_argument(call: ast.Call) -> str | None:
    if not call.args:
        return None
    argument = call.args[0]
    if isinstance(argument, ast.Name):
        return argument.id
    if isinstance(argument, ast.Attribute):
        return render_source(argument)
    return render_source(argument)


def _single_direct_call(node: ast.With | ast.AsyncWith) -> ast.Call | None:
    if len(node.body) != 1:
        return None
    statement = node.body[0]
    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
        return statement.value
    return None


def _callee_name(call: ast.Call) -> str:
    """Bare callee name, or the full dotted syntax when a receiver is present."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    return render_source(func)


def deduplicate_expectations(
    items: list[TestExpectationCandidate],
) -> tuple[TestExpectationCandidate, ...]:
    """Merge identical expectations, keeping every supporting line in order.

    Applied once across the whole repository, so the same expectation repeated
    in two test files becomes one candidate carrying both locations.
    """
    first_seen: dict[_ExpectationKey, TestExpectationCandidate] = {}
    evidence: dict[_ExpectationKey, list[OracleEvidence]] = {}

    for item in items:
        key = _expectation_key(item)
        first_seen.setdefault(key, item)
        collected = evidence.setdefault(key, [])
        for entry in item.evidence:
            if entry not in collected:
                collected.append(entry)

    merged: list[TestExpectationCandidate] = []
    for key, base in first_seen.items():
        merged.append(
            replace(
                base,
                evidence=tuple(
                    sorted(
                        evidence[key],
                        key=lambda entry: (
                            entry.relative_path.as_posix(),
                            entry.lineno,
                            entry.detail,
                        ),
                    )
                ),
            )
        )
    merged.sort(
        key=lambda item: (
            item.evidence[0].relative_path.as_posix(),
            item.evidence[0].lineno,
            item.kind.value,
            item.callee,
            item.call_expression or "",
        )
    )
    return tuple(merged)


_ExpectationKey = tuple[
    PropertyKind,
    str,
    str,
    "ComparisonOperator | None",
    str,
    "str | None",
    "str | None",
]


def _expectation_key(item: TestExpectationCandidate) -> _ExpectationKey:
    return (
        item.kind,
        item.callee,
        item.call_expression or "",
        item.comparison_operator,
        item.expected_expression or "",
        item.exception or "",
        item.target,
    )


def resolve_expectations(
    expectations: tuple[TestExpectationCandidate, ...],
    target_index: dict[str, tuple[str, ...]],
) -> tuple[TestExpectationCandidate, ...]:
    """Attach targets where the callee resolves to exactly one production target.

    ``target_index`` maps a simple callable name to every qualified name that
    carries it. Ambiguity is reported, never guessed, and unresolved evidence is
    still returned.
    """
    resolved: list[TestExpectationCandidate] = []
    for item in expectations:
        if "." in item.callee:
            resolved.append(
                replace(
                    item,
                    resolution=TargetResolution.UNRESOLVED,
                    resolution_reason="receiver resolution not implemented",
                )
            )
            continue

        matches = target_index.get(item.callee, ())
        if len(matches) == 1:
            resolved.append(
                replace(
                    item,
                    target=matches[0],
                    resolution=TargetResolution.RESOLVED,
                    resolution_reason="unique target with this name",
                )
            )
        elif matches:
            resolved.append(
                replace(
                    item,
                    resolution=TargetResolution.AMBIGUOUS,
                    resolution_reason=f"{len(matches)} targets share this name",
                )
            )
        else:
            resolved.append(
                replace(
                    item,
                    resolution=TargetResolution.UNRESOLVED,
                    resolution_reason="no target with this name",
                )
            )
    return tuple(resolved)
