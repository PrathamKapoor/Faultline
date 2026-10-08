"""Mining of explicit oracle evidence from production source.

Two signals are mined, both of them statements the repository itself writes:

* ``assert`` conditions, reusing the ``assert`` predicates already extracted by
  the semantic layer, plus the assertion message when it is a literal string;
* conservative validation guards of the form ``if CONDITION: raise EXC``.

Nothing here is inverted into a claim about what the code is *not* doing: an
assertion is recorded as "the code expects this when execution reaches it", and
a guard is recorded as "this condition leads to this raise".
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from typing import Final

from edgehunter.analysis.models import ModuleSemantics, PredicateKind
from edgehunter.oracle.models import (
    ExpectedOutcome,
    OracleEvidence,
    OracleEvidenceKind,
    OracleStrength,
    ProductionPropertyCandidate,
    PropertyKind,
)
from edgehunter.oracle.test_expectations import is_test_file
from edgehunter.scanner.ast_parser import SourceFile
from edgehunter.scanner.rendering import render_source, source_lineno
from edgehunter.scanner.targets import CallableNode, collect_callable_nodes, iter_callable_body

EXCEPTION_NAME_SUFFIXES: Final[tuple[str, ...]] = ("Error", "Exception", "Warning")

_PropertyKey = tuple[PropertyKind, str, str, "str | None", "str | None"]


def extract_production_properties(
    source: SourceFile,
    semantics: ModuleSemantics,
) -> tuple[ProductionPropertyCandidate, ...]:
    """Mine assertions and validation guards for every target of one module.

    Test files are skipped: an ``assert`` inside a test function is evidence
    about the *tested* callable and is mined as a test expectation instead, so
    counting it here would double-count the same statement.

    A file that failed to read or parse contributes no evidence; its structured
    issue remains the only report for it.
    """
    if source.tree is None or is_test_file(source.relative_path):
        return ()

    messages = _assertion_messages(source.tree)
    callables = {item.info.qualified_name: item for item in collect_callable_nodes(source.tree)}

    mined: list[ProductionPropertyCandidate] = []
    for target in semantics.targets:
        for predicate in target.predicates:
            if predicate.kind is not PredicateKind.ASSERT:
                continue
            mined.append(
                ProductionPropertyCandidate(
                    kind=PropertyKind.ASSERTED_CONDITION,
                    target=target.qualified_name,
                    condition=predicate.expression.text,
                    strength=OracleStrength.STRONG,
                    expected_outcome=ExpectedOutcome.CONDITION_HOLDS,
                    evidence=(
                        OracleEvidence(
                            kind=OracleEvidenceKind.PRODUCTION_ASSERTION,
                            relative_path=source.relative_path,
                            lineno=predicate.lineno,
                            detail=f"assert {predicate.expression.text}",
                        ),
                    ),
                    message=messages.get(predicate.lineno),
                )
            )

        callable_node = callables.get(target.qualified_name)
        if callable_node is not None:
            mined.extend(
                _validation_rules(target.qualified_name, source.relative_path, callable_node)
            )

    return _deduplicate(mined)


def _assertion_messages(tree: ast.Module) -> dict[int, str]:
    """Map assertion line to message, keeping only unambiguous literal messages."""
    messages: dict[int, str | None] = {}
    ambiguous_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        line = source_lineno(node)
        if line in messages:
            ambiguous_lines.add(line)
        messages[line] = _assertion_message(node)

    return {
        line: text
        for line, text in messages.items()
        if text is not None and line not in ambiguous_lines
    }


def _assertion_message(node: ast.Assert) -> str | None:
    if node.msg is None:
        return None
    if isinstance(node.msg, ast.Constant) and isinstance(node.msg.value, str):
        return node.msg.value
    return render_source(node.msg)


def _validation_rules(
    target: str, relative_path: Path, callable_node: CallableNode
) -> list[ProductionPropertyCandidate]:
    """Mine ``if COND: raise EXC`` guards owned by one callable."""
    rules: list[ProductionPropertyCandidate] = []
    for node in iter_callable_body(callable_node.node):
        if not isinstance(node, ast.If):
            continue
        raised = _rejection_raise(node)
        if raised is None:
            continue
        exception, detail = _raise_detail(raised)
        rules.append(
            ProductionPropertyCandidate(
                kind=PropertyKind.REJECTION_RULE,
                target=target,
                condition=render_source(node.test),
                strength=OracleStrength.STRONG,
                expected_outcome=ExpectedOutcome.RAISES,
                evidence=(
                    OracleEvidence(
                        kind=OracleEvidenceKind.VALIDATION_GUARD,
                        relative_path=relative_path,
                        lineno=source_lineno(node),
                        detail=detail,
                    ),
                ),
                exception=exception,
            )
        )
    return rules


def _rejection_raise(node: ast.If) -> ast.Raise | None:
    """Return the single ``raise`` of a guard, or ``None`` when it is not one."""
    if len(node.body) == 1 and isinstance(node.body[0], ast.Raise):
        return node.body[0]
    return None


def _raise_detail(raised: ast.Raise) -> tuple[str | None, str]:
    """Extract the exception syntax of a ``raise`` without importing anything."""
    if raised.exc is None:
        return None, "re-raises"
    exc = raised.exc
    if isinstance(exc, ast.Name):
        return exc.id, f"raises {exc.id}"
    if isinstance(exc, ast.Call):
        func = exc.func
        if isinstance(func, ast.Name):
            if func.id.endswith(EXCEPTION_NAME_SUFFIXES):
                return func.id, f"raises {func.id}(...)"
            return None, f"raises {render_source(exc)}"
        if _is_dotted_name(func):
            return render_source(func), f"raises {render_source(func)}(...)"
        return None, f"raises {render_source(exc)}"
    return None, f"raises {render_source(exc)}"


def _is_dotted_name(node: ast.expr) -> bool:
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        current = current.value
    return isinstance(current, ast.Name)


def _deduplicate(
    items: list[ProductionPropertyCandidate],
) -> tuple[ProductionPropertyCandidate, ...]:
    """Merge identical properties, keeping every supporting line in order."""
    first_seen: dict[_PropertyKey, ProductionPropertyCandidate] = {}
    evidence: dict[_PropertyKey, list[OracleEvidence]] = {}

    for item in items:
        key = _property_key(item)
        first_seen.setdefault(key, item)
        collected = evidence.setdefault(key, [])
        for entry in item.evidence:
            if entry not in collected:
                collected.append(entry)

    merged: list[ProductionPropertyCandidate] = []
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
            item.target,
            item.kind.value,
            item.condition,
        )
    )
    return tuple(merged)


def _property_key(item: ProductionPropertyCandidate) -> _PropertyKey:
    return (item.kind, item.target, item.condition, item.exception, item.message)
