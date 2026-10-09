"""
Source catalog + retrieval layer for AgentIvan (Brick 3).

Sits between the Brick 2 project workspace / source intake and the
future AI processing layer. The catalog is a *stateless view* over a
project's source records:

- It persists nothing of its own. ``project.json`` plus the preserved
  files under ``sources/`` remain the single source of truth; a catalog
  can always be rebuilt from them.
- Listing order is deterministic and platform-independent: it never
  depends on filesystem enumeration order or hash-map iteration.
- Retrieval returns the original material verbatim. References are
  returned as stored and are never fetched.
- Integrity checks recompute the identity hash and compare it with the
  recorded one. They never repair, overwrite, or delete anything.
- Removal is explicit and keeps "remove the catalog record" separate
  from "delete the preserved file".

This layer assigns no Build Pack statuses, infers no meaning, summarizes
nothing, and performs no AI processing of any kind.

Stdlib only. No network, no database.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Iterable

from .intake import identity_hash_for_bytes, utc_now_iso
from .sources import SourceKind, SourceRecord, SourceStatus

if TYPE_CHECKING:
    from .project import Project

__all__ = [
    "CatalogError",
    "SourceIntegrity",
    "IntegrityStatus",
    "SourceCatalog",
    "catalog_order_key",
]


class CatalogError(ValueError):
    """Raised for invalid catalog operations (unknown id, unsafe path, ...)."""


class SourceIntegrity(Enum):
    """Result of comparing a preserved file with its recorded identity.

    This is source integrity information only -- it says nothing about
    project meaning and must not be confused with a Build Pack
    contradiction (Brick 1).
    """

    INTACT = "intact"  # file present and identity hash matches the record
    CHANGED = "changed"  # file present but no longer matches the record
    MISSING = "missing"  # record exists but the preserved file is gone


@dataclass(frozen=True)
class IntegrityStatus:
    source_id: str
    state: SourceIntegrity
    recorded_sha256: str | None  # hash stored on the SourceRecord
    actual_sha256: str | None  # hash recomputed now (None when missing)


def catalog_order_key(record: SourceRecord) -> tuple[str, str, str]:
    """Deterministic, platform-independent ordering for source listings.

    Sorts by ``(kind, label, source_id)``. The unique ``source_id`` tie
    break makes the order total: the same project always lists the same
    way on Linux and Windows, regardless of filesystem enumeration
    order or insertion order.
    """
    return (record.kind.value, record.label.casefold(), record.source_id)


# Metadata fields covered by the free-text search. Documented so the
# search stays predictable and testable.
_SEARCH_FIELDS = (
    "label",
    "original_filename",
    "description",
    "media_type",
    "reference",
    "source_id",
)


class SourceCatalog:
    """Deterministic access to one project's source records.

    The catalog holds no state of its own: every operation reads the
    live ``project.sources`` list, and ``rebuild()`` reopens the project
    from disk. Mutating operations (intake, removal) change the in-memory
    project; the caller persists with ``project.save()``, exactly like
    Brick 2 project-level intake.
    """

    def __init__(self, project: "Project") -> None:
        self._project = project

    # ------------------------------------------------------------------
    # registration (delegates to the existing project intake layer)
    # ------------------------------------------------------------------
    def intake_text(self, text: str, **kwargs: Any) -> SourceRecord:
        """Register supplied text via project intake. Caller saves."""
        return self._project.intake_text(text, **kwargs)

    def intake_file(self, source_path: str | Path, **kwargs: Any) -> SourceRecord:
        """Register a file via project intake. Caller saves."""
        return self._project.intake_file(source_path, **kwargs)

    def intake_reference(self, reference: str, **kwargs: Any) -> SourceRecord:
        """Register a reference via project intake. Caller saves."""
        return self._project.intake_reference(reference, **kwargs)

    # ------------------------------------------------------------------
    # basic access
    # ------------------------------------------------------------------
    def get(self, source_id: str) -> SourceRecord:
        """Return the record for ``source_id``; raise CatalogError if unknown."""
        for record in self._project.sources:
            if record.source_id == source_id:
                return record
        raise CatalogError(f"unknown source_id: {source_id}")

    def list(self, *, kind: SourceKind | Iterable[SourceKind] | None = None) -> list[SourceRecord]:
        """All source records in deterministic catalog order."""
        records = list(self._project.sources)
        if kind is not None:
            kinds = {kind} if isinstance(kind, SourceKind) else set(kind)
            records = [r for r in records if r.kind in kinds]
        return sorted(records, key=catalog_order_key)

    def count(self) -> int:
        return len(self._project.sources)

    def exists(self, source_id: str) -> bool:
        return any(r.source_id == source_id for r in self._project.sources)

    # ------------------------------------------------------------------
    # retrieval
    # ------------------------------------------------------------------
    def _stored_file_path(self, record: SourceRecord) -> Path:
        """Resolve a record's project-relative path to an absolute Path.

        The stored path is POSIX-style by Brick 2 convention. Parts are
        validated so a corrupted project.json cannot escape the project
        directory via traversal or absolute paths.
        """
        rel = record.relative_path
        if not rel:
            raise CatalogError(f"source {record.source_id} has no stored file")
        posix = PurePosixPath(rel)
        if posix.is_absolute():
            raise CatalogError(f"stored path is absolute: {rel!r}")
        parts = posix.parts
        for part in parts:
            if part in ("", ".", ".."):
                raise CatalogError(f"stored path escapes project: {rel!r}")
            if "\x00" in part:
                raise CatalogError(f"stored path contains NUL: {rel!r}")
        return self._project.root.joinpath(*parts)

    def stored_path(self, source_id: str) -> Path:
        """Absolute path of the preserved file for ``source_id``.

        The file itself is not opened or modified. Raises CatalogError
        for references (they store no file) and for unsafe stored paths.
        """
        record = self.get(source_id)
        if record.kind is SourceKind.REFERENCE:
            raise CatalogError("reference sources store no file")
        return self._stored_file_path(record)

    def read_bytes(self, source_id: str) -> bytes:
        """Raw preserved bytes of the stored file, byte-for-byte."""
        record = self.get(source_id)
        path = self._stored_file_path(record)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise CatalogError(f"stored file cannot be read: {exc}") from exc

    def read(self, source_id: str) -> str | bytes:
        """Return the original source material for ``source_id``.

        - text: the original text, decoded verbatim (no normalization)
        - file/image: the original bytes, byte-for-byte
        - reference: the stored reference string, exactly as recorded;
          nothing is fetched

        Retrieval never modifies the stored material.
        """
        record = self.get(source_id)
        if record.kind is SourceKind.REFERENCE:
            assert record.reference is not None
            return record.reference
        data = self.read_bytes(source_id)
        if record.kind is SourceKind.TEXT:
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CatalogError(
                    f"text source is not valid UTF-8: {exc}"
                ) from exc
        return data

    # ------------------------------------------------------------------
    # search / filter
    # ------------------------------------------------------------------
    def search(
        self,
        query: str | None = None,
        *,
        kind: SourceKind | Iterable[SourceKind] | None = None,
        label: str | None = None,
        original_filename: str | None = None,
        media_type: str | None = None,
        status: SourceStatus | None = None,
        source_id: str | None = None,
    ) -> list[SourceRecord]:
        """Deterministic source discovery.

        ``query`` is a case-insensitive substring matched against the
        documented metadata fields (label, original filename,
        description, media type, reference, source_id). The keyword
        filters are exact matches. This is plain string matching -- no
        semantic search, no embeddings, no LLM.

        Results come back in deterministic catalog order.
        """
        records = list(self._project.sources)
        if kind is not None:
            kinds = {kind} if isinstance(kind, SourceKind) else set(kind)
            records = [r for r in records if r.kind in kinds]
        if label is not None:
            records = [r for r in records if r.label == label]
        if original_filename is not None:
            records = [r for r in records if r.original_filename == original_filename]
        if media_type is not None:
            records = [r for r in records if r.media_type == media_type]
        if status is not None:
            records = [r for r in records if r.status is status]
        if source_id is not None:
            records = [r for r in records if r.source_id == source_id]
        if query is not None:
            needle = query.casefold()
            records = [r for r in records if self._matches(r, needle)]
        return sorted(records, key=catalog_order_key)

    @staticmethod
    def _matches(record: SourceRecord, needle: str) -> bool:
        for field_name in _SEARCH_FIELDS:
            value = getattr(record, field_name)
            if isinstance(value, str) and needle in value.casefold():
                return True
        return False

    def duplicates(self) -> list[list[str]]:
        """Groups of source_ids whose recorded identity hashes match.

        Identification only: nothing is deduplicated or deleted. Text
        hashes are newline-insensitive (Brick 2), so the same logical
        content stored with different line endings is recognized as
        duplicate content.
        """
        by_hash: dict[str, list[str]] = {}
        for record in self._project.sources:
            if record.kind is SourceKind.REFERENCE or not record.sha256:
                continue
            by_hash.setdefault(record.sha256, []).append(record.source_id)
        groups = [sorted(ids) for ids in by_hash.values() if len(ids) > 1]
        return sorted(groups)

    # ------------------------------------------------------------------
    # integrity
    # ------------------------------------------------------------------
    def verify(self, source_id: str) -> IntegrityStatus:
        """Check one source against its recorded identity hash.

        Reference sources carry no file, so their record is the original:
        they always report INTACT. File/text/image sources report
        INTACT, CHANGED, or MISSING. Nothing is repaired or overwritten;
        the status is returned for later logic to decide on.
        """
        record = self.get(source_id)
        if record.kind is SourceKind.REFERENCE:
            return IntegrityStatus(
                source_id=record.source_id,
                state=SourceIntegrity.INTACT,
                recorded_sha256=None,
                actual_sha256=None,
            )
        path = self._stored_file_path(record)
        try:
            data = path.read_bytes()
        except OSError:
            return IntegrityStatus(
                source_id=record.source_id,
                state=SourceIntegrity.MISSING,
                recorded_sha256=record.sha256,
                actual_sha256=None,
            )
        actual = identity_hash_for_bytes(data, record.media_type)
        state = (
            SourceIntegrity.INTACT
            if actual == record.sha256
            else SourceIntegrity.CHANGED
        )
        return IntegrityStatus(
            source_id=record.source_id,
            state=state,
            recorded_sha256=record.sha256,
            actual_sha256=actual,
        )

    def verify_all(self) -> list[IntegrityStatus]:
        """Integrity status for every source, in deterministic order."""
        return [self.verify(r.source_id) for r in self.list()]

    # ------------------------------------------------------------------
    # explicit removal
    # ------------------------------------------------------------------
    def remove(self, source_id: str, *, delete_file: bool = False) -> SourceRecord:
        """Explicitly remove a source record from the catalog.

        By default only the record is removed; the preserved file stays
        on disk. Pass ``delete_file=True`` to also delete the preserved
        file -- the two actions are never performed silently together.
        Nothing is ever removed automatically (not for duplicates,
        changed hashes, or failed reads). The caller persists with
        ``project.save()``.
        """
        record = self.get(source_id)  # raises CatalogError when unknown
        if delete_file and record.kind is not SourceKind.REFERENCE:
            path = self._stored_file_path(record)
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                raise CatalogError(f"stored file cannot be deleted: {exc}") from exc
        self._project.sources = [
            r for r in self._project.sources if r.source_id != source_id
        ]
        self._project.updated_at = utc_now_iso()
        return record

    # ------------------------------------------------------------------
    # rebuild
    # ------------------------------------------------------------------
    def rebuild(self) -> "SourceCatalog":
        """Rebuild the catalog from the on-disk project data.

        The catalog keeps no persisted metadata of its own, so a fresh
        catalog bound to a freshly loaded project is the complete
        rebuild. The project directory is the single source of truth.
        """
        from .project import Project

        return SourceCatalog(Project.load(self._project.root))
