# EdgeHunter — Session Handoff

## 1. Current Phase

- **Phase:** Phase 5 — Static Annotation Shapes + Safe Baselines + Property-Based Strategy Planning
- **Subphase:** none (single-session phase)
- **Objective:** Replace two deliberate Phase 4 coverage gaps (subscripted
  annotations were unrecognized; unknown collection elements got a fabricated
  `None` placeholder) with a conservative static annotation interpreter and
  honest materialization, then build the bridge from static candidates to future
  executable test plans — invocation plans and strategy plans, still with no
  execution, no models, and no defect claims.
- **Status:** complete

Repository state at handoff: branch `main`, no remote configured. Implementation
commits `4c6b922` ("Interpret modern annotation shapes statically and stop
fabricating None") and `52fb551` ("Add safe baselines, invocation plans, and
strategy plans"), followed by documentation commits `76bce8f` and this metadata
commit (see `git log`). Version remains `0.1.0`; no bump was made (D-002 makes
the version literal the single source, and no decision requires a bump).

## 2. Work Completed

The repository had a green Phase 0-4 baseline (244 tests). This session added:

- **The canonical annotation interpreter** (`scanner/annotations.py`):
  `AnnotationKind` (`UNKNOWN`, `INT`, `FLOAT`, `BOOL`, `STR`, `NONE`, `LIST`,
  `TUPLE`, `SET`, `DICT`, `UNION`) and an immutable
  `StaticAnnotation(kind, rendered, nullable, element, key, value, variadic)`.
  Built from the `ast.arg` annotation node the scanner already holds and stored
  on `ParameterInfo.shape`, so **no extra read and no extra `ast.parse`**.
- **The `None`-placeholder removal**: a singleton or repeated-element collection
  is written down only when the element shape yields a trustworthy baseline;
  otherwise the action is still proposed but its materialization is
  `UNSUPPORTED` with a reason.
- **Safe baseline synthesis** (`generation/baselines.py`): `BaselineSource`,
  `BaselineValue`, and `baseline_for(shape)`.
- **Typed invocation plans** (`generation/invocation.py`): `InvocationPlan`,
  `ArgumentAssignment`, `AssignmentOrigin`, `ArgumentPlacement`,
  `InvocationStatus`, and `plan_case()`.
- **Symbolic dependency resolution**: `x <= limit` with `limit: int` now resolves
  through the reference's baseline, reusing the same adjacency materializer.
- **Existing-test baselines**: literal-only call arguments recorded during oracle
  mining and used as observed baselines for resolved targets.
- **Framework-independent strategy plans** (`generation/strategy.py`):
  `StrategyKind`, `StrategyValue`, `ParameterStrategy`, `StrategyPlan`.
- **Repository aggregation** (`generation/planning.py`): one plan per candidate
  case, one strategy plan per target, plus a per-parameter annotation report.
- **CLI**: `--annotations`, `--plans`, `--strategies`, and a "Planning" summary
  block; all six Phase 1-4 flags and exit codes unchanged.
- **Tests**: 368 total (was 244). New: `test_annotations.py` (61),
  `test_invocation.py` (19), `test_strategy.py` (16); extended
  `test_boundaries.py`, `test_generation_actions.py`, `test_generation_cases.py`,
  `test_generation_materialize.py`, `test_cli.py`, `test_pipeline.py`,
  `test_safety.py`.
- **Docs**: decisions D-036..D-044; `flow.md` sections 2/4/11/12/13/14/15/16 plus
  the Phase 5 table; README status/known-limitation/usage/architecture/roadmap
  with verified output.

Non-obvious behavior worth knowing:

- **Nullability is a flag, not a kind** (D-036). `int | None` is
  `kind=INT, nullable=True`, so every `INT` rule — numeric boundary candidates,
  the `0` baseline, the integer strategy set — still applies. `description`
  renders it as `OPTIONAL[INT]`. The alternative (a separate `OPTIONAL` kind)
  would have made every numeric rule need a special case.
- **Shapes always keep their rendered text** (D-036). A forward reference stays
  `UNKNOWN` *and* keeps `"'User'"`, so a report can still show the original
  syntax. This was a real bug caught by
  `test_forward_reference_text_is_preserved_but_not_parsed` after a first draft
  returned a shared `UNKNOWN` singleton with `rendered=None`.
- **`bool` is a distinct kind and therefore never numeric** (D-036). This
  preserves D-016's bool-before-int discipline: `def f(x: bool)` gets no numeric
  boundary candidates.
- **`frozenset` is not recognized** (D-036). `set()` is not a `frozenset`, and
  emitting one would be a wrong-typed value, so `frozenset` is `UNKNOWN` rather
  than given a new `AnnotationKind` the phase did not ask for.
- **A nested container element uses its own baseline, not recursion** (D-037).
  `list[list[int]]`'s singleton is `[[]]`, because the inner `list[int]` receives
  its own simplest trustworthy value.
- **An unhashable element is refused** (D-037). `set[list[int]]` is `UNSUPPORTED`
  because `{[0]}` is not valid Python.
- **A strategy plan's values are deduplicated by rendered text, not equality**
  (D-043). `NaN != NaN` would make equality-based dedup behave unpredictably for
  the float specials. Text is the real identity of a concrete source-text value.
- **Filler precedence** (D-039): existing-test literal → dependency baseline →
  declared default (parameter omitted) → static type baseline → nothing. A
  declared default outranks a static baseline deliberately: if the author
  declared a default, that default is the more faithful filler.
- **A nullable parameter is never auto-filled with `None`** (D-039). It gets its
  non-null baseline, or is omitted if it has a default. `None` stays an
  adversarial choice via `SET_NONE`.
- **Strategy plans exist only for targets that have at least one action**, and
  `*args`/`**kwargs` get no strategy entry.
- **Hypothesis was deliberately not added** (D-043). No dependency was added in
  this phase; `pyproject.toml` still declares exactly one runtime dependency.

## 3. Files Changed

All paths relative to the repository root.

```text
src/edgehunter/scanner/annotations.py        NEW   AnnotationKind, StaticAnnotation,
                                                     build_static_annotation()
src/edgehunter/scanner/models.py             MODIFIED  ParameterInfo.shape
src/edgehunter/scanner/targets.py            MODIFIED  shape built during traversal
src/edgehunter/scanner/__init__.py           MODIFIED  exports the annotation layer
src/edgehunter/analysis/boundaries.py        MODIFIED  numeric applicability by shape
src/edgehunter/generation/baselines.py       NEW   BaselineSource, BaselineValue,
                                                     baseline_for()
src/edgehunter/generation/invocation.py      NEW   InvocationPlan, ArgumentAssignment,
                                                     AssignmentOrigin, ArgumentPlacement,
                                                     InvocationStatus, plan_case(),
                                                     symbolic dependency resolution
src/edgehunter/generation/strategy.py        NEW   StrategyKind, StrategyValue,
                                                     ParameterStrategy, StrategyPlan,
                                                     plan_strategies()
src/edgehunter/generation/planning.py        NEW   RepositoryPlanning, RepositoryAnnotations,
                                                     ParameterAnnotation, build_repository_planning(),
                                                     annotation_report()
src/edgehunter/generation/materialize.py     MODIFIED  shape-driven values; no None placeholder
src/edgehunter/generation/actions.py         MODIFIED  annotation applicability by shape;
                                                     SET_NONE + DUPLICATE_ELEMENT_COLLECTION;
                                                     receiver_parameter()
src/edgehunter/generation/models.py          MODIFIED  14 actions; AnnotationCategory removed;
                                                     SemanticAction.annotation replaces
                                                     annotation_category
src/edgehunter/generation/__init__.py        MODIFIED  exports baselines, invocation, planning, strategy
src/edgehunter/generation/repository.py      MODIFIED  (unchanged behaviour; signature stable)
src/edgehunter/oracle/models.py              MODIFIED  TestExpectationCandidate.literal_arguments
src/edgehunter/oracle/test_expectations.py   MODIFIED  _literal_arguments() literal-only extraction
src/edgehunter/pipeline.py                   MODIFIED  RepositoryScan.planning + .annotations;
                                                     _observed_literals()
src/edgehunter/cli.py                        MODIFIED  --annotations, --plans, --strategies,
                                                     _print_planning_counts, _print_annotations,
                                                     _print_plans, _print_strategies,
                                                     _render_assignment, section order
tests/test_annotations.py                    NEW   61 annotation-shape tests
tests/test_invocation.py                     NEW   19 invocation-plan tests
tests/test_strategy.py                       NEW   16 strategy-plan tests
tests/test_boundaries.py                     MODIFIED  nullable/unrecognized/bool applicability
tests/test_generation_actions.py             MODIFIED  shape-driven applicability; SET_NONE
tests/test_generation_cases.py               MODIFIED  bare-list and unsupported-action counts
tests/test_generation_materialize.py         MODIFIED  element baselines; no-placeholder coverage
tests/test_cli.py                            MODIFIED  help flag list; 10 new Phase 5 CLI tests
tests/test_pipeline.py                       MODIFIED  planning aggregate + no-second-read test
tests/test_safety.py                         MODIFIED  hostile-annotation, no-resolution,
                                                     hostile-repository planning tests
README.md                                    MODIFIED  status, known limitation, usage, architecture, roadmap
decisions.md                                 MODIFIED  appended D-036..D-044
flow.md                                      MODIFIED  command tree, scan flow, sections 12-16,
                                                 diagram, Phase 5 table
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
│   ├── annotations.py AnnotationKind / StaticAnnotation / build_static_annotation
│   ├── models.py      structural models + shape on ParameterInfo
│   └── rendering.py   render_source / source_lineno
├── analysis/          semantics + risk
│   ├── repository.py  analyze_discovered_files / AnalyzedFile / RepositorySemantics
│   ├── semantics.py   predicates/comparisons/constants/arithmetic
│   ├── boundaries.py  symbolic boundary candidates
│   ├── risk.py        structural risk scoring
│   └── models.py      semantic, boundary, and risk models
├── oracle/            oracles (what should happen)
│   ├── models.py      oracle/property models + literal_arguments
│   ├── production.py  asserts + validation guards
│   ├── test_expectations.py  expectations + resolution + literal args
│   └── repository.py  dedup + support summaries
└── generation/        actions + planning (what to try, and how to call it)
    ├── models.py      closed action vocabulary, statuses, completeness, cases
    ├── materialize.py concrete / symbolic / unsupported value rendering
    ├── actions.py     applicability: boundary kinds + annotation shapes
    ├── baselines.py   trustworthy filler values + provenance
    ├── invocation.py  invocation plans: placement, origins, status
    ├── strategy.py    framework-independent strategy plans
    ├── planning.py    repository aggregation + annotation report
    └── repository.py  per-target aggregation + candidate cases
```

Call chain: `cli.scan()` -> `pipeline.analyze_repository()` ->
`scan_repository()` -> per file `analyze_discovered_files()` (one read+parse,
where annotation shapes are built) -> per file `extract_production_properties`,
`extract_test_expectations`, `score_module`, `select_module_actions` ->
`build_oracle_analysis`, `rank_profiles`, `build_repository_generation`,
`build_repository_planning`, `annotation_report` -> `RepositoryScan` ->
`_print_analysis`.

Data at rest: none. Output to stdout; errors to stderr. No state between runs.

Safety boundary: no imports, `eval`/`exec`/`literal_eval`/`__import__`/
`import_module`/`run_path`, or subprocess anywhere; target tests are parsed,
never run; exceptions are read from AST, never raised; annotations are
interpreted syntactically and never resolved, parsed as code, or evaluated;
materialized values, planned arguments, and strategy values are source *text* and
are never evaluated. `tests/test_safety.py` enforces the primitive ban over the
whole package plus six hostile-source tests for the generation and planning
layers.

Determinism: files in discovery order; facts sorted; boundary candidates
sorted/deduped; risk profiles ranked by `(-score, path, line, target)`; actions
ordered by `(parameter, ACTION_KIND_ORDER, reference)`; cases ordered by
`(path, line, target, parameter, ACTION_KIND_ORDER, reference)`; plans ordered by
`(path, line, target, primary parameter, action kind, reference)`; strategies
ordered by `(path, line, target)`; strategy values deduplicated by rendered text;
properties/expectations deduplicated with sorted evidence. Repeated
`analyze_repository()` on unchanged files compares equal (asserted).

## 5. Decisions Made

- **D-036** — One canonical static annotation-shape interpreter (`scanner/annotations.py`), built during the single parse and stored on `ParameterInfo.shape`; nullability as a flag; `Union[...]` supported alongside PEP 604; `AnnotationCategory` removed. Supersedes D-030 for applicability; extends D-017's numeric rule from text to shape.
- **D-037** — Unknown element types no longer produce a `None` placeholder: the action is still proposed but materialization is `UNSUPPORTED` with a reason. Supersedes D-033.
- **D-038** — The action vocabulary grows to fourteen with `SET_NONE` and `DUPLICATE_ELEMENT_COLLECTION`; `BOUNDARY_ACTIONS` still maps all ten boundary kinds one-to-one.
- **D-039** — Safe baseline synthesis with explicit `BaselineSource` provenance and a fixed precedence (existing test → dependency → declared default → static type → nothing); a nullable shape uses its non-null baseline.
- **D-040** — Typed `InvocationPlan` above the candidate layer: exactly one adversarial assignment, placement from `ParameterKind`, declared defaults omitted without evaluation, explicit `NEEDS_RECEIVER`, four statuses with most-fundamental-gap-first precedence.
- **D-041** — Symbolic parameter references resolve through baselines via the same `materialize_relational()`; dotted references never resolve.
- **D-042** — Literal-only arguments from resolved existing tests may serve as baselines.
- **D-043** — Framework-independent `StrategyPlan`; **Hypothesis deliberately not added** (no consumer until Phase 6, and `.example()` would be nondeterministic).
- **D-044** — CLI `--annotations`/`--plans`/`--strategies`, a Planning summary block, and plans/strategies before source issues; existing flags and exit codes unchanged.

## 6. Requirements and Constraints

- **Python:** `requires-python = ">=3.10"`; only 3.13.14 exercised. No upper bound.
- **Dependencies:** runtime `typer>=0.12` only; dev extra `pytest>=8`,
  `ruff>=0.6`, `mypy>=1.10`. **No dependency was added in Phase 5** (D-043).
  D-014 forbids third-party parsers/solvers without a decision.
- **Safety invariant:** never import or execute analyzed code or analyzed tests;
  no `eval`/`literal_eval`; annotations interpreted syntactically and never
  resolved; materialized values and planned arguments stay text and are never
  run.
- **Single-parse invariant:** one read + one `ast.parse` per file. Annotation
  shapes come from the existing AST, not a second parse. Verified: a full Phase 5
  scan of this repository performed 49 reads for 49 files.
- **Determinism:** explicit sort keys for every user-visible ordering
  (D-016/D-018/D-022/D-029/D-032/D-040/D-043).
- **Models:** frozen dataclasses + slots; `str`-mixin enums; source text only.
- **Terminology discipline:** risk is a search-priority heuristic, not a bug
  probability; oracle evidence is an explicit expectation, not a defect; a
  candidate case or invocation plan is not a reproducing input until something
  runs it; a baseline is a filler, not a validity claim. Strength is categorical,
  never a percentage.
- **No fabrication:** unknown does not mean nullable. No placeholder element, no
  `None` fallback for an unknown type, no constructor call, no invented sibling
  argument.
- **No inference:** no free-form docstring reasoning, no metamorphic property
  invention, no annotations-as-oracles, no LLM.
- **CLI:** 0 = clean, 1 = completed with syntax/read issues, 2 = invalid input;
  ambiguity, symbolic actions, unsupported actions, unknown annotations, and
  incomplete plans never change the exit code; library modules do not print.
- **Documentation process:** read `decisions.md`/`flow.md`/`handoff.md` first;
  append decisions; update `flow.md` with real names; rewrite `handoff.md` at
  session end.
- **Authorship:** commits only as `PrathamKapoor <prathamkapoor027@gmail.com>`;
  no attribution; never commit `paper/`, `research/`, `*.tex`.
- **Scope discipline:** do not implement Phase 6 features (execution, runtime
  oracle evaluation, shrinking, search policy, a Hypothesis adapter) without
  instruction.

## 7. Testing and Verification

Baseline (before any change) and final results of the project's real commands:

```text
.venv\Scripts\python.exe -m pytest -q
  baseline -> 244 passed in 10.36s
  final    -> 368 passed in 6.77s

.venv\Scripts\python.exe -m ruff check .
  baseline -> All checks passed!
  final    -> All checks passed!

.venv\Scripts\python.exe -m ruff format --check .
  baseline -> 45 files already formatted
  final    -> 53 files already formatted

.venv\Scripts\python.exe -m mypy
  baseline -> Success: no issues found in 41 source files
  final    -> Success: no issues found in 49 source files
```

Single-parse property verified directly on this repository with the full Phase 5
pipeline: 49 Python files on disk, **49 reads performed**, 49 reported by the
scan — equal. No additional parse was introduced for annotation shapes.

Development issues found and fixed (all in our code/tests, none pre-existing):
- **`"{e}".format(e="False")` returns `'False'`, not `'{False}'`** (real bug):
  the braces were consumed as a format field, so a `set[bool]` singleton rendered
  as a bare `False` instead of a set. Found by
  `test_collection_values_use_the_element_baseline[set-element2...]` and fixed by
  replacing `.format` templating with explicit f-string construction
  (`f"{{{value}}}"`). This is the kind of bug that string templating invites.
- **A forward reference lost its rendered text** (real bug): the first draft
  returned a shared `UNKNOWN` singleton with `rendered=None`, so `"User"` was not
  visible in reports. Fixed by always attaching the rendered text in
  `build_static_annotation`. Caught by
  `test_forward_reference_text_is_preserved_but_not_parsed`.
- **Declared default did not outrank the static baseline** (real bug): `limit: int
  = 10` was filled with `0` instead of being omitted, contradicting the phase's
  stated precedence. Fixed by reordering `_filler_assignment`.
- **A strategy plan listed the same value twice** (real bug): `empty` and
  `empty_collection` both produced `[]`. Fixed by deduplicating on rendered text.
- `_functions_by_target()` lost the module's relative path, and a follow-up edit
  duplicated a block instead of replacing it; both caught by ruff/mypy and fixed.
- Two `object`-typed parameters with `type: ignore` escapes were introduced and
  then removed in favour of precise types (`StaticAnnotation`, `ParameterInfo`).
  Two dead constants (`_POSITIONAL_PARAMETER_KINDS`, a vestigial
  `AnnotationCategory`) were written and then deleted.
- Six wrong test expectations, each corrected against a real probe rather than a
  guess: action counts, `set`/`dict`/nested-collection rendering, two plan
  selections that ignored which parameter the action targeted, a test whose name
  no longer matched its assertion, and two test sources that were not valid
  Python (a non-default parameter after a default).
- `test_strategies_exist_only_for_targets_with_actions` initially used
  `value: int`, which does have generic numeric actions; changed to a genuinely
  unrecognized type.

CLI verified manually:

```text
edgehunter --version / python -m edgehunter --version  -> "edgehunter 0.1.0", exit 0
edgehunter scan <demo>                                   -> stats + Oracle evidence + Semantic actions + Planning blocks, exit 0
edgehunter scan <demo> --annotations                     -> per-parameter rendered -> shape mapping
edgehunter scan <demo> --plans                          -> primary action, origins, placement, status
edgehunter scan <demo> --strategies                     -> descriptor/value tables, no samples
edgehunter scan <demo> --actions                        -> per-target actions with origin/status/value/reason
edgehunter scan <demo> --candidates                     -> per-target cases with completeness and missing:
edgehunter scan .                                        -> 49 files, 1314 predicates, 187 actions, 120 plans, 62 strategies, exit 0
edgehunter scan .\nope                                   -> "error: path does not exist: nope", exit 2
```

The demos live under the OS temp directory (`p5demo`, `p5manual`, `eh_p4_demo`,
`eh_p4_edge`, `example`, `eh_broken`), outside the repository.

Manual verification of the phase's required example (a module with `count: int`,
`ratio: float`, `name: str`, `tags: list[int]`, `user: User`,
`limit: int | None = None`) confirmed: `count -> INT`, `ratio -> FLOAT`,
`name -> STR`, `tags -> LIST[INT]`, `user -> UNKNOWN`, `limit -> OPTIONAL[INT]`;
a plan on `count` with baselines for `ratio`/`name`/`tags`, `user` reported
missing, and `limit` using its declared default. No target code was executed.

Not tested:
- Python 3.10-3.12; POSIX filesystems; permission-denied paths; symlinks;
  pathological nesting (`RecursionError` from `ast.parse`, unchanged known
  issue); a direct `ast.parse` call-count assertion (blocked by the global-patch
  limitation noted in D-021); very large repositories; deep generic nesting
  beyond the one or two levels exercised.

## 8. Known Issues / Risks

**Confirmed:**
- None blocking. All executed checks pass.

**Possible:**
- Most parameters in a real codebase stay `UNKNOWN` (622 of 758 in this
  repository's own scan), because they are annotated with project types or
  subscripted generics of them. This caps baseline coverage and is the main thing
  a future phase could improve — but only by adding resolution, which is
  deliberately out of scope.
- `Sequence`, `Iterable`, `Mapping`, `MutableMapping`, `FrozenSet`,
  `Annotated`, `Literal`, `NewType`, `TypeVar`, `Protocol`, `Callable`, and
  `frozenset` are all `UNKNOWN`. The empty value for an abstract collection base
  class is a judgement call rather than a syntactic fact, so it was left out.
- A multi-type union (`int | str`) is `UNION` and generates nothing. Only one
  recognized type plus `None` is supported.
- Keyword arguments in existing test calls are not mined, only positional ones,
  so `assert add(a=1, b=2) == 3` supplies no observed baseline.
- When several tests exercise one target, the first resolved expectation supplies
  the baseline; literals are never merged across tests, because a merged
  combination would be an input no test ever used.
- Assembling an actual Python call from a plan is deliberately not implemented.
  The plan records placement, so the next phase can do it correctly — including
  the rule that an omitted positional parameter cannot precede a supplied one.
- Comparisons outside `if`/`while`/`assert` are not recorded (D-016).
- Pathological nesting can still raise `RecursionError` in `ast.parse` (unchanged
  from Phase 1).
- Class-definition keywords (`metaclass=`) are still not captured (Phase 1 issue).
- `.PY` uppercase suffix excluded; `os.walk` silently skips unreadable
  subdirectories (both unchanged).
- No `LICENSE`, no CI, no remote; README has a `<repository-url>` placeholder.
- LF->CRLF warnings on `git add` are expected on Windows (no `.gitattributes`).

## 9. Unfinished Work

Nothing from Phase 5 is unfinished. All required behaviors, tests, and
documentation were implemented and verified.

Intentionally absent (later phases): isolated execution/sandbox, runtime oracle
evaluation, counterexample shrinking, adaptive neighborhood search, learned search
policy, full import resolution, call-graph/data-flow, alias resolution
(`MyInt = int`), `Annotated`/`Literal` extraction, docstring `Raises:`
extraction, metamorphic properties, and any generator-library adapter.

## 10. Next Subphase

**Phase 6 — Isolated Execution Engine + Runtime Oracle Evaluation** (per project
plan).

That phase introduces the first real execution boundary: invocation plan →
isolated worker/subprocess → target import inside isolation → call execution →
timeout → captured return/exception → oracle evaluation →
`PASS`/`VIOLATION`/`EXCEPTION`/`TIMEOUT`.

What Phase 5 deliberately set up for it: `InvocationPlan.status` distinguishes a
plan that is locally invocable (`COMPLETE_FUNCTION`) from one that is not
(`NEEDS_RECEIVER`, `PARTIAL_ARGUMENTS`, `SYMBOLIC_DEPENDENCY`); every assignment
carries an `AssignmentOrigin` so a failure can be attributed to the adversarial
action rather than to a filler; `StrategyPlan` is a framework-independent
description that a generator adapter can consume without changing anything above;
and `missing_parameters` names exactly what a harness would still have to supply.
A Hypothesis adapter (D-043) is best added here, alongside the execution
infrastructure that would actually consume it.

## 11. Critical Context

- **Project venv:** `.venv/` (gitignored). Interpreter `.venv\Scripts\python.exe`;
  installed: edgehunter 0.1.0 (editable), typer 0.27.3 (vendors click as
  `typer._click`; `import click` fails), rich 15.0.0, pytest 9.1.1, ruff 0.16.10,
  mypy 2.4.0, Python 3.13.14. **`hypothesis` is NOT installed and was
  deliberately not added.**
- **Do not `import click`**; use `typer.testing.CliRunner`/`Result` (D-008).
- **Version single source:** `src/edgehunter/__init__.py` literal
  `__version__ = "0.1.0"`; unchanged.
- **Exit codes:** `EXIT_SCAN_ISSUES = 1`, `EXIT_INVALID_INPUT = 2`; ambiguity,
  symbolic actions, unsupported actions, unknown annotations, and incomplete
  plans never affect them.
- **Single-parse invariant:** one read + one `ast.parse` per file.
  `pipeline.analyze_repository()` is the CLI entry;
  `analyze_repository_semantics()` remains public for Phase 2 consumers.
  Annotation shapes are built in `scanner/targets._build_parameter` from the
  existing AST — do **not** introduce an annotation-string parse.
- **Annotation layer:** `AnnotationKind` and `StaticAnnotation` live in
  `scanner/annotations.py`; the shape is on `ParameterInfo.shape`. Do not write a
  second annotation interpreter, and do not go back to matching
  `parameter.annotation` text in generation code.
- **Action vocabulary:** `generation/models.py::SemanticActionKind` (14 members);
  boundary mapping `generation/actions.py::BOUNDARY_ACTIONS` (total over all ten
  `BoundaryKind` values); ordering `ACTION_KIND_ORDER`.
- **Baselines:** `generation/baselines.py::baseline_for()` returns `None` for an
  unknown shape. That `None` result is meaningful — do not substitute a value.
- **Invocation plans:** `generation/invocation.py`; one plan per candidate case,
  so `plan_count == case_count`. Status precedence is `SYMBOLIC_DEPENDENCY`,
  `PARTIAL_ARGUMENTS`, `NEEDS_RECEIVER`, `COMPLETE_FUNCTION`.
- **Shared structural constants** live in `scanner/models.py`:
  `METHOD_KINDS`, `RECEIVER_PARAMETER_NAMES`, `SINGLE_VALUE_PARAMETER_KINDS`.
- **Risk formula constants** live in `analysis/risk.py`; boundary grouping is
  `(parameter, reference)`; ranking is `(-score, path, lineno, target)`.
- **Oracle strength** is categorical (`strong`); there is no numeric confidence
  anywhere.
- **Test files** (`test_*.py`, `*_test.py`) are excluded from production oracle
  mining and mined as expectations; a test file assert is not a production
  property. Only `RESOLVED` expectations supply observed literals.
- **Test helpers:** `tests/test_cli.py::stat_value(output, label)` for CLI stats;
  `tests/test_safety.py` holds the no-execution enforcement; generation tests
  build a one-file repo in `tmp_path` and call
  `pipeline.analyze_repository()` (or `analyze_repository_semantics()` +
  `select_module_actions()` for action selection).
- **Git identity:** repo-local `PrathamKapoor` / `prathamkapoor027@gmail.com`; no
  remote. Phase 5 commits: `4c6b922`, `52fb551`, `76bce8f`, plus this metadata
  commit.
- **Windows shell:** PowerShell 7; commands use `.venv\Scripts\...` paths.

## 12. Agent Instructions

1. Read `decisions.md`, `flow.md`, and `handoff.md` completely before changing
   code; Phase 0-5 decisions are binding.
2. Keep the hard invariant: no import/exec/eval of analyzed code or tests;
   materialized values, planned arguments, and strategy values stay source text;
   extend `tests/test_safety.py` when adding modules that touch source.
3. Keep one read + one parse per file. Annotation shapes come from the existing
   AST; never introduce an annotation-string or source re-parse.
4. Prefer leaving something unknown, missing, or unsupported over inventing it.
   That is the project's core property, not a limitation to route around.
5. Never fabricate a value for an unrecognized type — in particular never a
   `None` element, key, or argument.
6. Make the smallest coherent change; keep the CLI a formatting layer; typed,
   frozen models only. No `object`-typed parameters or `type: ignore` escapes in
   production code.
7. New user-visible ordering must be deterministic with explicit sort keys.
8. When adding an action kind, extend `SemanticActionKind`,
   `ACTION_KIND_ORDER`, the applicability rule, and the materialization rule
   together, then assert `BOUNDARY_ACTIONS` is still total.
9. Append a `decisions.md` entry (`D-NNN`, all required fields) for every
   meaningful change and update `flow.md` with real names in the same commit; use
   `uncommitted`, then replace with the real hash in a follow-up documentation
   commit. Record an explicit `Supersedes: D-NNN` line when replacing prior
   behaviour.
10. Before every commit run: `.venv\Scripts\python.exe -m pytest`,
    `-m ruff check .`, `-m ruff format --check .`, `-m mypy`; never claim results
    that were not observed. Verify test expectations against a real probe rather
    than arithmetic — six were wrong this session.
11. Commit only as `PrathamKapoor <prathamkapoor027@gmail.com>`; no
    co-author/tool/model attribution anywhere.
12. Never commit `.venv/`, caches, egg-info, `paper/`, `research/`, `*.tex`, or
    temporary demos.
13. Do not start Phase 6 features (execution, runtime oracle evaluation,
    shrinking, search policy, a Hypothesis adapter) unless the task explicitly
    instructs it.
14. At the end of the next session, rewrite `handoff.md` with all 12 sections and
    record real commit hashes.
