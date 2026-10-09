"""
Source intake for AgentIvan (Brick 2).

Intake copies or stores raw material into the project WITHOUT modifying
it:

- Files are copied byte-for-byte; not a single newline is translated.
- Supplied text is stored verbatim as UTF-8.
- References (URLs/citations) are stored as records and never fetched.

Every intake produces a SourceRecord carrying the metadata needed to
identify the source later: stable id, kind, original name, relative
project path, intake timestamp, media type, content hash, size, and a
human label. The original bytes stay available under ``sources/`` next
to the record.

Hashing: the identity hash (``sha256``) is newline-insensitive for text
so the same logical content with LF vs CRLF vs CR line endings is
recognized as identical instead of producing a false "changed" signal
when a project moves between Linux and Windows. The stored bytes are
never normalized -- only the hash input is.

After every file write the bytes are read back and compared to what was
supplied; intake is only reported successful when the source was
actually preserved.

Stdlib only. No network, no AI.
"""

from __future__ import annotations

import hashlib
import mimetypes
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .paths import join_posix, sanitize_filename, validate_supplied_path
from .sources import SourceKind, SourceRecord, SourceStatus

if TYPE_CHECKING:
    from .project import Project

__all__ = [
    "IntakeError",
    "utc_now_iso",
    "sha256_hex",
    "normalize_newlines",
    "identity_hash_for_bytes",
    "identity_hash_for_text",
    "intake_text",
    "intake_file",
    "intake_reference",
]


class IntakeError(ValueError):
    """Raised when source material cannot be safely ingested."""


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string (platform-neutral)."""
    return datetime.now(timezone.utc).isoformat()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_newlines(text: str) -> str:
    """Canonicalize CRLF/CR to LF for identity comparison only."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def identity_hash_for_text(text: str) -> str:
    """Newline-insensitive identity hash for text content."""
    return sha256_hex(normalize_newlines(text).encode("utf-8"))


def identity_hash_for_bytes(data: bytes, media_type: str | None) -> str:
    """Identity hash for file bytes.

    Text-decodable content is hashed newline-insensitively so a project
    moved between Linux and Windows does not report false changes;
    binary content is hashed byte-for-byte.
    """
    text_hint = (media_type or "").split(";")[0].strip().lower()
    looks_textual = text_hint.startswith("text/") or text_hint in {
        "application/json",
        "application/xml",
        "application/javascript",
    }
    if looks_textual:
        try:
            return identity_hash_for_text(data.decode("utf-8"))
        except UnicodeDecodeError:
            pass
    else:
        try:
            data.decode("utf-8")
            return identity_hash_for_text(data.decode("utf-8"))
        except UnicodeDecodeError:
            pass
    return sha256_hex(data)


def _guess_media_type(filename: str | None, default: str) -> str:
    if filename:
        guessed, _ = mimetypes.guess_type(filename)
        if guessed:
            if guessed.startswith("text/"):
                return f"{guessed}; charset=utf-8"
            return guessed
    return default


def _new_source_id() -> str:
    return uuid.uuid4().hex


def _store_bytes(project: "Project", source_id: str, data: bytes, name_hint: str) -> str:
    """Write bytes verbatim under sources/ and verify the write.

    Returns the POSIX-style relative path of the stored file.
    """
    safe_name = sanitize_filename(name_hint)
    stored_name = f"{source_id}__{safe_name}"
    relative = join_posix("sources", stored_name)
    target = project.sources_dir / stored_name
    try:
        target.write_bytes(data)
    except OSError as exc:
        raise IntakeError(f"could not preserve source bytes: {exc}") from exc
    # Prove the source was actually preserved before claiming success.
    try:
        written = target.read_bytes()
    except OSError as exc:
        raise IntakeError(f"source written but not readable: {exc}") from exc
    if written != data:
        raise IntakeError("source write verification failed: bytes differ")
    return relative


def intake_text(
    project: "Project",
    text: str,
    *,
    label: str,
    filename: str | None = None,
    media_type: str | None = None,
    description: str = "",
) -> SourceRecord:
    """Intake text supplied directly. Stored verbatim as UTF-8."""
    if not isinstance(text, str):
        raise IntakeError("text source must be a string")
    if not isinstance(label, str) or not label.strip():
        raise IntakeError("label must be a non-empty string")
    original: str | None = None
    if filename is not None:
        original = validate_supplied_path(filename)
    data = text.encode("utf-8")  # exact bytes; no newline translation
    resolved_media = media_type or _guess_media_type(
        original, "text/plain; charset=utf-8"
    )
    source_id = _new_source_id()
    name_hint = original.split("/")[-1] if original else f"{label}.txt"
    relative = _store_bytes(project, source_id, data, name_hint)
    return SourceRecord(
        source_id=source_id,
        kind=SourceKind.TEXT,
        label=label.strip(),
        intake_timestamp=utc_now_iso(),
        status=SourceStatus.STORED,
        original_filename=original,
        relative_path=relative,
        media_type=resolved_media,
        sha256=identity_hash_for_text(text),
        byte_size=len(data),
        description=description,
    )


def intake_file(
    project: "Project",
    source_path: str | Path,
    *,
    label: str,
    description: str = "",
) -> SourceRecord:
    """Intake an existing file. Copied byte-for-byte, never modified."""
    if not isinstance(label, str) or not label.strip():
        raise IntakeError("label must be a non-empty string")
    path = Path(source_path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise IntakeError(f"source cannot be read: {exc}") from exc
    if not path.is_file():
        raise IntakeError(f"source is not a file: {path}")
    original = validate_supplied_path(path.name)
    media_type = _guess_media_type(original, "application/octet-stream")
    kind = SourceKind.IMAGE if media_type.startswith("image/") else SourceKind.FILE
    source_id = _new_source_id()
    relative = _store_bytes(project, source_id, data, original.split("/")[-1])
    return SourceRecord(
        source_id=source_id,
        kind=kind,
        label=label.strip(),
        intake_timestamp=utc_now_iso(),
        status=SourceStatus.STORED,
        original_filename=original,
        relative_path=relative,
        media_type=media_type,
        sha256=identity_hash_for_bytes(data, media_type),
        byte_size=len(data),
        description=description,
    )


def intake_reference(
    project: "Project",
    reference: str,
    *,
    label: str,
    description: str = "",
) -> SourceRecord:
    """Intake a URL/citation as a reference record. Never fetched."""
    if not isinstance(reference, str) or not reference.strip():
        raise IntakeError("reference must be a non-empty string")
    if not isinstance(label, str) or not label.strip():
        raise IntakeError("label must be a non-empty string")
    _ = project  # references store no file; the record is the original
    return SourceRecord(
        source_id=_new_source_id(),
        kind=SourceKind.REFERENCE,
        label=label.strip(),
        intake_timestamp=utc_now_iso(),
        status=SourceStatus.STORED,
        reference=reference.strip(),
        description=description,
    )
