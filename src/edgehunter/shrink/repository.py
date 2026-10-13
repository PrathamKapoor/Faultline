"""The bounded minimizer: one user-supplied integer, one user-declared contract.

This is the entry point the ``shrink`` CLI command drives. It is deliberately
narrow: one integer parameter, one declared inequality domain, one expected
builtin exception, one source snapshot, one failure signature, and a bounded
deterministic proposal list. Everything it does not support is listed in
``handoff.md`` rather than approximated.

The order of operations is the safety property. Nothing is executed until every
static gate has passed:

1. read and validate the explicitly selected contract file (pure parsing);
2. resolve the contract's target to exactly one analyzed callable;
3. verify the target's analyzed fingerprint against the contract's
   ``source_sha256``;
4. check the target is an ordinary synchronous top-level function whose only
   parameter is the contract's, annotated ``int``;
5. check the seed satisfies the declared domain;
6. check no exact-input repository expectation conflicts with the selection;
7. open **one** snapshot-fixed execution session and verify the staged bytes
   match the analyzed bytes;
8. execute the seed the required number of times and require a *reproduced*
   mismatch;
9. only then propose, execute, verify, and accept smaller integers.

The refusal in step 1 for an exact-input oracle is the reason a naive shrinker
would be dangerous: no smaller input is ever executed and then judged against an
oracle that does not cover it. The verification in steps 3 and 7 is what keeps
every attempt measured against one source revision. The reproduction in step 8
is what stops a single flaky observation from becoming a minimization problem.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, Protocol

from edgehunter.evaluation.evaluator import identity_from_observation, is_infrastructure_outcome
from edgehunter.evaluation.exceptions import match_exception_observed
from edgehunter.evaluation.models import OracleStatus
from edgehunter.evaluation.repository import SourceSnapshot
from edgehunter.execution.codec import PROTOCOL_VERSION, TypedValue, ValueKind
from edgehunter.execution.docker import DEFAULT_TIMEOUT_SECONDS, clamp_timeout, new_execution_id
from edgehunter.execution.eligibility import ELIGIBLE_TARGET_KIND
from edgehunter.execution.models import (
    ExecutionRequest,
    ExecutionResult,
    InvocationArguments,
    OutcomeKind,
    TargetRef,
)
from edgehunter.execution.repository import (
    ExecutionRefused,
    ExecutionSession,
    open_session,
    runtime_context,
)
from edgehunter.oracle.models import (
    TargetResolution,
)
from edgehunter.pipeline import RepositoryScan
from edgehunter.scanner.annotations import AnnotationKind
from edgehunter.scanner.models import FunctionInfo, ParameterKind
from edgehunter.shrink.contract import Contract, ContractError, read_contract_file
from edgehunter.shrink.models import (
    MAX_SIGNED_INT,
    MIN_SIGNED_INT,
    AttemptOutcome,
    InvocationOrigin,
    OracleSource,
    ShrinkAttempt,
    ShrinkPolicy,
    ShrinkResult,
    ShrinkStatus,
)
from edgehunter.shrink.proposals import complexity, is_simpler, propose_values
from edgehunter.shrink.shrinkability import (
    assess_production_property,
    assess_target_shrinkability,
    assess_test_expectation,
    refuse_conflicting,
)
from edgehunter.shrink.signature import FailureSignature, signature_of

# The versioned evidence artifact's own schema, distinct from the execution
# protocol version and from the contract file version.
SHRINK_ARTIFACT_SCHEMA: Final[int] = 1


@dataclass(frozen=True, slots=True)
class ShrinkRequest:
    """Everything the user explicitly asked for, before anything is inspected."""

    contract_path: Path | None
    """The explicitly selected contract file, or ``None`` for no contract at all.

    ``None`` is a supported and important case: it is what the CLI's mandatory
    option prevents but the library allows, and it is the only way to ask the
    question "could this target be shrunk using the repository's own evidence
    alone?". The answer is recorded as a shrinkability assessment and nothing is
    executed.
    """

    contract_id: str
    seed: int
    target_spec: str = ""
    """Optional CLI cross-check; when set it must agree with the contract."""

    policy: ShrinkPolicy = ShrinkPolicy()
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def bounded_timeout(self) -> float:
        return clamp_timeout(self.timeout_seconds)


class AttemptRunner(Protocol):
    """How one integer is turned into execution results.

    The real implementation runs containers; the unit tests supply fabricated
    observations. Keeping this the only seam is what lets the whole acceptance
    logic be tested without Docker, and it is a seam rather than a bypass: the
    real runner is the same ``_run_request`` the ``run`` command uses.
    """

    def execute_attempts(self, value: int, *, attempts: int) -> tuple[ExecutionResult, ...]:
        """Run up to ``attempts`` containers for one integer, in order."""


class SessionAttemptRunner:
    """The real runner: containers against one snapshot-fixed session."""

    def __init__(
        self,
        session: ExecutionSession,
        target: TargetRef,
        parameter: str,
        timeout_seconds: float,
    ) -> None:
        self._session = session
        self._target = target
        self._parameter = parameter
        self._timeout = timeout_seconds

    def execute_attempts(self, value: int, *, attempts: int) -> tuple[ExecutionResult, ...]:
        results: list[ExecutionResult] = []
        for _ in range(max(0, attempts)):
            request = _seed_request(self._target, self._parameter, value)
            results.append(self._session.execute(request, timeout_seconds=self._timeout))
        return tuple(results)


def _seed_request(target: TargetRef, parameter: str, value: int) -> ExecutionRequest:
    """Build one typed request for a single validated integer argument.

    The value travels as a typed protocol integer, not as rendered text, so it
    cannot smuggle an expression past the codec's whitelist. The action fields
    are filled with the shrinker's own provenance rather than a Phase 5 action,
    because a minimization step is not an adversarial action the action layer
    proposed.
    """

    arguments = InvocationArguments(
        args=(TypedValue(kind=ValueKind.INTEGER, scalar=str(value)),), kwargs=()
    )
    execution_id = new_execution_id()
    return ExecutionRequest(
        protocol_version=PROTOCOL_VERSION,
        execution_id=execution_id,
        nonce=new_execution_id(),
        target=target,
        invocation=arguments,
        action_id=f"shrink:{parameter}={value}",
        primary_action_kind=InvocationOrigin.INTEGER_SHRINK_PROPOSAL.value,
        primary_action_parameter=parameter,
    )


def shrink_integer(
    scan: RepositoryScan,
    source_root: Path,
    request: ShrinkRequest,
    *,
    authorized: bool,
    repository_context: Path | None = None,
    runner: AttemptRunner | None = None,
) -> ShrinkResult:
    """Minimize one integer counterexample under one user-declared contract.

    When ``runner`` is supplied the operation is executed entirely against
    fabricated observations and never touches Docker; that path exists for the
    unit tests of the acceptance rules, not for producing evidence. It shares the
    same static gates, so it cannot be a weaker route.

    When ``request.contract_path`` is ``None`` no contract was selected. The
    repository's own evidence is then assessed on its own and the result is
    ``NOT_SHRINKABLE`` without anything being executed: mined test expectations
    and mined production properties never authorize changing an input.
    """

    if request.contract_path is None:
        return _assess_without_contract(scan, request)
    if runner is not None:
        return _shrink_with_runner(scan, request, runner)
    return _shrink_with_docker(scan, source_root, request, authorized, repository_context)


def _assess_without_contract(scan: RepositoryScan, request: ShrinkRequest) -> ShrinkResult:
    """Answer "can this target be shrunk using the repository's own evidence?".

    No contract means no reusable oracle, so this can only ever refuse, and it
    refuses *before* anything is executed. That is the §7 requirement made
    structural: the exact-input oracle is rejected as unusable for a changed
    input, and the proposed smaller input is never run.
    """

    target_name = request.target_spec.rpartition("::")[2] if request.target_spec else ""
    if not target_name:
        return _refused(
            ShrinkStatus.REFUSED,
            "a target must be selected as 'relative/path.py::qualified_name' to assess whether "
            "it can be shrunk",
        )
    assessment = assess_target_shrinkability(
        scan.oracles.test_expectations, scan.oracles.production_properties, target_name
    )
    return ShrinkResult(
        status=ShrinkStatus.NOT_SHRINKABLE,
        reason=(
            f"no reusable oracle is available: {assessment.reason} An explicitly selected "
            "user-declared contract is the only source that authorizes changing an input."
        ),
        target_identity=request.target_spec,
        contract_id="",
        parameter="",
        seed_value=request.seed,
    )


# ---------------------------------------------------------------------------
# Refusals that happen before any execution
# ---------------------------------------------------------------------------


def _refused(
    status: ShrinkStatus, reason: str, contract_id: str = "", detail: str = ""
) -> ShrinkResult:
    return ShrinkResult(
        status=status,
        reason=reason,
        target_identity=detail,
        contract_id=contract_id,
        parameter="",
        seed_value=0,
    )


def _not_shrinkable(reason: str, contract: Contract) -> ShrinkResult:
    """The available evidence does not survive an input change.

    Distinct from :func:`_refused`: nothing here is malformed or unauthorised.
    The operation is well-formed and simply not sound, because the only oracle
    that covers this invocation is exact-input.
    """

    return ShrinkResult(
        status=ShrinkStatus.NOT_SHRINKABLE,
        reason=reason,
        target_identity=contract.target_spec,
        contract_id=contract.contract_id,
        parameter=contract.parameter,
        seed_value=0,
        contract_source=OracleSource.USER_DECLARED_CONTRACT,
    )


def _resolve_contract(request: ShrinkRequest) -> tuple[Contract, str]:
    """Read, validate, and select the contract. Never executes anything."""

    if request.contract_path is None:
        raise _ContractRefusal(
            "no contract file was selected; a reusable oracle must come from an explicitly "
            "selected contract, because the repository's own exact-input expectations cannot "
            "authorize changing an input"
        )
    try:
        contract_file = read_contract_file(request.contract_path)
        contract = contract_file.select(request.contract_id)
    except ContractError as exc:
        raise _ContractRefusal(str(exc)) from exc

    if request.target_spec and request.target_spec != contract.target_spec:
        raise _ContractRefusal(
            f"the selected target {request.target_spec!r} does not match the contract's bound "
            f"target {contract.target_spec!r}; a contract may not be redirected to another target"
        )
    if not contract.is_satisfiable:
        raise _ContractRefusal(
            f"the contract's domain {contract.domain.describe()} admits no signed 64-bit integer, "
            "so no input could ever satisfy it"
        )
    return contract, contract_file.sha256


class _ContractRefusal(Exception):
    """Internal signal carrying a user-facing contract refusal message."""


def _resolve_target(scan: RepositoryScan, contract: Contract) -> tuple[TargetRef, FunctionInfo]:
    """Resolve the contract's target to exactly one analyzed callable."""

    matches = [
        (module.relative_path, function)
        for module in scan.semantics.structure.modules
        for function in module.functions
        if function.qualified_name == contract.target_name
    ]
    exact = [entry for entry in matches if entry[0].as_posix() == contract.target_file]
    if not exact:
        found = ", ".join(sorted(relative.as_posix() for relative, _ in matches)) or "none"
        raise _ContractRefusal(
            f"the contract names {contract.target_spec!r}, but this repository has no such "
            f"analyzed target (matching name(s) found in: {found})"
        )
    if len(exact) > 1:
        raise _ContractRefusal(
            f"the contract's target {contract.target_spec!r} is ambiguous; it matched more than "
            "one definition"
        )
    relative, function = exact[0]
    return TargetRef(
        relative_file=relative.as_posix(),
        qualified_name=function.qualified_name,
        lineno=function.lineno,
    ), function


def _check_seed_target(function: FunctionInfo, contract: Contract) -> None:
    """Refuse anything outside the narrow supported target shape.

    Ordinary synchronous top-level function, no receiver, no decorators, no
    variadics, and the contract's parameter as the only input, annotated ``int``
    with no default. The rules reuse the existing eligibility constants rather
    than restating them.
    """

    if function.kind is not ELIGIBLE_TARGET_KIND:
        raise _ContractRefusal(
            f"{function.qualified_name} is a {function.kind.value}; only an ordinary synchronous "
            "top-level function can be shrunk in this phase"
        )
    if function.is_async:
        raise _ContractRefusal(f"{function.qualified_name} is async and is not supported")
    if function.decorators:
        raise _ContractRefusal(
            f"{function.qualified_name} is decorated ({', '.join(function.decorators)}); a "
            "decorator's effect on the call is not inferred"
        )
    variadic = [
        parameter.name
        for parameter in function.parameters
        if parameter.kind in (ParameterKind.VAR_POSITIONAL, ParameterKind.VAR_KEYWORD)
    ]
    if variadic:
        raise _ContractRefusal(
            f"{function.qualified_name} has variadic parameter(s) {', '.join(variadic)}, which "
            "this phase does not shrink"
        )

    names = [parameter.name for parameter in function.parameters]
    if contract.parameter not in names:
        raise _ContractRefusal(
            f"the contract constrains {contract.parameter!r} but "
            f"{function.qualified_name} declares {', '.join(names) or 'no parameters'}"
        )
    if len(names) != 1:
        raise _ContractRefusal(
            f"{function.qualified_name} declares {len(names)} parameters ({', '.join(names)}); "
            "this phase shrinks exactly one, and inventing a value for the others is forbidden"
        )

    parameter = function.parameters[0]
    if parameter.has_default:
        raise _ContractRefusal(
            f"the contract's parameter {parameter.name!r} has a declared default; a declared "
            "default is never evaluated, so the exact call is not knowable"
        )
    if parameter.kind not in (ParameterKind.POSITIONAL_ONLY, ParameterKind.POSITIONAL_OR_KEYWORD):
        raise _ContractRefusal(
            f"the contract's parameter {parameter.name!r} is {parameter.kind.value}; only an "
            "ordinary positional parameter is supported"
        )
    if parameter.shape.kind is not AnnotationKind.INT:
        raise _ContractRefusal(
            f"the contract's parameter {parameter.name!r} is annotated "
            f"{parameter.shape.description}, not INT; a typed integer value is required"
        )


def _check_seed(value: int, contract: Contract) -> None:
    """Validate the user's seed: in range and inside the declared domain."""

    if not MIN_SIGNED_INT <= value <= MAX_SIGNED_INT:
        raise _ContractRefusal(
            f"the seed {value} is outside the signed 64-bit range this phase supports"
        )
    if not contract.domain.contains(value):
        raise _ContractRefusal(
            f"the seed {value} does not satisfy the contract's domain "
            f"({contract.parameter} {contract.domain.describe()}); a seed outside the domain "
            "would not be governed by the contract, so shrinking cannot start"
        )


def _conflicting_expectation(scan: RepositoryScan, contract: Contract) -> str | None:
    """Report an exact-input expectation that also covers this target.

    A repository's own test is not discarded because a user declaration is more
    convenient (D-064). Both evidence sources are preserved and the operation is
    refused as conflicting.
    """

    assessments = []
    for expectation in scan.oracles.test_expectations:
        if expectation.target is None or expectation.target != contract.target_name:
            continue
        if TargetResolution(expectation.resolution) is TargetResolution.RESOLVED:
            assessments.append(assess_test_expectation(expectation))
    for candidate in scan.oracles.production_properties:
        if candidate.target != contract.target_name:
            continue
        assessments.append(assess_production_property(candidate))
    return refuse_conflicting(tuple(assessments))


# ---------------------------------------------------------------------------
# The contract adapter: applicability plus verdict, reusing the evaluator's rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContractVerdict:
    """One observation judged against one contract."""

    status: OracleStatus
    reason: str


def evaluate_against_contract(
    contract: Contract, result: ExecutionResult, *, snapshot_verified: bool
) -> ContractVerdict:
    """Judge one observation against a contract's expectation.

    A small adapter, and deliberately so: the domain check and the "did it
    return instead of raise" step are the only things added, because a contract is
    applicable across a domain rather than at one input. Everything else -- the
    builtin exception hierarchy, the identity rules, the infrastructure-outcome
    classification -- is the evaluator's, called rather than copied.
    """

    observation = result.observation
    if not snapshot_verified:
        return ContractVerdict(
            OracleStatus.INCONCLUSIVE,
            "the source snapshot was not verified, so no verdict is reported",
        )
    if observation is None:
        return ContractVerdict(
            OracleStatus.INCONCLUSIVE,
            "the attempt produced no observation, so the contract could not be evaluated",
        )
    if is_infrastructure_outcome(observation.outcome):
        return ContractVerdict(
            OracleStatus.INCONCLUSIVE,
            f"the execution ended as {observation.outcome.value} before the target's behaviour "
            "could be observed; an infrastructure failure is not an oracle violation",
        )
    if observation.outcome is OutcomeKind.RETURNED and observation.opaque_return is not None:
        # A generator, or any other unrepresentable return: calling it did not
        # execute a body that could have raised, so the call is not comparable.
        return ContractVerdict(
            OracleStatus.UNSUPPORTED,
            "the call returned a value the protocol cannot represent "
            f"({observation.opaque_return}), so it is not a normal return and cannot be judged "
            "against the contract",
        )
    if observation.outcome is OutcomeKind.RETURNED:
        return ContractVerdict(
            OracleStatus.MISMATCH,
            f"the contract expects {contract.target_name} to {contract.expectation.describe()} "
            "when the domain holds, but the invocation returned normally",
        )
    if observation.outcome is not OutcomeKind.RAISED:
        return ContractVerdict(
            OracleStatus.INCONCLUSIVE,
            f"the execution ended as {observation.outcome.value}, which is not a comparable "
            "return or raise",
        )

    match = match_exception_observed(
        contract.expectation.exception, identity_from_observation(observation)
    )
    return ContractVerdict(match.status, match.reason)


# ---------------------------------------------------------------------------
# The search itself
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Reproduction:
    """What repeating one input proved, with the refusal classified honestly."""

    equivalent: int
    signature: FailureSignature | None
    status: ShrinkStatus
    reason: str

    @property
    def reproduced(self) -> bool:
        return not self.reason


def _verify_reproduced(
    contract: Contract,
    results: tuple[ExecutionResult, ...],
    *,
    snapshot_verified: bool,
    label: str,
) -> _Reproduction:
    """Require every attempt to contradict the contract, with one failure shape.

    The three refusals are distinguished rather than collapsed, because they mean
    different things to a reader: *not failing* is that the seed never
    contradicted the contract at all, *not reproducible* is that attempts
    disagreed, and *inconclusive* is that an attempt could not be judged. A seed
    that mismatched once and matched once is the second, not the first.
    """

    if not results:
        return _Reproduction(0, None, ShrinkStatus.INCONCLUSIVE, f"{label}: no attempts were made")

    mismatches: list[FailureSignature] = []
    for result in results:
        verdict = evaluate_against_contract(contract, result, snapshot_verified=snapshot_verified)
        if verdict.status is not OracleStatus.MISMATCH:
            if not mismatches and verdict.status is OracleStatus.MATCH:
                # The contract was satisfied, so this input is not a counterexample
                # at all -- a different fact from "the attempts disagreed".
                return _Reproduction(
                    0,
                    None,
                    ShrinkStatus.ORIGINAL_NOT_FAILING,
                    f"{label}: the contract was satisfied ({verdict.reason}), so the input does "
                    "not contradict it",
                )
            if not mismatches:
                return _Reproduction(
                    0,
                    None,
                    ShrinkStatus.INCONCLUSIVE,
                    f"{label}: the contract was {verdict.status.value}, not a mismatch "
                    f"({verdict.reason})",
                )
            return _Reproduction(
                0,
                mismatches[0],
                ShrinkStatus.ORIGINAL_NOT_REPRODUCIBLE,
                f"{label}: attempt {len(mismatches) + 1} was {verdict.status.value} while an "
                f"earlier attempt contradicted the contract; the seed is not reproducible",
            )
        signature = signature_of(result.observation) if result.observation else None
        if signature is None:
            return _Reproduction(
                0,
                None,
                ShrinkStatus.INCONCLUSIVE,
                f"{label}: attempt ended as {result.outcome.value}, which has no comparable "
                "failure signature",
            )
        if mismatches and not mismatches[0].matches(signature):
            return _Reproduction(
                0,
                mismatches[0],
                ShrinkStatus.ORIGINAL_NOT_REPRODUCIBLE,
                f"{label}: attempts disagreed ({mismatches[0].describe()} vs "
                f"{signature.describe()})",
            )
        mismatches.append(signature)

    if len(mismatches) < len(results):
        return _Reproduction(
            len(mismatches),
            mismatches[0],
            ShrinkStatus.INCONCLUSIVE,
            f"{label}: only {len(mismatches)} of {len(results)} attempts were usable, and an "
            "attempt that could not be evaluated is not a counterexample",
        )
    return _Reproduction(len(mismatches), mismatches[0], ShrinkStatus.SHRUNK, "")


def _preflight(
    scan: RepositoryScan, request: ShrinkRequest
) -> tuple[Contract, str, TargetRef, FunctionInfo] | ShrinkResult:
    """Run every static gate, in order, before anything can be executed.

    Both the Docker route and the fabricated-runner route go through here, so a
    unit test can never pass on a weaker path than production. The order is
    deliberate: contract validity, then target identity, then target shape, then
    the source fingerprint, then the seed's domain, then conflicting evidence.
    Nothing here touches Docker, reads a staged tree, or imports target code.
    """

    try:
        contract, contract_sha = _resolve_contract(request)
        target_ref, function = _resolve_target(scan, contract)
        _check_seed_target(function, contract)
    except _ContractRefusal as exc:
        return _refused(ShrinkStatus.REFUSED, str(exc), request.contract_id)

    analyzed_digest = _analyzed_digest(scan, contract)
    if analyzed_digest is None:
        return _refused(
            ShrinkStatus.INCONCLUSIVE,
            f"the analyzed fingerprint of {contract.target_file} is unavailable, so the "
            "contract's source_sha256 cannot be verified",
            contract.contract_id,
            contract.target_spec,
        )
    if analyzed_digest != contract.source_sha256:
        return _refused(
            ShrinkStatus.REFUSED,
            f"source fingerprint mismatch: the contract was written against "
            f"{contract.source_sha256[:12]}... but {contract.target_file} now hashes to "
            f"{analyzed_digest[:12]}...; a contract is never silently rebound to changed code",
            contract.contract_id,
            contract.target_spec,
        )

    try:
        _check_seed(request.seed, contract)
    except _ContractRefusal as exc:
        return _refused(ShrinkStatus.REFUSED, str(exc), contract.contract_id, contract.target_spec)

    conflict = _conflicting_expectation(scan, contract)
    if conflict is not None:
        return _not_shrinkable(conflict, contract)

    return contract, contract_sha, target_ref, function


def preflight_contract(
    scan: RepositoryScan, request: ShrinkRequest
) -> tuple[Contract, str, TargetRef, FunctionInfo] | ShrinkResult:
    """Run the shared static gates and return either the prepared tuple or a refusal.

    Public so another input-changing operation -- semantic neighborhood search --
    can be bound by exactly the same checks instead of restating them. There is
    one contract parser, one target-shape check, one fingerprint comparison, and
    one conflict detector in this project; a second copy of any of them would
    eventually disagree with the first.
    """

    return _preflight(scan, request)


def _shrink_with_runner(
    scan: RepositoryScan, request: ShrinkRequest, runner: AttemptRunner
) -> ShrinkResult:
    """Run the search against a supplied runner, with the snapshot assumed verified.

    The static gates are the shared ones, so this path substitutes the *executor*
    only, never the validation.
    """

    prepared = _preflight(scan, request)
    if isinstance(prepared, ShrinkResult):
        return prepared
    contract, _contract_sha, target_ref, _function = prepared

    return _run_search(
        contract=contract,
        target_ref=target_ref,
        request=request,
        runner=runner,
        snapshot_verified=True,
    )


def _shrink_with_docker(
    scan: RepositoryScan,
    source_root: Path,
    request: ShrinkRequest,
    authorized: bool,
    repository_context: Path | None,
) -> ShrinkResult:
    """Run the search against real containers on one snapshot-fixed session."""

    prepared = _preflight(scan, request)
    if isinstance(prepared, ShrinkResult):
        return prepared
    contract, contract_sha, target_ref, _function = prepared

    try:
        session, sandbox = open_session(
            source_root,
            authorized=authorized,
            repository_context=repository_context or runtime_context(),
        )
    except ExecutionRefused as exc:
        return _refused(ShrinkStatus.REFUSED, str(exc), contract.contract_id, contract.target_spec)

    if session is None:
        return ShrinkResult(
            status=ShrinkStatus.INCONCLUSIVE,
            reason=f"no sandbox was available, so nothing was executed: {sandbox.detail}",
            target_identity=contract.target_spec,
            contract_id=contract.contract_id,
            contract_source=OracleSource.USER_DECLARED_CONTRACT,
            parameter=contract.parameter,
            seed_value=request.seed,
        )

    try:
        # Re-read the analyzed digest here: the preflight proved it equals the
        # contract's, and the staged bytes are now compared against *it*, which
        # is the Phase 6B snapshot rule reused rather than restated.
        snapshot = SourceSnapshot.verify(
            _analyzed_digest(scan, contract), session.staged_digests, contract.target_file
        )
        if not snapshot.verified:
            return ShrinkResult(
                status=ShrinkStatus.INCONCLUSIVE,
                reason=f"the staged source could not be proven to be the analyzed source: "
                f"{snapshot.detail}",
                target_identity=contract.target_spec,
                contract_id=contract.contract_id,
                parameter=contract.parameter,
                seed_value=request.seed,
            )
        runner = SessionAttemptRunner(
            session, target_ref, contract.parameter, request.bounded_timeout()
        )
        return _run_search(
            contract=contract,
            target_ref=target_ref,
            request=request,
            runner=runner,
            snapshot_verified=True,
            contract_sha=contract_sha,
        )
    finally:
        problem = session.close()
        if problem is not None:
            # Surfaced rather than swallowed: a host that still holds a staged
            # copy is not a clean host (D-055).
            print(f"warning: {problem}")


def _analyzed_digest(scan: RepositoryScan, contract: Contract) -> str | None:
    """The digest the single analysis pass recorded for the contract's file."""

    for module in scan.semantics.structure.modules:
        if module.relative_path.as_posix() == contract.target_file:
            return module.digest
    return None


def _run_search(
    *,
    contract: Contract,
    target_ref: TargetRef,
    request: ShrinkRequest,
    runner: AttemptRunner,
    snapshot_verified: bool,
    contract_sha: str = "",
) -> ShrinkResult:
    """The verified search: seed first, then bounded proposals, then acceptance."""

    policy = request.policy
    reproduction = policy.bounded_reproduction()
    seed_attempts = reproduction
    proposal_budget = policy.bounded_proposals()
    execution_budget = policy.bounded_executions()
    consumed = 0

    def result_of(
        status: ShrinkStatus,
        reason: str,
        *,
        best: int | None = None,
        best_signature: FailureSignature | None = None,
        attempts: tuple[ShrinkAttempt, ...] = (),
        considered: int = 0,
        exhausted: bool = False,
        seed_status: str = "",
        seed_signature: FailureSignature | None = None,
    ) -> ShrinkResult:
        return ShrinkResult(
            status=status,
            reason=reason,
            target_identity=contract.target_spec,
            contract_id=contract.contract_id,
            parameter=contract.parameter,
            seed_value=request.seed,
            seed_status=seed_status,
            seed_signature=seed_signature.describe() if seed_signature else "",
            best_value=best,
            best_signature=best_signature.describe() if best_signature else "",
            attempts=attempts,
            proposals_considered=considered,
            proposals_exhausted=exhausted,
            executions_consumed=consumed,
            policy=policy,
            snapshot_verified=snapshot_verified,
            contract_source=OracleSource.USER_DECLARED_CONTRACT,
        )

    # --- step 8: the seed must genuinely and repeatedly contradict the contract
    if seed_attempts > execution_budget:
        return result_of(
            ShrinkStatus.INCONCLUSIVE,
            f"the execution budget of {execution_budget} cannot cover the {seed_attempts} seed "
            "attempts required before shrinking may begin",
        )
    seed_results = runner.execute_attempts(request.seed, attempts=seed_attempts)
    consumed += len(seed_results)
    reproduced = _verify_reproduced(
        contract, seed_results, snapshot_verified=snapshot_verified, label="seed"
    )
    seed_signature = reproduced.signature
    if not reproduced.reproduced:
        return result_of(
            reproduced.status,
            reproduced.reason,
            seed_status="mismatch" if reproduced.equivalent else "",
            seed_signature=seed_signature,
        )

    # --- step 9: bounded, deterministic proposals
    proposals = propose_values(request.seed, contract.domain, proposal_budget)
    if not proposals:
        return result_of(
            ShrinkStatus.NO_SMALLER_CASE_FOUND,
            f"no admissible integer is strictly simpler than the seed {request.seed} under the "
            f"metric abs(x), so there is nothing to try",
            best=request.seed,
            best_signature=seed_signature,
            seed_status="mismatch",
            seed_signature=seed_signature,
            exhausted=True,
            considered=0,
        )

    best = request.seed
    best_signature = seed_signature
    attempts: list[ShrinkAttempt] = []
    considered = 0
    exhausted = True

    # A candidate is only ever compared against the *original* failure shape, and
    # a signature can only be established once an attempt actually produced one.
    # `_verify_reproduced` returning without a reason guarantees it, and this
    # guard makes that precondition explicit rather than implied.
    if seed_signature is None:
        return result_of(
            ShrinkStatus.INCONCLUSIVE,
            "the seed's failure signature could not be established, so no candidate has a "
            "failure character to preserve",
            best=request.seed,
            seed_status="mismatch",
            exhausted=False,
            considered=0,
        )

    for value in proposals:
        if consumed + reproduction > execution_budget:
            exhausted = False
            break
        if not is_simpler(value, best):
            attempts.append(
                ShrinkAttempt(
                    value=value,
                    origin=InvocationOrigin.INTEGER_SHRINK_PROPOSAL,
                    complexity=complexity(value),
                    in_domain=True,
                    executed=False,
                    outcome=AttemptOutcome.NOT_SIMPLER,
                    reason=f"abs({value}) is not smaller than the current best abs({best})",
                )
            )
            continue

        results = runner.execute_attempts(value, attempts=reproduction)
        consumed += len(results)
        considered += 1
        attempt = _judge_candidate(
            contract=contract,
            value=value,
            results=results,
            snapshot_verified=snapshot_verified,
            best=best,
            seed_signature=seed_signature,
            reproduction=reproduction,
        )
        attempts.append(attempt)
        if attempt.accepted and attempt.failure_signature:
            best = value
            best_signature = _signature_for(attempt, results)

    reduced = best != request.seed
    if reduced:
        status = ShrinkStatus.SHRUNK
        reason = (
            f"reduced {contract.parameter} from {request.seed} to {best} (abs {complexity(best)}), "
            f"every accepted value verified against the same contract on the same source snapshot"
        )
    elif exhausted:
        status = ShrinkStatus.NO_SMALLER_CASE_FOUND
        reason = (
            f"{considered} proposal(s) were executed and none qualified as an accepted reduction "
            f"of the seed {request.seed}"
        )
    else:
        status = ShrinkStatus.BUDGET_EXHAUSTED
        reason = (
            f"the execution budget of {execution_budget} was reached after {considered} "
            f"proposal(s); a budget stop is never reported as a successful reduction"
        )

    return result_of(
        status,
        reason,
        best=best,
        best_signature=best_signature,
        attempts=tuple(attempts),
        considered=considered,
        exhausted=exhausted,
        seed_status="mismatch",
        seed_signature=seed_signature,
    )


def _signature_for(
    attempt: ShrinkAttempt, results: tuple[ExecutionResult, ...]
) -> FailureSignature:
    """The signature recorded on an accepted attempt, rebuilt from its results."""

    for result in results:
        if result.observation is not None:
            signature = signature_of(result.observation)
            if signature is not None:
                return signature
    return FailureSignature(outcome="unknown", raised=False)


def _judge_candidate(
    *,
    contract: Contract,
    value: int,
    results: tuple[ExecutionResult, ...],
    snapshot_verified: bool,
    best: int,
    seed_signature: FailureSignature,
    reproduction: int,
) -> ShrinkAttempt:
    """Apply every acceptance check to one executed candidate.

    All ten conditions from the phase checklist are checked here, and the first
    failure becomes the recorded reason. Nothing is partially accepted: a
    candidate that fails any check is rejected with that reason, never
    downgraded to "probably fine".
    """

    def build(
        outcome: AttemptOutcome,
        reason: str,
        status: str = "",
        signature: str = "",
        matches: bool = False,
    ) -> ShrinkAttempt:
        return ShrinkAttempt(
            value=value,
            origin=InvocationOrigin.INTEGER_SHRINK_PROPOSAL,
            complexity=complexity(value),
            in_domain=contract.domain.contains(value),
            executed=bool(results),
            outcome=outcome,
            reason=reason,
            oracle_status=status,
            failure_signature=signature,
            signature_matches=matches,
            executions_consumed=len(results),
        )

    if not contract.domain.contains(value):
        return build(
            AttemptOutcome.OUTSIDE_DOMAIN,
            f"{value} does not satisfy the declared domain "
            f"({contract.parameter} {contract.domain.describe()})",
        )
    if not results:
        return build(AttemptOutcome.INFRASTRUCTURE_FAILURE, "the attempt produced no result")
    if not is_simpler(value, best):
        return build(
            AttemptOutcome.NOT_SIMPLER,
            f"abs({value}) is not smaller than the current best abs({best})",
        )

    signatures: list[FailureSignature] = []
    for result in results:
        verdict = evaluate_against_contract(contract, result, snapshot_verified=snapshot_verified)
        if verdict.status is OracleStatus.INCONCLUSIVE:
            return build(
                AttemptOutcome.INFRASTRUCTURE_FAILURE,
                f"the attempt could not be decided: {verdict.reason}",
                status=verdict.status.value,
            )
        if verdict.status is OracleStatus.UNSUPPORTED:
            return build(
                AttemptOutcome.ORACLE_NOT_APPLICABLE,
                f"the contract could not be applied: {verdict.reason}",
                status=verdict.status.value,
            )
        if verdict.status is not OracleStatus.MISMATCH:
            if signatures:
                # An earlier attempt did contradict the contract, so this is
                # flakiness, which is a different fact from "not a counterexample".
                return build(
                    AttemptOutcome.NOT_REPRODUCIBLE,
                    f"attempt {len(signatures) + 1} was {verdict.status.value} while an earlier "
                    f"attempt contradicted the contract ({verdict.reason}); a candidate that "
                    "succeeds once is not a verified reduction",
                    status=verdict.status.value,
                )
            return build(
                AttemptOutcome.ORACLE_NOT_MISMATCH,
                f"the contract was {verdict.status.value}, so this is not a counterexample",
                status=verdict.status.value,
            )
        signature = signature_of(result.observation) if result.observation else None
        if signature is None:
            return build(
                AttemptOutcome.INFRASTRUCTURE_FAILURE,
                f"the attempt ended as {result.outcome.value}, which has no comparable failure",
                status=verdict.status.value,
            )
        signatures.append(signature)

    described = signatures[0].describe()
    if any(not signatures[0].matches(other) for other in signatures[1:]):
        return build(
            AttemptOutcome.NOT_REPRODUCIBLE,
            f"attempts disagreed with each other ({described} vs {signatures[1].describe()})",
            status=OracleStatus.MISMATCH.value,
            signature=described,
        )
    if len(signatures) < reproduction:
        return build(
            AttemptOutcome.NOT_REPRODUCIBLE,
            f"only {len(signatures)} of {reproduction} required attempts were usable",
            status=OracleStatus.MISMATCH.value,
            signature=described,
        )
    if not seed_signature.matches(signatures[0]):
        return build(
            AttemptOutcome.SIGNATURE_MISMATCH,
            f"the observed failure changed character: the original was "
            f"{seed_signature.describe()} but {value} produced {described}; a different failure is "
            "not a reduction of this one",
            status=OracleStatus.MISMATCH.value,
            signature=described,
        )

    return build(
        AttemptOutcome.ACCEPTED,
        f"abs({value}) is smaller than abs({best}) and the same failure {described} reproduced "
        f"{len(signatures)}/{reproduction} times against the same contract",
        status=OracleStatus.MISMATCH.value,
        signature=described,
        matches=True,
    )


# ---------------------------------------------------------------------------
# Evidence artifact
# ---------------------------------------------------------------------------


def shrink_artifact_document(result: ShrinkResult, *, contract_sha: str) -> dict[str, object]:
    """Build the versioned, evidence-only JSON document for one operation.

    Deliberately excludes captured target stdout, stderr, and exception
    messages, exactly as the Phase 6B artifact does: this file is meant for
    review, and it must not become the place secrets accumulate. Nothing here is
    a serialized Python object.
    """

    return {
        "schema_version": SHRINK_ARTIFACT_SCHEMA,
        "kind": "counterexample_minimization",
        "target_identity": result.target_identity,
        "contract": {
            "id": result.contract_id,
            "source": result.contract_source.value,
            "file_sha256": contract_sha,
            "provenance": (
                "user-declared JSON contract, explicitly selected on the command line; this is an "
                "expectation the user asserts, not a fact proven from the repository"
            ),
        },
        "parameter": result.parameter,
        "seed": {
            "value": result.seed_value,
            "origin": InvocationOrigin.USER_SUPPLIED_SEED.value,
            "contract_status": result.seed_status,
            "failure_signature": result.seed_signature,
        },
        "reduced": {
            "value": result.best_value,
            "failure_signature": result.best_signature,
            "found_by_bounded_search": True,
        },
        "status": result.status.value,
        "reason": result.reason,
        "snapshot_verified": result.snapshot_verified,
        "policy": {
            "max_proposals": result.policy.bounded_proposals(),
            "max_executions": result.policy.bounded_executions(),
            "reproduction_attempts": result.policy.bounded_reproduction(),
            "metric": "abs(value)",
            "proposals_considered": result.proposals_considered,
            "proposals_exhausted": result.proposals_exhausted,
            "executions_consumed": result.executions_consumed,
        },
        "attempts": [
            {
                "value": attempt.value,
                "origin": attempt.origin.value,
                "complexity": attempt.complexity,
                "in_domain": attempt.in_domain,
                "executed": attempt.executed,
                "outcome": attempt.outcome.value,
                "reason": attempt.reason,
                "contract_status": attempt.oracle_status,
                "failure_signature": attempt.failure_signature,
                "signature_matches": attempt.signature_matches,
                "executions_consumed": attempt.executions_consumed,
            }
            for attempt in result.attempts
        ],
        "meaning": (
            "Reproduced mismatch against the same user-declared contract, on one source snapshot, "
            "bounded search. Not a universal proof of a software defect, and not a statement of "
            "global minimality."
        ),
    }


def write_shrink_artifact(
    result: ShrinkResult, *, contract_sha: str, run_id: str, base: Path | None = None
) -> Path:
    """Write the artifact under ``.edgehunter/runs/<run-id>/shrink.json``.

    The run id is sanitized with the *same* rule the Phase 6B artifact uses, so
    a crafted id cannot write outside the artifact directory by any different
    route, and the whole tree is Git-ignored.
    """

    from edgehunter.evaluation.repository import ARTIFACT_DIRECTORY, _safe_run_id

    # `ARTIFACT_DIRECTORY` is already `.edgehunter/runs`, so only the run id and
    # the file name are appended.
    root = (base or Path(*PurePosixPath(ARTIFACT_DIRECTORY).parts)) / _safe_run_id(run_id)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "shrink.json"
    document = shrink_artifact_document(result, contract_sha=contract_sha)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8"
    )
    return path
