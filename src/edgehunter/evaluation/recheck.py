"""Re-execution of one plan, bounded and only when explicitly requested.

Rechecking lives beside the execution layer rather than in the evaluator, for two
reasons that are architectural rather than stylistic:

* **The evaluator stays pure.** :mod:`edgehunter.evaluation.evaluator` is a
  function over decoded observations; it starts no process. This module is the
  only place that asks for another container run, and it does so through exactly
  the same authorization and staging path as the first attempt.
* **Authorization is not bypassable.** :func:`execute_with_rechecks` requires the
  same ``authorized`` flag as :func:`edgehunter.execution.repository.execute_target`
  and reuses its own selection, staging, and cleanup. There is no separate,
  weaker route into a container.

The rules:

* Zero rechecks is the default. Nothing retries automatically.
* The count is clamped to :data:`~edgehunter.evaluation.repository.HARD_MAX_RECHECKS`.
* Every attempt reuses the *same* staged snapshot, so all attempts measure the
  same source revision. A source change between attempts is impossible by
  construction rather than by check.
* Each attempt gets its own request nonce and execution id, exactly like a first
  attempt. The protocol is not weakened to make repetition convenient.
* A total attempt budget is enforced: ``1 + rechecks`` containers per plan, always.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from edgehunter.execution.models import ExecutionResult, OutcomeKind

# An attempt that could not produce an observation. It ends a reproduction verdict
# rather than confirming it.
UNUSABLE_OUTCOMES: Final[frozenset[OutcomeKind]] = frozenset(
    {
        OutcomeKind.TIMEOUT,
        OutcomeKind.IMPORT_FAILURE,
        OutcomeKind.TARGET_NOT_FOUND,
        OutcomeKind.PROTOCOL_ERROR,
        OutcomeKind.WORKER_FAILURE,
        OutcomeKind.RESOURCE_LIMIT,
        OutcomeKind.SANDBOX_UNAVAILABLE,
        OutcomeKind.INELIGIBLE,
    }
)


@dataclass(frozen=True, slots=True)
class AttemptSequence:
    """Every attempt made for one plan, in order."""

    results: tuple[ExecutionResult, ...]

    @property
    def first(self) -> ExecutionResult | None:
        return self.results[0] if self.results else None

    @property
    def rechecks(self) -> tuple[ExecutionResult, ...]:
        return self.results[1:]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def usable_count(self) -> int:
        """Attempts that produced a real observation of the target's behaviour."""
        return sum(
            1
            for result in self.results
            if result.observation is not None and result.outcome not in UNUSABLE_OUTCOMES
        )

    @property
    def had_infrastructure_failure(self) -> bool:
        """True when at least one attempt failed for a reason unrelated to the target."""
        return any(
            result.observation is None or result.outcome in UNUSABLE_OUTCOMES
            for result in self.results
        )


def build_sequence(results: list[ExecutionResult]) -> AttemptSequence:
    """Freeze an attempt list into a sequence."""
    return AttemptSequence(results=tuple(results))
