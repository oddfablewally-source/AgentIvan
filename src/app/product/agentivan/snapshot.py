"""Deterministic project snapshots / portable archives (Brick 9).

A snapshot captures an entire AgentIvan project as a self-contained,
portable ZIP archive and restores it into another location without
losing canonical project information, source files, extraction data,
reconciliation data, or project identity.

This is NOT cloud sync, collaboration, version control, Git, an
automatic backup daemon, or a database. It is an explicit,
programmatic transport/backup artifact. The canonical project on
disk remains the source of truth while it exists.

Archive layout (POSIX-style member names, lexicographic order,
manifest first)::

    manifest.json
    project.json
    buildpack/buildpack.json
    extractions/extractions.json
    reconciliation/reconciliation.json
    sources/<stored source files>

Generated Markdown/PDF exports (``exports/``) are NOT included: they
are regenerable presentation artifacts, not canonical project state.

Determinism: identical project state produces byte-identical archive
bytes. All ZIP metadata is fixed (timestamps, compression method and
level, permissions, archive comment, member order); no filesystem
modification times, hostnames, usernames, or current times appear.

Stdlib only: zipfile, hashlib, json, pathlib, io, shutil, tempfile.
No network, no AI, no UI, no cloud, no version control.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Union

from .paths import UnsafePathError, validate_supplied_path
from .project import Project


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

SNAPSHOT_FORMAT_NAME = "agentivan-project-snapshot"
SNAPSHOT_FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
PROJECT_JSON_NAME = "project.json"
EXPORTS_DIR_NAME = "exports"

# Canonical project-state trees included in a snapshot, byte-for-byte.
CANONICAL_TREES = ("sources", "extractions", "buildpack", "reconciliation")

# ZIP requires a timestamp on every member. A fixed value keeps archives
# deterministic; filesystem modification times are never used.
_FIXED_ZIP_DATE_TIME = (2000, 1, 1, 0, 0, 0)

# Fixed compression: DEFLATE level 6, chosen for portability and
# determinism. Level is pinned so the stdlib default can never drift.
_FIXED_COMPRESS_LEVEL = 6

# Fixed Unix file mode recorded in member external attributes.
_FIXED_UNIX_MODE = 0o644


class SnapshotError(Exception):
    """Raised when a snapshot cannot be created or an archive is invalid."""


# ---------------------------------------------------------------------------
# result wrapper (immutable, serializable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapshotResult:
    """Outcome of a snapshot export. Immutable and JSON-serializable.

    ``data`` holds the archive bytes only when no destination was given;
    it is intentionally excluded from ``to_dict()`` (raw bytes are not
    JSON). ``archive_path`` is the destination path as given, or None.
    """

    project_id: str
    format_version: int
    member_count: int
    payload_bytes: int
    archive_bytes: int
    archive_sha256: str
    archive_path: str | None = None
    data: bytes | None = field(default=None, compare=False, repr=False)

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "format_version": self.format_version,
            "member_count": self.member_count,
            "payload_bytes": self.payload_bytes,
            "archive_bytes": self.archive_bytes,
            "archive_sha256": self.archive_sha256,
            "archive_path": self.archive_path,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SnapshotResult":
        if not isinstance(data, dict):
            raise SnapshotError("snapshot result is not an object")
        try:
            return cls(
                project_id=data["project_id"],
                format_version=data["format_version"],
                member_count=data["member_count"],
                payload_bytes=data["payload_bytes"],
                archive_bytes=data["archive_bytes"],
                archive_sha256=data["archive_sha256"],
                archive_path=data.get("archive_path"),
            )
        except KeyError as exc:
            raise SnapshotError(f"snapshot result missing field: {exc}") from exc

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "SnapshotResult":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SnapshotError(f"snapshot result is not valid JSON: {exc}") from exc
        return cls.from_dict(data)


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _deterministic_json(payload: object) -> bytes:
    """Canonical JSON bytes: sorted keys, fixed indent, LF endings, UTF-8."""
    return (
        json.dumps(
            payload, indent=2, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
        + b"\n"
    )


def _validate_member_name(name: object) -> str:
    """Validate an archive member name and return its normalized POSIX form.

    Rejects absolute paths, ``..`` traversal, Windows drive letters,
    UNC/network paths, NUL characters, and directory entries. The name
    must already be in normalized form: normalization is a validation
    signal, not a repair step.
    """
    if not isinstance(name, str):
        raise SnapshotError(f"archive member name is not a string: {name!r}")
    if name.endswith("/"):
        raise SnapshotError(f"directory entries are not allowed: {name!r}")
    try:
        normalized = validate_supplied_path(name)
    except UnsafePathError as exc:
        raise SnapshotError(f"unsafe archive member path: {exc}") from exc
    if normalized != name:
        raise SnapshotError(
            f"archive member path is not normalized: {name!r}"
        )
    return normalized


def _collect_members(root: Path) -> list[tuple[str, bytes]]:
    """Collect canonical project-state files as (POSIX name, bytes) pairs.

    Copies existing canonical files byte-for-byte; canonical JSON is
    never re-serialized through a different serializer. Generated
    exports (``exports/``) are excluded by design. Symlinks and any
    unexpected top-level entries fail loudly rather than being
    silently dropped or silently followed.
    """
    root = Path(root)
    project_json_path = root / PROJECT_JSON_NAME
    try:
        project_json_bytes = project_json_path.read_bytes()
    except OSError as exc:
        raise SnapshotError(
            f"project.json cannot be read: {exc}"
        ) from exc

    collected: dict[str, bytes] = {}
    try:
        top_entries = list(root.iterdir())
    except OSError as exc:
        raise SnapshotError(f"project directory cannot be listed: {exc}") from exc

    unexpected: list[str] = []
    for entry in top_entries:
        name = entry.name
        if name == PROJECT_JSON_NAME:
            continue
        if name in CANONICAL_TREES:
            continue
        if name == EXPORTS_DIR_NAME and entry.is_dir():
            continue  # generated exports: regenerable, excluded by default
        unexpected.append(name)
    if unexpected:
        raise SnapshotError(
            "unexpected top-level project entries are not snapshotted: "
            + ", ".join(sorted(unexpected))
        )

    for tree in CANONICAL_TREES:
        tree_dir = root / tree
        if not tree_dir.exists():
            continue
        if not tree_dir.is_dir():
            raise SnapshotError(f"expected a directory: {tree}")
        try:
            files = sorted(
                (p for p in tree_dir.rglob("*") if p.is_file() or p.is_symlink()),
                key=lambda p: p.relative_to(root).as_posix(),
            )
        except OSError as exc:
            raise SnapshotError(f"{tree}/ cannot be listed: {exc}") from exc
        for path in files:
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise SnapshotError(
                    f"symlinks are not snapshotted: {rel}"
                )
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise SnapshotError(f"source file cannot be read: {rel}: {exc}") from exc
            collected[rel] = data

    members = [(PROJECT_JSON_NAME, project_json_bytes)]
    members.extend(sorted(collected.items(), key=lambda item: item[0]))
    return members


def _project_id_from_bytes(project_json_bytes: bytes) -> str:
    try:
        data = json.loads(project_json_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"project.json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SnapshotError("project.json is not an object")
    project_id = data.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise SnapshotError("project.json has no usable project_id")
    return project_id


def _build_manifest(members: list[tuple[str, bytes]], project_id: str) -> dict:
    """Deterministic manifest: format, version, identity, member table."""
    return {
        "format": SNAPSHOT_FORMAT_NAME,
        "format_version": SNAPSHOT_FORMAT_VERSION,
        "project_id": project_id,
        "members": [
            {
                "path": name,
                "sha256": _sha256_hex(data),
                "size": len(data),
            }
            for name, data in sorted(members, key=lambda item: item[0])
        ],
    }


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename=name, date_time=_FIXED_ZIP_DATE_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3  # Unix
    info.external_attr = _FIXED_UNIX_MODE << 16
    info.flag_bits |= 0x800  # UTF-8 filename
    info.extra = b""
    return info


def _build_archive(manifest_bytes: bytes, members: list[tuple[str, bytes]]) -> bytes:
    """Deterministic ZIP: manifest first, then lexicographic member order."""
    ordered = [(MANIFEST_NAME, manifest_bytes)] + sorted(
        members, key=lambda item: item[0]
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=_FIXED_COMPRESS_LEVEL,
    ) as archive:
        for name, data in ordered:
            archive.writestr(_zip_info(name), data)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# exporter
# ---------------------------------------------------------------------------


class ProjectSnapshotExporter:
    """Create deterministic portable project snapshots. Read-only."""

    def export_project(
        self, project: Project, destination: str | Path | None = None
    ) -> SnapshotResult:
        """Snapshot ``project`` to ``destination`` (or return bytes).

        Never mutates project state: it only reads canonical files.
        ``destination`` may be None (bytes returned in ``result.data``),
        a relative path (resolved against the current working directory),
        or an absolute path. Parent directories are created as needed.
        """
        if not isinstance(project, Project):
            raise SnapshotError("project must be a Project instance")
        root = Path(project.root)

        # Read-only pass: every file is copied verbatim, nothing written.
        members = _collect_members(root)
        project_id = _project_id_from_bytes(
            dict(members)[PROJECT_JSON_NAME]
        )
        manifest_bytes = _deterministic_json(_build_manifest(members, project_id))
        archive_bytes = _build_archive(manifest_bytes, members)

        archive_path: str | None = None
        data: bytes | None = None
        if destination is None:
            data = archive_bytes
        else:
            dest = Path(destination)
            if not dest.is_absolute():
                dest = Path.cwd() / dest
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(archive_bytes)
            except OSError as exc:
                raise SnapshotError(
                    f"snapshot cannot be written to {dest}: {exc}"
                ) from exc
            archive_path = str(dest)

        payload_bytes = sum(len(item[1]) for item in members)
        return SnapshotResult(
            project_id=project_id,
            format_version=SNAPSHOT_FORMAT_VERSION,
            member_count=len(members),
            payload_bytes=payload_bytes,
            archive_bytes=len(archive_bytes),
            archive_sha256=_sha256_hex(archive_bytes),
            archive_path=archive_path,
            data=data,
        )


def export_snapshot(
    project: Project, destination: str | Path | None = None
) -> SnapshotResult:
    """Convenience wrapper around :class:`ProjectSnapshotExporter`."""
    return ProjectSnapshotExporter().export_project(project, destination)


# ---------------------------------------------------------------------------
# importer
# ---------------------------------------------------------------------------


def _read_archive_bytes(archive: Union[str, Path, bytes]) -> bytes:
    if isinstance(archive, (str, Path)):
        path = Path(archive)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise SnapshotError(
                f"archive cannot be read: {path}: {exc}"
            ) from exc
    if isinstance(archive, (bytes, bytearray)):
        return bytes(archive)
    raise SnapshotError("archive must be a path or bytes")


def _validate_archive(data: bytes) -> tuple[dict, dict[str, bytes]]:
    """Full validation before anything is written. Returns (manifest, members)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SnapshotError(f"not a valid ZIP archive: {exc}") from exc

    with archive:
        names = archive.namelist()
        if len(set(names)) != len(names):
            raise SnapshotError("archive contains duplicate member names")
        safe_names = [_validate_member_name(name) for name in names]

        if MANIFEST_NAME not in safe_names:
            raise SnapshotError("archive has no manifest.json")

        try:
            manifest_raw = archive.read(MANIFEST_NAME)
        except KeyError as exc:  # pragma: no cover - namelist said it exists
            raise SnapshotError("manifest.json cannot be read") from exc
        try:
            manifest = json.loads(manifest_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SnapshotError(f"manifest.json is malformed: {exc}") from exc
        if not isinstance(manifest, dict):
            raise SnapshotError("manifest.json is not an object")
        if manifest.get("format") != SNAPSHOT_FORMAT_NAME:
            raise SnapshotError(
                f"not an AgentIvan project snapshot: format={manifest.get('format')!r}"
            )
        version = manifest.get("format_version")
        if version != SNAPSHOT_FORMAT_VERSION:
            raise SnapshotError(
                f"unsupported snapshot format_version: {version!r} "
                f"(this build supports {SNAPSHOT_FORMAT_VERSION})"
            )
        manifest_project_id = manifest.get("project_id")
        if not isinstance(manifest_project_id, str) or not manifest_project_id.strip():
            raise SnapshotError("manifest has no usable project_id")
        raw_members = manifest.get("members")
        if not isinstance(raw_members, list) or not raw_members:
            raise SnapshotError("manifest has no member table")

        expected: dict[str, dict] = {}
        for entry in raw_members:
            if not isinstance(entry, dict):
                raise SnapshotError("manifest member entry is not an object")
            path = entry.get("path")
            size = entry.get("size")
            digest = entry.get("sha256")
            if not isinstance(path, str):
                raise SnapshotError("manifest member has no path")
            _validate_member_name(path)
            if not isinstance(size, int) or size < 0:
                raise SnapshotError(f"manifest member has bad size: {path!r}")
            if not isinstance(digest, str) or len(digest) != 64:
                raise SnapshotError(
                    f"manifest member has bad sha256: {path!r}"
                )
            if path in expected:
                raise SnapshotError(
                    f"manifest lists a duplicate member: {path!r}"
                )
            expected[path] = {"size": size, "sha256": digest.lower()}

        if PROJECT_JSON_NAME not in expected:
            raise SnapshotError("manifest is missing required member project.json")

        actual = set(safe_names)
        expected_names = set(expected) | {MANIFEST_NAME}
        missing = sorted(expected_names - actual)
        extra = sorted(actual - expected_names)
        if missing:
            raise SnapshotError(
                "archive is missing manifest members: " + ", ".join(missing)
            )
        if extra:
            raise SnapshotError(
                "archive has unexpected members: " + ", ".join(extra)
            )

        members: dict[str, bytes] = {}
        for path, spec in expected.items():
            try:
                payload = archive.read(path)
            except KeyError as exc:
                raise SnapshotError(
                    f"archive member cannot be read: {path!r}"
                ) from exc
            if len(payload) != spec["size"]:
                raise SnapshotError(
                    f"size mismatch for {path!r}: manifest says "
                    f"{spec['size']}, archive has {len(payload)}"
                )
            if _sha256_hex(payload) != spec["sha256"]:
                raise SnapshotError(f"sha256 mismatch for {path!r}")
            members[path] = payload

        project_id = _project_id_from_bytes(members[PROJECT_JSON_NAME])
        if project_id != manifest_project_id:
            raise SnapshotError(
                "manifest project_id does not match project.json"
            )

    return manifest, members


class ProjectSnapshotImporter:
    """Restore a validated snapshot into a new project location."""

    def import_project(
        self, archive: Union[str, Path, bytes], destination: str | Path
    ) -> Project:
        """Import ``archive`` into ``destination`` and return the Project.

        The destination must not already contain an AgentIvan project and
        must not be a non-empty directory: existing projects are never
        silently overwritten. The archive and the original project are
        never mutated; only the destination is created. On failure no
        partial project is left behind.
        """
        data = _read_archive_bytes(archive)
        # Validate everything BEFORE touching the filesystem.
        _, members = _validate_archive(data)

        dest = Path(destination)
        if not dest.is_absolute():
            dest = Path.cwd() / dest
        existed_empty = False
        if dest.exists():
            if not dest.is_dir():
                raise SnapshotError(
                    f"destination is not a directory: {dest}"
                )
            try:
                has_contents = any(dest.iterdir())
            except OSError as exc:
                raise SnapshotError(
                    f"destination cannot be inspected: {exc}"
                ) from exc
            if has_contents:
                raise SnapshotError(
                    f"destination is not empty, refusing to overwrite: {dest}"
                )
            if (dest / PROJECT_JSON_NAME).exists():  # pragma: no cover
                raise SnapshotError(
                    f"destination already contains a project: {dest}"
                )
            existed_empty = True

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SnapshotError(
                f"destination parent cannot be created: {exc}"
            ) from exc

        staging: str | None = None
        try:
            staging = tempfile.mkdtemp(
                dir=str(dest.parent), prefix=".agentivan-import-"
            )
            staging_path = Path(staging)
            for name in sorted(members):
                target = staging_path.joinpath(
                    *PurePosixPath(name).parts
                )
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(members[name])
                except OSError as exc:
                    raise SnapshotError(
                        f"restoration failed while writing {name!r}: {exc}"
                    ) from exc
            # Sanity: the staged tree must load as a real AgentIvan project.
            Project.load(staging_path)

            if existed_empty:
                try:
                    dest.rmdir()
                except OSError as exc:
                    raise SnapshotError(
                        f"destination cannot be prepared: {exc}"
                    ) from exc
            try:
                os.rename(staging, str(dest))
            except OSError:
                # Cross-device fallback: still atomic from the user's view
                # because staging is removed on any failure below.
                shutil.move(staging, str(dest))
            staging = None  # moved; do not clean up
            return Project.load(dest)
        except SnapshotError:
            raise
        except Exception as exc:
            raise SnapshotError(f"import failed: {exc}") from exc
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)


def import_snapshot(
    archive: Union[str, Path, bytes], destination: str | Path
) -> Project:
    """Convenience wrapper around :class:`ProjectSnapshotImporter`."""
    return ProjectSnapshotImporter().import_project(archive, destination)
