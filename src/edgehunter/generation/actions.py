"""Selecting the constrained actions that apply to each parameter of a target.

Two independent sources justify an action, and the difference is recorded on
every action:

* a **boundary** candidate mined from the target's own comparisons. Each
  :class:`~edgehunter.analysis.models.BoundaryKind` maps to exactly one action,
  so the mapping is total and no candidate is silently dropped.
* the parameter's **annotation**. A bare ``list``/``dict``/``set``/``tuple``/
  ``frozenset`` annotation justifies an empty and a singleton collection, with
  no comparison required. This mirrors the annotation-driven generic numeric
  candidates that boundary mining already produces, and it is restricted to the
  same single-value parameter buckets so ``*args`` and ``**kwargs`` are left
  alone.

Untyped and unrecognized annotations get no generic action at all. That is
deliberate: an unparameterized ``list[int]`` would have to be parsed and
resolved to know what it means, and guessing is worse than reporting nothing.
An untyped parameter can still receive actions from its own comparisons.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from edgehunter.analysis.models import (
    BoundaryCandidate,
    BoundaryKind,
    ConstantKind,
    ModuleSemantics,
    TargetBoundaries,
    TargetSemantics,
)
from edgehunter.generation.materialize import (
    materialize_collection,
    materialize_generic_numeric,
    materialize_relational,
)
from edgehunter.generation.models import (
    ACTION_KIND_ORDER,
    ActionEvidence,
    ActionOrigin,
    AnnotationCategory,
    SemanticAction,
    SemanticActionKind,
    TargetActions,
)
from edgehunter.scanner.models import (
    METHOD_KINDS,
    RECEIVER_PARAMETER_NAMES,
    SINGLE_VALUE_PARAMETER_KINDS,
    FunctionInfo,
    ModuleAnalysis,
    ParameterInfo,
    ParameterKind,
)

# Total mapping from boundary vocabulary to action vocabulary.
BOUNDARY_ACTIONS: Final[dict[BoundaryKind, SemanticActionKind]] = {
    BoundaryKind.JUST_BELOW: SemanticActionKind.JUST_BELOW_BOUNDARY,
    BoundaryKind.AT_BOUNDARY: SemanticActionKind.AT_BOUNDARY,
    BoundaryKind.JUST_ABOVE: SemanticActionKind.JUST_ABOVE_BOUNDARY,
    BoundaryKind.ZERO: SemanticActionKind.SET_ZERO,
    BoundaryKind.ONE: SemanticActionKind.SET_ONE,
    BoundaryKind.NEGATIVE_ONE: SemanticActionKind.SET_NEGATIVE_ONE,
    BoundaryKind.NEGATIVE_ZERO: SemanticActionKind.SET_NEGATIVE_ZERO,
    BoundaryKind.NAN: SemanticActionKind.USE_NAN,
    BoundaryKind.POSITIVE_INFINITY: SemanticActionKind.USE_POSITIVE_INFINITY,
    BoundaryKind.NEGATIVE_INFINITY: SemanticActionKind.USE_NEGATIVE_INFINITY,
}

# Only exact builtin collection spellings are recognized; everything else,
# including subscripted generics, is UNKNOWN and contributes nothing.
ANNOTATION_CATEGORIES: Final[dict[str, AnnotationCategory]] = {
    "list": AnnotationCategory.LIST,
    "tuple": AnnotationCategory.TUPLE,
    "dict": AnnotationCategory.MAPPING,
    "set": AnnotationCategory.SET,
    "frozenset": AnnotationCategory.FROZENSET,
}

_COLLECTION_ACTION_KINDS: Final[tuple[SemanticActionKind, ...]] = (
    SemanticActionKind.EMPTY_COLLECTION,
    SemanticActionKind.SINGLETON_COLLECTION,
)

ANNOTATION_ACTIONS: Final[dict[AnnotationCategory, tuple[SemanticActionKind, ...]]] = {
    category: _COLLECTION_ACTION_KINDS
    for category in (
        AnnotationCategory.LIST,
        AnnotationCategory.TUPLE,
        AnnotationCategory.MAPPING,
        AnnotationCategory.SET,
        AnnotationCategory.FROZENSET,
    )
}

_OMITTABLE_PARAMETER_KINDS: Final[frozenset[ParameterKind]] = frozenset(
    {ParameterKind.VAR_POSITIONAL, ParameterKind.VAR_KEYWORD}
)


def classify_annotation(annotation: str | None) -> AnnotationCategory:
    """Map annotation source text to a recognized category, or ``UNKNOWN``."""
    if annotation is None:
        return AnnotationCategory.UNKNOWN
    return ANNOTATION_CATEGORIES.get(annotation, AnnotationCategory.UNKNOWN)


def required_parameters(function: FunctionInfo) -> tuple[str, ...]:
    """Parameters a caller must supply for this target to run.

    A parameter is required when it has no default, is not ``*args``/
    ``**kwargs``, and is not the implicit receiver of a method. The receiver rule
    is the same one structural risk scoring uses, so both views of a target agree
    about what counts as user input.
    """
    parameters = list(function.parameters)
    if (
        function.kind in METHOD_KINDS
        and parameters
        and parameters[0].name in RECEIVER_PARAMETER_NAMES
    ):
        parameters = parameters[1:]
    return tuple(
        parameter.name
        for parameter in parameters
        if not parameter.has_default and parameter.kind not in _OMITTABLE_PARAMETER_KINDS
    )


def reference_constant_kinds(semantics: TargetSemantics) -> dict[str, ConstantKind]:
    """Index the literal constant kind of every simple comparison operand.

    Boundary candidates store a reference as source text only, so materializing
    "just below 10" needs to know that ``10`` is an integer literal rather than a
    name. This index is built from the comparisons already mined for the target;
    no source is re-read or re-parsed.
    """
    kinds: dict[str, ConstantKind] = {}
    for comparison in semantics.comparisons:
        for operand in (comparison.left, comparison.right):
            if operand.constant_kind is not None:
                kinds.setdefault(operand.text, operand.constant_kind)
    return kinds


def select_target_actions(
    relative_path: Path,
    function: FunctionInfo,
    semantics: TargetSemantics,
    boundaries: TargetBoundaries | None,
) -> TargetActions:
    """Select every applicable action for one target, deduplicated and ordered."""
    constant_kinds = reference_constant_kinds(semantics)
    boundaries_by_parameter: dict[str, list[BoundaryCandidate]] = {}
    if boundaries is not None:
        for candidate in boundaries.candidates:
            boundaries_by_parameter.setdefault(candidate.parameter, []).append(candidate)

    actions: dict[tuple[str, SemanticActionKind, str | None], SemanticAction] = {}
    for parameter in function.parameters:
        category = classify_annotation(parameter.annotation)
        for candidate in boundaries_by_parameter.get(parameter.name, ()):
            kind = BOUNDARY_ACTIONS[candidate.kind]
            key = (parameter.name, kind, candidate.reference)
            if key in actions:
                continue
            actions[key] = _boundary_action(
                relative_path, function, parameter, category, candidate, kind, constant_kinds
            )

        if parameter.kind in SINGLE_VALUE_PARAMETER_KINDS:
            for kind in ANNOTATION_ACTIONS.get(category, ()):
                key = (parameter.name, kind, None)
                if key in actions:
                    continue
                actions[key] = _annotation_action(
                    relative_path, function, parameter, category, kind
                )

    return TargetActions(
        relative_path=relative_path,
        target=function.qualified_name,
        lineno=function.lineno,
        signature=function.signature,
        required_parameters=required_parameters(function),
        actions=tuple(sorted(actions.values(), key=_sort_key)),
    )


def select_module_actions(
    module: ModuleAnalysis, semantics: ModuleSemantics
) -> tuple[TargetActions, ...]:
    """Select actions for every target of one module, in source order.

    Targets with no applicable action are omitted rather than reported as empty,
    so a repository's action list contains only targets EdgeHunter can say
    something about.
    """
    semantics_by_name = {target.qualified_name: target for target in semantics.targets}
    boundaries_by_name = {entry.qualified_name: entry for entry in semantics.boundaries}

    results: list[TargetActions] = []
    for function in module.functions:
        target = semantics_by_name.get(function.qualified_name)
        if target is None:
            continue
        selected = select_target_actions(
            module.relative_path,
            function,
            target,
            boundaries_by_name.get(function.qualified_name),
        )
        if selected.actions:
            results.append(selected)
    return tuple(results)


def _boundary_action(
    relative_path: Path,
    function: FunctionInfo,
    parameter: ParameterInfo,
    category: AnnotationCategory,
    candidate: BoundaryCandidate,
    kind: SemanticActionKind,
    constant_kinds: dict[str, ConstantKind],
) -> SemanticAction:
    """Build one action derived from a mined boundary candidate."""
    reference = candidate.reference
    if reference is None:
        outcome = materialize_generic_numeric(candidate.kind, parameter.annotation)
    else:
        outcome = materialize_relational(kind, reference, constant_kinds.get(reference))
    return SemanticAction(
        relative_path=relative_path,
        target=function.qualified_name,
        lineno=function.lineno,
        signature=function.signature,
        parameter=parameter.name,
        kind=kind,
        origin=ActionOrigin.BOUNDARY,
        status=outcome.status,
        annotation_category=category,
        boundary_kind=candidate.kind,
        reference=reference,
        value_text=outcome.value_text,
        symbolic_text=outcome.symbolic_text,
        literal_kind=outcome.literal_kind,
        reason=candidate.reason,
        materialization_reason=outcome.reason,
        evidence=tuple(
            ActionEvidence(relative_path=relative_path, lineno=line, detail=candidate.reason)
            for line in candidate.evidence
        ),
    )


def _annotation_action(
    relative_path: Path,
    function: FunctionInfo,
    parameter: ParameterInfo,
    category: AnnotationCategory,
    kind: SemanticActionKind,
) -> SemanticAction:
    """Build one action derived only from the parameter's annotation."""
    outcome = materialize_collection(kind, category)
    return SemanticAction(
        relative_path=relative_path,
        target=function.qualified_name,
        lineno=function.lineno,
        signature=function.signature,
        parameter=parameter.name,
        kind=kind,
        origin=ActionOrigin.ANNOTATION,
        status=outcome.status,
        annotation_category=category,
        boundary_kind=None,
        reference=None,
        value_text=outcome.value_text,
        symbolic_text=outcome.symbolic_text,
        literal_kind=outcome.literal_kind,
        reason=f"parameter annotated {parameter.annotation}",
        materialization_reason=outcome.reason,
        evidence=(),
    )


def _sort_key(action: SemanticAction) -> tuple[str, int, str]:
    """Deterministic ordering: parameter, then vocabulary order, then reference."""
    return (action.parameter, ACTION_KIND_ORDER[action.kind], action.reference or "")
