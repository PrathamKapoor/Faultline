"""Typed vocabulary for budgeted, feedback-guided semantic neighborhood search.

Three concepts are kept apart here, because collapsing them is how a search
engine starts lying to its user.

**A semantic action** (:class:`SearchAction`) is *what input is being proposed*.
It is a value, an origin, a reason, and a stable identifier. It says nothing
about when it will be tried.

**A search policy** is *which available action is tried next*. A policy reads a
state and a typed candidate list and returns a selection. It may not import
target code, start a container, or evaluate an oracle.

**Execution feedback** (:class:`SearchFeedback`) is *what actually happened* when
a candidate was run and judged. It is derived from the existing evaluator's
verdict, never invented by the searcher.

Every status below is a statement about *this bounded search*. None of them is a
severity, a confidence number, or a claim that the code is wrong. A mismatch is
disagreement with one explicit user declaration; reproducing it twice makes the
disagreement stable, not the declaration correct.

The budget constants live next to their shrink counterparts rather than in a
shared module, because the two searches are different searches: shrinking walks
*towards zero*, search walks *towards unexplored behaviour*. Only the signed
64-bit range and the reproduction threshold are genuinely shared, and those are
imported rather than restated.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from edgehunter.execution.models import OutcomeKind
from edgehunter.generation.models import SemanticAction
from edgehunter.shrink.models import (
    DEFAULT_REPRODUCTION_ATTEMPTS,
    MAX_SIGNED_INT,
    MIN_SIGNED_INT,
)

__all__ = [
    "DEFAULT_CANDIDATE_LIMIT",
    "DEFAULT_MAX_EXECUTIONS",
    "DEFAULT_MAX_PROBES",
    "DEFAULT_REPRODUCTION_ATTEMPTS",
    "DEFAULT_WALL_CLOCK_SECONDS",
    "HARD_MAX_CANDIDATES",
    "HARD_MAX_EXECUTIONS",
    "HARD_MAX_PROBES",
    "HARD_MAX_WALL_CLOCK_SECONDS",
    "MAX_SIGNED_INT",
    "MIN_SIGNED_INT",
    "PolicyName",
    "ProposalOrigin",
    "SearchAction",
    "SearchActionKind",
    "SearchBudgets",
    "SearchFeedback",
    "SearchResult",
    "SearchSelection",
    "SearchState",
    "SearchStep",
    "SearchTermination",
    "SelectionUse",
    "TARGET_INDEPENDENT_OUTCOMES",
    "feedback_for",
    "is_target_independent",
]

# The whole cost model of the search, in one place. Every one of these is a real
# container or a real bounded computation, so they are all enforced rather than
# suggested.
DEFAULT_MAX_PROBES: Final[int] = 8
HARD_MAX_PROBES: Final[int] = 20
DEFAULT_MAX_EXECUTIONS: Final[int] = 12
HARD_MAX_EXECUTIONS: Final[int] = 32
DEFAULT_CANDIDATE_LIMIT: Final[int] = 64
HARD_MAX_CANDIDATES: Final[int] = 128
DEFAULT_WALL_CLOCK_SECONDS: Final[float] = 180.0
HARD_MAX_WALL_CLOCK_SECONDS: Final[float] = 1800.0


class PolicyName(str, Enum):
    """The two policies this phase supports, sharing one candidate universe."""

    STATIC_PRIORITY = "static_priority"
    """A deterministic nonadaptive baseline.

    Selects by a fixed documented order and never consults runtime feedback. Its
    whole purpose is to be the thing the adaptive policy must beat to prove
    anything, so making it adaptive would destroy the only comparison that means
    something.
    """

    FEEDBACK_GUIDED = "feedback_guided"
    """A policy whose next choice depends on the previous probe's verdict."""

    @classmethod
    def parse(cls, value: str) -> PolicyName:
        """Accept the CLI spellings ``static`` and ``feedback``."""

        text = value.strip().lower()
        match text:
            case "static" | "static_priority":
                return cls.STATIC_PRIORITY
            case "feedback" | "feedback_guided":
                return cls.FEEDBACK_GUIDED
        known = ", ".join(member.value for member in cls)
        raise ValueError(f"unknown policy {value!r}; expected one of: {known}")


class ProposalOrigin(str, Enum):
    """Where a proposed integer came from.

    Kept distinct from :class:`SearchActionKind` because the *kind* says what
    transformation produced the number while the *origin* says which evidence
    justified proposing it at all. A midpoint and an integer neighbour are both
    search-derived, but only one of them is a Phase 5 semantic action.
    """

    CONTRACT_BOUNDARY = "contract_boundary"
    """The closest-to-zero admissible integer of the declared domain."""

    USER_SUPPLIED_SEED = "user_supplied_seed"
    """The integer the user explicitly supplied as the starting point."""

    STATIC_SOURCE_BOUNDARY = "static_source_boundary"
    """A concrete integer boundary already mined from the target's own source.

    Reuses Phase 2 boundary evidence rather than re-extracting comparisons.
    """

    SEARCH_NEIGHBOR = "search_neighbor"
    """An adjacent admissible integer derived by the search itself."""

    SEARCH_MIDPOINT = "search_midpoint"
    """A midpoint between the start value and the anchor, derived by the search."""

    def rank(self) -> int:
        """The fixed ordering the static baseline documents and follows."""

        return ORIGIN_RANK[self]


ORIGIN_RANK: Final[dict[ProposalOrigin, int]] = {
    ProposalOrigin.CONTRACT_BOUNDARY: 0,
    ProposalOrigin.USER_SUPPLIED_SEED: 1,
    ProposalOrigin.STATIC_SOURCE_BOUNDARY: 2,
    ProposalOrigin.SEARCH_MIDPOINT: 3,
    ProposalOrigin.SEARCH_NEIGHBOR: 4,
}
"""The documented static baseline order: anchor, user seed, source boundaries,
midpoints, remaining neighbours. Both policies share it; only one ignores it."""


class SearchActionKind(str, Enum):
    """The closed vocabulary of input transformations Phase 7C may propose.

    Deliberately small and integer-only. There is no string or collection
    neighbourhood here: widening the vocabulary without measurement would make
    every later benchmark unattributable.
    """

    CONTRACT_ANCHOR = "contract_anchor"
    USER_SEED = "user_seed"
    SOURCE_BOUNDARY_AT = "source_boundary_at"
    SOURCE_BOUNDARY_BELOW = "source_boundary_below"
    SOURCE_BOUNDARY_ABOVE = "source_boundary_above"
    STEP_TOWARD_ZERO = "step_toward_zero"
    STEP_AWAY_FROM_ZERO = "step_away_from_zero"
    INTEGER_NEIGHBOR_BELOW = "integer_neighbor_below"
    INTEGER_NEIGHBOR_ABOVE = "integer_neighbor_above"
    MIDPOINT_TO_ANCHOR = "midpoint_to_anchor"

    def rank(self) -> int:
        """A stable kind rank, used as a deterministic tie-breaker."""

        return ACTION_KIND_RANK[self]


ACTION_KIND_RANK: Final[dict[SearchActionKind, int]] = {
    SearchActionKind.CONTRACT_ANCHOR: 0,
    SearchActionKind.USER_SEED: 1,
    SearchActionKind.SOURCE_BOUNDARY_AT: 2,
    SearchActionKind.SOURCE_BOUNDARY_BELOW: 3,
    SearchActionKind.SOURCE_BOUNDARY_ABOVE: 4,
    SearchActionKind.STEP_TOWARD_ZERO: 5,
    SearchActionKind.STEP_AWAY_FROM_ZERO: 6,
    SearchActionKind.INTEGER_NEIGHBOR_BELOW: 7,
    SearchActionKind.INTEGER_NEIGHBOR_ABOVE: 8,
    SearchActionKind.MIDPOINT_TO_ANCHOR: 9,
}


class SearchFeedback(str, Enum):
    """Typed runtime feedback for one executed probe.

    The mapping from the existing evaluator's verdict is exact and is written out
    once, in :func:`feedback_for`. The important part is what is *absent*: there
    is no feedback value meaning "the sandbox was difficult" that a policy could
    mistake for progress. An infrastructure failure has its own member precisely
    so it cannot be silently upgraded into a reward.
    """

    EXPECTED_BEHAVIOR = "expected_behavior"
    EXPECTATION_MISMATCH = "expectation_mismatch"
    INCONCLUSIVE = "inconclusive"
    UNSUPPORTED = "unsupported"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    CONFLICTING_EVIDENCE = "conflicting_evidence"

    @property
    def is_evidence(self) -> bool:
        """True only when the probe produced a usable verdict.

        ``INCONCLUSIVE``, ``UNSUPPORTED``, ``INFRASTRUCTURE_FAILURE`` and
        ``CONFLICTING_EVIDENCE`` are all explicitly *not* evidence, in either
        direction.
        """

        return self in _FEEDBACK_EVIDENCE

    @property
    def is_usable_observation(self) -> bool:
        """True when the target's behaviour was actually observed.

        The distinction matters for stopping: a target-independent sandbox
        failure wastes the rest of the budget, while one candidate-specific
        unusable result does not.
        """

        return self in (SearchFeedback.EXPECTED_BEHAVIOR, SearchFeedback.EXPECTATION_MISMATCH)


_FEEDBACK_EVIDENCE: Final[frozenset[SearchFeedback]] = frozenset(
    {SearchFeedback.EXPECTED_BEHAVIOR, SearchFeedback.EXPECTATION_MISMATCH}
)

TARGET_INDEPENDENT_OUTCOMES: Final[frozenset[OutcomeKind]] = frozenset(
    {
        OutcomeKind.SANDBOX_UNAVAILABLE,
        OutcomeKind.TARGET_NOT_FOUND,
        OutcomeKind.INELIGIBLE,
        OutcomeKind.PROTOCOL_ERROR,
        OutcomeKind.WORKER_FAILURE,
    }
)
"""Outcomes that describe the *backend*, not the candidate.

A timeout or an import failure can genuinely depend on which input was passed,
so those move on to another candidate with the reason recorded. A container that
could not start, a target the worker could not find, or a malformed result
document will fail identically for every remaining candidate, so continuing
would spend a real budget proving nothing.

Both branches are spelled out rather than collapsed into "stop on any failure",
because "the backend is dead" and "this one input was slow" are different facts
and a policy that cannot tell them apart either wastes a budget or hides a real
problem.
"""


def is_target_independent(outcome: OutcomeKind | None) -> bool:
    """Whether a failure says the backend cannot serve this target at all."""

    return outcome is not None and outcome in TARGET_INDEPENDENT_OUTCOMES


class SearchTermination(str, Enum):
    """Why the search stopped.

    These are ten distinct facts and are never collapsed. The difference between
    "we tried everything we planned and found nothing" and "we ran out of
    budget" is the difference between a bounded negative result and an unknown
    one, and reporting the first as the second (or the reverse) would be a
    claim this project cannot support.
    """

    VIOLATION_REPRODUCED = "violation_reproduced"
    """One probe contradicted the contract on two independent executions."""

    NO_REPRODUCED_VIOLATION = "no_reproduced_violation"
    """The whole candidate universe ran out with nothing reproduced.

    Says nothing about untested inputs. Never reported as "the target is
    correct".
    """

    PROBE_BUDGET_EXHAUSTED = "probe_budget_exhausted"
    EXECUTION_BUDGET_EXHAUSTED = "execution_budget_exhausted"
    WALL_TIME_BUDGET_EXHAUSTED = "wall_time_budget_exhausted"
    """A bound stopped the search before the universe was exhausted."""

    FLAKY_OBSERVATION = "flaky_observation"
    """Two executions of the same input disagreed, so nothing is stable."""

    INCONCLUSIVE = "inconclusive"
    """A recheck could not be evaluated, so no finding is established."""

    CONFLICTING_EVIDENCE = "conflicting_evidence"
    """Applicable evidence disagreed; neither side was chosen."""

    REFUSED = "refused"
    """An input was rejected before execution. Nothing ran."""

    SANDBOX_UNAVAILABLE = "sandbox_unavailable"
    """The execution backend could not be established."""


class SelectionUse(str, Enum):
    """Whether a selection is a new distinct probe or a verification rerun.

    The distinction is load-bearing for the budget model: a rerun consumes an
    execution but never a distinct-probe slot, and neither is ever silently
    treated as the other.
    """

    NEW_PROBE = "new_probe"
    VERIFICATION_RERUN = "verification_rerun"


@dataclass(frozen=True, slots=True)
class SearchAction:
    """One proposed integer input, with its full provenance.

    ``semantic_action`` is set **only** when this proposal really is a Phase 5
    ``SemanticAction`` recovered from mined source evidence. A midpoint the
    search derived carries ``None`` there, because reporting a search-derived
    value as though the action layer had proposed it would be claiming
    provenance the search does not have.
    """

    kind: SearchActionKind
    target_identity: str
    parameter: str
    value: int
    contract_id: str
    source_sha256: str
    origin: ProposalOrigin
    reason: str
    priority: int
    semantic_action: SemanticAction | None = None

    def __post_init__(self) -> None:
        if not MIN_SIGNED_INT <= self.value <= MAX_SIGNED_INT:
            raise SearchValueError(
                f"proposed value {self.value} is outside the signed 64-bit range this phase "
                "supports"
            )

    @property
    def identifier(self) -> str:
        """A stable identity derived from the action's own content.

        Two runs over the same snapshot and contract produce the same identifier,
        which is what lets the state track "already tested" without storing a
        mutable set anywhere. It is not a hash of the *reason* or the *priority*,
        because those are presentation and ordering, not identity.
        """

        payload = "|".join(
            (
                self.kind.value,
                self.target_identity,
                self.parameter,
                str(self.value),
                self.contract_id,
                self.source_sha256,
                self.origin.value,
            )
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
        return f"ehs-{digest}"

    @property
    def static_order_key(self) -> tuple[int, int, int, int, str]:
        """The fixed ordering key the static baseline sorts on.

        Ordered by ``(origin rank, action kind rank, abs(value), value,
        identifier)``. Every component is total and stable: magnitude before
        sign so ``-2`` and ``2`` never tie on an arbitrary coin flip, and the
        identifier last so two genuinely equal candidates still resolve the same
        way every time.
        """

        return (
            self.origin.rank(),
            self.kind.rank(),
            abs(self.value),
            self.value,
            self.identifier,
        )

    def distance_key(self, reference: int) -> tuple[int, int, int, int, int, int, str]:
        """Ordering by how close an action is to ``reference``.

        Used by the feedback policy to prefer *nearby* unexplored behaviour after
        a probe behaved as expected. Distance comes first, then the static order
        key, so proximity can reorder candidates but can never introduce a
        nondeterministic tie.
        """

        return (
            abs(self.value - reference),
            self.origin.rank(),
            self.kind.rank(),
            self.priority,
            abs(self.value),
            self.value,
            self.identifier,
        )


class SearchValueError(ValueError):
    """A proposed or requested value is not representable in this phase."""


@dataclass(frozen=True, slots=True)
class SearchBudgets:
    """Every bound the search runs under, each clamped on read.

    Clamping rather than raising means an unreasonable request costs a smaller
    search instead of an exception, but the *effective* value is always
    reported so a caller can see what they actually got.
    """

    max_probes: int = DEFAULT_MAX_PROBES
    max_executions: int = DEFAULT_MAX_EXECUTIONS
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT
    wall_clock_seconds: float = DEFAULT_WALL_CLOCK_SECONDS
    reproduction_attempts: int = DEFAULT_REPRODUCTION_ATTEMPTS

    def bounded_probes(self) -> int:
        return max(0, min(HARD_MAX_PROBES, self.max_probes))

    def bounded_executions(self) -> int:
        return max(0, min(HARD_MAX_EXECUTIONS, self.max_executions))

    def bounded_candidates(self) -> int:
        return max(1, min(HARD_MAX_CANDIDATES, self.candidate_limit))

    def bounded_wall_clock(self) -> float:
        return max(0.0, min(HARD_MAX_WALL_CLOCK_SECONDS, self.wall_clock_seconds))

    def bounded_reproduction(self) -> int:
        return max(1, min(3, self.reproduction_attempts))


@dataclass(frozen=True, slots=True)
class SearchSelection:
    """A policy's decision: which action, used how, and why.

    ``reason`` is mandatory. A search that cannot explain a choice cannot be
    audited, and an unexplained choice is indistinguishable from an arbitrary
    one.
    """

    action: SearchAction
    use: SelectionUse
    reason: str
    priority: int = 0
    """The policy's own effective priority, already resolved to a total order."""


@dataclass(frozen=True, slots=True)
class SearchStep:
    """One entry of the deterministic search trace.

    Durations are deliberately absent. They are real observations that differ
    between runs, and putting them here would make two identical searches
    produce different documents, which defeats the purpose of a trace.
    """

    step: int
    policy: PolicyName
    action: SearchAction
    use: SelectionUse
    selection_reason: str
    execution_outcome: str
    observation_description: str
    oracle_status: str
    feedback: SearchFeedback
    feedback_reason: str
    reproduction_status: str = ""
    reproduction_detail: str = ""
    executions_consumed: int = 0
    remaining_probes: int = 0
    remaining_executions: int = 0


@dataclass(frozen=True, slots=True)
class SearchState:
    """The whole search situation at one point in time.

    Immutable, and advanced only by :func:`edgehunter.search.state.update_state`.
    There is no process-global registry: two searches running in one interpreter
    cannot see each other's executed probes, because neither one holds a
    reference to the other.

    No target module, no source text, and no captured output is stored here. The
    state is a decision record, not a transcript.
    """

    target_identity: str
    contract_id: str
    source_sha256: str
    parameter: str
    policy: PolicyName
    budgets: SearchBudgets
    universe: tuple[SearchAction, ...] = ()
    """Every candidate that was generated, in static order. Fixed for the search."""

    tested: tuple[str, ...] = ()
    """Identifiers of candidates already executed as a distinct probe."""

    executions_consumed: int = 0
    recheck_executions: int = 0
    """Reruns of an already-tested candidate. Counted, never free."""

    last_action: SearchAction | None = None
    last_feedback: SearchFeedback | None = None
    last_feedback_reason: str = ""
    last_outcome: OutcomeKind | None = None
    """The raw execution outcome behind ``last_feedback``.

    Kept alongside the feedback because the two answer different questions. The
    feedback says "the contract was not satisfied"; the outcome says whether the
    target was observed at all, and therefore whether a failure was about the
    backend or about this one candidate.
    """

    last_reproduction: str = ""

    observed_match: int = 0
    observed_mismatch: int = 0
    observed_inconclusive: int = 0

    first_mismatch_probe: int | None = None
    """Index of the first distinct probe whose verdict was a mismatch."""

    reproduced_probe: int | None = None
    """Index of the first distinct probe whose mismatch was reproduced."""

    termination: SearchTermination | None = None
    termination_reason: str = ""
    steps: tuple[SearchStep, ...] = field(default=())

    @property
    def available(self) -> tuple[SearchAction, ...]:
        """Candidates not yet executed as a distinct probe, in static order."""

        seen = set(self.tested)
        return tuple(action for action in self.universe if action.identifier not in seen)

    @property
    def probes_used(self) -> int:
        return len(self.tested)

    @property
    def remaining_probes(self) -> int:
        return max(0, self.budgets.bounded_probes() - self.probes_used)

    @property
    def remaining_executions(self) -> int:
        return max(0, self.budgets.bounded_executions() - self.executions_consumed)

    @property
    def finished(self) -> bool:
        return self.termination is not None


@dataclass(frozen=True, slots=True)
class SearchResult:
    """The outcome of one whole search.

    ``measurements`` are the only quantities this phase reports, and each one is
    counted rather than estimated: there is no coverage figure, no path
    coverage, no mutation score, and no probability that a defect exists.
    """

    termination: SearchTermination
    reason: str
    target_identity: str
    contract_id: str
    parameter: str
    source_sha256: str = ""
    policy: PolicyName = PolicyName.FEEDBACK_GUIDED
    budgets: SearchBudgets = SearchBudgets()
    universe_size: int = 0
    steps: tuple[SearchStep, ...] = ()
    distinct_probes: int = 0
    executions: int = 0
    reproduction_executions: int = 0
    match_count: int = 0
    mismatch_count: int = 0
    inconclusive_count: int = 0
    first_mismatch_probe: int | None = None
    first_reproduced_probe: int | None = None
    reproduction_attempts: int = 0
    wall_clock_seconds: float = 0.0
    """Observed elapsed time. Recorded as a measurement, never used for ordering."""

    snapshot_verified: bool = False
    contract_file_sha256: str = ""
    reproduced_value: int | None = None
    """The input whose contradiction was reproduced, when one was."""

    @property
    def found_violation(self) -> bool:
        """True only for a reproduced, signature-matched contradiction."""

        return self.termination is SearchTermination.VIOLATION_REPRODUCED

    @property
    def exhausted(self) -> bool:
        """True when a bound stopped the search before the universe ran out."""

        return self.termination in (
            SearchTermination.PROBE_BUDGET_EXHAUSTED,
            SearchTermination.EXECUTION_BUDGET_EXHAUSTED,
            SearchTermination.WALL_TIME_BUDGET_EXHAUSTED,
        )


def feedback_for(outcome: OutcomeKind | None, oracle_status: str) -> SearchFeedback:
    """Map one executed probe's outcome and oracle verdict to typed feedback.

    The mapping is exact and total, written out once so no call site has to
    restate it. Two rules are not negotiable:

    * an infrastructure outcome is **never** evidence, in either direction, and
      becomes ``INFRASTRUCTURE_FAILURE`` even when a verdict happens to be
      attached to it; and
    * ``MATCH`` is only reached from an observable outcome, so a policy cannot
      read a satisfied expectation out of a failed container.
    """

    if (
        outcome is not None
        and outcome is not OutcomeKind.RETURNED
        and outcome is not OutcomeKind.RAISED
    ):
        # Everything the worker could not observe the target doing: timeout,
        # import failure, protocol error, sandbox unavailable, and so on.
        from edgehunter.evaluation.evaluator import is_infrastructure_outcome

        if is_infrastructure_outcome(outcome):
            return SearchFeedback.INFRASTRUCTURE_FAILURE

    observed = outcome is OutcomeKind.RETURNED or outcome is OutcomeKind.RAISED
    match oracle_status:
        case "match":
            return (
                SearchFeedback.EXPECTED_BEHAVIOR
                if observed
                else SearchFeedback.INFRASTRUCTURE_FAILURE
            )
        case "mismatch":
            return (
                SearchFeedback.EXPECTATION_MISMATCH
                if observed
                else SearchFeedback.INFRASTRUCTURE_FAILURE
            )
        case "inconclusive" | "not_applicable" | "":
            return SearchFeedback.INCONCLUSIVE
        case "unsupported":
            return SearchFeedback.UNSUPPORTED
        case "conflicting_evidence":
            return SearchFeedback.CONFLICTING_EVIDENCE
    return SearchFeedback.INCONCLUSIVE
