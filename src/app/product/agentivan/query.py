"""Brick 6 -- deterministic Build Pack query + change intelligence.

Read-only views over the canonical Build Pack and the layers around it:

- the Build Pack itself (Brick 1 model): identity, sections, records,
  statuses, revisions, contradictions
- reconciliation decisions (Brick 5): candidates, contradictions,
  resolution state
- source catalog integrity (Brick 3): intact / changed / missing

Layer separation is preserved: queries never merge sources, extraction
results, reconciliation decisions, and canonical state into one bucket.
Every operation is read-only -- nothing here may modify the Build Pack,
sources, extraction results, reconciliation decisions, or create
revisions. Only explicit Brick 5 reconciliation operations alter
canonical state.

Standard library only. No LLM, no embeddings, no network, no database.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .catalog import SourceCatalog, SourceIntegrity
from .extraction import InfoStatus
from .model import (
    SECTION_SCHEMAS,
    BuildPack,
    BuildRecord,
    ProjectIdentity,
    Revision,
)
from .statuses import ResolutionStatus
from .reconciliation import (
    ReconciledCandidate,
    Reconciliation,
    ReconciliationContradiction,
    ReconciliationError,
    ReconciliationState,
)

__all__ = [
    "QueryError",
    "RecordHit",
    "ProvenanceHit",
    "ChangeEntry",
    "ChangeSet",
    "KnowledgeReport",
    "RemainingWork",
    "ContradictionDetail",
    "BuildPackQuery",
    "diff_packs",
]


class QueryError(ValueError):
    """Raised when a query is malformed or cannot be answered as asked."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _coerce_status(value: Any) -> InfoStatus:
    """Accept an InfoStatus or its exact string value; never invent one."""
    if isinstance(value, InfoStatus):
        return value
    if isinstance(value, str):
        try:
            return InfoStatus(value)
        except ValueError:
            raise QueryError(f"unknown information status: {value!r}") from None
    raise QueryError(
        f"status must be an InfoStatus or its string value, got {type(value).__name__}"
    )


def _require_section(section: str) -> str:
    if not isinstance(section, str) or section not in SECTION_SCHEMAS:
        raise QueryError(
            f"unknown Build Pack section: {section!r}; "
            f"known sections: {sorted(SECTION_SCHEMAS)}"
        )
    return section


def _sort_key_hit(hit: "RecordHit") -> tuple[str, str]:
    return (hit.section, hit.record_id)


def _provenance_reference_matches(reference: str | None, source_id: str) -> bool:
    """Conservative match: exact id or the Brick 5 ``id (filename)`` form."""
    if not reference:
        return False
    return reference == source_id or reference.startswith(source_id + " (")


def _parse_notes_kv(notes: str | None) -> dict[str, str]:
    parsed: dict[str, str] = {}
    if not notes:
        return parsed
    for part in notes.split(";"):
        key, sep, value = part.partition("=")
        if sep:
            parsed[key.strip()] = value.strip()
    return parsed


def _record_strings(record: BuildRecord) -> list[str]:
    """Every searchable string a record carries (fields + notes)."""
    strings = [record.record_id]
    for value in record.fields.values():
        if isinstance(value, str):
            strings.append(value)
        else:
            try:
                strings.append(json.dumps(value, sort_keys=True))
            except (TypeError, ValueError):
                strings.append(repr(value))
    if record.notes:
        strings.append(record.notes)
    return strings


# ---------------------------------------------------------------------------
# result types (all serializable)
# ---------------------------------------------------------------------------


@dataclass
class RecordHit:
    """One canonical Build Pack record, located by section."""

    section: str
    record_id: str
    status: str
    fields: dict[str, Any]
    provenance: dict[str, Any] | None
    notes: str | None

    @classmethod
    def from_record(cls, section: str, record: BuildRecord) -> "RecordHit":
        return cls(
            section=section,
            record_id=record.record_id,
            status=record.status.value,
            fields=dict(record.fields),
            provenance=(
                record.provenance.to_dict() if record.provenance is not None else None
            ),
            notes=record.notes,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "record_id": self.record_id,
            "status": self.status,
            "fields": self.fields,
            "provenance": self.provenance,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "RecordHit":
        if not isinstance(data, dict):
            raise QueryError("record hit must be an object")
        return cls(
            section=data["section"],
            record_id=data["record_id"],
            status=data["status"],
            fields=dict(data["fields"]),
            provenance=data["provenance"],
            notes=data["notes"],
        )


@dataclass
class ProvenanceHit(RecordHit):
    """A RecordHit enriched with reconciliation/source linkage.

    ``provenance_kind`` is one of:

    - ``"extracted"`` -- the record came through extraction/reconciliation
      and carries a parseable ``extraction_id`` in its provenance notes
    - ``"manual"`` -- the record carries provenance that is not extraction
      linkage (older/manual information); provenance is reported as-is and
      never fabricated into extraction linkage
    - ``"none"`` -- the record carries no provenance at all
    """

    provenance_kind: str = "none"
    extraction_id: str | None = None
    reconciliation_id: str | None = None
    reconciliation_state: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload.update(
            {
                "provenance_kind": self.provenance_kind,
                "extraction_id": self.extraction_id,
                "reconciliation_id": self.reconciliation_id,
                "reconciliation_state": self.reconciliation_state,
            }
        )
        return payload

    @classmethod
    def from_dict(cls, data: Any) -> "ProvenanceHit":
        if not isinstance(data, dict):
            raise QueryError("provenance hit must be an object")
        return cls(
            section=data["section"],
            record_id=data["record_id"],
            status=data["status"],
            fields=dict(data["fields"]),
            provenance=data["provenance"],
            notes=data["notes"],
            provenance_kind=data.get("provenance_kind", "none"),
            extraction_id=data.get("extraction_id"),
            reconciliation_id=data.get("reconciliation_id"),
            reconciliation_state=data.get("reconciliation_state"),
        )


@dataclass
class ChangeEntry:
    """One structural change between two Build Pack states.

    ``change`` is ``"added"``, ``"removed"``, or ``"modified"``.
    ``path`` identifies the affected location: ``None`` for whole-record
    add/remove, otherwise ``"status"``, ``"notes"``, ``"provenance"``, or
    ``"fields.<name>"``.
    """

    change: str
    section: str
    record_id: str
    path: str | None
    old_value: Any
    new_value: Any
    provenance: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "change": self.change,
            "section": self.section,
            "record_id": self.record_id,
            "path": self.path,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "provenance": self.provenance,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ChangeEntry":
        if not isinstance(data, dict):
            raise QueryError("change entry must be an object")
        if data.get("change") not in ("added", "removed", "modified"):
            raise QueryError(f"unknown change kind: {data.get('change')!r}")
        return cls(
            change=data["change"],
            section=data["section"],
            record_id=data["record_id"],
            path=data["path"],
            old_value=data["old_value"],
            new_value=data["new_value"],
            provenance=data.get("provenance"),
        )


@dataclass
class ChangeSet:
    """Structured result of comparing two Build Pack states.

    ``unchanged`` holds ``{"section", "record_id"}`` pairs for records
    identical in both states.
    """

    added: list[ChangeEntry] = field(default_factory=list)
    removed: list[ChangeEntry] = field(default_factory=list)
    modified: list[ChangeEntry] = field(default_factory=list)
    unchanged: list[dict[str, str]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.added and not self.removed and not self.modified

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": [entry.to_dict() for entry in self.added],
            "removed": [entry.to_dict() for entry in self.removed],
            "modified": [entry.to_dict() for entry in self.modified],
            "unchanged": [dict(pair) for pair in self.unchanged],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Any) -> "ChangeSet":
        if not isinstance(data, dict):
            raise QueryError("change set must be an object")
        return cls(
            added=[ChangeEntry.from_dict(e) for e in data.get("added", [])],
            removed=[ChangeEntry.from_dict(e) for e in data.get("removed", [])],
            modified=[ChangeEntry.from_dict(e) for e in data.get("modified", [])],
            unchanged=[dict(p) for p in data.get("unchanged", [])],
        )

    @classmethod
    def from_json(cls, text: str) -> "ChangeSet":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise QueryError(f"change set is not valid JSON: {exc}") from exc
        return cls.from_dict(data)


@dataclass
class KnowledgeReport:
    """Canonical information grouped by information status ("what do we know?").

    Every InfoStatus appears as a key in enum order, even when empty --
    the absence of e.g. VERIFIED information is itself information.
    No narrative, no inference, no correctness judgements.
    """

    groups: dict[str, list[RecordHit]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            status: [hit.to_dict() for hit in self.groups.get(status, [])]
            for status in self.groups
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Any) -> "KnowledgeReport":
        if not isinstance(data, dict):
            raise QueryError("knowledge report must be an object")
        groups: dict[str, list[RecordHit]] = {}
        for status, hits in data.items():
            if not isinstance(hits, list):
                raise QueryError(f"knowledge group {status!r} must be a list")
            groups[status] = [RecordHit.from_dict(h) for h in hits]
        return cls(groups=groups)

    @classmethod
    def from_json(cls, text: str) -> "KnowledgeReport":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise QueryError(f"knowledge report is not valid JSON: {exc}") from exc
        return cls.from_dict(data)


@dataclass
class RemainingWork:
    """Unresolved work ("what remains?").

    Categories stay distinct on purpose: UNKNOWN is not a contradiction,
    DEFERRED is not rejected, and a changed source file is not a project
    contradiction. Each item keeps its own identity and provenance.
    """

    open_issues: list[RecordHit] = field(default_factory=list)
    unknown_entries: list[RecordHit] = field(default_factory=list)
    deferred_candidates: list[dict[str, Any]] = field(default_factory=list)
    unresolved_contradictions: list[dict[str, Any]] = field(default_factory=list)
    source_integrity_warnings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "open_issues": [hit.to_dict() for hit in self.open_issues],
            "unknown_entries": [hit.to_dict() for hit in self.unknown_entries],
            "deferred_candidates": [dict(c) for c in self.deferred_candidates],
            "unresolved_contradictions": [
                dict(c) for c in self.unresolved_contradictions
            ],
            "source_integrity_warnings": [
                dict(w) for w in self.source_integrity_warnings
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Any) -> "RemainingWork":
        if not isinstance(data, dict):
            raise QueryError("remaining-work report must be an object")
        return cls(
            open_issues=[
                RecordHit.from_dict(h) for h in data.get("open_issues", [])
            ],
            unknown_entries=[
                RecordHit.from_dict(h) for h in data.get("unknown_entries", [])
            ],
            deferred_candidates=[
                dict(c) for c in data.get("deferred_candidates", [])
            ],
            unresolved_contradictions=[
                dict(c) for c in data.get("unresolved_contradictions", [])
            ],
            source_integrity_warnings=[
                dict(w) for w in data.get("source_integrity_warnings", [])
            ],
        )

    @classmethod
    def from_json(cls, text: str) -> "RemainingWork":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise QueryError(
                f"remaining-work report is not valid JSON: {exc}"
            ) from exc
        return cls.from_dict(data)


@dataclass
class ContradictionDetail:
    """A reconciliation contradiction with both competing sides resolved.

    Nothing is resolved here -- the contradiction stays as recorded; this
    is read visibility only. Either side may be ``None`` when the linked
    record or candidate no longer exists, which is reported rather than
    hidden.
    """

    contradiction: dict[str, Any]
    existing: dict[str, Any] | None
    candidate: dict[str, Any] | None
    resolution: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "contradiction": dict(self.contradiction),
            "existing": dict(self.existing) if self.existing is not None else None,
            "candidate": dict(self.candidate) if self.candidate is not None else None,
            "resolution": dict(self.resolution) if self.resolution is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ContradictionDetail":
        if not isinstance(data, dict):
            raise QueryError("contradiction detail must be an object")
        return cls(
            contradiction=dict(data["contradiction"]),
            existing=(
                dict(data["existing"]) if data.get("existing") is not None else None
            ),
            candidate=(
                dict(data["candidate"]) if data.get("candidate") is not None else None
            ),
            resolution=(
                dict(data["resolution"]) if data.get("resolution") is not None else None
            ),
        )


# ---------------------------------------------------------------------------
# the query API
# ---------------------------------------------------------------------------


class BuildPackQuery:
    """Deterministic, read-only queries over one Build Pack and its layers.

    ``pack`` is required. ``reconciliation`` unlocks candidate and
    contradiction queries; ``catalog`` unlocks source-integrity queries.
    Use :meth:`for_project` to wire all three from a project.
    """

    def __init__(
        self,
        pack: BuildPack,
        *,
        reconciliation: Reconciliation | None = None,
        catalog: SourceCatalog | None = None,
    ) -> None:
        if not isinstance(pack, BuildPack):
            raise QueryError("pack must be a BuildPack")
        self._pack = pack
        self._reconciliation = reconciliation
        self._catalog = catalog

    @classmethod
    def for_project(cls, project: Any) -> "BuildPackQuery":
        """Build a fully-wired query for a project (loads, never writes)."""
        pack = project.buildpack_store.load_or_create()
        reconciliation = project.reconciliation_store.load_or_empty()
        return cls(pack, reconciliation=reconciliation, catalog=project.catalog)

    # -- guards ------------------------------------------------------------

    def _require_reconciliation(self) -> Reconciliation:
        if self._reconciliation is None:
            raise QueryError(
                "this query needs reconciliation state; "
                "pass reconciliation=... or use BuildPackQuery.for_project()"
            )
        return self._reconciliation

    def _require_catalog(self) -> SourceCatalog:
        if self._catalog is None:
            raise QueryError(
                "this query needs the source catalog; "
                "pass catalog=... or use BuildPackQuery.for_project()"
            )
        return self._catalog

    # -- identity and sections ----------------------------------------------

    def identity(self) -> ProjectIdentity:
        """The project's canonical identity."""
        return self._pack.identity

    def sections(self) -> list[str]:
        """Names of sections currently present in the Build Pack, sorted."""
        return sorted(self._pack.sections)

    def section_has_content(self, section: str) -> bool:
        """Whether a section exists and holds at least one record."""
        _require_section(section)
        return bool(self._pack.sections.get(section))

    def section(self, section: str) -> list[RecordHit]:
        """All records in a section, in deterministic order."""
        _require_section(section)
        return [
            RecordHit.from_record(section, record)
            for record in sorted(
                self._pack.sections.get(section, []),
                key=lambda record: record.record_id,
            )
        ]

    # -- record queries ------------------------------------------------------

    def records(
        self,
        *,
        section: str | None = None,
        status: InfoStatus | str | None = None,
        source_id: str | None = None,
        text: str | None = None,
    ) -> list[RecordHit]:
        """Records matching every given filter; empty filters match all.

        - ``section``: one Brick 1 section name
        - ``status``: exact information status -- never inferred, only matched
        - ``source_id``: records whose provenance references this source
        - ``text``: case-insensitive substring over record text
        """
        if section is not None:
            _require_section(section)
        wanted_status = _coerce_status(status) if status is not None else None
        needle = text.casefold() if text is not None else None

        hits: list[RecordHit] = []
        section_names = [section] if section is not None else sorted(self._pack.sections)
        for section_name in section_names:
            for record in self._pack.sections.get(section_name, []):
                if wanted_status is not None and record.status is not wanted_status:
                    continue
                if source_id is not None and not _provenance_reference_matches(
                    record.provenance.source_reference
                    if record.provenance is not None
                    else None,
                    source_id,
                ):
                    continue
                if needle is not None and not any(
                    needle in value.casefold() for value in _record_strings(record)
                ):
                    continue
                hits.append(RecordHit.from_record(section_name, record))
        hits.sort(key=_sort_key_hit)
        return hits

    def by_status(self, status: InfoStatus | str) -> list[RecordHit]:
        """Records carrying exactly this status. Nothing is upgraded,
        downgraded, or inferred."""
        return self.records(status=status)

    def unknown(self) -> list[RecordHit]:
        """Records whose status is explicitly UNKNOWN."""
        return self.by_status(InfoStatus.UNKNOWN)

    def open_issues(self) -> list[RecordHit]:
        """Records in the open_issues section."""
        return self.section("open_issues")

    # -- provenance -----------------------------------------------------------

    @staticmethod
    def provenance_kind(record: BuildRecord) -> str:
        """``"extracted"``, ``"manual"``, or ``"none"`` -- never invented."""
        if record.provenance is None:
            return "none"
        notes = _parse_notes_kv(record.provenance.notes)
        if (
            record.provenance.source_type == "source_extraction"
            and notes.get("extraction_id")
        ):
            return "extracted"
        return "manual"

    def by_source(self, source_id: str) -> list[ProvenanceHit]:
        """Everything currently in the Build Pack derived from one source.

        Each hit preserves the source id, the extraction id where
        available, the reconciliation decision where available, and the
        Build Pack location. Records without provenance -- or with
        provenance for a different source -- simply do not match; their
        ``provenance_kind`` reports that fact instead of inventing linkage.
        """
        if not isinstance(source_id, str) or not source_id.strip():
            raise QueryError("source_id must be a non-empty string")
        reconciliation = self._reconciliation

        hits: list[ProvenanceHit] = []
        for section_name in sorted(self._pack.sections):
            for record in self._pack.sections[section_name]:
                reference = (
                    record.provenance.source_reference
                    if record.provenance is not None
                    else None
                )
                if not _provenance_reference_matches(reference, source_id):
                    continue
                kind = self.provenance_kind(record)
                notes = _parse_notes_kv(
                    record.provenance.notes if record.provenance is not None else None
                )
                extraction_id = notes.get("extraction_id")
                reconciliation_id = notes.get("reconciliation_id")
                reconciliation_state: str | None = None
                if reconciliation is not None and extraction_id:
                    linked = reconciliation.find_by_extraction_id(extraction_id)
                    if linked is not None:
                        reconciliation_state = linked.state.value
                        reconciliation_id = linked.reconciliation_id
                hits.append(
                    ProvenanceHit(
                        section=section_name,
                        record_id=record.record_id,
                        status=record.status.value,
                        fields=dict(record.fields),
                        provenance=(
                            record.provenance.to_dict()
                            if record.provenance is not None
                            else None
                        ),
                        notes=record.notes,
                        provenance_kind=kind,
                        extraction_id=extraction_id,
                        reconciliation_id=reconciliation_id,
                        reconciliation_state=reconciliation_state,
                    )
                )
        hits.sort(key=_sort_key_hit)
        return hits

    # -- reconciliation visibility ---------------------------------------------

    def deferred_candidates(self) -> list[ReconciledCandidate]:
        """Candidates in the deferred (non-terminal) review state."""
        reconciliation = self._require_reconciliation()
        return sorted(
            (
                candidate
                for candidate in reconciliation.candidates
                if candidate.state is ReconciliationState.DEFERRED
            ),
            key=lambda candidate: candidate.reconciliation_id,
        )

    def contradictions(
        self, *, resolved: bool | None = None
    ) -> list[ReconciliationContradiction]:
        """Reconciliation contradictions, optionally filtered by resolution.

        ``resolved=None`` returns all; nothing is resolved here.
        """
        reconciliation = self._require_reconciliation()
        items = list(reconciliation.contradictions)
        if resolved is not None:
            items = [
                c
                for c in items
                if (c.state is ResolutionStatus.RESOLVED) == resolved
            ]
        return sorted(items, key=lambda c: c.contradiction_id)

    def unresolved_contradictions(self) -> list[ReconciliationContradiction]:
        return self.contradictions(resolved=False)

    def resolved_contradictions(self) -> list[ReconciliationContradiction]:
        return self.contradictions(resolved=True)

    def contradiction_detail(
        self, contradiction_id: str
    ) -> ContradictionDetail:
        """One contradiction with both competing values identified.

        ``existing`` is the live canonical record (section, id, status,
        fields); ``candidate`` is the reconciled candidate (id, text,
        state, source linkage). ``resolution`` holds the decision when the
        contradiction was resolved, else ``None``. This is visibility only.
        """
        reconciliation = self._require_reconciliation()
        contradiction = reconciliation.get_contradiction(contradiction_id)

        existing: dict[str, Any] | None = None
        for section_name in sorted(self._pack.sections):
            for record in self._pack.sections[section_name]:
                if record.record_id == contradiction.existing_record_id:
                    existing = {
                        "section": section_name,
                        "record_id": record.record_id,
                        "status": record.status.value,
                        "fields": dict(record.fields),
                    }
                    break
            if existing is not None:
                break

        candidate: dict[str, Any] | None = None
        try:
            cand = reconciliation.get_candidate(contradiction.candidate_id)
        except ReconciliationError:
            cand = None
        if cand is not None:
            candidate = {
                "reconciliation_id": cand.reconciliation_id,
                "extraction_id": cand.extraction_id,
                "text": cand.text,
                "state": cand.state.value,
                "source_id": cand.source_id,
                "category": cand.category.value,
            }

        resolution: dict[str, Any] | None = None
        if contradiction.state is ResolutionStatus.RESOLVED:
            resolution = {
                "status": contradiction.state.value,
                "decision": contradiction.resolution,
                "note": contradiction.resolution_note,
                "resolved_at": contradiction.resolved_at,
            }

        return ContradictionDetail(
            contradiction=contradiction.to_dict(),
            existing=existing,
            candidate=candidate,
            resolution=resolution,
        )

    # -- revisions ---------------------------------------------------------------

    def revisions(self) -> list[Revision]:
        """Change history in chronological order. Records are never rewritten."""
        return list(self._pack.change_history)

    def revision(self, number: int) -> Revision:
        """One revision by its number."""
        for rev in self._pack.change_history:
            if rev.revision == number:
                return rev
        raise QueryError(f"no revision numbered {number!r}")

    @staticmethod
    def revision_sources(revision: Revision) -> dict[str, Any] | None:
        """Source/provenance recorded on a revision, when present.

        Extracted from the revision's new_state provenance snapshot where
        one was stored. Brick 5 acceptance revisions instead record the
        source id in their description ("... from source <source_id>");
        that is reported as a source reference, never expanded into
        provenance the revision does not carry. ``None`` means the
        revision records no source at all.
        """
        new_state = revision.new_state
        if isinstance(new_state, dict):
            provenance = new_state.get("provenance")
            if isinstance(provenance, dict):
                return {
                    "source_type": provenance.get("source_type"),
                    "source_reference": provenance.get("source_reference"),
                    "notes": provenance.get("notes"),
                }
        marker = "from source "
        index = revision.description.rfind(marker)
        if index != -1:
            source_id = revision.description[index + len(marker) :].strip()
            if source_id:
                return {
                    "source_type": None,
                    "source_reference": source_id,
                    "notes": None,
                }
        return None

    # -- source integrity ----------------------------------------------------------

    def source_integrity(self) -> list[dict[str, Any]]:
        """Integrity status for every source, in deterministic catalog order.

        Reports only; a changed or missing file is not a Build Pack change
        and is never reinterpreted as one.
        """
        catalog = self._require_catalog()
        return [
            {
                "source_id": status.source_id,
                "state": status.state.value,
                "recorded_sha256": status.recorded_sha256,
                "actual_sha256": status.actual_sha256,
            }
            for status in catalog.verify_all()
        ]

    def integrity_warnings(self) -> list[dict[str, Any]]:
        """Sources whose files are changed or missing (intact ones excluded)."""
        return [
            status
            for status in self.source_integrity()
            if status["state"] != SourceIntegrity.INTACT.value
        ]

    # -- reports -------------------------------------------------------------------

    def what_do_we_know(self) -> KnowledgeReport:
        """Canonical information grouped by information status.

        Machine-readable; no narrative, no inference about correctness.
        """
        groups: dict[str, list[RecordHit]] = {}
        for status in InfoStatus:
            groups[status.value] = []
        for section_name in sorted(self._pack.sections):
            for record in self._pack.sections[section_name]:
                groups[record.status.value].append(
                    RecordHit.from_record(section_name, record)
                )
        for hits in groups.values():
            hits.sort(key=_sort_key_hit)
        return KnowledgeReport(groups=groups)

    def what_remains(self) -> RemainingWork:
        """Unresolved work, with categories kept strictly distinct."""
        return RemainingWork(
            open_issues=self.open_issues(),
            unknown_entries=self.unknown(),
            deferred_candidates=[
                candidate.to_dict() for candidate in self.deferred_candidates()
            ],
            unresolved_contradictions=[
                contradiction.to_dict()
                for contradiction in self.unresolved_contradictions()
            ],
            source_integrity_warnings=self.integrity_warnings(),
        )

    # -- search ----------------------------------------------------------------------

    def search(
        self,
        query_text: str | None = None,
        *,
        section: str | None = None,
        status: InfoStatus | str | None = None,
        field: str | None = None,
        value: Any = None,
    ) -> list[RecordHit]:
        """Deterministic search over canonical structured information.

        - ``query_text``: case-insensitive substring over record text
        - ``field``/``value``: exact field/value match
        - ``section``/``status``: further filters

        No semantic search, no embeddings, no LLM -- exact and
        substring matching only.
        """
        if (
            query_text is None
            and field is None
            and section is None
            and status is None
        ):
            raise QueryError("search needs at least one criterion")
        if (field is None) != (value is None):
            raise QueryError("field and value must be given together")

        hits = self.records(section=section, status=status, text=query_text)
        if field is not None:
            hits = [
                hit
                for hit in hits
                if field in hit.fields and hit.fields[field] == value
            ]
        return hits


# ---------------------------------------------------------------------------
# structural diff
# ---------------------------------------------------------------------------


def _record_key(section: str, record: BuildRecord) -> tuple[str, str]:
    return (section, record.record_id)


def _record_snapshot(record: BuildRecord) -> dict[str, Any]:
    return {
        "status": record.status.value,
        "fields": dict(record.fields),
        "provenance": (
            record.provenance.to_dict() if record.provenance is not None else None
        ),
        "notes": record.notes,
    }


def diff_packs(before: BuildPack, after: BuildPack) -> ChangeSet:
    """Structural comparison between two Build Pack states.

    Identifies added, removed, modified, and unchanged canonical records
    (plus project-identity changes). The comparison is structural -- field
    paths and old/new values -- never prose, never an LLM. Compare the
    current pack against a saved earlier pack state (for example a copy of
    ``buildpack/buildpack.json`` from before a review session); no Git is
    involved. Change history and contradictions are queried separately --
    they are the history itself, not pack state.

    Read-only: both packs are left untouched.
    """
    if not isinstance(before, BuildPack) or not isinstance(after, BuildPack):
        raise QueryError("diff_packs compares two BuildPack objects")

    changeset = ChangeSet()
    before_index = {
        _record_key(section, record): record
        for section in before.sections
        for record in before.sections[section]
    }
    after_index = {
        _record_key(section, record): record
        for section in after.sections
        for record in after.sections[section]
    }

    # -- identity --------------------------------------------------------
    if before.identity.to_dict() != after.identity.to_dict():
        changeset.modified.append(
            ChangeEntry(
                change="modified",
                section="identity",
                record_id="identity",
                path="identity",
                old_value=before.identity.to_dict(),
                new_value=after.identity.to_dict(),
                provenance=None,
            )
        )

    # -- records -----------------------------------------------------------
    for key in sorted(set(before_index) & set(after_index)):
        section, record_id = key
        old_record = before_index[key]
        new_record = after_index[key]
        provenance = (
            new_record.provenance.to_dict()
            if new_record.provenance is not None
            else None
        )
        modified_here: list[ChangeEntry] = []

        if old_record.status is not new_record.status:
            modified_here.append(
                ChangeEntry(
                    change="modified",
                    section=section,
                    record_id=record_id,
                    path="status",
                    old_value=old_record.status.value,
                    new_value=new_record.status.value,
                    provenance=provenance,
                )
            )

        old_fields = old_record.fields
        new_fields = new_record.fields
        for name in sorted(set(old_fields) | set(new_fields)):
            in_old = name in old_fields
            in_new = name in new_fields
            if in_old and in_new and old_fields[name] != new_fields[name]:
                modified_here.append(
                    ChangeEntry(
                        change="modified",
                        section=section,
                        record_id=record_id,
                        path=f"fields.{name}",
                        old_value=old_fields[name],
                        new_value=new_fields[name],
                        provenance=provenance,
                    )
                )
            elif in_old and not in_new:
                modified_here.append(
                    ChangeEntry(
                        change="modified",
                        section=section,
                        record_id=record_id,
                        path=f"fields.{name}",
                        old_value=old_fields[name],
                        new_value=None,
                        provenance=provenance,
                    )
                )
            elif in_new and not in_old:
                modified_here.append(
                    ChangeEntry(
                        change="modified",
                        section=section,
                        record_id=record_id,
                        path=f"fields.{name}",
                        old_value=None,
                        new_value=new_fields[name],
                        provenance=provenance,
                    )
                )

        old_prov = _record_snapshot(old_record)["provenance"]
        new_prov = _record_snapshot(new_record)["provenance"]
        if old_prov != new_prov:
            modified_here.append(
                ChangeEntry(
                    change="modified",
                    section=section,
                    record_id=record_id,
                    path="provenance",
                    old_value=old_prov,
                    new_value=new_prov,
                    provenance=provenance,
                )
            )

        if old_record.notes != new_record.notes:
            modified_here.append(
                ChangeEntry(
                    change="modified",
                    section=section,
                    record_id=record_id,
                    path="notes",
                    old_value=old_record.notes,
                    new_value=new_record.notes,
                    provenance=provenance,
                )
            )

        if modified_here:
            changeset.modified.extend(modified_here)
        else:
            changeset.unchanged.append({"section": section, "record_id": record_id})

    for key in sorted(set(after_index) - set(before_index)):
        section, record_id = key
        record = after_index[key]
        changeset.added.append(
            ChangeEntry(
                change="added",
                section=section,
                record_id=record_id,
                path=None,
                old_value=None,
                new_value=_record_snapshot(record),
                provenance=(
                    record.provenance.to_dict()
                    if record.provenance is not None
                    else None
                ),
            )
        )

    for key in sorted(set(before_index) - set(after_index)):
        section, record_id = key
        record = before_index[key]
        changeset.removed.append(
            ChangeEntry(
                change="removed",
                section=section,
                record_id=record_id,
                path=None,
                old_value=_record_snapshot(record),
                new_value=None,
                provenance=(
                    record.provenance.to_dict()
                    if record.provenance is not None
                    else None
                ),
            )
        )

    changeset.unchanged.sort(key=lambda pair: (pair["section"], pair["record_id"]))
    return changeset
