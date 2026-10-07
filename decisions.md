# EdgeHunter — Decision Log

This file records *why* EdgeHunter is built the way it is. Entries are
append-only: existing entries are never edited or deleted, only superseded by
a new entry that references them. Decisions are referenced from `flow.md` and
`handoff.md` by their `D-NNN` number.

---

### D-001: Adopt a `src/` layout for the `edgehunter` package

- Date / phase / commit: 2026-10-08, Phase 0 (Repository Bootstrap and Engineering Foundation), uncommitted
- Context: The repository started empty. An installable package plus a console entry point are required, and later phases will add further subpackages (analysis, oracle, search, generators, execution, findings, ...). The physical layout had to be chosen before any code was written.
- Decision: Keep all importable code under `src/edgehunter/`; tests live in a separate top-level `tests/` directory.
- Why: The `src/` layout prevents accidental imports of working-copy modules (for example when running tools from the repository root) and forces contributors to validate the packaging configuration by installing the project. It is the layout the Python Packaging User Guide recommends for packages intended for distribution.
- Alternatives considered: Flat `edgehunter/` at repository root — works, but a stray `import edgehunter` can silently pick up the working tree instead of the installed package and hide packaging errors.
- Consequences: The CLI and tests require an editable install (`pip install -e ".[dev]"`) before `import edgehunter` works from an arbitrary directory. All future modules go under `src/edgehunter/<subpackage>/`.
- Files: pyproject.toml, src/edgehunter/, tests/

### D-002: Use setuptools with a dynamic version sourced from `edgehunter.__version__`

- Date / phase / commit: 2026-10-08, Phase 0 (Repository Bootstrap and Engineering Foundation), uncommitted
- Context: Packaging is defined in `pyproject.toml`. The version must be available both to the build backend and at runtime for `edgehunter --version`.
- Decision: Build backend `setuptools>=68` with `dynamic = ["version"]` and `[tool.setuptools.dynamic] version = { attr = "edgehunter.__version__" }`. `src/edgehunter/__init__.py` contains the literal `__version__ = "0.1.0"`.
- Why: setuptools is the most widely available backend and supports PEP 660 editable installs; reading the version via `attr` keeps a single source of truth. Because `__version__` is a simple literal, setuptools reads it without importing the package, so no runtime dependency is needed at build time.
- Alternatives considered: Static `version = "0.1.0"` in `pyproject.toml` — duplicates the version in two files that can drift; `hatchling` backend — modern and fast, but adds a less ubiquitous build requirement with no benefit at this stage; `flit` — infers metadata from docstrings, which conflicts with keeping `__init__.py` trivial.
- Consequences: Version bumps happen in exactly one place: `src/edgehunter/__init__.py`. `__init__.py` must not import runtime dependencies.
- Files: pyproject.toml, src/edgehunter/__init__.py

### D-003: Build the CLI with Typer

- Date / phase / commit: 2026-10-08, Phase 0 (Repository Bootstrap and Engineering Foundation), uncommitted
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

- Date / phase / commit: 2026-10-08, Phase 0 (Repository Bootstrap and Engineering Foundation), uncommitted
- Context: The `scan` command must not pretend that analysis exists, but this phase also asks for the smallest genuinely useful behavior rather than a pure stub. Repository discovery (finding Python sources) is safe because it touches only the filesystem.
- Decision: `scan_repository()` walks a directory with `os.walk` (symlinks not followed), prunes directories named in `EXCLUDED_DIRECTORY_NAMES` and directories ending in `.egg-info`, collects `*.py` files in sorted order, and returns an immutable `ScanSummary`. A single `*.py` file is accepted as a target; any other regular file is rejected with `UnsupportedTargetError`. The CLI prints the summary and explicitly states that repository analysis is not implemented yet.
- Why: Real discovery is small, deterministic, and testable, and it establishes the `scanner` package that later phases build on. `os.walk` defaults to `followlinks=False`, which avoids symlink cycles and keeps the scan inside the requested tree. Exclusion by explicit name/suffix is conservative: unknown directories are always scanned.
- Alternatives considered: A pure placeholder that prints "not implemented" — no behavior to test, no extension point; AST parsing or module import — explicitly out of scope for this phase and unsafe (importing target code executes it); recursive `Path.rglob("*.py")` — cannot prune directory subtrees efficiently, while `os.walk` allows in-place pruning via the `dirnames` list.
- Consequences: The exclusion list is policy, not truth; repositories with unconventional vendor directories will include extra files. Discovery never imports or executes target code. Suffix matching is case-sensitive (a file named `X.PY` on Windows is not collected) — intentional, matches Python's module naming, revisit if it becomes an issue.
- Files: src/edgehunter/scanner/repository.py, src/edgehunter/scanner/__init__.py, src/edgehunter/cli.py, tests/test_repository_scanner.py, tests/test_cli.py

### D-005: Exit codes: 0 for success, 2 for invalid user input

- Date / phase / commit: 2026-10-08, Phase 0 (Repository Bootstrap and Engineering Foundation), uncommitted
- Context: Invalid input (missing path, non-Python file target) must fail with a non-zero status and without a traceback, while unexpected programming errors must stay visible.
- Decision: The `scan` command catches `ScanError` subclasses, prints `error: <message>` to stderr, and exits with code 2 (`EXIT_INVALID_INPUT` in `src/edgehunter/cli.py`). Unexpected exceptions are not caught: they surface as tracebacks with a non-zero exit.
- Why: Code 2 matches the convention used by argparse and click for usage errors, and keeps user errors distinguishable from internal bugs.
- Alternatives considered: Exit 1 for everything — loses the distinction between bad input and bugs; exit 0 with an error message — wrong, scripts could not detect failure.
- Consequences: Future commands should reuse code 2 for invalid input; new error families should subclass `ScanError` or a shared base when one exists.
- Files: src/edgehunter/cli.py, tests/test_cli.py

### D-006: Development tooling via the `dev` extra: pytest, ruff, mypy

- Date / phase / commit: 2026-10-08, Phase 0 (Repository Bootstrap and Engineering Foundation), uncommitted
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

- Date / phase / commit: 2026-10-08, Phase 0 (Repository Bootstrap and Engineering Foundation), uncommitted
- Context: In-process `CliRunner` tests bypass packaging: they call the Typer application directly and would pass even if `[project.scripts]` were wrong or `__main__.py` were broken.
- Decision: Keep the main CLI tests in-process (fast), and add a single end-to-end test (`test_python_dash_m_entry_point_reports_version` in `tests/test_cli.py`) that runs `python -m edgehunter --version` as a subprocess and checks its output and exit code.
- Why: Catches packaging and entry-point mistakes that in-process tests cannot see, at the cost of one subprocess per test run.
- Alternatives considered: Testing the installed `edgehunter` script name directly — depends on the virtual environment's `Scripts/` directory being on `PATH`, which is not guaranteed under pytest; no end-to-end test — leaves packaging untested.
- Consequences: This test requires the package to be installed (the editable install satisfies it); running pytest from a source checkout without installing makes this test fail while the in-process tests still pass.
- Files: tests/test_cli.py
