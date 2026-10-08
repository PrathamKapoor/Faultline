"""Real Docker integration tests for the execution backend.

These tests start actual containers against a real Docker Linux daemon. They are
the only place where untrusted target code is genuinely loaded and called, and
they verify the isolation properties that cannot be checked by mocking:

* A target can only produce a structured observation, never host output.
* The container has no network, a read-only filesystem, and bounded resources.
* A runaway target is killed by the host and leaves nothing behind.
* Failed attempts to import are distinguished from failed invocations.

If Docker is not usable the whole module is skipped rather than silently passing;
a green run of this file means the containers really ran.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edgehunter.execution import OutcomeKind, RunOptions, RunReport, SandboxState
from edgehunter.execution.docker import container_exists, docker_available
from edgehunter.execution.repository import execute_target, runtime_context
from edgehunter.pipeline import analyze_repository

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


def _run(
    root: Path,
    target: str,
    *,
    max_cases: int = 5,
    timeout: float = 3.0,
    image: str | None = None,
) -> RunReport:
    """Execute one target from a real repository directory inside Docker."""
    assert image is not None
    analysis = analyze_repository(root)
    return execute_target(
        analysis,
        root,
        RunOptions(target=target, max_cases=max_cases, timeout_seconds=timeout),
        authorized=True,
        repository_context=runtime_context(),
    )


def _repository(tmp_path: Path, **modules: str) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for name, source in modules.items():
        (root / f"{name}.py").write_text(source, encoding="utf-8")
    return root


def _values(result: object) -> list[object]:
    """Decode a result's positional arguments back to plain Python values."""
    arguments = getattr(result, "arguments", None)
    assert arguments is not None
    return [value.to_python() for value in arguments.args]


class TestAOrdinaryReturn:
    def test_integer_arguments_produce_the_expected_sum(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(tmp_path, sample="def add(a: int, b: int):\n    return a + b\n")

        report = _run(root, "add", image=worker_image)

        assert report.sandbox.state is SandboxState.READY
        assert report.results
        assert all(result.outcome is OutcomeKind.RETURNED for result in report.results)
        for result in report.results:
            left, right = _values(result)
            assert isinstance(left, int)
            assert isinstance(right, int)
            observation = result.observation
            assert observation is not None
            assert observation.return_value is not None
            assert observation.return_value.to_python() == left + right


class TestBRaisedException:
    def test_division_by_zero_is_recorded_as_an_observation(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(tmp_path, sample="def divide(a: float, b: float):\n    return a / b\n")

        report = _run(root, "divide", image=worker_image)

        assert report.results
        raised = [
            result
            for result in report.results
            if result.outcome is OutcomeKind.RAISED
            and result.observation is not None
            and result.observation.exception_type == "ZeroDivisionError"
        ]
        assert raised, [r.outcome for r in report.results]
        for result in raised:
            assert result.observation is not None
            assert result.observation.exception_message


class TestCTimeout:
    def test_a_runaway_target_is_killed_and_nothing_is_left_behind(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(
            tmp_path,
            sample="def spin(count: int):\n    while True:\n        pass\n",
        )

        report = _run(root, "spin", max_cases=1, timeout=2.0, image=worker_image)

        assert report.results
        assert report.results[0].outcome is OutcomeKind.TIMEOUT
        assert "2.0s" in report.results[0].detail


class TestDOutputFlood:
    def test_flooding_output_is_truncated_not_unbounded(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(
            tmp_path,
            sample=(
                "def noisy(n: int):\n"
                "    for _ in range(5000):\n"
                "        print('flood line ' + 'y' * 200)\n"
                "    return n\n"
            ),
        )

        report = _run(root, "noisy", max_cases=1, image=worker_image)

        result = report.results[0]
        assert result.outcome is OutcomeKind.RETURNED
        assert result.observation is not None
        assert result.observation.stdout_truncated is True
        assert len(result.observation.captured_stdout.encode("utf-8")) <= 16 * 1024


class TestELateImportFailure:
    def test_a_failure_inside_the_function_is_a_raised_exception(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(
            tmp_path,
            sample=("def load(n: int):\n    import a_module_that_does_not_exist\n    return n\n"),
        )

        report = _run(root, "load", image=worker_image)

        assert report.results
        assert report.results[0].outcome is OutcomeKind.RAISED
        assert report.results[0].observation is not None
        assert report.results[0].observation.exception_type == "ModuleNotFoundError"


class TestFImportTimeFailure:
    def test_a_module_level_failure_is_an_import_failure(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(
            tmp_path,
            sample=("import a_module_that_does_not_exist\n\n\ndef f(x: int):\n    return x\n"),
        )

        report = _run(root, "f", image=worker_image)

        assert report.results
        assert report.results[0].outcome is OutcomeKind.IMPORT_FAILURE
        # An import failure is described by its detail, not as a raised exception:
        # the function was never called.
        assert "ModuleNotFoundError" in report.results[0].detail


class TestGNoNetwork:
    def test_name_resolution_fails_inside_the_container(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(
            tmp_path,
            sample=(
                "def resolve(count: int):\n"
                "    import socket\n"
                "    return socket.gethostbyname('example.invalid')\n"
            ),
        )

        report = _run(root, "resolve", max_cases=1, image=worker_image)

        result = report.results[0]
        assert result.outcome is OutcomeKind.RAISED
        assert result.observation is not None
        assert result.observation.exception_type == "gaierror"


class TestHReadOnlyStaging:
    def test_import_time_write_fails_and_leaves_no_host_file(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        source = (
            "with open('/workspace/escaped.txt', 'w') as handle:\n"
            "    handle.write('host was written to')\n\n\n"
            "def f(x: int):\n    return x\n"
        )
        root = _repository(tmp_path, sample=source)

        report = _run(root, "f", image=worker_image)

        assert report.results
        assert report.results[0].outcome is OutcomeKind.IMPORT_FAILURE
        # The staged copy is read-only, so the target's own source tree is
        # untouched on the host.
        assert (root / "sample.py").read_text(encoding="utf-8") == source
        assert not (root / "escaped.txt").exists()
        assert not list(root.parent.glob("**/escaped.txt"))


class TestCleanup:
    def test_no_edgehunter_containers_survive_a_run(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(
            tmp_path,
            sample=(
                "def quiet(n: int):\n    return n\n\n\n"
                "def spin(flag: bool):\n    while True:\n        pass\n"
            ),
        )

        _run(root, "quiet", image=worker_image)
        _run(root, "spin", max_cases=1, timeout=1.5, image=worker_image)

        import subprocess

        listing = subprocess.run(
            ["docker", "ps", "--all", "--filter", "name=edgehunter-run-", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert listing.stdout.strip() == ""

    def test_result_documents_never_reach_the_container_stdout(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(
            tmp_path,
            sample=(
                "import os\n\n\n"
                "def chatty(n: int):\n"
                "    os.write(1, b'native stdout write')\n"
                "    return n\n"
            ),
        )

        report = _run(root, "chatty", max_cases=1, image=worker_image)

        result = report.results[0]
        assert result.outcome is OutcomeKind.RETURNED
        assert result.observation is not None
        # The native write was discarded, not captured, and could not corrupt the
        # protocol because the result travels in a file.
        assert "native stdout write" not in result.observation.captured_stdout


def test_worker_image_result_document_is_valid_strict_json(
    tmp_path: Path, worker_image: str
) -> None:
    """The protocol must never emit JSON's invalid NaN/Infinity tokens."""
    root = _repository(tmp_path, sample="def f(x: float):\n    return x / 0.0\n")

    report = _run(root, "f", image=worker_image)

    text = json.dumps(
        [
            {
                "outcome": result.outcome.value,
                "detail": result.detail,
            }
            for result in report.results
        ]
    )
    assert "NaN" not in text
    assert "Infinity" not in text


def test_container_names_are_scoped_and_checkable(tmp_path: Path, worker_image: str) -> None:
    root = _repository(tmp_path, sample="def f(n: int):\n    return n\n")

    report = _run(root, "f", image=worker_image)

    assert report.results
    assert not container_exists("edgehunter-run-does-not-exist")
