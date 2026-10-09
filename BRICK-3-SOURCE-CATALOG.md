# BRICK 3 — SOURCE CATALOG + RETRIEVAL LAYER

Status: COMPLETE (2026-09-20)
Baseline before: 230 tests passing, Ivan Core 9/9, boilerplate untouched.

## Purpose

Brick 2 established raw material → project workspace → source record.
Brick 3 establishes source records → catalog → search / filter /
retrieve → raw source content.

The catalog gives later AgentIvan processing a clean, predictable way
to inspect project material without knowing filesystem details. It
deals only with *what the user provided* — never with what AgentIvan
may later conclude about it.

## Relationship to Brick 2

- Operates through the existing project/workspace layer; no project
  storage logic is duplicated.
- Registration delegates to the Brick 2 intake functions
  (`intake_text` / `intake_file` / `intake_reference`).
- Integrity checking reuses the Brick 2 identity hash
  (`identity_hash_for_bytes`), so the newline-insensitive text hashing
  carries over: a project moved between Linux and Windows does not
  report false changes.
- Stored paths remain project-relative POSIX strings; path resolution
  uses the Brick 2 platform-neutral path handling.

## Source catalog behavior (`catalog.py`)

`SourceCatalog` is a **stateless view** over one `Project`. It persists
nothing of its own; every operation reads the live `project.sources`
list. `project.json` plus the preserved files under `sources/` remain
the single source of truth.

Exposed via `project.catalog`:

- `intake_text / intake_file / intake_reference` — register through
  project intake. Mutates the in-memory project; the caller persists
  with `project.save()` (same rule as Brick 2 project-level intake).
- `get(source_id)` — record lookup; `CatalogError` when unknown.
- `list(kind=...)` — all records in deterministic catalog order.
- `count()`, `exists(source_id)`.
- `remove(source_id, delete_file=False)` — explicit removal only.
  Default removes the catalog record and keeps the preserved file.
  `delete_file=True` also deletes the preserved file. The two are never
  performed silently together, and nothing is ever removed
  automatically (not for duplicates, changed hashes, failed reads, or
  rebuilds).
- `rebuild()` — returns a fresh catalog bound to a freshly loaded
  project. Proves the catalog is fully reconstructable from disk.

## Retrieval behavior

`read(source_id)` returns the original material:

- text → the original `str`, decoded verbatim (no normalization)
- file / image → the original `bytes`, byte-for-byte
- reference → the stored reference string, exactly as recorded

References are never fetched; no network requests are made.
`read_bytes(source_id)` returns raw stored bytes; `stored_path(source_id)`
resolves the project-relative path to an absolute `Path` after
validating it cannot escape the project directory (defense against
corrupted `project.json`).

## Integrity checking

`verify(source_id)` / `verify_all()` return `IntegrityStatus` with one
of three explicit states:

- `intact` — file present, identity hash matches the record
- `changed` — file present, hash no longer matches
- `missing` — record exists, preserved file is gone

Reference sources carry no file, so they always report `intact`.
Checks never repair, overwrite, or delete. Integrity states are source
information, not Build Pack contradictions (Brick 1).

## Search / filter behavior

`search(query, kind=, label=, original_filename=, media_type=, status=,
source_id=)`:

- keyword filters are exact matches;
- `query` is case-insensitive substring matching across documented
  metadata fields: label, original filename, description, media type,
  reference, source_id;
- plain string matching only — no semantic search, no embeddings,
  no LLM, no vector database.

`duplicates()` groups source_ids by recorded identity hash.
Identification only; nothing is deduplicated or deleted.

## Deterministic ordering

`list()` and `search()` sort by `(kind, label.casefold(), source_id)`
(`catalog_order_key`). The unique `source_id` tie-break makes the order
total. Ordering never depends on OS directory enumeration, filesystem
implementation order, or hash-map iteration: the same project lists
identically on Linux and Windows.

## Platform considerations

- Linux + Windows remains a hard V1 requirement.
- All path handling goes through the Brick 2 platform-neutral
  abstraction; no hard-coded OS paths, no shell commands, no
  `os.sep`-dependent logic.
- Tests exercise Windows-style inputs through the platform-neutral
  validation logic on Linux. **Windows runtime verification is pending:
  no Windows execution was performed or claimed.**

## Explicit limitations

- The catalog assigns no Build Pack statuses and never modifies the
  Build Pack (`buildpack/` is untouched by every catalog operation).
- No inference, classification, summarization, or rewriting of source
  material.
- No caching layer; no opaque database. There is nothing to go stale.
- Removal with `delete_file=False` (the default) can leave orphaned
  files under `sources/`; that is the documented safe-minimal behavior.

## What this brick deliberately does NOT do

No LLM calls, no PydanticAI, no Ollama, no semantic classification,
no semantic search, no embeddings, no RAG, no vector databases, no
automatic Build Pack extraction, no contradiction reasoning, no AI
summaries, no UI, no PDF/export generation.

## Tests

`tests/test_source_catalog.py` — 34 focused tests reusing the Brick 2
synthetic fixture project where practical. Covers: registration,
lookup, deterministic listing (incl. reload and relocation),
kind/label/filename/media-type/status/id filtering, metadata search,
verbatim text/file/image/reference retrieval, intact/changed/missing
detection, relocation, duplicate identification, explicit removal
(record-only and record+file), rebuild, catalog persistence of
nothing, original content unchanged, Build Pack untouched.

Full suite: `python3 scripts/verify.py` — 264 passed, 0 failed
(230 baseline + 34 new). Ivan Core baseline 9/9 OK.
Locked master `~/workspace/boilerplate/` unchanged (SHA-256
pre/post snapshot, 0 differences).
