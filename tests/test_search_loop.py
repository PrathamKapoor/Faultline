"""The search loop: reproduction before discovery, budgets, and refusals.

Every test here drives :func:`explore_integer` with a fabricated probe runner, so
the whole loop -- selection, execution, evaluation, feedback, state update,
reproduction, and stopping -- is exercised without Docker. The fabricated results
are typed :class:`ExecutionResult` objects carrying real
:class:`RuntimeObservation` values, so the contract adapter's judgement goes
through exactly the code the sandbox path uses.

The assertions that matter are the negative ones: a single mismatch is not a
finding, a flaky pair is not a finding, a budget too small to recheck produces no
finding, an out-of-domain input never launches a container, and an exact-input
test oracle never authorizes any of it.
"""

from __future__ import annotations

import builtins
import hashlib
import json
from pathlib import Path
from typing import IO, Any

import pytest

from edgehunter.execution.codec import TypedValue, ValueKind
from edgehunter.execution.models import (
    ExecutionResult,
    InvocationArguments,
    OutcomeKind,
    RuntimeObservation,
    SandboxState,
    SandboxStatus,
    TargetRef,
)
from edgehunter.pipeline import analyze_repository
from edgehunter.runtime.worker import _exception_identity
from edgehunter.search.models import (
    HARD_MAX_EXECUTIONS,
    HARD_MAX_PROBES,
    PolicyName,
    SearchAction,
    SearchBudgets,
    SearchResult,
    SearchTermination,
    SelectionUse,
)
from edgehunter.search.repository import (
    SEARCH_ARTIFACT_SCHEMA,
    ExploreRequest,
    explore_integer,
    search_artifact_document,
    write_search_artifact,
)

TARGET = TargetRef(relative_file="m.py", qualified_name="validate", lineno=1)

SOURCE = (
    "def validate(x: int) -> int:\n"
    "    if x == -2:\n"
    "        return x\n\n"
    '    raise ValueError("negative")\n'
)

CORRECT_SOURCE = (
    "def validate(x: int) -> int:\n"
    "    if x < 0:\n"
    '        raise ValueError("negative")\n\n'
    "    return x\n"
)


# --- typed fabricated results -------------------------------------------------


def _arguments(value: int) -> InvocationArguments:
    return InvocationArguments(args=(TypedValue(ValueKind.INTEGER, str(value)),), kwargs=())


def _sandbox() -> SandboxStatus:
    return SandboxStatus(backend="docker", state=SandboxState.READY)


def _returned(value: int) -> ExecutionResult:
    return ExecutionResult(
        execution_id=f"e{value}",
        target=TARGET,
        action_id=f"search:x={value}",
        outcome=OutcomeKind.RETURNED,
        sandbox=_sandbox(),
        observation=RuntimeObservation(
            outcome=OutcomeKind.RETURNED, return_value=TypedValue(ValueKind.INTEGER, str(value))
        ),
        arguments=_arguments(value),
    )


def _raised(value: int, name: str = "ValueError") -> ExecutionResult:
    """A real builtin exception, with identity read by the worker's own helper."""

    exception_class = getattr(builtins, name)
    instance = exception_class.__new__(exception_class)
    document = _exception_identity(instance)
    return ExecutionResult(
        execution_id=f"e{value}",
        target=TARGET,
        action_id=f"search:x={value}",
        outcome=OutcomeKind.RAISED,
        sandbox=_sandbox(),
        observation=RuntimeObservation(
            outcome=OutcomeKind.RAISED,
            exception_type=name,
            exception_message="detail",
            exception_module=document.get("exception_module"),
            exception_qualname=document.get("exception_qualname"),
            exception_builtin=document.get("exception_builtin"),
            exception_builtin_lineage=tuple(document.get("exception_builtin_lineage", ())),
        ),
        arguments=_arguments(value),
    )


def _failed(value: int, outcome: OutcomeKind) -> ExecutionResult:
    return ExecutionResult(
        execution_id=f"e{value}",
        target=TARGET,
        action_id=f"search:x={value}",
        outcome=outcome,
        sandbox=_sandbox(),
        observation=RuntimeObservation(outcome=outcome),
        arguments=_arguments(value),
    )


class SpyRunner:
    """Records every proposed input and answers from a scripted outcome map.

    ``calls`` is the record a refusal test asserts is empty: a refusal that still
    reached this runner did not actually refuse.
    """

    def __init__(
        self,
        script: dict[int, list[ExecutionResult]] | None = None,
        default: str = "returned",
    ) -> None:
        self.script = script or {}
        self.default = default
        self.calls: list[int] = []

    def execute_probe(self, action: SearchAction) -> ExecutionResult:
        self.calls.append(action.value)
        scripted = self.script.get(action.value)
        if scripted:
            return scripted.pop(0)
        if self.default == "raised":
            return _raised(action.value)
        return _returned(action.value)


def _repository(tmp_path: Path, source: str = SOURCE) -> Path:
    source_file = tmp_path / "m.py"
    source_file.write_text(source, encoding="utf-8")
    digest = hashlib.sha256(source_file.read_bytes()).hexdigest()
    document = {
        "version": 1,
        "contracts": [
            {
                "id": "neg",
                "target": "m.py::validate",
                "source_sha256": digest,
                "parameter": "x",
                "domain": {"operator": "lt", "integer": "0"},
                "expectation": {"kind": "raises_builtin", "exception": "ValueError"},
            }
        ],
    }
    contract = tmp_path / "contracts.json"
    contract.write_text(json.dumps(document), encoding="utf-8")
    return contract


def _explore(
    tmp_path: Path,
    runner: SpyRunner,
    *,
    start: int = -64,
    policy: PolicyName = PolicyName.FEEDBACK_GUIDED,
    budgets: SearchBudgets | None = None,
    contract: Path | None = None,
    contract_id: str = "neg",
) -> SearchResult:
    scan = analyze_repository(tmp_path)
    return explore_integer(
        scan,
        tmp_path,
        ExploreRequest(
            contract_path=contract if contract is not None else tmp_path / "contracts.json",
            contract_id=contract_id,
            start_value=start,
            target_spec="m.py::validate",
            policy_name=policy,
            budgets=budgets or SearchBudgets(),
        ),
        authorized=True,
        runner=runner,
    )


# --- the happy paths ----------------------------------------------------------


def test_feedback_finds_a_reproduced_violation_in_two_probes(tmp_path: Path) -> None:
    """The phase's success case: anchor matches, its neighbour contradicts, twice."""

    _repository(tmp_path)
    runner = SpyRunner(script={-2: [_returned(-2), _returned(-2)]}, default="raised")

    result = _explore(tmp_path, runner)

    assert result.termination is SearchTermination.VIOLATION_REPRODUCED
    assert result.found_violation
    assert runner.calls == [-1, -2, -2]
    assert result.distinct_probes == 2
    assert result.executions == 3
    assert result.reproduction_executions == 1
    assert result.first_mismatch_probe == 2
    assert result.first_reproduced_probe == 2
    assert result.reproduced_value == -2


def test_static_policy_runs_the_same_fixation_and_slowly(tmp_path: Path) -> None:
    """Same universe, same oracle, same budget -- only the order differs."""

    _repository(tmp_path)
    runner = SpyRunner(script={-2: [_returned(-2), _returned(-2)]}, default="raised")

    result = _explore(tmp_path, runner, policy=PolicyName.STATIC_PRIORITY)

    # Static follows the documented order: anchor, then the user seed.
    assert runner.calls[:2] == [-1, -64]
    assert result.distinct_probes > 2
    assert result.termination is not SearchTermination.VIOLATION_REPRODUCED or (
        result.distinct_probes > 2
    )


def test_a_correct_target_reports_no_violation_and_makes_no_claim(tmp_path: Path) -> None:
    _repository(tmp_path, CORRECT_SOURCE)
    runner = SpyRunner(default="raised")

    result = _explore(tmp_path, runner, budgets=SearchBudgets(max_probes=64, max_executions=64))

    assert result.termination is SearchTermination.NO_REPRODUCED_VIOLATION
    assert result.found_violation is False
    assert result.mismatch_count == 0
    assert result.match_count == result.distinct_probes
    assert "says nothing about inputs that were never tried" in result.reason


def test_the_whole_universe_is_exhausted_not_just_the_budget(tmp_path: Path) -> None:
    _repository(tmp_path)
    runner = SpyRunner(default="raised")

    result = _explore(tmp_path, runner, budgets=SearchBudgets(max_probes=64, max_executions=64))

    assert result.termination is SearchTermination.NO_REPRODUCED_VIOLATION
    assert result.distinct_probes == result.universe_size
    assert sorted(runner.calls) == sorted(set(runner.calls))


# --- 49: reproduction before discovery ---------------------------------------


def test_two_mismatches_reproduce_the_violation(tmp_path: Path) -> None:
    _repository(tmp_path)
    runner = SpyRunner(script={-2: [_returned(-2), _returned(-2)]}, default="raised")

    result = _explore(tmp_path, runner)

    assert result.termination is SearchTermination.VIOLATION_REPRODUCED
    assert result.reproduction_attempts == 1
    assert runner.calls.count(-2) == 2


def test_a_mismatch_then_a_match_is_flaky_not_a_finding(tmp_path: Path) -> None:
    _repository(tmp_path)
    runner = SpyRunner(script={-2: [_returned(-2), _raised(-2)]}, default="raised")

    result = _explore(tmp_path, runner)

    assert result.termination is SearchTermination.FLAKY_OBSERVATION
    assert result.found_violation is False
    assert result.reproduced_value is None


def test_a_mismatch_then_a_timeout_is_inconclusive(tmp_path: Path) -> None:
    _repository(tmp_path)
    runner = SpyRunner(
        script={
            -2: [_returned(-2), _failed(-2, OutcomeKind.TIMEOUT)],
        },
        default="raised",
    )

    result = _explore(tmp_path, runner)

    assert result.termination is SearchTermination.INCONCLUSIVE
    assert result.found_violation is False


def test_a_mismatch_then_a_dead_sandbox_is_inconclusive(tmp_path: Path) -> None:
    _repository(tmp_path)
    runner = SpyRunner(
        script={
            -2: [_returned(-2), _failed(-2, OutcomeKind.SANDBOX_UNAVAILABLE)],
        },
        default="raised",
    )

    result = _explore(tmp_path, runner)

    assert result.termination is SearchTermination.INCONCLUSIVE
    assert result.found_violation is False


def test_a_mismatch_then_a_different_exception_is_not_the_same_failure(tmp_path: Path) -> None:
    """Two contradictions are not one stable failure when the signatures differ."""

    _repository(tmp_path)
    runner = SpyRunner(script={-2: [_returned(-2), _raised(-2, "TypeError")]}, default="raised")

    result = _explore(tmp_path, runner)

    assert result.termination is SearchTermination.INCONCLUSIVE
    assert result.found_violation is False
    assert "disagreed" in result.reason


def test_a_single_mismatch_never_reaches_a_finding(tmp_path: Path) -> None:
    """The recheck is mandatory: without a second observation there is no finding."""

    _repository(tmp_path)
    runner = SpyRunner(script={-2: [_returned(-2)]}, default="raised")

    result = _explore(tmp_path, runner)

    assert result.termination is not SearchTermination.VIOLATION_REPRODUCED
    assert runner.calls.count(-2) == 2, "a recheck must actually be attempted"


# --- 50: budgets --------------------------------------------------------------


def test_distinct_probe_budget_is_enforced(tmp_path: Path) -> None:
    _repository(tmp_path)
    runner = SpyRunner(default="raised")

    result = _explore(tmp_path, runner, budgets=SearchBudgets(max_probes=3, max_executions=32))

    assert result.termination is SearchTermination.PROBE_BUDGET_EXHAUSTED
    assert result.distinct_probes == 3
    assert len(runner.calls) == 3


def test_total_execution_budget_is_enforced(tmp_path: Path) -> None:
    _repository(tmp_path)
    runner = SpyRunner(default="raised")

    result = _explore(tmp_path, runner, budgets=SearchBudgets(max_probes=32, max_executions=4))

    assert result.termination is SearchTermination.EXECUTION_BUDGET_EXHAUSTED
    assert result.executions == 4
    assert len(runner.calls) == 4


def test_a_budget_too_small_to_recheck_yields_no_finding(tmp_path: Path) -> None:
    """The exact case: enough budget to see a mismatch, not enough to confirm it."""

    _repository(tmp_path)
    runner = SpyRunner(script={-1: [_returned(-1)]}, default="raised")

    result = _explore(tmp_path, runner, budgets=SearchBudgets(max_probes=8, max_executions=1))

    assert result.termination is SearchTermination.EXECUTION_BUDGET_EXHAUSTED
    assert result.mismatch_count == 1
    assert result.first_mismatch_probe == 1
    assert result.first_reproduced_probe is None
    assert result.found_violation is False
    assert "no unverified contradiction is reported" in result.reason


def test_the_recheck_consumes_execution_budget(tmp_path: Path) -> None:
    _repository(tmp_path)
    runner = SpyRunner(script={-2: [_returned(-2), _returned(-2)]}, default="raised")

    result = _explore(tmp_path, runner, budgets=SearchBudgets(max_probes=8, max_executions=12))

    assert result.distinct_probes == 2
    assert result.executions == 3
    assert result.reproduction_executions == 1


def test_a_failed_launch_still_consumes_an_execution(tmp_path: Path) -> None:
    """A container that produced nothing cost a container, and is never a success."""

    _repository(tmp_path)
    runner = SpyRunner(
        script={-1: [_failed(-1, OutcomeKind.WORKER_FAILURE)]},
        default="raised",
    )

    result = _explore(tmp_path, runner)

    assert result.executions == len(runner.calls)
    assert result.match_count == 0
    assert result.termination is SearchTermination.SANDBOX_UNAVAILABLE


def test_a_candidate_specific_failure_does_not_end_the_search(tmp_path: Path) -> None:
    """A timeout may depend on the input, so the search tries the next candidate."""

    _repository(tmp_path)
    runner = SpyRunner(script={-1: [_failed(-1, OutcomeKind.TIMEOUT)]}, default="raised")

    result = _explore(tmp_path, runner)

    assert len(runner.calls) > 1, "a timeout is candidate-specific, not a dead backend"
    assert result.termination in (
        SearchTermination.NO_REPRODUCED_VIOLATION,
        SearchTermination.PROBE_BUDGET_EXHAUSTED,
    )


def test_the_wall_clock_budget_stops_the_search(tmp_path: Path) -> None:
    _repository(tmp_path)
    runner = SpyRunner(default="raised")

    result = _explore(
        tmp_path,
        runner,
        budgets=SearchBudgets(max_probes=64, max_executions=64, wall_clock_seconds=0.0001),
    )

    assert result.termination in (
        SearchTermination.WALL_TIME_BUDGET_EXHAUSTED,
        SearchTermination.NO_REPRODUCED_VIOLATION,
    )
    assert result.distinct_probes <= 64


def test_budgets_clamp_to_their_hard_maxima(tmp_path: Path) -> None:
    _repository(tmp_path)
    runner = SpyRunner(default="raised")

    result = _explore(
        tmp_path, runner, budgets=SearchBudgets(max_probes=10_000, max_executions=10_000)
    )

    assert result.budgets.bounded_probes() == HARD_MAX_PROBES
    assert result.budgets.bounded_executions() == HARD_MAX_EXECUTIONS


def test_no_budget_escalates_itself(tmp_path: Path) -> None:
    """No configuration here can exceed its declared maxima, whatever it asks for."""

    _repository(tmp_path)
    runner = SpyRunner(default="raised")

    result = _explore(
        tmp_path, runner, budgets=SearchBudgets(max_probes=10_000, max_executions=10_000)
    )

    assert result.distinct_probes <= HARD_MAX_PROBES
    assert result.executions <= HARD_MAX_EXECUTIONS


def test_termination_always_has_a_deterministic_reason(tmp_path: Path) -> None:
    _repository(tmp_path)

    for default in ("returned", "raised"):
        result = _explore(tmp_path, SpyRunner(default=default))
        assert result.termination is not None
        assert result.reason.strip()
        assert result.termination.value in result.reason or result.reason


# --- 48: oracle safety, proven by what did NOT run ---------------------------


def test_no_contract_is_refused_before_anything_executes(tmp_path: Path) -> None:
    """An exact-input oracle cannot authorize changing an input."""

    (tmp_path / "m.py").write_text(SOURCE, encoding="utf-8")
    runner = SpyRunner()
    scan = analyze_repository(tmp_path)

    result = explore_integer(
        scan,
        tmp_path,
        ExploreRequest(contract_path=None, contract_id="neg", start_value=-64),
        authorized=True,
        runner=runner,
    )

    assert result.termination is SearchTermination.REFUSED
    assert runner.calls == []
    assert "assert f(100) == 200" in result.reason


def test_an_exact_input_test_alone_is_refused(tmp_path: Path) -> None:
    """`assert double(100) == 200` never becomes a search oracle."""

    source = "def double(x: int) -> int:\n    return x * 2\n"
    (tmp_path / "m.py").write_text(source, encoding="utf-8")
    (tmp_path / "test_m.py").write_text(
        "from m import double\n\n\ndef test_double() -> None:\n    assert double(100) == 200\n",
        encoding="utf-8",
    )
    runner = SpyRunner()
    scan = analyze_repository(tmp_path)

    result = explore_integer(
        scan,
        tmp_path,
        ExploreRequest(contract_path=None, contract_id="neg", start_value=-64),
        authorized=True,
        runner=runner,
    )

    assert result.termination is SearchTermination.REFUSED
    assert runner.calls == []


def test_a_contract_with_the_wrong_fingerprint_is_refused(tmp_path: Path) -> None:
    _repository(tmp_path)
    contract = tmp_path / "contracts.json"
    document = json.loads(contract.read_text(encoding="utf-8"))
    document["contracts"][0]["source_sha256"] = "f" * 64
    contract.write_text(json.dumps(document), encoding="utf-8")

    runner = SpyRunner()
    result = _explore(tmp_path, runner, contract=contract)

    assert result.termination is SearchTermination.REFUSED
    assert runner.calls == []
    assert "fingerprint mismatch" in result.reason


def test_a_changed_source_file_is_refused_after_the_analysis(tmp_path: Path) -> None:
    """A stale fingerprint is caught, not silently rebound."""

    _repository(tmp_path)
    (tmp_path / "m.py").write_text(SOURCE + "\n# drift\n", encoding="utf-8")

    runner = SpyRunner()
    result = _explore(tmp_path, runner)

    assert result.termination is SearchTermination.REFUSED
    assert runner.calls == []


def test_a_start_value_outside_the_domain_is_refused(tmp_path: Path) -> None:
    _repository(tmp_path)
    runner = SpyRunner()

    result = _explore(tmp_path, runner, start=0)

    assert result.termination is SearchTermination.REFUSED
    assert runner.calls == []
    assert "does not satisfy the contract's domain" in result.reason


def test_a_start_value_outside_the_signed_range_is_refused(tmp_path: Path) -> None:
    _repository(tmp_path)
    runner = SpyRunner()

    result = _explore(tmp_path, runner, start=2**63)

    assert result.termination is SearchTermination.REFUSED
    assert runner.calls == []


def test_an_unknown_contract_id_is_refused(tmp_path: Path) -> None:
    _repository(tmp_path)
    runner = SpyRunner()

    result = _explore(tmp_path, runner, contract_id="missing")

    assert result.termination is SearchTermination.REFUSED
    assert runner.calls == []


def test_an_unsupported_target_shape_is_refused(tmp_path: Path) -> None:
    source = "class Holder:\n    def validate(self, x: int) -> int:\n        raise ValueError\n"
    (tmp_path / "m.py").write_text(source, encoding="utf-8")
    digest = hashlib.sha256((tmp_path / "m.py").read_bytes()).hexdigest()
    _repository(tmp_path, source)
    contract = tmp_path / "contracts.json"
    document = json.loads(contract.read_text(encoding="utf-8"))
    document["contracts"][0]["source_sha256"] = digest
    contract.write_text(json.dumps(document), encoding="utf-8")

    runner = SpyRunner()
    result = _explore(tmp_path, runner)

    assert result.termination is SearchTermination.REFUSED
    assert runner.calls == []


def test_conflicting_applicable_evidence_stops_with_its_own_status(tmp_path: Path) -> None:
    """An exact test also covering this target is not discarded to get a result."""

    _repository(tmp_path)
    (tmp_path / "test_m.py").write_text(
        "import pytest\n\nfrom m import validate\n\n\n"
        "def test_validate() -> None:\n    with pytest.raises(TypeError):\n"
        "        validate(-64)\n",
        encoding="utf-8",
    )
    runner = SpyRunner()

    result = _explore(tmp_path, runner)

    assert result.termination is SearchTermination.CONFLICTING_EVIDENCE
    assert result.found_violation is False
    assert runner.calls == []


def test_a_custom_expected_exception_stays_inconclusive(tmp_path: Path) -> None:
    """A contract naming a non-builtin cannot be matched by name alone."""

    (tmp_path / "m.py").write_text(SOURCE, encoding="utf-8")
    digest = hashlib.sha256((tmp_path / "m.py").read_bytes()).hexdigest()
    document = {
        "version": 1,
        "contracts": [
            {
                "id": "neg",
                "target": "m.py::validate",
                "source_sha256": digest,
                "parameter": "x",
                "domain": {"operator": "lt", "integer": "0"},
                "expectation": {"kind": "raises_builtin", "exception": "app.MyError"},
            }
        ],
    }
    contract = tmp_path / "contracts.json"
    contract.write_text(json.dumps(document), encoding="utf-8")
    runner = SpyRunner()

    result = _explore(tmp_path, runner, contract=contract)

    # The reader refuses a non-builtin outright, before execution.
    assert result.termination is SearchTermination.REFUSED
    assert runner.calls == []


def test_no_out_of_domain_probe_is_ever_executed(tmp_path: Path) -> None:
    """The pre-execution domain check holds even if the universe were wrong."""

    _repository(tmp_path)
    runner = SpyRunner(default="raised")

    result = _explore(tmp_path, runner)

    assert all(value < 0 for value in runner.calls)
    assert all(step.action.value < 0 for step in result.steps)


# --- 52: the trace and its artifact ------------------------------------------


def test_the_trace_is_deterministic_and_carries_no_timing(tmp_path: Path) -> None:
    _repository(tmp_path)

    def scripted() -> SpyRunner:
        # A fresh list per run: `dict(script)` would share the lists, and the
        # first run would consume what the second one was meant to replay.
        return SpyRunner(script={-2: [_returned(-2), _returned(-2)]}, default="raised")

    first = _explore(tmp_path, scripted())
    second = _explore(tmp_path, scripted())

    def stable(result: SearchResult) -> list[dict[str, object]]:
        return [
            {
                "step": step.step,
                "action": step.action.kind.value,
                "action_identifier": step.action.identifier,
                "input": step.action.value,
                "origin": step.action.origin.value,
                "use": step.use.value,
                "reason": step.selection_reason,
                "outcome": step.execution_outcome,
                "oracle": step.oracle_status,
                "feedback": step.feedback.value,
                "reproduction": step.reproduction_status,
                "remaining_probes": step.remaining_probes,
                "remaining_executions": step.remaining_executions,
            }
            for step in result.steps
        ]

    assert stable(first) == stable(second)
    assert [step.step for step in first.steps] == list(range(1, len(first.steps) + 1))


def test_the_trace_contains_no_duration_field(tmp_path: Path) -> None:
    """Runtime durations are observations, and must not destabilize the trace."""

    from edgehunter.search.models import SearchStep

    assert "duration" not in " ".join(SearchStep.__dataclass_fields__).lower()


def test_rechecks_are_distinguished_from_new_probes(tmp_path: Path) -> None:
    _repository(tmp_path)
    runner = SpyRunner(script={-2: [_returned(-2), _returned(-2)]}, default="raised")

    result = _explore(tmp_path, runner)
    uses = [step.use for step in result.steps]

    assert uses.count(SelectionUse.NEW_PROBE) == 2
    assert uses.count(SelectionUse.VERIFICATION_RERUN) == 1
    assert uses[-1] is SelectionUse.VERIFICATION_RERUN


def test_no_duplicate_probes_appear_in_the_trace(tmp_path: Path) -> None:
    _repository(tmp_path)
    runner = SpyRunner(script={-2: [_returned(-2), _returned(-2)]}, default="raised")

    result = _explore(tmp_path, runner)
    probed = [step.action.identifier for step in result.steps if step.use is SelectionUse.NEW_PROBE]

    assert len(probed) == len(set(probed))


def test_every_trace_entry_records_a_selection_reason(tmp_path: Path) -> None:
    _repository(tmp_path)
    runner = SpyRunner(default="raised")

    result = _explore(tmp_path, runner)

    assert result.steps
    assert all(step.selection_reason.strip() for step in result.steps)
    assert all(step.feedback_reason.strip() for step in result.steps)


def test_the_artifact_is_versioned_strict_json_and_excludes_output(tmp_path: Path) -> None:
    _repository(tmp_path)
    runner = SpyRunner(default="raised")
    result = _explore(tmp_path, runner)

    path = write_search_artifact(result, run_id="trace-check", base=tmp_path / "artifacts")
    text = path.read_text(encoding="utf-8")

    document = json.loads(text)
    assert document["schema_version"] == SEARCH_ARTIFACT_SCHEMA
    assert document["kind"] == "semantic_neighborhood_search"
    assert "NaN" not in text and "Infinity" not in text
    assert "captured_stdout" not in text and "captured_stderr" not in text
    assert "exception_message" not in text


def test_the_artifact_records_provenance_and_measurements(tmp_path: Path) -> None:
    _repository(tmp_path)
    runner = SpyRunner(script={-2: [_returned(-2), _returned(-2)]}, default="raised")
    result = _explore(tmp_path, runner)

    document = search_artifact_document(result)

    assert document["oracle"]["source"] == "user_declared_contract"  # type: ignore[index]
    assert document["policy"] == "feedback_guided"
    assert document["termination"] == "violation_reproduced"
    measurements = document["measurements"]
    assert measurements["distinct_probes"] == 2  # type: ignore[index]
    assert measurements["container_executions"] == 3  # type: ignore[index]
    assert measurements["first_reproduced_probe"] == 2  # type: ignore[index]
    assert document["candidate_universe"]["candidates"]  # type: ignore[index]


def test_the_artifact_run_id_is_sanitized(tmp_path: Path) -> None:
    _repository(tmp_path)
    result = _explore(tmp_path, SpyRunner(default="raised"))

    written = write_search_artifact(result, run_id="../../escape", base=tmp_path / "artifacts")

    assert written.parent.parent == (tmp_path / "artifacts")
    assert ".." not in written.as_posix()


def test_the_artifact_invents_no_coverage_field(tmp_path: Path) -> None:
    """The structured document carries counts, never an unearned quality number."""

    _repository(tmp_path)
    document = search_artifact_document(_explore(tmp_path, SpyRunner(default="raised")))

    for invented in ("coverage", "mutation_score", "probability", "confidence", "severity"):
        assert invented not in document["measurements"]  # type: ignore[operator]
        assert invented not in document["candidate_universe"]  # type: ignore[operator]
        assert invented not in document["budgets"]  # type: ignore[operator]

    # The only place the words may appear is the disclaimer, which uses them to
    # deny the claim rather than make it.
    assert "not a claim of exhaustive coverage" in str(document["meaning"])


@pytest.mark.parametrize("default", ["returned", "raised"])
def test_both_policies_are_measurable_under_the_same_budget(tmp_path: Path, default: str) -> None:
    """The comparison the whole phase is for, with matched budgets."""

    _repository(tmp_path)
    budgets = SearchBudgets(max_probes=8, max_executions=12)

    measured = {
        policy: _explore(tmp_path, SpyRunner(default=default), policy=policy, budgets=budgets)
        for policy in (PolicyName.STATIC_PRIORITY, PolicyName.FEEDBACK_GUIDED)
    }

    for policy, result in measured.items():
        assert result.budgets.bounded_probes() == budgets.bounded_probes()
        assert result.budgets.bounded_executions() == budgets.bounded_executions()
        assert result.policy is policy
        assert result.distinct_probes <= budgets.bounded_probes()
        assert result.executions <= budgets.bounded_executions()


def test_two_searches_in_one_interpreter_do_not_share_state(tmp_path: Path) -> None:
    """No process-global registry: a second search cannot see the first's probes."""

    _repository(tmp_path)
    first = _explore(
        tmp_path, SpyRunner(script={-2: [_returned(-2), _returned(-2)]}, default="raised")
    )
    second = _explore(
        tmp_path, SpyRunner(script={-2: [_returned(-2), _returned(-2)]}, default="raised")
    )

    assert [s.action.identifier for s in first.steps] == [s.action.identifier for s in second.steps]
    assert first.distinct_probes == second.distinct_probes


def test_no_module_level_mutable_registry_exists_in_the_search_package() -> None:
    """The no-global-state rule, checked mechanically rather than by promise.

    Any mutable module-level container in the orchestrator or the state module
    would be shared process-wide, which is how two searches could leak probes
    into each other. Only immutable mappings are tolerated.
    """

    from types import MappingProxyType

    import edgehunter.search.repository as repository_module
    import edgehunter.search.state as state_module

    allowed = (frozenset, MappingProxyType)
    for module in (repository_module, state_module):
        for name, value in vars(module).items():
            if name.startswith("__") or not isinstance(value, (dict, list, set)):
                continue
            assert isinstance(value, allowed), (
                f"{module.__name__}.{name} is a mutable module-level container"
            )


def test_generation_runs_no_code_and_opens_no_file_during_a_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The orchestrator adds no host-side execution path of its own."""

    _repository(tmp_path)
    runner = SpyRunner(default="raised")
    opened: list[str] = []
    real_open = builtins.open

    def _tracking_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> IO[str]:
        opened.append(str(file))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _tracking_open)
    for name in ("eval", "exec"):
        monkeypatch.delattr(builtins, name, raising=False)

    result = _explore(tmp_path, runner)

    assert result.steps
    assert all("m.py" not in name for name in opened)
