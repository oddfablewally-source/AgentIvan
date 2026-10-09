"""
Canonical Build Pack model for AgentIvan (Brick 1).

The Build Pack is the structured representation of the current project
state and the source of truth for the compiled project. Conversation and
source material are raw input; this model is what later systems (AI
compilation, PDF/Markdown generation) read and update.

Core rules enforced here:

- Every record carries one controlled InfoStatus; statuses are never
  silently promoted, defaulted, or converted.
- UNKNOWN is valid data: incomplete projects validate.
- Every fact can carry provenance; provenance is never invented --
  records without a known origin simply carry none.
- Revisions preserve history; important state is never overwritten
  without a record.
- Contradictions are representable and are never auto-resolved.
- Serialization is JSON: human-readable, deterministic (sorted keys,
  records ordered by id), and round-trips exactly.

Stdlib only. No database, no network, no AI: this brick defines the
representation, not the behavior built on top of it.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .statuses import InfoStatus, ResolutionStatus

FORMAT_NAME = "agentivan-build-pack"
FORMAT_VERSION = 1


class BuildPackError(ValueError):
    """Raised when a Build Pack record or structure is malformed."""


# ---------------------------------------------------------------------------
# Section schemas
# ---------------------------------------------------------------------------
#
# Each content section maps to the fields Brick 1 requires it to support.
# "required" lists the identifying field(s) a record must carry;
# everything else is optional so incomplete information stays valid.
# The record-level information status lives on the record itself, not in
# the fields dict. Unknown field names are rejected as malformed: new
# fields are added by extending this schema, not by smuggling keys in.

SECTION_SCHEMAS: dict[str, dict[str, list[str]]] = {
    "donor": {
        "required": [],
        "optional": [
            "manufacturer",
            "model",
            "year",
            "configuration",
            "known_condition",
            "relevant_specifications",
            "notes",
        ],
    },
    "design": {
        "required": [],
        "optional": [
            "design_objectives",
            "constraints",
            "design_decisions",
            "alternatives",
            "concept_notes",
        ],
    },
    "measurements": {
        "required": ["measurement"],
        "optional": ["value", "unit", "location", "source", "date"],
    },
    "parts": {
        "required": ["name"],
        "optional": ["category", "quantity", "source", "cost", "notes"],
    },
    "research": {
        "required": ["subject"],
        "optional": ["finding", "source", "date", "notes"],
    },
    "engineering": {
        "required": ["calculation"],
        "optional": ["inputs", "result", "units", "assumptions", "source"],
    },
    "cad": {
        "required": ["component"],
        "optional": [
            "file_reference",
            "drawing_info",
            "fabrication_process",
            "material",
            "dimensions",
            "tolerances",
            "notes",
        ],
    },
    "build": {
        "required": ["task"],
        "optional": ["sequence", "dependencies", "tools", "notes"],
    },
    "finishing": {
        "required": ["material"],
        "optional": ["treatment", "process", "finish_specification", "notes"],
    },
    "testing": {
        "required": ["item"],
        "optional": [
            "method",
            "expected_result",
            "actual_result",
            "date",
            "notes",
        ],
    },
    "cost": {
        "required": ["item"],
        "optional": ["quantity", "unit_cost", "total", "category"],
    },
    "safety": {
        "required": ["issue"],
        "optional": ["requirement", "source", "notes"],
    },
    "open_issues": {
        "required": ["question"],
        "optional": ["context", "priority", "notes"],
    },
}


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _require_non_empty_str(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BuildPackError(f"{what} must be a non-empty string")
    return value


def _optional_str(value: Any, what: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise BuildPackError(f"{what} must be a string or null")
    return value


def _check_iso_datetime(value: Any, what: str) -> str:
    """Validate an ISO-8601 date/datetime string; return it unchanged."""
    if not isinstance(value, str):
        raise BuildPackError(f"{what} must be an ISO-8601 date string")
    try:
        datetime.fromisoformat(value)
    except ValueError:
        raise BuildPackError(
            f"{what} must be an ISO-8601 date string, got {value!r}"
        ) from None
    return value


def _check_json_value(value: Any, what: str) -> None:
    """Field values must be JSON-native so packs always serialize."""
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise BuildPackError(f"{what} must be finite, got {value!r}")
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _check_json_value(item, f"{what}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise BuildPackError(f"{what} has a non-string key {key!r}")
            _check_json_value(item, f"{what}[{key!r}]")
        return
    raise BuildPackError(
        f"{what} must be a JSON-native value (str, number, bool, null, "
        f"list, or object), got {type(value).__name__}"
    )


def _coerce_info_status(value: Any, what: str = "status") -> InfoStatus:
    if isinstance(value, InfoStatus):
        return value
    if isinstance(value, str):
        try:
            return InfoStatus(value)
        except ValueError:
            pass
    valid = ", ".join(s.value for s in InfoStatus)
    raise BuildPackError(f"{what} must be one of [{valid}], got {value!r}")


def _coerce_resolution_status(value: Any) -> ResolutionStatus:
    if isinstance(value, ResolutionStatus):
        return value
    if isinstance(value, str):
        try:
            return ResolutionStatus(value)
        except ValueError:
            pass
    valid = ", ".join(s.value for s in ResolutionStatus)
    raise BuildPackError(
        f"resolution_status must be one of [{valid}], got {value!r}"
    )


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


@dataclass
class Provenance:
    """Origin of a project fact.

    source_type is a free-form label (e.g. "conversation",
    "manufacturer_document", "field_measurement") so future input types
    never require a model redesign. Everything else is optional: when
    the origin is not known, the record carries no provenance rather
    than an invented one.
    """

    source_type: str
    source_reference: str | None = None
    source_text: str | None = None
    date: str | None = None
    author: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        self.source_type = _require_non_empty_str(
            self.source_type, "provenance.source_type"
        )
        for attr in ("source_reference", "source_text", "author", "notes"):
            setattr(
                self,
                attr,
                _optional_str(getattr(self, attr), f"provenance.{attr}"),
            )
        if self.date is not None:
            self.date = _check_iso_datetime(self.date, "provenance.date")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_reference": self.source_reference,
            "source_text": self.source_text,
            "date": self.date,
            "author": self.author,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Provenance:
        if not isinstance(data, dict):
            raise BuildPackError("provenance must be an object")
        known = {
            "source_type",
            "source_reference",
            "source_text",
            "date",
            "author",
            "notes",
        }
        unknown = set(data) - known
        if unknown:
            raise BuildPackError(
                f"provenance has unknown keys: {sorted(unknown)}"
            )
        return cls(
            source_type=data.get("source_type"),
            source_reference=data.get("source_reference"),
            source_text=data.get("source_text"),
            date=data.get("date"),
            author=data.get("author"),
            notes=data.get("notes"),
        )


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass
class BuildRecord:
    """One project fact inside a section.

    The section a record belongs to is determined by where it sits in
    BuildPack.sections; the record itself carries its id, its controlled
    information status, its section-specific fields, optional provenance,
    and optional notes. Status is mandatory: it is never defaulted.
    """

    record_id: str
    status: InfoStatus
    fields: dict[str, Any] = field(default_factory=dict)
    provenance: Provenance | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        self.record_id = _require_non_empty_str(self.record_id, "record_id")
        self.status = _coerce_info_status(self.status)
        if not isinstance(self.fields, dict):
            raise BuildPackError(
                f"record {self.record_id!r}: fields must be an object"
            )
        if self.provenance is not None and not isinstance(
            self.provenance, Provenance
        ):
            raise BuildPackError(
                f"record {self.record_id!r}: provenance must be a Provenance"
            )
        self.notes = _optional_str(self.notes, f"record {self.record_id!r} notes")

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "status": self.status.value,
            "fields": {k: self.fields[k] for k in sorted(self.fields)},
            "provenance": (
                self.provenance.to_dict() if self.provenance is not None else None
            ),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Any) -> BuildRecord:
        if not isinstance(data, dict):
            raise BuildPackError("record must be an object")
        known = {"record_id", "status", "fields", "provenance", "notes"}
        unknown = set(data) - known
        if unknown:
            raise BuildPackError(f"record has unknown keys: {sorted(unknown)}")
        if "status" not in data:
            # Never silently promote or default a missing status.
            raise BuildPackError("record is missing required 'status'")
        provenance = data.get("provenance")
        return cls(
            record_id=data.get("record_id"),
            status=data.get("status"),
            fields=data.get("fields") if data.get("fields") is not None else {},
            provenance=(
                Provenance.from_dict(provenance)
                if provenance is not None
                else None
            ),
            notes=data.get("notes"),
        )


# ---------------------------------------------------------------------------
# Revisions
# ---------------------------------------------------------------------------


@dataclass
class Revision:
    """One entry in the Build Pack change history.

    History is append-only in spirit: updating the current project state
    adds a revision here instead of silently overwriting what came
    before. previous_state/new_state are JSON-native snapshots of what
    changed, or null when not applicable.
    """

    revision: int
    timestamp: str
    description: str
    affected_area: str
    previous_state: Any | None = None
    new_state: Any | None = None
    author: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.revision, int)
            or isinstance(self.revision, bool)
            or self.revision <= 0
        ):
            raise BuildPackError("revision number must be a positive integer")
        self.timestamp = _check_iso_datetime(self.timestamp, "revision.timestamp")
        self.description = _require_non_empty_str(
            self.description, "revision.description"
        )
        self.affected_area = _require_non_empty_str(
            self.affected_area, "revision.affected_area"
        )
        _check_json_value(self.previous_state, "revision.previous_state")
        _check_json_value(self.new_state, "revision.new_state")
        self.author = _optional_str(self.author, "revision.author")

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "timestamp": self.timestamp,
            "description": self.description,
            "affected_area": self.affected_area,
            "previous_state": self.previous_state,
            "new_state": self.new_state,
            "author": self.author,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Revision:
        if not isinstance(data, dict):
            raise BuildPackError("revision must be an object")
        known = {
            "revision",
            "timestamp",
            "description",
            "affected_area",
            "previous_state",
            "new_state",
            "author",
        }
        unknown = set(data) - known
        if unknown:
            raise BuildPackError(f"revision has unknown keys: {sorted(unknown)}")
        return cls(
            revision=data.get("revision"),
            timestamp=data.get("timestamp"),
            description=data.get("description"),
            affected_area=data.get("affected_area"),
            previous_state=data.get("previous_state"),
            new_state=data.get("new_state"),
            author=data.get("author"),
        )


# ---------------------------------------------------------------------------
# Contradictions
# ---------------------------------------------------------------------------


@dataclass
class Contradiction:
    """Conflicting information, retained until explicitly resolved.

    The model never resolves these on its own: both records stay in the
    pack, the conflict stays visible, and resolution requires an explicit
    status change plus a note explaining how it was resolved.
    """

    contradiction_id: str
    record_ids: list[str]
    affected_field: str
    explanation: str
    resolution_status: ResolutionStatus = ResolutionStatus.UNRESOLVED
    resolution_note: str | None = None

    def __post_init__(self) -> None:
        self.contradiction_id = _require_non_empty_str(
            self.contradiction_id, "contradiction_id"
        )
        if not isinstance(self.record_ids, list) or len(self.record_ids) < 2:
            raise BuildPackError("contradiction must reference at least two records")
        for rid in self.record_ids:
            _require_non_empty_str(rid, "contradiction record_ids entry")
        if len(set(self.record_ids)) != len(self.record_ids):
            raise BuildPackError("contradiction record_ids must be distinct")
        self.affected_field = _require_non_empty_str(
            self.affected_field, "contradiction.affected_field"
        )
        self.explanation = _require_non_empty_str(
            self.explanation, "contradiction.explanation"
        )
        self.resolution_status = _coerce_resolution_status(self.resolution_status)
        self.resolution_note = _optional_str(
            self.resolution_note, "contradiction.resolution_note"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contradiction_id": self.contradiction_id,
            "record_ids": list(self.record_ids),
            "affected_field": self.affected_field,
            "explanation": self.explanation,
            "resolution_status": self.resolution_status.value,
            "resolution_note": self.resolution_note,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Contradiction:
        if not isinstance(data, dict):
            raise BuildPackError("contradiction must be an object")
        known = {
            "contradiction_id",
            "record_ids",
            "affected_field",
            "explanation",
            "resolution_status",
            "resolution_note",
        }
        unknown = set(data) - known
        if unknown:
            raise BuildPackError(
                f"contradiction has unknown keys: {sorted(unknown)}"
            )
        return cls(
            contradiction_id=data.get("contradiction_id"),
            record_ids=data.get("record_ids"),
            affected_field=data.get("affected_field"),
            explanation=data.get("explanation"),
            resolution_status=data.get("resolution_status")
            if data.get("resolution_status") is not None
            else ResolutionStatus.UNRESOLVED,
            resolution_note=data.get("resolution_note"),
        )


# ---------------------------------------------------------------------------
# Project identity
# ---------------------------------------------------------------------------


@dataclass
class ProjectIdentity:
    """Who the project is: required identity block of every Build Pack."""

    name: str
    project_id: str
    description: str
    intent: str
    status: str
    created: str
    modified: str
    revision: int

    def __post_init__(self) -> None:
        for attr in ("name", "project_id", "description", "intent", "status"):
            setattr(
                self,
                attr,
                _require_non_empty_str(getattr(self, attr), f"identity.{attr}"),
            )
        self.created = _check_iso_datetime(self.created, "identity.created")
        self.modified = _check_iso_datetime(self.modified, "identity.modified")
        if (
            not isinstance(self.revision, int)
            or isinstance(self.revision, bool)
            or self.revision < 0
        ):
            raise BuildPackError("identity.revision must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "project_id": self.project_id,
            "description": self.description,
            "intent": self.intent,
            "status": self.status,
            "created": self.created,
            "modified": self.modified,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, data: Any) -> ProjectIdentity:
        if not isinstance(data, dict):
            raise BuildPackError("identity must be an object")
        known = {
            "name",
            "project_id",
            "description",
            "intent",
            "status",
            "created",
            "modified",
            "revision",
        }
        unknown = set(data) - known
        if unknown:
            raise BuildPackError(f"identity has unknown keys: {sorted(unknown)}")
        return cls(
            name=data.get("name"),
            project_id=data.get("project_id"),
            description=data.get("description"),
            intent=data.get("intent"),
            status=data.get("status"),
            created=data.get("created"),
            modified=data.get("modified"),
            revision=data.get("revision"),
        )


# ---------------------------------------------------------------------------
# Build Pack
# ---------------------------------------------------------------------------


@dataclass
class BuildPack:
    """The canonical Build Pack: current project state, source of truth."""

    identity: ProjectIdentity
    sections: dict[str, list[BuildRecord]] = field(default_factory=dict)
    change_history: list[Revision] = field(default_factory=list)
    contradictions: list[Contradiction] = field(default_factory=list)

    def __eq__(self, other: Any) -> bool:
        # Two packs are equal when their canonical serialized forms are
        # equal: record/section insertion order is not meaningful, but
        # change-history order is (to_dict preserves list order there).
        if not isinstance(other, BuildPack):
            return NotImplemented
        return self.to_dict() == other.to_dict()

    def validate(self) -> None:
        """Check the whole pack; raise BuildPackError on the first problem."""
        if not isinstance(self.identity, ProjectIdentity):
            raise BuildPackError("identity must be a ProjectIdentity")
        # ProjectIdentity self-validates on construction, but a pack can
        # be mutated afterwards, so re-run the checks through a copy.
        ProjectIdentity.from_dict(self.identity.to_dict())

        if not isinstance(self.sections, dict):
            raise BuildPackError("sections must be an object")
        seen_ids: set[str] = set()
        for section_name, records in self.sections.items():
            if section_name not in SECTION_SCHEMAS:
                raise BuildPackError(f"unknown section: {section_name!r}")
            if not isinstance(records, list):
                raise BuildPackError(
                    f"section {section_name!r} must be a list of records"
                )
            schema = SECTION_SCHEMAS[section_name]
            allowed = set(schema["required"]) | set(schema["optional"])
            for record in records:
                if not isinstance(record, BuildRecord):
                    raise BuildPackError(
                        f"section {section_name!r} contains a non-record"
                    )
                if record.record_id in seen_ids:
                    raise BuildPackError(
                        f"duplicate record_id: {record.record_id!r}"
                    )
                seen_ids.add(record.record_id)
                if not isinstance(record.status, InfoStatus):
                    raise BuildPackError(
                        f"record {record.record_id!r} has an invalid status"
                    )
                for required_key in schema["required"]:
                    if required_key not in record.fields:
                        raise BuildPackError(
                            f"record {record.record_id!r}: missing required "
                            f"field {required_key!r} for section {section_name!r}"
                        )
                for key, value in record.fields.items():
                    if not isinstance(key, str):
                        raise BuildPackError(
                            f"record {record.record_id!r} has a non-string "
                            f"field name {key!r}"
                        )
                    if key not in allowed:
                        raise BuildPackError(
                            f"record {record.record_id!r}: unknown field "
                            f"{key!r} for section {section_name!r}"
                        )
                    _check_json_value(
                        value, f"record {record.record_id!r} field {key!r}"
                    )
                if record.provenance is not None and not isinstance(
                    record.provenance, Provenance
                ):
                    raise BuildPackError(
                        f"record {record.record_id!r} has invalid provenance"
                    )

        last_revision = 0
        if not isinstance(self.change_history, list):
            raise BuildPackError("change_history must be a list")
        for rev in self.change_history:
            if not isinstance(rev, Revision):
                raise BuildPackError("change_history must contain Revisions")
            if rev.revision <= last_revision:
                raise BuildPackError(
                    "change_history revision numbers must be positive and "
                    f"strictly increasing, got {rev.revision} after "
                    f"{last_revision}"
                )
            last_revision = rev.revision

        if not isinstance(self.contradictions, list):
            raise BuildPackError("contradictions must be a list")
        for contradiction in self.contradictions:
            if not isinstance(contradiction, Contradiction):
                raise BuildPackError(
                    "contradictions must contain Contradictions"
                )
            cid = contradiction.contradiction_id
            if cid in seen_ids:
                raise BuildPackError(f"duplicate contradiction_id: {cid!r}")
            seen_ids.add(cid)
            for rid in contradiction.record_ids:
                if rid not in seen_ids:
                    raise BuildPackError(
                        f"contradiction {cid!r} references unknown "
                        f"record {rid!r}"
                    )
            if (
                contradiction.resolution_status is ResolutionStatus.RESOLVED
                and not contradiction.resolution_note
            ):
                raise BuildPackError(
                    f"contradiction {cid!r} is marked RESOLVED but has no "
                    "resolution_note"
                )

    def to_dict(self) -> dict[str, Any]:
        """Serialize deterministically: sorted keys, records ordered by id."""
        return {
            "format": FORMAT_NAME,
            "format_version": FORMAT_VERSION,
            "identity": self.identity.to_dict(),
            "sections": {
                name: [
                    record.to_dict()
                    for record in sorted(
                        self.sections[name], key=lambda r: r.record_id
                    )
                ]
                for name in sorted(self.sections)
            },
            "change_history": [rev.to_dict() for rev in self.change_history],
            "contradictions": [c.to_dict() for c in self.contradictions],
        }

    def to_json(self) -> str:
        """Serialize to canonical JSON (sorted keys, 2-space indent)."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Any) -> BuildPack:
        """Deserialize and validate; malformed packs are rejected."""
        if not isinstance(data, dict):
            raise BuildPackError("build pack must be an object")
        known = {
            "format",
            "format_version",
            "identity",
            "sections",
            "change_history",
            "contradictions",
        }
        unknown = set(data) - known
        if unknown:
            raise BuildPackError(
                f"build pack has unknown keys: {sorted(unknown)}"
            )
        if "format" in data and data["format"] != FORMAT_NAME:
            raise BuildPackError(
                f"unknown build pack format: {data['format']!r}"
            )
        if "format_version" in data and data["format_version"] != FORMAT_VERSION:
            raise BuildPackError(
                f"unsupported build pack format_version: "
                f"{data['format_version']!r}"
            )
        if "identity" not in data:
            raise BuildPackError("build pack is missing required 'identity'")
        sections_data = data.get("sections") or {}
        if not isinstance(sections_data, dict):
            raise BuildPackError("sections must be an object")
        sections: dict[str, list[BuildRecord]] = {}
        for section_name, records_data in sections_data.items():
            if not isinstance(records_data, list):
                raise BuildPackError(
                    f"section {section_name!r} must be a list of records"
                )
            sections[section_name] = [
                BuildRecord.from_dict(item) for item in records_data
            ]
        pack = cls(
            identity=ProjectIdentity.from_dict(data["identity"]),
            sections=sections,
            change_history=[
                Revision.from_dict(item)
                for item in (data.get("change_history") or [])
            ],
            contradictions=[
                Contradiction.from_dict(item)
                for item in (data.get("contradictions") or [])
            ],
        )
        pack.validate()
        return pack

    @classmethod
    def from_json(cls, text: str) -> BuildPack:
        """Deserialize canonical JSON; malformed packs are rejected."""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BuildPackError(f"invalid JSON: {exc}") from None
        return cls.from_dict(data)
