# Universal Boilerplate — Authoritative Baseline Spec

**Status: LOCKED.** This document is the authoritative specification of the
Universal Boilerplate. It describes only what exists on disk and what has
been verified. Future work adds bricks on top of this foundation; it does
not redesign it.

Locked: 2026-09-18. Location: `/home/hatch/workspace/boilerplate/`.

---

## 1. What the boilerplate is for

A reusable foundation for building small, sellable, local-first Windows AI
desktop applications. Stack: PySide6 (GUI), SQLite (persistence), Ollama
(local model server), PyInstaller (packaging).

It is intentionally generic: it provides infrastructure (config, logging,
database, AI access, UI shell, tests) and empty slots for product code. It
contains no product behavior of its own beyond a minimal example.

---

## 2. Current directory structure

```
boilerplate/
├── README.md                  # Usage guide (points here for the baseline spec)
├── UNIVERSAL-BOILERPLATE.md   # This file — the locked baseline
├── requirements.txt           # PySide6, requests, pyinstaller, pytest
├── main.py                    # Application entry point
├── .gitignore
├── app.spec                   # PyInstaller spec (onedir)
├── scripts/
│   └── build_windows.bat      # Windows build script
├── src/
│   └── app/
│       ├── __init__.py
│       ├── __main__.py        # Allows `python -m src.app`
│       ├── core/              # Infrastructure
│       │   ├── paths.py       # Path resolution (dev + frozen modes)
│       │   ├── logging_setup.py
│       │   ├── config.py      # ConfigManager (JSON file + env-var
│       │   │                       #   overrides; precedence env > file > defaults)
│       │   └── app_context.py # Composition root (chat_provider seam,
│       │                           #   idempotent shutdown)
│       ├── data/              # SQLite persistence
│       │   ├── database.py    # Per-thread connections, WAL mode, migrations
│       │   └── repositories.py# Conversation + Message repositories
│       ├── ai/                # AI service layer
│       │   ├── __init__.py    # Re-exports ChatProvider, OllamaChatProvider,
│       │   │                  # ProviderMessage
│       │   ├── ollama_service.py  # Concrete Ollama REST abstraction
│       │   └── provider.py    # Provider boundary (protocol + adapter)
│       ├── product/           # PRODUCT CODE SLOT (see §4)
│       │   └── example_service.py # Product example: depends on the
│       │                           #   ChatProvider seam, tested with fakes
│       ├── resources/__init__.py
│       ├── ui/                # GUI shell
│       │   ├── main_window.py
│       │   ├── chat_worker.py # QThread background worker (ChatProvider-based)
│       │   ├── settings_dialog.py
│       │   ├── about_dialog.py
│       │   ├── dialogs.py
│       │   └── style.py
│       └── utils/helpers.py
└── tests/
    ├── conftest.py            # Isolated fixtures (temp db, temp config)
    ├── test_app_context.py    # 4 tests (seam wiring + shutdown lifecycle)
    ├── test_config.py         # 12 tests
    ├── test_database.py       # 8 tests
    ├── test_logging.py        # 4 tests (handler setup, no-duplicate guard)
    ├── test_ollama_service.py # 20 tests (mocked HTTP, no server)
    ├── test_paths.py        # 11 tests (dev/frozen/user-data resolution)
    ├── test_product.py        # 3 tests
    ├── test_provider.py       # 8 tests (fakes, no network)
    ├── test_repositories.py   # 10 tests
    └── test_utils.py          # 6 tests
```

---

## 3. What is reusable

Everything outside `src/app/product/` is generic infrastructure:

- `core/`: paths, rotating file logging, persistent JSON `ConfigManager`
  (with change signals), and `AppContext` as the composition root.
  `AppContext` exposes the provider seam as `chat_provider`
  (protocol-typed `ChatProvider` wrapping the managed `OllamaService`;
  config changes keep working through it with no extra rewiring).
- `data/`: SQLite with a migration system, per-thread connections, WAL
  mode, and conversation/message repositories using plain parameterized SQL
  (no ORM, by design).
- `ai/`: the concrete `OllamaService` plus the provider boundary —
  `ChatProvider` protocol, `ProviderMessage`, and the `OllamaChatProvider`
  adapter. This is the seam future products use to swap or fake providers.
- `ui/`: a working GUI shell (main window, menus, toolbar, status bar,
  system tray, settings dialog, dark style) with the QThread worker pattern
  for non-blocking AI calls.
- `utils/`: small helpers.
- `tests/`: the full isolated test suite (see §6).

---

## 4. What is intentionally product-specific and must NOT be placed in the boilerplate

The following belong in `src/app/product/` (or a product subpackage under
it) and must never leak into the generic layers:

- Agent/product identities (names, descriptions, personas).
- System prompts and operating rules.
- Product behavior and business logic (e.g., an agent's `chat()` loop).
- Product-specific configuration dataclasses.
- Product-level error vocabularies. (The boilerplate already ships the
  equivalent pattern at the infrastructure level: one base exception per
  layer with typed subtypes, e.g. the `OllamaError` hierarchy.)
- Authentication, memory systems, tool/plugin frameworks.

`src/app/product/example_service.py` is the only product code allowed here,
and it is a minimal orchestration example, not a product.

---

## 5. Current AI/provider foundation

Two layers, with a hard boundary between them:

1. **Concrete** — `OllamaService` (`ai/ollama_service.py`): the only place
   that knows the Ollama REST API (`GET /`, `GET /api/tags`,
   `POST /api/chat`, streaming NDJSON). Configurable host/model/timeout.
   Typed errors: `OllamaError` base with `OllamaConnectionError`,
   `OllamaModelNotFoundError`, `OllamaTimeoutError`, `OllamaAPIError`.
   Data types: `ChatMessage`, `OllamaModel`.

2. **Abstract** — `provider.py`: `ProviderMessage` (frozen dataclass, the
   single message shape crossing the boundary), `ChatProvider`
   (`runtime_checkable` Protocol with two methods — `chat(messages,
   system_prompt) -> str` and `chat_stream(messages, system_prompt) ->
   Iterator[str]`), and `OllamaChatProvider`, the one sanctioned adapter
   wrapping `OllamaService` without modifying it. The adapter propagates
   `OllamaError` unchanged — no new exception types.

**Rule:** product code depends on `ChatProvider`, never on `OllamaService`
directly. This is what makes providers swappable and product logic testable
with in-memory fakes.

**Wiring:** `AppContext` exposes the seam as `self.chat_provider`, an
`OllamaChatProvider` wrapping the same `OllamaService` instance the context
manages. Because the adapter delegates to that instance, host/model/timeout
changes applied via `_on_config_changed` remain effective through the seam
automatically. Product code should obtain the provider from the context
(`ctx.chat_provider`) rather than constructing the adapter itself.

---

## 6. Current testing foundation

- Framework: pytest. Run: `python -m pytest tests/ -v`.
- Isolation: every test uses temporary directories; no test touches real
  user data, the network, a real Ollama server, or a display.
- Techniques in use: mocked HTTP responses (`test_ollama_service.py`),
  temporary SQLite databases (`test_database.py`, `test_repositories.py`),
  temporary config files (`test_config.py`), and protocol-conforming fakes
  (`test_provider.py`).
- Note: `tests/conftest.py` imports `ConfigManager`, which requires PySide6.
  The full suite therefore requires PySide6 installed. Tests that do not
  need fixtures (e.g. `test_provider.py`) can run with `--noconftest`.
- Reproducible verification: `python3 scripts/verify.py` (pure stdlib,
  cross-platform) is the one-command gate. From a fresh checkout with
  only a system Python 3 it creates (or reuses) a venv, installs the
  test dependencies (`pytest`, `PySide6`, `requests`), runs the full
  suite, and verifies the Ivan Core SHA-256 baseline. It exits non-zero
  on any failure and never skips silently: a missing baseline file is an
  error, not a pass. Overrides: `BOILERPLATE_VENV` (venv dir, default
  `<repo>/.venv`), `IVAN_DIR`, `IVAN_BASELINE`. This exists because a VM
  reset once wiped the system site-packages and the `/tmp` baseline,
  leaving verification dependent on tribal knowledge to rebuild; the
  script makes the from-scratch path executable and repeatable.
- Reproducible packaging: `python3 scripts/package.py` is the
  one-command package gate. It ensures PyInstaller is installed in the
  venv, runs `pyinstaller app.spec --noconfirm --clean`, then executes
  the frozen binary with `--self-test` (offscreen Qt, redirected
  user-data dir, no proxy env) and requires exit 0 plus the
  `self-test OK` marker. Any failure exits non-zero. `main.py
  --self-test` is the headless smoke test: it constructs a real
  QApplication (proving Qt platform plugins load from the bundle),
  builds the full AppContext, runs a small SQLite round-trip through
  the repositories, and shuts down cleanly. It writes to the normal
  user data directory, so the script (and the regression tests) run it
  with a redirected HOME/LOCALAPPDATA to keep it hermetic.

---

## 7. Conventions future products must follow

These are observed from what exists and is verified — not aspirations:

1. **Product code lives in `src/app/product/`**, as a subpackage per
   product with its public surface re-exported from its `__init__.py`.
   The generic layers (`core/`, `data/`, `ai/`, `ui/`) stay untouched.
2. **Depend on the `ChatProvider` protocol**, not concrete services.
   Obtain it from the composition root (`ctx.chat_provider`); do not
   construct adapters or reach for `ctx.ollama` in product code. Both
   `chat()` and `chat_stream()` are available through the seam, so
   streaming never requires bypassing the boundary.
3. **Configuration is explicit and separate from logic.** Services receive
   settings via constructor arguments; logic never reads global state,
   environment, or config files directly. The only sanctioned environment
   reads live in infrastructure: `paths.py` (`LOCALAPPDATA`) and
   `ConfigManager`'s documented `<PREFIX><KEY>` overrides, with precedence
   env > file > defaults.
4. **Errors are typed where callers must distinguish failure modes.**
   The ai/ layer normalizes transport failures into the `OllamaError`
   hierarchy at its boundary. The data layer intentionally exposes
   `sqlite3.Error` unwrapped (stdlib-stable; no wrapper hierarchy is
   invented for its own sake) — nothing outside `data/` imports sqlite3
   today, and `Database` construction fails fast on corrupt files.
5. **No ORM, no speculative abstractions.** Plain SQL, small modules, one
   concern per file. A change to the foundation requires a documented,
   strictly-necessary reason.
6. **Every new reusable piece ships with isolated tests** following §6.
7. **Lifecycle is owned by the composition root and the entry point.**
   `AppContext.shutdown()` is idempotent; `main()` guarantees it runs via
   try/finally around the event loop. GUI close handlers do GUI-specific
   teardown first (workers, tray, geometry) and may also call it — the
   overlap is safe by design, never by accident.

---

## 8. What has been verified

- Full suite: **142 passed, 0 failed** (`python -m pytest tests/`),
  verified 2026-09-19 across normal, hostile-env, and
  TZ=Pacific/Kiritimati runs.
  - test_app_context: 5 · test_app_identity: 3 · test_chat_worker: 4 ·
    test_config: 19 · test_database: 11 · test_layer_boundaries: 3 ·
    test_logging: 4 · test_ollama_service: 34 · test_paths: 11 ·
    test_product: 3 · test_provider: 8 · test_repositories: 13 ·
    test_utils: 9 · test_verify: 12 · test_selftest: 2 ·
    test_packaging: 1
- Reproducible verification infrastructure (2026-09-18): verification
  itself was non-reproducible -- after a VM reset wiped pytest/PySide6
  from system site-packages and destroyed the `/tmp` checksum baseline,
  the only way to re-verify was hand-rebuilt tribal knowledge (a venv
  at a remembered path, a re-established baseline, a shell one-liner
  for checksums). Any future autonomous agent generating an app from
  this boilerplate would face the same wall on a fresh machine, and a
  green suite would depend on invisible environment luck. Fix:
  `scripts/verify.py`, a checked-in, pure-stdlib, cross-platform
  one-command gate: `python3 scripts/verify.py` creates (or reuses) a
  venv (`BOILERPLATE_VENV`, default `<repo>/.venv`), installs the test
  deps (`pytest>=8.0,<10.0`, `PySide6>=6.6`, `requests>=2.31,<3.0`), runs the
  full suite, then verifies the durable Ivan Core SHA-256 baseline
  (`IVAN_DIR`, `IVAN_BASELINE`). Any failure exits non-zero; a missing
  baseline is an error, never a silent skip. Added 6 regression tests
  for the checksum gate (`tests/test_verify.py`): all-match, tampered
  content, missing file, missing baseline (raises), malformed lines,
  and binary-marker/subdir parsing. Failure proof: pointed the script
  at a baseline copy with one flipped hex digit -- it reported
  `checksum mismatch` for the tampered entry and exited 1, proving the
  gate actually gates. From-scratch proof: ran the script with
  `BOILERPLATE_VENV` set to an empty directory -- it created the venv,
  installed the deps, ran the suite (133 passed), verified the Ivan
  baseline, and exited 0. (First attempt targeted `/tmp` and failed on
  pip with `No space left on device`; retried on the home filesystem --
  the script failed loudly rather than silently passing, which is the
  intended behavior, and the retry proved the bootstrap path.) Proof
  artifacts were removed afterwards. Verified 133 pass under normal,
  hostile-env (ambient `LOCALAI_DESKTOP_*` + dead proxy vars, NO_PROXY
  unset), and alternate-timezone (TZ=Pacific/Kiritimati) runs.
- Packaging smoke verification (2026-09-18): the `package` stage of
  bootstrap -> develop -> test -> verify -> package was the only stage
  with zero verification -- `app.spec` was hand-written and never
  executed, so a future agent would discover frozen-startup breakage
  (missing modules, wrong data paths, Qt platform plugin issues) only
  at ship time. Fix, in two small composable pieces: (1) `main.py
  --self-test`, a headless smoke test that constructs a real
  QApplication (proving Qt platform plugins load), builds the full
  AppContext, runs a small SQLite round-trip through the repositories,
  and shuts down cleanly, exiting 0 only on full success; (2)
  `scripts/package.py`, a one-command gate that ensures PyInstaller is
  installed, runs `pyinstaller app.spec --noconfirm --clean`, then
  executes the frozen binary with `--self-test` under a redirected
  user-data dir (hermetic: never touches real user data) and requires
  exit 0 plus the `self-test OK` marker. Added 2 regression tests
  (`tests/test_selftest.py`) that run the real entry point in a
  subprocess with redirected HOME: one asserts exit 0 + the OK marker,
  one asserts hermeticity (no writes outside the redirected home, data
  dir created inside it). Failure proof: the first run of the new
  tests failed with `ImportError: libEGL.so.1` -- the dev machine
  could not load QtWidgets at all, and the self-test path failed
  loudly (exit 1) instead of silently passing, exactly as designed.
  After resolving the system library, the full proof ran green:
  `scripts/package.py` built the frozen app with PyInstaller 6.22.3
  and the frozen binary printed `self-test OK: LocalAI Desktop 1.0.0`
  with exit 0. Build artifacts (`dist/`, `build/`) were removed
  afterwards; the gate rebuilds them deterministically. Verified 135
  pass under normal, hostile-env, and alternate-timezone runs.
- Gate composition + dependency single-source-of-truth (2026-09-18):
  the two gates did not compose. `scripts/package.py` went straight to
  the expensive PyInstaller build without running the test suite, so a
  green package created false confidence about red tests -- and a
  failure was discovered late (minutes into a build) instead of early.
  Separately, `scripts/verify.py` and `scripts/package.py` hardcoded
  their own dependency version floors, duplicating `requirements.txt`
  as a second source of truth that would silently rot when requirements
  change. Fix: (1) `package.py` now runs the full test suite via
  `verify.run_tests()` *before* building and refuses with a clear
  `test suite failed; refusing to build` message on red -- the
  composition contract is tests -> build -> frozen smoke, enforced in
  code, not convention; (2) both scripts read their install specs from
  `requirements.txt` through a new `verify.load_requirement_specs()`
  parser (case-insensitive names, skips comments/blanks, requested
  order; missing file or missing name raises loudly); (3) `package.py`
  removes any previous `dist/` before building (`--clean` only clears
  `build/`), so a stale artifact can never contaminate the rebuild
  under verification. Added 4 tests: 3 for the requirements parser
  (ordering/case/comments, missing file, missing name) in
  `tests/test_verify.py`, and 1 gate-composition test in the new
  `tests/test_packaging.py` that injects a deliberately failing probe
  test, runs the real `scripts/package.py` in a subprocess, and asserts
  it exits non-zero with `refusing to build` and never prints
  `building with PyInstaller` (a `BOILERPLATE_GATE_NESTED` recursion
  guard keeps the inner suite run from re-entering the composition
  test). Failure proof: the composition test itself is the deliberate
  break -- with the probe present the gate refuses before PyInstaller
  starts. Verification (2026-09-19, after the execution-backend outage):
  the composition test initially FAILED -- root-caused to
  `tests/test_packaging.py::_run_package()` deriving the venv dir as
  `Path(sys.executable).resolve().parent.parent`. The durable venv's
  python is a symlink to `/usr/bin/python3`, so this resolved to `/usr`
  and package.py attempted `venv.create("/usr", clear=True)`, dying
  with `ModuleNotFoundError: No module named
  '_sysconfigdata__x86_64-linux-gnu'` before reaching the gate logic.
  Fixed: `Path(sys.prefix)` plus an assert that
  `(venv_dir / "pyvenv.cfg").exists()`, so the test can never point
  package.py at `/usr` again. Two related fixes in the same pass: (1)
  `scripts/package.py` never installed test deps -- a fresh venv died
  on `No module named pytest` instead of running the gate, so it now
  calls `verify.ensure_test_deps()` after `ensure_venv()`, before the
  test gate; (2) the test self-heals a stale probe file (a backend kill
  between write and unlink would otherwise leave the deliberately
  failing probe for the next run to collect). The composition test now
  passes against the real `scripts/package.py` -- the refusal behavior
  is proven live, not just reviewed. Full suite: 139 passed under
  normal, hostile-env (ambient `LOCALAI_DESKTOP_*` + dead proxy vars),
  and alternate-timezone (TZ=Pacific/Kiritimati) runs. `python3
  scripts/verify.py` end-to-end: exit 0 (fresh venv bootstrap at
  `<repo>/.venv`, 139 tests, Ivan baseline 9/9 OK). Fresh-venv packaging
  proof: `BOILERPLATE_VENV` pointed at an empty directory --
  `scripts/package.py` created the venv, installed `pytest>=8.0,<9.0`
  (resolved to 8.4.2), `PySide6>=6.6`, `pyinstaller>=6.0,<7.0`, ran the
  suite green, removed `dist/`, built with PyInstaller, and the frozen
  binary printed `self-test OK: LocalAI Desktop 1.0.0` with exit 0.
  Dependency-bounds note: the durable venv runs pytest 9.1.1 while
  `requirements.txt` declared `<9.0`. The suite is verified green on
  both 8.4.2 (fresh venv) and 9.1.1 (durable venv), so the bound was
  widened to `pytest>=8.0,<10.0` to match verified reality instead of
  adding version-enforcement machinery without demonstrated need.
  Environment note:
  this VM was missing `libEGL.so.1` (PySide6 system dependency), so the
  2 selftest tests failed with ImportError until `libegl1` 1.7.0-1build1
  was installed via apt -- same class of environment gap as the
  2026-09-18 packaging proof, not a code defect. The 5 timestamp
  `_strptime` errors reported during the outage did not reproduce and
  no pollution mechanism was found in the fixed test (child env is a
  copy; probe file is removed in `finally`); they are attributed to the
  corrupted outage-era environment.
- Venv-target safety guard (2026-09-19): `verify.ensure_venv()` ran
  `venv.create(target, clear=True)` on any `BOILERPLATE_VENV` whose
  `bin/python` was missing -- and adopted any whose `bin/python`
  existed -- with no check that the target was actually a venv. The
  gate-composition incident proved the failure mode: a misresolved
  venv dir (`/usr`, from `Path(sys.executable).resolve().parent.parent`
  following a symlink) made the gate attempt `venv.create("/usr",
  clear=True)`. Worse, had `/usr/bin/python` existed, the gate would
  have silently pip-installed into the system interpreter. Fix:
  `ensure_venv()` now refuses any existing target that is neither an
  empty directory nor a venv (`pyvenv.cfg` present), raising loudly
  instead of wiping or adopting it; missing/empty dirs and real venvs
  keep the existing create/reuse behavior. Added 3 tests in
  `tests/test_verify.py`: populated non-venv dir is refused and its
  contents survive; a `bin/python` without `pyvenv.cfg` is refused
  (foreign interpreter not adopted); an empty dir still bootstraps a
  real venv. Failure proof: with the guard temporarily removed, both
  refusal tests fail (the old code path would wipe/adopt); with the
  guard restored, all 12 pass. Full suite: 142 passed under normal,
  hostile-env, and alternate-timezone runs; `python3 scripts/verify.py`
  end-to-end exit 0; Ivan baseline 9/9 OK.
- Migration-upgrade path (2026-09-18): the sequential `_migrate()`
  loop had never executed against a genuinely old database -- every
  migration test started from a fresh DB, so the one contract a
  migration system exists for (old database + user data in, upgraded
  database + user data intact out) was unproven. Added
  `test_database_upgrades_from_v1_preserving_data`, which hand-builds
  a v1 database (v1 schema, `_meta` pinned at version 1, real
  conversation/message rows), opens it with `Database`, and asserts
  the version advances to current, the v2 schema change applies, and
  all pre-existing rows survive. Failure proof: injected a
  data-wiping statement into `_migration_v2` -- the old suite stayed
  10/10 green (fresh-init fixtures contain no user data, so they are
  blind to upgrade data loss), while the new test fails. Both files
  restored byte-identical afterwards. (Note: a merely
  syntax-broken migration *is* caught by existing tests, since the
  fixture runs `_migrate()`; the blind spot was specifically
  data preservation through the upgrade path.) Verified 127 pass
  under normal, hostile-env, and alternate-timezone runs.
- Loopback proxy bypass (2026-09-18): `OllamaService` used bare
  `requests.get/post`, so proxy environment variables (HTTP_PROXY,
  HTTPS_PROXY, ALL_PROXY -- on Windows these can also come from the
  registry) were honored even for `http://localhost:11434`. On a
  machine with a proxy set and no NO_PROXY entry for localhost, every
  AI call was routed to the proxy, which typically refuses it -- the
  app then misleadingly reported "Could not connect to Ollama. Is the
  server running?" while Ollama was fine. Environment-specific,
  platform-specific, invisible in normal dev. Fix: the service now
  passes per-request `proxies={"no_proxy": "localhost,127.0.0.1,::1"}`
  (requests' own no_proxy mechanism, stable across the pinned
  `requests>=2.31,<3.0` range) when the configured host is loopback
  (detected via `urllib.parse` + `ipaddress.is_loopback`, covering
  `localhost`, `127.0.0.1`, `::1`), recomputed in `set_host()`.
  Remote hosts keep default behavior so a corporate egress proxy is
  still honored where it is actually needed. All four request call
  sites (test_connection, list_models, chat, chat_stream) pass it.
  Added 14 tests: parametrized `_is_loopback_host` cases, bypass
  wiring for loopback vs remote, `set_host()` recompute, and two
  integration tests spinning up a real local HTTP stub server under a
  dead-proxy environment -- one proving the connection now succeeds,
  one control proving it fails with the bypass disabled (the old
  behavior). Failure proof: the control test fails if the bypass is
  removed. Verified 126 pass normally AND with a dead ambient proxy
  (`HTTP(S)_PROXY`/`ALL_PROXY` set, `NO_PROXY` unset).
- close_all() lifecycle contract (2026-09-18): `Database.close_all()`
  closed every connection but left each thread's `threading.local()`
  cache pointing at the closed object, so the next `get_connection()`
  returned the *same closed connection* and any use raised
  `sqlite3.ProgrammingError: Cannot operate on a closed database`
  (reproduced) -- the documented "get a usable connection" contract was
  silently broken by a public lifecycle method, with the failure far
  from its cause. Fix: a `_generation` counter bumped by `close_all()`;
  `get_connection()` discards any cached connection from an older
  generation and transparently opens a fresh one (a thread's own
  `threading.local` can't be cleared from the closing thread, hence the
  counter). `threading.Lock` became `threading.RLock` so
  `get_connection()` can hold it across `_create_connection()` without
  self-deadlock. Added 2 regression tests: single-thread reuse after
  `close_all()`, and cross-thread cache invalidation (worker caches a
  connection, main thread closes, worker must get a fresh usable one).
  Violation proof: restored the pre-fix implementation → both new
  tests fail with the original `ProgrammingError` → restored
  `database.py` byte-identical to the fixed version. Verified 112
  pass under normal, hostile-env, and alternate-timezone runs.
- Timestamp timezone correction (2026-09-18): `format_timestamp()`
  formatted SQLite `datetime('now')` strings with no timezone
  conversion — but SQLite's `datetime('now')` is UTC, so every message
  timestamp in the chat UI was silently displayed shifted by the user's
  UTC offset (e.g. +4h for EDT). The old test only asserted "Jan" and
  "2024" in the output — happy path, not the invariant. Fix: parse
  stored timestamps as UTC (naive values assumed UTC, the documented
  storage invariant) and convert to local time via `astimezone()`;
  unparseable input still returns unchanged. Storage is untouched, so
  existing databases display correctly. Added 3 regression tests
  pinning UTC→local conversion for the SQLite format, ISO with offset,
  and naive ISO — all timezone-independent (verified passing under
  EDT, UTC+14, and UTC-11). Violation proof: swapped in the old
  no-conversion implementation → all 3 new tests fail → restored
  `helpers.py` byte-identical to the fixed version.
- Test-suite hermeticity (2026-09-18): the suite was not isolated from
  the ambient environment — `ConfigManager` applies
  `<APP_NAME>_*` env overrides at load, so running pytest with e.g.
  `LOCALAI_DESKTOP_OLLAMA_HOST` set broke 5 config/context tests
  (reproduced). Every future product inherits this suite, so ambient
  CI/dev-machine variables would cause mysterious failures. Added an
  autouse fixture in `tests/conftest.py` that strips
  `<APP_NAME>_*` variables via monkeypatch before every test
  (restored afterwards; tests needing env vars set their own).
  Added `test_ambient_env_vars_do_not_leak_into_config` pinning the
  stripping logic. Verified: 107 pass in a clean env AND with hostile
  ambient vars set. Violation proof: disabled the fixture and
  re-ran under hostile vars — the original 5 failures returned;
  restored `conftest.py` byte-identical to the fixed version.
- Message-ordering determinism (2026-09-18): `list_by_conversation`
  ordered by `datetime(created_at)` alone, but `created_at` has second
  precision and the UI inserts the user row and assistant placeholder
  back-to-back — so ties occur on literally every exchange, and the
  "oldest first" contract rested on SQLite's sorter tie behavior rather
  than a guaranteed order. The same listing also builds the chat history
  sent to the provider, so a misorder would silently scramble model
  input. One-line fix: `ORDER BY datetime(created_at) ASC, id ASC`
  (id is monotonic with insertion; conversations already had the
  equivalent `, id DESC` tiebreak). Added 2 regression tests: forced
  identical timestamps list in insertion order; rapid back-to-back
  inserts (the real UI pattern) list user-then-assistant. Violation
  proof: this SQLite build happens to preserve tie order, so the old
  flaw isn't deterministically demonstrable — instead inverted the
  ORDER BY to DESC (violating the oldest-first invariant) and confirmed
  both new tests plus the pre-existing ordering test fail; restored the
  fix and confirmed `repositories.py` byte-identical to the fixed
  version.
- Atomic config saves (2026-09-18): `ConfigManager.save()` used
  truncate-then-write (`open(path, "w")` + `json.dump`), so a crash or
  disk-full mid-write destroyed the previous good config — reproduced:
  failed save left a 0-byte file. Every future OddFable app persists
  settings through this method, so the fix is shared reliability, not
  product polish. `save()` now writes to `config.json.tmp` in the same
  directory and `os.replace()`s it into place; a best-effort cleanup in
  `finally` guarantees no stale temp file, and `save()` still never
  raises (OSError is logged, as before). Added 2 regression tests:
  failed save preserves the previous file byte-for-byte with no temp
  litter; successful save leaves no temp file. Violation-proven by
  temporarily restoring the old implementation and watching the
  regression test fail, then restoring the fix.
- Config-reset precedence fix (2026-09-18): `reset_to_defaults()`
  rebuilt `_data` from `DEFAULT_CONFIG` alone, silently dropping
  environment overrides — the documented "env > file > defaults"
  precedence held on load but not on reset (reproduced: env host
  reverted to `localhost` after reset). One-line fix: reset now
  rebuilds via `apply_env_overrides(dict(DEFAULT_CONFIG))`, the same
  layer `load()` uses. Added
  `test_config_reset_to_defaults_preserves_env_precedence`;
  violation-proven by temporarily restoring the old line and watching
  the new test fail, then restoring the fix. The dialog has no reset
  button today, but any future settings-reset UI would have hit this.
- Config-notification consistency (2026-09-18): `ConfigManager` had two
  observer mechanisms — the Qt `config_changed` signal and plain-Python
  `add_listener` callbacks — but only `set()` notified both.
  `update()` and `reset_to_defaults()` emitted the Qt signal alone, so
  a listener-only observer would silently miss notifications. This
  mattered because the settings dialog, the app's primary
  config-mutation path, saves via `update()`. Fixed with a single
  `_notify_listeners(key, value)` helper used by all three mutators —
  one notification path, no duplication. Added 3 tests
  (set/update/reset listener notification); violation-proven by
  temporarily restoring the old `update()` behavior and watching the
  new test fail, then restoring the fix.
- Layer-boundary enforcement (2026-09-18): the spec claimed "no raw SQL
  remains in ui/" after the repository-boundary brick, but no test
  proved it — a reintroduced bypass would have stayed green (it
  happened once already with `MainWindow._update_message_content`).
  New `test_layer_boundaries.py` (3 tests) pins the contracts by
  scanning sources as text (Qt can't run headless): nothing in ui/,
  product/, or main.py may grab a raw DB connection
  (`get_connection`); nothing outside data/ may import sqlite3; product/
  may not reference the concrete `OllamaService` (seam convention
  §7.2). Checks are lexical and precise — an earlier SQL-keyword
  approach was rejected for false positives in comments and method
  names. The settings dialog's throwaway `OllamaService` (tests
  *unsaved* dialog values) is a documented exception, so the
  concrete-service check scopes to product/. Mutation-checked: an
  injected `get_connection` in ui/ fails the suite. No production
  code changed — the implementation was already correct.
- Config-propagation contract test (2026-09-18): audit found the
  implementation correct — no production change. But the spec's promise
  (§5) that runtime config changes "remain effective through the seam
  automatically" had only a static test (the seam wraps the managed
  service), not a dynamic one: nothing proved a `config.set()` at
  runtime actually reaches the service. Added
  `test_config_change_propagates_to_managed_service`, which builds a
  fully real `AppContext` (real `ConfigManager`, `Database`, and
  `OllamaService`, all redirected to tmp paths) and asserts host, model,
  and timeout changes land on the managed service the seam wraps.
  Deliberately unchanged: the wiring itself, already correct.
- Repository-boundary audit (2026-09-18): tracing how the UI consumes
  the data layer found `MainWindow._update_message_content` reaching
  past the repositories — grabbing a raw thread-local connection from
  `Database` and executing hand-written SQL in the UI layer — because
  `MessageRepository` had no update method. The UI knew the table
  schema, connection handling, and transaction semantics; any future
  product needing message updates would have copied the bypass instead
  of using the seam. Added `MessageRepository.update_content()`
  following the existing repository conventions, and the UI now calls
  `self.ctx.messages.update_content(...)`. No raw SQL remains in
  `ui/`. New test pins the behavior (content persists, other fields
  untouched). Deliberately unchanged: the repository's otherwise
  complete surface (cascade delete was already present and tested).
- ChatWorker protocol migration (2026-09-18): the background worker —
  the concurrency pattern every product copies — took the concrete
  `OllamaService` and Ollama `ChatMessage` types, and the main window
  fed it `ctx.ollama`, leaving the boilerplate's own UI violating
  locked convention §7.2 while its product example complied. Migrated
  the worker to `ChatProvider`/`ProviderMessage`; its logic was already
  provider-shaped (only `chat_stream` is ever called), so this is a
  type-level decoupling, not a redesign. The main window now builds
  `ProviderMessage` history and passes `ctx.chat_provider`. The
  `OllamaError` catch is kept deliberately: the bundled adapter
  propagates it unchanged, and other providers' errors degrade to the
  existing generic path. No other code referenced the old constructor.
  New `test_chat_worker.py` (4 tests) proves the decoupling headlessly:
  a `QCoreApplication` plus direct `run()` covers streaming, typed
  errors, unexpected errors, and cooperative cancellation (the latter
  with a real thread start and a blocking fake provider), all through
  a fake that is not an Ollama service.
- Application-identity audit (2026-09-18): the app name was hardcoded in
  five places (`main.py` application name, main-window title, tray
  tooltip, about-dialog heading, `paths.py` APP_NAME) and the version in
  two (`main.py`, about dialog), while the README's rename checklist
  only mentioned two of them — a renamed app would have shipped
  half-branded. Centralized on `APP_NAME` / `APP_VERSION` in `paths.py`
  (the existing identity owner); every surface now derives from them.
  Deliberately left separate: the Qt organization name in `main.py`
  (publisher metadata, nothing reads it) and the exe name in `app.spec`
  (build-time setting). Qt UI strings can't run headless here, so the
  invariant is pinned by `test_app_identity.py`, which scans the
  production sources as text and fails on any hardcoded copy. Also
  corrected stale README: the "orchestrate the Ollama service" product
  line (leftover from before the provider seam), the incomplete rename
  step, the phantom `config/ (reserved)` tree entry (no such package
  exists), the missing `ai/provider.py`, and the outdated tests listing.
- Product-example audit (2026-09-18): `example_service.py` — the
  reference every future product copies — still took the concrete
  `OllamaService` and Ollama `ChatMessage` types, violating locked
  convention §7.2, and its tests used `MagicMock` instead of the
  documented fake pattern. Migrated the example to depend on
  `ChatProvider`/`ProviderMessage` and rewrote its tests around a
  trivial in-memory fake (also asserting the fake satisfies the
  protocol). Nothing else referenced the example's old constructor,
  so the blast radius was zero.
- Paths audit (2026-09-18): `paths.py` was the only infrastructure module
  with zero tests, and it guards the highest-risk seam in a PyInstaller
  app (dev vs frozen resolution). The implementation was found coherent:
  `get_resource_dir()` matches the `app.spec` datas target
  (`src/app/resources`), frozen onefile/onedir branches are correct, and
  the `%LOCALAPPDATA%` fallback chain is sound. Deliberately unchanged —
  this brick is pure verification: 11 tests pin the contracts with
  platform/interpreter attributes monkeypatched and all user-data writes
  redirected to tmp_path.
- Error-boundary audit (2026-09-18): ai/ has a complete typed hierarchy
  (`OllamaError` + 4 subtypes, transport exceptions normalized at the
  boundary, error paths covered by 20 tests); the app boundary logs
  unhandled exceptions and shows a safe dialog (generic text, traceback
  in expandable details). Deliberately NOT added: a data-layer exception
  wrapper — `sqlite3.Error` is stdlib-stable, no caller outside `data/`
  imports sqlite3 today, and wrapping would require an invasive refactor
  of every repository method for a problem no product has yet. Genuine
  coverage gaps closed with 2 tests: corrupt DB file fails fast at
  `Database.__init__` with `sqlite3.DatabaseError`; `close_all()` is
  idempotent (which `AppContext.shutdown()` depends on). Convention §7.4
  corrected to describe what actually exists.
- Logging audit (2026-09-18): the implementation already met every
  criterion (centralized `app` logger, rotating file + console,
  `get_logger()` children, level policy, exception logging, global
  excepthook, duplicate-handler guard). The gap was testability: zero
  coverage. Added `test_logging.py` (global state isolated per test);
  writing it caught one real wart — `get_logger()` with no args returned
  `app.app` — fixed to return the `app` logger itself.
- Lifecycle audit (2026-09-18): startup was already clean (ordered
  construction in `AppContext`, logging first in `main()`), but shutdown
  was split-brain — only `MainWindow.closeEvent` called `ctx.shutdown()`,
  so the entry point never guaranteed cleanup, and `shutdown()` was not
  idempotent. Fixed with an idempotent `shutdown()` and a try/finally in
  `main()`; the closeEvent path is unchanged and the overlap is safe.
- Config audit (2026-09-18): the configuration boundary was checked against
  represent/load/defaults/separation/test-override/no-hardcoded-values.
  The only genuine gap was environment loading; the fix is a whitelisted
  `<PREFIX><KEY>` override layer (prefix derived from `APP_NAME`,
  values coerced to the default's type), not a new config system.
- The provider boundary works: fakes satisfy `ChatProvider`; the adapter
  translates messages, passes the system prompt through, and propagates
  typed `OllamaError`s unchanged — for both `chat()` and `chat_stream()`
  (all covered by `test_provider.py`).
- The existing Ollama, database, repository, config, product-example, and
  utility tests all still pass unmodified after the provider boundary was
  added — the foundation was extended, not redesigned.

---

## 9. What remains intentionally empty for future bricks

- `src/app/product/` beyond the example: the slot where real products go.
- Additional `ChatProvider` implementations (only the Ollama adapter
  exists; no other backends are claimed or implied).
- Multi-turn conversation handling, persistence-backed agents, memory
  systems, authentication, tool/plugin systems — none exist, none are
  implied. Each arrives as its own future brick or not at all.
- No `config/` package exists on disk; the README's old
  "(reserved for future config modules)" tree entry was removed
  2026-09-18 as stale documentation.

---

**Lock rule:** do not add features, redesign, or modify this foundation
without a new explicit task. Future bricks build on top via
`src/app/product/` and the `ChatProvider` seam.
