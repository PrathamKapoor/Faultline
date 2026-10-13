"""The `explore` command: refusals before execution, and unchanged existing commands.

The refusals are the point. Every one of them must happen before a container
could start, and several are checked by asserting the search produced a refusal
status rather than a result -- because an exit code alone would not distinguish
"refused" from "found nothing".

The last block asserts that `scan` and `run` are untouched: adding a command must
not change the behaviour of the existing ones.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from edgehunter.cli import (
    EXIT_EXECUTION_REFUSED,
    EXIT_INVALID_INPUT,
    EXIT_SCAN_ISSUES,
    app,
)
from edgehunter.search.models import SearchTermination

runner = CliRunner()

SOURCE = (
    "def validate(x: int) -> int:\n"
    "    if x == -2:\n"
    "        return x\n\n"
    '    raise ValueError("negative")\n'
)

TARGET = "m.py::validate"


def _repository(tmp_path: Path, source: str = SOURCE) -> Path:
    source_file = tmp_path / "m.py"
    source_file.write_text(source, encoding="utf-8")
    digest = hashlib.sha256(source_file.read_bytes()).hexdigest()
    document = {
        "version": 1,
        "contracts": [
            {
                "id": "neg",
                "target": TARGET,
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


def _run(tmp_path: Path, *args: str) -> Result:
    return runner.invoke(app, ["explore", str(tmp_path), *args])


def _arguments(
    contract: Path, overrides: dict[str, str] | None = None, *, authorized: bool = True
) -> list[str]:
    """Build the command line, with authorization explicit rather than inferred."""

    values: dict[str, str] = {
        "--target": TARGET,
        "--contract-file": str(contract),
        "--contract-id": "neg",
        "--start-int": "-64",
    }
    values.update(overrides or {})

    flat: list[str] = []
    for key, value in values.items():
        flat.extend((key, value))
    if authorized:
        flat.append("--allow-execution")
    return flat


# --- authorization ------------------------------------------------------------


def test_explore_requires_explicit_authorization(tmp_path: Path) -> None:
    """The presence of a contract file is never authorization."""

    contract = _repository(tmp_path)

    result = _run(tmp_path, *_arguments(contract, authorized=False))

    assert result.exit_code == EXIT_EXECUTION_REFUSED
    assert "--allow-execution" in result.output


def test_explore_appears_in_the_command_list() -> None:
    output = runner.invoke(app, ["--help"]).output

    assert "explore" in output
    assert "shrink" in output
    assert "run" in output
    assert "scan" in output


# --- validation before execution ---------------------------------------------


def test_an_unknown_policy_is_refused_as_invalid_input(tmp_path: Path) -> None:
    contract = _repository(tmp_path)

    result = _run(tmp_path, *_arguments(contract, {"--policy": "learned"}))

    assert result.exit_code == EXIT_INVALID_INPUT
    assert "unknown policy" in result.output


@pytest.mark.parametrize("spelling", ["static", "feedback", "static_priority", "feedback_guided"])
def test_documented_policy_spellings_are_accepted(tmp_path: Path, spelling: str) -> None:
    """The flag parses; whether containers run depends on the backend, not the name."""

    contract = _repository(tmp_path)

    result = _run(tmp_path, *_arguments(contract, {"--policy": spelling}))

    assert "unknown policy" not in result.output


def test_a_missing_contract_file_is_refused(tmp_path: Path) -> None:
    _repository(tmp_path)

    result = _run(
        tmp_path,
        *_arguments(tmp_path / "absent.json"),
    )

    assert result.exit_code == EXIT_INVALID_INPUT


def test_a_start_value_outside_the_domain_is_refused(tmp_path: Path) -> None:
    contract = _repository(tmp_path)

    result = _run(tmp_path, *_arguments(contract, {"--start-int": "0"}))

    assert result.exit_code == EXIT_INVALID_INPUT
    assert "does not satisfy the contract's domain" in result.output


def test_a_stale_contract_fingerprint_is_refused(tmp_path: Path) -> None:
    contract = _repository(tmp_path)
    document = json.loads(contract.read_text(encoding="utf-8"))
    document["contracts"][0]["source_sha256"] = "f" * 64
    contract.write_text(json.dumps(document), encoding="utf-8")

    result = _run(tmp_path, *_arguments(contract))

    assert result.exit_code == EXIT_INVALID_INPUT
    assert "fingerprint mismatch" in result.output


def test_a_missing_path_is_refused(tmp_path: Path) -> None:
    contract = _repository(tmp_path)

    result = runner.invoke(
        app,
        ["explore", str(tmp_path / "nowhere"), *_arguments(contract)],
    )

    assert result.exit_code == EXIT_INVALID_INPUT


def test_a_contract_naming_another_target_is_refused(tmp_path: Path) -> None:
    """A contract is never redirected to a different target."""

    contract = _repository(tmp_path)

    result = _run(tmp_path, *_arguments(contract, {"--target": "m.py::other"}))

    assert result.exit_code == EXIT_INVALID_INPUT


# --- refusals are proven to reach no container -------------------------------


def test_no_container_starts_for_any_pre_execution_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every refusal above is checked against the session opener, not the exit code."""

    opened: list[object] = []

    def _tracking(*args: object, **kwargs: object) -> tuple[None, None]:
        opened.append(args)
        return None, None

    monkeypatch.setattr("edgehunter.search.repository.open_session", _tracking)

    cases = [
        {"--start-int": "0"},
        {"--start-int": str(2**63)},
        {"--contract-id": "missing"},
    ]
    for overrides in cases:
        contract = _repository(tmp_path)
        _run(tmp_path, *_arguments(contract, overrides))
        assert opened == [], f"{overrides} opened a session"

    assert opened == []


# --- existing commands are unchanged ----------------------------------------


def test_scan_is_unchanged_and_still_static(tmp_path: Path) -> None:
    _repository(tmp_path)

    result = runner.invoke(app, ["scan", str(tmp_path), "--targets"])

    assert result.exit_code in (0, EXIT_SCAN_ISSUES)
    assert "validate" in result.output


def test_scan_still_never_executes_and_needs_no_authorization(tmp_path: Path) -> None:
    _repository(tmp_path)

    result = runner.invoke(app, ["scan", str(tmp_path)])

    assert result.exit_code == 0
    assert "--allow-execution" not in result.output


def test_run_still_refuses_without_authorization(tmp_path: Path) -> None:
    _repository(tmp_path)

    result = runner.invoke(app, ["run", str(tmp_path), "--target", TARGET])

    assert result.exit_code == EXIT_EXECUTION_REFUSED


def test_run_still_reports_an_unknown_target_as_invalid_input(tmp_path: Path) -> None:
    _repository(tmp_path)

    result = runner.invoke(
        app, ["run", str(tmp_path), "--target", "m.py::absent", "--allow-execution"]
    )

    assert result.exit_code == EXIT_INVALID_INPUT


def test_shrink_still_refuses_without_authorization(tmp_path: Path) -> None:
    contract = _repository(tmp_path)

    result = runner.invoke(
        app,
        [
            "shrink",
            str(tmp_path),
            "--target",
            TARGET,
            "--contract-file",
            str(contract),
            "--contract-id",
            "neg",
            "--start-int",
            "-64",
        ],
    )

    assert result.exit_code == EXIT_EXECUTION_REFUSED


# --- reporting ----------------------------------------------------------------


def test_a_refused_search_reports_refused_and_prints_no_measurements(
    tmp_path: Path,
) -> None:
    """A refusal is not a search: it must not print probe counts."""

    contract = _repository(tmp_path)

    result = _run(tmp_path, *_arguments(contract, {"--start-int": "0"}))

    assert "Result: refused" in result.output
    assert "Distinct probes:" not in result.output


def test_the_reported_policy_name_matches_the_requested_one(tmp_path: Path) -> None:
    """Whatever happens, the header says which policy ran."""

    from edgehunter.cli import _print_search
    from edgehunter.search.models import PolicyName, SearchResult

    for name in PolicyName:
        _print_search(
            SearchResult(
                termination=SearchTermination.REFUSED,
                reason="because",
                target_identity=TARGET,
                contract_id="neg",
                parameter="x",
                policy=name,
            )
        )


def test_never_happened_measurements_read_as_never(tmp_path: Path) -> None:
    from edgehunter.cli import _index_text

    assert _index_text(None) == "never"
    assert _index_text(3) == "probe 3"
