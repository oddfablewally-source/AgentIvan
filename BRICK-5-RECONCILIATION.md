# Brick 5 — Candidate Reconciliation + Build Pack Assembly

The controlled layer between Brick 4's extracted candidate information
and Brick 1's canonical Build Pack.

Core principle: **extraction is not truth**. Nothing reaches the
canonical Build Pack without an explicit, recorded reconciliation
decision.

## Pipeline

```
RAW SOURCE (Brick 2)
  -> SOURCE RECORD (Brick 2)
  -> EXTRACTION RESULT (Brick 4: "the source says X")
  -> CANDIDATE (Brick 5: extracted item registered for review)
  -> RECONCILIATION DECISION (Brick 5: explicit review outcome)
  -> BUILD PACK ENTRY (Brick 1 model: "X is true for this project")
  -> REVISION (Brick 1 change history)
```

A future AgentIvan response can answer "where did this Build Pack
information come from?" by walking that chain: the record's provenance
carries the source reference, the original source text, the extraction
id, the method, the category, and the reconciliation id.

## Candidate lifecycle

Explicit states; information is never deleted, only re-stated:

- `candidate` — extracted, awaiting review
- `accepted` — reviewed and applied to the Build Pack
- `rejected` — reviewed and deliberately not accepted (reason kept)
- `deferred` — reviewed but left unresolved (reason kept)
- `duplicate` — reviewed; identical information already in the pack
- `contradicted` — conflicts with existing pack content; recorded, not
  silently replaced

`deferred` is non-terminal: a later review may still accept, reject, or
flag the candidate as a contradiction. Each decision is recorded with
its own timestamp and note. Terminal states (`accepted`, `rejected`,
`duplicate`) and contradictions under resolution cannot be re-decided;
contradictions leave that state only through explicit resolution.

## Explicit acceptance

`ReconciliationSession.accept(candidate_id, note=...)`:

1. Requires an explicit call. Deterministic extraction confidence is
   recorded for inspection only and is never consulted by any decision.
2. Maps the candidate's Brick 4 category to its deterministic Build
   Pack section (see mapping below).
3. Checks for an exact duplicate first: same section, same category,
   same primary field value (case-insensitive). A duplicate marks the
   candidate `duplicate` and creates no new record.
4. Creates a `BuildRecord` with the candidate's status preserved
   verbatim, provenance preserved, and a deterministic content-derived
   record id.
5. Appends a Brick 1 `Revision` (what changed, why, what source caused
   it, when). One revision per accepted change; history is never
   rewritten or collapsed.
6. Validates the whole pack before finishing.

## Status preservation

The seven Brick 1 statuses are authoritative. Acceptance never upgrades
status: PROPOSED stays PROPOSED, MEASURED stays MEASURED, RESEARCHED
stays RESEARCHED. A candidate the source gave no status becomes
`UNKNOWN` — the honest conservative representation for the mandatory
record status — never an invented KNOWN.

## Duplicate vs contradiction

- **Duplicate**: same information, different source. Detected
  deterministically and conservatively (exact primary-field match).
- **Contradiction**: different information concerning the same project
  fact. This judgment is reviewer-driven: the deterministic layer never
  infers that two different values describe the same fact. The caller
  names the conflict with `flag_contradiction`, which records the
  contradiction and leaves the existing record untouched.

Differing text that is neither an exact duplicate nor an explicitly
flagged conflict simply becomes a new candidate.

## Contradictions

`ReconciliationContradiction` is distinct from Brick 1's
`Contradiction` (a conflict between two canonical records): one side
here is a not-yet-accepted candidate. It preserves the existing
record's provenance and the candidate's full provenance, plus a
description of the conflict. State starts `UNRESOLVED` and stays that
way until explicitly resolved. There is no "auto resolve".

Resolution (`resolve_contradiction`) has exactly two outcomes:

- `keep_existing` — the Build Pack is untouched; the contradiction is
  marked resolved with the resolution and note.
- `accept_incoming` — the existing record is updated in place from the
  candidate (status and provenance preserved from the candidate) and a
  `Revision` records the old values in `previous_state`, so no history
  is lost. The old record's content survives in the revision, the
  contradiction record, the extraction results, and the raw source.

## Category -> section mapping

Deterministic; uses only fields allowed by the Brick 1 section schemas.
The Build Pack schema is not redesigned.

| Category | Section | Primary field |
|---|---|---|
| donor | donor | notes |
| design | design | concept_notes |
| measurement | measurements | measurement (+ value/unit) |
| part | parts | name |
| material | finishing | material |
| research | research | subject |
| engineering | engineering | calculation |
| cad_fabrication | cad | component |
| build_assembly | build | task |
| finishing | finishing | material |
| testing_verification | testing | item |
| cost | cost | item |
| safety_legal | safety | issue |
| open_issue | open_issues | question |

No safe mapping: `project_identity` (special-cased, see below),
`change_history`, `general`. Accepting these raises
`ReconciliationError`; the honest path is defer or reject explicitly.
No new Build Pack field is ever invented for an interesting extraction.

## Project identity

Project identity can never be changed automatically. Accepting a
project-identity candidate raises `ReconciliationError`; the identity
block (created once from the project's own identity) is never mutated
by reconciliation. A conflicting project name from a source is
candidate information requiring human resolution, optionally recorded
as a contradiction against the identity.

## Measurements

The original expression is always preserved verbatim in the
`measurement` field. The extracted numeric value and unit ride along as
separate fields — never a replacement, never recalculated, never
silently converted. Two conflicting measurements become a
contradiction, never an overwrite.

## Provenance chain

Every accepted record's provenance carries:

- `source_type = "source_extraction"`
- `source_reference` — source id (plus filename when known)
- `source_text` — the candidate's original expression
- `notes` — extraction id, method, category, reconciliation id

Raw sources stay verbatim, extraction results stay cached, and
reconciliation history stays persisted. None of them is the source of
truth for current project state — the Build Pack is — but all of them
remain recoverable.

## Storage

- `<project-root>/buildpack/buildpack.json` — the canonical Build Pack.
- `<project-root>/reconciliation/reconciliation.json` — candidates,
  decisions, and contradictions.

Both are deterministic, human-inspectable JSON (canonical key order,
records/candidates ordered by id), written with pathlib only. The
project stays relocatable: move the directory and everything loads and
keeps working. The reconciliation store never touches sources,
extraction results, project.json, or anything outside its own
directory; the Build Pack store validates before writing.

## Deterministic behavior

Record ids, candidate ids, and contradiction ids are content-derived
(SHA-256, no randomness, no timestamps). Given identical source
material, identical extraction results, identical existing Build Pack
state, and identical reconciliation decisions, the resulting Build Pack
is equivalent: `packs_equivalent` and `Reconciliation.equivalent`
compare material content while ignoring decision/run timestamps.

## Cross-platform considerations

Core reconciliation and Build Pack assembly use pathlib and the
standard library only. No hard-coded paths, no shell commands, no
OS-specific behavior. Behavior is identical on Linux and Windows by
construction. Linux runtime verified via the test suite; Windows
runtime verification remains pending (no Windows machine available) and
is never claimed.

## What Brick 5 deliberately does NOT do

- No automatic application of extraction results.
- No status upgrades on acceptance.
- No inferred contradictions, no automatic contradiction resolution.
- No new Build Pack fields or schema redesign.
- No project identity mutation.
- No engineering calculations or measurement conversions.
- No LLM, Ollama, PydanticAI, cloud APIs, embeddings, vector
  databases, RAG, or semantic search.
- No network client, no database, no new dependencies (stdlib only).
- No final UI; the programmatic `ReconciliationSession` API is the
  interface.
