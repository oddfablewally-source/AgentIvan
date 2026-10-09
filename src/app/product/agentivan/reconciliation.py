"""Brick 5 — candidate reconciliation and Build Pack assembly.

Brick 4 turns raw sources into *extracted candidates* ("the source says
X"). Brick 1 defines the *canonical Build Pack* ("X is true for this
project"). This module is the controlled layer between them:

    EXTRACTED CANDIDATES
            |
     RECONCILIATION        <- explicit review decisions, never automatic
            |
     CANONICAL BUILD PACK  <- Brick 1 model, populated only by decision

The critical rule is: EXTRACTION IS NOT TRUTH. Nothing reaches the
canonical Build Pack without an explicit, recorded reconciliation
decision. Confidence scores, phrasing, and repetition are never treated
as permission to accept a candidate.

Candidate lifecycle (explicit states, never deletion):

    candidate      extracted, awaiting review
    accepted       reviewed and applied to the Build Pack
    rejected       reviewed and deliberately not accepted (reason kept)
    deferred       reviewed but left unresolved (reason kept); non-terminal,
                   a later review may still accept, reject, or contradict it
    duplicate      reviewed; identical information already in the pack
    contradicted   conflicts with existing pack content; recorded, not
                   silently replaced

Contradictions carry their own explicit resolution lifecycle:
UNRESOLVED -> RESOLVED via ``keep_existing`` or ``accept_incoming``.
There is no "auto resolve". Both sides' provenance always survives.

Design notes:

- Read-only inspection never mutates the Build Pack.
- Statuses are preserved verbatim: accepting a PROPOSED candidate
  records it as PROPOSED, never promotes it to KNOWN.
- Conflict identification is reviewer-driven: the deterministic layer
  detects exact duplicates, but it never infers that two different
  values describe the same project fact. A human (or an explicit caller)
  names the conflict via ``flag_contradiction``.
- Project identity can never be changed automatically: accepting a
  project-identity candidate raises ``ReconciliationError``.
- Measurements keep their original expression, extracted value, unit,
  normalized form, provenance, and status. No new engineering
  calculations are performed.
- Every accepted change appends a Brick 1 ``Revision`` (what changed,
  why, what source caused it, when). History is never rewritten.
- Storage is deterministic, human-inspectable JSON under the existing
  project layout (``<root>/buildpack/buildpack.json`` and
  ``<root>/reconciliation/reconciliation.json``). No database, no
  network, no LLM, no new dependencies. pathlib only, so Linux and
  Windows behave identically.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .extraction import (
    CandidateCategory,
    ExtractionMethod,
    ExtractionSet,
    SourceLocation,
)
from .intake import utc_now_iso
from .model import (
    BuildPack,
    BuildPackError,
    BuildRecord,
    ProjectIdentity,
    Provenance,
    Revision,
)
from .statuses import InfoStatus, ResolutionStatus

if TYPE_CHECKING:
    from .project import Project

RECONCILIATION_FORMAT_NAME = "agentivan-reconciliation"
RECONCILIATION_FORMAT_VERSION = 1

BUILDPACK_FILENAME = "buildpack.json"
RECONCILIATION_FILENAME = "reconciliation.json"

# Resolution choices for an explicit contradiction resolution. There is
# deliberately no third option: "auto resolve" does not exist.
KEEP_EXISTING = "keep_existing"
ACCEPT_INCOMING = "accept_incoming"
RESOLUTION_CHOICES = (KEEP_EXISTING, ACCEPT_INCOMING)


class ReconciliationError(ValueError):
    """Raised for any invalid reconciliation operation."""


class ReconciliationState(str, Enum):
    """Explicit lifecycle states for an extracted candidate."""

    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    DUPLICATE = "duplicate"
    CONTRADICTED = "contradicted"


# ---------------------------------------------------------------------------
# Small validation helpers (mirroring the codebase's conventions)
# ---------------------------------------------------------------------------


def _require_non_empty_str(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReconciliationError(f"{what} must be a non-empty string")
    return value.strip()


def _optional_str(value: Any, what: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ReconciliationError(f"{what} must be a string or null")
    return value


def _check_iso_datetime(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReconciliationError(f"{what} must be an ISO-8601 datetime string")
    return value


def _check_json_native(value: Any, what: str) -> None:
    if value is None or isinstance(value, (bool, int, float, str)):
        return
    if isinstance(value, list):
        for item in value:
            _check_json_native(item, what)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ReconciliationError(
                    f"{what} must use string keys, got {key!r}"
                )
            _check_json_native(item, what)
        return
    raise ReconciliationError(
        f"{what} must be JSON-native, got {type(value).__name__}"
    )


def _coerce_state(value: Any) -> ReconciliationState:
    try:
        return ReconciliationState(value)
    except ValueError:
        raise ReconciliationError(
            f"unknown reconciliation state: {value!r}"
        ) from None


def _coerce_info_status(value: Any) -> InfoStatus | None:
    if value is None:
        return None
    try:
        return InfoStatus(value)
    except ValueError:
        raise ReconciliationError(
            f"unknown information status: {value!r}"
        ) from None


def _coerce_resolution_status(value: Any) -> ResolutionStatus:
    try:
        return ResolutionStatus(value)
    except ValueError:
        raise ReconciliationError(
            f"unknown resolution status: {value!r}"
        ) from None


def _deterministic_id(prefix: str, *parts: str) -> str:
    """Content-derived identifier: identical inputs -> identical ids.

    No randomness, no timestamps: ids are stable across runs, machines,
    and relocations, so repeatability comparisons stay meaningful.
    """
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}{digest[:16]}"


# ---------------------------------------------------------------------------
# Deterministic category -> Build Pack section mapping (brief section 9)
# ---------------------------------------------------------------------------

# Each mapped category names the Brick 1 section it populates and the
# single section field that carries the candidate's original expression.
# Only fields allowed by the Brick 1 section schemas are used; the Build
# Pack schema itself is not redesigned.
_CATEGORY_SECTION_FIELD: dict[CandidateCategory, tuple[str, str]] = {
    CandidateCategory.DONOR: ("donor", "notes"),
    CandidateCategory.DESIGN: ("design", "concept_notes"),
    CandidateCategory.MEASUREMENT: ("measurements", "measurement"),
    CandidateCategory.PART: ("parts", "name"),
    CandidateCategory.MATERIAL: ("finishing", "material"),
    CandidateCategory.RESEARCH: ("research", "subject"),
    CandidateCategory.ENGINEERING: ("engineering", "calculation"),
    CandidateCategory.CAD_FABRICATION: ("cad", "component"),
    CandidateCategory.BUILD_ASSEMBLY: ("build", "task"),
    CandidateCategory.FINISHING: ("finishing", "material"),
    CandidateCategory.TESTING_VERIFICATION: ("testing", "item"),
    CandidateCategory.COST: ("cost", "item"),
    CandidateCategory.SAFETY_LEGAL: ("safety", "issue"),
    CandidateCategory.OPEN_ISSUE: ("open_issues", "question"),
}

# Public deterministic mapping. Categories with no safe mapping resolve
# to None: those candidates must be deferred or rejected explicitly; the
# layer never invents a new Build Pack field for them. Project identity
# is special-cased separately: it can never be applied automatically.
CATEGORY_SECTION_MAP: dict[CandidateCategory, str | None] = {
    category: section
    for category, (section, _field) in _CATEGORY_SECTION_FIELD.items()
}
for _unmapped in (
    CandidateCategory.PROJECT_IDENTITY,
    CandidateCategory.CHANGE_HISTORY,
    CandidateCategory.GENERAL,
):
    CATEGORY_SECTION_MAP[_unmapped] = None


# ---------------------------------------------------------------------------
# Reconciled candidate
# ---------------------------------------------------------------------------


@dataclass
class ReconciledCandidate:
    """One extracted candidate under reconciliation review.

    ``state`` is the explicit lifecycle state. ``status`` is the Brick 4
    extraction status, preserved verbatim; it is never upgraded by
    acceptance, and ``None`` (the source established nothing) stays
    ``None`` until acceptance, where the conservative ``UNKNOWN``
    representation is used for the mandatory BuildRecord status.
    Extraction confidence is recorded for inspection only: it is never
    consulted by any decision operation.
    """

    reconciliation_id: str
    extraction_id: str
    source_id: str
    category: CandidateCategory
    text: str
    method: ExtractionMethod
    state: ReconciliationState = ReconciliationState.CANDIDATE
    status: InfoStatus | None = None
    normalized: Any | None = None
    location: SourceLocation | None = None
    section: str | None = None
    confidence: float | None = None
    decision_note: str | None = None
    decided_at: str | None = None
    build_record_id: str | None = None
    duplicate_of_record_id: str | None = None
    contradiction_id: str | None = None

    def __post_init__(self) -> None:
        self.reconciliation_id = _require_non_empty_str(
            self.reconciliation_id, "reconciliation_id"
        )
        self.extraction_id = _require_non_empty_str(
            self.extraction_id, "extraction_id"
        )
        self.source_id = _require_non_empty_str(self.source_id, "source_id")
        try:
            self.category = CandidateCategory(self.category)
        except ValueError:
            raise ReconciliationError(
                f"unknown candidate category: {self.category!r}"
            ) from None
        self.text = _require_non_empty_str(self.text, "text")
        try:
            self.method = ExtractionMethod(self.method)
        except ValueError:
            raise ReconciliationError(
                f"unknown extraction method: {self.method!r}"
            ) from None
        self.state = _coerce_state(self.state)
        self.status = _coerce_info_status(self.status)
        _check_json_native(self.normalized, "normalized")
        if self.location is not None and not isinstance(
            self.location, SourceLocation
        ):
            raise ReconciliationError("location must be a SourceLocation")
        if self.section is not None:
            self.section = _require_non_empty_str(self.section, "section")
        if self.confidence is not None:
            if (
                isinstance(self.confidence, bool)
                or not isinstance(self.confidence, (int, float))
                or not 0.0 <= float(self.confidence) <= 1.0
            ):
                raise ReconciliationError(
                    "confidence must be a number in [0, 1]"
                )
            self.confidence = float(self.confidence)
        self.decision_note = _optional_str(
            self.decision_note, "decision_note"
        )
        if self.decided_at is not None:
            self.decided_at = _check_iso_datetime(
                self.decided_at, "decided_at"
            )
        self.build_record_id = _optional_str(
            self.build_record_id, "build_record_id"
        )
        self.duplicate_of_record_id = _optional_str(
            self.duplicate_of_record_id, "duplicate_of_record_id"
        )
        self.contradiction_id = _optional_str(
            self.contradiction_id, "contradiction_id"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "reconciliation_id": self.reconciliation_id,
            "extraction_id": self.extraction_id,
            "source_id": self.source_id,
            "category": self.category.value,
            "text": self.text,
            "method": self.method.value,
            "state": self.state.value,
            "status": self.status.value if self.status is not None else None,
            "normalized": self.normalized,
            "location": (
                self.location.to_dict() if self.location is not None else None
            ),
            "section": self.section,
            "confidence": self.confidence,
            "decision_note": self.decision_note,
            "decided_at": self.decided_at,
            "build_record_id": self.build_record_id,
            "duplicate_of_record_id": self.duplicate_of_record_id,
            "contradiction_id": self.contradiction_id,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ReconciledCandidate":
        if not isinstance(data, dict):
            raise ReconciliationError("candidate must be an object")
        known = {
            "reconciliation_id",
            "extraction_id",
            "source_id",
            "category",
            "text",
            "method",
            "state",
            "status",
            "normalized",
            "location",
            "section",
            "confidence",
            "decision_note",
            "decided_at",
            "build_record_id",
            "duplicate_of_record_id",
            "contradiction_id",
        }
        unknown = set(data) - known
        if unknown:
            raise ReconciliationError(
                f"candidate has unknown keys: {sorted(unknown)}"
            )
        location = data.get("location")
        return cls(
            reconciliation_id=data.get("reconciliation_id"),
            extraction_id=data.get("extraction_id"),
            source_id=data.get("source_id"),
            category=data.get("category"),
            text=data.get("text"),
            method=data.get("method"),
            state=data.get("state", ReconciliationState.CANDIDATE.value),
            status=data.get("status"),
            normalized=data.get("normalized"),
            location=(
                SourceLocation.from_dict(location)
                if location is not None
                else None
            ),
            section=data.get("section"),
            confidence=data.get("confidence"),
            decision_note=data.get("decision_note"),
            decided_at=data.get("decided_at"),
            build_record_id=data.get("build_record_id"),
            duplicate_of_record_id=data.get("duplicate_of_record_id"),
            contradiction_id=data.get("contradiction_id"),
        )


# ---------------------------------------------------------------------------
# Candidate-vs-pack contradiction
# ---------------------------------------------------------------------------


@dataclass
class ReconciliationContradiction:
    """A candidate that conflicts with existing Build Pack content.

    Distinct from Brick 1's ``Contradiction`` (a conflict between two
    canonical records): one side here is a not-yet-accepted candidate.
    Both sides' provenance is preserved, the existing record is never
    touched by flagging, and resolution is an explicit operation with
    only two honest outcomes: keep the existing information, or accept
    the incoming information. An unresolved contradiction stays
    unresolved.
    """

    contradiction_id: str
    candidate_id: str
    section: str | None
    affected_field: str
    description: str
    candidate_provenance: dict[str, Any]
    existing_record_id: str | None = None
    existing_provenance: dict[str, Any] | None = None
    state: ResolutionStatus = ResolutionStatus.UNRESOLVED
    resolution: str | None = None
    resolution_note: str | None = None
    resolved_at: str | None = None

    def __post_init__(self) -> None:
        self.contradiction_id = _require_non_empty_str(
            self.contradiction_id, "contradiction_id"
        )
        self.candidate_id = _require_non_empty_str(
            self.candidate_id, "candidate_id"
        )
        if self.section is not None:
            self.section = _require_non_empty_str(self.section, "section")
        self.affected_field = _require_non_empty_str(
            self.affected_field, "affected_field"
        )
        self.description = _require_non_empty_str(
            self.description, "description"
        )
        if not isinstance(self.candidate_provenance, dict):
            raise ReconciliationError(
                "candidate_provenance must be an object"
            )
        _check_json_native(
            self.candidate_provenance, "candidate_provenance"
        )
        self.existing_record_id = _optional_str(
            self.existing_record_id, "existing_record_id"
        )
        if self.existing_provenance is not None:
            if not isinstance(self.existing_provenance, dict):
                raise ReconciliationError(
                    "existing_provenance must be an object or null"
                )
            _check_json_native(
                self.existing_provenance, "existing_provenance"
            )
        self.state = _coerce_resolution_status(self.state)
        if self.resolution is not None:
            if self.resolution not in RESOLUTION_CHOICES:
                raise ReconciliationError(
                    f"resolution must be one of {RESOLUTION_CHOICES}, "
                    f"got {self.resolution!r}"
                )
        if self.state is ResolutionStatus.RESOLVED and self.resolution is None:
            raise ReconciliationError(
                "a resolved contradiction must record its resolution"
            )
        self.resolution_note = _optional_str(
            self.resolution_note, "resolution_note"
        )
        if self.resolved_at is not None:
            self.resolved_at = _check_iso_datetime(
                self.resolved_at, "resolved_at"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contradiction_id": self.contradiction_id,
            "candidate_id": self.candidate_id,
            "section": self.section,
            "affected_field": self.affected_field,
            "description": self.description,
            "candidate_provenance": self.candidate_provenance,
            "existing_record_id": self.existing_record_id,
            "existing_provenance": self.existing_provenance,
            "state": self.state.value,
            "resolution": self.resolution,
            "resolution_note": self.resolution_note,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ReconciliationContradiction":
        if not isinstance(data, dict):
            raise ReconciliationError("contradiction must be an object")
        known = {
            "contradiction_id",
            "candidate_id",
            "section",
            "affected_field",
            "description",
            "candidate_provenance",
            "existing_record_id",
            "existing_provenance",
            "state",
            "resolution",
            "resolution_note",
            "resolved_at",
        }
        unknown = set(data) - known
        if unknown:
            raise ReconciliationError(
                f"contradiction has unknown keys: {sorted(unknown)}"
            )
        return cls(
            contradiction_id=data.get("contradiction_id"),
            candidate_id=data.get("candidate_id"),
            section=data.get("section"),
            affected_field=data.get("affected_field"),
            description=data.get("description"),
            candidate_provenance=data.get("candidate_provenance"),
            existing_record_id=data.get("existing_record_id"),
            existing_provenance=data.get("existing_provenance"),
            state=data.get("state", ResolutionStatus.UNRESOLVED.value),
            resolution=data.get("resolution"),
            resolution_note=data.get("resolution_note"),
            resolved_at=data.get("resolved_at"),
        )


# ---------------------------------------------------------------------------
# Reconciliation state (persisted)
# ---------------------------------------------------------------------------


@dataclass
class Reconciliation:
    """All reconciliation decisions for one project.

    Persisted as deterministic JSON. Candidates and contradictions are
    ordered by id so serialization is stable across runs and platforms.
    """

    project_id: str
    candidates: list[ReconciledCandidate] = field(default_factory=list)
    contradictions: list[ReconciliationContradiction] = field(
        default_factory=list
    )

    def __post_init__(self) -> None:
        self.project_id = _require_non_empty_str(
            self.project_id, "project_id"
        )
        if not isinstance(self.candidates, list) or not all(
            isinstance(c, ReconciledCandidate) for c in self.candidates
        ):
            raise ReconciliationError(
                "candidates must be a list of ReconciledCandidate"
            )
        if not isinstance(self.contradictions, list) or not all(
            isinstance(c, ReconciliationContradiction)
            for c in self.contradictions
        ):
            raise ReconciliationError(
                "contradictions must be a list of ReconciliationContradiction"
            )
        seen: set[str] = set()
        for candidate in self.candidates:
            if candidate.reconciliation_id in seen:
                raise ReconciliationError(
                    f"duplicate reconciliation_id: "
                    f"{candidate.reconciliation_id!r}"
                )
            seen.add(candidate.reconciliation_id)
        seen_contradictions: set[str] = set()
        for contradiction in self.contradictions:
            if contradiction.contradiction_id in seen_contradictions:
                raise ReconciliationError(
                    f"duplicate contradiction_id: "
                    f"{contradiction.contradiction_id!r}"
                )
            seen_contradictions.add(contradiction.contradiction_id)
        self.candidates.sort(key=lambda c: c.reconciliation_id)
        self.contradictions.sort(key=lambda c: c.contradiction_id)

    def find_by_extraction_id(
        self, extraction_id: str
    ) -> ReconciledCandidate | None:
        for candidate in self.candidates:
            if candidate.extraction_id == extraction_id:
                return candidate
        return None

    def get_candidate(self, reconciliation_id: str) -> ReconciledCandidate:
        for candidate in self.candidates:
            if candidate.reconciliation_id == reconciliation_id:
                return candidate
        raise ReconciliationError(
            f"unknown candidate: {reconciliation_id!r}"
        )

    def get_contradiction(
        self, contradiction_id: str
    ) -> ReconciliationContradiction:
        for contradiction in self.contradictions:
            if contradiction.contradiction_id == contradiction_id:
                return contradiction
        raise ReconciliationError(
            f"unknown contradiction: {contradiction_id!r}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": RECONCILIATION_FORMAT_NAME,
            "format_version": RECONCILIATION_FORMAT_VERSION,
            "project_id": self.project_id,
            "candidates": [
                candidate.to_dict() for candidate in self.candidates
            ],
            "contradictions": [
                contradiction.to_dict()
                for contradiction in self.contradictions
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Any) -> "Reconciliation":
        if not isinstance(data, dict):
            raise ReconciliationError("reconciliation must be an object")
        known = {
            "format",
            "format_version",
            "project_id",
            "candidates",
            "contradictions",
        }
        unknown = set(data) - known
        if unknown:
            raise ReconciliationError(
                f"reconciliation has unknown keys: {sorted(unknown)}"
            )
        if "format" in data and data["format"] != RECONCILIATION_FORMAT_NAME:
            raise ReconciliationError(
                f"unknown reconciliation format: {data['format']!r}"
            )
        if (
            "format_version" in data
            and data["format_version"] != RECONCILIATION_FORMAT_VERSION
        ):
            raise ReconciliationError(
                "unsupported reconciliation format_version: "
                f"{data['format_version']!r}"
            )
        candidates_data = data.get("candidates") or []
        contradictions_data = data.get("contradictions") or []
        if not isinstance(candidates_data, list):
            raise ReconciliationError("candidates must be a list")
        if not isinstance(contradictions_data, list):
            raise ReconciliationError("contradictions must be a list")
        return cls(
            project_id=data.get("project_id"),
            candidates=[
                ReconciledCandidate.from_dict(item)
                for item in candidates_data
            ],
            contradictions=[
                ReconciliationContradiction.from_dict(item)
                for item in contradictions_data
            ],
        )

    @classmethod
    def from_json(cls, text: str) -> "Reconciliation":
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ReconciliationError(
                f"reconciliation is not valid JSON: {exc}"
            ) from exc
        return cls.from_dict(data)

    def equivalent(self, other: "Reconciliation") -> bool:
        """Material equality, ignoring decision timestamps.

        Two reconciliations built from identical sources, identical
        extraction results, and identical decisions are equivalent even
        though they were decided at different moments.
        """
        if not isinstance(other, Reconciliation):
            return NotImplemented
        return (
            _strip_candidate_timestamps(self.to_dict())
            == _strip_candidate_timestamps(other.to_dict())
        )


def _strip_candidate_timestamps(payload: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(payload))
    for candidate in payload.get("candidates", []):
        if isinstance(candidate, dict):
            candidate.pop("decided_at", None)
    for contradiction in payload.get("contradictions", []):
        if isinstance(contradiction, dict):
            contradiction.pop("resolved_at", None)
    return payload


def _strip_pack_volatile(payload: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(payload))
    identity = payload.get("identity")
    if isinstance(identity, dict):
        identity.pop("created", None)
        identity.pop("modified", None)
    for revision in payload.get("change_history", []):
        if isinstance(revision, dict):
            revision.pop("timestamp", None)
    return payload


def packs_equivalent(first: BuildPack, second: BuildPack) -> bool:
    """Material equality of two Build Packs, ignoring volatile timestamps.

    Identity creation/modification times and revision timestamps are
    metadata: identical reconciliation work performed at different
    moments must compare as equivalent.
    """
    if not isinstance(first, BuildPack) or not isinstance(second, BuildPack):
        raise ReconciliationError("packs_equivalent needs two BuildPacks")
    return _strip_pack_volatile(first.to_dict()) == _strip_pack_volatile(
        second.to_dict()
    )


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------


class ReconciliationStore:
    """Persists reconciliation decisions under ``<root>/reconciliation/``.

    Deterministic, human-inspectable JSON (pathlib only). The store
    touches nothing else: sources, the extraction cache, project.json,
    and the Build Pack are never modified by saving decisions.
    """

    def __init__(self, project: "Project") -> None:
        self._project = project

    @property
    def reconciliation_dir(self) -> Path:
        return self._project.root / "reconciliation"

    @property
    def reconciliation_path(self) -> Path:
        return self.reconciliation_dir / RECONCILIATION_FILENAME

    def save(self, reconciliation: Reconciliation) -> Path:
        """Write decisions deterministically. Only touches reconciliation/."""
        from .project import dump_project_json

        if not isinstance(reconciliation, Reconciliation):
            raise ReconciliationError("can only save a Reconciliation")
        if reconciliation.project_id != self._project.project_id:
            raise ReconciliationError(
                "reconciliation belongs to a different project"
            )
        try:
            self.reconciliation_dir.mkdir(parents=True, exist_ok=True)
            self.reconciliation_path.write_bytes(
                dump_project_json(reconciliation.to_dict())
            )
        except OSError as exc:
            raise ReconciliationError(
                f"reconciliation could not be saved: {exc}"
            ) from exc
        return self.reconciliation_path

    def load(self) -> Reconciliation:
        """Read stored decisions. Raises ReconciliationError when missing."""
        try:
            raw = self.reconciliation_path.read_bytes()
        except OSError:
            raise ReconciliationError(
                "no saved reconciliation for this project"
            ) from None
        try:
            reconciliation = Reconciliation.from_json(raw.decode("utf-8"))
        except ReconciliationError as exc:
            raise ReconciliationError(
                f"invalid reconciliation file: {exc}"
            ) from exc
        if reconciliation.project_id != self._project.project_id:
            raise ReconciliationError(
                "saved reconciliation belongs to a different project"
            )
        return reconciliation

    def load_or_empty(self) -> Reconciliation:
        """Load stored decisions, or start with an empty decision set."""
        if self.exists():
            return self.load()
        return Reconciliation(project_id=self._project.project_id)

    def exists(self) -> bool:
        return self.reconciliation_path.is_file()


class BuildPackStore:
    """Persists the canonical Build Pack under ``<root>/buildpack/``.

    The saved pack is the canonical current project representation
    (brief section 19). Raw sources, extraction results, and
    reconciliation history remain preserved alongside it, but they are
    never treated as the source of truth for current project state.
    """

    def __init__(self, project: "Project") -> None:
        self._project = project

    @property
    def buildpack_dir(self) -> Path:
        return self._project.root / "buildpack"

    @property
    def buildpack_path(self) -> Path:
        return self.buildpack_dir / BUILDPACK_FILENAME

    def save(self, pack: BuildPack) -> Path:
        """Validate then write the pack deterministically."""
        from .project import dump_project_json

        if not isinstance(pack, BuildPack):
            raise ReconciliationError("can only save a BuildPack")
        if pack.identity.project_id != self._project.project_id:
            raise ReconciliationError(
                "build pack belongs to a different project"
            )
        try:
            pack.validate()
        except BuildPackError as exc:
            raise ReconciliationError(
                f"build pack is invalid and was not saved: {exc}"
            ) from exc
        try:
            self.buildpack_dir.mkdir(parents=True, exist_ok=True)
            self.buildpack_path.write_bytes(
                dump_project_json(pack.to_dict())
            )
        except OSError as exc:
            raise ReconciliationError(
                f"build pack could not be saved: {exc}"
            ) from exc
        return self.buildpack_path

    def load(self) -> BuildPack:
        """Read the saved pack. Raises ReconciliationError when missing."""
        try:
            raw = self.buildpack_path.read_bytes()
        except OSError:
            raise ReconciliationError(
                "no saved build pack for this project"
            ) from None
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ReconciliationError(
                f"build pack is not valid JSON: {exc}"
            ) from exc
        try:
            pack = BuildPack.from_dict(data)
        except BuildPackError as exc:
            raise ReconciliationError(
                f"invalid build pack file: {exc}"
            ) from exc
        if pack.identity.project_id != self._project.project_id:
            raise ReconciliationError(
                "saved build pack belongs to a different project"
            )
        try:
            pack.validate()
        except BuildPackError as exc:
            raise ReconciliationError(
                f"saved build pack failed validation: {exc}"
            ) from exc
        return pack

    def load_or_create(self) -> BuildPack:
        """Load the saved pack, or create a fresh one from project identity.

        The fresh identity block is derived from the project's own
        identity and is never mutated by reconciliation afterwards:
        project identity cannot be silently changed (brief section 10).
        """
        if self.exists():
            return self.load()
        now = utc_now_iso()
        pack = BuildPack(
            identity=ProjectIdentity(
                name=self._project.name,
                project_id=self._project.project_id,
                description="Not recorded",
                intent="Not recorded",
                status="draft",
                created=now,
                modified=now,
                revision=0,
            )
        )
        pack.validate()
        return pack

    def exists(self) -> bool:
        return self.buildpack_path.is_file()


# ---------------------------------------------------------------------------
# Field building and provenance
# ---------------------------------------------------------------------------


def build_record_fields(candidate: ReconciledCandidate) -> dict[str, Any]:
    """Map a candidate onto its Build Pack section fields.

    The candidate's original expression is always preserved verbatim in
    the section's primary field. Measurements additionally carry the
    extracted numeric value and unit as separate fields, never as a
    replacement for the original expression, and never recalculated.
    """
    if not isinstance(candidate, ReconciledCandidate):
        raise ReconciliationError(
            "build_record_fields needs a ReconciledCandidate"
        )
    mapping = _CATEGORY_SECTION_FIELD.get(candidate.category)
    if mapping is None:
        raise ReconciliationError(
            f"category {candidate.category.value!r} has no safe Build Pack "
            "section mapping; defer or reject the candidate explicitly"
        )
    _section, field_name = mapping
    if candidate.category is CandidateCategory.MEASUREMENT:
        fields: dict[str, Any] = {"measurement": candidate.text}
        normalized = candidate.normalized
        if isinstance(normalized, dict):
            value = normalized.get("value")
            unit = normalized.get("unit")
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
            ):
                fields["value"] = value
            if isinstance(unit, str) and unit:
                fields["unit"] = unit
        return fields
    return {field_name: candidate.text}


def candidate_provenance(candidate: ReconciledCandidate) -> Provenance:
    """Build the BuildRecord provenance for an accepted candidate.

    Preserves the source, the original source text, and the full
    extraction linkage (extraction id, method, category, reconciliation
    id) so the chain SOURCE -> SOURCE RECORD -> EXTRACTION RESULT ->
    CANDIDATE -> RECONCILIATION DECISION -> BUILD PACK ENTRY stays
    recoverable without guessing.
    """
    location = candidate.location
    reference = candidate.source_id
    if location is not None and location.source_filename:
        reference = f"{candidate.source_id} ({location.source_filename})"
    return Provenance(
        source_type="source_extraction",
        source_reference=reference,
        source_text=candidate.text,
        notes=(
            f"extraction_id={candidate.extraction_id}; "
            f"method={candidate.method.value}; "
            f"category={candidate.category.value}; "
            f"reconciliation_id={candidate.reconciliation_id}"
        ),
    )


def _candidate_provenance_dict(
    candidate: ReconciledCandidate,
) -> dict[str, Any]:
    """Provenance snapshot for the contradiction record's candidate side."""
    return {
        "extraction_id": candidate.extraction_id,
        "method": candidate.method.value,
        "category": candidate.category.value,
        "status": (
            candidate.status.value if candidate.status is not None else None
        ),
        "source_id": candidate.source_id,
        "location": (
            candidate.location.to_dict()
            if candidate.location is not None
            else None
        ),
    }


# ---------------------------------------------------------------------------
# Reconciliation session
# ---------------------------------------------------------------------------


class ReconciliationSession:
    """Working session over a project's Build Pack and decisions.

    Usage::

        session = ReconciliationSession(project)
        session.import_candidates(project.extractions.load())
        session.accept(candidate_id, note="reviewed")
        session.reject(other_id, reason="out of scope")
        session.save()   # persists the Build Pack and the decisions

    All decision operations are explicit and recorded. Nothing is
    applied automatically, extraction confidence is never consulted,
    and read-only inspection never mutates the Build Pack.
    """

    def __init__(self, project: "Project") -> None:
        self._project = project
        self.pack: BuildPack = project.buildpack_store.load_or_create()
        self.reconciliation: Reconciliation = (
            project.reconciliation_store.load_or_empty()
        )

    # -- read-only inspection -----------------------------------------

    def get_candidate(self, reconciliation_id: str) -> ReconciledCandidate:
        """Inspect a candidate. Never mutates the Build Pack."""
        return self.reconciliation.get_candidate(reconciliation_id)

    def get_contradiction(
        self, contradiction_id: str
    ) -> ReconciliationContradiction:
        """Inspect a contradiction. Never mutates the Build Pack."""
        return self.reconciliation.get_contradiction(contradiction_id)

    def find_record(self, record_id: str) -> BuildRecord | None:
        """Find a Build Pack record by id across all sections."""
        for records in self.pack.sections.values():
            for record in records:
                if record.record_id == record_id:
                    return record
        return None

    def find_duplicate(
        self, candidate: ReconciledCandidate
    ) -> BuildRecord | None:
        """Conservatively detect an existing equivalent Build Pack record.

        A duplicate is the *same information* from a different source:
        same section, same category, and the same primary field value
        (case-insensitive, whitespace-normalized). Differing text is
        never treated as a duplicate, and never automatically treated
        as a contradiction either: it simply becomes a new candidate
        the reviewer can name as conflicting via ``flag_contradiction``.
        """
        if not isinstance(candidate, ReconciledCandidate):
            raise ReconciliationError(
                "find_duplicate needs a ReconciledCandidate"
            )
        mapping = _CATEGORY_SECTION_FIELD.get(candidate.category)
        if mapping is None:
            return None
        section, field_name = mapping
        wanted = candidate.text.casefold().strip()
        for record in self.pack.sections.get(section, []):
            existing = record.fields.get(field_name)
            if (
                isinstance(existing, str)
                and existing.casefold().strip() == wanted
            ):
                return record
        return None

    # -- importing candidates ------------------------------------------

    def import_candidates(
        self, extraction_set: ExtractionSet
    ) -> list[ReconciledCandidate]:
        """Register extracted items as reviewable candidates.

        Idempotent: items already imported (by extraction id) are
        skipped, so re-running an import never duplicates candidates.
        Importing mutates only reconciliation state, never the pack.
        """
        if not isinstance(extraction_set, ExtractionSet):
            raise ReconciliationError("can only import an ExtractionSet")
        if extraction_set.project_id != self._project.project_id:
            raise ReconciliationError(
                "extraction set belongs to a different project"
            )
        added: list[ReconciledCandidate] = []
        for item in extraction_set.all_items():
            if (
                self.reconciliation.find_by_extraction_id(item.extraction_id)
                is not None
            ):
                continue
            candidate = ReconciledCandidate(
                reconciliation_id=_deterministic_id("rc-", item.extraction_id),
                extraction_id=item.extraction_id,
                source_id=item.source_id,
                category=item.category,
                text=item.text,
                method=item.method,
                status=item.status,
                normalized=item.normalized,
                location=item.location,
                section=CATEGORY_SECTION_MAP[item.category],
                confidence=item.confidence,
            )
            self.reconciliation.candidates.append(candidate)
            added.append(candidate)
        self.reconciliation.candidates.sort(
            key=lambda c: c.reconciliation_id
        )
        return added

    # -- decisions ------------------------------------------------------

    def _require_state(
        self,
        reconciliation_id: str,
        states: tuple[ReconciliationState, ...],
        action: str,
    ) -> ReconciledCandidate:
        candidate = self.reconciliation.get_candidate(reconciliation_id)
        if candidate.state not in states:
            expected = "/".join(state.value for state in states)
            raise ReconciliationError(
                f"cannot {action}: candidate {reconciliation_id!r} is "
                f"{candidate.state.value}, expected {expected}"
            )
        return candidate

    # States a new explicit decision may start from. DEFERRED is
    # deliberately non-terminal: deferring means "not now", and a later
    # review may accept, reject, or contradict the candidate. Each such
    # decision is recorded with its own timestamp and note.
    _REVIEWABLE_STATES = (
        ReconciliationState.CANDIDATE,
        ReconciliationState.DEFERRED,
    )

    def accept(
        self, reconciliation_id: str, *, note: str | None = None
    ) -> BuildRecord | None:
        """Explicitly accept a candidate into the canonical Build Pack.

        Maps the candidate to its deterministic section, preserves its
        information status verbatim (never upgraded), preserves its
        provenance, and appends a Revision describing the change.

        Returns the created BuildRecord, or ``None`` when the candidate
        was identified as a duplicate of an existing record (the
        candidate is then marked ``duplicate`` and no new record is
        created). Extraction confidence plays no role in this decision.
        """
        candidate = self._require_state(
            reconciliation_id, self._REVIEWABLE_STATES, "accept"
        )
        if candidate.category is CandidateCategory.PROJECT_IDENTITY:
            raise ReconciliationError(
                "project identity candidates can never be accepted "
                "automatically; project identity is not silently changed"
            )
        mapping = _CATEGORY_SECTION_FIELD.get(candidate.category)
        if mapping is None:
            raise ReconciliationError(
                f"category {candidate.category.value!r} has no safe "
                "Build Pack section mapping; defer or reject the candidate "
                "explicitly"
            )
        section, _field_name = mapping
        decided_at = utc_now_iso()

        duplicate = self.find_duplicate(candidate)
        if duplicate is not None:
            candidate.state = ReconciliationState.DUPLICATE
            candidate.duplicate_of_record_id = duplicate.record_id
            candidate.decision_note = (
                note
                or f"duplicate of Build Pack record {duplicate.record_id}"
            )
            candidate.decided_at = decided_at
            return None

        fields = build_record_fields(candidate)
        status = (
            candidate.status if candidate.status is not None else InfoStatus.UNKNOWN
        )
        record_id = _deterministic_id(
            "rec-",
            section,
            json.dumps(fields, sort_keys=True, separators=(",", ":")),
            status.value,
            candidate.extraction_id,
        )
        record = BuildRecord(
            record_id=record_id,
            status=status,
            fields=fields,
            provenance=candidate_provenance(candidate),
        )
        self.pack.sections.setdefault(section, []).append(record)
        self.pack.change_history.append(
            Revision(
                revision=len(self.pack.change_history) + 1,
                timestamp=decided_at,
                description=(
                    f"Accepted {candidate.category.value} candidate "
                    f"{candidate.reconciliation_id} ({status.value}) "
                    f"from source {candidate.source_id}"
                ),
                affected_area=section,
                previous_state=None,
                new_state={
                    "record_id": record_id,
                    "section": section,
                    "fields": fields,
                    "status": status.value,
                },
                author="reconciliation",
            )
        )
        self.pack.validate()

        candidate.state = ReconciliationState.ACCEPTED
        candidate.build_record_id = record_id
        candidate.decision_note = note
        candidate.decided_at = decided_at
        return record

    def reject(
        self, reconciliation_id: str, *, reason: str | None = None
    ) -> ReconciledCandidate:
        """Reject a candidate. It stays recorded; only its state changes.

        Rejection deletes nothing: the source, the extraction result,
        and this decision all remain part of project history.
        """
        candidate = self._require_state(
            reconciliation_id, self._REVIEWABLE_STATES, "reject"
        )
        candidate.state = ReconciliationState.REJECTED
        candidate.decision_note = reason
        candidate.decided_at = utc_now_iso()
        return candidate

    def defer(
        self, reconciliation_id: str, *, reason: str | None = None
    ) -> ReconciledCandidate:
        """Defer a candidate. It stays unresolved; UNKNOWN is legitimate."""
        candidate = self._require_state(
            reconciliation_id, self._REVIEWABLE_STATES, "defer"
        )
        candidate.state = ReconciliationState.DEFERRED
        candidate.decision_note = reason
        candidate.decided_at = utc_now_iso()
        return candidate

    def flag_contradiction(
        self,
        reconciliation_id: str,
        *,
        existing_record_id: str | None = None,
        affected_field: str | None = None,
        description: str,
        note: str | None = None,
    ) -> ReconciliationContradiction:
        """Record that a candidate conflicts with existing pack content.

        The existing record is left untouched; the conflict stays
        visible as an UNRESOLVED contradiction carrying both sides'
        provenance. Conflict identification is explicit: the caller
        names the existing record (or omits it when the conflict is
        with project identity or has no single existing record).
        """
        candidate = self._require_state(
            reconciliation_id,
            self._REVIEWABLE_STATES,
            "flag as contradiction",
        )
        description = _require_non_empty_str(description, "description")
        existing: BuildRecord | None = None
        if existing_record_id is not None:
            existing = self.find_record(existing_record_id)
            if existing is None:
                raise ReconciliationError(
                    f"unknown existing record: {existing_record_id!r}"
                )
        mapping = _CATEGORY_SECTION_FIELD.get(candidate.category)
        if affected_field is None:
            if mapping is not None:
                affected_field = mapping[1]
            elif candidate.category is CandidateCategory.PROJECT_IDENTITY:
                affected_field = "identity"
            else:
                affected_field = "general"
        contradiction_id = _deterministic_id(
            "ctrd-",
            candidate.reconciliation_id,
            existing_record_id or "",
            description,
        )
        contradiction = ReconciliationContradiction(
            contradiction_id=contradiction_id,
            candidate_id=candidate.reconciliation_id,
            section=candidate.section,
            affected_field=affected_field,
            description=description,
            candidate_provenance=_candidate_provenance_dict(candidate),
            existing_record_id=existing_record_id,
            existing_provenance=(
                existing.provenance.to_dict()
                if existing is not None and existing.provenance is not None
                else None
            ),
        )
        self.reconciliation.contradictions.append(contradiction)
        self.reconciliation.contradictions.sort(
            key=lambda c: c.contradiction_id
        )
        candidate.state = ReconciliationState.CONTRADICTED
        candidate.contradiction_id = contradiction_id
        candidate.decision_note = note
        candidate.decided_at = utc_now_iso()
        return contradiction

    def resolve_contradiction(
        self,
        contradiction_id: str,
        *,
        resolution: str,
        note: str | None = None,
    ) -> ReconciliationContradiction:
        """Explicitly resolve a contradiction.

        ``keep_existing`` leaves the Build Pack untouched. ``accept_incoming``
        updates the existing record in place from the candidate (status
        and provenance preserved from the candidate), appending a
        Revision whose ``previous_state`` keeps the old values, so no
        history is lost. Both outcomes mark the contradiction RESOLVED
        with the resolution and note recorded.
        """
        if resolution not in RESOLUTION_CHOICES:
            raise ReconciliationError(
                f"resolution must be one of {RESOLUTION_CHOICES}, "
                f"got {resolution!r}"
            )
        contradiction = self.reconciliation.get_contradiction(
            contradiction_id
        )
        if contradiction.state is not ResolutionStatus.UNRESOLVED:
            raise ReconciliationError(
                f"contradiction {contradiction_id!r} is already resolved"
            )
        candidate = self._require_state(
            contradiction.candidate_id,
            (ReconciliationState.CONTRADICTED,),
            "resolve",
        )
        resolved_at = utc_now_iso()

        if resolution == ACCEPT_INCOMING:
            if contradiction.existing_record_id is None:
                raise ReconciliationError(
                    "accept_incoming needs an existing record to update; "
                    "flag the contradiction against a record first"
                )
            record = self.find_record(contradiction.existing_record_id)
            if record is None:  # defensive; flag_contradiction checks this
                raise ReconciliationError(
                    f"existing record "
                    f"{contradiction.existing_record_id!r} no longer exists"
                )
            previous_state = {
                "record_id": record.record_id,
                "fields": record.fields,
                "status": record.status.value,
                "provenance": (
                    record.provenance.to_dict()
                    if record.provenance is not None
                    else None
                ),
            }
            new_fields = build_record_fields(candidate)
            new_status = (
                candidate.status
                if candidate.status is not None
                else InfoStatus.UNKNOWN
            )
            record.fields = new_fields
            record.status = new_status
            record.provenance = candidate_provenance(candidate)
            record.notes = (
                f"Updated by accepting candidate "
                f"{candidate.reconciliation_id} "
                f"(contradiction {contradiction_id} resolved)."
            )
            self.pack.change_history.append(
                Revision(
                    revision=len(self.pack.change_history) + 1,
                    timestamp=resolved_at,
                    description=(
                        f"Contradiction {contradiction_id} resolved by "
                        f"accepting incoming candidate "
                        f"{candidate.reconciliation_id} "
                        f"({candidate.category.value})"
                    ),
                    affected_area=contradiction.section or "general",
                    previous_state=previous_state,
                    new_state={
                        "record_id": record.record_id,
                        "fields": new_fields,
                        "status": new_status.value,
                        "provenance": record.provenance.to_dict(),
                    },
                    author="reconciliation",
                )
            )
            self.pack.validate()
            candidate.state = ReconciliationState.ACCEPTED
            candidate.build_record_id = record.record_id

        contradiction.state = ResolutionStatus.RESOLVED
        contradiction.resolution = resolution
        contradiction.resolution_note = note
        contradiction.resolved_at = resolved_at
        if note is not None:
            candidate.decision_note = note
        candidate.decided_at = resolved_at
        return contradiction

    # -- persistence ----------------------------------------------------

    def save(self) -> tuple[Path, Path]:
        """Persist the Build Pack and the reconciliation decisions.

        Returns the two file paths written. The Build Pack is validated
        before it is written; sources and extraction results are never
        touched here.
        """
        buildpack_path = self._project.buildpack_store.save(self.pack)
        reconciliation_path = self._project.reconciliation_store.save(
            self.reconciliation
        )
        return buildpack_path, reconciliation_path


__all__ = [
    "RECONCILIATION_FORMAT_NAME",
    "RECONCILIATION_FORMAT_VERSION",
    "BUILDPACK_FILENAME",
    "RECONCILIATION_FILENAME",
    "KEEP_EXISTING",
    "ACCEPT_INCOMING",
    "RESOLUTION_CHOICES",
    "ReconciliationError",
    "ReconciliationState",
    "CATEGORY_SECTION_MAP",
    "ReconciledCandidate",
    "ReconciliationContradiction",
    "Reconciliation",
    "ReconciliationStore",
    "BuildPackStore",
    "build_record_fields",
    "candidate_provenance",
    "packs_equivalent",
    "ReconciliationSession",
]