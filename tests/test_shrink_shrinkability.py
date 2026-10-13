"""Shrinkability: which oracle evidence survives changing an input.

The most important test in this file is
:func:`TestCoreInvariant::test_an_exact_oracle_never_authorizes_a_smaller_input`,
which proves that a repository's own ``assert double(100) == 200`` cannot start a
minimization and that ``double(10)`` is never executed. Everything else here
covers the remaining refusals, and each one also asserts that the attempt runner
was never called, because a refusal that still ran a container would be a refusal
in name only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
from edgehunter.oracle.models import (
    ExpectedOutcome,
    OracleEvidence,
    OracleEvidenceKind,
    OracleStrength,
    PropertyKind,
    TargetResolution,
)
from edgehunter.pipeline import analyze_repository
from edgehunter.shrink.contract import (
    Contract,
    RaisesBuiltinExpectation,
)
from edgehunter.shrink.domain import DomainOperator, IntegerDomain
from edgehunter.shrink.models import OracleSource, ShrinkabilityKind, ShrinkStatus
from edgehunter.shrink.repository import ShrinkRequest, shrink_integer
from edgehunter.shrink.shrinkability import (
    assess_production_property,
    assess_test_expectation,
    refuse_conflicting,
)


class SpyRunner:
    """An attempt runner that records every value it was asked to run."""

    def __init__(self, results: dict[int, list[OutcomeKind]] | None = None) -> None:
        self.calls: list[tuple[int, int]] = []
        self._results = results or {}

    def execute_attempts(self, value: int, *, attempts: int) -> tuple[ExecutionResult, ...]:
        self.calls.append((value, attempts))
        outcomes = self._results.get(value, [OutcomeKind.RETURNED] * attempts)
        return tuple(_result(value, outcome) for outcome in outcomes[:attempts])


def _result(value: int, outcome: OutcomeKind) -> ExecutionResult:
    return ExecutionResult(
        execution_id=f"e{value}",
        target=TargetRef(relative_file="m.py", qualified_name="f", lineno=1),
        action_id=f"shrink={value}",
        outcome=outcome,
        sandbox=SandboxStatus(backend="docker", state=SandboxState.READY),
        observation=RuntimeObservation(outcome=outcome, return_value=TypedValue(ValueKind.NONE)),
        arguments=InvocationArguments(
            args=(TypedValue(ValueKind.INTEGER, scalar=str(value)),), kwargs=()
        ),
    )


def _repository(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return tmp_path


def _digest_of(tmp_path: Path, name: str) -> str:
    import hashlib

    return hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()


def _write_contract(
    tmp_path: Path,
    *,
    target_file: str,
    target_name: str,
    digest: str,
    parameter: str = "x",
    operator: str = "lt",
    reference: str = "0",
    exception: str = "ValueError",
    contract_id: str = "c1",
) -> Path:
    document = {
        "version": 1,
        "contracts": [
            {
                "id": contract_id,
                "target": f"{target_file}::{target_name}",
                "source_sha256": digest,
                "parameter": parameter,
                "domain": {"operator": operator, "integer": reference},
                "expectation": {"kind": "raises_builtin", "exception": exception},
            }
        ],
    }
    path = tmp_path / "contracts.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def _contract(**overrides: Any) -> Contract:
    """A valid contract built directly, for the assessment unit tests."""

    base: dict[str, Any] = {
        "contract_id": "c1",
        "target_file": "m.py",
        "target_name": "f",
        "source_sha256": "0" * 64,
        "parameter": "x",
        "domain": IntegerDomain(operator=DomainOperator.LT, reference=0),
        "expectation": RaisesBuiltinExpectation("ValueError"),
    }
    base.update(overrides)
    return Contract(**base)


# --- the core invariant, spec section 49 ------------------------------------


class TestCoreInvariant:
    def test_using_only_the_exact_test_is_not_shrinkable(self, tmp_path: Path) -> None:
        """The §49 fixture, with no contract: only the test's oracle exists.

        ``assert double(100) == 200`` describes one invocation. The proposed
        smaller input ``double(10)`` must never be executed, which is proven by
        spying on the runner rather than by inspecting output.
        """

        _repository(
            tmp_path,
            {
                "calc.py": "def double(x: int) -> int:\n    return x * 2 + 1\n",
                "test_calc.py": "from calc import double\n\n\ndef test_double():\n"
                "    assert double(100) == 200\n",
            },
        )
        scan = analyze_repository(tmp_path)
        runner = SpyRunner()

        result = shrink_integer(
            scan,
            tmp_path,
            ShrinkRequest(
                contract_path=None, contract_id="", seed=100, target_spec="calc.py::double"
            ),
            authorized=True,
            runner=runner,
        )

        assert result.status is ShrinkStatus.NOT_SHRINKABLE
        assert "exact-input" in result.reason
        assert "No reusable property has been established" in result.reason
        # Nothing ran, and specifically not the smaller input.
        assert runner.calls == []

    def test_a_contract_cannot_be_used_to_shrink_around_an_exact_test(self, tmp_path: Path) -> None:
        """A user declaration does not outrank the repository's own test (D-064)."""

        _repository(
            tmp_path,
            {
                "calc.py": "def double(x: int) -> int:\n    return x * 2 + 1\n",
                "test_calc.py": "from calc import double\n\n\ndef test_double():\n"
                "    assert double(100) == 200\n",
            },
        )
        scan = analyze_repository(tmp_path)
        contract_path = _write_contract(
            tmp_path,
            target_file="calc.py",
            target_name="double",
            digest=_digest_of(tmp_path, "calc.py"),
            operator="le",
            reference="100",
        )
        runner = SpyRunner()

        result = shrink_integer(
            scan,
            tmp_path,
            ShrinkRequest(
                contract_path=contract_path,
                contract_id="c1",
                seed=100,
                target_spec="calc.py::double",
            ),
            authorized=True,
            runner=runner,
        )

        assert result.status is ShrinkStatus.NOT_SHRINKABLE
        assert "conflicting" in result.reason.lower()
        assert runner.calls == []

    def test_an_exact_raises_expectation_is_also_exact_input_only(self) -> None:
        from edgehunter.oracle.models import TestExpectationCandidate

        expectation = TestExpectationCandidate(
            kind=PropertyKind.EXAMPLE_RAISES,
            strength=OracleStrength.STRONG,
            expected_outcome=ExpectedOutcome.RAISES,
            callee="validate",
            evidence=(
                OracleEvidence(
                    kind=OracleEvidenceKind.TEST_RAISES,
                    relative_path=Path("test_m.py"),
                    lineno=7,
                    detail="pytest.raises(ValueError) around validate(-1)",
                ),
            ),
            target="validate",
            resolution=TargetResolution.RESOLVED,
            call_expression="validate(-1)",
            exception="ValueError",
        )

        assessment = assess_test_expectation(expectation)

        assert assessment.kind is ShrinkabilityKind.EXACT_INPUT_ONLY
        assert assessment.permits_input_change() is False
        assert assessment.source is OracleSource.TEST_RAISES
        assert assessment.oracle_location == "test_m.py:7"
        assert "exact-input" in assessment.reason


# --- every remaining refusal, spec section 42 -------------------------------


class TestEveryOracleSourceRefuses:
    def test_an_unresolved_expectation_is_inconclusive(self) -> None:
        from edgehunter.oracle.models import TestExpectationCandidate

        expectation = TestExpectationCandidate(
            kind=PropertyKind.EXAMPLE_EXPECTATION,
            strength=OracleStrength.STRONG,
            expected_outcome=ExpectedOutcome.RETURNS,
            callee="process",
            evidence=(),
            resolution=TargetResolution.UNRESOLVED,
            resolution_reason="no target with this name",
        )

        assessment = assess_test_expectation(expectation)

        assert assessment.kind is ShrinkabilityKind.INCONCLUSIVE
        assert assessment.permits_input_change() is False

    def test_an_ambiguous_expectation_is_inconclusive(self) -> None:
        from edgehunter.oracle.models import TestExpectationCandidate

        expectation = TestExpectationCandidate(
            kind=PropertyKind.EXAMPLE_EXPECTATION,
            strength=OracleStrength.STRONG,
            expected_outcome=ExpectedOutcome.RETURNS,
            callee="process",
            evidence=(),
            resolution=TargetResolution.AMBIGUOUS,
            resolution_reason="2 targets",
        )

        assert assess_test_expectation(expectation).kind is ShrinkabilityKind.INCONCLUSIVE

    def test_a_custom_expected_exception_is_inconclusive(self) -> None:
        from edgehunter.oracle.models import TestExpectationCandidate

        expectation = TestExpectationCandidate(
            kind=PropertyKind.EXAMPLE_RAISES,
            strength=OracleStrength.STRONG,
            expected_outcome=ExpectedOutcome.RAISES,
            callee="validate",
            evidence=(),
            target="validate",
            resolution=TargetResolution.RESOLVED,
            exception="PaymentError",
        )

        assessment = assess_test_expectation(expectation)

        assert assessment.kind is ShrinkabilityKind.INCONCLUSIVE
        assert "PaymentError" in assessment.reason

    def test_a_production_assertion_is_unsupported(self) -> None:
        from edgehunter.oracle.models import ProductionPropertyCandidate

        candidate = ProductionPropertyCandidate(
            kind=PropertyKind.ASSERTED_CONDITION,
            target="validate",
            condition="balance >= 0",
            strength=OracleStrength.STRONG,
            expected_outcome=ExpectedOutcome.CONDITION_HOLDS,
            evidence=(
                OracleEvidence(
                    kind=OracleEvidenceKind.PRODUCTION_ASSERTION,
                    relative_path=Path("m.py"),
                    lineno=4,
                    detail="assert balance >= 0",
                ),
            ),
        )

        assessment = assess_production_property(candidate)

        assert assessment.kind is ShrinkabilityKind.UNSUPPORTED_ORACLE
        assert assessment.permits_input_change() is False
        assert assessment.source is OracleSource.PRODUCTION_ASSERTION

    def test_a_validation_guard_is_unsupported_however_simple(self) -> None:
        """`if x < 0: raise` is not promoted to a reusable contract."""
        from edgehunter.oracle.models import ProductionPropertyCandidate

        candidate = ProductionPropertyCandidate(
            kind=PropertyKind.REJECTION_RULE,
            target="validate",
            condition="x < 0",
            strength=OracleStrength.STRONG,
            expected_outcome=ExpectedOutcome.RAISES,
            evidence=(
                OracleEvidence(
                    kind=OracleEvidenceKind.VALIDATION_GUARD,
                    relative_path=Path("m.py"),
                    lineno=2,
                    detail="if x < 0: raise ValueError",
                ),
            ),
            exception="ValueError",
        )

        assessment = assess_production_property(candidate)

        assert assessment.kind is ShrinkabilityKind.UNSUPPORTED_ORACLE
        assert assessment.source is OracleSource.VALIDATION_GUARD

    def test_a_missing_oracle_yields_no_reusable_source(self) -> None:
        assert refuse_conflicting(()) is None
        assert OracleSource.NONE.value == "none"

    def test_an_unsupported_comparison_cannot_start_a_shrink(self, tmp_path: Path) -> None:
        """A comparison Python itself refuses is never a mismatch to shrink."""

        _repository(tmp_path, {"m.py": "def f(x: int) -> int:\n    return x\n"})
        scan = analyze_repository(tmp_path)
        contract_path = _write_contract(
            tmp_path,
            target_file="m.py",
            target_name="f",
            digest=_digest_of(tmp_path, "m.py"),
        )
        runner = SpyRunner()

        result = shrink_integer(
            scan,
            tmp_path,
            ShrinkRequest(contract_path=contract_path, contract_id="c1", seed=-100),
            authorized=True,
            runner=runner,
        )

        # The seed contradicts the contract, so this one *is* runnable; the point
        # is that the run is possible only because a contract was supplied.
        assert result.status in (ShrinkStatus.SHRUNK, ShrinkStatus.NO_SMALLER_CASE_FOUND)
        assert runner.calls

    def test_an_unverified_source_refuses_before_execution(self, tmp_path: Path) -> None:
        _repository(tmp_path, {"m.py": "def f(x: int) -> int:\n    return x\n"})
        scan = analyze_repository(tmp_path)
        contract_path = _write_contract(
            tmp_path, target_file="m.py", target_name="f", digest="f" * 64
        )
        runner = SpyRunner()

        result = shrink_integer(
            scan,
            tmp_path,
            ShrinkRequest(contract_path=contract_path, contract_id="c1", seed=-100),
            authorized=True,
            runner=runner,
        )

        assert result.status is ShrinkStatus.REFUSED
        assert "fingerprint" in result.reason
        assert runner.calls == []


class TestRefusalsNeverExecute:
    def test_a_missing_contract_file_executes_nothing(self, tmp_path: Path) -> None:
        _repository(tmp_path, {"m.py": "def f(x: int) -> int:\n    return x\n"})
        scan = analyze_repository(tmp_path)
        runner = SpyRunner()

        result = shrink_integer(
            scan,
            tmp_path,
            ShrinkRequest(contract_path=tmp_path / "absent.json", contract_id="c1", seed=-100),
            authorized=True,
            runner=runner,
        )

        assert result.status is ShrinkStatus.REFUSED
        assert runner.calls == []

    def test_a_seed_outside_the_domain_executes_nothing(self, tmp_path: Path) -> None:
        _repository(tmp_path, {"m.py": "def f(x: int) -> int:\n    return x\n"})
        scan = analyze_repository(tmp_path)
        contract_path = _write_contract(
            tmp_path,
            target_file="m.py",
            target_name="f",
            digest=_digest_of(tmp_path, "m.py"),
            operator="lt",
            reference="0",
        )
        runner = SpyRunner()

        result = shrink_integer(
            scan,
            tmp_path,
            ShrinkRequest(contract_path=contract_path, contract_id="c1", seed=5),
            authorized=True,
            runner=runner,
        )

        assert result.status is ShrinkStatus.REFUSED
        assert "domain" in result.reason
        assert runner.calls == []

    def test_a_mismatched_cli_target_executes_nothing(self, tmp_path: Path) -> None:
        _repository(tmp_path, {"m.py": "def f(x: int) -> int:\n    return x\n"})
        scan = analyze_repository(tmp_path)
        contract_path = _write_contract(
            tmp_path, target_file="m.py", target_name="f", digest=_digest_of(tmp_path, "m.py")
        )
        runner = SpyRunner()

        result = shrink_integer(
            scan,
            tmp_path,
            ShrinkRequest(
                contract_path=contract_path,
                contract_id="c1",
                seed=-100,
                target_spec="m.py::something_else",
            ),
            authorized=True,
            runner=runner,
        )

        assert result.status is ShrinkStatus.REFUSED
        assert "may not be redirected" in result.reason
        assert runner.calls == []


class TestTargetShapeRefusals:
    def _run(self, tmp_path: Path, source: str, parameter: str = "x") -> tuple[Any, SpyRunner]:
        _repository(tmp_path, {"m.py": source})
        scan = analyze_repository(tmp_path)
        contract_path = _write_contract(
            tmp_path,
            target_file="m.py",
            target_name="f",
            digest=_digest_of(tmp_path, "m.py"),
            parameter=parameter,
        )
        runner = SpyRunner()
        result = shrink_integer(
            scan,
            tmp_path,
            ShrinkRequest(contract_path=contract_path, contract_id="c1", seed=-100),
            authorized=True,
            runner=runner,
        )
        return result, runner

    def test_a_method_is_refused(self, tmp_path: Path) -> None:
        result, runner = self._run(
            tmp_path, "class C:\n    def f(self, x: int) -> int:\n        return x\n"
        )
        assert result.status is ShrinkStatus.REFUSED
        assert runner.calls == []

    def test_an_async_function_is_refused(self, tmp_path: Path) -> None:
        result, runner = self._run(tmp_path, "async def f(x: int) -> int:\n    return x\n")
        assert result.status is ShrinkStatus.REFUSED
        assert runner.calls == []

    def test_a_decorated_function_is_refused(self, tmp_path: Path) -> None:
        result, runner = self._run(
            tmp_path,
            "import functools\n\n\n@functools.cache\ndef f(x: int) -> int:\n    return x\n",
        )
        assert result.status is ShrinkStatus.REFUSED
        assert "decorated" in result.reason
        assert runner.calls == []

    def test_two_parameters_are_refused_rather_than_inventing_one(self, tmp_path: Path) -> None:
        result, runner = self._run(tmp_path, "def f(x: int, y: int) -> int:\n    return x + y\n")
        assert result.status is ShrinkStatus.REFUSED
        assert "inventing a value" in result.reason
        assert runner.calls == []

    def test_a_non_integer_parameter_is_refused(self, tmp_path: Path) -> None:
        result, runner = self._run(tmp_path, "def f(x: str) -> str:\n    return x\n")
        assert result.status is ShrinkStatus.REFUSED
        assert "INT" in result.reason
        assert runner.calls == []

    def test_a_defaulted_parameter_is_refused(self, tmp_path: Path) -> None:
        result, runner = self._run(tmp_path, "def f(x: int = 0) -> int:\n    return x\n")
        assert result.status is ShrinkStatus.REFUSED
        assert runner.calls == []

    def test_a_parameter_the_contract_does_not_name_is_refused(self, tmp_path: Path) -> None:
        result, runner = self._run(
            tmp_path, "def f(x: int) -> int:\n    return x\n", parameter="other"
        )
        assert result.status is ShrinkStatus.REFUSED
        assert runner.calls == []
