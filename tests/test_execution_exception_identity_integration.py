"""Real Docker integration tests for observed exception identity.

Phase 7A makes the worker report module, qualified name, and a class's real
``builtins`` lineage. None of that can be verified with a fabricated result
document: the whole point is that these facts come from the sandbox's own class
objects. These tests therefore raise real exceptions inside a real container and
assert on what the host actually decoded.

The demonstrations:

* a real builtin ``ValueError`` is reported as builtin, with ``builtins`` lineage
* a custom class *named* ``ValueError`` is **not** reported as the builtin
* a genuine subclass of the builtin reports the builtin in its lineage
* nothing is left running afterwards
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
from edgehunter.execution.docker import docker_available
from edgehunter.execution.repository import WORKER_IMAGE, execute_target, runtime_context
from edgehunter.pipeline import analyze_repository

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def worker_image() -> str:
    available, detail = docker_available()
    if not available:
        pytest.skip(f"Docker is not usable here: {detail}")
    from edgehunter.execution.repository import ensure_image

    ready, image_detail = ensure_image(runtime_context())
    if not ready:
        pytest.skip(f"worker image could not be prepared: {image_detail}")
    assert WORKER_IMAGE
    return image_detail


IDENTITY_SOURCE = '''import builtins

# Captured before the shadow below, so this really is the standard library class.
_RealValueError = builtins.ValueError


class ValueError(Exception):  # noqa: A001 - the shadowing is the point
    """A custom class that deliberately shares a builtin name."""


class MyValueError(_RealValueError):
    """A genuine subclass of the real builtin ValueError."""


class Unrelated(Exception):
    pass


def builtin_error(n: int):
    raise _RealValueError("real builtin")


def shadowed_error(n: int):
    raise ValueError("custom class with a builtin name")


def subclass_error(n: int):
    raise MyValueError("genuine subclass")


def unrelated_error(n: int):
    raise Unrelated("not a ValueError at all")
'''


def _repository(tmp_path: Path, source: str) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "identity.py").write_text(source, encoding="utf-8")
    return root


def _run(root: Path, target: str, *, max_cases: int = 3) -> RunReport:
    analysis = analyze_repository(root)
    return execute_target(
        analysis,
        root,
        RunOptions(target=target, max_cases=max_cases, timeout_seconds=5.0),
        authorized=True,
        repository_context=runtime_context(),
    )


def _raised(report: RunReport) -> list[ExecutionResult]:
    raised = [r for r in report.results if r.outcome is OutcomeKind.RAISED and r.observation]
    assert raised, [r.outcome for r in report.results]
    return raised


class TestGBuiltinException:
    def test_a_real_builtin_value_error_is_reported_as_builtin(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(tmp_path, IDENTITY_SOURCE)

        report = _run(root, "builtin_error")

        assert report.sandbox.state is SandboxState.READY
        for result in _raised(report):
            observation = result.observation
            assert observation is not None
            assert observation.exception_type == "ValueError"
            assert observation.exception_module == "builtins"
            assert observation.exception_qualname == "ValueError"
            assert observation.exception_builtin is True
            lineage = observation.exception_builtin_lineage
            assert lineage is not None
            assert "ValueError" in lineage
            assert observation.exception_identity_text == "ValueError"


class TestHShadowedBuiltin:
    def test_a_custom_class_named_value_error_is_not_the_builtin(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(tmp_path, IDENTITY_SOURCE)

        report = _run(root, "shadowed_error")

        for result in _raised(report):
            observation = result.observation
            assert observation is not None
            # Same bare name, and the result must not be mistakable for the builtin.
            assert observation.exception_type == "ValueError"
            assert observation.exception_builtin is False
            assert observation.exception_module == "identity"
            lineage = observation.exception_builtin_lineage
            assert lineage is not None
            assert "ValueError" not in lineage
            assert "Exception" in lineage

    def test_the_two_same_named_classes_remain_distinguishable(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(tmp_path, IDENTITY_SOURCE)

        builtin = _raised(_run(root, "builtin_error"))[0].observation
        shadowed = _raised(_run(root, "shadowed_error"))[0].observation
        assert builtin is not None and shadowed is not None

        # Identical bare names, different qualified identities.
        assert builtin.exception_type == shadowed.exception_type
        assert builtin.exception_identity_text != shadowed.exception_identity_text
        assert builtin.exception_builtin is True
        assert shadowed.exception_builtin is False


class TestIBuiltinSubclass:
    def test_a_genuine_subclass_reports_the_builtin_lineage(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(tmp_path, IDENTITY_SOURCE)

        report = _run(root, "subclass_error")

        for result in _raised(report):
            observation = result.observation
            assert observation is not None
            assert observation.exception_type == "MyValueError"
            assert observation.exception_builtin is False
            lineage = observation.exception_builtin_lineage
            assert lineage is not None
            # Truthfully reported: it does derive from the real builtin.
            assert "ValueError" in lineage
            assert "MyValueError" not in lineage

    def test_an_unrelated_exception_reports_no_value_error_lineage(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(tmp_path, IDENTITY_SOURCE)

        report = _run(root, "unrelated_error")

        for result in _raised(report):
            observation = result.observation
            assert observation is not None
            assert observation.exception_builtin is False
            lineage = observation.exception_builtin_lineage
            assert lineage is not None
            assert "ValueError" not in lineage


class TestJIdentityBounded:
    def test_a_hostile_module_and_class_name_are_clipped(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        long_name = "N" * 5_000
        long_module = "M" * 5_000
        root = _repository(
            tmp_path,
            "def wide(n: int):\n"
            f"    error = type({long_name!r}, (Exception,), {{'__module__': {long_module!r}}})\n"
            "    raise error('boom')\n",
        )

        report = _run(root, "wide", max_cases=1)

        for result in _raised(report):
            observation = result.observation
            assert observation is not None
            assert observation.exception_module is not None
            assert len(observation.exception_module) <= 200
            assert observation.exception_qualname is not None
            assert len(observation.exception_qualname) <= 200

    def test_the_exception_message_remains_bounded(self, tmp_path: Path, worker_image: str) -> None:
        root = _repository(
            tmp_path,
            "def loud(n: int):\n    raise ValueError('z' * 20000)\n",
        )

        report = _run(root, "loud", max_cases=1)

        for result in _raised(report):
            observation = result.observation
            assert observation is not None
            assert observation.exception_message is not None
            assert len(observation.exception_message) <= 4096


class TestKCleanup:
    def test_no_edgehunter_container_survives_the_run(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        root = _repository(tmp_path, IDENTITY_SOURCE)

        for target in ("builtin_error", "shadowed_error", "subclass_error"):
            _run(root, target, max_cases=1)

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
        assert listing.stdout.strip() == ""
