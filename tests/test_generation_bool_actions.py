"""Tests for ``bool`` semantic actions.

Phase 7A closed a real coverage gap: a recognized ``bool`` parameter used to
produce no action, therefore no candidate case, therefore no invocation plan,
and not even a strategy plan. These tests pin the observable behaviour that
closed it -- the materialization, the provenance, the planning, and the
boundaries that must *not* move (a ``bool`` is never numeric, and an untyped
parameter never acquires boolean actions without evidence).
"""

from __future__ import annotations

from pathlib import Path

from edgehunter.analysis.models import BoundaryKind
from edgehunter.execution.codec import ValueKind, recognize_literal
from edgehunter.generation import (
    ANNOTATION_ACTIONS,
    BOUNDARY_ACTIONS,
    ActionOrigin,
    AssignmentOrigin,
    InvocationStatus,
    LiteralKind,
    MaterializationStatus,
    SemanticAction,
    SemanticActionKind,
    StrategyKind,
)
from edgehunter.generation.materialize import (
    LONG_STRING_LENGTH,
    materialize_bool,
    materialize_string,
)
from edgehunter.pipeline import RepositoryScan, analyze_repository
from edgehunter.scanner import AnnotationKind
from edgehunter.scanner.annotations import StaticAnnotation

BOOL_SOURCE = """def invert(flag: bool) -> bool:
    return not flag


def maybe(flag: bool | None):
    if flag is None:
        return "unset"
    return "set"


def untyped(flag):
    return flag
"""


def _scan(tmp_path: Path, source: str = BOOL_SOURCE) -> RepositoryScan:
    (tmp_path / "service.py").write_text(source, encoding="utf-8")
    return analyze_repository(tmp_path)


def _actions(scan: RepositoryScan, target: str) -> tuple[SemanticAction, ...]:
    entry = next(e for e in scan.generation.action_sets if e.target == target)
    return entry.actions


def _action(scan: RepositoryScan, target: str, kind: SemanticActionKind) -> SemanticAction:
    return next(a for a in _actions(scan, target) if a.kind is kind)


# --- 1. the annotation is recognized -----------------------------------------


def test_bool_annotation_is_recognized(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    shapes = {
        item.parameter: item.description
        for item in scan.annotations.parameters
        if item.target == "invert"
    }
    assert shapes == {"flag": "BOOL"}


# --- 2/3. both actions are generated ----------------------------------------


def test_set_false_and_set_true_are_generated(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    kinds = [a.kind for a in _actions(scan, "invert")]
    assert kinds == [SemanticActionKind.SET_FALSE, SemanticActionKind.SET_TRUE]


# --- 4. the values are actual booleans --------------------------------------


def test_boolean_values_are_real_booleans_not_integers(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    false = _action(scan, "invert", SemanticActionKind.SET_FALSE)
    true = _action(scan, "invert", SemanticActionKind.SET_TRUE)

    assert false.value_text == "False"
    assert true.value_text == "True"
    assert false.literal_kind is LiteralKind.BOOLEAN
    assert true.literal_kind is LiteralKind.BOOLEAN

    # The stored text is a Python boolean literal, and the protocol preserves
    # that: `type(True) is bool`, so it can never be read back as an integer.
    decoded_false = recognize_literal(str(false.value_text))
    decoded_true = recognize_literal(str(true.value_text))
    assert decoded_false.kind is ValueKind.BOOLEAN
    assert decoded_true.kind is ValueKind.BOOLEAN
    assert decoded_false.to_python() is False
    assert decoded_true.to_python() is True


# --- 5. bool never receives numeric generic actions -------------------------


def test_bool_generates_no_numeric_generic_actions(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    numeric = {
        SemanticActionKind.SET_ZERO,
        SemanticActionKind.SET_ONE,
        SemanticActionKind.SET_NEGATIVE_ONE,
        SemanticActionKind.SET_NEGATIVE_ZERO,
        SemanticActionKind.USE_NAN,
        SemanticActionKind.USE_POSITIVE_INFINITY,
        SemanticActionKind.USE_NEGATIVE_INFINITY,
    }
    assert not numeric & {a.kind for a in _actions(scan, "invert")}


def test_bool_is_not_a_numeric_shape_for_boundary_mining(tmp_path: Path) -> None:
    """Phase 2's generic numeric rule must still exclude ``bool`` (D-036)."""
    scan = _scan(
        tmp_path,
        "def cmp(flag: bool, limit: int = 3):\n    if flag:\n        return 1\n    return 0\n",
    )

    entries = [e for e in scan.semantics.boundaries if e.qualified_name == "cmp"]
    assert entries, "the target should still be mined for boundaries"
    for entry in entries:
        assert [c for c in entry.candidates if c.parameter == "flag"] == []


# --- 6. no duplicate equivalent action --------------------------------------


def test_boolean_actions_are_not_duplicated(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    kinds = [a.kind for a in _actions(scan, "invert")]
    assert len(kinds) == len(set(kinds))


def test_a_literal_comparison_keeps_both_derivations_distinguishable(tmp_path: Path) -> None:
    """Two derivations of one value stay two actions, not one merged action.

    ``flag == True`` mines an ``AT_BOUNDARY`` action (origin ``BOUNDARY``) and the
    ``bool`` annotation proposes ``SET_TRUE`` (origin ``ANNOTATION``). They write
    down the same value, but they are different semantic actions with different
    provenance, so the semantic key ``(parameter, kind, reference)`` keeps both.
    Collapsing them by value is exactly what must not happen: ``False`` and ``0``
    compare equal in Python but are different typed values, and ``NaN`` does not
    even compare equal to itself. The strategy table, which dedupes by rendered
    text, is the layer that legitimately shows one entry per distinct value.
    """
    scan = _scan(
        tmp_path,
        "def check(flag: bool):\n    if flag == True:\n        return 1\n    return 0\n",
    )

    actions = _actions(scan, "check")
    by_kind = {a.kind: a for a in actions}
    assert by_kind[SemanticActionKind.AT_BOUNDARY].origin is ActionOrigin.BOUNDARY
    assert by_kind[SemanticActionKind.SET_TRUE].origin is ActionOrigin.ANNOTATION
    assert by_kind[SemanticActionKind.AT_BOUNDARY].value_text == "True"
    assert by_kind[SemanticActionKind.SET_TRUE].value_text == "True"

    # The strategy table is where the same written value collapses to one row.
    plan = next(e for e in scan.planning.strategies if e.target == "check")
    strategy = next(p for p in plan.parameters if p.parameter == "flag")
    texts = [v.text for v in strategy.values]
    assert texts == ["False", "True"]
    assert len(texts) == len(set(texts))


# --- 7. deterministic ordering ----------------------------------------------


def test_boolean_actions_are_deterministically_ordered(tmp_path: Path) -> None:
    first = [a.kind for a in _actions(_scan(tmp_path), "invert")]
    second = [a.kind for a in _actions(_scan(tmp_path), "invert")]

    assert first == second == [SemanticActionKind.SET_FALSE, SemanticActionKind.SET_TRUE]


# --- 8. provenance ----------------------------------------------------------


def test_every_boolean_action_carries_annotation_provenance(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    for action in _actions(scan, "invert"):
        assert action.origin is ActionOrigin.ANNOTATION
        assert action.boundary_kind is None
        assert action.reference is None
        assert action.evidence == ()
        assert action.reason == "parameter annotated BOOL"
        assert action.annotation.kind is AnnotationKind.BOOL
        assert action.materialization_reason


# --- 9. candidate cases -----------------------------------------------------


def test_both_boolean_actions_produce_candidate_cases(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    cases = [c for c in scan.generation.cases if c.target == "invert"]
    assert [c.action.kind for c in cases] == [
        SemanticActionKind.SET_FALSE,
        SemanticActionKind.SET_TRUE,
    ]
    assert all(c.completeness.value == "complete" for c in cases)
    assert all(c.missing_parameters == () for c in cases)
    assert scan.generation.case_count == scan.generation.action_count


# --- 10. complete invocation plans ------------------------------------------


def test_one_argument_bool_function_produces_complete_plans(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    plans = [p for p in scan.planning.plans if p.target == "invert"]
    assert len(plans) == 2
    for plan in plans:
        assert plan.status is InvocationStatus.COMPLETE_FUNCTION
        assert plan.requires_receiver is False
        assert plan.missing_parameters == ()
        assert [(a.parameter, a.value_text) for a in plan.assignments] == [
            ("flag", str(plan.primary_action.value_text))
        ]
        assert plan.assignments[0].origin is AssignmentOrigin.ADVERSARIAL_ACTION


def test_a_bool_baseline_fills_a_string_argument(tmp_path: Path) -> None:
    """Spec example: the bool baseline is ``False`` and the string baseline ``""``."""
    scan = _scan(tmp_path, "def f(flag: bool, name: str):\n    return flag, name\n")

    plan = next(
        p
        for p in scan.planning.plans
        if p.primary_action.parameter == "flag"
        and p.primary_action.kind is SemanticActionKind.SET_TRUE
    )
    assert plan.status is InvocationStatus.COMPLETE_FUNCTION
    assert [(a.parameter, a.value_text, a.origin) for a in plan.assignments] == [
        ("flag", "True", AssignmentOrigin.ADVERSARIAL_ACTION),
        ("name", '""', AssignmentOrigin.STATIC_TYPE_BASELINE),
    ]


# --- 11. strategy plans -----------------------------------------------------


def test_bool_strategy_plan_contains_exactly_the_supported_values(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    plan = next(e for e in scan.planning.strategies if e.target == "invert")
    strategy = next(p for p in plan.parameters if p.parameter == "flag")

    assert strategy.kind is StrategyKind.SAMPLED_FROM
    assert [(v.descriptor, v.text) for v in strategy.values] == [
        ("false", "False"),
        ("true", "True"),
    ]


# --- 12. nullable bool keeps SET_NONE ---------------------------------------


def test_nullable_bool_keeps_the_existing_set_none_behaviour(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    # ``SET_NONE`` sorts before the boolean actions because ACTION_KIND_ORDER
    # places the annotation-driven null action with the other single values.
    kinds = [a.kind for a in _actions(scan, "maybe")]
    assert kinds == [
        SemanticActionKind.SET_NONE,
        SemanticActionKind.SET_FALSE,
        SemanticActionKind.SET_TRUE,
    ]
    none_action = _action(scan, "maybe", SemanticActionKind.SET_NONE)
    assert none_action.value_text == "None"
    assert none_action.literal_kind is LiteralKind.NOTHING


def test_nullable_bool_strategy_still_includes_none(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    plan = next(e for e in scan.planning.strategies if e.target == "maybe")
    strategy = next(p for p in plan.parameters if p.parameter == "flag")
    assert [v.text for v in strategy.values] == ["False", "True", "None"]


# --- 13. untyped parameters stay conservative -------------------------------


def test_untyped_parameter_gains_no_boolean_action(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    assert not [e for e in scan.generation.action_sets if e.target == "untyped"]
    assert not [c for c in scan.generation.cases if c.target == "untyped"]
    assert not [p for p in scan.planning.plans if p.target == "untyped"]


def test_a_parameter_named_like_a_boolean_is_not_treated_as_one(tmp_path: Path) -> None:
    """A name is not type evidence: ``flag`` without an annotation is not a bool."""
    scan = _scan(
        tmp_path,
        "def f(flag, text, message, path, is_enabled):\n"
        "    return flag, text, message, path, is_enabled\n",
    )

    assert scan.generation.action_count == 0
    assert scan.planning.plan_count == 0


# --- applicability table ----------------------------------------------------


def test_bool_is_mapped_in_the_annotation_action_table() -> None:
    assert ANNOTATION_ACTIONS[AnnotationKind.BOOL] == (
        SemanticActionKind.SET_FALSE,
        SemanticActionKind.SET_TRUE,
    )


def test_boolean_actions_have_no_boundary_counterpart() -> None:
    """D-029's total injective BoundaryKind -> action mapping must not grow."""
    assert not set(BOUNDARY_ACTIONS.values()) & {
        SemanticActionKind.SET_FALSE,
        SemanticActionKind.SET_TRUE,
    }
    assert len(set(BOUNDARY_ACTIONS.values())) == len(BOUNDARY_ACTIONS)
    assert set(BOUNDARY_ACTIONS) == set(BoundaryKind)


# --- refusal: a non-bool shape is never coerced -----------------------------


def test_materialize_bool_refuses_a_non_boolean_shape() -> None:
    outcome = materialize_bool(
        SemanticActionKind.SET_TRUE, StaticAnnotation(kind=AnnotationKind.INT)
    )
    assert outcome.status is MaterializationStatus.UNSUPPORTED
    assert "would change its type" in outcome.reason


def test_materialize_string_refuses_a_non_string_shape() -> None:
    outcome = materialize_string(
        SemanticActionKind.EMPTY_STRING, StaticAnnotation(kind=AnnotationKind.BOOL)
    )
    assert outcome.status is MaterializationStatus.UNSUPPORTED
    assert "would change its type" in outcome.reason


def test_long_string_length_bound_is_a_module_constant() -> None:
    """Neither the target nor the CLI can influence the long-string length."""
    assert LONG_STRING_LENGTH == 1024
