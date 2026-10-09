# EdgeHunter — Session Handoff

## 1. Current Phase

- **Phase:** Phase 6B — Runtime Oracle Evaluation
- **Subphase:** none (single-session phase)
- **Objective:** Compare isolated execution observations against explicit,
  applicable program expectations, without inventing correctness claims. Exact
  input matching, safe comparison, expected-exception verification, source
  snapshot verification, optional bounded reproduction, and conservative
  evidence-backed findings.
- **Status:** complete

Repository state at handoff: branch `main`, remote `origin` ->
`PrathamKapoor/Faultline`. Implementation commit `b64e97f` ("feat: evaluate
runtime observations against exact explicit oracles"). Version remains `0.1.0`;
no bump was made (D-002 makes the version literal the single source, and no
decision requires a bump).

## 2. Work Completed

The repository had a green Phase 6A baseline (558 passed, 1 skipped). This
session added:

- a six-value `OracleStatus` vocabulary where no status is a synonym for another,
  plus `FindingKind`, `ReproductionStatus`, `ApplicabilityKind`, and provenance
  records (`evaluation/models.py`)
- an exact applicability layer (`evaluation/applicability.py`): same target,
  all-literal arguments, no starred arguments, and the same arguments bound to
  the same parameter names through the target's own signature
- safe comparison over already-decoded protocol values using Python's own
  operator semantics (`evaluation/compare.py`), with `is`/`is not` restricted to
  `None` and a Python `TypeError` becoming `UNSUPPORTED` rather than `MISMATCH`
- built-in exception identity via the real hierarchy derived from
  `builtins.__mro__`, with custom exception identity reported `INCONCLUSIVE`
  (`evaluation/exceptions.py`)
- canonical value identity as text, so `NaN` and `-0.0` stay distinguishable
  during deduplication and recheck comparison (`evaluation/values.py`)
- the evaluator itself (`evaluation/evaluator.py`): applicability runs first and
  unconditionally, infrastructure failures are always `INCONCLUSIVE`, a raise that
  satisfies `pytest.raises` is a `MATCH`, conflicting expectations are reconciled
  rather than picked, and production assertions yield `ASSERTION_OBSERVED`
- finding classification with no severity field, deterministic hashed identifiers,
  and deterministic deduplication (`evaluation/findings.py`)
- snapshot verification by SHA-256 comparison of analyzed and staged bytes, with
  no Git commit invented (`evaluation/repository.py`)
- bounded re-execution wired into the existing execution layer, plus attempt
  bookkeeping that separates usable attempts from infrastructure failures
  (`execution/repository.py`, `evaluation/recheck.py`)
- a versioned JSON artifact that stores evidence and deliberately excludes
  captured target output, plus `.edgehunter/` in `.gitignore`
- `run --evaluate` and `run --recheck N`, with exit codes unchanged and `scan`
  entirely untouched
- six test suites: applicability, verdicts, findings, structural safety, CLI, and
  real-Docker integration

## 3. Files Changed

- `src/edgehunter/evaluation/__init__.py` (new) — public interface
- `src/edgehunter/evaluation/models.py` (new) — statuses, findings, provenance
- `src/edgehunter/evaluation/applicability.py` (new) — exact invocation binding
- `src/edgehunter/evaluation/compare.py` (new) — Python-semantics comparison
- `src/edgehunter/evaluation/exceptions.py` (new) — exception identity
- `src/edgehunter/evaluation/values.py` (new) — materialization and identity text
- `src/edgehunter/evaluation/evaluator.py` (new) — applicability then verdict
- `src/edgehunter/evaluation/findings.py` (new) — classification and dedup
- `src/edgehunter/evaluation/repository.py` (new) — snapshots, reproduction, artifact
- `src/edgehunter/evaluation/recheck.py` (new) — attempt sequence bookkeeping
- `src/edgehunter/oracle/test_expectations.py` — records the mined call shape
- `src/edgehunter/execution/repository.py` — `rechecks` option, snapshot-fixed attempts
- `src/edgehunter/execution/models.py` — `RunReport.rechecks`, `RunReport.staged_digests`
- `src/edgehunter/cli.py` — `--evaluate`, `--recheck`, evaluation output
- `.gitignore` — `.edgehunter/`
- `tests/test_evaluation_applicability.py`, `tests/test_evaluation_verify.py`,
  `tests/test_evaluation_findings.py`, `tests/test_evaluation_safety.py`,
  `tests/test_evaluation_cli.py`, `tests/test_evaluation_integration.py` (all new)
- `decisions.md` — D-058 through D-069
- `flow.md` — section 18 and the phase table
- `README.md` — status, `--evaluate`, status table, limitations, roadmap

## 4. Current Architecture / State

The separation the phase exists to establish is now structural:

```text
host                                          container
──────────────────────────────────────       ───────────────────────────
execution/repository.py                      runtime/worker.py
  authorize, select, stage, run N attempts     read + validate request
  recheck within the same snapshot             resolve the staged file
  decode one result per attempt                decode typed values
  capture staged digests       ◄────────       import module (only here)
                                              invoke once
evaluation/evaluator.py                        write /output/result.json
  applicability → verdict → finding
  pure: no process, no target import
```

`evaluation/` contains no module that can execute code, reach an interpreter, or
import the Docker backend. `tests/test_evaluation_safety.py` walks the package's
own AST and fails if that ever changes, if it gains `eval`/`exec`/`importlib`/
`subprocess`/`pickle`, if it imports the worker, or if `pipeline.py` pulls it in.

Statuses are `MATCH`, `MISMATCH`, `NOT_APPLICABLE`, `INCONCLUSIVE`,
`UNSUPPORTED`, `CONFLICTING_EVIDENCE`. Findings are
`OBSERVED_EXPECTATION_MISMATCH`, `REPRODUCED_EXPECTATION_MISMATCH`,
`ASSERTION_OBSERVED`, `SUSPICIOUS_BEHAVIOR`, `INCONCLUSIVE`. There is no severity
field anywhere, and `FindingKind` has no `CRITICAL` or `CONFIRMED_VULNERABILITY`
member — a test asserts that.

## 5. Decisions Made

D-058 Six distinct oracle statuses, none a synonym for another.
D-059 An expectation covers one exact invocation and is never generalized.
D-060 Comparison uses Python's own operator semantics; a refusal is never a contradiction.
D-061 Builtin exception identity uses the real hierarchy; custom identity is `INCONCLUSIVE`.
D-062 Source snapshots verified by SHA-256 content fingerprint; no Git commit invented.
D-063 Production assertions yield `ASSERTION_OBSERVED`; guard reachability never inferred.
D-064 Conflicting applicable expectations are reported; neither one is chosen.
D-065 Rechecking is opt-in, bounded, snapshot-fixed; a failure is never confirmation.
D-066 Findings carry no severity; identifiers derived deterministically.
D-067 `--evaluate` is additive and changes no exit code.
D-068 The evaluator is pure; re-execution lives in the execution layer.
D-069 A versioned JSON artifact stores evidence only, and is Git-ignored.

Nothing in this phase supersedes an earlier decision. D-062 relies on the Phase 6A
fingerprint work; D-060 and D-059 both depend on the D-046 literal whitelist.

## 6. Requirements and Constraints

- Applicability is checked before evaluation, always. An inapplicable oracle is
  never value-compared.
- An expectation applies to one exact recorded invocation. No generalization from
  a single example, however obvious the pattern looks.
- Non-literal test arguments are never evaluated and the target test is never run.
- Declared defaults are never evaluated, so `f(1)` and `f(1, 10)` stay distinct.
- Ambiguous and unresolved expectations are never attached to a target.
- Host-side evaluation imports no target code, evaluates no target expression, and
  starts no process.
- Only built-in values are compared; a `TypeError` from Python is `UNSUPPORTED`,
  never `MISMATCH`.
- Identity comparisons are limited to `None`.
- Custom exception identity from a bare name is `INCONCLUSIVE`.
- An unverified source snapshot yields no verdict.
- Infrastructure failures are never oracle violations.
- Production assertions are never universalized; guard reachability is never
  inferred and guards are never evaluated.
- Rechecking requires explicit request, is bounded to 5, reuses one snapshot, and
  needs `--allow-execution`.
- No LLM, no Hypothesis, no shrinking, no adaptive search, no severity.
- All commits are authored solely as PrathamKapoor, with no AI attribution.
- `paper/`, `research/`, and `*.tex` remain untouched and uncommitted.

## 7. Testing and Verification

Baseline before changes, with a live Docker Linux daemon:

- `558 passed, 1 skipped (72.33s)`

Final, same daemon:

- `741 passed, 1 skipped (228.85s)`
- ruff check: `All checks passed!`
- ruff format: `89 files already formatted`
- mypy strict: `Success: no issues found in 85 source files`

Real Docker integration, executed against Docker CLI 29.8.1 with a Linux daemon
(`linux/amd64`), all passing. These are the required Phase 6B demonstrations:

| Case | Expected | Result |
|---|---|---|
| A matching return expectation | `MATCH`, no finding | pass |
| B mismatching return expectation | `MISMATCH`, `OBSERVED_EXPECTATION_MISMATCH` | pass |
| C matching `pytest.raises` | `MATCH`, no finding, `Execution: RAISED` | pass |
| D missing `pytest.raises` | `MISMATCH` | pass |
| E no applicable oracle | `INCONCLUSIVE`, no confirmed finding | pass |
| F three rechecks | `REPRODUCED`, 4/4, no container survives | pass |
| G unavailable sandbox | no execution, no evaluation, no fallback | pass |
| H conflicting expectations | `CONFLICTING_EVIDENCE`, both locations kept | pass |
| provenance | every evaluation names its `path:line` | pass |
| artifact safety | captured target output absent from the artifact | pass |

Case H is worth noting: `add(0, 0)` genuinely returns `0`, so the observation
matches one of the two conflicting expectations. The verdict is still
`CONFLICTING_EVIDENCE`, which is the direct evidence that the evaluator does not
pick the convenient oracle.

The one skip is `test_symlink_is_refused_and_not_followed`: creating a symlink
requires Developer Mode or elevation on this Windows host. The staging code
refuses symlinks by `is_symlink()` check and the behaviour is unit-covered
otherwise; the test reports a skip rather than passing unverified.

Manual CLI verification against a real repository with a deliberately wrong
`add`: the `MISMATCH` output names `test_calculator.py:7`, every other candidate
reports `ARGUMENTS_DIFFER / NOT_APPLICABLE`, `validate(-1)` reports `MATCH` with
no finding, and `Bug severity: not assessed` is printed.

## 8. Known Issues / Risks

- Docker reduces risk; it does not eliminate it. Container escapes have existed.
- The nonce-checked result document proves the document belongs to one request. It
  does **not** prove a deliberately hostile target could not have influenced the
  worker process that produced it. Strengthening worker/target integrity is a
  separate future security task.
- A custom exception matched only by bare name is permanently `INCONCLUSIVE`
  until a later phase adds qualified exception identity to the protocol.
- `bool` and `str` parameters still produce no invocation plans, inherited from
  Phase 5 (D-038's action layer never generated values for them). Those targets
  are silently unreachable rather than refused, and this is unchanged in Phase 6B.
- Method receivers are never constructed, so method targets are refused with
  `NEEDS_RECEIVER`.
- Framework fixtures are never executed; an expectation using one is reported
  `UNSUPPORTED`.
- Production assertion preconditions are not generalized: an `AssertionError` on
  an invalid-input path is `ASSERTION_OBSERVED`, review required.
- Guard reachability is not analyzed, so a returning value is never called a guard
  violation.
- Arbitrary runtime objects remain opaque, so their comparisons are `UNSUPPORTED`.
- Symlink refusal is unit-tested but the test skips on this host.
- A timeout kills the container without explaining what the target was doing.
- Captured output is truncated and native fd writes are discarded, so the capture
  is a sample, not a transcript.

## 9. Unfinished Work

Nothing in the Phase 6B scope is unfinished. Deliberately out of scope:

- counterexample search over the deterministic candidate space
- failure shrinking and minimization
- adaptive neighborhood search and any learned policy
- corpus-scale validation against real repositories
- coverage for `bool` and `str` parameter plans
- qualified custom-exception identity in the protocol
- guard reachability analysis

## 10. Next Subphase

The natural next phase is Phase 7 — counterexample shrinking plus adaptive
semantic neighborhood search. The prerequisite is now in place: a trustworthy
execution and evaluation subsystem, so a shrinking step can only ever operate on
an already-diagnosed contradiction. Two smaller items remain worth doing first,
and both are honest gaps rather than work in progress:

- decide whether `bool` and `str` parameters should produce plans or an explicit
  refusal. They are currently silently unreachable, which is the least honest of
  the current behaviours.
- add qualified exception identity (module and qualname) to the worker protocol,
  which would turn the custom-exception `INCONCLUSIVE` cases into decidable
  matches or mismatches.

Also worth re-running on an elevated host: `test_symlink_is_refused_and_not_followed`,
so it stops being a skip.

## 11. Critical Context

- Repo `edgehunter`, branch `main`, remote `origin` -> `PrathamKapoor/Faultline`.
  Published as a public snapshot; do not force-push over it.
- Python 3.10+; the venv is `.venv`. Run gates as
  `.venv\Scripts\python.exe -m pytest -q`, `-m ruff check .`,
  `-m ruff format --check .`, `-m mypy`.
- Docker must be running. `docker version --format '{{.Server.Os}}'` must print
  `linux`; the integration tests skip (not pass) when it does not.
- The worker image `edgehunter-runtime:1` is built once from `runtime_context()`
  and cached; a stale image after editing the worker needs
  `docker image rm edgehunter-runtime:1`. Changing `runtime/Dockerfile` requires
  the image to be rebuilt for integration tests to see the change.
- The build context is the directory containing the `edgehunter` package, never
  the scanned repository.
- `evaluation/` must stay pure. The safety suite fails if any module in it gains
  an execution primitive, imports `execution.docker` or the worker, or builds its
  own literal grammar with `ast.parse`/`json.loads`.
- `pipeline.py` must not import `evaluation`. A safety test asserts it, because
  the static scan path has to stay static.
- `check_relative_path` must keep both the POSIX and Windows readings. Removing
  either reintroduces a real escape.
- `run` deliberately never exits `1`, and `--evaluate` does not change that.
- Every `docker run` hardening argument in `execution/docker.py` must stay. Do not
  enable network access or a host fallback to make evaluation easier.
- The evaluator compares decoded protocol values. If a comparison appears to need
  a target object, the answer is `UNSUPPORTED`, not a host-side comparison.
- `mypy` runs over `src` and `tests` in strict mode.
- `.edgehunter/` is Git-ignored; never commit a generated evaluation artifact.

## 12. Agent Instructions

- Read `decisions.md`, `flow.md`, and this file fully before changing anything.
- Append decisions in the established format. Every bug fix needs `Symptom`,
  `Root cause`, and `Regression test`. Mark superseded decisions explicitly.
- Update `flow.md` with real module, function, and file names as the code changes.
- The central rule of this project is that an observation is not a judgement. If
  a change makes an exception look more like a bug without adding evidence, it is
  wrong.
- Never report an exception as a finding on its own. Never convert a structural
  risk score into severity. Never add a severity field.
- Never weaken an isolation property to make a test pass. A failing refusal test
  is a finding about the code, not about the test.
- Prefer `INCONCLUSIVE`, `UNSUPPORTED`, or `NOT_APPLICABLE` over a verdict the
  evidence does not support. A small number of well-evidenced counterexamples is
  the entire point of this project.
- Verify with evidence before claiming completion: run the gates, read the
  output, and quote the numbers.
- Commit as PrathamKapoor only. No AI attribution in commits, branches, tags,
  PRs, or release notes.
- Do not commit `paper/`, `research/`, or `*.tex`.
