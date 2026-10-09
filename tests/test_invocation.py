"""Tests for typed invocation planning with trustworthy filler values."""

from __future__ import annotations

from pathlib import Path

from edgehunter.generation import (
    ArgumentAssignment,
    ArgumentPlacement,
    AssignmentOrigin,
    InvocationPlan,
    InvocationStatus,
)
from edgehunter.pipeline import RepositoryScan, analyze_repository

SERVICE = """class User:
    pass


def add(a: int, b: int):
    return a + b


def basic(x: int, name: str):
    return x, name


def with_unknown(x: int, user: User):
    return x, user


def with_default(x: int, limit: int = 10):
    return x, limit


def with_symbolic(x: int, limit: int):
    if x <= limit:
        return x
    return limit


def with_attribute(x: int):
    return x


class Holder:
    def __init__(self):
        self.limit = 5

    def method(self, x: int):
        return x

    def attribute(self, x: int):
        if x <= self.limit:
            return x
        return 0

    @classmethod
    def class_method(cls, x: int):
        return x


def positional_only(x: int, /, y: str):
    return x, y


def keyword_only(x: int, *, mode: str):
    return x, mode


def variadic(x: int, *items, **options):
    return x, items, options
"""


def _scan(tmp_path: Path, source: str = SERVICE) -> RepositoryScan:
    (tmp_path / "service.py").write_text(source, encoding="utf-8")
    return analyze_repository(tmp_path)


def _plans(scan: RepositoryScan, target: str) -> list[InvocationPlan]:
    return [plan for plan in scan.planning.plans if plan.target == target]


def _plan_for(
    scan: RepositoryScan, target: str, kind: str, parameter: str | None = None
) -> InvocationPlan:
    matches = [
        plan
        for plan in _plans(scan, target)
        if plan.primary_action.kind.value == kind
        and (parameter is None or plan.primary_action.parameter == parameter)
    ]
    assert matches, f"no {kind} plan for {target}"
    return matches[0]


def _assignment(plan: InvocationPlan, name: str) -> ArgumentAssignment:
    return next(item for item in plan.assignments if item.parameter == name)


def test_baseline_completes_an_ordinary_function(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    plan = _plan_for(scan, "basic", "set_negative_one")

    assert plan.status is InvocationStatus.COMPLETE_FUNCTION
    assert _assignment(plan, "x").value_text == "-1"
    assert _assignment(plan, "x").origin is AssignmentOrigin.ADVERSARIAL_ACTION
    assert _assignment(plan, "name").value_text == '""'
    assert _assignment(plan, "name").origin is AssignmentOrigin.STATIC_TYPE_BASELINE
    assert plan.missing_parameters == ()


def test_unknown_argument_keeps_the_plan_partial(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    plan = _plan_for(scan, "with_unknown", "set_zero")

    assert plan.status is InvocationStatus.PARTIAL_ARGUMENTS
    assert plan.missing_parameters == ("user",)
    assert "user" not in {item.parameter for item in plan.assignments}
    assert all("None" != item.value_text for item in plan.assignments)


def test_declared_default_satisfies_completeness_without_evaluation(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    plan = _plan_for(scan, "with_default", "set_zero", "x")
    limit = _assignment(plan, "limit")

    assert plan.status is InvocationStatus.COMPLETE_FUNCTION
    assert limit.origin is AssignmentOrigin.DECLARED_DEFAULT
    assert limit.placement is ArgumentPlacement.OMITTED
    assert limit.value_text is None
    assert "never evaluated" in limit.reason


def test_method_requires_a_receiver(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    plan = _plan_for(scan, "Holder.method", "set_zero")

    assert plan.status is InvocationStatus.NEEDS_RECEIVER
    assert plan.requires_receiver is True
    assert plan.receiver_name == "self"
    assert "self" not in {item.parameter for item in plan.assignments}


def test_classmethod_receiver_is_not_a_missing_argument(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    plan = _plan_for(scan, "Holder.class_method", "set_zero")

    assert plan.status is InvocationStatus.NEEDS_RECEIVER
    assert plan.receiver_name == "cls"
    assert plan.missing_parameters == ()


def test_symbolic_parameter_boundary_is_resolved_through_a_baseline(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    for kind, expected in (
        ("just_below_boundary", "-1"),
        ("at_boundary", "0"),
        ("just_above_boundary", "1"),
    ):
        plan = _plan_for(scan, "with_symbolic", kind)
        assert _assignment(plan, "x").value_text == expected, kind
        assert _assignment(plan, "limit").value_text == "0"
        assert _assignment(plan, "limit").origin is AssignmentOrigin.DEPENDENCY_BASELINE
        assert plan.status is InvocationStatus.COMPLETE_FUNCTION, kind


def test_symbolic_attribute_reference_is_never_resolved(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    plan = _plan_for(scan, "Holder.attribute", "just_above_boundary")

    assert plan.status is InvocationStatus.SYMBOLIC_DEPENDENCY
    assert _assignment(plan, "x").value_text is None
    assert plan.symbolic_dependencies == ("x",)
    assert plan.requires_receiver is True


def test_exactly_one_adversarial_assignment_per_plan(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    for plan in scan.planning.plans:
        assert len(plan.adversarial_assignments) == 1


def test_positional_only_parameter_is_marked(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    plan = _plan_for(scan, "positional_only", "set_zero")

    assert _assignment(plan, "x").placement is ArgumentPlacement.POSITIONAL_ONLY
    assert _assignment(plan, "y").placement is ArgumentPlacement.POSITIONAL
    assert [item.parameter for item in plan.positional_assignments] == ["x", "y"]


def test_keyword_only_parameter_is_marked(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    plan = _plan_for(scan, "keyword_only", "set_zero")

    assert _assignment(plan, "x").placement is ArgumentPlacement.POSITIONAL
    assert _assignment(plan, "mode").placement is ArgumentPlacement.KEYWORD
    assert [item.parameter for item in plan.keyword_assignments] == ["mode"]


def test_empty_variadics_do_not_make_a_plan_incomplete(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    plan = _plan_for(scan, "variadic", "set_zero")

    assert plan.status is InvocationStatus.COMPLETE_FUNCTION
    assert [item.parameter for item in plan.assignments] == ["x"]


def test_existing_test_literals_are_used_as_baselines(tmp_path: Path) -> None:
    scan = _scan(tmp_path)
    (tmp_path / "test_service.py").write_text(
        "def test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8"
    )
    scan = analyze_repository(tmp_path)

    plan = _plan_for(scan, "add", "set_zero")
    second = _assignment(plan, "b")

    assert second.value_text == "2"
    assert second.origin is AssignmentOrigin.EXISTING_TEST_BASELINE
    assert plan.status is InvocationStatus.COMPLETE_FUNCTION


def test_non_literal_argument_does_not_shift_the_literal_baseline(tmp_path: Path) -> None:
    """The literal keeps its real call position when an earlier argument is skipped.

    ``add(fixture, 2)`` writes ``2`` at position 1, so it belongs to ``b``. The
    pre-Phase-6B behaviour dropped ``fixture`` and returned ``("2",)``, which the
    planner then bound to ``a`` by declaration order — the literal moved one
    parameter to the left of where the test actually put it.
    """
    scan = _scan(tmp_path)
    (tmp_path / "test_service.py").write_text(
        "def test_add():\n    assert add(fixture, 2) == 3\n", encoding="utf-8"
    )
    scan = analyze_repository(tmp_path)

    plan = _plan_for(scan, "add", "set_zero")

    assert _assignment(plan, "a").origin is AssignmentOrigin.ADVERSARIAL_ACTION
    assert _assignment(plan, "b").origin is AssignmentOrigin.EXISTING_TEST_BASELINE
    assert _assignment(plan, "b").value_text == "2"


def test_literal_after_an_unknown_first_argument_is_never_bound_to_the_first(
    tmp_path: Path,
) -> None:
    """The first parameter is not filled from a literal written at position 1."""
    scan = _scan(tmp_path)
    (tmp_path / "test_service.py").write_text(
        "def test_add():\n    assert add(fixture, 2) == 3\n", encoding="utf-8"
    )
    scan = analyze_repository(tmp_path)

    plans = [
        plan
        for plan in scan.planning.plans
        if plan.target == "add" and plan.primary_action.parameter == "b"
    ]

    assert plans, "expected a plan whose adversarial parameter is b"
    for plan in plans:
        first = _assignment(plan, "a")
        # ``a`` was the unknown ``fixture``; it must not be quietly given the 2.
        assert first.value_text != "2"


def test_unresolved_expectation_never_supplies_a_baseline(tmp_path: Path) -> None:
    scan = _scan(tmp_path)
    (tmp_path / "test_service.py").write_text(
        "def test_other():\n    assert missing_helper(1, 2) == 3\n", encoding="utf-8"
    )
    scan = analyze_repository(tmp_path)

    plan = _plan_for(scan, "add", "set_zero")

    assert _assignment(plan, "b").origin is AssignmentOrigin.STATIC_TYPE_BASELINE


def test_nullable_argument_without_default_gets_a_non_null_baseline(
    tmp_path: Path,
) -> None:
    """``None`` is an adversarial choice, never an automatic filler."""
    scan = _scan(tmp_path, "def f(x: int, limit: int | None):\n    return x, limit\n")

    plan = _plan_for(scan, "f", "set_zero", "x")
    limit = _assignment(plan, "limit")

    assert limit.origin is AssignmentOrigin.STATIC_TYPE_BASELINE
    assert limit.value_text == "0"
    assert plan.status is InvocationStatus.COMPLETE_FUNCTION


def test_nullable_argument_with_default_is_omitted(tmp_path: Path) -> None:
    scan = _scan(tmp_path, "def f(x: int, limit: int | None = None):\n    return x, limit\n")

    plan = _plan_for(scan, "f", "set_zero", "x")

    assert _assignment(plan, "limit").origin is AssignmentOrigin.DECLARED_DEFAULT


def test_nullable_primary_action_sets_none(tmp_path: Path) -> None:
    scan = _scan(tmp_path, "def f(x: int | None):\n    return x\n")

    plan = _plan_for(scan, "f", "set_none")

    assert plan.status is InvocationStatus.COMPLETE_FUNCTION
    assert _assignment(plan, "x").value_text == "None"
    assert _assignment(plan, "x").origin is AssignmentOrigin.ADVERSARIAL_ACTION


def test_plans_are_ordered_deterministically(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    keys = [
        (
            plan.relative_path.as_posix(),
            plan.lineno,
            plan.target,
            plan.primary_action.parameter,
            plan.primary_action.kind.value,
            plan.primary_action.reference or "",
        )
        for plan in scan.planning.plans
    ]
    assert keys == sorted(keys)


def test_repeated_scans_produce_equal_plans(tmp_path: Path) -> None:
    _scan(tmp_path)

    assert analyze_repository(tmp_path).planning == analyze_repository(tmp_path).planning
