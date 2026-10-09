# BRICK-1: AgentIvan Canonical Build Pack

**Status:** Brick 1 scope. Data model and project specification only.

## 1. What this brick establishes

The canonical internal representation that later AgentIvan systems
(AI ingestion/compilation, PDF/Markdown generation) will read and
update. It defines what AgentIvan *manages*, not what it *does*.

Core principle: conversation and source material are raw project
material. The **canonical Build Pack** is the structured representation
of the current project state and the source of truth for the compiled
project. AgentIvan preserves uncertainty instead of inventing certainty.

## 2. Where it lives

`src/app/product/agentivan/` inside `~/workspace/agent-ivan/`:

- `statuses.py` — `InfoStatus` (7 states), `ResolutionStatus`
- `model.py` — `ProjectIdentity`, `BuildRecord`, `Provenance`,
  `Revision`, `Contradiction`, `BuildPack`, section schemas,
  validation (`BuildPackError`), JSON serialization
- `__init__.py` — public API

Stdlib only (`dataclasses`, `enum`, `json`, `datetime`). No database,
no network, no AI, no new dependencies.

## 3. Project model

`BuildPack` holds:

- **identity** — name, project_id, description, intent, status,
  created, modified, revision (all required, validated)
- **sections** — 13 content sections, each a list of records:
  `donor`, `design`, `measurements`, `parts`, `research`,
  `engineering`, `cad`, `build`, `finishing`, `testing`, `cost`,
  `safety`, `open_issues`
- **change_history** — list of `Revision`
- **contradictions** — list of `Contradiction`

Each section declares required vs optional fields in
`SECTION_SCHEMAS`. Required fields are the minimum identifying field
(e.g. `measurements` requires `measurement`); everything else is
optional, so incomplete projects are valid.

## 4. Information statuses

Seven controlled states, part of the data model (`InfoStatus`):

| Status      | Meaning                                              |
|-------------|------------------------------------------------------|
| KNOWN       | Directly established fact                            |
| RESEARCHED  | Spec/finding obtained from documentation or research |
| PROPOSED    | Design idea; not decided, measured, or verified       |
| MEASURED    | Field measurement from the physical project          |
| CALCULATED  | Mathematical result derived from recorded inputs     |
| VERIFIED    | Result confirmed through testing or inspection       |
| UNKNOWN     | Missing value; legitimate state, never an error      |

Rules:

- Every record carries exactly one status. It is mandatory and is
  never defaulted: a record without a status is malformed.
- Unknown status strings are rejected at construction and at
  deserialization (`BuildPackError`), never coerced.
- The model never promotes one status into another. A PROPOSED idea
  stays PROPOSED until something explicitly changes it through a
  recorded revision.

## 5. Provenance

`Provenance` records `source_type` (free-form label, so future input
types never require a model redesign), `source_reference`,
`source_text`, `date`, `author`, and `notes`. Everything except
`source_type` is optional.

Provenance is never invented: a record with no known origin carries
`provenance: null`, and serialization preserves that explicitly.

## 6. Revisions

`Revision`: revision number (positive, strictly increasing),
timestamp, change description, affected area, previous state, new
state, optional author. States are JSON-native snapshots or null.

History is append-only in spirit: future AI compilation updates the
current project by adding revisions, not by silently overwriting.

## 7. Contradictions

`Contradiction`: id, ≥2 distinct record ids (must reference real
records), affected field, explanation, resolution status
(`UNRESOLVED`/`RESOLVED`), resolution note (required when resolved).

Conflicting records are both retained with their statuses untouched.
The model never auto-resolves.

## 8. Format

Canonical form is JSON:

- human-readable (2-space indent), deterministic (sorted keys;
  sections and records ordered canonically)
- self-describing header: `format: "agentivan-build-pack"`,
  `format_version: 1`
- statuses, provenance, revisions, and contradictions all survive
  round-trips exactly
- suitable for later Markdown/PDF generation and AI processing

No database: Brick 1 defines the representation; persistence choices
belong to later bricks.

## 9. Validation

`BuildPack.validate()` raises `BuildPackError` on the first problem:

- required identity fields present, non-empty, correctly typed;
  ISO-8601 dates; non-negative revision
- sections known; records well-formed; record ids unique pack-wide
- required section fields present; no unknown fields; JSON-native
  field values
- statuses are valid `InfoStatus` members
- revision numbers positive and strictly increasing
- contradictions reference existing records; resolved ones carry a
  resolution note

`from_dict`/`from_json` always validate. Malformed packs are rejected,
never repaired silently.

## 10. Test fixture

`tests/fixtures/canonical_build_pack.json`: one small synthetic
project ("Sample Utility Trailer Build", explicitly not real) with one
record per status (all seven), provenance, one revision, one open
issue, and one unresolved contradiction (two deck-width measurements,
60.5 in vs 60.0 in, both retained).

## 11. Tests

`tests/test_build_pack.py` — 27 focused tests covering creation,
deterministic serialization, deserialization round-trips, status /
provenance / revision survival, contradiction representation without
auto-resolution, UNKNOWN validity, invalid-status rejection,
missing-status rejection (no silent promotion), malformed identity
rejection, and relationship rules (unique ids, known references,
strictly increasing revisions, resolution notes).

## 12. Explicit non-goals (later bricks)

AI ingestion/compilation, automatic project extraction, chat UI, PDF
rendering, RAG/vector stores, cloud services, autonomous engineering,
database persistence of packs. None of that is in this brick.
