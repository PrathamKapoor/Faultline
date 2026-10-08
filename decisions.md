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

- Date / phase / commit: 2026-10-08, Phase 1 (Repository Scanner + Safe AST Extraction), uncommitted
- Context: Phase 1 requires structural facts (functions, classes, methods, parameters, annotations, decorators, line spans) from every discovered Python file without executing it. Phase 0 had a single `repository.py` whose only responsibility was filesystem discovery.
- Decision: Add three modules under `src/edgehunter/scanner/`: `models.py` (frozen dataclasses and enums), `targets.py` (AST to classes/callables), `ast_parser.py` (safe source read + `ast.parse` + issue capture). Add `RepositoryAnalysis` and `analyze_repository()` to `repository.py`. `ScanSummary` and `scan_repository()` keep their Phase 0 signatures and behavior unchanged; `RepositoryAnalysis` composes a `ScanSummary` (`discovery`) with a tuple of `ModuleAnalysis` and adds derived counters.
- Why: The responsibilities differ (data structures, AST traversal, file-level analysis, repository orchestration), and the phase's recommended structure matches this split. Keeping `scan_repository()` untouched preserves every Phase 0 test and any external caller; new capability is purely additive.
- Alternatives considered: Putting everything in `repository.py` (one growing module with three responsibilities, harder to test in isolation); extending `ScanSummary` directly with modules/stats (makes "discovery-only" results ambiguous with "no modules found", and mutates a Phase 0 contract); placing `RepositoryAnalysis` in `models.py` (would require moving `ScanSummary` there to avoid a circular import between `models` and `repository`, a larger refactor than the phase needs).
- Consequences: Future analysis stages should add their own modules rather than growing `repository.py`; `RepositoryAnalysis` is the stable aggregate the CLI and later phases consume. `models.py` must stay free of runtime behaviour (no evaluation of annotations or defaults).
- Files: src/edgehunter/scanner/models.py, src/edgehunter/scanner/targets.py, src/edgehunter/scanner/ast_parser.py, src/edgehunter/scanner/repository.py, src/edgehunter/scanner/__init__.py, tests/test_ast_parser.py, tests/test_repository_analysis.py

### D-010: Target model and scope-aware classification rules

- Date / phase / commit: 2026-10-08, Phase 1 (Repository Scanner + Safe AST Extraction), uncommitted
- Context: Later phases need to distinguish functions, async functions, methods, async methods, and nested definitions, with deterministic source-local names and reproducible ordering.
- Decision: `FunctionInfo` records `name`, `qualified_name`, `TargetKind` (six values), `lineno`/`end_lineno`, `is_async`, `owner`, `parameters`, `return_annotation`, and `decorators`. Classification uses the *immediate lexical scope*: directly inside a class body it is a method; directly inside a function body it is a nested function; at module scope it is a function; async variants are selected when the definition is `async def`. "Directly" ignores compound statements (`if`/`try`/loops/`with`/`match`), which do not change scope. `qualified_name` joins lexical names with dots (`Outer.Inner.run`, `outer.inner`). `owner` is set only for direct methods (the dotted class path); nested functions have no owner. Functions inside one module are flattened into a single tuple sorted by `(lineno, qualified_name)`; classes are sorted the same way. `ModuleAnalysis.functions` contains all callables (including methods and nested functions); `ClassInfo` does not duplicate them.
- Why: The classification rule is deterministic, easy to explain, and matches how Python name binding works lexically. `(lineno, qualified_name)` sorting makes output stable even if the AST traversal order ever changes. Flattening avoids duplicating method objects inside `ClassInfo` while `owner` keeps the class link.
- Alternatives considered: Full import-system qualified names (requires module path resolution, explicitly out of scope); classifying nested definitions by their own `def`/`async def` only (loses the method/nested distinction the phase requires); sorting by qualified name only (loses source locality, which later reporting depends on).
- Consequences: "Functions" statistics include nested functions but not methods; methods are counted separately. A later phase that needs true package-qualified names must build them on top of `qualified_name`, not replace it. Classes defined inside functions keep function names in their qualified path (`outer.Helper`), which is intentional.
- Files: src/edgehunter/scanner/models.py, src/edgehunter/scanner/targets.py, tests/test_ast_parser.py, tests/test_repository_analysis.py

### D-011: Read Python source with `tokenize.open` (PEP 263 encoding detection)

- Date / phase / commit: 2026-10-08, Phase 1 (Repository Scanner + Safe AST Extraction), uncommitted
- Context: Python source files are not guaranteed to be UTF-8; they may declare an encoding with a coding cookie or a BOM. Reading with a hard-coded encoding would mislabel valid files as broken.
- Decision: `_read_source()` in `ast_parser.py` uses `tokenize.open(path)`, which implements Python's own source encoding rules (BOM and PEP 263 coding cookies), then returns the decoded text. Decoding failures (`UnicodeDecodeError`), unreadable files (`OSError`), and unknown/invalid declared encodings (`SyntaxError` from `tokenize`) become a structured `ReadIssue`; they are never mislabeled as syntax errors.
- Why: `tokenize.open` applies exactly the rules the Python interpreter uses to read source, without executing anything, and closing is handled by the context manager. It requires only the standard library.
- Alternatives considered: `Path.read_text(encoding="utf-8")` (fails on legacy encodings that Python itself accepts); manual BOM/cookie detection (reimplements CPython behaviour with no benefit); `ast.parse` of raw bytes (would silently pick an encoding we cannot report on and gives no distinct read/parse error stage).
- Consequences: Read failures carry the original exception message (for example `'ascii' codec can't decode ...`), which the CLI prints verbatim. Future phases that open source files should reuse the same mechanism.
- Files: src/edgehunter/scanner/ast_parser.py, tests/test_ast_parser.py, tests/test_repository_analysis.py

### D-012: Separate `SyntaxIssue` and `ReadIssue`; one bad file never aborts the scan

- Date / phase / commit: 2026-10-08, Phase 1 (Repository Scanner + Safe AST Extraction), uncommitted
- Context: A repository may contain unparseable or unreadable files. The phase requires these to be visible, structured, and unable to crash the whole scan, and encoding/read failures must not be reported as syntax errors.
- Decision: `ModuleAnalysis` carries at most one of `syntax_issue: SyntaxIssue | None` (line, column, message taken from `ast.parse`'s `SyntaxError`: `lineno`, `offset`, `msg`) and `read_issue: ReadIssue | None` (message from the read/decode exception). Both yield a normal `ModuleAnalysis` with empty targets; analysis continues with the remaining files. `is_parsed` is true only when both are `None`.
- Why: Distinguishing the two failure modes lets the CLI report them honestly in separate sections, and per-file result objects keep the repository loop free of exception control flow. `ast.parse`'s `msg` is used instead of `str(exc)` because the latter embeds filename/line text.
- Alternatives considered: Raising on first failure (loses all later results, contradicts the requirement); a single generic issue type with a kind field (viable, but two frozen types make the line/column availability explicit and type-checkable); normalizing or rewriting Python's messages (the phase forbids inventing messages).
- Consequences: The CLI exit code is 1 when `issue_count > 0` (see D-013). `SyntaxIssue.line`/`column` stay `int | None` because Python does not guarantee them. Read issues deliberately carry no line number.
- Files: src/edgehunter/scanner/models.py, src/edgehunter/scanner/ast_parser.py, src/edgehunter/cli.py, tests/test_ast_parser.py, tests/test_cli.py

### D-013: CLI `scan` reports AST statistics, supports `--targets`, and adds exit code 1

- Date / phase / commit: 2026-10-08, Phase 1 (Repository Scanner + Safe AST Extraction), uncommitted
- Context: `edgehunter scan` previously printed discovery counts plus a notice that analysis was not implemented. Phase 1 makes analysis real, so the output and exit-code contract must reflect it. D-005 established 0 = success and 2 = invalid input but said nothing about completed scans with unparseable files.
- Decision: `scan` now calls `analyze_repository()` and prints discovered/parsed/syntax-issue/read-issue counts, class/function/method/async/callable-target counts, and (for directories) excluded-directory count. It prints `Syntax issues` and `Read issues` sections when present. A new `--targets` option lists every callable target as `relative/path.py:line` plus its static signature (`signature` property on `FunctionInfo`, defaults rendered as `...`). Exit codes: 0 = all discovered files parsed, 1 = scan completed with at least one syntax or read issue (`EXIT_SCAN_ISSUES`), 2 = invalid input (`EXIT_INVALID_INPUT`, unchanged). Invalid paths/unsupported targets still print `error: ...` to stderr with no traceback.
- Why: The phase requires visible syntax failures, deliberate exit codes, and a target listing. Exit 1 gives scripts a way to detect incomplete analysis without conflating it with bad user input (2) or success (0), extending D-005 rather than reversing it.
- Alternatives considered: Keep exit 0 for scans with issues (hides failures from CI); exit 2 for parse failures (would make a completed scan indistinguishable from a bad path); always listing targets without a flag (noisy for large repositories), separate `list` command (premature; the phase asks for an option).
- Consequences: Two Phase 0 CLI tests were updated for the new output (`test_scan_directory_succeeds_and_reports_counts`, `test_scan_single_python_file_succeeds`); the removed "not implemented" notice is asserted absent. New tests cover stats, `--targets`, and exit code 1 for both issue kinds. Later commands should follow the same 0/1/2 convention.
- Files: src/edgehunter/cli.py, tests/test_cli.py

### D-014: AST analysis uses only the standard library; no third-party parser dependency

- Date / phase / commit: 2026-10-08, Phase 1 (Repository Scanner + Safe AST Extraction), uncommitted
- Context: Phase 1 needs to parse arbitrary Python 3 source and extract syntax facts. The phase allows a third-party AST dependency only with a demonstrated need the standard library cannot satisfy.
- Decision: Use the standard library only: `ast` for parsing and `ast.unparse` for rendering annotations/decorators/bases, `tokenize` for encoding detection. No new runtime dependencies were added.
- Why: `ast.parse` accepts every syntax the running interpreter accepts and never imports analyzed modules. `ast.unparse` round-trips the expressions needed here. Both are guaranteed present, fast enough, and keep the dependency surface at `typer` only (Phase 0).
- Alternatives considered: `astroid` (adds inference and dependencies, unnecessary at this stage); `LibCST` (lossless CST, heavier, aimed at rewriting); `parso` (multiple-version grammar support not needed); `tree-sitter` (native dependency, non-Python grammar versions, overkill). All were rejected because nothing in this phase needs error-recovering parsing or round-trip rewriting.
- Consequences: Analysis is tied to the running interpreter's grammar (a file using a newer syntax than the running Python reports a syntax issue; documented as a known limit). If a later phase needs to parse across Python versions or preserve formatting, that requires a new decision.
- Files: pyproject.toml (unchanged; recorded for the record), src/edgehunter/scanner/ast_parser.py, src/edgehunter/scanner/targets.py
