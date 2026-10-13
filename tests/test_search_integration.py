"""Real Docker integration tests for feedback-guided semantic neighborhood search.

Mocked policy tests are necessary but not sufficient. What has to be proven here
is that the whole causal loop survives contact with a real sandbox: that a
proposed integer is really executed in the hardened container, that the contract
verdict comes from the real worker, and that only a *reproduced* contradiction
is reported. Every test in this file starts actual containers against a real
Linux daemon.

The demonstrations:

A. every in-domain probe against a correct target matches; no bug is claimed
B. the feedback policy finds a nearby contradiction and reproduces it
C. the static baseline runs the same snapshot and budget and is slower
D. an out-of-domain starting value is refused before any container starts
E. an unavailable sandbox reports SANDBOX_UNAVAILABLE with no fallback
F. a contract bound to changed source is refused with no execution
G. no EdgeHunter container survives any of it

If Docker is not usable the module is skipped rather than silently passing.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from edgehunter.execution.docker import docker_available
from edgehunter.execution.models import SandboxState, SandboxStatus
from edgehunter.execution.repository import WORKER_IMAGE, runtime_context
from edgehunter.pipeline import analyze_repository
from edgehunter.search import (
    ExploreRequest,
    PolicyName,
    SearchBudgets,
    SearchResult,
    SearchTermination,
    explore_integer,
)

pytestmark = pytest.mark.integration

CORRECT_SOURCE = (
    "def validate(x: int) -> int:\n"
    "    if x < 0:\n"
    '        raise ValueError("negative")\n'
    "\n"
    "    return x\n"
)

OMISSION_SOURCE = (
    "def validate(x: int) -> int:\n"
    "    if x == -2:\n"
    "        return x\n"
    "\n"
    '    raise ValueError("negative")\n'
)


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


def _edgehunter_containers() -> list[str]:
    listing = subprocess.run(
        ["docker", "ps", "--all", "--filter", "name=edgehunter-run-", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return [line for line in listing.stdout.splitlines() if line.strip()]


def _build(tmp_path: Path, source: str, *, name: str = "validate") -> tuple[Path, Path]:
    """A one-file repository plus a contract bound to that file's real digest."""

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    target = repo / "validators.py"
    target.write_text(source, encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    document = {
        "version": 1,
        "contracts": [
            {
                "id": "negative-rejected",
                "target": f"validators.py::{name}",
                "source_sha256": digest,
                "parameter": "x",
                "domain": {"operator": "lt", "integer": "0"},
                "expectation": {"kind": "raises_builtin", "exception": "ValueError"},
            }
        ],
    }
    contract = repo / "contracts.json"
    contract.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return repo, contract


def _explore(
    repo: Path,
    contract: Path,
    start: int,
    *,
    policy: PolicyName = PolicyName.FEEDBACK_GUIDED,
    budgets: SearchBudgets | None = None,
    contract_sha: str | None = None,
) -> SearchResult:
    scan = analyze_repository(repo)
    return explore_integer(
        scan,
        repo,
        ExploreRequest(
            contract_path=contract,
            contract_id="negative-rejected",
            start_value=start,
            target_spec="validators.py::validate",
            policy_name=policy,
            budgets=budgets or SearchBudgets(),
        ),
        authorized=True,
        repository_context=runtime_context(),
    )


def _mismatched_contract(repo: Path, contract: Path) -> Path:
    """The same contract, bound to a fingerprint the source no longer has."""

    document = json.loads(contract.read_text(encoding="utf-8"))
    document["contracts"][0]["source_sha256"] = "0" * 64
    stale = repo / "stale-contracts.json"
    stale.write_text(json.dumps(document), encoding="utf-8")
    return stale


# --- A: a correct target -----------------------------------------------------


def test_every_in_domain_probe_matches_a_correct_target(tmp_path: Path, worker_image: str) -> None:
    """Test A: no reproduced violation, and no claim that the target is correct."""

    repo, contract = _build(tmp_path, CORRECT_SOURCE)

    result = _explore(repo, contract, -64, budgets=SearchBudgets(max_probes=8, max_executions=12))

    assert result.snapshot_verified
    assert result.steps, "the search executed nothing"
    assert all(step.action.value < 0 for step in result.steps)
    assert result.mismatch_count == 0
    assert result.match_count == result.distinct_probes
    assert result.found_violation is False
    assert result.termination in (
        SearchTermination.NO_REPRODUCED_VIOLATION,
        SearchTermination.PROBE_BUDGET_EXHAUSTED,
    )
    if result.termination is SearchTermination.PROBE_BUDGET_EXHAUSTED:
        assert "distinct-probe budget" in result.reason
    else:
        assert "says nothing about inputs that were never tried" in result.reason


# --- B: the feedback policy finds a nearby violation ------------------------


def test_feedback_finds_and_reproduces_a_nearby_violation(
    tmp_path: Path, worker_image: str
) -> None:
    """Test B: anchor matches, its neighbour contradicts, the recheck agrees."""

    repo, contract = _build(tmp_path, OMISSION_SOURCE)

    result = _explore(repo, contract, -64, budgets=SearchBudgets(max_probes=8, max_executions=12))

    probes = [(step.action.value, step.oracle_status) for step in result.steps]
    assert probes[0] == (-1, "match"), probes
    assert probes[1] == (-2, "mismatch"), probes

    assert result.termination is SearchTermination.VIOLATION_REPRODUCED
    assert result.reproduced_value == -2
    assert result.first_mismatch_probe == 2
    assert result.first_reproduced_probe == 2
    assert result.distinct_probes == 2
    assert result.executions == 3
    assert result.reproduction_executions == 1

    rechecks = [step for step in result.steps if step.use.value == "verification_rerun"]
    assert len(rechecks) == 1
    assert rechecks[0].action.value == -2
    assert rechecks[0].action.identifier == result.steps[1].action.identifier


def test_the_nearby_probe_is_not_hardcoded_in_the_policy(tmp_path: Path, worker_image: str) -> None:
    """The value -2 emerges from the comparison evidence, not from the policy."""

    repo, contract = _build(tmp_path, OMISSION_SOURCE)

    result = _explore(repo, contract, -64)

    selected = result.steps[1].action
    assert selected.kind.value == "source_boundary_at"
    assert selected.origin.value == "static_source_boundary"
    assert "x == -2" in selected.reason


# --- C: the matched-budget static baseline ---------------------------------


def test_the_static_baseline_runs_the_same_snapshot_and_budget(
    tmp_path: Path, worker_image: str
) -> None:
    """Test C: the comparison, reported as two observations and no generalisation."""

    repo, contract = _build(tmp_path, OMISSION_SOURCE)
    budgets = SearchBudgets(max_probes=8, max_executions=12)

    static = _explore(repo, contract, -64, policy=PolicyName.STATIC_PRIORITY, budgets=budgets)
    feedback = _explore(repo, contract, -64, policy=PolicyName.FEEDBACK_GUIDED, budgets=budgets)

    for result in (static, feedback):
        assert result.budgets.bounded_probes() == budgets.bounded_probes()
        assert result.budgets.bounded_executions() == budgets.bounded_executions()
        assert result.snapshot_verified
        assert result.executions <= budgets.bounded_executions()

    assert [step.action.value for step in static.steps][:2] == [-1, -64]
    assert [step.action.value for step in feedback.steps][:2] == [-1, -2]

    # Recorded exactly as observed. This is one fixture, not a benchmark.
    print(
        "\nSTATIC_PRIORITY: "
        f"{static.distinct_probes} probes, {static.executions} executions, "
        f"first mismatch at {static.first_mismatch_probe}, "
        f"first reproduced at {static.first_reproduced_probe}, "
        f"{static.termination.value}"
    )
    print(
        "FEEDBACK_GUIDED: "
        f"{feedback.distinct_probes} probes, {feedback.executions} executions, "
        f"first mismatch at {feedback.first_mismatch_probe}, "
        f"first reproduced at {feedback.first_reproduced_probe}, "
        f"{feedback.termination.value}"
    )

    assert static.termination is SearchTermination.VIOLATION_REPRODUCED
    assert feedback.termination is SearchTermination.VIOLATION_REPRODUCED
    assert feedback.first_reproduced_probe is not None
    assert static.first_reproduced_probe is not None
    assert feedback.first_reproduced_probe <= static.first_reproduced_probe


# --- D: a refusal before execution -----------------------------------------


def test_an_out_of_domain_start_value_never_reaches_a_container(tmp_path: Path) -> None:
    """Test D: refusal happens before the session is even opened."""

    repo, contract = _build(tmp_path, OMISSION_SOURCE)
    before = _edgehunter_containers()

    result = _explore(repo, contract, 0)

    assert result.termination is SearchTermination.REFUSED
    assert result.executions == 0
    assert not result.steps
    assert _edgehunter_containers() == before


# --- E: an unavailable sandbox ---------------------------------------------


def test_an_unavailable_sandbox_is_reported_not_worked_around(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test E: no host fallback, and no fabricated finding."""

    repo, contract = _build(tmp_path, OMISSION_SOURCE)
    before = _edgehunter_containers()

    def _unavailable(*args: object, **kwargs: object) -> tuple[None, SandboxStatus]:
        return None, SandboxStatus(
            backend="docker", state=SandboxState.UNAVAILABLE, detail="simulated outage"
        )

    monkeypatch.setattr("edgehunter.search.repository.open_session", _unavailable)

    result = _explore(repo, contract, -64)

    assert result.termination is SearchTermination.SANDBOX_UNAVAILABLE
    assert result.executions == 0
    assert result.found_violation is False
    assert "simulated outage" in result.reason
    assert _edgehunter_containers() == before


# --- F: the source fingerprint --------------------------------------------


def test_a_contract_bound_to_changed_source_is_refused(tmp_path: Path) -> None:
    """Test F: a stale fingerprint is refused with nothing executed."""

    repo, contract = _build(tmp_path, OMISSION_SOURCE)
    stale = _mismatched_contract(repo, contract)
    before = _edgehunter_containers()

    scan = analyze_repository(repo)
    result = explore_integer(
        scan,
        repo,
        ExploreRequest(
            contract_path=stale,
            contract_id="negative-rejected",
            start_value=-64,
            target_spec="validators.py::validate",
        ),
        authorized=True,
        repository_context=runtime_context(),
    )

    assert result.termination is SearchTermination.REFUSED
    assert result.executions == 0
    assert not result.steps
    assert _edgehunter_containers() == before


def test_all_probes_share_one_staged_snapshot(tmp_path: Path, worker_image: str) -> None:
    """The whole search runs against bytes verified once, before the first probe."""

    repo, contract = _build(tmp_path, OMISSION_SOURCE)

    result = _explore(repo, contract, -64)

    assert result.snapshot_verified
    assert result.source_sha256 == hashlib.sha256((repo / "validators.py").read_bytes()).hexdigest()
    assert {step.action.source_sha256 for step in result.steps} == {result.source_sha256}


# --- G: cleanup ------------------------------------------------------------


def test_no_edgehunter_container_survives_the_search(tmp_path: Path, worker_image: str) -> None:
    """Test G: successful, failing, and budget-limited runs all clean up."""

    before = _edgehunter_containers()

    correct_repo, correct_contract = _build(tmp_path / "ok", CORRECT_SOURCE)
    result = _explore(
        correct_repo, correct_contract, -64, budgets=SearchBudgets(max_probes=4, max_executions=6)
    )
    assert result.executions > 0

    omission_repo, omission_contract = _build(tmp_path / "bad", OMISSION_SOURCE)
    assert (
        _explore(omission_repo, omission_contract, -64).termination
        is SearchTermination.VIOLATION_REPRODUCED
    )

    limited_repo, limited_contract = _build(tmp_path / "limited", OMISSION_SOURCE)
    _explore(
        limited_repo, limited_contract, -64, budgets=SearchBudgets(max_probes=1, max_executions=1)
    )

    assert _edgehunter_containers() == before


def test_the_sandbox_flags_are_unchanged(tmp_path: Path) -> None:
    """The search adds no capability; the hardening is the same container.

    The command builder is shared with `run` and `shrink` and was not modified by
    this phase, so this re-asserts the flags on the exact function the search's
    probes go through.
    """

    from edgehunter.execution.docker import CONTAINER_USER, DockerPaths, build_run_command

    workspace = tmp_path / "workspace"
    request_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    for directory in (workspace, request_dir, output_dir):
        directory.mkdir()

    command = build_run_command(
        container_name="edgehunter-run-search-flags",
        image=WORKER_IMAGE or "edgehunter-runtime:1",
        paths=DockerPaths(
            workspace=workspace,
            request=request_dir / "request.json",
            output=output_dir / "result.json",
        ),
    )
    rendered = " ".join(command)

    for flag in (
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--pids-limit 64",
        "--memory 256m",
        "--cpus 1.0",
        f"--user {CONTAINER_USER}",
    ):
        assert flag in rendered, flag
    assert "docker.sock" not in rendered
    # The only writable mount is the bounded, nodev, nosuid tmpfs; the staged
    # workspace and the request are both read-only.
    assert "--tmpfs /tmp:rw,nosuid,nodev,size=16m" in rendered
    assert "target=/workspace,readonly" in rendered
    assert "target=/input,readonly" in rendered
