"""CLI behaviour for the explicit ``run`` command.

Execution must never be implicit: every test here checks that nothing is executed
unless both a target and ``--allow-execution`` are supplied, and that a refusal
path exits with a distinct, documented code rather than a generic failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from edgehunter.cli import EXIT_EXECUTION_REFUSED, EXIT_INVALID_INPUT, app

runner = CliRunner()


def _repository(tmp_path: Path, **modules: str) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    for name, source in modules.items():
        (root / f"{name}.py").write_text(source, encoding="utf-8")
    return root


def test_run_without_opt_in_refuses(tmp_path: Path) -> None:
    root = _repository(tmp_path, sample="def add(a: int):\n    return a\n")

    result = runner.invoke(app, ["run", str(root), "--target", "add"])

    assert result.exit_code == EXIT_EXECUTION_REFUSED
    assert "--allow-execution" in result.output


def test_run_refuses_before_reading_the_repository(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", str(tmp_path / "absent"), "--target", "add"])

    assert result.exit_code == EXIT_EXECUTION_REFUSED
    assert "--allow-execution" in result.output


def test_run_with_unknown_target_is_invalid_input(tmp_path: Path) -> None:
    root = _repository(tmp_path, sample="def add(a: int):\n    return a\n")

    result = runner.invoke(app, ["run", str(root), "--target", "absent", "--allow-execution"])

    assert result.exit_code == EXIT_INVALID_INPUT
    assert "no analyzed target matched" in result.output


def test_run_with_ambiguous_target_is_invalid_input(tmp_path: Path) -> None:
    root = _repository(
        tmp_path,
        one="def process(x: int):\n    return x\n",
        two="def process(x: int):\n    return x + 1\n",
    )

    result = runner.invoke(app, ["run", str(root), "--target", "process", "--allow-execution"])

    assert result.exit_code == EXIT_INVALID_INPUT
    assert "more than one file" in result.output
    assert "--target 'path::process'" in result.output


def test_run_with_a_file_hint_resolves_the_ambiguity(tmp_path: Path) -> None:
    root = _repository(
        tmp_path,
        one="def process(x: int):\n    return x\n",
        two="def process(x: int):\n    return x + 1\n",
    )

    result = runner.invoke(
        app,
        ["run", str(root), "--target", "two.py::process", "--allow-execution"],
    )

    # Resolution succeeded, so the failure is now about Docker or eligibility,
    # never about the target spec.
    assert "more than one file" not in result.output
    assert "no analyzed target matched" not in result.output


def test_run_with_a_file_hint_for_the_wrong_file_is_invalid_input(tmp_path: Path) -> None:
    root = _repository(tmp_path, one="def process(x: int):\n    return x\n")

    result = runner.invoke(
        app,
        ["run", str(root), "--target", "missing.py::process", "--allow-execution"],
    )

    assert result.exit_code == EXIT_INVALID_INPUT
    assert "no analyzed target matched" in result.output


def test_run_without_an_eligible_plan_reports_refusal(tmp_path: Path) -> None:
    # A decorated target is refused by the eligibility gate, so nothing runs.
    root = _repository(
        tmp_path,
        sample=("def logged(f):\n    return f\n\n\n@logged\ndef target(x: int):\n    return x\n"),
    )

    result = runner.invoke(app, ["run", str(root), "--target", "target", "--allow-execution"])

    assert result.exit_code == EXIT_EXECUTION_REFUSED
    assert "no eligible plan" in result.output


def test_run_reports_that_nothing_was_executed_when_no_plan_exists(tmp_path: Path) -> None:
    root = _repository(tmp_path, sample="CONSTANT = 1\n")

    result = runner.invoke(app, ["run", str(root), "--target", "CONSTANT", "--allow-execution"])

    assert result.exit_code in (EXIT_EXECUTION_REFUSED, EXIT_INVALID_INPUT)


def test_scan_never_consults_the_execution_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import edgehunter.execution.docker as docker

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("scan must not touch the execution backend")

    monkeypatch.setattr(docker, "docker_available", forbidden)
    root = _repository(tmp_path, sample="def add(a: int):\n    return a\n")

    result = runner.invoke(app, ["scan", str(root)])

    assert result.exit_code == 0


def test_scan_exit_codes_are_unchanged(tmp_path: Path) -> None:
    clean = _repository(tmp_path, sample="def add(a: int):\n    return a\n")
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "broken.py").write_text("def broken(\n", encoding="utf-8")
    missing = tmp_path / "does-not-exist"

    assert runner.invoke(app, ["scan", str(clean)]).exit_code == 0
    assert runner.invoke(app, ["scan", str(broken)]).exit_code == 1
    assert runner.invoke(app, ["scan", str(missing)]).exit_code == EXIT_INVALID_INPUT


@pytest.mark.integration
def test_run_executes_a_real_container(tmp_path: Path) -> None:
    from edgehunter.execution.docker import docker_available

    available, detail = docker_available()
    if not available:
        pytest.skip(f"Docker is not usable here: {detail}")

    root = _repository(tmp_path, sample="def add(a: int):\n    return a\n")

    result = runner.invoke(
        app,
        ["run", str(root), "--target", "add", "--allow-execution", "--max-cases", "1"],
    )

    assert result.exit_code == 0
    assert "docker (ready)" in result.output
    assert "no target code was executed" not in result.output
    # The report must never present an outcome as a defect.
    assert "Oracle evaluation: not performed" in result.output
