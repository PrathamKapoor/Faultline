# Security and trust model

EdgeHunter's central claim is not "EdgeHunter finds bugs". It is narrower and
more important:

> **EdgeHunter will not tell you your code is broken unless an explicit,
> applicable expectation was contradicted, twice, on a source snapshot whose
> fingerprint it can verify.**

That claim is only as good as the boundary it rests on. This document describes
that boundary as it actually is: what is trusted, what is not, what is contained,
and — more importantly — what remains possible even when everything works as
designed.

Nothing here should be read as a guarantee. Container isolation reduces risk; it
does not eliminate it.

---

## 1. Trust zones

```text
┌─ TRUSTED: host orchestrator ────────────────────────────────────────────┐
│                                                                        │
│  cli.py  pipeline.py  scanner/  analysis/  oracle/  generation/         │
│  evaluation/  search/  benchmark/                                       │
│                                                                        │
│  Never imports target code. Never evaluates a target expression.       │
│  Treats the repository strictly as data.                               │
│                                                                        │
├────────────────────── read, parse, analyse ─────────────────────────────┤
│                                                                        │
│  UNTRUSTED: the repository under analysis                             │
│                                                                        │
│  Source text, AST, string annotations, comparison literals.             │
│  All of it may be hostile and none of it is executed here.             │
│                                                                        │
├────────────────────── stage a bounded copy ─────────────────────────────┤
│                                                                        │
│  execution/staging.py                                                  │
│    containment checked, symlinks refused, .py only,                    │
│    ≤100 files, ≤1 MiB each, ≤8 MiB total                               │
│                                                                        │
├══════════════ DOCKER BOUNDARY (the real trust line) ══════════════════┤
│                                                                        │
│  UNTRUSTED: the container                                              │
│                                                                        │
│  runtime/worker.py + the staged target, in one process context.        │
│  The target's code runs here, with the same OS privileges as the       │
│  worker. See §7.                                                        │
│                                                                        │
├────────────────────── typed JSON, one direction ───────────────────────┤
│                                                                        │
│  host decodes the result document; it re-derives nothing from it       │
│                                                                        │
├────────────────────────────────────────────────────────────────────────┤
│  EVIDENCE: .edgehunter/runs/<id>/*.json (Git-ignored)                  │
│                                                                        │
│  Deliberately excludes captured stdout, stderr, and exception          │
│  messages, so this directory cannot become a place secrets collect.    │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. What runs where

| Component | Location | Runs target code? |
|---|---|---|
| `scanner`, `analysis`, `oracle`, `generation` | host | **No** |
| `evaluation` (verdict logic) | host | **No** |
| `search` (policy, state, actions) | host | **No** |
| `shrink` (contract adapter) | host | **No** |
| `execution` (staging, protocol, docker) | host | **No** |
| `runtime/worker.py` | container | **Yes** |
| the analysed target | container | **Yes** |

The host never imports a target module, never calls `eval`/`exec`/`pickle` on
anything from the repository, and never runs a candidate. The only code that
imports target source is the worker, and the worker only ever runs inside a
container.

This is enforced by test, not by convention: `tests/test_safety.py`,
`tests/test_execution_safety.py`, and `tests/test_evaluation_safety.py` assert
the absence of import, execution, and deserialization primitives in each host
module, and `tests/test_shrink_search.py` does the same for `shrink`.

---

## 3. Why Docker is required rather than optional

There is no host fallback, and no flag to enable one. Three reasons:

1. **Analysing untrusted code and running it are different risks.** Reading a
   file cannot execute it. Importing it can. A tool whose "fast path" imports
   untrusted source would make every scan an execution event.
2. **Containment must be the default.** If host execution were available, a
   misconfigured invocation would silently become a worse one. There is no path
   that reaches a target without passing the availability and authorization
   checks in `execution/docker.py` and `execution/repository.py`.
3. **Budgets must be enforceable.** Timeouts, process limits, and cleanup are
   container properties. On the host they would be best-effort at best.

---

## 4. Container restrictions

Built by `execution/docker.py::build_run_command()` and asserted by
`tests/test_execution_docker.py`:

| Restriction | Value | Purpose |
|---|---|---|
| Network | `--network none` | No exfiltration, no dependency install, no beaconing |
| Root filesystem | `--read-only` | The image cannot be modified at runtime |
| Capabilities | `--cap-drop ALL` | No `CAP_SYS_ADMIN`, no raw sockets, no mount |
| Privilege escalation | `--security-opt no-new-privileges` | `setuid` binaries cannot gain privilege |
| Process count | `--pids-limit 64` | Fork bomb containment |
| Memory | `--memory 256m` | Allocation bomb containment |
| CPU | `--cpus 1.0` | CPU starvation of the host |
| User | `--user 65534:65534` (nobody) | The target is not root |
| Writable space | `--tmpfs /tmp:rw,nosuid,nodev,size=16m` | The only writable path, bounded and unexecutable |
| Workspace mount | `target=/workspace,readonly` | Staged source cannot be edited |
| Request mount | `target=/input,readonly` | The request cannot be read back or tampered with |
| Docker socket | **not mounted** | The container cannot start containers |

`build_run_command` also refuses to build a command containing `--privileged`,
`--network=host`, or `--userns=host`; a test asserts their absence.

---

## 5. Staging

`execution/staging.py` decides what crosses into the container:

- every path is resolved and checked for containment inside the scanned root, so
  `..` cannot escape;
- symlinks and Windows junctions are refused outright rather than followed;
- only `.py` files are staged;
- at most 100 files, 1 MiB each, 8 MiB total;
- the original repository is **never** mounted, writable or otherwise. The
  container sees the staged copy only.

Windows drive-letter paths are checked as Windows paths as well as POSIX ones.
This is a historical defect that was found and fixed; `tests/test_execution_staging.py`
keeps regression tests for absolute paths and drive-letter escapes.

---

## 6. Serialization across the boundary

Values cross in **one direction**, as strict typed JSON
(`execution/codec.py`), and never as Python text:

- every value is a tagged `TypedValue`, not rendered source;
- rendered literals are recognized by an AST whitelist, never by `eval`;
- the request document is capped at 64 KiB, so an oversized request never starts
  a container;
- nesting depth ≤ 4, collection items ≤ 100, mapping entries ≤ 100, strings
  ≤ 4096 characters;
- the integer path is `int()`, never `ast.literal_eval`;
- the host reads the result through a key allowlist and rejects a document with
  an unexpected key, a wrong nonce, or a mismatched execution id.

There is no `pickle`, no arbitrary-object deserialization, and no `eval` of any
user-controlled string on either side of the boundary.

---

## 7. Known limitations of the container boundary

These are the honest caveats. None of them is fixed by the measures in §4.

### 7.1 The target and the worker share a process

`runtime/worker.py` imports the target module and calls the function **in the
same interpreter**. That means:

- Target code can reach the worker's own module objects.
- Target code can attempt to monkeypatch, introspect, or subvert the observation
  path.
- A target that deliberately fabricates a result document can influence what the
  host believes it observed.

The protocol's nonce and execution-id echo associate a result with a request,
and the host verifies both. **That is a consistency check, not a cryptographic
guarantee.** A determined hostile target running in the same process can observe
the nonce. Treating the container's word as ground truth is a deliberate,
documented trust decision, and it is why findings are reported as
*reproduced disagreement with an explicit contract* rather than as proven facts.

### 7.2 Container escape is out of scope

Docker's isolation is a strong default, not a proof. A kernel or runtime
vulnerability in Docker itself defeats everything above. EdgeHunter does not
attempt to detect or prevent that, and does not claim to.

### 7.3 Resource limits are best-effort against determined code

`--memory` and `--pids-limit` bound ordinary misbehaviour. A carefully
constructed payload can still consume the wall-clock budget, which is why every
operation has a timeout and a budget and why a timeout is reported as
`TIMEOUT` — never as a match, a mismatch, or a defect.

### 7.4 A timeout is not a verdict

A container that is killed for exceeding its time limit tells you nothing about
whether the contract holds. EdgeHunter classifies it as `INCONCLUSIVE` /
`INFRASTRUCTURE_FAILURE`, and no policy is permitted to read it as evidence in
either direction.

### 7.5 Observed exception identity is real but not absolute

Exception identity is decided by class identity and MRO walk **inside the
sandbox** (`type.__getattribute__`, real `__mro__`, identity comparison against
`builtins`). That is far stronger than comparing names — a target's own
`class ValueError(Exception)` is correctly reported as *not* the builtin. But it
is still an observation produced by a process the target also runs in; see §7.1.

### 7.6 Cached worker images can be stale

The worker image is cached by tag. After editing `runtime/worker.py`, an existing
`edgehunter-runtime:1` still contains the **previous** worker and will produce
documents without newer fields; the host reads those fields as unknown rather
than as an answer, but the behaviour will be older than the source.

**Before integration testing after a worker edit, run:**

```bash
docker image rm edgehunter-runtime:1
```

To verify freshness without deleting anything, compare the SHA-256 of the
working-copy `runtime/worker.py` with the file inside a throwaway container:

```bash
docker cp "$(docker create edgehunter-runtime:1):/opt/edgehunter/site/edgehunter/runtime/worker.py" ./worker-in-image.py
```

---

## 8. Oracle correctness as a security property

The most damaging failure mode for a tool like this is not a crash; it is a
**confident false report**. The controls against it:

| Risk | Control |
|---|---|
| A finding on a correct target | An exact-input expectation is never generalized; a finding needs a reproduced contradiction |
| An exception read as a bug | Expected exceptions are `MATCH`; only *disagreement* is a mismatch |
| A timeout read as a bug | Infrastructure failures are `INCONCLUSIVE`, never evidence |
| A wrong exception class accepted | Builtin identity is decided by class identity and real MRO, never by name |
| A custom exception "matched" | A non-builtin expectation stays `INCONCLUSIVE` |
| Two disagreeing expectations | Reported as `CONFLICTING_EVIDENCE`; neither is chosen |
| Code changed mid-run | Every operation verifies a SHA-256 content fingerprint of the staged bytes |
| A contract silently rebound to edited source | Fingerprint mismatch is refused before execution |
| A minimized input that no longer contradicts | Shrinking re-verifies the contract and the failure signature at every step |

---

## 9. Explicit authorization

`run`, `shrink`, `explore`, and `benchmark` all require `--allow-execution`. The
presence of a contract file, a target name, or a fixture is **never**
authorization. `scan` is static, imports nothing from the repository, and never
requires or accepts the flag.

---

## 10. Reporting a vulnerability

EdgeHunter is not yet published, so there is no security contact address to
publish. Once a repository exists, add a `SECURITY.md` policy section with a
real contact route rather than relying on a public issue tracker for sandbox
escape reports.

In the meantime, vulnerabilities in EdgeHunter itself can be reported directly
to the repository owner. Vulnerabilities in **Docker**, the Python runtime, or
the host kernel are out of scope and should go to those projects.

---

## 11. What this document does not claim

- It does not claim the sandbox is unbreakable.
- It does not claim the container's observations are cryptographically verified.
- It does not claim EdgeHunter can prove a repository correct.
- It does not claim zero false positives. It claims that the paths that would
  produce them are closed, tested, and documented — and §8 lists which is which.