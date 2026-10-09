# BRICK-8 — DETERMINISTIC BUILD PACK PDF EXPORT

## Purpose

Brick 8 adds a deterministic, read-only PDF export layer for the canonical
Build Pack. The PDF is a presentation artifact for human inspection and
distribution — a paginated rendering of the same substantive project
information the Brick 7 Markdown export carries.

**PDF is an output artifact. The canonical Build Pack remains the source of truth.**

Brick 7 (Markdown) remains independently useful and untouched. Markdown and
PDF are sibling output layers over the same canonical BuildPack; neither
parses the other, and neither becomes canonical truth.

## API

`src/app/product/agentivan/pdf.py` exposes a small public surface:

- `BuildPackPDFExporter` — stateless renderer.
  - `export(pack, reconciliation=None) -> PDFDocument`
    Pure in-memory operation. Raises `PDFExportError` (a `ValueError`) on
    invalid input. Never mutates its inputs.
- `PDFDocument` — immutable result wrapper.
  - `data: bytes` — the complete PDF file bytes.
  - `page_count: int` — deterministic page count.
  - Deterministic serialization: `to_dict` / `from_dict` / `to_json` /
    `from_json`, with a SHA-256 integrity check on deserialization.
- `export_project_pdf(project, destination=None) -> Path` — project-level
  convenience: loads the canonical pack and reconciliation through the
  existing stores, renders, and writes **only** the PDF bytes.
- `Project.export_pdf(destination=None) -> Path` — same, as a method.
- Constants: `PDF_FORMAT_NAME`, `PDF_FORMAT_VERSION`,
  `DEFAULT_PDF_FILENAME = "build-pack.pdf"`.

`src/app/product/agentivan/__init__.py` re-exports the PDF names alongside
the existing Markdown names; no existing export was disturbed.

## PDF generation flow

```
Canonical BuildPack (+ optional Reconciliation)
        |
        v
BuildPackPDFExporter.export      (reads model objects directly)
        |
        v
ReportLab Platypus story         (Paragraphs, spacers; headings keepWithNext)
        |
        v
_DeterministicCanvas (invariant=True) -> PDF bytes
```

The exporter walks canonical model objects directly (identity, sections in
`SECTION_SCHEMAS` order, records sorted by `record_id`, contradictions,
change history). It does not consume the Brick 7 Markdown output and does
not parse any intermediate representation.

## Canonical section ordering

Sections render in the exact `SECTION_SCHEMAS` (Brick 1) key order — never
alphabetized, never reordered. Sections that are absent or empty are
**omitted** (same documented behavior as Brick 7).

## Status handling

Every record's status renders verbatim as `KNOWN`, `RESEARCHED`, `PROPOSED`,
`MEASURED`, `CALCULATED`, `VERIFIED`, or `UNKNOWN`. Statuses are labels, never
translated into prose, never inferred, never hidden.

## Provenance handling

Existing provenance renders where present: source type, source reference,
date, author, source text, extraction id, reconciliation linkage (id plus
decision state, resolved through the canonical reconciliation object), and
the Brick 6 `provenance_kind` (`extracted` / `manual` / `none`). Absent
provenance is omitted, never fabricated.

## Contradiction handling

Canonical and reconciliation contradictions both render under a
"Contradictions" section with an explicit unresolved notice. Each shows its
identity, kind, state, affected section/field, the existing side (live
canonical record fields where the record still exists), the candidate side
(recorded provenance), and any recorded resolution — without resolving
anything or choosing a side.

## Revision handling

The existing change history renders with revision number, affected area,
description, Before/After snapshots where the canonical data carries them,
source information (via the Brick 6 revision-source lookup), author, and
recorded timestamp. Exporting never creates a revision: repeated exports of
the same Build Pack leave history byte-identical.

## Unicode / font strategy

ReportLab's built-in fonts are WinAnsi-only and cannot represent the full
fixture content (e.g. `☃` U+2603). The exporter therefore registers two
**project-local bundled fonts** — `fonts/DejaVuSans.ttf` and
`fonts/DejaVuSans-Bold.ttf`, copied verbatim from the DejaVu project
(permissive Bitstream Vera / Arev license) into
`src/app/product/agentivan/fonts/`. No host system font is ever consulted,
no font is downloaded at runtime, and no font-management machinery was
added. SHA-256 of the bundled files is recorded in the Brick 8 verification
notes; the bytes are embedded deterministically by ReportLab.

## Pagination behavior

Platypus handles page breaks automatically: text wraps, no content clips or
overlaps, and section/record headings use `keepWithNext` so a heading stays
with its content where practical. Every page carries a deterministic footer
(`Page N`). No elaborate publishing system was built — this is the simplest
reliable ReportLab layout.

## Metadata behavior

All PDF metadata is set explicitly; nothing time- or host-dependent leaks:

| Key          | Value                                              |
|--------------|----------------------------------------------------|
| Title        | canonical project name (identity, never a path)    |
| Author       | `""`                                               |
| Subject      | `AgentIvan Build Pack`                             |
| Keywords     | `""`                                               |
| Creator      | `AgentIvan`                                        |
| Producer     | ReportLab default (constant string)                |
| CreationDate | `D:20000101000000+00'00'` (fixed, see below)        |
| ModDate      | `D:20000101000000+00'00'` (fixed, see below)        |

The document title falls back to `"Untitled project"` only if the canonical
identity name were empty; `ProjectIdentity` requires a non-empty name, so
this is unreachable defensive code, documented here rather than hidden.

## Deterministic-output behavior

The canvas runs with ReportLab's `invariant=True`, which fixes the
document ID and both PDF dates to 2000-01-01 UTC (this is ReportLab's own
reproducible-build mode, also honoring `SOURCE_DATE_EPOCH` semantics).
Combined with explicit metadata, sorted rendering order, and bundled fonts,
**identical BuildPack input produces byte-for-byte identical PDF bytes** —
verified by tests that compare actual bytes, including across separate OS
processes.

## File-output behavior

`export_project_pdf(project, destination=None)` writes only the PDF:

- `None` → `<project>/exports/build-pack.pdf` (the existing `exports/`
  directory from Brick 2; no new hierarchy).
- Relative destination → resolved under `exports/` (same convention as
  Brick 7).
- Absolute destination → used as given.
- Parent directories are created; only the PDF is written.

Path handling is `pathlib`-only, no hardcoded OS paths.

## Read-only guarantee

Export never mutates the BuildPack, project metadata, source records,
extraction records, reconciliation records, or revision history. Tests prove
this by snapshotting canonical `to_dict()` state and every project file's
bytes before and after export and requiring equality.

## Dependency added

- `reportlab>=4.0,<5.0` added to `requirements.txt` (the project's single
  source of truth for dependencies; `scripts/verify.py` installs from it).
  This is the one PDF dependency the brief allows. No other dependency was
  added — no WeasyPrint, no Chromium, no Jinja, no Markdown engine, no
  database, no network client, no LLM package.

## Test coverage

`tests/test_pdf_export.py` — **53 focused tests**. The fixture reuses the
Brick 5/6/7 "Nefarious Rig" scenario plus the Brick 7 manual
PROPOSED/VERIFIED records (multiline text, Unicode, Markdown-special
characters, structured values, absent provenance), which yields an 8-page PDF.

Coverage: exporter imports; basic export; PDF result type; `%PDF-` header and
`%%EOF`; identity in text; canonical section order; populated sections;
empty-section omission (documented); all seven statuses verbatim plus a
no-translation guard; provenance block; source_reference preservation;
extraction_id preservation; reconciliation linkage (accepted candidate id +
state); absent provenance not fabricated; open issues; UNKNOWN visibility;
contradictions visible with both sides; revision history; After rendering
where available plus a synthetic Before-path test; structured lists/dicts
(JSON-style scalars, no Python repr); multiline text; Unicode (`café ☃`);
special characters (`|`, backticks, `*`, `#`-leading lines); multi-page
generation with page-count agreement between the result and the PDF's own
`/Count`; byte-identical repeated exports (same process and across
processes); read-only proof (canonical dicts + all project file bytes);
file export default/relative/absolute destinations; read-back validation;
no current timestamp injected; deterministic metadata assertions;
relocation byte-identity; Linux/Windows-safe destination handling; no
network (socket blocked); no LLM (source scan); Brick 7 behavior unchanged;
source/extraction/reconciliation data unchanged; PDFDocument serialization
round-trip and tamper rejection; invalid-input rejection; deterministic
page numbers; title from identity; query layer still read-only after PDF
export.

PDF text inspection uses a minimal **stdlib-only** extractor written for the
tests (`_extract_pdf_text`): it decompresses ASCII85+Flate content streams,
decodes literal and hex text-showing operators, and translates subsetted
TrueType glyph codes through each font's ToUnicode CMap (`beginbfchar` /
`beginbfrange`). It validates the generated artifact itself, never product
internals, and no new test dependency was added.

## Explicit non-goals

No interactive forms, annotations, JavaScript, embedded external resources,
remote fonts, external images, QR codes, network hyperlinks, generated
charts, or illustrations. No AI summarization, no semantic search, no
version-control integration, no cloud sync, no accounts. Text-only PDF
rendering is sufficient for V1.

## Windows verification status

Windows runtime verification pending. The implementation uses only
`pathlib`, the standard library, and reportlab (pure Python + bundled
fonts); no OS-specific behavior was introduced, but no Windows execution
was performed on this Linux-only machine.
