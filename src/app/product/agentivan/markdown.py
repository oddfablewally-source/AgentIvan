"""Brick 7 -- deterministic Markdown export of the canonical Build Pack.

This module is an *output layer only*. It renders the current canonical
:class:`~app.product.agentivan.model.BuildPack` into a human-readable Markdown
document suitable for human inspection, versioned project documentation, and
as a pre-stage for later PDF generation, UI display, or export/API layers.

Design rules (all enforced here, none optional):

- Markdown is an output artifact. The canonical Build Pack remains the
  structured source of truth; this module never modifies it.
- Export is read-only: no revisions are created, no statuses change, no
  contradictions are resolved, no provenance is fabricated.
- The canonical section order from ``SECTION_SCHEMAS`` (Brick 1) is the
  rendering order. Sections that are absent or empty are omitted.
- Statuses are rendered with their exact existing vocabulary
  (``KNOWN`` / ``RESEARCHED`` / ``PROPOSED`` / ``MEASURED`` / ``CALCULATED`` /
  ``VERIFIED`` / ``UNKNOWN``); they are never reinterpreted into prose.
- Provenance is rendered only where the canonical data already contains it.
- Contradictions, open issues, and UNKNOWN values stay visible.
- Output is deterministic: identical BuildPack input produces byte-for-byte
  identical Markdown. No current time, random IDs, filesystem paths,
  hostnames, or environment values are emitted unless they already exist as
  canonical Build Pack data.
- Dictionaries are rendered with keys sorted by their string form so output
  is deterministic regardless of insertion order.
- Values are rendered as Markdown bullet text, never as tables, so pipes,
  brackets, backticks, asterisks, underscores, and multiline content cannot
  corrupt the document structure. Newlines in single-line contexts (document
  title, section headings) are collapsed to spaces.
- Standard library only. No Markdown/template libraries, no PDF libraries,
  no network, no LLM, no database.

The public API is deliberately small::

    exporter = BuildPackMarkdownExporter()
    document = exporter.export(pack, reconciliation=reconciliation)
    text = document.text

``export_project_markdown(project, destination)`` wires the exporter to an
existing :class:`~app.product.agentivan.project.Project`, loads the canonical
pack and reconciliation, and writes the Markdown to the explicitly requested
destination (defaulting to ``exports/build-pack.md`` under the project root).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .model import (
    SECTION_SCHEMAS,
    BuildPack,
    BuildRecord,
    Contradiction,
    ProjectIdentity,
    Provenance,
    Revision,
)
from .query import BuildPackQuery
from .reconciliation import Reconciliation, ReconciliationContradiction
from .statuses import ResolutionStatus

__all__ = [
    "BuildPackMarkdownExporter",
    "MarkdownDocument",
    "MarkdownExportError",
    "export_project_markdown",
]

MARKDOWN_FORMAT_NAME = "agentivan-markdown"
MARKDOWN_FORMAT_VERSION = 1
DEFAULT_EXPORT_FILENAME = "build-pack.md"


class MarkdownExportError(ValueError):
    """Raised when a Markdown export cannot be performed."""


@dataclass(frozen=True)
class MarkdownDocument:
    """An immutable rendered Markdown document.

    The wrapper exists only to give the export result deterministic
    serialization; there is no persistent export state.
    """

    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": MARKDOWN_FORMAT_NAME,
            "format_version": MARKDOWN_FORMAT_VERSION,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MarkdownDocument":
        if not isinstance(data, Mapping):
            raise MarkdownExportError("MarkdownDocument.from_dict requires a mapping")
        if data.get("format") != MARKDOWN_FORMAT_NAME:
            raise MarkdownExportError(
                f"Unsupported markdown document format: {data.get('format')!r}"
            )
        if data.get("format_version") != MARKDOWN_FORMAT_VERSION:
            raise MarkdownExportError(
                "Unsupported markdown document format version: "
                f"{data.get('format_version')!r}"
            )
        text = data.get("text")
        if not isinstance(text, str):
            raise MarkdownExportError("Markdown document text must be a string")
        return cls(text=text)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "MarkdownDocument":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MarkdownExportError(f"Invalid markdown document JSON: {exc}") from exc
        return cls.from_dict(data)


def _single_line(value: str) -> str:
    """Collapse newlines for contexts that must stay on one line."""
    return " ".join(str(value).replace("\r", "\n").split("\n")).strip()


def _render_scalar(value: Any) -> str:
    """Render a JSON scalar deterministically (never Python repr)."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


class BuildPackMarkdownExporter:
    """Deterministic, read-only renderer: canonical BuildPack -> Markdown.

    The exporter holds no state between calls; every ``export`` call is
    independent and produces byte-identical output for identical input.
    """

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def export(
        self,
        pack: BuildPack,
        reconciliation: Reconciliation | None = None,
    ) -> MarkdownDocument:
        """Render ``pack`` (and optionally ``reconciliation``) to Markdown."""
        if not isinstance(pack, BuildPack):
            raise MarkdownExportError(
                f"export() requires a BuildPack, got {type(pack).__name__}"
            )
        if reconciliation is not None and not isinstance(reconciliation, Reconciliation):
            raise MarkdownExportError(
                "export() reconciliation must be a Reconciliation or None, "
                f"got {type(reconciliation).__name__}"
            )
        lines: list[str] = []
        query = BuildPackQuery(pack, reconciliation=reconciliation)
        self._render_title(lines, pack.identity)
        self._render_identity(lines, pack.identity)
        for section in SECTION_SCHEMAS:
            records = pack.sections.get(section, [])
            if records:
                self._render_section(lines, query, section, records, reconciliation)
        self._render_contradictions(lines, query, pack, reconciliation)
        self._render_revisions(lines, query, pack)
        return MarkdownDocument(text="\n".join(lines) + "\n")

    def export_text(
        self,
        pack: BuildPack,
        reconciliation: Reconciliation | None = None,
    ) -> MarkdownDocument:
        """Alias for :meth:`export`; both return a MarkdownDocument."""
        return self.export(pack, reconciliation)

    # ------------------------------------------------------------------
    # document structure
    # ------------------------------------------------------------------
    def _render_title(self, lines: list[str], identity: ProjectIdentity) -> None:
        lines.append(f"# {_single_line(identity.name) or 'Untitled project'}")
        lines.append("")

    def _render_identity(self, lines: list[str], identity: ProjectIdentity) -> None:
        lines.append("## Identity")
        lines.append("")
        for label, value in (
            ("Name", identity.name),
            ("Project ID", identity.project_id),
            ("Description", identity.description),
            ("Intent", identity.intent),
            ("Status", identity.status),
            ("Created", identity.created),
            ("Modified", identity.modified),
        ):
            if value:
                self._bullet(lines, 0, label, _render_scalar(value))
        self._bullet(lines, 0, "Revision", _render_scalar(identity.revision))
        lines.append("")

    def _render_section(
        self,
        lines: list[str],
        query: BuildPackQuery,
        section: str,
        records: Sequence[BuildRecord],
        reconciliation: Reconciliation | None,
    ) -> None:
        lines.append(f"## {_single_line(_section_title(section))}")
        lines.append("")
        for record in sorted(records, key=lambda r: r.record_id):
            lines.append(f"### {record.record_id}")
            lines.append("")
            self._bullet(lines, 0, "Status", record.status.value)
            for key in sorted(record.fields, key=str):
                self._render_value(lines, 0, key, record.fields[key])
            if record.notes:
                self._render_value(lines, 0, "Notes", record.notes)
            self._render_provenance(lines, query, record, reconciliation)
            lines.append("")

    def _render_provenance(
        self,
        lines: list[str],
        query: BuildPackQuery,
        record: BuildRecord,
        reconciliation: Reconciliation | None,
    ) -> None:
        prov: Provenance | None = record.provenance
        if prov is None:
            return
        lines.append("- **Provenance:**")
        for label, value in (
            ("Source type", prov.source_type),
            ("Source reference", prov.source_reference),
            ("Date", prov.date),
            ("Author", prov.author),
        ):
            if value:
                self._render_value(lines, 1, label, value)
        if prov.source_text:
            self._render_value(lines, 1, "Source text", prov.source_text)
        notes_kv = _notes_kv(prov.notes)
        extraction_id = notes_kv.get("extraction_id")
        if extraction_id:
            self._render_value(lines, 1, "Extraction", extraction_id)
        reconciliation_id = notes_kv.get("reconciliation_id")
        reconciliation_state: str | None = None
        if reconciliation is not None and extraction_id:
            linked = reconciliation.find_by_extraction_id(extraction_id)
            if linked is not None:
                reconciliation_state = linked.state.value
                reconciliation_id = linked.reconciliation_id
        if reconciliation_id:
            detail = reconciliation_id
            if reconciliation_state:
                detail += f" ({reconciliation_state})"
            self._render_value(lines, 1, "Reconciliation", detail)
        self._render_value(lines, 1, "Provenance kind", query.provenance_kind(record))
        if prov.notes:
            self._render_value(lines, 1, "Notes", prov.notes)

    def _render_contradictions(
        self,
        lines: list[str],
        query: BuildPackQuery,
        pack: BuildPack,
        reconciliation: Reconciliation | None,
    ) -> None:
        canonical = sorted(pack.contradictions, key=lambda c: c.contradiction_id)
        reconciled = (
            sorted(reconciliation.contradictions, key=lambda c: c.contradiction_id)
            if reconciliation is not None
            else []
        )
        if not canonical and not reconciled:
            return
        lines.append("## Contradictions")
        lines.append("")
        lines.append(
            "The following information is unresolved. Nothing here has been "
            "decided; both sides are shown where the canonical data records them."
        )
        lines.append("")
        for contradiction in canonical:
            self._render_canonical_contradiction(lines, contradiction)
        for contradiction in reconciled:
            self._render_reconciliation_contradiction(lines, query, pack, contradiction)

    def _render_canonical_contradiction(
        self, lines: list[str], contradiction: Contradiction
    ) -> None:
        lines.append(f"### {contradiction.contradiction_id}")
        lines.append("")
        self._bullet(lines, 0, "Kind", "canonical")
        self._bullet(lines, 0, "Resolution status", contradiction.resolution_status.value)
        if contradiction.section:
            self._bullet(lines, 0, "Section", contradiction.section)
        if contradiction.field:
            self._bullet(lines, 0, "Field", contradiction.field)
        if contradiction.record_ids:
            self._render_value(lines, 0, "Records", list(contradiction.record_ids))
        if contradiction.explanation:
            self._render_value(lines, 0, "Explanation", contradiction.explanation)
        if contradiction.resolution_note:
            self._render_value(lines, 0, "Resolution note", contradiction.resolution_note)
        lines.append("")

    def _render_reconciliation_contradiction(
        self,
        lines: list[str],
        query: BuildPackQuery,
        pack: BuildPack,
        contradiction: ReconciliationContradiction,
    ) -> None:
        lines.append(f"### {contradiction.contradiction_id}")
        lines.append("")
        self._bullet(lines, 0, "Kind", "reconciliation")
        self._bullet(lines, 0, "State", contradiction.state.value)
        if contradiction.section:
            self._bullet(lines, 0, "Section", contradiction.section)
        if contradiction.affected_field:
            self._bullet(lines, 0, "Affected field", contradiction.affected_field)
        if contradiction.description:
            self._render_value(lines, 0, "Description", contradiction.description)
        if contradiction.existing_record_id:
            self._bullet(lines, 0, "Existing record", contradiction.existing_record_id)
        existing = None
        for record in pack.sections.get(contradiction.section or "", []):
            if record.record_id == contradiction.existing_record_id:
                existing = record
                break
        lines.append("- **Existing side:**")
        if existing is not None:
            self._bullet(lines, 1, "Status", existing.status.value)
            for key in sorted(existing.fields, key=str):
                self._render_value(lines, 1, key, existing.fields[key])
        elif contradiction.existing_provenance:
            self._render_value(
                lines, 1, "Recorded provenance", contradiction.existing_provenance
            )
        else:
            self._bullet(lines, 1, "Detail", "not available in current Build Pack")
        lines.append("- **Candidate side:**")
        self._render_value(
            lines, 1, "Candidate provenance", contradiction.candidate_provenance
        )
        if contradiction.resolution:
            self._bullet(lines, 0, "Resolution", contradiction.resolution)
        if contradiction.resolution_note:
            self._render_value(lines, 0, "Resolution note", contradiction.resolution_note)
        if contradiction.resolved_at:
            self._bullet(lines, 0, "Resolved at", contradiction.resolved_at)
        lines.append("")

    def _render_revisions(
        self, lines: list[str], query: BuildPackQuery, pack: BuildPack
    ) -> None:
        revisions = list(pack.change_history)
        if not revisions:
            return
        lines.append("## Change history")
        lines.append("")
        for index, revision in enumerate(revisions, start=1):
            lines.append(f"### Revision {index}")
            lines.append("")
            self._bullet(lines, 0, "Revision number", str(revision.revision))
            if revision.affected_area:
                self._bullet(lines, 0, "Affects", revision.affected_area)
            if revision.description:
                self._render_value(lines, 0, "Description", revision.description)
            if revision.previous_state is not None:
                self._render_value(lines, 0, "Before", revision.previous_state)
            if revision.new_state is not None:
                self._render_value(lines, 0, "After", revision.new_state)
            sources = query.revision_sources(revision)
            if sources is not None:
                if sources.get("source_reference"):
                    self._render_value(
                        lines, 0, "Source", str(sources["source_reference"])
                    )
                if sources.get("source_type"):
                    self._render_value(
                        lines, 0, "Source type", str(sources["source_type"])
                    )
            if revision.author:
                self._bullet(lines, 0, "Recorded by", revision.author)
            if revision.timestamp:
                self._bullet(lines, 0, "Recorded at", revision.timestamp)
            lines.append("")

    # ------------------------------------------------------------------
    # value rendering
    # ------------------------------------------------------------------
    def _bullet(self, lines: list[str], depth: int, label: str, text: str) -> None:
        # Continuation lines are indented four spaces past the bullet so a
        # value line that begins with "#" can never become a heading (four
        # spaces make it an indented code block instead). Bullets themselves
        # never start a value at column zero, and no tables are used, so
        # pipes, backticks, brackets, asterisks, and underscores in values
        # cannot corrupt the document structure.
        indent = "  " * depth
        first, *rest = str(text).split("\n")
        lines.append(f"{indent}- **{label}:** {first}")
        continuation = indent + "    "
        for line in rest:
            lines.append(f"{continuation}{line}")

    def _render_value(
        self, lines: list[str], depth: int, label: str, value: Any
    ) -> None:
        indent = "  " * depth
        if isinstance(value, Mapping):
            lines.append(f"{indent}- **{label}:**")
            for key in sorted(value, key=str):
                self._render_value(lines, depth + 1, str(key), value[key])
            return
        if isinstance(value, (list, tuple)):
            lines.append(f"{indent}- **{label}:**")
            for item in value:
                self._render_sequence_item(lines, depth + 1, item)
            return
        self._bullet(lines, depth, label, _render_scalar(value))

    def _render_sequence_item(
        self, lines: list[str], depth: int, item: Any
    ) -> None:
        indent = "  " * depth
        if isinstance(item, Mapping):
            lines.append(f"{indent}-")
            for key in sorted(item, key=str):
                self._render_value(lines, depth + 1, str(key), item[key])
            return
        if isinstance(item, (list, tuple)):
            lines.append(f"{indent}-")
            for sub in item:
                self._render_sequence_item(lines, depth + 1, sub)
            return
        first, *rest = _render_scalar(item).split("\n")
        lines.append(f"{indent}- {first}")
        continuation = indent + "    "
        for line in rest:
            lines.append(f"{continuation}{line}")


def _section_title(section: str) -> str:
    return section.replace("_", " ").title()


def _notes_kv(notes: str | None) -> dict[str, str]:
    """Parse the Brick 5 ``key=value; key=value`` provenance-notes convention.

    The same deterministic rule as the Brick 6 query layer; kept local so the
    renderer never depends on a private query helper. Values that are not
    ``key=value`` pairs are ignored here (the raw notes are rendered as-is).
    """
    parsed: dict[str, str] = {}
    if not notes:
        return parsed
    for part in notes.split(";"):
        key, sep, value = part.partition("=")
        if sep:
            parsed[key.strip()] = value.strip()
    return parsed


def export_project_markdown(
    project: Any, destination: str | Path | None = None
) -> Path:
    """Render a project's canonical Build Pack to a Markdown file.

    Loads the canonical pack and reconciliation through the existing project
    stores, renders them with :class:`BuildPackMarkdownExporter`, and writes
    only the Markdown bytes to the destination. Canonical project state is
    never mutated.

    ``destination`` may be ``None`` (defaults to ``exports/build-pack.md``
    under the project root), a path relative to the project's ``exports/``
    directory, or an absolute path. The Markdown is written with explicit
    ``\\n`` newlines so identical canonical data produces byte-identical
    files on Linux and Windows.
    """
    pack = project.buildpack_store.load_or_create()
    reconciliation = project.reconciliation_store.load_or_empty()
    document = BuildPackMarkdownExporter().export(pack, reconciliation)

    if destination is None:
        target = project.exports_dir / DEFAULT_EXPORT_FILENAME
    else:
        supplied = Path(destination)
        target = supplied if supplied.is_absolute() else project.exports_dir / supplied
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document.text, encoding="utf-8", newline="\n")
    return target
