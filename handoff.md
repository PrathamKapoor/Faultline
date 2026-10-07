# EdgeHunter — Session Handoff

## 1. Current Phase

- **Phase:** Phase 0 — Repository Bootstrap and Engineering Foundation
- **Subphase:** none (single-session phase)
- **Objective:** Create the engineering foundation for EdgeHunter: an installable package, a working CLI, safe minimal repository discovery, tests, tooling configuration, and process documentation. No counterexample-search functionality.
- **Status:** complete

Repository state at handoff: branch `main`, no remote configured, HEAD = `ac9b2289091c6eac21dda32e2b066986a2429fca` (`ac9b228`, "Bootstrap EdgeHunter engineering foundation"), plus a follow-up documentation commit that records this handoff.

## 2. Work Completed

The repository started completely empty (no `.git`). Work performed:

- Initialized a git repository on branch `main` and confirmed the repository-local identity `PrathamKapoor <prathamkapoor027@gmail.com>`.
- Chose and implemented a `src/`-layout Python package (`src/edgehunter/`) with `pyproject.toml` only (no `setup.py`, no `requirements.txt`) — see D-001, D-002.
- Built a Typer CLI exposing `edgehunter --help`, `--version`, and `scan PATH` — see D-003.
- Implemented safe filesystem repository discovery in `scanner/repository.py`: counts `*.py` files, prunes virtual environments/caches/build directories, never imports or executes target code — see D-004.
- Defined clean error behavior: `ScanError` hierarchy, `error: ...` to stderr, exit code 2 for invalid input — see D-005.
- Added development tooling (pytest, ruff, mypy) through the `dev` extra and configured all tools in `pyproject.toml` — see D-006.
- Wrote 18 tests: 9 CLI tests (including one subprocess test of `python -m edgehunter`) and 9 scanner tests — see D-007.
- Wrote `README.md`, `decisions.md`, `flow.md`, and `.gitignore`.

Non-obvious behavior worth knowing:

- `--version` is an eager option on the Typer root callback, so it works before command resolution and exits 0.
- `scan` on a directory prints the discovered file count and an explicit "Repository analysis is not implemented yet" note; it never overstates capability.
- `scan` on a single non-`.py` file fails with exit code 2 and `error: not a Python source file: ...`.
- In the project venv, `import click` fails: typer 0.27.3 vendors click as `typer._click`. Tests therefore use `typer.testing.CliRunner` exclusively (D-008).

## 3. Files Changed

All paths relative to the repository root.

```text
.gitignore
  Added standard Python ignores (bytecode, venvs, build output, tool caches, IDE files).
  Keeps .venv/, egg-info, and caches out of commits.

README.md
  Project description, honest current status, real installation/usage commands, architecture, roadmap.
  Claims only verified functionality; scan output shown is real output from this session.

pyproject.toml
  Single authoritative packaging and tool configuration: setuptools backend, dynamic version
  (attr edgehunter.__version__), console script edgehunter = edgehunter.cli:main, dev extra
  (pytest/ruff/mypy), pytest/ruff/mypy settings.

decisions.md
  Append-only decision log D-001..D-008 with context, alternatives, and consequences.

flow.md
  Implemented flows: entry points, command tree, --version flow, scan flow with nested calls,
  discovery policy, mermaid diagram, changes-in-this-session table.

src/edgehunter/__init__.py
  Package docstring and the single version literal __version__ = "0.1.0".

src/edgehunter/__main__.py
  Enables python -m edgehunter by calling cli.main().

src/edgehunter/cli.py
  Typer application: root callback with eager --version, scan command, summary printer,
  ScanError handling with exit code 2, main() console entry.

src/edgehunter/scanner/__init__.py
  Public scanner interface re-exporting scan_repository, ScanSummary, error classes,
  is_excluded_directory.

src/edgehunter/scanner/repository.py
  Filesystem-only discovery: _resolve_target, scan_repository, _walk_python_files,
  EXCLUDED_DIRECTORY_NAMES/SUFFIXES, ScanError hierarchy, frozen ScanSummary dataclass.

tests/test_cli.py
  Help/version/scan help, usage errors, valid scan (directory and file), missing path,
  non-Python file, and subprocess entry-point tests.

tests/test_repository_scanner.py
  Collection, exclusion/pruning, single-file target, empty directory, error cases,
  common error base, deterministic ordering, excluded-name predicate.
```

`handoff.md` (this file) is created in the follow-up documentation commit; no code changes accompanied it.

## 4. Current Architecture / State

Implemented and working today:

```text
src/edgehunter/
├── __init__.py        version literal only (import-safe, no runtime deps)
├── __main__.py        python -m edgehunter -> cli.main()
├── cli.py             Typer app; presentation layer only (parses input, prints output)
└── scanner/
    ├── __init__.py    public interface
    └── repository.py  os.walk discovery with pruning; no imports/execution of target code
```

Call chain: `edgehunter` console script or `python -m edgehunter` -> `cli.main()` -> `typer.Typer` app -> `scan()` -> `scanner.scan_repository()` -> `_resolve_target()` + either single-file branch or `_walk_python_files()` -> `ScanSummary` -> `cli._print_scan_summary()`.

Data at rest: none. The scan writes nothing to disk and keeps no state. Output goes to stdout; errors to stderr.

Safety boundary: the scanner touches only the filesystem. No `eval`/`exec`, no imports of target repos, no execution. This boundary must be preserved until the future isolated execution phase.

## 5. Decisions Made

- **D-001** — `src/` layout for the package; tests at top level.
- **D-002** — setuptools backend; version has a single source (`edgehunter.__version__`, read via `attr`).
- **D-003** — Typer as the CLI framework; entry point `edgehunter.cli:main`; completion options disabled.
- **D-004** — `scan` does filesystem discovery only; explicit exclusion list plus `.egg-info` suffix; single `.py` file targets allowed; deterministic sorted traversal; no symlink following.
- **D-005** — exit code convention: 0 success, 2 invalid user input; unexpected exceptions propagate.
- **D-006** — dev tooling as a `dev` extra: pytest, ruff, mypy; all configured in `pyproject.toml`.
- **D-007** — one subprocess test (`python -m edgehunter --version`) to verify packaging/entry point.
- **D-008** — CLI code/tests use `typer.testing` only, never standalone `click` (typer 0.27.3 vendors click; older typer depends on separate click).

## 6. Requirements and Constraints

- **Python:** `requires-python = ">=3.10"`; no upper bound. Only Python 3.13.14 has been exercised so far.
- **Dependencies:** runtime is `typer>=0.12` only. Dev extras: `pytest>=8`, `ruff>=0.6`, `mypy>=1.10`. Keep dependencies minimal; any new dependency needs a decisions.md entry comparing alternatives.
- **No premature scaffolding:** do not create files/subpackages for features that do not exist (no AST, oracle, search, execution modules yet). Document extension points instead.
- **Deterministic logic before AI; models propose, execution proves.** Do not design the model as a free-form test generator.
- **Safety:** never import/execute scanned code in the scanner; execution belongs to a future isolated phase.
- **CLI:** user errors must be clean (no traceback) and non-zero; user errors use exit code 2.
- **Testing honesty:** tests must actually be run; README and docs claim only what exists.
- **Documentation process:** read `decisions.md`, `flow.md`, `handoff.md` before any task; add decisions.md entries for meaningful changes (append-only, `D-NNN`); update flow.md with real names when flows change; rewrite handoff.md at end of session.
- **Authorship:** commits solely as `PrathamKapoor <prathamkapoor027@gmail.com>`; no co-author or tool attribution in commits or docs; never commit `paper/`, `research/`, or `*.tex`.

## 7. Testing and Verification

Commands actually executed in this session and their results:

```text
.venv\Scripts\python.exe -m pytest -v
  -> 18 passed in 2.75s

.venv\Scripts\python.exe -m pytest -q
  -> 18 passed in 14.52s (after switching cli.py to Annotated style)

.venv\Scripts\python.exe -m ruff check .
  -> All checks passed!   (after fixing one B008 warning by adopting Annotated)

.venv\Scripts\python.exe -m ruff format --check .
  -> 9 files already formatted (run before README.md and handoff.md existed)
  -> 11 files already formatted (final run: 7 Python files + 4 Markdown files; see Known Issues)

.venv\Scripts\python.exe -m mypy
  -> Success: no issues found in 7 source files
```

CLI verified manually with the installed console script and module entry point:

```text
.venv\Scripts\edgehunter.exe --version        -> "edgehunter 0.1.0", exit 0
.venv\Scripts\edgehunter.exe --help           -> usage, --version option, scan command listed, exit 0
.venv\Scripts\edgehunter.exe scan --help      -> PATH argument documented, exit 0
.venv\Scripts\edgehunter.exe scan .           -> "python files discovered: 7", "excluded directories skipped: 9", exit 0
.venv\Scripts\edgehunter.exe scan .\does-not-exist -> "error: path does not exist: does-not-exist", exit 2
.venv\Scripts\edgehunter.exe scan pyproject.toml   -> "error: not a Python source file: ...", exit 2
.venv\Scripts\python.exe -m edgehunter --version   -> "edgehunter 0.1.0", exit 0
```

Not tested (do not assume these work):

- Python 3.10, 3.11, 3.12 (only 3.13.14 present).
- POSIX filesystem behavior (paths, symlinks, permissions).
- Permission-denied paths and unreadable subdirectories.
- Symlink cycles or broken symlinks as scan targets.
- Repositories modified during a scan.
- Large repositories (performance has not been measured).

## 8. Known Issues / Risks

**Confirmed:**

- None blocking. All executed checks pass.

**Possible:**

- `os.walk` is called without an `onerror` handler, so unreadable subdirectories are silently skipped during a directory scan (Python default behavior). Not verified on Windows with a real permission-denied directory.
- Suffix matching is case-sensitive: a file named `X.PY` is not collected even on case-insensitive Windows filesystems. Intentional per D-004, but may surprise.
- The exclusion list is policy, not truth; unconventional vendor directories will be scanned.
- Help output is rendered by rich (installed transitively by typer 0.27.3); box-drawing and layout may differ across typer/rich versions. Tests assert only substrings, not exact formatting.
- `ruff format` (0.16.10) also formats Python code fences inside Markdown files: `ruff format --check .` counts README.md, decisions.md, flow.md, and handoff.md alongside the 7 Python files (confirmed with `--verbose`). Untagged or non-Python code fences in the current docs are left untouched. Future doc edits with ```` ```python ```` fences will be reformatted by `ruff format`.
- No `LICENSE` file has been chosen. Public release needs a license decision.
- README contains a `<repository-url>` placeholder in the install instructions; no remote exists yet.
- No CI workflow exists; checks are manual commands (listed in README and section 7).
- `git add` reported LF→CRLF conversion warnings on Windows (core.autocrlf); files are stored with LF in git and checked out CRLF locally. No `.gitattributes` exists yet.
- The subprocess test (`test_python_dash_m_entry_point_reports_version`) requires the package to be installed in the running interpreter; from an uninstalled source checkout it fails while in-process tests pass.

## 9. Unfinished Work

Nothing from Phase 0 is unfinished. Everything listed in the phase definition was implemented, executed, and verified.

Intentionally absent (later phases): AST extraction, CFG/dataflow analysis, boundary mining, property inference, hypothesis generation, semantic transformations, LLM integration, training, RAG/vector stores, failure retrieval, Docker/sandbox execution, severity classification, counterexample shrinking, CI, API/UI, multi-language support, research tooling.

## 10. Next Subphase

**Repository Scanner + AST Extraction.**

Expected work (per project plan): extend the `scanner` package to parse discovered Python files with the standard `ast` module, record module/function/class structure and relationships, and keep deterministic, execution-free behavior. The AST layer should build on `scan_repository()` output (`ScanSummary.python_files`), and failures to parse individual files need a defined policy (record-and-continue vs abort) with tests.

## 11. Critical Context

- **Project venv:** `.venv/` at the repo root (gitignored). Everything verified with `.venv\Scripts\python.exe`. Installed versions: edgehunter 0.1.0 (editable), typer 0.27.3, rich 15.0.0, pytest 9.1.1, ruff 0.16.10, mypy 2.4.0, Python 3.13.14.
- **No standalone click in the venv.** Typer 0.27.3 vendors click (`typer._click`); `import click` raises `ModuleNotFoundError`. Use `typer.testing.CliRunner` / `typer.testing.Result` (see D-008). The `typer>=0.12` floor allows older versions where click is separate; project code must not care.
- **Version single source:** `src/edgehunter/__init__.py` contains the literal `__version__`; `pyproject.toml` reads it dynamically. Bump versions only there.
- **Exit codes:** 0 success, 2 invalid input (`EXIT_INVALID_INPUT` in `cli.py`); unexpected exceptions are not caught (traceback by design).
- **Error hierarchy:** `ScanError` base; `PathNotFoundError`, `UnsupportedTargetError` subclasses, all in `scanner/repository.py`, re-exported from `scanner/__init__.py`.
- **Install command for development:** `.venv\Scripts\python.exe -m pip install -e ".[dev]"` (or the activated-venv equivalent `pip install -e ".[dev]"`).
- **Git identity:** repository-local `user.name` / `user.email` are set to `PrathamKapoor` / `prathamkapoor027@gmail.com`; no remote configured.
- **Windows shell:** commands run in PowerShell 7; use `Get-ChildItem`-style commands or the repo's documented commands as-is.
- **Documentation contract:** `decisions.md` is append-only with format-mandated fields; `flow.md` must use real function/file names with call order; `handoff.md` must be rewritten with its 12 fixed sections at the end of each session.

## 12. Agent Instructions

1. Read `decisions.md`, `flow.md`, and `handoff.md` completely before changing code.
2. Make the smallest coherent change; keep the CLI as a thin presentation layer and library modules print-free.
3. Never import or execute scanned target code outside the future isolated execution subsystem.
4. Update `decisions.md` (new `D-NNN` entry, required fields) and `flow.md` (real names, call order, session table row) in the same commit as the change.
5. Run before every commit: `.venv\Scripts\python.exe -m pytest`, `-m ruff check .`, `-m ruff format --check .`, `-m mypy`. Do not claim checks passed without pasting real results into the handoff.
6. Commit only as `PrathamKapoor <prathamkapoor027@gmail.com>`; no co-author or tool/model attribution anywhere.
7. Do not commit `.venv/`, caches, egg-info, `paper/`, `research/`, or `*.tex`.
8. Do not implement features from later phases (see section 9) unless the task explicitly requires them.
9. At the end of the next session, rewrite `handoff.md` with all 12 sections and record actual commit hashes in `decisions.md`/`flow.md`.
