"""Evidence-backed findings, classified as carefully as the evidence allows.

A finding is what a user reads, so it is where the temptation to overstate is
highest. The rules this module enforces:

* **A mismatch is a contradiction with one explicit expectation.** It is never
  phrased as a confirmed defect, and there is no severity field, because structural
  risk is a search-priority heuristic (D-022) and nothing here is calibrated.
* **A production assertion yields review, not a bug.** ``ASSERTION_OBSERVED`` says
  the code's own assertion fired; whether that is wrong depends on whether the
  caller honoured a precondition, which EdgeHunter cannot see.
* **An observation with no applicable oracle yields ``SUSPICIOUS_BEHAVIOR``** so it
  is not silently discarded, explicitly not as a defect.
* **Conflicting expectations yield ``INCONCLUSIVE``**, retaining both locations.
* **Findings are deduplicated deterministically** on target identity, source
  snapshot, oracle identity, invocation identity, and mismatch category. Two
  findings that merely share an exception name are *not* merged, because different
  inputs reaching the same exception class are different facts.
* **Identifiers are deterministic**, derived from the deduplication key, so two runs
  of the same case name the same finding and unrelated findings never collide.
"""

from __future__ import annotations

import hashlib

from edgehunter.evaluation.models import (
    ApplicabilityKind,
    AppliedOracle,
    BoundCall,
    FindingCandidate,
    FindingKind,
    OracleEvidenceRef,
    OracleKind,
    OracleStatus,
    ReproductionStatus,
    ReproductionSummary,
)
from edgehunter.execution.models import ExecutionResult, OutcomeKind

_ID_PREFIX: str = "ehf"
_ID_LENGTH: int = 12


def build_findings(
    result: ExecutionResult,
    report_evaluation: AppliedOracle | None,
    *,
    actual: BoundCall,
    guards: tuple[OracleEvidenceRef, ...] = (),
    primary_action: str = "",
    assignment_provenance: tuple[str, ...] = (),
    reproduction: ReproductionSummary,
    snapshot_digest: str | None,
    reproduction_command: str = "",
) -> tuple[FindingCandidate, ...]:
    """Classify one evaluated invocation into zero or more findings.

    Returning an empty tuple is the normal, expected outcome. EdgeHunter is
    explicitly allowed to execute an interesting case without knowing whether the
    behaviour is correct.
    """
    if report_evaluation is None:
        return ()

    kind = _classify(result, report_evaluation)
    if kind is None:
        return ()
    if kind is FindingKind.OBSERVED_EXPECTATION_MISMATCH and (
        reproduction.status is ReproductionStatus.REPRODUCED
    ):
        # Every requested attempt contradicted the same expectation. That is a
        # stronger statement than one observation, so it is recorded distinctly --
        # and still not as a bug.
        kind = FindingKind.REPRODUCED_EXPECTATION_MISMATCH

    location = report_evaluation.describe_location()
    invocation = actual.render()
    expected = report_evaluation.expected_description or "no explicit expectation"
    observed = report_evaluation.observed_description or result.outcome.value

    finding_id = finding_identifier(
        target=result.target.identity,
        digest=snapshot_digest,
        location=location,
        invocation=invocation,
        category=kind.value,
    )
    detail = report_evaluation.reason
    if guards:
        detail = f"{detail}; validation guards are retained as context only, not evaluated"

    return (
        FindingCandidate(
            finding_id=finding_id,
            kind=kind,
            target_identity=result.target.identity,
            source_digest=snapshot_digest,
            argument_description=invocation,
            oracle_kind=report_evaluation.oracle,
            oracle_location=location,
            expected_behavior=expected,
            observed_behavior=observed,
            oracle_status=report_evaluation.status,
            reproduction=reproduction.status,
            attempts=reproduction.attempts,
            consistent=reproduction.status is not ReproductionStatus.FLAKY,
            primary_action=primary_action,
            assignment_provenance=assignment_provenance,
            reproduction_command=reproduction_command,
            detail=detail,
        ),
    )


def _classify(result: ExecutionResult, evaluation: AppliedOracle) -> FindingKind | None:
    """Map an evaluation verdict onto a finding kind, or ``None`` for no finding.

    Two verdicts deliberately produce *no* finding at all:

    * ``MATCH`` -- the expected case. Nothing to report.
    * ``UNSUPPORTED`` -- EdgeHunter could not perform the comparison. The report
      already shows the status and the reason; inventing a finding for a question
      that was never answered would be noise, and could read as a defect.

    ``NOT_APPLICABLE`` with no oracle at all produces ``SUSPICIOUS_BEHAVIOR`` only
    when the target raised, because an exception with no applicable expectation is
    the one case worth a human look without claiming anything.
    """
    status = evaluation.status

    if status is OracleStatus.MATCH:
        return None

    if status is OracleStatus.MISMATCH:
        return FindingKind.OBSERVED_EXPECTATION_MISMATCH

    if status is OracleStatus.CONFLICTING_EVIDENCE:
        # Inconsistent expectations are a fact about the repository, not a defect.
        return FindingKind.INCONCLUSIVE

    if status is OracleStatus.UNSUPPORTED:
        return None

    # A production assertion is labelled before anything else. It is a specific,
    # named kind of inconclusive evidence, and the honest classification is review
    # rather than "no oracle exists".
    if status is OracleStatus.INCONCLUSIVE and evaluation.oracle is OracleKind.PRODUCTION_ASSERTION:
        return FindingKind.ASSERTION_OBSERVED

    # An invocation with no recorded expectation is checked before the
    # infrastructure branch: a raise with nothing to check it against is exactly
    # the case worth a human look, and "no oracle exists" is not an infrastructure
    # failure even though the verdict is INCONCLUSIVE.
    if evaluation.applicability_kind is ApplicabilityKind.NO_EXPECTATION:
        if result.outcome is OutcomeKind.RAISED:
            return FindingKind.SUSPICIOUS_BEHAVIOR
        return None

    if status is OracleStatus.INCONCLUSIVE:
        if _is_infrastructure_inconclusive(result):
            return None
        return FindingKind.INCONCLUSIVE

    return None


def _is_infrastructure_inconclusive(result: ExecutionResult) -> bool:
    """True when the run itself failed, so there is nothing to report about behaviour."""
    return result.observation is None or result.outcome in {
        OutcomeKind.TIMEOUT,
        OutcomeKind.IMPORT_FAILURE,
        OutcomeKind.TARGET_NOT_FOUND,
        OutcomeKind.PROTOCOL_ERROR,
        OutcomeKind.WORKER_FAILURE,
        OutcomeKind.RESOURCE_LIMIT,
        OutcomeKind.INELIGIBLE,
    }


def deduplicate_findings(
    findings: tuple[FindingCandidate, ...],
) -> tuple[FindingCandidate, ...]:
    """Collapse identical findings deterministically, keeping every distinct one.

    The key is the same one that generates the identifier, so two findings merge
    exactly when they describe the same contradiction. Distinct inputs reaching the
    same exception class keep separate findings.
    """
    unique: dict[str, FindingCandidate] = {}
    for finding in findings:
        key = _dedup_key(finding)
        current = unique.get(key)
        if current is None:
            unique[key] = finding
            continue
        if finding.attempts > current.attempts:
            unique[key] = finding
    return tuple(sorted(unique.values(), key=lambda item: item.finding_id))


def _dedup_key(finding: FindingCandidate) -> str:
    return "|".join(
        (
            finding.target_identity,
            finding.source_digest or "",
            finding.oracle_location,
            finding.oracle_kind.value,
            finding.argument_description,
            finding.kind.value,
        )
    )


def finding_identifier(
    *,
    target: str,
    digest: str | None,
    location: str,
    invocation: str,
    category: str,
) -> str:
    """A deterministic identifier derived from the finding's own identity.

    Hashing the identity rather than counting means the same case is named the same
    way on every run, and two unrelated findings cannot collide because their keys
    differ. Git commit hashes are deliberately not involved: a target repository may
    not use Git at all, so no commit is referenced or invented.
    """
    material = "|".join((target, digest or "", location, invocation, category))
    digest_bytes = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"{_ID_PREFIX}-{digest_bytes[:_ID_LENGTH]}"


def summarize_reproduction(
    statuses: tuple[OracleStatus, ...],
    observation_keys: tuple[str, ...],
    requested_rechecks: int,
) -> ReproductionSummary:
    """Derive the reproduction verdict from a sequence of per-attempt evaluations.

    An attempt counts as a mismatch only when the oracle actually evaluated it and
    contradicted the observation. An attempt that could not run -- a sandbox that
    would not start, a timeout -- is a failure, never confirmation.
    """
    mismatches = sum(1 for status in statuses if status is OracleStatus.MISMATCH)
    failures = sum(1 for status in statuses if status in _FAILED_STATUSES)

    if requested_rechecks == 0:
        return ReproductionSummary(
            status=ReproductionStatus.NOT_RECHECKED,
            attempts=len(statuses),
            equivalent_mismatches=mismatches,
            failures=failures,
            observation_keys=observation_keys,
        )

    if failures:
        return ReproductionSummary(
            status=ReproductionStatus.RECHECK_FAILED,
            attempts=len(statuses),
            equivalent_mismatches=mismatches,
            failures=failures,
            observation_keys=observation_keys,
        )

    evaluated = [status for status in statuses if status is OracleStatus.MISMATCH]
    if not evaluated:
        return ReproductionSummary(
            status=ReproductionStatus.NOT_RECHECKED,
            attempts=len(statuses),
            equivalent_mismatches=0,
            failures=failures,
            observation_keys=observation_keys,
        )

    if len(set(statuses)) == 1:
        return ReproductionSummary(
            status=ReproductionStatus.REPRODUCED,
            attempts=len(statuses),
            equivalent_mismatches=mismatches,
            failures=failures,
            observation_keys=observation_keys,
        )

    # Attempts disagreed. Some matched while others did not, which is flakiness and
    # is reported as such rather than promoted to a stable bug.
    return ReproductionSummary(
        status=ReproductionStatus.FLAKY,
        attempts=len(statuses),
        equivalent_mismatches=mismatches,
        failures=failures,
        observation_keys=observation_keys,
    )


_FAILED_STATUSES: frozenset[OracleStatus] = frozenset(
    {
        OracleStatus.INCONCLUSIVE,
        OracleStatus.UNSUPPORTED,
    }
)


def is_consistent(summary: ReproductionSummary) -> bool:
    """True when every attempt agreed, or when only one attempt was made."""
    return summary.status is not ReproductionStatus.FLAKY


__all__ = [
    "build_findings",
    "deduplicate_findings",
    "finding_identifier",
    "is_consistent",
    "summarize_reproduction",
]
