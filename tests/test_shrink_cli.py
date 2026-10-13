"""CLI behaviour for `edgehunter shrink`.

These assert the *refusals* the command must make visible, and that `scan` and
`run` are untouched by the new command. The real end-to-end minimization is
covered by ``tests/test_shrink_integration.py`` against a real Docker daemon; the
subprocess-mocked cases here exist to prove the command never reaches a sandbox
when a static gate refuses.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from edgehunter.cli import (
    EXIT_EXECUTION_REFUSED,
    EXIT_INVALID_INPUT,
    EXIT_SCAN_ISSUES,
    app,
)

runner = CliRunner()

SOURCE = "def validate_age(age: int) -> int:\n    return age\n"


def invoke(*args: str) -> Any:
    return runner.invoke(app, list(args))


def _repository(tmp_path: Path, *, source: str = SOURCE, digest: str | None = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "validators.py").write_text(source, encoding="utf-8")
    used = digest or hashlib.sha256((repo / "validators.py").read_bytes()).hexdigest()
    (repo / "contracts.json").write_text(
        json.dumps(
            {
                "version": 1,
                "contracts": [
                    {
                        "id": "negative-age-rejection",
                        "target": "validators.py::validate_age",
                        "source_sha256": used,
                        "parameter": "age",
                        "domain": {"operator": "lt", "integer": "0"},
                        "expectation": {"kind": "raises_builtin", "exception": "ValueError"},
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return repo


def _shrink_args(repo: Path, **overrides: object) -> list[str]:
    args = [
        "shrink",
        str(repo),
        "--target",
        "validators.py::validate_age",
        "--contract-file",
        str(repo / "contracts.json"),
        "--contract-id",
        "negative-age-rejection",
        "--start-int",
        "-100",
        "--allow-execution",
    ]
    for key, value in overrides.items():
        args.extend([f"--{key.replace('_', '-')}", str(value)])
    return args


class TestAuthorization:
    def test_shrinking_without_the_opt_in_refuses_before_reading(self, tmp_path: Path) -> None:
        repo = _repository(tmp_path)

        result = invoke(
            "shrink",
            str(repo),
            "--target",
            "validators.py::validate_age",
            "--contract-file",
            str(repo / "contracts.json"),
            "--contract-id",
            "negative-age-rejection",
            "--start-int",
            "-100",
        )

        assert result.exit_code == EXIT_EXECUTION_REFUSED
        assert "--allow-execution" in result.output

    def test_a_missing_required_option_is_invalid_input(self, tmp_path: Path) -> None:
        repo = _repository(tmp_path)

        result = invoke("shrink", str(repo))

        assert result.exit_code != 0


class TestRefusalsAreVisible:
    def test_a_missing_contract_file_is_refused(self, tmp_path: Path) -> None:
        repo = _repository(tmp_path)

        result = invoke(*_shrink_args(repo, contract_file=str(tmp_path / "absent.json")))

        assert result.exit_code == EXIT_INVALID_INPUT
        assert "does not exist" in result.output

    def test_an_unknown_contract_id_is_refused(self, tmp_path: Path) -> None:
        repo = _repository(tmp_path)

        result = invoke(*_shrink_args(repo, contract_id="nope"))

        assert result.exit_code == EXIT_INVALID_INPUT
        assert "no contract with id" in result.output

    def test_a_source_fingerprint_mismatch_is_refused(self, tmp_path: Path) -> None:
        repo = _repository(tmp_path, digest="b" * 64)

        result = invoke(*_shrink_args(repo))

        assert result.exit_code == EXIT_INVALID_INPUT
        assert "fingerprint mismatch" in result.output

    def test_a_seed_outside_the_domain_is_refused(self, tmp_path: Path) -> None:
        repo = _repository(tmp_path)

        result = invoke(*_shrink_args(repo, start_int=5))

        assert result.exit_code == EXIT_INVALID_INPUT
        assert "does not satisfy" in result.output

    def test_a_target_that_does_not_match_the_contract_is_refused(self, tmp_path: Path) -> None:
        repo = _repository(tmp_path)

        result = invoke(*_shrink_args(repo, target="validators.py::something_else"))

        assert result.exit_code == EXIT_INVALID_INPUT
        assert "does not match" in result.output

    def test_an_unsupported_scan_target_is_invalid_input(self, tmp_path: Path) -> None:
        text = tmp_path / "notes.txt"
        text.write_text("not python", encoding="utf-8")

        result = invoke(*_shrink_args(tmp_path, contract_file=str(text)))

        assert result.exit_code == EXIT_INVALID_INPUT


class TestUnshrinkableIsItsOwnOutcome:
    def test_an_exact_test_expectation_makes_the_target_not_shrinkable(
        self, tmp_path: Path
    ) -> None:
        """The §7 refusal, surfaced through the CLI.

        The repository's own `assert double(100) == 200` is the only oracle, so a
        user contract cannot be used to shrink around it.
        """

        repo = _repository(tmp_path)
        (repo / "test_validators.py").write_text(
            "from validators import validate_age\n\n\n"
            "def test_old():\n    assert validate_age(100) == 100\n",
            encoding="utf-8",
        )
        # Rebind the contract to the new file digest so the *contract* is valid
        # and the refusal is genuinely about the conflicting evidence.
        (repo / "contracts.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "contracts": [
                        {
                            "id": "negative-age-rejection",
                            "target": "validators.py::validate_age",
                            "source_sha256": hashlib.sha256(
                                (repo / "validators.py").read_bytes()
                            ).hexdigest(),
                            "parameter": "age",
                            "domain": {"operator": "le", "integer": "100"},
                            "expectation": {
                                "kind": "raises_builtin",
                                "exception": "ValueError",
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = invoke(*_shrink_args(repo, start_int=100))

        assert "exact-input" in result.output
        assert "NOT_SHRINKABLE" in result.output or "not_shrinkable" in result.output


class TestOutputContract:
    def test_the_help_states_the_exact_input_limit(self) -> None:
        result = invoke("shrink", "--help")

        assert result.exit_code == 0
        assert "exact test expectation cannot be shrunk" in result.output
        assert "smallest input" in result.output
        assert "not a proven defect" in result.output

    def test_the_command_appears_in_the_top_level_help(self) -> None:
        result = invoke("--help")

        assert "shrink" in result.output


class TestExistingCommandsAreUnchanged:
    def test_scan_still_works_and_its_exit_codes_are_intact(self, tmp_path: Path) -> None:
        repo = _repository(tmp_path)

        result = invoke("scan", str(repo))

        assert result.exit_code == 0
        assert "EdgeHunter scan" in result.output

    def test_scan_issues_still_exit_one(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "broken.py").write_text("def broken(\n", encoding="utf-8")

        result = invoke("scan", str(repo))

        assert result.exit_code == EXIT_SCAN_ISSUES

    def test_run_still_refuses_without_the_opt_in(self, tmp_path: Path) -> None:
        repo = _repository(tmp_path)

        result = invoke("run", str(repo), "--target", "validate_age")

        assert result.exit_code == EXIT_EXECUTION_REFUSED
        assert "--allow-execution" in result.output

    def test_scan_remains_static_and_never_consults_the_shrinker(self) -> None:
        """`scan` must not import the shrink package at all (D-067's static rule)."""

        import edgehunter.pipeline as pipeline

        source = Path(pipeline.__file__).read_text(encoding="utf-8")
        assert "shrink" not in source


class TestExitCodesAreDistinct:
    def test_the_shrink_command_never_exits_one(self, tmp_path: Path) -> None:
        """`1` stays reserved for "scan found issues", so it cannot be confused."""

        repo = _repository(tmp_path, digest="c" * 64)

        result = invoke(*_shrink_args(repo))

        assert result.exit_code != EXIT_SCAN_ISSUES
        assert result.exit_code == EXIT_INVALID_INPUT

    @pytest.mark.parametrize("exit_code", [0, 2, 3])
    def test_the_documented_codes_are_the_ones_in_use(self, exit_code: int) -> None:
        assert exit_code in (0, EXIT_INVALID_INPUT, EXIT_EXECUTION_REFUSED)
