"""Pure state transitions for the search loop.

Every function here takes a state and returns a new one. Nothing is mutated in
place, nothing is stored globally, and no module-level registry of "candidates
already tried" exists anywhere in the package. That is what makes the search
deterministic by construction rather than by discipline: two identical sequences
of inputs produce identical states, and two searches in one interpreter cannot
influence each other.

The transitions are also the only place the counters live. Probe accounting,
execution accounting, and the verdict tallies all advance here, so a caller can
never report a budget it did not actually spend.
"""

from __future__ import annotations

from dataclasses import replace

from edgehunter.execution.models import OutcomeKind
from edgehunter.search.models import (
    SearchAction,
    SearchFeedback,
    SearchState,
    SearchStep,
    SearchTermination,
)


def update_state(
    state: SearchState,
    action: SearchAction,
    feedback: SearchFeedback,
    *,
    reason: str,
    executions: int,
    outcome: OutcomeKind | None = None,
    reproduction: str = "",
    termination: SearchTermination | None = None,
) -> SearchState:
    """Fold one probe's outcome into the state.

    ``executions`` is the number of containers this probe actually launched,
    including any verification rerun and including a container that failed to
    produce evidence. A probe that turned out to be a rerun of an
    already-tested candidate therefore consumes an execution without consuming a
    distinct-probe slot, and the two counters stay separate on purpose.
    """

    tested = state.tested
    was_tested = action.identifier in tested
    if not was_tested:
        tested = (*tested, action.identifier)

    match_count = state.observed_match
    mismatch_count = state.observed_mismatch
    inconclusive_count = state.observed_inconclusive
    if feedback is SearchFeedback.EXPECTED_BEHAVIOR:
        match_count += 1
    elif feedback is SearchFeedback.EXPECTATION_MISMATCH:
        mismatch_count += 1
    elif feedback is not SearchFeedback.INFRASTRUCTURE_FAILURE:
        inconclusive_count += 1

    # A rerun is recognised by the candidate already having been tested, not by
    # the container count: one step may launch a probe and its verification in a
    # single call, and both spellings have to land in the same bucket.
    rechecks = executions if was_tested else max(0, executions - 1)

    return replace(
        state,
        tested=tested,
        executions_consumed=state.executions_consumed + executions,
        recheck_executions=state.recheck_executions + rechecks,
        last_action=action,
        last_feedback=feedback,
        last_feedback_reason=reason,
        last_outcome=outcome,
        last_reproduction=reproduction,
        observed_match=match_count,
        observed_mismatch=mismatch_count,
        observed_inconclusive=inconclusive_count,
        termination=termination or state.termination,
    )


def note_first_mismatch(state: SearchState, probe_index: int) -> SearchState:
    """Record where the first contradiction appeared, once."""

    if state.first_mismatch_probe is not None:
        return state
    return replace(state, first_mismatch_probe=probe_index)


def note_reproduced(state: SearchState, probe_index: int) -> SearchState:
    """Record where a contradiction was reproduced, once."""

    if state.reproduced_probe is not None:
        return state
    return replace(state, reproduced_probe=probe_index)


def record_step(state: SearchState, step: SearchStep) -> SearchState:
    """Append one trace entry to the state."""

    return replace(state, steps=(*state.steps, step))


def terminate(state: SearchState, termination: SearchTermination, reason: str) -> SearchState:
    """Stop the search with an explicit, non-collapsible status.

    Every loop exit funnels through here, so a state can never finish without a
    termination status and a report can never have to guess why it ended.
    """

    return replace(state, termination=termination, termination_reason=reason)
