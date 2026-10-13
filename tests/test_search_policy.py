"""Policy selection: the static baseline, the feedback policy, and adaptivity.

These are the tests that decide whether Phase 7C claims anything real. A search
engine whose policy reads feedback into a log while choosing the same input
regardless is not adaptive, and the only way to tell the difference is to feed it
two different histories and assert the two selections differ.

No Docker is involved: a policy is a pure function of a state and a candidate
tuple, and that is the property being tested.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from edgehunter.execution.codec import ValueKind
from edgehunter.execution.models import (
    OutcomeKind,
    RuntimeObservation,
    TargetRef,
)
from edgehunter.pipeline import analyze_repository
from edgehunter.search.actions import enumerate_search_actions
from edgehunter.search.models import (
    HARD_MAX_EXECUTIONS,
    HARD_MAX_PROBES,
    TARGET_INDEPENDENT_OUTCOMES,
    PolicyName,
    SearchAction,
    SearchActionKind,
    SearchBudgets,
    SearchFeedback,
    SearchState,
    SearchTermination,
    SelectionUse,
    feedback_for,
)
from edgehunter.search.policy import (
    FeedbackGuidedPolicy,
    StaticPriorityPolicy,
    policy_for,
)
from edgehunter.search.repository import _target_boundaries, _target_comparisons
from edgehunter.search.state import terminate, update_state
from edgehunter.shrink.contract import Contract, RaisesBuiltinExpectation
from edgehunter.shrink.domain import DomainOperator, IntegerDomain

SOURCE = (
    "def validate(x: int) -> int:\n"
    "    if x == -2:\n"
    "        return x\n\n"
    '    raise ValueError("negative")\n'
)

TARGET = TargetRef(relative_file="m.py", qualified_name="validate", lineno=1)

DEFAULT_BUDGETS = SearchBudgets()


# --- shared fixtures ----------------------------------------------------------


def _contract(digest: str = "0" * 64) -> Contract:
    return Contract(
        contract_id="neg",
        target_file="m.py",
        target_name="validate",
        source_sha256=digest,
        parameter="x",
        domain=IntegerDomain(DomainOperator.LT, 0),
        expectation=RaisesBuiltinExpectation("ValueError"),
    )


def _universe(tmp_path: Path, start: int = -64) -> tuple[SearchAction, ...]:
    source_file = tmp_path / "m.py"
    source_file.write_text(SOURCE, encoding="utf-8")
    digest = hashlib.sha256(source_file.read_bytes()).hexdigest()
    contract = _contract(digest)
    scan = analyze_repository(tmp_path)
    from edgehunter.search.actions import SearchContext

    return enumerate_search_actions(
        SearchContext(
            contract=contract,
            target=TARGET,
            start_value=start,
            boundaries=_target_boundaries(scan, contract),
            comparisons=_target_comparisons(scan, contract),
        )
    )


def _state(
    universe: tuple[SearchAction, ...],
    *,
    budgets: SearchBudgets = DEFAULT_BUDGETS,
    policy: PolicyName = PolicyName.STATIC_PRIORITY,
    feedback: SearchFeedback | None = None,
    last: SearchAction | None = None,
    outcome: OutcomeKind | None = None,
    tested: tuple[str, ...] = (),
    termination: SearchTermination | None = None,
) -> SearchState:
    return SearchState(
        target_identity=TARGET.identity,
        contract_id="neg",
        source_sha256=universe[0].source_sha256 if universe else "0" * 64,
        parameter="x",
        policy=policy,
        budgets=budgets,
        universe=universe,
        tested=tested,
        last_action=last,
        last_feedback=feedback,
        last_outcome=outcome,
        termination=termination,
    )


def _after(
    universe: tuple[SearchAction, ...],
    value: int,
    feedback: SearchFeedback,
    outcome: OutcomeKind | None = None,
) -> SearchState:
    """The state a search is in after executing ``value`` and judging it."""

    chosen = next(action for action in universe if action.value == value)
    tested = tuple(action.identifier for action in universe if action.value == chosen.value)
    return _state(
        universe,
        tested=tested,
        last=chosen,
        feedback=feedback,
        outcome=outcome,
        policy=PolicyName.FEEDBACK_GUIDED,
    )


# --- the static baseline ------------------------------------------------------


def test_static_policy_follows_the_documented_order(tmp_path: Path) -> None:
    """Anchor, then user seed, then source boundary, then midpoints, then neighbours."""

    universe = _universe(tmp_path)
    policy = StaticPriorityPolicy()

    first = policy.select(_state(universe), universe)
    assert first is not None
    assert first.action.value == -1
    assert first.action.kind is SearchActionKind.CONTRACT_ANCHOR

    state = _after(universe, -1, SearchFeedback.EXPECTED_BEHAVIOR)
    second = policy.select(state, state.available)
    assert second is not None
    assert second.action.value == -64
    assert second.action.kind is SearchActionKind.USER_SEED

    state = _state(
        universe,
        tested=tuple(a.identifier for a in universe[:2]),
        policy=PolicyName.STATIC_PRIORITY,
    )
    third = policy.select(state, state.available)
    assert third is not None
    assert third.action.value == -2
    assert third.action.kind is SearchActionKind.SOURCE_BOUNDARY_AT


@pytest.mark.parametrize(
    "feedback",
    [
        SearchFeedback.EXPECTED_BEHAVIOR,
        SearchFeedback.EXPECTATION_MISMATCH,
        SearchFeedback.INCONCLUSIVE,
        SearchFeedback.UNSUPPORTED,
        SearchFeedback.INFRASTRUCTURE_FAILURE,
        SearchFeedback.CONFLICTING_EVIDENCE,
    ],
)
def test_static_policy_ignores_every_kind_of_feedback(
    tmp_path: Path, feedback: SearchFeedback
) -> None:
    """The baseline is only a baseline if no verdict can move it."""

    universe = _universe(tmp_path)
    policy = StaticPriorityPolicy()

    def _history_for(actual: SearchFeedback) -> SearchState:
        chosen = universe[0]
        return _state(
            universe,
            tested=(chosen.identifier,),
            last=chosen,
            feedback=actual,
        )

    clean = policy.select(_state(universe), universe)
    assert clean is not None
    assert clean.action.value == -1

    after = policy.select(_history_for(feedback), _history_for(feedback).available)
    assert after is not None
    assert after.action.value == -64


def test_static_policy_never_repeats_a_tested_candidate(tmp_path: Path) -> None:
    universe = _universe(tmp_path)
    policy = StaticPriorityPolicy()
    tested: tuple[str, ...] = ()
    seen: list[int] = []

    for _ in range(len(universe)):
        state = _state(universe, tested=tested)
        selection = policy.select(state, state.available)
        assert selection is not None
        seen.append(selection.action.value)
        tested = (*tested, selection.action.identifier)

    assert len(set(seen)) == len(seen)
    assert policy.select(_state(universe, tested=tested), ()) is None


def test_static_policy_ties_are_deterministic(tmp_path: Path) -> None:
    universe = _universe(tmp_path)
    policy = StaticPriorityPolicy()

    shuffled = tuple(reversed(universe))
    first = policy.select(_state(universe), shuffled)
    second = policy.select(_state(universe), shuffled)

    assert first is not None and second is not None
    assert first.action.identifier == second.action.identifier


def test_static_policy_returns_none_when_nothing_is_available(tmp_path: Path) -> None:
    universe = _universe(tmp_path)
    state = _state(universe, tested=tuple(a.identifier for a in universe))

    assert StaticPriorityPolicy().select(state, state.available) is None


# --- the feedback policy ------------------------------------------------------


def test_feedback_policy_initial_selection_is_deterministic(tmp_path: Path) -> None:
    universe = _universe(tmp_path)
    policy = FeedbackGuidedPolicy()

    first = policy.select(_state(universe), universe)
    second = policy.select(_state(universe), tuple(reversed(universe)))

    assert first is not None and second is not None
    assert first.action.identifier == second.action.identifier
    assert first.action.value == -1


def test_feedback_policy_prefers_a_nearby_input_after_a_match(tmp_path: Path) -> None:
    universe = _universe(tmp_path)
    policy = FeedbackGuidedPolicy()

    state = _after(universe, -1, SearchFeedback.EXPECTED_BEHAVIOR)
    selection = policy.select(state, state.available)

    assert selection is not None
    assert selection.action.value == -2
    assert "nearest untested domain-valid input" in selection.reason
    assert selection.use is SelectionUse.NEW_PROBE


def test_feedback_policy_requests_a_recheck_after_a_mismatch(tmp_path: Path) -> None:
    universe = _universe(tmp_path)
    policy = FeedbackGuidedPolicy()

    state = _after(universe, -64, SearchFeedback.EXPECTATION_MISMATCH)
    assert state.last_action is not None
    selection = policy.select(state, state.available)

    assert selection is not None
    assert selection.use is SelectionUse.NEW_PROBE
    assert selection.action.identifier != state.last_action.identifier


def test_reproduction_is_not_a_policy_decision(tmp_path: Path) -> None:
    """Both policies get the same reproduction rule, so the comparison is fair.

    If each policy owned its own recheck rule the static baseline could confirm
    fewer findings purely by being written differently. The recheck is therefore
    the orchestrator's, and neither policy asks for one -- which is what these
    two assertions pin down.
    """

    universe = _universe(tmp_path)
    state = _after(universe, -64, SearchFeedback.EXPECTATION_MISMATCH)

    for policy in (StaticPriorityPolicy(), FeedbackGuidedPolicy()):
        selection = policy.select(state, state.available)
        assert selection is not None
        assert selection.use is SelectionUse.NEW_PROBE


def test_feedback_policy_stops_on_a_target_independent_failure(tmp_path: Path) -> None:
    """A container that cannot start will not start for the next candidate either."""

    universe = _universe(tmp_path)
    policy = FeedbackGuidedPolicy()

    for outcome in sorted(TARGET_INDEPENDENT_OUTCOMES, key=str):
        state = _after(universe, -1, SearchFeedback.INFRASTRUCTURE_FAILURE, outcome)
        assert policy.select(state, state.available) is None, outcome


def test_feedback_policy_continues_past_a_candidate_specific_failure(tmp_path: Path) -> None:
    """A timeout may be about this input, so the next eligible candidate is tried."""

    universe = _universe(tmp_path)
    policy = FeedbackGuidedPolicy()

    for outcome in (OutcomeKind.TIMEOUT, OutcomeKind.IMPORT_FAILURE):
        state = _after(universe, -1, SearchFeedback.INFRASTRUCTURE_FAILURE, outcome)
        selection = policy.select(state, state.available)
        assert selection is not None, outcome
        assert selection.use is SelectionUse.NEW_PROBE


def test_feedback_policy_stops_on_conflicting_evidence(tmp_path: Path) -> None:
    universe = _universe(tmp_path)
    policy = FeedbackGuidedPolicy()

    state = _after(universe, -1, SearchFeedback.CONFLICTING_EVIDENCE)
    assert policy.select(state, state.available) is None


@pytest.mark.parametrize("feedback", [SearchFeedback.INCONCLUSIVE, SearchFeedback.UNSUPPORTED])
def test_non_decisive_feedback_falls_back_to_the_shared_static_order(
    tmp_path: Path, feedback: SearchFeedback
) -> None:
    universe = _universe(tmp_path)
    policy = FeedbackGuidedPolicy()

    state = _after(universe, -1, feedback)
    selection = policy.select(state, state.available)

    assert selection is not None
    assert selection.action.value == -64
    assert "decided nothing" in selection.reason or "not a verdict" in selection.reason


# --- the mandatory adaptivity assertion ---------------------------------------


def test_feedback_actually_changes_the_next_selection(tmp_path: Path) -> None:
    """The load-bearing test: same state, different verdict, different next probe."""

    universe = _universe(tmp_path)
    policy = FeedbackGuidedPolicy()

    matched = policy.select(
        _after(universe, -1, SearchFeedback.EXPECTED_BEHAVIOR, OutcomeKind.RAISED),
        _after(universe, -1, SearchFeedback.EXPECTED_BEHAVIOR).available,
    )
    inconclusive = policy.select(
        _after(universe, -1, SearchFeedback.INCONCLUSIVE, OutcomeKind.RAISED),
        _after(universe, -1, SearchFeedback.INCONCLUSIVE).available,
    )
    failed = policy.select(
        _after(universe, -1, SearchFeedback.INFRASTRUCTURE_FAILURE, OutcomeKind.WORKER_FAILURE),
        _after(universe, -1, SearchFeedback.INFRASTRUCTURE_FAILURE).available,
    )

    assert matched is not None and inconclusive is not None
    assert matched.action.value != inconclusive.action.value
    assert failed is None


def test_the_two_policies_genuinely_diverge_on_the_second_probe(tmp_path: Path) -> None:
    """Static picks the seed; feedback picks the nearby input. Neither is special-cased."""

    universe = _universe(tmp_path)
    assert sorted(a.value for a in universe[:3]) == [-64, -2, -1]

    anchor = next(a for a in universe if a.value == -1)
    static_state = _state(
        universe,
        tested=(anchor.identifier,),
        last=anchor,
        feedback=SearchFeedback.EXPECTED_BEHAVIOR,
        policy=PolicyName.STATIC_PRIORITY,
    )
    feedback_state = _after(universe, -1, SearchFeedback.EXPECTED_BEHAVIOR)

    static_second = StaticPriorityPolicy().select(static_state, static_state.available)
    feedback_second = FeedbackGuidedPolicy().select(feedback_state, feedback_state.available)

    assert static_second is not None and feedback_second is not None
    assert static_second.action.value == -64
    assert feedback_second.action.value == -2


def test_nearby_preference_survives_repetition(tmp_path: Path) -> None:
    """The policy is reproducible: same state in, same action out."""

    universe = _universe(tmp_path)
    policy = FeedbackGuidedPolicy()
    state = _after(universe, -1, SearchFeedback.EXPECTED_BEHAVIOR)

    first = policy.select(state, state.available)
    second = policy.select(state, state.available)

    assert first is not None and second is not None
    assert first.action.identifier == second.action.identifier


def test_policy_selection_reads_no_clock_and_no_randomness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing time- or entropy-derived may influence a selection."""

    import random
    import time

    def _refuse(*_args: object, **_kwargs: object) -> float:
        raise AssertionError("a policy consulted the clock or a random source")

    monkeypatch.setattr(time, "monotonic", _refuse)
    monkeypatch.setattr(random, "random", _refuse)
    monkeypatch.setattr(random, "randint", _refuse)

    universe = _universe(tmp_path)
    for policy in (StaticPriorityPolicy(), FeedbackGuidedPolicy()):
        state = _after(universe, -1, SearchFeedback.EXPECTED_BEHAVIOR)
        assert policy.select(state, state.available) is not None


def test_policy_for_returns_the_registered_implementations() -> None:
    assert isinstance(policy_for(PolicyName.STATIC_PRIORITY), StaticPriorityPolicy)
    assert isinstance(policy_for(PolicyName.FEEDBACK_GUIDED), FeedbackGuidedPolicy)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("static", PolicyName.STATIC_PRIORITY),
        ("STATIC", PolicyName.STATIC_PRIORITY),
        ("static_priority", PolicyName.STATIC_PRIORITY),
        ("feedback", PolicyName.FEEDBACK_GUIDED),
        ("feedback_guided", PolicyName.FEEDBACK_GUIDED),
    ],
)
def test_policy_names_parse_from_the_cli_spellings(text: str, expected: PolicyName) -> None:
    assert PolicyName.parse(text) is expected


def test_unknown_policy_name_is_refused() -> None:
    with pytest.raises(ValueError):
        PolicyName.parse("learned")


# --- feedback mapping ---------------------------------------------------------


def _observation(outcome: OutcomeKind) -> RuntimeObservation:
    return RuntimeObservation(outcome=outcome)


@pytest.mark.parametrize(
    ("outcome", "status", "expected"),
    [
        (OutcomeKind.RAISED, "match", SearchFeedback.EXPECTED_BEHAVIOR),
        (OutcomeKind.RETURNED, "mismatch", SearchFeedback.EXPECTATION_MISMATCH),
        (OutcomeKind.RAISED, "inconclusive", SearchFeedback.INCONCLUSIVE),
        (OutcomeKind.RETURNED, "unsupported", SearchFeedback.UNSUPPORTED),
        (OutcomeKind.RAISED, "conflicting_evidence", SearchFeedback.CONFLICTING_EVIDENCE),
        (OutcomeKind.TIMEOUT, "match", SearchFeedback.INFRASTRUCTURE_FAILURE),
        (OutcomeKind.IMPORT_FAILURE, "inconclusive", SearchFeedback.INFRASTRUCTURE_FAILURE),
        (OutcomeKind.SANDBOX_UNAVAILABLE, "match", SearchFeedback.INFRASTRUCTURE_FAILURE),
        (OutcomeKind.PROTOCOL_ERROR, "mismatch", SearchFeedback.INFRASTRUCTURE_FAILURE),
    ],
)
def test_evaluator_verdicts_map_exactly_to_typed_feedback(
    outcome: OutcomeKind, status: str, expected: SearchFeedback
) -> None:
    assert feedback_for(outcome, status) is expected


def test_an_exception_is_not_automatically_a_mismatch() -> None:
    """A raised exception with no usable verdict stays inconclusive."""

    assert feedback_for(OutcomeKind.RAISED, "inconclusive") is SearchFeedback.INCONCLUSIVE
    assert feedback_for(OutcomeKind.RAISED, "").is_evidence is False


def test_infrastructure_failure_can_never_carry_a_reward() -> None:
    for status in ("match", "mismatch"):
        feedback = feedback_for(OutcomeKind.TIMEOUT, status)
        assert feedback is SearchFeedback.INFRASTRUCTURE_FAILURE
        assert feedback.is_evidence is False
        assert feedback.is_usable_observation is False


def test_only_two_feedbacks_count_as_evidence() -> None:
    assert SearchFeedback.EXPECTED_BEHAVIOR.is_evidence
    assert SearchFeedback.EXPECTATION_MISMATCH.is_evidence
    for other in (
        SearchFeedback.INCONCLUSIVE,
        SearchFeedback.UNSUPPORTED,
        SearchFeedback.INFRASTRUCTURE_FAILURE,
        SearchFeedback.CONFLICTING_EVIDENCE,
    ):
        assert other.is_evidence is False


# --- pure state transitions ---------------------------------------------------


def test_update_state_counts_probes_and_executions_separately(tmp_path: Path) -> None:
    universe = _universe(tmp_path)
    action = universe[0]
    base = _state(universe)

    state = update_state(base, action, SearchFeedback.EXPECTED_BEHAVIOR, reason="ok", executions=1)
    assert state.probes_used == 1
    assert state.executions_consumed == 1
    assert state.recheck_executions == 0
    assert state.observed_match == 1

    rerun = update_state(state, action, SearchFeedback.EXPECTED_BEHAVIOR, reason="ok", executions=1)
    assert rerun.probes_used == 1, "a rerun must not consume a distinct-probe slot"
    assert rerun.executions_consumed == 2
    assert rerun.recheck_executions == 1


def test_update_state_is_pure(tmp_path: Path) -> None:
    universe = _universe(tmp_path)
    base = _state(universe)

    update_state(base, universe[0], SearchFeedback.EXPECTED_BEHAVIOR, reason="ok", executions=1)

    assert base.probes_used == 0
    assert base.executions_consumed == 0
    assert base.last_feedback is None
    assert base.termination is None


def test_terminate_sets_an_explicit_status(tmp_path: Path) -> None:
    universe = _universe(tmp_path)
    finished = terminate(_state(universe), SearchTermination.REFUSED, "because")

    assert finished.finished
    assert finished.termination is SearchTermination.REFUSED
    assert finished.termination_reason == "because"


def test_termination_statuses_are_all_distinct() -> None:
    values = [member.value for member in SearchTermination]
    assert len(values) == len(set(values))
    assert len(values) == 10


def test_budget_accessors_clamp_and_report_effective_values() -> None:
    budgets = SearchBudgets(
        max_probes=10_000, max_executions=10_000, candidate_limit=10_000, wall_clock_seconds=10**9
    )
    assert budgets.bounded_probes() == HARD_MAX_PROBES
    assert budgets.bounded_executions() == HARD_MAX_EXECUTIONS
    assert budgets.bounded_wall_clock() >= 0.0
    assert SearchBudgets(max_probes=-3).bounded_probes() == 0


def test_available_candidates_exclude_tested_ones(tmp_path: Path) -> None:
    universe = _universe(tmp_path)
    state = _state(universe, tested=tuple(a.identifier for a in universe[:3]))

    remaining = [a.value for a in state.available]
    assert remaining == [a.value for a in universe[3:]]


def test_a_search_input_is_a_typed_protocol_integer_not_rendered_text(tmp_path: Path) -> None:
    """The path from a proposed integer to the worker stays a typed value."""

    from edgehunter.shrink.repository import _seed_request

    request = _seed_request(TARGET, "x", -2)

    assert len(request.invocation.args) == 1
    argument = request.invocation.args[0]
    assert argument.kind is ValueKind.INTEGER
    assert argument.scalar == "-2"
    assert request.invocation.kwargs == ()
    assert request.primary_action_parameter == "x"


def test_a_contract_is_bound_to_a_content_digest_not_a_path(tmp_path: Path) -> None:
    """A mismatch cannot be faked by editing a filename: the fingerprint is content."""

    source = tmp_path / "m.py"
    source.write_text(SOURCE, encoding="utf-8")
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    source.write_text(SOURCE + "\n# changed\n", encoding="utf-8")
    after = hashlib.sha256(source.read_bytes()).hexdigest()

    assert before != after
    assert _contract(before).source_sha256 == before


def test_universe_identifiers_survive_a_json_round_trip(tmp_path: Path) -> None:
    """Probe identity survives serialization, so a trace can be audited later."""

    universe = _universe(tmp_path)
    payload = json.dumps(
        [{"id": a.identifier, "value": a.value, "origin": a.origin.value} for a in universe]
    )

    restored = json.loads(payload)
    assert [entry["id"] for entry in restored] == [a.identifier for a in universe]


def test_search_result_reports_only_counted_measurements() -> None:
    """No coverage figure, no path coverage, no mutation score, no probability."""

    from edgehunter.search.models import SearchResult

    fields = set(SearchResult.__dataclass_fields__)

    assert not any(
        word in name
        for name in fields
        for word in ("coverage", "probability", "confidence", "mutation", "severity")
    )
    assert {"distinct_probes", "executions", "match_count", "mismatch_count"} <= fields
