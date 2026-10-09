# BRICK-4 — SOURCE INGESTION + STRUCTURED EXTRACTION

## Purpose

Brick 4 is the first intelligence layer of AgentIvan: it inspects
supplied source material and produces **structured candidate
information** for later Build Pack processing.

The pipeline is:

```
RAW SOURCE  ->  INGESTION  ->  STRUCTURED CANDIDATE INFORMATION
                                      ->  LATER BUILD PACK PROCESSING
```

Brick 4 is an **extraction layer**, not a truth layer. It identifies
what a source *appears to contain* and preserves *where that
information came from*. Nothing becomes canonical Build Pack
information merely because an extractor identified it.

## Core principle

AgentIvan must never confuse:

- "the source says X" (extraction), with
- "X is true" (Build Pack).

Extracted information stays explicitly tied to its source. The
canonical Build Pack (`model.py`) is never modified by extraction.

## Extraction pipeline

1. **Chunk** — `chunk_text()` splits text at natural boundaries
   (Markdown headings, paragraphs); `chunk_json()` splits a parsed
   JSON document into one chunk per scalar leaf with its JSON path.
2. **Extract** — `DeterministicExtractor` runs conservative
   rule-based passes over each chunk and emits `ExtractedItem`s.
3. **Store** — `ExtractionStore.run()` extracts every source in
   deterministic catalog order; `save()` writes an `ExtractionSet`
   to `<project-root>/extractions/extractions.json`.

Extraction is **read-only**: it reads preserved material through the
Brick 3 catalog and never rewrites source files, normalizes source
text, alters `SourceRecord`s, changes source hashes, or touches the
Build Pack.

## Candidate categories

A controlled routing-hint set (not Build Pack truth):

`project_identity`, `donor`, `design`, `measurement`, `part`,
`material`, `research`, `engineering`, `cad_fabrication`,
`build_assembly`, `finishing`, `testing_verification`, `cost`,
`safety_legal`, `open_issue`, `change_history`, `general`.

## Status vs confidence

These are separate concepts, enforced by the model:

- **Confidence** (0.0–1.0): "How confident are we that the source
  contains this information?"
- **Status** (Brick 1 `InfoStatus`, or `None`): "What kind of
  information is this?"

A confident extraction never upgrades a status. When the source does
not establish a status, the item carries `status=None` rather than an
invented one.

Examples:

| Source says | Category | Status |
|---|---|---|
| `Seat height measured at 28 inches` | measurement | MEASURED |
| `I think the seat should be 28 inches` | measurement | PROPOSED |
| `Seat height is 28 inches` | measurement | *none* (not VERIFIED) |
| `Weight 12.5 kg [MEASURED]` | measurement | MEASURED |
| `[PROPOSED]` on a line | general | PROPOSED |
| `TODO: confirm donor year` | open_issue | *none* |

`VERIFIED` is never inferred from confident-sounding phrasing — only
from explicit `[VERIFIED]` markers or `VERIFIED:` declarations.

## Provenance

Every item carries a `SourceLocation`: `source_id`, filename,
heading/section, chunk id, character offsets into the original text
(or the JSON path when offsets are unreliable, as for JSON leaves),
and the original text/context. Provenance is never fabricated:
unavailable fields stay `None`. A later system can always answer
"Where did AgentIvan get this?" without guessing.

## Deterministic extraction

The deterministic extractor identifies only obvious structure:

- explicit status markers: `[KNOWN]`, `[PROPOSED]`, `[MEASURED]`,
  `[VERIFIED]`, `[CALCULATED]`, `[RESEARCHED]`, `[UNKNOWN]`
  (uppercase, bracketed)
- explicit declarations: `MEASURED: ...`, `TODO: ...`, ...
- measurements: number immediately followed by a unit
  (`mm cm m in inch inches ft feet lb lbs kg`, degree forms, `°`);
  the original expression is preserved verbatim and a normalized
  `{"value", "unit"}` form is kept separately; units are never
  converted
- URLs (identified, never fetched)
- labeled declarations: `Project:`, `Part:`, `Material:`
- Markdown headings and sections
- JSON object keys, scalar values, and nested paths

It is deliberately conservative: thousands separators (`1,000 mm`),
ranges (`28-30 inches`), fractions, and numbers glued to words are
skipped rather than misread. Ambiguous material becomes `general`
candidates. Short ambiguous units (`m`, `in`, `ft`, `lb`) are
extracted at reduced confidence (0.8).

## Chunking

`chunk_text()`: Markdown heading lines become their own chunks and
attribute following chunks; blank-line-separated paragraphs become
chunks; character offsets always refer to the original text (no
newline normalization — `\r\n`/`\r` are preserved in offset
accounting). Chunk ids are `<source_id>:chunk:<index>`.
`chunk_json()`: one chunk per scalar leaf, keys in sorted order,
chunk ids `<source_id>:json:<index>`. No embeddings, no semantic
chunking, no RAG.

## Supported source types

- **TEXT**: inspected as text or JSON (media type / `.json` name /
  JSON-shaped content).
- **FILE**: inspected when safely decodable as UTF-8 text
  (`text/*`, `application/json`, known text extensions); otherwise
  reported as not available.
- **MARKDOWN / JSON**: headings, paragraphs, fields as above.
- **IMAGE**: not inspected — no OCR in this brick; reported as
  `no_extraction_available`.
- **REFERENCE**: recorded as a `research` candidate; never fetched.
- **Arbitrary binary**: preserved; reported as not available rather
  than pretended to be understood.

## Extraction errors

One source's failure never destroys the project or blocks other
sources. Results distinguish `success`, `no_extraction_available`,
and `error` (with the failure message recorded, never swallowed).

## Repeatability

Re-running deterministic extraction over unchanged sources produces
equivalent results. Item ids are content-derived SHA-256
(`make_extraction_id`); timestamps (`created_at`, `extracted_at`)
are metadata excluded from equivalence comparison — use
`ExtractionSet.equivalent()`, not raw dict equality.

## Storage

`extractions/extractions.json`: deterministic JSON (sorted keys),
results ordered by `source_id`, items ordered by `extraction_id`.
Relocatable (sources referenced by id, never by absolute path) and
inspectable. It is a cache, not a second source of truth: deleting it
loses nothing — `store.run()` regenerates everything from the
original sources.

## Extension point

`Extractor` is the common interface. `DeterministicExtractor` is
implemented; `LLMExtractor` exists as a stub that raises
`ExtractionNotAvailableError` — no model backend is wired, nothing
is installed, no network calls are made. `get_extractor(name)`
resolves `"deterministic"` / `"llm"`.

## Explicitly excluded

No LLM calls, no Ollama, no API keys, no cloud services, no
embeddings, no RAG/vector database, no OCR, no web client, no
database, no semantic classification, no automatic Build Pack
compilation, no Build Pack modification, no final UI, no PDF/export.

## Verification (2026-09-20)

- Focused Brick 4 tests: **41/41 passed**
  (`tests/test_source_extraction.py`).
- Full suite: **305/305 passed** via `python3 scripts/verify.py`
  (264 baseline + 41 new), Ivan Core baseline **9/9 OK**.
- Locked master `~/workspace/boilerplate/`: SHA-256 snapshot before
  and after — **0 differences across 119 files**.
- Linux runtime verified. Windows runtime verification **pending**
  (no Windows machine available); behavior is platform-neutral
  (pathlib only, no shell, no hard-coded paths) and Windows-style
  inputs are exercised through the Brick 2 path logic.

## Files

- `src/app/product/agentivan/extraction.py` — model: categories,
  methods, outcomes, review states, `SourceLocation`, `TextChunk`,
  `ExtractedItem`, `ExtractionResult`, `ExtractionSet`,
  `ExtractionStore`, chunking, deterministic ids.
- `src/app/product/agentivan/extractors.py` — `Extractor`
  interface, `DeterministicExtractor`, `LLMExtractor` stub,
  `get_extractor` registry.
- `src/app/product/agentivan/project.py` — added the
  `Project.extractions` property (Brick 3 pattern).
- `src/app/product/agentivan/__init__.py` — public exports.
- `tests/test_source_extraction.py` — 41 focused tests.
