# EdgeHunter — Session Handoff

## 1. Current Phase

- **Phase:** Phase 6A — Isolated Execution Backend
- **Subphase:** none (single-session phase)
- **Objective:** Execute Phase 5 invocation plans without ever importing or
  invoking target code on the host, by adding a Docker-only backend with an
  explicit opt-in, a typed protocol across the boundary, a deterministic
  eligibility gate, safe staging, bounded observation, and verified cleanup —
  while still claiming no defect, since no oracle evaluation exists yet.
- **Status:** complete

Repository state at handoff: branch `main`, no remote configured. Implementation
commit `89865f7` ("Execute eligible plans in a hardened Docker sandbox with no
host fallback") and documentation commit `71fa3ba`, followed by this metadata
commit (see `git log`). Version remains `0.1.0`; no bump was made (D-002 makes
the version literal the single source, and no decision requires a bump).

## 2. Work Completed

The repository had a green Phase 0-5 baseline (368 tests). This session added:

- a strict typed-value protocol (`execution/codec.py`) with nine value kinds, a
  versioned JSON document, special floats carried as text, deterministic set
  ordering, and explicit depth, size, item, and string budgets
- a rendered-literal reader that recognizes only literal AST nodes, so no value
  is ever handed to `eval`
- typed models for outcomes, eligibility, requests, runtime observations, staged
  sources, sandbox status, and run reports (`execution/models.py`)
- deterministic argument assembly that closes the positional run at the first
  omitted default (`execution/assembly.py`)
- an eligibility gate refusing unsupported status, kind, decorators, unsafe
  paths, and unsupported values with a named reason (`execution/eligibility.py`)
- safe staging: `.py` only, containment checks, symlink refusal, and hard
  file-count and size limits (`execution/staging.py`)
- a hardened `docker run` builder, availability check, bounded file-backed output
  capture, host-owned timeout, forced cleanup, and container verification
  (`execution/docker.py`)
- orchestration that resolves one unambiguous target, stages once, runs one
  container per plan, verifies the result, and deletes the temporary tree
  (`execution/repository.py`)
- the container-only worker, the sole code in the project that loads target code
  (`runtime/worker.py`), plus its digest-pinned packaged `Dockerfile`
- `edgehunter run` with `--target`, `--allow-execution`, `--max-cases`, and
  `--timeout`, and a target resolver that refuses ambiguity
- nine test suites: codec, assembly, eligibility, staging, docker arguments,
  worker protocol, host/worker boundary, CLI, and real-Docker integration

## 3. Files Changed

- `src/edgehunter/execution/codec.py` (new) — typed-value protocol and literal reader
- `src/edgehunter/execution/models.py` (new) — outcome, eligibility, request, and report models
- `src/edgehunter/execution/assembly.py` (new) — arguments to a real call
- `src/edgehunter/execution/eligibility.py` (new) — deterministic refusal gate
- `src/edgehunter/execution/staging.py` (new) — bounded read-only source copy
- `src/edgehunter/execution/docker.py` (new) — the only host process launcher
- `src/edgehunter/execution/repository.py` (new) — orchestration and result decoding
- `src/edgehunter/execution/__init__.py` (new) — public interface
- `src/edgehunter/runtime/worker.py` (new) — in-container target loader and invoker
- `src/edgehunter/runtime/Dockerfile` (new) — pinned, non-root sandbox image
- `src/edgehunter/runtime/__init__.py` (new) — package marker
- `src/edgehunter/cli.py` — added the `run` command and target resolution
- `pyproject.toml` — runtime package data and the `integration` test marker
- `tests/test_execution_codec.py`, `tests/test_execution_assembly.py`,
  `tests/test_execution_eligibility.py`, `tests/test_execution_staging.py`,
  `tests/test_execution_docker.py`, `tests/test_execution_worker.py`,
  `tests/test_execution_safety.py`, `tests/test_execution_cli.py`,
  `tests/test_execution_integration.py` (all new)
- `decisions.md` — D-045 through D-057
- `flow.md` — execution rules, extended diagram, phase table
- `README.md` — status, limitations, usage, architecture

## 4. Current Architecture / State

The isolation boundary is now structural rather than documentary. `scan` is
unchanged and still purely static. `run` adds a second path:

```text
host                                          container
──────────────────────────────────────       ────────────────────────────
execution/repository.py                      runtime/worker.py
  resolve one unambiguous target                read + validate request
  check_relative_path / check_eligibility       resolve the staged file
  assemble_invocation                           decode typed values
  stage read-only copy                          import module (only here)
  build hardened docker run  ─────────────►     invoke once, fds 1/2 → /dev/null
  timeout, forced cleanup                       bounded output capture
  verify nonce + id + keys      ◄───────────    write /output/result.json
  report an observation
```

`execution/` contains no module that can import target code. `tests/test_execution_safety.py`
walks the package AST and fails if any module other than `runtime/worker.py`
gains `eval`/`exec`/`importlib`/a deserializer, or if host code imports the
worker, or if the static entry point reaches into `execution`.

Outcomes are `RETURNED`, `RAISED`, `TIMEOUT`, `IMPORT_FAILURE`,
`TARGET_NOT_FOUND`, `PROTOCOL_ERROR`, `WORKER_FAILURE`, plus the report-level
`NOT_REQUIRED`, `UNAVAILABLE`, `READY`, `ERROR`. `run` exits `0`, `2`, or `3`;
`1` is never used so a refusal cannot be read as "issues found".

## 5. Decisions Made

D-045 Docker is the only backend, with no host fallback.
D-046 Strict typed JSON; rendered literals recognized by an AST whitelist.
D-047 The result travels in a file guarded by a nonce, never on stdout.
D-048 Returns described by exact built-in type checks; anything else opaque.
D-049 The positional run closes at the first omitted default.
D-050 Output bounded on both sides; the capture never grows without limit.
D-051 Host-side process output captured through files, not an in-memory pipe.
D-052 A target is path plus qualified name; ambiguity is refused.
D-053 Path validation reads paths as Windows paths as well as POSIX paths.
D-054 `None` is a supported return kind, not an unrepresentable one.
D-055 Cleanup is verified rather than assumed; deletion failures are reported.
D-056 The worker image is built from EdgeHunter's own packaged sources.
D-057 Execution is opt-in only; a missing or duplicate result is never a pass.

D-033 was previously superseded by D-037; nothing in this phase supersedes an
earlier decision. D-030's applicability question was settled by D-036.

## 6. Requirements and Constraints

- Target code is imported and invoked in exactly one module,
  `edgehunter.runtime.worker`, and only inside a container.
- The host scanner, analysis, oracle, generation, pipeline, and execution
  orchestration never import or execute target code.
- Docker is the only backend. A missing, unreachable, or non-Linux daemon yields
  a truthful unavailable report and zero results.
- No `eval`, `exec`, `literal_eval`, pickle, marshal, or object deserialization.
- `scan` never executes and its exit codes are unchanged.
- `run` requires both an explicit target and `--allow-execution`.
- Exceptions are observations, never findings; no oracle evaluation exists.
- No Hypothesis, shrinking, adaptive loops, dependency installation, LLM use, or
  receiver construction.
- No new runtime dependency was added; `pyproject.toml` still declares only
  `typer`.
- All commits are authored solely as PrathamKapoor, with no AI attribution.
- `paper/`, `research/`, and `*.tex` remain untouched and uncommitted.

## 7. Testing and Verification

Verified in this session, from a clean tree at the Phase 5 baseline:

- baseline before changes: `368 passed`, ruff clean, 53 files formatted, mypy
  clean in 49 source files
- final: `557 passed, 1 skipped` (`73.22s`)
- ruff: `All checks passed!`
- ruff format: `68 files already formatted`
- mypy strict: `Success: no issues found in 68 source files`

Real Docker integration, executed against Docker CLI 29.8.1 / Docker Desktop
4.93.0 with a Linux daemon (`linux/amd64`), all passing:

| Case | Expected observation | Result |
|---|---|---|
| A ordinary return | `RETURNED`, value matches the arguments | pass |
| B exception during the call | `RAISED` with `ZeroDivisionError` | pass |
| C infinite loop | `TIMEOUT` after the 2 s budget | pass |
| D output flood | `RETURNED`, `stdout_truncated`, ≤16 KiB | pass |
| E import failure inside the function | `RAISED ModuleNotFoundError` | pass |
| F import failure at module level | `IMPORT_FAILURE` | pass |
| G name resolution | `RAISED gaierror`, no network | pass |
| H import-time write | `IMPORT_FAILURE`, no host file created | pass |
| cleanup | no `edgehunter-run-*` container survives | pass |
| native fd write | `os.write(1, ...)` discarded, protocol intact | pass |

The one skip is `test_symlink_is_refused_and_not_followed`: creating a symlink
requires Developer Mode or elevation on this Windows host, so the test reports a
skip instead of passing unverified. The staging code refuses symlinks by
`is_symlink()` check and the behaviour is otherwise covered.

Manual verification of the CLI exit contract: no opt-in exits `3` without
reading the repository, an ambiguous target exits `2` naming both files, and a
successful run exits `0` with the observation report.

## 8. Known Issues / Risks

- A Docker sandbox reduces risk; it does not eliminate it. Container escapes have
  existed and a kernel or Docker vulnerability is outside EdgeHunter's control.
- Files a target creates at run time vanish with the container and are not
  reported to the host.
- Targets needing third-party packages are refused rather than installed.
- Only synchronous, undecorated, top-level functions with fully supported
  argument values are eligible. Coverage of real codebases is therefore modest,
  because most parameters remain statically unknown (D-042's conservative
  interpretation).
- A timeout kills the container without explaining what the target was doing.
- Captured output is truncated and native fd writes are discarded, so the
  capture is a sample, not a transcript.
- Symlink refusal is unit-tested but the test skips on this host, so path-through-
  symlink has not been exercised on this machine.
- Phase 5's planner emits plans only for parameters with recognizable shapes;
  `bool` and `str` parameters currently produce no plan, so those targets are
  silently unreachable rather than refused. This is inherited behaviour, not a
  Phase 6A change, and is worth a decision.

## 9. Unfinished Work

Nothing in the Phase 6A scope is unfinished. Deliberately out of scope:

- oracle evaluation of a recorded outcome (Phase 6B and later)
- failure minimization, deduplication, and reproducible-test emission
- corpus-scale validation of the eligibility gate against real repositories
- coverage for `bool` and `str` parameter plans

## 10. Next Subphase

The natural next phase is oracle evaluation: given a recorded `RETURNED` or
`RAISED` observation, evaluate the mined oracles from the same repository and
report an explicit contradiction — never a bare "failing test". It should keep
the existing separation (evaluation happens on the host against decoded data,
never inside the sandbox) and must not report a bug where the only evidence is
that an exception occurred. Two smaller items are worth doing first: deciding
whether `bool`/`str` parameters should produce plans or an explicit refusal, and
re-running the symlink staging test on an elevated host so it is no longer
skipped.

## 11. Critical Context

- Repo `edgehunter`, branch `main`, remote `origin` -> `PrathamKapoor/Faultline`.
  Published as a public snapshot; do not force-push over it.
- Python 3.10+; the venv is `.venv`. Run gates as
  `.venv\Scripts\python.exe -m pytest -q`, `-m ruff check src tests`,
  `-m ruff format src tests`, `-m mypy`.
- Docker is available with a Linux backend. The worker image
  `edgehunter-runtime:1` is built once from `runtime_context()` and cached; a
  stale image after editing the worker needs `docker image rm edgehunter-runtime:1`.
- The build context is the directory containing the `edgehunter` package, never
  the scanned repository. Changing `runtime/Dockerfile` requires the image to be
  rebuilt for integration tests to see the change.
- The staged module directory is appended to `sys.path`, never prepended, so a
  staged module cannot shadow a standard-library module.
- The base image is pinned by digest; do not float it to a tag.
- `check_relative_path` must keep both the POSIX and Windows readings. Removing
  either reintroduces a real escape.
- `run` deliberately never exits `1`. Do not "fix" that.
- `run_docker` must keep file-backed capture. Reverting to `capture_output=True`
  reintroduces unbounded host memory growth.
- The worker's `_BoundedStream` must keep discarding excess as it is written;
  clipping after the call was the original defect.
- `pyproject.toml` `package-data` must keep `edgehunter = ["runtime/Dockerfile"]`,
  or an installed distribution cannot build the worker.
- `mypy` runs over `src` and `tests` in strict mode, which is why the worker is
  type-checked despite being container-only code.

## 12. Agent Instructions

- Read `decisions.md`, `flow.md`, and this file fully before changing anything.
- Append decisions in the established format. Every bug fix needs `Symptom`,
  `Root cause`, and `Regression test`. Mark superseded decisions explicitly.
- Update `flow.md` with real module, function, and file names as the code changes.
- Never weaken an isolation property to make a test pass. A failing refusal
  test is a finding about the code, not about the test.
- Never make a run report a defect. `RAISED` is an observation.
- Verify with evidence before claiming completion: run the gates, read the
  output, and quote the numbers.
- Commit as PrathamKapoor only. No AI attribution in commits, branches, tags,
  PRs, or release notes.
- Do not commit `paper/`, `research/`, or `*.tex`.