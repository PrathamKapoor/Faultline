# EdgeHunter — Session Handoff

## 1. Current Phase

- **Phase:** Phase 3 — Structural Risk Scoring + Oracle / Property Mining
- **Subphase:** none (single-session phase)
- **Objective:** Rank callable targets with an explainable deterministic structural-risk score, and mine explicit oracle/property evidence from production source and existing tests — statically, with no execution, no models, and no defect claims.
- **Status:** complete

Repository state at handoff: branch `main`, no remote configured. Implementation commit `12d82dff46d83f06e9335987d7c50ed555929991` (`12d82df`, "Add structural risk scoring and explicit oracle mining"), followed by documentation metadata commits (see `git log`). Version remains `0.1.0`; no bump was made (D-002 makes the version literal the single source, and no decision requires a bump).

## 2. Work Completed

The repository had a green Phase 0-2 baseline (113 tests). This session added:

- **Single-parse composition root** (`src/edgehunter/pipeline.py`): `analyze_repository()` performs discovery, then one read + one `ast.parse` per file, then derives structure, semantics, boundaries, oracles, and risk from that single AST. `analysis/repository.py` gained `analyze_discovered_files()` returning `AnalyzedFile(source, module, semantics)`; `analyze_repository_semantics()` keeps its Phase 2 signature and behavior by composing it.
- **Structural risk scoring** (`analysis/risk.py` + `analysis/models.py`): `RiskFeatureKind`, `RiskContribution`, `TargetRiskProfile`, `RepositoryRiskProfile`, with five capped contributions and deterministic ranking.
- **Oracle layer** (`src/edgehunter/oracle/`): `models.py` (narrow `ProductionPropertyCandidate` and `TestExpectationCandidate`, categorical `OracleStrength`, `PropertyKind`, `OracleEvidence`, `TargetResolution`, `OracleSupport`), `production.py` (asserts + `if cond: raise` guards), `test_expectations.py` (test-file expectations + conservative target resolution), `repository.py` (dedup, resolution, per-target support).
- **Shared primitives** (D-028): inversion table moved to `analysis/models.py` as `invert_comparison_operator()` (used by boundaries and by test normalization); `scanner.iter_callable_body()` and `source_lineno()` added; `TestExpectationCandidate.__test__ = False` to stop pytest collecting the domain class.
- **CLI**: `--risk` and `--properties`, an "Oracle evidence" count block, and a fixed section order (summary, targets, boundaries, risk, properties, source issues). Exit codes unchanged (0/1/2); oracle ambiguity does not affect them.
- **Tests**: 173 total (was 113). New: `test_risk.py` (13), `test_oracle_production.py` (14), `test_oracle_test_expectations.py` (14), `test_pipeline.py` (10); extended `test_safety.py` and `test_cli.py`.
- **Docs**: decisions D-021..D-028; `flow.md` sections 2/4/9/10/11 + Phase 3 table; README status/usage/architecture/roadmap with verified output.

Non-obvious behavior worth knowing:

- Risk formula (D-022): predicates +6 (cap 30), arithmetic +4 (cap 20), boundary groups +4 (cap 20), input parameters +3 (cap 15), source span +3 per 20-line block (cap 15); total capped 100. Score is a search-priority heuristic, not a probability.
- A boundary *group* is a unique `(parameter, reference)` pair, so a float's seven generic values count once. Source span uses `lineno`/`end_lineno` (no re-read). A method's first `self`/`cls` is not an input parameter.
- Production oracle mining skips test files: an assert inside a test function is mined as a test expectation, not as a production property (avoids double counting the same statement). Phase 2 semantics still mine `assert` predicates from test files (unchanged).
- Validation guard rule: `if` body must be exactly one `raise`. Exception name recorded only when syntactically confident (`raise Name`, convention-suffixed `Name(...)`, or dotted `a.b.X(...)`); otherwise the evidence detail keeps the rendered call and `exception` stays `None`.
- Test expectations: filename rule only (`test_*.py`, `*_test.py`); `assert <call> <op> <expected>` (reversed normalized only for safe inversions) and `with pytest.raises(X):` with a single direct call body. Resolution: unique bare name → resolved; several → ambiguous; none/dotted/chained → unresolved with a reason. Unresolved evidence is retained.
- CLI section order puts source issues last (D-027).

## 3. Files Changed

All paths relative to the repository root.

```text
src/edgehunter/pipeline.py                NEW   composition root (single read+parse; RepositoryScan)
src/edgehunter/analysis/risk.py          NEW   structural risk scorer + formula constants
src/edgehunter/analysis/models.py         MODIFIED  added RiskFeatureKind/RiskContribution/TargetRiskProfile/
                                                RepositoryRiskProfile and invert_comparison_operator()
src/edgehunter/analysis/repository.py    MODIFIED  AnalyzedFile + analyze_discovered_files(); analyze_repository_semantics unchanged
src/edgehunter/analysis/boundaries.py    MODIFIED  uses shared invert_comparison_operator()
src/edgehunter/analysis/__init__.py       MODIFIED  exports risk + inversion + per-file helpers
src/edgehunter/oracle/__init__.py        NEW   oracle public interface
src/edgehunter/oracle/models.py          NEW   oracle/property models, strengths, resolution
src/edgehunter/oracle/production.py      NEW   asserts + conservative if/raise validation guards
src/edgehunter/oracle/test_expectations.py NEW  test-file expectations + conservative target resolution
src/edgehunter/oracle/repository.py      NEW   dedup, resolution, per-target support, counts
src/edgehunter/scanner/targets.py        MODIFIED  iter_callable_body()
src/edgehunter/scanner/rendering.py      MODIFIED  source_lineno()
src/edgehunter/scanner/__init__.py       MODIFIED  exports iter_callable_body
src/edgehunter/cli.py                    MODIFIED  --risk, --properties, oracle counts, section order
tests/test_risk.py                       NEW   13 risk tests (exact scores, caps, receiver, grouping, span, ordering)
tests/test_oracle_production.py          NEW   14 production-oracle tests
tests/test_oracle_test_expectations.py   NEW   14 expectation + resolution tests
tests/test_pipeline.py                   NEW   10 single-read / aggregation / independence tests
tests/test_safety.py                     MODIFIED  oracle no-execution tests; pipeline aliased import
tests/test_cli.py                        MODIFIED  --risk / --properties / oracle-count / ambiguity tests
README.md                                MODIFIED  status/usage/architecture/roadmap with verified output
decisions.md                             MODIFIED  appended D-021..D-028
flow.md                                  MODIFIED  scan flow, risk/oracle rules, diagram, Phase 3 table
handoff.md                               REWRITTEN (this file)
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
│   ├── models.py      structural models
│   └── rendering.py   render_source / source_lineno
├── analysis/          semantics + risk
│   ├── repository.py  analyze_discovered_files / AnalyzedFile / RepositorySemantics
│   ├── semantics.py   predicates/comparisons/constants/arithmetic
│   ├── boundaries.py  symbolic boundary candidates
│   ├── risk.py        structural risk scoring
│   └── models.py      semantic, boundary, and risk models
└── oracle/            oracles (what should happen)
    ├── models.py      oracle/property models
    ├── production.py  asserts + validation guards
    ├── test_expectations.py  test expectations + resolution
    └── repository.py  dedup + support summaries
```

Call chain: `cli.scan()` -> `pipeline.analyze_repository()` -> `scan_repository()` -> per file `analyze_discovered_files()` (one read+parse) -> per file `extract_production_properties`, `extract_test_expectations`, `score_module` -> `build_oracle_analysis`, `rank_profiles` -> `RepositoryScan` -> `_print_analysis` (counts, targets, boundaries, risk, properties, issues).

Data at rest: none. Output to stdout; errors to stderr. No state between runs.

Safety boundary: no imports, `eval`/`exec`/`literal_eval`/`__import__`/`import_module`/`run_path`, or subprocess anywhere; target tests are parsed, never run; exceptions are read from AST, never raised. `tests/test_safety.py` enforces this over the whole package.

Determinism: files in discovery order; facts sorted; boundary candidates sorted/deduped; risk profiles ranked by `(-score, path, line, target)`; properties/expectations deduplicated with sorted evidence. Repeated `analyze_repository()` on unchanged files compares equal (asserted).

## 5. Decisions Made

- **D-021** — Single-parse composition root `pipeline.py`; `analyze_discovered_files()`/`AnalyzedFile`; `analyze_repository_semantics()` preserved; CLI uses `analyze_repository()`.
- **D-022** — Structural risk formula (weights, caps, boundary grouping, receiver exclusion, span source, ranking) and its "search-priority heuristic, not a probability" framing.
- **D-023** — Oracle model split by evidence source (narrow production vs test records); categorical strength only; no numeric confidence.
- **D-024** — Production oracle rules: assert reuse of Phase 2 predicates + message, `if cond: raise` guard rule, exception extraction, test files excluded from production mining, no inversion of rules into contracts.
- **D-025** — Test-file filename rule and the mined expectation subset (`assert call op expected`, `pytest.raises` with a single call); chained asserts and compound bodies ignored.
- **D-026** — Conservative target resolution (unique/ambiguous/unresolved; dotted receivers never resolved; unresolved evidence retained).
- **D-027** — CLI `--risk`/`--properties`, oracle count block, fixed section order (issues last), exit codes unchanged, ambiguity not a source failure.
- **D-028** — Shared reuse primitives (inversion table, `iter_callable_body`, `source_lineno`, `__test__ = False`).

## 6. Requirements and Constraints

- **Python:** `requires-python = ">=3.10"`; only 3.13.14 exercised. No upper bound.
- **Dependencies:** runtime `typer>=0.12` only; dev extra `pytest>=8`, `ruff>=0.6`, `mypy>=1.10`. No new dependency this phase; D-014 forbids third-party parsers/solvers without a decision.
- **Safety invariant:** never import or execute analyzed code or analyzed tests; no `eval`/`literal_eval`; annotations matched as text.
- **Single-parse invariant:** one read + one `ast.parse` per file; new stages consume `analyze_discovered_files()`/`SourceFile`, never re-read.
- **Determinism:** explicit sort keys for every user-visible ordering (D-016/D-018/D-022).
- **Models:** frozen dataclasses + slots; `str`-mixin enums; source text only.
- **Terminology discipline:** risk is a search-priority heuristic, not a bug probability; oracle evidence is an explicit expectation, not a defect; nothing in Phase 3 claims a bug exists. Strength is categorical, never a percentage.
- **No inference:** no free-form docstring reasoning, no metamorphic property invention, no annotations-as-oracles, no LLM.
- **CLI:** 0 = clean, 1 = completed with syntax/read issues, 2 = invalid input; ambiguity never changes the exit code; library modules do not print.
- **Documentation process:** read `decisions.md`/`flow.md`/`handoff.md` first; append decisions; update `flow.md` with real names; rewrite `handoff.md` at session end.
- **Authorship:** commits only as `PrathamKapoor <prathamkapoor027@gmail.com>`; no attribution; never commit `paper/`, `research/`, `*.tex`.
- **Scope discipline:** do not implement Phase 4 features (action vocabulary, input materialization, test generation, execution, models) without instruction.

## 7. Testing and Verification

Baseline (before any change) and final results of the project's real commands:

```text
.venv\Scripts\python.exe -m pytest -q
  baseline -> 113 passed in 3.99s
  final    -> 173 passed in 4.09s

.venv\Scripts\python.exe -m ruff check .
  baseline -> All checks passed!
  final    -> All checks passed!

.venv\Scripts\python.exe -m ruff format --check .
  baseline -> 26 files already formatted
  final    -> 37 files already formatted

.venv\Scripts\python.exe -m mypy
  baseline -> Success: no issues found in 22 source files
  final    -> Success: no issues found in 33 source files
```

Development issues found and fixed (all in our code/tests, none pre-existing):
- ruff/mypy: three long lines, two loop-variable shadowings, an un-narrowed node, an over-broad `object` annotation in a first `pipeline.py` draft (rewritten cleanly).
- Two test-side mistakes (not code bugs): wrong risk expectations (2-line span, boundary groups from literal references) and a wrong property-candidate count (3, not 2); corrected after verifying real numbers with a probe.
- `test_full_pipeline_reads_each_file_exactly_once` initially crashed pytest with an INTERNALERROR because patching `ast.parse` also affects the test runner; rewritten to count reads (which bounds parses, since parsing needs source text).
- pytest warned it could not collect `TestExpectationCandidate`; fixed with `__test__ = False`.

CLI verified manually (installed console script and module entry point):

```text
edgehunter --version / python -m edgehunter --version  -> "edgehunter 0.1.0", exit 0
edgehunter scan <demo>                                   -> stats + Oracle evidence block, exit 0
edgehunter scan <demo> --risk                            -> ranked profiles with 5 contributions + "not a probability of a bug"
edgehunter scan <demo> --properties                      -> production rules, expectations with [resolved]/[unresolved] + reasons, support
edgehunter scan .                                        -> 33 files, 733 predicates, 85 property candidates, 339 risk targets, exit 0
edgehunter scan .\nope                                   -> "error: path does not exist: nope", exit 2
edgehunter scan <demo-with-broken.py> --properties       -> syntax issue listed last, exit 1
```

The demos live under the OS temp directory (`eh_phase3_demo`, `eh_broken`), outside the repository.

Not tested:
- Python 3.10-3.12; POSIX filesystems; permission-denied paths; symlinks; large repositories; pathological nesting (`RecursionError` from `ast.parse`, unchanged known issue); a direct `ast.parse` call-count assertion (blocked by the global-patch limitation noted above).

## 8. Known Issues / Risks

**Confirmed:**
- None blocking. All executed checks pass.

**Possible:**
- Pathological nesting can still raise `RecursionError` in `ast.parse` (unchanged from Phase 1).
- Comparisons outside predicates are not recorded as comparison facts (Phase 2 design, D-016).
- Class-definition keywords (`metaclass=`) are still not captured (Phase 1 issue).
- `.PY` uppercase suffix excluded; `os.walk` silently skips unreadable subdirectories (both unchanged).
- Help output is rich-formatted and version-dependent; tests assert substrings only.
- No `LICENSE`, no CI, no remote; README has a `<repository-url>` placeholder.
- LF->CRLF warnings on `git add` are expected on Windows (no `.gitattributes`).

## 9. Unfinished Work

Nothing from Phase 3 is unfinished. All required behaviors, tests, and documentation were implemented and verified.

Intentionally absent (later phases): semantic action vocabulary, input materialization, property-based test generation, execution/sandbox, oracle evaluation at runtime, counterexample shrinking, adaptive neighborhood search, learned search policy, full import resolution, call-graph/data-flow, structured docstring `Raises:` extraction (explicitly optional in the phase), metamorphic properties.

## 10. Next Subphase

**Phase 4 — Semantic Action Engine + Input Materialization** (per project plan).

Expected direction: a constrained action vocabulary (SET_ZERO, USE_NAN, CROSS_BOUNDARY, AT_BOUNDARY, JUST_BELOW/ABOVE_BOUNDARY, EMPTY/SINGLETON_COLLECTION, ...) that converts the symbolic `BoundaryCandidate` and parameter metadata into concrete candidate inputs — still without execution and still without an LLM.

## 11. Critical Context

- **Project venv:** `.venv/` (gitignored). Interpreter `.venv\Scripts\python.exe`; installed: edgehunter 0.1.0 (editable), typer 0.27.3 (vendors click as `typer._click`; `import click` fails), rich 15.0.0, pytest 9.1.1, ruff 0.16.10, mypy 2.4.0, Python 3.13.14.
- **Do not `import click`**; use `typer.testing.CliRunner`/`Result` (D-008).
- **Version single source:** `src/edgehunter/__init__.py` literal `__version__ = "0.1.0"`; unchanged.
- **Exit codes:** `EXIT_SCAN_ISSUES = 1`, `EXIT_INVALID_INPUT = 2`; ambiguity never affects them.
- **Single-parse invariant:** one read + one `ast.parse` per file; `pipeline.analyze_repository()` is the CLI entry; `analyze_repository_semantics()` remains public for Phase 2 consumers.
- **Risk formula constants** live in `analysis/risk.py`; boundary grouping is `(parameter, reference)`; ranking is `(-score, path, lineno, target)`.
- **Oracle strength** is categorical (`strong`); there is no numeric confidence anywhere.
- **Test files** (`test_*.py`, `*_test.py`) are excluded from production oracle mining and mined as expectations; a test file assert is not a production property.
- **Test helpers:** `tests/test_cli.py::stat_value(output, label)` for CLI stats; `tests/test_safety.py` holds the no-execution enforcement.
- **Git identity:** repo-local `PrathamKapoor` / `prathamkapoor027@gmail.com`; no remote. Phase 3 commit: `12d82df` plus documentation commits.
- **Windows shell:** PowerShell 7; commands use `.venv\Scripts\...` paths.

## 12. Agent Instructions

1. Read `decisions.md`, `flow.md`, and `handoff.md` completely before changing code; Phase 0-3 decisions are binding.
2. Keep the hard invariant: no import/exec/eval of analyzed code or tests; extend `tests/test_safety.py` when adding modules that touch source.
3. Keep one read + one parse per file; new stages consume `analyze_discovered_files()`/`SourceFile`.
4. Make the smallest coherent change; keep the CLI a formatting layer; typed, frozen models only.
5. New user-visible ordering must be deterministic with explicit sort keys.
6. Candidates are regions; oracle evidence is an explicit expectation; neither is a bug or a defect.
7. Append a `decisions.md` entry (`D-NNN`, all required fields) for every meaningful change and update `flow.md` with real names in the same commit; use `uncommitted`, then replace with the real hash in a follow-up documentation commit.
8. Before every commit run: `.venv\Scripts\python.exe -m pytest`, `-m ruff check .`, `-m ruff format --check .`, `-m mypy`; never claim results that were not observed.
9. Commit only as `PrathamKapoor <prathamkapoor027@gmail.com>`; no co-author/tool/model attribution anywhere.
10. Never commit `.venv/`, caches, egg-info, `paper/`, `research/`, `*.tex`, or temporary demos.
11. Do not start Phase 4 features (action vocabulary, materialization, generation, execution, models) unless the task explicitly instructs it.
12. At the end of the next session, rewrite `handoff.md` with all 12 sections and record real commit hashes.