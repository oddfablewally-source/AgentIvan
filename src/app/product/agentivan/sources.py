"""
Normalized source records for AgentIvan (Brick 2).

A source record describes raw material supplied to a project WITHOUT
interpreting it. The AI layer (later bricks) does the understanding;
intake only records what was supplied, where the original is preserved,
and how to identify it again.

Three intake kinds:

- SOURCE FILE:      an actual file copied byte-for-byte into the project.
- SOURCE TEXT:      text supplied directly; stored verbatim as UTF-8.
- SOURCE REFERENCE: a URL/citation stored as text; never fetched here.

No Build Pack information statuses (KNOWN, PROPOSED, ...) are attached
at intake. Those classifications belong to later processing. Intake
records what was supplied.

``extra`` is a free-form dict carried verbatim through save/load so
future bricks can add fields without a destructive redesign.

Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SourceKind(Enum):
    """What form the supplied source material takes."""

    TEXT = "text"  # text supplied directly (plain text, Markdown, JSON, ...)
    FILE = "file"  # an arbitrary file copied into the project
    IMAGE = "image"  # an image file copied into the project
    REFERENCE = "reference"  # a URL/citation stored as a record, never fetched


class SourceStatus(Enum):
    """Lifecycle status of a source record. Intake always yields STORED."""

    STORED = "stored"


class SourceRecordError(ValueError):
    """Raised when a source record is malformed."""


@dataclass
class SourceRecord:
    source_id: str
    kind: SourceKind
    label: str
    intake_timestamp: str
    status: SourceStatus = SourceStatus.STORED
    original_filename: str | None = None
    relative_path: str | None = None
    media_type: str | None = None
    sha256: str | None = None
    byte_size: int | None = None
    reference: str | None = None
    description: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise SourceRecordError("source_id must be a non-empty string")
        if isinstance(self.kind, str):
            try:
                self.kind = SourceKind(self.kind)
            except ValueError:
                raise SourceRecordError(f"invalid source kind: {self.kind!r}")
        if not isinstance(self.kind, SourceKind):
            raise SourceRecordError("kind must be a SourceKind")
        if isinstance(self.status, str):
            try:
                self.status = SourceStatus(self.status)
            except ValueError:
                raise SourceRecordError(f"invalid source status: {self.status!r}")
        if not isinstance(self.status, SourceStatus):
            raise SourceRecordError("status must be a SourceStatus")
        if not isinstance(self.label, str) or not self.label.strip():
            raise SourceRecordError("label must be a non-empty string")
        if not isinstance(self.intake_timestamp, str) or not self.intake_timestamp:
            raise SourceRecordError("intake_timestamp must be a non-empty string")
        if not isinstance(self.extra, dict):
            raise SourceRecordError("extra must be a dict")

        if self.kind is SourceKind.REFERENCE:
            if not isinstance(self.reference, str) or not self.reference.strip():
                raise SourceRecordError("reference sources require a reference value")
            if self.relative_path is not None:
                raise SourceRecordError("reference sources store no file path")
            if self.sha256 is not None:
                raise SourceRecordError("reference sources carry no content hash")
        else:
            if self.reference is not None:
                raise SourceRecordError("only reference sources carry a reference value")
            if not isinstance(self.relative_path, str) or not self.relative_path:
                raise SourceRecordError("file/text sources require a relative_path")
            if not isinstance(self.sha256, str) or not self.sha256:
                raise SourceRecordError("file/text sources require a sha256 hash")
            if self.byte_size is not None and (
                not isinstance(self.byte_size, int) or self.byte_size < 0
            ):
                raise SourceRecordError("byte_size must be a non-negative int")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "kind": self.kind.value,
            "label": self.label,
            "status": self.status.value,
            "intake_timestamp": self.intake_timestamp,
            "original_filename": self.original_filename,
            "relative_path": self.relative_path,
            "media_type": self.media_type,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "reference": self.reference,
            "description": self.description,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceRecord":
        if not isinstance(data, dict):
            raise SourceRecordError("source record must be a dict")
        known = {
            "source_id",
            "kind",
            "label",
            "status",
            "intake_timestamp",
            "original_filename",
            "relative_path",
            "media_type",
            "sha256",
            "byte_size",
            "reference",
            "description",
            "extra",
        }
        unknown = set(data) - known
        if unknown:
            raise SourceRecordError(f"unknown source record fields: {sorted(unknown)}")
        try:
            return cls(
                source_id=data["source_id"],
                kind=data["kind"],
                label=data["label"],
                intake_timestamp=data["intake_timestamp"],
                status=data.get("status", SourceStatus.STORED.value),
                original_filename=data.get("original_filename"),
                relative_path=data.get("relative_path"),
                media_type=data.get("media_type"),
                sha256=data.get("sha256"),
                byte_size=data.get("byte_size"),
                reference=data.get("reference"),
                description=data.get("description", ""),
                extra=dict(data.get("extra", {})),
            )
        except KeyError as exc:
            raise SourceRecordError(f"missing source record field: {exc}") from exc
