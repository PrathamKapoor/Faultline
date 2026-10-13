"""Search policies: which available action to try next.

A policy here is a pure decision function over typed values. It reads a
:class:`SearchState` and a tuple of typed :class:`SearchAction` objects and
returns a :class:`SearchSelection`. It may not import target code, launch a
container, evaluate an oracle, read the clock, or consult randomness. Those
restrictions are what make the policies testable without Docker and what leave
room for a future learned model to implement the same interface.

Two policies exist, and the second is only meaningful because the first is
honest:

:class:`StaticPriorityPolicy` follows one documented fixed order and *never*
looks at runtime feedback. It is the baseline every claim about the adaptive
policy is measured against, so it must not quietly become adaptive.

:class:`FeedbackGuidedPolicy` uses the previous probe's typed feedback to choose
the next action. Its rules are written out as an explicit decision table below
rather than described in a docstring, because a transition that only exists in
prose is not a transition the tests can pin down.
"""

from __future__ import annotations

from typing import Protocol

from edgehunter.search.models import (
    PolicyName,
    SearchAction,
    SearchFeedback,
    SearchSelection,
    SearchState,
    SelectionUse,
    is_target_independent,
)


class SearchPolicy(Protocol):
    """The interface a future learned policy must be able to implement.

    Note what is absent: no executor, no evaluator, no Typer, no terminal
    formatting. Swapping :class:`StaticPriorityPolicy` for a small model later
    must not require touching the orchestrator.
    """

    name: PolicyName

    def select(
        self, state: SearchState, available: tuple[SearchAction, ...]
    ) -> SearchSelection | None:
        """Choose the next action, or ``None`` to stop.

        Returning ``None`` rather than raising is deliberate: "I have nothing
        left worth trying" is a legitimate, common answer, and it lets the
        orchestrator report it without catching an exception as control flow.
        """


class StaticPriorityPolicy:
    """The deterministic nonadaptive baseline.

    Ordering, fixed once and documented here:

    1. the contract anchor,
    2. the user seed, when distinct,
    3. source-derived boundaries,
    4. midpoints towards the anchor,
    5. remaining integer neighbours.

    This is :meth:`SearchAction.static_order_key` -- ``(origin rank, action kind
    rank, abs(value), value, identifier)`` -- and every component is total. The
    baseline receives exactly the same candidate universe, budgets, executor,
    and evaluator as the adaptive policy; the only difference is that it reads
    no feedback whatsoever.
    """

    name = PolicyName.STATIC_PRIORITY

    def select(
        self, state: SearchState, available: tuple[SearchAction, ...]
    ) -> SearchSelection | None:
        if not available:
            return None
        # `available` is already in static order; sorting again makes the
        # baseline robust to a caller that hands over an unordered tuple.
        chosen = min(available, key=lambda action: action.static_order_key)
        return SearchSelection(
            action=chosen,
            use=SelectionUse.NEW_PROBE,
            reason=(
                "static priority baseline: lowest (origin, action kind, magnitude, value, "
                "identifier) key among the untested candidates; runtime feedback is not consulted"
            ),
            priority=chosen.priority,
        )


class FeedbackGuidedPolicy:
    """A policy whose next choice depends on the previous probe's verdict.

    The decision table, which is what :meth:`select` implements literally:

    ==============================  ====================================================
    Previous feedback               Next decision
    ==============================  ====================================================
    ``EXPECTED_BEHAVIOR``           The nearest untested domain-valid neighbour of the
                                   last probe's value. Nearby inputs are the cheapest
                                   place to find out whether the observed behaviour
                                   holds just past the boundary the last probe sat on.
    ``INCONCLUSIVE``                Next eligible candidate in static order. Decided
                                   nothing, so nothing was learned from it.
    ``UNSUPPORTED``                 Next eligible candidate in static order, with the
                                   reason recorded. The probe was not comparable.
    ``INFRASTRUCTURE_FAILURE``      Stop **only** when the failure was target-independent
                                   -- a container that could not start will not start for
                                   the next candidate either. A timeout or an import
                                   failure may depend on the input, so the search
                                   continues with the reason recorded.
    ``CONFLICTING_EVIDENCE``        Stop. Applicable evidence disagrees, so no verdict
                                   about this target is trustworthy.
    ==============================  ====================================================

    The first row is the whole point of the phase: it makes the second probe a
    *function of the first probe's verdict* while staying a deterministic
    function of the state.

    Note what is **not** in this table: a mismatch. Deciding that a
    contradiction must be re-executed is the orchestrator's job, applied
    identically to both policies. Giving each policy its own reproduction rule
    would let the baseline confirm fewer findings than the adaptive policy purely
    by being written differently, which is exactly the comparison this phase must
    not make.
    """

    name = PolicyName.FEEDBACK_GUIDED

    def select(
        self, state: SearchState, available: tuple[SearchAction, ...]
    ) -> SearchSelection | None:
        feedback = state.last_feedback
        last_action = state.last_action

        if feedback is SearchFeedback.CONFLICTING_EVIDENCE:
            return None
        if feedback is SearchFeedback.INFRASTRUCTURE_FAILURE and is_target_independent(
            state.last_outcome
        ):
            return None

        if not available:
            return None

        if feedback is SearchFeedback.EXPECTED_BEHAVIOR and last_action is not None:
            nearby = min(available, key=lambda action: action.distance_key(last_action.value))
            return SearchSelection(
                action=nearby,
                use=SelectionUse.NEW_PROBE,
                reason=(
                    f"the previous probe at {state.parameter} = {last_action.value} behaved as the "
                    f"contract expects, so the nearest untested domain-valid input "
                    f"({state.parameter} = {nearby.value}) is tried next to test whether the "
                    "observed behaviour holds just past that point"
                ),
                priority=nearby.priority,
            )

        # No usable verdict yet, or a verdict that taught us nothing: fall back
        # to the shared static order so the two policies only diverge when the
        # feedback actually justifies it.
        chosen = min(available, key=lambda action: action.static_order_key)
        if feedback is None:
            reason = (
                "no probe has run yet, so the shared static order applies: "
                f"{state.parameter} = {chosen.value}"
            )
        else:
            reason = (
                f"the previous probe was {feedback.value}, which decided nothing about the "
                f"contract, so the shared static order continues at "
                f"{state.parameter} = {chosen.value}"
            )
        return SearchSelection(
            action=chosen,
            use=SelectionUse.NEW_PROBE,
            reason=reason,
            priority=chosen.priority,
        )


def policy_for(name: PolicyName) -> SearchPolicy:
    """Build the one policy registered for a name."""

    match name:
        case PolicyName.STATIC_PRIORITY:
            return StaticPriorityPolicy()
        case PolicyName.FEEDBACK_GUIDED:
            return FeedbackGuidedPolicy()
    raise ValueError(f"no policy is registered for {name!r}")
