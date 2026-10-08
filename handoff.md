# EdgeHunter — Session Handoff

## 1. Current Phase

- **Phase:** Phase 4 — Semantic Action Engine + Input Materialization
- **Subphase:** none (single-session phase)
- **Objective:** Turn the symbolic regions and parameter metadata that already
  exist into a closed vocabulary of candidate inputs — concrete, symbolic, or
  explicitly unsupported — and into single-action candidate cases, still with no
  execution, no models, and no defect claims.
- **Status:** complete

Repository state at handoff: branch `main`, no remote configured. Implementation
commit `1500640` ("Add constrained semantic actions and input materialization"),
followed by a documentation metadata commit (see `git log`). Version remains
`0.1.0`; no bump was made (D-002 makes the version literal the single source, and
no decision requires a bump).

## 2. Work Completed

The repository had a green Phase 0-3 baseline (173 tests). This session added:

- **The `generation` package** (`src/edgehunter/generation/`): a closed action
  vocabulary (`SemanticActionKind`, twelve members), applicability selection,
  value materialization, and candidate-case construction.
- **Total boundary mapping** (`generation/actions.py`): `BOUNDARY_ACTIONS` maps
  every one of the nine numeric/relational `BoundaryKind` values onto exactly one
  action, so the action list can be checked against the boundary list for
  equality.
- **Annotation-driven collection actions** (`generation/actions.py`): a bare
  builtin annotation (`list`, `tuple`, `dict`, `set`, `frozenset`) justifies
  `EMPTY_COLLECTION` and `SINGLETON_COLLECTION` with no comparison required,
  mirroring Phase 2's annotation-driven generic numeric candidates.
- **Three-state materialization** (`generation/materialize.py`): `CONCRETE` for
  literal references (exact integer neighbours, adjacent floats via
  `math.nextafter`), `SYMBOLIC` when the value depends on a name, `UNSUPPORTED`
  when no value exists (stepping around a `complex` literal).
- **Candidate cases** (`generation/repository.py`): exactly one action per case,
  never a value for another parameter, with `missing_parameters` and
  `COMPLETE`/`PARTIAL`/`SYMBOLIC` completeness.
- **Shared structural constants** (D-035): `RECEIVER_PARAMETER_NAMES` and
  `SINGLE_VALUE_PARAMETER_KINDS` moved to `scanner/models.py`; `analysis.risk`
  now reuses the public `METHOD_KINDS` instead of a private copy.
- **Pipeline and CLI**: `RepositoryScan` gained `generation`; `scan` gained
  `--actions` and `--candidates` plus a nine-line "Semantic actions" summary
  block, with actions/candidates placed before source issues.
- **Tests**: 244 total (was 173). New: `test_generation_actions.py` (21),
  `test_generation_materialize.py` (23), `test_generation_cases.py` (12);
  extended `test_pipeline.py`, `test_cli.py`, `test_safety.py`.
- **Docs**: decisions D-029..D-035; `flow.md` sections 2/4/11/12 + Phase 4
  table; README status/usage/architecture/roadmap with verified output.

Non-obvious behavior worth knowing:

- **The mapping is total** (D-029): all ten `BoundaryKind` members map 1:1 onto
  ten of the twelve actions, and the two collection actions have no boundary
  counterpart. `test_boundary_kind_maps_onto_every_action_kind` asserts
  `set(BOUNDARY_ACTIONS) == set(BoundaryKind)` and injectivity.
- **Numeric applicability is inherited, not re-implemented** (D-030): Phase 2
  already restricts generic numeric candidates to an exact `int`/`float`
  annotation, so the action layer reads the annotation only to choose between
  `0` and `0.0` renderings.
- **Unrecognized annotations yield nothing** (D-030): `list[int]`,
  `dict[str, int]`, and `Optional[int]` are all `UNKNOWN`. This is deliberate,
  and it means collection actions are rare on modern code. Two tests lock it in
  as intended behavior rather than a gap.
- **Untyped parameters still get relational actions** (D-030): applicability is
  annotation-gated only for *generic* actions; comparison-derived actions apply
  regardless of annotation, which is what keeps the phase useful on unannotated
  code.
- **Two reasons per action** (D-031): `reason` says why the action was proposed
  (the comparison or the annotation), `materialization_reason` says how the value
  was written down. Splitting them is what makes an `unsupported` action
  actionable instead of mysterious.
- **Signed literals never appear as references** (D-031): Phase 2 requires
  `simple` references and a `UnaryOp` is not simple, so `-1` cannot be a
  boundary reference. No sign handling is needed in materialization.
- **Completeness is measured against the signature** (D-032): a `PARTIAL` case
  has a concrete value but other required parameters uncovered. A required
  parameter is one with no default, not `*args`/`**kwargs`, and not a method's
  `self`/`cls`. Because the receiver rule is shared with risk scoring, both views
  of a target agree about user input.
- **`UNSUPPORTED` actions are not cases** (D-032): they stay in the action list
  with their reason, but there is nothing to hand a runner, so they do not appear
  in the candidate list. `case_count == action_count - unsupported_action_count`.
- **The singleton element is a documented placeholder** (D-033): `[None]`,
  `(None,)`, `{None: None}`, `{None}`, `frozenset({None})`. A bare `list`
  annotation does not say what the element type is, and `None` is the only value
  valid in every recognized collection. `None` keys rather than an invented
  string key. It also points the search at the target's own validation.
- **Float neighbours are genuinely adjacent** (D-031): `just below 0.5`
  materializes to the literal `0.49999999999999994`. An epsilon step such as
  `0.5 - 1e-9` was rejected because it is not adjacent and is silently wrong.
- **One pre-existing test was tightened** (D-034): `test_scan_without_targets_flag_omits_listing`
  matched the substring `"Targets"`, which now collides with the summary label
  "Targets with actions". It matches the section header `"\nTargets\n"` instead.

## 3. Files Changed

All paths relative to the repository root.

```text
src/edgehunter/generation/__init__.py        NEW   generation public interface
src/edgehunter/generation/models.py          NEW   SemanticActionKind, statuses, completeness,
                                                     ActionEvidence, SemanticAction, TargetActions,
                                                     CandidateCase, RepositoryGeneration, count properties
src/edgehunter/generation/materialize.py     NEW   Materialization result; generic numeric, relational
                                                     (int/float/other literal/name), and collection
                                                     value rendering; int/float literal parsers
src/edgehunter/generation/actions.py         NEW   BOUNDARY_ACTIONS, ANNOTATION_CATEGORIES,
                                                     classify_annotation, required_parameters,
                                                     reference_constant_kinds, select_target_actions,
                                                     select_module_actions
src/edgehunter/generation/repository.py      NEW   build_repository_generation; single-action cases,
                                                     completeness, deterministic ordering, counts
src/edgehunter/pipeline.py                   MODIFIED  RepositoryScan.generation; select_module_actions
                                                     in the same single-parse pass
src/edgehunter/cli.py                        MODIFIED  --actions, --candidates, _print_generation_counts,
                                                     _print_actions, _print_candidates, _render_value,
                                                     section order
src/edgehunter/scanner/models.py             MODIFIED  RECEIVER_PARAMETER_NAMES, SINGLE_VALUE_PARAMETER_KINDS
src/edgehunter/scanner/__init__.py           MODIFIED  exports the two new structural constants
src/edgehunter/analysis/boundaries.py        MODIFIED  uses shared SINGLE_VALUE_PARAMETER_KINDS
src/edgehunter/analysis/risk.py              MODIFIED  reuses RECEIVER_PARAMETER_NAMES + public METHOD_KINDS;
                                                 __all__ added
tests/test_generation_actions.py             NEW   21 action-selection tests
tests/test_generation_materialize.py         NEW   23 materialization tests
tests/test_generation_cases.py               NEW   12 candidate-case / aggregation tests
tests/test_pipeline.py                       MODIFIED  generation assertions, no-second-read test, independence
tests/test_cli.py                            MODIFIED  10 action/candidate CLI tests; tightened one
                                                 existing assertion
tests/test_safety.py                         MODIFIED  3 no-execution tests for the generation layer
README.md                                    MODIFIED  status/usage/architecture/roadmap with verified output
decisions.md                                 MODIFIED  appended D-029..D-035
flow.md                                      MODIFIED  command tree, scan flow, section 11 generation
                                                 rules, new diagram, Phase 4 table
handoff.md                                   REWRITTEN (this file)
```

## 4. Current Architecture / State

```text
src/edgehunter/
├── __init__.py        version literal only
├── __main__.py        python -m edgehunter -> cli.main()
├── cli.py             Typer app; presentation only
├── pipeline.py        composition root: analyze_repository -> RepositoryScan
├── scanner/           structure (what exists)
│   ├── repository.py  discovery + RepositoryAnalysis
│   ├── ast_parser.py  SourceFile / parse_source_file / build_module_analysis
│   ├── targets.py     collect_callable_nodes / iter_callable_body
│   ├── models.py      structural models + RECEIVER_PARAMETER_NAMES,
│   │                  METHOD_KINDS, SINGLE_VALUE_PARAMETER_KINDS
│   └── rendering.py   render_source / source_lineno
├── analysis/          semantics + risk
│   ├── repository.py  analyze_discovered_files / AnalyzedFile / RepositorySemantics
│   ├── semantics.py   predicates/comparisons/constants/arithmetic
│   ├── boundaries.py  symbolic boundary candidates
│   ├── risk.py        structural risk scoring
│   └── models.py      semantic, boundary, and risk models
├── oracle/            oracles (what should happen)
│   ├── models.py      oracle/property models
│   ├── production.py  asserts + validation guards
│   ├── test_expectations.py  test expectations + resolution
│   └── repository.py  dedup + support summaries
└── generation/        actions (what to try)
    ├── models.py      closed action vocabulary, statuses, completeness, cases
    ├── materialize.py concrete / symbolic / unsupported value rendering
    ├── actions.py     applicability: boundary kinds + annotation categories
    └── repository.py  per-target aggregation + candidate cases
```

Call chain: `cli.scan()` -> `pipeline.analyze_repository()` ->
`scan_repository()` -> per file `analyze_discovered_files()` (one read+parse) ->
per file `extract_production_properties`, `extract_test_expectations`,
`score_module`, `select_module_actions` -> `build_oracle_analysis`,
`rank_profiles`, `build_repository_generation` -> `RepositoryScan` ->
`_print_analysis` (counts, targets, boundaries, risk, properties, actions,
candidates, issues).

Data at rest: none. Output to stdout; errors to stderr. No state between runs.

Safety boundary: no imports, `eval`/`exec`/`literal_eval`/`__import__`/
`import_module`/`run_path`, or subprocess anywhere; target tests are parsed,
never run; exceptions are read from AST, never raised; annotations are matched as
source text, never resolved; materialized values are source *text* and are never
evaluated. `tests/test_safety.py` enforces the primitive ban over the whole
package and adds three hostile-source tests for this phase.

Determinism: files in discovery order; facts sorted; boundary candidates
sorted/deduped; risk profiles ranked by `(-score, path, line, target)`; actions
ordered by `(parameter, ACTION_KIND_ORDER, reference)`; cases ordered by
`(path, line, target, parameter, ACTION_KIND_ORDER, reference)`;
properties/expectations deduplicated with sorted evidence. Repeated
`analyze_repository()` on unchanged files compares equal (asserted).

## 5. Decisions Made

- **D-029** — Closed `SemanticActionKind` vocabulary (twelve members), total `BoundaryKind`→action mapping (all ten boundary kinds), and `ActionOrigin` (`BOUNDARY` vs `ANNOTATION`) recorded on every action.
- **D-030** — Applicability reuses Phase 2's exact-match annotation rule, extended only to bare builtin collections; subscripted/unrecognized annotations yield nothing; same single-value parameter buckets; untyped parameters still get comparison-derived actions.
- **D-031** — Three-state materialization; integer ±1 neighbours, `math.nextafter` floats rendered with `repr`, exact matching for other literals, symbolic for names, `UNSUPPORTED` for complex stepping; `int`/`float` parsing never `eval`; `reason` and `materialization_reason` kept apart.
- **D-032** — Candidate cases: one action each, no Cartesian products, no invented sibling values; `missing_parameters` plus `COMPLETE`/`PARTIAL`/`SYMBOLIC`; unsupported actions produce no case.
- **D-033** — Annotation-driven collection values use a documented `None` element placeholder for singletons; empty values per category; `None` keys rather than an invented string key.
- **D-034** — CLI `--actions`/`--candidates`, a nine-line "Semantic actions" summary block, extended section order with issues last, unchanged exit codes, symbolic values rendered in angle brackets.
- **D-035** — `RECEIVER_PARAMETER_NAMES` and `SINGLE_VALUE_PARAMETER_KINDS` move to `scanner/models.py`; `analysis.risk` reuses the public `METHOD_KINDS` (D-028 reuse pattern).

## 6. Requirements and Constraints

- **Python:** `requires-python = ">=3.10"`; only 3.13.14 exercised. No upper bound.
- **Dependencies:** runtime `typer>=0.12` only; dev extra `pytest>=8`,
  `ruff>=0.6`, `mypy>=1.10`. No new dependency this phase; D-014 forbids
  third-party parsers/solvers without a decision.
- **Safety invariant:** never import or execute analyzed code or analyzed tests;
  no `eval`/`literal_eval`; annotations matched as text; materialized values are
  text and are never run.
- **Single-parse invariant:** one read + one `ast.parse` per file; new stages
  consume `analyze_discovered_files()`/`SourceFile`, never re-read.
- **Determinism:** explicit sort keys for every user-visible ordering
  (D-016/D-018/D-022/D-029/D-032).
- **Models:** frozen dataclasses + slots; `str`-mixin enums; source text only.
- **Terminology discipline:** risk is a search-priority heuristic, not a bug
  probability; oracle evidence is an explicit expectation, not a defect; a
  candidate case is not a reproducing input until something runs it. Strength is
  categorical, never a percentage.
- **No inference:** no free-form docstring reasoning, no metamorphic property
  invention, no annotations-as-oracles, no LLM.
- **CLI:** 0 = clean, 1 = completed with syntax/read issues, 2 = invalid input;
  ambiguity, symbolic actions, and unsupported actions never change the exit
  code; library modules do not print.
- **Documentation process:** read `decisions.md`/`flow.md`/`handoff.md` first;
  append decisions; update `flow.md` with real names; rewrite `handoff.md` at
  session end.
- **Authorship:** commits only as `PrathamKapoor <prathamkapoor027@gmail.com>`;
  no attribution; never commit `paper/`, `research/`, `*.tex`.
- **Scope discipline:** do not implement Phase 5 features (test generation,
  execution, oracle evaluation, shrinking, search policy) without instruction.

## 7. Testing and Verification

Baseline (before any change) and final results of the project's real commands:

```text
.venv\Scripts\python.exe -m pytest -q
  baseline -> 173 passed in 9.52s
  final    -> 244 passed in 4.65s

.venv\Scripts\python.exe -m ruff check .
  baseline -> All checks passed!
  final    -> All checks passed!

.venv\Scripts\python.exe -m ruff format --check .
  baseline -> 37 files already formatted
  final    -> 45 files already formatted

.venv\Scripts\python.exe -m mypy
  baseline -> Success: no issues found in 33 source files
  final    -> Success: no issues found in 41 source files
```

The shared-constant move (D-035) was verified behavior-neutral on its own before
any Phase 4 code existed: 173 passed, ruff and mypy clean.

Development issues found and fixed (all in our code/tests, none pre-existing):
- The first `actions.py` draft used `object` types and a placeholder
  `_evidence_path()` helper while wiring evidence paths; rewritten to thread
  `relative_path: Path` explicitly.
- `SemanticAction` initially carried only `reason`, which silently dropped the
  materialization explanation ("why is this symbolic/unsupported?"). Split into
  `reason` and `materialization_reason` (D-031); the CLI now prints the latter for
  any non-concrete action.
- Two type errors mypy caught in the first draft: a long function signature and a
  bare `return self.kind.value` (typed `Any` for enums), fixed with an f-string
  matching the codebase's existing style.
- New test files initially used untyped helpers (`-> dict[str, tuple]`,
  unannotated `monkeypatch`), which strict mypy rejects; annotated.
- Three wrong test expectations, each corrected against a real probe rather than
  a guess: 5 actions (actually 8), case ordering compared by `kind.value`
  alphabetically instead of `ACTION_KIND_ORDER`, and a symbolic-reference test
  whose source used `return value < limit` — a comparison outside a predicate,
  which Phase 2 deliberately does not record.
- One library-behavior test initially asserted `dict[str, int]` would yield 2
  actions; it correctly yields 0, and that no-guessing rule is now asserted
  deliberately in both `test_safety.py` and `test_generation_actions.py`.
- `test_scan_without_targets_flag_omits_listing` broke on the new summary label
  "Targets with actions"; tightened to match the section header (D-034).

CLI verified manually:

```text
edgehunter --version / python -m edgehunter --version  -> "edgehunter 0.1.0", exit 0
edgehunter scan <demo>                                   -> stats + Oracle evidence + Semantic actions blocks, exit 0
edgehunter scan <demo> --actions                        -> per-target actions with origin/status/value/reason
edgehunter scan <demo> --candidates                     -> per-target cases with completeness and missing:
edgehunter scan .                                        -> 41 files, 954 predicates, 19 actions, 19 cases, exit 0
edgehunter scan .\nope                                   -> "error: path does not exist: nope", exit 2
```

The demos live under the OS temp directory (`eh_p4_demo`, `eh_p4_edge`,
`example`, `eh_broken`), outside the repository.

Not tested:
- Python 3.10-3.12; POSIX filesystems; permission-denied paths; symlinks; large
  repositories; pathological nesting (`RecursionError` from `ast.parse`,
  unchanged known issue); a direct `ast.parse` call-count assertion (blocked by
  the global-patch limitation noted in D-021).

## 8. Known Issues / Risks

**Confirmed:**
- None blocking. All executed checks pass.

**Possible:**
- Subscripted collection annotations (`list[int]`, `Optional[int]`) produce no
  actions. Deliberate under D-030, but it is the main reason collection actions
  are rare on real code and the first thing a later phase should revisit.
- A bare `list` annotation cannot say what a singleton's element should be, so
  `None` is used (D-033). A target that type-checks its elements will reject
  every singleton case; that is a search cost, not a correctness bug.
- Comparisons outside `if`/`while`/`assert` are not recorded (Phase 2, D-016), so
  a relation written as a bare `return a < b` yields no action.
- Pathological nesting can still raise `RecursionError` in `ast.parse` (unchanged
  from Phase 1).
- Class-definition keywords (`metaclass=`) are still not captured (Phase 1 issue).
- `.PY` uppercase suffix excluded; `os.walk` silently skips unreadable
  subdirectories (both unchanged).
- Help output is rich-formatted and version-dependent; tests assert substrings
  only.
- No `LICENSE`, no CI, no remote; README has a `<repository-url>` placeholder.
- LF->CRLF warnings on `git add` are expected on Windows (no `.gitattributes`).

## 9. Unfinished Work

Nothing from Phase 4 is unfinished. All required behaviors, tests, and
documentation were implemented and verified.

Intentionally absent (later phases): property-based test generation,
execution/sandbox, oracle evaluation at runtime, counterexample shrinking,
adaptive neighborhood search, learned search policy, full import resolution,
call-graph/data-flow, annotation subscript/generic resolution, structured
docstring `Raises:` extraction (explicitly optional in the phase), metamorphic
properties.

## 10. Next Subphase

**Phase 5 — property-based test generation / execution** (per project plan).

Likely direction: turn candidate cases into executable property-based tests
(Hypothesis strategies built from the action vocabulary), then execute them in
isolation against the mined oracles. The two things this phase set up for that
are `COMPLETE` vs `PARTIAL` completeness (a `PARTIAL` case needs a strategy for
its `missing_parameters`) and the `SYMBOLIC` status (a symbolic case becomes
runnable once a later stage supplies the referenced value). Nothing in this phase
assumes a particular generator library.

## 11. Critical Context

- **Project venv:** `.venv/` (gitignored). Interpreter `.venv\Scripts\python.exe`;
  installed: edgehunter 0.1.0 (editable), typer 0.27.3 (vendors click as
  `typer._click`; `import click` fails), rich 15.0.0, pytest 9.1.1, ruff 0.16.10,
  mypy 2.4.0, Python 3.13.14.
- **Do not `import click`**; use `typer.testing.CliRunner`/`Result` (D-008).
- **Version single source:** `src/edgehunter/__init__.py` literal
  `__version__ = "0.1.0"`; unchanged.
- **Exit codes:** `EXIT_SCAN_ISSUES = 1`, `EXIT_INVALID_INPUT = 2`; ambiguity,
  symbolic actions, and unsupported actions never affect them.
- **Single-parse invariant:** one read + one `ast.parse` per file;
  `pipeline.analyze_repository()` is the CLI entry;
  `analyze_repository_semantics()` remains public for Phase 2 consumers.
- **Risk formula constants** live in `analysis/risk.py`; boundary grouping is
  `(parameter, reference)`; ranking is `(-score, path, lineno, target)`.
- **Action vocabulary** is `generation/models.py::SemanticActionKind`; the
  boundary mapping is `generation/actions.py::BOUNDARY_ACTIONS`; ordering is
  `ACTION_KIND_ORDER`. Generation does not read risk scores or oracle support,
  and neither is derived from it.
- **Shared structural constants** live in `scanner/models.py`:
  `METHOD_KINDS`, `RECEIVER_PARAMETER_NAMES`, `SINGLE_VALUE_PARAMETER_KINDS`.
- **Oracle strength** is categorical (`strong`); there is no numeric confidence
  anywhere.
- **Test files** (`test_*.py`, `*_test.py`) are excluded from production oracle
  mining and mined as expectations; a test file assert is not a production
  property.
- **Test helpers:** `tests/test_cli.py::stat_value(output, label)` for CLI stats;
  `tests/test_safety.py` holds the no-execution enforcement; generation tests
  build a one-file repo in `tmp_path` and call `analyze_repository_semantics()` +
  `select_module_actions()` (or `pipeline.analyze_repository()` for aggregation).
- **Git identity:** repo-local `PrathamKapoor` / `prathamkapoor027@gmail.com`; no
  remote. Phase 4 commit: `1500640` plus documentation commits.
- **Windows shell:** PowerShell 7; commands use `.venv\Scripts\...` paths.

## 12. Agent Instructions

1. Read `decisions.md`, `flow.md`, and `handoff.md` completely before changing
   code; Phase 0-4 decisions are binding.
2. Keep the hard invariant: no import/exec/eval of analyzed code or tests;
   materialized values stay source text; extend `tests/test_safety.py` when adding
   modules that touch source.
3. Keep one read + one parse per file; new stages consume
   `analyze_discovered_files()`/`SourceFile`.
4. Make the smallest coherent change; keep the CLI a formatting layer; typed,
   frozen models only.
5. New user-visible ordering must be deterministic with explicit sort keys.
6. Candidates are regions; oracle evidence is an explicit expectation; a
   candidate case is not a reproducing input. None of them is a bug or a defect.
7. When adding a new action kind, extend `SemanticActionKind`,
   `ACTION_KIND_ORDER`, and the applicable materialization rule together, then
   assert that `BOUNDARY_ACTIONS` is still total.
8. Append a `decisions.md` entry (`D-NNN`, all required fields) for every
   meaningful change and update `flow.md` with real names in the same commit; use
   `uncommitted`, then replace with the real hash in a follow-up documentation
   commit.
9. Before every commit run: `.venv\Scripts\python.exe -m pytest`,
   `-m ruff check .`, `-m ruff format --check .`, `-m mypy`; never claim results
   that were not observed. Verify test expectations against a real probe rather
   than arithmetic guesses — three were wrong this session.
10. Commit only as `PrathamKapoor <prathamkapoor027@gmail.com>`; no
    co-author/tool/model attribution anywhere.
11. Never commit `.venv/`, caches, egg-info, `paper/`, `research/`, `*.tex`, or
    temporary demos.
12. Do not start Phase 5 features (generation, execution, oracle evaluation,
    shrinking, search policy) unless the task explicitly instructs it.
13. At the end of the next session, rewrite `handoff.md` with all 12 sections and
    record real commit hashes.
