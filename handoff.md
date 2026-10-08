# EdgeHunter — Session Handoff

## 1. Current Phase

- **Phase:** Phase 2 — Static Semantic Analysis + Deterministic Boundary Mining
- **Subphase:** none (single-session phase)
- **Objective:** Extract branch predicates, comparisons, constants, and simple arithmetic relationships from Python functions, then deterministically derive symbolic boundary candidates — with no AI, no execution, and no property or bug claims.
- **Status:** complete

Repository state at handoff: branch `main`, no remote configured. Implementation commit `b538557adcda0570aa0b2d0b089ccb6a0a4bd399` (`b538557`, "Add static semantic analysis and deterministic boundary mining"), followed by documentation metadata commits (see `git log`). Version remains `0.1.0`; no bump was made (D-002 makes the version literal the single source, and no decision requires a bump).

## 2. Work Completed

The repository had a green Phase 0/1 baseline (discovery + structural AST extraction, 52 tests). This session added:

- **Single-parse scanner refactor** (`src/edgehunter/scanner/`): `SourceFile` (holds the parsed `ast.Module` or a structured issue), `parse_source_file()`, `build_module_analysis()`, `CallableNode` + `collect_callable_nodes()`, and a new shared `rendering.py` holding `render_source()`. `analyze_python_file()` remains as the Phase 1 wrapper; all 52 Phase 1 tests passed unchanged right after the refactor.
- **New `analysis` package** (`src/edgehunter/analysis/`):
  - `models.py`: `ExpressionKind`, `ComparisonOperator`, `ArithmeticOperator`, `ArithmeticContext`, `PredicateKind`, `ConstantKind`, `ExpressionInfo`, `ComparisonInfo`, `PredicateInfo`, `ArithmeticInfo`, `ConstantInfo`, `TargetSemantics`, `BoundaryKind`, `BoundaryOrigin`, `BoundaryCandidate` (with `description`), `TargetBoundaries`, `ModuleSemantics`, `BOUNDARY_KIND_ORDER`.
  - `semantics.py`: `extract_module_semantics()` + `_FactCollector`.
  - `boundaries.py`: `mine_target_boundaries()`, `mine_module_boundaries()`.
  - `repository.py`: `RepositorySemantics` and `analyze_repository_semantics()`.
- **CLI**: semantic statistics block in the default summary, new `--boundaries` view, structural counts read from `analysis.structure`. Exit codes unchanged (0/1/2). No `--semantics` switch was added (D-019).
- **Tests**: 113 total (was 52). New: `tests/test_semantics.py` (24), `tests/test_boundaries.py` (24), `tests/test_semantic_repository.py` (9), plus semantic no-execution test and CLI additions; `tests/test_cli.py` gained 4 tests and one help assertion; `tests/test_safety.py` gained a hostile-source semantic test and `literal_eval` in the forbidden-call set.
- **Docs**: decisions D-015..D-020 appended; `flow.md` sections 2/4/7/8/9 updated plus a Phase 2 table; README status/usage/architecture/roadmap rewritten with verified output.

Non-obvious behavior worth knowing:

- Each file is read and parsed exactly once per scan; the same AST feeds structural and semantic extraction (`test_each_source_file_is_read_once` monkeypatches `_read_source` to count reads).
- Semantic facts use the scanner's own `collect_callable_nodes()`, so qualified names cannot drift between structure and semantics.
- Comparisons come only from predicates (`if`/`while`/`assert`); comparison facts inside boolean operands are included; chained comparisons are decomposed into adjacent pairs.
- Arithmetic is recorded as **maximal** subexpressions: `(x + y) * 2` is one `ArithmeticInfo` with operators `(*, +)`, so counts are not double-counted.
- `...` (Ellipsis) is not recorded as a constant (stub placeholder); `bool` is classified before `int`.
- Boundary references are symbolic; the safe-reference rule is AST-based (`self.limit` yes; `f().x`, `items[i]` no).
- Scanning EdgeHunter's own source yields only 6 boundary candidates because almost nothing is annotated exactly `int`/`float` — the conservative rule working as designed.

## 3. Files Changed

All paths relative to the repository root.

```text
src/edgehunter/analysis/__init__.py          NEW   public analysis interface
src/edgehunter/analysis/models.py            NEW   semantic + boundary immutable models and enums
src/edgehunter/analysis/semantics.py         NEW   AST -> predicates/comparisons/constants/arithmetic
src/edgehunter/analysis/boundaries.py        NEW   facts + parameters -> boundary candidates
src/edgehunter/analysis/repository.py        NEW   analyze_repository_semantics (single read/parse per file)
src/edgehunter/scanner/rendering.py          NEW   shared render_source() (ast.unparse + fallback)
src/edgehunter/scanner/ast_parser.py         MODIFIED  SourceFile, parse_source_file, build_module_analysis
src/edgehunter/scanner/targets.py            MODIFIED  CallableNode, collect_callable_nodes, shared rendering
src/edgehunter/scanner/__init__.py           MODIFIED  exports new pipeline pieces
src/edgehunter/cli.py                        MODIFIED  semantic stats, --boundaries, structural counts from .structure
tests/test_semantics.py                      NEW   24 semantic extraction tests (A-N and scope/literal cases)
tests/test_boundaries.py                     NEW   24 boundary mining tests (O-V, conservative refs)
tests/test_semantic_repository.py            NEW   9 aggregation/determinism/single-read tests
tests/test_safety.py                         MODIFIED  hostile-source semantic test; literal_eval forbidden
tests/test_cli.py                            MODIFIED  semantic stats + --boundaries tests
README.md                                    MODIFIED  status/usage/architecture/roadmap with verified output
decisions.md                                 MODIFIED  appended D-015..D-020
flow.md                                      MODIFIED  scan flow, new semantic/boundary rules, diagram, Phase 2 table
handoff.md                                   REWRITTEN (this file)
```

## 4. Current Architecture / State

```text
src/edgehunter/
├── __init__.py        version literal only
├── __main__.py        python -m edgehunter -> cli.main()
├── cli.py             Typer app; presentation only
├── scanner/           structural layer (what exists)
│   ├── __init__.py    public interface
│   ├── repository.py  discovery (os.walk + pruning) + RepositoryAnalysis
│   ├── ast_parser.py  SourceFile / parse_source_file / build_module_analysis
│   ├── targets.py     collect_callable_nodes + CallableNode (scope-aware)
│   ├── models.py      immutable structural models
│   └── rendering.py   render_source()
└── analysis/          semantic layer (what the code cares about)
    ├── __init__.py    public interface
    ├── repository.py  analyze_repository_semantics / RepositorySemantics
    ├── semantics.py   extract_module_semantics / _FactCollector
    ├── boundaries.py  mine_target_boundaries / mine_module_boundaries
    └── models.py      immutable semantic + boundary models
```

Call chain: `cli.scan()` -> `analyze_repository_semantics()` -> `scan_repository()` (discovery) -> per file `parse_source_file()` (read + parse once) -> `build_module_analysis()` (structure) and `extract_module_semantics()` (facts + `mine_module_boundaries()`) -> `RepositorySemantics` -> `_print_analysis()` (+ `_print_issues`, `_print_targets`, `_print_boundaries`).

Data at rest: none. Output to stdout; errors to stderr. No state between runs.

Safety boundary: `analysis` reads text and walks ASTs only; no imports, `eval`/`exec`/`literal_eval`/`__import__`/`import_module`/`run_path`, or subprocess. `tests/test_safety.py` enforces it over the whole package.

Determinism: files in discovery order; targets and facts sorted; candidates sorted by `(parameter, kind rank, reference)` and deduplicated by `(parameter, kind, reference)` with sorted evidence. Repeated scans are equal (asserted).

## 5. Decisions Made

- **D-015** — `analysis` layer + single-parse split (`SourceFile`, `parse_source_file`, `build_module_analysis`, `collect_callable_nodes`, `scanner/rendering.py`); `analysis` depends on `scanner`, never the reverse; no new dependency.
- **D-016** — Semantic extraction rules: predicates from `if`/`while`/`assert`; comparisons in boolean operands; chained decomposition; `bool` before `int`; `...` skipped; signed literals via explicit helper; maximal arithmetic only; no folding; scope stop at nested defs/classes/lambdas.
- **D-017** — Boundary vocabulary and derivation: inequality -> below/at/above; equality -> at; identity/membership -> none; generic numeric only for literal `int`/`float` annotations on positional/keyword-only params; symbolic references; `origin`/`reason`/evidence recorded.
- **D-018** — Normalization and conservative rules: parameter-subject normalization with the exact inversion table (never invert `is`/`in`); left side wins when both are parameters; safe-reference rule from the AST; dedup key; `(parameter, kind rank, reference)` ordering.
- **D-019** — CLI semantic statistics and `--boundaries`; exit codes unchanged; no `--semantics` switch.
- **D-020** — Deliberately unsupported semantics recorded as limitations (comprehension filters, lambdas, `match`/`case`, `try`/`except` flow, conditional expressions, class/module-level conditions, non-`int`/`float` annotations, bitwise aug-assign).

## 6. Requirements and Constraints

- **Python:** `requires-python = ">=3.10"`; only 3.13.14 exercised. No upper bound.
- **Dependencies:** runtime `typer>=0.12` only; dev extra `pytest>=8`, `ruff>=0.6`, `mypy>=1.10`. No new dependency this phase; D-014 forbids third-party parsers/solvers without a new decision.
- **Safety invariant:** never import or execute analyzed code; annotations are matched as text, never resolved; no `eval`/`literal_eval`.
- **Determinism:** semantic facts and boundary candidates must be stably ordered; sort keys must be explicit (D-016/D-018).
- **Models:** frozen dataclasses + slots; `str`-mixin enums; source text only; no runtime values.
- **CLI:** 0 = clean scan, 1 = completed with syntax/read issues, 2 = invalid input; library modules do not print; a bug in semantic analysis must never be converted into a source issue.
- **Language discipline:** boundary candidates are "candidates"/"regions"/"evidence" — never "bugs"/"vulnerabilities"; an `assert` predicate is syntax, not an inferred property.
- **Documentation process:** read `decisions.md`, `flow.md`, `handoff.md` first; append decisions (never edit old entries except commit-hash metadata); update `flow.md` with real names; rewrite `handoff.md` at session end.
- **Authorship:** commits only as `PrathamKapoor <prathamkapoor027@gmail.com>`; no attribution lines; never commit `paper/`, `research/`, `*.tex`.
- **Scope discipline:** do not implement Phase 3 features (risk scoring, property/oracle mining, test generation, execution, models) without the corresponding phase instruction.

## 7. Testing and Verification

Baseline (before any change) and final results of the project's real commands:

```text
.venv\Scripts\python.exe -m pytest -q
  baseline -> 52 passed in 4.37s
  final    -> 113 passed in 3.23s

.venv\Scripts\python.exe -m ruff check .
  baseline -> All checks passed!
  final    -> All checks passed!

.venv\Scripts\python.exe -m ruff format --check .
  baseline -> 17 files already formatted
  final    -> 26 files already formatted

.venv\Scripts\python.exe -m mypy
  baseline -> Success: no issues found in 13 source files
  final    -> Success: no issues found in 22 source files
```

During development, two check failures were real and fixed:
- `ruff check` and `mypy` flagged `origin`/`parameter` scope and loop-variable shadowing in `boundaries.py` and an un-narrowed `node` in `semantics.py`; all fixed without weakening typing.
- Two test expectations were wrong (not the code): a miscounted candidate total in `test_semantic_repository.py`-style accounting (17 vs 9 in `test_scan_reports_semantic_statistics`) and a missing generic-candidate expectation in `test_parameter_to_parameter_boundary_stays_symbolic`.
- `ruff format` reformatted 11 files (line wrapping and multi-line string collapse); no behavior changed.

CLI verified manually (installed console script and module entry point):

```text
.venv\Scripts\edgehunter.exe --version                  -> "edgehunter 0.1.0", exit 0
.venv\Scripts\python.exe -m edgehunter --version        -> "edgehunter 0.1.0", exit 0
.venv\Scripts\edgehunter.exe scan --help                 -> lists PATH, --targets, --boundaries, exit 0
.venv\Scripts\edgehunter.exe scan .                      -> 22 files, 465 predicates, 408 comparisons,
                                                            88 arithmetic, 6 boundary candidates, exit 0
.venv\Scripts\edgehunter.exe scan . --targets            -> target listing unchanged, exit 0
.venv\Scripts\edgehunter.exe scan . --boundaries         -> Boundary sections, exit 0
.venv\Scripts\edgehunter.exe scan .\nope                 -> "error: path does not exist: nope", exit 2
.venv\Scripts\edgehunter.exe scan <temp demo>            -> Account.withdraw: [comparison] just below/at/just above
                                                            self.balance + [numeric] NaN/+Infinity; exit 0
```

The temp demo was `C:\Users\LENOVO\AppData\Local\Temp\opencode\eh_phase2_demo\bank.py` (outside the repository). README scan output was re-verified against a fresh run after all test files existed.

Not tested:

- Python 3.10, 3.11, 3.12 (only 3.13.14 present).
- POSIX filesystems (path display, symlinks, permissions).
- Permission-denied files/dirs; unreadable subdirectory behavior (still Phase 0 `os.walk` default).
- Pathologically deep source nesting (`RecursionError` from `ast.parse`; unchanged known issue).
- Very large repositories (no performance measurements).
- Encodings beyond the Phase 1 cases.

## 8. Known Issues / Risks

**Confirmed:**

- None blocking. All executed checks pass; no `RecursionError` path was exercised.

**Possible:**

- Pathological nesting can still raise `RecursionError` in `ast.parse` (unchanged from Phase 1).
- Comparisons outside predicates (e.g. `y = x > 3`) are not recorded as comparisons by design (D-016 consequences).
- Class-definition keywords (`metaclass=`) are still not captured (Phase 1 issue).
- `.PY` uppercase suffix excluded; `os.walk` silently skips unreadable subdirectories (both unchanged).
- Help output is rich-formatted and version-dependent; tests assert substrings only.
- No `LICENSE`, no CI, no remote; README still has a `<repository-url>` placeholder.
- LF->CRLF warnings on `git add` are expected on Windows (no `.gitattributes`).

## 9. Unfinished Work

Nothing from Phase 2 is unfinished. All required behaviors, tests, and documentation were implemented and verified.

Intentionally absent (later phases): risk scoring, target prioritization, property/oracle extraction (assertions, docstrings, test expectations), metamorphic/property candidates, semantic action engine, test generation, mutation testing, LLM integration, training, RAG/vector stores, failure retrieval, Docker/sandbox execution, counterexample shrinking, severity ranking, CI, API/UI, IDE plugin, multi-language parsing.

## 10. Next Subphase

**Phase 3 — Risk Scoring + Oracle / Property Mining** (per project plan).

Expected direction: extract explicit assertions, validation conditions, docstring claims, and existing test expectations; classify oracle strength; define risk features and target prioritization — building on `TargetSemantics` and `BoundaryCandidate`. Still static; no execution and no models unless a later phase says so.

## 11. Critical Context

- **Project venv:** `.venv/` (gitignored). Interpreter `.venv\Scripts\python.exe`; installed: edgehunter 0.1.0 (editable), typer 0.27.3 (vendors click as `typer._click`; `import click` fails), rich 15.0.0, pytest 9.1.1, ruff 0.16.10, mypy 2.4.0, Python 3.13.14.
- **Do not `import click`** in code or tests; use `typer.testing.CliRunner`/`Result` (D-008).
- **Version single source:** `src/edgehunter/__init__.py` literal `__version__ = "0.1.0"`; unchanged this phase.
- **Exit codes:** `EXIT_SCAN_ISSUES = 1`, `EXIT_INVALID_INPUT = 2` in `cli.py`; semantic bugs must not be masked as source issues.
- **Single-parse invariant:** read/parse each file once; both extractors share the AST. Keep it that way when adding new analysis.
- **Ordering rules:** discovery = files-before-subdirectories (Phase 0 contract); facts sorted by discovery/target line; candidates sorted by `(parameter, kind rank, reference)`.
- **Test helpers:** `tests/test_cli.py::stat_value(output, label)` for CLI stats; `tests/test_safety.py` holds the no-execution enforcement over the whole package.
- **Ruff formats Markdown Python fences:** `ruff format --check .` counts README/decisions/flow/handoff.
- **Git identity:** repo-local `PrathamKapoor` / `prathamkapoor027@gmail.com`; no remote. Phase 2 commit: `b538557` plus documentation commits.
- **Windows shell:** PowerShell 7; commands use `.venv\Scripts\...` paths.

## 12. Agent Instructions

1. Read `decisions.md`, `flow.md`, and `handoff.md` completely before changing code; Phase 0/1/2 decisions are binding.
2. Keep the hard invariant: no import/exec/eval of analyzed code; extend `tests/test_safety.py` when new modules are added.
3. Keep one read/parse per file; new analysis consumes the existing AST, never re-reads or re-parses.
4. Make the smallest coherent change; keep the CLI a formatting layer and library modules print-free; typed, frozen models only.
5. New user-visible ordering must be deterministic with explicit sort keys.
6. Candidates are regions, never bugs; `assert` predicates are syntax, never inferred properties.
7. Append a `decisions.md` entry (`D-NNN`, all required fields) for every meaningful change and update `flow.md` with real names in the same commit; use `uncommitted`, then replace with the real hash in a follow-up documentation commit.
8. Before every commit run: `.venv\Scripts\python.exe -m pytest`, `-m ruff check .`, `-m ruff format --check .`, `-m mypy`; never claim results that were not observed.
9. Commit only as `PrathamKapoor <prathamkapoor027@gmail.com>`; no co-author/tool/model attribution anywhere.
10. Never commit `.venv/`, caches, egg-info, `paper/`, `research/`, or `*.tex`.
11. Do not start Phase 3 features (risk scoring, property/oracle mining, test generation, execution, models) unless the task explicitly instructs it.
12. At the end of the next session, rewrite `handoff.md` with all 12 sections and record real commit hashes.