"""Typed vocabulary for shrinkability assessment and bounded minimization.

Two ideas carry this module, and everything else follows from them.

**An oracle is either reusable or it is not.** A ``pytest.raises(ValueError)``
recorded in a test describes one exact invocation. Changing that invocation's
input invalidates the oracle, so the only honest move is to refuse. A
*user-declared contract* states a condition over a declared integer domain, so
every input inside that domain keeps the same oracle. :class:`ShrinkabilityKind`
is the difference, made a type.

**A smaller input is not automatically a smaller counterexample.** Acceptance
requires the same target, the same staged source, the same contract, a still
applicable oracle, a still-failing observation, an equivalent *failure
signature*, and a reproduced result. :class:`ShrinkAttempt` records each of
those checks and, when one fails, the reason it failed.

Nothing here executes anything. No status below is a severity, a confidence
number, or a claim that the code is wrong: a mismatch is disagreement with one
explicit user declaration, and the search is bounded and heuristic.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

# Signed 64-bit range. A contract may not name an integer outside it, and neither
# may a proposal: an unbounded integer would let a file drive arbitrary
# allocation and text into the planner.
MIN_SIGNED_INT: Final[int] = -(2**63)
MAX_SIGNED_INT: Final[int] = 2**63 - 1

# The whole cost model of the search, in one place.
DEFAULT_MAX_PROPOSALS: Final[int] = 8
HARD_MAX_PROPOSALS: Final[int] = 20
DEFAULT_MAX_EXECUTIONS: Final[int] = 32
HARD_MAX_EXECUTIONS: Final[int] = 64
DEFAULT_REPRODUCTION_ATTEMPTS: Final[int] = 2
HARD_MAX_REPRODUCTION_ATTEMPTS: Final[int] = 3


class ShrinkabilityKind(str, Enum):
    """Whether the available oracle evidence survives changing an input."""

    REUSABLE_CONTRACT = "reusable_contract"
    """A user-declared contract whose domain still contains the input.

    The only kind that permits shrinking. Reached solely from an explicitly
    selected contract file, never inferred from a production guard or a test.
    """

    EXACT_INPUT_ONLY = "exact_input_only"
    """A recorded example that describes one exact invocation.

    ``assert f(100) == 200`` says nothing about ``f(10)``. This is the most
    important refusal in the package.
    """

    UNSUPPORTED_ORACLE = "unsupported_oracle"
    """Evidence that constrains no input domain at all.

    A production ``assert`` states a precondition at one execution point, and an
    ``if cond: raise`` guard says nothing about whether the branch is reached.
    Neither is a reusable contract, however simple the condition looks.
    """

    UNVERIFIED_SOURCE = "unverified_source"
    """The executed revision could not be proven to be the analyzed one."""

    INCONCLUSIVE = "inconclusive"
    """The oracle itself could not be decided: unresolved or ambiguous target,
    or a custom exception whose identity is unestablished."""

    def permits_input_change(self) -> bool:
        """True only for an oracle that genuinely applies across a domain."""
        return self is ShrinkabilityKind.REUSABLE_CONTRACT


class OracleSource(str, Enum):
    """Where a reusable oracle came from, stated explicitly and never blurred.

    A user declaration is not a test, not an assertion, and not a guard, and the
    distinction survives into the artifact and the CLI output so nobody reads a
    mismatch against a declared contract as a fact proven from the repository.
    """

    USER_DECLARED_CONTRACT = "user_declared_contract"
    TEST_ASSERTION = "test_assertion"
    TEST_RAISES = "test_raises"
    PRODUCTION_ASSERTION = "production_assertion"
    VALIDATION_GUARD = "validation_guard"
    NONE = "none"


class ShrinkStatus(str, Enum):
    """The outcome of a whole minimization operation."""

    SHRUNK = "shrunk"
    """A strictly smaller input was verified against the same contract.

    Not a global minimum: only the smallest input this bounded search tried and
    confirmed.
    """

    NO_SMALLER_CASE_FOUND = "no_smaller_case_found"
    """Every proposal was exhausted and none qualified as an accepted reduction.

    Distinct from ``BUDGET_EXHAUSTED``: the proposal list ran out, not the budget.
    """

    BUDGET_EXHAUSTED = "budget_exhausted"
    """A budget stopped the search. Never reported as ``SHRUNK``."""

    NOT_SHRINKABLE = "not_shrinkable"
    """The available oracle does not survive an input change, so nothing ran."""

    ORIGINAL_NOT_FAILING = "original_not_failing"
    """The seed did not contradict the contract, so there was nothing to shrink."""

    ORIGINAL_NOT_REPRODUCIBLE = "original_not_reproducible"
    """The seed contradicted the contract but not consistently."""

    INCONCLUSIVE = "inconclusive"
    """The seed's result could not be decided: infrastructure failure, unverified
    source, or an unverifiable contract."""

    REFUSED = "refused"
    """An input was rejected before execution: bad contract, fingerprint mismatch,
    ineligible target, or a seed outside the declared domain."""


class AttemptOutcome(str, Enum):
    """Why one execution attempt did or did not support acceptance."""

    ACCEPTED = "accepted"
    NOT_SIMPLER = "not_simpler"
    OUTSIDE_DOMAIN = "outside_domain"
    ORACLE_NOT_APPLICABLE = "oracle_not_applicable"
    ORACLE_NOT_MISMATCH = "oracle_not_mismatch"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    SIGNATURE_MISMATCH = "signature_mismatch"
    NOT_REPRODUCIBLE = "not_reproducible"
    BUDGET_EXHAUSTED = "budget_exhausted"


class InvocationOrigin(str, Enum):
    """Where the input under test came from.

    A shrinker that reported its own output as though the action layer had
    proposed it would be claiming provenance it does not have, so the origin is
    part of the typed record rather than prose.
    """

    USER_SUPPLIED_SEED = "user_supplied_seed"
    """The integer the user asked to start from. The only entry point in 7B."""

    INTEGER_SHRINK_PROPOSAL = "integer_shrink_proposal"
    """A candidate this shrinker derived. A minimization step, not a Phase 5
    adversarial action."""


@dataclass(frozen=True, slots=True)
class ShrinkabilityAssessment:
    """Whether one oracle survives changing an input, and why."""

    kind: ShrinkabilityKind
    reason: str
    source: OracleSource
    oracle_description: str
    oracle_location: str = ""
    """``path:line`` for repository evidence; the contract id for a declaration."""

    def permits_input_change(self) -> bool:
        """True when this oracle may be applied to a different input."""
        return self.kind.permits_input_change()


@dataclass(frozen=True, slots=True)
class ShrinkPolicy:
    """The bounds one minimization operation runs under.

    Every field is clamped, so an unreasonable request costs a smaller search
    rather than an unbounded one. Nothing here retries on its own.
    """

    max_proposals: int = DEFAULT_MAX_PROPOSALS
    max_executions: int = DEFAULT_MAX_EXECUTIONS
    reproduction_attempts: int = DEFAULT_REPRODUCTION_ATTEMPTS

    def bounded_proposals(self) -> int:
        return max(0, min(HARD_MAX_PROPOSALS, self.max_proposals))

    def bounded_executions(self) -> int:
        return max(0, min(HARD_MAX_EXECUTIONS, self.max_executions))

    def bounded_reproduction(self) -> int:
        return max(1, min(HARD_MAX_REPRODUCTION_ATTEMPTS, self.reproduction_attempts))


@dataclass(frozen=True, slots=True)
class ShrinkAttempt:
    """One proposed integer and every check applied to it.

    ``executions_consumed`` counts containers, including ones that failed to
    produce evidence: an unusable attempt still costs a container and still
    invalidates verification, but it is never counted as a mismatch.
    """

    value: int
    origin: InvocationOrigin
    complexity: int
    """``abs(value)``, the single simplification metric for Phase 7B."""

    in_domain: bool
    executed: bool
    outcome: AttemptOutcome
    reason: str
    oracle_status: str = ""
    """The contract verdict as a plain string; the enum lives in ``evaluation``."""

    failure_signature: str = ""
    signature_matches: bool = False
    executions_consumed: int = 0
    equivalent_observations: int = 0
    """How many attempts agreed, which is what reproduction actually means."""

    @property
    def accepted(self) -> bool:
        return self.outcome is AttemptOutcome.ACCEPTED


@dataclass(frozen=True, slots=True)
class ShrinkResult:
    """The outcome of one whole minimization operation.

    ``best_value`` is the smallest input that was *verified*, which is not the
    same as the smallest input that exists. ``proposals_exhausted`` is recorded
    separately from ``status`` so a budget stop can never be read as success.
    """

    status: ShrinkStatus
    reason: str
    target_identity: str
    contract_id: str
    parameter: str
    seed_value: int
    seed_status: str = ""
    seed_signature: str = ""
    best_value: int | None = None
    best_signature: str = ""
    attempts: tuple[ShrinkAttempt, ...] = ()
    proposals_considered: int = 0
    proposals_exhausted: bool = False
    executions_consumed: int = 0
    policy: ShrinkPolicy = ShrinkPolicy()
    snapshot_verified: bool = False
    contract_source: OracleSource = OracleSource.USER_DECLARED_CONTRACT

    @property
    def reduced(self) -> bool:
        """True when a strictly smaller input was verified."""
        return self.status is ShrinkStatus.SHRUNK and self.best_value is not None

    @property
    def executions_remaining(self) -> int:
        return max(0, self.policy.bounded_executions() - self.executions_consumed)
