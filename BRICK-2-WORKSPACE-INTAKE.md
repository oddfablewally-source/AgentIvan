# BRICK 2 — Project Workspace + Source Intake

Status: implemented 2026-09-20. No AI, no UI, no PDF generation.

## What this brick builds

The reliable intake and workspace layer the AI compilation layer will
use in later bricks:

```
USER
  |
AGENTIVAN PROJECT WORKSPACE          workspace.py
  |
RAW SOURCE MATERIAL                  intake.py copies/stores it verbatim
  |
NORMALIZED SOURCE RECORD             sources.py SourceRecord
  |
READY FOR AI PROCESSING              (later bricks)
```

Raw source material is preserved byte-for-byte. Nothing is summarized,
rewritten, or destroyed during intake.

## Cross-platform requirement (hard)

Linux and Windows are both supported by construction:

- All filesystem work uses `pathlib`; no hard-coded OS paths, no
  `/home/...`, no `C:\...`, no shell commands, no bash/PowerShell/cmd
  in application logic.
- The workspace root is caller-supplied; the default is
  `Path.home() / ".agentivan" / "workspace"` (valid on both platforms).
- Supplied filenames/paths are validated against BOTH POSIX and Windows
  path semantics on every host (`paths.validate_supplied_path`), so a
  Windows-style attack string is rejected on Linux and vice versa.
  Rejected: `..` traversal, POSIX/Windows absolute paths, drive letters
  (`C:...`), drive-rooted paths (`\dir\file`), UNC/network confusion
  (`\\server\share`), NUL bytes, empty names.
- Stored relative paths are always recorded POSIX-style (`sources/`),
  which `pathlib` parses correctly on every platform.
- Newline handling: text identity hashes are newline-insensitive
  (CRLF/CR/LF normalize to LF for the hash only), so moving a project
  between Linux and Windows does not produce false "changed" signals.
  The stored bytes are never normalized.

Windows runtime verification is PENDING: this machine runs Linux only.
Portable tests exercise Windows-style inputs through the
platform-neutral validation logic; no Windows execution is claimed.

## Project structure

```
<workspace-root>/
    workspace.json            workspace state (active project id)
    projects/
        <project_id>/
            project.json      canonical project state (identity, sources)
            sources/          original raw material, preserved verbatim
            buildpack/        structured representation (later bricks)
            exports/          generated artifacts, e.g. PDF (later bricks)
```

- `project_id` is a UUID generated at creation and stored in
  `project.json`. Identity never depends on the directory name or path:
  the project is relocatable without changing its identity.
- Serialization is deterministic JSON (sorted keys, UTF-8, LF).
  Saving without changes produces byte-identical output.
- No database. Stdlib only.

## Source intake

Three kinds, no AI classification at intake (intake records what was
supplied; KNOWN/PROPOSED/... statuses belong to later bricks):

- `intake_text` — text supplied directly; stored verbatim as UTF-8.
  Media type guessed from the filename (`text/markdown`,
  `application/json`, default `text/plain`).
- `intake_file` — an existing file copied byte-for-byte. Images are
  detected by media type (`SourceKind.IMAGE`); everything else is
  `SourceKind.FILE`. Unreadable/missing sources fail explicitly and
  register nothing.
- `intake_reference` — a URL/citation stored as a record. Never
  fetched; no file is created; the record is the preserved original.

Every write is read back and byte-compared before intake is reported
successful. Intake never claims success unless the source was actually
preserved.

## Source record metadata

`source_id`, kind, label, status (`stored`), intake timestamp (UTC
ISO-8601), original filename (validated, normalized), relative project
path (POSIX), media type, SHA-256 identity hash, byte size, description,
and `extra` (free-form dict carried verbatim so future bricks can add
fields without a redesign).

Hashing: text (or text-decodable) content hashes newline-insensitively
for identity; binary content hashes byte-for-byte. The same content
encountered again is recognizable by hash without relying on the
filename. Duplicates are never auto-deleted.

## Active project

`AgentIvanWorkspace` tracks one active project, persisted in
`workspace.json`. `open_project` loads and activates;
`get_project` loads without activating. Workspace-level
`intake_text/intake_file/intake_reference` operate against the active
project (and persist it); with no active project they raise
`WorkspaceError` instead of guessing.

## Errors

`WorkspaceError`, `ProjectError`, `IntakeError`, `UnsafePathError`,
`SourceRecordError` (all `ValueError` subclasses, mirroring the
`BuildPackError` convention). Explicit failures for: unknown project,
invalid identifier, unsafe path, unreadable source, corrupted
project/workspace data, write failure, no active project. Source
material is never silently discarded.

## Tests

`tests/test_workspace_intake.py` (61 tests) plus the synthetic fixture
`tests/fixtures/agentivan_synthetic_project/` (~3.4 KB): project
identity, text/Markdown/JSON sources, a PNG with a unicode filename,
one URL reference, unicode content, mixed newlines, full metadata and
hashes. No real OddFable data.

Covers: creation, save/load, relocation, identity persistence,
preservation byte-exactness, hash stability and newline-insensitivity,
duplicate recognition, traversal/drive/UNC/absolute rejection,
Windows- and Linux-style input handling, unicode filenames and text,
mixed newlines, active-project behavior, deterministic serialization,
corrupted-data rejection.

## Public API

From `src.app.product.agentivan`: `AgentIvanWorkspace`,
`default_workspace_root`, `Project`, `SourceRecord`, `SourceKind`,
`SourceStatus`, `intake_text`, `intake_file`, `intake_reference`,
`validate_supplied_path`, `sanitize_filename`, `join_posix`,
`normalize_newlines`, `identity_hash_for_text`,
`identity_hash_for_bytes`, `sha256_hex`, `utc_now_iso`, and the error
types.

## Explicitly NOT in this brick

LLM calls, Ollama, PydanticAI, classification, extraction, RAG,
embeddings, vector DBs, reasoning, Build Pack compilation,
contradiction detection, summaries, UI, PDF generation.
