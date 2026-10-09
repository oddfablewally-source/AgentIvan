"""
AgentIvan project (Brick 2).

A project is a relocatable directory with a stable identity:

    <project-root>/
        project.json      canonical project state (identity, sources)
        sources/          original raw material, preserved byte-for-byte
        buildpack/        structured project representation (later bricks)
        exports/          generated artifacts such as PDF/Markdown (later)

Identity lives in project.json as a UUID, never in the directory name
or path: moving the directory to another machine or folder does not
change the project's identity.

Serialization is deterministic JSON (sorted keys, UTF-8, LF endings) so
save/load round-trips exactly and repeated saves are byte-identical.

Stdlib only. No database, no network, no AI.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from .catalog import SourceCatalog
from .intake import (
    IntakeError,
    intake_file,
    intake_reference,
    intake_text,
    utc_now_iso,
)
from .paths import UnsafePathError
from .sources import SourceKind, SourceRecord

PROJECT_FORMAT_NAME = "agentivan-project"
PROJECT_FORMAT_VERSION = 1

__all__ = [
    "PROJECT_FORMAT_NAME",
    "PROJECT_FORMAT_VERSION",
    "ProjectError",
    "Project",
    "dump_project_json",
]


class ProjectError(ValueError):
    """Raised for invalid project state, identifiers, or corrupted data."""


def dump_project_json(payload: dict) -> bytes:
    """Deterministic UTF-8 JSON encoding shared by save paths."""
    return (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


class Project:
    """One AgentIvan project: identity + preserved source material."""

    def __init__(
        self,
        *,
        project_id: str,
        name: str,
        created_at: str,
        updated_at: str,
        root: Path,
        sources: list[SourceRecord] | None = None,
    ) -> None:
        if not isinstance(project_id, str) or not project_id.strip():
            raise ProjectError("project_id must be a non-empty string")
        if not isinstance(name, str) or not name.strip():
            raise ProjectError("project name must be a non-empty string")
        self.project_id = project_id
        self.name = name
        self.created_at = created_at
        self.updated_at = updated_at
        self.root = Path(root)
        self.sources: list[SourceRecord] = list(sources) if sources else []
        self._check_duplicate_source_ids()

    # ------------------------------------------------------------------
    # directories (pathlib only; platform-neutral)
    # ------------------------------------------------------------------
    @property
    def sources_dir(self) -> Path:
        return self.root / "sources"

    @property
    def buildpack_dir(self) -> Path:
        return self.root / "buildpack"

    @property
    def exports_dir(self) -> Path:
        return self.root / "exports"

    @property
    def project_json_path(self) -> Path:
        return self.root / "project.json"

    # ------------------------------------------------------------------
    # creation / loading
    # ------------------------------------------------------------------
    @classmethod
    def create(
        cls, name: str, root: str | Path, project_id: str | None = None
    ) -> "Project":
        """Create a new project at ``root`` with a stable identity.

        ``project_id`` may be supplied by the caller (the workspace does
        this so the directory can be located by id); otherwise a fresh
        UUID is generated. Identity always lives in project.json, never
        in the directory name alone.
        """
        if not isinstance(name, str) or not name.strip():
            raise ProjectError("project name must be a non-empty string")
        if project_id is None:
            project_id = uuid.uuid4().hex
        elif not isinstance(project_id, str) or not project_id.strip():
            raise ProjectError("project_id must be a non-empty string")
        root = Path(root)
        now = utc_now_iso()
        project = cls(
            project_id=project_id.strip(),
            name=name.strip(),
            created_at=now,
            updated_at=now,
            root=root,
        )
        project._ensure_dirs()
        project.save()
        return project

    @classmethod
    def load(cls, root: str | Path) -> "Project":
        """Reopen a project from its directory. Identity comes from the file."""
        root = Path(root)
        path = root / "project.json"
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ProjectError(f"project data cannot be read: {exc}") from exc
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectError(f"project data is corrupted: {exc}") from exc
        if not isinstance(data, dict):
            raise ProjectError("project data is corrupted: not an object")
        if data.get("format") != PROJECT_FORMAT_NAME:
            raise ProjectError(
                f"not an AgentIvan project file: {path} "
                f"(format={data.get('format')!r})"
            )
        try:
            project_id = data["project_id"]
            name = data["name"]
            created_at = data["created_at"]
            updated_at = data["updated_at"]
            raw_sources = data.get("sources", [])
        except KeyError as exc:
            raise ProjectError(f"project data is missing field: {exc}") from exc
        if not isinstance(raw_sources, list):
            raise ProjectError("project data is corrupted: sources is not a list")
        sources = [SourceRecord.from_dict(item) for item in raw_sources]
        project = cls(
            project_id=project_id,
            name=name,
            created_at=created_at,
            updated_at=updated_at,
            root=root,
            sources=sources,
        )
        project._ensure_dirs()
        return project

    def _ensure_dirs(self) -> None:
        try:
            self.sources_dir.mkdir(parents=True, exist_ok=True)
            self.buildpack_dir.mkdir(parents=True, exist_ok=True)
            self.exports_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ProjectError(f"project directories cannot be created: {exc}") from exc

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "format": PROJECT_FORMAT_NAME,
            "format_version": PROJECT_FORMAT_VERSION,
            "project_id": self.project_id,
            "name": self.name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "sources": [record.to_dict() for record in self.sources],
        }

    def save(self) -> None:
        """Write project.json deterministically.

        Does not bump ``updated_at``: saving without changes produces
        byte-identical output.
        """
        self._ensure_dirs()
        try:
            self.project_json_path.write_bytes(dump_project_json(self.to_dict()))
        except OSError as exc:
            raise ProjectError(f"project could not be saved: {exc}") from exc

    # ------------------------------------------------------------------
    # source intake (delegates to intake.py; records are appended verbatim)
    # ------------------------------------------------------------------
    def _register(self, record: SourceRecord) -> SourceRecord:
        if any(r.source_id == record.source_id for r in self.sources):
            raise ProjectError(f"duplicate source_id: {record.source_id}")
        self.sources.append(record)
        self.updated_at = utc_now_iso()
        return record

    def _check_duplicate_source_ids(self) -> None:
        seen = set()
        for record in self.sources:
            if record.source_id in seen:
                raise ProjectError(f"duplicate source_id: {record.source_id}")
            seen.add(record.source_id)

    def intake_text(self, text: str, **kwargs) -> SourceRecord:
        try:
            return self._register(intake_text(self, text, **kwargs))
        except (IntakeError, UnsafePathError):
            raise
        except (OSError, ValueError) as exc:
            raise ProjectError(f"text intake failed: {exc}") from exc

    def intake_file(self, source_path: str | Path, **kwargs) -> SourceRecord:
        try:
            return self._register(intake_file(self, source_path, **kwargs))
        except (IntakeError, UnsafePathError):
            raise
        except (OSError, ValueError) as exc:
            raise ProjectError(f"file intake failed: {exc}") from exc

    def intake_reference(self, reference: str, **kwargs) -> SourceRecord:
        try:
            return self._register(intake_reference(self, reference, **kwargs))
        except (IntakeError, UnsafePathError):
            raise
        except (OSError, ValueError) as exc:
            raise ProjectError(f"reference intake failed: {exc}") from exc

    def get_source(self, source_id: str) -> SourceRecord:
        for record in self.sources:
            if record.source_id == source_id:
                return record
        raise ProjectError(f"unknown source_id: {source_id}")

    def export_markdown(self, destination: "str | Path | None" = None) -> Path:
        """Render the current canonical Build Pack to a Markdown file (Brick 7).

        Writes only the Markdown output to ``destination`` -- ``None``
        defaults to ``exports/build-pack.md`` under the project root; a
        relative path resolves under ``exports/``. The canonical Build Pack,
        sources, extractions, and reconciliation state are never mutated.
        """
        from .markdown import export_project_markdown

        return export_project_markdown(self, destination)

    def export_pdf(self, destination: "str | Path | None" = None) -> Path:
        """Render the current canonical Build Pack to a PDF file (Brick 8).

        Writes only the PDF output to ``destination`` -- ``None``
        defaults to ``exports/build-pack.pdf`` under the project root; a
        relative path resolves under ``exports/``. The canonical Build Pack,
        sources, extractions, and reconciliation state are never mutated.
        """
        from .pdf import export_project_pdf

        return export_project_pdf(self, destination)

    def export_snapshot(
        self, destination: "str | Path | None" = None
    ) -> "SnapshotResult":
        """Create a deterministic portable snapshot of this project (Brick 9).

        ``None`` returns the archive bytes inside the result without
        writing a file; a relative destination resolves against the
        current working directory. Read-only: the canonical project,
        sources, extractions, reconciliation state, and Build Pack are
        never mutated.
        """
        from .snapshot import export_snapshot

        return export_snapshot(self, destination)

    def sources_by_kind(self, kind: SourceKind) -> list[SourceRecord]:
        return [record for record in self.sources if record.kind is kind]

    @property
    def catalog(self) -> SourceCatalog:
        """Deterministic source catalog view over this project (Brick 3).

        The catalog is stateless: it reads the live source records and
        persists nothing of its own. See ``catalog.py``.
        """
        return SourceCatalog(self)

    @property
    def extractions(self) -> "ExtractionStore":
        """Project-level extraction store (Brick 4).

        Runs extractors over the project's sources and caches the
        structured results under ``<root>/extractions/``. The cache is
        regenerable: deleting it loses nothing. See ``extraction.py``.
        """
        from .extraction import ExtractionStore

        return ExtractionStore(self)

    @property
    def buildpack_store(self) -> "BuildPackStore":
        """Project-level canonical Build Pack store (Brick 5).

        Persists the canonical Build Pack under ``<root>/buildpack/``.
        See ``reconciliation.py``.
        """
        from .reconciliation import BuildPackStore

        return BuildPackStore(self)

    @property
    def reconciliation_store(self) -> "ReconciliationStore":
        """Project-level reconciliation decision store (Brick 5).

        Persists reconciliation decisions under
        ``<root>/reconciliation/``. See ``reconciliation.py``.
        """
        from .reconciliation import ReconciliationStore

        return ReconciliationStore(self)
