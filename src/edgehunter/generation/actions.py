"""Selecting the constrained actions that apply to each parameter of a target.

Two independent sources justify an action, and the difference is recorded on
every action:

* a **boundary** candidate mined from the target's own comparisons. Each
  :class:`~edgehunter.analysis.models.BoundaryKind` maps to exactly one action,
  so the mapping is total and no candidate is silently dropped.
* the parameter's **annotation shape**. A recognized container justifies an
  empty, a singleton, and (for an ordered container) a repeated element; a
  nullable shape justifies ``SET_NONE``.

Applicability is decided from
:class:`~edgehunter.scanner.annotations.StaticAnnotation`, never from annotation
text, so subscripted generics and ``int | None`` are handled by the one canonical
annotation interpreter. An unrecognized shape contributes no annotation-driven
action at all, and a parameter with no annotation at all is not excluded from the
phase: it still receives actions derived from its own comparisons.
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
    Materialization,
    is_string_action,
    materialize_bool,
    materialize_collection,
    materialize_generic_numeric,
    materialize_none,
    materialize_relational,
    materialize_string,
)
from edgehunter.generation.models import (
    ACTION_KIND_ORDER,
    ActionEvidence,
    ActionOrigin,
    SemanticAction,
    SemanticActionKind,
    TargetActions,
)
from edgehunter.scanner.annotations import AnnotationKind, StaticAnnotation
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

# Annotation-driven actions per recognized shape. A repeated element is offered
# only where order makes it meaningful, so a set does not get one.
#
# `BOOL` and `STR` are here because no boundary candidate can reach them: Phase 2
# mines relational boundaries for a parameter compared against something, and its
# generic numeric candidates are restricted to `int`/`float` shapes. A `bool` or
# `str` parameter was therefore given no action at all, which produced no
# candidate case and so no invocation plan — the parameter was silently
# unreachable rather than explicitly refused. These entries close that gap using
# the recognized annotation shape, which is the same evidence source the container
# entries already used.
ANNOTATION_ACTIONS: Final[dict[AnnotationKind, tuple[SemanticActionKind, ...]]] = {
    AnnotationKind.LIST: (
        SemanticActionKind.EMPTY_COLLECTION,
        SemanticActionKind.SINGLETON_COLLECTION,
        SemanticActionKind.DUPLICATE_ELEMENT_COLLECTION,
    ),
    AnnotationKind.TUPLE: (
        SemanticActionKind.EMPTY_COLLECTION,
        SemanticActionKind.SINGLETON_COLLECTION,
        SemanticActionKind.DUPLICATE_ELEMENT_COLLECTION,
    ),
    AnnotationKind.SET: (
        SemanticActionKind.EMPTY_COLLECTION,
        SemanticActionKind.SINGLETON_COLLECTION,
    ),
    AnnotationKind.DICT: (
        SemanticActionKind.EMPTY_COLLECTION,
        SemanticActionKind.SINGLETON_COLLECTION,
    ),
    AnnotationKind.BOOL: (
        SemanticActionKind.SET_FALSE,
        SemanticActionKind.SET_TRUE,
    ),
    AnnotationKind.STR: (
        SemanticActionKind.EMPTY_STRING,
        SemanticActionKind.WHITESPACE_STRING,
        SemanticActionKind.UNICODE_STRING,
        SemanticActionKind.NULL_CHARACTER_STRING,
        SemanticActionKind.LONG_STRING,
    ),
}

_OMITTABLE_PARAMETER_KINDS: Final[frozenset[ParameterKind]] = frozenset(
    {ParameterKind.VAR_POSITIONAL, ParameterKind.VAR_KEYWORD}
)

# The two boolean action kinds, named locally so the dispatch in
# `_annotation_action` reads as a shape question rather than a set lookup.
_BOOL_ACTIONS: Final[frozenset[SemanticActionKind]] = frozenset(
    {SemanticActionKind.SET_FALSE, SemanticActionKind.SET_TRUE}
)


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


def receiver_parameter(function: FunctionInfo) -> ParameterInfo | None:
    """The implicit ``self``/``cls`` receiver of a method, when there is one."""
    parameters = function.parameters
    if (
        function.kind in METHOD_KINDS
        and parameters
        and parameters[0].name in RECEIVER_PARAMETER_NAMES
    ):
        return parameters[0]
    return None


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
        shape = parameter.shape
        for candidate in boundaries_by_parameter.get(parameter.name, ()):
            kind = BOUNDARY_ACTIONS[candidate.kind]
            key = (parameter.name, kind, candidate.reference)
            if key in actions:
                continue
            actions[key] = _boundary_action(
                relative_path, function, parameter, candidate, kind, constant_kinds
            )

        if parameter.kind in SINGLE_VALUE_PARAMETER_KINDS:
            for kind in _annotation_action_kinds(shape):
                key = (parameter.name, kind, None)
                if key in actions:
                    continue
                actions[key] = _annotation_action(
                    relative_path, function, parameter, kind, function.lineno
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


def _annotation_action_kinds(shape: StaticAnnotation) -> tuple[SemanticActionKind, ...]:
    """Annotation-driven actions for one shape, in vocabulary order."""
    kinds = list(ANNOTATION_ACTIONS.get(shape.kind, ()))
    if shape.is_nullable:
        kinds.append(SemanticActionKind.SET_NONE)
    return tuple(kinds)


def _boundary_action(
    relative_path: Path,
    function: FunctionInfo,
    parameter: ParameterInfo,
    candidate: BoundaryCandidate,
    kind: SemanticActionKind,
    constant_kinds: dict[str, ConstantKind],
) -> SemanticAction:
    """Build one action derived from a mined boundary candidate."""
    reference = candidate.reference
    if reference is None:
        outcome = materialize_generic_numeric(candidate.kind, parameter.shape)
    else:
        outcome = materialize_relational(kind, reference, constant_kinds.get(reference))
    return _action(
        relative_path=relative_path,
        function=function,
        parameter=parameter,
        kind=kind,
        origin=ActionOrigin.BOUNDARY,
        boundary_kind=candidate.kind,
        reference=reference,
        outcome=outcome,
        evidence=tuple(
            ActionEvidence(relative_path=relative_path, lineno=line, detail=candidate.reason)
            for line in candidate.evidence
        ),
        reason=candidate.reason,
    )


def _annotation_action(
    relative_path: Path,
    function: FunctionInfo,
    parameter: ParameterInfo,
    kind: SemanticActionKind,
    lineno: int,
) -> SemanticAction:
    """Build one action derived only from the parameter's annotation shape."""
    if kind is SemanticActionKind.SET_NONE:
        outcome = materialize_none(parameter.shape)
    elif kind in _BOOL_ACTIONS:
        outcome = materialize_bool(kind, parameter.shape)
    elif is_string_action(kind):
        outcome = materialize_string(kind, parameter.shape)
    else:
        outcome = materialize_collection(kind, parameter.shape)
    return _action(
        relative_path=relative_path,
        function=function,
        parameter=parameter,
        kind=kind,
        origin=ActionOrigin.ANNOTATION,
        boundary_kind=None,
        reference=None,
        outcome=outcome,
        evidence=(),
        reason=f"parameter annotated {parameter.shape.description}",
    )


def _action(
    *,
    relative_path: Path,
    function: FunctionInfo,
    parameter: ParameterInfo,
    kind: SemanticActionKind,
    origin: ActionOrigin,
    boundary_kind: BoundaryKind | None,
    reference: str | None,
    outcome: Materialization,
    evidence: tuple[ActionEvidence, ...],
    reason: str,
) -> SemanticAction:
    """Assemble one action from its justification and its materialization."""
    return SemanticAction(
        relative_path=relative_path,
        target=function.qualified_name,
        lineno=function.lineno,
        signature=function.signature,
        parameter=parameter.name,
        kind=kind,
        origin=origin,
        status=outcome.status,
        annotation=parameter.shape,
        boundary_kind=boundary_kind,
        reference=reference,
        value_text=outcome.value_text,
        symbolic_text=outcome.symbolic_text,
        literal_kind=outcome.literal_kind,
        reason=reason,
        materialization_reason=outcome.reason,
        evidence=evidence,
    )


def _sort_key(action: SemanticAction) -> tuple[str, int, str]:
    """Deterministic ordering: parameter, then vocabulary order, then reference."""
    return (action.parameter, ACTION_KIND_ORDER[action.kind], action.reference or "")
