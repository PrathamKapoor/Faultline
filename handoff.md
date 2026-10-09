# EdgeHunter — Session Handoff

## 1. Current Phase

- **Phase:** Phase 7A — Action Coverage Repair + Exception Identity Hardening
- **Subphase:** none (single-session phase)
- **Objective A:** Make a recognized `bool` or `str` parameter reachable — actions,
  materialized values, candidate cases, invocation plans, and strategy plans —
  by reusing the existing generation pipeline end to end.
- **Objective B:** Make observed exception identity more precise using qualified
  runtime information, without turning a better observation into a less justified
  oracle match.
- **Status:** complete

Repository state at handoff: branch `main`, remote `origin` ->
`PrathamKapoor/Faultline`. Implementation commit `dbb27e2` ("Cover bool and str
parameters, and observe qualified exception identity"). Version remains `0.1.0`;
no bump was made (D-002 makes the version literal the single source, and no
decision requires a bump).

## 2. Work Completed

Baseline before this session, with the Docker Linux daemon stopped: `711 passed,
31 skipped`. The Phase 6B handoff's `741 passed, 1 skipped` assumed a live daemon;
30 of the 31 skips were Docker integration tests. Docker Desktop 4.93.0 was
already installed and was started (not installed, not reconfigured) to obtain a
real Linux daemon, after which the true baseline reproduced at `741 passed, 1
skipped`. This session added:

Objective A:

- `ANNOTATION_ACTIONS` now maps `AnnotationKind.BOOL` and `AnnotationKind.STR`
  (D-070)
- `SemanticActionKind` grew from fourteen to twenty-one members: `SET_FALSE`,
  `SET_TRUE`, `EMPTY_STRING`, `WHITESPACE_STRING`, `UNICODE_STRING`,
  `NULL_CHARACTER_STRING`, `LONG_STRING` (D-071)
- `materialize_bool()` and `materialize_string()`, each refusing a mismatched
  annotation shape with `UNSUPPORTED` rather than coercing the value (D-071)
- `render_string_literal()` as the single rendering for string values, shared by
  the action layer, the strategy layer, and (asserted equal) the `str` baseline
  (D-072)
- `LONG_STRING_LENGTH = 1024`, `UNICODE_STRING_SAMPLE = "é漢🙂"`,
  `NULL_CHARACTER = "\x00"`, all exported
- `cli.py::_display_text()`: presentation-only ASCII escaping and length
  compaction, leaving every stored value exact (D-072)
- 22 boolean tests, 33 string tests, and 10 real-Docker execution tests

Objective B:

- `_exception_identity()` in the worker: module, qualified name, builtin-by-
  identity, and real-MRO builtin lineage, read with `type.__getattribute__` so a
  custom metaclass never runs on the observation path (D-073)
- four optional fields on `RuntimeObservation`, decoded defensively: a
  non-boolean becomes unknown, a malformed lineage is dropped entirely rather
  than repaired (D-073, D-074)
- `match_exception_observed()` replacing the name comparison, with the decision
  ladder in D-075; the string-only `match_exception()` kept as a wrapper
- `TestExpectationCandidate.exception_binding`, recording a test module's own
  module-scope binding of a bare raised name (D-076)
- `_outcome()` now clips the exception message itself rather than relying on the
  call site
- 27 identity tests, 36 protocol tests, 4 shadowing-mining tests, and 8
  real-Docker exception tests

## 3. Files Changed

- `src/edgehunter/generation/actions.py` — `ANNOTATION_ACTIONS` gains `BOOL` and
  `STR`; `_annotation_action` dispatches by shape
- `src/edgehunter/generation/models.py` — seven new `SemanticActionKind` members
  and their `ACTION_KIND_ORDER` ranks
- `src/edgehunter/generation/materialize.py` — `materialize_bool`,
  `materialize_string`, `is_string_action`, `render_string_literal`, the string
  constants, `LONG_STRING_LENGTH`
- `src/edgehunter/generation/strategy.py` — reads the shared constants and
  renderer instead of restating them
- `src/edgehunter/generation/__init__.py` — exports the new surface
- `src/edgehunter/cli.py` — `_display_text()` and its three call sites
- `src/edgehunter/runtime/worker.py` — `_exception_identity()` and its helpers;
  message clipping in `_outcome()`
- `src/edgehunter/execution/models.py` — four optional identity fields on
  `RuntimeObservation` plus `exception_identity_text`
- `src/edgehunter/execution/repository.py` — the four keys in `_RESULT_KEYS`;
  `_optional_bool()` and `_optional_str_tuple()`
- `src/edgehunter/evaluation/exceptions.py` — `ExceptionIdentity`,
  `match_exception_observed()`, the rebuilt matching ladder
- `src/edgehunter/evaluation/evaluator.py` — `identity_from_observation()` and
  the qualified `observed_description`
- `src/edgehunter/oracle/models.py` — `TestExpectationCandidate.exception_binding`
- `src/edgehunter/oracle/test_expectations.py` — `_module_bindings()` and the
  binding lookup for bare raised names
- `tests/test_generation_bool_actions.py` (new)
- `tests/test_generation_string_actions.py` (new)
- `tests/test_generation_bool_str_integration.py` (new)
- `tests/test_evaluation_exception_identity.py` (new)
- `tests/test_execution_exception_protocol.py` (new)
- `tests/test_execution_exception_identity_integration.py` (new)
- `tests/test_pipeline.py`, `tests/test_safety.py`,
  `tests/test_oracle_test_expectations.py` — two updated, one extended
- `decisions.md` — D-070 through D-077
- `flow.md` — sections 11, 15, 16, 18 updated; a Phase 7A session table
- `README.md` — status, vocabulary, the identity limitation, non-inferences,
  roadmap

## 4. Current Architecture / State

Neither objective introduced a subsystem. Both closed a gap inside an existing
one, which is why the layer boundaries are unchanged:

```text
Objective A: annotation shape ──► applicability ──► value ──► case ──► plan
             (unchanged)          (NEW entries)     (NEW)    (unchanged)

Objective B: raised exception ──► worker metadata ──► observation ──► matcher
              (unchanged)           (NEW, 4 fields)    (NEW fields)  (NEW ladder)
```

`evaluation/` remains pure: no module there gained an execution primitive, an
`importlib`/`pickle` import, or its own literal grammar. `tests/
test_evaluation_safety.py` still passes unchanged. `execution/docker.py`,
`execution/staging.py`, `execution/eligibility.py`, `execution/assembly.py`, and
`execution/codec.py` are **unmodified** in this phase — confirmed with
`git diff --stat`, which reports nothing for those paths. Docker hardening was
neither relaxed nor re-checked in; the 31 existing docker tests pass untouched.

`PROTOCOL_VERSION` is still `1`. The four new result keys are optional and absent
from any non-raised outcome, so a document written before this change still
decodes with all four fields `None`, and `None` means "never observed" rather than
`False`.

## 5. Decisions Made

D-070 The `bool`/`str` gap is in action applicability, not annotation parsing.
D-071 Seven new actions with fixed documented values and a hard length bound.
D-072 One canonical string renderer; CLI compaction is display-only.
D-073 Worker reports qualified identity from real class metadata, bypassing any
metaclass.
D-074 Protocol stays at version 1 because the change is purely additive.
D-075 Richer observation buys lineage-based verdicts for builtins, never
name-based custom matching.
D-076 A narrow source-level safeguard catches a test module that shadows a builtin
name.
D-077 No shrinking, no adaptive search, and an exact oracle does not survive its own
input change.

Nothing in this phase supersedes an earlier decision. D-070 confirms D-036 was
correct and fixes a separate table; D-072 tightens D-072's predecessor D-043's
string spelling but does not reverse it; D-075 extends D-061, which continues to
govern every custom-exception case.

## 6. Requirements and Constraints

- A `bool` parameter receives `SET_FALSE`/`SET_TRUE` only; a `str` parameter
  receives the five string actions only. `UNKNOWN` receives neither, and a
  parameter's *name* is never type evidence.
- A nullable `bool` or `str` gets its shape actions **and** the pre-existing
  `SET_NONE`.
- `BOUNDARY_ACTIONS` remains total and injective over all ten `BoundaryKind`
  values; the new actions are annotation-driven and have no boundary counterpart.
- Booleans are `LiteralKind.BOOLEAN` values `False`/`True`, never `0`/`1`, and
  never coerced into a mismatched shape.
- `LONG_STRING_LENGTH` is a module constant; the target and CLI cannot influence
  it, and the value is never truncated.
- The stored value and the terminal display are separate. `render_string_literal`
  owns the value; `_display_text` owns the output.
- Exactly one adversarial assignment per plan. A second parameter is filled by a
  labelled baseline (`bool`→`False`, `str`→`""`), never by a second adversarial
  action. There is no Cartesian product.
- Generation stays independent of risk: risk determines priority, never
  availability. No risk-scoring change was made.
- Exactly one renderer spells a string value, so the strategy layer's by-text
  dedup cannot list the same input twice.
- The worker reads exception class metadata with `type.__getattribute__` and
  decides builtin's identity by class identity against `vars(builtins)`. No
  `repr`, no `str` on an exception, no serialization, no import of a target class
  on the host.
- A custom expected exception stays `INCONCLUSIVE`, even when the observed
  qualified name is textually identical.
- A name difference is never a mismatch by itself; only a real MRO fact is.
- An observation with no identity fields keeps the Phase 6B name-only verdicts and
  is never silently downgraded.
- Only module-scope shadowing is detected; a function-local import of the same
  name beside a `pytest.raises` is a documented gap.
- No severity, no confidence score, no new `FindingKind`, no exception is
  automatically a finding.
- No dependencies added or removed. `typer` remains the only runtime dependency.
- No Hypothesis, no LLM, no shrinker, no adaptive search, no new input
  combinations.
- All commits authored solely as PrathamKapoor, with no AI attribution.
  `paper/`, `research/`, and `*.tex` remain untouched and uncommitted.

## 7. Testing and Verification

Baseline before changes, daemon stopped:

- `711 passed, 31 skipped (18.81s)`; ruff check, ruff format, and mypy all clean

Baseline after starting the existing Docker Desktop, before any Phase 7A code:

- `741 passed, 1 skipped`; `mypy: Success: no issues found in 85 source files`
- `ruff format: 89 files already formatted`

Final, same Linux daemon (`linux/amd64`, Docker 29.8.1):

- `881 passed, 1 skipped (355.51s)` — 140 new tests over the 741 baseline
- ruff check: `All checks passed!`
- ruff format: `95 files already formatted`
- mypy strict: `Success: no issues found in 91 source files`
- `docker ps -a --filter name=edgehunter-run-` empty after the suite

The single skip is `test_symlink_is_refused_and_not_followed`: creating a symlink
requires Developer Mode or elevation on this Windows host. The staging code
refuses symlinks by `is_symlink()` check and the behaviour is unit-covered
otherwise.

Real Docker, Phase 7A acceptance, all passing:

| Case | Expected | Result |
|---|---|---|
| `invert(False)` | `True` | pass |
| `invert(True)` | `False` | pass |
| argument arrives as a real `bool` | `type(...) is bool` | pass |
| `length("")` | `0` | pass |
| `length("é漢🙂")` | `3` | pass |
| `length("\x00")` | `1` | pass |
| all five string actions execute | `len` agrees with the planned value | pass |
| `""` vs `"\x00"` stay distinct | `0` and `1` | pass |
| executed cases are the planned ones | `{False, True}` | pass |
| real builtin `ValueError` | `exception_builtin=True`, module `builtins` | pass |
| custom class *named* `ValueError` | `exception_builtin=False`, no builtin lineage | pass |
| genuine subclass of the builtin | builtin present in lineage | pass |
| unrelated exception | no `ValueError` in lineage | pass |
| hostile module/class names | clipped to 200 characters | pass |
| hostile exception message | clipped to 4096 characters | pass |
| cleanup after all runs | no container survives | pass |

Two real-Docker oracle tests failed mid-session and the cause was the documented
stale-image trap, not a defect: `edgehunter-runtime:1` had been built before the
worker edit. `docker image rm edgehunter-runtime:1` followed by a rebuild made
both pass. This is a session fact worth keeping: **after editing
`runtime/worker.py`, remove the cached image or the integration tests silently
test the old worker.**

Manual CLI verification on a bool/str demo module: `--annotations` shows
`flag: bool -> BOOL` and `value: str -> STR`; `--plans` shows
`flag = True [adversarial_action, positional]` beside
`name = "" [static_type_baseline, positional]` with
`status: complete_function`; a NUL action prints as `"\x00"` and a Unicode action
as `"\xe9\u6f22\U0001f642"`; the long string prints as a prefix plus
`... (1026 chars)`. All five scan flags and the combined form exit `0`, and a
plain `scan` remains static.

## 8. Known Issues / Risks

- Docker reduces risk; it does not eliminate it. Container escapes have existed.
- The nonce-checked result document proves the document belongs to one request. It
  does **not** prove a deliberately hostile target could not have influenced the
  worker process that produced it. A target is also not prevented from reporting a
  misleading `__module__` or `__qualname__` for its own exception class. This is
  unchanged from Phase 6B and was not weakened here.
- Custom *expected* exception identity is still unresolved. A qualified observed
  name is good evidence and nothing more; the test module's own import resolution
  is still not performed, so a qualified expectation against a custom observation
  stays `INCONCLUSIVE`.
- Test-local shadowing is detected only for module-scope bindings. A
  function-local `from x import Y` beside `pytest.raises(Y)` is not detected.
- The worker image is cached, so an edited worker is not seen by the integration
  tests until `docker image rm edgehunter-runtime:1` is run. This produced two
  false failures during the session.
- Method receivers are never constructed, so method targets are refused with
  `NEEDS_RECEIVER`.
- Framework fixtures are never executed; an expectation using one is `UNSUPPORTED`.
- Production assertion preconditions are not generalized: an `AssertionError` on
  an invalid-input path is `ASSERTION_OBSERVED`, review required.
- Guard reachability is not analyzed, so a returning value is never called a guard
  violation.
- Arbitrary runtime objects remain opaque, so their comparisons are `UNSUPPORTED`.
- Symlink refusal is unit-tested but the test skips on this host.
- A timeout kills the container without explaining what the target was doing.
- Captured output is truncated and native fd writes are discarded, so the capture
  is a sample, not a transcript.
- Action, case, plan, and strategy counts are now higher on repositories with
  `bool` or `str` parameters. That is coverage, not defect count, and no metric
  is a bug estimate.

## 9. Unfinished Work

Nothing in the Phase 7A scope is unfinished. Both objectives are implemented,
tested, and verified against a real Docker daemon. Deliberately out of scope:

- counterexample search over the deterministic candidate space
- failure shrinking and minimization
- adaptive neighborhood search and any learned policy
- corpus-scale validation against real repositories
- resolution of custom *expected* exception classes (import resolution)
- guard reachability analysis
- framework fixture execution

## 10. Next Subphase

The natural next phase is **Phase 7B — shrinkability contracts and conservative
counterexample minimization**. The deterministic candidate space is now larger and
reaches `bool` and `str` targets, so there is something to minimize.

**The correctness constraint it must honour, recorded in D-077:** an exact test
expectation does not automatically remain applicable after an input is changed.
`assert f(100) == 200` cannot be applied to `f(10)` merely because `10` is
smaller. A shrinker must therefore either preserve applicable oracle semantics or
explicitly refuse to shrink. Concretely, a shrinking step must not:

- reuse a `MATCH`/`MISMATCH` verdict recorded for a different input,
- turn an `INCONCLUSIVE` into a verdict by shrinking away the evidence that made
  it inconclusive,
- break D-062's snapshot verification, so every attempt is measured against the
  same staged bytes,
- break D-064's reconciliation, so a conflict stays a conflict after shrinking,
- report a shrunken input whose contradiction was never re-established.

The honest maximum for a shrinker here is: reduce an input only while the
applicable oracle is re-evaluated against the new input and still applies, and
refuse the shrink otherwise.

Two smaller honest gaps remain worth doing before or alongside it:

- function-local shadowing detection for raised exception names
- a decision on whether the `INCONCLUSIVE` custom-exception cases can ever be made
  decidable without importing target code, or should be documented as permanent

Also worth re-running on an elevated host: `test_symlink_is_refused_and_not_followed`,
so it stops being a skip.

## 11. Critical Context

- Repo `edgehunter`, branch `main`, remote `origin` -> `PrathamKapoor/Faultline`.
  Published as a public snapshot; do not force-push over it.
- Python 3.10+; the venv is `.venv`. Run gates as
  `.venv\Scripts\python.exe -m pytest -q`, `-m ruff check .`,
  `-m ruff format --check .`, `-m mypy`.
- Docker must be running with a Linux engine: `docker version --format
  '{{.Server.Os}}'` must print `linux`. Integration tests **skip** when it does
  not, which means a skipped run is a partial verification, not a pass. Docker
  Desktop is installed at `C:\Program Files\Docker\Docker\Docker Desktop.exe`; if
  the daemon is down, start it and wait for `docker version` to report `linux`.
- **After any edit to `runtime/worker.py`, run `docker image rm
  edgehunter-runtime:1`** or the integration tests will exercise the previous
  worker. Changing `runtime/Dockerfile` has the same effect.
- The build context is the directory containing the `edgehunter` package, never
  the scanned repository.
- `evaluation/` must stay pure. The safety suite fails if any module in it gains
  an execution primitive, imports `execution.docker` or the worker, or builds its
  own literal grammar with `ast.parse`/`json.loads`.
- `pipeline.py` must not import `evaluation`. A safety test asserts it.
- `check_relative_path` must keep both the POSIX and Windows readings.
- `run` deliberately never exits `1`, and `--evaluate` does not change that.
- Every `docker run` hardening argument in `execution/docker.py` must stay. That
  file was not touched in Phase 7A.
- `render_string_literal()` in `generation/materialize.py` is the only rendering
  for string values. `cli.py::_display_text()` is display-only. Adding a second
  spelling in any layer reintroduces the duplicate-row defect D-072 fixed.
- `is_string_action()` / `is_collection_action()` are the shape predicates
  `_annotation_action` dispatches on.
- The evaluator compares decoded protocol values. If a comparison appears to need
  a target object, the answer is `UNSUPPORTED`, not a host-side comparison.
- `mypy` runs over `src` and `tests` in strict mode.
- `.edgehunter/` is Git-ignored; never commit a generated evaluation artifact.

## 12. Agent Instructions

- Read `decisions.md`, `flow.md`, and this file fully before changing anything.
- Append decisions in the established format. Every bug fix needs `Symptom`,
  `Root cause`, and `Regression test`. Mark superseded decisions explicitly.
- Update `flow.md` with real module, function, and file names as the code changes.
- The central rule of this project is that an observation is not a judgement. The
  Phase 7A sharpening makes this harder, not easier: a *more precise* observation
  is exactly when a careless reader will start treating it as proof. If a change
  makes an exception look more like a bug without adding evidence, it is wrong.
- Never turn a richer observation into a match. "The names now include a module"
  is not a licence to resolve a custom expected class, and D-075's ladder exists
  to make that refusal structural rather than a matter of care.
- Never report an exception as a finding on its own. Never convert a structural
  risk score into severity. Never add a severity field or a confidence number.
- Never weaken an isolation property to make a test pass, and never add a host
  execution fallback. A failing refusal test is a finding about the code, not
  about the test.
- More candidate coverage is not success if those candidates cannot execute
  safely. Verify new actions through the real Docker backend, not only through a
  static scan.
- Prefer `INCONCLUSIVE`, `UNSUPPORTED`, or `NOT_APPLICABLE` over a verdict the
  evidence does not support. A small number of well-evidenced counterexamples is
  the entire point of this project.
- If you change an existing test's expectation, record the test name, the old and
  new expectations, why the old one was wrong, and the independent evidence. Never
  adjust a count to match what the code happened to produce.
- Verify with evidence before claiming completion: run the gates, read the
  output, and quote the numbers. A skipped Docker test means the phase is
  partial.
- Commit as PrathamKapoor only. No AI attribution in commits, branches, tags,
  PRs, or release notes.
- Do not commit `paper/`, `research/`, or `*.tex`.
