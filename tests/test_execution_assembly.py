"""Tests for argument assembly, including the omitted-default trap."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import pytest

from edgehunter.execution import AssemblyFailure, action_identifier, assemble_invocation
from edgehunter.generation.invocation import InvocationPlan, InvocationStatus
from edgehunter.pipeline import analyze_repository
from edgehunter.scanner import FunctionInfo


def _scan(tmp_path: Path, source: str) -> tuple[list[InvocationPlan], dict[str, FunctionInfo]]:
    (tmp_path / "module.py").write_text(source, encoding="utf-8")
    scan = analyze_repository(tmp_path)
    functions = {
        function.qualified_name: function
        for module in scan.semantics.structure.modules
        for function in module.functions
    }
    return list(scan.planning.plans), functions


def _plan(
    tmp_path: Path,
    source: str,
    target: str,
    parameter: str,
    kind: str = "set_zero",
) -> tuple[InvocationPlan, FunctionInfo]:
    """Return the plan whose adversarial action targets ``parameter`` and ``kind``."""
    plans, functions = _scan(tmp_path, source)
    for candidate in plans:
        if (
            candidate.target == target
            and candidate.primary_action.parameter == parameter
            and candidate.primary_action.kind.value == kind
            and candidate.status is InvocationStatus.COMPLETE_FUNCTION
        ):
            return candidate, functions[target]
    raise AssertionError(f"no complete plan for {target}.{parameter}/{kind}")


def test_single_argument_is_passed_positionally(tmp_path: Path) -> None:
    plan, function = _plan(tmp_path, "def f(x: int):\n    return x\n", "f", "x")

    arguments = assemble_invocation(plan, function)

    assert [value.to_python() for value in arguments.args] == [0]
    assert arguments.kwargs == ()


def test_two_positional_arguments_keep_their_order(tmp_path: Path) -> None:
    plan, function = _plan(tmp_path, "def f(x: int, y: str):\n    return x, y\n", "f", "x")

    arguments = assemble_invocation(plan, function)

    assert [value.to_python() for value in arguments.args] == [0, ""]


def test_positional_only_parameter_is_passed_positionally(tmp_path: Path) -> None:
    plan, function = _plan(tmp_path, "def f(x: int, /, y: int):\n    return x + y\n", "f", "x")

    arguments = assemble_invocation(plan, function)

    assert [value.to_python() for value in arguments.args] == [0, 0]
    assert arguments.kwargs == ()


def test_keyword_only_parameter_is_passed_by_keyword(tmp_path: Path) -> None:
    plan, function = _plan(tmp_path, "def f(x: int, *, mode: str):\n    return x, mode\n", "f", "x")

    arguments = assemble_invocation(plan, function)

    assert [value.to_python() for value in arguments.args] == [0]
    assert {name: value.to_python() for name, value in arguments.kwargs} == {"mode": ""}


def test_omitted_default_does_not_shift_the_next_positional(tmp_path: Path) -> None:
    """`f(a, c_value)` would bind c_value to b, so `c` must be passed by keyword."""
    source = "def f(a: int, b: int = 10, c: int = 20):\n    return a + b + c\n"
    plan, function = _plan(tmp_path, source, "f", "c")

    arguments = assemble_invocation(plan, function)

    positional = [value.to_python() for value in arguments.args]
    keyword = {name: value.to_python() for name, value in arguments.kwargs}

    # `a` precedes the omitted `b`, so it stays positional and `b` is untouched.
    assert len(positional) == 1
    assert positional[0] == 0
    assert "b" not in keyword
    assert keyword["c"] == 0


def test_supplied_value_is_never_bound_to_an_omitted_default(tmp_path: Path) -> None:
    """For every plan of a three-parameter signature, no value may land on `b`."""
    plans, functions = _scan(
        tmp_path,
        "def f(a: int, b: int = 10, c: int = 20):\n    return a + b + c\n",
    )
    function = functions["f"]

    checked = 0
    for plan in plans:
        if plan.status is not InvocationStatus.COMPLETE_FUNCTION:
            continue
        arguments = assemble_invocation(plan, function)
        keyword_names = {name for name, _ in arguments.kwargs}
        assert "b" not in keyword_names or len(arguments.args) >= 2
        if plan.primary_action.parameter == "c":
            assert keyword_names == {"c"}
        checked += 1
    assert checked > 0


def test_variadic_parameters_receive_nothing(tmp_path: Path) -> None:
    plan, function = _plan(tmp_path, "def f(x: int, *items, **options):\n    return x\n", "f", "x")

    arguments = assemble_invocation(plan, function)

    assert [value.to_python() for value in arguments.args] == [0]
    assert arguments.kwargs == ()


def test_unrepresentable_value_is_refused_not_invented(tmp_path: Path) -> None:
    plan, function = _plan(tmp_path, "def f(x: int):\n    return x\n", "f", "x")
    hostile = replace(
        plan,
        assignments=(
            replace(
                plan.assignments[0],
                value_text="__import__('os').system('echo pwned')",
            ),
        ),
    )

    with pytest.raises(AssemblyFailure):
        assemble_invocation(hostile, function)


def test_unresolved_value_is_refused(tmp_path: Path) -> None:
    plan, function = _plan(tmp_path, "def f(x: int):\n    return x\n", "f", "x")
    unresolved = replace(
        plan,
        assignments=(replace(plan.assignments[0], value_text=None),),
    )

    with pytest.raises(AssemblyFailure):
        assemble_invocation(unresolved, function)


def test_incomplete_plan_cannot_be_assembled(tmp_path: Path) -> None:
    plan, function = _plan(tmp_path, "def f(x: int):\n    return x\n", "f", "x")
    partial = replace(plan, status=InvocationStatus.PARTIAL_ARGUMENTS)

    with pytest.raises(AssemblyFailure):
        assemble_invocation(partial, function)


def test_special_float_values_survive_assembly(tmp_path: Path) -> None:
    plan, function = _plan(tmp_path, "def f(x: float):\n    return x\n", "f", "x", kind="use_nan")

    arguments = assemble_invocation(plan, function)

    assert [value.scalar for value in arguments.args] == ["nan"]
    assert math.isnan(arguments.args[0].to_python())  # type: ignore[arg-type]


def test_action_identifier_is_stable(tmp_path: Path) -> None:
    plan, _ = _plan(tmp_path, "def f(x: int):\n    return x\n", "f", "x")

    assert action_identifier(plan) == action_identifier(plan)
    assert "f:x:set_" in action_identifier(plan)
