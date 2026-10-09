"""Typed vocabulary for runtime oracle evaluation.

Every status here is deliberately distinct from every other one, and none of them
is a synonym for another. The vocabulary exists so that the difference between
"the target raised", "the sandbox could not start", "no oracle applies", and "an
applicable oracle was contradicted" is a type the compiler can see rather than a
string a reader has to interpret.

Two dimensions are kept separate on purpose:

* :class:`OracleStatus` is about the *oracle*: was it applicable, and did the
  observation satisfy it?
* :class:`ExecutionOutcomeKind` is about the *run*: it already exists in
  :mod:`edgehunter.execution.models` and is not redefined here.

``Execution: RAISED`` with ``Oracle: MATCH`` is a normal, expected outcome, not a
contradiction of this model. That combination is what makes
``with pytest.raises(ValueError): validate(-1)`` verifiable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from edgehunter.execution.models import OutcomeKind


class OracleStatus(str, Enum):
    """The verdict of one oracle against one observation."""

    MATCH = "match"
    """An applicable oracle was evaluated and the observation satisfied it."""

    MISMATCH = "mismatch"
    """An applicable oracle was evaluated and the observation contradicted it.

    This is evidence of disagreement with one explicit, applicable expectation.
    It is not a confirmed software defect: the expectation itself may be wrong,
    outdated, or testing behaviour that changed deliberately.
    """

    NOT_APPLICABLE = "not_applicable"
    """The oracle does not apply to this target, input, or invocation."""

    INCONCLUSIVE = "inconclusive"
    """Not enough evidence to decide either way.

    Infrastructure failures land here: a timeout, an import failure, an
    unavailable sandbox, an unverifiable source snapshot, or an expectation whose
    target resolved ambiguously.
    """

    UNSUPPORTED = "unsupported"
    """The oracle needs a comparison or runtime feature this phase cannot perform.

    Reporting this separately from ``MISMATCH`` is the point: an unsupported
    comparison must never be mistaken for a contradiction.
    """

    CONFLICTING_EVIDENCE = "conflicting_evidence"
    """Several applicable explicit expectations disagree with each other.

    The evaluator does not pick the convenient one and does not report a defect
    from inconsistent expectations alone.
    """


class OracleKind(str, Enum):
    """Which evidence source an evaluated oracle came from.

    Tiered by how much the expectation actually constrains the invocation, in the
    conservative order the phase requires.
    """

    TEST_ASSERTION = "test_assertion"
    """Tier A: an ``assert <call> <op> <value>`` in an existing test.

    The primary verifiable oracle. Applies only to the same target and the exact
    recorded arguments.
    """

    TEST_RAISES = "test_raises"
    """Tier A: a ``with pytest.raises(X):`` block around one call."""

    PRODUCTION_ASSERTION = "production_assertion"
    """Tier B: an ``assert`` in production source.

    Describes a condition at a specific execution point, not a contract on all
    inputs. Never promoted into a universal property.
    """

    VALIDATION_GUARD = "validation_guard"
    """Tier C: an ``if COND: raise EXC`` guard.

    Retained as explanatory evidence only. Reachability is not inferred, so a
    guard never produces a confirmed violation on its own.
    """


class FindingKind(str, Enum):
    """What a finding asserts, calibrated to the evidence actually collected.

    There is deliberately no ``CRITICAL`` or ``CONFIRMED_VULNERABILITY`` member.
    A mismatch is evidence of disagreement with an explicit expectation; real-world
    impact and severity are a separate assessment this phase does not perform.
    """

    OBSERVED_EXPECTATION_MISMATCH = "observed_expectation_mismatch"
    """One execution contradicted one applicable explicit expectation."""

    REPRODUCED_EXPECTATION_MISMATCH = "reproduced_expectation_mismatch"
    """Every requested recheck reproduced the same contradiction."""

    ASSERTION_OBSERVED = "assertion_observed"
    """A production assertion was hit during execution.

    This is a fact about the run, not a confirmed defect. The assertion may be a
    precondition, so the finding asks for review rather than claiming a bug.
    """

    SUSPICIOUS_BEHAVIOR = "suspicious_behavior"
    """The target behaved in a way worth a human look, with no applicable oracle.

    Reported so the observation is not lost, explicitly not as a defect.
    """

    INCONCLUSIVE = "inconclusive"
    """Evaluation could not reach a verdict; nothing is claimed."""


class ReproductionStatus(str, Enum):
    """Whether a contradiction survived repeated execution."""

    NOT_RECHECKED = "not_rechecked"
    """Rechecking was not requested, so there is one observation and no claim."""

    REPRODUCED = "reproduced"
    """Every requested attempt produced an equivalent contradiction."""

    FLAKY = "flaky"
    """Attempts disagreed with each other.

    Reported honestly as flakiness. This is explicitly not a stable bug.
    """

    RECHECK_FAILED = "recheck_failed"
    """At least one attempt could not be evaluated, typically a sandbox failure.

    An environment failure is never confirmation.
    """


class ApplicabilityKind(str, Enum):
    """How an oracle was (or was not) bound to one exact invocation."""

    EXACT_MATCH = "exact_match"
    """Same target identity, same signature-bound arguments, same values."""

    NO_EXPECTATION = "no_expectation"
    """The repository records no expectation for this target at all."""

    TARGET_DIFFERS = "target_differs"
    """The expectation belongs to another target."""

    ARGUMENTS_DIFFER = "arguments_differ"
    """Same target, but the recorded example used different arguments.

    This is the most important refusal in the package: ``assert add(1, 2) == 3``
    says nothing about ``add(0, 0)``.
    """

    ARGUMENTS_NOT_LITERAL = "arguments_not_literal"
    """The recorded call used a fixture, name, or constructor.

    Its value is unknown, so exact matching is impossible and nothing is inferred.
    """

    AMBIGUOUS_TARGET = "ambiguous_target"
    """The expectation's callee matched several targets.

    No arbitrary target is chosen and no mismatch is declared.
    """

    UNRESOLVED_TARGET = "unresolved_target"
    """The expectation's callee could not be tied to a target at all."""


@dataclass(frozen=True, slots=True)
class BoundCall:
    """One invocation described as ``(parameter name, canonical value)`` bindings.

    Values are canonical text, never objects, so comparing two of these cannot
    reach a user-defined ``__eq__`` and does not depend on ``nan == nan``.

    Defined here rather than in :mod:`edgehunter.evaluation.applicability` because
    both the models and the applicability layer need it, and this module is the one
    both can import without a cycle.
    """

    target_identity: str
    bindings: tuple[tuple[str, str], ...]

    @property
    def is_empty(self) -> bool:
        return not self.bindings

    def render(self) -> str:
        """A readable ``target(a=..., b=...)`` rendering for CLI output."""
        if self.is_empty:
            return f"{self.target_identity}()"
        arguments = ", ".join(f"{name}={value}" for name, value in self.bindings)
        return f"{self.target_identity}({arguments})"


@dataclass(frozen=True, slots=True)
class OracleEvidenceRef:
    """One source location backing an evaluated oracle.

    Provenance is never discarded: an evaluation that cannot name the file and
    line it came from is not reviewable, so every :class:`AppliedOracle` carries at
    least one of these.
    """

    kind: OracleKind
    relative_path: Path
    lineno: int
    detail: str


@dataclass(frozen=True, slots=True)
class ReproductionSummary:
    """The outcome of an explicitly requested bounded re-execution."""

    status: ReproductionStatus
    attempts: int
    equivalent_mismatches: int
    """How many attempts agreed with each other on the contradiction."""

    failures: int
    """How many attempts could not be evaluated at all."""

    observation_keys: tuple[str, ...] = ()
    """The normalized observation key per attempt, in attempt order.

    Durations, execution ids, container ids, and temporary paths are deliberately
    excluded, so two runs of the same case produce the same sequence.
    """


@dataclass(frozen=True, slots=True)
class AppliedOracle:
    """The evaluation of one oracle against one observation.

    ``applicability_kind`` and ``status`` answer different questions. When the
    oracle was not applicable there is no evaluation verdict, and ``status`` is
    ``NOT_APPLICABLE`` for that reason alone rather than because anything was
    contradicted.
    """

    oracle: OracleKind
    applicability_kind: ApplicabilityKind
    status: OracleStatus
    reason: str
    evidence: tuple[OracleEvidenceRef, ...]
    expected_description: str = ""
    observed_description: str = ""
    snapshot_verified: bool = True
    """False when the executed source revision was not proven to be the analyzed one."""

    def describe_location(self) -> str:
        """A stable ``path:line`` rendering of the first evidence location."""
        if not self.evidence:
            return "<no location recorded>"
        first = self.evidence[0]
        return f"{first.relative_path.as_posix()}:{first.lineno}"


@dataclass(frozen=True, slots=True)
class FindingCandidate:
    """One piece of evidence, classified as carefully as the evidence allows.

    A finding carries the oracle it came from, the source revision it was measured
    against, the invocation, and the reproduction status. It never carries a
    severity: structural risk is a search-priority heuristic (D-022), not bug
    severity, and converting one into the other is exactly the confusion this
    project exists to avoid.
    """

    finding_id: str
    kind: FindingKind
    target_identity: str
    source_digest: str | None
    argument_description: str
    oracle_kind: OracleKind
    oracle_location: str
    expected_behavior: str
    observed_behavior: str
    oracle_status: OracleStatus
    reproduction: ReproductionStatus
    attempts: int
    consistent: bool
    primary_action: str = ""
    assignment_provenance: tuple[str, ...] = ()
    reproduction_command: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Everything one evaluated invocation produced.

    ``execution_outcome`` is preserved verbatim from Phase 6A. Nothing in this
    report rewrites it, and ``execution_outcome is OutcomeKind.RAISED`` never by
    itself produces a finding.
    """

    target_identity: str
    source_digest: str | None
    execution_outcome: OutcomeKind
    executions: tuple[AppliedOracle, ...]
    primary: AppliedOracle | None
    findings: tuple[FindingCandidate, ...]
    reproduction: ReproductionSummary
    invocation: BoundCall | None = None
    """The exact call that was executed, as canonical parameter bindings.

    Kept on the report so the CLI shows what was invoked rather than re-deriving
    it from the plan, which could disagree with what was actually sent.
    """

    attempts: tuple[tuple[OutcomeKind, str], ...] = ()
    """Per-attempt ``(outcome, normalized observation key)`` in attempt order."""

    @property
    def worst_status(self) -> OracleStatus:
        """The most severe status present, used only to summarize a run."""
        for status in (
            OracleStatus.CONFLICTING_EVIDENCE,
            OracleStatus.MISMATCH,
            OracleStatus.UNSUPPORTED,
            OracleStatus.INCONCLUSIVE,
            OracleStatus.NOT_APPLICABLE,
            OracleStatus.MATCH,
        ):
            if any(entry.status is status for entry in self.executions):
                return status
        return OracleStatus.NOT_APPLICABLE
