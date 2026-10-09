# BRICK 6 — Build Pack Query + Change Intelligence

Deterministic, read-only query layer over the canonical Build Pack and
the layers around it.

## What it is

`src/app/product/agentivan/query.py` exposes the state built by Bricks
1–5 without collapsing the layers together:

- the Build Pack (Brick 1) stays the source of truth for CURRENT state
- sources (Brick 2/3) stay the source of ORIGINAL MATERIAL
- extraction (Brick 4) stays the source of CANDIDATES
- reconciliation (Brick 5) stays the source of DECISIONS

Brick 6 queries across these layers but never merges them and never
rewrites any of them.

## Query API

`BuildPackQuery(pack, reconciliation=None, catalog=None)`, or
`BuildPackQuery.for_project(project)` to wire all three (loads only,
never writes). Results are structured dataclasses, never prose:

- `identity()` — the canonical project identity
- `sections()` / `section(name)` / `section_has_content(name)` —
  deterministic section access over the existing Brick 1 sections only;
  no schema redesign, no speculative sections
- `records(section=, status=, source_id=, text=)` — combined filters
- `by_status(status)` / `unknown()` — exact status match; statuses are
  never inferred, upgraded, or downgraded
- `by_source(source_id)` — everything in the pack derived from one
  source, as `ProvenanceHit`s carrying source id, extraction id,
  reconciliation id/decision, and Build Pack location
- `open_issues()` — the open_issues section
- `search(query_text=, section=, status=, field=, value=)` — exact
  field/value filtering, case-insensitive substring, section/status
  filters; no semantic search, no embeddings, no LLM

`RecordHit` / `ProvenanceHit` serialize with `to_dict`/`from_dict`.

## Provenance honesty

`provenance_kind(record)` reports `"extracted"`, `"manual"`, or
`"none"`. Records with manual or missing provenance are reported as
such; provenance is never fabricated for older/manual information, and
`by_source` simply does not match records it cannot link.

## Unresolved work ("what remains?")

`what_remains()` returns a `RemainingWork` with five strictly distinct
categories — UNKNOWN entries, open issues, deferred candidates,
unresolved contradictions, source-integrity warnings. UNKNOWN is not a
contradiction, DEFERRED is not rejected, and a changed source file is
not a project contradiction; they are never merged into a generic
"problem" bucket. Each item keeps its own identity and provenance.

`what_do_we_know()` returns a `KnowledgeReport`: every record grouped
by its exact information status (all seven statuses appear, even when
empty). No narrative, no inference, no correctness judgements.

## Contradictions

`unresolved_contradictions()` / `resolved_contradictions()` /
`contradictions(resolved=...)` list Brick 5 contradiction records.
`contradiction_detail(id)` identifies the affected Build Pack location,
both competing values (the live canonical record and the candidate
text/state/source linkage), and the resolution decision when resolved.
Nothing is resolved here — this brick is read/query intelligence only.

## Revisions and diffs

- `revisions()` — change history in chronological order, never rewritten
- `revision(n)` — one revision by number
- `revision_sources(rev)` — source/provenance where the revision
  recorded it (Brick 5 acceptance revisions name the source id in their
  description); `None` when the revision records no source
- `diff_packs(before, after)` — structural comparison between two
  Build Pack states: added / removed / modified / unchanged records,
  plus project-identity changes. `ChangeEntry` carries section,
  record id, field path (`fields.<name>`, `status`, `notes`,
  `provenance`), old value, new value, and provenance where available.
  The diff is structural, never prose, never an LLM. Compare the
  current pack against a saved earlier pack state (e.g. a copy of
  `buildpack/buildpack.json`); no Git is required or used. Change
  history and contradictions are queried separately — they are the
  history itself, not pack state.

`ChangeSet`, `KnowledgeReport`, and `RemainingWork` all round-trip
through `to_json`/`from_json`, suitable for future UI, conversational,
Markdown, PDF, or API rendering. Those presentation layers are not
built here.

## Source integrity

`source_integrity()` / `integrity_warnings()` expose Brick 3
intact/changed/missing information in deterministic catalog order.
Reported only: sources are never repaired, records never modified, and
a changed file is never reinterpreted as a Build Pack change.

## Read-only guarantee

Every query operation is read-only. Queries never modify the Build
Pack, sources, extraction results, or reconciliation decisions, and
never create revisions. Only explicit Brick 5 reconciliation operations
alter canonical state. (Proven by test: full layer snapshots before and
after every query operation are byte-identical.)

## Determinism

Equivalent Build Pack states produce equivalent query results.
Ordering is by `(section, record_id)` (and contradiction/candidate id)
throughout — never filesystem order, dict iteration, or timestamps.
Record, candidate, and contradiction ids are content-derived; volatile
timestamps are excluded from semantic comparisons.

## Cross-platform

`pathlib` only, no shell commands, no hard-coded OS paths, no
OS-specific behavior. Verified on Linux; Windows runtime verification
remains pending (no Windows machine available), same as Bricks 0–5.

## Tests

`tests/test_buildpack_query.py` — 37 focused tests reusing the Brick 5
synthetic "Nefarious Rig" scenario (extended with a research source:
RESEARCHED/CALCULATED/UNKNOWN records, an open issue, unicode content)
plus one changed and one missing source file. They prove identity,
section, status, provenance, open-issue, UNKNOWN, deferred,
contradiction (open + resolved), integrity, revision, and search
queries; added/removed/modified/unchanged diff detection with
structured paths; "what do we know?" / "what remains?" groupings;
serialization round-trips; read-only behavior across all four layers;
relocation; unicode; determinism; and no-network/no-LLM operation.

## Non-goals

No conversational AI, no LLM, no Ollama/PydanticAI, no RAG, no
embeddings, no semantic or vector search, no database, no network
client, no Git dependency, no UI, no PDF generation, no automatic
contradiction resolution, no status inference, no Bricks 1–5 redesign.
