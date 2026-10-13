"""Real Docker integration tests for bounded counterexample minimization.

Mocked acceptance rules are necessary but not sufficient: what has to be proven
here is that a proposed smaller integer is really executed, on the same staged
source, in the same hardened container, and that the reduced counterexample is
real. Everything in this file starts actual containers against a real Linux
daemon.

The demonstrations:

A. a negative domain shrinks a genuine counterexample and reaches the anchor
B. a positive domain shrinks to the domain's closest admissible value
C. a correct implementation produces a match and no shrink at all
D. a trial whose failure character differs is rejected, not accepted
E. a contract bound to changed source is refused before any container starts
F. an unavailable sandbox yields INCONCLUSIVE and never a host execution
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
from edgehunter.execution.repository import WORKER_IMAGE, runtime_context
from edgehunter.pipeline import analyze_repository
from edgehunter.shrink import (
    DEFAULT_MAX_EXECUTIONS,
    DEFAULT_MAX_PROPOSALS,
    ShrinkPolicy,
    ShrinkRequest,
    ShrinkResult,
    ShrinkStatus,
    shrink_integer,
)

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


def _edgehunter_containers() -> list[str]:
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
        timeout=60,
    )
    return [line for line in listing.stdout.splitlines() if line.strip()]


def _build(
    tmp_path: Path,
    *,
    source: str,
    operator: str = "lt",
    reference: str = "0",
    parameter: str = "x",
    name: str = "validate",
    contract_id: str = "neg",
) -> tuple[Path, Path]:
    """A one-file repository plus a contract bound to that file's real digest."""

    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "validators.py"
    target.write_text(source, encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    document = {
        "version": 1,
        "contracts": [
            {
                "id": contract_id,
                "target": f"validators.py::{name}",
                "source_sha256": digest,
                "parameter": parameter,
                "domain": {"operator": operator, "integer": reference},
                "expectation": {"kind": "raises_builtin", "exception": "ValueError"},
            }
        ],
    }
    contract = repo / "contracts.json"
    contract.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return repo, contract


def _shrink(repo: Path, contract: Path, seed: int, **policy: int) -> ShrinkResult:
    scan = analyze_repository(repo)
    return shrink_integer(
        scan,
        repo,
        ShrinkRequest(
            contract_path=contract,
            contract_id="neg",
            seed=seed,
            policy=ShrinkPolicy(**policy) if policy else ShrinkPolicy(),
        ),
        authorized=True,
        repository_context=runtime_context(),
    )


PASSTHROUGH = "def validate(x: int) -> int:\n    return x\n"


class TestANegativeIntegerShrinks:
    def test_a_genuine_counterexample_is_reduced_to_the_anchor(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        repo, contract = _build(tmp_path, source=PASSTHROUGH, reference="0")

        result = _shrink(repo, contract, -100)

        assert result.snapshot_verified is True
        # The seed returned normally where a ValueError was expected.
        assert result.seed_signature == "returned normally"
        assert result.status is ShrinkStatus.SHRUNK
        assert result.seed_value == -100
        assert result.best_value == -1
        assert result.reduced is True
        # The same failure character was preserved, and it reproduced.
        assert result.best_signature == "returned normally"
        accepted = [a for a in result.attempts if a.accepted]
        assert accepted, [(a.value, a.outcome.value) for a in result.attempts]
        assert all(a.signature_matches for a in accepted)
        assert all(a.in_domain for a in result.attempts)
        assert all(a.complexity < 100 for a in result.attempts)

    def test_the_reduced_input_still_satisfies_the_declared_domain(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        repo, contract = _build(tmp_path, source=PASSTHROUGH, reference="0")

        result = _shrink(repo, contract, -100)

        assert result.best_value is not None
        assert result.best_value < 0


class TestBPositiveDomain:
    def test_a_positive_domain_reduces_to_its_closest_admissible_value(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        repo, contract = _build(tmp_path, source=PASSTHROUGH, operator="ge", reference="10")

        result = _shrink(repo, contract, 100)

        assert result.status is ShrinkStatus.SHRUNK
        assert result.seed_value == 100
        assert result.best_value == 10
        assert result.best_signature == "returned normally"
        # Nothing below the declared domain was ever proposed or executed.
        assert all(a.complexity < 100 for a in result.attempts)
        assert all(a.in_domain for a in result.attempts)


class TestCCorrectImplementation:
    def test_a_matching_implementation_is_not_shrunk(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        repo, contract = _build(
            tmp_path,
            source="def validate(x: int) -> int:\n    if x < 0:\n"
            '        raise ValueError("negative")\n    return x\n',
            reference="0",
        )

        result = _shrink(repo, contract, -100)

        assert result.status is ShrinkStatus.ORIGINAL_NOT_FAILING
        assert "does not contradict it" in result.reason
        assert result.best_value is None
        assert result.attempts == ()
        assert result.executions_consumed == 2


class TestDFailureSignatureChanges:
    def test_a_trial_with_a_different_failure_is_rejected(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        """`-1` raises TypeError; that is a different failure, not a reduction."""

        repo, contract = _build(
            tmp_path,
            source="def validate(x: int) -> int:\n"
            "    if x == -1:\n"
            '        raise TypeError("different failure")\n    return x\n',
            reference="0",
        )

        result = _shrink(repo, contract, -100)

        assert result.seed_signature == "returned normally"
        trial = next(a for a in result.attempts if a.value == -1)
        assert trial.outcome.value == "signature_mismatch"
        assert "different failure" in trial.reason
        assert result.best_value != -1
        # The search still reduced the input, just never through -1.
        assert result.status is ShrinkStatus.SHRUNK
        assert result.best_signature == "returned normally"

    def test_a_trial_matching_the_original_failure_is_accepted(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        repo, contract = _build(
            tmp_path,
            source="def validate(x: int) -> int:\n"
            "    if x == -1:\n"
            '        raise ValueError("same failure as the contract wants")\n    return x\n',
            reference="0",
        )

        result = _shrink(repo, contract, -100)

        # -1 raises the *expected* exception, so it satisfies the contract and is
        # not a counterexample at all.
        trial = next(a for a in result.attempts if a.value == -1)
        assert trial.outcome.value == "oracle_not_mismatch"
        assert trial.oracle_status == "match"
        assert result.best_value != -1


class TestESourceMismatch:
    def test_a_contract_bound_to_changed_source_is_refused(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        """Integration E: alter the file after authoring the contract."""

        repo, contract = _build(tmp_path, source=PASSTHROUGH, reference="0")
        before = _edgehunter_containers()

        # The source changes after the contract recorded its digest.
        (repo / "validators.py").write_text(
            PASSTHROUGH + "\n\ndef extra() -> None:\n    return None\n", encoding="utf-8"
        )

        result = _shrink(repo, contract, -100)

        assert result.status is ShrinkStatus.REFUSED
        assert "fingerprint mismatch" in result.reason
        assert result.executions_consumed == 0
        assert result.attempts == ()
        # No container was started at all.
        assert _edgehunter_containers() == before


class TestFNoDockerFallback:
    def test_an_unavailable_sandbox_is_inconclusive_and_never_executes_on_the_host(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker_image: str
    ) -> None:
        repo, contract = _build(tmp_path, source=PASSTHROUGH, reference="0")

        import edgehunter.shrink.repository as shrink_repository

        monkeypatch.setattr(shrink_repository, "open_session", _unavailable_session, raising=True)

        result = _shrink(repo, contract, -100)

        assert result.status is ShrinkStatus.INCONCLUSIVE
        assert "no sandbox was available" in result.reason
        assert result.executions_consumed == 0
        assert result.attempts == ()


def _unavailable_session(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
    """An `open_session` that reports the backend as unavailable, as D-045 does."""

    from edgehunter.execution.models import SandboxState, SandboxStatus

    return None, SandboxStatus(
        backend="docker",
        state=SandboxState.UNAVAILABLE,
        detail="simulated: the docker daemon is not reachable",
    )


class TestGCleanup:
    def test_no_edgehunter_container_survives(self, tmp_path: Path, worker_image: str) -> None:
        repo, contract = _build(tmp_path, source=PASSTHROUGH, reference="0")

        _shrink(repo, contract, -100)
        shrunk = _shrink(repo, contract, -100, max_proposals=DEFAULT_MAX_PROPOSALS)
        assert shrunk.status in (ShrinkStatus.SHRUNK, ShrinkStatus.NO_SMALLER_CASE_FOUND)
        assert shrunk.executions_consumed <= DEFAULT_MAX_EXECUTIONS

        assert _edgehunter_containers() == []


class TestHExecutionBudgetInReality:
    def test_real_containers_stay_within_the_default_budget(
        self, tmp_path: Path, worker_image: str
    ) -> None:
        repo, contract = _build(tmp_path, source=PASSTHROUGH, reference="0")

        result = _shrink(repo, contract, -100, max_proposals=2, max_executions=6)

        # Two seed attempts plus at most two proposals of two attempts each.
        assert result.executions_consumed <= 6
        assert len(result.attempts) <= 2
        assert result.reason
