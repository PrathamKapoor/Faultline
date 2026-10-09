"""CLI tests for ``run --evaluate`` and ``--recheck``.

These run the Typer application in-process with the execution backend replaced,
which is what makes the whole evaluated-output surface testable without starting a
container. The assertions are about what the CLI is *allowed to say*: a
contradiction may be reported, a bug may not.

Phase 6A behaviour is asserted alongside it, because ``--evaluate`` is additive:
without the flag the output must still be exactly the observation-only report.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from edgehunter import cli
from edgehunter.evaluation.models import (
    ApplicabilityKind,
    AppliedOracle,
    BoundCall,
    EvaluationReport,
    FindingCandidate,
    FindingKind,
    OracleEvidenceRef,
    OracleKind,
    OracleStatus,
    ReproductionStatus,
    ReproductionSummary,
)
from edgehunter.execution.codec import TypedValue, ValueKind
from edgehunter.execution.models import (
    ExecutionResult,
    InvocationArguments,
    OutcomeKind,
    RunReport,
    RuntimeObservation,
    SandboxState,
    SandboxStatus,
    TargetRef,
)
from edgehunter.execution.repository import RunOptions

runner = CliRunner()

TARGET = TargetRef(relative_file="calculator.py", qualified_name="add", lineno=1)
READY = SandboxStatus(backend="docker", state=SandboxState.READY)
DIGEST = "c" * 64
EVIDENCE = OracleEvidenceRef(
    kind=OracleKind.TEST_ASSERTION,
    relative_path=Path("tests/test_calculator.py"),
    lineno=5,
    detail="assert add(0, 0) == 0",
)


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "calculator.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b + 1\n", encoding="utf-8"
    )
    (root / "test_calculator.py").write_text(
        "from calculator import add\n\n\ndef test_add():\n    assert add(0, 0) == 0\n",
        encoding="utf-8",
    )
    return root


def _result(*, returned: bool = True) -> ExecutionResult:
    observation = (
        RuntimeObservation(outcome=OutcomeKind.RETURNED, return_value=_one())
        if returned
        else RuntimeObservation(outcome=OutcomeKind.RAISED, exception_type="ZeroDivisionError")
    )
    return ExecutionResult(
        execution_id="run-1",
        target=TARGET,
        action_id="add:a:set_zero:",
        outcome=observation.outcome,
        sandbox=READY,
        duration_seconds=0.042,
        observation=observation,
        arguments=InvocationArguments(args=(), kwargs=()),
    )


def _one() -> TypedValue:
    return TypedValue(kind=ValueKind.INTEGER, scalar="1")


def _report(
    *,
    status: OracleStatus,
    kind: FindingKind | None,
    reproduction: ReproductionStatus = ReproductionStatus.NOT_RECHECKED,
    attempts: int = 1,
) -> EvaluationReport:
    entry = AppliedOracle(
        oracle=OracleKind.TEST_ASSERTION,
        applicability_kind=ApplicabilityKind.EXACT_MATCH,
        status=status,
        reason="the observation contradicted the recorded expectation",
        evidence=(EVIDENCE,),
        expected_description="add(0, 0) == 0",
        observed_description="1 (integer)",
    )
    findings: tuple[FindingCandidate, ...] = ()
    if kind is not None:
        findings = (
            FindingCandidate(
                finding_id="ehf-abcdef123456",
                kind=kind,
                target_identity=TARGET.identity,
                source_digest=DIGEST,
                argument_description="calculator.py::add(a=integer:0, b=integer:0)",
                oracle_kind=OracleKind.TEST_ASSERTION,
                oracle_location="tests/test_calculator.py:5",
                expected_behavior="add(0, 0) == 0",
                observed_behavior="1 (integer)",
                oracle_status=status,
                reproduction=reproduction,
                attempts=attempts,
                consistent=reproduction is not ReproductionStatus.FLAKY,
                primary_action="add:a:set_zero:",
                assignment_provenance=("a=0 [adversarial_action]", "b=0 [existing_test_baseline]"),
                reproduction_command="edgehunter run . --target 'calculator.py::add'",
                detail="expected add(0, 0) == 0; observed integer:1",
            ),
        )
    return EvaluationReport(
        target_identity=TARGET.identity,
        source_digest=DIGEST,
        execution_outcome=OutcomeKind.RETURNED,
        executions=(entry,),
        primary=entry,
        findings=findings,
        reproduction=ReproductionSummary(
            status=reproduction,
            attempts=attempts,
            equivalent_mismatches=attempts,
            failures=0,
        ),
        invocation=BoundCall(
            target_identity=TARGET.identity,
            bindings=(("a", "integer:0"), ("b", "integer:0")),
        ),
    )


def _install(monkeypatch: pytest.MonkeyPatch, reports: tuple[EvaluationReport, ...]) -> None:
    """Replace the execution and evaluation boundaries with fixed values."""
    run_report = RunReport(
        results=(_result(),),
        sandbox=READY,
        skipped=(),
        rechecks={},
        staged_digests=(("calculator.py", DIGEST),),
    )
    monkeypatch.setattr(cli, "execute_target", lambda *args, **kwargs: run_report)
    monkeypatch.setattr(cli, "evaluate_run", lambda *args, **kwargs: reports)
    monkeypatch.setattr(cli, "runtime_context", lambda: Path.cwd())


class TestEvaluateFlagIsAdditive:
    def test_without_the_flag_the_output_still_only_observes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, ())
        root = _repository(tmp_path)

        result = runner.invoke(
            cli.app,
            ["run", str(root), "--target", "add", "--allow-execution"],
        )

        assert result.exit_code == 0
        assert "Oracle evaluation: not performed" in result.output
        assert "Finding: none (this phase only observes)" in result.output
        assert "Oracle Evaluation" not in result.output

    def test_the_evaluate_flag_reaches_the_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, (_report(status=OracleStatus.MISMATCH, kind=None),))
        root = _repository(tmp_path)

        result = runner.invoke(
            cli.app,
            ["run", str(root), "--target", "add", "--allow-execution", "--evaluate"],
        )

        assert result.exit_code == 0
        assert "EdgeHunter — Oracle Evaluation" in result.output

    def test_scan_never_gains_an_evaluation_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, (_report(status=OracleStatus.MISMATCH, kind=None),))
        root = _repository(tmp_path)

        result = runner.invoke(cli.app, ["scan", str(root), "--evaluate"])

        # `scan` stays static; the flag belongs to `run` alone.
        assert result.exit_code != 0
        assert "No such option" in result.output

    def test_scan_still_reports_no_execution(self, tmp_path: Path) -> None:
        root = _repository(tmp_path)

        result = runner.invoke(cli.app, ["scan", str(root)])

        assert result.exit_code == 0
        assert "Sandbox" not in result.output


class TestOutputNeverClaimsABug:
    def test_a_mismatch_is_reported_as_a_contradiction_not_a_bug(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(
            monkeypatch,
            (
                _report(
                    status=OracleStatus.MISMATCH, kind=FindingKind.OBSERVED_EXPECTATION_MISMATCH
                ),
            ),
        )
        root = _repository(tmp_path)

        result = runner.invoke(
            cli.app,
            ["run", str(root), "--target", "add", "--allow-execution", "--evaluate"],
        )

        assert result.exit_code == 0
        assert "MISMATCH" in result.output
        assert "Bug severity: not assessed" in result.output
        for forbidden in ("CRITICAL", "VULNERABILITY", "confirmed bug", "Confirmed"):
            assert forbidden not in result.output

    def test_an_observed_mismatch_is_titled_as_such(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(
            monkeypatch,
            (
                _report(
                    status=OracleStatus.MISMATCH, kind=FindingKind.OBSERVED_EXPECTATION_MISMATCH
                ),
            ),
        )
        root = _repository(tmp_path)

        result = runner.invoke(
            cli.app,
            ["run", str(root), "--target", "add", "--allow-execution", "--evaluate"],
        )

        assert "Observed Expectation Mismatch" in result.output
        assert "ehf-abcdef123456" in result.output

    def test_a_reproduced_mismatch_says_so_with_its_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(
            monkeypatch,
            (
                _report(
                    status=OracleStatus.MISMATCH,
                    kind=FindingKind.REPRODUCED_EXPECTATION_MISMATCH,
                    reproduction=ReproductionStatus.REPRODUCED,
                    attempts=4,
                ),
            ),
        )
        root = _repository(tmp_path)

        result = runner.invoke(
            cli.app,
            [
                "run",
                str(root),
                "--target",
                "add",
                "--allow-execution",
                "--evaluate",
                "--recheck",
                "3",
            ],
        )

        assert "4 / 4 equivalent mismatches" in result.output
        assert "Reproduced Expectation Mismatch" in result.output


class TestHonestInconclusiveOutput:
    def test_no_applicable_oracle_is_a_normal_supported_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entry = AppliedOracle(
            oracle=OracleKind.TEST_ASSERTION,
            applicability_kind=ApplicabilityKind.NO_EXPECTATION,
            status=OracleStatus.INCONCLUSIVE,
            reason="the repository records no explicit expectation for this invocation",
            evidence=(),
            expected_description="no applicable explicit oracle",
            observed_description="raised ZeroDivisionError",
        )
        report = replace(
            _report(status=OracleStatus.INCONCLUSIVE, kind=None),
            execution_outcome=OutcomeKind.RAISED,
            executions=(entry,),
            primary=entry,
        )
        _install(monkeypatch, (report,))
        root = _repository(tmp_path)

        result = runner.invoke(
            cli.app,
            ["run", str(root), "--target", "add", "--allow-execution", "--evaluate"],
        )

        assert result.exit_code == 0
        assert "INCONCLUSIVE" in result.output
        assert "Finding: None" in result.output

    def test_a_match_reports_no_finding(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, (_report(status=OracleStatus.MATCH, kind=None),))
        root = _repository(tmp_path)

        result = runner.invoke(
            cli.app,
            ["run", str(root), "--target", "add", "--allow-execution", "--evaluate"],
        )

        assert "MATCH" in result.output
        assert "Finding: None" in result.output

    def test_a_conflict_is_shown_as_a_conflict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(
            monkeypatch,
            (_report(status=OracleStatus.CONFLICTING_EVIDENCE, kind=FindingKind.INCONCLUSIVE),),
        )
        root = _repository(tmp_path)

        result = runner.invoke(
            cli.app,
            ["run", str(root), "--target", "add", "--allow-execution", "--evaluate"],
        )

        assert "CONFLICTING_EVIDENCE" in result.output
        assert "Inconclusive" in result.output


class TestInvocationAndProvenanceAreShown:
    def test_the_exact_invocation_is_shown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, (_report(status=OracleStatus.MISMATCH, kind=None),))
        root = _repository(tmp_path)

        result = runner.invoke(
            cli.app,
            ["run", str(root), "--target", "add", "--allow-execution", "--evaluate"],
        )

        assert "calculator.py::add(a=integer:0, b=integer:0)" in result.output

    def test_the_oracle_location_and_provenance_are_shown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(
            monkeypatch,
            (
                _report(
                    status=OracleStatus.MISMATCH, kind=FindingKind.OBSERVED_EXPECTATION_MISMATCH
                ),
            ),
        )
        root = _repository(tmp_path)

        result = runner.invoke(
            cli.app,
            ["run", str(root), "--target", "add", "--allow-execution", "--evaluate"],
        )

        assert "tests/test_calculator.py:5" in result.output
        assert "a=0 [adversarial_action]" in result.output

    def test_the_verified_source_snapshot_is_shown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, (_report(status=OracleStatus.MATCH, kind=None),))
        root = _repository(tmp_path)

        result = runner.invoke(
            cli.app,
            ["run", str(root), "--target", "add", "--allow-execution", "--evaluate"],
        )

        assert "Source snapshot:" in result.output
        assert "(verified)" in result.output


class TestAuthorizationIsNotBypassable:
    def test_evaluate_still_requires_allow_execution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install(monkeypatch, (_report(status=OracleStatus.MISMATCH, kind=None),))
        root = _repository(tmp_path)

        result = runner.invoke(cli.app, ["run", str(root), "--target", "add", "--evaluate"])

        assert result.exit_code == cli.EXIT_EXECUTION_REFUSED
        assert "--allow-execution" in result.output

    def test_evaluate_does_not_change_the_run_exit_codes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A contradiction is not a distinct exit code. `run` still uses 0/2/3."""
        _install(monkeypatch, (_report(status=OracleStatus.MISMATCH, kind=None),))
        root = _repository(tmp_path)

        result = runner.invoke(
            cli.app,
            ["run", str(root), "--target", "add", "--allow-execution", "--evaluate"],
        )

        assert result.exit_code == 0
        assert result.exit_code not in (1,)


class TestRecheckInput:
    def test_a_negative_recheck_is_invalid_input(self, tmp_path: Path) -> None:
        root = _repository(tmp_path)

        result = runner.invoke(
            cli.app,
            [
                "run",
                str(root),
                "--target",
                "add",
                "--allow-execution",
                "--evaluate",
                "--recheck",
                "-1",
            ],
        )

        assert result.exit_code == cli.EXIT_INVALID_INPUT

    def test_recheck_is_clamped_to_the_hard_maximum(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from edgehunter.evaluation.repository import HARD_MAX_RECHECKS

        captured: dict[str, int] = {}

        run_report = RunReport(results=(_result(),), sandbox=READY, skipped=())

        def _capture(
            scan: object, source_root: Path, options: RunOptions, **kwargs: object
        ) -> RunReport:
            captured["rechecks"] = options.rechecks
            captured["attempts"] = options.attempts_per_plan
            return run_report

        monkeypatch.setattr(cli, "execute_target", _capture)
        monkeypatch.setattr(cli, "evaluate_run", lambda *args, **kwargs: ())
        monkeypatch.setattr(cli, "runtime_context", lambda: Path.cwd())
        root = _repository(tmp_path)

        result = runner.invoke(
            cli.app,
            [
                "run",
                str(root),
                "--target",
                "add",
                "--allow-execution",
                "--evaluate",
                "--recheck",
                "999",
            ],
        )

        assert result.exit_code == 0
        assert captured["rechecks"] == HARD_MAX_RECHECKS
        # The total container budget stays bounded no matter what was requested.
        assert captured["attempts"] == HARD_MAX_RECHECKS + 1

    def test_recheck_appears_in_the_help(self) -> None:
        result = runner.invoke(cli.app, ["run", "--help"])

        assert result.exit_code == 0
        assert "--evaluate" in result.output
        assert "--recheck" in result.output
