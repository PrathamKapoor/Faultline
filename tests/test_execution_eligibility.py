"""Tests for the execution eligibility gate.

Every refusal is asserted by reason and detail, not merely by a boolean: an
unsupported target must always say why it was skipped.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from edgehunter.execution import (
    EligibilityOutcome,
    IneligibilityReason,
    check_eligibility,
    check_relative_path,
)
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


def _complete(tmp_path: Path, source: str, target: str) -> tuple[InvocationPlan, FunctionInfo]:
    plans, functions = _scan(tmp_path, source)
    for candidate in plans:
        if candidate.target == target and candidate.status is InvocationStatus.COMPLETE_FUNCTION:
            return candidate, functions[target]
    raise AssertionError(f"no complete plan for {target}")


def test_ordinary_function_is_eligible(tmp_path: Path) -> None:
    plan, function = _complete(tmp_path, "def add(a: int, b: int):\n    return a + b\n", "add")

    verdict = check_eligibility(plan, function)

    assert verdict.outcome is EligibilityOutcome.ELIGIBLE
    assert verdict.reason is None
    assert verdict.detail


def test_partial_arguments_are_ineligible(tmp_path: Path) -> None:
    plans, functions = _scan(tmp_path, "def f(x: int, user: User):\n    return x, user\n")
    plan = next(item for item in plans if item.target == "f")

    verdict = check_eligibility(plan, functions["f"])

    assert verdict.outcome is EligibilityOutcome.INELIGIBLE
    assert verdict.reason is IneligibilityReason.PARTIAL_ARGUMENTS
    assert "user" in verdict.detail


def test_symbolic_dependency_is_ineligible(tmp_path: Path) -> None:
    source = (
        "class C:\n"
        "    def f(self, x: int):\n"
        "        if x <= self.limit:\n"
        "            return x\n"
        "        return 0\n"
    )
    plans, functions = _scan(tmp_path, source)
    plan = next(
        item
        for item in plans
        if item.target == "C.f" and item.status is InvocationStatus.SYMBOLIC_DEPENDENCY
    )

    verdict = check_eligibility(plan, functions["C.f"])

    assert verdict.outcome is EligibilityOutcome.INELIGIBLE
    assert verdict.reason is IneligibilityReason.SYMBOLIC_DEPENDENCY


def test_receiver_dependent_plan_is_ineligible(tmp_path: Path) -> None:
    source = "class C:\n    def f(self, x: int):\n        return x\n"
    plans, functions = _scan(tmp_path, source)
    plan = next(item for item in plans if item.target == "C.f")

    verdict = check_eligibility(plan, functions["C.f"])

    assert verdict.outcome is EligibilityOutcome.INELIGIBLE
    # The status check runs first, so a receiver requirement is named precisely.
    assert verdict.reason is IneligibilityReason.NEEDS_RECEIVER
    assert "receiver" in verdict.detail


def test_async_target_is_ineligible(tmp_path: Path) -> None:
    plans, functions = _scan(tmp_path, "async def f(x: int):\n    return x\n")
    plan = next(item for item in plans if item.target == "f")

    verdict = check_eligibility(plan, functions["f"])

    assert verdict.reason is IneligibilityReason.UNSUPPORTED_TARGET_KIND
    assert "async_function" in verdict.detail


def test_nested_target_is_ineligible(tmp_path: Path) -> None:
    source = "def outer():\n    def inner(x: int):\n        return x\n    return inner\n"
    plans, functions = _scan(tmp_path, source)
    plan = next(item for item in plans if item.target == "outer.inner")

    verdict = check_eligibility(plan, functions["outer.inner"])

    assert verdict.reason is IneligibilityReason.UNSUPPORTED_TARGET_KIND
    assert "nested_function" in verdict.detail


def test_decorated_target_is_ineligible(tmp_path: Path) -> None:
    plans, functions = _scan(
        tmp_path, "def logged(f):\n    return f\n\n\n@logged\ndef target(x: int):\n    return x\n"
    )
    plan = next(item for item in plans if item.target == "target")

    verdict = check_eligibility(plan, functions["target"])

    assert verdict.reason is IneligibilityReason.DECORATED_TARGET
    assert "logged" in verdict.detail


def test_unsupported_value_is_ineligible(tmp_path: Path) -> None:
    plan, function = _complete(tmp_path, "def f(x: int):\n    return x\n", "f")
    hostile = replace(
        plan,
        assignments=(replace(plan.assignments[0], value_text="__import__('os').system('x')"),),
    )

    verdict = check_eligibility(hostile, function)

    assert verdict.reason is IneligibilityReason.UNSUPPORTED_VALUE


@pytest.mark.parametrize(
    "relative",
    ["../outside.py", "a/../../escape.py", "/etc/passwd", "C:/Windows/system32/x.py"],
)
def test_unsafe_relative_paths_are_rejected(relative: str) -> None:
    verdict = check_relative_path(relative)

    assert verdict is not None
    assert verdict.outcome is EligibilityOutcome.INELIGIBLE
    assert verdict.reason is IneligibilityReason.UNSAFE_PATH


@pytest.mark.parametrize("relative", ["module.py", "pkg/sub/module.py"])
def test_safe_relative_paths_are_accepted(relative: str) -> None:
    assert check_relative_path(relative) is None


def test_empty_relative_path_is_rejected() -> None:
    verdict = check_relative_path("")

    assert verdict is not None
    assert verdict.reason is IneligibilityReason.UNSAFE_PATH
