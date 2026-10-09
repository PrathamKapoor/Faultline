"""Runtime oracle evaluation.

Phase 6A recorded what happened when a plan ran. This package asks the separate
question of whether that observation *disagrees with an explicit expectation* the
repository itself wrote. The two questions are kept apart on purpose:

* ``ExecutionResult`` records an observation. Nothing about it is a verdict.
* :func:`~edgehunter.evaluation.evaluator.evaluate_observation` decides whether a
  particular mined oracle applies to that observation at all, and only then
  whether the observation satisfies it.
* :func:`~edgehunter.evaluation.repository.evaluate_run` aggregates evaluated
  results into findings that carry their evidence.

The rules this package exists to enforce:

* **Applicability is checked before evaluation.** An oracle that does not apply is
  never evaluated, so a wrong value can never be read as a contradiction.
* **Only exact invocations match.** ``assert add(1, 2) == 3`` says something about
  ``add(1, 2)`` and nothing else. It is never generalized into a contract.
* **The evaluator is pure.** It is a function over an already-decoded observation,
  already-mined oracle evidence, and an invocation context. It starts no process,
  imports nothing from the target repository, and never evaluates a target
  expression.
* **Infrastructure failure is never a violation.** A timeout, an import failure, a
  sandbox that would not start, or an unverifiable source snapshot is
  ``INCONCLUSIVE``, never ``MISMATCH``.
* **A mismatch is a contradiction with a recorded expectation.** It is not a bug
  claim, not a severity, and never a critical vulnerability.
"""

from edgehunter.evaluation.applicability import (
    InvocationParameter,
    InvocationSignature,
    OracleApplicability,
    bind_actual_call,
    bind_recorded_call,
    check_applicability,
    expectations_for_target,
    recognize_recorded_value,
    signature_for_target,
    supplied_bindings,
)
from edgehunter.evaluation.compare import (
    ComparisonOutcome,
    ComparisonResult,
    compare_typed_values,
)
from edgehunter.evaluation.evaluator import (
    assertion_observed,
    evaluate_observation,
    evaluate_oracle,
    guard_evidence,
    observation_key,
    reconcile,
    report_unresolved_evidence,
)
from edgehunter.evaluation.exceptions import ExceptionMatch, exception_descendants, match_exception
from edgehunter.evaluation.findings import (
    build_findings,
    deduplicate_findings,
    summarize_reproduction,
)
from edgehunter.evaluation.models import (
    ApplicabilityKind,
    AppliedOracle,
    BoundCall,
    EvaluationReport,
    FindingCandidate,
    FindingKind,
    OracleEvidenceRef,
    OracleKind,
    OracleStatus,
    ReproductionStatus,
    ReproductionSummary,
)
from edgehunter.evaluation.recheck import AttemptSequence, build_sequence
from edgehunter.evaluation.repository import (
    ARTIFACT_SCHEMA_VERSION,
    DEFAULT_MAX_RECHECKS,
    HARD_MAX_RECHECKS,
    SourceSnapshot,
    build_evaluation_report,
    evaluate_run,
    evaluation_artifact,
    write_evaluation_artifact,
)
from edgehunter.evaluation.values import canonical_value_text, describe_typed, materialize

__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "DEFAULT_MAX_RECHECKS",
    "HARD_MAX_RECHECKS",
    "ApplicabilityKind",
    "AppliedOracle",
    "AttemptSequence",
    "BoundCall",
    "ComparisonOutcome",
    "ComparisonResult",
    "EvaluationReport",
    "ExceptionMatch",
    "FindingCandidate",
    "FindingKind",
    "InvocationParameter",
    "InvocationSignature",
    "OracleApplicability",
    "OracleEvidenceRef",
    "OracleKind",
    "OracleStatus",
    "ReproductionStatus",
    "ReproductionSummary",
    "SourceSnapshot",
    "assertion_observed",
    "bind_actual_call",
    "bind_recorded_call",
    "build_evaluation_report",
    "build_findings",
    "build_sequence",
    "canonical_value_text",
    "check_applicability",
    "compare_typed_values",
    "deduplicate_findings",
    "describe_typed",
    "evaluate_observation",
    "evaluate_oracle",
    "evaluate_run",
    "evaluation_artifact",
    "exception_descendants",
    "expectations_for_target",
    "guard_evidence",
    "match_exception",
    "materialize",
    "observation_key",
    "reconcile",
    "recognize_recorded_value",
    "report_unresolved_evidence",
    "signature_for_target",
    "summarize_reproduction",
    "supplied_bindings",
    "write_evaluation_artifact",
]
