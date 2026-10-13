"""Acceptance, failure-signature, budget, and reproduction rules.

Every test here drives the search with a fabricated attempt runner, so the whole
decision procedure is exercised without Docker. The fabricated results are typed
:class:`ExecutionResult` objects carrying real :class:`RuntimeObservation` values,
so the contract adapter's judgement goes through exactly the same code the sandbox
path uses.

The ten acceptance conditions are the point of this file. In particular:
"any exception" is never treated as "the same failure".
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

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
from edgehunter.shrink.models import (
    HARD_MAX_EXECUTIONS,
    HARD_MAX_PROPOSALS,
    AttemptOutcome,
    InvocationOrigin,
    OracleSource,
    ShrinkPolicy,
    ShrinkResult,
    ShrinkStatus,
)
from edgehunter.shrink.repository import (
    SHRINK_ARTIFACT_SCHEMA,
    ShrinkRequest,
    shrink_artifact_document,
    shrink_integer,
    write_shrink_artifact,
)
from edgehunter.shrink.signature import FailureSignature, signature_of

SOURCE = "def validate(x: int) -> int:\n    return x\n"


def _target() -> TargetRef:
    return TargetRef(relative_file="m.py", qualified_name="validate", lineno=1)


def _returned(value: int) -> ExecutionResult:
    return ExecutionResult(
        execution_id=f"e{value}",
        target=_target(),
        action_id=f"shrink={value}",
        outcome=OutcomeKind.RETURNED,
        sandbox=SandboxStatus(backend="docker", state=SandboxState.READY),
        observation=RuntimeObservation(
            outcome=OutcomeKind.RETURNED, return_value=TypedValue(ValueKind.INTEGER, str(value))
        ),
        arguments=InvocationArguments(args=(TypedValue(ValueKind.INTEGER, str(value)),), kwargs=()),
    )


def _raised(value: int, name: str) -> ExecutionResult:
    cls = getattr(__builtins__, name, None) if not isinstance(__builtins__, dict) else None
    del cls
    import builtins

    exception_class = getattr(builtins, name)
    instance = exception_class.__new__(exception_class)
    from edgehunter.runtime.worker import _exception_identity

    document = _exception_identity(instance)
    return ExecutionResult(
        execution_id=f"e{value}",
        target=_target(),
        action_id=f"shrink={value}",
        outcome=OutcomeKind.RAISED,
        sandbox=SandboxStatus(backend="docker", state=SandboxState.READY),
        observation=RuntimeObservation(
            outcome=OutcomeKind.RAISED,
            exception_type=name,
            exception_message="detail",
            exception_module=document.get("exception_module"),
            exception_qualname=document.get("exception_qualname"),
            exception_builtin=document.get("exception_builtin"),
            exception_builtin_lineage=tuple(document.get("exception_builtin_lineage", ())),
        ),
        arguments=InvocationArguments(args=(TypedValue(ValueKind.INTEGER, str(value)),), kwargs=()),
    )


def _infrastructure(value: int, outcome: OutcomeKind) -> ExecutionResult:
    return ExecutionResult(
        execution_id=f"e{value}",
        target=_target(),
        action_id=f"shrink={value}",
        outcome=outcome,
        sandbox=SandboxStatus(backend="docker", state=SandboxState.READY),
        observation=RuntimeObservation(outcome=outcome),
        arguments=InvocationArguments(args=(TypedValue(ValueKind.INTEGER, str(value)),), kwargs=()),
    )


def _opaque(value: int) -> ExecutionResult:
    result = _returned(value)
    return ExecutionResult(
        execution_id=result.execution_id,
        target=result.target,
        action_id=result.action_id,
        outcome=result.outcome,
        sandbox=result.sandbox,
        observation=RuntimeObservation(
            outcome=OutcomeKind.RETURNED, opaque_return="returned generator (not representable)"
        ),
        arguments=result.arguments,
    )


def _section(document: dict[str, object], key: str) -> dict[str, object]:
    """One nested object of the artifact document, for readable assertions."""

    value = document[key]
    assert isinstance(value, dict), (key, type(value))
    return value


def _signature(result: ExecutionResult) -> FailureSignature:
    """The failure signature of a result whose observation is known to exist."""

    assert result.observation is not None
    signature = signature_of(result.observation)
    assert signature is not None
    return signature


class ScriptedRunner:
    """A runner that returns a scripted outcome per value.

    ``RETURNED`` is the default so a test only describes the values it cares
    about, and ``calls`` records every request so budget behaviour is observable.
    """

    def __init__(self, script: dict[int, list[ExecutionResult]] | None = None) -> None:
        self.script = script or {}
        self.calls: list[tuple[int, int]] = []

    def execute_attempts(self, value: int, *, attempts: int) -> tuple[ExecutionResult, ...]:
        self.calls.append((value, attempts))
        scripted = self.script.get(value)
        if scripted is None:
            return tuple(_returned(value) for _ in range(attempts))
        return tuple(scripted[:attempts])


def _repository(tmp_path: Path, source: str = SOURCE) -> tuple[Path, Path, Path]:
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
    return tmp_path, contract, source_file


def _shrink(
    tmp_path: Path,
    runner: ScriptedRunner,
    *,
    seed: int = -100,
    policy: ShrinkPolicy | None = None,
) -> ShrinkResult:
    scan = analyze_repository(tmp_path)
    return shrink_integer(
        scan,
        tmp_path,
        ShrinkRequest(
            contract_path=tmp_path / "contracts.json",
            contract_id="neg",
            seed=seed,
            policy=policy or ShrinkPolicy(),
        ),
        authorized=True,
        runner=runner,
    )


class TestAcceptance:
    def test_a_smaller_mismatch_with_the_same_signature_is_accepted(self, tmp_path: Path) -> None:
        repo, _, _ = _repository(tmp_path)
        runner = ScriptedRunner()

        result = _shrink(repo, runner)

        assert result.status is ShrinkStatus.SHRUNK
        assert result.best_value == -1
        assert result.seed_value == -100
        assert result.reduced is True
        accepted = [a for a in result.attempts if a.outcome is AttemptOutcome.ACCEPTED]
        assert [a.value for a in accepted] == [-1]
        assert all(a.signature_matches for a in accepted)
        assert all(a.origin is InvocationOrigin.INTEGER_SHRINK_PROPOSAL for a in result.attempts)

    def test_a_smaller_candidate_that_matches_is_rejected(self, tmp_path: Path) -> None:
        repo, _, _ = _repository(tmp_path)
        # The seed returns (a mismatch), but -1 raises the expected exception.
        runner = ScriptedRunner({-1: [_raised(-1, "ValueError"), _raised(-1, "ValueError")]})

        result = _shrink(repo, runner)

        attempt = next(a for a in result.attempts if a.value == -1)
        assert attempt.outcome is AttemptOutcome.ORACLE_NOT_MISMATCH
        assert attempt.oracle_status == "match"
        assert attempt.accepted is False
        # -1 is not the best, so it was not the accepted reduction.
        assert result.best_value != -1

    def test_a_smaller_candidate_that_does_not_reproduce_is_rejected(self, tmp_path: Path) -> None:
        repo, _, _ = _repository(tmp_path)
        runner = ScriptedRunner({-1: [_returned(-1), _raised(-1, "ValueError")]})

        result = _shrink(repo, runner)

        attempt = next(a for a in result.attempts if a.value == -1)
        assert attempt.outcome is AttemptOutcome.NOT_REPRODUCIBLE
        assert attempt.accepted is False
        assert result.best_value != -1

    def test_a_smaller_candidate_with_an_infrastructure_failure_is_rejected(
        self, tmp_path: Path
    ) -> None:
        repo, _, _ = _repository(tmp_path)
        runner = ScriptedRunner({-1: [_returned(-1), _infrastructure(-1, OutcomeKind.TIMEOUT)]})

        result = _shrink(repo, runner)

        attempt = next(a for a in result.attempts if a.value == -1)
        assert attempt.outcome is AttemptOutcome.INFRASTRUCTURE_FAILURE
        assert attempt.accepted is False
        assert result.best_value != -1

    def test_an_unrepresentable_return_is_not_a_normal_return(self, tmp_path: Path) -> None:
        """A generator's return cannot be judged as a plain normal return."""

        repo, _, _ = _repository(tmp_path)
        runner = ScriptedRunner({-1: [_opaque(-1), _opaque(-1)]})

        result = _shrink(repo, runner)

        attempt = next(a for a in result.attempts if a.value == -1)
        assert attempt.outcome is AttemptOutcome.ORACLE_NOT_APPLICABLE
        assert attempt.oracle_status == "unsupported"
        assert attempt.accepted is False
        assert result.best_value != -1

    def test_a_candidate_outside_the_domain_is_rejected(self, tmp_path: Path) -> None:
        """Directly on the acceptance rules, independent of proposal generation."""

        from edgehunter.shrink.contract import Contract, RaisesBuiltinExpectation
        from edgehunter.shrink.domain import DomainOperator, IntegerDomain
        from edgehunter.shrink.repository import _judge_candidate

        contract = Contract(
            contract_id="neg",
            target_file="m.py",
            target_name="validate",
            source_sha256="0" * 64,
            parameter="x",
            domain=IntegerDomain(DomainOperator.LT, 0),
            expectation=RaisesBuiltinExpectation("ValueError"),
        )
        seed_signature = _signature(_returned(-100))

        attempt = _judge_candidate(
            contract=contract,
            value=0,
            results=(_returned(0), _returned(0)),
            snapshot_verified=True,
            best=-100,
            seed_signature=seed_signature,
            reproduction=2,
        )

        assert attempt.outcome is AttemptOutcome.OUTSIDE_DOMAIN
        assert attempt.in_domain is False
        assert attempt.accepted is False

    def test_a_candidate_that_is_not_simpler_is_rejected(self, tmp_path: Path) -> None:
        from edgehunter.shrink.contract import Contract, RaisesBuiltinExpectation
        from edgehunter.shrink.domain import DomainOperator, IntegerDomain
        from edgehunter.shrink.repository import _judge_candidate

        contract = Contract(
            contract_id="neg",
            target_file="m.py",
            target_name="validate",
            source_sha256="0" * 64,
            parameter="x",
            domain=IntegerDomain(DomainOperator.LT, 0),
            expectation=RaisesBuiltinExpectation("ValueError"),
        )

        attempt = _judge_candidate(
            contract=contract,
            value=-5,
            results=(_returned(-5), _returned(-5)),
            snapshot_verified=True,
            best=-2,
            seed_signature=_signature(_returned(-100)),
            reproduction=2,
        )

        assert attempt.outcome is AttemptOutcome.NOT_SIMPLER
        assert attempt.accepted is False

    def test_an_unverified_snapshot_is_rejected(self, tmp_path: Path) -> None:
        from edgehunter.shrink.contract import Contract, RaisesBuiltinExpectation
        from edgehunter.shrink.domain import DomainOperator, IntegerDomain
        from edgehunter.shrink.repository import _judge_candidate

        contract = Contract(
            contract_id="neg",
            target_file="m.py",
            target_name="validate",
            source_sha256="0" * 64,
            parameter="x",
            domain=IntegerDomain(DomainOperator.LT, 0),
            expectation=RaisesBuiltinExpectation("ValueError"),
        )

        attempt = _judge_candidate(
            contract=contract,
            value=-1,
            results=(_returned(-1), _returned(-1)),
            snapshot_verified=False,
            best=-100,
            seed_signature=_signature(_returned(-100)),
            reproduction=2,
        )

        assert attempt.outcome is AttemptOutcome.INFRASTRUCTURE_FAILURE
        assert "could not be decided" in attempt.reason


class TestFailureSignature:
    def test_a_changed_failure_character_is_rejected(self, tmp_path: Path) -> None:
        """The original returned; a trial that raises is a *different* failure.

        Every proposal is scripted to raise, so the only thing that can prevent a
        reduction here is the signature rule itself.
        """

        from edgehunter.shrink.domain import DomainOperator, IntegerDomain
        from edgehunter.shrink.proposals import propose_values

        repo, _, _ = _repository(tmp_path)
        domain = IntegerDomain(DomainOperator.LT, 0)
        proposals = propose_values(-100, domain, 8)
        script = {
            value: [_raised(value, "TypeError"), _raised(value, "TypeError")] for value in proposals
        }
        runner = ScriptedRunner(script)

        result = _shrink(repo, runner)

        assert len(result.attempts) == len(proposals)
        assert all(
            attempt.outcome is AttemptOutcome.SIGNATURE_MISMATCH for attempt in result.attempts
        )
        assert all("different failure" in attempt.reason for attempt in result.attempts)
        assert result.best_value == -100
        assert result.status is ShrinkStatus.NO_SMALLER_CASE_FOUND

    def test_an_equal_failure_character_keeps_being_reducible(self, tmp_path: Path) -> None:
        """When the original also raised, the same exception identity reduces."""

        repo, _, _ = _repository(tmp_path)
        runner = ScriptedRunner(
            {
                -100: [_raised(-100, "TypeError"), _raised(-100, "TypeError")],
                -1: [_raised(-1, "TypeError"), _raised(-1, "TypeError")],
            }
        )

        result = _shrink(repo, runner)

        assert result.seed_signature == "raised TypeError"
        assert result.status is ShrinkStatus.SHRUNK
        assert result.best_value == -1

    def test_a_different_exception_class_is_a_different_failure(self) -> None:
        a = _signature(_raised(1, "TypeError"))
        b = _signature(_raised(2, "ValueError"))

        assert a is not None and b is not None
        assert a.matches(b) is False

    def test_a_return_never_matches_a_raise(self) -> None:
        returned = _signature(_returned(1))
        raised = _signature(_raised(1, "TypeError"))

        assert returned is not None and raised is not None
        assert returned.matches(raised) is False
        assert raised.matches(returned) is False

    def test_infrastructure_outcomes_have_no_signature(self) -> None:
        """A timeout says nothing about what the target does with an input."""

        for outcome in (
            OutcomeKind.TIMEOUT,
            OutcomeKind.IMPORT_FAILURE,
            OutcomeKind.WORKER_FAILURE,
            OutcomeKind.PROTOCOL_ERROR,
        ):
            observation = _infrastructure(1, outcome).observation
            assert observation is not None
            assert signature_of(observation) is None

    def test_messages_and_timings_are_not_part_of_the_signature(self) -> None:
        first = _raised(1, "ValueError")
        second = _raised(2, "ValueError")
        second = ExecutionResult(
            execution_id="other",
            target=second.target,
            action_id=second.action_id,
            outcome=second.outcome,
            sandbox=second.sandbox,
            observation=RuntimeObservation(
                outcome=OutcomeKind.RAISED,
                exception_type="ValueError",
                exception_message="a completely different message",
                exception_module="builtins",
                exception_qualname="ValueError",
                exception_builtin=True,
                exception_builtin_lineage=("ValueError", "Exception", "BaseException"),
            ),
            arguments=second.arguments,
            duration_seconds=99.0,
        )

        left = _signature(first)
        right = _signature(second)
        assert left is not None and right is not None
        assert left.matches(right) is True


class TestSeedPreconditions:
    def test_a_seed_that_does_not_contradict_the_contract_stops_everything(
        self, tmp_path: Path
    ) -> None:
        repo, _, _ = _repository(tmp_path)
        runner = ScriptedRunner({-100: [_raised(-100, "ValueError"), _raised(-100, "ValueError")]})

        result = _shrink(repo, runner)

        assert result.status is ShrinkStatus.ORIGINAL_NOT_FAILING
        assert "does not contradict it" in result.reason
        # Nothing was proposed, so nothing beyond the seed ran.
        assert [value for value, _ in runner.calls] == [-100]

    def test_a_flaky_seed_stops_everything(self, tmp_path: Path) -> None:
        repo, _, _ = _repository(tmp_path)
        runner = ScriptedRunner({-100: [_returned(-100), _raised(-100, "ValueError")]})

        result = _shrink(repo, runner)

        assert result.status is ShrinkStatus.ORIGINAL_NOT_REPRODUCIBLE
        assert "not reproducible" in result.reason
        assert [value for value, _ in runner.calls] == [-100]

    def test_a_seed_with_an_infrastructure_failure_is_inconclusive(self, tmp_path: Path) -> None:
        repo, _, _ = _repository(tmp_path)
        runner = ScriptedRunner({-100: [_infrastructure(-100, OutcomeKind.TIMEOUT)] * 2})

        result = _shrink(repo, runner)

        assert result.status is ShrinkStatus.INCONCLUSIVE
        assert [value for value, _ in runner.calls] == [-100]

    def test_a_flaky_candidate_is_rejected_but_the_search_continues(self, tmp_path: Path) -> None:
        repo, _, _ = _repository(tmp_path)
        runner = ScriptedRunner({-1: [_returned(-1), _raised(-1, "ValueError")]})

        result = _shrink(repo, runner)

        assert result.status is ShrinkStatus.SHRUNK
        # -1 was rejected, and -2 was still tried afterwards.
        assert any(value == -2 for value, _ in runner.calls)


class TestBudgets:
    def test_the_proposal_count_is_bounded(self, tmp_path: Path) -> None:
        repo, _, _ = _repository(tmp_path)
        runner = ScriptedRunner()

        result = _shrink(repo, runner, policy=ShrinkPolicy(max_proposals=3))

        proposals = [value for value, _ in runner.calls if value != -100]
        assert len(proposals) <= 3
        assert result.proposals_considered <= 3

    def test_an_oversized_proposal_request_is_clamped(self, tmp_path: Path) -> None:
        repo, _, _ = _repository(tmp_path)
        runner = ScriptedRunner()

        result = _shrink(repo, runner, policy=ShrinkPolicy(max_proposals=10_000))

        assert result.proposals_considered <= HARD_MAX_PROPOSALS
        assert len([v for v, _ in runner.calls if v != -100]) <= HARD_MAX_PROPOSALS

    def test_the_execution_budget_is_enforced_and_never_escalated(self, tmp_path: Path) -> None:
        """A reduction is still a reduction; the budget stop is recorded separately.

        The search accepted `-1`, then ran out of containers. Reporting `SHRUNK`
        with `proposals_exhausted is False` is the honest pair: the reduction was
        verified, and the search was cut short. Conflating the two would either
        hide the reduction or overstate the search.
        """

        repo, _, _ = _repository(tmp_path)
        runner = ScriptedRunner()

        result = _shrink(repo, runner, policy=ShrinkPolicy(max_executions=5))

        total = sum(attempts for _, attempts in runner.calls)
        assert total <= 5
        assert result.executions_consumed <= 5
        assert result.status is ShrinkStatus.SHRUNK
        assert result.best_value == -1
        assert result.proposals_exhausted is False

    def test_a_budget_stop_with_no_reduction_is_reported_as_such(self, tmp_path: Path) -> None:
        """When nothing qualified, exhausting the budget is never a success."""

        from edgehunter.shrink.domain import DomainOperator, IntegerDomain
        from edgehunter.shrink.proposals import propose_values

        repo, _, _ = _repository(tmp_path)
        domain = IntegerDomain(DomainOperator.LT, 0)
        # Every proposal raises a *different* failure, so none can be accepted.
        script = {
            value: [_raised(value, "TypeError"), _raised(value, "TypeError")]
            for value in propose_values(-100, domain, 8)
        }
        runner = ScriptedRunner(script)

        result = _shrink(repo, runner, policy=ShrinkPolicy(max_executions=4))

        assert result.status is ShrinkStatus.BUDGET_EXHAUSTED
        assert result.best_value == -100
        assert result.proposals_exhausted is False
        assert result.executions_consumed <= 4

    def test_a_budget_too_small_for_the_seed_never_starts_proposals(self, tmp_path: Path) -> None:
        repo, _, _ = _repository(tmp_path)
        runner = ScriptedRunner()

        result = _shrink(repo, runner, policy=ShrinkPolicy(max_executions=1))

        assert result.status is ShrinkStatus.INCONCLUSIVE
        assert runner.calls == []

    def test_an_oversized_execution_request_is_clamped(self, tmp_path: Path) -> None:
        repo, _, _ = _repository(tmp_path)
        runner = ScriptedRunner()

        result = _shrink(repo, runner, policy=ShrinkPolicy(max_executions=100_000))

        assert result.executions_consumed <= HARD_MAX_EXECUTIONS

    def test_reproduction_attempts_are_bounded(self, tmp_path: Path) -> None:
        repo, _, _ = _repository(tmp_path)
        runner = ScriptedRunner()

        result = _shrink(
            repo, runner, policy=ShrinkPolicy(reproduction_attempts=99, max_proposals=1)
        )

        assert all(attempts <= 3 for _, attempts in runner.calls)
        assert result.executions_consumed <= 3 * 2

    def test_a_timeout_does_not_retry_forever(self, tmp_path: Path) -> None:
        """A timeout costs its attempts and stops; it never loops."""

        repo, _, _ = _repository(tmp_path)
        runner = ScriptedRunner(
            {
                -100: [_returned(-100)] * 2,
                -1: [_infrastructure(-1, OutcomeKind.TIMEOUT)] * 2,
            }
        )

        result = _shrink(repo, runner)

        assert result.status is ShrinkStatus.SHRUNK
        assert [v for v, _ in runner.calls].count(-1) == 1

    def test_the_search_stops_deterministically(self, tmp_path: Path) -> None:
        repo, _, _ = _repository(tmp_path)
        first = _shrink(repo, ScriptedRunner())
        second = _shrink(repo, ScriptedRunner())

        assert first.status == second.status
        assert first.best_value == second.best_value
        assert [a.value for a in first.attempts] == [a.value for a in second.attempts]


class TestNoHostExecution:
    def test_a_hostile_module_is_never_imported_by_the_planning_path(self, tmp_path: Path) -> None:
        """Contract parsing, eligibility, and planning must not execute the target."""

        marker = tmp_path / "MUST_NOT_EXIST.txt"
        repo, _, _ = _repository(
            tmp_path,
            "from pathlib import Path\n\n"
            f"Path({str(marker)!r}).write_text('bad')\n\n\n"
            "def validate(x: int) -> int:\n    return x\n",
        )
        runner = ScriptedRunner()

        result = _shrink(repo, runner)

        assert not marker.exists()
        # The whole decision path ran; the marker would exist if it had imported.
        assert result.status in (ShrinkStatus.SHRUNK, ShrinkStatus.NO_SMALLER_CASE_FOUND)

    def test_the_shrink_package_never_starts_a_process(self) -> None:
        """No subprocess, no eval/exec, and no import of target code anywhere."""

        import ast

        from edgehunter.shrink import repository

        path = Path(repository.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert called & {"eval", "exec", "compile", "__import__"} == set()

        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert "subprocess" not in imported
        assert "importlib" not in imported
        assert "pickle" not in imported


class TestArtifact:
    def test_the_artifact_records_provenance_and_the_budget(self, tmp_path: Path) -> None:
        repo, _, _ = _repository(tmp_path)
        result = _shrink(repo, ScriptedRunner())

        document = shrink_artifact_document(result, contract_sha="b" * 64)
        contract = _section(document, "contract")
        seed = _section(document, "seed")
        reduced = _section(document, "reduced")
        policy = _section(document, "policy")

        assert document["schema_version"] == SHRINK_ARTIFACT_SCHEMA
        assert document["kind"] == "counterexample_minimization"
        assert document["target_identity"] == "m.py::validate"
        assert contract["id"] == "neg"
        assert contract["source"] == OracleSource.USER_DECLARED_CONTRACT.value
        assert contract["file_sha256"] == "b" * 64
        assert seed["origin"] == InvocationOrigin.USER_SUPPLIED_SEED.value
        assert seed["value"] == -100
        assert reduced["value"] == -1
        assert policy["metric"] == "abs(value)"
        assert document["status"] == "shrunk"
        assert "not a universal proof" in str(document["meaning"]).lower()

    def test_the_artifact_excludes_target_output_and_messages(self, tmp_path: Path) -> None:
        repo, _, _ = _repository(tmp_path)
        result = _shrink(repo, ScriptedRunner())

        text = json.dumps(shrink_artifact_document(result, contract_sha="c" * 64))

        assert "captured_stdout" not in text
        assert "captured_stderr" not in text
        assert "exception_message" not in text
        assert "!!python" not in text

    def test_the_artifact_is_written_under_a_sanitized_run_id(self, tmp_path: Path) -> None:
        repo, _, _ = _repository(tmp_path)
        result = _shrink(repo, ScriptedRunner())

        written = write_shrink_artifact(
            result, contract_sha="d" * 64, run_id="../../escape", base=tmp_path / "out"
        )

        assert written.is_file()
        assert tmp_path in written.parents
        assert "escape" in written.parent.name
        assert json.loads(written.read_text(encoding="utf-8"))["schema_version"] == (
            SHRINK_ARTIFACT_SCHEMA
        )
