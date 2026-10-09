"""Orchestration: executed results in, evaluated reports out.

This module owns three things the evaluator itself deliberately does not:

1. **Source snapshot verification.** The Phase 6A fingerprint work makes it possible
   to compare the bytes that were analyzed with the bytes that were staged and
   executed. :class:`SourceSnapshot` does that comparison; the evaluator refuses to
   produce a verdict when they differ. No Git commit hash is involved: a target
   repository may not use Git, and inventing one would be a fabrication.
2. **Reproduction bookkeeping.** It derives :class:`ReproductionSummary` from the
   per-attempt statuses the execution layer recorded, treating an attempt that
   could not run as a failure rather than as confirmation.
3. **Aggregation and deterministic deduplication** into findings.

The evaluator in :mod:`edgehunter.evaluation.evaluator` stays a pure function over
decoded data. Nothing in this package starts a process either: re-execution is
performed by the execution layer, which already owns authorization, staging, and
the Docker backend.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from edgehunter.evaluation.applicability import (
    bind_actual_call,
    signature_for_target,
    supplied_bindings,
)
from edgehunter.evaluation.evaluator import (
    assertion_observed,
    evaluate_observation,
    guard_evidence,
    observation_key,
    reconcile,
    report_unresolved_evidence,
)
from edgehunter.evaluation.findings import (
    build_findings,
    deduplicate_findings,
    is_consistent,
    summarize_reproduction,
)
from edgehunter.evaluation.models import (
    ApplicabilityKind,
    AppliedOracle,
    EvaluationReport,
    OracleEvidenceRef,
    OracleKind,
    OracleStatus,
    ReproductionStatus,
    ReproductionSummary,
)
from edgehunter.execution.assembly import action_identifier
from edgehunter.execution.models import (
    ExecutionResult,
    OutcomeKind,
    RunReport,
    SandboxState,
)
from edgehunter.oracle.models import ProductionPropertyCandidate, TestExpectationCandidate
from edgehunter.pipeline import RepositoryScan
from edgehunter.scanner.models import FunctionInfo

DEFAULT_MAX_RECHECKS: Final[int] = 0
"""Rechecking is off unless the user asks for it. Nothing retries automatically."""

HARD_MAX_RECHECKS: Final[int] = 5
"""The absolute ceiling, regardless of what was requested."""

ARTIFACT_SCHEMA_VERSION: Final[int] = 1
"""Bumped when the artifact's shape changes; recorded in the file itself."""

ARTIFACT_DIRECTORY: Final[str] = ".edgehunter/runs"

_KNOWN_OUTCOME_FIELDS: Final[frozenset[str]] = frozenset(
    {"RETURNED", "RAISED", "TIMEOUT", "IMPORT_FAILURE", "PROTOCOL_ERROR", "WORKER_FAILURE"}
)


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    """Whether the executed bytes are provably the analyzed bytes.

    ``verified`` is true only when both an analyzed digest and a staged digest
    exist and are equal. Anything else is unverifiable, and unverifiable means the
    evaluation stays ``INCONCLUSIVE`` with the reason recorded in ``detail``.
    """

    digest: str | None
    analyzed: str | None
    staged: str | None
    verified: bool
    detail: str = ""

    @classmethod
    def verify(
        cls,
        analyzed: str | None,
        staged_digests: tuple[tuple[str, str], ...],
        relative_file: str,
    ) -> SourceSnapshot:
        """Compare the analyzed fingerprint with the fingerprint of the staged copy.

        ``staged_digests`` is the ``(path, sha256)`` mapping the execution layer
        captured before deleting the temporary tree, so verification survives
        cleanup.
        """
        staged_digest: str | None = None
        for name, digest in staged_digests:
            if name == relative_file:
                staged_digest = digest
                break
        if analyzed is None or staged_digest is None:
            missing = "analyzed" if analyzed is None else "staged"
            return cls(
                digest=analyzed or staged_digest,
                analyzed=analyzed,
                staged=staged_digest,
                verified=False,
                detail=(
                    f"source snapshot not verified: the {missing} fingerprint is unavailable, "
                    "so the executed revision cannot be proven to be the analyzed one"
                ),
            )
        if analyzed != staged_digest:
            return cls(
                digest=analyzed,
                analyzed=analyzed,
                staged=staged_digest,
                verified=False,
                detail=(
                    "source snapshot not verified: the staged bytes differ from the analyzed "
                    "bytes, so the oracle may describe a different revision"
                ),
            )
        return cls(
            digest=analyzed,
            analyzed=analyzed,
            staged=staged_digest,
            verified=True,
            detail="the executed source matches the analyzed source byte for byte",
        )


def analyzed_digest(scan: RepositoryScan, relative_file: str) -> str | None:
    """The fingerprint of the analyzed bytes for one relative source path."""
    wanted = relative_file.replace("\\", "/")
    for module in scan.semantics.structure.modules:
        if module.relative_path.as_posix() == wanted:
            return module.digest
    return None


def bindings_for_action(scan: RepositoryScan, action_id: str) -> tuple[tuple[str, str], ...]:
    """Canonical ``(parameter, value)`` bindings for the plan that produced a request.

    Looked up by the same action identifier the execution layer sends, so the
    bindings used for evaluation are exactly the ones that were sent rather than a
    re-derivation that could disagree.
    """
    for plan in scan.planning.plans:
        if action_identifier(plan) == action_id:
            return supplied_bindings(plan)
    return ()


def build_evaluation_report(
    result: ExecutionResult,
    *,
    scan: RepositoryScan,
    function: FunctionInfo,
    snapshot: SourceSnapshot,
    reproduction: ReproductionSummary,
    attempts: tuple[ExecutionResult, ...] = (),
) -> EvaluationReport:
    """Evaluate one executed result and build its report.

    Pure with respect to target code: it reads an already-decoded observation and
    already-mined evidence. ``attempts`` are the additional re-executions the
    execution layer performed, used only for the reproduction summary.
    """
    signature = signature_for_target(result.target.identity, function)
    actual = bind_actual_call(signature, bindings_for_action(scan, result.action_id))

    evaluated = evaluate_observation(
        result,
        signature,
        actual,
        scan.oracles.test_expectations,
        snapshot_verified=snapshot.verified,
    )
    entries = evaluated + report_unresolved_evidence(
        scan.oracles.test_expectations, function.qualified_name
    )

    primary = reconcile(evaluated) or reconcile(entries)
    if primary is None:
        assertion = assertion_observed(
            result, scan.oracles.production_properties, function.qualified_name
        )
        primary = assertion if assertion is not None else _no_applicable_oracle(result)

    guards = guard_evidence_list(scan.oracles.production_properties, function.qualified_name)

    findings = build_findings(
        result,
        primary,
        actual=actual,
        reproduction=reproduction,
        snapshot_digest=snapshot.digest,
        primary_action=result.action_id,
        assignment_provenance=_provenance(result),
        reproduction_command=_reproduction_command(result),
        guards=guards,
    )

    return EvaluationReport(
        target_identity=result.target.identity,
        source_digest=snapshot.digest,
        execution_outcome=result.outcome,
        executions=entries,
        primary=primary,
        findings=deduplicate_findings(findings),
        reproduction=reproduction,
        invocation=actual,
        attempts=tuple((item.outcome, observation_key(item)) for item in (result, *attempts)),
    )


def guard_evidence_list(
    properties: tuple[ProductionPropertyCandidate, ...], qualified_name: str
) -> tuple[OracleEvidenceRef, ...]:
    """Validation guards for a target, as explanation only. Never evaluated."""
    return guard_evidence(properties, qualified_name)


def _provenance(result: ExecutionResult) -> tuple[str, ...]:
    """Each argument's origin, so a reader can see what was adversarial and what filled."""
    return tuple(
        f"{item.parameter}={item.value_text} [{item.origin.value}]" for item in result.assignments
    )


def _no_applicable_oracle(result: ExecutionResult) -> AppliedOracle:
    """Describe the honest "nothing applies here" outcome.

    This is a normal, supported result. EdgeHunter is allowed to execute an
    interesting case without knowing whether the behaviour is correct, and saying
    so plainly beats inventing an oracle.
    """
    return AppliedOracle(
        oracle=OracleKind.TEST_ASSERTION,
        applicability_kind=ApplicabilityKind.NO_EXPECTATION,
        status=OracleStatus.INCONCLUSIVE,
        reason=(
            "the repository records no explicit expectation for this exact invocation, so "
            "whether the behaviour is correct cannot be decided"
        ),
        evidence=(),
        expected_description="no applicable explicit oracle",
        observed_description=_observed_description(result),
    )


def _observed_description(result: ExecutionResult) -> str:
    observation = result.observation
    if observation is None:
        return result.outcome.value
    if observation.return_description is not None:
        return f"returned {observation.return_description}"
    if observation.exception_type is not None:
        return f"raised {observation.exception_type}"
    return observation.outcome.value


def _reproduction_command(result: ExecutionResult) -> str:
    """A command the user can run to reproduce the same execution."""
    return (
        f"edgehunter run . --target '{result.target.identity}' "
        f"--allow-execution --evaluate --recheck 3"
    )


def summarize_attempts(
    attempts: tuple[ExecutionResult, ...],
    *,
    scan: RepositoryScan,
    function: FunctionInfo,
    snapshot: SourceSnapshot,
    requested_rechecks: int,
) -> ReproductionSummary:
    """Derive the reproduction verdict from the per-attempt execution results.

    Each attempt is evaluated through exactly the same evaluator as the first one,
    so a recheck cannot be more lenient than the original. An attempt whose outcome
    is an infrastructure failure counts as a failure, never as confirmation.
    """
    if not attempts:
        return ReproductionSummary(
            status=ReproductionStatus.NOT_RECHECKED,
            attempts=0,
            equivalent_mismatches=0,
            failures=0,
            observation_keys=(),
        )

    statuses = tuple(_attempt_status(item, scan, function, snapshot) for item in attempts)
    keys = tuple(observation_key(item) for item in attempts)
    return summarize_reproduction(
        statuses=statuses,
        observation_keys=keys,
        requested_rechecks=requested_rechecks,
    )


def _attempt_status(
    result: ExecutionResult,
    scan: RepositoryScan,
    function: FunctionInfo,
    snapshot: SourceSnapshot,
) -> OracleStatus:
    """Evaluate one attempt purely to obtain its status."""
    if result.observation is None:
        return OracleStatus.INCONCLUSIVE
    if result.outcome not in _KNOWN_OUTCOME_FIELDS and result.outcome is not OutcomeKind.RETURNED:
        # RESOURCE_LIMIT and any future outcome is not something an oracle can judge.
        return OracleStatus.INCONCLUSIVE
    signature = signature_for_target(result.target.identity, function)
    actual = bind_actual_call(signature, bindings_for_action(scan, result.action_id))
    evaluated = evaluate_observation(
        result,
        signature,
        actual,
        scan.oracles.test_expectations,
        snapshot_verified=snapshot.verified,
    )
    primary = reconcile(evaluated)
    return primary.status if primary is not None else OracleStatus.INCONCLUSIVE


def evaluate_run(
    report: RunReport,
    *,
    scan: RepositoryScan,
    functions: dict[str, FunctionInfo],
    requested_rechecks: int = 0,
) -> tuple[EvaluationReport, ...]:
    """Evaluate every executed result in one run report.

    ``functions`` maps qualified names to the analyzed :class:`FunctionInfo` for the
    executed target, so no module is re-read or re-parsed here. Recheck attempts
    come from the report's own ``rechecks`` mapping, which is keyed by action id so
    an attempt is always attached to the plan that produced it.
    """
    evaluated: list[EvaluationReport] = []
    for result in report.results:
        function = functions.get(result.target.qualified_name)
        if function is None:
            continue
        extra = report.rechecks.get(result.action_id, ())
        snapshot = SourceSnapshot.verify(
            analyzed_digest(scan, result.target.relative_file),
            report.staged_digests,
            result.target.relative_file,
        )
        evaluated.append(
            build_evaluation_report(
                result,
                scan=scan,
                function=function,
                snapshot=snapshot,
                reproduction=summarize_attempts(
                    (result, *extra),
                    scan=scan,
                    function=function,
                    snapshot=snapshot,
                    requested_rechecks=requested_rechecks,
                ),
                attempts=extra,
            )
        )
    return tuple(evaluated)


def snapshot_digests(report: RunReport, relative_file: str) -> tuple[tuple[str, str], ...]:
    """The staged fingerprints a run captured, kept even after cleanup."""
    return report.staged_digests


def evaluation_artifact(run_id: str) -> Path:
    """The path one run's evaluation artifact belongs at.

    ``run_id`` is sanitized to a single safe path segment, so a crafted id cannot
    write outside the artifact directory.
    """
    safe = "".join(character for character in run_id if character.isalnum() or character in "-_")
    safe = safe.strip("-_") or "run"
    return Path(*PurePosixPath(ARTIFACT_DIRECTORY).parts) / safe / "evaluation.json"


def write_evaluation_artifact(path: Path, reports: tuple[EvaluationReport, ...]) -> Path:
    """Write a versioned, strict-JSON evaluation artifact.

    Only evidence needed for reproduction and diagnosis is stored: target identity,
    source fingerprint, oracle locations and statuses, and reproduction state.
    Captured target output and exception messages are deliberately excluded,
    because runtime output can contain sensitive data and there is no reason to
    persist it.

    The artifact directory is created on demand and is Git-ignored, so a generated
    artifact can never enter the repository.
    """
    document = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "reports": [_report_document(report) for report in reports],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return path


def _report_document(report: EvaluationReport) -> dict[str, object]:
    return {
        "target_identity": report.target_identity,
        "source_digest": report.source_digest,
        "execution_outcome": report.execution_outcome.value,
        "reproduction": {
            "status": report.reproduction.status.value,
            "attempts": report.reproduction.attempts,
            "equivalent_mismatches": report.reproduction.equivalent_mismatches,
            "failures": report.reproduction.failures,
        },
        "executions": [
            {
                "oracle": entry.oracle.value,
                "applicability": entry.applicability_kind.value,
                "status": entry.status.value,
                "reason": entry.reason,
                "expected": entry.expected_description,
                "observed": entry.observed_description,
                "snapshot_verified": entry.snapshot_verified,
                "evidence": [
                    {
                        "path": item.relative_path.as_posix(),
                        "line": item.lineno,
                        "detail": item.detail,
                    }
                    for item in entry.evidence
                ],
            }
            for entry in report.executions
        ],
        "findings": [
            {
                "id": finding.finding_id,
                "kind": finding.kind.value,
                "expected": finding.expected_behavior,
                "observed": finding.observed_behavior,
                "oracle_status": finding.oracle_status.value,
                "oracle_location": finding.oracle_location,
                "reproduction": finding.reproduction.value,
                "attempts": finding.attempts,
                "consistent": finding.consistent,
                "detail": finding.detail,
            }
            for finding in report.findings
        ],
    }


def target_functions(scan: RepositoryScan, target_identity: str) -> FunctionInfo | None:
    """The analyzed function for one ``file::qualified_name`` identity."""
    relative, _, qualified = target_identity.partition("::")
    for module in scan.semantics.structure.modules:
        if module.relative_path.as_posix() != relative.replace("\\", "/"):
            continue
        for function in module.functions:
            if function.qualified_name == qualified:
                return function
    return None


def resolved_expectations_for(
    scan: RepositoryScan, function: FunctionInfo
) -> tuple[TestExpectationCandidate, ...]:
    """Expectations resolved to exactly this target, for reporting."""
    return tuple(
        item for item in scan.oracles.test_expectations if item.target == function.qualified_name
    )


def sandbox_is_usable(report: RunReport) -> bool:
    """True when a container actually ran, which every evaluation depends on."""
    return report.sandbox.state is SandboxState.READY


__all__ = [
    "ARTIFACT_DIRECTORY",
    "ARTIFACT_SCHEMA_VERSION",
    "DEFAULT_MAX_RECHECKS",
    "HARD_MAX_RECHECKS",
    "SourceSnapshot",
    "analyzed_digest",
    "bindings_for_action",
    "build_evaluation_report",
    "evaluate_run",
    "evaluation_artifact",
    "is_consistent",
    "resolved_expectations_for",
    "sandbox_is_usable",
    "summarize_attempts",
    "target_functions",
    "write_evaluation_artifact",
]
