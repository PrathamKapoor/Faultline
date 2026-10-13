"""Command-line interface for EdgeHunter.

This module is a thin presentation layer: it parses user input, delegates to
the library modules, and formats their results. Analysis logic stays out of
this module so it can be tested without going through the command line.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Final

import typer

from edgehunter import __version__
from edgehunter.evaluation.models import EvaluationReport, ReproductionStatus
from edgehunter.evaluation.repository import HARD_MAX_RECHECKS, evaluate_run, target_functions
from edgehunter.execution.models import RunReport
from edgehunter.execution.repository import (
    ExecutionRefused,
    RunOptions,
    execute_target,
    runtime_context,
)
from edgehunter.generation.invocation import ArgumentAssignment, ArgumentPlacement
from edgehunter.generation.models import MaterializationStatus, SemanticAction
from edgehunter.oracle.models import (
    ProductionPropertyCandidate,
    PropertyKind,
    TestExpectationCandidate,
)
from edgehunter.pipeline import RepositoryScan, analyze_repository
from edgehunter.scanner import RepositoryAnalysis, ScanError
from edgehunter.scanner.models import FunctionInfo
from edgehunter.search import (
    DEFAULT_MAX_EXECUTIONS as DEFAULT_SEARCH_MAX_EXECUTIONS,
)
from edgehunter.search import (
    DEFAULT_MAX_PROBES,
    ExploreRequest,
    PolicyName,
    SearchBudgets,
    SearchResult,
    SearchTermination,
    SelectionUse,
    explore_integer,
    write_search_artifact,
)
from edgehunter.shrink import (
    DEFAULT_MAX_EXECUTIONS,
    DEFAULT_MAX_PROPOSALS,
    ShrinkPolicy,
    ShrinkRequest,
    ShrinkResult,
    ShrinkStatus,
    shrink_integer,
    write_shrink_artifact,
)

EXIT_SCAN_ISSUES = 1
EXIT_INVALID_INPUT = 2
EXIT_EXECUTION_REFUSED = 3
"""`run` uses its own code: nothing executed at all.

Exit code 1 is deliberately never used by `run`, so it cannot be mistaken for
"a bug was found". This phase performs no oracle evaluation, so there is no such
outcome to report.
"""

EXIT_SHRINK_COMPLETED = 0
"""`shrink` completed and the structured result says what happened.

Never "a counterexample was found". The exit code is not a verdict: whether a
mismatch was minimized is carried by the result's own status, which is printed.
"""

app = typer.Typer(
    name="edgehunter",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"edgehunter {__version__}")
        raise typer.Exit()


@app.callback()
def _root_callback(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the EdgeHunter version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Oracle-aware counterexample search engine for Python."""


@app.command()
def run(
    path: Annotated[
        Path,
        typer.Argument(
            metavar="PATH", help="Directory containing the target, or a single Python file."
        ),
    ],
    target: Annotated[
        str,
        typer.Option(
            "--target",
            help="Qualified target to execute, e.g. 'divide' or 'app.py::divide'.",
        ),
    ],
    allow_execution: Annotated[
        bool,
        typer.Option(
            "--allow-execution",
            help="Authorize running untrusted target code inside a Docker container.",
        ),
    ] = False,
    max_cases: Annotated[
        int,
        typer.Option("--max-cases", help="Maximum number of eligible candidates to execute."),
    ] = 5,
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Per-invocation wall-clock budget in seconds."),
    ] = 3.0,
    evaluate: Annotated[
        bool,
        typer.Option(
            "--evaluate",
            help=(
                "Compare each observation against the explicit expectations the repository "
                "itself records for that exact invocation."
            ),
        ),
    ] = False,
    recheck: Annotated[
        int,
        typer.Option(
            "--recheck",
            help=(
                "Re-execute each candidate this many extra times to test whether a "
                "contradiction reproduces. Bounded; never automatic. Requires --evaluate."
            ),
        ),
    ] = 0,
) -> None:
    """Execute selected candidate inputs for ONE target inside a Docker sandbox.

    Execution is never implicit: `scan` never runs target code, and this command
    requires both an explicit ``--target`` and ``--allow-execution``.

    Target code is imported and called only inside a restricted container with no
    network, a read-only root filesystem, dropped privileges, and bounded
    time/memory/process limits. If Docker cannot be used, nothing is executed.

    Without ``--evaluate`` this command only reports what happened. With
    ``--evaluate`` it also checks each observation against the repository's own
    explicit expectations for that exact input, and reports a contradiction when
    one is genuinely contradicted. An exception on its own is never a finding, and
    no severity is assigned.
    """
    if not allow_execution:
        typer.secho(
            "error: execution requires explicit authorization; pass --allow-execution",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=EXIT_EXECUTION_REFUSED)

    try:
        analysis = analyze_repository(path)
    except ScanError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=EXIT_INVALID_INPUT) from exc

    resolved, target_error = _resolve_target(analysis, target)
    if resolved is None:
        typer.secho(f"error: {target_error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=EXIT_INVALID_INPUT)

    evaluation: tuple[EvaluationReport, ...] = ()
    if evaluate:
        requested = _bounded_recheck(recheck)
        try:
            report = execute_target(
                analysis,
                path if path.is_dir() else path.parent,
                RunOptions(
                    target=resolved,
                    max_cases=max_cases,
                    timeout_seconds=timeout,
                    rechecks=requested,
                ),
                authorized=True,
                repository_context=runtime_context(),
            )
        except ExecutionRefused as exc:
            typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=EXIT_EXECUTION_REFUSED) from exc

        _print_execution(report, evaluate=evaluate)
        evaluation = evaluate_run(
            report,
            scan=analysis,
            functions=_target_functions(analysis, report),
            requested_rechecks=requested,
        )
        _print_evaluation(evaluation)
        if not report.executed_count:
            raise typer.Exit(code=EXIT_EXECUTION_REFUSED)
        return

    try:
        report = execute_target(
            analysis,
            path if path.is_dir() else path.parent,
            RunOptions(target=resolved, max_cases=max_cases, timeout_seconds=timeout),
            authorized=True,
            repository_context=runtime_context(),
        )
    except ExecutionRefused as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=EXIT_EXECUTION_REFUSED) from exc

    _print_execution(report)
    if not report.executed_count:
        raise typer.Exit(code=EXIT_EXECUTION_REFUSED)


@app.command()
def shrink(
    path: Annotated[
        Path,
        typer.Argument(metavar="PATH", help="Directory containing the target to minimize."),
    ],
    target: Annotated[
        str,
        typer.Option(
            "--target",
            help=(
                "Target to minimize as 'relative/path.py::qualified_name'. Must match the "
                "contract's bound target exactly; a contract is never redirected."
            ),
        ),
    ],
    contract_file: Annotated[
        Path,
        typer.Option(
            "--contract-file",
            help=(
                "JSON file of user-declared contracts. Never discovered automatically: this "
                "option is the only way a contract is selected."
            ),
        ),
    ],
    contract_id: Annotated[
        str,
        typer.Option("--contract-id", help="Id of the one contract to apply."),
    ],
    start_int: Annotated[
        int,
        typer.Option(
            "--start-int",
            help=(
                "The integer to start from. Must satisfy the contract's domain; it is recorded "
                "as a user-supplied seed, not as a discovered action."
            ),
        ),
    ],
    allow_execution: Annotated[
        bool,
        typer.Option(
            "--allow-execution",
            help="Authorize running untrusted target code inside a Docker container.",
        ),
    ] = False,
    max_attempts: Annotated[
        int,
        typer.Option("--max-attempts", help="Maximum shrink proposals to execute (bounded)."),
    ] = DEFAULT_MAX_PROPOSALS,
    max_executions: Annotated[
        int,
        typer.Option(
            "--max-executions", help="Hard cap on total containers for this operation (bounded)."
        ),
    ] = DEFAULT_MAX_EXECUTIONS,
    timeout: Annotated[
        float, typer.Option("--timeout", help="Per-invocation wall-clock budget in seconds.")
    ] = 3.0,
    artifact_id: Annotated[
        str,
        typer.Option(
            "--artifact-id",
            help="Write the evidence artifact under .edgehunter/runs/<id>/shrink.json.",
        ),
    ] = "",
) -> None:
    """Minimize ONE integer counterexample against ONE user-declared contract.

    An exact test expectation cannot be shrunk: ``assert f(100) == 200`` describes
    one invocation, so changing the input invalidates the oracle. A *contract* is
    different -- it declares a condition over a range of inputs, so it keeps
    applying as the input shrinks. This command requires one explicitly selected
    contract file, one contract id, one starting integer, and ``--allow-execution``.

    The search is bounded and deterministic, runs entirely in the same hardened
    Docker sandbox as ``run`` against one staged source snapshot, and reports the
    smallest input it *verified*. It never claims a global minimum, and a result
    is a reproduced mismatch against a user declaration, not a proven defect.
    """
    if not allow_execution:
        typer.secho(
            "error: shrinking requires explicit authorization; pass --allow-execution",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=EXIT_EXECUTION_REFUSED)

    try:
        analysis = analyze_repository(path)
    except ScanError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=EXIT_INVALID_INPUT) from exc

    request = ShrinkRequest(
        contract_path=contract_file,
        contract_id=contract_id,
        seed=start_int,
        target_spec=target,
        policy=ShrinkPolicy(max_proposals=max_attempts, max_executions=max_executions),
        timeout_seconds=timeout,
    )
    try:
        result = shrink_integer(
            analysis,
            path if path.is_dir() else path.parent,
            request,
            authorized=True,
            repository_context=runtime_context(),
        )
    except ExecutionRefused as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=EXIT_EXECUTION_REFUSED) from exc

    _print_shrink(result)

    if artifact_id:
        try:
            written = write_shrink_artifact(
                result, contract_sha=_contract_digest(contract_file), run_id=artifact_id
            )
        except (OSError, ValueError) as exc:
            typer.secho(f"error: the artifact could not be written: {exc}", fg=typer.colors.RED)
        else:
            typer.echo("")
            typer.echo(f"Evidence artifact: {written.as_posix()}")

    # A contract that is missing, invalid, out of domain, or bound to changed
    # source is invalid user input, not an execution failure.
    if result.status is ShrinkStatus.REFUSED:
        raise typer.Exit(code=EXIT_INVALID_INPUT)
    if result.status is ShrinkStatus.INCONCLUSIVE:
        raise typer.Exit(code=EXIT_EXECUTION_REFUSED)
    raise typer.Exit(code=EXIT_SHRINK_COMPLETED)


@app.command()
def explore(
    path: Annotated[
        Path,
        typer.Argument(metavar="PATH", help="Directory containing the target to search around."),
    ],
    target: Annotated[
        str,
        typer.Option(
            "--target",
            help=(
                "Target to search as 'relative/path.py::qualified_name'. Must match the "
                "contract's bound target exactly."
            ),
        ),
    ],
    contract_file: Annotated[
        Path,
        typer.Option(
            "--contract-file",
            help=(
                "JSON file of user-declared contracts. Never discovered automatically, and its "
                "presence is never authorization: --allow-execution is still required."
            ),
        ),
    ],
    contract_id: Annotated[
        str, typer.Option("--contract-id", help="Id of the one contract to search against.")
    ],
    start_int: Annotated[
        int,
        typer.Option(
            "--start-int",
            help=(
                "Starting integer. Must satisfy the contract's declared domain; recorded as a "
                "user-supplied seed, not as a discovered action."
            ),
        ),
    ],
    allow_execution: Annotated[
        bool,
        typer.Option(
            "--allow-execution", help="Authorize running untrusted target code in Docker."
        ),
    ] = False,
    policy: Annotated[
        str,
        typer.Option(
            "--policy",
            help=(
                "'feedback' selects the next input from the previous verdict; 'static' selects it "
                "from a fixed order that ignores runtime feedback. Both share one candidate "
                "universe and one budget."
            ),
        ),
    ] = "feedback",
    max_probes: Annotated[
        int,
        typer.Option("--max-probes", help="Maximum distinct inputs to execute (bounded)."),
    ] = DEFAULT_MAX_PROBES,
    max_executions: Annotated[
        int,
        typer.Option(
            "--max-executions",
            help="Hard cap on total containers, including verification reruns (bounded).",
        ),
    ] = DEFAULT_SEARCH_MAX_EXECUTIONS,
    timeout: Annotated[
        float, typer.Option("--timeout", help="Per-invocation wall-clock budget in seconds.")
    ] = 3.0,
    artifact_id: Annotated[
        str,
        typer.Option(
            "--artifact-id",
            help="Write the evidence artifact under .edgehunter/runs/<id>/search.json.",
        ),
    ] = "",
) -> None:
    """Search around ONE integer input for a reproducible contract contradiction.

    This is the first command whose next input depends on what the previous input
    did. An exact test expectation still cannot drive it -- ``assert f(100) ==
    200`` says nothing about ``f(10)`` -- so a search requires an explicitly
    selected user-declared contract, a starting integer inside that contract's
    domain, and ``--allow-execution``.

    The search is bounded, deterministic, and snapshot-fixed, and it reports the
    smallest number of probes it actually used rather than any claim about how
    good the policy is. A reproduced contradiction is a reproduced contradiction
    of one user declaration; it is not a confirmed defect, and a search that
    found nothing is not a statement that the target is correct.
    """
    if not allow_execution:
        typer.secho(
            "error: searching requires explicit authorization; pass --allow-execution",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=EXIT_EXECUTION_REFUSED)

    try:
        analysis = analyze_repository(path)
    except ScanError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=EXIT_INVALID_INPUT) from exc

    try:
        policy_name = PolicyName.parse(policy)
    except ValueError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=EXIT_INVALID_INPUT) from exc

    budgets = SearchBudgets(max_probes=max_probes, max_executions=max_executions)
    request = ExploreRequest(
        contract_path=contract_file,
        contract_id=contract_id,
        start_value=start_int,
        target_spec=target,
        policy_name=policy_name,
        budgets=budgets,
        timeout_seconds=timeout,
    )
    try:
        result = explore_integer(
            analysis,
            path if path.is_dir() else path.parent,
            request,
            authorized=True,
            repository_context=runtime_context(),
        )
    except ExecutionRefused as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=EXIT_EXECUTION_REFUSED) from exc

    _print_search(result)

    if artifact_id:
        try:
            written = write_search_artifact(result, run_id=artifact_id)
        except (OSError, ValueError) as exc:
            typer.secho(f"error: the artifact could not be written: {exc}", fg=typer.colors.RED)
        else:
            typer.echo("")
            typer.echo(f"Evidence artifact: {written.as_posix()}")

    # A missing contract, a bad fingerprint, an out-of-domain start value, or an
    # ineligible target is invalid user input. An unavailable backend is a
    # refusal to execute. Neither is ever reported as a search result.
    if result.termination is SearchTermination.REFUSED:
        raise typer.Exit(code=EXIT_INVALID_INPUT)
    if result.termination is SearchTermination.SANDBOX_UNAVAILABLE:
        raise typer.Exit(code=EXIT_EXECUTION_REFUSED)
    raise typer.Exit(code=EXIT_SHRINK_COMPLETED)


def _print_search(result: SearchResult) -> None:
    """Print one search result: what was tried, why, and what it does not mean."""

    typer.echo("EdgeHunter explore")
    typer.echo("")
    typer.echo("  Budgeted semantic neighborhood search around one integer input.")
    typer.echo("  The contract is an expectation you assert, not a proven fact.")
    typer.echo("")
    typer.echo(f"  Target:          {result.target_identity or '(unresolved)'}")
    typer.echo(f"  Contract:        {result.contract_id or '(none)'}")
    typer.echo("  Oracle source:   user_declared_contract")
    typer.echo(f"  Parameter:       {result.parameter or '(none)'}")
    typer.echo(f"  Policy:          {result.policy.value}")
    typer.echo(f"  Source snapshot: {'verified' if result.snapshot_verified else 'not verified'}")
    typer.echo(
        f"  Budget:          {result.budgets.bounded_probes()} distinct probes, "
        f"{result.budgets.bounded_executions()} total executions, "
        f"{result.budgets.bounded_wall_clock()}s wall clock"
    )
    typer.echo("")

    for step in result.steps:
        use = "recheck" if step.use is SelectionUse.VERIFICATION_RERUN else "probe"
        typer.echo(
            f"  Step {step.step} ({use})  {step.action.kind.value}  "
            f"{step.action.parameter} = {step.action.value}"
        )
        typer.echo(f"    Origin:   {step.action.origin.value}")
        typer.echo(f"    Why:      {step.selection_reason}")
        typer.echo(f"    Observed: {step.observation_description}")
        typer.echo(f"    Oracle:   {step.oracle_status}  ->  feedback {step.feedback.value}")
        if step.reproduction_detail:
            typer.echo(f"    Recheck:  {step.reproduction_detail}")
        typer.echo("")

    # A refusal that never ran has no measurements worth printing: a table of
    # zeroes beside "Result: refused" reads like a search that found nothing.
    if result.steps or result.executions:
        typer.echo("  Measurements")
        typer.echo(f"    Candidate universe:  {result.universe_size}")
        typer.echo(f"    Distinct probes:     {result.distinct_probes}")
        typer.echo(f"    Docker executions:   {result.executions}")
        typer.echo(f"    Recheck executions:  {result.reproduction_executions}")
        typer.echo(f"    Expected behaviour:  {result.match_count}")
        typer.echo(f"    Mismatches:          {result.mismatch_count}")
        typer.echo(f"    Inconclusive:        {result.inconclusive_count}")
        typer.echo(f"    First mismatch at:   {_index_text(result.first_mismatch_probe)}")
        typer.echo(f"    First reproduced at: {_index_text(result.first_reproduced_probe)}")
        typer.echo("")

    typer.echo(f"  Result: {result.termination.value}")
    typer.echo(f"  Reason: {result.reason}")
    typer.echo("")
    typer.echo("  Meaning:")
    if result.found_violation:
        typer.echo("    Two independent executions of one input contradicted the same")
        typer.echo("    user-declared contract on one source snapshot.")
    else:
        typer.echo("    No reproduced contradiction was found within the probes this")
        typer.echo("    bounded search actually ran. That is not a statement that the")
        typer.echo("    target is correct, and no coverage is claimed.")
    typer.echo("    Not a universally confirmed defect, and not a calibrated claim about")
    typer.echo("    how effective this policy is.")


def _index_text(value: int | None) -> str:
    """Render a probe index, keeping 'never happened' visibly distinct."""

    return "never" if value is None else f"probe {value}"


def _contract_digest(path: Path) -> str:
    """The contract file's own fingerprint, recorded in the artifact.

    Falls back to a recorded placeholder when the file is unreadable, which can
    only happen for a result that is already a refusal carrying its own reason.
    """

    from edgehunter.fingerprint import fingerprint_file

    try:
        return fingerprint_file(path)
    except OSError:
        return "unavailable"


def _print_shrink(result: ShrinkResult) -> None:
    """Print one minimization result, with provenance visible and no overstatement."""

    typer.echo("EdgeHunter shrink")
    typer.echo("")
    typer.echo("  Bounded minimization of one integer against one user-declared contract.")
    typer.echo("  A contract is an expectation the user asserts, not a proven fact.")
    typer.echo("")
    typer.echo(f"  Target:          {result.target_identity or '(unresolved)'}")
    typer.echo(f"  Contract:        {result.contract_id or '(none)'}")
    typer.echo(f"  Oracle source:   {result.contract_source.value}")
    typer.echo(f"  Parameter:       {result.parameter or '(none)'}")
    typer.echo(f"  Seed:            {result.parameter} = {result.seed_value} [user_supplied_seed]")
    typer.echo(f"  Source snapshot: {'verified' if result.snapshot_verified else 'not verified'}")
    typer.echo("")

    if result.attempts:
        typer.echo("  Shrink proposals")
        typer.echo("")
        for attempt in result.attempts:
            typer.echo(
                f"    {result.parameter} = {attempt.value:<8} abs={attempt.complexity:<8} "
                f"{attempt.outcome.value}"
            )
            typer.echo(f"        {attempt.reason}")
            if attempt.signature_matches:
                typer.echo(f"        failure signature: {attempt.failure_signature}")
        typer.echo("")

    typer.echo(f"  Proposals executed: {result.proposals_considered}")
    typer.echo(
        f"  Proposal list exhausted: {'yes' if result.proposals_exhausted else 'no (budget stop)'}"
    )
    typer.echo(f"  Containers used:   {result.executions_consumed}")
    typer.echo("")

    if result.best_value is not None:
        verb = "Reduced" if result.reduced else "Smallest verified input"
        typer.echo(f"  {verb}: {result.parameter} = {result.best_value}")
        if result.best_signature:
            typer.echo(f"  Failure signature:  {result.best_signature}")
    typer.echo("")
    typer.echo(f"  Result: {result.status.value}")
    typer.echo(f"  Reason: {result.reason}")
    typer.echo("")
    typer.echo("  Meaning:")
    typer.echo("    Reproduced mismatch against the same user-declared contract, on one")
    typer.echo("    source snapshot, within a bounded deterministic search.")
    typer.echo("    Not a universal proof of a software defect, and not a statement that")
    typer.echo("    this input is the smallest possible counterexample.")


def _resolve_target(analysis: RepositoryScan, spec: str) -> tuple[str | None, str | None]:
    """Resolve ``name`` or ``path.py::name`` to exactly one known target.

    The same identity rules the execution layer applies are used here, so an
    ambiguous name is reported as invalid input before Docker is ever consulted
    instead of silently resolving to whichever module was scanned first.
    """
    file_hint, _, qualified = spec.rpartition("::")
    file_hint = file_hint.strip()
    qualified = qualified.strip()
    if not qualified:
        return None, f"no target name was given in {spec!r}"

    matches = [
        module.relative_path
        for module in analysis.semantics.structure.modules
        for function in module.functions
        if function.qualified_name == qualified
    ]
    if file_hint:
        wanted = file_hint.replace("\\", "/")
        matches = [
            relative
            for relative in matches
            if relative.as_posix() == wanted
            or relative.name == wanted
            or relative.as_posix().endswith(f"/{wanted}")
        ]

    if not matches:
        suffix = f" in {file_hint!r}" if file_hint else ""
        return None, f"no analyzed target matched {qualified!r}{suffix}"
    if len(matches) > 1:
        where = ", ".join(sorted(relative.as_posix() for relative in matches))
        return None, (
            f"{qualified!r} is defined in more than one file ({where}); "
            f"select it with --target 'path::{qualified}'"
        )
    # The file hint is carried through so the execution layer resolves the same
    # identity instead of being handed a bare, ambiguous name.
    return (f"{file_hint}::{qualified}" if file_hint else qualified), None


def _bounded_recheck(requested: int) -> int:
    """Clamp the recheck count, refusing a negative request outright."""
    if requested < 0:
        typer.secho("error: --recheck must be zero or greater", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=EXIT_INVALID_INPUT)
    return min(requested, HARD_MAX_RECHECKS)


def _target_functions(analysis: RepositoryScan, report: RunReport) -> dict[str, FunctionInfo]:
    """The analyzed function behind each executed target, looked up by identity.

    Reads the single parse the scan already performed. No module is re-read and no
    source is re-parsed here.
    """
    functions: dict[str, FunctionInfo] = {}
    for result in report.results:
        if result.target.qualified_name in functions:
            continue
        function = target_functions(analysis, result.target.identity)
        if function is not None:
            functions[result.target.qualified_name] = function
    return functions


def _print_evaluation(reports: tuple[EvaluationReport, ...]) -> None:
    """Print the oracle evaluation for every executed candidate.

    Output never says a bug was confirmed. A contradiction is described as a
    contradiction with one explicit expectation, and bug severity is explicitly
    reported as not assessed, because nothing here is calibrated.
    """
    typer.echo("EdgeHunter — Oracle Evaluation")
    typer.echo("")

    for report in reports:
        _print_evaluation_report(report)

    typer.echo("  Bug severity: not assessed")
    typer.echo("  (an expectation mismatch is evidence of disagreement with one explicit")
    typer.echo("   expectation; it is not by itself a confirmed software defect)")
    typer.echo("")


def _print_evaluation_report(report: EvaluationReport) -> None:
    primary = report.primary
    typer.echo(f"  Target: {report.target_identity}")
    typer.echo(f"  Execution: {report.execution_outcome.value.upper()}")
    if report.source_digest is not None:
        typer.echo(f"  Source snapshot: {report.source_digest[:12]} (verified)")
    else:
        typer.echo("  Source snapshot: not verified")

    if primary is None:
        typer.echo("  Applicable oracle: none")
        typer.echo("  Evaluation: INCONCLUSIVE")
        typer.echo("  Finding: None")
        typer.echo("")
        return

    typer.echo(f"  Invoked: {_invocation_text(report)}")
    typer.echo(f"  Observed: {primary.observed_description or '(not described)'}")
    typer.echo(f"  Oracle: {primary.oracle.value.replace('_', ' ')}")
    typer.echo(f"  Expected: {primary.expected_description or '(none recorded)'}")
    typer.echo(f"  Evidence: {primary.describe_location()}")
    typer.echo(f"  Applicability: {primary.applicability_kind.value.upper()}")
    typer.echo(f"  Evaluation: {primary.status.value.upper()}")

    if len(report.executions) > 1:
        typer.echo("  All applicable oracles:")
        for entry in report.executions:
            location = entry.describe_location()
            typer.echo(f"    {entry.status.value.upper():<22} {location}")
            typer.echo(f"      {entry.reason}")

    _print_reproduction(report)
    _print_findings(report)
    typer.echo("")


def _invocation_text(report: EvaluationReport) -> str:
    """The exact call that was executed, in canonical parameter bindings."""
    if report.invocation is None:
        return "(no invocation recorded)"
    return report.invocation.render()


def _print_reproduction(report: EvaluationReport) -> None:
    summary = report.reproduction
    if summary.status is ReproductionStatus.NOT_RECHECKED:
        typer.echo("  Reproduction: Not rechecked")
        return
    if summary.status is ReproductionStatus.REPRODUCED:
        typer.echo(
            f"  Reproduction: {summary.equivalent_mismatches} / {summary.attempts} "
            "equivalent mismatches"
        )
        return
    if summary.status is ReproductionStatus.FLAKY:
        typer.echo(
            f"  Reproduction: FLAKY ({summary.equivalent_mismatches} of {summary.attempts} "
            "attempts agreed; the behaviour is not stable and is not reported as a defect)"
        )
        return
    typer.echo(
        f"  Reproduction: RECHECK_FAILED ({summary.failures} of {summary.attempts} attempts "
        "could not be evaluated; an environment failure is not confirmation)"
    )


def _print_findings(report: EvaluationReport) -> None:
    if not report.findings:
        typer.echo("  Finding: None")
        return
    for finding in report.findings:
        typer.echo(f"  Finding: {finding.kind.value.replace('_', ' ').title()}")
        typer.echo(f"    id: {finding.finding_id}")
        typer.echo(f"    oracle: {finding.oracle_location}")
        typer.echo(f"    expected: {finding.expected_behavior}")
        typer.echo(f"    observed: {finding.observed_behavior}")
        if finding.primary_action:
            typer.echo(f"    action: {finding.primary_action}")
        for provenance in finding.assignment_provenance:
            typer.echo(f"      {provenance}")
        if finding.reproduction_command:
            typer.echo(f"    reproduce: {finding.reproduction_command}")
        typer.echo(f"    detail: {finding.detail}")


def _print_execution(report: RunReport, *, evaluate: bool = False) -> None:
    """Print one structured report; no outcome is described as a defect."""
    typer.echo("EdgeHunter execution")
    typer.echo("")
    typer.echo(f"  Sandbox: {report.sandbox.backend} ({report.sandbox.state.value})")
    if report.sandbox.detail:
        typer.echo(f"  {report.sandbox.detail}")
    if evaluate:
        typer.echo("  Oracle evaluation: requested; reported below")
        typer.echo("  Finding: reported by the oracle evaluation below")
    else:
        typer.echo("  Oracle evaluation: not performed")
        typer.echo("  Finding: none (this phase only observes)")
    typer.echo("")

    if not report.sandbox.is_usable:
        typer.echo("  Execution unavailable: no target code was executed.")
        typer.echo("")
        _print_skipped(report)
        return

    for result in report.results:
        typer.echo(f"  Target: {result.target.identity}")
        typer.echo(f"  Action: {result.action_id}")
        if result.assignments:
            typer.echo("    Arguments:")
            for item in result.assignments:
                typer.echo(
                    f"      {item.parameter} = {_render_planned(item)}  [{item.origin.value}]"
                )
        typer.echo(f"  Outcome: {result.outcome.value.upper()}")
        observation = result.observation
        if observation is not None:
            returned = observation.return_description
            if returned is not None:
                typer.echo(f"  Returned:  {returned}")
            if observation.exception_type is not None:
                typer.echo(f"  Exception: {observation.exception_type}")
                if observation.exception_message:
                    typer.echo(f"  Message:   {observation.exception_message[:200]}")
            if observation.opaque_return is not None:
                typer.echo(f"  Returned:  {observation.opaque_return}")
            if observation.captured_stdout:
                typer.echo(f"  Stdout:    {_first_line(observation.captured_stdout)}")
                if observation.stdout_truncated:
                    typer.echo("             (truncated)")
        if result.detail:
            typer.echo(f"  Detail:    {result.detail}")
        if result.duration_seconds is not None:
            typer.echo(f"  Duration:  {result.duration_seconds:.3f}s")
        typer.echo("")

    for refusal in report.skipped:
        reason = refusal.reason.value if refusal.reason is not None else "unknown"
        typer.echo(f"  Skipped: {reason}: {refusal.detail}")
    if report.skipped:
        typer.echo("")


def _print_skipped(report: RunReport) -> None:
    """Report every refused plan with its reason, even when nothing ran."""
    if not report.skipped:
        return
    typer.echo("  No eligible plan for the selected target:")
    for refusal in report.skipped:
        reason = refusal.reason.value if refusal.reason is not None else "unknown"
        typer.echo(f"    {reason}: {refusal.detail}")
    typer.echo("")


def _render_planned(item: ArgumentAssignment) -> str:
    """Render a planned argument, showing that an omitted default carries no value."""
    if item.value_text is not None:
        return item.value_text
    if item.placement is ArgumentPlacement.OMITTED:
        return "(declared default)"
    return "(unresolved)"


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0][:200] if text.strip() else ""


@app.command()
def scan(
    path: Annotated[
        Path,
        typer.Argument(metavar="PATH", help="Directory to scan, or a single Python file."),
    ],
    targets: Annotated[
        bool,
        typer.Option("--targets", help="List discovered callable targets."),
    ] = False,
    boundaries: Annotated[
        bool,
        typer.Option("--boundaries", help="List boundary candidates per target."),
    ] = False,
    risk: Annotated[
        bool,
        typer.Option("--risk", help="List targets ranked by structural risk score."),
    ] = False,
    properties: Annotated[
        bool,
        typer.Option("--properties", help="List mined oracle and property evidence."),
    ] = False,
    actions: Annotated[
        bool,
        typer.Option("--actions", help="List the constrained semantic actions per target."),
    ] = False,
    candidates: Annotated[
        bool,
        typer.Option("--candidates", help="List materialized candidate cases per target."),
    ] = False,
    annotations: Annotated[
        bool,
        typer.Option("--annotations", help="List each parameter's static annotation shape."),
    ] = False,
    plans: Annotated[
        bool,
        typer.Option("--plans", help="List invocation plans with argument origins."),
    ] = False,
    strategies: Annotated[
        bool,
        typer.Option("--strategies", help="List deterministic strategy plans per target."),
    ] = False,
) -> None:
    """Discover Python sources under PATH and analyze them statically.

    Files are parsed once with Python's ``ast`` module, then analyzed for
    structure, predicates, comparisons, arithmetic, boundary candidates, mined
    oracle evidence, structural risk, constrained semantic actions, static
    annotation shapes, invocation plans, and strategy plans. Analyzed code is
    never imported or executed, and candidate inputs are never run. The command
    exits with 1 when one or more files could not be read or parsed; oracle
    ambiguity is not a source failure.
    """
    try:
        analysis = analyze_repository(path)
    except ScanError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=EXIT_INVALID_INPUT) from exc

    _print_analysis(
        analysis,
        show_targets=targets,
        show_boundaries=boundaries,
        show_risk=risk,
        show_properties=properties,
        show_actions=actions,
        show_candidates=candidates,
        show_annotations=annotations,
        show_plans=plans,
        show_strategies=strategies,
    )

    if analysis.semantics.structure.issue_count:
        raise typer.Exit(code=EXIT_SCAN_ISSUES)


def _print_analysis(
    analysis: RepositoryScan,
    *,
    show_targets: bool,
    show_boundaries: bool,
    show_risk: bool,
    show_properties: bool,
    show_actions: bool,
    show_candidates: bool,
    show_annotations: bool,
    show_plans: bool,
    show_strategies: bool,
) -> None:
    semantics = analysis.semantics
    structure = semantics.structure
    kind = "file" if structure.is_file else "directory"
    typer.echo("EdgeHunter scan")
    typer.echo(f"  target ({kind}): {structure.target}")
    typer.echo("")
    typer.echo(f"  Python files discovered: {structure.python_file_count}")
    typer.echo(f"  Parsed successfully:     {structure.parsed_file_count}")
    typer.echo(f"  Syntax issues:           {structure.syntax_issue_count}")
    typer.echo(f"  Read issues:             {structure.read_issue_count}")
    typer.echo("")
    typer.echo(f"  Classes:                 {structure.class_count}")
    typer.echo(f"  Functions:               {structure.function_count}")
    typer.echo(f"  Methods:                 {structure.method_count}")
    typer.echo(f"  Async targets:           {structure.async_target_count}")
    typer.echo(f"  Callable targets:        {structure.callable_target_count}")
    if not structure.is_file:
        typer.echo(f"  Excluded directories:    {len(structure.excluded_directories)}")
    typer.echo("")
    typer.echo(f"  Predicates:              {semantics.predicate_count}")
    typer.echo(f"  Comparisons:             {semantics.comparison_count}")
    typer.echo(f"  Arithmetic expressions:  {semantics.arithmetic_expression_count}")
    typer.echo(f"  Boundary candidates:     {semantics.boundary_candidate_count}")

    _print_oracle_counts(analysis)
    _print_generation_counts(analysis)
    _print_planning_counts(analysis)

    if show_annotations:
        _print_annotations(analysis)

    if show_targets:
        _print_targets(structure)

    if show_boundaries:
        _print_boundaries(analysis)

    if show_risk:
        _print_risk(analysis)

    if show_properties:
        _print_properties(analysis)

    if show_actions:
        _print_actions(analysis)

    if show_candidates:
        _print_candidates(analysis)

    if show_plans:
        _print_plans(analysis)

    if show_strategies:
        _print_strategies(analysis)

    _print_issues(structure)


def _print_planning_counts(analysis: RepositoryScan) -> None:
    planning = analysis.planning
    annotations = analysis.annotations
    typer.echo("")
    typer.echo("  Planning")
    typer.echo(f"    Parameters recognized:    {annotations.recognized_count}")
    typer.echo(f"    Parameters unknown:       {annotations.unknown_count}")
    typer.echo(f"    Invocation plans:         {planning.plan_count}")
    typer.echo(f"    Complete function plans:  {planning.complete_plan_count}")
    typer.echo(f"    Needs receiver:           {planning.needs_receiver_count}")
    typer.echo(f"    Partial plans:            {planning.partial_plan_count}")
    typer.echo(f"    Symbolic plans:           {planning.symbolic_plan_count}")
    typer.echo(f"    Strategy plans:           {planning.strategy_plan_count}")


def _print_generation_counts(analysis: RepositoryScan) -> None:
    generation = analysis.generation
    typer.echo("")
    typer.echo("  Semantic actions")
    typer.echo(f"    Actions proposed:          {generation.action_count}")
    typer.echo(f"    Concrete values:           {generation.concrete_action_count}")
    typer.echo(f"    Symbolic values:           {generation.symbolic_action_count}")
    typer.echo(f"    Unsupported actions:       {generation.unsupported_action_count}")
    typer.echo(f"    Targets with actions:      {generation.targets_with_actions}")
    typer.echo(f"    Candidate cases:           {generation.case_count}")
    typer.echo(f"    Complete cases:            {generation.complete_case_count}")
    typer.echo(f"    Partial cases:             {generation.partial_case_count}")
    typer.echo(f"    Symbolic cases:            {generation.symbolic_case_count}")


def _print_oracle_counts(analysis: RepositoryScan) -> None:
    oracles = analysis.oracles
    typer.echo("")
    typer.echo("  Oracle evidence")
    typer.echo(f"    Assertions:                   {oracles.assertion_count}")
    typer.echo(f"    Validation rules:             {oracles.validation_rule_count}")
    typer.echo(f"    Resolved test expectations:   {oracles.resolved_test_expectation_count}")
    typer.echo(f"    Unresolved test expectations: {oracles.unresolved_test_expectation_count}")
    typer.echo(f"    Ambiguous test expectations:  {oracles.ambiguous_test_expectation_count}")
    typer.echo(f"    Property candidates:          {oracles.property_candidate_count}")
    typer.echo(f"    Strongly supported targets:   {oracles.strongly_supported_target_count}")
    typer.echo(f"    Risk-scored targets:          {analysis.risk.scored_target_count}")


def _print_issues(analysis: RepositoryAnalysis) -> None:
    syntax_modules = [module for module in analysis.modules if module.syntax_issue is not None]
    if syntax_modules:
        typer.echo("")
        typer.echo("Syntax issues")
        typer.echo("")
        for module in syntax_modules:
            syntax_issue = module.syntax_issue
            if syntax_issue is None:
                continue
            location = _format_location(
                module.relative_path, syntax_issue.line, syntax_issue.column
            )
            typer.echo(f"  {location}")
            typer.echo(f"    {syntax_issue.message}")

    read_modules = [module for module in analysis.modules if module.read_issue is not None]
    if read_modules:
        typer.echo("")
        typer.echo("Read issues")
        typer.echo("")
        for module in read_modules:
            read_issue = module.read_issue
            if read_issue is None:
                continue
            typer.echo(f"  {module.relative_path.as_posix()}")
            typer.echo(f"    {read_issue.message}")


def _print_targets(analysis: RepositoryAnalysis) -> None:
    typer.echo("")
    typer.echo("Targets")
    typer.echo("")

    found = False
    for module in analysis.modules:
        for function in module.functions:
            found = True
            typer.echo(f"  {module.relative_path.as_posix()}:{function.lineno}")
            typer.echo(f"    {function.signature}")

    if not found:
        typer.echo("  (no callable targets)")


def _print_boundaries(analysis: RepositoryScan) -> None:
    typer.echo("")
    typer.echo("Boundaries")
    typer.echo("")

    found = False
    for entry in analysis.semantics.boundaries:
        found = True
        typer.echo(f"  {entry.relative_path.as_posix()}:{entry.lineno}")
        typer.echo(f"    {entry.signature}")
        typer.echo("")
        current_parameter = ""
        for candidate in entry.candidates:
            if candidate.parameter != current_parameter:
                current_parameter = candidate.parameter
                typer.echo(f"    {candidate.parameter}")
            typer.echo(f"      [{candidate.origin.value}] {candidate.description}")

    if not found:
        typer.echo("  (no boundary candidates)")


def _print_risk(analysis: RepositoryScan) -> None:
    typer.echo("")
    typer.echo("Structural risk")
    typer.echo("  Search-priority heuristic, not a probability of a bug.")
    typer.echo("")

    if not analysis.risk.profiles:
        typer.echo("  (no callable targets)")
        return

    for profile in analysis.risk.profiles:
        location = f"{profile.relative_path.as_posix()}:{profile.lineno}"
        typer.echo(f"  {profile.score:>3}  {location}")
        typer.echo(f"      {profile.signature}")
        for contribution in profile.contributions:
            points = f"+{contribution.points}"
            typer.echo(f"      {contribution.kind.value:<18} {points:<4} {contribution.detail}")
        typer.echo("")


def _print_properties(analysis: RepositoryScan) -> None:
    oracles = analysis.oracles

    typer.echo("")
    typer.echo("Properties")
    typer.echo("")
    typer.echo("  Mined evidence, not confirmed defects.")
    typer.echo("")

    typer.echo("  Production")
    typer.echo("")
    if oracles.production_properties:
        for property_candidate in oracles.production_properties:
            _print_production_property(property_candidate)
    else:
        typer.echo("    (no production assertions or validation guards)")

    typer.echo("")
    typer.echo("  Existing test expectations")
    typer.echo("")
    if oracles.test_expectations:
        for expectation in oracles.test_expectations:
            _print_test_expectation(expectation)
    else:
        typer.echo("    (no test expectations recognized)")

    typer.echo("")
    typer.echo("  Oracle support by target")
    typer.echo("")
    if oracles.support:
        for entry in oracles.support:
            typer.echo(
                f"    {entry.level.value.upper():<6} {entry.target}  "
                f"({entry.assertion_count} assertion(s), "
                f"{entry.validation_rule_count} validation rule(s), "
                f"{entry.resolved_test_expectation_count} resolved test(s))"
            )
    else:
        typer.echo("    (no target has explicit oracle evidence)")


def _print_production_property(item: ProductionPropertyCandidate) -> None:
    strength = item.strength.value.upper()
    typer.echo(f"    [{strength}] {item.kind.value}  {item.target}")
    for entry in item.evidence:
        typer.echo(f"      evidence: {entry.relative_path.as_posix()}:{entry.lineno}")
    if item.kind is PropertyKind.ASSERTED_CONDITION:
        typer.echo(f"      condition: {item.condition}")
        if item.message is not None:
            typer.echo(f"      message:   {item.message}")
    else:
        typer.echo(f"      when:      {item.condition}")
        outcome = item.expected_outcome.value
        if item.exception is not None:
            outcome = f"{outcome} {item.exception}"
        typer.echo(f"      expected:  {outcome}")
    typer.echo("")


def _print_test_expectation(item: TestExpectationCandidate) -> None:
    strength = item.strength.value.upper()
    typer.echo(f"    [{strength}] {item.kind.value}  [{item.resolution.value}]")
    for entry in item.evidence:
        typer.echo(f"      evidence: {entry.relative_path.as_posix()}:{entry.lineno}")
    if item.comparison_operator is not None:
        typer.echo(
            f"      expect:   {item.call_expression} "
            f"{item.comparison_operator.value} {item.expected_expression}"
        )
    else:
        typer.echo(f"      expect:   {item.call_expression} raises {item.exception}")
    typer.echo(f"      callee:   {item.callee}")
    typer.echo(f"      target:   {item.target if item.target is not None else 'unresolved'}")
    typer.echo(f"      reason:   {item.resolution_reason}")
    typer.echo("")


def _print_actions(analysis: RepositoryScan) -> None:
    typer.echo("")
    typer.echo("Actions")
    typer.echo("")
    typer.echo("  Constrained input vocabulary. Nothing here is executed.")
    typer.echo("")

    found = False
    for entry in analysis.generation.action_sets:
        found = True
        typer.echo(f"  {entry.relative_path.as_posix()}:{entry.lineno}")
        typer.echo(f"    {entry.signature}")
        typer.echo("")
        current_parameter = ""
        for action in entry.actions:
            if action.parameter != current_parameter:
                current_parameter = action.parameter
                typer.echo(f"    {action.parameter}")
            typer.echo(
                f"      [{action.origin.value}] {action.description:<34} "
                f"{action.status.value:<11} {_render_value(action)}"
            )
            if action.status is not MaterializationStatus.CONCRETE:
                typer.echo(f"        {action.materialization_reason}")

    if not found:
        typer.echo("  (no applicable semantic actions)")


def _print_candidates(analysis: RepositoryScan) -> None:
    typer.echo("")
    typer.echo("Candidates")
    typer.echo("")
    typer.echo("  One action per case. Values for other parameters are never invented.")
    typer.echo("")

    found = False
    for entry in analysis.generation.action_sets:
        cases = [
            case
            for case in analysis.generation.cases
            if case.relative_path == entry.relative_path and case.target == entry.target
        ]
        if not cases:
            continue
        found = True
        typer.echo(f"  {entry.relative_path.as_posix()}:{entry.lineno}")
        typer.echo(f"    {entry.signature}")
        typer.echo("")
        for case in cases:
            assignment = f"{case.parameter} = {_render_value(case.action)}"
            typer.echo(
                f"      [{case.completeness.value:<8}] {assignment:<28} {case.action.description}"
            )
            if case.missing_parameters:
                typer.echo(f"        missing: {', '.join(case.missing_parameters)}")
        typer.echo("")

    if not found:
        typer.echo("  (no candidate cases)")


def _render_value(action: SemanticAction) -> str:
    """Render a materialized value; a symbolic value is shown in angle brackets."""
    if action.value_text is not None:
        return _display_text(action.value_text)
    if action.symbolic_text is not None:
        return f"<{action.symbolic_text}>"
    return "(none)"


def _print_annotations(analysis: RepositoryScan) -> None:
    typer.echo("")
    typer.echo("Annotations")
    typer.echo("")
    typer.echo("  Static annotation interpretation is syntactic, not type resolution.")
    typer.echo("")

    found = False
    current = ("", 0)
    for item in analysis.annotations.parameters:
        location = (item.relative_path.as_posix(), item.lineno)
        if location != current:
            current = location
            found = True
            typer.echo(f"  {location[0]}:{location[1]}  {item.target}")
        rendered = item.rendered if item.rendered is not None else "(unannotated)"
        typer.echo(f"    {item.parameter}: {rendered} -> {item.description}")

    if not found:
        typer.echo("  (no callable targets)")


def _print_plans(analysis: RepositoryScan) -> None:
    typer.echo("")
    typer.echo("Invocation plans")
    typer.echo("")
    typer.echo("  One adversarial action per plan. Filler values record their origin.")
    typer.echo("")

    if not analysis.planning.plans:
        typer.echo("  (no invocation plans)")
        return

    current = ("", 0)
    for plan in analysis.planning.plans:
        location = (plan.relative_path.as_posix(), plan.lineno)
        if location != current:
            current = location
            typer.echo(f"  {location[0]}:{location[1]}  {plan.signature}")
            typer.echo("")

        action = plan.primary_action
        header = f"    {action.parameter} <- {action.kind.value}"
        if action.reference is not None:
            header = f"{header} {action.reference}"
        typer.echo(header)
        for item in plan.assignments:
            typer.echo(
                f"      {item.parameter} = {_render_assignment(item)}"
                f"  [{item.origin.value}, {item.placement.value}]"
            )
        if plan.requires_receiver:
            typer.echo(f"      receiver: {plan.receiver_name} (not constructed)")
        if plan.missing_parameters:
            typer.echo(f"      missing: {', '.join(plan.missing_parameters)}")
        typer.echo(f"      status: {plan.status.value}")
        typer.echo("")


def _print_strategies(analysis: RepositoryScan) -> None:
    typer.echo("")
    typer.echo("Strategy plans")
    typer.echo("")
    typer.echo("  Deterministic descriptions, not generated samples.")
    typer.echo("")

    if not analysis.planning.strategies:
        typer.echo("  (no strategy plans)")
        return

    for entry in analysis.planning.strategies:
        typer.echo(f"  {entry.relative_path.as_posix()}:{entry.lineno}  {entry.signature}")
        typer.echo("")
        for parameter in entry.parameters:
            typer.echo(
                f"    {parameter.parameter}: {parameter.annotation.description}"
                f"  [{parameter.kind.value}]"
            )
            for value in parameter.values:
                typer.echo(f"      {value.descriptor:<28} {_display_text(value.text)}")
            if parameter.depends_on:
                typer.echo(f"      depends on: {', '.join(parameter.depends_on)}")
            typer.echo("")


def _render_assignment(item: ArgumentAssignment) -> str:
    """Render a planned argument value, or how it is left unsupplied."""
    if item.value_text is not None:
        return _display_text(item.value_text)
    if item.placement is ArgumentPlacement.OMITTED:
        return "(declared default)"
    return "(unresolved)"


# Longest value text printed verbatim before the display is compacted. The value
# itself is never shortened -- the codec, the plans, and the execution layer all
# keep the exact literal -- this only bounds what a terminal has to render.
_DISPLAY_TEXT_LIMIT: Final[int] = 80

# Only the three control characters that would otherwise break a single line.
# Quotes and backslashes are deliberately *not* escaped: display text is terminal
# output, not source to be re-parsed, so escaping them would misreport values such
# as `float("nan")` and a stored `"\x00"` literal that already reads correctly.
_DISPLAY_SHORT_ESCAPES: Final[dict[str, str]] = {
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _display_text(text: str) -> str:
    """Render a stored value as bounded, ASCII-safe, deterministic terminal text.

    Two separate problems are solved here, and neither touches the stored value.

    * **Length.** A bounded long string is 1,024 characters, which would be
      printed in full by every one of the actions, candidates, plans, and
      strategies sections. The displayed form is compacted to a prefix plus its
      true length so a reader can tell what it is, while ``value_text`` keeps
      all 1,024 characters for the codec.
    * **Encodability.** The output stream may not be able to encode every
      character (a legacy console encoding cannot represent a NUL, an emoji, or
      a CJK character). Every character outside printable ASCII is therefore
      shown as an explicit escape. A real NUL byte is never written to a
      terminal, and the rendering does not depend on the host's console
      encoding, so the same scan prints the same bytes everywhere.
    """
    body = "".join(_display_char(character) for character in text)
    if len(body) <= _DISPLAY_TEXT_LIMIT:
        return body
    return f"{body[:_DISPLAY_TEXT_LIMIT]}... ({len(text)} chars)"


def _display_char(character: str) -> str:
    """Escape one character for display unless it is printable ASCII."""
    short = _DISPLAY_SHORT_ESCAPES.get(character)
    if short is not None:
        return short
    code_point = ord(character)
    if 0x20 <= code_point < 0x7F:
        return character
    if code_point < 0x100:
        return f"\\x{code_point:02x}"
    if code_point < 0x10000:
        return f"\\u{code_point:04x}"
    return f"\\U{code_point:08x}"


def _format_location(relative_path: Path, line: int | None, column: int | None) -> str:
    location = relative_path.as_posix()
    if line is not None:
        location = f"{location}:{line}"
        if column is not None:
            location = f"{location}:{column}"
    return location


def main() -> None:
    """Run the EdgeHunter command-line application."""
    app()


if __name__ == "__main__":
    main()
