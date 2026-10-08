"""Deterministic boundary-candidate mining from semantic facts.

Mining turns semantic facts into *candidates*: regions of a parameter's value
space that a future generator should attack. A candidate is never a claim of
defect. Nothing here evaluates a reference expression, calls anything, or
materializes a concrete number: for ``amount <= balance`` the candidate is
"amount at balance", and how to step away from ``balance`` is deliberately left
to the future generation stage.

Two rules are kept deliberately conservative:

* candidates are derived for declared parameters, never for arbitrary names;
* a comparison produces candidates only when the opposite side is a stable
  reference (literal, bare name, or plain dotted name chain).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from edgehunter.analysis.models import (
    BOUNDARY_KIND_ORDER,
    BoundaryCandidate,
    BoundaryKind,
    BoundaryOrigin,
    ComparisonInfo,
    ComparisonOperator,
    ConstantKind,
    ExpressionInfo,
    ExpressionKind,
    TargetBoundaries,
    TargetSemantics,
    invert_comparison_operator,
)
from edgehunter.scanner.models import (
    SINGLE_VALUE_PARAMETER_KINDS,
    FunctionInfo,
    ModuleAnalysis,
    ParameterInfo,
)

_NUMERIC_ANNOTATION_KINDS: Final[dict[str, tuple[BoundaryKind, ...]]] = {
    "int": (
        BoundaryKind.ZERO,
        BoundaryKind.ONE,
        BoundaryKind.NEGATIVE_ONE,
    ),
    "float": (
        BoundaryKind.ZERO,
        BoundaryKind.ONE,
        BoundaryKind.NEGATIVE_ONE,
        BoundaryKind.NEGATIVE_ZERO,
        BoundaryKind.NAN,
        BoundaryKind.POSITIVE_INFINITY,
        BoundaryKind.NEGATIVE_INFINITY,
    ),
}

_NUMERIC_CONSTANT_KINDS: Final[frozenset[ConstantKind]] = frozenset(
    {ConstantKind.INTEGER, ConstantKind.FLOAT, ConstantKind.COMPLEX}
)

_INEQUALITY_OPERATORS: Final[frozenset[ComparisonOperator]] = frozenset(
    {
        ComparisonOperator.LESS_THAN,
        ComparisonOperator.LESS_THAN_OR_EQUAL,
        ComparisonOperator.GREATER_THAN,
        ComparisonOperator.GREATER_THAN_OR_EQUAL,
    }
)

_EQUALITY_OPERATORS: Final[frozenset[ComparisonOperator]] = frozenset(
    {ComparisonOperator.EQUAL, ComparisonOperator.NOT_EQUAL}
)

# Only inequality and equality are invertible; `is` and `in` are deliberately
# not reversed (see D-018). The table itself lives in models.py so oracle
# mining can reuse exactly the same rule.


@dataclass(frozen=True, slots=True)
class _NormalizedComparison:
    """A comparison rewritten so the parameter of interest is on the left."""

    parameter: str
    operator: ComparisonOperator
    reference: ExpressionInfo


def mine_target_boundaries(
    function: FunctionInfo, semantics: TargetSemantics
) -> tuple[BoundaryCandidate, ...]:
    """Mine deduplicated, deterministically ordered candidates for one target."""
    reasons: dict[tuple[str, BoundaryKind, str | None], tuple[BoundaryOrigin, str]] = {}
    evidence: dict[tuple[str, BoundaryKind, str | None], list[int]] = {}

    def _add(
        parameter: str,
        kind: BoundaryKind,
        reference: str | None,
        origin: BoundaryOrigin,
        reason: str,
        line: int,
    ) -> None:
        key = (parameter, kind, reference)
        reasons.setdefault(key, (origin, reason))
        lines = evidence.setdefault(key, [])
        if line not in lines:
            lines.append(line)

    for parameter in function.parameters:
        for kind in _generic_numeric_kinds(parameter):
            _add(
                parameter.name,
                kind,
                None,
                BoundaryOrigin.NUMERIC,
                f"generic numeric candidate for {parameter.annotation} parameter",
                function.lineno,
            )

    parameter_names = frozenset(parameter.name for parameter in function.parameters)
    for comparison in semantics.comparisons:
        normalized = _normalize(comparison, parameter_names)
        if normalized is None:
            continue
        kinds = _comparison_boundary_kinds(normalized.operator, normalized.reference)
        if not kinds:
            continue
        reason = (
            f"comparison-derived: {comparison.left.text} "
            f"{comparison.operator.value} {comparison.right.text}"
        )
        for kind in kinds:
            _add(
                normalized.parameter,
                kind,
                normalized.reference.text,
                BoundaryOrigin.COMPARISON,
                reason,
                comparison.lineno,
            )

    candidates: list[BoundaryCandidate] = []
    for name, kind, reference in reasons:
        origin, reason = reasons[(name, kind, reference)]
        candidates.append(
            BoundaryCandidate(
                target=function.qualified_name,
                parameter=name,
                kind=kind,
                origin=origin,
                reference=reference,
                evidence=tuple(sorted(evidence[(name, kind, reference)])),
                reason=reason,
            )
        )

    candidates.sort(key=_sort_key)
    return tuple(candidates)


def mine_module_boundaries(
    module: ModuleAnalysis, targets: tuple[TargetSemantics, ...]
) -> tuple[TargetBoundaries, ...]:
    """Mine boundaries for every target of a module, in deterministic order."""
    semantics_by_name = {target.qualified_name: target for target in targets}
    entries: list[TargetBoundaries] = []

    for function in module.functions:
        semantics = semantics_by_name.get(function.qualified_name)
        if semantics is None:
            continue
        candidates = mine_target_boundaries(function, semantics)
        if not candidates:
            continue
        entries.append(
            TargetBoundaries(
                relative_path=module.relative_path,
                qualified_name=function.qualified_name,
                lineno=function.lineno,
                signature=function.signature,
                candidates=candidates,
            )
        )
    return tuple(entries)


def _generic_numeric_kinds(parameter: ParameterInfo) -> tuple[BoundaryKind, ...]:
    if parameter.annotation is None:
        return ()
    if parameter.kind not in SINGLE_VALUE_PARAMETER_KINDS:
        return ()
    return _NUMERIC_ANNOTATION_KINDS.get(parameter.annotation, ())


def _normalize(
    comparison: ComparisonInfo, parameter_names: frozenset[str]
) -> _NormalizedComparison | None:
    """Rewrite a comparison so a declared parameter is the subject.

    Returns ``None`` when neither side is a parameter, when the operator cannot
    be safely inverted, or when the opposite side is not a stable reference.
    When both sides are parameters the syntactic left side wins.
    """
    if comparison.left.kind is ExpressionKind.NAME and comparison.left.text in parameter_names:
        subject, operator, reference = comparison.left, comparison.operator, comparison.right
    elif comparison.right.kind is ExpressionKind.NAME and comparison.right.text in parameter_names:
        inverted = invert_comparison_operator(comparison.operator)
        if inverted is None:
            return None
        subject, operator, reference = comparison.right, inverted, comparison.left
    else:
        return None

    if not reference.simple:
        return None
    return _NormalizedComparison(parameter=subject.text, operator=operator, reference=reference)


def _comparison_boundary_kinds(
    operator: ComparisonOperator, reference: ExpressionInfo
) -> tuple[BoundaryKind, ...]:
    if operator in _INEQUALITY_OPERATORS:
        if _is_ordered_reference(reference):
            return (
                BoundaryKind.JUST_BELOW,
                BoundaryKind.AT_BOUNDARY,
                BoundaryKind.JUST_ABOVE,
            )
        return ()
    if operator in _EQUALITY_OPERATORS:
        return (BoundaryKind.AT_BOUNDARY,)
    return ()


def _is_ordered_reference(reference: ExpressionInfo) -> bool:
    if reference.kind in (ExpressionKind.NAME, ExpressionKind.ATTRIBUTE):
        return True
    return reference.constant_kind in _NUMERIC_CONSTANT_KINDS


def _sort_key(candidate: BoundaryCandidate) -> tuple[str, int, str]:
    return (
        candidate.parameter,
        BOUNDARY_KIND_ORDER[candidate.kind],
        candidate.reference or "",
    )
