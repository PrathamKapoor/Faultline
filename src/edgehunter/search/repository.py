"""Budgeted, snapshot-fixed orchestration of feedback-guided semantic search.

This module owns the causal loop and nothing else. It decides *when* to stop, it
counts every container, and it refuses before execution when the evidence does
not support changing an input. It does not decide which input comes next -- that
is the policy's job -- and it does not decide what a verdict means -- that is
the existing evaluator's job, called through the shrink package's contract
adapter rather than reimplemented.

What it deliberately does **not** do:

* It never proposes an out-of-domain input. Candidates are checked against the
  declared domain during enumeration, and the same check runs again immediately
  before execution as an independent second line of defence.
* It never reports a contradiction it did not see twice. A single mismatch sends
  the policy back to the same input as a verification rerun, and only two
  signature-matched executions become a finding.
* It never reports an infrastructure failure as progress. A launch that produced
  no observation consumes its execution slot and moves the feedback to its own
  typed state, which no policy is allowed to treat as a reward.
* It never shrinks. Discovery and minimization stay separate operations so a
  future experiment can attribute each one.
* It keeps no module-level state. Every fact the loop needs between iterations
  -- the first contradicting signature, the start of the clock -- is a local or
  a field, so two searches in one interpreter cannot influence each other.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Final, Protocol

from edgehunter.analysis.models import BoundaryCandidate, ComparisonInfo
from edgehunter.evaluation.repository import SourceSnapshot
from edgehunter.execution.docker import clamp_timeout
from edgehunter.execution.models import (
    ExecutionRequest,
    ExecutionResult,
    OutcomeKind,
    SandboxStatus,
    TargetRef,
)
from edgehunter.execution.repository import (
    ExecutionRefused,
    ExecutionSession,
    open_session,
    runtime_context,
)
from edgehunter.pipeline import RepositoryScan
from edgehunter.search.actions import SearchContext, enumerate_search_actions
from edgehunter.search.models import (
    PolicyName,
    SearchAction,
    SearchBudgets,
    SearchFeedback,
    SearchResult,
    SearchSelection,
    SearchState,
    SearchStep,
    SearchTermination,
    SelectionUse,
    feedback_for,
)
from edgehunter.search.policy import SearchPolicy, policy_for
from edgehunter.search.state import (
    note_first_mismatch,
    note_reproduced,
    record_step,
    terminate,
    update_state,
)
from edgehunter.shrink.contract import Contract
from edgehunter.shrink.models import ShrinkResult, ShrinkStatus
from edgehunter.shrink.repository import (
    ShrinkRequest,
    _seed_request,
    evaluate_against_contract,
    preflight_contract,
)
from edgehunter.shrink.signature import FailureSignature, signature_of

SEARCH_ARTIFACT_SCHEMA: Final[int] = 1


@dataclass(frozen=True, slots=True)
class ExploreRequest:
    """Everything the user explicitly asked for, before anything is inspected."""

    contract_path: Path | None
    contract_id: str
    start_value: int
    target_spec: str = ""
    policy_name: PolicyName = PolicyName.FEEDBACK_GUIDED
    budgets: SearchBudgets = SearchBudgets()
    timeout_seconds: float = 3.0

    def bounded_timeout(self) -> float:
        return clamp_timeout(self.timeout_seconds)


class ProbeRunner(Protocol):
    """How one proposed integer becomes an execution result.

    The seam that lets the whole budget, reproduction, and stopping logic be
    tested without Docker. It is a seam and not a bypass: the real runner calls
    the same ``ExecutionSession.execute`` that the ``run`` command and the
    shrinker use, against the same staged snapshot.
    """

    def execute_probe(self, action: SearchAction) -> ExecutionResult:
        """Run one container for one proposed input."""


class SessionProbeRunner:
    """The real runner: containers against one snapshot-fixed session."""

    def __init__(
        self,
        session: ExecutionSession,
        target: TargetRef,
        parameter: str,
        timeout_seconds: float,
    ) -> None:
        self._session = session
        self._target = target
        self._parameter = parameter
        self._timeout = timeout_seconds

    def execute_probe(self, action: SearchAction) -> ExecutionResult:
        # `_seed_request` is the shrinker's typed request builder, reused rather
        # than copied: it is the one place that turns an integer into a protocol
        # value, so reusing it is what keeps a search input from ever arriving
        # as evaluated text.
        request: ExecutionRequest = _seed_request(self._target, self._parameter, action.value)
        result: ExecutionResult = self._session.execute(request, timeout_seconds=self._timeout)
        return result


def explore_integer(
    scan: RepositoryScan,
    source_root: Path,
    request: ExploreRequest,
    *,
    authorized: bool,
    repository_context: Path | None = None,
    runner: ProbeRunner | None = None,
) -> SearchResult:
    """Search for a reproducible contradiction of one user-declared contract.

    The loop is the project's stated loop: enumerate available actions, ask the
    policy for one, execute it, evaluate it against the applicable contract,
    fold the typed feedback into the state, and repeat until a termination status
    is reached.

    Passing ``runner`` substitutes only the *executor*. Every static gate still
    runs first, so a unit test can never exercise a weaker path than production.
    """

    prepared = _run_static_gates(scan, request)
    if isinstance(prepared, SearchResult):
        return prepared
    contract, contract_sha, target, analyzed_digest = prepared

    universe = enumerate_search_actions(
        SearchContext(
            contract=contract,
            target=target,
            start_value=request.start_value,
            budgets=request.budgets,
            boundaries=_target_boundaries(scan, contract),
            comparisons=_target_comparisons(scan, contract),
        )
    )
    if not universe:
        return _early(
            SearchTermination.NO_REPRODUCED_VIOLATION,
            (
                f"no candidate satisfied the declared domain {contract.parameter} "
                f"{contract.domain.describe()}, so nothing could be executed"
            ),
            contract,
            request,
        )

    policy = policy_for(request.policy_name)
    state = SearchState(
        target_identity=target.identity,
        contract_id=contract.contract_id,
        source_sha256=analyzed_digest,
        parameter=contract.parameter,
        policy=policy.name,
        budgets=request.budgets,
        universe=universe,
    )

    if runner is not None:
        return _run_loop(
            contract=contract,
            state=state,
            policy=policy,
            runner=runner,
            snapshot_verified=True,
            contract_sha=contract_sha,
            started=time.monotonic(),
        )

    return _explore_with_docker(
        scan=scan,
        source_root=source_root,
        contract=contract,
        contract_sha=contract_sha,
        target=target,
        state=state,
        policy=policy,
        request=request,
        authorized=authorized,
        repository_context=repository_context,
    )


def _explore_with_docker(
    *,
    scan: RepositoryScan,
    source_root: Path,
    contract: Contract,
    contract_sha: str,
    target: TargetRef,
    state: SearchState,
    policy: SearchPolicy,
    request: ExploreRequest,
    authorized: bool,
    repository_context: Path | None,
) -> SearchResult:
    """Open one snapshot-fixed session and search against it.

    The repository is staged exactly once, for the whole search. Every probe and
    every verification rerun therefore observes the same bytes by construction,
    which is stronger than re-verifying per probe and is what D-062's snapshot
    rule needs.
    """

    try:
        session, sandbox = open_session(
            source_root,
            authorized=authorized,
            repository_context=repository_context or runtime_context(),
        )
    except ExecutionRefused as exc:
        return _early(SearchTermination.REFUSED, str(exc), contract, request)

    if session is None:
        return _early(
            SearchTermination.SANDBOX_UNAVAILABLE,
            f"no sandbox was available, so nothing was executed: {sandbox.detail}",
            contract,
            request,
        )

    try:
        snapshot = SourceSnapshot.verify(
            _analyzed_digest(scan, contract), session.staged_digests, contract.target_file
        )
        if not snapshot.verified:
            return _early(
                SearchTermination.INCONCLUSIVE,
                (
                    "the staged source could not be proven to be the analyzed source: "
                    f"{snapshot.detail}"
                ),
                contract,
                request,
            )
        runner = SessionProbeRunner(session, target, contract.parameter, request.bounded_timeout())
        return _run_loop(
            contract=contract,
            state=state,
            policy=policy,
            runner=runner,
            snapshot_verified=True,
            contract_sha=contract_sha,
            started=time.monotonic(),
        )
    finally:
        problem = session.close()
        if problem is not None:
            # Surfaced rather than swallowed: a host that still holds a staged
            # copy is not a clean host.
            print(f"warning: {problem}")


def _run_loop(
    *,
    contract: Contract,
    state: SearchState,
    policy: SearchPolicy,
    runner: ProbeRunner,
    snapshot_verified: bool,
    contract_sha: str,
    started: float,
) -> SearchResult:
    """The causal loop: select, execute, evaluate, update, repeat.

    Reproduction lives here rather than in a policy on purpose. A contradiction
    has to be re-executed before it is a finding, and if that rule belonged to
    the policy then the two policies could confirm different numbers of
    findings purely by being written differently -- which would make any
    comparison between them meaningless. Here it applies identically to both.

    ``pending`` holds the probe whose mismatch has not yet been confirmed. It is
    a loop local rather than a field or a global because it is meaningful for
    exactly one span and nothing else here needs it.
    """

    pending: PendingRecheck | None = None

    while True:
        exhausted = _budget_stop(state, started, pending is not None)
        if exhausted is not None:
            state = terminate(state, *exhausted)
            break

        if pending is not None:
            selection = _recheck_selection(pending)
        else:
            chosen = policy.select(state, state.available)
            if chosen is None:
                state = terminate(state, *_stop_for(state))
                break
            selection = chosen

        action = selection.action
        # An independent domain check immediately before execution. The universe
        # already filtered these, so a failure here would mean the universe is
        # wrong -- and an out-of-domain input must never start a container,
        # whatever its provenance.
        if not contract.domain.contains(action.value):
            state = terminate(
                state,
                SearchTermination.INCONCLUSIVE,
                (
                    f"{contract.parameter} = {action.value} left the declared domain "
                    f"{contract.domain.describe()} before execution; it was refused rather than run"
                ),
            )
            break

        result = runner.execute_probe(action)
        verdict = evaluate_against_contract(contract, result, snapshot_verified=snapshot_verified)
        outcome = result.observation.outcome if result.observation else None
        feedback = feedback_for(outcome, verdict.status.value)
        signature = signature_of(result.observation) if result.observation else None

        probe_index = pending.probe_index if pending is not None else state.probes_used + 1
        reproduction = ""
        termination: SearchTermination | None = None

        if pending is not None:
            termination, reproduction = _judge_recheck(feedback, signature, pending.signature)
        elif feedback is SearchFeedback.EXPECTATION_MISMATCH:
            state = note_first_mismatch(state, probe_index)
            pending = PendingRecheck(action, probe_index, signature)

        state = update_state(
            state,
            action,
            feedback,
            reason=verdict.reason,
            executions=1,
            outcome=outcome,
            reproduction=reproduction,
        )
        state = record_step(
            state,
            SearchStep(
                step=len(state.steps) + 1,
                policy=policy.name,
                action=action,
                use=selection.use,
                selection_reason=selection.reason,
                execution_outcome=outcome.value if outcome is not None else "unknown",
                observation_description=_describe(result),
                oracle_status=verdict.status.value,
                feedback=feedback,
                feedback_reason=verdict.reason,
                reproduction_status=reproduction,
                reproduction_detail=f"the identical request was re-executed: {reproduction}"
                if reproduction
                else "",
                executions_consumed=1,
                remaining_probes=state.remaining_probes,
                remaining_executions=state.remaining_executions,
            ),
        )

        # A recheck always decides the search's outcome. Allowing another one
        # would loop on a repeated mismatch indefinitely, so exactly one
        # verification is performed per contradiction.
        if termination is not None:
            if termination is SearchTermination.VIOLATION_REPRODUCED:
                state = note_reproduced(state, pending.probe_index if pending else probe_index)
            state = terminate(
                state,
                termination,
                _reason_for(termination, action, reproduction or verdict.reason),
            )
            break

    return _result_from_state(
        state, contract_sha=contract_sha, snapshot_verified=snapshot_verified, started=started
    )


@dataclass(frozen=True, slots=True)
class PendingRecheck:
    """One contradicting observation still waiting for its confirmation."""

    action: SearchAction
    probe_index: int
    signature: FailureSignature | None


def _recheck_selection(pending: PendingRecheck) -> SearchSelection:
    """Re-run the identical request, and say plainly why."""

    return SearchSelection(
        action=pending.action,
        use=SelectionUse.VERIFICATION_RERUN,
        reason=(
            f"{pending.action.parameter} = {pending.action.value} contradicted the contract once; "
            "re-executing the identical request on the same source snapshot before any other "
            "input is tried, because a single contradiction is not a finding"
        ),
        priority=0,
    )


def _judge_recheck(
    feedback: SearchFeedback, signature: FailureSignature | None, original: FailureSignature | None
) -> tuple[SearchTermination, str]:
    """Classify what repeating one contradicting input actually proved.

    Three refusals stay distinct because they mean different things to a reader:
    *the second attempt contradicted nothing* (flaky), *the second attempt could
    not be judged at all* (inconclusive), and *the two attempts disagreed about
    what happened* (not stable). Collapsing any of them into "reproduced" would
    manufacture findings out of noise.
    """

    if feedback is not SearchFeedback.EXPECTATION_MISMATCH:
        status = (
            SearchTermination.FLAKY_OBSERVATION
            if feedback is SearchFeedback.EXPECTED_BEHAVIOR
            else SearchTermination.INCONCLUSIVE
        )
        return (
            status,
            (
                f"the identical input produced {feedback.value} on recheck instead of a second "
                "contradiction"
            ),
        )
    if signature is None or original is None:
        return (
            SearchTermination.INCONCLUSIVE,
            "recheck_failed: no comparable failure signature was available for both executions",
        )
    if not original.matches(signature):
        return (
            SearchTermination.INCONCLUSIVE,
            (
                f"recheck_failed: the two executions disagreed ({original.describe()} vs "
                f"{signature.describe()}), so the contradiction is not a stable failure"
            ),
        )
    return (
        SearchTermination.VIOLATION_REPRODUCED,
        f"reproduced: two independent executions agreed on {signature.describe()}",
    )


def _budget_stop(
    state: SearchState, started: float, recheck_pending: bool
) -> tuple[SearchTermination, str] | None:
    """Whether a bound has been reached before the next selection.

    A pending recheck is exempt from the probe budget, because re-running an
    already-tested input is not a new distinct probe. It is **not** exempt from
    the execution or wall-clock budget: a recheck costs a container like any
    other, which is why a budget too small to recheck can never produce a
    finding.
    """

    wall_clock = state.budgets.bounded_wall_clock()
    if wall_clock and time.monotonic() - started >= wall_clock:
        return (
            SearchTermination.WALL_TIME_BUDGET_EXHAUSTED,
            f"the wall-clock budget of {wall_clock}s was reached",
        )
    if state.remaining_executions <= 0:
        return (
            SearchTermination.EXECUTION_BUDGET_EXHAUSTED,
            (
                f"the total execution budget of {state.budgets.bounded_executions()} was consumed, "
                "including verification reruns; no probe was left to run and no unverified "
                "contradiction is reported"
            ),
        )
    if not recheck_pending and state.remaining_probes <= 0:
        return (
            SearchTermination.PROBE_BUDGET_EXHAUSTED,
            f"the distinct-probe budget of {state.budgets.bounded_probes()} was consumed",
        )
    return None


def _stop_for(state: SearchState) -> tuple[SearchTermination, str]:
    """Why the policy declined to select another action."""

    feedback = state.last_feedback
    if feedback is SearchFeedback.INFRASTRUCTURE_FAILURE:
        return (
            SearchTermination.SANDBOX_UNAVAILABLE,
            (
                f"the last probe ended in an infrastructure failure "
                f"({state.last_feedback_reason}); it is not evidence about the contract, and "
                "continuing would spend a real budget on a backend that cannot serve this target"
            ),
        )
    if feedback is SearchFeedback.CONFLICTING_EVIDENCE:
        return (
            SearchTermination.CONFLICTING_EVIDENCE,
            (
                "applicable evidence disagreed and neither side was chosen, so no verdict about "
                "this target is trustworthy"
            ),
        )
    return (
        SearchTermination.NO_REPRODUCED_VIOLATION,
        (
            f"every candidate in this bounded universe was executed and none produced a "
            f"reproduced contradiction ({state.probes_used} distinct probe(s), "
            f"{state.executions_consumed} container(s)); this says nothing about inputs that "
            "were never tried"
        ),
    )


def _reason_for(termination: SearchTermination, action: SearchAction, detail: str) -> str:
    if termination is SearchTermination.VIOLATION_REPRODUCED:
        return (
            f"{action.parameter} = {action.value} contradicted the contract twice on one source "
            f"snapshot: {detail}"
        )
    return f"{termination.value} at {action.value}: {detail}"


# ---------------------------------------------------------------------------
# Static gates, all of them before a container could start
# ---------------------------------------------------------------------------


def _run_static_gates(
    scan: RepositoryScan, request: ExploreRequest
) -> tuple[Contract, str, TargetRef, str] | SearchResult:
    """Every static gate, delegated to the shrink package's shared preflight.

    The contract parser, the target-shape rules, the fingerprint comparison, and
    the conflict detector stay single implementations: a second copy of any of
    them would eventually disagree with the first, and a disagreement about
    *whether an oracle applies* is exactly the bug this project exists to avoid.
    """

    if request.contract_path is None:
        return _early(
            SearchTermination.REFUSED,
            (
                "no contract file was selected. An exact-input test expectation cannot authorize "
                "changing an input -- `assert f(100) == 200` says nothing about f(10) -- so a "
                "search requires an explicitly selected user-declared contract"
            ),
            None,
            request,
        )

    prepared = preflight_contract(
        scan,
        ShrinkRequest(
            contract_path=request.contract_path,
            contract_id=request.contract_id,
            seed=request.start_value,
            target_spec=request.target_spec,
            timeout_seconds=request.timeout_seconds,
        ),
    )
    if isinstance(prepared, ShrinkResult):
        return _from_shrink_refusal(prepared, request)
    contract, contract_sha, target, _function = prepared
    return contract, contract_sha, target, contract.source_sha256


_SHRINK_TO_SEARCH: Final[Mapping[ShrinkStatus, SearchTermination]] = MappingProxyType(
    {
        ShrinkStatus.REFUSED: SearchTermination.REFUSED,
        ShrinkStatus.INCONCLUSIVE: SearchTermination.INCONCLUSIVE,
        ShrinkStatus.NOT_SHRINKABLE: SearchTermination.CONFLICTING_EVIDENCE,
    }
)


def _from_shrink_refusal(result: ShrinkResult, request: ExploreRequest) -> SearchResult:
    """Translate a shared-preflight refusal into search vocabulary.

    The mapping is small and deliberate: a refusal stays a refusal, an
    unverifiable result stays inconclusive, and the conflict case is reported as
    conflicting evidence rather than as "not shrinkable", because in a *search*
    disagreeing applicable expectations are a real stopping condition with its own
    name.
    """

    return SearchResult(
        termination=_SHRINK_TO_SEARCH.get(result.status, SearchTermination.REFUSED),
        reason=result.reason,
        target_identity=result.target_identity,
        contract_id=result.contract_id or request.contract_id,
        parameter=result.parameter,
        policy=request.policy_name,
        budgets=request.budgets,
    )


def _early(
    termination: SearchTermination,
    reason: str,
    contract: Contract | None,
    request: ExploreRequest,
) -> SearchResult:
    """A result for anything decided before the first container could start."""

    return SearchResult(
        termination=termination,
        reason=reason,
        target_identity=contract.target_spec if contract is not None else request.target_spec,
        contract_id=contract.contract_id if contract is not None else request.contract_id,
        parameter=contract.parameter if contract is not None else "",
        source_sha256=contract.source_sha256 if contract is not None else "",
        policy=request.policy_name,
        budgets=request.budgets,
    )


def _target_boundaries(scan: RepositoryScan, contract: Contract) -> tuple[BoundaryCandidate, ...]:
    """Phase 2's mined boundaries for exactly this target, reused as-is."""

    return tuple(
        candidate
        for entry in scan.semantics.boundaries
        if entry.qualified_name == contract.target_name
        and entry.relative_path.as_posix() == contract.target_file
        for candidate in entry.candidates
    )


def _target_comparisons(scan: RepositoryScan, contract: Contract) -> tuple[ComparisonInfo, ...]:
    """Phase 2's mined comparisons for exactly this target, reused as-is."""

    return tuple(
        comparison
        for module in scan.semantics.modules
        for target in module.targets
        if target.qualified_name == contract.target_name
        and module.relative_path.as_posix() == contract.target_file
        for comparison in target.comparisons
    )


def _analyzed_digest(scan: RepositoryScan, contract: Contract) -> str | None:
    """The digest the single analysis pass recorded for the contract's file."""

    for module in scan.semantics.structure.modules:
        if module.relative_path.as_posix() == contract.target_file:
            return module.digest
    return None


def _describe(result: ExecutionResult) -> str:
    """A short, bounded rendering of one observation."""

    observation = result.observation
    if observation is None:
        return str(result.detail or result.outcome.value)
    if observation.outcome is OutcomeKind.RAISED:
        return f"RAISED {observation.exception_identity_text}"
    if observation.outcome is OutcomeKind.RETURNED:
        returned = observation.return_description
        return "RETURNED" if returned is None else f"RETURNED {returned}"
    return str(observation.outcome.value)


def _result_from_state(
    state: SearchState, *, contract_sha: str, snapshot_verified: bool, started: float
) -> SearchResult:
    """Assemble the reported result, counting only what actually happened."""

    reproduced = next(
        (
            step.action.value
            for step in state.steps
            if step.reproduction_status.startswith("reproduced")
        ),
        None,
    )
    return SearchResult(
        termination=state.termination or SearchTermination.INCONCLUSIVE,
        reason=state.termination_reason,
        target_identity=state.target_identity,
        contract_id=state.contract_id,
        parameter=state.parameter,
        source_sha256=state.source_sha256,
        policy=state.policy,
        budgets=state.budgets,
        universe_size=len(state.universe),
        steps=state.steps,
        distinct_probes=state.probes_used,
        executions=state.executions_consumed,
        reproduction_executions=state.recheck_executions,
        match_count=state.observed_match,
        mismatch_count=state.observed_mismatch,
        inconclusive_count=state.observed_inconclusive,
        first_mismatch_probe=state.first_mismatch_probe,
        first_reproduced_probe=state.reproduced_probe,
        reproduction_attempts=sum(
            1 for step in state.steps if step.use is SelectionUse.VERIFICATION_RERUN
        ),
        wall_clock_seconds=round(time.monotonic() - started, 3),
        snapshot_verified=snapshot_verified,
        contract_file_sha256=contract_sha,
        reproduced_value=reproduced,
    )


# ---------------------------------------------------------------------------
# The versioned evidence artifact
# ---------------------------------------------------------------------------


def search_artifact_document(result: SearchResult) -> dict[str, object]:
    """The evidence-only JSON document for one search.

    Excludes captured target stdout, stderr, and exception messages for the same
    reason the Phase 6B and 7B artifacts do: this file is meant for review, and
    it must not become the place secrets accumulate. Nothing here is a
    serialized object.
    """

    return {
        "schema_version": SEARCH_ARTIFACT_SCHEMA,
        "kind": "semantic_neighborhood_search",
        "target_identity": result.target_identity,
        "parameter": result.parameter,
        "source_sha256": result.source_sha256,
        "snapshot_verified": result.snapshot_verified,
        "oracle": {
            "id": result.contract_id,
            "source": "user_declared_contract",
            "contract_file_sha256": result.contract_file_sha256,
            "provenance": (
                "user-declared JSON contract, explicitly selected on the command line; an "
                "expectation the user asserts, not a fact proven from the repository"
            ),
        },
        "policy": result.policy.value,
        "budgets": {
            "max_probes": result.budgets.bounded_probes(),
            "max_executions": result.budgets.bounded_executions(),
            "candidate_limit": result.budgets.bounded_candidates(),
            "wall_clock_seconds": result.budgets.bounded_wall_clock(),
            "reproduction_attempts": result.budgets.bounded_reproduction(),
        },
        "candidate_universe": {
            "size": result.universe_size,
            "candidates": [
                {
                    "identifier": action.identifier,
                    "kind": action.kind.value,
                    "parameter": action.parameter,
                    "input": str(action.value),
                    "origin": action.origin.value,
                    "reason": action.reason,
                    "phase5_action": (
                        action.semantic_action.kind.value if action.semantic_action else None
                    ),
                }
                for action in _observed_universe(result)
            ],
        },
        "trace": [
            {
                "step": step.step,
                "policy": step.policy.value,
                "action": step.action.kind.value,
                "action_identifier": step.action.identifier,
                "parameter": step.action.parameter,
                "input": str(step.action.value),
                "origin": step.action.origin.value,
                "use": step.use.value,
                "selection_reason": step.selection_reason,
                "execution_outcome": step.execution_outcome,
                "observation": step.observation_description,
                "oracle_status": step.oracle_status,
                "feedback": step.feedback.value,
                "feedback_reason": step.feedback_reason,
                "reproduction": step.reproduction_status,
                "executions_consumed": step.executions_consumed,
                "remaining_probes": step.remaining_probes,
                "remaining_executions": step.remaining_executions,
            }
            for step in result.steps
        ],
        "measurements": {
            "distinct_probes": result.distinct_probes,
            "container_executions": result.executions,
            "reproduction_executions": result.reproduction_executions,
            "reproduction_attempts": result.reproduction_attempts,
            "match_count": result.match_count,
            "mismatch_count": result.mismatch_count,
            "inconclusive_count": result.inconclusive_count,
            "first_mismatch_probe": result.first_mismatch_probe,
            "first_reproduced_probe": result.first_reproduced_probe,
            "universe_size": result.universe_size,
            "wall_clock_seconds": result.wall_clock_seconds,
        },
        "termination": result.termination.value,
        "reason": result.reason,
        "reproduced_value": result.reproduced_value,
        "meaning": (
            "A reproduced contradiction of one user-declared contract on one source snapshot, "
            "inside a bounded deterministic search. Not a universal proof of a software defect, "
            "not a claim of exhaustive coverage, and not a calibrated claim about how effective "
            "this policy is."
        ),
    }


def _observed_universe(result: SearchResult) -> tuple[SearchAction, ...]:
    """The distinct candidates this search actually reached, in trace order."""

    seen: set[str] = set()
    ordered: list[SearchAction] = []
    for step in result.steps:
        if step.action.identifier not in seen:
            seen.add(step.action.identifier)
            ordered.append(step.action)
    return tuple(ordered)


def write_search_artifact(result: SearchResult, *, run_id: str, base: Path | None = None) -> Path:
    """Write the artifact under ``.edgehunter/runs/<run-id>/search.json``.

    The run id goes through the *same* sanitizer the Phase 6B and 7B artifacts
    use, so a crafted id cannot escape the artifact directory by a new route, and
    the whole tree stays Git-ignored.
    """

    from edgehunter.evaluation.repository import ARTIFACT_DIRECTORY, _safe_run_id

    root = (base or Path(*PurePosixPath(ARTIFACT_DIRECTORY).parts)) / _safe_run_id(run_id)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "search.json"
    path.write_text(
        json.dumps(search_artifact_document(result), indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    return path


def sandbox_detail(sandbox: SandboxStatus) -> str:
    """The sandbox's own condition, for callers that report it verbatim."""

    return f"{sandbox.backend}/{sandbox.state.value}: {sandbox.detail}"
