# EdgeHunter — Decision Log

This file records *why* EdgeHunter is built the way it is. Entries are
append-only: existing entries are never edited or deleted, only superseded by
a new entry that references them. Decisions are referenced from `flow.md` and
`handoff.md` by their `D-NNN` number.

---

### D-001: Adopt a `src/` layout for the `edgehunter` package

- Date / phase / commit: 2026-10-08, Phase 0 (Repository Bootstrap and Engineering Foundation), ac9b228
- Context: The repository started empty. An installable package plus a console entry point are required, and later phases will add further subpackages (analysis, oracle, search, generators, execution, findings, ...). The physical layout had to be chosen before any code was written.
- Decision: Keep all importable code under `src/edgehunter/`; tests live in a separate top-level `tests/` directory.
- Why: The `src/` layout prevents accidental imports of working-copy modules (for example when running tools from the repository root) and forces contributors to validate the packaging configuration by installing the project. It is the layout the Python Packaging User Guide recommends for packages intended for distribution.
- Alternatives considered: Flat `edgehunter/` at repository root — works, but a stray `import edgehunter` can silently pick up the working tree instead of the installed package and hide packaging errors.
- Consequences: The CLI and tests require an editable install (`pip install -e ".[dev]"`) before `import edgehunter` works from an arbitrary directory. All future modules go under `src/edgehunter/<subpackage>/`.
- Files: pyproject.toml, src/edgehunter/, tests/

### D-002: Use setuptools with a dynamic version sourced from `edgehunter.__version__`

- Date / phase / commit: 2026-10-08, Phase 0 (Repository Bootstrap and Engineering Foundation), ac9b228
- Context: Packaging is defined in `pyproject.toml`. The version must be available both to the build backend and at runtime for `edgehunter --version`.
- Decision: Build backend `setuptools>=68` with `dynamic = ["version"]` and `[tool.setuptools.dynamic] version = { attr = "edgehunter.__version__" }`. `src/edgehunter/__init__.py` contains the literal `__version__ = "0.1.0"`.
- Why: setuptools is the most widely available backend and supports PEP 660 editable installs; reading the version via `attr` keeps a single source of truth. Because `__version__` is a simple literal, setuptools reads it without importing the package, so no runtime dependency is needed at build time.
- Alternatives considered: Static `version = "0.1.0"` in `pyproject.toml` — duplicates the version in two files that can drift; `hatchling` backend — modern and fast, but adds a less ubiquitous build requirement with no benefit at this stage; `flit` — infers metadata from docstrings, which conflicts with keeping `__init__.py` trivial.
- Consequences: Version bumps happen in exactly one place: `src/edgehunter/__init__.py`. `__init__.py` must not import runtime dependencies.
- Files: pyproject.toml, src/edgehunter/__init__.py

### D-003: Build the CLI with Typer

- Date / phase / commit: 2026-10-08, Phase 0 (Repository Bootstrap and Engineering Foundation), ac9b228
- Context: The CLI must expose `edgehunter --help`, `edgehunter --version`, and `edgehunter scan PATH` now, and is expected to grow nested commands later (`show`, `reproduce`, `report`, `export-test`). It also has to be testable in-process.
- Decision: Use Typer (`typer>=0.12`) as the CLI framework. Console entry point `edgehunter = "edgehunter.cli:main"`, where `main()` invokes the `typer.Typer` application. One root callback hosts the eager `--version` option; `scan` is a command under the same application.
- Why: Typer derives argument parsing from type hints, makes subcommand registration a decorator, and ships `typer.testing.CliRunner`, which invokes the application in-process and captures stdout and stderr separately. This keeps `src/edgehunter/cli.py` small and directly testable.
- Alternatives considered: `argparse` (standard library, zero dependencies) — nested subparsers, help formatting, and in-process exit handling all become manual code as commands accumulate; `click` directly — Typer is a thin layer over click, and using click directly adds boilerplate (explicit parameter declarations) with no capability we need beyond what Typer provides.
- Consequences: Runtime dependency on `typer` (and transitively `click`). CLI tests use `typer.testing.CliRunner`, not `click.testing.CliRunner`: the latter requires the underlying click command object and raises `AttributeError: 'Typer' object has no attribute 'name'` when handed a `typer.Typer` (verified with click 8.3.1 / typer 0.27.2). Shell-completion options are disabled (`add_completion=False`) to keep help output focused.
- Package: typer >=0.12 (verified working with typer 0.27.2 and click 8.3.1)
- Why this package: type-hint driven command definitions, subcommands, and an in-process test runner.
- Packages compared: argparse (standard library; more manual code for multi-command CLIs), click (more verbose API; Typer is built on it).
- Files: pyproject.toml, src/edgehunter/cli.py, tests/test_cli.py

### D-004: `scan` performs filesystem discovery only, with pruned non-source directories

- Date / phase / commit: 2026-10-08, Phase 0 (Repository Bootstrap and Engineering Foundation), ac9b228
- Context: The `scan` command must not pretend that analysis exists, but this phase also asks for the smallest genuinely useful behavior rather than a pure stub. Repository discovery (finding Python sources) is safe because it touches only the filesystem.
- Decision: `scan_repository()` walks a directory with `os.walk` (symlinks not followed), prunes directories named in `EXCLUDED_DIRECTORY_NAMES` and directories ending in `.egg-info`, collects `*.py` files in sorted order, and returns an immutable `ScanSummary`. A single `*.py` file is accepted as a target; any other regular file is rejected with `UnsupportedTargetError`. The CLI prints the summary and explicitly states that repository analysis is not implemented yet.
- Why: Real discovery is small, deterministic, and testable, and it establishes the `scanner` package that later phases build on. `os.walk` defaults to `followlinks=False`, which avoids symlink cycles and keeps the scan inside the requested tree. Exclusion by explicit name/suffix is conservative: unknown directories are always scanned.
- Alternatives considered: A pure placeholder that prints "not implemented" — no behavior to test, no extension point; AST parsing or module import — explicitly out of scope for this phase and unsafe (importing target code executes it); recursive `Path.rglob("*.py")` — cannot prune directory subtrees efficiently, while `os.walk` allows in-place pruning via the `dirnames` list.
- Consequences: The exclusion list is policy, not truth; repositories with unconventional vendor directories will include extra files. Discovery never imports or executes target code. Suffix matching is case-sensitive (a file named `X.PY` on Windows is not collected) — intentional, matches Python's module naming, revisit if it becomes an issue.
- Files: src/edgehunter/scanner/repository.py, src/edgehunter/scanner/__init__.py, src/edgehunter/cli.py, tests/test_repository_scanner.py, tests/test_cli.py

### D-005: Exit codes: 0 for success, 2 for invalid user input

- Date / phase / commit: 2026-10-08, Phase 0 (Repository Bootstrap and Engineering Foundation), ac9b228
- Context: Invalid input (missing path, non-Python file target) must fail with a non-zero status and without a traceback, while unexpected programming errors must stay visible.
- Decision: The `scan` command catches `ScanError` subclasses, prints `error: <message>` to stderr, and exits with code 2 (`EXIT_INVALID_INPUT` in `src/edgehunter/cli.py`). Unexpected exceptions are not caught: they surface as tracebacks with a non-zero exit.
- Why: Code 2 matches the convention used by argparse and click for usage errors, and keeps user errors distinguishable from internal bugs.
- Alternatives considered: Exit 1 for everything — loses the distinction between bad input and bugs; exit 0 with an error message — wrong, scripts could not detect failure.
- Consequences: Future commands should reuse code 2 for invalid input; new error families should subclass `ScanError` or a shared base when one exists.
- Files: src/edgehunter/cli.py, tests/test_cli.py

### D-006: Development tooling via the `dev` extra: pytest, ruff, mypy

- Date / phase / commit: 2026-10-08, Phase 0 (Repository Bootstrap and Engineering Foundation), ac9b228
- Context: This phase requires working test infrastructure, appropriate lint/format configuration, and typed code, without creating multiple competing dependency files.
- Decision: Declare development dependencies in `pyproject.toml` under `[project.optional-dependencies] dev` (`pytest>=8`, `ruff>=0.6`, `mypy>=1.10`), installed with `pip install -e ".[dev]"`. Configuration for pytest, ruff, and mypy lives in the same `pyproject.toml`.
- Why: A single authoritative dependency definition; extras are supported by every pip version, unlike PEP 735 dependency groups whose pip support is still recent. All three tools are actually executed in this session's verification and are enforced by the tests/lint/typecheck in `handoff.md`.
- Alternatives considered: `requirements-dev.txt` — second dependency source that can drift from `pyproject.toml`; PEP 735 `[dependency-groups]` — newer, unnecessary compatibility risk at this stage; skipping ruff/mypy — the phase asks for appropriate lint/format configuration and typed code.
- Consequences: Contributors must install the `dev` extra before running tests or checks. CI (future phase) should use the same command.
- Package: pytest >=8 — test runner.
- Package: ruff >=0.6 — linting and formatting.
- Package: mypy >=1.10 — static type checking.
- Why this package: pytest offers fixtures and parametrization with minimal boilerplate; ruff covers linting and formatting in one fast tool; mypy integrates directly with pyproject configuration and validates the type hints this project requires.
- Packages compared: unittest (stdlib; more boilerplate, weaker fixture model), nose (unmaintained); flake8 + black + isort (three tools and more configuration than ruff), pylint (slower, opinionated defaults); pyright (excellent but requires a Node toolchain), ty/pyre (less mature integration with pyproject).
- Files: pyproject.toml

### D-007: Verify the installed entry point with one subprocess test

- Date / phase / commit: 2026-10-08, Phase 0 (Repository Bootstrap and Engineering Foundation), ac9b228
- Context: In-process `CliRunner` tests bypass packaging: they call the Typer application directly and would pass even if `[project.scripts]` were wrong or `__main__.py` were broken.
- Decision: Keep the main CLI tests in-process (fast), and add a single end-to-end test (`test_python_dash_m_entry_point_reports_version` in `tests/test_cli.py`) that runs `python -m edgehunter --version` as a subprocess and checks its output and exit code.
- Why: Catches packaging and entry-point mistakes that in-process tests cannot see, at the cost of one subprocess per test run.
- Alternatives considered: Testing the installed `edgehunter` script name directly — depends on the virtual environment's `Scripts/` directory being on `PATH`, which is not guaranteed under pytest; no end-to-end test — leaves packaging untested.
- Consequences: This test requires the package to be installed (the editable install satisfies it); running pytest from a source checkout without installing makes this test fail while the in-process tests still pass.
- Files: tests/test_cli.py

### D-008: CLI code and tests import `typer.testing` only, never a standalone `click` package

- Date / phase / commit: 2026-10-08, Phase 0 (Repository Bootstrap and Engineering Foundation), ac9b228
- Context: While verifying the CLI, the Typer installed in the project virtual environment (typer 0.27.3) was found to vendor click internally (`typer._click`): `import click` raises `ModuleNotFoundError` in the venv, and the typer distribution declares no click requirement. Older Typer versions allowed by the `typer>=0.12` spec depend on a separate `click` package instead. Any CLI test must work under both layouts.
- Decision: Production and test code import only `typer` and `typer.testing` (`CliRunner`, `Result`); nothing imports `click` directly, and `click` is not declared in `pyproject.toml`.
- Why: `typer.testing.CliRunner` provides everything the tests need (in-process invocation, merged `output`, separate `stderr`) regardless of whether click is vendored or a separate distribution. A probe with typer 0.27.2 + click 8.3.1 (global environment) confirmed `click.testing.CliRunner` refuses a `typer.Typer` object with `AttributeError: 'Typer' object has no attribute 'name'`, so importing click offered no benefit.
- Alternatives considered: Declaring `click` as a direct test dependency and using `click.testing.CliRunner` — imports an object Typer does not promise to expose and would diverge across Typer versions.
- Consequences: Future CLI code and tests must not `import click`; use `typer.testing` and Typer's own helpers. The `typer>=0.12` floor admits versions both with and without vendored click; this decision keeps the project compatible with both.
- Files: tests/test_cli.py, decisions.md

### D-009: Split scanner analysis into models.py, targets.py, and ast_parser.py; add `analyze_repository()`

- Date / phase / commit: 2026-10-08, Phase 1 (Repository Scanner + Safe AST Extraction), 82d42f3
- Context: Phase 1 requires structural facts (functions, classes, methods, parameters, annotations, decorators, line spans) from every discovered Python file without executing it. Phase 0 had a single `repository.py` whose only responsibility was filesystem discovery.
- Decision: Add three modules under `src/edgehunter/scanner/`: `models.py` (frozen dataclasses and enums), `targets.py` (AST to classes/callables), `ast_parser.py` (safe source read + `ast.parse` + issue capture). Add `RepositoryAnalysis` and `analyze_repository()` to `repository.py`. `ScanSummary` and `scan_repository()` keep their Phase 0 signatures and behavior unchanged; `RepositoryAnalysis` composes a `ScanSummary` (`discovery`) with a tuple of `ModuleAnalysis` and adds derived counters.
- Why: The responsibilities differ (data structures, AST traversal, file-level analysis, repository orchestration), and the phase's recommended structure matches this split. Keeping `scan_repository()` untouched preserves every Phase 0 test and any external caller; new capability is purely additive.
- Alternatives considered: Putting everything in `repository.py` (one growing module with three responsibilities, harder to test in isolation); extending `ScanSummary` directly with modules/stats (makes "discovery-only" results ambiguous with "no modules found", and mutates a Phase 0 contract); placing `RepositoryAnalysis` in `models.py` (would require moving `ScanSummary` there to avoid a circular import between `models` and `repository`, a larger refactor than the phase needs).
- Consequences: Future analysis stages should add their own modules rather than growing `repository.py`; `RepositoryAnalysis` is the stable aggregate the CLI and later phases consume. `models.py` must stay free of runtime behaviour (no evaluation of annotations or defaults).
- Files: src/edgehunter/scanner/models.py, src/edgehunter/scanner/targets.py, src/edgehunter/scanner/ast_parser.py, src/edgehunter/scanner/repository.py, src/edgehunter/scanner/__init__.py, tests/test_ast_parser.py, tests/test_repository_analysis.py

### D-010: Target model and scope-aware classification rules

- Date / phase / commit: 2026-10-08, Phase 1 (Repository Scanner + Safe AST Extraction), 82d42f3
- Context: Later phases need to distinguish functions, async functions, methods, async methods, and nested definitions, with deterministic source-local names and reproducible ordering.
- Decision: `FunctionInfo` records `name`, `qualified_name`, `TargetKind` (six values), `lineno`/`end_lineno`, `is_async`, `owner`, `parameters`, `return_annotation`, and `decorators`. Classification uses the *immediate lexical scope*: directly inside a class body it is a method; directly inside a function body it is a nested function; at module scope it is a function; async variants are selected when the definition is `async def`. "Directly" ignores compound statements (`if`/`try`/loops/`with`/`match`), which do not change scope. `qualified_name` joins lexical names with dots (`Outer.Inner.run`, `outer.inner`). `owner` is set only for direct methods (the dotted class path); nested functions have no owner. Functions inside one module are flattened into a single tuple sorted by `(lineno, qualified_name)`; classes are sorted the same way. `ModuleAnalysis.functions` contains all callables (including methods and nested functions); `ClassInfo` does not duplicate them.
- Why: The classification rule is deterministic, easy to explain, and matches how Python name binding works lexically. `(lineno, qualified_name)` sorting makes output stable even if the AST traversal order ever changes. Flattening avoids duplicating method objects inside `ClassInfo` while `owner` keeps the class link.
- Alternatives considered: Full import-system qualified names (requires module path resolution, explicitly out of scope); classifying nested definitions by their own `def`/`async def` only (loses the method/nested distinction the phase requires); sorting by qualified name only (loses source locality, which later reporting depends on).
- Consequences: "Functions" statistics include nested functions but not methods; methods are counted separately. A later phase that needs true package-qualified names must build them on top of `qualified_name`, not replace it. Classes defined inside functions keep function names in their qualified path (`outer.Helper`), which is intentional.
- Files: src/edgehunter/scanner/models.py, src/edgehunter/scanner/targets.py, tests/test_ast_parser.py, tests/test_repository_analysis.py

### D-011: Read Python source with `tokenize.open` (PEP 263 encoding detection)

- Date / phase / commit: 2026-10-08, Phase 1 (Repository Scanner + Safe AST Extraction), 82d42f3
- Context: Python source files are not guaranteed to be UTF-8; they may declare an encoding with a coding cookie or a BOM. Reading with a hard-coded encoding would mislabel valid files as broken.
- Decision: `_read_source()` in `ast_parser.py` uses `tokenize.open(path)`, which implements Python's own source encoding rules (BOM and PEP 263 coding cookies), then returns the decoded text. Decoding failures (`UnicodeDecodeError`), unreadable files (`OSError`), and unknown/invalid declared encodings (`SyntaxError` from `tokenize`) become a structured `ReadIssue`; they are never mislabeled as syntax errors.
- Why: `tokenize.open` applies exactly the rules the Python interpreter uses to read source, without executing anything, and closing is handled by the context manager. It requires only the standard library.
- Alternatives considered: `Path.read_text(encoding="utf-8")` (fails on legacy encodings that Python itself accepts); manual BOM/cookie detection (reimplements CPython behaviour with no benefit); `ast.parse` of raw bytes (would silently pick an encoding we cannot report on and gives no distinct read/parse error stage).
- Consequences: Read failures carry the original exception message (for example `'ascii' codec can't decode ...`), which the CLI prints verbatim. Future phases that open source files should reuse the same mechanism.
- Files: src/edgehunter/scanner/ast_parser.py, tests/test_ast_parser.py, tests/test_repository_analysis.py

### D-012: Separate `SyntaxIssue` and `ReadIssue`; one bad file never aborts the scan

- Date / phase / commit: 2026-10-08, Phase 1 (Repository Scanner + Safe AST Extraction), 82d42f3
- Context: A repository may contain unparseable or unreadable files. The phase requires these to be visible, structured, and unable to crash the whole scan, and encoding/read failures must not be reported as syntax errors.
- Decision: `ModuleAnalysis` carries at most one of `syntax_issue: SyntaxIssue | None` (line, column, message taken from `ast.parse`'s `SyntaxError`: `lineno`, `offset`, `msg`) and `read_issue: ReadIssue | None` (message from the read/decode exception). Both yield a normal `ModuleAnalysis` with empty targets; analysis continues with the remaining files. `is_parsed` is true only when both are `None`.
- Why: Distinguishing the two failure modes lets the CLI report them honestly in separate sections, and per-file result objects keep the repository loop free of exception control flow. `ast.parse`'s `msg` is used instead of `str(exc)` because the latter embeds filename/line text.
- Alternatives considered: Raising on first failure (loses all later results, contradicts the requirement); a single generic issue type with a kind field (viable, but two frozen types make the line/column availability explicit and type-checkable); normalizing or rewriting Python's messages (the phase forbids inventing messages).
- Consequences: The CLI exit code is 1 when `issue_count > 0` (see D-013). `SyntaxIssue.line`/`column` stay `int | None` because Python does not guarantee them. Read issues deliberately carry no line number.
- Files: src/edgehunter/scanner/models.py, src/edgehunter/scanner/ast_parser.py, src/edgehunter/cli.py, tests/test_ast_parser.py, tests/test_cli.py

### D-013: CLI `scan` reports AST statistics, supports `--targets`, and adds exit code 1

- Date / phase / commit: 2026-10-08, Phase 1 (Repository Scanner + Safe AST Extraction), 82d42f3
- Context: `edgehunter scan` previously printed discovery counts plus a notice that analysis was not implemented. Phase 1 makes analysis real, so the output and exit-code contract must reflect it. D-005 established 0 = success and 2 = invalid input but said nothing about completed scans with unparseable files.
- Decision: `scan` now calls `analyze_repository()` and prints discovered/parsed/syntax-issue/read-issue counts, class/function/method/async/callable-target counts, and (for directories) excluded-directory count. It prints `Syntax issues` and `Read issues` sections when present. A new `--targets` option lists every callable target as `relative/path.py:line` plus its static signature (`signature` property on `FunctionInfo`, defaults rendered as `...`). Exit codes: 0 = all discovered files parsed, 1 = scan completed with at least one syntax or read issue (`EXIT_SCAN_ISSUES`), 2 = invalid input (`EXIT_INVALID_INPUT`, unchanged). Invalid paths/unsupported targets still print `error: ...` to stderr with no traceback.
- Why: The phase requires visible syntax failures, deliberate exit codes, and a target listing. Exit 1 gives scripts a way to detect incomplete analysis without conflating it with bad user input (2) or success (0), extending D-005 rather than reversing it.
- Alternatives considered: Keep exit 0 for scans with issues (hides failures from CI); exit 2 for parse failures (would make a completed scan indistinguishable from a bad path); always listing targets without a flag (noisy for large repositories), separate `list` command (premature; the phase asks for an option).
- Consequences: Two Phase 0 CLI tests were updated for the new output (`test_scan_directory_succeeds_and_reports_counts`, `test_scan_single_python_file_succeeds`); the removed "not implemented" notice is asserted absent. New tests cover stats, `--targets`, and exit code 1 for both issue kinds. Later commands should follow the same 0/1/2 convention.
- Files: src/edgehunter/cli.py, tests/test_cli.py

### D-014: AST analysis uses only the standard library; no third-party parser dependency

- Date / phase / commit: 2026-10-08, Phase 1 (Repository Scanner + Safe AST Extraction), 82d42f3
- Context: Phase 1 needs to parse arbitrary Python 3 source and extract syntax facts. The phase allows a third-party AST dependency only with a demonstrated need the standard library cannot satisfy.
- Decision: Use the standard library only: `ast` for parsing and `ast.unparse` for rendering annotations/decorators/bases, `tokenize` for encoding detection. No new runtime dependencies were added.
- Why: `ast.parse` accepts every syntax the running interpreter accepts and never imports analyzed modules. `ast.unparse` round-trips the expressions needed here. Both are guaranteed present, fast enough, and keep the dependency surface at `typer` only (Phase 0).
- Alternatives considered: `astroid` (adds inference and dependencies, unnecessary at this stage); `LibCST` (lossless CST, heavier, aimed at rewriting); `parso` (multiple-version grammar support not needed); `tree-sitter` (native dependency, non-Python grammar versions, overkill). All were rejected because nothing in this phase needs error-recovering parsing or round-trip rewriting.
- Consequences: Analysis is tied to the running interpreter's grammar (a file using a newer syntax than the running Python reports a syntax issue; documented as a known limit). If a later phase needs to parse across Python versions or preserve formatting, that requires a new decision.
- Files: pyproject.toml (unchanged; recorded for the record), src/edgehunter/scanner/ast_parser.py, src/edgehunter/scanner/targets.py

### D-015: Add an `analysis` layer and split the scanner pipeline so each file is read and parsed once

- Date / phase / commit: 2026-10-08, Phase 2 (Static Semantic Analysis + Boundary Mining), uncommitted
- Context: Phase 1 already reads a file with `tokenize.open` and parses it with `ast.parse`, then discards the AST. Phase 2 needs that same AST to extract semantic facts. Re-parsing every file would duplicate all I/O and parsing work, and a second, independent safe-parsing implementation was explicitly ruled out.
- Decision: (1) Add a sibling package `src/edgehunter/analysis/` with `models.py`, `semantics.py`, `boundaries.py`, and `repository.py`; `scanner` keeps answering "what exists", `analysis` answers "which semantic facts exist". (2) Split the scanner pipeline: `parse_source_file()` returns a `SourceFile` carrying the parsed `ast.Module` (or a structured issue); `build_module_analysis()` derives structural facts from it; `collect_callable_nodes()` returns `CallableNode` pairs so semantic facts are attributed to exactly the targets the structural scanner discovered; `analyze_python_file()` remains as the Phase 1 convenience wrapper. (3) Move the shared `ast.unparse` rendering with its fallback into `scanner/rendering.py`. (4) `analysis/repository.py::analyze_repository_semantics()` reads and parses each discovered file once, then derives both structure and semantics, and returns `RepositorySemantics` with the scanner's `RepositoryAnalysis` embedded as `.structure`. No new runtime dependency (standard library only, consistent with D-014).
- Why: One read/parse per file keeps the scan cost linear and guarantees the structural and semantic views of a file can never disagree. Reusing `collect_callable_nodes()` removes any possibility of a second, divergent implementation of the qualified-name and scope rules from D-010 — a real divergence risk, since boundary mining must know a target's parameters. `SourceFile.tree` is internal pipeline state and is never part of the public scanner models or CLI output. A test (`test_each_source_file_is_read_once`) locks the single-read property in place.
- Alternatives considered: Keeping the AST inside `ModuleAnalysis` (leaks a raw AST into a Phase 1 public model and its equality/repr); re-parsing in the analysis layer (duplicates the safe-read/parse logic and doubles I/O); having `analysis` re-derive qualified names itself (second implementation of D-010's scope rules, guaranteed to drift); putting semantic extraction in `scanner/targets.py` (would make `scanner` depend on semantic concepts and blur the "what exists" boundary).
- Consequences: `scanner/ast_parser.py` gained `SourceFile`/`parse_source_file`/`build_module_analysis` and `scanner/targets.py` gained `CallableNode`/`collect_callable_nodes`; Phase 1 public behavior and all 52 Phase 1 tests are unchanged (verified immediately after the refactor). `analysis` depends on `scanner`, never the reverse. The CLI now calls `analyze_repository_semantics()` and reads structural counts from `.structure`.
- Files: src/edgehunter/analysis/__init__.py, src/edgehunter/analysis/models.py, src/edgehunter/analysis/semantics.py, src/edgehunter/analysis/boundaries.py, src/edgehunter/analysis/repository.py, src/edgehunter/scanner/rendering.py, src/edgehunter/scanner/ast_parser.py, src/edgehunter/scanner/targets.py, src/edgehunter/scanner/__init__.py

### D-016: Semantic extraction rules (predicates, comparisons, constants, arithmetic)

- Date / phase / commit: 2026-10-08, Phase 2 (Static Semantic Analysis + Boundary Mining), uncommitted
- Context: Phase 2 must turn syntax into deterministic, test-relevant facts, with classification rules precise enough to implement identically twice.
- Decision: Facts are collected per callable target. Predicates come from `if`, `while`, and `assert` only (`PredicateKind`); `elif` yields its own predicate through nested `ast.If`. Each predicate keeps its containing expression *and* every comparison inside it, including inside `and`/`or` operands. A chained comparison is decomposed into adjacent pairs (`0 < x <= 10` yields `0 < x` and `x <= 10`), never into `0 < 10`. Operators are mapped to a stable `ComparisonOperator` enum (`<`, `<=`, `>`, `>=`, `==`, `!=`, `is`, `is not`, `in`, `not in`). Constants are recorded per literal occurrence with a `ConstantKind` vocabulary of integer, float, complex, string, bytes, bool, none; `bool` is tested before `int` so `True` is never numeric, and `...` (Ellipsis) is deliberately not recorded because it is a stub placeholder rather than a value literal. Signed numeric literals (`-1`, `+0.5`) are recognized through an explicit `UnaryOp`-over-`Constant` helper; no `eval`/`literal_eval` is used anywhere. Arithmetic is recorded as *maximal* subexpressions: a `BinOp` whose ancestor is also an arithmetic `BinOp` is not recorded separately, so `(x + y) * 2` is one `ArithmeticInfo` carrying both operators, avoiding double counting. Contexts are return, assignment, annotated assignment, augmented assignment, or plain expression. No simplification, folding, or algebra is performed. Traversal stops at nested `FunctionDef`/`AsyncFunctionDef`/`ClassDef`/`Lambda`, so an inner callable's facts are never attributed to its parent, and module- or class-level conditions belong to no target.
- Why: Recording the containing predicate alongside its comparisons preserves boolean structure without attempting satisfiability solving. Recording maximal arithmetic keeps counts meaningful (one expression, one record) while still exposing every operator involved. `bool`-before-`int` is required because `bool` subclasses `int` at runtime; the test `test_bool_literal_is_not_numeric` pins it.
- Alternatives considered: Recording nested arithmetic separately (double counts `(x + y) * 2` as two expressions); omitting comparisons from boolean operands (loses real constraints); recording `...` as a constant (pollutes every stub body with a meaningless fact); classifying constants by `type(value)` only (misclassifies `bool` as `integer`).
- Consequences: Comparison facts only originate inside predicates, so a comparison outside `if`/`while`/`assert` (for example `y = x > 3`) is not recorded as a comparison; it is still visible as part of an `ArithmeticInfo`/expression text if it appears there. `assert` predicates are recorded as syntax only: no property or invariant claim is made anywhere (that is a later phase).
- Files: src/edgehunter/analysis/models.py, src/edgehunter/analysis/semantics.py, tests/test_semantics.py

### D-017: Boundary candidate vocabulary and derivation rules

- Date / phase / commit: 2026-10-08, Phase 2 (Static Semantic Analysis + Boundary Mining), uncommitted
- Context: Boundary mining must propose regions worth testing without claiming a defect, without evaluating anything, and without inventing numbers that are only meaningful for one numeric type.
- Decision: A finite `BoundaryKind` vocabulary: comparison-derived `JUST_BELOW`, `AT_BOUNDARY`, `JUST_ABOVE`; generic numeric `ZERO`, `ONE`, `NEGATIVE_ONE`, `NEGATIVE_ZERO`, `NAN`, `POSITIVE_INFINITY`, `NEGATIVE_INFINITY`. Derivation rules: inequalities (`<`, `<=`, `>`, `>=`) with an ordered reference yield below/at/above; equality and inequality-difference (`==`, `!=`) yield `AT_BOUNDARY` only; `is`, `is not`, `in`, `not in` yield nothing. Generic numeric candidates are derived only when a parameter's annotation text is exactly `int` or `float`, and only for positional-only, positional-or-keyword, and keyword-only parameters. References stay symbolic: for `amount <= balance` the candidate is "amount at balance", never `balance - 1`, because the correct step depends on a type this phase does not resolve. Every candidate records `origin` (`COMPARISON`/`NUMERIC`), the rendering `reason` (static evidence, never a bug claim), and every source line that suggested it.
- Why: `int` yields zero/one/negative-one; `float` additionally yields negative zero, NaN, and both infinities, which is exactly the edge region a float parameter can be mishandled at. Keeping references symbolic is what lets one boundary description serve floats, `Decimal`, dates, and custom numerics in a later phase. `==`/`!=` deliberately do not synthesize numeric neighbors, so `status_code == 200` does not invent `199`/`201`.
- Alternatives considered: Materializing concrete values such as `17/18/19` for `age >= 18` (premature: needs type resolution and would bake in an integer step); deriving numeric neighbors for equality (unsound for any non-numeric or mixed type); omitting generic NaN/infinity candidates (they are a primary float boundary region); applying generic candidates to `*args`/`**kwargs` (a variadic parameter is a sequence, not one value).
- Consequences: Scanning EdgeHunter's own source yields very few candidates because almost nothing is annotated exactly `int`/`float`; that is the conservative rule working as designed, not a defect. Annotation matching is textual, so `Optional[int]`, `int | None`, `Annotated[int, ...]`, `Decimal`, and `numpy.int64` receive no generic candidates while still getting comparison-derived ones.
- Files: src/edgehunter/analysis/models.py, src/edgehunter/analysis/boundaries.py, tests/test_boundaries.py

### D-018: Parameter normalization, safe-reference rule, deduplication, and ordering

- Date / phase / commit: 2026-10-08, Phase 2 (Static Semantic Analysis + Boundary Mining), uncommitted
- Context: Boundary candidates are only useful when the interesting parameter is the subject of the boundary, and they must be conservative about references whose value cannot be known statically.
- Decision: For each comparison, a candidate is mined only when one side is a plain name that is a declared parameter. That side becomes the subject; if it was the right side, the operator is inverted using the exact table `< -> >`, `<= -> >=`, `> -> <`, `>= -> <=`, `== -> ==`, `!= -> !=`; `is`, `is not`, `in`, and `not in` are never inverted (they return no candidate). When both sides are parameters the syntactic left side wins, so no inverse relational candidate is generated. The opposite side must be a stable reference — a literal, a bare name, or a plain dotted name chain (determined from the AST, not from the rendered text, so `self.limit` is allowed but `f().x` and `items[i]` are not). A non-numeric literal reference (for example `'admin'`) yields candidates only for `==`/`!=`. Candidates are deduplicated by `(parameter, kind, reference)` within a target, with every evidence line preserved and sorted. Final ordering is `(parameter, boundary-kind rank, reference)` where the rank table puts comparison-derived kinds before generic numeric kinds.
- Why: Normalizing `0 <= amount` to "amount at 0" is what makes boundary mining useful regardless of how the source happens to be written. Restricting inversion to operators where the reversal is provably equivalent avoids unsound rewrites of identity and membership. Rejecting call/subscript/complex references keeps every reference materializable later without execution, while still recording the comparison itself as a fact. Preferring the left side avoids emitting both `amount` relative to `balance` and the redundant inverse.
- Alternatives considered: Emitting the inverse relational candidate as well (duplicate noise); inverting `is`/`in` (unsound or meaningless); accepting any expression as a reference (a future generator could not materialize it safely); deduplicating by rendered description text (kind and reference belong in the key); sorting alphabetically by kind (would print `NaN` before `just below`, which reads badly and hides the comparison evidence).
- Consequences: `test_call_reference_is_never_invoked_and_produces_no_candidate` and `test_complex_reference_produces_no_candidate` pin the conservative behavior. Comparisons with neither side a parameter (for example `config.limit < 10`) produce no candidate but remain visible as semantic facts.
- Files: src/edgehunter/analysis/boundaries.py, tests/test_boundaries.py

### D-019: CLI reports semantic statistics and gains `--boundaries`

- Date / phase / commit: 2026-10-08, Phase 2 (Static Semantic Analysis + Boundary Mining), uncommitted
- Context: `edgehunter scan` printed structural statistics only. Phase 2 adds semantic facts and candidates, which must be visible without overloading the default output. D-013 established the 0/1/2 exit-code contract and must keep applying.
- Decision: `scan` calls `analyze_repository_semantics()` and prints the existing structural block unchanged, followed by a new semantic block: `Predicates`, `Comparisons`, `Arithmetic expressions`, `Boundary candidates`, each with an exact definition (predicate records, adjacent comparison records, maximal arithmetic records, and deduplicated candidates). A new `--boundaries` option prints a `Boundaries` section listing, per target, `path:line`, the static signature, and candidates grouped by parameter as `[comparison] ...` / `[numeric] ...`. `--targets` is unchanged and both flags may be combined. Exit codes are unchanged: 1 when the scan completed with syntax/read issues (`analysis.structure.issue_count`), 2 for invalid input, 0 otherwise. No `--semantics` view was added: the required `--boundaries` output plus the statistics block already cover the useful detail, and extra switches would only add surface.
- Why: Semantic counts belong in the default output because they are the substance of the phase; the candidate listing is verbose and therefore opt-in, matching `--targets`. Reading structural counts from `analysis.structure` preserves every Phase 1 CLI test unchanged.
- Alternatives considered: Semantics only in `--boundaries` (the summary would not show the new capability); a separate `--semantics` switch alongside `--boundaries` (promptly optional, adds surface without a demonstrated need); deriving exit code 1 from semantic exceptions (conceals EdgeHunter bugs, explicitly forbidden).
- Consequences: `tests/test_cli.py` gained the semantic-statistics and `--boundaries` tests and one new assertion in the help test; no Phase 1 CLI test needed modification.
- Files: src/edgehunter/cli.py, tests/test_cli.py

### D-020: Deliberately unsupported semantics are recorded as limitations, not approximated

- Date / phase / commit: 2026-10-08, Phase 2 (Static Semantic Analysis + Boundary Mining), uncommitted
- Context: Several Python constructs contain comparisons and arithmetic that this phase's rules do not classify. Approximating them silently would make the deterministic baseline untrustworthy for later benchmarking.
- Decision: The following are intentionally out of scope and are documented rather than faked: comprehension filters (`[x for x in items if x > 0]` — constants inside are still recorded, the filter is not a predicate); lambdas (never targets, their bodies are skipped, preserving Phase 1 behavior); `match`/`case` patterns; `try`/`except` flow and raised-exception modeling; conditional expressions (`x if cond else y`); module-level and class-level conditions (they belong to no callable target); bitwise and bit-shift operators in augmented assignments (not arithmetic, so no `ArithmeticInfo`); annotations other than a literal `int` or `float` for generic numeric candidates (D-017). Nothing here is silently skipped without a test or a note in `handoff.md`.
- Why: A future experiment comparing deterministic boundary search against model-guided search needs the deterministic baseline to be exactly specified. Documented gaps are measurable; silent approximations are not.
- Alternatives considered: Treating comprehension filters as predicates (starts a comprehension control-flow engine, explicitly out of scope); treating lambdas as anonymous targets (contradicts Phase 1 and creates unnameable candidates); recording bitwise operations as arithmetic (mislabels facts that risk scoring will consume).
- Consequences: These constructs will need explicit decisions in a later phase; the scan of real repositories will under-report until then. This is a known limitation, not a bug.
- Files: src/edgehunter/analysis/semantics.py, src/edgehunter/analysis/boundaries.py, tests/test_semantics.py, tests/test_boundaries.py
