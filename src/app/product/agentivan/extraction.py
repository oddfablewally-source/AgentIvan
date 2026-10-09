"""
Source ingestion + structured extraction layer for AgentIvan (Brick 4).

This is the first intelligence layer of AgentIvan, and it is an
*extraction* layer, not a truth layer. The critical distinction:

    RAW SOURCE  ->  INGESTION  ->  STRUCTURED CANDIDATE INFORMATION
                                          ->  LATER BUILD PACK PROCESSING

Extraction identifies what a source *appears to contain* and preserves
*where that information came from*. Nothing becomes canonical Build Pack
information merely because an extractor identified it. The canonical
Build Pack (``model.py``) is never modified by anything in this module.

Core rules enforced here:

- "the source says X" is never confused with "X is true".
- Every extracted item carries provenance back to its source:
  source_id, filename, heading/section, chunk id, character offsets
  (or the strongest available location when offsets are unreliable),
  and the original text. Provenance is never fabricated.
- Confidence ("how sure are we the source contains this?") and status
  ("what kind of information is this?", the Brick 1 InfoStatus
  vocabulary) are separate concepts. A confident extraction never
  upgrades a status. When the source does not establish a status, the
  item carries ``status=None`` rather than an invented one.
- Extraction is read-only: it never rewrites source files, normalizes
  source text destructively, alters SourceRecords, changes source
  hashes, or touches the Build Pack.
- Results are deterministic, serializable, relocatable JSON stored
  under ``<project-root>/extractions/``. They are a cache, not a second
  source of truth: deleting them loses nothing, because extraction can
  always be re-run from the original sources.
- Deterministic re-runs over unchanged sources produce equivalent
  results. Unique ids are content-derived (sha256); timestamps are
  metadata and are excluded from equivalence comparison.

Stdlib only. No network, no database, no LLM, no embeddings, no OCR.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .intake import utc_now_iso
from .statuses import InfoStatus

if TYPE_CHECKING:
    from .project import Project

__all__ = [
    "EXTRACTIONS_FORMAT_NAME",
    "EXTRACTIONS_FORMAT_VERSION",
    "CandidateCategory",
    "ExtractionMethod",
    "ExtractionOutcome",
    "ReviewState",
    "SourceLocation",
    "TextChunk",
    "ExtractedItem",
    "ExtractionResult",
    "ExtractionSet",
    "ExtractionStore",
    "ExtractionError",
    "ExtractionNotAvailableError",
    "chunk_text",
    "chunk_json",
    "make_extraction_id",
]

EXTRACTIONS_FORMAT_NAME = "agentivan-extractions"
EXTRACTIONS_FORMAT_VERSION = 1


class ExtractionError(ValueError):
    """Raised for malformed extraction data or store problems."""


class ExtractionNotAvailableError(ExtractionError):
    """Raised when a requested extractor cannot run (e.g. LLM, no backend)."""


# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------


class CandidateCategory(str, Enum):
    """Routing hints for extracted candidate information.

    These mirror the Build Pack structure loosely, but they are *not*
    Build Pack truth: they only suggest where a candidate might belong
    during later processing. The set is deliberately small.
    """

    PROJECT_IDENTITY = "project_identity"
    DONOR = "donor"
    DESIGN = "design"
    MEASUREMENT = "measurement"
    PART = "part"
    MATERIAL = "material"
    RESEARCH = "research"
    ENGINEERING = "engineering"
    CAD_FABRICATION = "cad_fabrication"
    BUILD_ASSEMBLY = "build_assembly"
    FINISHING = "finishing"
    TESTING_VERIFICATION = "testing_verification"
    COST = "cost"
    SAFETY_LEGAL = "safety_legal"
    OPEN_ISSUE = "open_issue"
    CHANGE_HISTORY = "change_history"
    GENERAL = "general"


class ExtractionMethod(str, Enum):
    """How an extracted item was produced."""

    MANUAL = "manual"  # recorded by a human reviewer
    DETERMINISTIC = "deterministic"  # rule-based extractor in this brick
    LLM = "llm"  # model-backed extractor (extension point; not implemented)


class ExtractionOutcome(str, Enum):
    """What happened when a source was offered to an extractor."""

    SUCCESS = "success"  # extraction ran; items may still be empty
    NO_EXTRACTION_AVAILABLE = (
        "no_extraction_available"  # honest refusal (image, binary, ...)
    )
    ERROR = "error"  # extraction failed; the failure is recorded, not hidden


class ReviewState(str, Enum):
    """Human review state of an extracted item. Starts UNREVIEWED."""

    UNREVIEWED = "unreviewed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


def _coerce_category(value: Any) -> CandidateCategory:
    if isinstance(value, CandidateCategory):
        return value
    if isinstance(value, str):
        try:
            return CandidateCategory(value)
        except ValueError:
            pass
    valid = ", ".join(c.value for c in CandidateCategory)
    raise ExtractionError(f"category must be one of [{valid}], got {value!r}")


def _coerce_method(value: Any) -> ExtractionMethod:
    if isinstance(value, ExtractionMethod):
        return value
    if isinstance(value, str):
        try:
            return ExtractionMethod(value)
        except ValueError:
            pass
    valid = ", ".join(m.value for m in ExtractionMethod)
    raise ExtractionError(f"method must be one of [{valid}], got {value!r}")


def _coerce_outcome(value: Any) -> ExtractionOutcome:
    if isinstance(value, ExtractionOutcome):
        return value
    if isinstance(value, str):
        try:
            return ExtractionOutcome(value)
        except ValueError:
            pass
    valid = ", ".join(o.value for o in ExtractionOutcome)
    raise ExtractionError(f"outcome must be one of [{valid}], got {value!r}")


def _coerce_review_state(value: Any) -> ReviewState:
    if isinstance(value, ReviewState):
        return value
    if isinstance(value, str):
        try:
            return ReviewState(value)
        except ValueError:
            pass
    valid = ", ".join(s.value for s in ReviewState)
    raise ExtractionError(f"review_state must be one of [{valid}], got {value!r}")


def _coerce_info_status(value: Any) -> InfoStatus | None:
    """An extraction status may be absent: None means the source did not
    establish one, and inventing one is forbidden."""
    if value is None:
        return None
    if isinstance(value, InfoStatus):
        return value
    if isinstance(value, str):
        try:
            return InfoStatus(value)
        except ValueError:
            pass
    valid = ", ".join(s.value for s in InfoStatus)
    raise ExtractionError(
        f"status must be null or one of [{valid}], got {value!r}"
    )


def _check_confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExtractionError(f"confidence must be a number, got {value!r}")
    confidence = float(value)
    if not 0.0 <= confidence <= 1.0:
        raise ExtractionError(
            f"confidence must be in [0.0, 1.0], got {value!r}"
        )
    return confidence


def _check_json_native(value: Any, what: str) -> None:
    """Normalized values must stay JSON-serializable."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ExtractionError(f"{what} must be finite, got {value!r}")
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _check_json_native(item, f"{what}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ExtractionError(f"{what} has non-string key {key!r}")
            _check_json_native(item, f"{what}[{key!r}]")
        return
    raise ExtractionError(
        f"{what} must be JSON-native, got {type(value).__name__}"
    )


def _require_non_empty_str(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExtractionError(f"{what} must be a non-empty string")
    return value


def _optional_str(value: Any, what: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ExtractionError(f"{what} must be a string or null")
    return value


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


@dataclass
class SourceLocation:
    """Where an extracted item came from. Never fabricated.

    Fields that cannot be established for a source type stay None: for
    JSON leaves there are no reliable character offsets, so the JSON
    path is stored instead; for references there is no file at all.
    """

    source_id: str
    source_filename: str | None = None
    heading: str | None = None
    chunk_id: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    json_path: str | None = None

    def __post_init__(self) -> None:
        self.source_id = _require_non_empty_str(self.source_id, "source_id")
        self.source_filename = _optional_str(
            self.source_filename, "source_filename"
        )
        self.heading = _optional_str(self.heading, "heading")
        self.chunk_id = _optional_str(self.chunk_id, "chunk_id")
        self.json_path = _optional_str(self.json_path, "json_path")
        for attr in ("char_start", "char_end"):
            value = getattr(self, attr)
            if value is not None and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ExtractionError(
                    f"{attr} must be a non-negative int or null"
                )
        if (
            self.char_start is not None
            and self.char_end is not None
            and self.char_end < self.char_start
        ):
            raise ExtractionError("char_end must not precede char_start")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_filename": self.source_filename,
            "heading": self.heading,
            "chunk_id": self.chunk_id,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "json_path": self.json_path,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "SourceLocation":
        if not isinstance(data, dict):
            raise ExtractionError("source location must be an object")
        known = {
            "source_id",
            "source_filename",
            "heading",
            "chunk_id",
            "char_start",
            "char_end",
            "json_path",
        }
        unknown = set(data) - known
        if unknown:
            raise ExtractionError(
                f"source location has unknown keys: {sorted(unknown)}"
            )
        try:
            return cls(
                source_id=data["source_id"],
                source_filename=data.get("source_filename"),
                heading=data.get("heading"),
                chunk_id=data.get("chunk_id"),
                char_start=data.get("char_start"),
                char_end=data.get("char_end"),
                json_path=data.get("json_path"),
            )
        except KeyError as exc:
            raise ExtractionError(
                f"source location is missing field: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# Text chunks
# ---------------------------------------------------------------------------


@dataclass
class TextChunk:
    """One deterministic extraction unit of source text.

    chunk_id is deterministic (source id + zero-padded index), so
    re-chunking unchanged text yields identical chunks. char_start /
    char_end are offsets into the original text; for JSON leaves they
    are None and json_path carries the location instead.
    """

    chunk_id: str
    source_id: str
    heading: str | None
    text: str
    char_start: int | None
    char_end: int | None
    json_path: str | None = None

    def __post_init__(self) -> None:
        self.chunk_id = _require_non_empty_str(self.chunk_id, "chunk_id")
        self.source_id = _require_non_empty_str(self.source_id, "source_id")
        self.heading = _optional_str(self.heading, "heading")
        if not isinstance(self.text, str):
            raise ExtractionError("chunk text must be a string")
        self.json_path = _optional_str(self.json_path, "json_path")
        for attr in ("char_start", "char_end"):
            value = getattr(self, attr)
            if value is not None and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ExtractionError(
                    f"chunk {attr} must be a non-negative int or null"
                )
        if (
            self.char_start is not None
            and self.char_end is not None
            and self.char_end < self.char_start
        ):
            raise ExtractionError("chunk char_end must not precede char_start")

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "source_id": self.source_id,
            "heading": self.heading,
            "text": self.text,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "json_path": self.json_path,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "TextChunk":
        if not isinstance(data, dict):
            raise ExtractionError("chunk must be an object")
        known = {
            "chunk_id",
            "source_id",
            "heading",
            "text",
            "char_start",
            "char_end",
            "json_path",
        }
        unknown = set(data) - known
        if unknown:
            raise ExtractionError(f"chunk has unknown keys: {sorted(unknown)}")
        try:
            return cls(
                chunk_id=data["chunk_id"],
                source_id=data["source_id"],
                heading=data.get("heading"),
                text=data["text"],
                char_start=data.get("char_start"),
                char_end=data.get("char_end"),
                json_path=data.get("json_path"),
            )
        except KeyError as exc:
            raise ExtractionError(f"chunk is missing field: {exc}") from exc


_HEADING_RE = re.compile(r"^[ ]{0,3}(#{1,6})[ \t]+(.*?)[ \t]*$")
_BLANK_RE = re.compile(r"^[ \t]*$")


def chunk_text(source_id: str, text: str) -> list[TextChunk]:
    """Split text into deterministic chunks at natural boundaries.

    Rules, in order:

    - A Markdown heading line (``#`` to ``######``) becomes its own
      chunk and sets the heading attributed to following chunks until
      the next heading.
    - Blank-line-separated paragraphs become chunks.
    - Character offsets always refer to the *original* text (no
      newline normalization; ``\\r\\n`` line breaks are preserved in
      offset accounting).

    Chunk ids are ``<source_id>:chunk:<zero-padded index>``. Empty or
    whitespace-only text yields no chunks.
    """
    if not isinstance(source_id, str) or not source_id.strip():
        raise ExtractionError("source_id must be a non-empty string")
    if not isinstance(text, str):
        raise ExtractionError("text must be a string")

    chunks: list[TextChunk] = []
    index = 0
    heading: str | None = None
    paragraph: list[tuple[int, int]] = []  # (line_start, line_end) spans
    pos = 0

    def flush_paragraph() -> None:
        nonlocal index
        if not paragraph:
            return
        start = paragraph[0][0]
        end = paragraph[-1][1]
        chunks.append(
            TextChunk(
                chunk_id=f"{source_id}:chunk:{index:04d}",
                source_id=source_id,
                heading=heading,
                text=text[start:end],
                char_start=start,
                char_end=end,
            )
        )
        index += 1
        paragraph.clear()

    for raw_line in text.splitlines(keepends=True):
        # Strip exactly one line break for matching; offsets account for it.
        if raw_line.endswith("\r\n"):
            core, break_len = raw_line[:-2], 2
        elif raw_line.endswith("\n") or raw_line.endswith("\r"):
            core, break_len = raw_line[:-1], 1
        else:
            core, break_len = raw_line, 0
        line_start = pos
        line_end = pos + len(core)
        pos += len(raw_line)

        match = _HEADING_RE.match(core)
        if match:
            flush_paragraph()
            heading_text = match.group(2).strip()
            heading = heading_text or None
            chunks.append(
                TextChunk(
                    chunk_id=f"{source_id}:chunk:{index:04d}",
                    source_id=source_id,
                    heading=heading,
                    text=core,
                    char_start=line_start,
                    char_end=line_end,
                )
            )
            index += 1
        elif _BLANK_RE.match(core):
            flush_paragraph()
        else:
            paragraph.append((line_start, line_end))
    flush_paragraph()
    return chunks


def _json_path_key(parent: str, key: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        return f"{parent}.{key}"
    return f"{parent}[{json.dumps(key, ensure_ascii=False)}]"


def chunk_json(source_id: str, value: Any) -> list[TextChunk]:
    """Split a parsed JSON value into one chunk per scalar leaf.

    Each chunk carries the leaf's JSON path (``$.key[0].nested``) as its
    heading and json_path. Character offsets are None: offsets into a
    re-serialized document would not be reliable provenance, so the
    path is stored instead. Object keys are visited in sorted order so
    chunking is deterministic.
    """
    if not isinstance(source_id, str) or not source_id.strip():
        raise ExtractionError("source_id must be a non-empty string")

    leaves: list[tuple[str, Any]] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key in sorted(node.keys()):
                if not isinstance(key, str):
                    raise ExtractionError("JSON object keys must be strings")
                walk(node[key], _json_path_key(path, key))
            return
        if isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")
            return
        if node is None or isinstance(node, (str, int, float, bool)):
            leaves.append((path, node))
            return
        raise ExtractionError(
            f"unsupported JSON value at {path}: {type(node).__name__}"
        )

    walk(value, "$")
    chunks = []
    for i, (path, leaf) in enumerate(leaves):
        rendered = leaf if isinstance(leaf, str) else json.dumps(
            leaf, ensure_ascii=False
        )
        chunks.append(
            TextChunk(
                chunk_id=f"{source_id}:json:{i:04d}",
                source_id=source_id,
                heading=path,
                text=rendered,
                char_start=None,
                char_end=None,
                json_path=path,
            )
        )
    return chunks


# ---------------------------------------------------------------------------
# Extracted items
# ---------------------------------------------------------------------------


def make_extraction_id(
    *,
    extractor_name: str,
    extractor_version: int,
    source_id: str,
    chunk_id: str | None,
    category: CandidateCategory,
    status: InfoStatus | None,
    confidence: float,
    method: ExtractionMethod,
    text: str,
    normalized: Any | None,
    char_start: int | None,
    char_end: int | None,
    json_path: str | None,
) -> str:
    """Deterministic content-derived id for an extracted item.

    Two runs over unchanged sources produce identical ids, so
    re-extraction is comparable and repeatable. Timestamps and review
    state are deliberately excluded: they are metadata, not identity.
    """
    if normalized is None:
        normalized_repr = "null"
    else:
        normalized_repr = json.dumps(
            normalized, sort_keys=True, ensure_ascii=False
        )
    canonical = "|".join(
        [
            extractor_name,
            str(extractor_version),
            source_id,
            chunk_id or "",
            category.value,
            status.value if status is not None else "",
            f"{confidence:.4f}",
            method.value,
            text,
            normalized_repr,
            str(char_start) if char_start is not None else "",
            str(char_end) if char_end is not None else "",
            json_path or "",
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class ExtractedItem:
    """One structured candidate found in a source.

    ``text`` is the original expression, verbatim. ``normalized`` is an
    optional JSON-native normalized representation (e.g. a measurement
    value + canonical unit); when present, ``text`` is still preserved.

    ``confidence`` answers "how confident are we that the source
    contains this information?" (0.0-1.0). ``status`` answers "what
    kind of information is this?" using the Brick 1 vocabulary, or None
    when the source does not establish one. The two are never mixed:
    high confidence never upgrades a status.
    """

    extraction_id: str
    source_id: str
    location: SourceLocation
    text: str
    category: CandidateCategory
    confidence: float
    method: ExtractionMethod
    normalized: Any | None = None
    status: InfoStatus | None = None
    context: str | None = None
    created_at: str = ""
    review_state: ReviewState = ReviewState.UNREVIEWED

    def __post_init__(self) -> None:
        self.extraction_id = _require_non_empty_str(
            self.extraction_id, "extraction_id"
        )
        self.source_id = _require_non_empty_str(self.source_id, "source_id")
        if not isinstance(self.location, SourceLocation):
            raise ExtractionError("location must be a SourceLocation")
        if not isinstance(self.text, str) or not self.text:
            raise ExtractionError("text must be a non-empty string")
        self.category = _coerce_category(self.category)
        self.confidence = _check_confidence(self.confidence)
        self.method = _coerce_method(self.method)
        if self.normalized is not None:
            _check_json_native(self.normalized, "normalized")
        self.status = _coerce_info_status(self.status)
        self.context = _optional_str(self.context, "context")
        if not isinstance(self.created_at, str) or not self.created_at:
            raise ExtractionError("created_at must be a non-empty string")
        self.review_state = _coerce_review_state(self.review_state)
        if self.location.source_id != self.source_id:
            raise ExtractionError(
                "location.source_id must match the item's source_id"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "extraction_id": self.extraction_id,
            "source_id": self.source_id,
            "location": self.location.to_dict(),
            "text": self.text,
            "category": self.category.value,
            "confidence": self.confidence,
            "method": self.method.value,
            "normalized": self.normalized,
            "status": self.status.value if self.status is not None else None,
            "context": self.context,
            "created_at": self.created_at,
            "review_state": self.review_state.value,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ExtractedItem":
        if not isinstance(data, dict):
            raise ExtractionError("extracted item must be an object")
        known = {
            "extraction_id",
            "source_id",
            "location",
            "text",
            "category",
            "confidence",
            "method",
            "normalized",
            "status",
            "context",
            "created_at",
            "review_state",
        }
        unknown = set(data) - known
        if unknown:
            raise ExtractionError(
                f"extracted item has unknown keys: {sorted(unknown)}"
            )
        try:
            return cls(
                extraction_id=data["extraction_id"],
                source_id=data["source_id"],
                location=SourceLocation.from_dict(data["location"]),
                text=data["text"],
                category=data["category"],
                confidence=data["confidence"],
                method=data["method"],
                normalized=data.get("normalized"),
                status=data.get("status"),
                context=data.get("context"),
                created_at=data["created_at"],
                review_state=data.get("review_state", ReviewState.UNREVIEWED),
            )
        except KeyError as exc:
            raise ExtractionError(
                f"extracted item is missing field: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# Per-source extraction results
# ---------------------------------------------------------------------------


@dataclass
class ExtractionResult:
    """The outcome of offering one source to an extractor.

    ``items`` is empty both when nothing was found and when extraction
    is honestly unavailable; ``outcome`` tells the two apart. ``error``
    carries the failure message when ``outcome`` is ERROR -- failures
    are recorded, never swallowed.
    """

    source_id: str
    outcome: ExtractionOutcome
    items: list[ExtractedItem] = field(default_factory=list)
    error: str | None = None
    note: str | None = None
    extracted_at: str = ""
    extractor_name: str = ""
    extractor_version: int = 1

    def __post_init__(self) -> None:
        self.source_id = _require_non_empty_str(self.source_id, "source_id")
        self.outcome = _coerce_outcome(self.outcome)
        if not isinstance(self.items, list):
            raise ExtractionError("items must be a list")
        for item in self.items:
            if not isinstance(item, ExtractedItem):
                raise ExtractionError("items must contain ExtractedItems")
            if item.source_id != self.source_id:
                raise ExtractionError(
                    "every item must belong to the result's source_id"
                )
        # Deterministic item order, independent of extraction pass order.
        self.items.sort(key=lambda item: item.extraction_id)
        self.error = _optional_str(self.error, "error")
        self.note = _optional_str(self.note, "note")
        if not isinstance(self.extracted_at, str) or not self.extracted_at:
            raise ExtractionError("extracted_at must be a non-empty string")
        self.extractor_name = _require_non_empty_str(
            self.extractor_name, "extractor_name"
        )
        if (
            not isinstance(self.extractor_version, int)
            or isinstance(self.extractor_version, bool)
            or self.extractor_version < 1
        ):
            raise ExtractionError("extractor_version must be a positive int")
        if self.outcome is ExtractionOutcome.ERROR and not self.error:
            raise ExtractionError("ERROR outcomes must carry an error message")
        if self.outcome is not ExtractionOutcome.ERROR and self.error:
            raise ExtractionError("only ERROR outcomes carry an error message")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "outcome": self.outcome.value,
            "items": [item.to_dict() for item in self.items],
            "error": self.error,
            "note": self.note,
            "extracted_at": self.extracted_at,
            "extractor_name": self.extractor_name,
            "extractor_version": self.extractor_version,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ExtractionResult":
        if not isinstance(data, dict):
            raise ExtractionError("extraction result must be an object")
        known = {
            "source_id",
            "outcome",
            "items",
            "error",
            "note",
            "extracted_at",
            "extractor_name",
            "extractor_version",
        }
        unknown = set(data) - known
        if unknown:
            raise ExtractionError(
                f"extraction result has unknown keys: {sorted(unknown)}"
            )
        try:
            raw_items = data.get("items", [])
            if not isinstance(raw_items, list):
                raise ExtractionError("items must be a list")
            return cls(
                source_id=data["source_id"],
                outcome=data["outcome"],
                items=[ExtractedItem.from_dict(item) for item in raw_items],
                error=data.get("error"),
                note=data.get("note"),
                extracted_at=data["extracted_at"],
                extractor_name=data["extractor_name"],
                extractor_version=data.get("extractor_version", 1),
            )
        except KeyError as exc:
            raise ExtractionError(
                f"extraction result is missing field: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# Project-level extraction set (the stored representation)
# ---------------------------------------------------------------------------


def _strip_timestamps(value: Any) -> Any:
    """Remove run metadata for equivalence comparison."""
    if isinstance(value, dict):
        return {
            key: _strip_timestamps(item)
            for key, item in value.items()
            if key not in ("created_at", "extracted_at")
        }
    if isinstance(value, list):
        return [_strip_timestamps(item) for item in value]
    return value


@dataclass
class ExtractionSet:
    """All extraction results for one project.

    Serializable, deterministic (results ordered by source_id), and
    relocatable: it references sources by id, never by absolute path.
    A cache, not a second source of truth.
    """

    project_id: str
    results: list[ExtractionResult] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.project_id = _require_non_empty_str(self.project_id, "project_id")
        if not isinstance(self.results, list):
            raise ExtractionError("results must be a list")
        for result in self.results:
            if not isinstance(result, ExtractionResult):
                raise ExtractionError("results must contain ExtractionResults")
        seen = set()
        for result in self.results:
            if result.source_id in seen:
                raise ExtractionError(
                    f"duplicate result for source_id: {result.source_id!r}"
                )
            seen.add(result.source_id)
        self.results.sort(key=lambda result: result.source_id)

    def for_source(self, source_id: str) -> ExtractionResult | None:
        for result in self.results:
            if result.source_id == source_id:
                return result
        return None

    def all_items(self) -> list[ExtractedItem]:
        items = []
        for result in self.results:
            items.extend(result.items)
        return items

    def equivalent(self, other: "ExtractionSet") -> bool:
        """True when two runs produced the same extraction content.

        Timestamps (created_at / extracted_at) are run metadata, not
        content: they are excluded so identical extractions compare
        equal across runs. Use this -- not raw dict equality -- to
        verify repeatability.
        """
        if not isinstance(other, ExtractionSet):
            return NotImplemented
        return _strip_timestamps(self.to_dict()) == _strip_timestamps(
            other.to_dict()
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": EXTRACTIONS_FORMAT_NAME,
            "format_version": EXTRACTIONS_FORMAT_VERSION,
            "project_id": self.project_id,
            "results": [result.to_dict() for result in self.results],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Any) -> "ExtractionSet":
        if not isinstance(data, dict):
            raise ExtractionError("extraction set must be an object")
        known = {"format", "format_version", "project_id", "results"}
        unknown = set(data) - known
        if unknown:
            raise ExtractionError(
                f"extraction set has unknown keys: {sorted(unknown)}"
            )
        if "format" in data and data["format"] != EXTRACTIONS_FORMAT_NAME:
            raise ExtractionError(
                f"unknown extractions format: {data['format']!r}"
            )
        if (
            "format_version" in data
            and data["format_version"] != EXTRACTIONS_FORMAT_VERSION
        ):
            raise ExtractionError(
                "unsupported extractions format_version: "
                f"{data['format_version']!r}"
            )
        try:
            raw_results = data.get("results", [])
            if not isinstance(raw_results, list):
                raise ExtractionError("results must be a list")
            return cls(
                project_id=data["project_id"],
                results=[
                    ExtractionResult.from_dict(item) for item in raw_results
                ],
            )
        except KeyError as exc:
            raise ExtractionError(
                f"extraction set is missing field: {exc}"
            ) from exc

    @classmethod
    def from_json(cls, text: str) -> "ExtractionSet":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ExtractionError(f"invalid JSON: {exc}") from None
        return cls.from_dict(data)


# ---------------------------------------------------------------------------
# Project-level store
# ---------------------------------------------------------------------------


class ExtractionStore:
    """Runs extractors over a project and persists the results.

    Storage lives under ``<project-root>/extractions/extractions.json``
    (pathlib only; platform-neutral). The store writes nothing anywhere
    else: running extraction never modifies sources, project.json, or
    the Build Pack. Like the Brick 3 catalog, extraction reads live
    project state; unlike the catalog, results are cached on disk so
    they can be inspected without re-running.
    """

    def __init__(self, project: "Project") -> None:
        self._project = project

    @property
    def extractions_dir(self) -> Path:
        return self._project.root / "extractions"

    @property
    def extractions_path(self) -> Path:
        return self.extractions_dir / "extractions.json"

    def run(self, extractor: Any = None) -> ExtractionSet:
        """Extract every source in deterministic catalog order.

        A failure in one source becomes an ERROR result for that source
        only; it never aborts the run or destroys the project.
        """
        if extractor is None:
            from .extractors import DeterministicExtractor

            extractor = DeterministicExtractor()
        catalog = self._project.catalog
        results = []
        for record in catalog.list():
            try:
                results.append(extractor.extract_source(catalog, record))
            except Exception as exc:  # one bad source must not kill the run
                results.append(
                    ExtractionResult(
                        source_id=record.source_id,
                        outcome=ExtractionOutcome.ERROR,
                        items=[],
                        error=f"{type(exc).__name__}: {exc}",
                        extracted_at=utc_now_iso(),
                        extractor_name=getattr(
                            extractor, "name", type(extractor).__name__
                        ),
                        extractor_version=int(
                            getattr(extractor, "version", 1)
                        ),
                    )
                )
        return ExtractionSet(
            project_id=self._project.project_id, results=results
        )

    def save(self, extraction_set: ExtractionSet) -> Path:
        """Write results deterministically. Only touches extractions/."""
        from .project import dump_project_json

        if not isinstance(extraction_set, ExtractionSet):
            raise ExtractionError("can only save an ExtractionSet")
        if extraction_set.project_id != self._project.project_id:
            raise ExtractionError(
                "extraction set belongs to a different project"
            )
        try:
            self.extractions_dir.mkdir(parents=True, exist_ok=True)
            self.extractions_path.write_bytes(
                dump_project_json(extraction_set.to_dict())
            )
        except OSError as exc:
            raise ExtractionError(
                f"extractions could not be saved: {exc}"
            ) from exc
        return self.extractions_path

    def load(self) -> ExtractionSet:
        """Read stored results. Raises ExtractionError when missing."""
        try:
            raw = self.extractions_path.read_bytes()
        except OSError as exc:
            raise ExtractionError(
                f"no stored extractions: {exc}"
            ) from exc
        try:
            extraction_set = ExtractionSet.from_json(raw.decode("utf-8"))
        except (UnicodeDecodeError, ExtractionError) as exc:
            raise ExtractionError(
                f"stored extractions are corrupted: {exc}"
            ) from exc
        if extraction_set.project_id != self._project.project_id:
            raise ExtractionError(
                "stored extractions belong to a different project"
            )
        return extraction_set

    def exists(self) -> bool:
        return self.extractions_path.is_file()

    def clear(self) -> None:
        """Delete stored results. Sources are untouched; re-run to rebuild."""
        try:
            self.extractions_path.unlink(missing_ok=True)
        except OSError as exc:
            raise ExtractionError(
                f"stored extractions could not be deleted: {exc}"
            ) from exc
