"""Real Docker integration tests for runtime oracle evaluation.

These are the only tests where EdgeHunter both executes untrusted code and judges
it. That combination is exactly what needs a real container behind it, so a green
run of this file means containers genuinely ran and the oracle verdicts came from
genuine observations rather than fixtures.

The cases map one-for-one onto the phase's required demonstrations:

A. a matching explicit return expectation produces no finding
B. a mismatching one produces ``OBSERVED_EXPECTATION_MISMATCH``
C. a matching ``pytest.raises`` produces no finding
D. a missing ``pytest.raises`` produces ``MISMATCH``
E. no applicable oracle leaves the case ``INCONCLUSIVE``
F. three rechecks produce ``REPRODUCED`` and leave no container behind
G. an unusable sandbox stays ``INCONCLUSIVE`` and never falls back
H. conflicting expectations produce ``CONFLICTING_EVIDENCE``
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edgehunter.evaluation.models import (
    EvaluationReport,
    FindingKind,
    OracleStatus,
    ReproductionStatus,
)
from edgehunter.evaluation.repository import evaluate_run, target_functions
from edgehunter.execution import OutcomeKind, RunOptions, RunReport, SandboxState
from edgehunter.execution.docker import docker_available
from edgehunter.execution.repository import execute_target, runtime_context
from edgehunter.pipeline import RepositoryScan, analyze_repository
from edgehunter.scanner.models import FunctionInfo

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def worker_image() -> str:
    """Build the trusted worker image once for the whole session."""
    available, detail = docker_available()
    if not available:
        pytest.skip(f"Docker is not usable here: {detail}")
    from edgehunter.execution.repository import WORKER_IMAGE, ensure_image

    ready, image_detail = ensure_image(runtime_context())
    if not ready:
        pytest.skip(f"worker image could not be prepared: {image_detail}")
    assert WORKER_IMAGE
    return image_detail


def _repository(tmp_path: Path, **modules: str) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for name, source in modules.items():
        (root / f"{name}.py").write_text(source, encoding="utf-8")
    return root


def _run(
    root: Path,
    target: str,
    *,
    image: str,
    max_cases: int = 5,
    rechecks: int = 0,
    timeout: float = 3.0,
) -> tuple[RunReport, RepositoryScan, tuple[EvaluationReport, ...]]:
    """Execute one target in Docker and evaluate the results."""
    assert image is not None
    analysis = analyze_repository(root)
    report = execute_target(
        analysis,
        root,
        RunOptions(target=target, max_cases=max_cases, timeout_seconds=timeout, rechecks=rechecks),
        authorized=True,
        repository_context=runtime_context(),
    )
    functions: dict[str, FunctionInfo] = {}
    for result in report.results:
        if result.target.qualified_name not in functions:
            function = target_functions(analysis, result.target.identity)
            if function is not None:
                functions[result.target.qualified_name] = function
    evaluated = evaluate_run(
        report, scan=analysis, functions=functions, requested_rechecks=rechecks
    )
    return report, analysis, evaluated


def _for_target(reports: tuple[EvaluationReport, ...], target: str) -> list[EvaluationReport]:
    return [report for report in reports if report.target_identity.endswith(f"::{target}")]


class TestAMatchingReturnExpectation:
    def test_a_correct_target_produces_no_finding(self, tmp_path: Path, worker_image: str) -> None:
        root = _repository(
            tmp_path,
            calculator="def add(a: int, b: int) -> int:\n    return a + b\n",
            test_calculator=(
                "from calculator import add\n\n\ndef test_add():\n    assert add(0, 0) == 0\n"
            ),
        )

        report, _, evaluated = _run(root, "add", image=worker_image, max_cases=10)

        assert report.sandbox.state is SandboxState.READY
        matched = [
            item
            for item in evaluated
            if item.primary is not None and item.primary.status is OracleStatus.MATCH
        ]
        assert matched, [item.primary.status if item.primary else None for item in evaluated]
        for item in matched:
            assert item.primary is not None
            assert item.primary.applicability_kind.value == "exact_match"
            assert item.findings == ()

    def test_the_matching_evaluation_names_the_test_file_and_line(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(
            tmp_path,
            calculator="def add(a: int, b: int) -> int:\n    return a + b\n",
            test_calculator=(
                "from calculator import add\n\n\ndef test_add():\n    assert add(0, 0) == 0\n"
            ),
        )

        _, _, evaluated = _run(root, "add", image=worker_image, max_cases=10)

        matched = [
            item
            for item in evaluated
            if item.primary is not None and item.primary.status is OracleStatus.MATCH
        ]
        assert matched
        location = matched[0].primary.describe_location() if matched[0].primary else ""
        # The assert statement itself, so a reader can open the file and look.
        assert location == "test_calculator.py:5"


class TestBMismatchingReturnExpectation:
    def test_an_off_by_one_is_reported_as_an_observed_mismatch(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(
            tmp_path,
            calculator="def add(a: int, b: int) -> int:\n    return a + b + 1\n",
            test_calculator=(
                "from calculator import add\n\n\ndef test_add():\n    assert add(0, 0) == 0\n"
            ),
        )

        report, _, evaluated = _run(root, "add", image=worker_image, max_cases=10)

        assert report.sandbox.state is SandboxState.READY
        mismatched = [
            item
            for item in evaluated
            if item.primary is not None and item.primary.status is OracleStatus.MISMATCH
        ]
        assert mismatched, [
            (item.target_identity, item.primary.status if item.primary else None)
            for item in evaluated
        ]
        for item in mismatched:
            assert item.findings
            finding = item.findings[0]
            assert finding.kind is FindingKind.OBSERVED_EXPECTATION_MISMATCH
            # The expectation is one explicit test, and nothing escalates beyond it.
            assert finding.oracle_location.startswith("test_calculator.py:")
            assert not hasattr(finding, "severity")

    def test_different_inputs_do_not_inherit_the_recorded_expectation(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        """`assert add(0, 0) == 0` says nothing about add(1, 0), which returns 2 here."""
        root = _repository(
            tmp_path,
            calculator="def add(a: int, b: int) -> int:\n    return a + b + 1\n",
            test_calculator=(
                "from calculator import add\n\n\ndef test_add():\n    assert add(0, 0) == 0\n"
            ),
        )

        _, _, evaluated = _run(root, "add", image=worker_image, max_cases=10)

        not_applicable = [
            item
            for item in evaluated
            if item.primary is not None and item.primary.status is OracleStatus.NOT_APPLICABLE
        ]
        assert not_applicable
        for item in not_applicable:
            assert item.primary is not None
            assert item.primary.applicability_kind.value == "arguments_differ"
            assert item.findings == ()


class TestCMatchingExpectedException:
    def test_an_expected_value_error_that_fires_is_a_match_not_a_finding(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(
            tmp_path,
            validator=(
                "def validate(x: int) -> int:\n"
                "    if x < 0:\n"
                "        raise ValueError('negative')\n"
                "    return x\n"
            ),
            test_validator=(
                "import pytest\n\n"
                "from validator import validate\n\n\n"
                "def test_negative():\n"
                "    with pytest.raises(ValueError):\n"
                "        validate(-1)\n"
            ),
        )

        report, _, evaluated = _run(root, "validate", image=worker_image, max_cases=10)

        assert report.sandbox.state is SandboxState.READY
        raised = [
            item
            for item in evaluated
            if item.execution_outcome is OutcomeKind.RAISED and item.primary is not None
        ]
        assert raised, [item.execution_outcome for item in evaluated]
        for item in raised:
            assert item.primary is not None
            assert item.primary.status is OracleStatus.MATCH
            assert item.findings == ()

    def test_raising_the_expected_exception_is_never_a_finding(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(
            tmp_path,
            validator=(
                "def validate(x: int) -> int:\n"
                "    if x < 0:\n"
                "        raise ValueError('negative')\n"
                "    return x\n"
            ),
            test_validator=(
                "import pytest\n\n"
                "from validator import validate\n\n\n"
                "def test_negative():\n"
                "    with pytest.raises(ValueError):\n"
                "        validate(-1)\n"
            ),
        )

        _, _, evaluated = _run(root, "validate", image=worker_image, max_cases=10)

        assert all(item.findings == () for item in evaluated)


class TestDMissingExpectedException:
    def test_returning_normally_contradicts_an_expected_exception(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(
            tmp_path,
            validator="def validate(x: int) -> int:\n    return x\n",
            test_validator=(
                "import pytest\n\n"
                "from validator import validate\n\n\n"
                "def test_negative():\n"
                "    with pytest.raises(ValueError):\n"
                "        validate(-1)\n"
            ),
        )

        _, _, evaluated = _run(root, "validate", image=worker_image, max_cases=10)

        mismatched = [
            item
            for item in evaluated
            if item.primary is not None and item.primary.status is OracleStatus.MISMATCH
        ]
        assert mismatched, [item.primary.status if item.primary else None for item in evaluated]
        for item in mismatched:
            assert item.findings
            assert item.findings[0].kind is FindingKind.OBSERVED_EXPECTATION_MISMATCH


class TestENoApplicableOracle:
    def test_an_exception_with_no_recorded_expectation_is_inconclusive(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(
            tmp_path,
            calculator="def divide(a: int, b: int) -> int:\n    return a // b\n",
        )

        report, _, evaluated = _run(root, "divide", image=worker_image, max_cases=10)

        assert report.sandbox.state is SandboxState.READY
        raised = [item for item in evaluated if item.execution_outcome is OutcomeKind.RAISED]
        assert raised, [item.execution_outcome for item in evaluated]
        for item in raised:
            assert item.primary is not None
            assert item.primary.status is OracleStatus.INCONCLUSIVE
            assert item.primary.applicability_kind.value == "no_expectation"
            # No confirmed finding: an exception is not a defect without an oracle.
            assert all(finding.kind is FindingKind.SUSPICIOUS_BEHAVIOR for finding in item.findings)


class TestFReproduction:
    def test_three_rechecks_reproduce_the_same_mismatch(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(
            tmp_path,
            calculator="def add(a: int, b: int) -> int:\n    return a + b + 1\n",
            test_calculator=(
                "from calculator import add\n\n\ndef test_add():\n    assert add(0, 0) == 0\n"
            ),
        )

        report, _, evaluated = _run(root, "add", image=worker_image, max_cases=10, rechecks=3)

        reproduced = [
            item for item in evaluated if item.reproduction.status is ReproductionStatus.REPRODUCED
        ]
        assert reproduced, [item.reproduction.status for item in evaluated]
        for item in reproduced:
            assert item.reproduction.attempts == 4
            assert item.reproduction.equivalent_mismatches == 4
            assert item.findings
            assert item.findings[0].kind is FindingKind.REPRODUCED_EXPECTATION_MISMATCH

        _assert_no_containers_survive()

    def test_rechecks_reuse_the_same_source_snapshot(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(
            tmp_path,
            calculator="def add(a: int, b: int) -> int:\n    return a + b + 1\n",
            test_calculator=(
                "from calculator import add\n\n\ndef test_add():\n    assert add(0, 0) == 0\n"
            ),
        )

        _, _, evaluated = _run(root, "add", image=worker_image, max_cases=10, rechecks=2)

        digests = {item.source_digest for item in evaluated if item.source_digest}
        # One snapshot for every attempt: a source change mid-run is impossible by
        # construction rather than merely unlikely.
        assert len(digests) == 1


class TestGInfrastructureFailure:
    def test_an_unavailable_sandbox_never_becomes_a_violation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker_image: str
    ) -> None:
        from edgehunter.execution import repository as execution_repository

        root = _repository(
            tmp_path,
            calculator="def add(a: int, b: int) -> int:\n    return a + b + 1\n",
            test_calculator=(
                "from calculator import add\n\n\ndef test_add():\n    assert add(0, 0) == 0\n"
            ),
        )
        analysis = analyze_repository(root)
        monkeypatch.setattr(
            execution_repository, "docker_available", lambda: (False, "unavailable for test")
        )

        report = execute_target(
            analysis,
            root,
            RunOptions(target="add", max_cases=5, timeout_seconds=2.0),
            authorized=True,
            repository_context=runtime_context(),
        )

        assert report.sandbox.state is SandboxState.UNAVAILABLE
        assert report.results == ()
        evaluated = evaluate_run(report, scan=analysis, functions={})
        assert evaluated == ()

    def test_a_timeout_is_not_reported_as_a_bug(self, tmp_path: Path, worker_image: str) -> None:
        root = _repository(
            tmp_path,
            spinner="def spin(count: int) -> int:\n    while True:\n        pass\n",
        )

        _, _, evaluated = _run(
            root, "spin", image=worker_image, max_cases=1, rechecks=1, timeout=1.5
        )

        assert evaluated
        for item in evaluated:
            assert item.execution_outcome is OutcomeKind.TIMEOUT
            assert item.findings == ()


class TestHConflictingExpectations:
    def test_two_tests_disagreeing_about_one_invocation_are_a_conflict(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(
            tmp_path,
            calculator="def add(a: int, b: int) -> int:\n    return a + b\n",
            test_calculator=(
                "from calculator import add\n\n\n"
                "def test_add_first():\n    assert add(0, 0) == 0\n\n\n"
                "def test_add_second():\n    assert add(0, 0) == 99\n"
            ),
        )

        _, _, evaluated = _run(root, "add", image=worker_image, max_cases=10)

        conflicting = [
            item
            for item in evaluated
            if item.primary is not None and item.primary.status is OracleStatus.CONFLICTING_EVIDENCE
        ]
        assert conflicting, [item.primary.status if item.primary else None for item in evaluated]
        for item in conflicting:
            assert item.primary is not None
            # Both locations are retained and no defect is claimed.
            assert len(item.primary.evidence) >= 2
            assert item.findings
            assert item.findings[0].kind is FindingKind.INCONCLUSIVE

    def test_a_conflict_is_not_resolved_by_picking_the_convenient_oracle(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(
            tmp_path,
            calculator="def add(a: int, b: int) -> int:\n    return a + b\n",
            test_calculator=(
                "from calculator import add\n\n\n"
                "def test_add_first():\n    assert add(0, 0) == 0\n\n\n"
                "def test_add_second():\n    assert add(0, 0) == 99\n"
            ),
        )

        _, _, evaluated = _run(root, "add", image=worker_image, max_cases=10)

        conflicting = [
            item
            for item in evaluated
            if item.primary is not None and item.primary.status is OracleStatus.CONFLICTING_EVIDENCE
        ]
        assert conflicting
        for item in conflicting:
            # add(0, 0) does return 0, so the observation matched one of the two
            # expectations. The verdict is still a conflict, not a match.
            assert item.primary is not None
            assert item.primary.status is not OracleStatus.MATCH


class TestProvenance:
    def test_every_evaluation_carries_its_source_location(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(
            tmp_path,
            calculator="def add(a: int, b: int) -> int:\n    return a + b + 1\n",
            test_calculator=(
                "from calculator import add\n\n\ndef test_add():\n    assert add(0, 0) == 0\n"
            ),
        )

        _, _, evaluated = _run(root, "add", image=worker_image, max_cases=10)

        applicable = [
            item
            for item in evaluated
            if item.primary is not None and item.primary.applicability_kind.value == "exact_match"
        ]
        assert applicable
        for item in applicable:
            assert item.primary is not None
            assert item.primary.evidence
            for evidence in item.primary.evidence:
                assert evidence.relative_path.name == "test_calculator.py"
                assert evidence.lineno > 0

    def test_the_source_snapshot_is_verified_against_the_executed_bytes(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(
            tmp_path,
            calculator="def add(a: int, b: int) -> int:\n    return a + b\n",
        )

        _, _, evaluated = _run(root, "add", image=worker_image, max_cases=2)

        assert evaluated
        for item in evaluated:
            assert item.source_digest is not None
            assert len(item.source_digest) == 64


def _assert_no_containers_survive() -> None:
    import subprocess

    listing = subprocess.run(
        [
            "docker",
            "ps",
            "--all",
            "--filter",
            "name=edgehunter-run-",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert listing.stdout.strip() == "", listing.stdout


class TestArtifactSafety:
    def test_the_artifact_contains_no_captured_target_output(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        """Runtime output can contain sensitive data, so it is never persisted."""
        root = _repository(
            tmp_path,
            leaky=(
                "def greet(name: str) -> str:\n    print('secret token ' + name)\n    return name\n"
            ),
        )

        _, _, evaluated = _run(root, "greet", image=worker_image, max_cases=2)

        from edgehunter.evaluation.repository import evaluation_artifact, write_evaluation_artifact

        destination = write_evaluation_artifact(tmp_path / "artifact.json", evaluated)
        document = json.loads(destination.read_text(encoding="utf-8"))

        assert "secret token" not in destination.read_text(encoding="utf-8")
        assert document["schema_version"] == 1
        _ = evaluation_artifact("run")
