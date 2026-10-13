"""Run the versioned corpus through the real engine and record what happened.

The harness deliberately has no code path of its own for judging a fixture. It
builds a real repository on disk, writes a real contract bound to that
repository's real SHA-256, and calls the same `explore_integer()` the CLI calls.
Everything reported is then read back out of the engine's own result.

Two consequences follow, and both matter:

* A benchmark number cannot disagree with the product's real behaviour, because
  there is no second implementation to disagree.
* A harness failure shows up as a case that did not match its declaration, which
  is recorded as `unexpected` rather than being smoothed away.

Nothing here is mocked except the one case whose property *is* "the backend is
unavailable", where `open_session` is replaced to simulate an outage and the
assertion is that nothing executes.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from edgehunter.benchmark.models import (
    BenchmarkFixture,
    CaseKind,
    CaseReport,
    Corpus,
    CorpusReport,
    FixtureVerdict,
    PolicyMeasurement,
    aggregate_probes_to_first_reproduction,
    comparable_fixture_ids,
    head_to_head,
)
from edgehunter.execution.models import SandboxState, SandboxStatus
from edgehunter.generation.models import MaterializationStatus
from edgehunter.pipeline import analyze_repository
from edgehunter.search import ExploreRequest, PolicyName, SearchBudgets, explore_integer

__all__ = ["FIXTURE_ROOT", "run_corpus", "run_fixture"]

FIXTURE_ROOT = Path(__file__).parent / "fixtures"
"""The shipped fixture sources.

They live inside the package so that a wheel install can still reproduce the
benchmark. They are excluded from this project's own lint and type-check gates
(see ``pyproject.toml``) because they are deliberately-written *targets*, not
library code.
"""


def _fixture_source(fixture: BenchmarkFixture, repository: Path) -> Path:
    """Copy the one source file a fixture declares into its scratch repository.

    One file per fixture keeps each case independently understandable: a reader
    can read a dozen-line target and know exactly what it does. Every fixture is
    copied to the same in-repository name (``target.py``) so the contract binds
    by content rather than by whichever file the corpus happened to store.
    """

    source = FIXTURE_ROOT / fixture.source_file
    if not source.is_file():
        raise FileNotFoundError(
            f"fixture source {fixture.source_file!r} for {fixture.fixture_id!r} "
            f"is missing: {source}"
        )

    repository.mkdir(parents=True, exist_ok=True)
    target = repository / "target.py"
    shutil.copyfile(source, target)
    return target


def _write_contract(repository: Path, fixture: BenchmarkFixture, digest: str) -> Path:
    """Write a contract bound to this run's real digest of the real source."""

    operator, reference = fixture.domain
    document = {
        "version": 1,
        "contracts": [
            {
                "id": fixture.contract_id,
                "target": fixture.target,
                "source_sha256": digest,
                "parameter": "x",
                "domain": {"operator": operator, "integer": reference},
                "expectation": {
                    "kind": "raises_builtin",
                    "exception": fixture.expectation,
                },
            }
        ],
    }
    path = repository / "contracts.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def _contract_id(fixture: BenchmarkFixture) -> str:
    return fixture.contract_id or "negative-rejected"


@contextmanager
def _sandbox_outage() -> Iterator[None]:
    """Simulate a backend outage for exactly one corpus case.

    This is the single place the harness substitutes anything, and it substitutes
    the *availability check*, not the engine. The assertion is that a search with
    no backend reports that and executes nothing on the host.

    The patch targets the symbol the orchestrator actually calls. Patching
    `execution.repository.open_session` instead would not work: the orchestrator
    imported that name directly, so rebinding the source module's attribute would
    leave the orchestrator holding the real function.
    """

    import edgehunter.search.repository as search_repository

    def _unavailable(*args: object, **kwargs: object) -> tuple[None, SandboxStatus]:
        del args, kwargs
        return None, SandboxStatus(
            backend="docker", state=SandboxState.UNAVAILABLE, detail="simulated outage"
        )

    with patch.object(search_repository, "open_session", _unavailable):
        yield


def _measure(
    fixture: BenchmarkFixture, policy_name: PolicyName, request: ExploreRequest, repository: Path
) -> PolicyMeasurement:
    """Run one real search and read every field back out of its result."""

    started = time.monotonic()
    scan = analyze_repository(repository)
    result = explore_integer(scan, repository, request, authorized=True, runner=None)
    elapsed = time.monotonic() - started

    return PolicyMeasurement(
        fixture_id=fixture.fixture_id,
        policy=policy_name.value,
        candidate_universe=result.universe_size,
        distinct_probes=result.distinct_probes,
        container_executions=result.executions,
        match_count=result.match_count,
        mismatch_count=result.mismatch_count,
        inconclusive_count=result.inconclusive_count,
        reproduced_count=1 if result.found_violation else 0,
        first_mismatch_probe=result.first_mismatch_probe,
        first_reproduced_probe=result.first_reproduced_probe,
        termination=result.termination.value,
        termination_reason=result.reason,
        wall_clock_seconds=round(elapsed, 3),
        reproduction_executions=result.reproduction_executions,
        trace=tuple(
            {
                "step": step.step,
                "action": step.action.kind.value,
                "input": step.action.value,
                "origin": step.action.origin.value,
                "use": step.use.value,
                "oracle": step.oracle_status,
                "feedback": step.feedback.value,
                "reproduction": step.reproduction_status,
            }
            for step in result.steps
        ),
    )


def _search_case(fixture: BenchmarkFixture, repository: Path, contract: Path) -> CaseReport:
    """Run every policy the fixture declares and judge the outcome."""

    measurements: list[PolicyMeasurement] = []
    observed: list[str] = []
    for name in fixture.policies:
        policy_name = PolicyName.parse(name)
        request = ExploreRequest(
            contract_path=contract,
            contract_id=_contract_id(fixture),
            start_value=fixture.start_int or -64,
            target_spec=fixture.target,
            policy_name=policy_name,
            budgets=SearchBudgets(),
        )
        measurement = _measure(fixture, policy_name, request, repository)
        measurements.append(measurement)
        observed.append(measurement.termination)

    if not observed:
        return CaseReport(
            fixture_id=fixture.fixture_id,
            kind=fixture.kind,
            declared=fixture.expected,
            observed="",
            verdict=FixtureVerdict.ERROR,
            detail="the fixture declares no policy to run",
        )

    # A negative fixture is satisfied when every policy stayed silent; a search
    # fixture when at least one policy reproduced the seeded contradiction. Both
    # are checked against what the corpus declared, never against a wish.
    if fixture.kind is CaseKind.NEGATIVE:
        matched = all(
            measurement.termination in ("no_reproduced_violation", "probe_budget_exhausted")
            for measurement in measurements
        )
    else:
        matched = any(measurement.found_within_budget for measurement in measurements)

    return CaseReport(
        fixture_id=fixture.fixture_id,
        kind=fixture.kind,
        declared=fixture.expected,
        observed=",".join(observed),
        verdict=FixtureVerdict.AS_DECLARED if matched else FixtureVerdict.UNEXPECTED,
        detail=fixture.description,
        measurements=tuple(measurements),
    )


def _unsupported_shape_case(
    fixture: BenchmarkFixture, repository: Path, contract: Path
) -> CaseReport:
    """An unsupported target shape must be refused before any execution."""

    request = ExploreRequest(
        contract_path=contract,
        contract_id=_contract_id(fixture),
        start_value=fixture.start_int or -64,
        target_spec=fixture.target,
    )
    scan = analyze_repository(repository)
    result = explore_integer(scan, repository, request, authorized=True)

    return CaseReport(
        fixture_id=fixture.fixture_id,
        kind=fixture.kind,
        declared=fixture.expected,
        observed=result.termination.value,
        verdict=(
            FixtureVerdict.AS_DECLARED
            if result.termination.value == "refused" and result.executions == 0
            else FixtureVerdict.UNEXPECTED
        ),
        detail=fixture.subsystem,
    )


def _fingerprint_case(fixture: BenchmarkFixture, repository: Path) -> CaseReport:
    """A contract bound to a digest the source no longer has must be refused."""

    target = _fixture_source(fixture, repository)
    real = hashlib.sha256(target.read_bytes()).hexdigest()
    contract = _write_contract(repository, fixture, "0" * 64)
    del real

    request = ExploreRequest(
        contract_path=contract,
        contract_id=_contract_id(fixture),
        start_value=fixture.start_int or -64,
        target_spec=fixture.target,
    )
    scan = analyze_repository(repository)
    result = explore_integer(scan, repository, request, authorized=True)

    return CaseReport(
        fixture_id=fixture.fixture_id,
        kind=fixture.kind,
        declared=fixture.expected,
        observed=result.termination.value,
        verdict=(
            FixtureVerdict.AS_DECLARED
            if result.termination.value == "refused" and result.executions == 0
            else FixtureVerdict.UNEXPECTED
        ),
        detail=fixture.subsystem,
    )


def _materialization_case(fixture: BenchmarkFixture, repository: Path) -> CaseReport:
    """Assert that a supported non-integer parameter actually materializes.

    Driven through the existing generation pipeline rather than a search policy:
    the property under test is that the value can be written down at all, which
    has nothing to do with which input a policy chooses next.
    """

    scan = analyze_repository(repository)
    target_name = fixture.target.partition("::")[2]
    wanted = "bool" if fixture.fixture_id.startswith("bool") else "str"

    values: set[str] = set()
    for entry in scan.generation.action_sets:
        if entry.target != target_name:
            continue
        for action in entry.actions:
            if action.parameter != ("flag" if wanted == "bool" else "label"):
                continue
            if action.status is not MaterializationStatus.CONCRETE:
                continue
            values.add(action.kind.value)

    expected = (
        {"set_false", "set_true"}
        if wanted == "bool"
        else {
            "empty_string",
            "whitespace_string",
            "unicode_string",
            "null_character_string",
            "long_string",
        }
    )
    missing = sorted(expected - values)
    matched = not missing

    return CaseReport(
        fixture_id=fixture.fixture_id,
        kind=fixture.kind,
        declared=fixture.subsystem,
        observed=f"{len(values & expected)}/{len(expected)} declared materializations",
        verdict=FixtureVerdict.AS_DECLARED if matched else FixtureVerdict.UNEXPECTED,
        detail=(
            f"missing: {', '.join(missing)}"
            if missing
            else f"observed: {', '.join(sorted(values & expected))}"
        ),
    )


def _sandbox_case(fixture: BenchmarkFixture, repository: Path, contract: Path) -> CaseReport:
    """An unavailable backend must be reported, with no host fallback."""

    request = ExploreRequest(
        contract_path=contract,
        contract_id=_contract_id(fixture),
        start_value=fixture.start_int or -64,
        target_spec=fixture.target,
    )
    scan = analyze_repository(repository)

    with _sandbox_outage():
        result = explore_integer(scan, repository, request, authorized=True)

    matched = result.termination.value == "sandbox_unavailable" and result.executions == 0
    return CaseReport(
        fixture_id=fixture.fixture_id,
        kind=fixture.kind,
        declared=fixture.expected,
        observed=result.termination.value,
        verdict=FixtureVerdict.AS_DECLARED if matched else FixtureVerdict.UNEXPECTED,
        detail=fixture.subsystem,
    )


def run_fixture(fixture: BenchmarkFixture, root: Path) -> CaseReport:
    """Run one fixture through whichever route its kind calls for."""

    repository = root / fixture.fixture_id
    target = _fixture_source(fixture, repository)

    if (
        fixture.fixture_id == "bool-materialization"
        or fixture.fixture_id == "string-materialization"
    ):
        return _materialization_case(fixture, repository)
    if fixture.fixture_id == "fingerprint-mismatch":
        return _fingerprint_case(fixture, repository)
    if fixture.fixture_id == "sandbox-unavailable":
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        contract = _write_contract(repository, fixture, digest)
        return _sandbox_case(fixture, repository, contract)

    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    contract = _write_contract(repository, fixture, digest)

    if fixture.fixture_id == "unsupported-shape":
        return _unsupported_shape_case(fixture, repository, contract)
    return _search_case(fixture, repository, contract)


def run_corpus(
    corpus: Corpus,
    *,
    only: tuple[str, ...] = (),
) -> CorpusReport:
    """Run the whole corpus (or a named subset) and return a report.

    Aggregates are computed per policy over the cases that actually reproduced a
    contradiction, and the exclusion is stated in the returned notes rather than
    left for a reader to infer.
    """

    selected = tuple(corpus.fixtures)
    if only:
        selected = tuple(corpus.by_id(fixture_id) for fixture_id in only)

    cases: list[CaseReport] = []
    with tempfile.TemporaryDirectory(prefix="edgehunter-benchmark-") as scratch:
        root = Path(scratch)
        for fixture in selected:
            started = time.monotonic()
            try:
                cases.append(run_fixture(fixture, root))
            except Exception as exc:  # noqa: BLE001 - see below
                # A harness failure is recorded as a failed case rather than
                # raised: a benchmark that silently skipped the case it could
                # not run would understate its own failure rate. `noqa: BLE001`
                # is deliberate — the point is to report *any* failure, not to
                # handle a known set of them.
                cases.append(
                    CaseReport(
                        fixture_id=fixture.fixture_id,
                        kind=fixture.kind,
                        declared=fixture.expected,
                        observed="",
                        verdict=FixtureVerdict.ERROR,
                        detail=(
                            f"{type(exc).__name__}: {exc} after {time.monotonic() - started:.2f}s"
                        ),
                    )
                )

    aggregates: dict[str, object] = {}
    notes: list[str] = []
    policies = sorted({name for fixture in selected for name in fixture.policies})
    comparable = comparable_fixture_ids(CorpusReport(corpus.version, tuple(cases)))
    for policy in policies:
        aggregates[policy] = aggregate_probes_to_first_reproduction(
            CorpusReport(corpus.version, tuple(cases)), policy, restrict_to=comparable
        )
    aggregates["per_policy_totals"] = {
        policy: aggregate_probes_to_first_reproduction(
            CorpusReport(corpus.version, tuple(cases)), policy
        )
        for policy in policies
    }
    aggregates["head_to_head"] = head_to_head(CorpusReport(corpus.version, tuple(cases)))

    notes.append(
        "Fixtures are synthetic targets with deliberately seeded behaviour, executed through "
        "the real engine. They measure this corpus only and say nothing about any real "
        "repository."
    )
    notes.append(
        f"Cross-policy aggregates cover only the {len(comparable)} fixture(s) every compared "
        "policy ran. Per-policy totals over each policy's own fixtures are reported separately, "
        "because averaging over different case sets measures the fixtures, not the policy."
    )
    notes.append(
        "Averages cover only cases that reproduced a contradiction; cases that reproduced nothing "
        "are counted separately rather than averaged as zero, which would reward a policy for "
        "missing a seeded defect."
    )
    notes.append(
        "The corpus deliberately includes fixtures where the static baseline reaches a "
        "contradiction the feedback policy does not. Those results are reported as observed."
    )

    return CorpusReport(
        corpus_version=corpus.version,
        cases=tuple(cases),
        aggregates=aggregates,
        notes=tuple(notes),
    )
