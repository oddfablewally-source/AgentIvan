# BRICK 7 — Deterministic Build Pack Markdown Export

## Purpose

Brick 7 adds a deterministic **output layer** that renders the current
canonical Build Pack into a human-readable Markdown document.

Markdown is an **output artifact**, not the canonical Build Pack. The
canonical Build Pack (Brick 1 model, persisted by the Brick 5 stores) remains
the structured source of truth. The exporter consumes it read-only and
produces text only.

The document is suitable for:

- human inspection of a project's compiled state,
- versioned project documentation,
- later PDF generation (a future brick),
- later UI display,
- later export/API layers.

## API

`src/app/product/agentivan/markdown.py`, standard library only.

```python
from src.app.product.agentivan import (
    BuildPackMarkdownExporter,
    MarkdownDocument,
    MarkdownExportError,
    export_project_markdown,
)

exporter = BuildPackMarkdownExporter()

# Pure rendering: canonical BuildPack -> Markdown text, no side effects.
document: MarkdownDocument = exporter.export(pack, reconciliation=reconciliation)
text: str = document.text
exporter.export_text(pack, reconciliation=reconciliation)  # alias, same result

# Project convenience: renders and writes exports/build-pack.md (or an
# explicitly requested destination). Writes ONLY the Markdown file.
path = project.export_markdown()                      # exports/build-pack.md
path = project.export_markdown("sub/dir/custom.md")  # under exports/
```

`reconciliation` is optional. When provided, reconciliation-level
contradictions render with both sides; when omitted, only canonical data
renders (nothing is invented to fill the gap).

`MarkdownDocument` is an immutable wrapper whose only purpose is
deterministic serialization (`to_dict` / `from_dict`, `to_json` /
`from_json`). There is no persistent export state and no cache: the brick
is deliberately `BuildPack -> Markdown`, not `BuildPack -> ExportRecord ->
Database -> Markdown`.

Errors raise `MarkdownExportError` (a `ValueError`).

## Canonical section ordering

Sections render in the exact canonical order established by Brick 1's
`SECTION_SCHEMAS`: donor, design, measurements, parts, research, engineering,
cad, build, finishing, testing, cost, safety, open_issues.

- Sections that are absent or empty are **omitted** (consistently and
  deterministically). The test suite pins both the order and the omission.
- Records within a section render sorted by `record_id`.
- Record fields render with keys sorted by their string form. Where a
  dictionary requires ordering for deterministic output, **sorted-by-key** is
  the single rule, applied everywhere (fields, nested values, provenance
  snapshots).

## Status handling

Every rendered record shows its status with the exact existing vocabulary:

`KNOWN`, `RESEARCHED`, `PROPOSED`, `MEASURED`, `CALCULATED`, `VERIFIED`,
`UNKNOWN`.

Statuses are never reinterpreted, upgraded, downgraded, or translated into
prose ("probably", "confirmed", ...). The test suite asserts all seven appear
verbatim and that no paraphrases leak in.

## Provenance handling

Provenance renders only where the canonical record already carries it:

- `source_type`, `source_reference`, `date`, `author`, `source_text`,
- `extraction_id` (parsed from the established Brick 5 `key=value` notes
  convention),
- reconciliation linkage (`reconciliation_id` plus the live decision state
  looked up from the provided reconciliation),
- `provenance_kind` via the Brick 6 `BuildPackQuery.provenance_kind`
  classifier: `extracted` / `manual` / `none`.

Missing provenance is reported by omission. No fake source labels are ever
created. The exporter does not merge the source, extraction, reconciliation,
and canonical layers; it renders each where the canonical data records it.

## Contradiction handling

The `## Contradictions` section renders what is already recorded:

- Brick 1 canonical contradictions (between two canonical records).
- Brick 5 reconciliation contradictions (candidate vs. existing), each with an
  **Existing side** (the live canonical record, or its recorded provenance
  snapshot when the record is no longer present) and a **Candidate side**
  (the stored candidate provenance). Both sides are shown; nothing is
  decided.

The exporter resolves nothing, picks no winner, and never hides
contradictory information to make the document look cleaner. Unresolved
information (UNKNOWN records, open issues, unresolved contradictions) stays
visible. A resolved contradiction renders its recorded resolution verbatim.

## Revision / change history

`## Change history` renders the existing revision list in order: revision
number, affected area, description, `Before` / `After` snapshots where the
revision carries them (null states are omitted, never fabricated), the source
reference parsed honestly from the revision data via the Brick 6
`revision_sources` rule, the author, and the recorded timestamp.

Exporting never creates revisions: the same Build Pack exported twice is
unchanged (proven by byte-identical canonical snapshots before and after).

## Deterministic-output rule

Identical BuildPack input produces byte-for-byte identical Markdown:

- No current time, random IDs, filesystem paths, hostnames, environment
  variables, or temporary filenames are emitted -- unless they already exist
  as canonical Build Pack data (e.g. identity `created`, revision
  `timestamp`), in which case they are part of the input, not added by the
  exporter. A test asserts every timestamp-like string in the output already
  exists in the canonical JSON.
- Newlines are always `\n`. File output writes with explicit `newline="\n"`,
  so identical canonical data produces byte-identical files on Linux and
  Windows.
- The document title collapses newlines in the project name to spaces so a
  hostile name cannot break the heading structure.

## Markdown safety

Values are rendered as Markdown bullet text, never as tables, so pipes,
brackets, backticks, asterisks, and underscores in project data cannot corrupt
the document structure. Multiline values keep their line breaks with
continuation lines indented four spaces past the bullet, which guarantees a
value line beginning with `#` can never become a heading (it parses as an
indented code block instead). No structural line ever places arbitrary value
text at column zero. Escaping is intentionally minimal: deterministic
structure, not character-level escaping.

Structured values render deterministically and readably: strings verbatim,
numbers/booleans in JSON style (`true`/`false`/`null`, never Python repr),
lists as nested bullets, dictionaries with sorted keys. No information is
dropped for presentation.

## Read-only guarantee

Export is read-only. Proven by tests:

- Canonical JSON snapshots of the Build Pack and reconciliation are
  byte-identical before and after export.
- `project.json`, source records, stored source files, extraction storage,
  and reconciliation storage are byte-identical before and after export and
  file export.
- No revisions are created, no statuses change, no timestamps advance.
- `export_project_markdown` writes **only** the requested Markdown file; it
  never mutates canonical state.

## File-output behavior

- `project.export_markdown()` writes `exports/build-pack.md` (the Brick 2
  `exports/` directory; no new hierarchy).
- A relative destination resolves under `exports/`; an absolute destination
  is used as given. Parent directories are created. All paths go through
  `pathlib` only -- no hardcoded OS paths, no shell, no subprocess.
- Written bytes are exactly `document.text` encoded as UTF-8 with `\n`
  newlines; reading the file back yields the identical string.

## Test coverage

`tests/test_markdown_export.py`: **48 tests**, all passing. Covers the
brief's required list and more:

1. exporter imports and package `__all__` exposure
2. basic export returns a `MarkdownDocument` with `str` text
3. project identity appears (title + identity fields, no filesystem paths)
4. canonical section order matches `SECTION_SCHEMAS`
5. populated sections render
6. empty sections omitted
7-13. all seven statuses preserved verbatim; no prose paraphrases
14. provenance renders
15. source_id preservation
16. extraction_id preservation where present
17. reconciliation linkage where present
18. open issues render as data, not action plans
19. UNKNOWN information remains visible
20. contradiction information remains visible (resolved and unresolved)
21. both contradiction sides represented; unresolved has no Resolution line
22. revision history renders
23. before/after render where available (and are omitted when null)
24. structured lists/dicts render (JSON-style scalars, sorted keys)
25. multiline content renders
26. Markdown-special characters cannot corrupt document structure
27. Unicode survives unchanged
28. identical input produces byte-identical Markdown (including after reload)
29. export does not mutate BuildPack/project state
30. file export writes the expected Markdown bytes
31. exported file reads back exactly
32. no timestamps/random values appear unless canonical data contains them
33. Linux/Windows-safe path handling (no `\r`, under `exports/`)
34. relocation does not change rendered Markdown
35. no network access (socket blocked)
36. no LLM invocation (no new suspicious modules, no suspicious imports)
37. no changes to extraction/reconciliation/source data
+ `MarkdownDocument` serialization round trips and rejection of bad input,
  `export()` type validation, contradiction omission without reconciliation.

Fixture: the Brick 5/6 synthetic "Nefarious Rig" project (measurements,
parts, open issue, UNKNOWN entries, one open + one resolved contradiction,
revision history, Unicode, one changed + one missing source file), extended
with manual PROPOSED research and VERIFIED design records carrying multiline
text, Markdown-special characters, structured list/dict values, and Unicode,
plus one record with deliberately absent provenance.

## Explicit non-goals

Not built, per the brief: PDF export, HTML/DOCX export, UI, chat commands,
conversational AI, LLM integration, semantic search, embeddings, RAG,
automatic summarization, AI-generated prose, autonomous recommendations, Git
integration, cloud sync, accounts, multi-user support. The exporter is a
renderer, not a project manager: open issues are rendered as recorded data,
never converted into recommendations.

## Cross-platform

Linux runtime verification is available and was performed. Windows runtime
verification pending -- no Windows runtime exists on this machine. The module
uses `pathlib` and the standard library only, writes explicit `\n` newlines,
and hardcodes no OS paths; Windows verification must still be performed on a
real Windows machine before any Windows claim is made.
