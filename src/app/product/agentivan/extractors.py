"""
Extractors for AgentIvan (Brick 4).

All extractors sit behind the ``Extractor`` interface so future
extractors (LLM-backed, or anything else) can be added without
changing the storage, provenance, or review model:

    DeterministicExtractor   rule-based; implemented in this brick
    LLMExtractor             extension point; NOT implemented
    FutureExtractor          anything later, behind the same interface

Brick 4 implements deterministic extraction only. The deterministic
extractor is deliberately conservative: it identifies obvious
structured information (explicit status markers, measurements with
units, URLs, labeled declarations, Markdown headings, JSON fields)
without pretending to understand arbitrary prose. Ambiguous material
is preserved as general/unclassified candidates, never given invented
meaning.

Stdlib only. No network, no model calls, no embeddings, no OCR.
"""

from __future__ import annotations

import abc
import json
import re
from typing import TYPE_CHECKING, Any, ClassVar

from .extraction import (
    CandidateCategory,
    ExtractedItem,
    ExtractionError,
    ExtractionMethod,
    ExtractionNotAvailableError,
    ExtractionOutcome,
    ExtractionResult,
    SourceLocation,
    TextChunk,
    chunk_json,
    chunk_text,
    make_extraction_id,
)
from .intake import utc_now_iso
from .sources import SourceKind, SourceRecord
from .statuses import InfoStatus

if TYPE_CHECKING:
    from .catalog import SourceCatalog

__all__ = [
    "Extractor",
    "DeterministicExtractor",
    "LLMExtractor",
    "get_extractor",
    "EXTRACTOR_NAMES",
]


class Extractor(abc.ABC):
    """Common interface for every AgentIvan extractor.

    ``extract_source`` inspects one source record (reading preserved
    material through the catalog) and returns an ExtractionResult. It
    must never modify the source, the project, or the Build Pack.
    """

    name: ClassVar[str] = "base"
    version: ClassVar[int] = 1

    @abc.abstractmethod
    def extract_source(
        self, catalog: "SourceCatalog", record: SourceRecord
    ) -> ExtractionResult:
        """Extract candidate information from one source record."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Deterministic extractor
# ---------------------------------------------------------------------------

# Explicit status markers: uppercase, bracketed, unambiguous.
_STATUS_MARKER_RE = re.compile(
    r"\[(KNOWN|RESEARCHED|PROPOSED|MEASURED|CALCULATED|VERIFIED|UNKNOWN)\]"
)

# Explicit textual declarations: "MEASURED: ...", "TODO: ...", ...
# Conservative: keyword at line start, followed by a colon.
_DECLARATION_RE = re.compile(
    r"^(KNOWN|RESEARCHED|PROPOSED|MEASURED|CALCULATED|VERIFIED|UNKNOWN|TODO)"
    r"\s*:\s*(.+?)\s*$",
    re.IGNORECASE,
)

_DECLARATION_STATUS = {
    "KNOWN": InfoStatus.KNOWN,
    "RESEARCHED": InfoStatus.RESEARCHED,
    "PROPOSED": InfoStatus.PROPOSED,
    "MEASURED": InfoStatus.MEASURED,
    "CALCULATED": InfoStatus.CALCULATED,
    "VERIFIED": InfoStatus.VERIFIED,
    "UNKNOWN": InfoStatus.UNKNOWN,
}

# Conservative measurement pattern: a number immediately followed by a
# recognized unit. Deliberately does NOT match:
#   - thousands separators ("1,000 mm" is skipped, not misread as 0 mm)
#   - ranges ("28-30 inches" yields nothing, not a wrong "30 inches")
#   - numbers glued to words ("abc28in" is skipped)
#   - fractions ("1/2 in" is skipped)
# Units are listed longest-first so "inches" wins over "in".
_MEASUREMENT_RE = re.compile(
    r"(?<![\d.,\-])\b"
    r"(?P<value>\d+(?:\.\d+)?)"
    r"\s*"
    r"(?P<unit>millimeters|millimeter|centimeters|centimeter|meters|meter"
    r"|inches|inch|feet|foot|degrees|degree|mm|cm|m|in|ft|lbs|lb|kg|°)"
    r"\b",
    re.IGNORECASE,
)

_UNIT_CANONICAL = {
    "millimeter": "mm",
    "millimeters": "mm",
    "mm": "mm",
    "centimeter": "cm",
    "centimeters": "cm",
    "cm": "cm",
    "meter": "m",
    "meters": "m",
    "m": "m",
    "inch": "in",
    "inches": "in",
    "in": "in",
    "foot": "ft",
    "feet": "ft",
    "ft": "ft",
    "lb": "lb",
    "lbs": "lb",
    "kg": "kg",
    "degree": "deg",
    "degrees": "deg",
    "°": "deg",
}

# Short unit tokens that are easily misread ("5 m", "3 in stock"):
# extracted, but at reduced confidence.
_AMBIGUOUS_UNITS = {"m", "in", "ft", "lb"}

_URL_RE = re.compile(r"https?://[^\s<>\"'`)\]]+")

# Explicit labeled declarations: "Project: X", "Part: bracket",
# "Material: steel". Conservative: the label must be at line start.
_PROJECT_LABEL_RE = re.compile(
    r"^(project(?:\s+name)?|title)\s*:\s*(.+?)\s*$", re.IGNORECASE
)
_PART_LABEL_RE = re.compile(r"^parts?\s*:\s*(.+?)\s*$", re.IGNORECASE)
_MATERIAL_LABEL_RE = re.compile(
    r"^materials?\s*:\s*(.+?)\s*$", re.IGNORECASE
)

# Bare TODO mentions (the "TODO: ..." form is handled as a declaration).
_TODO_RE = re.compile(r"\bTODO\b", re.IGNORECASE)

# Conservative status keywords for measurement lines. These only ever
# produce MEASURED or PROPOSED -- VERIFIED is never inferred from
# phrasing, only from explicit markers/declarations.
_MEASURED_KEYWORD_RE = re.compile(r"\bmeasured\b", re.IGNORECASE)
_PROPOSED_KEYWORD_RE = re.compile(
    r"\b(should|proposed|proposal|think|maybe|suggest|suggestion|idea)\b",
    re.IGNORECASE,
)

_TEXT_EXTENSIONS = (
    ".txt",
    ".text",
    ".md",
    ".markdown",
    ".json",
    ".csv",
    ".log",
)

# Matches an actual Markdown heading line (hashes followed by whitespace),
# used to tell heading chunks apart from paragraphs that merely carry
# heading attribution (a "#hashtag" paragraph line is not a heading).
_HEADING_LINE_RE = re.compile(r"^[ ]{0,3}#{1,6}[ \t]+")


def _looks_like_json(text: str) -> Any | None:
    """Parse text as JSON when it is plausibly a JSON document.

    Returns the parsed value (dict/list) or None. Conservative: the
    text must start with ``{`` or ``[`` and parse cleanly.
    """
    stripped = text.strip()
    if not stripped or stripped[0] not in ("{", "["):
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, (dict, list)) else None


class DeterministicExtractor(Extractor):
    """Rule-based extractor: explicit structure only, no understanding.

    Never fetches references, never performs OCR, never calls a model.
    Anything it cannot classify conservatively becomes a GENERAL
    candidate or an honest NO_EXTRACTION_AVAILABLE outcome.
    """

    name: ClassVar[str] = "deterministic"
    version: ClassVar[int] = 1

    # ------------------------------------------------------------------
    # entry point
    # ------------------------------------------------------------------
    def extract_source(
        self, catalog: "SourceCatalog", record: SourceRecord
    ) -> ExtractionResult:
        started = utc_now_iso()
        self._decoded_text = ""
        self._json_value: Any = None
        if record.kind is SourceKind.REFERENCE:
            return self._reference_result(record, started)
        if record.kind is SourceKind.IMAGE:
            return ExtractionResult(
                source_id=record.source_id,
                outcome=ExtractionOutcome.NO_EXTRACTION_AVAILABLE,
                note=(
                    "Image sources are preserved but not inspected: "
                    "Brick 4 performs no OCR."
                ),
                extracted_at=started,
                extractor_name=self.name,
                extractor_version=self.version,
            )
        # TEXT or FILE: read the preserved bytes (raises CatalogError
        # when the file is missing/unreadable; the store turns that
        # into an ERROR outcome for this source only).
        raw = catalog.read_bytes(record.source_id)
        kind = self._classify_bytes(record, raw)
        if kind == "unavailable":
            return ExtractionResult(
                source_id=record.source_id,
                outcome=ExtractionOutcome.NO_EXTRACTION_AVAILABLE,
                note=(
                    "Deterministic extraction is not available for this "
                    "content type: it is not decodable as UTF-8 text."
                ),
                extracted_at=started,
                extractor_name=self.name,
                extractor_version=self.version,
            )
        if kind == "json":
            value = self._json_value
            chunks = chunk_json(record.source_id, value)
            items = [
                self._json_item(record, chunk, value, started)
                for chunk in chunks
            ]
        else:
            chunks = chunk_text(record.source_id, self._decoded_text)
            items = self._extract_text_chunks(record, chunks, started)
        # Deduplicate identical extractions (same deterministic id).
        unique: dict[str, ExtractedItem] = {}
        for item in items:
            unique.setdefault(item.extraction_id, item)
        return ExtractionResult(
            source_id=record.source_id,
            outcome=ExtractionOutcome.SUCCESS,
            items=list(unique.values()),
            extracted_at=started,
            extractor_name=self.name,
            extractor_version=self.version,
        )

    # ------------------------------------------------------------------
    # content classification
    # ------------------------------------------------------------------
    def _classify_bytes(self, record: SourceRecord, raw: bytes) -> str:
        """Return "text", "json", or "unavailable".

        Stashes the decoded text / parsed JSON on the instance for the
        caller (single-threaded, one source at a time).
        """
        media = (record.media_type or "").split(";")[0].strip().lower()
        filename = (record.original_filename or "").lower()
        wants_json = media == "application/json" or filename.endswith(".json")
        looks_textual = (
            media.startswith("text/")
            or wants_json
            or any(filename.endswith(ext) for ext in _TEXT_EXTENSIONS)
        )
        if not looks_textual:
            return "unavailable"
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return "unavailable"
        self._decoded_text = text
        # A text file that actually contains a JSON document is treated
        # as JSON: _looks_like_json requires a {/[ start and clean parse.
        parsed = _looks_like_json(text)
        if parsed is not None:
            self._json_value = parsed
            return "json"
        return "text"

    # ------------------------------------------------------------------
    # references
    # ------------------------------------------------------------------
    def _reference_result(
        self, record: SourceRecord, started: str
    ) -> ExtractionResult:
        """Record the reference as a candidate; never fetch it."""
        assert record.reference is not None
        location = SourceLocation(
            source_id=record.source_id,
            source_filename=record.original_filename or record.label,
        )
        item = ExtractedItem(
            extraction_id=make_extraction_id(
                extractor_name=self.name,
                extractor_version=self.version,
                source_id=record.source_id,
                chunk_id=None,
                category=CandidateCategory.RESEARCH,
                status=None,
                confidence=1.0,
                method=ExtractionMethod.DETERMINISTIC,
                text=record.reference,
                normalized=None,
                char_start=None,
                char_end=None,
                json_path=None,
            ),
            source_id=record.source_id,
            location=location,
            text=record.reference,
            category=CandidateCategory.RESEARCH,
            confidence=1.0,
            method=ExtractionMethod.DETERMINISTIC,
            status=None,
            created_at=started,
        )
        return ExtractionResult(
            source_id=record.source_id,
            outcome=ExtractionOutcome.SUCCESS,
            items=[item],
            note="Reference recorded as a candidate; it was not fetched.",
            extracted_at=started,
            extractor_name=self.name,
            extractor_version=self.version,
        )

    # ------------------------------------------------------------------
    # JSON extraction
    # ------------------------------------------------------------------
    def _json_item(
        self,
        record: SourceRecord,
        chunk: TextChunk,
        value: Any,
        started: str,
    ) -> ExtractedItem:
        assert chunk.json_path is not None
        leaf = self._leaf_at(value, chunk.json_path)
        text = f"{chunk.json_path} = {chunk.text}"
        location = SourceLocation(
            source_id=record.source_id,
            source_filename=record.original_filename or record.label,
            heading=chunk.json_path,
            chunk_id=chunk.chunk_id,
            json_path=chunk.json_path,
        )
        return ExtractedItem(
            extraction_id=make_extraction_id(
                extractor_name=self.name,
                extractor_version=self.version,
                source_id=record.source_id,
                chunk_id=chunk.chunk_id,
                category=CandidateCategory.GENERAL,
                status=None,
                confidence=1.0,
                method=ExtractionMethod.DETERMINISTIC,
                text=text,
                normalized=leaf,
                char_start=None,
                char_end=None,
                json_path=chunk.json_path,
            ),
            source_id=record.source_id,
            location=location,
            text=text,
            category=CandidateCategory.GENERAL,
            confidence=1.0,
            method=ExtractionMethod.DETERMINISTIC,
            normalized=leaf,
            status=None,
            created_at=started,
        )

    @staticmethod
    def _leaf_at(value: Any, path: str) -> Any:
        """Resolve a ``$.a[0].b`` / ``$["weird key"]`` path from chunk_json."""
        token_re = re.compile(
            r"\.([A-Za-z_][A-Za-z0-9_]*)" r"|\[(\d+)\]" r'|\["((?:[^"\\]|\\.)*)"\]'
        )
        node = value
        for match in token_re.finditer(path):
            key, index, quoted = match.groups()
            if key is not None:
                node = node[key]
            elif index is not None:
                node = node[int(index)]
            else:
                node = node[json.loads(f'"{quoted}"')]
        return node

    # ------------------------------------------------------------------
    # text extraction
    # ------------------------------------------------------------------
    def _extract_text_chunks(
        self,
        record: SourceRecord,
        chunks: list[TextChunk],
        started: str,
    ) -> list[ExtractedItem]:
        items: list[ExtractedItem] = []
        for chunk in chunks:
            if chunk.heading is not None and _HEADING_LINE_RE.match(chunk.text):
                items.append(self._heading_item(record, chunk, started))
                continue
            for line_text, line_start, line_end in self._chunk_lines(chunk):
                items.extend(
                    self._extract_line(
                        record, chunk, line_text, line_start, line_end, started
                    )
                )
        return items

    @staticmethod
    def _chunk_lines(chunk: TextChunk) -> list[tuple[str, int, int]]:
        """Split a chunk into lines with absolute character offsets."""
        assert chunk.char_start is not None and chunk.char_end is not None
        lines = []
        pos = chunk.char_start
        for raw_line in chunk.text.splitlines(keepends=True):
            if raw_line.endswith("\r\n"):
                core, break_len = raw_line[:-2], 2
            elif raw_line.endswith("\n") or raw_line.endswith("\r"):
                core, break_len = raw_line[:-1], 1
            else:
                core, break_len = raw_line, 0
            start = pos
            # Offsets cover the stripped line: leading/trailing
            # whitespace is not part of the extracted expression.
            stripped = core.strip()
            if stripped:
                lead = len(core) - len(core.lstrip())
                start = pos + lead
                end = start + len(stripped)
                lines.append((stripped, start, end))
            pos += len(raw_line)
        return lines

    def _heading_item(
        self, record: SourceRecord, chunk: TextChunk, started: str
    ) -> ExtractedItem:
        assert chunk.heading is not None
        assert chunk.char_start is not None and chunk.char_end is not None
        # Locate the heading text within the "# ..." line for precise
        # offsets (first occurrence of the stripped heading text).
        found = chunk.text.find(chunk.heading)
        heading_start = chunk.char_start + (found if found >= 0 else 0)
        heading_end = heading_start + len(chunk.heading)
        location = SourceLocation(
            source_id=record.source_id,
            source_filename=record.original_filename or record.label,
            heading=chunk.heading,
            chunk_id=chunk.chunk_id,
            char_start=heading_start,
            char_end=heading_end,
        )
        return ExtractedItem(
            extraction_id=make_extraction_id(
                extractor_name=self.name,
                extractor_version=self.version,
                source_id=record.source_id,
                chunk_id=chunk.chunk_id,
                category=CandidateCategory.GENERAL,
                status=None,
                confidence=1.0,
                method=ExtractionMethod.DETERMINISTIC,
                text=chunk.heading,
                normalized=None,
                char_start=heading_start,
                char_end=heading_end,
                json_path=None,
            ),
            source_id=record.source_id,
            location=location,
            text=chunk.heading,
            category=CandidateCategory.GENERAL,
            confidence=1.0,
            method=ExtractionMethod.DETERMINISTIC,
            status=None,
            created_at=started,
        )

    def _extract_line(
        self,
        record: SourceRecord,
        chunk: TextChunk,
        line: str,
        line_start: int,
        line_end: int,
        started: str,
    ) -> list[ExtractedItem]:
        items: list[ExtractedItem] = []
        filename = record.original_filename or record.label

        def location_for(
            start: int, end: int, heading: str | None = None
        ) -> SourceLocation:
            return SourceLocation(
                source_id=record.source_id,
                source_filename=filename,
                heading=heading if heading is not None else chunk.heading,
                chunk_id=chunk.chunk_id,
                char_start=start,
                char_end=end,
            )

        def build(
            *,
            text: str,
            category: CandidateCategory,
            confidence: float,
            status: InfoStatus | None,
            normalized: Any | None,
            start: int,
            end: int,
            context: str | None = None,
        ) -> ExtractedItem:
            if context is None:
                chunk_text_stripped = chunk.text.strip()
                context = (
                    chunk_text_stripped
                    if chunk_text_stripped != text
                    else None
                )
            return ExtractedItem(
                extraction_id=make_extraction_id(
                    extractor_name=self.name,
                    extractor_version=self.version,
                    source_id=record.source_id,
                    chunk_id=chunk.chunk_id,
                    category=category,
                    status=status,
                    confidence=confidence,
                    method=ExtractionMethod.DETERMINISTIC,
                    text=text,
                    normalized=normalized,
                    char_start=start,
                    char_end=end,
                    json_path=None,
                ),
                source_id=record.source_id,
                location=location_for(start, end),
                text=text,
                category=category,
                confidence=confidence,
                method=ExtractionMethod.DETERMINISTIC,
                normalized=normalized,
                status=status,
                context=context,
                created_at=started,
            )

        # 1. Explicit status markers: [KNOWN], [MEASURED], ...
        marker_status: InfoStatus | None = None
        for match in _STATUS_MARKER_RE.finditer(line):
            status = InfoStatus(match.group(1))
            marker_status = marker_status or status
            items.append(
                build(
                    text=line,
                    category=CandidateCategory.GENERAL,
                    confidence=1.0,
                    status=status,
                    normalized=None,
                    start=line_start,
                    end=line_end,
                )
            )

        # 2. Explicit declarations: "MEASURED: ...", "TODO: ...", ...
        declaration_status: InfoStatus | None = None
        match = _DECLARATION_RE.match(line)
        if match:
            keyword = match.group(1).upper()
            if keyword == "TODO":
                items.append(
                    build(
                        text=line,
                        category=CandidateCategory.OPEN_ISSUE,
                        confidence=1.0,
                        status=None,
                        normalized=None,
                        start=line_start,
                        end=line_end,
                    )
                )
            else:
                declaration_status = _DECLARATION_STATUS[keyword]
                items.append(
                    build(
                        text=line,
                        category=CandidateCategory.GENERAL,
                        confidence=1.0,
                        status=declaration_status,
                        normalized=None,
                        start=line_start,
                        end=line_end,
                    )
                )

        # 3. Explicit labeled declarations.
        match = _PROJECT_LABEL_RE.match(line)
        if match:
            items.append(
                build(
                    text=line,
                    category=CandidateCategory.PROJECT_IDENTITY,
                    confidence=1.0,
                    status=None,
                    normalized=None,
                    start=line_start,
                    end=line_end,
                )
            )
        match = _PART_LABEL_RE.match(line)
        if match:
            items.append(
                build(
                    text=line,
                    category=CandidateCategory.PART,
                    confidence=1.0,
                    status=None,
                    normalized=None,
                    start=line_start,
                    end=line_end,
                )
            )
        match = _MATERIAL_LABEL_RE.match(line)
        if match:
            items.append(
                build(
                    text=line,
                    category=CandidateCategory.MATERIAL,
                    confidence=1.0,
                    status=None,
                    normalized=None,
                    start=line_start,
                    end=line_end,
                )
            )

        # 4. Bare TODO mentions.
        if _TODO_RE.search(line) and not _DECLARATION_RE.match(line):
            items.append(
                build(
                    text=line,
                    category=CandidateCategory.OPEN_ISSUE,
                    confidence=0.9,
                    status=None,
                    normalized=None,
                    start=line_start,
                    end=line_end,
                )
            )

        # Status inherited by measurements on this line: explicit
        # marker/declaration wins; otherwise conservative keywords
        # (MEASURED/PROPOSED only -- VERIFIED is never inferred).
        line_status = marker_status or declaration_status
        if line_status is None:
            if _MEASURED_KEYWORD_RE.search(line):
                line_status = InfoStatus.MEASURED
            elif _PROPOSED_KEYWORD_RE.search(line):
                line_status = InfoStatus.PROPOSED

        # 5. Measurements with explicit units.
        for match in _MEASUREMENT_RE.finditer(line):
            expression = match.group(0)
            unit_raw = match.group("unit")
            canonical_unit = _UNIT_CANONICAL[unit_raw.lower()]
            confidence = (
                0.8 if unit_raw.lower() in _AMBIGUOUS_UNITS else 1.0
            )
            start = line_start + match.start()
            end = line_start + match.end()
            items.append(
                build(
                    text=expression,
                    category=CandidateCategory.MEASUREMENT,
                    confidence=confidence,
                    status=line_status,
                    normalized={
                        "value": float(match.group("value")),
                        "unit": canonical_unit,
                    },
                    start=start,
                    end=end,
                )
            )

        # 6. URLs: identified, never fetched.
        for match in _URL_RE.finditer(line):
            url = match.group(0).rstrip(".,;:!?")
            start = line_start + match.start()
            end = start + len(url)
            items.append(
                build(
                    text=url,
                    category=CandidateCategory.RESEARCH,
                    confidence=1.0,
                    status=None,
                    normalized=None,
                    start=start,
                    end=end,
                )
            )

        return items


# ---------------------------------------------------------------------------
# LLM extension point (NOT implemented in this brick)
# ---------------------------------------------------------------------------


class LLMExtractor(Extractor):
    """Placeholder for a future model-backed extractor.

    This is an extension point only: Brick 4 wires no model backend,
    installs nothing, and makes no network calls. Attempting to use it
    raises honestly instead of pretending to work.
    """

    name: ClassVar[str] = "llm"
    version: ClassVar[int] = 1

    def extract_source(
        self, catalog: "SourceCatalog", record: SourceRecord
    ) -> ExtractionResult:
        raise ExtractionNotAvailableError(
            "LLM extraction is not implemented in Brick 4: no model "
            "backend is wired up and none may be added in this brick."
        )


EXTRACTOR_NAMES = ("deterministic", "llm")


def get_extractor(name: str) -> Extractor:
    """Return an extractor instance by name.

    ``"deterministic"`` is implemented; ``"llm"`` exists as an
    extension point and raises ExtractionNotAvailableError when used.
    """
    if name == DeterministicExtractor.name:
        return DeterministicExtractor()
    if name == LLMExtractor.name:
        return LLMExtractor()
    raise ExtractionError(
        f"unknown extractor: {name!r} (known: {', '.join(EXTRACTOR_NAMES)})"
    )
