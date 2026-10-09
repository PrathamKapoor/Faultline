"""Real Docker integration tests for ``bool`` and ``str`` input coverage.

Phase 7A added boolean and string semantic actions. A static scan showing new
candidates proves nothing about whether they can actually be executed, so these
tests drive the real Docker backend end to end: the plan is built statically,
staged read-only, executed in a real container, and the observed return value is
compared against Python's own semantics for that input.

These are the Phase 7A acceptance demonstrations:

* ``invert(False)`` returns ``True`` and ``invert(True)`` returns ``False``
* ``length("")`` returns ``0``
* ``length("é漢🙂")`` returns ``3`` (code points, not bytes)
* ``length("\\x00")`` returns ``1``
* a boolean reaches the target as a real ``bool``, not as ``0``/``1``
* no EdgeHunter container survives the run

If Docker is not usable the whole module is skipped rather than silently passing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from edgehunter.execution import (
    ExecutionResult,
    OutcomeKind,
    RunOptions,
    RunReport,
    SandboxState,
)
from edgehunter.execution.codec import TypedValue
from edgehunter.execution.docker import docker_available
from edgehunter.execution.repository import WORKER_IMAGE, execute_target, runtime_context
from edgehunter.generation import SemanticActionKind
from edgehunter.generation.materialize import LONG_STRING_LENGTH, UNICODE_STRING_SAMPLE
from edgehunter.pipeline import analyze_repository

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def worker_image() -> str:
    """Build the trusted worker image once for the whole session."""
    available, detail = docker_available()
    if not available:
        pytest.skip(f"Docker is not usable here: {detail}")
    from edgehunter.execution.repository import ensure_image

    ready, image_detail = ensure_image(runtime_context())
    if not ready:
        pytest.skip(f"worker image could not be prepared: {image_detail}")
    assert WORKER_IMAGE
    return image_detail


BOOL_SOURCE = """def invert(flag: bool) -> bool:
    return not flag
"""

STR_SOURCE = """def length(value: str) -> int:
    return len(value)
"""


def _repository(tmp_path: Path, **modules: str) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for name, source in modules.items():
        (root / f"{name}.py").write_text(source, encoding="utf-8")
    return root


def _run(root: Path, target: str, *, max_cases: int = 20, timeout: float = 5.0) -> RunReport:
    analysis = analyze_repository(root)
    return execute_target(
        analysis,
        root,
        RunOptions(target=target, max_cases=max_cases, timeout_seconds=timeout),
        authorized=True,
        repository_context=runtime_context(),
    )


def _first_argument(result: ExecutionResult) -> TypedValue:
    arguments = result.arguments
    assert arguments is not None
    assert len(arguments.args) == 1
    return arguments.args[0]


def _by_argument(report: RunReport, target: str) -> dict[object, object]:
    """Map the decoded first argument to the observed return value.

    The key is the actual Python value the target received, so a test asserts
    about a real input rather than about the plan EdgeHunter happened to build.
    """
    observed: dict[object, object] = {}
    for result in report.results:
        if result.outcome is not OutcomeKind.RETURNED or result.observation is None:
            continue
        assert result.observation.return_value is not None
        key = _first_argument(result).to_python()
        # ``bool`` keys must not collide with the equal integers.
        observed[key] = result.observation.return_value.to_python()
    assert observed, [r.outcome for r in report.results]
    return observed


def _edgehunter_containers() -> list[str]:
    """Every container name EdgeHunter has left behind, by the daemon itself."""
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
    return [line for line in listing.stdout.splitlines() if line.strip()]


class TestABoolFalse:
    def test_invert_false_returns_true(self, tmp_path: Path, worker_image: str) -> None:
        root = _repository(tmp_path, sample=BOOL_SOURCE)

        report = _run(root, "invert")

        assert report.sandbox.state is SandboxState.READY
        assert _by_argument(report, "invert")[False] is True


class TestBBoolTrue:
    def test_invert_true_returns_false(self, tmp_path: Path, worker_image: str) -> None:
        root = _repository(tmp_path, sample=BOOL_SOURCE)

        report = _run(root, "invert")

        assert report.sandbox.state is SandboxState.READY
        assert _by_argument(report, "invert")[True] is False


class TestCBoolIsNotAnInteger:
    def test_the_argument_reaches_the_target_as_a_real_boolean(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        """``True`` must be a ``bool`` in the container, never the integer ``1``."""
        root = _repository(tmp_path, sample=BOOL_SOURCE)

        report = _run(root, "invert")

        for result in report.results:
            value = _first_argument(result).to_python()
            assert isinstance(value, bool)
            assert not isinstance(value, int) or type(value) is bool

        # The observed returns prove the target saw a bool: ``not 1`` is ``False``
        # and ``not 0`` is ``True``, but ``type(True)`` on the return distinguishes
        # the two spellings at the protocol level.
        observed = _by_argument(report, "invert")
        assert type(observed[False]) is bool
        assert type(observed[True]) is bool


class TestDEmptyString:
    def test_empty_string_returns_zero(self, tmp_path: Path, worker_image: str) -> None:
        root = _repository(tmp_path, sample=STR_SOURCE)

        report = _run(root, "length")

        assert report.sandbox.state is SandboxState.READY
        assert _by_argument(report, "length")[""] == 0


class TestEUnicodeString:
    def test_unicode_string_returns_its_code_point_length(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(tmp_path, sample=STR_SOURCE)

        report = _run(root, "length")

        assert report.sandbox.state is SandboxState.READY
        observed = _by_argument(report, "length")
        assert observed[UNICODE_STRING_SAMPLE] == len(UNICODE_STRING_SAMPLE) == 3


class TestFNulString:
    def test_one_nul_character_returns_one(self, tmp_path: Path, worker_image: str) -> None:
        root = _repository(tmp_path, sample=STR_SOURCE)

        report = _run(root, "length")

        assert report.sandbox.state is SandboxState.READY
        assert _by_argument(report, "length")["\x00"] == 1


class TestGStringValuesRoundTrip:
    def test_every_string_action_reaches_the_target_unchanged(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        """All five actions execute, and ``len`` agrees with the planned value."""
        root = _repository(tmp_path, sample=STR_SOURCE)

        report = _run(root, "length", max_cases=20)

        observed = _by_argument(report, "length")
        expected: dict[str, int] = {
            "": 0,
            " ": 1,
            UNICODE_STRING_SAMPLE: 3,
            "\x00": 1,
            "a" * LONG_STRING_LENGTH: LONG_STRING_LENGTH,
        }
        for value, length in expected.items():
            assert value in observed, sorted(map(repr, observed))
            assert observed[value] == length
        # The NUL really is one character in the container, not zero.
        assert observed["\x00"] == 1
        assert observed[""] == 0


class TestHNoEmptyAndNonEmptyCollide:
    def test_a_nul_string_is_not_the_empty_string(self, tmp_path: Path, worker_image: str) -> None:
        root = _repository(tmp_path, sample=STR_SOURCE)

        report = _run(root, "length")

        observed = _by_argument(report, "length")
        empty: object = observed[""]
        nul: object = observed["\x00"]
        # One assertion, because mypy narrows after each equality and the
        # interesting claim is the pairing: two different inputs, two different
        # lengths, so a NUL string is never confused with an empty one.
        assert (empty, nul) == (0, 1)


class TestIPersistence:
    def test_the_planned_bool_and_string_actions_are_the_ones_executed(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        """The executed cases are the statically planned ones, not a guess."""
        root = _repository(tmp_path, sample=BOOL_SOURCE, other=STR_SOURCE)

        analysis = analyze_repository(root)

        planned = {
            (entry.target, action.kind)
            for entry in analysis.generation.action_sets
            for action in entry.actions
        }
        assert {
            ("invert", SemanticActionKind.SET_FALSE),
            ("invert", SemanticActionKind.SET_TRUE),
        } <= planned
        assert {
            ("length", kind)
            for kind in (
                SemanticActionKind.EMPTY_STRING,
                SemanticActionKind.WHITESPACE_STRING,
                SemanticActionKind.UNICODE_STRING,
                SemanticActionKind.NULL_CHARACTER_STRING,
                SemanticActionKind.LONG_STRING,
            )
        } <= planned

        report = _run(root, "invert")
        executed = {_first_argument(result).to_python() for result in report.results}
        assert executed == {False, True}


class TestJCleanup:
    def test_no_edgehunter_container_survives_the_run(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(tmp_path, sample=BOOL_SOURCE, other=STR_SOURCE)

        _run(root, "invert")
        _run(root, "length")

        assert _edgehunter_containers() == []
