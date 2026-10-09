"""
AgentIvan project workspace (Brick 2).

The workspace is a relocatable root directory holding any number of
projects:

    <workspace-root>/
        workspace.json        workspace state (active project id)
        projects/
            <project_id>/      one Project directory per project

The workspace root is supplied by the caller (pathlib only, no
hard-coded OS paths). When omitted it defaults to a platform-neutral
location under the user's home directory, which works unchanged on
Linux and Windows.

The workspace knows which project is ACTIVE. Intake convenience
methods operate against the active project; without an active project
they fail explicitly instead of guessing.

Stdlib only. No database, no network, no AI.
"""

from __future__ import annotations

import json
from pathlib import Path

from .project import Project, ProjectError, dump_project_json

WORKSPACE_FORMAT_NAME = "agentivan-workspace"
WORKSPACE_FORMAT_VERSION = 1

__all__ = [
    "WORKSPACE_FORMAT_NAME",
    "WORKSPACE_FORMAT_VERSION",
    "WorkspaceError",
    "AgentIvanWorkspace",
    "default_workspace_root",
]


class WorkspaceError(ValueError):
    """Raised for invalid workspace state or unknown projects."""


def default_workspace_root() -> Path:
    """Platform-neutral default workspace location under the user home."""
    return Path.home() / ".agentivan" / "workspace"


class AgentIvanWorkspace:
    """Owns project directories and tracks the active project."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_workspace_root()
        try:
            self.projects_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WorkspaceError(f"workspace root cannot be created: {exc}") from exc
        self._active_project_id: str | None = self._read_active_id()

    # ------------------------------------------------------------------
    # paths
    # ------------------------------------------------------------------
    @property
    def projects_dir(self) -> Path:
        return self.root / "projects"

    @property
    def workspace_json_path(self) -> Path:
        return self.root / "workspace.json"

    def project_root(self, project_id: str) -> Path:
        if not isinstance(project_id, str) or not project_id.strip():
            raise WorkspaceError("project_id must be a non-empty string")
        # project_id is a hex uuid; still guard the join explicitly.
        if set(project_id.strip()) - set("0123456789abcdef"):
            raise WorkspaceError(f"invalid project identifier: {project_id!r}")
        return self.projects_dir / project_id.strip()

    # ------------------------------------------------------------------
    # project lifecycle
    # ------------------------------------------------------------------
    def create_project(self, name: str) -> Project:
        import uuid as _uuid

        project_id = _uuid.uuid4().hex
        return Project.create(name, self.project_root(project_id), project_id=project_id)

    def open_project(self, project_id: str) -> Project:
        """Load a project and make it the active project."""
        project = self.get_project(project_id)
        self.set_active_project(project.project_id)
        return project

    def get_project(self, project_id: str) -> Project:
        """Load a project without changing the active project."""
        root = self.project_root(project_id)
        if not (root / "project.json").is_file():
            raise WorkspaceError(f"project does not exist: {project_id!r}")
        try:
            project = Project.load(root)
        except ProjectError as exc:
            raise WorkspaceError(f"project cannot be opened: {exc}") from exc
        if project.project_id != project_id.strip():
            raise WorkspaceError(
                "project identity mismatch: directory and project.json disagree"
            )
        return project

    def list_projects(self) -> list[dict]:
        """Summaries of loadable projects, sorted deterministically."""
        entries = []
        if self.projects_dir.is_dir():
            for child in sorted(self.projects_dir.iterdir(), key=lambda p: p.name):
                if not child.is_dir() or not (child / "project.json").is_file():
                    continue
                try:
                    project = Project.load(child)
                except ProjectError:
                    continue
                entries.append(
                    {
                        "project_id": project.project_id,
                        "name": project.name,
                        "path": str(child),
                        "source_count": len(project.sources),
                    }
                )
        entries.sort(key=lambda e: (e["name"].lower(), e["project_id"]))
        return entries

    # ------------------------------------------------------------------
    # active project
    # ------------------------------------------------------------------
    @property
    def active_project_id(self) -> str | None:
        return self._active_project_id

    @property
    def active_project(self) -> Project | None:
        if self._active_project_id is None:
            return None
        try:
            return self.get_project(self._active_project_id)
        except WorkspaceError:
            return None

    def set_active_project(self, project_id: str) -> Project:
        project = self.get_project(project_id)  # validates existence
        self._active_project_id = project.project_id
        self._write_state()
        return project

    def clear_active_project(self) -> None:
        self._active_project_id = None
        self._write_state()

    def require_active_project(self) -> Project:
        project = self.active_project
        if project is None:
            raise WorkspaceError("no active project: open a project first")
        return project

    # ------------------------------------------------------------------
    # intake against the active project
    # ------------------------------------------------------------------
    def intake_text(self, text: str, **kwargs):
        project = self.require_active_project()
        record = project.intake_text(text, **kwargs)
        project.save()
        return record

    def intake_file(self, source_path: str | Path, **kwargs):
        project = self.require_active_project()
        record = project.intake_file(source_path, **kwargs)
        project.save()
        return record

    def intake_reference(self, reference: str, **kwargs):
        project = self.require_active_project()
        record = project.intake_reference(reference, **kwargs)
        project.save()
        return record

    # ------------------------------------------------------------------
    # workspace state
    # ------------------------------------------------------------------
    def _read_active_id(self) -> str | None:
        path = self.workspace_json_path
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkspaceError(f"workspace state is corrupted: {exc}") from exc
        if not isinstance(data, dict) or data.get("format") != WORKSPACE_FORMAT_NAME:
            raise WorkspaceError("workspace state is corrupted: bad format")
        active = data.get("active_project_id")
        if active is not None and (not isinstance(active, str) or not active.strip()):
            raise WorkspaceError("workspace state is corrupted: bad active_project_id")
        return active

    def _write_state(self) -> None:
        payload = {
            "format": WORKSPACE_FORMAT_NAME,
            "format_version": WORKSPACE_FORMAT_VERSION,
            "active_project_id": self._active_project_id,
        }
        try:
            self.workspace_json_path.write_bytes(dump_project_json(payload))
        except OSError as exc:
            raise WorkspaceError(f"workspace state cannot be saved: {exc}") from exc
