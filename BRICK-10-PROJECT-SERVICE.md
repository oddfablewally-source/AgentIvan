# BRICK 10 — PROJECT SERVICE / OPERATIONS FACADE

## Purpose

`ProjectService` (`src/app/product/agentivan/service.py`) is the single
programmatic entry point through which higher-level AgentIvan features
operate on a project. It is an **orchestration facade**: it coordinates
the existing Brick 2–9 implementations without reimplementing any of
them, and without knowing their internal details.

It is NOT an AI agent, NOT conversational behavior, NOT a UI, NOT an
LLM layer, NOT a new data model, and NOT a replacement for the
existing lower-level APIs. Every one of those APIs remains available
and authoritative.

> **ProjectService is an orchestration facade, not a second domain
> model or persistence layer.**

> **The canonical Build Pack, source records, extraction state,
> reconciliation state, and project metadata remain owned by their
> existing layers.**

## Relationship to Project and Workspace

- The service is bound to **exactly one** `Project` for its lifetime.
  `create_project`, `open_project`, and `load_project` are the only
  operations that rebind it, and each is explicit.
- Because the bound project is explicit, an operation can never
  accidentally act on whichever project happens to be active
  elsewhere. Two services on two projects are fully isolated.
- The service keeps **no second active-project state**. The
  workspace's active project id is exposed read-only through
  `active_project_id`; it is never copied into service state.
- `create_project` / `ProjectService.create` create the project via
  `AgentIvanWorkspace.create_project` and make it the workspace's
  active project, so the service binding and workspace state stay
  coherent. `open_project` delegates to
  `AgentIvanWorkspace.open_project` (sets active). `load_project`
  delegates to `AgentIvanWorkspace.get_project` (active untouched).
- No `ProjectManager`, `ProjectRepository`, `ProjectDatabase`, or
  `ProjectRegistry` was needed: the existing Workspace/Project
  architecture was sufficient.

## Operations

### Lifecycle

| Operation | Kind | Delegates to |
|---|---|---|
| `ProjectService(workspace)` | bind | workspace active project |
| `ProjectService(workspace, project_id=...)` | bind | `workspace.get_project` |
| `ProjectService(workspace, project=...)` | bind | (object used as-is, never copied) |
| `ProjectService.create(workspace, name)` | MUTATING | `workspace.create_project` + set active |
| `create_project(name)` | MUTATING | `workspace.create_project` + set active |
| `open_project(project_id)` | MUTATING (active only) | `workspace.open_project` |
| `load_project(project_id)` | READ-ONLY | `workspace.get_project` |

Binding with no project available raises `ProjectServiceError`, the
only service-level error type. It exists solely for orchestration
misuse (nothing bound, wrong type bound).

### Source intake (Brick 2) — MUTATING

`add_text`, `add_file`, `add_reference` delegate to the matching
`Project.intake_*` implementation (byte-copying, path safety, hashing,
and `SourceCatalog` behavior stay exactly where they are) and then
persist through the project's existing `save()` path — the same path
`AgentIvanWorkspace.intake_*` uses. References are recorded locally
and never fetched. Nothing is extracted automatically.

### Source inspection (Brick 3) — READ-ONLY

`get_source`, `list_sources`, `search_sources`, `verify_source`,
`verify_all_sources`, plus a `source_catalog()` accessor. All route to
the project's `SourceCatalog`. No second catalog exists.

### Extraction (Brick 4) — MUTATING, explicit only

`extract()` runs `ExtractionStore.run()` with its default
`DeterministicExtractor` and persists via `ExtractionStore.save()`.
The operation deliberately takes **no extractor parameter**, so no LLM
extraction can be introduced through this path.
`get_extraction_set()` is the read-only counterpart. Extraction never
runs implicitly: intake, queries, and exports do not trigger it.

### Reconciliation / Build Pack assembly (Brick 5)

| Operation | Kind | Delegates to |
|---|---|---|
| `import_candidates()` | MUTATING | `ReconciliationSession.import_candidates` + `save()` |
| `pending_candidates()` | READ-ONLY | reconciliation store (`CANDIDATE` state) |
| `get_candidate(id)` | READ-ONLY | reconciliation store |
| `get_contradiction(id)` | READ-ONLY | reconciliation store |
| `accept_candidate(id, note=...)` | MUTATING | `ReconciliationSession.accept` + `save()` |
| `reject_candidate(id, reason=...)` | MUTATING | `ReconciliationSession.reject` + `save()` |
| `defer_candidate(id, reason=...)` | MUTATING | `ReconciliationSession.defer` + `save()` |
| `flag_contradiction(...)` | MUTATING | `ReconciliationSession.flag_contradiction` + `save()` |
| `resolve_contradiction(...)` | MUTATING | `ReconciliationSession.resolve_contradiction` + `save()` |

Each mutating decision builds a fresh `ReconciliationSession` (which
loads canonical state from disk), applies exactly one decision, and
saves. All Brick 5 semantics are preserved: nothing is accepted
silently, `PROJECT_IDENTITY` and unmappable categories cannot be
accepted, contradictions stay visible until explicitly resolved, and
`keep_existing` leaves the pack untouched.

### Build Pack query (Brick 6) — READ-ONLY

`get_build_pack`, `get_project_identity`, `query` (routes to
`BuildPackQuery.records`), `search` (routes to `BuildPackQuery.search`),
`what_do_we_know`, `what_remains`, `get_changes` / `diff_build_packs`
(both route to `diff_packs`), plus a `buildpack_query()` accessor
returning the fully-wired `BuildPackQuery`. No second query engine, no
semantic search, no embeddings, no natural-language interpretation.

### Exports — OUTPUT (Brick 7 / Brick 8)

`export_markdown(destination=None)` and `export_pdf(destination=None)`
delegate to `Project.export_markdown` / `Project.export_pdf`. Output
bytes are identical to calling those directly. Canonical state is
never mutated; Markdown and PDF remain independent of each other.

### Snapshots — OUTPUT / IMPORT (Brick 9)

`export_snapshot(destination=None)` delegates to
`Project.export_snapshot` (byte-identical deterministic ZIP).
`import_snapshot(archive, destination)` is a **static** method because
it does not operate on the bound project; it delegates to the Brick 9
importer, so all archive validation, manifest checks, and Brick 2 path
safety stay in that layer. Existing destinations are never
overwritten.

## Result discipline

Where the lower layers return strong result types (`SourceRecord`,
`ExtractionSet`, `BuildRecord`, `ReconciledCandidate`,
`ReconciliationContradiction`, `BuildPack`, `KnowledgeReport`,
`SnapshotResult`, …), the facade returns them **directly**. No
service-specific result wrapper was needed, and none was introduced.

## Error handling

Lower-layer domain errors (`ProjectError`, `WorkspaceError`,
`CatalogError`, `ExtractionError`, `ReconciliationError`,
`QueryError`, `MarkdownExportError`, `PDFExportError`,
`SnapshotError`, `IntakeError`, `UnsafePathError`, …) propagate
**unchanged** — never caught, never converted, never flattened into a
generic message. `ProjectServiceError` is raised only for errors
genuinely caused by service orchestration (no project bound / wrong
object bound).

## Persistence semantics

- Reads never write. The service introduces no autosave, no cache, and
  no hidden state file.
- Mutations persist through the existing controlled mechanisms only:
  `Project.save()` for intake, `ExtractionStore.save()` for
  extraction, `ReconciliationSession.save()` for decisions.
- After a full lifecycle (create → intake → extract → decide → export →
  snapshot), a project directory contains only the known entries:
  `project.json`, `sources/`, `extractions/`, `buildpack/`,
  `reconciliation/`, `exports/`.

## Project isolation

Every operation acts on the bound `Project` object. The service never
consults "whichever project is active" at call time, so
`service_a.add_text(...)` cannot affect project B even when B is the
workspace's active project (proven by
`test_project_scoped_isolation`).

## Cross-platform behavior

Path handling is `pathlib` only, via the existing path abstractions.
No hard-coded home directories, no shell, no subprocess, no
OS-specific branches. The service adds no platform-specific behavior
of its own.

**Windows runtime verification pending** (Linux-only dev machine; same
standing status as Bricks 0–9).

## Deterministic behavior

The service introduces no nondeterminism: no random IDs (creation uses
the existing UUID path), no timestamps added to reads, no reordering
of deterministic results, and byte-identical Markdown/PDF/snapshot
output versus the lower layers (proven by byte-equality tests).

No threads, no processes, no asyncio, no daemons, no schedulers, no
queues, no caches. Every operation is explicit and synchronous.

## Tests

`tests/test_service.py` — 60 focused tests reusing the Brick 5/6/7/9
"Nefarious Rig" fixture (relocated under a real workspace, which the
architecture supports because identity lives in `project.json`).
Coverage: imports, construction forms, lifecycle, identity, Build Pack
reads, intake delegation (with spies proving single delegation),
catalog reads, extraction delegation + determinism guard, explicit
candidate import (idempotent), all six reconciliation decisions (with
spies), query/search/knowledge/remaining/changes delegation,
Markdown/PDF/snapshot byte-equality with the lower layers, snapshot
import round-trip + invalid/unsafe/conflict rejection, read-only
battery (canonical hashes unchanged), mutation persistence through
reload, identity non-duplication, project isolation, preserved error
identity, deterministic repeated reads and outputs, relocation,
unicode, no-network (socket blocked), no-LLM (signature + extractor
name), no semantic search (typo returns nothing), no new persistence,
no background threads, no banned imports, and Brick 7/8/9 regression
pins.

## Explicit non-goals

- No AI/LLM/embeddings/RAG/semantic search/summarization.
- No UI, chat, project browser, or upload screen.
- No network activity; references are never fetched.
- No background processing, caching, or global mutable state.
- No new dependencies (stdlib + existing AgentIvan only).
- No second domain model, no second persistence layer, no
  `ProjectManager`/`ProjectRepository`/database/registry types.
- No automatic extraction, reconciliation, export, or snapshotting:
  higher layers request each operation explicitly.
- No redesign of Bricks 0–9; the facade only coordinates them.

## Verification (2026-09-20)

- Focused Brick 10 suite: **60/60 passed** (`tests/test_service.py`).
- Full gate `python3 scripts/verify.py`: **602/602 passed**
  (542 baseline + 60 new), Ivan Core baseline 9/9 OK.
- Locked boilerplate at `~/workspace/boilerplate/`: 0 checksum
  mismatches before and after (baseline
  `~/workspace/boilerplate_baseline.sha256`).
- Linux runtime verified. **Windows runtime verification pending.**
- No blockers. Brick 10 complete; STOP — Brick 11 not started.
