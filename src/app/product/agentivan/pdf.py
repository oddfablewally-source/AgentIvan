"""Brick 8 -- deterministic PDF export of the canonical Build Pack.

This module is an *output layer only*. It renders the current canonical
:class:`~app.product.agentivan.model.BuildPack` into a paginated,
human-readable PDF document suitable for human inspection, versioned project
documentation, and distribution.

Design rules (all enforced here, none optional):

- PDF is an output artifact. The canonical Build Pack remains the structured
  source of truth; this module never modifies it.
- Export is read-only: no revisions are created, no statuses change, no
  contradictions are resolved, no provenance is fabricated.
- The canonical section order from ``SECTION_SCHEMAS`` (Brick 1) is the
  rendering order, matching Brick 7. Sections that are absent or empty are
  omitted.
- Statuses are rendered with their exact existing vocabulary
  (``KNOWN`` / ``RESEARCHED`` / ``PROPOSED`` / ``MEASURED`` / ``CALCULATED`` /
  ``VERIFIED`` / ``UNKNOWN``); they are never reinterpreted into prose.
- Provenance is rendered only where the canonical data already contains it.
- Contradictions, open issues, and UNKNOWN values stay visible; both sides
  of a contradiction render where stored.
- Project data is treated as *content*, never as formatting instructions:
  ``&``, ``<``, ``>`` are escaped for the ReportLab paragraph parser, text
  is never placed in PDF operators, and no PDF features beyond static text
  (no forms, annotations, JavaScript, external resources, hyperlinks,
  charts) are used.
- Output is deterministic: identical BuildPack input produces byte-for-byte
  identical PDF bytes. The ReportLab canvas runs in ``invariant`` mode
  (fixed document ID, fixed CreationDate/ModDate of 2000-01-01 UTC) and all
  metadata is set explicitly -- no current time, hostnames, usernames, or
  environment values are emitted.
- Fonts are bundled project-locally (``fonts/DejaVuSans*.ttf``), never taken
  from the host system and never downloaded.
- Dictionaries render with keys sorted by their string form.
- Only ``reportlab`` is used (the one PDF dependency allowed for this
  brick). Standard library otherwise. No network, no LLM, no database.

The public API is deliberately small::

    exporter = BuildPackPDFExporter()
    document = exporter.export(pack, reconciliation=reconciliation)
    pdf_bytes = document.data  # byte-identical for identical input

``export_project_pdf(project, destination)`` wires the exporter to an
existing :class:`~app.product.agentivan.project.Project` and writes the PDF
to the explicitly requested destination (default ``exports/build-pack.pdf``).
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from xml.sax.saxutils import escape as _xml_escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from .model import (
    SECTION_SCHEMAS,
    BuildPack,
    BuildRecord,
    Contradiction,
    Provenance,
)
from .query import BuildPackQuery
from .reconciliation import Reconciliation, ReconciliationContradiction

__all__ = [
    "BuildPackPDFExporter",
    "PDFDocument",
    "PDFExportError",
    "export_project_pdf",
]

PDF_FORMAT_NAME = "agentivan-pdf"
PDF_FORMAT_VERSION = 1
DEFAULT_PDF_FILENAME = "build-pack.pdf"

_FONT_DIR = Path(__file__).resolve().parent / "fonts"
_FONT_REGULAR = _FONT_DIR / "DejaVuSans.ttf"
_FONT_BOLD = _FONT_DIR / "DejaVuSans-Bold.ttf"
FONT_NAME = "AgentIvanSans"
FONT_NAME_BOLD = "AgentIvanSans-Bold"

# Fixed by ReportLab invariant mode; recorded here so the choice is explicit.
INVARIANT_CREATION_DATE = "2000-01-01T00:00:00+00:00"


class PDFExportError(ValueError):
    """Raised when a PDF export cannot be performed."""


@dataclass(frozen=True)
class PDFDocument:
    """An immutable rendered PDF document.

    ``data`` holds the complete PDF bytes; ``page_count`` is the
    deterministic page count. The wrapper exists only to give the export
    result deterministic serialization; there is no persistent export state.
    """

    data: bytes
    page_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.data, (bytes, bytearray)):
            raise PDFExportError("PDFDocument data must be bytes")
        if not isinstance(self.page_count, int) or self.page_count < 1:
            raise PDFExportError("PDFDocument page_count must be a positive int")

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": PDF_FORMAT_NAME,
            "format_version": PDF_FORMAT_VERSION,
            "page_count": self.page_count,
            "sha256": _sha256_hex(self.data),
            "data_hex": bytes(self.data).hex(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PDFDocument":
        if not isinstance(data, Mapping):
            raise PDFExportError("PDFDocument.from_dict requires a mapping")
        if data.get("format") != PDF_FORMAT_NAME:
            raise PDFExportError(
                f"Unsupported PDF document format: {data.get('format')!r}"
            )
        if data.get("format_version") != PDF_FORMAT_VERSION:
            raise PDFExportError(
                "Unsupported PDF document format version: "
                f"{data.get('format_version')!r}"
            )
        try:
            raw = bytes.fromhex(data["data_hex"])
        except (KeyError, ValueError, TypeError) as exc:
            raise PDFExportError(f"Invalid PDF document data: {exc}") from exc
        page_count = data.get("page_count")
        if not isinstance(page_count, int) or page_count < 1:
            raise PDFExportError("Invalid PDF document page_count")
        document = cls(data=raw, page_count=page_count)
        if _sha256_hex(raw) != data.get("sha256"):
            raise PDFExportError("PDF document checksum mismatch")
        return document

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "PDFDocument":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PDFExportError(f"Invalid PDF document JSON: {exc}") from exc
        return cls.from_dict(data)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# fonts (bundled, project-local, never host system fonts)
# ---------------------------------------------------------------------------


def _ensure_fonts() -> None:
    """Register the bundled DejaVu fonts with ReportLab (idempotent)."""
    for name, path in ((FONT_NAME, _FONT_REGULAR), (FONT_NAME_BOLD, _FONT_BOLD)):
        if name in pdfmetrics.getRegisteredFontNames():
            continue
        if not path.is_file():
            raise PDFExportError(f"Bundled font missing: {path}")
        pdfmetrics.registerFont(TTFont(name, str(path)))


# ---------------------------------------------------------------------------
# deterministic canvas
# ---------------------------------------------------------------------------


class _DeterministicCanvas(Canvas):
    """ReportLab canvas locked to deterministic output.

    ``invariant=True`` fixes the document ID and CreationDate/ModDate
    (2000-01-01 UTC) so identical input yields identical bytes. Metadata is
    set explicitly by the exporter; nothing time- or host-dependent leaks in.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["invariant"] = True
        super().__init__(*args, **kwargs)


def _footer(canvas: Canvas, doc: Any) -> None:
    """Deterministic page footer: a simple ``Page N`` marker."""
    canvas.saveState()
    canvas.setFont(FONT_NAME, 8)
    canvas.setFillColorRGB(0.35, 0.35, 0.35)
    canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f"Page {doc.page}")
    canvas.restoreState()


# ---------------------------------------------------------------------------
# paragraph styles and text handling
# ---------------------------------------------------------------------------


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle(
        "AgentIvanBase", fontName=FONT_NAME, fontSize=10, leading=14
    )
    return {
        "title": ParagraphStyle(
            "AgentIvanTitle",
            parent=base,
            fontName=FONT_NAME_BOLD,
            fontSize=18,
            leading=22,
            spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "AgentIvanH2",
            parent=base,
            fontName=FONT_NAME_BOLD,
            fontSize=14,
            leading=17,
            spaceBefore=16,
            spaceAfter=8,
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "AgentIvanH3",
            parent=base,
            fontName=FONT_NAME_BOLD,
            fontSize=11,
            leading=14,
            spaceBefore=10,
            spaceAfter=4,
            keepWithNext=True,
        ),
        "body": ParagraphStyle("AgentIvanBody", parent=base, spaceAfter=4),
        "bullet": ParagraphStyle(
            "AgentIvanBullet",
            parent=base,
            leftIndent=18,
            firstLineIndent=0,
            bulletIndent=9,
            spaceAfter=2,
        ),
        "bullet2": ParagraphStyle(
            "AgentIvanBullet2",
            parent=base,
            leftIndent=36,
            firstLineIndent=0,
            bulletIndent=27,
            spaceAfter=2,
        ),
    }


def _esc(text: Any) -> str:
    """Escape project data for the ReportLab paragraph parser.

    Project content is data, never markup: ``&``, ``<``, ``>`` are escaped
    so arbitrary content (angle brackets, Markdown characters, backslashes,
    pipes, backticks) cannot become formatting instructions.
    """
    return _xml_escape(str(text))


def _single_line(value: str) -> str:
    return " ".join(str(value).replace("\r", "\n").split("\n")).strip()


def _render_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _para(text: str, style: ParagraphStyle) -> Paragraph:
    paragraph = Paragraph(_esc(text), style)
    paragraph.splitLongWords = 1
    return paragraph


def _rich(lines: Sequence[str], style: ParagraphStyle) -> Paragraph:
    """One paragraph from pre-escaped fragments joined with line breaks."""
    paragraph = Paragraph("<br/>".join(lines), style)
    paragraph.splitLongWords = 1
    return paragraph


# ---------------------------------------------------------------------------
# the exporter
# ---------------------------------------------------------------------------


class BuildPackPDFExporter:
    """Deterministic, read-only renderer: canonical BuildPack -> PDF.

    The exporter holds no state between calls; every ``export`` call is
    independent and produces byte-identical output for identical input.
    """

    def __init__(self) -> None:
        _ensure_fonts()
        self._styles = _styles()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def export(
        self,
        pack: BuildPack,
        reconciliation: Reconciliation | None = None,
    ) -> PDFDocument:
        """Render ``pack`` (and optionally ``reconciliation``) to a PDF."""
        if not isinstance(pack, BuildPack):
            raise PDFExportError(
                f"export() requires a BuildPack, got {type(pack).__name__}"
            )
        if reconciliation is not None and not isinstance(reconciliation, Reconciliation):
            raise PDFExportError(
                "export() reconciliation must be a Reconciliation or None, "
                f"got {type(reconciliation).__name__}"
            )
        query = BuildPackQuery(pack, reconciliation=reconciliation)
        story = self._build_story(query, pack, reconciliation)
        title = _single_line(pack.identity.name) or "Untitled project"

        buffer = io.BytesIO()
        holder: dict[str, Any] = {}

        def _canvas_maker(*args: Any, **kwargs: Any) -> _DeterministicCanvas:
            canvas = _DeterministicCanvas(*args, **kwargs)
            canvas.setTitle(title)
            canvas.setAuthor("")
            canvas.setSubject("AgentIvan Build Pack")
            canvas.setCreator("AgentIvan")
            canvas.setKeywords("")
            holder["canvas"] = canvas
            return canvas

        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
            title=title,
            author="",
            subject="AgentIvan Build Pack",
            creator="AgentIvan",
            keywords="",
            invariant=True,
        )
        doc.build(story, canvasmaker=_canvas_maker, onFirstPage=_footer, onLaterPages=_footer)
        if "canvas" not in holder:
            raise PDFExportError("PDF canvas was never created during export")
        page_count = int(doc.page)
        data = buffer.getvalue()
        if not data.startswith(b"%PDF-"):
            raise PDFExportError("generated PDF is missing its %PDF- header")
        return PDFDocument(data=data, page_count=page_count)

    # ------------------------------------------------------------------
    # story construction
    # ------------------------------------------------------------------
    def _build_story(
        self,
        query: BuildPackQuery,
        pack: BuildPack,
        reconciliation: Reconciliation | None,
    ) -> list:
        story: list = []
        styles = self._styles
        identity = pack.identity
        story.append(_para(_single_line(identity.name) or "Untitled project", styles["title"]))
        story.append(self._h2("Identity"))
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
                story.append(self._kv(label, _render_scalar(value), 0))
        story.append(self._kv("Revision", _render_scalar(identity.revision), 0))
        for section in SECTION_SCHEMAS:
            records = pack.sections.get(section, [])
            if records:
                story.append(self._h2(_section_title(section)))
                for record in sorted(records, key=lambda r: r.record_id):
                    story.extend(self._record_flowables(query, record, reconciliation))
        self._contradiction_flowables(story, query, pack, reconciliation)
        self._revision_flowables(story, query, pack)
        return story

    def _h2(self, text: str) -> Paragraph:
        return _para(text, self._styles["h2"])

    def _h3(self, text: str) -> Paragraph:
        return _para(text, self._styles["h3"])

    def _kv(self, label: str, value: str, level: int) -> Paragraph:
        style = self._styles["body"] if level == 0 else self._styles["bullet" if level == 1 else "bullet2"]
        fragments = [_esc(line) for line in str(value).split("\n")]
        paragraph = Paragraph(
            f"<b>{_esc(label)}:</b> " + "<br/>".join(fragments),
            style,
            bulletText="•" if level > 0 else None,
        )
        paragraph.splitLongWords = 1
        return paragraph

    def _bullet_text(self, text: str, level: int) -> Paragraph:
        style = self._styles["bullet" if level <= 1 else "bullet2"]
        paragraph = Paragraph(_esc(text), style, bulletText="•")
        paragraph.splitLongWords = 1
        return paragraph

    def _render_value(self, label: str, value: Any, level: int) -> list:
        """Deterministic structured-value rendering (mirrors Brick 7 rules)."""
        flowables: list = []
        if isinstance(value, Mapping):
            flowables.append(self._bullet_text(f"{label}:", level))
            for key in sorted(value, key=str):
                flowables.extend(self._render_value(str(key), value[key], level + 1))
            return flowables
        if isinstance(value, (list, tuple)):
            flowables.append(self._bullet_text(f"{label}:", level))
            for item in value:
                flowables.extend(self._render_sequence_item(item, level + 1))
            return flowables
        flowables.append(self._kv(label, _render_scalar(value), level))
        return flowables

    def _render_sequence_item(self, item: Any, level: int) -> list:
        flowables: list = []
        if isinstance(item, Mapping):
            flowables.append(self._bullet_text("—", level))
            for key in sorted(item, key=str):
                flowables.extend(self._render_value(str(key), item[key], level + 1))
            return flowables
        if isinstance(item, (list, tuple)):
            flowables.append(self._bullet_text("—", level))
            for sub in item:
                flowables.extend(self._render_sequence_item(sub, level + 1))
            return flowables
        style = self._styles["bullet" if level <= 1 else "bullet2"]
        fragments = [_esc(line) for line in _render_scalar(item).split("\n")]
        paragraph = Paragraph("<br/>".join(fragments), style, bulletText="•")
        paragraph.splitLongWords = 1
        flowables.append(paragraph)
        return flowables

    def _record_flowables(
        self,
        query: BuildPackQuery,
        record: BuildRecord,
        reconciliation: Reconciliation | None,
    ) -> list:
        flowables: list = [self._h3(record.record_id)]
        flowables.append(self._kv("Status", record.status.value, 0))
        for key in sorted(record.fields, key=str):
            flowables.extend(self._render_value(key, record.fields[key], 0))
        if record.notes:
            flowables.extend(self._render_value("Notes", record.notes, 0))
        flowables.extend(self._provenance_flowables(query, record, reconciliation))
        flowables.append(Spacer(1, 4))
        return flowables

    def _provenance_flowables(
        self,
        query: BuildPackQuery,
        record: BuildRecord,
        reconciliation: Reconciliation | None,
    ) -> list:
        prov: Provenance | None = record.provenance
        if prov is None:
            return []
        flowables: list = [self._bullet_text("Provenance:", 0)]
        for label, value in (
            ("Source type", prov.source_type),
            ("Source reference", prov.source_reference),
            ("Date", prov.date),
            ("Author", prov.author),
        ):
            if value:
                flowables.extend(self._render_value(label, value, 1))
        if prov.source_text:
            flowables.extend(self._render_value("Source text", prov.source_text, 1))
        notes_kv = _notes_kv(prov.notes)
        extraction_id = notes_kv.get("extraction_id")
        if extraction_id:
            flowables.extend(self._render_value("Extraction", extraction_id, 1))
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
            flowables.extend(self._render_value("Reconciliation", detail, 1))
        flowables.extend(
            self._render_value("Provenance kind", query.provenance_kind(record), 1)
        )
        if prov.notes:
            flowables.extend(self._render_value("Notes", prov.notes, 1))
        return flowables

    def _contradiction_flowables(
        self,
        story: list,
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
        story.append(self._h2("Contradictions"))
        story.append(
            _para(
                "The following information is unresolved. Nothing here has been "
                "decided; both sides are shown where the canonical data records them.",
                self._styles["body"],
            )
        )
        for contradiction in canonical:
            story.extend(self._canonical_contradiction_flowables(contradiction))
        for contradiction in reconciled:
            story.extend(
                self._reconciliation_contradiction_flowables(query, pack, contradiction)
            )

    def _canonical_contradiction_flowables(
        self, contradiction: Contradiction
    ) -> list:
        flowables: list = [self._h3(contradiction.contradiction_id)]
        flowables.append(self._kv("Kind", "canonical", 0))
        flowables.append(
            self._kv("Resolution status", contradiction.resolution_status.value, 0)
        )
        if contradiction.section:
            flowables.append(self._kv("Section", contradiction.section, 0))
        if contradiction.field:
            flowables.append(self._kv("Field", contradiction.field, 0))
        if contradiction.record_ids:
            flowables.extend(
                self._render_value("Records", list(contradiction.record_ids), 0)
            )
        if contradiction.explanation:
            flowables.extend(self._render_value("Explanation", contradiction.explanation, 0))
        if contradiction.resolution_note:
            flowables.extend(
                self._render_value("Resolution note", contradiction.resolution_note, 0)
            )
        flowables.append(Spacer(1, 4))
        return flowables

    def _reconciliation_contradiction_flowables(
        self,
        query: BuildPackQuery,
        pack: BuildPack,
        contradiction: ReconciliationContradiction,
    ) -> list:
        flowables: list = [self._h3(contradiction.contradiction_id)]
        flowables.append(self._kv("Kind", "reconciliation", 0))
        flowables.append(self._kv("State", contradiction.state.value, 0))
        if contradiction.section:
            flowables.append(self._kv("Section", contradiction.section, 0))
        if contradiction.affected_field:
            flowables.append(self._kv("Affected field", contradiction.affected_field, 0))
        if contradiction.description:
            flowables.extend(self._render_value("Description", contradiction.description, 0))
        if contradiction.existing_record_id:
            flowables.append(self._kv("Existing record", contradiction.existing_record_id, 0))
        existing = None
        for record in pack.sections.get(contradiction.section or "", []):
            if record.record_id == contradiction.existing_record_id:
                existing = record
                break
        flowables.append(self._bullet_text("Existing side:", 0))
        if existing is not None:
            flowables.append(self._kv("Status", existing.status.value, 1))
            for key in sorted(existing.fields, key=str):
                flowables.extend(self._render_value(key, existing.fields[key], 1))
        elif contradiction.existing_provenance:
            flowables.extend(
                self._render_value(
                    "Recorded provenance", contradiction.existing_provenance, 1
                )
            )
        else:
            flowables.append(self._kv("Detail", "not available in current Build Pack", 1))
        flowables.append(self._bullet_text("Candidate side:", 0))
        flowables.extend(
            self._render_value("Candidate provenance", contradiction.candidate_provenance, 1)
        )
        if contradiction.resolution:
            flowables.append(self._kv("Resolution", contradiction.resolution, 0))
        if contradiction.resolution_note:
            flowables.extend(
                self._render_value("Resolution note", contradiction.resolution_note, 0)
            )
        if contradiction.resolved_at:
            flowables.append(self._kv("Resolved at", contradiction.resolved_at, 0))
        flowables.append(Spacer(1, 4))
        return flowables

    def _revision_flowables(self, story: list, query: BuildPackQuery, pack: BuildPack) -> None:
        revisions = list(pack.change_history)
        if not revisions:
            return
        story.append(self._h2("Change history"))
        for index, revision in enumerate(revisions, start=1):
            story.append(self._h3(f"Revision {index}"))
            story.append(self._kv("Revision number", str(revision.revision), 0))
            if revision.affected_area:
                story.append(self._kv("Affects", revision.affected_area, 0))
            if revision.description:
                story.extend(self._render_value("Description", revision.description, 0))
            if revision.previous_state is not None:
                story.extend(self._render_value("Before", revision.previous_state, 0))
            if revision.new_state is not None:
                story.extend(self._render_value("After", revision.new_state, 0))
            sources = query.revision_sources(revision)
            if sources is not None:
                if sources.get("source_reference"):
                    story.extend(
                        self._render_value("Source", str(sources["source_reference"]), 0)
                    )
                if sources.get("source_type"):
                    story.extend(
                        self._render_value("Source type", str(sources["source_type"]), 0)
                    )
            if revision.author:
                story.append(self._kv("Recorded by", revision.author, 0))
            if revision.timestamp:
                story.append(self._kv("Recorded at", revision.timestamp, 0))
            story.append(Spacer(1, 4))


def _section_title(section: str) -> str:
    return section.replace("_", " ").title()


def _notes_kv(notes: str | None) -> dict[str, str]:
    """Parse the Brick 5 ``key=value; key=value`` provenance-notes convention."""
    parsed: dict[str, str] = {}
    if not notes:
        return parsed
    for part in notes.split(";"):
        key, sep, value = part.partition("=")
        if sep:
            parsed[key.strip()] = value.strip()
    return parsed


def export_project_pdf(project: Any, destination: str | Path | None = None) -> Path:
    """Render a project's canonical Build Pack to a PDF file.

    Loads the canonical pack and reconciliation through the existing project
    stores, renders them with :class:`BuildPackPDFExporter`, and writes only
    the PDF bytes to the destination. Canonical project state is never
    mutated.

    ``destination`` may be ``None`` (defaults to ``exports/build-pack.pdf``
    under the project root), a path relative to the project's ``exports/``
    directory, or an absolute path.
    """
    pack = project.buildpack_store.load_or_create()
    reconciliation = project.reconciliation_store.load_or_empty()
    document = BuildPackPDFExporter().export(pack, reconciliation)

    if destination is None:
        target = project.exports_dir / DEFAULT_PDF_FILENAME
    else:
        supplied = Path(destination)
        target = supplied if supplied.is_absolute() else project.exports_dir / supplied
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(document.data)
    return target
