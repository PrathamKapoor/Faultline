"""Deciding whether one invocation plan may be handed to the sandbox.

This check is deliberately a gate, not a best-effort filter. A plan that fails
any rule here is reported as ineligible with a reason and is **not** attempted:
the phase's rule is that an unsupported target produces a structured skip, never
a partial or improvised invocation.

The rules, in order:

* the plan must be ``COMPLETE_FUNCTION`` — a partial plan has unfilled
  arguments, and filling them would mean fabricating values;
* the target must be an ordinary synchronous top-level function;
* a decorated function is refused, because whether the decorator changes the
  call's meaning cannot be established conservatively;
* every planned value must be recognizable by the typed-value protocol;
* the target's relative path must be a safe, relative, non-escaping path.

Methods, async functions, nested functions, and receiver-dependent plans are
refused here so they can never reach the container.
"""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from edgehunter.execution.assembly import AssemblyFailure, assemble_invocation
from edgehunter.execution.codec import ProtocolError
from edgehunter.execution.models import (
    EligibilityOutcome,
    ExecutionEligibility,
    IneligibilityReason,
)
from edgehunter.generation.invocation import InvocationPlan, InvocationStatus
from edgehunter.scanner.models import FunctionInfo, TargetKind

ELIGIBLE_TARGET_KIND: TargetKind = TargetKind.FUNCTION


def check_eligibility(plan: InvocationPlan, function: FunctionInfo) -> ExecutionEligibility:
    """Return a deterministic verdict for one plan, with a reason when refused."""
    verdict = _check_status(plan)
    if verdict is not None:
        return verdict

    verdict = _check_target_kind(function)
    if verdict is not None:
        return verdict

    verdict = _check_decorators(function)
    if verdict is not None:
        return verdict

    return _check_assembly(plan, function)


def _eligible(detail: str) -> ExecutionEligibility:
    return ExecutionEligibility(outcome=EligibilityOutcome.ELIGIBLE, detail=detail)


def _refused(reason: IneligibilityReason, detail: str) -> ExecutionEligibility:
    return ExecutionEligibility(outcome=EligibilityOutcome.INELIGIBLE, detail=detail, reason=reason)


def _check_status(plan: InvocationPlan) -> ExecutionEligibility | None:
    """Refuse any plan that is not a complete, receiver-free function call."""
    if plan.status is InvocationStatus.PARTIAL_ARGUMENTS:
        return _refused(
            IneligibilityReason.PARTIAL_ARGUMENTS,
            f"required parameters have no value: {', '.join(plan.missing_parameters) or 'unknown'}",
        )
    if plan.status is InvocationStatus.SYMBOLIC_DEPENDENCY:
        return _refused(
            IneligibilityReason.SYMBOLIC_DEPENDENCY,
            "the primary action's value still depends on an unresolved reference",
        )
    if plan.status is InvocationStatus.NEEDS_RECEIVER:
        return _refused(
            IneligibilityReason.NEEDS_RECEIVER,
            "receiver construction is not supported; no instance is fabricated",
        )
    if plan.missing_parameters:
        return _refused(
            IneligibilityReason.MISSING_ARGUMENTS,
            f"required parameters have no value: {', '.join(plan.missing_parameters)}",
        )
    if plan.symbolic_dependencies:
        return _refused(
            IneligibilityReason.SYMBOLIC_DEPENDENCY,
            f"unresolved dependencies: {', '.join(plan.symbolic_dependencies)}",
        )
    if len(plan.adversarial_assignments) != 1:
        return _refused(
            IneligibilityReason.UNSUPPORTED_VALUE,
            "a run must exercise exactly one adversarial action",
        )
    return None


def _check_target_kind(function: FunctionInfo) -> ExecutionEligibility | None:
    """Refuse anything that is not an ordinary synchronous top-level function."""
    if function.kind is not ELIGIBLE_TARGET_KIND:
        return _refused(
            IneligibilityReason.UNSUPPORTED_TARGET_KIND,
            f"{function.kind.value} targets are not executable in this phase",
        )
    return None


def _check_decorators(function: FunctionInfo) -> ExecutionEligibility | None:
    """Refuse decorated functions rather than guessing what the wrapper does."""
    if function.decorators:
        return _refused(
            IneligibilityReason.DECORATED_TARGET,
            f"decorated target: {', '.join(function.decorators)}",
        )
    return None


def _check_assembly(plan: InvocationPlan, function: FunctionInfo) -> ExecutionEligibility:
    """Confirm the call can actually be assembled and encoded."""
    try:
        assemble_invocation(plan, function)
    except AssemblyFailure as exc:
        reason = (
            IneligibilityReason.UNSUPPORTED_VALUE
            if "not a supported protocol value" in exc.detail
            else IneligibilityReason.ASSEMBLY_FAILED
        )
        return _refused(reason, exc.detail)
    except ProtocolError as exc:  # pragma: no cover - defensive
        return _refused(IneligibilityReason.UNSUPPORTED_VALUE, str(exc))
    return _eligible("plan is a complete, receiver-free, synchronously callable target")


def check_relative_path(relative: str) -> ExecutionEligibility | None:
    """Refuse a relative source path that could escape the staged tree.

    Both a POSIX and a Windows reading are applied. ``PurePosixPath`` alone would
    accept ``C:/Windows/system32/x.py`` as a relative path because it has no
    concept of a drive letter, so ``PureWindowsPath`` is checked as well. This was
    a real defect caught by
    ``test_unsafe_relative_paths_are_rejected[C:/Windows/system32/x.py]``.
    """
    if not relative:
        return _refused(IneligibilityReason.UNSAFE_PATH, "target has no relative path")
    if PureWindowsPath(relative).drive or PureWindowsPath(relative).is_absolute():
        return _refused(
            IneligibilityReason.UNSAFE_PATH, f"absolute path is not accepted: {relative!r}"
        )
    if PurePosixPath(relative).is_absolute():
        return _refused(
            IneligibilityReason.UNSAFE_PATH, f"absolute path is not accepted: {relative!r}"
        )
    if "\\" in relative:
        return _refused(
            IneligibilityReason.UNSAFE_PATH, f"backslash separator is not accepted: {relative!r}"
        )
    if any(part == ".." for part in PurePosixPath(relative).parts):
        return _refused(
            IneligibilityReason.UNSAFE_PATH, f"path escapes the repository: {relative!r}"
        )
    if not PurePosixPath(relative).parts:
        return _refused(IneligibilityReason.UNSAFE_PATH, "target path is empty")
    return None
